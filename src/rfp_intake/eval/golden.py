"""Golden set loader — hand-labeled expected values per document."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
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


class GoldenContradiction(BaseModel):
    """Expected ADJUDICATE outcome for one planted, known-genuine contradiction.

    Not a negative-examples set — every entry here IS a real planted
    disagreement, so this set can score verdict-classification accuracy and
    candidate-detection recall, but not detection precision/false-positive
    rate (that needs a labeled "these do NOT conflict" set, which this file
    doesn't have — see ARCHITECTURE.md §9).
    """

    field_id: str
    description: str = ""
    documents: list[str] = Field(default_factory=list)
    expected_verdict: Literal["conflict", "reconcilable", "not_a_conflict"]
    severity: Literal["high", "medium", "low"] | None = None


def load_golden_contradictions(path: Path) -> list[GoldenContradiction]:
    """Load planted-contradiction golden labels from a YAML file.

    A golden set with two entries for the same field_id is not supported —
    scoring matches by field_id and the current corpus never repeats one.
    """
    if not path.exists():
        return []

    data: dict[str, Any] = yaml.safe_load(path.read_text())
    return [GoldenContradiction.model_validate(c) for c in data.get("contradictions", [])]
