"""FIND_SECTIONS on the two real sample PDFs — the stage 1 test in PLAN_2026-10-02.md.

Offline: the PDFs are read from `samples/`, and nothing here calls a model.
Marked `slow` with the other tests that parse real PDFs.

The text and the bookmarks come from `Rung1Parser`, the parser INGEST actually
used on this pair, so the offsets asserted below are offsets into the same text
the extraction step will see. Only `Rung1Parser.parse` is bypassed, because its
pdfplumber table pass takes minutes on a 137-page protocol and FIND_SECTIONS
reads no tables.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytest

from rfp_intake.domain.schemas import Document
from rfp_intake.sections import find_sections, section_text

PROTOCOL = "Example protocol 2.pdf"
SYNTHETIC_RFP = "Synthetic_RFP_NEOD001.pdf"


@lru_cache(maxsize=4)
def _document(pdf: Path, doc_id: str, kind: str) -> Document:
    """Parsed once per session, because `samples_dir` is a function-scoped fixture
    and re-reading a 137-page PDF for each test would be the slowest thing here."""
    from rfp_intake.ingest.parsers.rung1 import Rung1Parser

    parser = Rung1Parser()
    pages = parser._extract_text(pdf)  # noqa: SLF001 - the real parser's text, without its tables
    outline = parser._extract_outline(pdf)  # noqa: SLF001
    return Document(
        id=doc_id,
        path=str(pdf),
        kind=kind,  # type: ignore[arg-type]
        pages=len(pages),
        page_texts={p.page_num: p.text for p in pages},
        outline=outline,
    )


@pytest.fixture
def protocol(samples_dir: Path) -> Document:
    pdf = samples_dir / PROTOCOL
    if not pdf.exists():
        pytest.skip(f"{PROTOCOL} not available")
    return _document(pdf, "doc-protocol", "protocol")


@pytest.fixture
def synthetic_rfp(samples_dir: Path) -> Document:
    pdf = samples_dir / SYNTHETIC_RFP
    if not pdf.exists():
        pytest.skip(f"{SYNTHETIC_RFP} not available")
    return _document(pdf, "doc-rfp", "rfp")


@pytest.mark.slow
class TestProtocol:
    def test_sections_come_from_bookmarks(self, protocol: Document) -> None:
        _, source = find_sections(protocol)
        assert source == "bookmarks"

    def test_clinical_experience_starts_on_page_39_and_ends_inside_page_40(
        self, protocol: Document
    ) -> None:
        """The section that produced the wrong study phase is bounded exactly.

        Known problem 1 in CLAUDE.md: `study.phase` came back `phase_1_2` from
        section 1.3.2, which describes study NEOD001-001, not this one. PLAN could
        not drop that text while keeping section 1.3.1 on the same page.
        """
        sections, _ = find_sections(protocol)

        clinical = _one(sections, "1.3.2 Clinical Experience")
        rationale = _one(sections, "1.4 Rationale for Dose Selection")

        assert clinical.page_start == 39
        assert clinical.start_offset > 0, "the heading is part-way down page 39"
        # It ends where 1.4 begins, which is part-way down page 40.
        assert (clinical.page_end, clinical.end_offset) == (
            rationale.page_start,
            rationale.start_offset,
        )
        assert clinical.page_end == 40

    def test_the_boundary_inside_page_39_falls_after_the_end_of_section_1_3_1(
        self, protocol: Document
    ) -> None:
        """Section 1.3.1 ends part-way down page 39, and 1.3.2 starts at that same
        position, so the page is cut in two with no text lost between the halves."""
        sections, _ = find_sections(protocol)

        nonclinical = _one(sections, "1.3.1 Nonclinical Safety")
        clinical = _one(sections, "1.3.2 Clinical Experience")

        # 1.3.1 ends on page 39, not at the end of page 39.
        assert nonclinical.page_end == 39
        assert nonclinical.end_offset is not None
        assert 0 < nonclinical.end_offset < len(protocol.page_texts[39])
        # 1.3.2 starts at exactly the position 1.3.1 ends at: nothing between them.
        assert (nonclinical.page_end, nonclinical.end_offset) == (
            clinical.page_start,
            clinical.start_offset,
        )

        assert section_text(protocol, nonclinical).rstrip().endswith(
            "as would typically be performed in clinical development, is warranted."
        )
        assert section_text(protocol, clinical).lstrip().startswith("1.3.2")

    def test_the_other_studys_phase_is_only_in_section_1_3_2(self, protocol: Document) -> None:
        """"ongoing, open-label Phase 1/2" is in 1.3.2 and in no neighbouring section."""
        sections, _ = find_sections(protocol)

        def mentions_the_other_study(heading: str) -> bool:
            section = _one(sections, heading)
            return "ongoing, open-label" in section_text(protocol, section)

        assert mentions_the_other_study("1.3.2 Clinical Experience")
        assert not mentions_the_other_study("1.3.1 Nonclinical Safety")
        assert not mentions_the_other_study("1.4 Rationale for Dose Selection")

    def test_sections_tile_the_document_without_overlap(self, protocol: Document) -> None:
        sections, _ = find_sections(protocol)

        rejoined = "".join(section_text(protocol, s) for s in sections)
        whole = "".join(
            protocol.page_texts.get(page, "") for page in sorted(protocol.page_texts)
        )
        assert rejoined == whole

    def test_every_bookmark_becomes_a_section(self, protocol: Document) -> None:
        sections, _ = find_sections(protocol)

        # One section per bookmark, plus the front matter before the first one.
        # Two bookmarks of this document share a position with another, so the
        # count is bounded rather than exact.
        assert len(protocol.outline) - 5 <= len(sections) <= len(protocol.outline) + 1


@pytest.mark.slow
class TestSyntheticRfp:
    def test_one_section_covering_every_page(self, synthetic_rfp: Document) -> None:
        """Known problem 1a in CLAUDE.md: this PDF has no bookmarks, so PLAN gave
        every field group pages 1 to 5 and never read page 6, the services
        requested. Rule 2 sends the whole document instead."""
        assert synthetic_rfp.outline == []

        sections, source = find_sections(synthetic_rfp)

        assert source == "whole_document"
        assert len(sections) == 1
        assert (sections[0].page_start, sections[0].page_end) == (1, synthetic_rfp.pages)
        assert synthetic_rfp.pages == 6

    def test_page_6_is_inside_the_section(self, synthetic_rfp: Document) -> None:
        sections, _ = find_sections(synthetic_rfp)

        text = section_text(synthetic_rfp, sections[0])
        page_6 = synthetic_rfp.page_texts[6].strip()
        assert page_6
        assert page_6[:200] in text


def _one(sections: list, heading: str):  # type: ignore[type-arg, no-untyped-def]
    """The one section with this heading, failing loudly if it is not unique."""
    matches = [s for s in sections if s.heading.strip() == heading]
    assert len(matches) == 1, f"{heading}: found {len(matches)} sections"
    return matches[0]
