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
    """A candidate disagreement (RECONCILE) or an adjudicated one (ADJUDICATE).

    verdict/explanation/severity are None for a candidate RECONCILE has found
    but ADJUDICATE has not yet judged. ARCHITECTURE.md §4.7 lists these as
    ADJUDICATE's output, not RECONCILE's — a None verdict is "not yet
    adjudicated", not "not a conflict".
    """

    field_id: str
    records: list[FieldRecord]
    verdict: Literal["conflict", "reconcilable", "not_a_conflict"] | None = None
    explanation: str | None = None
    resolved_value: Any | None = None
    winning_doc_id: str | None = None
    # 1-based position in `records` of the single record the resolved value came
    # from, numbered exactly as ADJUDICATE's prompt numbers the candidates.
    # winning_doc_id alone cannot name a record: a 137-page protocol routinely
    # supplies several records for one field, so "the winning document" picked out
    # the earliest page rather than the best evidence (CLAUDE.md, stage 5). None
    # for a candidate not yet judged, and for a `conflict`, where
    # reconcile/precedence.py decides by document and not by record.
    winning_record_index: int | None = None
    severity: Literal["high", "medium", "low"] | None = None


class ResolvedField(BaseModel):
    field_id: str
    value: Any | None = None
    status: Literal["confirmed", "needs_review", "not_found", "not_specified"]
    confidence: float = Field(ge=0.0, le=1.0)
    sources: list[Provenance] = []
    quote: str | None = None
    # "total" | "cohort:A" | "country:DE" — carried from FieldRecord.scope so a
    # scoped field can have multiple ResolvedField entries that are not treated
    # as conflicting with each other. See ARCHITECTURE.md §3 on FieldRecord.scope.
    scope: str | None = None
    contradiction: Contradiction | None = None
    derived_from: list[str] = Field(default_factory=list)
    # Free-text explanation for a derived field's score (ARCHITECTURE.md §4.8:
    # "the report prints the contributing evidence"). Unused for extracted fields.
    notes: str | None = None


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


class Section(BaseModel):
    """One span of a document's text, bounded precisely enough to cut a page in two.

    FIND_SECTIONS (PLAN_2026-10-02.md stage 1) produces these; PLAN chooses whole
    sections instead of whole pages, so a section that starts halfway down a page
    must be able to say so. A boundary is therefore a (page, character offset into
    that page's `Document.page_texts` entry) pair, with `end_offset` exclusive.

    A section runs until the next heading of any level, so the spans of one
    document's sections tile its text without overlapping.
    """

    id: str
    heading: str
    level: int = 1
    page_start: int
    page_end: int
    start_offset: int = 0
    # Exclusive. None means "to the end of page_end's text", which is what the
    # last section of a document gets rather than a length nothing re-checks.
    end_offset: int | None = None


class TextSpan(BaseModel):
    """A range of one page's text, in the coordinates `Section` uses.

    `end_offset` is exclusive, and None means "to the end of this page's text" —
    the same convention as `Section.end_offset`, so a whole-section removal can
    be expressed without re-measuring the page.
    """

    page: int
    start_offset: int = 0
    end_offset: int | None = None


class SetAsideSection(BaseModel):
    """One section SET_ASIDE_SECTIONS removed, kept so a person can see what went.

    Stage 3 of PLAN_2026-10-02.md. It is deliberately not in `report.pdf` — the
    report stays short — but it is in `extraction.json`, because a field that
    comes back empty is often explained by a section that was set aside.

    `matched` is the `config/sections.yaml` entry that matched the heading.
    `via_parent` is set instead when this section was removed only because the
    section it nests under was removed; the heading itself was not on the list.
    """

    doc_id: str
    section_id: str
    heading: str
    page_start: int
    page_end: int
    matched: str | None = None
    via_parent: str | None = None


class RemovedPassage(BaseModel):
    """One passage MARK_OTHER_STUDY removed because it describes a different study.

    Stage 4 of PLAN_2026-10-02.md. `study.phase` came back as the phase of an
    earlier study the protocol merely mentions, from section 1.3.2 "Clinical
    Experience"; this is the record of that text being taken out.

    Two shapes, both carried here so the report can list them together:
    `verdict="other_study"` is a whole section, and `verdict="mixed"` is one
    sentence out of a section that otherwise describes this study.

    `spans` is what actually removes the text — `rfp_intake.sections.
    section_page_texts` cuts these ranges out of what PLAN and EXTRACT see. The
    offsets are into `Document.page_texts[page]`, the same coordinates `Section`
    uses, so nothing has to re-derive them. `text` is the removed text verbatim,
    for the report appendix, and `reason` is the model's own one sentence.
    """

    doc_id: str
    section_id: str
    heading: str
    page_start: int
    page_end: int
    verdict: Literal["other_study", "mixed"]
    reason: str
    text: str
    spans: list[TextSpan] = Field(default_factory=list)


class Document(BaseModel):
    id: str
    path: str
    kind: Literal["rfp", "protocol", "amendment", "soa", "other"] | None = None
    pages: int = 0
    outline: list[OutlineEntry] = Field(default_factory=list)
    tables: list[TableData] = Field(default_factory=list)
    page_texts: dict[int, str] = Field(default_factory=dict)
    sections: list[Section] = Field(default_factory=list)
    # Text MARK_OTHER_STUDY took out because it describes a different study.
    # Stored on the document rather than only on RunState because this is what
    # makes the removal real: `section_page_texts` reads it, so PLAN's scoring and
    # EXTRACT's excerpt both stop seeing the text, from one place.
    removed: list[RemovedPassage] = Field(default_factory=list)
    # Which of FIND_SECTIONS' three rules produced `sections`, for the report and
    # for debugging. None until FIND_SECTIONS has run.
    section_source: (
        Literal["bookmarks", "whole_document", "heading_scan", "whole_document_fallback"] | None
    ) = None
    version_label: str | None = None
    document_date: str | None = None
    sponsor: str | None = None
    protocol_id: str | None = None
    # The study's own title, from CLASSIFY. MARK_OTHER_STUDY puts it in the prompt
    # beside `protocol_id`, because a protocol that mentions an earlier study of
    # the same drug often prints both study numbers and the title is what tells
    # them apart. None when the first pages do not state one.
    title: str | None = None
    confidence: float | None = None
    parsing_metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionTask(BaseModel):
    doc_id: str
    group: str
    # The first and last page the task's sections touch. `extract/validate.py`
    # checks each extracted record's page against it, and it narrows a task that
    # had to be split because its sections did not fit one extraction call.
    #
    # When the chosen sections are not next to each other, this spans the gap
    # between them, so a record can name a page whose text is not in the excerpt.
    # Quote validation is what actually keeps unchosen text out: the quote must
    # be a substring of the excerpt, and the excerpt holds only chosen sections.
    page_window: tuple[int, int]
    # Which of the document's sections this task reads, by `Section.id`, chosen by
    # PLAN. Empty means "every page in page_window", which is what PLAN produced
    # before 2026-10-02 and what a task built by hand without sections still means.
    section_ids: list[str] = Field(default_factory=list)
    budget_tokens: int | None = None


class RunError(BaseModel):
    node: str
    task_id: str | None = None
    error: str
    # "validation" means the model answered and the answer was rejected — the
    # call itself worked. "call_failed" means the step never got an answer.
    # job/__init__.py uses the distinction to tell a run that extracted nothing
    # because the documents were silent from one that extracted nothing because
    # every LLM call failed.
    kind: Literal["call_failed", "validation"] = "call_failed"
    timestamp: datetime = Field(default_factory=datetime.now)


class Replace(list):  # type: ignore[type-arg]
    """Marker list telling append_or_replace to overwrite rather than append.

    A node that rewrites the whole collection returns Replace(items); a node that
    contributes one branch of a fan-in returns a plain list.
    """


def append_or_replace(current: list[Any], update: list[Any]) -> list[Any]:
    """Reducer for collections that are both fanned into and rewritten.

    EXTRACT runs one branch per (document, field group) and each branch returns
    only its own records, so the default LangGraph behaviour there must be to
    append. NORMALIZE, by contrast, rewrites every record it was given — and
    returning that full list under a plain append reducer silently doubled the
    output, which is what shipped until 2026-08-27. Distinguishing the two cases
    at the type level makes the intent explicit at each return site rather than
    leaving it to whoever next reads the reducer.
    """
    if isinstance(update, Replace):
        return list(update)
    return current + update


class RunState(BaseModel):
    run_id: str = ""
    documents: list[Document] = Field(default_factory=list)
    tasks: list[ExtractionTask] = Field(default_factory=list)
    records: Annotated[list[FieldRecord], append_or_replace] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    resolved: list[ResolvedField] = Field(default_factory=list)
    # Sections SET_ASIDE_SECTIONS removed, for extraction.json. No reducer: the
    # node is the only thing that writes this and it runs once, so the plain
    # last-value-wins behaviour is right. An append reducer here would double the
    # list if the node ever ran twice, which is the bug append_or_replace exists
    # to document.
    set_aside: list[SetAsideSection] = Field(default_factory=list)
    # Passages MARK_OTHER_STUDY removed, flattened across documents, for
    # extraction.json and the report's Appendix C. The same objects are on each
    # Document, where they do the removing; this list is the report's view of
    # them. No reducer, for the same reason as `set_aside` above.
    removed_passages: list[RemovedPassage] = Field(default_factory=list)
    report_paths: dict[str, str] = Field(default_factory=dict)
    errors: Annotated[list[RunError], operator.add] = Field(default_factory=list)
