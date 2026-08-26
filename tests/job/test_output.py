"""Tests for job/output.py — the I/O wrapper over render/'s pure functions."""

from __future__ import annotations

import json
import os
from pathlib import Path

from rfp_intake.domain.schemas import ResolvedField, RunState
from rfp_intake.job.output import write_reports


def _use_real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    from rfp_intake.domain.registry import get_registry
    get_registry.cache_clear()


class TestWriteReports:
    def test_writes_all_three_files(self, fields_yaml_path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        state = RunState(run_id="r-1", resolved=[
            ResolvedField(field_id="ops.sites_total", value=75, status="confirmed", confidence=0.9),
        ])

        paths = write_reports(tmp_path, state)

        assert Path(paths["extraction_json"]).exists()
        assert Path(paths["report_pdf"]).exists()
        assert Path(paths["report_xlsx"]).exists()
        assert (tmp_path / "extraction.json").read_bytes()
        assert (tmp_path / "report.pdf").read_bytes().startswith(b"%PDF")
        assert (tmp_path / "report.xlsx").stat().st_size > 0

    def test_extraction_json_has_generated_at(self, fields_yaml_path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        write_reports(tmp_path, RunState(run_id="r-1"))
        doc = json.loads((tmp_path / "extraction.json").read_text())
        assert doc["generated_at"]

    def test_no_leftover_tmp_files(self, fields_yaml_path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        write_reports(tmp_path, RunState(run_id="r-1"))
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []
