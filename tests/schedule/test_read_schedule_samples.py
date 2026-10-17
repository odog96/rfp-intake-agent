"""READ_SCHEDULE on the real sample protocol (Prompt C, Angus Gray's 13 October request).

Offline: the PDF is read from `samples/` and nothing here calls a model. Marked
`slow` with the other tests that parse real PDFs.

The target is Table 1, "Schedule of Events", of `Example protocol 2.pdf`, PDF
pages 26–27. Oliver Zarate counted its marks by position on the page and an
independent count agreed on all 11 columns. The Assessments count leaves out
the rows for randomization, premedication, the study drug infusion and
chemotherapy, which are not assessments; counting every mark is the other
setting of `schedule_table.count_non_assessments` in config/fields.yaml.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytest

from rfp_intake.domain.registry import ScheduleTable, load_registry
from rfp_intake.domain.schemas import Document, Schedule
from rfp_intake.schedule import read_schedules

PROTOCOL = "Example protocol 2.pdf"

ASSESSMENTS = [26, 16, 13, 7, 23, 6, 28, 7, 30, 22, 31]
ALL_MARKS = [26, 16, 17, 8, 26, 7, 31, 8, 33, 25, 31]
PERIODS = [
    "Screening", "Screening", "Month 1", "Month 1", "Month 2", "Month 2",
    "Month 3", "Month 3", "Months 6, 9, 12, etc. (Every 3rd Month)",
    "All Other Months", "EOT/ETD",
]


@lru_cache(maxsize=1)
def _config() -> ScheduleTable:
    table = load_registry(Path(__file__).parents[2] / "config" / "fields.yaml").schedule_table
    assert table is not None
    return table


@lru_cache(maxsize=1)
def _schedules(pdf: Path) -> list[Schedule]:
    from tests.plan.test_plan_sections_samples import _document

    doc: Document = _document(pdf, "doc-protocol", "protocol")
    return read_schedules(doc, _config())


@pytest.fixture
def schedules(samples_dir: Path) -> list[Schedule]:
    pdf = samples_dir / PROTOCOL
    if not pdf.exists():
        pytest.skip(f"{PROTOCOL} not available")
    return _schedules(pdf)


@pytest.fixture
def table_1(schedules: list[Schedule]) -> Schedule:
    counted = [s for s in schedules if s.counted]
    assert len(counted) == 1, [s.heading for s in counted]
    return counted[0]


@pytest.mark.slow
class TestTable1:
    def test_it_is_table_1_joined_over_both_pages(self, table_1: Schedule) -> None:
        assert table_1.heading == "Table 1: Schedule of Events"
        assert table_1.pages == [26, 27]
        assert table_1.problem is None

    def test_the_columns_are_the_protocols_own(self, table_1: Schedule) -> None:
        assert [c.period for c in table_1.columns] == PERIODS
        assert [c.timing for c in table_1.columns][:4] == [
            "Day-28 to Day -1", "Day -2 or Day -1", "Day 1", "Days 8, 15, 22 (±2)",
        ]

    def test_assessments_per_column(self, table_1: Schedule) -> None:
        assert [c.marks_assessments for c in table_1.columns] == ASSESSMENTS

    def test_every_mark_per_column(self, table_1: Schedule) -> None:
        assert [c.marks_all for c in table_1.columns] == ALL_MARKS

    def test_nothing_was_left_unplaced(self, table_1: Schedule) -> None:
        assert table_1.unplaced == []

    def test_visits_per_column(self, table_1: Schedule) -> None:
        assert [c.visits for c in table_1.columns] == [1, 1, 1, 3, 1, 3, 1, 3, None, None, 1]
        assert table_1.columns[8].visits_says == "1 every 3rd month"
        assert table_1.columns[9].visits_says == "1 a month"

    def test_each_column_is_in_its_stage(self, table_1: Schedule) -> None:
        assert [c.stage for c in table_1.columns] == (
            ["Screening"] * 2 + ["Months 1–3"] * 6 + ["From Month 4"] * 2 + ["End of treatment"]
        )

    def test_the_end_of_treatment_timing_is_its_own_footnote(self, table_1: Schedule) -> None:
        """Decision B of Prompt C: the EOT/ETD header gives no timing, so its own
        footnote 3, on p.28, is read for it."""
        eot = table_1.columns[10]
        assert eot.timing == ""
        assert eot.timing_footnote == (
            "EOT/ETD Visit to occur 28-35 days after the last study drug administration."
        )
        assert eot.timing_footnote_page == 28

    def test_consent_and_eligibility_at_the_first_screening_visit(
        self, table_1: Schedule
    ) -> None:
        assert table_1.columns[0].considerations == ["informed_consent", "eligibility_review"]
        for col in table_1.columns[1:]:
            assert "informed_consent" not in col.considerations
            assert "eligibility_review" not in col.considerations

    def test_randomization_at_month_1_day_1_only(self, table_1: Schedule) -> None:
        assert [i for i, c in enumerate(table_1.columns)
                if "randomization" in c.considerations] == [2]

    def test_study_drug_at_every_day_1_visit_from_month_1(self, table_1: Schedule) -> None:
        assert [i for i, c in enumerate(table_1.columns)
                if "study_drug_administration" in c.considerations] == [2, 4, 6, 8, 9]

    def test_premedication_is_its_own_consideration(self, table_1: Schedule) -> None:
        """Oliver, 2026-10-17: "Premedication Administration" is premedication,
        not study drug administration; the bare word "administration" is not on
        the study drug list."""
        premed = next(r for r in table_1.rows if r.label.startswith("Premedication"))
        assert premed.considerations == ["premedication"]

    def test_chemotherapy_follows_the_marks(self, table_1: Schedule) -> None:
        """Decision D of Prompt C: the Chemotherapy row's marks, which include
        Month 2 and 3 Day 1 and both repeating columns."""
        assert [i for i, c in enumerate(table_1.columns)
                if "concomitant_chemotherapy" in c.considerations] == [2, 3, 4, 5, 6, 7, 8, 9]


@pytest.mark.slow
def test_the_stopped_early_schedule_is_found_and_not_counted(
    schedules: list[Schedule],
) -> None:
    """Appendix 3 on p.111 is the schedule for patients who stop treatment early.
    It is never added to the main table (docs/ANALYST_PROCEDURE_PROTOCOL.md §9)."""
    early = [s for s in schedules if not s.counted]
    assert len(early) == 1
    assert early[0].heading.startswith("Appendix 3: Schedule Of Events")
    assert early[0].pages == [111]
