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


def _evidence(flags: list[str], scope: str | None, page: int) -> ResolvedField:
    return ResolvedField(
        field_id="visits.intensity_evidence",
        value=flags,
        status="needs_review",
        confidence=0.85,
        scope=scope,
        sources=[Provenance(doc_id="doc-1", doc_kind="protocol", page=page)],
    )


def _rating(rows: list[ResolvedField]) -> ResolvedField:
    from rfp_intake.derive import derive_node

    result = derive_node(RunState(run_id="t", resolved=rows))
    return next(r for r in result["resolved"] if r.field_id == "visits.intensity_rating")


# r-20261011-fix1's two rows: the study-wide one scored 14, "Months 1-3" scored 2,
# and the report said low because the second row replaced the first.
_STUDY_WIDE = [
    "pk_pd_sampling", "biomarker_sampling", "imaging", "ecgs", "safety_labs",
    "questionnaires", "infusion_observation_period", "frequent_early_visits",
    "multiple_assessments_per_visit",
]


class TestDeriveNodeReadsEveryRow:
    def test_a_later_narrower_row_no_longer_replaces_the_wide_row(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        rating = _rating([
            _evidence(_STUDY_WIDE, None, 40),
            _evidence(["ecgs", "safety_labs"], "Months 1-3", 41),
        ])
        assert rating.value == "high"
        assert rating.notes is not None
        assert "score=14 " in rating.notes
        assert "left out" not in rating.notes

    def test_a_row_about_patients_who_stopped_early_is_left_out(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """docs/ANALYST_PROCEDURE_PROTOCOL.md section 9: dropouts are budgeted apart."""
        _use_real_registry(fields_yaml_path)
        rating = _rating([
            _evidence(["questionnaires"], None, 40),
            _evidence(
                ["pk_pd_sampling", "imaging", "overnight_stay", "complex_procedures"],
                "subjects who discontinue study drug early", 52,
            ),
        ])
        assert rating.value == "low"  # the dropout row alone would have made it high
        assert rating.notes is not None
        assert (
            'left out Evidence of visit intensity for "subjects who discontinue study drug early" '
            "(protocol p.52): it is about patients who stopped treatment early"
        ) in rating.notes
        assert [p.page for p in rating.sources] == [40]

    def test_the_end_of_treatment_visit_is_not_left_out(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """"Early Termination" also names the last visit of the main schedule."""
        _use_real_registry(fields_yaml_path)
        rating = _rating([
            _evidence(["questionnaires"], None, 40),
            _evidence(["imaging"], "End of Treatment / Early Termination visit", 44),
        ])
        assert rating.notes is not None
        assert "left out" not in rating.notes
        assert "score=3 " in rating.notes

    def test_rows_with_one_scope_are_named_once_with_each_page_once(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """r-20260827-180418 split one dropout schedule into five rows on two pages."""
        _use_real_registry(fields_yaml_path)
        scope = "Early Treatment Discontinuation subjects"
        rating = _rating([
            _evidence(["questionnaires"], None, 40),
            _evidence(["imaging"], scope, 111),
            _evidence(["ecgs"], scope, 112),
            _evidence(["safety_labs"], scope, 111),
        ])
        assert rating.notes is not None
        assert rating.notes.count(scope) == 1
        assert f'"{scope}" (protocol p.111, protocol p.112):' in rating.notes

    def test_every_row_left_out_is_not_specified_and_still_named(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        rating = _rating([_evidence(["imaging"], "Post-ETD follow-up", 60)])
        assert rating.status == "not_specified"
        assert rating.notes is not None
        assert 'left out Evidence of visit intensity for "Post-ETD follow-up"' in rating.notes
