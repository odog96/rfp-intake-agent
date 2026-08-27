"""Table-driven tests for scope canonicalisation (CLAUDE.md rule 7)."""

from __future__ import annotations

import pytest

from rfp_intake.normalize.scope import normalize_scope, scopes_match


class TestNormalizeScope:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            ("", None),
            ("   ", None),
            # Whole-study synonyms collapse to one label.
            ("total", "total"),
            ("Total", "total"),
            ("overall", "total"),
            ("Study", "total"),
            ("all arms", "total"),
            ("  COMBINED  ", "total"),
            # Arm-ish prefixes are one concept.
            ("cohort:Placebo", "cohort:placebo"),
            ("cohort:placebo", "cohort:placebo"),
            ("arm:Placebo", "cohort:placebo"),
            ("Group:Placebo", "cohort:placebo"),
            ("part:Placebo", "cohort:placebo"),
            # Background therapy does not make it a different arm.
            ("cohort:NEOD001+SoC", "cohort:neod001"),
            ("cohort:NEOD001 + SoC", "cohort:neod001"),
            ("cohort:NEOD001 plus standard of care", "cohort:neod001"),
            ("arm:Placebo + standard-of-care", "cohort:placebo"),
            # Prefixes that must stay distinct.
            ("country:DE", "country:de"),
            ("region:DE", "country:de"),
            ("site:0123", "site:0123"),
            # No prefix, not a whole-study word: kept as written but tidied.
            ("Germany", "germany"),
            ("  Months 1-3  ", "months 1-3"),
            # Trailing punctuation and whitespace noise.
            ("cohort: Placebo .", "cohort:placebo"),
            ("cohort:\tPlacebo\n", "cohort:placebo"),
        ],
    )
    def test_canonical_form(self, raw: str | None, expected: str | None) -> None:
        assert normalize_scope(raw) == expected

    def test_absent_scope_is_not_the_whole_study(self) -> None:
        # None means the model did not say which part this covers. That is a
        # different claim from "this covers the whole study", and merging the two
        # would fold an unqualified number into the study-wide total.
        assert normalize_scope(None) != normalize_scope("total")


class TestScopesMatch:
    def test_the_placebo_arm_written_three_ways_is_one_arm(self) -> None:
        # The exact failure seen in run r-sonnet46-pair-114917: the number of
        # subjects resolved three times for one arm.
        assert scopes_match("cohort:Placebo", "cohort:placebo")
        assert scopes_match("cohort:Placebo", "cohort:Placebo+SoC")
        assert scopes_match("arm:Placebo", "cohort:Placebo + standard of care")

    def test_two_different_arms_stay_apart(self) -> None:
        assert not scopes_match("cohort:NEOD001", "cohort:Placebo")

    def test_an_arm_is_not_the_whole_study(self) -> None:
        assert not scopes_match("cohort:NEOD001", "total")

    def test_two_countries_stay_apart(self) -> None:
        assert not scopes_match("country:DE", "country:FR")
