"""The number printed on each page, read by INGEST.

Change 7 of the 2026-10-07 review. These tests use page text alone, because that
is the source that applies to both sample documents — neither declares PDF page
labels — and because a made-up PDF would only test PyMuPDF.
"""

from __future__ import annotations

from rfp_intake.ingest.page_labels import read_page_labels


def _page(number: str | None, body: str = "Some body text about the study.") -> str:
    """One page's text, with or without a printed number in its footer."""
    return body if number is None else f"{body}\n\n{number}"


class TestTheNumberPrintedOnThePage:
    def test_a_running_header_saying_page_n_of_m(self) -> None:
        texts = {
            3: "Page 1 of 135\nProtocol synopsis",
            4: "Page 2 of 135\nStudy design",
        }
        assert read_page_labels(None, texts) == {3: "1", 4: "2"}

    def test_a_bare_number_in_the_footer(self) -> None:
        texts = {3: _page("1"), 4: _page("2"), 5: _page("3")}
        assert read_page_labels(None, texts) == {3: "1", 4: "2", 5: "3"}

    def test_a_decorated_number_in_the_footer(self) -> None:
        texts = {3: _page("- 1 -"), 4: _page("[2]"), 5: _page("Page 3")}
        assert read_page_labels(None, texts) == {3: "1", 4: "2", 5: "3"}

    def test_a_page_with_no_printed_number_is_simply_absent(self) -> None:
        """Nothing is guessed. The report cites that page by its position alone."""
        texts = {1: _page(None), 2: _page(None), 3: _page("1"), 4: _page("2")}
        assert read_page_labels(None, texts) == {3: "1", 4: "2"}

    def test_a_number_in_the_middle_of_a_page_is_not_a_page_number(self) -> None:
        body = "\n".join(["Line one", "Line two", "Line three", "4", "Line five",
                          "Line six", "Line seven"])
        assert read_page_labels(None, {1: body}) == {}

    def test_a_number_on_a_line_with_other_words_is_not_read(self) -> None:
        assert read_page_labels(None, {1: "Section 4 Study design"}) == {}


class TestAPageThatPrintsItsOwnPosition:
    """Nothing is recorded, because there is no second number to show.

    Added 2026-10-08, after the pipeline-rules reviewer found that the
    page-text path kept these where the PDF-label path drops them. `report.pdf`
    was right anyway — `format_page_ref` compares before printing a second
    number — but `extraction.json` dumps the whole `Provenance`, so of the 374
    entries carrying a printed number in run r-20261008-review2-run3, 29 printed
    their own page position (18 reading "2", 11 reading "1"), and INGEST logged
    those pages as printed numbers found.
    `samples/Example Protocol.pdf` is such a document: 97 pages found, all 97
    printing their own position.
    """

    def test_a_document_numbered_from_its_first_page_records_nothing(self) -> None:
        texts = {position: _page(str(position)) for position in range(1, 8)}
        assert read_page_labels(None, texts) == {}

    def test_only_the_pages_that_differ_are_kept(self) -> None:
        """The synthetic RFP's shape: two pages agree, the rest do not."""
        texts = {1: _page("1"), 2: _page("2"), 3: _page("10"), 4: _page("14")}
        assert read_page_labels(None, texts) == {3: "10", 4: "14"}


class TestNumbersThatDoNotRiseAreThrownAway:
    """One wrong printed number is worse than none — see the module docstring."""

    def test_numbers_that_go_backwards_keep_nothing(self) -> None:
        texts = {1: _page("12"), 2: _page("3"), 3: _page("40")}
        assert read_page_labels(None, texts) == {}

    def test_a_repeated_number_keeps_nothing(self) -> None:
        """Also fixes the order of the two checks in `read_page_labels`.

        Page 1 prints its own position here, so the redundancy filter added on
        2026-10-08 would drop it — and what is left, "page 2 prints 1, page 3
        prints 2", rises. Running the filter first would therefore keep a map
        read off a document that numbers two pages the same. The filter runs
        after the rise check for exactly this reason.
        """
        texts = {1: _page("1"), 2: _page("1"), 3: _page("2")}
        assert read_page_labels(None, texts) == {}

    def test_front_matter_numbered_in_roman_keeps_nothing(self) -> None:
        texts = {1: _page("i"), 2: _page("ii")}
        assert read_page_labels(None, texts) == {}

    def test_no_pages_at_all_is_an_empty_map(self) -> None:
        assert read_page_labels(None, {}) == {}


class TestAPageNumberedDifferentlyFromItsPosition:
    """The shape change 7 exists for, built by hand rather than read off a PDF.

    The amendment's two cover pages make every printed number two lower than its
    position in the file, which is the whole reason for change 7. This class
    simulates that offset; it opens neither sample document, and was called
    `TestTheTwoSampleDocuments` until the plan-conformance reviewer pointed out
    on 2026-10-08 that the name claimed coverage it does not give. The real
    documents are read in
    `tests/graph/test_ingest_run_dir.py::test_ingest_records_the_number_printed_on_each_page`
    (the synthetic RFP) and
    `tests/other_study/test_other_study_real_document.py::test_appendix_c_records_the_printed_page_as_well`
    (the 137-page amendment).
    """

    def test_the_amendments_offset_of_two(self) -> None:
        texts = {1: _page(None), 2: _page(None)}
        texts |= {position: _page(str(position - 2)) for position in range(5, 15)}
        labels = read_page_labels(None, texts)
        assert labels[11] == "9", "p.11 is printed 9 — the reference Oliver could not find"
        assert 1 not in labels and 2 not in labels, "the cover pages print nothing"
