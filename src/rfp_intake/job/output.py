"""Report writer — the thin I/O layer over render/'s pure functions.
Writes extraction.json, report.pdf, report.xlsx into the run directory.
See ARCHITECTURE.md §4.10, §6.2.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from rfp_intake.domain.registry import Registry, get_registry
from rfp_intake.domain.schemas import RunState
from rfp_intake.render import build_extraction_document, build_report_pdf, build_report_xlsx


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.rename(path)


def write_reports(
    run_path: Path, state: RunState, registry: Registry | None = None
) -> dict[str, str]:
    """Write extraction.json, report.pdf, report.xlsx to run_path.

    Returns the report_paths mapping (ARCHITECTURE.md §6.2's report_paths).
    """
    if registry is None:
        registry = get_registry()

    generated_at = datetime.now(UTC).isoformat()

    extraction_doc = build_extraction_document(state, registry)
    extraction_doc["generated_at"] = generated_at
    extraction_json_path = run_path / "extraction.json"
    _write_atomic(
        extraction_json_path,
        json.dumps(extraction_doc, indent=2, default=str).encode("utf-8"),
    )

    report_pdf_path = run_path / "report.pdf"
    _write_atomic(report_pdf_path, build_report_pdf(state, registry, generated_at=generated_at))

    report_xlsx_path = run_path / "report.xlsx"
    _write_atomic(report_xlsx_path, build_report_xlsx(state, registry))

    return {
        "extraction_json": str(extraction_json_path),
        "report_pdf": str(report_pdf_path),
        "report_xlsx": str(report_xlsx_path),
    }
