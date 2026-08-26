"""Table-driven tests for duration/frequency parser."""

from __future__ import annotations

import pytest

from rfp_intake.normalize.duration import normalize_duration


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
