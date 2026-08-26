"""Tests for the ADJUDICATE graph node."""

from __future__ import annotations

import os
from typing import Literal

from rfp_intake.adjudicate import (
    AdjudicationResult,
    _handle_conflict,
    _handle_not_a_conflict,
    _handle_reconcilable,
    adjudicate_node,
)
from rfp_intake.domain.schemas import Contradiction, FieldRecord, Provenance, RunState


def _use_real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    from rfp_intake.domain.registry import get_registry
    get_registry.cache_clear()


DocKind = Literal["rfp", "protocol", "amendment", "soa", "other"]


def _record(value: object, doc_id: str, doc_kind: DocKind = "rfp", confidence: float = 0.9,
            scope: str | None = None) -> FieldRecord:
    r = FieldRecord(
        field_id="ops.sites_total",
        group="operational_metrics",
        raw_value=str(value),
        quote=f"quote {value}",
        provenance=Provenance(doc_id=doc_id, doc_kind=doc_kind, page=1),
        confidence=confidence,
        scope=scope,
    )
    r.value = value
    return r


def _candidate(records: list[FieldRecord]) -> Contradiction:
    return Contradiction(field_id="ops.sites_total", records=records)


class TestHandleNotAConflict:
    def test_unpacks_one_resolved_field_per_record(self) -> None:
        records = [_record(40, "a"), _record(52, "b")]
        c = _candidate(records).model_copy(update={
            "verdict": "not_a_conflict", "explanation": "different scopes",
        })

        contradiction, fields = _handle_not_a_conflict(c)

        assert len(fields) == 2
        assert {f.value for f in fields} == {40, 52}
        assert all(f.status == "needs_review" for f in fields)
        assert all(f.contradiction is contradiction for f in fields)
        assert all("different scopes" in (f.notes or "") for f in fields)


class TestHandleReconcilable:
    def test_uses_llm_chosen_winner(self) -> None:
        records = [_record(40, "a"), _record(52, "b")]
        c = _candidate(records).model_copy(update={
            "verdict": "reconcilable", "explanation": "b is more specific",
        })
        result = AdjudicationResult(
            verdict="reconcilable", explanation="b is more specific", winning_doc_id="b",
        )

        contradiction, fields = _handle_reconcilable(c, result)

        assert len(fields) == 1
        assert fields[0].value == 52
        assert fields[0].sources and len(fields[0].sources) == 2  # both provenances kept
        assert contradiction.winning_doc_id == "b"
        assert contradiction.resolved_value == 52

    def test_falls_back_to_highest_confidence_if_winning_doc_id_unmatched(self) -> None:
        records = [_record(40, "a", confidence=0.6), _record(52, "b", confidence=0.95)]
        c = _candidate(records)
        result = AdjudicationResult(
            verdict="reconcilable", explanation="x", winning_doc_id="does-not-exist",
        )

        _, fields = _handle_reconcilable(c, result)
        assert fields[0].value == 52


class TestHandleConflict:
    def test_applies_deterministic_precedence(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        field_def = get_registry().get_field("ops.sites_total")  # source_priority: rfp
        records = [_record(40, "proto", doc_kind="protocol"), _record(75, "rfp1", doc_kind="rfp")]
        c = _candidate(records).model_copy(update={
            "verdict": "conflict", "explanation": "genuine mismatch",
        })

        contradiction, fields = _handle_conflict(c, field_def)

        assert fields[0].value == 75  # rfp wins domain authority for this field
        assert "precedence: domain_authority" in (fields[0].notes or "")
        assert contradiction.winning_doc_id == "rfp1"

    def test_no_value_when_precedence_undecided(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        field_def = get_registry().get_field("ops.sites_total")
        records = [
            _record(40, "rfp1", doc_kind="rfp", confidence=0.9),
            _record(75, "rfp2", doc_kind="rfp", confidence=0.9),
        ]
        c = _candidate(records).model_copy(update={
            "verdict": "conflict", "explanation": "genuine mismatch",
        })

        contradiction, fields = _handle_conflict(c, field_def)

        assert fields[0].value is None  # no_silent_resolution — do not guess
        assert fields[0].status == "needs_review"


class TestAdjudicateNode:
    def test_already_adjudicated_passes_through(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        c = _candidate([_record(40, "a")]).model_copy(update={
            "verdict": "not_a_conflict", "explanation": "already done", "severity": "low",
        })
        state = RunState(run_id="t", contradictions=[c])

        result = adjudicate_node(state)
        assert result["contradictions"] == [c]
        assert result["resolved"] == []

    def test_unknown_field_id_is_a_run_error(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        c = Contradiction(field_id="not.a.real.field", records=[_record(40, "a")])
        state = RunState(run_id="t", contradictions=[c])

        result = adjudicate_node(state)
        assert result["contradictions"] == [c]  # unchanged, verdict still None
        assert len(result["errors"]) == 1
        assert "not.a.real.field" in result["errors"][0].error

    def test_mock_default_fixture_resolves_via_not_a_conflict(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Mock LLM's default adjudicate fixture always returns not_a_conflict."""
        _use_real_registry(fields_yaml_path)
        records = [_record(40, "a"), _record(52, "b")]
        c = _candidate(records)
        state = RunState(run_id="t", contradictions=[c])

        result = adjudicate_node(state)

        adjudicated = result["contradictions"][0]
        assert adjudicated.verdict == "not_a_conflict"
        assert len(result["resolved"]) == 2  # one ResolvedField per original record
