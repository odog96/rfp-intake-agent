"""READ_SCHEDULE: count a protocol's Schedule of Events in code (Prompt C).

Angus Gray asked on 13 October for a simplified Schedule of Events in place of the
"Visit frequency by study period" text: the protocol's own visit columns, each
column's marks counted as one number, what each visit is for, and the subject
visits per stage. Every number here is counted by code from where each mark sits
on the page; no model is asked anything, and nothing leaves the process (rule 9:
pdfplumber, which INGEST already uses).

`Document.tables` cannot be used: it keeps each cell's text but not where the cell
is or what size its letters are, and both are needed. The column a mark belongs
to is the ruled cell its words sit in, and a footnote mark ("Screening¹",
"Days 8², 15") is told from the header's own words by its smaller font.

What is read, and what is not:
- a ruled table inside a kept section whose heading says one of
  `schedule_table.headings`; a table without ruling is not read;
- the header is every row above the first row with a cell saying only one of
  `schedule_table.marks`; a table that runs over several pages repeats it, and
  pages whose headers read the same are joined into one schedule;
- every non-empty cell below the header is one mark, whatever it says;
- footnotes are not read, except a header's own footnote for its timing when the
  header gives none (decision B of Prompt C, approved 2026-10-17).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pdfplumber
import structlog

from rfp_intake.domain.registry import ScheduleTable, get_registry, scope_has_phrase
from rfp_intake.domain.schemas import (
    Document,
    RunError,
    RunState,
    Schedule,
    ScheduleColumn,
    ScheduleRow,
    Section,
)
from rfp_intake.sections import section_page_texts

logger = structlog.get_logger()

# A word in letters this much smaller than the table's usual size is a footnote
# mark. Measured on Example protocol 2, Table 1: body 9.96 pt, marks 6.48 pt.
SUPERSCRIPT_RATIO = 0.8
# "3rd" is set as "3" and a small "rd"; the small part stays when it follows a digit.
ORDINAL_SUFFIXES = ("st", "nd", "rd", "th")
# How far a header's own footnote is looked for: the table's last page and this
# many after it. Table 1's footnotes are on the two pages after the table.
FOOTNOTE_PAGES_AFTER = 3
# Slack, in points, for a word or cell edge sitting on a ruled line.
EDGE = 1.0

_NTH = re.compile(r"\d+(?:st|nd|rd|th)")


@dataclass
class _Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    small: bool

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class _Cell:
    x0: float
    top: float
    x1: float
    bottom: float
    words: list[_Word] = field(default_factory=list)

    def holds(self, w: _Word) -> bool:
        return (self.x0 - EDGE <= w.cx <= self.x1 + EDGE
                and self.top - EDGE <= w.cy <= self.bottom + EDGE)

    def area(self) -> float:
        return (self.x1 - self.x0) * (self.bottom - self.top)


@dataclass
class _Mark:
    text: str
    column: int
    cy: float
    page: int


@dataclass
class _Grid:
    """One page's part of a schedule: its columns' headers and its marks."""

    page: int
    headers: list[tuple[str, str, list[str]]]  # (period, timing, footnote numbers)
    rows: list[tuple[str, list[_Mark]]]  # (label, marks), in page order
    unplaced: list[str]


def read_schedule_node(state: RunState) -> dict[str, Any]:
    """READ_SCHEDULE graph node. Pure Python — no model call; reads each PDF again.

    Runs after MARK_OTHER_STUDY, so a schedule in a section an analyst skips, or
    in text about another study, is never read. A document that cannot be read
    is recorded as an error and the run goes on; the report then falls back to
    the extracted "Visit frequency by study period" text.
    """
    config = get_registry().schedule_table
    if config is None:
        return {"schedules": []}
    schedules: list[Schedule] = []
    errors: list[RunError] = []
    for doc in state.documents:
        try:
            found = read_schedules(doc, config)
        except Exception as e:  # noqa: BLE001 - one bad document must not end the run
            logger.error("read_schedule_failed", run_id=state.run_id, doc_id=doc.id, error=str(e))
            errors.append(RunError(
                node="READ_SCHEDULE",
                task_id=doc.id,
                error=f"Could not read the schedule table in {doc.id}: {e}",
                kind="validation",
            ))
            continue
        logger.info(
            "schedules_read",
            run_id=state.run_id,
            doc_id=doc.id,
            schedules=len(found),
            counted=sum(s.counted for s in found),
        )
        schedules.extend(found)
    return {"schedules": schedules, "errors": errors}


def read_schedules(doc: Document, config: ScheduleTable) -> list[Schedule]:
    """Every Schedule of Events in `doc`, read from its PDF in page order.

    A section MARK_OTHER_STUDY took out whole is not read. A schedule section whose
    pages hold no table with marks gives a Schedule with `problem` set, so the
    report can say a schedule was found but not read.
    """
    gone = {p.section_id for p in doc.removed if p.verdict == "other_study"}
    wanted = [
        s for s in doc.sections
        if s.id not in gone and scope_has_phrase(s.heading, config.headings)
    ]
    if not wanted:
        return []

    schedules: list[Schedule] = []
    with pdfplumber.open(doc.path) as pdf:
        tops = _section_tops(doc, pdf)
        for section in wanted:
            grids: list[_Grid] = []
            for page_no in range(section.page_start, section.page_end + 1):
                if page_no > len(pdf.pages):
                    break
                page = pdf.pages[page_no - 1]
                for table in page.find_tables():
                    if _owner(doc.sections, tops, page_no, table.bbox[1]) is not section:
                        continue
                    grid = _read_grid(page, page_no, table, config)
                    if grid is not None:
                        grids.append(grid)
            counted = not scope_has_phrase(section.heading, config.not_counted_headings)
            if not grids:
                schedules.append(Schedule(
                    doc_id=doc.id,
                    heading=section.heading.strip(),
                    pages=[section.page_start],
                    printed_pages=[doc.page_labels.get(section.page_start)],
                    counted=counted,
                    problem=(
                        f"No ruled table with marks under this heading, "
                        f"pages {section.page_start}–{section.page_end}"
                    ),
                ))
                continue
            for run in _join(grids):
                schedules.append(_schedule(doc, section, run, counted, config))
    return schedules


def _section_tops(doc: Document, pdf: Any) -> dict[str, float]:  # Any: pdfplumber's PDF
    """How far down its first page each section's heading is printed, so a table
    on a page two sections share goes to the section it is printed under. A
    heading that cannot be found on its page counts as at the top."""
    tops: dict[str, float] = {}
    for s in doc.sections:
        words = re.findall(r"\w+", s.heading)[:6]
        if not words or s.page_start > len(pdf.pages):
            tops[s.id] = 0.0
            continue
        found = pdf.pages[s.page_start - 1].search(r"\W+".join(map(re.escape, words)))
        tops[s.id] = float(found[0]["top"]) if found else 0.0
    return tops


def _owner(
    sections: list[Section], tops: dict[str, float], page: int, top: float
) -> Section | None:
    """The section a table at (`page`, `top`) is printed under: the last kept one
    to start before it. FIND_SECTIONS' sections tile the document, but
    SET_ASIDE_SECTIONS has already taken some out, so a table printed under a
    set-aside heading on a schedule section's last page would be read as the
    schedule section's. None of the three sample protocols has that layout."""
    owner: Section | None = None
    for s in sections:
        if (s.page_start, tops.get(s.id, 0.0)) <= (page, top + EDGE):
            owner = s
    return owner


def _read_grid(page: Any, page_no: int, table: Any, config: ScheduleTable) -> _Grid | None:
    """One ruled table on one page, or None when it has no row of marks — a
    glossary or a list of changes printed in a schedule section is not one."""
    # Any: pdfplumber's Page and Table carry no types.
    x0, top, x1, bottom = table.bbox
    raw = page.extract_words(extra_attrs=["size"], keep_blank_chars=False)
    inside = [w for w in raw if x0 - EDGE <= (w["x0"] + w["x1"]) / 2 <= x1 + EDGE
              and top - EDGE <= (w["top"] + w["bottom"]) / 2 <= bottom + EDGE]
    if not inside:
        return None
    usual = Counter(round(float(w["size"]), 1) for w in inside).most_common(1)[0][0]
    words = [
        _Word(str(w["text"]), float(w["x0"]), float(w["x1"]), float(w["top"]),
              float(w["bottom"]), float(w["size"]) < usual * SUPERSCRIPT_RATIO)
        for w in inside
    ]
    cells = [_Cell(float(c[0]), float(c[1]), float(c[2]), float(c[3])) for c in table.cells]
    leftover: list[_Word] = []
    for w in words:
        holding = [c for c in cells if c.holds(w)]
        if holding:
            min(holding, key=_Cell.area).words.append(w)
        else:
            leftover.append(w)

    widest = max(table.rows, key=lambda r: sum(c is not None for c in r.cells))
    columns = [(float(c[0]), float(c[2])) for c in widest.cells if c is not None]
    if len(columns) < 2:
        return None

    body_top = _body_top(table, cells, config)
    if body_top is None:
        return None

    headers = [_header(c, columns, cells, body_top, config) for c in range(1, len(columns))]

    label_cells = sorted(
        (c for c in cells if c.top >= body_top - EDGE and _column(c, columns) == 0),
        key=lambda c: c.top,
    )
    bands: list[tuple[_Cell, str, list[_Mark]]] = [(c, _text(c.words), []) for c in label_cells]
    unplaced: list[str] = []
    for c in cells:
        if c.top < body_top - EDGE or c in label_cells:
            continue
        text = _text(c.words)
        if not text:
            continue
        kept = [w for w in c.words if not w.small] or c.words
        cx = sum(w.cx for w in kept) / len(kept)
        cy = sum(w.cy for w in kept) / len(kept)
        column = _index(cx, columns)
        if column is None or column == 0:
            unplaced.append(f"p.{page_no}: “{text}” is in no visit column")
            continue
        mark = _Mark(text, column, cy, page_no)
        band = next((b for b in bands if b[0].top - EDGE <= cy <= b[0].bottom + EDGE), None)
        if band is None:
            bands.append((_Cell(columns[0][0], cy, columns[0][1], cy), "", [mark]))
        else:
            band[2].append(mark)
    for w in leftover:
        if w.top >= body_top and not w.small:
            unplaced.append(f"p.{page_no}: “{w.text}” is in no cell of the table")

    bands.sort(key=lambda b: b[0].top)
    rows = _place_unlabelled(bands, page_no, headers, unplaced)
    return _Grid(page_no, headers, rows, unplaced)


def _body_top(
    table: Any, cells: list[_Cell], config: ScheduleTable  # Any: pdfplumber's Table
) -> float | None:
    """The top of the first row with a cell saying only a mark ("X", "X8")."""
    marks = {m.lower() for m in config.marks}
    for row in table.rows:
        row_top = float(row.bbox[1])
        for c in cells:
            if abs(c.top - row_top) > EDGE:
                continue
            said = " ".join(w.text for w in c.words if not w.small).strip().lower()
            if said in marks or (said and said.rstrip("0123456789,") in marks):
                return row_top
    return None


def _header(
    column: int,
    columns: list[tuple[float, float]],
    cells: list[_Cell],
    body_top: float,
    config: ScheduleTable,
) -> tuple[str, str, list[str]]:
    """A column's period and timing, from every header cell over its middle, top
    to bottom, and the footnote numbers printed on them."""
    middle = sum(columns[column]) / 2
    stack = sorted(
        (c for c in cells if c.bottom <= body_top + EDGE and c.x0 - EDGE <= middle <= c.x1 + EDGE),
        key=lambda c: c.top,
    )
    lines: list[str] = []
    for c in stack:
        if not (t := _text(c.words)):
            continue
        if lines and lines[-1].endswith("/"):
            lines[-1] += t
        else:
            lines.append(t)
    notes = [w.text.strip(",") for c in stack for w in c.words
             if w.small and w.text.strip(",").isdigit()]
    starts = {s.lower() for s in config.timing_starts}
    split = next(
        (i for i, line in enumerate(lines)
         if (first := re.findall(r"[a-z]+", line.lower())[:1]) and first[0] in starts),
        len(lines),
    )
    return " ".join(lines[:split]), " ".join(lines[split:]), notes


def _place_unlabelled(
    bands: list[tuple[_Cell, str, list[_Mark]]],
    page_no: int,
    headers: list[tuple[str, str, list[str]]],
    unplaced: list[str],
) -> list[tuple[str, list[_Mark]]]:
    """Marks in a band with no label go to the labelled row beside it that has no
    marks of its own — a label wrapped onto the line above or below its marks. When
    neither neighbour, or both, could take them, they are listed as not placed;
    they stay in their column's count either way."""
    taken: set[int] = set()
    for i, (_cell, label, marks) in enumerate(bands):
        if label or not marks:
            continue
        free = [j for j in (i - 1, i + 1)
                if 0 <= j < len(bands) and bands[j][1] and not bands[j][2] and j not in taken]
        if len(free) == 1:
            bands[free[0]][2].extend(marks)
            taken.add(free[0])
            marks.clear()
        else:
            for m in marks:
                period, timing, _ = headers[m.column - 1]
                unplaced.append(
                    f"p.{page_no}: “{m.text}” under {period} {timing}".rstrip()
                    + " has no row label; counted in its column"
                )
    rows = [(label, marks) for _cell, label, marks in bands if label]
    rows += [("", marks) for _cell, label, marks in bands if not label and marks]
    return rows


def _join(grids: list[_Grid]) -> list[list[_Grid]]:
    """Consecutive pages whose headers read the same are one schedule."""
    runs: list[list[_Grid]] = []
    for g in grids:
        last = runs[-1][-1] if runs else None
        if last is not None and g.page == last.page + 1 and [
            h[:2] for h in g.headers
        ] == [h[:2] for h in last.headers]:
            runs[-1].append(g)
        else:
            runs.append([g])
    return runs


def _schedule(
    doc: Document, section: Section, run: list[_Grid], counted: bool, config: ScheduleTable
) -> Schedule:
    first = run[0]
    pages = [g.page for g in run]
    non_assessment = {c.id for c in config.considerations if not c.assessment}
    rows: list[ScheduleRow] = []
    columns = [
        ScheduleColumn(period=period, timing=timing, pages=pages)
        for period, timing, _notes in first.headers
    ]
    for g in run:
        for label, marks in g.rows:
            matched = [c.id for c in config.considerations if scope_has_phrase(label, c.wording)]
            if label:
                rows.append(ScheduleRow(
                    label=label,
                    page=g.page,
                    columns=sorted({m.column for m in marks}),
                    considerations=matched,
                ))
            for m in marks:
                col = columns[m.column - 1]
                col.marks_all += 1
                if not non_assessment.intersection(matched):
                    col.marks_assessments += 1
    for i, col in enumerate(columns, start=1):
        col.considerations = [
            c.id for c in config.considerations
            if any(i in r.columns and c.id in r.considerations for r in rows)
        ]
        if not col.timing and first.headers[i - 1][2]:
            note = _footnote(doc, section, first.headers[i - 1][2], pages[-1])
            if note is not None:
                col.timing_footnote, col.timing_footnote_page = note
        _visits(col, config)
        col.stage = next(
            (s.label for s in config.stages
             if any(_says(col.period, p) is not None for p in s.periods)),
            None,
        )
    return Schedule(
        doc_id=doc.id,
        heading=section.heading.strip(),
        pages=pages,
        printed_pages=[doc.page_labels.get(p) for p in pages],
        counted=counted,
        columns=columns,
        rows=rows,
        unplaced=[u for g in run for u in g.unplaced],
    )


def _visits(col: ScheduleColumn, config: ScheduleTable) -> None:
    """Visits in one column, from its header: a repeating period prints its
    `says`; otherwise one visit per item of the timing ("Days 8, 15, 22" is 3)."""
    for rep in config.repeating:
        nth = _says(f"{col.period} {col.timing}", rep.wording)
        if nth is not None:
            col.visits = None
            col.visits_says = rep.says.replace("{nth}", nth)
            return
    timing = re.sub(r"\([^)]*\)", " ", col.timing)
    pattern = "|".join(
        re.escape(s) if not s.isalpha() else rf"\b{re.escape(s)}\b"
        for s in config.visit_separators
    )
    items = [i for i in re.split(pattern, timing, flags=re.IGNORECASE) if i.strip()]
    col.visits = max(len(items), 1)


def _says(text: str, wording: str) -> str | None:
    """Whether `text` says `wording`, as words in order ignoring case and
    punctuation; `{nth}` matches an ordinal. Returns the ordinal matched, "" when
    the wording has none, or None when the text does not say it."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    want = re.findall(r"\{nth\}|[a-z0-9]+", wording.lower())
    for i in range(len(words) - len(want) + 1):
        nth = ""
        for w, x in zip(want, words[i:i + len(want)], strict=True):
            if w == "{nth}" and _NTH.fullmatch(x):
                nth = x
            elif w != x:
                break
        else:
            return nth
    return None


def _footnote(
    doc: Document, section: Section, numbers: list[str], last_page: int
) -> tuple[str, int] | None:
    """The first of a header's own footnotes printed after the table, with its
    page: the line starting "3." and any lines carrying on from it. Only the
    schedule section's own kept text is read (`section_page_texts`, which cuts
    out what MARK_OTHER_STUDY removed), so a "3." heading of the next section
    is never taken for a footnote."""
    texts = section_page_texts(doc, section)
    for page in range(last_page, min(last_page + FOOTNOTE_PAGES_AFTER, section.page_end) + 1):
        text = texts.get(page, "")
        for n in numbers:
            found = re.search(rf"(?m)^\s*{re.escape(n)}\.\s+(.+(?:\n(?!\s*\d+\.\s).+)*)", text)
            if found:
                return re.sub(r"\s+", " ", found.group(1)).strip(), page
    return None


def _text(words: list[_Word]) -> str:
    """A cell's words, line by line, without footnote marks. A word set against
    the one before it, or against a footnote mark after it ("8²,"), is joined
    without a space; a line ending in "/" runs on into the next ("EOT/" "ETD")."""
    lines: list[list[_Word]] = []
    for w in sorted(words, key=lambda w: w.top):
        # A raised footnote mark sits higher than its line's words but overlaps them.
        if lines and w.top < max(x.bottom for x in lines[-1]) - EDGE:
            lines[-1].append(w)
        else:
            lines.append([w])
    out = ""
    for line in lines:
        text = ""
        last: _Word | None = None
        for w in sorted(line, key=lambda w: w.x0):
            touching = last is not None and abs(w.x0 - last.x1) < EDGE / 2
            if w.small:
                if (w.text.lower() in ORDINAL_SUFFIXES and touching
                        and text[-1:].isdigit()):
                    text += w.text
                last = w
                continue
            text += w.text if touching or not text else " " + w.text
            last = w
        if text:
            out += text if not out or out.endswith("/") else " " + text
    return out.strip()


def _column(c: _Cell, columns: list[tuple[float, float]]) -> int | None:
    return _index((c.x0 + c.x1) / 2, columns)


def _index(x: float, columns: list[tuple[float, float]]) -> int | None:
    return next((i for i, (a, b) in enumerate(columns) if a - EDGE <= x <= b + EDGE), None)
