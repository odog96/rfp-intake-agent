"""ADJUDICATE — LLM judgment on candidate contradictions, invoked once per
candidate, never on the corpus at large. See ARCHITECTURE.md §4.7.

The LLM decides *whether* records genuinely disagree (verdict) and, for
reconcilable pairs, *which* record to show (winning_doc_id). Which value
wins a genuine "conflict" is a deterministic code decision — see
reconcile/precedence.py — not something the model picks, so an analyst can
trace a resolved value to a named rule instead of a black box.

Every adjudicated candidate produces a ResolvedField (or one per record, for
not_a_conflict — see _handle_not_a_conflict) with status="needs_review":
GATE always sends a field with a live, non-"not_a_conflict" contradiction to
needs_review regardless of confidence, and even a not_a_conflict verdict
starts as needs_review here so GATE's ordinary confidence gating is what
decides confirmed vs. needs_review for it, same as any other resolved field.
"""

from __future__ import annotations

from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field

from rfp_intake.adjudicate.prompt import build_adjudicate_prompt
from rfp_intake.domain.precedence import PrecedencePolicy, get_precedence_policy
from rfp_intake.domain.registry import FieldDef, get_registry
from rfp_intake.domain.schemas import Contradiction, FieldRecord, ResolvedField, RunError, RunState
from rfp_intake.llm.provider import get_llm
from rfp_intake.llm.structured import StructuredOutput, get_structured_output
from rfp_intake.reconcile.precedence import apply_precedence

logger = structlog.get_logger()


class AdjudicationResult(BaseModel):
    verdict: Literal["conflict", "reconcilable", "not_a_conflict"]
    explanation: str
    winning_doc_id: str | None = None
    severity: Literal["high", "medium", "low"] = Field(default="medium")


def adjudicate_node(state: RunState) -> dict[str, Any]:
    """ADJUDICATE graph node. Reads state.contradictions, judges each candidate."""
    registry = get_registry()
    policy = get_precedence_policy()
    llm = get_llm("adjudicate")
    structured = get_structured_output(llm)

    updated_contradictions: list[Contradiction] = []
    new_resolved: list[ResolvedField] = []
    errors: list[RunError] = []

    for c in state.contradictions:
        if c.verdict is not None:
            updated_contradictions.append(c)  # already adjudicated (e.g. resumed run)
            continue

        try:
            field_def = registry.get_field(c.field_id)
        except KeyError:
            errors.append(RunError(
                node="ADJUDICATE", task_id=c.field_id,
                error=f"Unknown field in contradiction: {c.field_id}",
            ))
            updated_contradictions.append(c)
            continue

        try:
            adjudicated, fields = _adjudicate_one(c, field_def, policy, structured)
            updated_contradictions.append(adjudicated)
            new_resolved.extend(fields)
        except Exception as e:
            logger.error("adjudicate_failed", field_id=c.field_id, error=str(e))
            errors.append(RunError(
                node="ADJUDICATE", task_id=c.field_id, error=f"Adjudication failed: {e}",
            ))
            updated_contradictions.append(c)  # left unadjudicated — verdict stays None

    logger.info(
        "adjudicate_complete",
        run_id=state.run_id,
        adjudicated=sum(1 for c in updated_contradictions if c.verdict is not None),
        errors=len(errors),
    )

    return {
        "contradictions": updated_contradictions,
        "resolved": state.resolved + new_resolved,
        "errors": errors,
    }


def _adjudicate_one(
    contradiction: Contradiction,
    field_def: FieldDef,
    policy: PrecedencePolicy,
    structured: StructuredOutput,
) -> tuple[Contradiction, list[ResolvedField]]:
    messages = build_adjudicate_prompt(contradiction, field_def, policy)
    result = structured.extract(AdjudicationResult, messages)

    adjudicated = contradiction.model_copy(update={
        "verdict": result.verdict,
        "explanation": result.explanation,
        "severity": result.severity,
    })

    if result.verdict == "not_a_conflict":
        return _handle_not_a_conflict(adjudicated)
    if result.verdict == "reconcilable":
        return _handle_reconcilable(adjudicated, result)
    return _handle_conflict(adjudicated, field_def)


def _handle_not_a_conflict(
    contradiction: Contradiction,
) -> tuple[Contradiction, list[ResolvedField]]:
    """Records weren't actually comparable — resolve each on its own rather
    than force a single merged value RECONCILE's scope grouping got wrong."""
    fields = [
        ResolvedField(
            field_id=r.field_id,
            value=r.value,
            status="needs_review",
            confidence=r.confidence,
            sources=[r.provenance],
            quote=r.quote,
            scope=r.scope,
            contradiction=contradiction,
            notes=f"NOT_A_CONFLICT — {contradiction.explanation}",
        )
        for r in contradiction.records
    ]
    return contradiction, fields


def _handle_reconcilable(
    contradiction: Contradiction, result: AdjudicationResult
) -> tuple[Contradiction, list[ResolvedField]]:
    winner = _find_record(contradiction.records, result.winning_doc_id)
    if winner is None:
        winner = max(contradiction.records, key=lambda r: r.confidence)

    contradiction = contradiction.model_copy(update={
        "resolved_value": winner.value,
        "winning_doc_id": winner.provenance.doc_id,
    })
    rf = ResolvedField(
        field_id=contradiction.field_id,
        value=winner.value,
        status="needs_review",
        confidence=winner.confidence,
        sources=[r.provenance for r in contradiction.records],
        quote=winner.quote,
        scope=winner.scope,
        contradiction=contradiction,
        notes=f"RECONCILABLE — {contradiction.explanation}",
    )
    return contradiction, [rf]


def _handle_conflict(
    contradiction: Contradiction, field_def: FieldDef
) -> tuple[Contradiction, list[ResolvedField]]:
    precedence = apply_precedence(field_def, contradiction.records)

    contradiction = contradiction.model_copy(update={
        "resolved_value": precedence.value,
        "winning_doc_id": precedence.winning_doc_id,
    })
    quote = None
    if precedence.winning_doc_id is not None:
        winner = _find_record(contradiction.records, precedence.winning_doc_id)
        quote = winner.quote if winner else None

    rf = ResolvedField(
        field_id=contradiction.field_id,
        value=precedence.value,
        status="needs_review",
        confidence=precedence.confidence,
        sources=[r.provenance for r in contradiction.records],
        quote=quote,
        scope=contradiction.records[0].scope if contradiction.records else None,
        contradiction=contradiction,
        notes=f"CONFLICT (precedence: {precedence.rule_applied}) — {contradiction.explanation}",
    )
    return contradiction, [rf]


def _find_record(records: list[FieldRecord], doc_id: str | None) -> FieldRecord | None:
    if doc_id is None:
        return None
    return next((r for r in records if r.provenance.doc_id == doc_id), None)
