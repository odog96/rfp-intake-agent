"""Golden set loader — hand-labeled expected values per document."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class GoldenField(BaseModel):
    """Expected answer for a single field in a document."""

    expected_value: str | float | int | bool | None = None
    expected_status: Literal["found", "not_specified", "not_found"] = "found"
    expected_page: int | None = None
    expected_quote: str | None = None
    notes: str | None = None


class GoldenDocument(BaseModel):
    """Expected answers for all labeled fields in a single document."""

    document_id: str
    document_kind: Literal["rfp", "protocol", "amendment", "soa", "other"]
    fields: dict[str, GoldenField] = Field(default_factory=dict)


def load_golden_set(golden_dir: Path) -> dict[str, GoldenDocument]:
    """Load all golden answer JSON files from a directory.

    Returns a dict keyed by document_id.
    """
    result: dict[str, GoldenDocument] = {}
    if not golden_dir.exists():
        return result

    for json_file in sorted(golden_dir.glob("*.json")):
        with json_file.open() as f:
            data = json.load(f)
        doc = GoldenDocument.model_validate(data)
        result[doc.document_id] = doc

    return result
