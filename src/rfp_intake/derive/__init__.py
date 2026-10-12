"""DERIVE — computed fields, kept structurally separate from extracted ones
so a report reader never confuses a rubric score with a cited source value.
Pure Python, no LLM. See ARCHITECTURE.md §4.8.

Adding a new derived field to fields.yaml (derived: true) requires adding a
matching entry to DERIVE_RUBRICS below — this is the one place P4 ("never
hardcode a field") cannot be fully honored, because a rubric is inherently
bespoke logic, not something a YAML edit alone can express. A derived field
with no registered rubric degrades to not_specified rather than crashing
the run, and is logged as an error so the gap is visible.

A rubric receives every row of each input, never one picked for it. Until
2026-10-11 DERIVE kept one row per field id and the last row won, so a small
"Months 1-3" row replaced the study's nine-item evidence row and the visit
intensity read low instead of high — in 13 of the 29 runs then in runs/. The
only rows held back are those the derived field's `leave_out_scopes` in
config/fields.yaml names, and the derived row's note says which and why.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from rfp_intake.derive.rubric import (
    RubricResult,
    compute_placebo_assumption,
    compute_visit_intensity,
)
from rfp_intake.domain.registry import Registry, ScopeLeftOut, get_registry, scope_has_phrase
from rfp_intake.domain.schemas import ResolvedField, RunError, RunState

logger = structlog.get_logger()

# A rubric reads the registry for the labels and `value_words` its plain-words
# reasons are written in (fix 8 of the 2026-10-12 brief).
DERIVE_RUBRICS: dict[str, Callable[[dict[str, list[ResolvedField]], Registry], RubricResult]] = {
    "visits.intensity_rating": compute_visit_intensity,
    "blinding.placebo_assumption": compute_placebo_assumption,
}


def derive_node(state: RunState) -> dict[str, Any]:
    """DERIVE graph node. Reads state.resolved, appends computed ResolvedFields."""
    registry = get_registry()
    rows_by_id: dict[str, list[ResolvedField]] = {}
    for r in state.resolved:
        rows_by_id.setdefault(r.field_id, []).append(r)
    errors: list[RunError] = []
    computed: list[ResolvedField] = []

    for field_def in registry.fields:
        if not field_def.derived:
            continue

        rubric = DERIVE_RUBRICS.get(field_def.id)
        if rubric is None:
            logger.error("no_rubric_registered", field_id=field_def.id)
            errors.append(RunError(
                node="DERIVE",
                task_id=field_def.id,
                error=f"No rubric implemented for derived field {field_def.id}",
            ))
            computed.append(ResolvedField(
                field_id=field_def.id,
                value=None,
                status="not_specified",
                confidence=0.0,
                derived_from=field_def.derived_from or [],
            ))
            continue

        try:
            inputs: dict[str, list[ResolvedField]] = {}
            left_out: list[ResolvedField] = []
            for dep in field_def.derived_from or []:
                inputs[dep] = []
                for r in rows_by_id.get(dep, []):
                    if _is_left_out(r.scope, field_def.leave_out_scopes):
                        left_out.append(r)
                    else:
                        inputs[dep].append(r)
            result = rubric(inputs, registry)
            notes = [result.explanation] if result.explanation else []
            if left_out and field_def.leave_out_scopes is not None:
                notes.append(_left_out_note(left_out, field_def.leave_out_scopes, registry))
            computed.append(ResolvedField(
                field_id=field_def.id,
                value=result.value,
                status=result.status,
                confidence=result.confidence,
                sources=result.sources,
                derived_from=field_def.derived_from or [],
                notes="; ".join(notes) or None,
                why=result.why or None,
            ))
        except Exception as e:
            logger.error("derive_field_failed", field_id=field_def.id, error=str(e))
            errors.append(RunError(
                node="DERIVE",
                task_id=field_def.id,
                error=f"Derivation failed for {field_def.id}: {e}",
            ))
            computed.append(ResolvedField(
                field_id=field_def.id,
                value=None,
                status="not_specified",
                confidence=0.0,
                derived_from=field_def.derived_from or [],
            ))

    logger.info("derive_complete", run_id=state.run_id, computed=len(computed))

    return {"resolved": state.resolved + computed, "errors": errors}


def _is_left_out(scope: str | None, leave_out: ScopeLeftOut | None) -> bool:
    """Whether a row's scope contains one of the phrases, word for word."""
    return leave_out is not None and scope_has_phrase(scope, leave_out.phrases)


def _left_out_note(rows: list[ResolvedField], leave_out: ScopeLeftOut, registry: Registry) -> str:
    """Names every row the rubric did not read, where it came from, and why.

    Rows with the same field and the same scope are named once, with all their
    pages, each page once: a schedule split into five rows is one thing a
    reviewer has to check, not five.
    """
    pages: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        cited = pages.setdefault((r.field_id, r.scope or ""), [])
        for p in r.sources:
            page = f"{p.doc_kind} p.{p.page}"
            if page not in cited:
                cited.append(page)
    named = [
        f'{registry.get_field(field_id).label} for "{scope}"'
        + (f" ({', '.join(cited)})" if cited else "")
        for (field_id, scope), cited in pages.items()
    ]
    return f"left out {'; '.join(named)}: {leave_out.reason}"
