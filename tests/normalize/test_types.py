"""Table-driven tests for type-specific normalizers."""

from __future__ import annotations

import pytest

from rfp_intake.normalize.types import (
    normalize_bool,
    normalize_enum,
    normalize_int,
    normalize_list_enum,
    normalize_text,
    normalize_value,
)

PHASE_VALUES = [
    "phase_1", "phase_1_2", "phase_2", "phase_2_3",
    "phase_3", "phase_3_4", "phase_4", "not_specified", "other",
]

BLINDING_VALUES = [
    "open_label", "single_blind", "double_blind",
    "triple_blind", "partially_blinded", "not_specified",
]

DRUG_FORM_VALUES = [
    "oral_tablet", "oral_capsule", "oral_solution",
    "injection_sc", "injection_im", "infusion_iv",
    "topical", "inhaled", "intrathecal", "ophthalmic",
    "other", "not_specified",
]


class TestNormalizeInt:
    @pytest.mark.parametrize("raw,expected", [
        ("40", 40),
        ("75", 75),
        ("120", 120),
        ("1,200", 1200),
        ("forty (40)", 40),
        ("approximately 75 sites", 75),
        ("~120", 120),
        ("≥100", 100),
        ("forty", 40),
        ("twenty", 20),
        ("100 subjects", 100),
        ("260 patients", 260),
    ])
    def test_valid(self, raw: str, expected: int) -> None:
        assert normalize_int(raw) == expected

    @pytest.mark.parametrize("raw", [
        "", "  ", "unknown", "N/A", "not specified",
    ])
    def test_invalid(self, raw: str) -> None:
        assert normalize_int(raw) is None


class TestNormalizeEnum:
    @pytest.mark.parametrize("raw,allowed,expected", [
        ("Phase 1/2", PHASE_VALUES, "phase_1_2"),
        ("Ph I/II", PHASE_VALUES, "phase_1_2"),
        ("Phase I", PHASE_VALUES, "phase_1"),
        ("phase 1", PHASE_VALUES, "phase_1"),
        ("Phase IIb", PHASE_VALUES, "phase_2"),
        ("Phase III", PHASE_VALUES, "phase_3"),
        ("Phase IV", PHASE_VALUES, "phase_4"),
        ("phase_1", PHASE_VALUES, "phase_1"),
        ("open-label", BLINDING_VALUES, "open_label"),
        ("double-blind", BLINDING_VALUES, "double_blind"),
        ("Double Blind", BLINDING_VALUES, "double_blind"),
        ("single-masked", BLINDING_VALUES, "single_blind"),
        ("oral tablet", DRUG_FORM_VALUES, "oral_tablet"),
        ("SC", DRUG_FORM_VALUES, "injection_sc"),
        ("IV", DRUG_FORM_VALUES, "infusion_iv"),
        ("subcutaneous", DRUG_FORM_VALUES, "injection_sc"),
    ])
    def test_valid(self, raw: str, allowed: list[str], expected: str) -> None:
        assert normalize_enum(raw, allowed) == expected

    def test_direct_match(self) -> None:
        assert normalize_enum("phase_1", PHASE_VALUES) == "phase_1"

    def test_unknown_returns_none(self) -> None:
        assert normalize_enum("unknown_value", PHASE_VALUES) is None


class TestNormalizeBool:
    @pytest.mark.parametrize("raw,expected", [
        ("yes", True),
        ("Yes", True),
        ("true", True),
        ("TRUE", True),
        ("y", True),
        ("1", True),
        ("no", False),
        ("No", False),
        ("false", False),
        ("n", False),
        ("0", False),
    ])
    def test_valid(self, raw: str, expected: bool) -> None:
        assert normalize_bool(raw) == expected

    def test_invalid(self) -> None:
        assert normalize_bool("maybe") is None
        assert normalize_bool("") is None


class TestNormalizeText:
    def test_collapses_whitespace(self) -> None:
        assert normalize_text("  hello   world  ") == "hello world"

    def test_collapses_newlines(self) -> None:
        assert normalize_text("line1\n  line2\n\nline3") == "line1 line2 line3"

    def test_preserves_content(self) -> None:
        text = "Phase 1, randomized, double-blind study"
        assert normalize_text(text) == text


class TestNormalizeListEnum:
    def test_comma_separated(self) -> None:
        result = normalize_list_enum("oral_tablet, injection_sc", DRUG_FORM_VALUES)
        assert result == ["oral_tablet", "injection_sc"]

    def test_with_mappings(self) -> None:
        result = normalize_list_enum("tablet, SC, IV", DRUG_FORM_VALUES)
        assert result == ["oral_tablet", "injection_sc", "infusion_iv"]

    def test_semicolon_separated(self) -> None:
        result = normalize_list_enum("tablet; capsule", DRUG_FORM_VALUES)
        assert result == ["oral_tablet", "oral_capsule"]

    def test_empty(self) -> None:
        assert normalize_list_enum("", DRUG_FORM_VALUES) is None

    def test_no_matches(self) -> None:
        assert normalize_list_enum("xyz, abc", DRUG_FORM_VALUES) is None


class TestNormalizeValue:
    def test_dispatches_int(self) -> None:
        assert normalize_value("75", "int") == 75

    def test_dispatches_enum(self) -> None:
        assert normalize_value("Phase III", "enum", PHASE_VALUES) == "phase_3"

    def test_dispatches_bool(self) -> None:
        assert normalize_value("yes", "bool") is True

    def test_dispatches_text(self) -> None:
        assert normalize_value("  hello  world  ", "text") == "hello world"

    def test_dispatches_list_enum(self) -> None:
        result = normalize_value("tablet, SC", "list[enum]", DRUG_FORM_VALUES)
        assert result == ["oral_tablet", "injection_sc"]
