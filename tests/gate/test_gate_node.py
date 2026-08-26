"""Tests for the GATE graph node."""

from __future__ import annotations

import os
from typing import Literal

from rfp_intake.domain.schemas import (
    Contradiction,
    FieldRecord,
    Provenance,
    ResolvedField,
    RunState,
)
from rfp_intake.gate import gate_node

# ops.sites_total: budget_driver: true in config/fields.yaml
# study.indication: not a budget driver — the control field for that behavior
BUDGET_DRIVER_FIELD = "ops.sites_total"
NON_BUDGET_DRIVER_FIELD = "study.indication"


def _use_real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    from rfp_intake.domain.registry import get_registry
    get_registry.cache_clear()


def _resolved(
    field_id: str = BUDGET_DRIVER_FIELD,
    confidence: float = 0.9,
    status: Literal["confirmed", "needs_review", "not_found", "not_specified"] = "needs_review",
    derived_from: list[str] | None = None,
    scope: str | None = None,
) -> ResolvedField:
    return ResolvedField(
        field_id=field_id,
        value=75,
        status=status,
        confidence=confidence,
        sources=[Provenance(doc_id="doc-1", doc_kind="rfp", page=1)],
        derived_from=derived_from or [],
        scope=scope,
    )


def _contradiction(
    field_id: str,
    verdict: Literal["conflict", "reconcilable", "not_a_conflict"] | None,
    scope: str | None = None,
) -> Contradiction:
    record = FieldRecord(
        field_id=field_id,
        group="operational_metrics",
        raw_value="75",
        quote="q",
        provenance=Provenance(doc_id="doc-1", doc_kind="rfp", page=1),
        confidence=0.9,
        scope=scope,
    )
    return Contradiction(field_id=field_id, records=[record], verdict=verdict)


class TestGateNode:
    def test_high_confidence_confirms(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        state = RunState(run_id="t", resolved=[_resolved(confidence=0.95)])
        result = gate_node(state)
        assert result["resolved"][0].status == "confirmed"

    def test_low_confidence_stays_needs_review(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        state = RunState(run_id="t", resolved=[_resolved(confidence=0.3)])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"

    def test_mid_confidence_needs_review(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        state = RunState(run_id="t", resolved=[_resolved(confidence=0.65)])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"

    def test_derived_field_always_needs_review_even_high_confidence(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        rf = _resolved(
            field_id="visits.intensity_rating",
            confidence=0.99,
            derived_from=["visits.intensity_evidence"],
        )
        state = RunState(run_id="t", resolved=[rf])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"

    def test_not_found_passes_through_unchanged(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        rf = _resolved(status="not_found", confidence=0.0)
        state = RunState(run_id="t", resolved=[rf])
        result = gate_node(state)
        assert result["resolved"][0].status == "not_found"

    def test_not_specified_passes_through_unchanged(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        rf = _resolved(status="not_specified", confidence=0.0)
        state = RunState(run_id="t", resolved=[rf])
        result = gate_node(state)
        assert result["resolved"][0].status == "not_specified"

    def test_conflict_verdict_forces_needs_review(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        rf = _resolved(confidence=0.95)
        contradiction = _contradiction(BUDGET_DRIVER_FIELD, verdict="conflict")
        state = RunState(run_id="t", resolved=[rf], contradictions=[contradiction])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"
        assert result["resolved"][0].contradiction is not None

    def test_reconcilable_verdict_forces_needs_review(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        rf = _resolved(confidence=0.95)
        contradiction = _contradiction(BUDGET_DRIVER_FIELD, verdict="reconcilable")
        state = RunState(run_id="t", resolved=[rf], contradictions=[contradiction])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"

    def test_not_a_conflict_allows_normal_gating_for_non_budget_driver(  # type: ignore[no-untyped-def]
        self, fields_yaml_path,
    ) -> None:
        _use_real_registry(fields_yaml_path)
        rf = _resolved(field_id=NON_BUDGET_DRIVER_FIELD, confidence=0.95)
        contradiction = _contradiction(NON_BUDGET_DRIVER_FIELD, verdict="not_a_conflict")
        state = RunState(run_id="t", resolved=[rf], contradictions=[contradiction])
        result = gate_node(state)
        assert result["resolved"][0].status == "confirmed"

    def test_not_a_conflict_still_forces_review_for_budget_driver(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """A misjudged not_a_conflict on a budget-driving field is the costliest
        place ADJUDICATE's verdict could be wrong — always surface it to a human."""
        _use_real_registry(fields_yaml_path)
        rf = _resolved(field_id=BUDGET_DRIVER_FIELD, confidence=0.99)
        contradiction = _contradiction(BUDGET_DRIVER_FIELD, verdict="not_a_conflict")
        state = RunState(run_id="t", resolved=[rf], contradictions=[contradiction])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"
        assert result["resolved"][0].contradiction is not None

    def test_unadjudicated_contradiction_forces_needs_review(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        rf = _resolved(confidence=0.95)
        contradiction = _contradiction(BUDGET_DRIVER_FIELD, verdict=None)
        state = RunState(run_id="t", resolved=[rf], contradictions=[contradiction])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"

    def test_contradiction_matched_by_scope(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        # Two ResolvedFields with the same non-budget-driver field_id but
        # different scopes; only the matching scope's contradiction should
        # force needs_review (budget-driver would force both regardless).
        _use_real_registry(fields_yaml_path)
        rf_de = _resolved(field_id=NON_BUDGET_DRIVER_FIELD, confidence=0.95, scope="country:DE")
        rf_us = _resolved(field_id=NON_BUDGET_DRIVER_FIELD, confidence=0.95, scope="country:US")
        contradiction = _contradiction(
            NON_BUDGET_DRIVER_FIELD, verdict="conflict", scope="country:DE",
        )
        state = RunState(run_id="t", resolved=[rf_de, rf_us], contradictions=[contradiction])
        result = gate_node(state)

        by_scope = {r.scope: r.status for r in result["resolved"]}
        assert by_scope["country:DE"] == "needs_review"
        assert by_scope["country:US"] == "confirmed"
