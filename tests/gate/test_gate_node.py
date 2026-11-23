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

    def test_each_row_is_checked_against_its_own_value(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """With 236 on-topic, ADJUDICATE gives a row per record: both documents
        state 260, so a 260 row confirms; only the amendment states 236."""
        _use_real_registry(fields_yaml_path)
        assert self._gate(self._subjects(), 260).status == "confirmed"
        assert self._gate(self._subjects(), 236).status == "needs_review"

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

