"""FIND_SECTIONS — split each document into sections. Pure Python, no LLM.

Stage 1 of `docs/PLAN_2026-10-02.md`. PLAN used to choose whole pages plus one
page of margin, so text from a section it had not chosen still reached the
extraction model — which is how the phase of a different study was extracted from
page 39 of `samples/Example protocol 2.pdf`. A section boundary here is a
(page, character offset into that page's text) pair, so a page can be cut in two.

Three rules, tried in order, recorded on each document as `section_source`:

1. `bookmarks` — the PDF's own outline, which INGEST already put on
   `Document.outline`. Each bookmark's heading is located in its page's text to
   get the offset; a heading that cannot be found falls back to the start of the
   page and is logged.
2. `whole_document` — no bookmarks, and the whole text fits in one extraction
   call (`DEFAULT_TOKEN_BUDGET`). One section covering every page. This is what
   `samples/Synthetic_RFP_NEOD001.pdf` lands on: 0 bookmarks, about 1,900
   estimated tokens, so all six pages are read instead of the first five.
3. `heading_scan` — no bookmarks and too long for rule 2, so headings are guessed
   from the text (`sections/headings.py`). `whole_document_fallback` is recorded
   when that guess finds nothing, because PLAN must never be handed a document
   with no sections.

Sections tile a document's text: each one runs to the start of the next heading,
so no text is in two sections and none is dropped. Text before the first heading
becomes a section headed "Front matter" rather than being lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import structlog

from rfp_intake.domain.schemas import Document, RunError, RunState, Section
from rfp_intake.plan.scoring import DEFAULT_TOKEN_BUDGET, estimate_tokens
from rfp_intake.sections.headings import find_headings

logger = structlog.get_logger()

SectionSource = Literal["bookmarks", "whole_document", "heading_scan", "whole_document_fallback"]

FRONT_MATTER_HEADING = "Front matter"
WHOLE_DOCUMENT_HEADING = "Whole document"


@dataclass(frozen=True)
class _Boundary:
    """Where one section starts: a page, an offset into that page's text, a heading."""

    page: int
    offset: int
    heading: str
    level: int


def find_sections(doc: Document) -> tuple[list[Section], SectionSource]:
    """Split one document into sections, and say which rule produced them."""
    first_page, last_page = _page_range(doc)

    if doc.outline:
        boundaries = _boundaries_from_outline(doc, first_page, last_page)
        if boundaries:
            return _sections_from_boundaries(doc, boundaries, first_page, last_page), "bookmarks"

    if estimate_tokens(doc.page_texts, (first_page, last_page)) <= DEFAULT_TOKEN_BUDGET:
        return [_whole_document(doc, first_page, last_page)], "whole_document"

    hits = find_headings(doc.page_texts)
    logger.info("section_heading_scan", doc_id=doc.id, headings_found=len(hits))
    if hits:
        boundaries = [
            _Boundary(page=hit.page, offset=hit.offset, heading=hit.heading, level=hit.level)
            for hit in hits
        ]
        return (
            _sections_from_boundaries(doc, boundaries, first_page, last_page),
            "heading_scan",
        )

    return [_whole_document(doc, first_page, last_page)], "whole_document_fallback"


def section_page_texts(doc: Document, section: Section) -> dict[int, str]:
    """The text of one section, page by page, so a caller can keep page markers.

    The first and last pages are cut at the section's offsets; the pages between
    are whole. A page missing from `Document.page_texts` yields an empty string.
    """
    out: dict[int, str] = {}
    for page in range(section.page_start, section.page_end + 1):
        text = doc.page_texts.get(page, "")
        start = section.start_offset if page == section.page_start else 0
        end = (
            section.end_offset
            if page == section.page_end and section.end_offset is not None
            else len(text)
        )
        out[page] = text[start:end]
    return out


def section_text(doc: Document, section: Section) -> str:
    """The whole text of one section, pages joined in order."""
    pages = section_page_texts(doc, section)
    return "".join(pages[page] for page in sorted(pages))


def find_sections_node(state: RunState) -> dict[str, Any]:
    """FIND_SECTIONS graph node. Pure Python — no model call, no I/O."""
    updated: list[Document] = []
    errors: list[RunError] = []

    for doc in state.documents:
        try:
            sections, source = find_sections(doc)
        except Exception as e:  # noqa: BLE001 - one unsplittable document must not end the run
            logger.error("find_sections_failed", doc_id=doc.id, error=str(e))
            errors.append(
                RunError(
                    node="FIND_SECTIONS",
                    task_id=doc.id,
                    error=f"Could not split {doc.id} into sections: {e}",
                    kind="validation",
                )
            )
            first_page, last_page = _page_range(doc)
            sections = [_whole_document(doc, first_page, last_page)]
            source = "whole_document_fallback"

        doc.sections = sections
        doc.section_source = source
        logger.info(
            "sections_found",
            run_id=state.run_id,
            doc_id=doc.id,
            source=source,
            sections=len(sections),
            pages=doc.pages,
        )
        updated.append(doc)

    return {"documents": updated, "errors": errors}


def _page_range(doc: Document) -> tuple[int, int]:
    """The first and last page that have text, falling back to 1..doc.pages."""
    if doc.page_texts:
        pages = sorted(doc.page_texts)
        return pages[0], pages[-1]
    return 1, max(doc.pages, 1)


def _whole_document(doc: Document, first_page: int, last_page: int) -> Section:
    return Section(
        id=_section_id(doc, 1),
        heading=WHOLE_DOCUMENT_HEADING,
        level=1,
        page_start=first_page,
        page_end=last_page,
        start_offset=0,
        end_offset=None,
    )


def _boundaries_from_outline(doc: Document, first_page: int, last_page: int) -> list[_Boundary]:
    """One boundary per bookmark, with the heading located in the page's text."""
    boundaries: list[_Boundary] = []
    not_located = 0

    for entry in doc.outline:
        page = min(max(entry.page_start, first_page), last_page)
        offset = _locate_heading(doc.page_texts.get(page, ""), entry.heading)
        if offset is None:
            not_located += 1
            offset = 0
        boundaries.append(
            _Boundary(page=page, offset=offset, heading=entry.heading, level=entry.level)
        )

    if not_located:
        logger.info(
            "section_headings_not_located",
            doc_id=doc.id,
            not_located=not_located,
            bookmarks=len(doc.outline),
        )
    return boundaries


def _locate_heading(text: str, heading: str) -> int | None:
    """The offset of `heading` in `text`, ignoring how whitespace is broken up.

    PyMuPDF returns the sample protocol's headings as "1.3.2 \\nClinical
    Experience", so the bookmark title "1.3.2 Clinical Experience" is not a
    literal substring of its page. Matching on whitespace-normalised, lowercased
    text and mapping the hit back to a raw offset finds 174 of that document's 176
    bookmark titles; a plain substring search finds far fewer.
    """
    needle = " ".join(heading.split()).lower()
    if not needle or not text:
        return None

    normalised: list[str] = []
    raw_offsets: list[int] = []
    previous_was_space = True
    for index, char in enumerate(text):
        if char.isspace():
            if previous_was_space:
                continue
            normalised.append(" ")
            raw_offsets.append(index)
            previous_was_space = True
        else:
            normalised.append(char.lower())
            raw_offsets.append(index)
            previous_was_space = False

    position = "".join(normalised).find(needle)
    if position < 0:
        return None
    return raw_offsets[position]


def _sections_from_boundaries(
    doc: Document,
    boundaries: list[_Boundary],
    first_page: int,
    last_page: int,
) -> list[Section]:
    """Turn start positions into sections that tile the document's text.

    Boundaries are put in document order by position, not by the order the
    bookmarks were listed in, because position order is document order. Two
    boundaries at the same position collapse into one, the shallower level first.
    """
    ordered = sorted(boundaries, key=lambda b: (b.page, b.offset, b.level))

    deduped: list[_Boundary] = []
    for boundary in ordered:
        if deduped and (boundary.page, boundary.offset) == (deduped[-1].page, deduped[-1].offset):
            continue
        deduped.append(boundary)

    if not deduped:
        return [_whole_document(doc, first_page, last_page)]

    if (deduped[0].page, deduped[0].offset) != (first_page, 0):
        deduped.insert(
            0,
            _Boundary(page=first_page, offset=0, heading=FRONT_MATTER_HEADING, level=1),
        )

    sections: list[Section] = []
    for index, boundary in enumerate(deduped):
        following = deduped[index + 1] if index + 1 < len(deduped) else None
        page_end, end_offset = _end_before(boundary, following, last_page)
        sections.append(
            Section(
                id=_section_id(doc, index + 1),
                heading=boundary.heading,
                level=boundary.level,
                page_start=boundary.page,
                page_end=page_end,
                start_offset=boundary.offset,
                end_offset=end_offset,
            )
        )
    return sections


def _end_before(
    boundary: _Boundary,
    following: _Boundary | None,
    last_page: int,
) -> tuple[int, int | None]:
    """Where the section starting at `boundary` ends, exclusive.

    A section that ends at offset 0 of the next page really ends at the end of the
    page before, and is recorded that way so `page_end` names only pages the
    section actually has text on. Stage 2 of `docs/PLAN_2026-10-02.md` builds each
    extraction task's page window from these numbers.
    """
    if following is None:
        return last_page, None
    if following.offset == 0 and following.page > boundary.page:
        return following.page - 1, None
    return following.page, following.offset


def _section_id(doc: Document, number: int) -> str:
    return f"{doc.id}:s{number:03d}"


__all__ = [
    "FRONT_MATTER_HEADING",
    "WHOLE_DOCUMENT_HEADING",
    "SectionSource",
    "find_sections",
    "find_sections_node",
    "section_page_texts",
    "section_text",
]
