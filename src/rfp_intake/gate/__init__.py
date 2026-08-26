"""GATE — maps confidence + contradiction state to a reviewer-facing status.
Pure Python, no LLM. See ARCHITECTURE.md §4.9.

| Condition                                                     | Status         |
|----------------------------------------------------------------|----------------|
| single/corroborated source, conf >= CONFIDENCE_CONFIRMED       | confirmed      |
| derived field                                                  | needs_review   |
| any contradiction whose verdict isn't "not_a_conflict"         | needs_review   |
| conf < CONFIDENCE_CONFIRMED                                    | needs_review   |
| already not_specified / not_found                              | unchanged      |

The spec table (ARCHITECTURE.md §4.9) calls out "budget_driver with any
disagreement" as its own row alongside "conflict verdict" and "reconcilable
verdict". Those three collapse to one rule here — any live contradiction
whose verdict isn't (yet, or ever) "not_a_conflict" forces needs_review,
regardless of budget_driver — because the table has no row where a
resolved value survives as "confirmed" next to an unresolved disagreement
of any kind. This path is presently untested against real data: RECONCILE
never emits a ResolvedField for a field with a live disagreement (see its
docstring), so a ResolvedField and a matching Contradiction never coexist
yet. It's wired correctly for when ADJUDICATE starts producing both.

Quote validation ("quote validated" in the table) is already enforced by
EXTRACT's post-call validation (ARCHITECTURE.md §4.4) before a record ever
reaches state.records, so it is not re-checked here.

Thresholds are the mechanism's placeholders, not gospel — calibrate against
the golden set per ARCHITECTURE.md §9 before trusting them in a report.
"""

from __future__ import annotations

from typing import Any

import structlog

from rfp_intake.domain.schemas import Contradiction, ResolvedField, RunState

logger = structlog.get_logger()

CONFIDENCE_CONFIRMED = 0.80

_TERMINAL_STATUSES = {"not_specified", "not_found"}


def gate_node(state: RunState) -> dict[str, Any]:
    """GATE graph node. Reads state.resolved / contradictions, rewrites status."""
    contradiction_by_key: dict[tuple[str, str | None], Contradiction] = {}
    for c in state.contradictions:
        scope = c.records[0].scope if c.records else None
        contradiction_by_key[(c.field_id, scope)] = c

    gated = [
        _gate_field(rf, contradiction_by_key.get((rf.field_id, rf.scope)))
        for rf in state.resolved
    ]

    logger.info("gate_complete", run_id=state.run_id, gated=len(gated))

    return {"resolved": gated}


def _gate_field(rf: ResolvedField, contradiction: Contradiction | None) -> ResolvedField:
    if rf.status in _TERMINAL_STATUSES:
        return rf

    if rf.derived_from:
        return rf.model_copy(update={"status": "needs_review"})

    if contradiction is not None and contradiction.verdict != "not_a_conflict":
        return rf.model_copy(update={"status": "needs_review", "contradiction": contradiction})

    if rf.confidence >= CONFIDENCE_CONFIRMED:
        return rf.model_copy(update={"status": "confirmed"})

    return rf.model_copy(update={"status": "needs_review"})
