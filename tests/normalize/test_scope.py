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


class TestRealLabelsFromRun20260827205037:
    """Every scope label the model actually produced on the two-document pair.

    Built from the 50 distinct labels in runs/r-20260827-205037/extraction.json
    rather than invented, so the rules stay tied to what the documents do.
    """

    @pytest.mark.parametrize(
        "group",
        [
            # One treatment arm, written five ways across two documents.
            ["NEOD001 arm", "cohort:NEOD001 arm", "cohort:NEOD001+SoC", "arm:NEOD001"],
            ["Placebo arm", "arm:Placebo", "cohort:Placebo arm", "cohort:Placebo+SoC"],
            # Hyphen and underscore are spelling, not meaning.
            ["per-subject", "per_subject"],
            # A trailing generic noun does not change which span is meant.
            ["screening", "Screening", "screening period"],
            ["Interim Monitoring Visits", "Interim Monitoring Visits total", "interim monitoring"],
            # "study" and "total" both mean the whole study.
            ["total", "study", "Overall"],
        ],
    )
    def test_labels_in_a_group_all_agree(self, group: list[str]) -> None:
        canonical = {normalize_scope(label) for label in group}
        assert len(canonical) == 1, f"{group} did not merge: {canonical}"

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            # Different arms.
            ("cohort:NEOD001", "cohort:Placebo"),
            # An arm is not the whole study.
            ("NEOD001 arm", "total"),
            # Different spans of the study.
            ("treatment", "treatment/follow-up period"),
            ("screening", "enrollment"),
            # A per-subject count is not a study total.
            ("per-subject", "total"),
            # Different countries.
            ("country:DE", "country:FR"),
        ],
    )
    def test_genuinely_different_scopes_stay_apart(self, left: str, right: str) -> None:
        assert not scopes_match(left, right)

    def test_a_bare_noise_word_survives_as_a_label(self) -> None:
        # "total" must not be stripped down to nothing by the trailing-noun rule.
        assert normalize_scope("total") == "total"
        assert normalize_scope("visits") is not None
