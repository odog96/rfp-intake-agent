"""The `breakdown` display of report.pdf: fix 10 and the CRF half of fix 11 of
the 2026-10-12 brief, stage 5 of that day's plan. The records are the brief's
own study, taken through NORMALIZE, RECONCILE and GATE as a run takes them
(tests/render/test_entries_display.py:_report). The expected lines are the
layouts Oliver approved.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from rfp_intake.domain.registry import get_registry
from rfp_intake.domain.schemas import FieldRecord
from tests.render.test_entries_display import (
    AM,
    RFP,
    _arms,
    _lines,
    _real_registry,  # noqa: F401 — the autouse fixture, so these tests read config/fields.yaml
    _rec,
    _report,
    _row,
)

N = "ops.subjects_total"
CRF = "ops.crf_pages"


def _subjects(per_arm: int = 130, arm_scope: str = "arm:NEOD001") -> list[FieldRecord]:
    return [
        _rec(N, "260", "total", AM, 43, "A total of 260 subjects will be randomized"),
        _rec(N, "260", "total", RFP, 4, "Total subjects: 260"),
        _rec(N, "130", arm_scope, AM, 15, "130 subjects will receive NEOD001"),
        _rec(N, "130", "arm:placebo", AM, 15, "130 subjects will receive placebo"),
        _rec(N, str(per_arm), "per arm", RFP, 4, f"{per_arm} per arm"),
    ]


class TestSubjects:
    """Fix 10: the total, the arms as "Cohorts / arms / dose levels / parts"
    names them, and the total again, every figure as a document states it."""

    def test_the_approved_layout(self) -> None:
        _, model = _report(_arms() + _subjects())
        row = _row(model, N)
        assert [v.text for v in row.values] == ["260 (130 per arm, randomized 1:1)"]
        assert _lines(row) == [
            "Arm 1 — NEOD001 plus standard of care: 130",
            "Arm 2 — Placebo plus standard of care: 130",
            "Total: 260",
        ]
        assert row.status == "confirmed"
        # The figures' pages and the ratio's, which the headline states.
        assert row.values[0].sources == "Amendment p.15, 43; RFP p.3, 4"
        # One row: "per arm" is not a row of its own any more.
        assert len([r for g in model.groups for r in g.rows if r.field_id == N]) == 1

    def test_an_arm_scoped_the_old_way_is_still_its_arm(self) -> None:
        # Run r-20261012-stage4check3 scoped the arm figures "cohort:NEOD001 arm".
        _, model = _report(_arms() + _subjects(arm_scope="cohort:NEOD001 arm"))
        assert "Arm 1 — NEOD001 plus standard of care: 130" in _lines(_row(model, N))

    def test_a_per_arm_figure_the_arms_do_not_agree_with_is_shown(self) -> None:
        _, model = _report(_arms() + _subjects(per_arm=120))
        row = _row(model, N)
        assert [v.text for v in row.values] == ["260 (randomized 1:1)"]
        assert ("Per arm: 120 — does not agree with the arms' own figures above"
                in _lines(row))

    def test_a_statistical_minimum_is_its_own_line_and_not_the_total(self) -> None:
        records = _arms() + _subjects() + [_rec(
            N, "236", "statistical minimum", AM, 92,
            "236 subjects are needed to detect a 20% reduction",
        )]
        _, model = _report(records)
        row = _row(model, N)
        assert "Statistical minimum: 236" in _lines(row)
        assert row.values[0].text == "260 (130 per arm, randomized 1:1)"
        assert row.status == "confirmed"
        # The Where cell is the block's: every page states a figure on one of
        # its lines, as p.15 states the arms' (pipeline-rules-reviewer, stage 5).
        assert row.values[0].sources == "Amendment p.15, 43, 92; RFP p.3, 4"

    def test_documents_differing_on_the_total_show_both_sides(self) -> None:
        records = _arms() + _subjects()
        records[-4] = _rec(N, "250", "total", RFP, 4, "Total subjects: 250")
        _, model = _report(records)
        row = _row(model, N)
        assert row.decision_no is not None
        assert row.status == "needs_review"
        # 29 October 2026: the total is its own row under the variable's, in
        # bold, saying the documents disagree; each side is a row under it with
        # its own pages in the Where column.
        total, *sides = [t for t in row.table if t.kind in ("part", "side")]
        assert (total.kind, total.variable, total.status) == ("part", "Total", "Review")
        assert total.alert == f"Documents disagree — see Disagreement {row.decision_no}"
        assert total.where == ""
        assert [(t.kind, t.values, t.where) for t in sides] == [
            ("side", ['Amendment: 260 — "A total of 260 subjects will be randomized"'],
             "Amendment p.43"),
            ("side", ['RFP: 250 — "Total subjects: 250"'], "RFP p.4"),
        ]

    def test_no_total_is_worked_out_from_the_arms(self) -> None:
        records = [r for r in _arms() + _subjects() if r.scope != "total"]
        _, model = _report(records)
        row = _row(model, N)
        assert row.values[0].text == "130 per arm"
        assert not any("260" in line for line in _lines(row))

    def test_the_members_are_every_figure(self) -> None:
        _, model = _report(_arms() + _subjects())
        # The review list and the counts see each figure (assert_nothing_lost in
        # _report has checked Appendix A).
        figures = [r for r in model.all_rows() if r.field_id == N]
        assert len(figures) == 4


class TestCrfPages:
    """The CRF half of fix 11: "80 per subject; 20,800 in total (RFP p.4)"."""

    def test_the_approved_layout(self) -> None:
        _, model = _report([
            _rec(CRF, "80", "per subject", RFP, 4, "80 CRF pages per subject"),
            _rec(CRF, "20800", "total", RFP, 4, "20,800 CRF pages in total"),
        ])
        row = _row(model, CRF)
        assert [v.text for v in row.values] == ["80 per subject; 20,800 in total"]
        assert row.values[0].sources == "RFP p.4"
        assert _lines(row) == []

    def test_one_figure_alone(self) -> None:
        _, model = _report([_rec(CRF, "80", "per subject", RFP, 4, "80 CRF pages per subject")])
        assert [v.text for v in _row(model, CRF).values] == ["80 per subject"]

    def test_an_unscoped_figure_is_printed_and_not_lost(self) -> None:
        _, model = _report([
            _rec(CRF, "80", "per subject", RFP, 4, "80 CRF pages per subject"),
            _rec(CRF, "95", None, AM, 50, "95 pages"),
        ])
        row = _row(model, CRF)
        assert row.values[0].text == "80 per subject"
        assert "95" in _lines(row)


def test_a_headline_that_does_not_say_a_figure_leaves_it_on_a_line(
    tmp_path: Path, fields_yaml_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pipeline-rules-reviewer, stage 5: a later wording in config/fields.yaml
    # must not take a stated figure out of view.
    text = fields_yaml_path.read_text()
    said = (
        '        - "{each} per subject; {total} in total"\n'
        '        - "{total} in total"\n'
        '        - "{each} per subject"\n'
        '        - "See the lines below"\n'
    )
    assert said in text
    yaml_path = tmp_path / "fields.yaml"
    yaml_path.write_text(text.replace(said, '        - "See the lines below"\n'))
    monkeypatch.setenv("RFP_INTAKE_FIELDS_YAML_PATH", str(yaml_path))
    get_registry.cache_clear()
    try:
        _, model = _report([
            _rec(CRF, "80", "per subject", RFP, 4, "80 CRF pages per subject"),
            _rec(CRF, "20800", "total", RFP, 4, "20,800 CRF pages in total"),
        ])
    finally:
        get_registry.cache_clear()
    row = _row(model, CRF)
    assert [v.text for v in row.values] == ["See the lines below"]
    assert _lines(row) == ["Per subject: 80", "Total: 20,800"]


def test_a_count_no_document_gives_keeps_its_not_found_row() -> None:
    _, model = _report(_arms())
    [row] = [r for g in model.groups for r in g.rows if r.field_id == N]
    assert row.status == "not_found"
    assert not row.block_lines


def test_the_pdf_prints_the_two_rows() -> None:
    from rfp_intake.render.pdf_renderer import build_report_pdf

    state, _ = _report(_arms() + _subjects() + [
        _rec(CRF, "80", "per subject", RFP, 4, "80 CRF pages per subject"),
        _rec(CRF, "20800", "total", RFP, 4, "20,800 CRF pages in total"),
    ])
    pdf = build_report_pdf(state, get_registry(), generated_at="2026-10-12")
    doc = pymupdf.open(stream=pdf, filetype="pdf")  # type: ignore[no-untyped-call]
    text = " ".join("\n".join(page.get_text() for page in doc).split())  # type: ignore[attr-defined]
    for said in (
        "260 (130 per arm, randomized 1:1)",
        "Arm 1 — NEOD001 plus standard of care: 130",
        "Arm 2 — Placebo plus standard of care: 130",
        "Total: 260",
        "80 per subject; 20,800 in total",
    ):
        assert said in text, said
