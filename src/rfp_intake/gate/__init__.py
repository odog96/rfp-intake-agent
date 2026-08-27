"""GATE — maps confidence + contradiction state to a reviewer-facing status.
Pure Python, no LLM. See ARCHITECTURE.md §4.9.

| Condition                                                          | Status         |
|-----------------------------------------------------------------------|----------------|
| single/corroborated source, conf >= CONFIDENCE_CONFIRMED              | confirmed      |
| derived field                                                         | needs_review   |
| any contradiction whose verdict isn't "not_a_conflict"                | needs_review   |
| budget_driver field with ANY contradiction, even "not_a_conflict"     | needs_review   |
| conf < CONFIDENCE_CONFIRMED                                           | needs_review   |
| already not_specified / not_found                                     | unchanged      |

The budget_driver row is deliberately stricter than ARCHITECTURE.md's own
table, which only names "budget_driver with any disagreement" alongside
conflict/reconcilable — read literally that leaves a misjudged
not_a_conflict verdict on a budget-driving field free to auto-confirm.
That is the single costliest place ADJUDICATE's verdict call (an LLM
judgment, not a deterministic one — see adjudicate/__init__.py) could be
wrong: a wrong "these don't really disagree" on a site count or subject
count changes the number DSB submits. Forcing review here doesn't fix a
wrong verdict, but it guarantees a human sees it before it becomes a
number in the report, rather than the disagreement being silently cleared.

Quote validation ("quote validated" in the table) is already enforced by
EXTRACT's post-call validation (ARCHITECTURE.md §4.4) before a record ever
reaches state.records, so it is not re-checked here.

Thresholds are the mechanism's placeholders, not gospel — calibrate against
the golden set per ARCHITECTURE.md §9 before trusting them in a report.
"""

from __future__ import annotations

from typing import Any

import structlog

from rfp_intake.domain.registry import get_registry
from rfp_intake.domain.schemas import Contradiction, ResolvedField, RunState

logger = structlog.get_logger()

CONFIDENCE_CONFIRMED = 0.80

_TERMINAL_STATUSES = {"not_specified", "not_found"}


def gate_node(state: RunState) -> dict[str, Any]:
    """GATE graph node. Reads state.resolved / contradictions, rewrites status."""
    budget_drivers = {f.id for f in get_registry().fields if f.budget_driver}

    contradiction_by_key: dict[tuple[str, str | None], Contradiction] = {}
    for c in state.contradictions:
        scope = c.records[0].scope if c.records else None
        contradiction_by_key[(c.field_id, scope)] = c

    gated = [
        _gate_field(
            rf,
            contradiction_by_key.get((rf.field_id, rf.scope)),
            is_budget_driver=rf.field_id in budget_drivers,
        )
        for rf in state.resolved
    ]

    logger.info("gate_complete", run_id=state.run_id, gated=len(gated))

    return {"resolved": gated}


def _gate_field(
    rf: ResolvedField, contradiction: Contradiction | None, *, is_budget_driver: bool
) -> ResolvedField:
    if rf.status in _TERMINAL_STATUSES:
        return rf

    if rf.derived_from:
        return rf.model_copy(update={"status": "needs_review"})

    disagreement = contradiction is not None and (
        contradiction.verdict != "not_a_conflict" or is_budget_driver
    )
    if disagreement:
        return rf.model_copy(update={"status": "needs_review", "contradiction": contradiction})

    # A budget driver holding several values never confirms on its own. RECONCILE
    # folds a collection field's records into one answer with several members —
    # correctly, since they are parts of one list rather than rival answers — but
    # that also removes the disagreement that used to force a human to look.
    # ops.monitoring_visits came back as ["75", "750", "300"] in run
    # r-listfix-175318: three different kinds of monitoring visit, and which
    # number belongs in a budget is exactly the judgement this pipeline must not
    # make silently.
    if is_budget_driver and isinstance(rf.value, list) and len(rf.value) > 1:
        return rf.model_copy(update={"status": "needs_review"})

    if rf.confidence >= CONFIDENCE_CONFIRMED:
        return rf.model_copy(update={"status": "confirmed"})

    return rf.model_copy(update={"status": "needs_review"})
