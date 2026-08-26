"""Extraction output writer — writes canonical extraction.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_extraction_json(run_path: Path, state: dict[str, Any]) -> None:
    """Write the canonical extraction output to runs/{run_id}/extraction.json."""
    records = state.get("records", [])
    errors = state.get("errors", [])
    output = {
        "run_id": run_path.name,
        "records": [r.model_dump(mode="json") for r in records],
        "errors": [e.model_dump(mode="json") for e in errors],
    }
    path = run_path / "extraction.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(output, indent=2, default=str))
    tmp.rename(path)
