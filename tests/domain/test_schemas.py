"""Tests for domain/schemas.py — Pydantic model validation."""

from __future__ import annotations

from rfp_intake.domain.schemas import (
    Contradiction,
    Document,
    ExtractionTask,
    FieldRecord,
    Provenance,
    ResolvedField,
    RunError,
    RunState,
)


def test_provenance_construction() -> None:
    p = Provenance(doc_id="doc1", doc_kind="protocol", page=5)
    assert p.doc_id == "doc1"
    assert p.doc_version is None


def test_field_record_validation() -> None:
    r = FieldRecord(
        field_id="ops.sites_total",
        group="operational_metrics",
        raw_value="75 sites",
        quote="The study will be conducted across 75 sites",
        provenance=Provenance(doc_id="rfp1", doc_kind="rfp", page=3),
        confidence=0.9,
        scope="total",
    )
    assert r.status == "found"
    assert r.value is None


def test_field_record_status_not_specified() -> None:
    r = FieldRecord(
        field_id="interim.planned",
        group="interim_analyses",
        raw_value="N/A",
        quote="No interim analyses are planned",
        provenance=Provenance(doc_id="p1", doc_kind="protocol", page=80),
        status="not_specified",
        confidence=0.95,
    )
    assert r.status == "not_specified"


def test_contradiction_model() -> None:
    r1 = FieldRecord(
        field_id="timeline.total_duration",
        group="timelines",
        raw_value="40 months",
        quote="Total study duration is 40 months",
        provenance=Provenance(doc_id="rfp1", doc_kind="rfp", page=4),
        confidence=0.9,
    )
    r2 = FieldRecord(
        field_id="timeline.total_duration",
        group="timelines",
        raw_value="42 months",
        quote="The overall study period is approximately 42 months",
        provenance=Provenance(doc_id="proto1", doc_kind="protocol", page=12),
        confidence=0.85,
    )
    c = Contradiction(
        field_id="timeline.total_duration",
        records=[r1, r2],
        verdict="conflict",
        explanation="RFP states 40 months, protocol states 42 months.",
        severity="high",
    )
    assert c.verdict == "conflict"
    assert len(c.records) == 2


def test_resolved_field_model() -> None:
    rf = ResolvedField(
        field_id="study.phase",
        value="phase_3",
        status="confirmed",
        confidence=0.95,
        sources=[Provenance(doc_id="p1", doc_kind="protocol", page=1)],
        quote="This Phase 3 study",
    )
    assert rf.derived_from == []


def test_document_model() -> None:
    doc = Document(id="doc1", path="/tmp/test.pdf", kind="protocol", pages=100)
    assert doc.page_texts == {}
    assert doc.outline == []


def test_extraction_task_model() -> None:
    task = ExtractionTask(doc_id="doc1", group="visits", page_window=(10, 25))
    assert task.budget_tokens is None


def test_run_state_records_reducer() -> None:
    """Verify that the records field supports operator.add semantics."""
    r1 = FieldRecord(
        field_id="a.b",
        group="g",
        raw_value="x",
        quote="q",
        provenance=Provenance(doc_id="d", doc_kind="rfp", page=1),
        confidence=0.8,
    )
    r2 = FieldRecord(
        field_id="a.c",
        group="g",
        raw_value="y",
        quote="q2",
        provenance=Provenance(doc_id="d", doc_kind="rfp", page=2),
        confidence=0.7,
    )
    state = RunState(run_id="test")
    # Simulate how LangGraph's operator.add would work
    combined = state.records + [r1] + [r2]
    assert len(combined) == 2


def test_run_error_model() -> None:
    err = RunError(node="EXTRACT", task_id="t1", error="Quote validation failed")
    assert err.node == "EXTRACT"
    assert err.timestamp is not None
