"""READ_SCHEDULE's header rules, table-driven, with no PDF."""

from __future__ import annotations

from pathlib import Path

import pytest

from rfp_intake.domain.registry import ScheduleTable, load_registry
from rfp_intake.domain.schemas import ScheduleColumn
from rfp_intake.schedule import _says, _visits


@pytest.fixture
def config(fields_yaml_path: Path) -> ScheduleTable:
    table = load_registry(fields_yaml_path).schedule_table
    assert table is not None
    return table


@pytest.mark.parametrize(
    ("period", "timing", "visits", "says"),
    [
        ("Screening", "Day-28 to Day -1", 1, None),
        ("Screening", "Day -2 or Day -1", 1, None),
        ("Month 1", "Day 1", 1, None),
        ("Month 1", "Days 8, 15, 22 (±2)", 3, None),
        ("Month 1", "Days 8, 15 and 22", 3, None),
        ("Months 6, 9, 12, etc. (Every 3rd Month)", "Day 1 (±5)", None, "1 every 3rd month"),
        ("All Other Months", "Day 1 (±5)", None, "1 a month"),
        ("EOT/ETD", "", 1, None),
    ],
)
def test_visits_per_column(
    config: ScheduleTable, period: str, timing: str, visits: int | None, says: str | None
) -> None:
    col = ScheduleColumn(period=period, timing=timing)
    _visits(col, config)
    assert (col.visits, col.visits_says) == (visits, says)


@pytest.mark.parametrize(
    ("text", "wording", "said"),
    [
        ("Months 6, 9, 12, etc. (Every 3rd Month)", "every {nth} month", "3rd"),
        ("All Other Months", "all other months", ""),
        ("Month 1", "month 1", ""),
        ("Months 6, 9, 12", "month 1", None),
        ("Month 12", "month 1", None),
        ("EOT/ETD", "eot", ""),
        ("Every Third Month", "every {nth} month", None),
    ],
)
def test_says(text: str, wording: str, said: str | None) -> None:
    assert _says(text, wording) == said


def test_the_counting_setting_defaults_to_assessments_only(config: ScheduleTable) -> None:
    assert config.count_non_assessments is False
    assert {c.id for c in config.considerations if not c.assessment} == {
        "randomization", "premedication", "study_drug_administration",
        "concomitant_chemotherapy",
    }


def test_study_drug_is_not_matched_on_the_bare_word_administration(
    config: ScheduleTable,
) -> None:
    """Oliver, 2026-10-17: "administration" alone matched "Premedication
    Administration"."""
    study_drug = next(c for c in config.considerations if c.id == "study_drug_administration")
    assert "administration" not in study_drug.wording
