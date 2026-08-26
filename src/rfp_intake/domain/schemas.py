"""Core Pydantic models from ARCHITECTURE.md §3."""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    doc_id: str
    doc_kind: Literal["rfp", "protocol", "amendment", "soa", "other"]
    doc_version: str | None = None
    doc_date: str | None = None
    page: int
    section: str | None = None
    char_span: tuple[int, int] | None = None


class FieldRecord(BaseModel):
    """One assertion about one field, from one place in one document."""

    field_id: str
    group: str
    raw_value: str
    value: Any | None = None
    unit: str | None = None
    quote: str
    provenance: Provenance
    status: Literal["found", "not_specified", "not_found"] = "found"
    confidence: float = Field(ge=0.0, le=1.0)
    scope: str | None = None
    notes: str | None = None


class Contradiction(BaseModel):
    field_id: str
    records: list[FieldRecord]
    verdict: Literal["conflict", "reconcilable", "not_a_conflict"]
    explanation: str
    resolved_value: Any | None = None
    winning_doc_id: str | None = None
    severity: Literal["high", "medium", "low"]


class ResolvedField(BaseModel):
    field_id: str
    value: Any | None = None
    status: Literal["confirmed", "needs_review", "not_found", "not_specified"]
    confidence: float = Field(ge=0.0, le=1.0)
    sources: list[Provenance] = []
    quote: str | None = None
    contradiction: Contradiction | None = None
    derived_from: list[str] = Field(default_factory=list)


class OutlineEntry(BaseModel):
    heading: str
    page_start: int
    page_end: int | None = None
    level: int = 1


class TableData(BaseModel):
    page: int
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    caption: str | None = None


class Document(BaseModel):
    id: str
    path: str
    kind: Literal["rfp", "protocol", "amendment", "soa", "other"] | None = None
    pages: int = 0
    outline: list[OutlineEntry] = Field(default_factory=list)
    tables: list[TableData] = Field(default_factory=list)
    page_texts: dict[int, str] = Field(default_factory=dict)
    version_label: str | None = None
    document_date: str | None = None
    sponsor: str | None = None
    protocol_id: str | None = None
    confidence: float | None = None
    parsing_metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionTask(BaseModel):
    doc_id: str
    group: str
    page_window: tuple[int, int]
    budget_tokens: int | None = None


class RunError(BaseModel):
    node: str
    task_id: str | None = None
    error: str
    timestamp: datetime = Field(default_factory=datetime.now)


class RunState(BaseModel):
    run_id: str = ""
    documents: list[Document] = Field(default_factory=list)
    tasks: list[ExtractionTask] = Field(default_factory=list)
    records: Annotated[list[FieldRecord], operator.add] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    resolved: list[ResolvedField] = Field(default_factory=list)
    report_paths: dict[str, str] = Field(default_factory=dict)
    errors: Annotated[list[RunError], operator.add] = Field(default_factory=list)
