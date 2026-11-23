"""Tests for the ADJUDICATE graph node."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from rfp_intake.adjudicate import (
    UNJUDGED_EXPLANATION,
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


def _not_a_conflict(winning_record: int | None = None) -> AdjudicationResult:
    return AdjudicationResult(
        verdict="not_a_conflict", explanation="x", winning_record=winning_record,
    )


def _schedule(value: str, doc_kind: DocKind, page: int, confidence: float,
              scope: str | None = None) -> FieldRecord:
    r = FieldRecord(
        field_id="visits.schedule_present", group="visits", raw_value=value,
        quote=f"quote p.{page}",
        provenance=Provenance(doc_id=doc_kind, doc_kind=doc_kind, page=page),
        confidence=confidence, scope=scope,
    )
    r.value = value
    return r


class TestHandleNotAConflict:
    def test_unpacks_one_resolved_field_per_record(self, fields_yaml_path: Path) -> None:
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        records = [_record(40, "a"), _record(52, "b")]
        c = _candidate(records).model_copy(update={
            "verdict": "not_a_conflict", "explanation": "different scopes",
        })

        contradiction, fields = _handle_not_a_conflict(
            c, get_registry().get_field("ops.sites_total"), _not_a_conflict(),
        )

        assert len(fields) == 2
        assert {f.value for f in fields} == {40, 52}
        assert all(f.status == "needs_review" for f in fields)
        assert all(f.contradiction is contradiction for f in fields)
        assert all("different scopes" in (f.notes or "") for f in fields)
        assert contradiction.winning_record_index is None

    def _cluster(self) -> Contradiction:
        """Run r-20261013-prompt-c's nine unscoped schedule records, cut to four."""
        return Contradiction(field_id="visits.schedule_present", records=[
            _schedule("yes_body", "amendment", 13, 0.9),
            _schedule("yes_body", "amendment", 26, 1.0),
            _schedule("yes_appendix", "amendment", 111, 0.95),
            _schedule("no", "rfp", 1, 0.95),
        ]).model_copy(update={"verdict": "not_a_conflict", "explanation": "x"})

    def test_a_one_choice_field_with_one_scope_is_one_row(self, fields_yaml_path: Path) -> None:
        """Prompt B item 2: nine rows of one answer each became one row."""
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        field_def = get_registry().get_field("visits.schedule_present")
        assert field_def.type == "enum"
        contradiction, fields = _handle_not_a_conflict(
            self._cluster(), field_def, _not_a_conflict(),
        )

        [row] = fields
        # No record named, so the most confident: the Schedule of Events, p.26.
        assert (row.value, row.quote, row.confidence) == ("yes_body", "quote p.26", 1.0)
        assert [s.page for s in row.sources] == [13, 26, 111, 1]
        assert contradiction.winning_record_index == 2
        assert contradiction.resolved_value == "yes_body"
        assert (row.notes or "").startswith("NOT_A_CONFLICT")

    def test_the_record_the_model_named_is_shown(self, fields_yaml_path: Path) -> None:
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        field_def = get_registry().get_field("visits.schedule_present")
        contradiction, [row] = _handle_not_a_conflict(
            self._cluster(), field_def, _not_a_conflict(winning_record=3),
        )
        assert row.value == "yes_appendix"
        assert contradiction.winning_record_index == 3

    def test_an_off_topic_record_is_neither_shown_nor_cited(self, fields_yaml_path: Path) -> None:
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        c = self._cluster().model_copy(update={"off_topic_record_indexes": [4]})
        _, [row] = _handle_not_a_conflict(
            c, get_registry().get_field("visits.schedule_present"), _not_a_conflict(4),
        )
        assert row.value == "yes_body"
        assert [s.page for s in row.sources] == [13, 26, 111]

    def test_different_scopes_keep_one_row_each(self, fields_yaml_path: Path) -> None:
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        c = Contradiction(field_id="visits.schedule_present", records=[
            _schedule("yes_body", "amendment", 26, 1.0, scope="part A"),
            _schedule("yes_appendix", "amendment", 111, 0.95, scope="part B"),
        ]).model_copy(update={"verdict": "not_a_conflict", "explanation": "x"})
        contradiction, fields = _handle_not_a_conflict(
            c, get_registry().get_field("visits.schedule_present"), _not_a_conflict(),
        )
        assert [f.scope for f in fields] == ["part A", "part B"]
        assert contradiction.winning_record_index is None

    def test_a_text_field_keeps_one_row_per_record(self, fields_yaml_path: Path) -> None:
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        text_field = next(f for f in get_registry().fields if f.type == "text")
        c = Contradiction(field_id=text_field.id, records=[
            _schedule("one wording", "amendment", 26, 1.0),
            _schedule("another wording", "rfp", 3, 0.9),
        ]).model_copy(update={"verdict": "not_a_conflict", "explanation": "x"})
        _, fields = _handle_not_a_conflict(c, text_field, _not_a_conflict())
        assert len(fields) == 2


class TestOneRowWhenEveryRecordAgrees:
    """Added 2026-11-23. Where every on-topic record has the same scope and the
    same normalized value, a not_a_conflict verdict gives one row, whatever the
    field's type, citing every on-topic record's page."""

    @staticmethod
    def _subjects(value: int, doc_kind: DocKind, page: int, confidence: float = 0.9,
                  scope: str | None = "total") -> FieldRecord:
        r = FieldRecord(
            field_id="ops.subjects_total", group="operational_metrics", raw_value=str(value),
            quote=f"quote p.{page}",
            provenance=Provenance(doc_id=doc_kind, doc_kind=doc_kind, page=page),
            confidence=confidence, scope=scope,
        )
        r.value = value
        return r

    def _field(self, fields_yaml_path: Path, field_id: str = "ops.subjects_total"):  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        return get_registry().get_field(field_id)

    def test_three_records_of_260_beside_an_off_topic_236_are_one_row(
        self, fields_yaml_path: Path,
    ) -> None:
        field_def = self._field(fields_yaml_path)
        assert field_def.type == "int"
        c = Contradiction(field_id="ops.subjects_total", records=[
            self._subjects(260, "amendment", 15, 0.95),
            self._subjects(236, "amendment", 24, 0.9),
            self._subjects(260, "amendment", 43, 0.9),
            self._subjects(260, "rfp", 4, 0.9),
        ]).model_copy(update={
            "verdict": "not_a_conflict", "explanation": "236 is the power calculation",
            "off_topic_record_indexes": [2],
        })

        contradiction, [row] = _handle_not_a_conflict(c, field_def, _not_a_conflict())

        assert (row.value, row.scope, row.quote) == (260, "total", "quote p.15")
        assert [(s.doc_kind, s.page) for s in row.sources] == [
            ("amendment", 15), ("amendment", 43), ("rfp", 4),
        ]
        assert row.status == "needs_review"
        assert row.contradiction is contradiction
        assert contradiction.winning_record_index == 1
        assert contradiction.resolved_value == 260
        assert (row.notes or "").startswith("NOT_A_CONFLICT")

    def test_the_record_the_model_named_gives_the_quote(self, fields_yaml_path: Path) -> None:
        field_def = self._field(fields_yaml_path)
        c = Contradiction(field_id="ops.subjects_total", records=[
            self._subjects(260, "amendment", 15, 0.95),
            self._subjects(260, "rfp", 4, 0.9),
        ]).model_copy(update={"verdict": "not_a_conflict", "explanation": "x"})
        contradiction, [row] = _handle_not_a_conflict(c, field_def, _not_a_conflict(2))
        assert row.quote == "quote p.4"
        assert contradiction.winning_record_index == 2

    def test_different_values_keep_one_row_per_record(self, fields_yaml_path: Path) -> None:
        field_def = self._field(fields_yaml_path)
        c = Contradiction(field_id="ops.subjects_total", records=[
            self._subjects(260, "amendment", 15),
            self._subjects(236, "amendment", 24),
            self._subjects(260, "rfp", 4),
        ]).model_copy(update={"verdict": "not_a_conflict", "explanation": "x"})
        contradiction, fields = _handle_not_a_conflict(c, field_def, _not_a_conflict())
        assert [f.value for f in fields] == [260, 236, 260]
        assert [[s.page for s in f.sources] for f in fields] == [[15], [24], [4]]
        assert contradiction.winning_record_index is None

    def test_one_value_at_different_scopes_keeps_one_row_per_record(
        self, fields_yaml_path: Path,
    ) -> None:
        field_def = self._field(fields_yaml_path)
        c = Contradiction(field_id="ops.subjects_total", records=[
            self._subjects(130, "amendment", 15, scope="arm A"),
            self._subjects(130, "amendment", 16, scope="arm B"),
        ]).model_copy(update={"verdict": "not_a_conflict", "explanation": "x"})
        _, fields = _handle_not_a_conflict(c, field_def, _not_a_conflict())
        assert [f.scope for f in fields] == ["arm A", "arm B"]

    def test_a_text_field_with_one_wording_is_one_row(self, fields_yaml_path: Path) -> None:
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        text_field = next(f for f in get_registry().fields if f.type == "text")
        c = Contradiction(field_id=text_field.id, records=[
            _schedule("one wording", "amendment", 26, 1.0),
            _schedule("one wording", "rfp", 3, 0.9),
        ]).model_copy(update={"verdict": "not_a_conflict", "explanation": "x"})
        _, [row] = _handle_not_a_conflict(c, text_field, _not_a_conflict())
        assert row.value == "one wording"
        assert [s.page for s in row.sources] == [26, 3]

    def test_equal_amounts_are_one_value_and_unequal_amounts_are_not(
        self, fields_yaml_path: Path,
    ) -> None:
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        text_field = next(f for f in get_registry().fields if f.type == "text")
        same = Contradiction(field_id=text_field.id, records=[
            _schedule("x", "amendment", 26, 1.0), _schedule("x", "rfp", 3, 0.9),
        ])
        same.records[0].value = {"n": 28, "unit": "days"}
        same.records[1].value = {"n": 28, "unit": "days"}
        _, rows = _handle_not_a_conflict(same, text_field, _not_a_conflict())
        assert len(rows) == 1
        same.records[1].value = {"n": 28, "unit": "weeks"}
        _, rows = _handle_not_a_conflict(same, text_field, _not_a_conflict())
        assert len(rows) == 2


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
        # Recorded for the report's "Shown provisionally" line (Prompt B item 1).
        assert contradiction.precedence_rule == "domain_authority"

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
        assert contradiction.precedence_rule == "no_silent_resolution"

    def test_value_and_quote_come_from_the_record_precedence_chose(
        self, fields_yaml_path: Path,
    ) -> None:
        """CLAUDE.md open item 7: one document holding two different values. The
        RFP gives 75 sites on one page and 80 on another; the protocol gives 75.
        Before 2026-10-03 the quote was the winning document's first record, so the
        row could print 80 beside the sentence that says 75. The row now takes its
        value, quote and confidence from the one record precedence chose, and
        names it. Today's precedence rules only choose a record that is also the
        best of its own document, so the quote assertions held before this change
        too and are a consistency check; the record index is what failed before.
        """
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        field_def = get_registry().get_field("ops.sites_total")  # source_priority: rfp
        records = [
            _record(75, "rfp1", doc_kind="rfp", confidence=0.90, page=3),
            _record(80, "rfp1", doc_kind="rfp", confidence=0.95, page=4),
            _record(75, "proto", doc_kind="protocol", confidence=0.85, page=15),
        ]
        c = _candidate(records).model_copy(update={
            "verdict": "conflict", "explanation": "genuine mismatch",
        })

        contradiction, fields = _handle_conflict(c, field_def)

        assert contradiction.winning_record_index == 2
        winner = records[contradiction.winning_record_index - 1]
        assert (fields[0].value, fields[0].quote, fields[0].confidence) == (
            winner.value, winner.quote, winner.confidence,
        )
        assert fields[0].quote == "quote 80"  # not "quote 75", the first rfp1 record
        assert contradiction.resolved_value == 80
        assert contradiction.winning_doc_id == "rfp1"

    def test_no_record_is_named_when_precedence_cannot_decide(
        self, fields_yaml_path: Path,
    ) -> None:
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry

        field_def = get_registry().get_field("ops.sites_total")
        records = [_record(40, "rfp1", confidence=0.9), _record(75, "rfp2", confidence=0.9)]
        c = _candidate(records).model_copy(update={"verdict": "conflict", "explanation": "x"})

        contradiction, fields = _handle_conflict(c, field_def)

        assert contradiction.winning_record_index is None
        assert contradiction.winning_doc_id is None
        assert fields[0].quote is None
        assert fields[0].confidence == 0.0


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
        self, fields_yaml_path: Path,
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
        self, fields_yaml_path: Path,
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


class _Failing:
    """A structured-output stand-in whose first `failures` calls raise."""

    def __init__(self, failures: int, result: AdjudicationResult) -> None:
        self.failures = failures
        self.calls = 0
        self.result = result

    def extract(self, schema, messages):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls <= self.failures:
            raise ValueError("Failed to parse guided response")
        return self.result


class TestAModelThatCannotAnswer:
    """Run r-20261123-oct29-run2: one malformed answer printed "Whole study
    duration" as Not found, with no disagreement listed."""

    def _run(self, fields_yaml_path, monkeypatch, failures: int):  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        import rfp_intake.adjudicate as adjudicate

        fake = _Failing(failures, AdjudicationResult(verdict="conflict", explanation="real"))
        monkeypatch.setattr(adjudicate, "get_structured_output_for_role", lambda llm, role: fake)
        records = [_record(75, "amend", "amendment"), _record(80, "rfp", "rfp")]
        state = RunState(run_id="t", contradictions=[_candidate(records)])
        return adjudicate_node(state), fake

    def test_one_failure_is_tried_again(self, fields_yaml_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        result, fake = self._run(fields_yaml_path, monkeypatch, failures=1)
        assert fake.calls == 2
        assert result["contradictions"][0].explanation == "real"
        assert result["errors"] == []

    def test_a_candidate_never_judged_is_still_a_disagreement_with_a_row(
        self, fields_yaml_path, monkeypatch,
    ) -> None:  # type: ignore[no-untyped-def]
        result, fake = self._run(fields_yaml_path, monkeypatch, failures=5)
        assert fake.calls == 2
        c = result["contradictions"][0]
        assert c.verdict == "conflict"
        assert c.explanation == UNJUDGED_EXPLANATION
        assert c.severity == "high"  # ops.sites_total is a budget driver
        [row] = result["resolved"]
        assert row.status == "needs_review"
        assert row.contradiction is not None and row.contradiction.verdict == "conflict"
        assert len(result["errors"]) == 1


class TestOffTopicRecordsAnswer:
    def test_an_empty_string_means_none_are_off_topic(self) -> None:
        r = AdjudicationResult.model_validate(
            {"verdict": "conflict", "explanation": "x", "off_topic_records": ""}
        )
        assert r.off_topic_records == []

    def test_null_means_none_are_off_topic(self) -> None:
        r = AdjudicationResult.model_validate(
            {"verdict": "conflict", "explanation": "x", "off_topic_records": None}
        )
        assert r.off_topic_records == []


class TestThePromptsWordingAfterOpus5:
    """Run r-20261123-opus5-base judged 42 against 40 months reconcilable and
    kept a statistical sample size of 236 beside the enrolment figure of 260."""

    @staticmethod
    def _system() -> str:
        from rfp_intake.adjudicate.prompt import ADJUDICATE_SYSTEM_PROMPT

        return " ".join(ADJUDICATE_SYSTEM_PROMPT.split())

    def test_two_close_amounts_at_one_scope_are_a_conflict(self) -> None:
        conflict = self._system().split("- conflict:")[1].split("off_topic_records:")[0]
        assert (
            "Two different amounts for the same quantity at the same scope are a conflict "
            "even when they are close"
        ) in conflict
        assert 'one says "approximately"' in conflict

    def test_the_same_answer_in_other_words_is_reconcilable(self) -> None:
        """Run r-20261123-oct29-run2 called the two documents' primary objectives
        not_a_conflict, which keeps one row per document."""
        reconcilable = self._system().split("- reconcilable:")[1].split("- conflict:")[0]
        assert "The same answer worded differently" in reconcilable
        assert "is reconcilable, not not_a_conflict" in reconcilable
        not_a_conflict = self._system().split("- not_a_conflict:")[1].split("- reconcilable:")[0]
        assert "about different things" in not_a_conflict

    def test_a_different_quantity_in_the_same_words_is_off_topic(self) -> None:
        off_topic = self._system().split("off_topic_records:")[1].split("Your explanation")[0]
        assert "a record for a different quantity that uses the same words IS off-topic" in (
            off_topic
        )
        assert "power calculation" in off_topic
        # The older rule still stands beside it.
        assert "A record that is about the field but gives a different value is never " \
            "off-topic." in off_topic
