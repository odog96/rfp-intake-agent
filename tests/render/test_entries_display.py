"""The `entries` and `periods` displays of report.pdf: fixes 1, 3 and 7 of the
2026-10-12 brief. The records are the brief's own study, as EXTRACT now returns
them (one JSON object per entry), taken through NORMALIZE, RECONCILE and GATE as
a run takes them. The expected lines are the layouts Oliver approved.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

import pymupdf
import pytest

from rfp_intake.domain.registry import get_registry
from rfp_intake.domain.schemas import (
    Document,
    FieldRecord,
    Provenance,
    ResolvedField,
    RunState,
)
from rfp_intake.render.report_model import ReportModel, Row, build_report_model
from tests.render.test_report_model import assert_nothing_lost

AM, RFP = "doc-62022c6f", "doc-62809b04"
DocKind = Literal["amendment", "rfp"]


@pytest.fixture(autouse=True)
def _real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    get_registry.cache_clear()


def _rec(field_id: str, value: Any, scope: str | None, doc: str, page: int,
         quote: str) -> FieldRecord:
    kind: DocKind = "amendment" if doc == AM else "rfp"
    return FieldRecord(
        field_id=field_id, group="g",
        raw_value=json.dumps(value) if isinstance(value, dict) else value,
        quote=quote, provenance=Provenance(doc_id=doc, doc_kind=kind, page=page),
        confidence=0.95, scope=scope,
    )


def _arms(rfp_placebo_n: int = 130) -> list[FieldRecord]:
    return [
        _rec("design.parts", {
            "name": "NEOD001 plus standard of care", "kind": "arm",
            "dose": "24 mg/kg IV infusion", "schedule": "every 28 days", "n_subjects": 130,
        }, "arm:NEOD001", AM, 15,
            "130 subjects will receive NEOD001 plus standard of care: 24 mg/kg IV infusion "
            "every 28 days"),
        _rec("design.parts", {
            "name": "Placebo plus standard of care", "kind": "arm",
            "dose": "normal saline IV infusion", "schedule": "every 28 days", "n_subjects": 130,
        }, "arm:placebo", AM, 15,
            "130 subjects will receive placebo plus standard of care: normal saline IV "
            "infusion every 28 days"),
        _rec("design.parts", {
            "name": "NEOD001 + SoC", "kind": "arm", "document_label": "Group 1",
            "n_subjects": 130,
        }, "arm:NEOD001", RFP, 3, "Group 1: NEOD001 + SoC, 130 subjects"),
        _rec("design.parts", {
            "name": "Placebo + SoC", "kind": "arm", "document_label": "Group 2",
            "n_subjects": rfp_placebo_n,
        }, "arm:placebo", RFP, 3, f"Group 2: Placebo + SoC, {rfp_placebo_n} subjects"),
        _rec("design.allocation_ratio", "1:1", None, AM, 15, "randomized 1:1"),
        _rec("design.allocation_ratio", "1:1", None, RFP, 3, "randomized 1:1"),
    ]


def _visits() -> list[FieldRecord]:
    f = "visits.frequency_by_period"
    return [
        _rec(f, {"period": "screening", "label": "Screening", "n_visits": 2,
                 "frequency": "2 visits (Day -28 to Day -1; Day -2 or Day -1)"}, None, AM, 26,
             "Screening 2 visits Day -28 to Day -1; Day -2 or Day -1"),
        # Stated out of order, to show the lines follow `period`'s values.
        _rec(f, {"period": "stopped_early", "label": "Stopped early",
                 "frequency": "every 3 months after the early-discontinuation visit"},
             None, AM, 26, "every 3 months after the early-discontinuation visit"),
        _rec(f, {"period": "early_treatment", "label": "Months 1–3",
                 "frequency": "weekly, 4 visits a month (Days 1, 8, 15, 22)"}, None, AM, 26,
             "Months 1–3 weekly Days 1, 8, 15, 22"),
        _rec(f, {"period": "later_treatment", "label": "Month 4 onwards",
                 "frequency": "monthly, Day 1 (every 28 days)"}, None, AM, 26,
             "Month 4 onwards monthly Day 1"),
        _rec(f, {"period": "end_of_treatment", "label": "End of treatment", "n_visits": 1,
                 "frequency": "1 visit, 28–35 days after last dose"}, None, AM, 26,
             "End of treatment 1 visit 28–35 days after last dose"),
    ]


def _timeline() -> list[FieldRecord]:
    f = "timeline.periods"
    return [
        _rec(f, {"component": "recruitment", "label": "Enrolment", "duration": "24 months"},
             None, AM, 15, "Enrolment 24 months"),
        _rec(f, {"component": "screening", "label": "Screening",
                 "duration": "28 days before first dose"}, None, AM, 13,
             "Screening 28 days before first dose"),
        _rec(f, {"component": "treatment", "label": "Treatment and follow-up",
                 "duration": "18 months after enrolment ends"}, None, AM, 15,
             "18 months after enrolment ends"),
        _rec(f, {"component": "end_of_treatment", "label": "End of treatment visit",
                 "duration": "28–35 days after last dose"}, None, AM, 24,
             "End of treatment visit 28–35 days after last dose"),
        _rec(f, {"component": "follow_up", "label": "Follow-up after last dose",
                 "duration": "every 3 months until the last event is adjudicated"},
             None, AM, 28, "every 3 months until the last event is adjudicated"),
    ]


def _durations() -> list[FieldRecord]:
    return [
        _rec("timeline.total_duration", "42 months", "study", AM, 24, "42 months"),
        _rec("timeline.total_duration", "40 months", "study", RFP, 3, "40 months"),
    ]


def _report(records: list[FieldRecord]) -> tuple[RunState, ReportModel]:
    from rfp_intake.gate import gate_node
    from rfp_intake.normalize import normalize_node
    from rfp_intake.reconcile import reconcile_node

    state = RunState(
        run_id="r-1",
        documents=[
            Document(id=AM, path="/runs/r-1/inputs/Amendment.pdf", kind="amendment", pages=120),
            Document(id=RFP, path="/runs/r-1/inputs/RFP.pdf", kind="rfp", pages=6),
        ],
        records=records,
    )
    state.records = normalize_node(state)["records"]
    out = reconcile_node(state)
    state.resolved, state.contradictions = out["resolved"], out["contradictions"]
    # ADJUDICATE's part, written by hand: each disagreement judged a conflict,
    # with no value chosen, as `_handle_conflict` writes one precedence cannot settle.
    for c in state.contradictions:
        c.verdict, c.severity, c.explanation = "conflict", "high", "The documents differ."
        state.resolved.append(ResolvedField(
            field_id=c.field_id, value=None, status="needs_review", confidence=0.0,
            sources=[r.provenance for r in c.records], quote=None,
            scope=c.records[0].scope, contradiction=c,
        ))
    state.resolved = gate_node(state)["resolved"]
    model = build_report_model(state, get_registry())
    assert_nothing_lost(state, get_registry(), model)
    return state, model


def _row(model: ReportModel, field_id: str) -> Row:
    [row] = [r for g in model.groups for r in g.rows if r.field_id == field_id]
    return row


def _lines(row: Row) -> list[str]:
    """The rows report.pdf prints for `row` as lines of text (29 October 2026
    layout): the variable's own lines, then each row under it as "Heading:
    value" (a heading with only lines: the heading, then each line indented),
    and each further row (a disagreement's side, another value) indented."""
    out: list[str] = []
    for t in row.table:
        own = list(t.values) + ([t.alert] if t.alert else [])
        if t.kind == "label":
            out += ["  " * b.indent + b.text for b in t.lines]
            continue
        if t.variable and own:
            out.append(f"{t.variable}: {'; '.join(own)}")
        elif t.variable:
            out.append(t.variable)
        else:
            out += ["  " + v for v in own]
        out += ["  " * (b.indent + 1) + b.text for b in t.lines]
    return out


def _printed(row: Row) -> list[tuple[str, str, str, str]]:
    """Each table row report.pdf prints for `row`: Variable, Value (with any
    red line), Status and Where."""
    return [
        (t.variable, "; ".join(t.values + ([t.alert] if t.alert else [])), t.status, t.where)
        for t in row.table
    ]


def _period_lines(row: Row) -> list[str]:
    """The visit-frequency row's lines. Prompt C's "No schedule table found." is
    a grey note in the Where cell of the variable's own row, which has no
    value, status or pages of its own (29 October 2026)."""
    assert row.table[0].notes[0] == "No schedule table found."
    assert (row.table[0].values, row.table[0].status, row.table[0].where) == ([], "", "")
    return _lines(row)


class TestArms:
    """Fix 1: one block per arm; the notes on them in the Where column (Prompt B item 3)."""

    def test_the_approved_layout(self) -> None:
        _, model = _report(_arms())
        row = _row(model, "design.parts")
        assert [v.text for v in row.values] == ["2 arms, randomized 1:1"]
        assert _lines(row) == [
            "Arm 1 — NEOD001 plus standard of care",
            "  Dose: 24 mg/kg IV infusion, every 28 days",
            "  Subjects: 130",
            "  RFP calls it: Group 1",
            "Arm 2 — Placebo plus standard of care",
            "  Dose: normal saline IV infusion, every 28 days",
            "  Subjects: 130",
            "  RFP calls it: Group 2",
        ]
        # Prompt B item 3 (Angus Gray, 2026-10-13): no line saying what the
        # documents agree on, and the two notes under the Where cell's pages.
        assert row.where_notes == [
            "Dose and schedule are stated in the amendment only.",
            "Other wordings are quoted in Appendix A.",
        ]
        # The approved GATE exception: the documents agree, so the row confirms.
        assert row.status == "confirmed"
        assert row.values[0].sources == "Amendment p.15; RFP p.3"
        # 29 October 2026: each arm is a row of its own under the headline, its
        # name in bold, its lines as the value, its own status and pages.
        assert _printed(row) == [
            ("Cohorts / arms / dose levels / parts", "2 arms, randomized 1:1", "Confirmed",
             "Amendment p.15; RFP p.3"),
            ("Arm 1 — NEOD001 plus standard of care", "", "Confirmed", "Amendment p.15; RFP p.3"),
            ("Arm 2 — Placebo plus standard of care", "", "Confirmed", "Amendment p.15; RFP p.3"),
        ]
        assert row.table[0].notes == row.where_notes

    def test_the_allocation_ratio_has_no_row_of_its_own_and_is_not_lost(self) -> None:
        _, model = _report(_arms())
        assert not [r for g in model.groups for r in g.rows
                    if r.field_id == "design.allocation_ratio"]
        # Its rows are members of the arms row, so they are in all_rows() and
        # Appendix A (assert_nothing_lost has checked both).
        assert "design.allocation_ratio" in {r.field_id for r in model.all_rows()}
        assert any(e.field_id == "design.allocation_ratio" for e in model.evidence)

    def test_a_count_the_documents_differ_on_is_shown_with_each_side_and_reviewed(
        self,
    ) -> None:
        _, model = _report(_arms(rfp_placebo_n=118))
        row = _row(model, "design.parts")
        lines = _lines(row)
        # Each side is a row of its own under the arm, with its own pages.
        i = lines.index("Arm 2 — Placebo plus standard of care")
        assert lines[i + 3:] == [
            "  Subjects: documents differ — 130",
            "  Subjects: documents differ — 118",
        ]
        assert _printed(row)[-2:] == [
            ("", "Subjects: documents differ — 130", "Review", "Amendment p.15"),
            ("", "Subjects: documents differ — 118", "Review", "RFP p.3"),
        ]
        # The arm's own "Subjects:" line is not printed with one side's number.
        assert "  Subjects: 130" not in lines[i:]
        # Nothing is said to be agreed about subjects.
        assert not any("130 subjects per arm" in line for line in lines)
        assert row.status == "needs_review"

    def test_a_dose_the_documents_differ_on_is_shown_with_each_side_and_reviewed(
        self,
    ) -> None:
        # pipeline-rules-reviewer, 2026-10-12: GATE reviews a dose two documents
        # state differently, so the row must say why, not print one dose.
        records = _arms()
        records[2].raw_value = json.dumps(
            dict(json.loads(str(records[2].raw_value)), dose="12 mg/kg"),
        )
        _, model = _report(records)
        row = _row(model, "design.parts")
        lines = _lines(row)
        assert ("", "Dose: documents differ — 24 mg/kg IV infusion", "Review",
                "Amendment p.15") in _printed(row)
        assert ("", "Dose: documents differ — 12 mg/kg", "Review", "RFP p.3") in _printed(row)
        assert "  Dose: 24 mg/kg IV infusion, every 28 days" not in lines
        # The other arm's dose is one document's alone and is printed as before.
        assert "  Dose: normal saline IV infusion, every 28 days" in lines
        assert row.status == "needs_review"

    def test_an_arm_one_document_does_not_list_is_named_and_reviewed(self) -> None:
        records = [r for r in _arms() if not (r.provenance.doc_id == RFP
                                              and r.scope == "arm:placebo")]
        _, model = _report(records)
        row = _row(model, "design.parts")
        # RECONCILE's note, as Flagged for review words it, in grey under the
        # arm's own pages: the RFP's page is not one of them.
        [arm_2] = [t for t in row.table if t.variable.startswith("Arm 2")]
        assert (arm_2.status, arm_2.where) == ("Review", "Amendment p.15")
        assert "Listed only in Amendment, not in RFP." in arm_2.notes
        assert row.status == "needs_review"

    def test_text_entries_from_an_older_run_keep_the_default_rows(self) -> None:
        # Records saved before fix 1 hold text, not JSON objects: no statements,
        # so the default one-row-per-scope layout, as the older runs printed it.
        records = [_rec("design.parts", "Group 1: NEOD001 + SoC", "arm:NEOD001", RFP, 3,
                        "Group 1: NEOD001 + SoC")]
        _, model = _report(records)
        [row] = [r for r in model.all_rows() if r.field_id == "design.parts"]
        assert not row.block_lines
        assert row.scope == "Arm: NEOD001"


class TestVisitFrequency:
    """Fix 3: one line per stretch of the study, in the order of `period`.

    None of these states holds a Schedule of Events READ_SCHEDULE could count,
    so since Prompt C each row first says none was found."""

    def test_the_approved_layout(self) -> None:
        _, model = _report(_visits())
        row = _row(model, "visits.frequency_by_period")
        # No page reference in the Value cell (29 October 2026): each stretch
        # is a row with its heading in bold and its own pages in Where.
        assert [v.text for v in row.values] == [""]
        assert _period_lines(row) == [
            "Screening: 2 visits (Day -28 to Day -1; Day -2 or Day -1)",
            "Early treatment (Months 1–3): Weekly, 4 visits a month (Days 1, 8, 15, 22)",
            "Later treatment (Month 4 onwards): Monthly, Day 1 (every 28 days)",
            "End of treatment: 1 visit, 28–35 days after last dose",
            "Stopped early: Every 3 months after the early-discontinuation visit "
            "(budgeted separately)",
        ]
        assert [where for *_, where in _printed(row)] == [""] + ["Amendment p.26"] * 5
        # GATE's one reason for the list, printed once, under the first stretch.
        notes = [n for t in row.table for n in t.notes]
        assert notes == ["No schedule table found.", "Budget driver holding more than one value."]
        assert row.table[1].notes == ["Budget driver holding more than one value."]

    def test_a_stretch_with_only_a_count_prints_the_count(self) -> None:
        _, model = _report([_rec("visits.frequency_by_period",
                                 {"period": "screening", "label": "Screening", "n_visits": 2},
                                 None, AM, 26, "Screening: 2 visits")])
        assert _period_lines(_row(model, "visits.frequency_by_period")) == ["Screening: 2 visits"]

    def test_stretches_of_one_period_make_one_line(self) -> None:
        # Oliver, 2026-10-12: run r-20261012-stage4check2 printed a line per
        # document label, 21 in all. A period is one line, worded as its most
        # confident record words it; every other record is in Appendix A.
        records = _visits() + [
            _rec("visits.frequency_by_period",
                 {"period": "later_treatment", "label": "All Other Months Day 1",
                  "frequency": "Day 1 of all other months"}, None, AM, 26,
                 "Day 1 of all other months"),
            _rec("visits.frequency_by_period",
                 {"period": "later_treatment", "label": "Month 3 onwards",
                  "frequency": "Every 28 days"}, None, AM, 13, "Every 28 days"),
        ]
        records[-2].confidence = records[-1].confidence = 0.8
        _, model = _report(records)
        row = _row(model, "visits.frequency_by_period")
        lines = _period_lines(row)
        assert [line for line in lines if line.startswith("Later treatment")] == [
            "Later treatment (Month 4 onwards): Monthly, Day 1 (every 28 days)",
        ]
        assert "Other wordings are quoted in Appendix A." not in lines
        assert row.where_notes == ["No schedule table found.",
                                   "Other wordings are quoted in Appendix A."]
        quotes = {e.quote for e in model.evidence if e.field_id == "visits.frequency_by_period"}
        assert {"Day 1 of all other months", "Every 28 days"} <= quotes

    @pytest.mark.parametrize(("period", "label", "line"), [
        ("end_of_treatment", "END OF TREATMENT", "End of treatment: 1 visit"),
        # pipeline-rules-reviewer, 2026-10-12: not "Follow up (Follow-up)".
        ("follow_up", "Follow-up", "Follow up: 1 visit"),
    ])
    def test_a_label_that_repeats_the_period_is_printed_once(
        self, period: str, label: str, line: str,
    ) -> None:
        _, model = _report([_rec("visits.frequency_by_period",
                                 {"period": period, "label": label,
                                  "frequency": "1 visit"}, None, AM, 26, "1 visit")])
        assert _period_lines(_row(model, "visits.frequency_by_period")) == [line]

    def test_a_stretch_with_no_label_is_named_by_its_period(self) -> None:
        _, model = _report([_rec("visits.frequency_by_period",
                                 {"period": "early_treatment", "frequency": "weekly"},
                                 None, AM, 26, "weekly")])
        assert _period_lines(_row(model, "visits.frequency_by_period")) == [
            "Early treatment: Weekly"]

    def test_a_schedule_for_one_part_is_under_its_heading(self) -> None:
        records = _visits()[:1] + [_rec(
            "visits.frequency_by_period",
            {"period": "early_treatment", "label": "Weeks 1–4", "frequency": "weekly"},
            "part:Part B", AM, 30, "Part B Weeks 1–4 weekly",
        )]
        _, model = _report(records)
        row = _row(model, "visits.frequency_by_period")
        assert _period_lines(row) == [
            "Screening: 2 visits (Day -28 to Day -1; Day -2 or Day -1)",
            "Early treatment (Weeks 1–4): Weekly",
        ]
        # The part in grey under its heading, and its own page.
        part_b = row.table[2]
        assert (part_b.scope, part_b.where) == ("Part: Part B", "Amendment p.30")


def _whole_study(row: Row) -> list[tuple[str, str, str, str]]:
    """The printed rows from the "Whole study" line of the patient timeline on."""
    printed = _printed(row)
    i = next(i for i, p in enumerate(printed) if p[0] == "Whole study")
    return printed[i:]


class TestTimeline:
    """Fix 7: the components; since 29 October 2026 (Angus Gray) the whole
    study's duration and the time window to budget for are rows under the
    patient timeline (`shown_under`), each with its own status and pages."""

    def test_the_approved_layout_with_the_duration_disagreement(self) -> None:
        state, model = _report(_timeline() + _durations())
        row = _row(model, "timeline.periods")
        [no] = [d.no for d in model.decisions if d.field_id == "timeline.total_duration"]
        # The label row has no value, status or pages of its own: no page
        # reference is left in a Value cell.
        assert [v.text for v in row.values] == [""]
        assert _printed(row)[0] == ("Patient timeline", "", "", "")
        # Every period `always` lists, in the order of `component`, a document
        # stating it or not; then the periods it does not list (`other`), each
        # under its own name; then the two variables shown under the timeline.
        assert _lines(row) == [
            "Screening: 28 days before first dose",
            "Run-in: Not found in the documents",
            "Treatment: 18 months after enrolment ends",
            "Follow-up: Every 3 months until the last event is adjudicated",
            "Open-label extension: Not found in the documents",
            "Long-term follow-up: Not found in the documents",
            "Overall patient participation: Not found in the documents",
            "Enrolment: 24 months",
            "End of treatment visit: 28–35 days after last dose",
            "Time window to budget for: Not found in the documents",
            f"Whole study: Documents disagree — see Disagreement {no}",
            '  Amendment: 42 months — "42 months"',
            '  RFP: 40 months — "40 months"',
        ]
        # Each period's own pages; a stretch no document gives has none.
        printed = _printed(row)
        # Confirmed: every period here comes from one document and none is worded
        # two ways, so GATE reads the display as one answer (gate/__init__.py,
        # _periods_agree).
        assert printed[1] == (
            "Screening", "28 days before first dose", "Confirmed", "Amendment p.13")
        assert printed[2] == ("Run-in", "Not found in the documents", "Review", "")
        # The disagreement's sides are rows right under "Whole study", each
        # with its own pages (Angus Gray: "and then the information about
        # where it's found the disagreement").
        assert _whole_study(row) == [
            ("Whole study", f"Documents disagree — see Disagreement {no}", "Review", ""),
            ("", 'Amendment: 42 months — "42 months"', "", "Amendment p.24"),
            ("", 'RFP: 40 months — "40 months"', "", "RFP p.3"),
        ]
        # The whole study's disagreement keeps its number and its own label.
        [decision] = [d for d in model.decisions if d.no == no]
        assert decision.label == "Whole study duration"
        # Neither variable shown under the timeline has a row of its own.
        assert not [r for g in model.groups for r in g.rows
                    if r.field_id in ("timeline.total_duration", "timeline.budget_window")]

    def test_every_wording_of_the_whole_study_is_a_side_with_its_pages(self) -> None:
        # Run r-20261012-stage4check2 printed four wordings on one line; each
        # is now a row of its own, beside the page that states it.
        long = "approximately 42 months, including the enrollment and treatment periods"
        records = _timeline() + _durations() + [
            _rec("timeline.total_duration", long, "study", AM, 15, long),
        ]
        _, model = _report(records)
        row = _row(model, "timeline.periods")
        sides = _whole_study(row)[1:]
        assert ("", f"Amendment: {long}", "", "Amendment p.15") in sides
        assert ("", 'RFP: 40 months — "40 months"', "", "RFP p.3") in sides
        assert ("", "Amendment: 42 months", "", "Amendment p.24") in sides
        assert len(sides) == 3

    @pytest.mark.parametrize("rfp_says", ["3.5 years", "about three and a half years"])
    def test_without_one_unit_each_side_is_printed_as_it_reads(self, rfp_says: str) -> None:
        # Amounts of two units, or a document with no amount at all: nothing is
        # converted or left out, each side is printed as it reads.
        records = _timeline() + [
            _rec("timeline.total_duration", "42 months", "study", AM, 24, "42 months"),
            _rec("timeline.total_duration", rfp_says, "study", RFP, 3, rfp_says),
        ]
        _, model = _report(records)
        assert _whole_study(_row(model, "timeline.periods"))[1:] == [
            ("", 'Amendment: 42 months — "42 months"', "", "Amendment p.24"),
            ("", f'RFP: {rfp_says} — "{rfp_says}"', "", "RFP p.3"),
        ]

    @pytest.mark.parametrize("wording", [
        "approximately 42 months", "42 months to 48 months", "3 years 6 months",
    ])
    def test_a_wording_is_printed_in_full(self, wording: str) -> None:
        # Prompt B item 7: nothing is cut to an amount on a side.
        records = _timeline() + [
            _rec("timeline.total_duration", wording, "study", AM, 24, wording),
            _rec("timeline.total_duration", "40 months", "study", RFP, 3, "40 months"),
        ]
        _, model = _report(records)
        assert _whole_study(_row(model, "timeline.periods"))[1] == (
            "", f'Amendment: {wording} — "{wording}"', "", "Amendment p.24",
        )

    def test_a_document_with_two_values_gives_each_with_its_page(self) -> None:
        # Two wordings that read differently are two values; neither is left out.
        records = _timeline() + [
            _rec("timeline.total_duration", "about 3.5 years", "study", AM, 15,
                 "about 3.5 years"),
            _rec("timeline.total_duration", "about 4 years", "study", AM, 24, "about 4 years"),
            _rec("timeline.total_duration", "40 months", "study", RFP, 3, "40 months"),
        ]
        _, model = _report(records)
        sides = _whole_study(_row(model, "timeline.periods"))[1:]
        assert ("", "Amendment: about 3.5 years", "", "Amendment p.15") in sides
        assert ("", "Amendment: about 4 years", "", "Amendment p.24") in sides
        assert ("", 'RFP: 40 months — "40 months"', "", "RFP p.3") in sides

    def test_without_a_disagreement_the_whole_study_row_gives_its_value(self) -> None:
        _, model = _report(_timeline() + _durations()[:1])
        assert _whole_study(_row(model, "timeline.periods")) == [
            ("Whole study", "42 months", "Confirmed", "Amendment p.24"),
        ]

    def test_a_stated_start_and_end_are_printed_beside_the_duration(self) -> None:
        _, model = _report([_rec(
            "timeline.periods",
            {"component": "treatment", "label": "Treatment", "duration": "24 months",
             "start": "Month 1", "end": "Month 25"},
            None, AM, 15, "Treatment 24 months, Month 1 to Month 25",
        )])
        # Angus Gray's model line: "28 days, Day -28 to Day 1".
        assert "Treatment: 24 months, Month 1 to Month 25" in _lines(
            _row(model, "timeline.periods"))

    def test_each_documents_own_quote_is_in_appendix_a(self) -> None:
        _, model = _report(_arms())
        quotes = {e.quote for e in model.evidence if e.field_id == "design.parts"}
        assert "Group 1: NEOD001 + SoC, 130 subjects" in quotes
        assert "Group 2: Placebo + SoC, 130 subjects" in quotes

    def test_two_documents_wording_a_period_differently_cite_only_the_shown_one(self) -> None:
        records = _timeline()[:1] + [_rec(
            "timeline.periods",
            {"component": "recruitment", "label": "Enrolment", "duration": "20 months"},
            None, RFP, 3, "Enrolment 20 months",
        )]
        _, model = _report(records)
        row = _row(model, "timeline.periods")
        # `duration` is text: two wordings, not a disagreement, both in
        # Appendix A. RFP p.3 says 20 months, so it is not cited beside 24.
        assert ("Enrolment", "24 months", "Review", "Amendment p.15") in _printed(row)
        assert row.where_notes == ["Other wordings are quoted in Appendix A."]
        quotes = {e.quote for e in model.evidence if e.field_id == "timeline.periods"}
        assert "Enrolment 20 months" in quotes

    def test_two_periods_the_registry_does_not_list_are_two_rows(self) -> None:
        # An enrolment period and an end of treatment visit are both `other`:
        # each is its own row with its own page, not one row citing both.
        _, model = _report(_timeline())
        printed = _printed(_row(model, "timeline.periods"))
        assert ("Enrolment", "24 months", "Confirmed", "Amendment p.15") in printed
        assert ("End of treatment visit", "28–35 days after last dose", "Confirmed",
                "Amendment p.24") in printed

    def test_a_variable_shown_under_the_timeline_with_no_value_still_has_its_row(
        self,
    ) -> None:
        _, model = _report(_timeline())
        printed = _printed(_row(model, "timeline.periods"))
        assert printed[-2:] == [
            ("Time window to budget for", "Not found in the documents", "Review", ""),
            ("Whole study", "Not found in the documents", "Review", ""),
        ]


def test_extraction_json_keeps_the_statements_so_a_rerender_prints_the_same_rows() -> None:
    # Run r-20261012-stage4check's extraction.json had no statements, so a
    # re-render printed these three fields as default rows.
    from rfp_intake.render.json_renderer import build_extraction_document

    state, model = _report(_arms() + _visits() + _timeline() + _durations())
    saved = json.loads(json.dumps(build_extraction_document(state, get_registry())))
    again = RunState(
        run_id=state.run_id, documents=state.documents,
        resolved=[ResolvedField.model_validate(r) for r in saved["resolved_fields"]],
        contradictions=state.contradictions,
    )
    rebuilt = build_report_model(again, get_registry())
    for field_id in ("design.parts", "visits.frequency_by_period", "timeline.periods"):
        assert _lines(_row(rebuilt, field_id)) == _lines(_row(model, field_id)), field_id
        assert _lines(_row(rebuilt, field_id))


def test_a_row_taller_than_a_page_continues_on_the_next_page() -> None:
    # Run r-20261012-stage4check wrote no report: its visit-frequency row held
    # 21 stretches, 794 points against a 686-point frame, and ReportLab raised
    # LayoutError.
    from rfp_intake.render.pdf_renderer import build_report_pdf

    # One part each, as visits group by period and a period is one line.
    long = " ".join(["Day 1, Days 8, 15 and 22, each at the study site"] * 4)
    records = [_rec("visits.frequency_by_period",
                    {"period": "early_treatment", "label": f"Month {n}", "frequency": long},
                    f"part:Part {n}", AM, 26, f"Month {n} {long}") for n in range(1, 41)]
    state, _ = _report(records)
    pdf = build_report_pdf(state, get_registry(), generated_at="2026-10-12")
    doc = pymupdf.open(stream=pdf, filetype="pdf")  # type: ignore[no-untyped-call]
    pages = [page.get_text() for page in doc]  # type: ignore[attr-defined]
    text = " ".join("\n".join(pages).split())
    # Every stretch, not only the first and last, so a split that dropped the
    # middle of the variable would fail. Since 29 October 2026 each stretch is
    # a table row of its own, its heading in the Variable column.
    for n in range(1, 41):
        assert f"Early treatment (Month {n}) Part: Part {n} Day 1" in text, n
    # And the variable did run onto a later page than the one it starts on (a
    # heading may wrap in the Variable column, so each page's words are joined).
    flat = [" ".join(p.split()) for p in pages]
    first = next(i for i, p in enumerate(flat) if "(Month 2)" in p)
    assert any("(Month 40)" in p for p in flat[first + 1:])


@pytest.mark.parametrize("tall", [0, 20, 59])
@pytest.mark.parametrize("start", [0, 200, 470, 600])
def test_only_a_row_taller_than_a_page_is_cut(start: int, tall: int) -> None:
    # plan-conformance-reviewer and pipeline-rules-reviewer, 2026-10-12: with
    # ReportLab's splitInRow on for a whole table, a row is cut at every page
    # break and its label left on the page before. Only the one row that fits
    # no page may be cut, wherever on the page the table starts and whether
    # that row is the table's first, a middle or its last row.
    import io

    from reportlab.lib.pagesizes import LETTER
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    from rfp_intake.render.pdf_renderer import (
        _BOTTOM_MARGIN,
        _FRAME_HEIGHT,
        _MAIN_COLS,
        _MARGIN,
        _styles,
        _table,
    )

    # The report's own page geometry, less the frame's 6-point padding each side.
    page = SimpleDocTemplate(io.BytesIO(), pagesize=LETTER, topMargin=_MARGIN,
                             bottomMargin=_BOTTOM_MARGIN)
    assert page.height - 12 == _FRAME_HEIGHT
    cell = _styles()["cell"]

    def row(n: int, lines: int) -> list[object]:
        value = Paragraph("<br/>".join(f"R{n}x{i}" for i in range(lines)), cell)
        return [Paragraph(f"Label{n}", cell), value, "", "", ""]

    # Row `tall` is 120 lines, about 1,200 points; the rest are 9 lines each.
    data = [["Variable", "Value", "", "", ""]] + [row(n, 120 if n == tall else 9)
                                                  for n in range(60)]
    buf = io.BytesIO()
    report_page = SimpleDocTemplate(buf, pagesize=LETTER, topMargin=_MARGIN,
                                    bottomMargin=_BOTTOM_MARGIN)
    report_page.build([Spacer(1, start), _table(data, _MAIN_COLS)])
    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")  # type: ignore[no-untyped-call]
    pages = [p.get_text() for p in doc]  # type: ignore[attr-defined]
    cut = [n for n in range(60) if sum(f"R{n}x" in p for p in pages) > 1]
    assert cut == [tall]
    # Nothing is lost: every line of every row is printed.
    text = "\n".join(pages)
    assert all(f"R{n}x8\n" in text for n in range(60))
    assert f"R{tall}x119" in text


def test_the_pdf_prints_the_three_rows() -> None:
    from rfp_intake.render.pdf_renderer import build_report_pdf

    state, _ = _report(_arms() + _visits() + _timeline() + _durations())
    pdf = build_report_pdf(state, get_registry(), generated_at="2026-10-12")
    doc = pymupdf.open(stream=pdf, filetype="pdf")  # type: ignore[no-untyped-call]
    text = " ".join("\n".join(page.get_text() for page in doc).split())  # type: ignore[attr-defined]
    for said in (
        "2 arms, randomized 1:1",
        "Arm 1 — NEOD001 plus standard of care",
        "RFP calls it: Group 2",
        "Dose and schedule are stated in the amendment only.",
        "Stopped early Every 3 months after the early-discontinuation visit "
        "(budgeted separately)",
        "Patient timeline",
        "Whole study Documents disagree — see Disagreement",
        'Amendment: 42 months — "42 months"',
    ):
        assert said in text, said
    # No page reference is printed inside a value, and no confidence figure.
    assert "(Amendment p." not in text
    assert "%" not in text


class TestLeadingAmount:
    """Which wordings start with an amount, for the whole-study line."""

    @pytest.mark.parametrize(("value", "words"), [
        ("42 months (total study duration)", "42 months"),
        ("42 months total study duration", "42 months"),
        ("3.5 years from first patient in", "3.5 years"),
        # One amount, whatever else the wording says: "1 visit" is no amount.
        ("42 months, with 1 visit per site", "42 months"),
        ({"n": 40, "unit": "months"}, "40 months"),
    ])
    def test_an_amount_is_read(self, value: Any, words: str) -> None:
        from rfp_intake.render.report_model import _leading_amount

        record = _rec("timeline.total_duration", value, "study", AM, 24, "q")
        record.value = value
        found = _leading_amount(record)
        assert found is not None and found[1] == words

    @pytest.mark.parametrize("value", [
        "approximately 42 months",
        "the study lasts 42 months",
        "1 visit per site",
        "3.5-4 years",
        # Prompt B item 7: a wording with two amounts stays in full.
        "42 months to 48 months",
        "3 years 6 months",
        "42 months, plus 6 months of follow-up",
        "42 months (24-month accrual period + 18-month treatment/follow-up period)",
    ])
    def test_anything_else_is_not(self, value: str) -> None:
        from rfp_intake.render.report_model import _leading_amount

        record = _rec("timeline.total_duration", value, "study", AM, 24, "q")
        record.value = value
        assert _leading_amount(record) is None


def test_an_arm_line_breaks_only_between_items_and_under_its_own_start() -> None:
    # Prompt B item 3 (Angus Gray, 2026-10-13): run r-20261013-prompt-c broke
    # "once every 28 days" after "every" and carried it on at the column's edge.
    from rfp_intake.render.pdf_renderer import _MAIN_COLS, build_report_pdf

    records = _arms()
    for r in records[:2]:
        entry = json.loads(str(r.raw_value))
        # Long enough to need a second line in the Value column.
        r.raw_value = json.dumps(dict(entry, dose=f"{entry['dose']} over 60 minutes",
                                      schedule="once every 28 days"))
    state, _ = _report(records)
    pdf = build_report_pdf(state, get_registry(), generated_at="2026-10-16")
    doc = pymupdf.open(stream=pdf, filetype="pdf")  # type: ignore[no-untyped-call]
    [page] = [p for p in doc if "Arm 1" in p.get_text()]  # type: ignore[attr-defined]
    words = page.get_text("words")
    lines: dict[tuple[int, int], list[tuple[float, str]]] = {}
    for x0, _, _, _, word, block, line, _ in words:
        lines.setdefault((block, line), []).append((x0, word))
    texts = {" ".join(w for _, w in ws): ws[0][0] for ws in lines.values()}
    # Each arm's dose is one item, and the schedule the next, under it.
    dose = next(t for t in texts if t.startswith("Dose: 24"))
    assert dose == "Dose: 24 mg/kg IV infusion over 60 minutes,"
    assert "once every 28 days" in texts
    assert abs(texts["once every 28 days"] - texts[dose]) < 0.5
    # The notes are in the Where column, the line on what the documents agree on gone.
    where = next(x for t, x in texts.items() if t.startswith("Dose and schedule"))
    assert where > page.rect.width - 72 * 0.6 - (sum(_MAIN_COLS) - sum(_MAIN_COLS[:3])) - 1
    assert not any("agree on" in t for t in texts)
