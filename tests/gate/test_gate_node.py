"""Tests for the GATE graph node."""

from __future__ import annotations

import os
from typing import Literal

import pytest

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
        # A stage total DERIVE counts from the Schedule of Events (derive/__init__.py:
        # stage_totals); the visit-intensity rating was the example until 2026-11-23.
        rf = _resolved(
            field_id="visits.total_count",
            confidence=0.99,
            derived_from=["visits.frequency_by_period"],
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

    def test_reconcilable_verdict_allows_normal_gating_for_non_budget_driver(  # type: ignore[no-untyped-def]
        self, fields_yaml_path,
    ) -> None:
        """Change 1 of the 2026-10-07 review: follow the reviewer's conclusion.

        "reconcilable" means ADJUDICATE found both values true, one a restatement
        of the other, and named which to show. Until 2026-10-07 GATE treated that
        as a live disagreement, which sent free-text variables whose two passages
        word the same fact differently to the Disagreements table. It now gates
        on confidence like any other value.
        """
        _use_real_registry(fields_yaml_path)
        rf = _resolved(field_id=NON_BUDGET_DRIVER_FIELD, confidence=0.95)
        contradiction = _contradiction(NON_BUDGET_DRIVER_FIELD, verdict="reconcilable")
        state = RunState(run_id="t", resolved=[rf], contradictions=[contradiction])
        result = gate_node(state)
        assert result["resolved"][0].status == "confirmed"

    def test_reconcilable_verdict_still_forces_review_for_budget_driver(  # type: ignore[no-untyped-def]
        self, fields_yaml_path,
    ) -> None:
        """The guard on change 1: a wrong "reconcilable" on a budget driver is
        still seen by a person. It moves from the Disagreements table to Flagged
        for review rather than being cleared."""
        _use_real_registry(fields_yaml_path)
        rf = _resolved(field_id=BUDGET_DRIVER_FIELD, confidence=0.99)
        contradiction = _contradiction(BUDGET_DRIVER_FIELD, verdict="reconcilable")
        state = RunState(run_id="t", resolved=[rf], contradictions=[contradiction])
        result = gate_node(state)
        assert result["resolved"][0].status == "needs_review"
        assert result["resolved"][0].contradiction is not None

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


class TestAFieldThatSharesAnotherFieldsDisagreement:
    """Change 4 of the 2026-10-07 review: the timing row follows the count row.

    `config/fields.yaml` gives `interim.timing` the key
    `follows_conflict_of: interim.count`. RECONCILE never compares a list across
    documents, so the timing row cannot notice that the two documents disagree
    about how many interim analyses there are; in run r-20261007-140411 it was
    printed as Confirmed next to a count that was in dispute.
    """

    FOLLOWER = "interim.timing"
    LEADER = "interim.count"

    def _count_conflict(self) -> Contradiction:
        return Contradiction(
            field_id=self.LEADER,
            verdict="conflict",
            severity="high",
            explanation="The amendment says none are planned; the RFP describes one.",
            records=[
                FieldRecord(
                    field_id=self.LEADER, group="interim_analyses", raw_value=raw, value=raw,
                    quote=f"quote on page {page}", confidence=0.9,
                    provenance=Provenance(doc_id=doc, doc_kind=kind, page=page),
                )
                for raw, page, doc, kind in (
                    ("0", 98, "d-prot", "protocol"), ("1", 4, "d-rfp", "rfp"),
                )
            ],
        )

    def test_the_timing_row_is_flagged_with_the_counts_disagreement(
        self, fields_yaml_path,  # type: ignore[no-untyped-def]
    ) -> None:
        _use_real_registry(fields_yaml_path)
        conflict = self._count_conflict()
        timing = ResolvedField(
            field_id=self.FOLLOWER,
            value=["After approximately 156 primary endpoint events"],
            status="needs_review",
            confidence=0.95,
            sources=[Provenance(doc_id="d-rfp", doc_kind="rfp", page=4)],
        )
        state = RunState(run_id="t", resolved=[timing], contradictions=[conflict])

        [gated] = gate_node(state)["resolved"]

        assert gated.status == "needs_review", "not Confirmed while the count is in dispute"
        assert gated.contradiction is conflict, (
            "the same disagreement object, so the report gives both rows one number"
        )

    def test_the_timing_rows_own_disagreement_wins(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        own = _contradiction(self.FOLLOWER, verdict="conflict")
        timing = ResolvedField(
            field_id=self.FOLLOWER, value=["after 156 events"], status="needs_review",
            confidence=0.95,
            sources=[Provenance(doc_id="d-rfp", doc_kind="rfp", page=4)],
        )
        state = RunState(
            run_id="t", resolved=[timing], contradictions=[own, self._count_conflict()],
        )

        [gated] = gate_node(state)["resolved"]

        assert gated.contradiction is own

    def test_without_the_counts_disagreement_the_timing_row_gates_normally(
        self, fields_yaml_path,  # type: ignore[no-untyped-def]
    ) -> None:
        _use_real_registry(fields_yaml_path)
        timing = ResolvedField(
            field_id=self.FOLLOWER, value=["after 156 events"], status="needs_review",
            confidence=0.95,
            sources=[Provenance(doc_id="d-rfp", doc_kind="rfp", page=4)],
        )
        [gated] = gate_node(RunState(run_id="t", resolved=[timing]))["resolved"]
        assert gated.status == "confirmed"


class TestABudgetDriverTheDocumentsState:
    """Angus Gray, 29 October 2026: why was 260 subjects, a value both documents
    state, in Review? A budget driver with a dismissed verdict is confirmed where
    the on-topic records come from two or more documents and each of those
    documents has an on-topic record giving the row's value."""

    @staticmethod
    def _record(value: object, doc: str, page: int, field_id: str = "ops.subjects_total",
                scope: str | None = "total") -> FieldRecord:
        kind: Literal["rfp", "amendment"] = "rfp" if doc == "doc-rfp" else "amendment"
        r = FieldRecord(
            field_id=field_id, group="operational_metrics", raw_value=str(value),
            quote=f"q p.{page}", provenance=Provenance(doc_id=doc, doc_kind=kind, page=page),
            confidence=0.9, scope=scope,
        )
        r.value = value
        return r

    def _subjects(self) -> list[FieldRecord]:
        """Run r-20261123-opus5-base's four records for the subject count."""
        return [
            self._record(260, "doc-am", 15),
            self._record(236, "doc-am", 24),
            self._record(260, "doc-am", 43),
            self._record(260, "doc-rfp", 4),
        ]

    @staticmethod
    def _gate(
        records: list[FieldRecord], value: object, *,
        verdict: Literal["conflict", "reconcilable", "not_a_conflict"] = "not_a_conflict",
        off_topic: list[int] | None = None, confidence: float = 0.95,
        review_note: str | None = None, field_id: str = "ops.subjects_total",
    ) -> ResolvedField:
        c = Contradiction(
            field_id=field_id, records=records, verdict=verdict,
            off_topic_record_indexes=off_topic or [],
        )
        rf = ResolvedField(
            field_id=field_id, value=value, status="needs_review", confidence=confidence,
            sources=[r.provenance for r in records], scope=records[0].scope,
            contradiction=c, review_note=review_note,
        )
        [out] = gate_node(RunState(run_id="t", resolved=[rf], contradictions=[c]))["resolved"]
        return out

    def test_260_beside_an_off_topic_236_is_confirmed(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        out = self._gate(self._subjects(), 260, off_topic=[2])
        assert out.status == "confirmed"
        assert out.contradiction is not None  # the verdict stays on the row

    @pytest.mark.parametrize("verdict", ["not_a_conflict", "reconcilable"])
    def test_either_dismissed_verdict(self, fields_yaml_path, verdict) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        records = [self._record(260, "doc-am", 15), self._record(260, "doc-rfp", 4)]
        assert self._gate(records, 260, verdict=verdict).status == "confirmed"

    def test_a_conflict_is_still_reviewed(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        records = [self._record(260, "doc-am", 15), self._record(260, "doc-rfp", 4)]
        assert self._gate(records, 260, verdict="conflict").status == "needs_review"

    def test_42_months_against_40_dismissed_is_still_reviewed(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """The RFP states only a different value, so a person must choose."""
        _use_real_registry(fields_yaml_path)
        field_id = "timeline.total_duration"
        records = [
            self._record({"n": 42, "unit": "months"}, "doc-am", 15, field_id, "study"),
            self._record({"n": 40, "unit": "months"}, "doc-rfp", 3, field_id, "study"),
        ]
        for verdict in ("not_a_conflict", "reconcilable"):
            out = self._gate(
                records, {"n": 42, "unit": "months"}, verdict=verdict,  # type: ignore[arg-type]
                field_id=field_id,
            )
            assert out.status == "needs_review"

    def test_equal_amounts_in_two_documents_are_stated(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        field_id = "timeline.total_duration"
        records = [
            self._record({"n": 42, "unit": "months"}, "doc-am", 15, field_id, "study"),
            self._record({"n": 42, "unit": "months"}, "doc-rfp", 3, field_id, "study"),
        ]
        out = self._gate(records, {"n": 42, "unit": "months"}, field_id=field_id)
        assert out.status == "confirmed"
        # A dict is the same value only if it is equal.
        records[1].value = {"n": 42, "unit": "weeks"}
        out = self._gate(records, {"n": 42, "unit": "months"}, field_id=field_id)
        assert out.status == "needs_review"

    def test_one_document_is_not_enough(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        records = [self._record(260, "doc-am", 15), self._record(260, "doc-am", 43)]
        assert self._gate(records, 260).status == "needs_review"

    def test_an_off_topic_record_does_not_count_as_a_document(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        records = [self._record(260, "doc-am", 15), self._record(260, "doc-rfp", 4)]
        assert self._gate(records, 260, off_topic=[2]).status == "needs_review"

    def test_a_document_stating_only_another_value_reviews(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        records = [
            self._record(260, "doc-am", 15),
            self._record(260, "doc-rfp", 4),
            self._record(236, "doc-third", 9),
        ]
        assert self._gate(records, 260).status == "needs_review"

    def test_a_different_value_on_topic_reviews_every_row(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """With 236 on-topic, ADJUDICATE gives a row per record. Only the
        model's verdict says 236 and 260 do not disagree, so neither row is
        confirmed, though both documents state 260 (ARCHITECTURE.md §4.9)."""
        _use_real_registry(fields_yaml_path)
        assert self._gate(self._subjects(), 260).status == "needs_review"
        assert self._gate(self._subjects(), 236).status == "needs_review"

    @pytest.mark.parametrize("verdict", ["not_a_conflict", "reconcilable"])
    def test_one_document_stating_two_numbers_reviews(self, fields_yaml_path, verdict) -> None:  # type: ignore[no-untyped-def]
        """pipeline-rules-reviewer, 23 November 2026: RFP p.4 and Amendment p.15
        say 260, Amendment p.43 says 280. Each document has a 260, but the
        amendment also states 280 on topic."""
        _use_real_registry(fields_yaml_path)
        records = [
            self._record(260, "doc-rfp", 4),
            self._record(260, "doc-am", 15),
            self._record(280, "doc-am", 43),
        ]
        assert self._gate(records, 260, verdict=verdict).status == "needs_review"
        assert self._gate(records, 280, verdict=verdict).status == "needs_review"
        # Called about something else, the 280 no longer counts against the 260.
        assert self._gate(records, 260, verdict=verdict, off_topic=[3]).status == "confirmed"

    def test_a_low_confidence_or_a_review_note_still_reviews(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        records = [self._record(260, "doc-am", 15), self._record(260, "doc-rfp", 4)]
        assert self._gate(records, 260, confidence=0.5).status == "needs_review"
        assert self._gate(records, 260, review_note="an arm").status == "needs_review"

    def test_a_number_and_words_are_not_the_same_value(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        records = [self._record(260, "doc-am", 15), self._record("260 in all", "doc-rfp", 4)]
        assert self._gate(records, 260).status == "needs_review"

    def test_a_row_following_another_fields_disagreement_is_not_stated(self) -> None:
        """A `follows_conflict_of` row's value is not what the leader's records give."""
        from rfp_intake.gate import _gate_field

        records = [self._record(260, "doc-am", 15), self._record(260, "doc-rfp", 4)]
        leader = Contradiction(
            field_id="ops.subjects_total", records=records, verdict="not_a_conflict",
        )
        follower = ResolvedField(
            field_id="ops.sites_total", value=260, status="needs_review", confidence=0.95,
        )
        assert _gate_field(follower, leader, is_budget_driver=True).status == "needs_review"



def test_a_row_finds_its_disagreement_by_any_records_scope(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    """RECONCILE groups an unscoped field's "total" with no scope, so a
    disagreement can hold both, and ADJUDICATE gives its row the winning
    record's scope, which need not be the first record's."""
    _use_real_registry(fields_yaml_path)

    def record(value: str, scope: str | None, page: int) -> FieldRecord:
        r = FieldRecord(
            field_id="blinding.placebo_matching", group="blinding_monitoring",
            raw_value=value, quote=f"q p.{page}",
            provenance=Provenance(doc_id="doc-am", doc_kind="amendment", page=page),
            confidence=0.9, scope=scope,
        )
        r.value = value
        return r

    c = Contradiction(
        field_id="blinding.placebo_matching",
        records=[record("not_matching_stated", None, 53), record("matching_stated", "total", 3)],
        verdict="reconcilable",
    )
    rf = ResolvedField(
        field_id="blinding.placebo_matching", value="matching_stated", status="needs_review",
        confidence=0.95, scope="total",
    )
    [out] = gate_node(RunState(run_id="t", resolved=[rf], contradictions=[c]))["resolved"]
    assert out.status == "needs_review"
    assert out.contradiction is c


class TestARowFindsItsDisagreementByTheGroupingKey:
    """GATE matches a row to its disagreement by reconcile/__init__.py:scope_key,
    the rule RECONCILE groups with: on a field that is not scoped, "total" and no
    scope are one group, so neither spelling hides the disagreement."""

    @staticmethod
    def _gate(
        field_id: str, record_scopes: list[str | None], row_scope: str | None,
    ) -> ResolvedField:
        records = []
        for i, scope in enumerate(record_scopes):
            r = FieldRecord(
                field_id=field_id, group="g", raw_value=f"v{i}", quote=f"q{i}",
                provenance=Provenance(doc_id=f"doc-{i}", doc_kind="rfp", page=i + 1),
                confidence=0.9, scope=scope,
            )
            r.value = f"v{i}"
            records.append(r)
        c = Contradiction(field_id=field_id, records=records, verdict="conflict")
        rf = ResolvedField(
            field_id=field_id, value="v0", status="needs_review", confidence=0.95,
            scope=row_scope,
        )
        [out] = gate_node(RunState(run_id="t", resolved=[rf], contradictions=[c]))["resolved"]
        return out

    @pytest.mark.parametrize("record_scopes,row_scope", [
        (["total", "total"], None),
        (["total", None], None),
        ([None, None], "total"),
        (["Total", "study"], None),
    ])
    def test_an_unscoped_field(self, fields_yaml_path, record_scopes, row_scope) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        out = self._gate("blinding.placebo_matching", record_scopes, row_scope)
        assert out.status == "needs_review"
        assert out.contradiction is not None

    def test_a_scoped_field_keeps_the_two_apart(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """As RECONCILE does: on a scoped field they are two groups, so a row
        with no scope is not the whole study's disagreement."""
        _use_real_registry(fields_yaml_path)
        out = self._gate("ops.sites_total", ["total", "total"], None)
        assert out.contradiction is None
        assert out.status == "confirmed"


class TestAPeriodsDisplayIsOneAnswerWhenItsPeriodsAgree:
    """The patient timeline: each entry is one stretch of the study, not a
    rival answer, so a budget driver with a `periods` display is not held in
    review for holding several values when every period reads the same in each
    document stating it. Found by its display kind, as GATE finds it."""

    @staticmethod
    def _field(fields_yaml_path, *, from_schedule: bool):  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        from rfp_intake.domain.registry import PeriodsDisplay, get_registry

        return next(
            f for f in get_registry().fields
            if isinstance(f.display, PeriodsDisplay) and f.budget_driver
            and (f.from_schedule is not None) is from_schedule
        )

    @staticmethod
    def _gate(field, entries: list[tuple[str, dict[str, object]]]) -> ResolvedField:  # type: ignore[no-untyped-def]
        statements = []
        for i, (doc, entry) in enumerate(entries):
            r = FieldRecord(
                field_id=field.id, group=field.group, raw_value=str(entry), quote=f"q{i}",
                provenance=Provenance(
                    doc_id=doc, doc_kind="rfp" if doc == "doc-rfp" else "amendment", page=i + 1,
                ),
                confidence=0.9,
            )
            r.value = entry
            statements.append(r)
        members: list[object] = []
        for r in statements:
            if r.value not in members:
                members.append(r.value)
        rf = ResolvedField(
            field_id=field.id, value=members, status="needs_review", confidence=0.9,
            statements=statements, sources=[r.provenance for r in statements],
        )
        [out] = gate_node(RunState(run_id="t", resolved=[rf]))["resolved"]
        return out

    def _key(self, field) -> str:  # type: ignore[no-untyped-def]
        [key] = field.display.one_per
        return str(key)

    def test_one_document_stating_screening_and_treatment_confirms(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        field = self._field(fields_yaml_path, from_schedule=False)
        key = self._key(field)
        out = self._gate(field, [
            ("doc-am", {key: "screening", "duration": "28 days"}),
            ("doc-am", {key: "treatment", "duration": "52 weeks"}),
        ])
        assert out.status == "confirmed"

    def test_two_screening_durations_from_two_documents_review(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        field = self._field(fields_yaml_path, from_schedule=False)
        key = self._key(field)
        out = self._gate(field, [
            ("doc-am", {key: "screening", "duration": "28 days"}),
            ("doc-rfp", {key: "screening", "duration": "21 days"}),
            ("doc-am", {key: "treatment", "duration": "52 weeks"}),
        ])
        assert out.status == "needs_review"

    def test_two_documents_agreeing_on_every_period_confirm(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        field = self._field(fields_yaml_path, from_schedule=False)
        key = self._key(field)
        out = self._gate(field, [
            ("doc-am", {key: "screening", "duration": "28 days"}),
            ("doc-rfp", {key: "screening", "duration": "28  Days"}),
            ("doc-rfp", {key: "treatment", "duration": "52 weeks"}),
        ])
        assert out.status == "confirmed"

    def test_one_document_wording_a_length_twice_confirms(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """The same length in other words ("a 28-day period"), and a day or a
        visit named two ways (`may_word_differently`), is one answer."""
        field = self._field(fields_yaml_path, from_schedule=False)
        key = self._key(field)
        out = self._gate(field, [
            ("doc-am", {key: "screening", "label": "Screening", "duration": "28 days",
                        "start": "Day -28", "end": "Month 1-Day 1"}),
            ("doc-am", {key: "screening", "label": "Screening period",
                        "duration": "a 28-day period", "start": "Day -28", "end": "Day -1"}),
        ])
        assert out.status == "confirmed"

    @pytest.mark.parametrize(("first", "second"), [
        ("24 months", "36 months"),
        ("28 days", "4 weeks"),
        ("28 days", "28-35 days"),
    ])
    def test_one_document_giving_two_lengths_reviews(  # type: ignore[no-untyped-def]
        self, fields_yaml_path, first: str, second: str,
    ) -> None:
        """pipeline-rules-reviewer, 23 November 2026: Amendment p.15 gives the
        treatment period as 24 months and p.47 as 36 months. Nothing is
        converted, so 28 days and 4 weeks are two lengths too."""
        field = self._field(fields_yaml_path, from_schedule=False)
        key = self._key(field)
        out = self._gate(field, [
            ("doc-am", {key: "treatment", "duration": first}),
            ("doc-am", {key: "treatment", "duration": second}),
        ])
        assert out.status == "needs_review"

    @pytest.mark.parametrize(("first", "second"), [
        ("1-2 years", "2 years"),
        ("3.5-4 years", "4 years"),
    ])
    def test_a_range_and_its_upper_end_review(  # type: ignore[no-untyped-def]
        self, fields_yaml_path, first: str, second: str,
    ) -> None:
        """pipeline-rules-reviewer, second reading, 23 November 2026: a range was
        read by its upper end only, so "1-2 years" confirmed beside "2 years"."""
        field = self._field(fields_yaml_path, from_schedule=False)
        key = self._key(field)
        out = self._gate(field, [
            ("doc-am", {key: "treatment", "duration": first}),
            ("doc-rfp", {key: "treatment", "duration": second}),
        ])
        assert out.status == "needs_review"

    @pytest.mark.parametrize(("name", "first", "second"), [
        ("end", "approximately 300 primary endpoint events",
         "approximately 250 primary endpoint events"),
        ("end", "Month 24", "Month 36"),
        ("start", "Day 1", "Week 4"),
        # The same length, a different event: compared word for word (second
        # reading, 23 November 2026).
        ("end", "24 months after the last subject is randomized",
         "24 months after the first subject is randomized"),
    ])
    def test_two_documents_ending_a_period_differently_review(  # type: ignore[no-untyped-def]
        self, fields_yaml_path, name: str, first: str, second: str,
    ) -> None:
        """pipeline-rules-reviewer, second reading, 23 November 2026: a start or
        an end may be worded two ways within one document, not between two, as
        an `end` can hold the event a treatment runs until."""
        field = self._field(fields_yaml_path, from_schedule=False)
        key = self._key(field)
        out = self._gate(field, [
            ("doc-am", {key: "treatment", "start": "Day 1", name: first}),
            ("doc-rfp", {key: "treatment", "start": "Day 1", name: second}),
        ])
        assert out.status == "needs_review"

    def test_two_documents_sharing_a_wording_agree(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """The amendment words the end of screening two ways; an RFP giving one
        of those wordings agrees with it."""
        field = self._field(fields_yaml_path, from_schedule=False)
        key = self._key(field)
        out = self._gate(field, [
            ("doc-am", {key: "screening", "duration": "28 days", "end": "Day -1"}),
            ("doc-am", {key: "screening", "duration": "28 days", "end": "Month 1-Day 1"}),
            ("doc-rfp", {key: "screening", "duration": "28 days", "end": "day -1"}),
        ])
        assert out.status == "confirmed"

    def test_two_documents_naming_a_period_differently_agree(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """plan-conformance-reviewer, 23 November 2026: a period's own name is
        no difference, whoever words it (`names: [label]`). Before, "Screening"
        and "Screening Period" in two documents put the timeline in review."""
        field = self._field(fields_yaml_path, from_schedule=False)
        key = self._key(field)
        out = self._gate(field, [
            ("doc-am", {key: "screening", "label": "Screening", "duration": "28 days",
                        "end": "Day -1"}),
            ("doc-rfp", {key: "screening", "label": "Screening Period", "duration": "28 days",
                         "end": "Day -1"}),
        ])
        assert out.status == "confirmed"

    def test_a_length_and_an_end_under_two_names_are_reviewed(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """pipeline-rules-reviewer, 23 November 2026: with the name never a
        difference, an RFP "Treatment Period" of 24 months and an amendment
        "Study drug treatment" until about 156 events shared no key to compare,
        and the line "24 months, until approximately 156 primary endpoint
        events", which neither document says, was Confirmed. A name is no
        difference only where another key is compared."""
        field = self._field(fields_yaml_path, from_schedule=False)
        key = self._key(field)
        out = self._gate(field, [
            ("doc-rfp", {key: "treatment", "label": "Treatment Period",
                         "duration": "24 months"}),
            ("doc-am", {key: "treatment", "label": "Study drug treatment",
                        "end": "approximately 156 primary endpoint events"}),
        ])
        assert out.status == "needs_review"

    def test_two_names_for_an_extension_are_reviewed(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """An open-label extension's line prints the document's own name, which
        can say what the heading cannot ("a separate open-label extension study"
        is not this study's budget), so two names are two answers
        (plan-conformance-reviewer, second reading, 23 November 2026)."""
        field = self._field(fields_yaml_path, from_schedule=False)
        key = self._key(field)
        out = self._gate(field, [
            ("doc-am", {key: "open_label_extension",
                        "label": "Separate open-label extension study of NEOD001",
                        "duration": "12 months"}),
            ("doc-rfp", {key: "open_label_extension", "label": "OLE study",
                         "duration": "12 months"}),
        ])
        assert out.status == "needs_review"

    def test_two_unlisted_periods_are_not_one_period(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """An enrolment period and an end of treatment visit, neither in the
        component list, are two stretches as the report prints them
        (reconcile/entries.py:period_groups), so their lengths do not differ."""
        field = self._field(fields_yaml_path, from_schedule=False)
        out = self._gate(field, [
            ("doc-am", {"label": "Enrolment", "duration": "24 months"}),
            ("doc-am", {"label": "End of treatment visit", "duration": "28-35 days"}),
        ])
        assert out.status == "confirmed"

    def test_the_schedule_counted_field_keeps_the_rule(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """The row the Schedule of Events replaces is the tool's count."""
        field = self._field(fields_yaml_path, from_schedule=True)
        key = self._key(field)
        out = self._gate(field, [
            ("doc-am", {key: "screening", "frequency": "once"}),
            ("doc-am", {key: "treatment", "frequency": "every 28 days"}),
        ])
        assert out.status == "needs_review"

    def test_low_confidence_still_reviews(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        field = self._field(fields_yaml_path, from_schedule=False)
        key = self._key(field)
        rf = self._gate(field, [
            ("doc-am", {key: "screening", "duration": "28 days"}),
            ("doc-am", {key: "treatment", "duration": "52 weeks"}),
        ])
        low = rf.model_copy(update={"status": "needs_review", "confidence": 0.5})
        [out] = gate_node(RunState(run_id="t", resolved=[low]))["resolved"]
        assert out.status == "needs_review"

    def test_entries_with_no_statements_keep_the_rule(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        field = self._field(fields_yaml_path, from_schedule=False)
        rf = ResolvedField(
            field_id=field.id, value=["Screening 28 days", "Treatment 52 weeks"],
            status="needs_review", confidence=0.9,
        )
        [out] = gate_node(RunState(run_id="t", resolved=[rf]))["resolved"]
        assert out.status == "needs_review"
