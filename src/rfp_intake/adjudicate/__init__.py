"""ADJUDICATE — LLM judgment on candidate contradictions, invoked once per
candidate, never on the corpus at large. See ARCHITECTURE.md §4.7.

The LLM decides *whether* records genuinely disagree (verdict) and, for
reconcilable pairs, *which* record to show — by the record's number in the
prompt's candidate list, not by document, because several records for one field
routinely come from the same document. Which value
wins a genuine "conflict" is a deterministic code decision — see
reconcile/precedence.py — not something the model picks, so an analyst can
trace a resolved value to a named rule instead of a black box.

Every adjudicated candidate produces a ResolvedField (or, for most
not_a_conflict verdicts, one per record — see _handle_not_a_conflict) with
status="needs_review":
GATE always sends a field with a live, non-"not_a_conflict" contradiction to
needs_review regardless of confidence, and even a not_a_conflict verdict
starts as needs_review here so GATE's ordinary confidence gating is what
decides confirmed vs. needs_review for it, same as any other resolved field.
"""

from __future__ import annotations

from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, field_validator

from rfp_intake.adjudicate.prompt import build_adjudicate_prompt
from rfp_intake.domain.precedence import PrecedencePolicy, get_precedence_policy
from rfp_intake.domain.registry import FieldDef, get_registry
from rfp_intake.domain.schemas import Contradiction, FieldRecord, ResolvedField, RunError, RunState
from rfp_intake.llm.provider import get_llm
from rfp_intake.llm.structured import StructuredOutput, get_structured_output_for_role
from rfp_intake.reconcile import scope_key
from rfp_intake.reconcile.precedence import apply_precedence

logger = structlog.get_logger()


class AdjudicationResult(BaseModel):
    verdict: Literal["conflict", "reconcilable", "not_a_conflict"]
    explanation: str
    # 1-based position in Contradiction.records, numbered as the prompt numbers
    # the candidates. Replaces the earlier winning_doc_id: a document id cannot
    # name one of several records from the same document.
    winning_record: int | None = None
    severity: Literal["high", "medium", "low"] = Field(default="medium")
    # 1-based positions, numbered as winning_record is, of records the model
    # says are not about this field at all. Fix 5 of the 2026-10-12 brief.
    off_topic_records: list[int] = Field(default_factory=list)

    @field_validator("off_topic_records", mode="before")
    @classmethod
    def _none_said(cls, v: object) -> object:
        """An empty string or null is "no record is off-topic". Claude Opus 5 wrote
        `"off_topic_records": ""` for "Whole study duration" in run
        r-20261123-oct29-run2, which failed validation and lost the verdict."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return []
        return v


def adjudicate_node(state: RunState) -> dict[str, Any]:
    """ADJUDICATE graph node. Reads state.contradictions, judges each candidate."""
    registry = get_registry()
    policy = get_precedence_policy()
    llm = get_llm("adjudicate")
    structured = get_structured_output_for_role(llm, "adjudicate")

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
            adjudicated, fields = _adjudicate_with_retry(c, field_def, policy, structured)
        except Exception as e:
            logger.error("adjudicate_failed", field_id=c.field_id, error=str(e))
            errors.append(RunError(
                node="ADJUDICATE", task_id=c.field_id, error=f"Adjudication failed: {e}",
            ))
            adjudicated, fields = _unjudged(c, field_def)
        updated_contradictions.append(adjudicated)
        new_resolved.extend(fields)

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


# How many times one candidate is put to the model before it is shown as a
# disagreement nobody has judged (_unjudged). A second attempt costs one call, and
# only where the first one failed.
ADJUDICATE_ATTEMPTS = 2

# What Appendix B says about a candidate the model could not judge.
UNJUDGED_EXPLANATION = (
    "The reviewer model could not judge these passages (its answer could not be read), "
    "so they are shown as a disagreement for a person to settle."
)


def _adjudicate_with_retry(
    contradiction: Contradiction,
    field_def: FieldDef,
    policy: PrecedencePolicy,
    structured: StructuredOutput,
) -> tuple[Contradiction, list[ResolvedField]]:
    """`_adjudicate_one`, tried again once if the call fails. Static control
    flow: the number of attempts is fixed here, never chosen by a model."""
    for attempt in range(1, ADJUDICATE_ATTEMPTS + 1):
        try:
            return _adjudicate_one(contradiction, field_def, policy, structured)
        except Exception as e:
            if attempt == ADJUDICATE_ATTEMPTS:
                raise
            logger.warning(
                "adjudicate_retry", field_id=contradiction.field_id, attempt=attempt,
                error=str(e)[:200],
            )
    raise AssertionError("unreachable")  # the loop returns or raises


def _unjudged(
    contradiction: Contradiction, field_def: FieldDef,
) -> tuple[Contradiction, list[ResolvedField]]:
    """A candidate the model could not judge is a disagreement for a person to
    settle, shown with the value document precedence gives (_handle_conflict),
    never dropped. Code found the documents' values differ (RECONCILE); without a
    model saying otherwise, a person must choose.

    Until 2026-11-23 the candidate kept verdict None and no row at all, so the
    report printed the variable as "Not found" and listed no disagreement: run
    r-20261123-oct29-run2 showed "Whole study duration: Not found" though the
    amendment says 42 months and the RFP 40. `explanation` says no model judged
    it, and the run's errors list keeps the failed call. Severity is high for a
    budget driver, as config/fields.yaml's header defines it for any disagreement
    on one, and otherwise left unrated: no model rated it.
    """
    marked = contradiction.model_copy(update={
        "verdict": "conflict",
        "explanation": UNJUDGED_EXPLANATION,
        "severity": "high" if field_def.budget_driver else None,
    })
    return _handle_conflict(marked, field_def)


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
        "off_topic_record_indexes": _off_topic(contradiction, result),
    })

    if result.verdict == "not_a_conflict":
        return _handle_not_a_conflict(adjudicated, field_def, result)
    if result.verdict == "reconcilable":
        return _handle_reconcilable(adjudicated, result)
    return _handle_conflict(adjudicated, field_def)


# The field types whose answer is one choice. Prompt B item 2: nine records of
# "Schedule of activities present", all unscoped, came back "not_a_conflict" in
# run r-20261013-prompt-c and became nine rows of one answer each.
_ONE_CHOICE_TYPES = ("enum", "bool")


def _handle_not_a_conflict(
    contradiction: Contradiction, field_def: FieldDef, result: AdjudicationResult,
) -> tuple[Contradiction, list[ResolvedField]]:
    """Records weren't actually comparable — resolve each on its own rather
    than force a single merged value RECONCILE's scope grouping got wrong.

    Two exceptions, both where every record about the field has the same scope,
    get one row for the cluster, written as a reconcilable one is (_one_row);
    the other records are in Appendix A:

    - the field's answer is one choice (`type` in config/fields.yaml is enum or
      bool): one row per record would only repeat one question;
    - every record about the field gives the same normalized value, whatever the
      field's type: once the off-topic records are set aside nothing differs.
      Added 2026-11-23: the subject count's three records of 260 (amendment
      p.15 and p.43, RFP p.4) beside an off-topic 236 came back as three rows
      of 260.

    Otherwise free-text and number fields keep one row per record.
    """
    on_topic = _on_topic(contradiction)
    # The same scope as RECONCILE groups scopes (reconcile/__init__.py:scope_key):
    # on a field that is not scoped, "total" and no scope are one.
    if len({scope_key(field_def, r.scope) for r in on_topic}) == 1 and (
        field_def.type in _ONE_CHOICE_TYPES or _one_value(on_topic)
    ):
        return _one_row(contradiction, result, "NOT_A_CONFLICT")
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
        for r in on_topic
    ]
    return contradiction, fields


def _handle_reconcilable(
    contradiction: Contradiction, result: AdjudicationResult
) -> tuple[Contradiction, list[ResolvedField]]:
    return _one_row(contradiction, result, "RECONCILABLE")


def _one_row(
    contradiction: Contradiction, result: AdjudicationResult, label: str,
) -> tuple[Contradiction, list[ResolvedField]]:
    """One row for the whole cluster: the record ADJUDICATE named, else the most
    confident, citing every on-topic record's page. `winning_record_index` is
    what tells the report the row stands for the whole cluster."""
    on_topic = _on_topic(contradiction)
    index = _winning_index(
        contradiction.records, result.winning_record, contradiction.off_topic_record_indexes,
    )
    winner = contradiction.records[index]

    contradiction = contradiction.model_copy(update={
        "resolved_value": winner.value,
        "winning_doc_id": winner.provenance.doc_id,
        "winning_record_index": index + 1,
    })
    rf = ResolvedField(
        field_id=contradiction.field_id,
        value=winner.value,
        status="needs_review",
        confidence=winner.confidence,
        sources=[r.provenance for r in on_topic],
        quote=winner.quote,
        scope=winner.scope,
        contradiction=contradiction,
        notes=f"{label} — {contradiction.explanation}",
    )
    return contradiction, [rf]


def _handle_conflict(
    contradiction: Contradiction, field_def: FieldDef
) -> tuple[Contradiction, list[ResolvedField]]:
    precedence = apply_precedence(field_def, contradiction.records)
    winner = precedence.winner
    # By identity: precedence returns one of these record objects.
    index = (
        next(i for i, r in enumerate(contradiction.records) if r is winner)
        if winner is not None else None
    )

    contradiction = contradiction.model_copy(update={
        "resolved_value": winner.value if winner else None,
        "winning_doc_id": winner.provenance.doc_id if winner else None,
        "winning_record_index": index + 1 if index is not None else None,
        "precedence_rule": precedence.rule_applied,
    })

    # Value, quote and confidence all come from the one record precedence chose,
    # so the row can never print one record's value with another's quote.
    rf = ResolvedField(
        field_id=contradiction.field_id,
        value=winner.value if winner else None,
        status="needs_review",
        confidence=winner.confidence if winner else 0.0,
        sources=[r.provenance for r in contradiction.records],
        quote=winner.quote if winner else None,
        scope=contradiction.records[0].scope if contradiction.records else None,
        contradiction=contradiction,
        notes=f"CONFLICT (precedence: {precedence.rule_applied}) — {contradiction.explanation}",
    )
    return contradiction, [rf]


def _winning_index(
    records: list[FieldRecord], winning_record: int | None, off_topic: list[int] | None = None,
) -> int:
    """Index in `records` of the record ADJUDICATE chose, by its prompt number.

    Falls back to the highest-confidence record when the model named no record,
    named one that is not in the list, or named one it also called off-topic —
    earlier ties going to the earlier record, so the choice is deterministic.
    Off-topic records are never the fallback. `records` is never empty, and
    `_off_topic` never marks every record, so a candidate always remains.
    """
    excluded = set(off_topic or [])
    if (winning_record is not None and 1 <= winning_record <= len(records)
            and winning_record not in excluded):
        return winning_record - 1
    if winning_record is not None:
        logger.warning(
            "adjudicate_winning_record_not_usable",
            winning_record=winning_record,
            record_count=len(records),
            off_topic=sorted(excluded),
        )
    usable = [i for i in range(len(records)) if i + 1 not in excluded]
    return max(usable, key=lambda i: records[i].confidence)


def _off_topic(contradiction: Contradiction, result: AdjudicationResult) -> list[int]:
    """The model's off-topic record numbers that ADJUDICATE acts on.

    Numbers outside the list are dropped. A `conflict` acts on none: its
    records stay sides of the disagreement, so a person sees each one, which is
    the safe direction if the model is wrong. Nor does a list naming every
    record: a field with no record about it is not this verdict's to decide.
    Either case is logged.
    """
    count = len(contradiction.records)
    numbers = sorted({n for n in result.off_topic_records if 1 <= n <= count})
    if numbers and (result.verdict == "conflict" or len(numbers) == count):
        logger.warning(
            "adjudicate_off_topic_ignored",
            field_id=contradiction.field_id,
            verdict=result.verdict,
            off_topic=numbers,
            record_count=count,
        )
        return []
    return numbers


def _one_value(records: list[FieldRecord]) -> bool:
    """Whether every record gives the same normalized value, compared with ==
    as RECONCILE compares values (reconcile/__init__.py:_group_by_canonical_value),
    so a list or a dict is the same value only if it is equal."""
    return all(r.value == records[0].value for r in records[1:])


def _on_topic(contradiction: Contradiction) -> list[FieldRecord]:
    """The records that get a row: every record not marked off-topic."""
    off = set(contradiction.off_topic_record_indexes)
    return [r for i, r in enumerate(contradiction.records, start=1) if i not in off]
