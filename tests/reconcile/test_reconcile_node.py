"""Tests for the RECONCILE graph node."""

from __future__ import annotations

import os
from typing import Literal

from rfp_intake.domain.schemas import FieldRecord, Provenance, RunState


def _use_real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    from rfp_intake.domain.registry import get_registry
    get_registry.cache_clear()


def _record(
    field_id: str,
    value: object,
    confidence: float,
    doc_id: str = "doc-1",
    scope: str | None = None,
    status: Literal["found", "not_specified", "not_found"] = "found",
) -> FieldRecord:
    r = FieldRecord(
        field_id=field_id,
        group="operational_metrics",
        raw_value=str(value),
        quote=f"quote for {value}",
        provenance=Provenance(doc_id=doc_id, doc_kind="rfp", page=1),
        confidence=confidence,
        scope=scope,
        status=status,
    )
    r.value = value  # bypass NORMALIZE — RECONCILE reads .value directly
    return r


class TestReconcileNode:
    def test_single_record_resolves_pending_gate(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.reconcile import reconcile_node

        record = _record("ops.sites_total", 75, 0.9)
        state = RunState(run_id="t", records=[record])
        result = reconcile_node(state)

        matches = [r for r in result["resolved"] if r.field_id == "ops.sites_total"]
        assert len(matches) == 1
        rf = matches[0]
        assert rf.value == 75
        assert rf.confidence == 0.9
        assert rf.status == "needs_review"  # GATE decides confirmed, not RECONCILE
        assert result["contradictions"] == []

    def test_agreeing_records_boost_confidence(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.reconcile import reconcile_node

        r1 = _record("ops.sites_total", 75, 0.80, doc_id="rfp")
        r2 = _record("ops.sites_total", 75, 0.70, doc_id="protocol")
        state = RunState(run_id="t", records=[r1, r2])
        result = reconcile_node(state)

        rf = next(r for r in result["resolved"] if r.field_id == "ops.sites_total")
        assert rf.value == 75
        assert rf.confidence > 0.80  # boosted above the best individual confidence
        assert len(rf.sources) == 2

    def test_disagreeing_records_become_contradiction_candidate(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.reconcile import reconcile_node

        r1 = _record("ops.sites_total", 75, 0.9, doc_id="rfp")
        r2 = _record("ops.sites_total", 40, 0.9, doc_id="protocol")
        state = RunState(run_id="t", records=[r1, r2])
        result = reconcile_node(state)

        # No resolved entry for the disagreeing field/scope yet.
        assert not any(r.field_id == "ops.sites_total" for r in result["resolved"])

        candidates = [c for c in result["contradictions"] if c.field_id == "ops.sites_total"]
        assert len(candidates) == 1
        assert candidates[0].verdict is None  # not yet adjudicated
        assert len(candidates[0].records) == 2

    def test_different_scopes_are_not_a_contradiction(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.reconcile import reconcile_node

        r1 = _record("ops.sites_total", 40, 0.9, scope="country:DE")
        r2 = _record("ops.sites_total", 12, 0.9, scope="country:US")
        state = RunState(run_id="t", records=[r1, r2])
        result = reconcile_node(state)

        assert result["contradictions"] == []
        matches = {r.scope: r.value for r in result["resolved"] if r.field_id == "ops.sites_total"}
        assert matches == {"country:DE": 40, "country:US": 12}

    def test_not_specified_only_resolves_as_not_specified(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.reconcile import reconcile_node

        record = _record("ops.sites_total", None, 0.9, status="not_specified")
        state = RunState(run_id="t", records=[record])
        result = reconcile_node(state)

        rf = next(r for r in result["resolved"] if r.field_id == "ops.sites_total")
        assert rf.status == "not_specified"
        assert rf.value is None

    def test_found_beats_not_specified_from_another_document(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.reconcile import reconcile_node

        found = _record("ops.sites_total", 75, 0.9, doc_id="protocol")
        not_specified = _record("ops.sites_total", None, 0.9, doc_id="rfp", status="not_specified")
        state = RunState(run_id="t", records=[found, not_specified])
        result = reconcile_node(state)

        rf = next(r for r in result["resolved"] if r.field_id == "ops.sites_total")
        assert rf.status == "needs_review"
        assert rf.value == 75

    def test_field_with_no_records_is_not_found(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.reconcile import reconcile_node

        state = RunState(run_id="t", records=[])
        result = reconcile_node(state)

        rf = next(r for r in result["resolved"] if r.field_id == "ops.sites_total")
        assert rf.status == "not_found"
        assert rf.value is None
        assert rf.sources == []

    def test_derived_fields_are_skipped(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """RECONCILE never emits a not_found row for a derived field — DERIVE owns it."""
        _use_real_registry(fields_yaml_path)
        from rfp_intake.reconcile import reconcile_node

        state = RunState(run_id="t", records=[])
        result = reconcile_node(state)

        assert not any(r.field_id == "visits.intensity_rating" for r in result["resolved"])
