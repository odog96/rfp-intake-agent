"""Table-driven tests for duration/frequency parser."""

from __future__ import annotations

import pytest

from rfp_intake.normalize.duration import amount_count, amounts_in, normalize_duration


class TestQNotation:
    @pytest.mark.parametrize("raw,expected", [
        ("Q3W", {"n": 21, "unit": "days"}),
        ("Q2W", {"n": 14, "unit": "days"}),
        ("Q4W", {"n": 28, "unit": "days"}),
        ("Q1W", {"n": 7, "unit": "days"}),
        ("Q12W", {"n": 84, "unit": "days"}),
        ("q3w", {"n": 21, "unit": "days"}),
        ("Q7D", {"n": 7, "unit": "days"}),
        ("Q3M", {"n": 3, "unit": "months"}),
    ])
    def test_q_notation(self, raw: str, expected: dict) -> None:  # type: ignore[type-arg]
        assert normalize_duration(raw) == expected


class TestEveryPattern:
    @pytest.mark.parametrize("raw,expected", [
        ("every 3 weeks", {"n": 21, "unit": "days"}),
        ("every 2 weeks", {"n": 14, "unit": "days"}),
        ("every 4 weeks", {"n": 28, "unit": "days"}),
        ("every 6 weeks", {"n": 42, "unit": "days"}),
        ("every 8 weeks", {"n": 56, "unit": "days"}),
        ("every 12 weeks", {"n": 84, "unit": "days"}),
        ("every 3 months", {"n": 3, "unit": "months"}),
        ("every 6 months", {"n": 6, "unit": "months"}),
        ("every 21 days", {"n": 21, "unit": "days"}),
    ])
    def test_every_numeric(self, raw: str, expected: dict) -> None:  # type: ignore[type-arg]
        assert normalize_duration(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("every three weeks", {"n": 21, "unit": "days"}),
        ("every four weeks", {"n": 28, "unit": "days"}),
        ("every two months", {"n": 2, "unit": "months"}),
    ])
    def test_every_words(self, raw: str, expected: dict) -> None:  # type: ignore[type-arg]
        assert normalize_duration(raw) == expected


class TestSimpleDuration:
    @pytest.mark.parametrize("raw,expected", [
        ("40 months", {"n": 40, "unit": "months"}),
        ("21 days", {"n": 21, "unit": "days"}),
        ("12 weeks", {"n": 12, "unit": "weeks"}),
        ("3 years", {"n": 3, "unit": "years"}),
        ("52 weeks", {"n": 52, "unit": "weeks"}),
        ("~24 months", {"n": 24, "unit": "months"}),
    ])
    def test_n_units(self, raw: str, expected: dict) -> None:  # type: ignore[type-arg]
        assert normalize_duration(raw) == expected


class TestRangePattern:
    @pytest.mark.parametrize("raw,expected", [
        ("6-8 weeks", {"n_min": 6, "n_max": 8, "unit": "weeks"}),
        ("6 to 8 weeks", {"n_min": 6, "n_max": 8, "unit": "weeks"}),
        ("12-18 months", {"n_min": 12, "n_max": 18, "unit": "months"}),
        ("3-6 months", {"n_min": 3, "n_max": 6, "unit": "months"}),
    ])
    def test_ranges(self, raw: str, expected: dict) -> None:  # type: ignore[type-arg]
        assert normalize_duration(raw) == expected


class TestFrequencyWords:
    @pytest.mark.parametrize("raw,expected", [
        ("once daily", {"n": 1, "unit": "days", "per": True}),
        ("twice daily", {"n": 2, "unit": "days", "per": True}),
        ("once weekly", {"n": 1, "unit": "weeks", "per": True}),
        ("twice weekly", {"n": 2, "unit": "weeks", "per": True}),
    ])
    def test_freq_words(self, raw: str, expected: dict) -> None:  # type: ignore[type-arg]
        assert normalize_duration(raw) == expected


class TestEdgeCases:
    def test_empty_string(self) -> None:
        assert normalize_duration("") is None

    def test_whitespace_only(self) -> None:
        assert normalize_duration("   ") is None

    def test_unparseable(self) -> None:
        assert normalize_duration("as needed") is None

    def test_complex_unparseable(self) -> None:
        assert normalize_duration("varies by cohort") is None


class TestAWordingWithTwoAmountsIsNotShortened:
    """Prompt B item 7: a wording is read as its amount only when it holds one."""

    @pytest.mark.parametrize("raw", [
        "every 2 weeks for 12 weeks, then every 4 weeks",
        "every 28 days for 24 months",
    ])
    def test_two_amounts_are_left_as_written(self, raw: str) -> None:
        assert normalize_duration(raw) is None

    def test_one_amount_with_other_words_is_still_read(self) -> None:
        raw = "once every 28 days from randomization until the EOT/ETD Visit"
        assert normalize_duration(raw) == {"n": 28, "unit": "days"}

    @pytest.mark.parametrize("raw,count", [
        ("42 months (total study duration)", 1),
        ("42 months to 48 months", 2),
        ("3 years 6 months", 2),
        ("every three weeks for six months", 2),
        # A hyphenated amount is an amount too (plan-conformance reviewer,
        # 2026-10-16): a real wording in seven saved runs.
        ("42 months (24-month accrual period + 18-month treatment/follow-up period)", 3),
        ("every 28-day cycle", 1),
        ("1 visit per site", 0),
    ])
    def test_amounts_are_counted(self, raw: str, count: int) -> None:
        assert amount_count(raw) == count


class TestAmountsIn:
    """The lengths a wording gives, as the patient timeline compares them
    (reconcile/entries.py:length_key): a range by both its ends, a day named by
    its number never read as an amount, nothing converted."""

    @pytest.mark.parametrize("raw,expected", [
        ("28 days", [(28, 28, "days")]),
        ("a 28-day period", [(28, 28, "days")]),
        ("28-35 days", [(28, 35, "days")]),
        ("28 to 35 days", [(28, 35, "days")]),
        ("28 \u2013 35 days", [(28, 35, "days")]),
        ("within 28-35 days after the last study drug administration", [(28, 35, "days")]),
        ("1-2 years", [(1, 2, "years")]),
        ("2 years", [(2, 2, "years")]),
        ("3.5-4 years", [(3.5, 4, "years")]),
        ("4 years", [(4, 4, "years")]),
        ("one to two years", [(1, 2, "years")]),
        ("4 weeks", [(4, 4, "weeks")]),
        ("every 28 days for 24 months", [(28, 28, "days"), (24, 24, "months")]),
        ("Day 1 to 28 days after the last dose", [(28, 28, "days")]),
        ("Day -28 to 14 days", [(14, 14, "days")]),
        ("Day -28 to Day -1", []),
        ("approximately 300 primary endpoint events", []),
        # A number word's own hyphen is not a range (second reading, 23 November).
        ("twenty-four months", [(24, 24, "months")]),
        ("fifty-two weeks", [(52, 52, "weeks")]),
        ("twenty to thirty days", [(20, 30, "days")]),
        ("1 or 2 years", [(1, 2, "years")]),
        ("between 1 and 2 years", [(1, 2, "years")]),
        ("6 and 12 months", [(6, 12, "months")]),
        # Any tens word with its unit, and digits with thousands separators
        # (third reading, 23 November).
        ("twenty-eight days", [(28, 28, "days")]),
        ("thirty one days", [(31, 31, "days")]),
        ("seventeen weeks", [(17, 17, "weeks")]),
        ("seventy days", [(70, 70, "days")]),
        ("1,000 days", [(1000, 1000, "days")]),
        ("1,000-2,000 days", [(1000, 2000, "days")]),
        ("two hundred days", []),
        ("one hundred and twenty days", []),
        ("1 000 days", []),
        ("10,12 days", []),
    ])
    def test_amounts_in(self, raw: str, expected: list[tuple[float, float, str]]) -> None:
        assert amounts_in(raw) == expected

    @pytest.mark.parametrize("a,b", [
        ("1-2 years", "2 years"),
        ("3.5-4 years", "4 years"),
        ("28-35 days", "35 days"),
        ("28 days", "4 weeks"),
        ("between 1 and 2 years", "2 years"),
        ("1 or 2 years", "2 years"),
        ("1,000 days", "2,000 days"),
        ("twenty-eight days", "eight days"),
    ])
    def test_a_range_is_not_its_upper_end(self, a: str, b: str) -> None:
        assert amounts_in(a) != amounts_in(b)

    def test_a_number_word_reads_as_the_number(self) -> None:
        assert amounts_in("twenty-four months") == amounts_in("24 months")
