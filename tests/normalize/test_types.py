"""Table-driven tests for type-specific normalizers."""

from __future__ import annotations

import pytest

from rfp_intake.domain.registry import parse_entry_key
from rfp_intake.normalize.types import (
    normalize_bool,
    normalize_entry,
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

    # CLAUDE.md open item 10 (2026-10-19): a leading whole number followed only
    # by words with no digits, or by a parenthetical, which may hold digits.
    @pytest.mark.parametrize("raw,expected", [
        ("20,800 CRF pages total", 20800),
        ("Approximately 20,800 CRF pages total.", 20800),
        ("20800", 20800),
        ("260 (130 per arm)", 260),
        ("260 (130 per arm), randomized", 260),
        ("approximately 260 subjects (130 per arm, randomized 1:1)", 260),
        ("260 patients (130 per arm)", 260),
        ("260 randomized", 260),
        ("260 total", 260),
        ("3 (three)", 3),
        ("75 sites (US & EU)", 75),
        ("52 weeks (1 year)", 52),
        ("260 subjects — randomized", 260),
    ])
    def test_a_number_followed_by_words_or_a_parenthetical(
        self, raw: str, expected: int,
    ) -> None:
        assert normalize_int(raw) == expected

    # What it accepted before the change and still accepts.
    @pytest.mark.parametrize("raw,expected", [
        ("approximately 260", 260),
        ("up to 40 sites", 40),
        ("about 30 sites across Europe", 30),
        ("1,000,000", 1000000),
        ("1 000", 1000),
        ("120.0", 120),
        ("120.0 sites", 120),
        ("0", 0),
        ("-5", -5),
        ("forty (40)", 40),
    ])
    def test_still_accepted(self, raw: str, expected: int) -> None:
        assert normalize_int(raw) == expected

    @pytest.mark.parametrize("raw", [
        # Words, then a second figure outside a parenthetical: two numbers.
        "10 visits per site for a total of 750 visits",
        "40 sites in 5 countries",
        "forty sites in 5 countries",
        "260 (130 per arm) randomised 1:1",
        "260 subjects (130 per arm), 236 evaluable",
        "10 to 12",
        "10 or 12",
        # A range, a fraction, a share, a rate: no whole count.
        "200-260",
        "200 - 260",
        "1.5",
        "1.5 years",
        "40 %",
        "10/site",
        "260 + 20",
        # Not a number followed by words or a whole parenthetical.
        "260 (130 per arm",
        "260pts",
        "N=260",
        "Total: 260",
    ])
    def test_still_none(self, raw: str) -> None:
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


# An `object[]` entry, key by key, in `schema` order (fix 1 of the 2026-10-12 brief).
_ENTRY_KEYS = [
    parse_entry_key("name", "text"),
    parse_entry_key("kind", "enum[part, cohort, arm]"),
    parse_entry_key("n_subjects", "int"),
]


@pytest.mark.parametrize(("raw", "expected"), [
    ('{"n_subjects": 130, "kind": "arm", "name": "Placebo"}',
     {"name": "Placebo", "kind": "arm", "n_subjects": 130}),
    ('{"n_subjects": "130"}', {"n_subjects": 130}),
    ('{"n_subjects": "20,800"}', {"n_subjects": 20800}),
    ('{"kind": "Arm"}', {"kind": "arm"}),
    ('{"name": "  NEOD001\\n plus   SoC "}', {"name": "NEOD001 plus SoC"}),
    # A key stated empty, null, or as a nested value is left out.
    ('{"name": "", "kind": null, "n_subjects": [1]}', {}),
    # A key the schema does not have is not carried; EXTRACT rejects it first.
    ('{"name": "x", "colour": "red"}', {"name": "x"}),
    ("{}", {}),
    ("not json", None),
    ('["a"]', None),
])
def test_normalize_entry(raw: str, expected: dict[str, object] | None) -> None:
    out = normalize_entry(raw, _ENTRY_KEYS)
    assert out == expected
    if out is not None:
        assert list(out) == [k.name for k in _ENTRY_KEYS if k.name in out]
