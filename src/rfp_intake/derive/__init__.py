"""DERIVE — computed fields, kept structurally separate from extracted ones
so a report reader never confuses a rubric score with a cited source value.
Pure Python, no LLM. See ARCHITECTURE.md §4.8.

Adding a new derived field to fields.yaml (derived: true) requires adding a
matching entry to DERIVE_RUBRICS below — this is the one place P4 ("never
hardcode a field") cannot be fully honored, because a rubric is inherently
bespoke logic, not something a YAML edit alone can express. A derived field
with no registered rubric degrades to not_specified rather than crashing
the run, and is logged as an error so the gap is visible.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from rfp_intake.derive.rubric import RubricResult, compute_visit_intensity
from rfp_intake.domain.registry import get_registry
from rfp_intake.domain.schemas import ResolvedField, RunError, RunState

logger = structlog.get_logger()

DERIVE_RUBRICS: dict[str, Callable[[dict[str, ResolvedField | None]], RubricResult]] = {
    "visits.intensity_rating": compute_visit_intensity,
}


def derive_node(state: RunState) -> dict[str, Any]:
    """DERIVE graph node. Reads state.resolved, appends computed ResolvedFields."""
    registry = get_registry()
    resolved_by_id = {r.field_id: r for r in state.resolved}
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
            inputs = {dep: resolved_by_id.get(dep) for dep in (field_def.derived_from or [])}
            result = rubric(inputs)
            computed.append(ResolvedField(
                field_id=field_def.id,
                value=result.value,
                status=result.status,
                confidence=result.confidence,
                sources=result.sources,
                derived_from=field_def.derived_from or [],
                notes=result.explanation or None,
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
