"""Tests for the visit-intensity rubric — pure function, no registry needed."""

from __future__ import annotations

from rfp_intake.derive.rubric import compute_visit_intensity
from rfp_intake.domain.schemas import Provenance, ResolvedField


def _evidence(flags: list[str], confidence: float = 0.85) -> ResolvedField:
    return ResolvedField(
        field_id="visits.intensity_evidence",
        value=flags,
        status="needs_review",
        confidence=confidence,
        sources=[Provenance(doc_id="doc-1", doc_kind="protocol", page=7)],
    )


class TestComputeVisitIntensity:
    def test_no_evidence_field_is_not_specified(self) -> None:
        result = compute_visit_intensity({"visits.intensity_evidence": None})
        assert result.status == "not_specified"
        assert result.value == "not_specified"

    def test_none_found_flag_is_not_specified(self) -> None:
        result = compute_visit_intensity({"visits.intensity_evidence": _evidence(["none_found"])})
        assert result.status == "not_specified"

    def test_low_score(self) -> None:
        # questionnaires(1) = 1 -> low
        evidence = _evidence(["questionnaires"])
        result = compute_visit_intensity({"visits.intensity_evidence": evidence})
        assert result.value == "low"
        assert result.status == "needs_review"

    def test_moderate_score(self) -> None:
        # biomarker_sampling(1) + ecgs(1) + safety_labs(1) + questionnaires(1) = 4 -> moderate
        flags = ["biomarker_sampling", "ecgs", "safety_labs", "questionnaires"]
        result = compute_visit_intensity({"visits.intensity_evidence": _evidence(flags)})
        assert result.value == "moderate"

    def test_high_score(self) -> None:
        # pk_pd_sampling(2) + imaging(2) + infusion_observation_period(2) + overnight_stay(2) = 8
        flags = ["pk_pd_sampling", "imaging", "infusion_observation_period", "overnight_stay"]
        result = compute_visit_intensity({"visits.intensity_evidence": _evidence(flags)})
        assert result.value == "high"

    def test_confidence_and_sources_carried_from_evidence(self) -> None:
        evidence = _evidence(["imaging"], confidence=0.77)
        result = compute_visit_intensity({"visits.intensity_evidence": evidence})
        assert result.confidence == 0.77
        assert result.sources == evidence.sources

    def test_explanation_lists_contributing_flags(self) -> None:
        evidence = _evidence(["imaging", "ecgs"])
        result = compute_visit_intensity({"visits.intensity_evidence": evidence})
        assert "imaging" in result.explanation
        assert "ecgs" in result.explanation

    def test_evidence_not_yet_gated_is_still_read(self) -> None:
        """RECONCILE always writes status=needs_review pre-GATE; DERIVE runs before GATE."""
        evidence = _evidence(["imaging"])
        assert evidence.status == "needs_review"
        result = compute_visit_intensity({"visits.intensity_evidence": evidence})
        assert result.status == "needs_review"

    def test_not_found_evidence_status_is_not_specified(self) -> None:
        evidence = ResolvedField(
            field_id="visits.intensity_evidence", value=None, status="not_found", confidence=0.0,
        )
        result = compute_visit_intensity({"visits.intensity_evidence": evidence})
        assert result.status == "not_specified"
