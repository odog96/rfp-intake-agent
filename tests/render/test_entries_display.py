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
        _rec(f, {"period": "after_stopping_early", "label": "Stopped early",
                 "frequency": "every 3 months after the early-discontinuation visit"},
             None, AM, 26, "every 3 months after the early-discontinuation visit"),
        _rec(f, {"period": "treatment", "label": "Months 1–3",
                 "frequency": "weekly, 4 visits a month (Days 1, 8, 15, 22)"}, None, AM, 26,
             "Months 1–3 weekly Days 1, 8, 15, 22"),
        _rec(f, {"period": "treatment", "label": "Month 4 onwards",
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
    return ["  " * b.indent + b.text for b in row.block_lines]


class TestArms:
    """Fix 1: one block per arm, and what the documents agree on under them."""

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
            "Both documents agree on 2 arms and 130 subjects per arm.",
            "Dose and schedule are stated in the amendment only.",
            "Other wordings are quoted in Appendix A.",
        ]
        # The approved GATE exception: the documents agree, so the row confirms.
        assert row.status == "confirmed"
        assert row.values[0].sources == "Amendment p.15; RFP p.3"

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
        assert "  Subjects: documents differ — 130 (Amendment p.15); 118 (RFP p.3)" in lines
        # The arm's own "Subjects:" line is not printed with one side's number.
        i = lines.index("Arm 2 — Placebo plus standard of care")
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
        assert ("  Dose: documents differ — 24 mg/kg IV infusion (Amendment p.15); "
                "12 mg/kg (RFP p.3)") in lines
        assert "  Dose: 24 mg/kg IV infusion, every 28 days" not in lines
        # The other arm's dose is one document's alone and is printed as before.
        assert "  Dose: normal saline IV infusion, every 28 days" in lines
        assert row.status == "needs_review"

    def test_an_arm_one_document_does_not_list_is_named_and_reviewed(self) -> None:
        records = [r for r in _arms() if not (r.provenance.doc_id == RFP
                                              and r.scope == "arm:placebo")]
        _, model = _report(records)
        row = _row(model, "design.parts")
        lines = _lines(row)
        assert "  Listed only in Amendment, not in RFP." in lines
        assert not any(line.startswith("Both documents agree") for line in lines)
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
    """Fix 3: one line per stretch of the study, in the order of `period`."""

    def test_the_approved_layout(self) -> None:
        _, model = _report(_visits())
        row = _row(model, "visits.frequency_by_period")
        assert [v.text for v in row.values] == ["(Amendment p.26)"]
        assert _lines(row) == [
            "Screening: 2 visits (Day -28 to Day -1; Day -2 or Day -1)",
            "Months 1–3: weekly, 4 visits a month (Days 1, 8, 15, 22)",
            "Month 4 onwards: monthly, Day 1 (every 28 days)",
            "End of treatment: 1 visit, 28–35 days after last dose",
            "Stopped early: every 3 months after the early-discontinuation visit "
            "(budgeted separately)",
        ]

    def test_a_stretch_with_only_a_count_prints_the_count(self) -> None:
        _, model = _report([_rec("visits.frequency_by_period",
                                 {"period": "screening", "label": "Screening", "n_visits": 2},
                                 None, AM, 26, "Screening: 2 visits")])
        assert _lines(_row(model, "visits.frequency_by_period")) == ["Screening: 2 visits"]

    def test_a_schedule_for_one_part_is_under_its_heading(self) -> None:
        records = _visits()[:1] + [_rec(
            "visits.frequency_by_period",
            {"period": "treatment", "label": "Weeks 1–4", "frequency": "weekly"},
            "part:Part B", AM, 30, "Part B Weeks 1–4 weekly",
        )]
        _, model = _report(records)
        assert _lines(_row(model, "visits.frequency_by_period")) == [
            "Screening: 2 visits (Day -28 to Day -1; Day -2 or Day -1)",
            "Part: Part B:",
            "  Weeks 1–4: weekly",
        ]


class TestTimeline:
    """Fix 7: the components, and the whole study sent to its own row."""

    def test_the_approved_layout_with_the_duration_disagreement(self) -> None:
        state, model = _report(_timeline() + _durations())
        row = _row(model, "timeline.periods")
        [no] = [d.no for d in model.decisions if d.field_id == "timeline.total_duration"]
        assert [v.text for v in row.values] == ["(Amendment p.13, 15, 24, 28)"]
        assert _lines(row) == [
            "Screening: 28 days before first dose",
            "Enrolment: 24 months",
            "Treatment and follow-up: 18 months after enrolment ends",
            "End of treatment visit: 28–35 days after last dose",
            "Follow-up after last dose: every 3 months until the last event is adjudicated",
            f"Whole study: see Overall study duration (42 months vs 40 months, "
            f"Disagreement {no})",
        ]

    def test_without_a_disagreement_the_whole_study_line_only_points(self) -> None:
        _, model = _report(_timeline() + _durations()[:1])
        assert _lines(_row(model, "timeline.periods"))[-1] == (
            "Whole study: see Overall study duration"
        )

    def test_a_stated_start_and_end_are_printed_beside_the_duration(self) -> None:
        _, model = _report([_rec(
            "timeline.periods",
            {"component": "treatment", "label": "Treatment", "duration": "24 months",
             "start": "Month 1", "end": "Month 25"},
            None, AM, 15, "Treatment 24 months, Month 1 to Month 25",
        )])
        assert _lines(_row(model, "timeline.periods"))[0] == (
            "Treatment: 24 months (Month 1 to Month 25)"
        )

    def test_each_documents_own_quote_is_in_appendix_a(self) -> None:
        _, model = _report(_arms())
        quotes = {e.quote for e in model.evidence if e.field_id == "design.parts"}
        assert "Group 1: NEOD001 + SoC, 130 subjects" in quotes
        assert "Group 2: Placebo + SoC, 130 subjects" in quotes

    def test_two_documents_differing_on_a_component_show_both(self) -> None:
        records = _timeline()[:1] + [_rec(
            "timeline.periods",
            {"component": "recruitment", "label": "Enrolment", "duration": "20 months"},
            None, RFP, 3, "Enrolment 20 months",
        )]
        _, model = _report(records)
        lines = _lines(_row(model, "timeline.periods"))
        # `duration` is text: two wordings, not a disagreement, both in Appendix A.
        assert lines[0] == "Enrolment: 24 months"
        assert "Other wordings are quoted in Appendix A." in lines


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
        "Both documents agree on 2 arms and 130 subjects per arm.",
        "Stopped early: every 3 months after the early-discontinuation visit "
        "(budgeted separately)",
        "Whole study: see Overall study duration (42 months vs 40 months, Disagreement",
    ):
        assert said in text, said
