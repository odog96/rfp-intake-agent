"""FIND_SECTIONS on hand-built documents — the three rules, offline, no PDFs."""

from __future__ import annotations

from rfp_intake.domain.schemas import Document, OutlineEntry, RunState
from rfp_intake.plan.scoring import DEFAULT_TOKEN_BUDGET
from rfp_intake.sections import (
    FRONT_MATTER_HEADING,
    WHOLE_DOCUMENT_HEADING,
    find_sections,
    find_sections_node,
    section_page_texts,
    section_text,
)
from rfp_intake.sections.headings import find_headings


def _doc(page_texts: dict[int, str], outline: list[OutlineEntry] | None = None) -> Document:
    return Document(
        id="doc-001",
        path="/tmp/test.pdf",
        kind="protocol",
        pages=max(page_texts) if page_texts else 0,
        page_texts=page_texts,
        outline=outline or [],
    )


def _long_page(label: str) -> str:
    """A page long enough that a few of them exceed one extraction call."""
    return (f"{label} " * 400).strip()


class TestRule1Bookmarks:
    def test_boundary_cuts_a_page_in_two(self) -> None:
        page_two = (
            "The end of the first section, which is warranted. \n"
            "1.3.2 \nClinical Experience \nAn ongoing open-label study of the drug. \n"
        )
        doc = _doc(
            {1: "Title page \n1.3.1 \nNonclinical Safety \nMouse data follows. \n", 2: page_two},
            [
                OutlineEntry(
                    heading="1.3.1 Nonclinical Safety", page_start=1, page_end=2, level=4
                ),
                OutlineEntry(
                    heading="1.3.2 Clinical Experience", page_start=2, page_end=2, level=4
                ),
            ],
        )

        sections, source = find_sections(doc)

        assert source == "bookmarks"
        headings = [s.heading for s in sections]
        assert headings == [
            FRONT_MATTER_HEADING,
            "1.3.1 Nonclinical Safety",
            "1.3.2 Clinical Experience",
        ]

        nonclinical = sections[1]
        clinical = sections[2]
        # The two sections meet at one position inside page 2, and that position is
        # after the last sentence of the earlier section.
        assert (nonclinical.page_end, nonclinical.end_offset) == (
            clinical.page_start,
            clinical.start_offset,
        )
        assert section_text(doc, nonclinical).rstrip().endswith("is warranted.")
        assert section_text(doc, clinical).startswith("1.3.2")
        assert "warranted" not in section_text(doc, clinical)

    def test_sections_tile_the_whole_document(self) -> None:
        doc = _doc(
            {1: "Front \nA Heading \nbody one \n", 2: "more body \nB Heading \nbody two \n"},
            [
                OutlineEntry(heading="A Heading", page_start=1, page_end=2, level=1),
                OutlineEntry(heading="B Heading", page_start=2, page_end=2, level=1),
            ],
        )

        sections, _ = find_sections(doc)

        rejoined = "".join(section_text(doc, s) for s in sections)
        assert rejoined == doc.page_texts[1] + doc.page_texts[2]

    def test_heading_that_is_not_on_its_page_falls_back_to_the_page_start(self) -> None:
        doc = _doc(
            {1: "Title \n", 2: "text with no heading line at all \n"},
            [OutlineEntry(heading="Missing Heading", page_start=2, page_end=2, level=1)],
        )

        sections, source = find_sections(doc)

        assert source == "bookmarks"
        missing = next(s for s in sections if s.heading == "Missing Heading")
        assert (missing.page_start, missing.start_offset) == (2, 0)

    def test_bookmarks_out_of_order_are_put_in_document_order(self) -> None:
        doc = _doc(
            {1: "Second Heading \nb \nFirst Heading \na \n"},
            [
                OutlineEntry(heading="First Heading", page_start=1, page_end=1, level=1),
                OutlineEntry(heading="Second Heading", page_start=1, page_end=1, level=1),
            ],
        )

        sections, _ = find_sections(doc)

        # Position in the text, not position in the bookmark list, is document order.
        assert [s.heading for s in sections] == ["Second Heading", "First Heading"]

    def test_section_ids_are_unique(self) -> None:
        doc = _doc(
            {1: "Front \nA \nx \nB \ny \nC \nz \n"},
            [
                OutlineEntry(heading="A", page_start=1, page_end=1, level=1),
                OutlineEntry(heading="B", page_start=1, page_end=1, level=1),
                OutlineEntry(heading="C", page_start=1, page_end=1, level=1),
            ],
        )

        sections, _ = find_sections(doc)

        ids = [s.id for s in sections]
        assert len(ids) == len(set(ids))
        assert all(i.startswith("doc-001:") for i in ids)


class TestRule2WholeDocument:
    def test_short_document_without_bookmarks_is_one_section(self) -> None:
        doc = _doc(
            {page: f"Page {page} content, incl. services requested. " for page in range(1, 7)}
        )

        sections, source = find_sections(doc)

        assert source == "whole_document"
        assert len(sections) == 1
        assert sections[0].heading == WHOLE_DOCUMENT_HEADING
        assert (sections[0].page_start, sections[0].page_end) == (1, 6)
        # Every page, including the last, reaches the extraction step.
        assert set(section_page_texts(doc, sections[0])) == set(range(1, 7))
        assert "Page 6" in section_text(doc, sections[0])

    def test_a_document_at_the_budget_is_still_one_section(self) -> None:
        doc = _doc({1: "x" * (DEFAULT_TOKEN_BUDGET * 4)})

        _, source = find_sections(doc)

        assert source == "whole_document"


class TestRule3HeadingScan:
    def test_numbered_and_capitalised_headings(self) -> None:
        text = (
            "STUDY SYNOPSIS:\n"
            "This study is described below.\n"
            "1 INTRODUCTION\n"
            "Some background text.\n"
            "1.3.2 Clinical Experience\n"
            "An ongoing study is described here.\n"
        )
        hits = find_headings({1: text})

        assert [(h.heading, h.level) for h in hits] == [
            ("STUDY SYNOPSIS", 1),
            ("1 INTRODUCTION", 1),
            ("1.3.2 Clinical Experience", 3),
        ]

    def test_number_alone_on_a_line_takes_the_next_line_as_its_title(self) -> None:
        page = "1.4 \nRationale for Dose Selection \nThe majority of subjects \n"
        hits = find_headings({1: page})

        assert [(h.heading, h.level) for h in hits] == [("1.4 Rationale for Dose Selection", 2)]

    def test_prose_starting_with_a_number_is_not_a_heading(self) -> None:
        text = "1.5 mg/kg was administered to every subject in the first cohort of the study.\n"
        assert find_headings({1: text}) == []

    def test_running_header_repeated_on_every_page_is_not_a_heading(self) -> None:
        pages = {page: f"CONFIDENTIAL\nbody text for page {page}\n" for page in range(1, 11)}
        pages[4] = "CONFIDENTIAL\n2 STUDY DESIGN\nbody text for page 4\n"

        hits = find_headings(pages)

        assert [h.heading for h in hits] == ["2 STUDY DESIGN"]

    def test_long_document_without_bookmarks_is_split_by_its_headings(self) -> None:
        pages = {
            1: "COVER PAGE\n" + _long_page("intro"),
            2: "2 STUDY DESIGN\n" + _long_page("design"),
            3: _long_page("design continued"),
            4: "3 STUDY POPULATION\n" + _long_page("population"),
        }
        doc = _doc(pages)

        sections, source = find_sections(doc)

        assert source == "heading_scan"
        assert [s.heading for s in sections] == [
            "COVER PAGE",
            "2 STUDY DESIGN",
            "3 STUDY POPULATION",
        ]
        design = sections[1]
        assert (design.page_start, design.page_end) == (2, 3)
        assert "population" not in section_text(doc, design)

    def test_long_document_with_no_headings_falls_back_to_one_section(self) -> None:
        doc = _doc({page: _long_page("plain prose") for page in range(1, 5)})

        sections, source = find_sections(doc)

        assert source == "whole_document_fallback"
        assert len(sections) == 1
        assert sections[0].page_end == 4


class TestFindSectionsNode:
    def test_node_writes_sections_onto_every_document(self) -> None:
        protocol = _doc(
            {1: "Front \nA Heading \nbody \n"},
            [OutlineEntry(heading="A Heading", page_start=1, page_end=1, level=1)],
        )
        rfp = Document(
            id="doc-002",
            path="/tmp/rfp.pdf",
            kind="rfp",
            pages=2,
            page_texts={1: "Short RFP text. ", 2: "Services requested. "},
        )
        state = RunState(run_id="test-run", documents=[protocol, rfp])

        result = find_sections_node(state)

        assert result["errors"] == []
        documents = result["documents"]
        assert [d.section_source for d in documents] == ["bookmarks", "whole_document"]
        assert all(d.sections for d in documents)

    def test_node_handles_a_document_with_no_text(self) -> None:
        empty = Document(id="doc-003", path="/tmp/empty.pdf", kind="other", pages=0)
        state = RunState(run_id="test-run", documents=[empty])

        result = find_sections_node(state)

        assert result["errors"] == []
        sections = result["documents"][0].sections
        assert len(sections) == 1
        assert section_text(empty, sections[0]) == ""
