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
    # The page number printed on that page, when the document prints one and it
    # is not the same as `page`. `page` is the page's position in the PDF file,
    # which is what a reader scrolling a viewer sees; the printed number is what
    # is on the paper. The protocol amendment's two cover pages make every
    # printed number two lower than its position, and "Amendment p.11" sent
    # Oliver Zarate to the wrong page on 2026-10-07. Added by change 7 of that
    # day's review; filled from `Document.page_labels` by
    # `domain/dynamic.py:extraction_response_to_records`, and None whenever the
    # two numbers agree or no printed number could be read.
    printed_page: str | None = None
    # The heading of the section the quote was read from, set by EXTRACT since
    # 2026-10-12 (extract/prompt.py:quote_section). None when no chosen
    # section's text on the record's page holds the quote. A field's
    # `prefer_sections` in config/fields.yaml reads it.
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
    # the earliest page rather than the best evidence (CLAUDE.md, stage 5). For a
    # `conflict` it is the record reconcile/precedence.py chose (since 2026-10-12).
    # None for a candidate not yet judged, and for a `conflict` precedence could
    # not decide.
    winning_record_index: int | None = None
    # For a `conflict`, the precedence rule that chose the value, or
    # "no_silent_resolution" where none did (reconcile/precedence.py). The
    # report says which rule it was. None for any other verdict, and in runs
    # made before 2026-10-16.
    precedence_rule: str | None = None
    severity: Literal["high", "medium", "low"] | None = None
    # 1-based positions in `records`, numbered as `winning_record_index` is, of
    # the records ADJUDICATE said are not about this field at all (fix 5 of the
    # 2026-10-12 brief: a lab instruction read as a monitoring strategy). They
    # get no row; Appendix A still lists each one, marked. The records stay in
    # `records`, so nothing is lost. Empty for a `conflict`, where they are
    # kept as sides (adjudicate/__init__.py:_off_topic).
    off_topic_record_indexes: list[int] = Field(default_factory=list)


class ResolvedField(BaseModel):
    field_id: str
    value: Any | None = None
    # "not_applicable": a rule in config/fields.yaml says the question does not
    # arise, because of another field's answer (reconcile/rules.py). A fourth
    # terminal state, kept apart from not_found and not_specified (rule 3):
    # the documents were not silent, the question simply has no answer.
    status: Literal["confirmed", "needs_review", "not_found", "not_specified", "not_applicable"]
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
    # A derived field's reasons in plain words, for the report's "Why:" line
    # (fix 8 of the 2026-10-12 brief). `notes` keeps the full account, with the
    # score and every input's quote, for Appendix A. Unused for extracted fields.
    why: str | None = None
    # Why RECONCILE wants a person to look at this row, in plain words — for
    # now only a field with `check_scopes_across_documents` writes one (an arm
    # listed in only one document, or an entry whose text does not say what its
    # scope names). GATE keeps the row in review, and the report prints the note
    # as the row's reason. Document ids in it are named by the report.
    review_note: str | None = None
    # The records an `object[]` field's entries came from, each with its own
    # document, page and quote, so one entry's keys can be merged across the
    # documents (reconcile/entries.py) and each document's own words shown.
    # Filled by RECONCILE for an `object[]` field with a schema; empty otherwise.
    # Added 2026-10-12, fix 1 of that day's brief.
    statements: list[FieldRecord] = Field(default_factory=list)
    # Each source's own quote, in the order of `sources`, where RECONCILE folded
    # several records into this row (_merge_collection). `quote` is only the
    # most confident one, and Appendix A prints each of these beside its own
    # page. Empty on any other row, and on rows made before 2026-10-12.
    source_quotes: list[str] = Field(default_factory=list)


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
    # The numbers printed on those two pages, where the document prints them and
    # they differ from the PDF positions. Appendix C cites a removed passage by
    # page like every other reference, so it needs the same pair of numbers. See
    # `Provenance.printed_page`; stamped by `other_study/__init__.py`, which has
    # the document in hand when it builds the passage.
    printed_page_start: str | None = None
    printed_page_end: str | None = None
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
    # PDF page position -> the page number printed on that page, for the pages
    # where the two differ and a printed number could be read. Filled by INGEST
    # from `ingest/page_labels.py`; see `Provenance.printed_page`. Empty for a
    # document that prints no page numbers, and for every run made before
    # 2026-10-08, the starting run r-20261007-140411 included.
    page_labels: dict[int, str] = Field(default_factory=dict)
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
    # The answer EXTRACT threw away, kept so a dropped record can be read
    # rather than inferred. Set only for a record that failed validation after
    # the repair retry, or that extract/validate.py:answers_field rejected,
    # which gets no retry. Before 2026-10-11 only the reason was kept, so the quote
    # behind run r-20261009-225414's Site selection error could not be shown.
    rejected_value: str | None = None
    rejected_quote: str | None = None
    # The page the rejected record named. Added 2026-10-12 so the records the
    # two rules of extract/validate.py:answers_field reject can be listed by
    # field, page and quote.
    rejected_page: int | None = None
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
