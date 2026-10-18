"""The must-hold check's KNOWN verdict, approved by Oliver on 2026-10-15, and its
`amount` match, asked for by Oliver on 2026-10-17.

A row of a `rows_say` fact may carry `known`, and a `coverage` fact may list
`known_missing` fields. Where only that named part is missing the fact is KNOWN,
which does not fail the run; anything else missing still fails.

An `amount` side or row is checked by its number and unit, whatever words
surround them, so "40 month study duration" and "40 months" both state 40 months.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def check() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_must_hold", ROOT / "scripts" / "check_must_hold.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(field_id: str, value: Any, doc_kind: str = "rfp") -> dict[str, Any]:
    return {"field_id": field_id, "value": value, "status": "confirmed",
            "sources": [{"doc_kind": doc_kind, "page": 3}]}


ROWS_FACT = {
    "kind": "rows_say",
    "rows": [
        {"field": "ops.a", "from": "rfp", "amount": {"n": 80, "unit": ["visit", "visits"]},
         "known": "open item 1"},
        {"field": "ops.b", "from": "rfp", "amount": {"n": 75, "unit": ["visit", "visits"]}},
    ],
}


class TestRowsSay:
    def test_every_row_present_passes(self, check: ModuleType) -> None:
        run = check.Run({"resolved_fields": [_row("ops.a", "80 visits"),
                                             _row("ops.b", "75 visits")]})
        assert check.rows_say(run, ROWS_FACT)[0] == "PASS"

    def test_only_the_known_row_missing_is_known(self, check: ModuleType) -> None:
        run = check.Run({"resolved_fields": [_row("ops.b", "75 visits")]})
        verdict, detail = check.rows_say(run, ROWS_FACT)
        assert verdict == "KNOWN"
        assert "a: KNOWN MISSING, open item 1" in detail

    def test_a_strict_row_missing_fails_even_beside_a_known_one(self, check: ModuleType) -> None:
        run = check.Run({"resolved_fields": [_row("ops.b", "70 visits")]})
        assert check.rows_say(run, ROWS_FACT)[0] == "FAIL"

    def test_a_known_row_with_the_wrong_figure_is_still_only_known(
        self, check: ModuleType
    ) -> None:
        run = check.Run({"resolved_fields": [_row("ops.a", "8 visits"),
                                             _row("ops.b", "75 visits")]})
        assert check.rows_say(run, ROWS_FACT)[0] == "KNOWN"


class TestCoverage:
    @pytest.fixture()
    def registry(self, check: ModuleType, tmp_path: Path,
                 monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "fields.yaml"
        path.write_text("fields:\n" + "".join(f"  - {{id: f.{i}}}\n" for i in range(5)))
        monkeypatch.setattr(check, "FIELDS_PATH", path)

    FACT = {"kind": "coverage", "at_least": 4,
            "known_missing": [{"field": "f.0", "known": "open item 1"}]}

    def _run(self, check: ModuleType, answered: list[int]) -> Any:
        return check.Run({"resolved_fields": [_row(f"f.{i}", "x") for i in answered]})

    @pytest.mark.usefixtures("registry")
    def test_enough_fields_passes(self, check: ModuleType) -> None:
        assert check.coverage(self._run(check, [1, 2, 3, 4]), self.FACT)[0] == "PASS"

    @pytest.mark.usefixtures("registry")
    def test_one_short_because_of_the_known_field_is_known(self, check: ModuleType) -> None:
        verdict, detail = check.coverage(self._run(check, [1, 2, 3]), self.FACT)
        assert verdict == "KNOWN"
        assert "known: f.0, open item 1" in detail

    @pytest.mark.usefixtures("registry")
    def test_one_short_with_the_known_field_present_fails(self, check: ModuleType) -> None:
        assert check.coverage(self._run(check, [0, 1, 2]), self.FACT)[0] == "FAIL"

    @pytest.mark.usefixtures("registry")
    def test_the_allowance_is_one_field_not_more(self, check: ModuleType) -> None:
        assert check.coverage(self._run(check, [1, 2]), self.FACT)[0] == "FAIL"


def test_the_facts_file_marks_only_site_selection_visits_as_known() -> None:
    import yaml

    facts = yaml.safe_load((ROOT / "eval" / "golden" / "sample_pair_must_hold.yaml").read_text())
    known_rows = [(f["id"], r["field"]) for f in facts["facts"] for r in f.get("rows", [])
                  if r.get("known")]
    known_fields = [(f["id"], k["field"]) for f in facts["facts"]
                    for k in f.get("known_missing", [])]
    assert known_rows == [(8, "ops.visits_site_selection")]
    assert known_fields == [(9, "ops.visits_site_selection")]


# --------------------------------------------------------------------------- #
# `amount`: a number and its unit, whatever words surround them
# --------------------------------------------------------------------------- #

FORTY_MONTHS = {"amount": {"n": 40, "unit": ["month", "months"]}}


@pytest.mark.parametrize("value", [
    "40 month study duration",
    "40 months",
    "40-month study",
    "a 40 Months study",
    {"n": 40, "unit": "months"},
    {"n": 40.0, "unit": "months"},
    "40 month study duration; enrollment over approximately 24 months",
])
def test_an_amount_matches_whatever_surrounds_it(check: ModuleType, value: Any) -> None:
    assert check._matches(value, FORTY_MONTHS)


@pytest.mark.parametrize("value", [
    "140 months",
    "40.5 months",
    "4,040 months",
    "40 weeks",
    "40 monthly visits",
    "42 months",
    "40",
    "months 40",
    None,
])
def test_an_amount_needs_that_number_directly_before_that_unit(
    check: ModuleType, value: Any
) -> None:
    assert not check._matches(value, FORTY_MONTHS)


def _record(value: Any, doc_kind: str, page: int) -> dict[str, Any]:
    return {"value": value, "scope": "study",
            "provenance": {"doc_kind": doc_kind, "page": page}}


def _duration_run(check: ModuleType, rfp_value: Any) -> Any:
    return check.Run({"resolved_fields": [], "contradictions": [{
        "field_id": "timeline.total_duration", "verdict": "conflict",
        "records": [_record("42 months (total study duration)", "amendment", 24),
                    _record(rfp_value, "rfp", 3)],
    }]})


def _fact_5() -> dict[str, Any]:
    import yaml

    facts = yaml.safe_load((ROOT / "eval" / "golden" / "sample_pair_must_hold.yaml").read_text())
    return next(f for f in facts["facts"] if f["id"] == 5)


@pytest.mark.parametrize("rfp_value", ["40 month study duration", "40 months"])
def test_fact_5_passes_on_either_wording_of_the_rfps_40_months(
    check: ModuleType, rfp_value: str
) -> None:
    # r-20261016-prompt-b's RFP record read "40 month study duration";
    # r-20261013-prompt-c's read "40 months".
    assert check.disagreement(_duration_run(check, rfp_value), _fact_5())[0] == "PASS"


def test_fact_5_still_fails_on_another_number(check: ModuleType) -> None:
    assert check.disagreement(_duration_run(check, "48 month study duration"),
                              _fact_5())[0] == "FAIL"


def test_no_fact_matches_a_number_and_unit_by_exact_words() -> None:
    import yaml

    facts = yaml.safe_load((ROOT / "eval" / "golden" / "sample_pair_must_hold.yaml").read_text())
    specs = [s for f in facts["facts"] for s in [f, *f.get("sides", []), *f.get("rows", [])]]
    assert [s["says"] for s in specs if any(str(w).isdigit() for w in s.get("says", []))] == []


# --------------------------------------------------------------------------- #
# The Schedule of Events facts (Prompt C stage 4)
# --------------------------------------------------------------------------- #

TABLE_1 = "Table 1: Schedule of Events"


def _schedule_facts() -> list[dict[str, Any]]:
    import yaml

    facts = yaml.safe_load((ROOT / "eval" / "golden" / "sample_pair_must_hold.yaml").read_text())
    return [f for f in facts["facts"] if f["kind"].startswith("schedule_")]


def _cited(doc_id: str, doc_kind: str) -> dict[str, Any]:
    """A row citing the document, which is how the check learns its kind."""
    return {"field_id": "study.phase", "value": "phase_3", "status": "confirmed",
            "sources": [{"doc_id": doc_id, "doc_kind": doc_kind, "page": 1}]}


def _column(period: str, timing: str, considerations: list[str],
            marks: int = 5) -> dict[str, Any]:
    return {"period": period, "timing": timing, "marks_all": marks + 1,
            "marks_assessments": marks, "considerations": considerations}


def _run(check: ModuleType, columns: list[dict[str, Any]], *, doc_kind: str = "amendment",
         heading: str = TABLE_1, counted: bool = True) -> Any:
    return check.Run({"resolved_fields": [_cited("doc-a", doc_kind)], "schedules": [
        {"doc_id": "doc-a", "heading": heading, "counted": counted, "columns": columns}]})


DAY_1 = ["randomization", "study_drug_administration"]
COLUMNS = [
    _column("Screening", "Day-28 to Day -1", ["informed_consent", "eligibility_review"]),
    _column("Screening", "Day -2 or Day -1", []),
    _column("Month 1", "Day 1", DAY_1),
    _column("Month 1", "Days 8, 15, 22 (±2)", []),
    _column("Month 2", "Day 1 (±2)", ["study_drug_administration"]),
    _column("Month 3", "Day 1 (±2)", ["study_drug_administration"]),
    _column("Months 6, 9, 12, etc. (Every 3rd Month)", "Day 1 (±5)",
            ["study_drug_administration"]),
    _column("All Other Months", "Day 1 (±5)", ["study_drug_administration"]),
    _column("EOT/ETD", "", []),
]


def _fact(fact_id: int) -> dict[str, Any]:
    return next(f for f in _schedule_facts() if f["id"] == fact_id)


class TestScheduleFacts:
    @pytest.mark.parametrize("fact_id", [12, 13, 14])
    def test_the_marks_hold(self, check: ModuleType, fact_id: int) -> None:
        assert check.schedule_marks(_run(check, COLUMNS), _fact(fact_id))[0] == "PASS"

    def test_a_run_from_before_read_schedule_fails(self, check: ModuleType) -> None:
        run = check.Run({"resolved_fields": [_cited("doc-a", "amendment")]})
        verdict, detail = check.schedule_counts(run, _fact(10))
        assert verdict == "FAIL" and "before READ_SCHEDULE" in detail

    @pytest.mark.parametrize("change", [
        {"doc_kind": "rfp"}, {"heading": "Appendix 3: Schedule Of Events"}, {"counted": False},
    ])
    def test_only_the_amendments_counted_table_1_is_read(
        self, check: ModuleType, change: dict[str, Any]
    ) -> None:
        assert check.schedule_marks(_run(check, COLUMNS, **change), _fact(12))[0] == "FAIL"

    def test_a_day_1_without_study_drug_fails(self, check: ModuleType) -> None:
        columns = [*COLUMNS[:5], _column("Month 3", "Day 1 (±2)", []), *COLUMNS[6:]]
        verdict, detail = check.schedule_marks(_run(check, columns), _fact(14))
        assert verdict == "FAIL" and "Month 3 Day 1 (±2) has no study_drug_administration" in detail

    def test_a_header_read_differently_cannot_pass_on_fewer_columns(
        self, check: ModuleType
    ) -> None:
        # Day 15 is not Day 1, so only four Day 1 columns are chosen, not five.
        columns = [*COLUMNS[:5], _column("Month 3", "Day 15", []), *COLUMNS[6:]]
        verdict, detail = check.schedule_marks(_run(check, columns), _fact(14))
        assert verdict == "FAIL" and "4 column(s) chosen" in detail

    def test_the_counts_are_read_in_either_setting(self, check: ModuleType) -> None:
        columns = [_column("Screening", "Day 1", [], marks=n) for n in _fact(10)["columns"]]
        assert check.schedule_counts(_run(check, columns), _fact(10))[0] == "PASS"
        # Every mark is one more than the assessments in these made-up columns.
        assert check.schedule_counts(_run(check, columns), _fact(11))[0] == "FAIL"


@pytest.mark.slow
def test_every_schedule_fact_holds_on_the_real_protocol(
    check: ModuleType, samples_dir: Path, fields_yaml_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """READ_SCHEDULE's tables for samples/Example protocol 2.pdf, as extraction.json
    saves them, pass facts 10 to 14. A live run classifies the PDF as the amendment."""
    from rfp_intake.domain.registry import get_registry

    pdf = samples_dir / "Example protocol 2.pdf"
    if not pdf.exists():
        pytest.skip("Example protocol 2.pdf not available")
    monkeypatch.setenv("RFP_INTAKE_FIELDS_YAML_PATH", str(fields_yaml_path))
    get_registry.cache_clear()
    from tests.render.test_visit_table import _protocol_schedules

    schedules = [s.model_dump(mode="json") for s in _protocol_schedules(pdf)]
    run = check.Run({"resolved_fields": [_cited("doc-protocol", "amendment")],
                     "schedules": schedules})
    results = {f["id"]: check.KINDS[f["kind"]](run, f) for f in _schedule_facts()}
    assert {i: v for i, (v, _) in results.items()} == {i: "PASS" for i in range(10, 15)}, results
