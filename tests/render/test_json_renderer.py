"""Tests for render/json_renderer.py."""

from __future__ import annotations

import os

from rfp_intake.domain.schemas import (
    Contradiction,
    FieldRecord,
    Provenance,
    ResolvedField,
    RunState,
)
from rfp_intake.render.json_renderer import build_extraction_document


def _use_real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    from rfp_intake.domain.registry import get_registry
    get_registry.cache_clear()


class TestBuildExtractionDocument:
    def test_includes_run_id_and_registry_version(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        state = RunState(run_id="r-1")
        doc = build_extraction_document(state, get_registry())
        assert doc["run_id"] == "r-1"
        assert doc["registry_version"].startswith("v1:")

    def test_resolved_field_enriched_with_group_and_label(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        rf = ResolvedField(
            field_id="ops.sites_total", value=75, status="confirmed", confidence=0.9,
            sources=[Provenance(doc_id="d1", doc_kind="rfp", page=3)], quote="75 sites",
        )
        state = RunState(run_id="r-1", resolved=[rf])
        doc = build_extraction_document(state, get_registry())

        entry = doc["resolved_fields"][0]
        assert entry["field_id"] == "ops.sites_total"
        assert entry["group"] == "operational_metrics"
        assert entry["label"] == "Total number of sites"  # from fields.yaml
        assert entry["value"] == 75
        assert entry["sources"][0]["doc_id"] == "d1"

    def test_unknown_field_id_falls_back_to_field_id_as_label(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        rf = ResolvedField(field_id="not.a.real.field", value=1, status="confirmed", confidence=0.9)
        state = RunState(run_id="r-1", resolved=[rf])
        doc = build_extraction_document(state, get_registry())

        entry = doc["resolved_fields"][0]
        assert entry["group"] is None
        assert entry["label"] == "not.a.real.field"

    def test_contradiction_serialized_when_present(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        record = FieldRecord(
            field_id="ops.sites_total", group="operational_metrics", raw_value="75",
            quote="q", provenance=Provenance(doc_id="d1", doc_kind="rfp", page=1), confidence=0.9,
        )
        contradiction = Contradiction(
            field_id="ops.sites_total", records=[record], verdict="conflict",
            explanation="mismatch", severity="high",
        )
        rf = ResolvedField(
            field_id="ops.sites_total", value=None, status="needs_review", confidence=0.5,
            contradiction=contradiction,
        )
        state = RunState(run_id="r-1", resolved=[rf], contradictions=[contradiction])
        doc = build_extraction_document(state, get_registry())

        assert doc["resolved_fields"][0]["contradiction"]["verdict"] == "conflict"
        assert doc["contradictions"][0]["field_id"] == "ops.sites_total"

    def test_no_resolved_fields_yields_empty_list(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        state = RunState(run_id="r-1")
        doc = build_extraction_document(state, get_registry())
        assert doc["resolved_fields"] == []
        assert doc["contradictions"] == []
        assert doc["errors"] == []
