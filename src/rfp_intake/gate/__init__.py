"""GATE — maps confidence + contradiction state to a reviewer-facing status.
Pure Python, no LLM. See ARCHITECTURE.md §4.9.

| Condition                                                          | Status         |
|-----------------------------------------------------------------------|----------------|
| single/corroborated source, conf >= CONFIDENCE_CONFIRMED              | confirmed      |
| derived field                                                         | needs_review   |
| any contradiction whose verdict is not in DISMISSED_VERDICTS          | needs_review   |
| budget_driver field with ANY contradiction, dismissed verdict or not  | needs_review   |
| field whose `follows_conflict_of` field has such a contradiction      | needs_review   |
| conf < CONFIDENCE_CONFIRMED                                           | needs_review   |
| already not_specified / not_found                                     | unchanged      |

The budget_driver row is what ARCHITECTURE.md §4.9 means by "budget_driver
with any disagreement": any means any adjudicated verdict, `not_a_conflict`
and `reconcilable` included. Without it a misjudged dismissed verdict on a
budget-driving field would be free to auto-confirm.
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

# Verdicts in which ADJUDICATE said the records do not really disagree, so the
# field is not a disagreement a reviewer has to settle.
#
# "reconcilable" joined "not_a_conflict" here on 2026-10-07, after the review of
# run r-20261007-140411. ADJUDICATE defines it (adjudicate/prompt.py) as "both
# values are true; one is a subset, restatement, or more specific version of the
# other" — that is the reviewer deciding there is nothing to settle, and it also
# names which record to show. RECONCILE flags any variable whose values are not
# byte-identical, so free-text variables were reaching the Disagreements table
# only because two passages worded the same fact differently: in
# r-20261007-140411 that was Disagreements 8 (general study design), 9
# (first-in-human) and 10 (patient population). The report now follows the
# reviewer's own conclusion.
#
# A budget driver still goes to review (the rule below), so a wrong
# "reconcilable" on a number the budget depends on is still seen by a person —
# it moves from the Disagreements table to Flagged for review rather than being
# cleared. A verdict of None means ADJUDICATE has not run or failed, which is
# not a dismissal, so it is deliberately absent from this tuple.
DISMISSED_VERDICTS = ("not_a_conflict", "reconcilable")


def gate_node(state: RunState) -> dict[str, Any]:
    """GATE graph node. Reads state.resolved / contradictions, rewrites status."""
    registry = get_registry()
    budget_drivers = {f.id for f in registry.fields if f.budget_driver}
    # Fields that config/fields.yaml says share another field's disagreement.
    follows = {
        f.id: f.follows_conflict_of
        for f in registry.fields
        if f.follows_conflict_of is not None
    }

    contradiction_by_key: dict[tuple[str, str | None], Contradiction] = {}
    for c in state.contradictions:
        scope = c.records[0].scope if c.records else None
        contradiction_by_key[(c.field_id, scope)] = c

    gated = [
        _gate_field(
            rf,
            _contradiction_for(rf, contradiction_by_key, follows),
            is_budget_driver=rf.field_id in budget_drivers,
        )
        for rf in state.resolved
    ]

    logger.info("gate_complete", run_id=state.run_id, gated=len(gated))

    return {"resolved": gated}


def _contradiction_for(
    rf: ResolvedField,
    contradiction_by_key: dict[tuple[str, str | None], Contradiction],
    follows: dict[str, str],
) -> Contradiction | None:
    """This field's own disagreement, or the one config says it shares.

    The *same* Contradiction object is handed back, not a copy of it, so the
    report gives both rows the same disagreement number: render/report_model.py
    identifies a disagreement by what is in it (`_signature`), and a second
    disagreement with the same contents would be printed twice.

    A field's own disagreement always wins. Only a field with nothing of its own
    picks up another's — otherwise a row would be marked with a disagreement
    that is not about the value it shows.
    """
    own = contradiction_by_key.get((rf.field_id, rf.scope))
    if own is not None:
        return own
    leader = follows.get(rf.field_id)
    if leader is None:
        return None
    # The leader's disagreement at the same part of the study, falling back to an
    # unscoped one: a count stated once for the whole study is still the count
    # this row's entry belongs to.
    return contradiction_by_key.get((leader, rf.scope)) or contradiction_by_key.get(
        (leader, None)
    )


def _gate_field(
    rf: ResolvedField, contradiction: Contradiction | None, *, is_budget_driver: bool
) -> ResolvedField:
    if rf.status in _TERMINAL_STATUSES:
        return rf

    if rf.derived_from:
        return rf.model_copy(update={"status": "needs_review"})

    disagreement = contradiction is not None and (
        contradiction.verdict not in DISMISSED_VERDICTS or is_budget_driver
    )
    if disagreement:
        return rf.model_copy(update={"status": "needs_review", "contradiction": contradiction})

    # A budget driver holding several values never confirms on its own. RECONCILE
    # folds a collection field's records into one answer with several members —
    # correctly, since they are parts of one list rather than rival answers — but
    # that also removes the disagreement that used to force a human to look.
    # The field that showed this was ops.monitoring_visits, which came back as
    # ["75", "750", "300"] in run r-listfix-175318: three different kinds of
    # monitoring visit, and which number belongs in a budget is exactly the
    # judgement this pipeline must not make silently. That field was replaced on
    # 2026-10-07 by five single-visit-type rows (change 5 of that day's review),
    # so the example is history — the rule stays for every other budget-driving
    # list field, ip.form among them.
    if is_budget_driver and isinstance(rf.value, list) and len(rf.value) > 1:
        return rf.model_copy(update={"status": "needs_review"})

    if rf.confidence >= CONFIDENCE_CONFIRMED:
        return rf.model_copy(update={"status": "confirmed"})

    return rf.model_copy(update={"status": "needs_review"})
