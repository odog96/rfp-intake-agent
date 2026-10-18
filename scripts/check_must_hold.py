"""Do the facts that must hold for the two sample documents hold in one run?

    python scripts/check_must_hold.py r-20261012-angus2

The facts are data, in eval/golden/sample_pair_must_hold.yaml; this script only
knows how to read each `kind` of fact. It reads the run's extraction.json, not
report.pdf: the question is whether the pipeline produced the answer, and a
report that drew a wrong answer correctly would pass a report check.

Prints one line per fact, PASS, KNOWN or FAIL with what the run holds, and
exits 1 if any fact fails, 2 if the run is missing or was not made from the two
sample documents. KNOWN means the only part missing is one the facts file names
as a known intermittent failure (`known` on a row, `known_missing` on a
coverage fact), tied to an open item in CLAUDE.md; it does not fail the run.

Reads only. Writes nothing, and makes no model calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FACTS_PATH = PROJECT_ROOT / "eval" / "golden" / "sample_pair_must_hold.yaml"
FIELDS_PATH = PROJECT_ROOT / "config" / "fields.yaml"

# extraction.json and the facts file are both free-form JSON/YAML, so a fact, a
# row and a record are plain dicts here.
Fact = dict[str, Any]
Row = dict[str, Any]
# A fact's verdict, and what the run holds.
Result = tuple[str, str]

PASS, KNOWN, FAIL = "PASS", "KNOWN", "FAIL"

# The statuses of a row that gives an answer; a disagreement row is
# needs_review with no value of its own, and still answers.
ANSWERED = frozenset({"confirmed", "needs_review"})


# --------------------------------------------------------------------------- #
# Values
# --------------------------------------------------------------------------- #


def _text(value: Any) -> str:
    """A value written out as text: {"n": 42, "unit": "months"} reads "42 months"."""
    if isinstance(value, dict):
        return " ".join(_text(v) for v in value.values() if v is not None)
    if isinstance(value, list):
        return "; ".join(_text(v) for v in value)
    return "" if value is None else str(value)


def _same(found: Any, want: Any) -> bool:
    """Equal values, with numbers compared as numbers and True never equal to 1."""
    numbers = (int, float)
    if isinstance(found, bool) or isinstance(want, bool):
        return found is want
    if isinstance(found, numbers) and isinstance(want, numbers):
        return float(found) == float(want)
    return bool(found == want)


def _states_amount(text: str, amount: dict[str, Any]) -> bool:
    """`text` states the number `n` directly followed by one of `unit`, whatever
    words surround the two: {n: 40, unit: [month, months]} matches "40 month
    study duration", "40 months" and "40-month", not "140 months" or "40.5 months"."""
    number = re.escape(str(amount["n"]))
    units = "|".join(re.escape(str(u).lower()) for u in amount["unit"])
    return re.search(rf"(?<![\d.,]){number}(?:\.0+)?(?![\d,]|\.\d)(?:\s+|-)(?:{units})(?!\w)",
                     text) is not None


def _matches(value: Any, spec: dict[str, Any]) -> bool:
    if "value" in spec:
        return _same(value, spec["value"])
    text = _text(value).lower()
    if "amount" in spec and not _states_amount(text, spec["amount"]):
        return False
    return all(re.search(rf"(?<!\w){re.escape(str(w).lower())}(?!\w)", text)
               for w in spec.get("says", []))


def _short(value: Any, width: int = 70) -> str:
    text = " ".join(_text(value).split()) if not isinstance(value, (int, float)) else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def _scope(row: Row) -> str:
    return (row.get("scope") or "").strip().lower()


def _where(prov: dict[str, Any]) -> str:
    return f"{prov.get('doc_kind')} p.{prov.get('page')}"


def _verdict(ok: bool) -> str:
    return PASS if ok else FAIL


# --------------------------------------------------------------------------- #
# The kinds of fact
# --------------------------------------------------------------------------- #


class Run:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.rows: list[Row] = data.get("resolved_fields", [])

    def rows_of(self, field_id: str) -> list[Row]:
        return [r for r in self.rows if r.get("field_id") == field_id]

    def conflicts_of(self, field_id: str) -> list[dict[str, Any]]:
        return [c for c in self.data.get("contradictions", [])
                if c.get("field_id") == field_id and c.get("verdict") == "conflict"]


def _describe_rows(rows: list[Row]) -> str:
    if not rows:
        return "no row"
    return "; ".join(
        f"{_short(r.get('value'), 40)} [{r.get('status')}"
        + (f", scope {r.get('scope')}" if r.get("scope") else "")
        + "".join(", " + _where(s) for s in r.get("sources", [])[:3])
        + "]"
        for r in rows
    )


def one_row(run: Run, fact: Fact) -> Result:
    rows = run.rows_of(fact["field"])
    ok = (len(rows) == 1 and rows[0].get("status") == fact["status"]
          and _matches(rows[0].get("value"), fact))
    return _verdict(ok), f"{len(rows)} row(s): {_describe_rows(rows)}"


def value_row(run: Run, fact: Fact) -> Result:
    rows = [r for r in run.rows_of(fact["field"])
            if _scope(r) == fact["scope"] and r.get("status") in ANSWERED]
    right = [r for r in rows if _matches(r.get("value"), fact)]
    ok = bool(right) and len(right) == len(rows)
    return _verdict(ok), f"scope {fact['scope']}: {_describe_rows(rows)}"


def arms(run: Run, fact: Fact) -> Result:
    rows = [r for r in run.rows_of(fact["field"]) if r.get("status") in ANSWERED]
    subjects = [r for r in run.rows_of(fact["subjects_field"]) if r.get("status") in ANSWERED]
    problems: list[str] = []
    found: list[str] = []
    for arm in fact["arms"]:
        word = arm["scope_says"].lower()
        mine = [r for r in rows if word in _scope(r)]
        counts = [r.get("value") for r in subjects if word in _scope(r)]
        found.append(f"{word}: {len(mine)} arm row(s), subjects {counts or 'none'}")
        if len(mine) != 1:
            problems.append(f"{word} has {len(mine)} arm rows")
        if not counts or not all(_same(c, arm["subjects"]) for c in counts):
            problems.append(f"{word}'s subjects rows do not all say {arm['subjects']}")
    if len(rows) != len(fact["arms"]):
        problems.append(f"{len(rows)} arm rows in all")
    detail = f"{len(rows)} arm row(s); " + "; ".join(found)
    return _verdict(not problems), detail + (f" — {', '.join(problems)}" if problems else "")


def disagreement(run: Run, fact: Fact) -> Result:
    conflicts = run.conflicts_of(fact["field"])
    if "scope" in fact:
        conflicts = [c for c in conflicts
                     if any(_scope(r) == fact["scope"] for r in c.get("records", []))]
    if not conflicts:
        rows = run.rows_of(fact["field"])
        if "scope" in fact:
            rows = [r for r in rows if _scope(r) == fact["scope"]]
        return FAIL, f"no disagreement; {_describe_rows(rows)}"
    for c in conflicts:
        records = [r for r in c.get("records", [])
                   if "scope" not in fact or _scope(r) == fact["scope"]]
        sides = []
        for side in fact["sides"]:
            hit = [r for r in records
                   if r["provenance"].get("doc_kind") == side["from"]
                   and ("page" not in side or r["provenance"].get("page") == side["page"])
                   and _matches(r.get("value"), side)]
            sides.append(bool(hit))
        shown = "; ".join(f"{_where(r['provenance'])}: {_short(r.get('value'), 40)}"
                          for r in records)
        if all(sides):
            return PASS, shown
    return FAIL, f"disagreement without every side: {shown}"


def removed(run: Run, fact: Fact) -> Result:
    headings = [(p.get("heading") or "").strip() for p in run.data.get("removed_passages", [])]
    ok = fact["heading"].lower() in (h.lower() for h in headings)
    return _verdict(ok), f"removed: {headings or 'nothing'}"


def rows_say(run: Run, fact: Fact) -> Result:
    """Every row must say its words; a row marked `known` that does not is KNOWN."""
    found: list[str] = []
    strict_missing = known_missing = False
    for want in fact["rows"]:
        rows = [r for r in run.rows_of(want["field"])
                if r.get("status") in ANSWERED
                and any(s.get("doc_kind") == want["from"] for s in r.get("sources", []))]
        hit = any(_matches(r.get("value"), want) for r in rows)
        value = _short(rows[0].get("value"), 45) if rows else "no row"
        if hit:
            mark = "ok"
        elif want.get("known"):
            known_missing = True
            mark = f"KNOWN MISSING, {want['known']}"
        else:
            strict_missing = True
            mark = "MISSING"
        found.append(f"{want['field'].split('.')[-1]}: {mark} ({value})")
    verdict = FAIL if strict_missing else KNOWN if known_missing else PASS
    return verdict, "; ".join(found)


def coverage(run: Run, fact: Fact) -> Result:
    """At least `at_least` fields answered. Each `known_missing` field that is in
    fact missing lowers the bar by one, and a pass that needs it is KNOWN."""
    registry = yaml.safe_load(FIELDS_PATH.read_text())
    field_ids = [f["id"] for f in registry["fields"]]
    answered = {r.get("field_id") for r in run.rows if r.get("status") in ANSWERED}
    have = [f for f in field_ids if f in answered]
    missing = [f for f in field_ids if f not in answered]
    known = [k for k in fact.get("known_missing", []) if k["field"] in missing]
    if len(have) >= fact["at_least"]:
        verdict = PASS
    elif known and len(have) >= fact["at_least"] - len(known):
        verdict = KNOWN
    else:
        verdict = FAIL
    detail = f"{len(have)} of {len(field_ids)} have a value; none: {', '.join(missing) or '—'}"
    if verdict == KNOWN:
        detail += "; known: " + "; ".join(f"{k['field']}, {k['known']}" for k in known)
    return verdict, detail


def _flat(text: str | None) -> str:
    return " ".join((text or "").split()).lower()


def _schedule(run: Run, fact: Fact) -> tuple[dict[str, Any] | None, str]:
    """The one counted schedule from `from`'s kind of document headed `heading`,
    or None and why not. A schedule names only its document id, so the kind comes
    from the rows that cite that document."""
    if "schedules" not in run.data:
        return None, "no schedules in extraction.json: the run was made before READ_SCHEDULE"
    kinds: dict[str, str] = {}
    for r in run.rows:
        for s in r.get("sources", []):
            kinds.setdefault(s.get("doc_id"), s.get("doc_kind"))
    found = [s for s in run.data["schedules"]
             if s.get("counted") and kinds.get(s.get("doc_id")) == fact["from"]
             and _flat(s.get("heading")) == _flat(fact["heading"])]
    if len(found) != 1:
        headings = [s.get("heading") for s in run.data["schedules"]]
        return None, f"{len(found)} counted schedule(s) so headed; schedules: {headings or 'none'}"
    return found[0], ""


def schedule_counts(run: Run, fact: Fact) -> Result:
    """Each visit column's count of marks, in order: `assessments` counts only the
    rows of assessments, `every_mark` every row (the two settings of Prompt C)."""
    schedule, why = _schedule(run, fact)
    if schedule is None:
        return FAIL, why
    key = {"assessments": "marks_assessments", "every_mark": "marks_all"}[fact["count"]]
    counts = [c.get(key) for c in schedule.get("columns", [])]
    return _verdict(counts == fact["columns"]), f"{fact['count']}: {counts}"


def _timing_starts(timing: str, words: str) -> bool:
    """"Day 1 (±5)" starts with Day 1; "Day 15" and "Days 8, 15, 22" do not."""
    return re.match(rf"{re.escape(words)}(?![\w-])", timing.strip(), re.IGNORECASE) is not None


def schedule_marks(run: Run, fact: Fact) -> Result:
    """Every chosen visit column has a mark in a row of each `marked`
    consideration. A column is chosen by its header: `period` (the first such
    column only if `first`), or every column from the first `from_period` on,
    and in either case `timing_starts`. `columns`, where given, is how many
    must be chosen, so a header read differently cannot pass on fewer."""
    schedule, why = _schedule(run, fact)
    if schedule is None:
        return FAIL, why
    columns = schedule.get("columns", [])
    if "from_period" in fact:
        start = next((i for i, c in enumerate(columns)
                      if _flat(c.get("period")) == _flat(fact["from_period"])), len(columns))
        chosen = columns[start:]
    else:
        chosen = [c for c in columns if _flat(c.get("period")) == _flat(fact["period"])]
    if "timing_starts" in fact:
        chosen = [c for c in chosen if _timing_starts(c.get("timing") or "", fact["timing_starts"])]
    if fact.get("first"):
        chosen = chosen[:1]
    shown = "; ".join(f"{c.get('period')} {c.get('timing') or ''}".strip()
                      + f": {', '.join(c.get('considerations', [])) or 'none'}" for c in chosen)
    problems = []
    if not chosen or ("columns" in fact and len(chosen) != fact["columns"]):
        problems.append(f"{len(chosen)} column(s) chosen")
    problems += [f"{c.get('period')} {c.get('timing') or ''} has no {m}".replace("  ", " ")
                 for c in chosen for m in fact["marked"] if m not in c.get("considerations", [])]
    return _verdict(not problems), (shown or "no column") + (
        f" — {', '.join(problems)}" if problems else "")


KINDS: dict[str, Callable[[Run, Fact], Result]] = {
    "schedule_counts": schedule_counts,
    "schedule_marks": schedule_marks,
    "one_row": one_row,
    "value_row": value_row,
    "arms": arms,
    "disagreement": disagreement,
    "removed": removed,
    "rows_say": rows_say,
    "coverage": coverage,
}


# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id")
    args = parser.parse_args()

    run_dir = PROJECT_ROOT / "runs" / args.run_id
    extraction = run_dir / "extraction.json"
    if not extraction.exists():
        print(f"no extraction.json at {extraction}", file=sys.stderr)
        return 2
    spec = yaml.safe_load(FACTS_PATH.read_text())
    inputs = sorted(p.name for p in (run_dir / "inputs").glob("*"))
    if sorted(spec["documents"]) != inputs:
        print(f"{args.run_id} was not made from the two sample documents: {inputs}",
              file=sys.stderr)
        return 2

    run = Run(json.loads(extraction.read_text()))
    print(f"Run {args.run_id} — facts from {FACTS_PATH.relative_to(PROJECT_ROOT)}")
    ids: dict[str, list[int]] = {PASS: [], KNOWN: [], FAIL: []}
    for fact in spec["facts"]:
        verdict, detail = KINDS[fact["kind"]](run, fact)
        ids[verdict].append(fact["id"])
        print(f"{verdict:<5} {fact['id']}. {fact['name']}")
        print(f"         {detail}")
    print()

    def count(verdict: str, word: str) -> str:
        listed = f" ({', '.join(str(i) for i in ids[verdict])})" if ids[verdict] else ""
        return f"{len(ids[verdict])} {word}" + (listed if verdict != PASS else "")

    print(", ".join([count(PASS, "pass"), count(KNOWN, "known"), count(FAIL, "fail")]))
    return 1 if ids[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
