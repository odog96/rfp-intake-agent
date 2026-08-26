"""Tests for the deterministic precedence tie-break (reconcile/precedence.py)."""

from __future__ import annotations

from typing import Literal

from rfp_intake.domain.registry import FieldDef
from rfp_intake.domain.schemas import FieldRecord, Provenance
from rfp_intake.reconcile.precedence import apply_precedence

DocKind = Literal["rfp", "protocol", "amendment", "soa", "other"]


def _field_def(source_priority: str | None = "any") -> FieldDef:
    return FieldDef(id="ops.sites_total", group="operational_metrics", label="Sites", type="int",
                     source_priority=source_priority)


def _record(value: object, doc_id: str, doc_kind: DocKind = "rfp", doc_date: str | None = None,
            confidence: float = 0.9) -> FieldRecord:
    r = FieldRecord(
        field_id="ops.sites_total",
        group="operational_metrics",
        raw_value=str(value),
        quote=f"quote {value}",
        provenance=Provenance(doc_id=doc_id, doc_kind=doc_kind, page=1, doc_date=doc_date),
        confidence=confidence,
    )
    r.value = value
    return r


class TestApplyPrecedence:
    def test_recency_decides_when_all_dated_and_distinct(self) -> None:
        older = _record(40, "protocol-v1", doc_date="2025-01-01")
        newer = _record(52, "protocol-v2", doc_date="2025-06-01")
        result = apply_precedence(_field_def(), [older, newer])
        assert result.rule_applied == "recency"
        assert result.value == 52
        assert result.winning_doc_id == "protocol-v2"

    def test_recency_not_decisive_when_a_date_is_missing(self) -> None:
        dated = _record(40, "protocol-v1", doc_kind="protocol", doc_date="2025-01-01")
        undated = _record(52, "rfp", doc_kind="rfp", doc_date=None)
        result = apply_precedence(_field_def("rfp"), [dated, undated])
        # falls through recency (missing date) to domain_authority
        assert result.rule_applied == "domain_authority"
        assert result.winning_doc_id == "rfp"

    def test_recency_not_decisive_on_tie(self) -> None:
        a = _record(40, "doc-a", doc_kind="protocol", doc_date="2025-01-01")
        b = _record(52, "doc-b", doc_kind="rfp", doc_date="2025-01-01")
        result = apply_precedence(_field_def("protocol"), [a, b])
        assert result.rule_applied == "domain_authority"
        assert result.winning_doc_id == "doc-a"

    def test_domain_authority_decides_for_protocol_field(self) -> None:
        protocol_rec = _record(40, "proto", doc_kind="protocol")
        rfp_rec = _record(52, "rfp", doc_kind="rfp")
        result = apply_precedence(_field_def("protocol"), [protocol_rec, rfp_rec])
        assert result.rule_applied == "domain_authority"
        assert result.value == 40
        assert result.winning_doc_id == "proto"

    def test_amendment_counts_as_protocol_family(self) -> None:
        amendment_rec = _record(40, "amend", doc_kind="amendment")
        rfp_rec = _record(52, "rfp", doc_kind="rfp")
        result = apply_precedence(_field_def("protocol"), [amendment_rec, rfp_rec])
        assert result.rule_applied == "domain_authority"
        assert result.winning_doc_id == "amend"

    def test_source_priority_any_skips_domain_authority(self) -> None:
        a = _record(40, "doc-a", doc_kind="protocol", confidence=0.95)
        b = _record(52, "doc-b", doc_kind="rfp", confidence=0.80)
        result = apply_precedence(_field_def("any"), [a, b])
        assert result.rule_applied == "specificity"
        assert result.winning_doc_id == "doc-a"

    def test_specificity_decides_on_unique_highest_confidence(self) -> None:
        a = _record(40, "doc-a", doc_kind="protocol", confidence=0.95)
        b = _record(52, "doc-b", doc_kind="protocol", confidence=0.80)
        result = apply_precedence(_field_def("protocol"), [a, b])
        assert result.rule_applied == "specificity"
        assert result.winning_doc_id == "doc-a"

    def test_no_silent_resolution_when_nothing_decisive(self) -> None:
        a = _record(40, "doc-a", doc_kind="protocol", confidence=0.9)
        b = _record(52, "doc-b", doc_kind="protocol", confidence=0.9)
        result = apply_precedence(_field_def("protocol"), [a, b])
        assert result.rule_applied == "no_silent_resolution"
        assert result.value is None
        assert result.winning_doc_id is None
