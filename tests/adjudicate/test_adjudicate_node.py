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
            scope: str | None = None, page: int = 1) -> FieldRecord:
    r = FieldRecord(
        field_id="ops.sites_total",
        group="operational_metrics",
        raw_value=str(value),
        quote=f"quote {value}",
        provenance=Provenance(doc_id=doc_id, doc_kind=doc_kind, page=page),
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
    def test_uses_llm_chosen_winning_record(self) -> None:
        records = [_record(40, "a"), _record(52, "b")]
        c = _candidate(records).model_copy(update={
            "verdict": "reconcilable", "explanation": "b is more specific",
        })
        result = AdjudicationResult(
            verdict="reconcilable", explanation="b is more specific", winning_record=2,
        )

        contradiction, fields = _handle_reconcilable(c, result)

        assert len(fields) == 1
        assert fields[0].value == 52
        assert fields[0].sources and len(fields[0].sources) == 2  # both provenances kept
        assert contradiction.winning_doc_id == "b"
        assert contradiction.winning_record_index == 2
        assert contradiction.resolved_value == 52

    def test_picks_a_later_record_from_the_same_document(self) -> None:
        """The stage 5 defect: blinding.unblinded_staff_stated had four records from
        one protocol — page 20 not_specified, then pages 52/54/54 all true — and
        choosing by document took page 20. Choosing by record number takes page 54.
        """
        records = [
            _record("not_specified", "proto", doc_kind="protocol", confidence=0.9, page=20),
            _record("true", "proto", doc_kind="protocol", confidence=1.0, page=52),
            _record("true", "proto", doc_kind="protocol", confidence=1.0, page=54),
        ]
        c = _candidate(records).model_copy(update={
            "verdict": "reconcilable", "explanation": "the later sections state it",
        })
        result = AdjudicationResult(
            verdict="reconcilable", explanation="the later sections state it", winning_record=3,
        )

        contradiction, fields = _handle_reconcilable(c, result)

        assert fields[0].value == "true"
        assert fields[0].quote == "quote true"
        assert contradiction.winning_record_index == 3
        assert contradiction.winning_doc_id == "proto"
        assert fields[0].sources and len(fields[0].sources) == 3

    def test_falls_back_to_highest_confidence_when_no_record_is_named(self) -> None:
        records = [_record(40, "a", confidence=0.6), _record(52, "b", confidence=0.95)]
        c = _candidate(records)
        result = AdjudicationResult(verdict="reconcilable", explanation="x")

        contradiction, fields = _handle_reconcilable(c, result)

        assert fields[0].value == 52
        assert contradiction.winning_record_index == 2

    def test_falls_back_to_highest_confidence_when_the_number_is_out_of_range(self) -> None:
        records = [_record(40, "a", confidence=0.6), _record(52, "b", confidence=0.95)]
        c = _candidate(records)
        result = AdjudicationResult(
            verdict="reconcilable", explanation="x", winning_record=7,
        )

        contradiction, fields = _handle_reconcilable(c, result)

        assert fields[0].value == 52
        assert contradiction.winning_record_index == 2

    def test_a_zero_or_negative_record_number_is_not_treated_as_an_index(self) -> None:
        """0 and -1 are valid Python indices and would silently pick a record."""
        records = [_record(40, "a", confidence=0.6), _record(52, "b", confidence=0.95)]
        for bad in (0, -1):
            result = AdjudicationResult(
                verdict="reconcilable", explanation="x", winning_record=bad,
            )
            _, fields = _handle_reconcilable(_candidate(records), result)
            assert fields[0].value == 52, f"winning_record={bad} should fall back"

    def test_highest_confidence_tie_goes_to_the_earlier_record(self) -> None:
        records = [_record(40, "a", confidence=0.9), _record(52, "b", confidence=0.9)]
        result = AdjudicationResult(verdict="reconcilable", explanation="x")

        contradiction, fields = _handle_reconcilable(_candidate(records), result)

        assert fields[0].value == 40
        assert contradiction.winning_record_index == 1


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

    def test_the_quote_is_the_best_record_from_the_winning_document(
        self, fields_yaml_path,  # type: ignore[no-untyped-def]
    ) -> None:
        """Precedence names a document, so the quote has to be chosen among that
        document's records. Taking the first one printed the earliest page's quote
        against a value decided by precedence.
        """
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        field_def = get_registry().get_field("ops.sites_total")  # source_priority: rfp
        records = [
            _record(40, "proto", doc_kind="protocol", confidence=0.9),
            _record(60, "rfp1", doc_kind="rfp", confidence=0.7, page=3),
            _record(75, "rfp1", doc_kind="rfp", confidence=0.99, page=9),
        ]
        c = _candidate(records).model_copy(update={
            "verdict": "conflict", "explanation": "genuine mismatch",
        })

        contradiction, fields = _handle_conflict(c, field_def)

        assert contradiction.winning_doc_id == "rfp1"
        assert fields[0].quote == "quote 75"  # not "quote 60", the first rfp1 record
        assert contradiction.winning_record_index is None  # a conflict names no record


class TestThePlaceboDisagreement:
    """Stage 5 criterion A, restated after the live run of 2026-10-03.

    The criterion was "blinding.placebo_matching is not_matching_stated". It
    failed, and the run showed the criterion itself was wrong: protocol page 53
    says "A matching placebo will not be provided for this study." at confidence
    1.00 and the RFP page 3 says "Placebo: Matching placebo for NEOD001" at 1.00.
    The two documents genuinely disagree, so a single value would have to be
    picked by discarding one of them. `source_priority: any` on this field means
    precedence cannot choose, and `no_silent_resolution` is the rule that it must
    not guess. The right output is a high-severity conflict that names both
    sentences and carries no value, which is what this asserts.
    """

    def _placebo(self, value: str, quote: str, doc_id: str, kind: DocKind) -> FieldRecord:
        r = FieldRecord(
            field_id="blinding.placebo_matching",
            group="blinding_monitoring",
            raw_value=value,
            quote=quote,
            provenance=Provenance(doc_id=doc_id, doc_kind=kind, page=53),
            confidence=1.0,
        )
        r.value = value
        return r

    def test_it_is_a_high_severity_conflict_naming_both_sentences(
        self, fields_yaml_path,  # type: ignore[no-untyped-def]
    ) -> None:
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        field_def = get_registry().get_field("blinding.placebo_matching")
        assert field_def.budget_driver, "severity high is claimed on this ground"
        assert field_def.source_priority == "any", "precedence must not be able to choose"

        protocol_sentence = "A matching placebo will not be provided for this study."
        rfp_sentence = "Placebo: Matching placebo for NEOD001"
        records = [
            self._placebo("not_matching_stated", protocol_sentence, "proto", "protocol"),
            self._placebo("matching_stated", rfp_sentence, "rfp1", "rfp"),
        ]
        c = Contradiction(field_id="blinding.placebo_matching", records=records).model_copy(
            update={
                "verdict": "conflict",
                "explanation": "the protocol refuses a matching placebo the RFP asks for",
                "severity": "high",
            }
        )

        contradiction, fields = _handle_conflict(c, field_def)

        assert contradiction.verdict == "conflict"
        assert contradiction.severity == "high"
        # Both sentences stay on the contradiction, which is what the report prints
        # and what a reviewer needs in order to settle it.
        quotes = [r.quote for r in contradiction.records]
        assert protocol_sentence in quotes
        assert rfp_sentence in quotes
        # And no value is invented from either of them.
        assert len(fields) == 1
        assert fields[0].value is None
        assert fields[0].status == "needs_review"
        assert contradiction.resolved_value is None
        assert contradiction.winning_record_index is None


class TestThePrompt:
    def test_candidates_are_numbered_and_the_model_is_asked_for_a_number(
        self, fields_yaml_path,  # type: ignore[no-untyped-def]
    ) -> None:
        _use_real_registry(fields_yaml_path)
        from rfp_intake.adjudicate.prompt import build_adjudicate_prompt
        from rfp_intake.domain.precedence import get_precedence_policy
        from rfp_intake.domain.registry import get_registry

        field_def = get_registry().get_field("ops.sites_total")
        records = [
            _record(40, "proto", doc_kind="protocol", page=20),
            _record(52, "proto", doc_kind="protocol", page=54),
        ]
        messages = build_adjudicate_prompt(
            _candidate(records), field_def, get_precedence_policy(),
        )

        system = str(messages[0].content)
        human = str(messages[1].content)
        assert "winning_record" in system
        assert "winning_doc_id" not in system  # the model must not be asked for a document
        assert "1. doc_id=proto" in human
        assert "2. doc_id=proto" in human


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
