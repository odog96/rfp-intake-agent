"""The page number printed on each page, where it differs from the PDF position.

Change 7 of the 2026-10-07 review. `report.pdf` cites pages by position in the
PDF file (`ingest/models.py`'s `PageText.page_num`, 1-based). The protocol
amendment's own printed numbers are two lower than its PDF positions, because
the file begins with two cover pages, so "Amendment p.11" sent Oliver Zarate to
the wrong page during the 7 October call (transcript 00:28-00:29).

Two sources, in this order:

1. **The PDF's own page labels.** A well-made PDF carries a label per page — the
   numbers a reader sees in a viewer's page box, "iv" and "12" rather than 4 and
   16. Neither of the two sample documents has them, but a sponsor's PDF may.
2. **The number printed on the page**, read from the page's own text: a
   "Page 12 of 135" running header or footer first, then a line that is nothing
   but a page number at the top or the bottom of the page.

Nothing is guessed. A page with no number found is simply absent from the map,
and `report.pdf` then cites that page by its PDF position alone, as it did
before this change. **A page that prints its own PDF position is absent too**,
from either source: there is no second number to show, and a map holding one
would put a redundant `printed_page` into `extraction.json` and over-report the
count INGEST logs.

**The whole document's numbers are thrown away unless they rise.** Printed page
numbers run upwards through a document; a set that does not is a set where
something other than a page number was read — a table of contents entry, a
section number, a figure count. One wrong printed number is worse than none at
all, because a reader who turns to it and finds the wrong text has no way to
tell which of the two numbers was wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

logger = structlog.get_logger()

# "Page 12 of 135", "Page 12/135", "page 12 of 135" — a running header or footer,
# the most reliable thing on a page because it says what the number is.
_OF_TOTAL = re.compile(r"\bpage\s+(\d{1,4})\s*(?:of|/)\s*\d{1,4}\b", re.IGNORECASE)

# A line that is only a page number: "12", "- 12 -", "[12]", "Page 12", "12.".
# Anchored at both ends, so a line with any other content on it is not read.
_ALONE = re.compile(
    r"^[\[\(\-–—\s]*(?:page\s+)?(\d{1,4})[\]\)\-–—.\s]*$", re.IGNORECASE
)

# How many of a page's own lines, from each end, may hold the printed number. A
# page's running header and footer sit in the first or last few lines of the text
# a parser returns; a bare number further in is part of the body.
_EDGE_LINES = 3


def read_page_labels(path: Path | str | None, page_texts: dict[int, str]) -> dict[int, str]:
    """PDF page number -> the page number printed on that page.

    Only pages where a printed number was found are in the result, and only when
    the document's numbers rise from first page to last. See the module docstring.
    """
    labels = _from_pdf_page_labels(path)
    source = "pdf_labels"
    if not labels:
        labels = {
            page: found
            for page, text in sorted(page_texts.items())
            if (found := _printed_on_page(text)) is not None
        }
        source = "page_text"

    if not labels:
        return {}
    if not _rises(labels):
        logger.warning(
            "page_labels_discarded",
            source=source,
            reason="the numbers found do not rise through the document",
            pages=len(labels),
        )
        return {}
    # A page that prints its own PDF position is dropped. `report.pdf` would be
    # right either way, because `format_page_ref` compares the two numbers before
    # printing a second one — but `extraction.json` dumps the whole `Provenance`,
    # so of the 431 provenance entries in run r-20261008-review2-run3, the 374
    # that carry a printed number include 29 whose printed number is its own page
    # position (18 reading "2" and 11 reading "1", from the synthetic RFP's first
    # two pages), against a schema comment promising the field is None whenever
    # the two numbers agree; and INGEST's log line counts those pages as printed
    # numbers found. On `samples/Example Protocol.pdf` all 97 pages found were
    # such pages.
    #
    # `_from_pdf_page_labels` tests a declared label the same way, but it tests
    # it while reading, before `_rises` sees anything, so the two paths are not
    # yet symmetrical: the misread this ordering protects the text path from
    # would still slip through a PDF that *declares* labels 1, 1, 2. Neither
    # sample document declares any, so that is untested ground rather than a
    # known fault; it cannot be closed by deleting the test there, because
    # PyMuPDF returns the page position as a string for a file with no label
    # tree and that comparison is how "no labels at all" is detected. Reported
    # to Oliver on 2026-10-08 rather than changed.
    #
    # **After the rise check, not inside it.** The check needs every number that
    # was read: a document whose pages 1 and 2 both print "1" is a misread, and
    # dropping page 1 here first would leave "2 prints 1, 3 prints 2", which
    # rises, and the misread map would be kept. See
    # `tests/ingest/test_page_labels.py::test_a_repeated_number_keeps_nothing`.
    return {page: printed for page, printed in labels.items() if printed != str(page)}


def _from_pdf_page_labels(path: Path | str | None) -> dict[int, str]:
    """The labels the PDF itself declares, if it declares any.

    PyMuPDF returns the PDF page position as a string when a file has no label
    tree, so a label equal to its own page position tells us nothing and is
    dropped — leaving the map empty, which sends `read_page_labels` to the text.
    """
    if path is None:
        return {}
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - pymupdf is a hard dependency of INGEST
        return {}
    try:
        with pymupdf.open(str(path)) as pdf:  # type: ignore[no-untyped-call]
            labels: dict[int, str] = {}
            for position, page in enumerate(pdf, start=1):
                label = (page.get_label() or "").strip()
                if label and label != str(position):
                    labels[position] = label
    except Exception as e:  # a damaged or non-PDF file is INGEST's problem, not ours
        logger.warning("page_labels_unreadable", path=str(path), error=str(e))
        return {}
    return labels


def _printed_on_page(text: str) -> str | None:
    """The page number printed on one page, from that page's own text."""
    match = _OF_TOTAL.search(text)
    if match:
        return match.group(1)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines[:_EDGE_LINES] + lines[-_EDGE_LINES:]:
        alone = _ALONE.match(line)
        if alone:
            return alone.group(1)
    return None


def _rises(labels: dict[int, str]) -> bool:
    """Do the numbers found rise from the document's first labelled page to its last?

    Strictly increasing, and read in PDF page order. A document whose front
    matter is numbered "i, ii, iii" fails this and keeps no labels at all, which
    is the safe answer: a roman numeral is not what a reader asked us to print.
    """
    numbers = [labels[page] for page in sorted(labels)]
    if not all(n.isdigit() for n in numbers):
        return False
    values = [int(n) for n in numbers]
    return all(earlier < later for earlier, later in zip(values, values[1:], strict=False))


__all__ = ["read_page_labels"]
