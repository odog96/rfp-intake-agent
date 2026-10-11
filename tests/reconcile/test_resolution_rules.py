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
