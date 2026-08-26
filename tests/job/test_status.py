"""Tests for the StatusWriter and status.json schema."""

from __future__ import annotations

import json
from pathlib import Path

from rfp_intake.job.status import DocumentStatus, RunStatus, StatusWriter


class TestRunStatus:
    def test_schema_fields(self) -> None:
        status = RunStatus(
            run_id="r-001",
            state="running",
            node="EXTRACT",
            started_at="2026-08-21T14:02:11+00:00",
            heartbeat_at="2026-08-21T14:03:47+00:00",
            progress={"tasks_total": 27, "tasks_done": 19},
            documents=[
                DocumentStatus(name="RFP.pdf", state="parsed", parser_rung=1, pages=42),
            ],
        )
        assert status.run_id == "r-001"
        assert status.state == "running"
        assert status.node == "EXTRACT"
        assert status.progress == {"tasks_total": 27, "tasks_done": 19}
        assert status.documents[0].name == "RFP.pdf"
        assert status.error is None

    def test_schema_serialization(self) -> None:
        status = RunStatus(
            run_id="r-002",
            state="failed",
            node="INGEST",
            started_at="2026-08-21T14:00:00+00:00",
            heartbeat_at="2026-08-21T14:00:05+00:00",
            error="No PDF files found",
        )
        data = json.loads(status.model_dump_json())
        assert data["state"] == "failed"
        assert data["error"] == "No PDF files found"
        assert data["documents"] == []


class TestStatusWriter:
    def test_write_creates_status_json(self, tmp_path: Path) -> None:
        run_path = tmp_path / "test-run"
        run_path.mkdir()

        writer = StatusWriter(run_path)
        writer.write("starting", node="INGEST")

        status_file = run_path / "status.json"
        assert status_file.exists()
        data = json.loads(status_file.read_text())
        assert data["run_id"] == "test-run"
        assert data["state"] == "starting"
        assert data["node"] == "INGEST"

    def test_write_atomic_no_tmp_left(self, tmp_path: Path) -> None:
        run_path = tmp_path / "test-run"
        run_path.mkdir()

        writer = StatusWriter(run_path)
        writer.write("running", node="CLASSIFY")

        assert not (run_path / "status.tmp").exists()
        assert (run_path / "status.json").exists()

    def test_write_updates_heartbeat(self, tmp_path: Path) -> None:
        run_path = tmp_path / "test-run"
        run_path.mkdir()

        writer = StatusWriter(run_path)
        writer.write("starting", node="INGEST")
        data1 = json.loads((run_path / "status.json").read_text())

        writer.write("running", node="CLASSIFY")
        data2 = json.loads((run_path / "status.json").read_text())

        assert data1["started_at"] == data2["started_at"]
        assert data2["state"] == "running"
        assert data2["node"] == "CLASSIFY"

    def test_write_with_progress(self, tmp_path: Path) -> None:
        run_path = tmp_path / "test-run"
        run_path.mkdir()

        writer = StatusWriter(run_path)
        writer.write(
            "running",
            node="EXTRACT",
            progress={"tasks_total": 9, "tasks_done": 3},
        )

        data = json.loads((run_path / "status.json").read_text())
        assert data["progress"] == {"tasks_total": 9, "tasks_done": 3}

    def test_write_with_documents(self, tmp_path: Path) -> None:
        run_path = tmp_path / "test-run"
        run_path.mkdir()

        writer = StatusWriter(run_path)
        docs = [DocumentStatus(name="RFP.pdf", state="parsed", parser_rung=1, pages=42)]
        writer.write("running", node="CLASSIFY", documents=docs)

        data = json.loads((run_path / "status.json").read_text())
        assert len(data["documents"]) == 1
        assert data["documents"][0]["name"] == "RFP.pdf"
        assert data["documents"][0]["pages"] == 42

    def test_write_with_error(self, tmp_path: Path) -> None:
        run_path = tmp_path / "test-run"
        run_path.mkdir()

        writer = StatusWriter(run_path)
        writer.write("failed", node="INGEST", error="File not found")

        data = json.loads((run_path / "status.json").read_text())
        assert data["state"] == "failed"
        assert data["error"] == "File not found"

    def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        run_path = tmp_path / "deep" / "nested" / "run"

        writer = StatusWriter(run_path)
        writer.write("starting", node="INGEST")

        assert (run_path / "status.json").exists()
