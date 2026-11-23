"""Tests for domain/dynamic.py — runtime extraction model builder."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from rfp_intake.domain.dynamic import (
    build_extraction_model,
    extraction_response_to_records,
)
from rfp_intake.domain.registry import load_registry


def test_build_extraction_model_for_visits(fields_yaml_path: Path, tmp_path: Path) -> None:
    # No field in config/fields.yaml is derived since 2026-11-23, so one is added
    # to a copy of it to show a derived field is left out of the model.
    import yaml

    data = yaml.safe_load(fields_yaml_path.read_text())
    data["fields"].append({
        "id": "visits.worked_out", "group": "visits", "label": "Worked out", "type": "text",
        "derived": True, "derived_from": ["visits.total_count"],
    })
    copy = tmp_path / "fields.yaml"
    copy.write_text(yaml.dump(data, allow_unicode=True))
    registry = load_registry(copy)
    model = build_extraction_model("visits", registry=registry)

    assert issubclass(model, BaseModel)
    assert model.__name__ == "VisitsExtraction"

    # Should have non-derived fields only
    field_names = set(model.model_fields.keys())
    assert "frequency_by_period" in field_names
    assert "total_count" in field_names
    assert "schedule_present" in field_names
    # Derived field should be excluded
    assert "worked_out" not in field_names


def test_build_extraction_model_for_study_design(fields_yaml_path: Path) -> None:
    registry = load_registry(fields_yaml_path)
    model = build_extraction_model("study_design", registry=registry)
    field_names = set(model.model_fields.keys())
    assert "overview" in field_names
    assert "is_multipart" in field_names
    assert "parts" in field_names


def test_generated_model_round_trips_json(fields_yaml_path: Path) -> None:
    registry = load_registry(fields_yaml_path)
    model = build_extraction_model("visits", registry=registry)

    payload = {
        "frequency_by_period": [
            {
                "raw_value": "Weekly visits for first 4 weeks, then every 4 weeks",
                "quote": "Subjects will attend weekly visits for the first 4 weeks",
                "status": "found",
                "confidence": 0.88,
                "scope": "total",
                "page": 45,
                "notes": None,
            }
        ],
        "total_count": [
            {
                "raw_value": "22",
                "quote": "A total of 22 visits are planned",
                "status": "found",
                "confidence": 0.92,
                "scope": "total",
                "page": 46,
                "notes": None,
            }
        ],
        "schedule_present": None,
    }

    instance = model.model_validate(payload)
    # Round-trip through JSON
    json_str = instance.model_dump_json()
    reparsed = model.model_validate_json(json_str)
    assert reparsed.total_count is not None  # type: ignore[attr-defined]
    assert len(reparsed.total_count) == 1  # type: ignore[attr-defined]
    assert reparsed.total_count[0].raw_value == "22"  # type: ignore[attr-defined]


def test_generated_model_json_schema(fields_yaml_path: Path) -> None:
    """Verify the JSON schema is well-formed for LLM consumption."""
    registry = load_registry(fields_yaml_path)
    model = build_extraction_model("visits", registry=registry)
    schema = model.model_json_schema()

    assert "properties" in schema
    assert "total_count" in schema["properties"]
    # Should be serializable
    json.dumps(schema)


def test_extraction_response_to_records(fields_yaml_path: Path) -> None:
    registry = load_registry(fields_yaml_path)
    model = build_extraction_model("visits", registry=registry)

    payload = {
        "total_count": [
            {
                "raw_value": "22",
                "quote": "A total of 22 visits are planned",
                "status": "found",
                "confidence": 0.92,
                "scope": "total",
                "page": 46,
                "notes": None,
            }
        ],
    }
    instance = model.model_validate(payload)

    records = extraction_response_to_records(
        group_id="visits",
        doc_id="proto1",
        doc_kind="protocol",
        response=instance,
        registry=registry,
    )
    assert len(records) == 1
    assert records[0].field_id == "visits.total_count"
    assert records[0].group == "visits"
    assert records[0].raw_value == "22"
    assert records[0].provenance.doc_id == "proto1"
    assert records[0].provenance.page == 46


def test_the_printed_page_number_is_stamped_on_the_provenance(fields_yaml_path: Path) -> None:
    """Change 7 of the 2026-10-07 review.

    This is the only place in `src/` that builds a `Provenance`, so stamping
    here is what puts the printed page number on every reference in the report.
    A page the document prints no number for, or prints the same number for, is
    not in the map and keeps a provenance of None.
    """
    registry = load_registry(fields_yaml_path)
    model = build_extraction_model("visits", registry=registry)
    item = {"raw_value": "22", "quote": "A total of 22 visits are planned",
            "status": "found", "confidence": 0.92, "scope": "total", "notes": None}
    instance = model.model_validate({"total_count": [item | {"page": 46}, item | {"page": 3}]})

    records = extraction_response_to_records(
        group_id="visits", doc_id="proto1", doc_kind="protocol", response=instance,
        registry=registry, page_labels={46: "44"},
    )
    printed = {r.provenance.page: r.provenance.printed_page for r in records}
    assert printed == {46: "44", 3: None}


def test_without_page_labels_nothing_is_stamped(fields_yaml_path: Path) -> None:
    """Every run before 2026-10-07 has no page labels, and must still load."""
    registry = load_registry(fields_yaml_path)
    model = build_extraction_model("visits", registry=registry)
    instance = model.model_validate({"total_count": [
        {"raw_value": "22", "quote": "A total of 22 visits are planned", "status": "found",
         "confidence": 0.92, "scope": "total", "page": 46, "notes": None},
    ]})
    records = extraction_response_to_records(
        group_id="visits", doc_id="proto1", doc_kind="protocol", response=instance,
        registry=registry,
    )
    assert records[0].provenance.printed_page is None


def test_all_groups_build_successfully(fields_yaml_path: Path) -> None:
    """Every group in the registry should produce a valid extraction model."""
    registry = load_registry(fields_yaml_path)
    for group in registry.groups:
        model = build_extraction_model(group.id, registry=registry)
        assert issubclass(model, BaseModel)
        # Empty payload should be valid (all fields optional)
        instance = model.model_validate({})
        assert instance is not None


def test_the_page_description_names_the_page_marker() -> None:
    """Run r-20261123-opus5-base: the description said "1-indexed page number
    within the excerpt", and Claude Opus 5 counted pages from the start of the
    excerpt. The page is the number in the excerpt's "--- Page N ---" marker."""
    from rfp_intake.domain.dynamic import FieldExtractionItem

    description = FieldExtractionItem.model_fields["page"].description or ""
    assert "--- Page N ---" in description
    assert "within the excerpt" not in description
    # And the JSON schema the model is sent carries the same words.
    schema = FieldExtractionItem.model_json_schema()
    assert schema["properties"]["page"]["description"] == description
