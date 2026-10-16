"""Tests for reconcile/rules.py — answers that follow from another field.

Change 3 of the 2026-10-07 review: a Phase 2, 3 or 4 study is never
first-in-human. The rule itself is in config/fields.yaml, so these tests read it
from the registry rather than restating it; what they pin down is the engine's
behaviour and the one rule the registry ships with today.
"""

from __future__ import annotations

import os

import pytest

from rfp_intake.domain.registry import get_registry
from rfp_intake.domain.schemas import (
    Contradiction,
    FieldRecord,
    Provenance,
    ResolvedField,
    RunState,
)
from rfp_intake.gate import gate_node
from rfp_intake.reconcile import reconcile_node
from rfp_intake.reconcile.rules import apply_resolution_rules

FIH = "design.first_in_human"
PHASE = "study.phase"


@pytest.fixture(autouse=True)
def _real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    get_registry.cache_clear()


def _record(field_id: str, group: str, value: str, page: int) -> FieldRecord:
    return FieldRecord(
        field_id=field_id,
        group=group,
        raw_value=value,
        value=value,
        quote=f"quote for {value}",
        provenance=Provenance(doc_id="doc-1", doc_kind="protocol", page=page),
        confidence=0.9,
        status="found",
    )


def _resolved(field_id: str, value: str, page: int = 7) -> ResolvedField:
    return ResolvedField(
        field_id=field_id,
        value=value,
        status="needs_review",
        confidence=0.9,
        sources=[Provenance(doc_id="doc-1", doc_kind="protocol", page=page)],
        quote=f"quote for {value}",
    )


class TestTheRuleTheRegistryShips:
    def test_the_rule_is_in_configuration_not_in_python(self) -> None:
        [rule] = get_registry().get_field(FIH).resolution_rules
        assert rule.when_field == PHASE
        assert rule.value == "false"
        assert rule.confidence == 1.0
        assert "phase_1" not in rule.when_value_in
        assert "phase_1_2" not in rule.when_value_in


class TestTheEngine:
    def test_a_phase_3_study_is_answered_no_at_full_confidence(self) -> None:
        registry = get_registry()
        resolved, contradictions = apply_resolution_rules(
            registry, [_resolved(PHASE, "phase_3", page=5)], [],
        )
        [answer] = [rf for rf in resolved if rf.field_id == FIH]
        assert answer.value == "false"
        assert answer.confidence == 1.0
        assert [p.page for p in answer.sources] == [5], "the phase passage is the evidence"
        assert answer.quote == "quote for phase_3", "rule 2: no value without evidence"
        assert answer.notes

    def test_a_phase_1_study_is_left_to_extraction(self) -> None:
        registry = get_registry()
        resolved, _ = apply_resolution_rules(
            registry, [_resolved(PHASE, "phase_1"), _resolved(FIH, "true")], [],
        )
        [answer] = [rf for rf in resolved if rf.field_id == FIH]
        assert answer.value == "true", "Phase 1 still needs checking"

    def test_the_rule_overrides_what_was_extracted(self) -> None:
        registry = get_registry()
        resolved, _ = apply_resolution_rules(
            registry, [_resolved(PHASE, "phase_3"), _resolved(FIH, "not_specified")], [],
        )
        assert [rf.value for rf in resolved if rf.field_id == FIH] == ["false"]

    def test_the_rule_settles_a_candidate_disagreement(self) -> None:
        registry = get_registry()
        candidate = Contradiction(
            field_id=FIH,
            records=[
                _record(FIH, "study_design", "true", 11),
                _record(FIH, "study_design", "false", 18),
            ],
        )
        resolved, contradictions = apply_resolution_rules(
            registry, [_resolved(PHASE, "phase_3")], [candidate],
        )
        assert contradictions == [], "a stated clinical rule is not outvoted by the documents"
        assert [rf.value for rf in resolved if rf.field_id == FIH] == ["false"]

    def test_nothing_happens_when_the_trigger_field_has_no_answer(self) -> None:
        registry = get_registry()
        resolved, contradictions = apply_resolution_rules(registry, [], [])
        assert [rf for rf in resolved if rf.field_id == FIH] == []


class TestThroughReconcileAndGate:
    def test_the_report_reads_no_confirmed_100_percent(self) -> None:
        """What check 3 of the review asks for, measured where GATE decides it."""
        state = RunState(run_id="t", records=[
            _record(PHASE, "phase_population", "phase_3", 5),
            _record(FIH, "study_design", "not_specified", 18),
        ])
        state = state.model_copy(update=reconcile_node(state))
        gated = gate_node(state)["resolved"]
        [answer] = [rf for rf in gated if rf.field_id == FIH]
        assert (answer.value, answer.status, answer.confidence) == ("false", "confirmed", 1.0)


HV = "design.healthy_volunteers"
HV_SCOPE = "design.healthy_volunteer_scope"


class TestAQuestionThatDoesNotArise:
    """Prompt B item 4 (Angus Gray, 13 October call, 11:30-11:55): with no healthy
    volunteers, which cohort includes them reads N/A, Not applicable, not Not found."""

    def test_the_dependency_is_in_configuration(self) -> None:
        [rule] = get_registry().get_field(HV_SCOPE).resolution_rules
        assert (rule.when_field, rule.when_value_in) == (HV, ["false"])
        assert (rule.value, rule.status) == (None, "not_applicable")

    def test_no_healthy_volunteers_makes_the_cohort_not_applicable(self) -> None:
        state = RunState(run_id="t", records=[
            _record(HV, "study_design", "false", 12),
        ])
        state = state.model_copy(update=reconcile_node(state))
        gated = gate_node(state)["resolved"]
        [answer] = [rf for rf in gated if rf.field_id == HV_SCOPE]
        assert (answer.value, answer.status) == (None, "not_applicable")
        assert [p.page for p in answer.sources] == [12], "the healthy-volunteer passage"
        assert answer.notes and "healthy volunteers" in answer.notes

    def test_healthy_volunteers_leave_the_cohort_to_extraction(self) -> None:
        state = RunState(run_id="t", records=[
            _record(HV, "study_design", "true", 12),
            _record(HV_SCOPE, "study_design", "Part A", 14),
        ])
        state = state.model_copy(update=reconcile_node(state))
        [answer] = [rf for rf in state.resolved if rf.field_id == HV_SCOPE]
        assert answer.value == "Part A"

    def test_the_report_reads_n_a_not_applicable(self) -> None:
        from rfp_intake.render.pdf_renderer import build_report_pdf
        from rfp_intake.render.report_model import build_report_model

        state = RunState(run_id="t", records=[_record(HV, "study_design", "false", 12)])
        state = state.model_copy(update=reconcile_node(state))
        state = state.model_copy(update=gate_node(state))
        model = build_report_model(state, get_registry())
        [row] = [r for r in model.all_rows() if r.field_id == HV_SCOPE]
        assert (row.status, [v.text for v in row.values]) == ("not_applicable", ["N/A"])
        [e] = [e for e in model.evidence if e.field_id == HV_SCOPE]
        assert e.value == "N/A"
        pdf = build_report_pdf(state, get_registry(), generated_at="2026-10-16")
        import pymupdf
        text = " ".join(pymupdf.open(stream=pdf, filetype="pdf")[0].get_text().split())  # type: ignore[no-untyped-call]
        assert "Which cohort/part includes healthy volunteers N/A Not applicable" in text

    def test_a_rule_gives_a_value_or_a_status_not_both(self) -> None:
        from pydantic import ValidationError

        from rfp_intake.domain.registry import ResolutionRule

        with pytest.raises(ValidationError):
            ResolutionRule(when_field=HV, when_value_in=["no"])
        with pytest.raises(ValidationError):
            ResolutionRule(
                when_field=HV, when_value_in=["no"], value="x", status="not_applicable",
            )

    def test_one_part_with_healthy_volunteers_keeps_the_cohort(self) -> None:
        """A "No" for one part and a "Yes" for another must not hide the
        extracted cohort behind N/A (gate reviewer on item 4)."""
        cohort = _resolved(HV_SCOPE, "Part A", 14)
        resolved, _ = apply_resolution_rules(
            get_registry(),
            [_resolved(HV, "false", 12), _resolved(HV, "true", 13), cohort],
            [],
        )
        assert [rf for rf in resolved if rf.field_id == HV_SCOPE] == [cohort]

    def test_the_workbook_reads_n_a_and_leaves_it_out_of_the_review_queue(self) -> None:
        import io

        from openpyxl import load_workbook

        from rfp_intake.render.xlsx_renderer import build_report_xlsx

        state = RunState(run_id="t", records=[_record(HV, "study_design", "false", 12)])
        state = state.model_copy(update=reconcile_node(state))
        state = state.model_copy(update=gate_node(state))
        wb = load_workbook(io.BytesIO(build_report_xlsx(state, get_registry())))
        label = get_registry().get_field(HV_SCOPE).label
        [row] = [r for r in wb["Fields"].iter_rows(values_only=True) if r[1] == label]
        assert (row[2], row[3]) == ("N/A", "not_applicable")
        assert all(r[1] != label for r in wb["Review Queue"].iter_rows(values_only=True))
