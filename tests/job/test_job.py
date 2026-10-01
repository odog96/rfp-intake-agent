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


class TestFailedRunIsVisible:
    """Regression for runs r-20260831-150720 .. r-20260901-140857: an expired
    Bedrock token failed every LLM call, and the job still reported
    "completed" with an empty `errors` list and a blank report.
    """

    def test_node_errors_reach_extraction_json(
        self, job_run: str, run_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rfp_intake.graph.nodes import classify as classify_mod

        def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("Bearer Token has expired")

        monkeypatch.setattr(classify_mod, "_classify_single", boom)

        # The mock model's canned quotes never survive validation, so this run
        # also extracts nothing and the job exits non-zero. The point here is
        # what reaches extraction.json, not the exit code.
        with pytest.raises(SystemExit):
            main(job_run)

        extraction = json.loads((run_dir / job_run / "extraction.json").read_text())
        nodes = {e["node"] for e in extraction["errors"]}
        assert "CLASSIFY" in nodes, "CLASSIFY errors were dropped from extraction.json"
        assert any("Bearer Token has expired" in e["error"] for e in extraction["errors"])

    def test_run_that_resolves_nothing_is_reported_failed(
        self, job_run: str, run_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rfp_intake import extract as extract_mod
        from rfp_intake.graph.nodes import classify as classify_mod

        def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("Bearer Token has expired")

        monkeypatch.setattr(classify_mod, "_classify_single", boom)
        monkeypatch.setattr(extract_mod, "extract_group", boom)

        with pytest.raises(SystemExit):
            main(job_run)

        status = json.loads((run_dir / job_run / "status.json").read_text())
        assert status["state"] == "failed"
        assert status["error"]
        assert "Bearer Token has expired" in status["error"]


class TestPreflight:
    """The job asks the model service one short question before reading anything.

    Runs r-20260831-150720 .. r-20260901-140857 parsed a 137-page protocol,
    called an expired credential 36 times and only then wrote an empty report.
    """

    def test_a_service_that_will_not_answer_stops_the_run_at_once(
        self, job_run: str, run_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import rfp_intake.job as job_mod
        from rfp_intake.llm.health import describe_failure

        monkeypatch.setattr(
            job_mod,
            "probe_model_service",
            lambda: describe_failure(
                "Bearer Token has expired", provider="bedrock", role="classify"
            ),
        )

        with pytest.raises(SystemExit):
            main(job_run)

        run_path = run_dir / job_run
        status = json.loads((run_path / "status.json").read_text())
        assert status["state"] == "failed"
        assert status["node"] == "PREFLIGHT"
        assert status["failure"]["kind"] == "model_service"
        assert status["failure"]["service"] == "AWS Bedrock"
        assert "not a problem with your documents" in status["failure"]["action"]
        assert "Bearer Token has expired" in status["failure"]["detail"]

        # Stopped before INGEST: no report was written, blank or otherwise.
        assert not (run_path / "extraction.json").exists()
        assert not (run_path / "report.pdf").exists()

    def test_a_service_that_answers_lets_the_run_proceed(
        self, job_run: str, run_dir: Path
    ) -> None:
        # The suite runs on the mock backend, where probe_model_service() has no
        # service to reach and says so by returning None.
        main(job_run)
        status = json.loads((run_dir / job_run / "status.json").read_text())
        assert status["state"] == "completed"
        assert status["failure"] is None


class TestFailureKindIsRecorded:
    """status.json says what kind of failure it was, so the Cloudera AI
    Application can tell an analyst whether to look at their documents."""

    def test_credential_that_lapses_mid_run_is_named_a_model_service_failure(
        self, job_run: str, run_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rfp_intake import extract as extract_mod
        from rfp_intake.graph.nodes import classify as classify_mod

        def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("Bearer Token has expired")

        monkeypatch.setattr(classify_mod, "_classify_single", boom)
        monkeypatch.setattr(extract_mod, "extract_group", boom)

        with pytest.raises(SystemExit):
            main(job_run)

        failure = json.loads((run_dir / job_run / "status.json").read_text())["failure"]
        assert failure["kind"] == "model_service"
        assert "expired" in failure["reason"]
        assert "not a problem with your documents" in failure["action"]

    def test_a_failure_with_no_provider_signature_is_not_blamed_on_the_service(
        self, job_run: str, run_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rfp_intake import extract as extract_mod
        from rfp_intake.graph.nodes import classify as classify_mod

        def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("the model returned nothing recognisable")

        monkeypatch.setattr(classify_mod, "_classify_single", boom)
        monkeypatch.setattr(extract_mod, "extract_group", boom)

        with pytest.raises(SystemExit):
            main(job_run)

        failure = json.loads((run_dir / job_run / "status.json").read_text())["failure"]
        assert failure["kind"] == "no_extraction"
