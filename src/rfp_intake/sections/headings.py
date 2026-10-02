"""Heading detection from page text alone — rule 3 of FIND_SECTIONS.

Reached only when a PDF carries no bookmarks and its whole text is too long for
one extraction call, so there is nothing to split it by except the text itself.
This is a best guess and will be wrong on some documents; FIND_SECTIONS logs how
many headings it found so a bad guess is visible in the run log.

`docs/PLAN_2026-10-02.md` stage 1 suggests also using PyMuPDF's font size and
weight (`page.get_text("dict")`) to spot a heading. That is not done here,
because the same stage says FIND_SECTIONS reads only what INGEST already
produced, and `Document.page_texts` carries no font information. Using font size
would mean re-opening the PDF in this module, which would put a second PDF reader
outside `ingest/parsers/`. Text-only detection is therefore the whole of rule 3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A heading is a short line. Anything longer is prose that happens to start with
# a number, such as "1.5 mg/kg was administered to each subject in the ...".
MAX_HEADING_CHARS = 120
MAX_CAPS_HEADING_CHARS = 80
MIN_HEADING_LETTERS = 3

# "1.3.2 Clinical Experience", "4 STUDY DESIGN", "1.3.2" alone on its line.
_NUMBERED = re.compile(r"^(\d{1,2}(?:\.\d{1,3}){0,4})\.?[ \t]*(.*)$")

# A running header or footer repeated on at least this share of the pages is
# furniture, not a heading. The protocol in `samples/` puts "CONFIDENTIAL" and a
# page number on every page, and each would otherwise start a new section.
REPEATED_LINE_SHARE = 0.5


@dataclass(frozen=True)
class HeadingHit:
    """One heading found in one page's text.

    `offset` is the character offset of the heading's first character within that
    page's entry in `Document.page_texts`, which is what `Section` boundaries are
    measured in.
    """

    page: int
    offset: int
    heading: str
    level: int


def find_headings(page_texts: dict[int, str]) -> list[HeadingHit]:
    """Every heading found in a document's pages, in document order."""
    furniture = _repeated_lines(page_texts)
    hits: list[HeadingHit] = []
    for page in sorted(page_texts):
        hits.extend(_find_in_page(page, page_texts[page], furniture))
    return hits


def _find_in_page(page: int, text: str, furniture: frozenset[str]) -> list[HeadingHit]:
    hits: list[HeadingHit] = []
    lines = _lines_with_offsets(text)
    for index, (offset, line) in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped in furniture:
            continue

        numbered = _as_numbered_heading(stripped, lines, index)
        if numbered is not None:
            heading, level = numbered
            hits.append(HeadingHit(page=page, offset=offset, heading=heading, level=level))
            continue

        if _is_capitalised_heading(stripped):
            hits.append(
                HeadingHit(page=page, offset=offset, heading=stripped.rstrip(":"), level=1)
            )
    return hits


def _as_numbered_heading(
    stripped: str,
    lines: list[tuple[int, str]],
    index: int,
) -> tuple[str, int] | None:
    """A dotted-number heading and its level, or None.

    The number's depth is the level: "1" is level 1, "1.3.2" is level 3. When the
    number is alone on its line the title is taken from the next non-empty line,
    which is the shape PyMuPDF returns for the sample protocol ("1.3.2 \\nClinical
    Experience").
    """
    if len(stripped) > MAX_HEADING_CHARS:
        return None
    match = _NUMBERED.match(stripped)
    if match is None:
        return None

    number, remainder = match.group(1), match.group(2).strip()
    level = number.count(".") + 1

    if not remainder:
        remainder = _next_nonempty(lines, index)
        if not remainder or len(remainder) > MAX_HEADING_CHARS:
            return None

    if not _looks_like_a_title(remainder):
        return None
    return f"{number} {remainder}", level


def _next_nonempty(lines: list[tuple[int, str]], index: int) -> str:
    for _, line in lines[index + 1 :]:
        if line.strip():
            return line.strip()
    return ""


def _looks_like_a_title(text: str) -> bool:
    """A title names something. A sentence ends in a full stop and is longer."""
    if len(_letters(text)) < MIN_HEADING_LETTERS:
        return False
    return not text.endswith((".", ",", ";"))


def _is_capitalised_heading(stripped: str) -> bool:
    """A line in capitals, such as "STUDY SYNOPSIS:" or "SCHEDULE OF EVENTS"."""
    if len(stripped) > MAX_CAPS_HEADING_CHARS:
        return False
    letters = _letters(stripped)
    if len(letters) < MIN_HEADING_LETTERS:
        return False
    if any(ch.islower() for ch in letters):
        return False
    return stripped.endswith(":") or stripped == stripped.upper()


def _letters(text: str) -> str:
    return "".join(ch for ch in text if ch.isalpha())


def _lines_with_offsets(text: str) -> list[tuple[int, str]]:
    """Each line of a page with the character offset it starts at."""
    out: list[tuple[int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        out.append((offset, line))
        offset += len(line)
    return out


def _repeated_lines(page_texts: dict[int, str]) -> frozenset[str]:
    """Lines that appear on at least half the pages: running headers and footers."""
    if len(page_texts) < 3:
        return frozenset()
    counts: dict[str, int] = {}
    for text in page_texts.values():
        for line in {line.strip() for line in text.splitlines() if line.strip()}:
            counts[line] = counts.get(line, 0) + 1
    threshold = max(2, int(len(page_texts) * REPEATED_LINE_SHARE))
    return frozenset(line for line, count in counts.items() if count >= threshold)
