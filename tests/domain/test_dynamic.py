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


def test_build_extraction_model_for_visits(fields_yaml_path: Path) -> None:
    registry = load_registry(fields_yaml_path)
    model = build_extraction_model("visits", registry=registry)

    assert issubclass(model, BaseModel)
    assert model.__name__ == "VisitsExtraction"

    # Should have non-derived fields only
    field_names = set(model.model_fields.keys())
    assert "frequency_by_period" in field_names
    assert "total_count" in field_names
    assert "intensity_evidence" in field_names
    # Derived field should be excluded
    assert "intensity_rating" not in field_names


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
        "intensity_evidence": None,
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


def test_all_groups_build_successfully(fields_yaml_path: Path) -> None:
    """Every group in the registry should produce a valid extraction model."""
    registry = load_registry(fields_yaml_path)
    for group in registry.groups:
        model = build_extraction_model(group.id, registry=registry)
        assert issubclass(model, BaseModel)
        # Empty payload should be valid (all fields optional)
        instance = model.model_validate({})
        assert instance is not None
