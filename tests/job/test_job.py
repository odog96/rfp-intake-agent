"""End-to-end test for the CML Job entry point."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rfp_intake.job import main

SAMPLES = Path(__file__).parent.parent.parent / "samples"
SMALL_PDF = SAMPLES / "Synthetic_RFP_NEOD001.pdf"


@pytest.fixture
def job_run(run_dir: Path) -> str:
    """Set up a run directory with a PDF and return the run_id."""
    run_id = "job-test-001"
    inputs = run_dir / run_id / "inputs"
    inputs.mkdir(parents=True)
    shutil.copy(SMALL_PDF, inputs / "Synthetic_RFP_NEOD001.pdf")
    return run_id


class TestJobMain:
    def test_successful_run(self, job_run: str, run_dir: Path) -> None:
        main(job_run)

        run_path = run_dir / job_run

        # status.json exists with completed state
        status_file = run_path / "status.json"
        assert status_file.exists()
        status = json.loads(status_file.read_text())
        assert status["state"] == "completed"
        assert status["node"] == "DONE"
        assert status["run_id"] == job_run

        # extraction.json exists with resolved fields
        extraction_file = run_path / "extraction.json"
        assert extraction_file.exists()
        extraction = json.loads(extraction_file.read_text())
        assert extraction["run_id"] == job_run
        assert isinstance(extraction["resolved_fields"], list)
        assert isinstance(extraction["contradictions"], list)
        assert isinstance(extraction["errors"], list)
        assert extraction["registry_version"]

        # report.pdf and report.xlsx exist and are non-trivial
        pdf_file = run_path / "report.pdf"
        assert pdf_file.exists()
        assert pdf_file.read_bytes().startswith(b"%PDF")

        xlsx_file = run_path / "report.xlsx"
        assert xlsx_file.exists()
        assert xlsx_file.stat().st_size > 0

    def test_no_inputs_dir_exits(self, run_dir: Path) -> None:
        with pytest.raises(SystemExit, match="No inputs directory"):
            main("nonexistent-run")

    def test_status_transitions(self, job_run: str, run_dir: Path) -> None:
        """Verify the final status reflects successful completion."""
        main(job_run)

        status = json.loads((run_dir / job_run / "status.json").read_text())
        assert status["started_at"]
        assert status["heartbeat_at"]
        assert status["state"] == "completed"

    def test_extraction_output_structure(self, job_run: str, run_dir: Path) -> None:
        main(job_run)

        extraction = json.loads((run_dir / job_run / "extraction.json").read_text())
        assert "run_id" in extraction
        assert "resolved_fields" in extraction
        assert "contradictions" in extraction
        assert "errors" in extraction
        assert isinstance(extraction["resolved_fields"], list)
        assert isinstance(extraction["errors"], list)
