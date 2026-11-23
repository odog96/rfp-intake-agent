"""Angus Gray's asks of 29 October 2026, measured on one run's report.pdf.

    python scripts/check_review_2026_10_29.py r-20261123-oct29-run2
    python scripts/check_review_2026_10_29.py r-20261123-oct29-run2 \
        --pdf runs/<run>/report-rerender.pdf

Angus reviewed report r-20261029-140643 on a call on 29 October 2026 (Zoom
transcript GMT20261029-142148). Each check below is one thing he asked for on
that call, or an earlier ask the report now answers, and prints PASS, FAIL or
LOOK (only a person can settle it, and the line says what to look at). The
checks read report.pdf itself, which is what Angus reads, using the table
reader in scripts/check_review_2026_10_07.py; `extraction.json` is not read.

The printed labels below are literals on purpose, as in
scripts/check_review_2026_10_07.py: rule 4 forbids keying pipeline behaviour to
a field name, and nothing here feeds the pipeline. Several name variables the
29 October changes delete, so they cannot be read from today's
config/fields.yaml.

Reads only. Writes nothing, and makes no model calls.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import check_review_2026_10_07 as c7  # noqa: E402

# The tables the report draws since the 29 October changes: no Confidence column.
c7.HEADER_SIGNATURES = (
    ("Variable", "Value", "Status", "Where"),
    ("Variable", "Value", "Why it is flagged", "Where"),
    ("Applies to", "Value", "Status", "Where"),
    *c7.HEADER_SIGNATURES,
)

ALL = "All variables"

# Labels the 29 October report printed that the changes remove or merge.
GONE = {
    "Evidence of visit intensity": "S12, visit intensity removed",
    "Subject visit intensity": "S12, visit intensity removed",
    "Overall study duration": "T3, merged into Patient timeline",
    "Timeline components": "T3, merged into Patient timeline",
    "Specific monitoring frequency requested": "M8, one requested monitoring frequency",
    "Basis of monitoring frequency": "M11, both basis rows removed",
    "Source data verification expectations": "M13, one source data row",
    "Source data review expectations": "M13, one source data row",
    "What to assume about the placebo": "B2, the tool does not guess about the placebo",
    "Unblinded staff and monitoring": "B7, every variable its own header",
}

TIMELINE = "Patient timeline"
# The lines of the patient timeline, in the order Angus gave them (29 Oct 00:01:05 to
# 00:01:23), then the time window to budget for (00:09:13 to 00:11:18) and the whole study.
TIMELINE_LINES = (
    "Screening",
    "Run-in",
    "Treatment",
    "Follow-up",
    "Open-label extension",
    "Long-term follow-up",
    "Overall patient participation",
    "Time window to budget for",
    "Whole study",
)
# "and then any additional timelines, whether there's an open-label extension, or a
# long-term follow-up" (29 Oct 00:01:14): printed only where a document states one.
OPTIONAL_TIMELINE_LINES = ("Open-label extension", "Long-term follow-up")
BLINDING_GROUP = "Blinding and Unblinded Monitoring"
PAGE_REF = re.compile(r"\bp\.\s?\d")
STATUS_WORDS = {"Confirmed", "Review", ""}


@dataclass
class Result:
    ask: str
    title: str
    verdict: str  # PASS | FAIL | LOOK
    reason: str
    notes: list[str] = field(default_factory=list)


def _value(row: c7.Row) -> str:
    return row.cell("Value")


def _all_rows(report: c7.Report) -> list[c7.Row]:
    return [r for r in report.rows if r.section == ALL and not r.is_heading]


def _in_visit_table(rows: list[c7.Row]) -> set[int]:
    """Indexes of the rows that belong to a visit table drawn inside All variables.

    The visit table spans the main table's width, so its own row rules read as
    rows of the main table. It runs from its header row ("Period Visit timing") to
    the grey sentence under it.
    """
    inside: set[int] = set()
    on = False
    for i, r in enumerate(rows):
        text = r.text
        if "Visit timing" in text and "Period" in text:
            on = True
        if on:
            inside.add(i)
        if on and ("count leaves out" in text or "Not counted:" in text):
            on = False
    return inside


def _label_rows(rows: list[c7.Row], label: str) -> list[int]:
    """The rows that print `label` itself in the Variable column.

    A disagreement's sides sit under its row with an empty Variable cell, so the
    table reader gives them the label above and no scope; they are not rows of
    their own.
    """
    return [i for i, r in enumerate(rows)
            if r.label == label and r.scope in ("", "BUDGET")
            and r.cell("Variable").startswith(label)]


def _children(rows: list[c7.Row], start: int) -> list[c7.Row]:
    """The continuation rows under the variable row at `start`."""
    out = []
    for r in rows[start + 1:]:
        if r.label != rows[start].label:
            break
        out.append(r)
    return out


def check_gone(report: c7.Report) -> Result:
    present = []
    for label, why in GONE.items():
        if any(r.label == label for r in _all_rows(report)) or label in report.text:
            present.append(f"{label} ({why})")
    if present:
        return Result("S12 T3 M8 M11 M13 B2 B7", "Removed and merged variables are gone", "FAIL",
                      f"still printed: {'; '.join(present)}")
    return Result("S12 T3 M8 M11 M13 B2 B7", "Removed and merged variables are gone", "PASS",
                  f"none of the {len(GONE)} old labels is printed anywhere in the report")


def check_intensity(report: c7.Report) -> Result:
    hits = [line for line in report.text.splitlines() if re.search(r"visit intensity", line, re.I)]
    if hits:
        return Result("S12", "No visit intensity anywhere", "FAIL", f"{len(hits)} line(s)",
                      hits[:3])
    return Result("S12", "No visit intensity anywhere", "PASS", "the words are not in the report")


def check_timeline(report: c7.Report) -> Result:
    rows = _all_rows(report)
    starts = _label_rows(rows, TIMELINE)
    title = "One patient timeline, a line per period, in Angus's order"
    if len(starts) != 1:
        return Result("T3 T4 T8", title, "FAIL", f"{len(starts)} rows labelled {TIMELINE}")
    lines = [r.scope.removeprefix("BUDGET").strip() for r in _children(rows, starts[0])]
    named = [s for s in lines if s]
    notes = [f"{r.scope}: {_value(r)} | {r.cell('Status')} | {r.cell('Where')}"
             for r in _children(rows, starts[0]) if r.scope]
    missing = [w for w in TIMELINE_LINES if w not in named and w not in OPTIONAL_TIMELINE_LINES]
    order = [w for w in named if w in TIMELINE_LINES]
    if missing:
        return Result("T3 T4 T8", title, "FAIL", f"missing lines: {', '.join(missing)}", notes)
    if order != [w for w in TIMELINE_LINES if w in order]:
        return Result("T3 T4 T8", title, "FAIL", f"out of order: {', '.join(order)}", notes)
    extra = [s for s in named if s not in TIMELINE_LINES]
    accrual = [s for s in named if re.search(r"accrual|recruit|enrol", s, re.I)]
    if accrual:
        return Result("T6", title, "FAIL", f"an enrolment line is back: {', '.join(accrual)}",
                      notes)
    reason = f"{len(order)} lines in order" + (f", plus {', '.join(extra)}" if extra else "")
    return Result("T3 T4 T6 T8", title, "PASS", reason, notes)


def _timeline_line(report: c7.Report, name: str) -> c7.Row | None:
    rows = _all_rows(report)
    starts = _label_rows(rows, TIMELINE)
    if not starts:
        return None
    for r in _children(rows, starts[0]):
        if r.scope.removeprefix("BUDGET").strip() == name:
            return r
    return None


def check_screening(report: c7.Report) -> Result:
    title = 'The screening line reads like "28 days, Day -28 to Day 1", nothing about dosing'
    r = _timeline_line(report, "Screening")
    if r is None:
        return Result("T4 T5", title, "FAIL", "no Screening line")
    value = _value(r)
    if re.search(r"administration|study drug|dose", value, re.I):
        return Result("T5", title, "FAIL", f"the line still explains Day 1: {value}")
    if re.match(r"^\d+ days?, Day ?-?\d+ to (Day|Month) ?\S+", value):
        return Result("T4 T5", title, "PASS", value)
    return Result("T4 T5", title, "LOOK", f"read the line: {value}")


def check_event_driven(report: c7.Report) -> Result:
    title = "Event-driven treatment is stated, and the time window to budget for is given"
    treat = _timeline_line(report, "Treatment")
    window = _timeline_line(report, "Time window to budget for")
    notes = [f"Treatment: {_value(treat) if treat else '(none)'}",
             f"Time window to budget for: {_value(window) if window else '(none)'} "
             f"({window.cell('Where') if window else ''})"]
    if treat is None or not re.search(r"event", _value(treat), re.I):
        return Result("T7", title, "FAIL", "the Treatment line does not say it runs until events",
                      notes)
    # Run r-20261123-oct29-run5 printed "18 months, Month 1-Day 1 to approximately 156
    # primary endpoint events have been reached": a power assumption's length beside an
    # end that is a number of events. Treatment that runs until events has no fixed length.
    if re.match(r"\s*(?:approximately\s+|about\s+|~\s*)?\d+(?:\.\d+)?(?:\s*[-\u2013]\s*\d+)?"
                r"\s+(?:days?|weeks?|months?|years?)\b", _value(treat), re.I):
        return Result("T7", title, "FAIL",
                      "the Treatment line gives a fixed length beside an end that is a number of "
                      "events", notes)
    if window is None:
        return Result("T7", title, "FAIL", "no Time window to budget for line", notes)
    if "Not found" in _value(window) or "RFP" not in window.cell("Where"):
        return Result("T7", title, "LOOK", "the window is not read from the RFP", notes)
    return Result("T7", title, "PASS", "treatment until events; window from the RFP", notes)


def check_whole_study(report: c7.Report) -> Result:
    title = "The whole-study duration is a line of the timeline, with its disagreement under it"
    r = _timeline_line(report, "Whole study")
    if r is None:
        return Result("T2 T3", title, "FAIL", "no Whole study line")
    return Result("T2 T3", title, "PASS", f"{_value(r)} | {r.cell('Where')}")


def check_no_confidence(report: c7.Report) -> Result:
    title = "No confidence percentage anywhere; status is the only judgement"
    problems = []
    if "Confidence is the extraction model" in report.text:
        problems.append("the Confidence paragraph is still printed")
    for r in report.rows:
        if "Confidence" in r.columns:
            problems.append(f"a table on page {r.page} still has a Confidence column")
            break
    # Appendix A used to print "— Amendment p.12 (printed p.10), 99%:".
    evidence = [m.group(0) for m in re.finditer(r"p\.\d+(?: \(printed p\.\d+\))?, \d{1,3}%:",
                                                report.text)]
    if evidence:
        problems.append(f"{len(evidence)} Appendix A line(s) still give a percentage")
    for r in _all_rows(report):
        if re.search(r"\b\d{1,3}%", r.cell("Status") + " " + r.cell("Where")):
            problems.append(f"a percentage in the Status or Where cell of {r.label}")
    if problems:
        return Result("L5", title, "FAIL", "; ".join(problems))
    return Result("L5", title, "PASS", "no Confidence column, paragraph or figure")


def check_status_words(report: c7.Report) -> Result:
    title = "Every status reads Confirmed or Review"
    rows = _all_rows(report)
    skip = _in_visit_table(rows)
    odd = sorted({r.cell("Status") for i, r in enumerate(rows)
                  if i not in skip and r.cell("Status") not in STATUS_WORDS})
    if odd:
        return Result("L5 L9", title, "FAIL", f"other status words: {', '.join(odd)}")
    return Result("L5 L9", title, "PASS", "only Confirmed and Review in All variables")


def check_references_right(report: c7.Report) -> Result:
    title = "References only on the right: no page reference inside a value"
    rows = _all_rows(report)
    skip = _in_visit_table(rows)
    bad = [f"{r.label} / {r.scope}: {_value(r)[:90]}" for i, r in enumerate(rows)
           if i not in skip and PAGE_REF.search(_value(r))]
    if bad:
        return Result("L2 L3 B9", title, "FAIL", f"{len(bad)} value cell(s) hold pages", bad[:6])
    where = sum(1 for i, r in enumerate(rows) if i not in skip and PAGE_REF.search(r.cell("Where")))
    return Result("L2 L3 B9", title, "PASS", f"{where} rows give their pages in the Where column")


def check_placebo(report: c7.Report) -> Result:
    title = "Placebo matches the study drug: its own header and answer, any disagreement under it"
    rows = _all_rows(report)
    starts = _label_rows(rows, "Placebo matches the study drug")
    if len(starts) != 1:
        return Result("B7 B3 B5", title, "FAIL", f"{len(starts)} rows with that label")
    r = rows[starts[0]]
    value = _value(r)
    sides = [c for c in _children(rows, starts[0])]
    notes = [f"{value} | {r.cell('Status')}"] + [
        f"  {_value(c)} | {c.cell('Where')}" for c in sides]
    if value.startswith("Documents disagree"):
        if len(sides) < 2 or not all(PAGE_REF.search(c.cell("Where")) for c in sides[:2]):
            return Result("B7", title, "FAIL", "the disagreement's sides are not under it with "
                          "their pages", notes)
        return Result("B7 B5", title, "PASS", "documents disagree, each side under it", notes)
    if re.match(r"^(Yes|No|N/A|Not found|Not specified)", value):
        return Result("B7 B3 B5", title, "PASS", value, notes)
    return Result("B7", title, "LOOK", f"read the answer: {value}", notes)


def check_pharmacy_first(report: c7.Report) -> Result:
    title = "The RFP's pharmacy monitoring request heads the unblinded section, said once"
    rows = [r for r in report.rows if r.section == ALL]
    group_rows: list[c7.Row] = []
    on = False
    for r in rows:
        if r.is_heading:
            on = r.group == BLINDING_GROUP
            continue
        if on:
            group_rows.append(r)
    labels = [r.label for r in group_rows if r.scope in ("", "BUDGET")]
    count = sum(1 for r in _all_rows(report) if r.label == "Pharmacy monitoring visits"
                and r.scope in ("", "BUDGET"))
    notes = [f"section order: {', '.join(dict.fromkeys(labels))}"]
    if not labels or labels[0] != "Pharmacy monitoring visits":
        return Result("B8", title, "FAIL", "the section does not start with the pharmacy visits",
                      notes)
    if count != 1:
        return Result("B8 L15", title, "FAIL", f"Pharmacy monitoring visits printed {count} times",
                      notes)
    second = next((lab for lab in dict.fromkeys(labels) if lab != labels[0]), "")
    if second != "Pharmacy monitoring frequency":
        return Result("M6", title, "FAIL", "the pharmacy frequency is not beside the visits", notes)
    return Result("B8 M6 L15", title, "PASS", "pharmacy visits, then pharmacy frequency, once each",
                  notes)


def check_monitoring_frequency(report: c7.Report) -> Result:
    title = "One requested monitoring frequency, in the RFP's own words"
    rows = _all_rows(report)
    starts = _label_rows(rows, "Requested monitoring frequency")
    if len(starts) != 1:
        return Result("M8", title, "FAIL", f"{len(starts)} rows with that label")
    value = _value(rows[starts[0]])
    if re.search(r"Every \d+ days", value):
        return Result("M9", title, "FAIL", f"converted to days: {value}")
    if re.search(r"week", value, re.I) or value.startswith(("Not specified", "Not found")):
        return Result("M8 M9", title, "PASS", value)
    return Result("M8 M9", title, "LOOK", f"compare with RFP p.4: {value}")


def check_pharmacy_frequency(report: c7.Report) -> Result:
    title = 'Its own row "Pharmacy monitoring frequency"'
    rows = _all_rows(report)
    starts = _label_rows(rows, "Pharmacy monitoring frequency")
    sub = [r for r in rows if r.scope.strip() == "Pharmacy monitoring"]
    if len(starts) != 1 or sub:
        return Result("M10", title, "FAIL", f"{len(starts)} rows; {len(sub)} old sub-rows")
    return Result("M10", title, "PASS", _value(rows[starts[0]]))


def check_source_data(report: c7.Report) -> Result:
    title = 'One "Source data monitoring expectations" row'
    rows = _all_rows(report)
    starts = _label_rows(rows, "Source data monitoring expectations")
    if len(starts) != 1:
        return Result("M13", title, "FAIL", f"{len(starts)} rows with that label")
    return Result("M13", title, "PASS", _value(rows[starts[0]]))


def check_subjects_confirmed(report: c7.Report) -> Result:
    title = "260 subjects, which both documents state, reads Confirmed"
    rows = _all_rows(report)
    starts = _label_rows(rows, "Total number of patients / subjects")
    if not starts:
        return Result("L9", title, "FAIL", "no subjects row")
    r = rows[starts[0]]
    status = r.cell("Status") or next((c.cell("Status") for c in _children(rows, starts[0])
                                       if c.cell("Status")), "")
    if status == "Confirmed":
        return Result("L9", title, "PASS", f"{_value(r)} | Confirmed")
    why = [f.cell("Why it is flagged") for f in report.table("Flagged for review")
           if f.label == "Total number of patients / subjects"]
    return Result("L9", title, "FAIL", f"{_value(r)} | {status}", why)


def check_title_first(report: c7.Report) -> Result:
    title = "The official title, word for word, is the first row"
    rows = [r for r in _all_rows(report) if r.scope in ("", "BUDGET")]
    if not rows or rows[0].label != "Official study title":
        first = rows[0].label if rows else "(no rows)"
        return Result("V1 V3", title, "FAIL", f"the first row is {first}")
    value = _value(rows[0])
    if "Phase 3" in value or "PHASE 3" in value:
        return Result("V1 V3", title, "PASS", value[:120])
    return Result("V1 V3", title, "LOOK", f"compare with Amendment p.1: {value[:120]}")


def check_design_summary(report: c7.Report) -> Result:
    title = "The design summary leaves out the stratification factors"
    rows = _all_rows(report)
    starts = _label_rows(rows, "General study design / structure")
    if not starts:
        return Result("V5", title, "FAIL", "no design row")
    value = _value(rows[starts[0]])
    if re.search(r"stratif", value, re.I):
        return Result("V5", title, "FAIL", value[:160])
    return Result("V5", title, "PASS", value[:160])


def check_considerations(report: c7.Report) -> Result:
    title = "Premedication and chemotherapy are not listed as key considerations"
    rows = _all_rows(report)
    inside = [rows[i] for i in sorted(_in_visit_table(rows))]
    if not inside:
        return Result("S9 V37", title, "LOOK", "no visit table read in All variables")
    listed = [r.text for r in inside if re.search(r"\b(Premedication|Chemotherapy)\b", r.text)
              and "leaves out" not in r.text]
    if listed:
        return Result("S9 V37", title, "FAIL", f"{len(listed)} visit row(s) list them", listed[:2])
    return Result("S9 V37", title, "PASS", "no visit row lists either")


def check_every_answer(report: c7.Report) -> Result:
    title = "Every variable has its own header and an answer"
    try:
        from rfp_intake.domain.registry import get_registry
        registry = get_registry()
    except Exception as e:  # pragma: no cover - a broken registry fails the test suite
        return Result("B7 L2", title, "LOOK", f"registry not readable: {e}")
    shown = {r.label for r in _all_rows(report)}
    # A variable printed inside another's headline has no row of its own by design:
    # the allocation ratio is in "2 arms, randomized 1:1" (decided by Oliver on
    # 2026-10-12, the layout Angus Gray approved on 13 and 29 October).
    in_headline = {
        h.field
        for f in registry.fields
        for h in (getattr(f.display, "headline_fields", None) or [])
        + (getattr(f.display, "also", None) or [])
    }
    missing = [f.label for f in registry.fields
               if f.label not in shown and not getattr(f, "shown_under", None)
               and f.id not in in_headline]
    rows = _all_rows(report)
    empty = []
    for i, r in enumerate(rows):
        if r.scope not in ("", "BUDGET") or r.label in ("",):
            continue
        if r.label in shown and not _value(r) and not _children(rows, i):
            empty.append(r.label)
    if missing or empty:
        notes = ([f"no row: {', '.join(missing)}"] if missing else []) + (
            [f"no answer: {', '.join(empty)}"] if empty else [])
        return Result("B7 L2", title, "FAIL", f"{len(missing)} without a row, {len(empty)} "
                      "without an answer", notes)
    return Result("B7 L2", title, "PASS",
                  f"every one of the {len(registry.fields)} variables has a row or a line")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id", help="a folder name under runs/")
    parser.add_argument("--pdf", help="read this PDF instead of runs/<run_id>/report.pdf")
    args = parser.parse_args()
    path = Path(args.pdf) if args.pdf else PROJECT_ROOT / "runs" / args.run_id / "report.pdf"
    if not path.is_file():
        print(f"No report at {path}", file=sys.stderr)
        return 2
    report = c7.parse_report(path)
    print(f"run {args.run_id} — {path}")
    print(f"{len(report.rows)} table rows read")
    print()
    results = [
        check_gone(report),
        check_intensity(report),
        check_timeline(report),
        check_screening(report),
        check_event_driven(report),
        check_whole_study(report),
        check_no_confidence(report),
        check_status_words(report),
        check_references_right(report),
        check_placebo(report),
        check_pharmacy_first(report),
        check_monitoring_frequency(report),
        check_pharmacy_frequency(report),
        check_source_data(report),
        check_subjects_confirmed(report),
        check_title_first(report),
        check_design_summary(report),
        check_considerations(report),
        check_every_answer(report),
    ]
    for n, result in enumerate(results, start=1):
        print(f"{result.verdict:<5} {n:>2}. [{result.ask}] {result.title}")
        print(f"          {result.reason}")
        for note in result.notes:
            print(f"          · {note}")
    failed = [n for n, r in enumerate(results, start=1) if r.verdict == "FAIL"]
    looks = [n for n, r in enumerate(results, start=1) if r.verdict == "LOOK"]
    print()
    print(f"{sum(1 for r in results if r.verdict == 'PASS')} pass, {len(failed)} fail"
          + (f" ({', '.join(map(str, failed))})" if failed else "")
          + f", {len(looks)} look" + (f" ({', '.join(map(str, looks))})" if looks else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
