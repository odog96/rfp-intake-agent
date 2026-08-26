"""Tests for the DERIVE graph node."""

from __future__ import annotations

import os

from rfp_intake.domain.schemas import Provenance, ResolvedField, RunState


def _use_real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    from rfp_intake.domain.registry import get_registry
    get_registry.cache_clear()


class TestDeriveNode:
    def test_computes_visit_intensity_from_resolved_evidence(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.derive import derive_node

        evidence = ResolvedField(
            field_id="visits.intensity_evidence",
            value=["pk_pd_sampling", "imaging"],
            status="needs_review",
            confidence=0.9,
            sources=[Provenance(doc_id="doc-1", doc_kind="protocol", page=3)],
        )
        state = RunState(run_id="t", resolved=[evidence])
        result = derive_node(state)

        rating = next(r for r in result["resolved"] if r.field_id == "visits.intensity_rating")
        assert rating.value == "moderate"  # 2 + 2 = 4
        assert rating.status == "needs_review"
        assert rating.derived_from == [
            "visits.intensity_evidence", "visits.frequency_by_period", "visits.total_count",
        ]
        assert rating.notes  # contributing-evidence explanation is present

    def test_preserves_prior_resolved_entries(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.derive import derive_node

        other = ResolvedField(
            field_id="ops.sites_total", value=75, status="needs_review", confidence=0.9,
        )
        state = RunState(run_id="t", resolved=[other])
        result = derive_node(state)

        assert any(r.field_id == "ops.sites_total" for r in result["resolved"])
        assert any(r.field_id == "visits.intensity_rating" for r in result["resolved"])

    def test_missing_evidence_yields_not_specified(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.derive import derive_node

        state = RunState(run_id="t", resolved=[])
        result = derive_node(state)

        rating = next(r for r in result["resolved"] if r.field_id == "visits.intensity_rating")
        assert rating.status == "not_specified"
        assert result["errors"] == []
