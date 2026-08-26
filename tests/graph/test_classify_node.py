"""Tests for CLASSIFY node with mock LLM."""

from rfp_intake.domain.schemas import Document, OutlineEntry, RunState
from rfp_intake.graph.nodes.classify import classify_node


def test_classify_node_processes_documents():
    """CLASSIFY node sets document kind and metadata from mock LLM."""
    doc = Document(
        id="doc-test",
        path="/tmp/test.pdf",
        pages=5,
        page_texts={
            1: "Request for Proposal\nClinical Study ABC-123\nPhase 1",
            2: "Scope of Work\nThe sponsor requests...",
            3: "Budget assumptions: 75 sites across 12 countries",
        },
        outline=[
            OutlineEntry(heading="Request for Proposal", page_start=1, level=1),
            OutlineEntry(heading="Scope of Work", page_start=2, level=1),
        ],
    )
    state = RunState(run_id="test-run", documents=[doc])

    result = classify_node(state)
    updated_docs = result["documents"]
    assert len(updated_docs) == 1

    classified = updated_docs[0]
    # Mock LLM returns a valid ClassificationResult, kind will be set
    assert classified.kind is not None
    assert classified.confidence is not None


def test_classify_node_handles_empty_doc():
    """CLASSIFY handles a document with minimal content gracefully."""
    doc = Document(
        id="doc-empty",
        path="/tmp/empty.pdf",
        pages=1,
        page_texts={1: ""},
    )
    state = RunState(run_id="test-run", documents=[doc])

    result = classify_node(state)
    assert len(result["documents"]) == 1


def test_classify_node_no_documents():
    """CLASSIFY with empty document list returns empty."""
    state = RunState(run_id="test-run", documents=[])
    result = classify_node(state)
    assert result["documents"] == []
    assert result["errors"] == []
