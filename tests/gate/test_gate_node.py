"""Tests for the GATE graph node."""

from __future__ import annotations

from typing import Literal

from rfp_intake.domain.schemas import (
    Contradiction,
    FieldRecord,
    Provenance,
    ResolvedField,
    RunState,
)
from rfp_intake.gate import gate_node


def _resolved(
    field_id: str = "ops.sites_total",
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
    def test_high_confidence_confirms(self) -> None:
        state = RunState(run_id="t", resolved=[_resolved(confidence=0.95)])
        result = gate_node(state)
        assert result["resolved"][0].status == "confirmed"

    def test_low_confidence_stays_needs_review(self) -> None:
        state = RunState(run_id="t", resolved=[_resolved(confidence=0.3)])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"

    def test_mid_confidence_needs_review(self) -> None:
        state = RunState(run_id="t", resolved=[_resolved(confidence=0.65)])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"

    def test_derived_field_always_needs_review_even_high_confidence(self) -> None:
        rf = _resolved(
            field_id="visits.intensity_rating",
            confidence=0.99,
            derived_from=["visits.intensity_evidence"],
        )
        state = RunState(run_id="t", resolved=[rf])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"

    def test_not_found_passes_through_unchanged(self) -> None:
        rf = _resolved(status="not_found", confidence=0.0)
        state = RunState(run_id="t", resolved=[rf])
        result = gate_node(state)
        assert result["resolved"][0].status == "not_found"

    def test_not_specified_passes_through_unchanged(self) -> None:
        rf = _resolved(status="not_specified", confidence=0.0)
        state = RunState(run_id="t", resolved=[rf])
        result = gate_node(state)
        assert result["resolved"][0].status == "not_specified"

    def test_conflict_verdict_forces_needs_review(self) -> None:
        rf = _resolved(confidence=0.95)
        contradiction = _contradiction("ops.sites_total", verdict="conflict")
        state = RunState(run_id="t", resolved=[rf], contradictions=[contradiction])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"
        assert result["resolved"][0].contradiction is not None

    def test_reconcilable_verdict_forces_needs_review(self) -> None:
        rf = _resolved(confidence=0.95)
        contradiction = _contradiction("ops.sites_total", verdict="reconcilable")
        state = RunState(run_id="t", resolved=[rf], contradictions=[contradiction])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"

    def test_not_a_conflict_verdict_allows_normal_gating(self) -> None:
        rf = _resolved(confidence=0.95)
        contradiction = _contradiction("ops.sites_total", verdict="not_a_conflict")
        state = RunState(run_id="t", resolved=[rf], contradictions=[contradiction])
        result = gate_node(state)
        assert result["resolved"][0].status == "confirmed"

    def test_unadjudicated_contradiction_forces_needs_review(self) -> None:
        rf = _resolved(confidence=0.95)
        contradiction = _contradiction("ops.sites_total", verdict=None)
        state = RunState(run_id="t", resolved=[rf], contradictions=[contradiction])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"

    def test_contradiction_matched_by_scope(self) -> None:
        # Two ResolvedFields with the same field_id but different scopes;
        # only the matching scope's contradiction should force needs_review.
        rf_de = _resolved(confidence=0.95, scope="country:DE")
        rf_us = _resolved(confidence=0.95, scope="country:US")
        contradiction = _contradiction("ops.sites_total", verdict="conflict", scope="country:DE")
        state = RunState(run_id="t", resolved=[rf_de, rf_us], contradictions=[contradiction])
        result = gate_node(state)

        by_scope = {r.scope: r.status for r in result["resolved"]}
        assert by_scope["country:DE"] == "needs_review"
        assert by_scope["country:US"] == "confirmed"
