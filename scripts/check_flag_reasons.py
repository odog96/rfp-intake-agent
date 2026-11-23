"""Check that every flagged row in report.pdf names the GATE rule that actually fired.

    python scripts/check_flag_reasons.py r-20260923-131601

A test cannot replace this one. The question is not whether a string is present
but whether the sentence is *true*: that the reason printed beside a value is the
rule in gate/__init__.py that put that value in needs_review, on a real run with
real values rather than on a hand-built state. So the rule is recomputed here
from extraction.json and config/fields.yaml, in GATE's own order, and never read
from report_model.py — agreement between two independent computations is
evidence, where asking report_model.py what report_model.py decided would be a
tautology.

Reads only. Writes nothing, and makes no model calls.

What it does not check: that the reason reaches the printed page. The rendering
step is covered by tests/render/test_pdf_renderer.py.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rfp_intake.domain.registry import FieldDef  # noqa: E402
from rfp_intake.domain.schemas import Contradiction, ResolvedField, RunState  # noqa: E402
from rfp_intake.gate import (  # noqa: E402
    CONFIDENCE_CONFIRMED,
    documents_state_it,
    entry_listings,
    rival_values,
)
from rfp_intake.render.report_model import build_report_model  # noqa: E402

# A phrase from each reason constant in report_model.py, by the rule name used
# below. Deliberately a fragment rather than the constant itself: importing the
# constants would make this script agree with report_model.py by construction,
# which is the tautology the module docstring rules out.
REASON_PHRASE = {
    "derived": "Worked out by the tool",
    "disagree": "The documents disagree",
    "dismissed": "the passages differ",
    "many_values": "more than one value",
    "low_confidence": "does not state it clearly",
    # RECONCILE's own note is printed as the reason, in its own words: the
    # phrase is read from the note itself (`_note_phrase`).
}


def _note_phrase(note: str) -> str:
    """The longest stretch of RECONCILE's note between document ids, which the
    report swaps for document names: "Listed only in doc-1, not in doc-2."
    gives "Listed only in". An empty phrase would match any printed reason
    (pipeline-rules-reviewer, second reading, 23 November 2026)."""
    pieces = [p.strip(" ,.;:") for p in re.split(r"\bdoc-[0-9a-f]+\b", note)]
    return max(pieces, key=len) or note


def rule_that_fired(
    rf: ResolvedField,
    budget_drivers: set[str],
    fields: dict[str, FieldDef] | None = None,
    listing: dict[str, set[str]] | None = None,
) -> str:
    """Which GATE rule put this value in needs_review, in gate/_gate_field's order.

    Order matters and is GATE's, not a guess: a derived field is flagged before
    its confidence is ever looked at, so a derived field below the threshold is a
    derived flag and not a low-confidence one. Where GATE skips a rule (the
    documents state the row's value; the entries or periods agree), GATE's own
    predicates say so here too.
    """
    fields = fields or {}
    listing = listing or {}
    if rf.derived_from:
        return "derived"
    c = rf.contradiction
    # GATE's DISMISSED_VERDICTS, written out rather than imported for the reason
    # the module docstring gives. "reconcilable" joined "not_a_conflict" when
    # ADJUDICATE learned to call the same answer in other words reconcilable.
    dismissed = c is not None and c.verdict in ("not_a_conflict", "reconcilable")
    if c is not None and (
        not dismissed
        or (rf.field_id in budget_drivers and not documents_state_it(rf, c))
    ):
        # GATE treats a dismissed verdict on a budget driver as a flag of its own
        # kind, and the report names it differently, so split them here too.
        return "dismissed" if dismissed else "disagree"
    fd = fields.get(rf.field_id)
    if rival_values(
        rf, is_budget_driver=rf.field_id in budget_drivers,
        one_answer=fd if fd is not None and fd.one_answer_when_documents_agree else None,
        listing=listing.get(rf.field_id, set()), field_def=fd,
    ):
        return "many_values"
    if rf.review_note:
        return "review_note"
    if rf.confidence < CONFIDENCE_CONFIRMED:
        return "low_confidence"
    return "NO RULE FIRED"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    run_id = sys.argv[1]

    from rfp_intake.domain.registry import get_registry

    path = PROJECT_ROOT / "runs" / run_id / "extraction.json"
    if not path.is_file():
        print(f"No extraction.json in {path.parent}", file=sys.stderr)
        return 1

    doc: dict[str, Any] = json.loads(path.read_text())
    state = RunState(
        run_id=doc["run_id"],
        resolved=[ResolvedField.model_validate(r) for r in doc["resolved_fields"]],
        contradictions=[Contradiction.model_validate(c) for c in doc["contradictions"]],
    )
    registry = get_registry()
    budget_drivers = {f.id for f in registry.fields if f.budget_driver}
    fields = {f.id: f for f in registry.fields}
    listing = entry_listings(
        state.resolved, {f.id: f for f in registry.fields if f.one_answer_when_documents_agree},
    )

    # Pointer rows are excluded: a pointer stands for a whole schedule table and
    # carries that table's own reason, not one value's.
    all_rows = build_report_model(state, registry).all_rows()
    rows = {(row.field_id, row.scope): row for row in all_rows if not row.is_pointer}
    pointers = {row.field_id for row in all_rows if row.is_pointer}

    flagged = [
        rf for rf in state.resolved
        if rf.status == "needs_review" and rf.field_id not in pointers
    ]
    print(f"{run_id}: {len(flagged)} needs_review entries in extraction.json\n")

    wrong = 0
    for rf in flagged:
        rule = rule_that_fired(rf, budget_drivers, fields, listing)
        row = rows.get((rf.field_id, rf.scope))
        if row is None:
            # A whole-study scope ("total", "study") is printed without one, and a
            # disagreement's row is keyed by the scope it prints.
            # The report also starts a scope with a capital letter.
            same = [r for (f, _), r in rows.items() if f == rf.field_id]
            cased = [r for r in same if (r.scope or "").lower() == (rf.scope or "").lower()]
            row = cased[0] if len(cased) == 1 else same[0] if len(same) == 1 else None
        printed = " / ".join(row.reasons) if row else "<no row in the report>"
        phrase = (
            _note_phrase(rf.review_note or "") if rule == "review_note"
            else REASON_PHRASE.get(rule, "\0")
        )
        ok = row is not None and bool(phrase) and phrase in printed
        if not ok:
            wrong += 1
        print(f"{'ok ' if ok else 'BAD'} {rf.field_id:<34} rule={rule:<14} printed={printed}")

    print(f"\n{len(flagged) - wrong}/{len(flagged)} reasons match the rule that fired")
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
