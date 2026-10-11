"""The ten checks agreed with Angus Gray on 7 October 2026, measured on one run.

    python scripts/check_review_2026_10_07.py r-20261007-140411
    python scripts/check_review_2026_10_07.py r-2026xxxx-xxxxxx --baseline r-20261007-140411

Checks 1 to 9 of the review are measured here. Each prints PASS, FAIL, or — where
only a person can settle it — what to look at. Check 10 (pytest, ruff, mypy and
the two commit-gate reviewers) is not a property of a run and is not measured.

The measurement is taken from `report.pdf` itself, not from the model that drew
it. report.pdf is what Oliver and Angus read, and a check that asked
render/report_model.py what render/report_model.py decided would agree with it
by construction. The tables are reconstructed from the PDF's own row rules and
column positions; `extraction.json` is read only for the two things the page
cannot show (the field registry's ids behind a printed label, and the run's
errors), and the two source PDFs are read to re-derive printed page numbers
independently of src/rfp_intake/ingest/.

The printed labels below are literals on purpose, and are not a breach of
non-negotiable rule 4. Rule 4 forbids keying *pipeline* behaviour to a field
name; nothing here feeds the pipeline. Three of these labels name variables that
changes 4 and 5 delete, so they cannot be read from today's config/fields.yaml —
the comparison set is the 7 October report, which is history.

Reads only. Writes nothing, and makes no model calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

BASELINE_RUN = "r-20261007-140411"

# One word as pymupdf reports it: x0, y0, x1, y1, text, and three block/line/word
# numbers this script does not use.
Word = tuple[float, float, float, float, str, int, int, int]

# Section headings, drawn at 12.5pt bold. Matched on a prefix because two of
# them carry an em-dash and a subtitle.
SECTION_HEADINGS = (
    "All variables",
    "Disagreements between the documents",
    "Flagged for review",
    "Schedules",
    "Appendix A",
    "Appendix B",
    "Appendix C",
    "Words used in this report",
)

# Labels the 7 October report printed, including the three that changes 4 and 5
# remove from the registry.
L_DESIGN = "General study design / structure"
L_FIH = "First-in-human"
L_POPULATION = "Patient / subject population"
L_SUBJECTS = "Total number of patients / subjects"
L_UNBLINDED = "Unblinded staff stated as required"
L_PHASE = "Study phase"
L_INTERIM_PLANNED = "Interim analyses planned"
L_INTERIM_COUNT = "Number of interim analyses"
L_INTERIM_TIMING = "Timing of each interim analysis"
L_MONITORING_VISITS = "Number of monitoring visits by type"
L_ONSITE_REMOTE_MIX = "On-site vs remote monitoring mix"
L_DURATION = "Overall study duration"
L_PLACEBO_MATCHES = "Placebo matches the study drug"

LEGACY_LABELS = (
    L_DESIGN,
    L_FIH,
    L_POPULATION,
    L_SUBJECTS,
    L_UNBLINDED,
    L_PHASE,
    L_INTERIM_PLANNED,
    L_INTERIM_COUNT,
    L_INTERIM_TIMING,
    L_MONITORING_VISITS,
    L_ONSITE_REMOTE_MIX,
    L_DURATION,
    L_PLACEBO_MATCHES,
)

# The five rows change 5 asks for, in the order the report must print them.
FIVE_VISIT_TYPES = (
    "Site selection visits",
    "Site initiation visits",
    "Interim monitoring visits",
    "Close-out visits",
    "Pharmacy monitoring visits",
)
# What the synthetic RFP states for the three the check names, as digit strings.
# The RFP's own words, from pages 3 and 4; no figure here is calculated.
RFP_VISIT_FIGURES = {
    "Interim monitoring visits": ("10", "750", "16"),
    "Close-out visits": ("1", "75", "12"),
    "Pharmacy monitoring visits": ("4", "300", "4"),
}

OPERATIONAL_GROUP = "Operational Metrics and Volumes"

# Removed by change 4 and change 5; added by change 5. Check 9's arithmetic.
REMOVED_BY_CHANGES = (L_INTERIM_PLANNED, L_MONITORING_VISITS, L_ONSITE_REMOTE_MIX)


# ---------------------------------------------------------------- PDF reading


@dataclass
class Row:
    """One row of one table in report.pdf, keyed by the printed column headings."""

    section: str
    group: str
    page: int
    columns: tuple[str, ...]
    cells: dict[str, str]
    # Filled in for the All variables and Flagged tables: the variable this row
    # is about, carried down from the row above when a scoped field prints its
    # label once and its scopes underneath.
    label: str = ""
    scope: str = ""
    # True for a field group's shaded heading row, which spans every column.
    is_heading: bool = False
    text: str = ""

    def cell(self, name: str) -> str:
        return self.cells.get(name, "")


@dataclass
class Report:
    path: Path
    pages: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.pages)

    def table(self, section: str) -> list[Row]:
        return [r for r in self.rows if r.section == section and not r.is_heading]

    def labelled(self, section: str, label: str) -> list[Row]:
        return [r for r in self.table(section) if r.label == label]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _headings_on_page(page) -> list[tuple[float, str]]:  # type: ignore[no-untyped-def]
    """(y, heading) for every 12.5pt bold section heading on the page."""
    found: list[tuple[float, str]] = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                if span["size"] < 12.0 or "Bold" not in span["font"]:
                    continue
                text = _norm(span["text"])
                for heading in SECTION_HEADINGS:
                    if text.startswith(heading):
                        found.append((span["bbox"][1], heading))
    return sorted(found)


def _row_rules(page) -> list[float]:  # type: ignore[no-untyped-def]
    """The y of every full-width horizontal rule — the tables' own row boundaries."""
    ys: set[float] = set()
    width = page.rect.width
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if abs(rect.y1 - rect.y0) > 1.0:
            continue
        if rect.x1 - rect.x0 < width * 0.8:
            continue
        ys.add(round(rect.y0, 1))
    return sorted(ys)


# Every table report.pdf draws, by its printed column headings. A filled band is
# a table header only if its words spell one of these; the shaded band a field
# group's heading row also carries is then correctly read as a row of the table
# above it rather than as the start of a new one.
HEADER_SIGNATURES = (
    ("Variable", "Value", "Status", "Confidence", "Where"),
    ("#", "Variable", "What the documents say", "Assessment"),
    ("Variable", "Value", "Confidence", "Why it is flagged", "Where"),
    ("Applies to", "Value", "Status", "Confidence", "Where"),
    ("Term", "What it means"),
)


def _header_bands(page) -> list[tuple[float, float]]:  # type: ignore[no-untyped-def]
    """The filled bands that could be a table's repeated header row."""
    bands: list[tuple[float, float]] = []
    width = page.rect.width
    for drawing in page.get_drawings():
        if drawing["type"] != "f":
            continue
        rect = drawing["rect"]
        if rect.x1 - rect.x0 < width * 0.8 or not 6 < rect.y1 - rect.y0 < 40:
            continue
        bands.append((round(rect.y0, 1), round(rect.y1, 1)))
    return sorted(bands)


def _columns(words: list[Word], top: float, bottom: float) -> list[tuple[float, str]]:
    """(x, heading) for the header row in this band, or [] if it is not a header."""
    inside = sorted(
        ((w[0], w[4]) for w in words if top - 1 <= w[1] < bottom), key=lambda t: t[0]
    )
    spelled = [text for _x, text in inside]
    for signature in HEADER_SIGNATURES:
        wanted = " ".join(signature).split()
        if spelled != wanted:
            continue
        columns: list[tuple[float, str]] = []
        at = 0
        for name in signature:
            columns.append((inside[at][0], name))
            at += len(name.split())
        return columns
    return []


def parse_report(path: Path) -> Report:
    """Reconstruct report.pdf's tables from its row rules and column positions."""
    import pymupdf

    report = Report(path=path)
    section = ""
    group = ""
    with pymupdf.open(path) as pdf:  # type: ignore[no-untyped-call]
        for page_no, page in enumerate(pdf, start=1):
            report.pages.append(page.get_text())
            words = [w for w in page.get_text("words") if w[1] < page.rect.height - 60]
            headings = _headings_on_page(page)
            rules = _row_rules(page)
            headers = _header_bands(page)

            tables = [
                (top, bottom, columns)
                for top, bottom in headers
                if (columns := _columns(words, top, bottom))
            ]
            # Every other shaded band is a field group's heading row, which runs
            # across all five columns and so cannot be read column by column.
            group_tops = {top for top, _b in headers} - {top for top, _b, _c in tables}
            pending = list(headings)
            for i, (htop, hbottom, columns) in enumerate(tables):
                while pending and pending[0][0] < htop:
                    section = pending.pop(0)[1]
                    group = ""
                names = tuple(name for _x, name in columns)
                stop = tables[i + 1][0] if i + 1 < len(tables) else page.rect.height
                boundaries = [y for y in rules if y >= hbottom - 1 and y <= stop + 1]
                prev = hbottom
                for boundary in boundaries:
                    if boundary - prev < 2:
                        prev = boundary
                        continue
                    is_heading = any(abs(prev - top) < 2 for top in group_tops)
                    cells = _cells(words, prev, boundary, columns)
                    text = _norm(
                        " ".join(
                            w[4]
                            for w in sorted(
                                (w for w in words if prev - 1 <= w[1] < boundary - 1),
                                key=lambda w: (round(w[1]), w[0]),
                            )
                        )
                    )
                    prev = boundary
                    if not text:
                        continue
                    if is_heading:
                        group = text.split(" — ")[0].strip()
                        cells = dict.fromkeys(names, "")
                    report.rows.append(
                        Row(
                            section=section,
                            group=group,
                            page=page_no,
                            columns=names,
                            cells=cells,
                            is_heading=is_heading,
                            text=text,
                        )
                    )
            # A heading printed below the last table on its page — or on a page
            # that holds no table header at all — belongs to the table that
            # starts on the next page, so it is carried over rather than
            # dropped. On run r-20261008-review2-run3 the "Disagreements
            # between the documents" heading sat at the foot of page 4 with its
            # table beginning on page 5; without this, every row of that table
            # was counted as part of All variables and checks 4 and 8 both
            # reported the three real conflicts missing from a report that in
            # fact showed all three.
            for _y, heading in pending:
                section = heading
                group = ""
    _attach_labels(report)
    return report


def _cells(
    words: list[Word], top: float, bottom: float, columns: list[tuple[float, str]]
) -> dict[str, str]:
    """Every word between two row rules, sorted into the column it starts in."""
    buckets: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    for w in words:
        if not (top - 1 <= w[1] < bottom - 1):
            continue
        index = 0
        for j, (x, _name) in enumerate(columns):
            if w[0] >= x - 3:
                index = j
        buckets[columns[index][1]].append((w[1], w[0], w[4]))
    return {
        name: _norm(" ".join(text for _y, _x, text in sorted(buckets[name])))
        for _x, name in columns
    }


def _attach_labels(report: Report) -> None:
    """Split each variable cell into label and scope, carrying a label downwards.

    A scoped field prints its label on its first row only and the scopes
    underneath it, so the label of a continuation row is the one above it.
    """
    known = set(LEGACY_LABELS) | set(FIVE_VISIT_TYPES) | _registry_labels()
    current = ""
    for row in report.rows:
        if row.is_heading or not row.columns:
            current = ""
            continue
        if "Variable" not in row.columns:
            continue
        cell = row.cell("Variable")
        matches = [lab for lab in known if cell == lab or cell.startswith(lab + " ")]
        if matches:
            label = max(matches, key=len)
            current = label
            row.label = label
            row.scope = _norm(cell[len(label) :].removeprefix(" BUDGET"))
        else:
            row.label = current
            row.scope = cell


def _registry_labels() -> set[str]:
    try:
        from rfp_intake.domain.registry import get_registry

        return {f.label for f in get_registry().fields}
    except Exception:  # pragma: no cover - a broken registry is check 10's problem
        return set()


# ---------------------------------------------------------------- the results


@dataclass
class Result:
    number: int
    title: str
    verdict: str  # PASS | FAIL | LOOK | BASELINE
    reason: str
    notes: list[str] = field(default_factory=list)


def _labels_in_disagreements(report: Report) -> list[str]:
    return [r.cell("Variable") for r in report.table("Disagreements between the documents")]


def _in_disagreements(report: Report, label: str) -> bool:
    return any(printed.startswith(label) for printed in _labels_in_disagreements(report))


def _disagreement_number(report: Report, label: str) -> str | None:
    for row in report.table("Disagreements between the documents"):
        if row.cell("Variable").startswith(label):
            return row.cell("#")
    return None


def check_1(report: Report) -> Result:
    title = "A reconcilable verdict is not a disagreement"
    wrong_table = [
        label for label in (L_DESIGN, L_FIH, L_POPULATION) if _in_disagreements(report, label)
    ]
    wrong_status = [
        label
        for label in (L_DESIGN, L_FIH, L_POPULATION)
        for row in report.labelled("All variables", label)
        if row.cell("Status") == "Disagreement" or "The documents disagree" in row.cell("Value")
    ]
    flagged = [r.label for r in report.table("Flagged for review")]
    subjects_flagged = L_SUBJECTS in flagged
    problems = []
    if wrong_table:
        problems.append(f"still in the Disagreements table: {', '.join(sorted(set(wrong_table)))}")
    if wrong_status:
        problems.append(f"still marked Disagreement: {', '.join(sorted(set(wrong_status)))}")
    if not subjects_flagged:
        problems.append(f"{L_SUBJECTS} is not in Flagged for review")
    if problems:
        return Result(1, title, "FAIL", "; ".join(problems))
    return Result(
        1,
        title,
        "PASS",
        "the three reconcilable variables are out of the Disagreements table and "
        f"{L_SUBJECTS} is in Flagged for review",
    )


def check_2(report: Report) -> Result:
    title = '"Not specified" is not a competing answer'
    wrong = [label for label in (L_FIH, L_UNBLINDED) if _in_disagreements(report, label)]
    if wrong:
        return Result(2, title, "FAIL", f"still in the Disagreements table: {', '.join(wrong)}")
    return Result(
        2, title, "PASS", f"neither {L_FIH} nor {L_UNBLINDED} is in the Disagreements table"
    )


def check_3(report: Report) -> Result:
    title = "A Phase 3 study is never first-in-human"
    rows = report.labelled("All variables", L_FIH)
    if not rows:
        return Result(3, title, "FAIL", f"no {L_FIH} row in All variables")
    if len(rows) != 1:
        return Result(3, title, "FAIL", f"{len(rows)} {L_FIH} rows, expected one")
    row = rows[0]
    value, status, confidence = row.cell("Value"), row.cell("Status"), row.cell("Confidence")
    problems = []
    if not value.startswith("No"):
        problems.append(f"value is {value[:40]!r}, expected No")
    if status != "Confirmed":
        problems.append(f"status is {status!r}, expected Confirmed")
    if confidence != "100%":
        problems.append(f"confidence is {confidence!r}, expected 100%")
    if _in_disagreements(report, L_FIH):
        problems.append("it is in the Disagreements table")
    phase = report.labelled("All variables", L_PHASE)
    phase_value = phase[0].cell("Value") if phase else "<missing>"
    if problems:
        return Result(3, title, "FAIL", "; ".join(problems) + f" (study phase: {phase_value})")
    return Result(
        3,
        title,
        "PASS",
        f"No, Confirmed, 100%, not in the Disagreements table (phase {phase_value})",
    )


def check_4(report: Report) -> Result:
    title = "Interim analyses: one count row, with the conflict flagged on it"
    problems = []
    if report.labelled("All variables", L_INTERIM_PLANNED):
        problems.append(f'the "{L_INTERIM_PLANNED}" row is still there')
    count_no = _disagreement_number(report, L_INTERIM_COUNT)
    if count_no is None:
        problems.append(f"{L_INTERIM_COUNT} is not in the Disagreements table")
    else:
        said = ""
        for row in report.table("Disagreements between the documents"):
            if row.cell("#") == count_no:
                said = row.cell("What the documents say")
        for needle in ("0", "Amendment p.98", "1", "RFP p.4"):
            if needle not in said:
                problems.append(f"Disagreement {count_no} does not say {needle!r}")
    timing = report.labelled("All variables", L_INTERIM_TIMING)
    if not timing:
        problems.append(f"no {L_INTERIM_TIMING} row")
    else:
        row = timing[0]
        if row.cell("Status") == "Confirmed":
            problems.append(f"{L_INTERIM_TIMING} is still marked Confirmed")
        if count_no is not None and f"Disagreement {count_no}" not in row.cell("Value"):
            problems.append(
                f"{L_INTERIM_TIMING} does not point to Disagreement {count_no} "
                f"(value cell: {row.cell('Value')[:60]!r})"
            )
    if problems:
        return Result(4, title, "FAIL", "; ".join(problems))
    return Result(
        4,
        title,
        "PASS",
        f"no planned row; the count is Disagreement {count_no} (0 against 1) and the "
        "timing row points at it",
    )


def check_5(report: Report, run_path: Path) -> Result:
    title = "Monitoring visits: one row per visit type, in the sponsor's own words"
    problems = []
    for gone in (L_MONITORING_VISITS, L_ONSITE_REMOTE_MIX):
        if report.labelled("All variables", gone):
            problems.append(f'the "{gone}" row is still there')

    order: list[str] = []
    for row in report.table("All variables"):
        if row.label in FIVE_VISIT_TYPES and row.label not in order:
            order.append(row.label)
            if row.group != OPERATIONAL_GROUP:
                problems.append(f"{row.label} is under {row.group!r}, not {OPERATIONAL_GROUP!r}")
    missing = [label for label in FIVE_VISIT_TYPES if label not in order]
    if missing:
        problems.append(f"no row for: {', '.join(missing)}")
    elif order != list(FIVE_VISIT_TYPES):
        problems.append(f"the five rows are in the order {order}")

    stated = _source_digits(run_path)
    for label, figures in RFP_VISIT_FIGURES.items():
        rows = report.labelled("All variables", label)
        if not rows:
            continue
        value = " ".join(r.cell("Value") for r in rows)
        absent = [f for f in figures if f not in re.findall(r"\d+", value.replace(",", ""))]
        if absent:
            problems.append(f"{label} does not state {', '.join(absent)} (value: {value[:60]!r})")
    invented: list[str] = []
    for label in FIVE_VISIT_TYPES:
        for row in report.labelled("All variables", label):
            for number in re.findall(r"\d+", row.cell("Value").replace(",", "")):
                if number not in stated:
                    invented.append(f"{label}: {number}")
    if invented:
        problems.append(f"numbers no document states: {', '.join(invented)}")
    if problems:
        return Result(5, title, "FAIL", "; ".join(problems))
    return Result(
        5,
        title,
        "PASS",
        "five rows in the asked-for order under Operational Metrics and Volumes, each "
        "carrying the RFP's own figures and no number the documents do not state",
    )


def _source_digits(run_path: Path) -> set[str]:
    """Every digit string in the two input PDFs, for "no invented number"."""
    import pymupdf

    digits: set[str] = set()
    for pdf_path in sorted((run_path / "inputs").glob("*.pdf")):
        with pymupdf.open(pdf_path) as pdf:  # type: ignore[no-untyped-call]
            for page in pdf:
                digits.update(re.findall(r"\d+", page.get_text().replace(",", "")))
    return digits


def check_6(report: Report) -> Result:
    title = "Keep text about related studies the sponsor is running alongside this one"
    notes = [
        "offline: tests/other_study/test_other_study.py — the companion-study sentence "
        '("approximately 80% of its sites with the companion study NEOD001-CL003") must be kept.',
    ]
    problems = []
    appendix_c = report.text.split("Appendix C")[-1] if "Appendix C" in report.text else ""
    if "Clinical Experience" not in appendix_c:
        problems.append("section 1.3.2 Clinical Experience is no longer listed in Appendix C")
    phase = report.labelled("All variables", L_PHASE)
    if len(phase) != 1:
        problems.append(f"{len(phase)} {L_PHASE} rows, expected one")
    elif phase[0].cell("Value") != "Phase 3" or phase[0].cell("Status") != "Confirmed":
        problems.append(
            f"{L_PHASE} reads {phase[0].cell('Value')!r} / {phase[0].cell('Status')!r}, "
            "expected Phase 3 / Confirmed"
        )
    live = "FAIL" if problems else "ok"
    notes.append(
        f"live regression on this run: {live}"
        + (f" — {'; '.join(problems)}" if problems else
           " — 1.3.2 Clinical Experience still removed and listed in Appendix C; "
           "Study phase is one row, Phase 3, Confirmed")
    )
    return Result(
        6,
        title,
        "LOOK",
        "the new behaviour can only be shown offline on a made-up section; the live half "
        "is a regression and is reported below",
        notes,
    )


# One entry in a page list. `(?![\d%])` is what stops the confidence figure at
# the end of an Appendix A line being read as another page: that line runs
# `Amendment p.21 (printed p.19), 98%: "..."`, and without the lookahead the
# ", 98" matched as a page with no printed number — 56 false failures on run
# r-20261008-review2-run1, every one of them a percentage.
_ENTRY = r"\d+(?:-\d+)?(?![\d%])(?:\s*\(printed p\.[^)]*\))?"
_REF_LIST = re.compile(rf"Amendment p\.\s*({_ENTRY}(?:\s*,\s*{_ENTRY})*)")
_ONE_PAGE = re.compile(r"(\d+)(?:-(\d+))?(?:\s*\(printed p\.\s*([^)]*?)\s*\))?")


def check_7(report: Report, run_path: Path) -> Result:
    title = "Show the page number printed on the page as well as the PDF page"
    printed = _printed_pages(run_path)
    if not printed:
        return Result(7, title, "FAIL", "no printed page numbers found in the amendment PDF")

    def want(pdf_page: int) -> str | None:
        """The annotation this page needs, or None when the two numbers agree."""
        label = printed.get(pdf_page)
        return None if label is None or label == str(pdf_page) else label

    bad: list[str] = []
    good: list[str] = []
    cited: set[int] = set()
    flat = report.text.replace("\n", " ")
    for match in _REF_LIST.finditer(flat):
        for entry in _ONE_PAGE.finditer(match.group(1)):
            start, end, shown = int(entry.group(1)), entry.group(2), entry.group(3)
            cited.add(start)
            wanted = want(start)
            if end is not None and want(int(end)) is not None:
                wanted = f"{wanted or start}-{want(int(end))}"
            if wanted is None:
                if shown is not None:
                    bad.append(f"p.{entry.group(0)} shows a printed number but the two agree")
                continue
            if shown is None:
                bad.append(f"p.{start} shows no printed number (printed p.{wanted})")
            elif shown.removeprefix("p.").strip() != wanted:
                bad.append(f"p.{start} shows printed p.{shown}, expected {wanted}")
            else:
                good.append(f"p.{start} (printed p.{wanted})")

    spot = [p for p in sorted(cited) if want(p) is not None][:3]
    notes = [
        f"spot-check by hand in runs/{run_path.name}/inputs/'Example protocol 2.pdf': "
        + ", ".join(f"PDF p.{p} should print {want(p)}" for p in spot),
    ]
    if bad:
        return Result(
            7,
            title,
            "FAIL",
            f"{len(bad)} of {len(bad) + len(good)} amendment page references are wrong, "
            f"e.g. {'; '.join(bad[:3])}",
            notes,
        )
    return Result(
        7,
        title,
        "PASS",
        f"all {len(good)} amendment page references whose printed number differs show both, "
        "and none that agree shows a second number",
        notes,
    )


def _printed_pages(run_path: Path) -> dict[int, str]:
    """PDF page -> the number printed on it, re-derived from the amendment itself.

    Deliberately independent of src/rfp_intake/ingest: the amendment prints
    "Page N of 135" in its header from PDF page 5 onwards, and that is read here
    with a regex of this script's own.
    """
    import pymupdf

    candidates = sorted((run_path / "inputs").glob("*.pdf"))
    amendment = next((p for p in candidates if "protocol" in p.name.lower()), None)
    if amendment is None:
        return {}
    printed: dict[int, str] = {}
    with pymupdf.open(amendment) as pdf:  # type: ignore[no-untyped-call]
        for page_no, page in enumerate(pdf, start=1):
            match = re.search(r"Page\s+(\d+)\s+of\s+\d+", page.get_text())
            if match:
                printed[page_no] = match.group(1)
    return printed


def check_8(report: Report) -> Result:
    title = "The two real planted disagreements are still there"
    missing = [
        label
        for label in (L_DURATION, L_PLACEBO_MATCHES)
        if not _in_disagreements(report, label)
    ]
    if missing:
        return Result(
            8, title, "FAIL", f"missing from the Disagreements table: {', '.join(missing)}"
        )
    return Result(
        8,
        title,
        "PASS",
        f"both {L_DURATION} and {L_PLACEBO_MATCHES} are still in the Disagreements table",
    )


_COVERAGE = re.compile(r"Values found for (\d+) of (\d+) variables")


def _coverage(report: Report) -> tuple[int, int]:
    match = _COVERAGE.search(_norm(report.pages[0]))
    if not match:
        raise SystemExit(f"{report.path}: no coverage sentence on page 1")
    return int(match.group(1)), int(match.group(2))


def check_9(report: Report, baseline: Report | None) -> Result:
    title = "Coverage has not fallen"
    found, total = _coverage(report)
    if baseline is None or baseline.path == report.path:
        return Result(
            9,
            title,
            "BASELINE",
            f"this run IS the baseline: {found} of {total} variables have a value found",
        )
    base_found, base_total = _coverage(baseline)

    def _value_found(rep: Report, label: str) -> bool:
        rows = rep.labelled("All variables", label)
        return any(r.cell("Status") not in ("", "Not found") for r in rows)

    removed_found = [lab for lab in REMOVED_BY_CHANGES if _value_found(baseline, lab)]
    added_found = [lab for lab in FIVE_VISIT_TYPES if _value_found(report, lab)]
    base_kept = base_found - len(removed_found)
    now_kept = found - len(added_found)
    notes = [
        f"baseline {baseline.path.parent.name}: {base_found} of {base_total} found",
        f"this run: {found} of {total} found",
        f"removed by changes 4 and 5 and found in the baseline: {len(removed_found)} "
        f"({', '.join(removed_found) or 'none'})",
        f"added by change 5 and found now: {len(added_found)} ({', '.join(added_found) or 'none'})",
        f"arithmetic: {base_found} - {len(removed_found)} = {base_kept} kept variables found "
        f"before; {found} - {len(added_found)} = {now_kept} kept variables found now",
    ]
    if now_kept < base_kept:
        return Result(
            9, title, "FAIL", f"{now_kept} kept variables found, down from {base_kept}", notes
        )
    return Result(
        9, title, "PASS", f"{now_kept} kept variables found, against {base_kept} before", notes
    )


def _errors(run_path: Path) -> int:
    data = json.loads((run_path / "extraction.json").read_text())
    return len(data.get("errors") or [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", help="a folder name under runs/")
    parser.add_argument(
        "--baseline",
        default=BASELINE_RUN,
        help=f"the run check 9 compares coverage against (default {BASELINE_RUN})",
    )
    args = parser.parse_args()

    run_path = PROJECT_ROOT / "runs" / args.run_id
    if not (run_path / "report.pdf").is_file():
        print(f"No report.pdf in {run_path}", file=sys.stderr)
        return 2
    report = parse_report(run_path / "report.pdf")

    baseline = None
    base_path = PROJECT_ROOT / "runs" / args.baseline / "report.pdf"
    if base_path.is_file():
        baseline = report if args.baseline == args.run_id else parse_report(base_path)

    print(f"run {args.run_id} — {report.path}")
    print(f"{len(report.rows)} table rows read; {_errors(run_path)} errors recorded in the run")
    print()

    results = [
        check_1(report),
        check_2(report),
        check_3(report),
        check_4(report),
        check_5(report, run_path),
        check_6(report),
        check_7(report, run_path),
        check_8(report),
        check_9(report, baseline),
    ]
    for result in results:
        print(f"{result.verdict:<8} {result.number}. {result.title}")
        print(f"         {result.reason}")
        for note in result.notes:
            print(f"         · {note}")
    failed = [r.number for r in results if r.verdict == "FAIL"]
    print()
    print(
        f"{sum(1 for r in results if r.verdict == 'PASS')} pass, {len(failed)} fail"
        + (f" ({', '.join(str(n) for n in failed)})" if failed else "")
    )
    print(
        "check 10 is not measured here: run pytest, ruff check . and mypy ., then the "
        "pipeline-rules-reviewer and plan-conformance-reviewer."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
