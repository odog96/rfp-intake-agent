"""Tests that ingest_node reads from runs/{run_id}/inputs/."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rfp_intake.domain.schemas import RunState
from rfp_intake.graph.nodes.ingest import ingest_node

SAMPLES = Path(__file__).parent.parent.parent / "samples"
SMALL_PDF = SAMPLES / "Synthetic_RFP_NEOD001.pdf"


@pytest.fixture
def run_with_pdf(run_dir: Path) -> str:
    """Create a run directory with a PDF in inputs/."""
    run_id = "test-ingest-001"
    inputs = run_dir / run_id / "inputs"
    inputs.mkdir(parents=True)
    shutil.copy(SMALL_PDF, inputs / "Synthetic_RFP_NEOD001.pdf")
    return run_id


class TestIngestRunDir:
    def test_ingest_reads_from_run_dir_inputs(self, run_with_pdf: str) -> None:
        state = RunState(run_id=run_with_pdf)
        result = ingest_node(state)

        assert result["documents"]
        assert len(result["documents"]) == 1
        assert result["errors"] == []

    def test_ingest_errors_when_inputs_missing(self, run_dir: Path) -> None:
        run_id = "no-inputs-run"
        # Don't create the inputs dir
        state = RunState(run_id=run_id)
        result = ingest_node(state)

        assert result["documents"] == []
        assert len(result["errors"]) == 1
        assert "not found" in result["errors"][0].error.lower()

    def test_ingest_errors_when_no_pdfs(self, run_dir: Path) -> None:
        run_id = "empty-inputs-run"
        inputs = run_dir / run_id / "inputs"
        inputs.mkdir(parents=True)
        # Create a non-PDF file
        (inputs / "readme.txt").write_text("not a pdf")

        state = RunState(run_id=run_id)
        result = ingest_node(state)

        assert result["documents"] == []
        assert len(result["errors"]) == 1
        assert "no pdf" in result["errors"][0].error.lower()

    def test_ingest_multiple_pdfs(self, run_dir: Path) -> None:
        run_id = "multi-pdf-run"
        inputs = run_dir / run_id / "inputs"
        inputs.mkdir(parents=True)
        shutil.copy(SMALL_PDF, inputs / "doc_a.pdf")
        shutil.copy(SMALL_PDF, inputs / "doc_b.pdf")

        state = RunState(run_id=run_id)
        result = ingest_node(state)

        assert len(result["documents"]) == 2
        assert result["errors"] == []
