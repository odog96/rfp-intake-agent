"""An `object[]` entry as one JSON object: the prompt sends its keys, the
extraction model's object is kept as JSON text, and `validate_entry` checks it
key by key. Fix 1 of the 2026-10-12 brief."""

from __future__ import annotations

import json
import os

import pytest

from rfp_intake.domain.dynamic import FieldExtractionItem
from rfp_intake.domain.registry import FieldDef, get_registry
from rfp_intake.extract.prompt import _render_fields_section
from rfp_intake.extract.validate import validate_entry, validate_record


@pytest.fixture(autouse=True)
def _real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    get_registry.cache_clear()


def _parts() -> FieldDef:
    return FieldDef(
        id="g.parts", group="g", label="Parts", type="object[]",
        schema={"name": "text", "kind": "enum[arm, cohort]", "n_subjects": "int"},
    )


QUOTE = "Group 1: NEOD001 + SoC, 130 subjects; 20,800 CRF pages"


@pytest.mark.parametrize(("raw", "reason"), [
    ("not json", "entry_not_json"),
    ('["a", "b"]', "entry_not_a_json_object"),
    ('{"name": "x", "colour": "red"}', "entry_key_not_in_schema_colour"),
    ('{"kind": "group"}', "entry_kind_value_not_allowed_group"),
    ('{"n_subjects": "about 130"}', "entry_n_subjects_not_a_whole_number_about 130"),
    ('{"n_subjects": true}', "entry_n_subjects_not_a_whole_number_True"),
    ('{"n_subjects": 1.5}', "entry_n_subjects_not_a_whole_number_1.5"),
    ('{"n_subjects": 260}', "entry_n_subjects_number_not_in_quote_260"),
    # A number inside a longer one is not stated.
    ('{"n_subjects": 13}', "entry_n_subjects_number_not_in_quote_13"),
])
def test_an_entry_that_fails_says_why(raw: str, reason: str) -> None:
    assert validate_entry(raw, QUOTE, _parts()) == reason


@pytest.mark.parametrize("raw", [
    '{"name": "NEOD001 plus standard of care", "kind": "arm", "n_subjects": 130}',
    '{"n_subjects": "130"}',
    # Written with or without thousands commas, the quote's form either way.
    '{"n_subjects": 20800}',
    '{"n_subjects": "20,800"}',
    # The enum read as validate_enum reads it, ignoring case and punctuation.
    '{"kind": "Arm"}',
    # A key stated as null is a key not stated.
    '{"name": "x", "n_subjects": null}',
    "{}",
])
def test_an_entry_that_passes(raw: str) -> None:
    assert validate_entry(raw, QUOTE, _parts()) is None


def test_a_number_written_with_commas_in_the_quote_matches_without() -> None:
    assert validate_entry('{"n_subjects": 20800}', "20,800 pages", _parts()) is None
    assert validate_entry('{"n_subjects": "20,800"}', "20800 pages", _parts()) is None


def test_validate_record_rejects_a_bad_entry_of_an_object_field() -> None:
    fd = get_registry().get_field("design.parts")
    item = FieldExtractionItem(raw_value='{"name": "Arm", "dose": 5, "size": 3}',
                               quote="Arm 5", confidence=0.9, page=1)
    ok, reason = validate_record(item, "--- Page 1 ---\nArm 5", (1, 1), fd)
    assert not ok
    assert reason == "entry_key_not_in_schema_size"


class TestTheModelMaySendTheObject:
    def test_an_object_is_kept_as_its_json_text(self) -> None:
        item = FieldExtractionItem.model_validate({
            "raw_value": {"name": "Placebo", "n_subjects": 130},
            "quote": "q", "confidence": 0.9, "page": 1,
        })
        assert json.loads(item.raw_value) == {"name": "Placebo", "n_subjects": 130}

    def test_text_is_unchanged(self) -> None:
        item = FieldExtractionItem(raw_value="1:1", quote="q", confidence=0.9, page=1)
        assert item.raw_value == "1:1"


def test_the_prompt_lists_each_key_and_its_kind() -> None:
    text = _render_fields_section([get_registry().get_field("design.parts")])
    assert "raw_value: one JSON object for this entry" in text
    assert "    name: text" in text
    assert "    kind: one of ['part', 'cohort', 'arm', 'dose_level', 'treatment_group']" in text
    assert "    n_subjects: a whole number" in text


def test_a_field_with_no_schema_gets_no_key_list() -> None:
    text = _render_fields_section([get_registry().get_field("design.allocation_ratio")])
    assert "JSON object" not in text
