"""What report.pdf says, decided before anything is laid out.

The analyst reading report.pdf is checking the pipeline's work instead of
reading the RFP and protocol themselves, so the report has two jobs that pull
against each other: be short enough to scan, and leave out nothing a reviewer
would otherwise have found in the documents. This module does the shortening,
and does it in plain data so that "nothing left out" can be tested directly
(tests/render/test_report_model.py) rather than by scraping a PDF.

Shortening is only ever merging, never dropping:
- Entries for one variable and one part of the study become one row.
- Inside a row, entries whose values read the same become one value line
  carrying all their sources. Values that read differently stay separate lines,
  so a disagreement is never hidden by the merge.
- Machine spellings become words (`phase_1_2` -> "Phase 1/2", `false` -> "No").
- Documents are named, and a row lists each document's pages once.
Quotes and the adjudicator's full reasoning are not dropped either; they move
to the appendix (`evidence`, `Decision.explanation`).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from rfp_intake.domain.registry import FieldDef, GroupDef, Registry
from rfp_intake.domain.schemas import (
    Contradiction,
    FieldRecord,
    Provenance,
    ResolvedField,
    RunState,
)
from rfp_intake.gate import CONFIDENCE_CONFIRMED, DISMISSED_VERDICTS
from rfp_intake.normalize.scope import normalize_scope

# A variable with more rows than this is a schedule, not a fact: in run
# r-20260901-172918 visit frequency had 22 rows and timeline components 20,
# which as paragraphs filled three of fifteen pages. They get their own tables
# after the main one, and the main table points to them.
SCHEDULE_THRESHOLD = 8

# Confidence is printed on every row. It used to be printed only below 0.7, but
# the example of the report a customer praised was
# "Healthy volunteers included: No (Confirmed, 100% confidence)" — the number was
# part of what worked, and a column that appears only sometimes reads as an
# afterthought. The threshold that matters is CONFIDENCE_CONFIRMED, imported from
# rfp_intake.gate: at or above it GATE confirms a value, below it GATE sends the
# value for review, and the renderer colours the figure to match. Importing the
# number rather than restating it is what stops the report disagreeing with GATE.

# Longest a value may be in the disagreement and flagged tables before it is cut
# short. The full text is in the main table and the appendix.
DECISION_VALUE_CHARS = 110

# Most attention first. A row takes the status of its most-attention entry, so
# merging can only ever make a row look less certain, never more.
_STATUS_RANK = {"needs_review": 0, "not_found": 1, "not_specified": 2, "confirmed": 3}

# Verdicts that put a variable in the Disagreements table and mark its row
# "Disagreement" — the ones where the reviewer found something a person still has
# to settle. The counterpart is gate.DISMISSED_VERDICTS, imported above: a
# verdict in that tuple is the reviewer saying the documents agree, and those go
# to Appendix B instead (_dismissed). A verdict of None is in neither tuple —
# ADJUDICATE did not reach it, so the report cannot claim either way, and
# _review_reasons says the documents disagree without a number.
#
# "reconcilable" was in this tuple until 2026-10-07. Removing it is the change
# agreed after the review of run r-20261007-140411: see gate.DISMISSED_VERDICTS
# for why, and docs/JOURNAL.md.
NEEDS_DECISION = ("conflict",)

_KIND_CODES = {
    "rfp": "RFP",
    "protocol": "Protocol",
    "amendment": "Amendment",
    "soa": "SoA",
    "other": "Other document",
}


# --------------------------------------------------------------------------- #
# Data the PDF is drawn from
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DocRef:
    doc_id: str
    code: str  # What a table cell calls it: "RFP", "Amendment", "Protocol 2"
    filename: str | None
    pages: int | None


@dataclass
class ValueLine:
    """One distinct value within a row, with every place it was found."""

    items: list[str]  # Display text; more than one item is a list value
    status: str
    confidence: float
    sources: str  # "Amendment p.12, 42; RFP p.2"
    entries: list[ResolvedField] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "; ".join(self.items)


@dataclass
class Row:
    field_id: str
    label: str
    scope: str | None
    budget_driver: bool
    status: str
    values: list[ValueLine]
    verdict: str | None = None  # The adjudicated verdict on this row, if any
    decision_no: int | None = None  # Its number in the Disagreements section
    # Why this row is flagged, in plain words, one entry per rule that fired.
    # Empty unless status is "needs_review". See _review_reasons.
    reasons: list[str] = field(default_factory=list)
    # True on the stand-in row that points at a schedule table instead of
    # carrying a value. Its one value line is a sentence counting the schedule's
    # entries, so there is no confidence figure to print for it.
    is_pointer: bool = False
    # Rewordings moved to Appendix B rather than printed in the table. See
    # _fold_rewordings for exactly when that happens.
    folded: list[ValueLine] = field(default_factory=list)
    # Set only when this row's disagreement number belongs to ANOTHER variable,
    # through that variable's `follows_conflict_of` in config/fields.yaml: the
    # label of the variable the documents actually disagree about. The report
    # then names that variable instead of saying "Sources disagree", which on
    # run r-20261008-review2-run2 sent a reader to a disagreement about the
    # number of interim analyses looking for one about their timing.
    follows_label: str | None = None


@dataclass
class Schedule:
    """A variable with too many rows to sit in the main table."""

    field_id: str
    label: str
    budget_driver: bool
    rows: list[Row]


@dataclass
class GroupSection:
    group: GroupDef
    rows: list[Row]
    schedules: list[Schedule]


@dataclass
class Position:
    """One side of a disagreement: a value and where the documents say it."""

    text: str
    sources: str


@dataclass
class Decision:
    no: int
    field_id: str
    label: str
    scope: str | None
    verdict: str
    severity: str | None
    positions: list[Position]
    provisional: str | None  # The value precedence would use, if it picked one
    explanation: str | None
    signature: tuple[object, ...] = ()


@dataclass
class Dismissed:
    field_id: str
    label: str
    scope: str | None
    values: list[Position]
    # Why the reviewer decided the documents do not disagree, in ADJUDICATE's own
    # words — the same text a Decision carries. Printed since 2026-10-08: change 1
    # of the 2026-10-07 review moved nine variables out of the Disagreements
    # table, and they arrived here as bare values, so the report stopped saying
    # why 260 rather than 236 is the number to budget from.
    explanation: str = ""


@dataclass
class Evidence:
    field_id: str
    label: str
    scope: str | None
    value: str
    sources: str
    confidence: float
    quote: str | None
    derived_from: list[str]
    notes: str | None


@dataclass
class RemovedText:
    """One passage MARK_OTHER_STUDY took out, for Appendix C.

    Stage 4 of docs/PLAN_2026-10-02.md. This is in the report, unlike the sections
    SET_ASIDE_SECTIONS skips, and the difference is who would want to argue with
    it: skipping the references is housekeeping, while deciding that a passage
    describes a different study is a judgement a reviewer may want to overturn.
    They cannot overturn what they cannot see.

    `extract` is the removed text's opening, not all of it — the full text is in
    `extraction.json` under `removed_passages`. A reviewer needs enough to
    recognise the passage and find it on the page; reprinting several pages of
    another study's results would make the report longer than the thing it saved.
    """

    doc_code: str
    heading: str
    pages: str
    verdict: str
    reason: str
    extract: str


@dataclass(frozen=True)
class Term:
    """One line of the report's "Words used in this report" list.

    The wording comes from `plain` in config/fields.yaml, so a clinical reviewer
    corrects it by editing YAML rather than Python. A field with no `plain` is
    absent from this list: the report explains the terms someone chose to explain
    and says nothing about the rest.
    """

    label: str
    plain: str


@dataclass
class ReportModel:
    run_id: str
    registry_version: str
    documents: list[DocRef]
    groups: list[GroupSection]
    decisions: list[Decision]
    dismissed: list[Dismissed]
    evidence: list[Evidence]
    glossary: list[Term]
    variables_total: int
    variables_found: int
    # Appendix C. Defaulted, because every other field here is positional and a
    # report rebuilt by older code — scripts/rerender_report.py against an
    # extraction.json written before 2026-10-02 — has nothing to put in it.
    removed: list[RemovedText] = field(default_factory=list)

    def all_rows(self) -> list[Row]:
        rows: list[Row] = []
        for g in self.groups:
            rows += g.rows
            for s in g.schedules:
                rows += s.rows
        return rows

    def review_rows(self) -> list[Row]:
        """Rows needing a look that are not already a page-1 decision."""
        return [
            r for r in self.all_rows()
            if r.status == "needs_review" and r.decision_no is None
        ]


# --------------------------------------------------------------------------- #
# Values into words
# --------------------------------------------------------------------------- #

_ENUM_WORDS = {
    "true": "Yes",
    "false": "No",
    "likely": "Likely",
    "not_specified": "Not specified",
    "none_found": "None found",
    "other": "Other",
    "open_label": "Open-label",
    "single_blind": "Single-blind",
    "double_blind": "Double-blind",
    "triple_blind": "Triple-blind",
    "infusion_iv": "IV infusion",
    "injection_sc": "Subcutaneous injection",
    "injection_im": "Intramuscular injection",
    "pk_pd_sampling": "PK/PD sampling",
    "ecgs": "ECGs",
}
_ACRONYMS = {"ip", "iv", "sc", "im", "pk", "pd", "ecg", "crf", "sdv", "sdr", "soa", "rfp", "soc"}
_ENUM_TOKEN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_PHASE = re.compile(r"^phase_(\d)(?:_(\d))?$")


def humanize_enum(token: str) -> str:
    """`phase_1_2` -> "Phase 1/2", `time_based` -> "Time-based"."""
    if token in _ENUM_WORDS:
        return _ENUM_WORDS[token]
    m = _PHASE.match(token)
    if m:
        return f"Phase {m.group(1)}/{m.group(2)}" if m.group(2) else f"Phase {m.group(1)}"
    words = [w.upper() if w in _ACRONYMS else w for w in token.split("_")]
    text = " ".join(words).replace(" based", "-based")
    return text[:1].upper() + text[1:]


def _is_enum(field_def: FieldDef | None) -> bool:
    return field_def is not None and "enum" in field_def.type


def format_value(value: Any, field_def: FieldDef | None) -> list[str]:
    """A stored value as the items an analyst reads. Never returns []."""
    if value is None:
        return ["—"]
    if isinstance(value, bool):
        return ["Yes" if value else "No"]
    if isinstance(value, int):
        return [f"{value:,}"]
    if isinstance(value, float):
        return [f"{value:,.10g}"]
    if isinstance(value, str):
        text = value.strip()
        # Yes/no answers are spelled true/false whatever the field's type.
        if text in ("true", "false", "not_specified") or (
            _is_enum(field_def) and _ENUM_TOKEN.match(text)
        ):
            return [humanize_enum(text)]
        return [text or "—"]
    if isinstance(value, dict):
        if set(value) == {"n", "unit"}:
            n = format_value(value["n"], None)[0]
            said = f"{n} {value['unit']}"
            if field_def is not None and "frequency" in field_def.id:
                return [f"Every {said}"]
            return [said]
        parts = [f"{humanize_enum(str(k))}: {format_value(v, None)[0]}" for k, v in value.items()]
        return ["; ".join(parts)]
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for v in value:
            items += format_value(v, field_def)
        return items or ["—"]
    return [str(value)]


def _same_key(items: list[str]) -> str:
    """Two values read the same if they differ only in case and spacing."""
    return re.sub(r"\s+", " ", " | ".join(items)).strip().casefold()


# --------------------------------------------------------------------------- #
# Page references
# --------------------------------------------------------------------------- #
#
# Change 7 of the 2026-10-07 review. A page reference names the page's position
# in the PDF file, because that is what gets a reader to the page in a viewer.
# The protocol amendment's own printed numbers are two lower, so "Amendment
# p.11" sent Oliver Zarate to the wrong page when he looked for the printed 11.
# Where the two numbers differ the reference now shows both; where they agree,
# or no printed number was read, it shows the file position alone as before.
# This changes how a reference is written, not where one appears.


def format_page_ref(page: int, printed: str | None) -> str:
    """One page: "11" on its own, or "11 (printed p.9)" when the two differ."""
    if printed is None or printed == str(page):
        return str(page)
    return f"{page} (printed p.{printed})"


def format_page_range(
    page_start: int,
    page_end: int,
    printed_start: str | None,
    printed_end: str | None,
) -> str:
    """A span of pages: "11 (printed p.9)", or "39-41 (printed p.37-39)".

    A span whose two ends are the same page is written as one page. Where only
    one end has a printed number, the printed span is filled in from what is
    known rather than guessed: a missing start falls back to its file position,
    and a missing end leaves the printed start standing alone. Nothing is
    inferred from the offset between the two numbers — a document can restart
    its numbering anywhere.
    """
    if page_start == page_end:
        return format_page_ref(page_start, printed_start or printed_end)

    span = f"{page_start}-{page_end}"
    if printed_end is not None and printed_end != str(page_end):
        return f"{span} (printed p.{printed_start or page_start}-{printed_end})"
    if printed_start is not None and printed_start != str(page_start):
        return f"{span} (printed p.{printed_start})"
    return span


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #


class _Docs:
    """Names each document once, and formats page references with those names."""

    def __init__(self, state: RunState) -> None:
        self._refs: dict[str, DocRef] = {}
        kinds: dict[str, str] = {}
        for d in state.documents:
            kinds[d.id] = d.kind or "other"
        # Provenance carries a kind even when state.documents is empty, which
        # is the case when a report is rebuilt from extraction.json.
        for p in _all_provenance(state):
            kinds.setdefault(p.doc_id, p.doc_kind)

        by_kind: dict[str, list[str]] = defaultdict(list)
        for doc_id, kind in kinds.items():
            by_kind[kind].append(doc_id)

        docs = {d.id: d for d in state.documents}
        for kind, ids in by_kind.items():
            base = _KIND_CODES.get(kind, kind.title())
            for i, doc_id in enumerate(ids, 1):
                code = base if len(ids) == 1 else f"{base} {i}"
                found = docs.get(doc_id)
                self._refs[doc_id] = DocRef(
                    doc_id=doc_id,
                    code=code,
                    filename=PurePath(found.path).name if found else None,
                    pages=(found.pages or None) if found else None,
                )

    @property
    def refs(self) -> list[DocRef]:
        return list(self._refs.values())

    def code(self, doc_id: str) -> str:
        ref = self._refs.get(doc_id)
        return ref.code if ref else doc_id

    def name_ids(self, text: str) -> str:
        """Swap document ids in model-written text for the names the report uses.
        Only the ids change; the adjudicator's words are otherwise left as written."""
        for doc_id, ref in self._refs.items():
            text = re.sub(rf"\b{re.escape(doc_id)}\b", ref.code, text, flags=re.IGNORECASE)
        return text

    def pages(self, provenance: list[Provenance]) -> str:
        """Each document once with its pages in order: "Amendment p.12, 42; RFP p.2".

        A page whose printed number differs from its position in the file shows
        both — "Amendment p.11 (printed p.9), 18 (printed p.16)". See
        `format_page_ref`.
        """
        pages: dict[str, dict[int, str | None]] = {}
        for p in provenance:
            printed = pages.setdefault(p.doc_id, {})
            # Several records can cite one page. They come from the same
            # document, so any printed number among them is that page's number;
            # the first one found stands.
            if printed.get(p.page) is None:
                printed[p.page] = p.printed_page
        return "; ".join(
            f"{self.code(doc_id)} p."
            + ", ".join(format_page_ref(n, ns[n]) for n in sorted(ns))
            for doc_id, ns in pages.items()
        )


def _all_provenance(state: RunState) -> list[Provenance]:
    out: list[Provenance] = []
    for rf in state.resolved:
        out += rf.sources
    for c in state.contradictions:
        out += [r.provenance for r in c.records]
    return out


# --------------------------------------------------------------------------- #
# Building the model
# --------------------------------------------------------------------------- #


def build_report_model(state: RunState, registry: Registry) -> ReportModel:
    docs = _Docs(state)
    field_by_id = {f.id: f for f in registry.fields}

    decisions = _decisions(state, field_by_id, docs)
    decision_no = {d.signature: d.no for d in decisions}

    by_field: dict[str, list[ResolvedField]] = defaultdict(list)
    for rf in state.resolved:
        by_field[rf.field_id].append(rf)

    sections: list[GroupSection] = []
    found = 0
    for group in registry.groups:
        rows: list[Row] = []
        schedules: list[Schedule] = []
        for fd in (f for f in registry.fields if f.group == group.id):
            leader = field_by_id.get(fd.follows_conflict_of or "")
            field_rows = _rows_for(
                fd, by_field.get(fd.id, []), docs, decision_no,
                leader.label if leader else None,
            )
            if any(r.status != "not_found" for r in field_rows):
                found += 1
            if len(field_rows) > SCHEDULE_THRESHOLD:
                schedules.append(Schedule(fd.id, fd.label, fd.budget_driver, field_rows))
                rows.append(_pointer_row(fd, field_rows))
            else:
                rows += field_rows
        sections.append(GroupSection(group, rows, schedules))

    # Entries for a field the registry no longer defines still get printed:
    # a report must not quietly lose a value because config moved on.
    unknown = [fid for fid in by_field if fid not in field_by_id]
    if unknown:
        rows = []
        for fid in unknown:
            fd = FieldDef(id=fid, group="_unregistered", label=fid, type="text")
            rows += _rows_for(fd, by_field[fid], docs, decision_no)
        sections.append(GroupSection(
            GroupDef(id="_unregistered", label="Not in the current field list"), rows, [],
        ))

    return ReportModel(
        run_id=state.run_id,
        registry_version=registry.registry_version,
        documents=docs.refs,
        groups=sections,
        decisions=decisions,
        dismissed=_dismissed(state, field_by_id, docs),
        evidence=_evidence(state, field_by_id, docs),
        glossary=[Term(f.label, f.plain) for f in registry.fields if f.plain],
        variables_total=len(registry.fields),
        variables_found=found,
        removed=_removed(state, docs),
    )


# How much of a removed passage Appendix C prints. One or two sentences is enough
# to recognise which passage went and to find it on the page named beside it; the
# whole text is in extraction.json.
REMOVED_EXTRACT_CHARS = 300


def _removed(state: RunState, docs: _Docs) -> list[RemovedText]:
    """Appendix C: what MARK_OTHER_STUDY took out, in document and page order."""
    ordered = sorted(state.removed_passages, key=lambda p: (p.doc_id, p.page_start, p.heading))
    return [
        RemovedText(
            doc_code=docs.code(p.doc_id),
            heading=p.heading,
            pages="p." + format_page_range(
                p.page_start, p.page_end, p.printed_page_start, p.printed_page_end
            ),
            verdict="whole section" if p.verdict == "other_study" else "part of the section",
            reason=p.reason,
            extract=_shorten(p.text, REMOVED_EXTRACT_CHARS),
        )
        for p in ordered
    ]


def _shorten(text: str, limit: int) -> str:
    """One line of `text`, cut to `limit` characters.

    Line breaks are collapsed because a removed passage comes straight out of a
    PDF's page text, where a sentence is broken wherever the line ended.
    """
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _rows_for(
    fd: FieldDef,
    entries: list[ResolvedField],
    docs: _Docs,
    decision_no: dict[tuple[object, ...], int],
    follows_label: str | None = None,
) -> list[Row]:
    if not entries:
        return [Row(
            field_id=fd.id, label=fd.label, scope=None, budget_driver=fd.budget_driver,
            status="not_found",
            values=[ValueLine(items=["—"], status="not_found", confidence=0.0, sources="")],
        )]

    # One row per part of the study, using the same scope rule RECONCILE uses,
    # so the report never merges two rows the pipeline kept apart.
    by_scope: dict[str | None, list[ResolvedField]] = {}
    for rf in entries:
        by_scope.setdefault(normalize_scope(rf.scope), []).append(rf)

    rows = []
    for scope_key, group in by_scope.items():
        values: dict[str, ValueLine] = {}
        for rf in group:
            items = format_value(rf.value, fd) if rf.status not in ("not_found",) else ["—"]
            key = _same_key(items)
            line = values.get(key)
            if line is None:
                values[key] = ValueLine(
                    items=items, status=rf.status, confidence=rf.confidence,
                    sources="", entries=[rf],
                )
            else:
                line.entries.append(rf)
                line.confidence = min(line.confidence, rf.confidence)
                if _STATUS_RANK.get(rf.status, 0) < _STATUS_RANK.get(line.status, 0):
                    line.status = rf.status
        for line in values.values():
            line.sources = docs.pages([p for rf in line.entries for p in _stating(rf, fd)])

        lines = list(values.values())
        verdicts = [rf.contradiction.verdict for rf in group if rf.contradiction]
        verdict = next((v for v in verdicts if v in NEEDS_DECISION), None) or (
            verdicts[0] if verdicts else None
        )
        no = next(
            (decision_no[_signature(rf.contradiction)] for rf in group
             if rf.contradiction and _signature(rf.contradiction) in decision_no),
            None,
        )
        # GATE hands a `follows_conflict_of` field the LEADER's contradiction, so
        # the contradiction on this row can carry another variable's field_id.
        # Where it does, the report names that variable.
        borrowed = follows_label if any(
            rf.contradiction is not None and rf.contradiction.field_id != fd.id
            for rf in group
        ) else None
        rows.append(_fold_rewordings(fd, Row(
            field_id=fd.id,
            label=fd.label,
            scope=group[0].scope if scope_key is not None else None,
            budget_driver=fd.budget_driver,
            status=min((ln.status for ln in lines), key=lambda s: _STATUS_RANK.get(s, 0)),
            values=lines,
            verdict=verdict,
            decision_no=no,
            reasons=_review_reasons(fd, group, no, borrowed),
            follows_label=borrowed,
        )))
    return rows


# The verdicts on which ADJUDICATE writes ONE row for a whole disagreement:
# adjudicate/_handle_reconcilable and _handle_conflict give that row the chosen
# value and quote, but `sources` naming every record's page, whichever value the
# record held. A "not_a_conflict" verdict gets one row per record instead, each
# citing only its own page, so its rows need none of what follows.
_ONE_ROW_VERDICTS = ("conflict", "reconcilable")


def _own_disagreement(rf: ResolvedField) -> Contradiction | None:
    """The disagreement this entry's value was chosen from, when ADJUDICATE wrote
    it as one row. Not a disagreement borrowed through `follows_conflict_of`
    (interim.timing carries interim.count's): those records are about another
    variable, so they say nothing about which pages state this one's value."""
    c = rf.contradiction
    if c is None or c.field_id != rf.field_id or c.verdict not in _ONE_ROW_VERDICTS:
        return None
    return c


def _record_items(record: FieldRecord, fd: FieldDef | None) -> list[str]:
    return format_value(record.value if record.value is not None else record.raw_value, fd)


def _stating(rf: ResolvedField, fd: FieldDef | None) -> list[Provenance]:
    """The pages that state this entry's value — the only pages printed beside it.

    Fix 2 of the 2026-10-09 brief. Run r-20261009-225414 printed "0" for the
    number of interim analyses beside "Amendment p.98; RFP p.4", and RFP p.4
    says 1: the row cited every record of the disagreement, because that is
    what ADJUDICATE puts in `sources` (see _ONE_ROW_VERDICTS). Here only the
    pages of records whose value reads the same as the entry's are kept. A row
    where no value won (value None, printed "—") keeps none, since no page says
    "—". The other side's page is not lost: every record has its own line in
    Appendix A (_evidence), and a conflict's positions are in the Disagreements
    table.
    """
    c = _own_disagreement(rf)
    if c is None:
        return list(rf.sources)
    if rf.value is None:
        return []
    want = _same_key(format_value(rf.value, fd))
    pages = {
        (r.provenance.doc_id, r.provenance.page)
        for r in c.records if _same_key(_record_items(r, fd)) == want
    }
    return [p for p in rf.sources if (p.doc_id, p.page) in pages]


def _fold_rewordings(fd: FieldDef, row: Row) -> Row:
    """Print one wording of a free-text answer; move the rest to Appendix B.

    Only when every one of these holds:
    - the adjudicator checked the wordings and said they do not disagree;
    - the field is free text, where rewordings are long (five paraphrases of
      the study population took most of a page in run r-20260901-172918);
    - the field is not a budget driver.
    The last is the guard against the adjudicator being wrong. A budget driver
    always shows every value, so a model's "not a conflict" can never hide a
    number the budget depends on. Enum and number fields are short enough that
    folding them would save nothing, so they always show every value too.
    """
    if (
        row.verdict != "not_a_conflict"
        or fd.type != "text"
        or fd.budget_driver
        or len(row.values) < 2
    ):
        return row
    shown = max(row.values, key=lambda v: len(v.entries))
    row.folded = [v for v in row.values if v is not shown]
    row.values = [shown]
    return row


# Why a value is flagged for review, in the words the report prints. These are
# a replay of the five rules in rfp_intake/gate/__init__.py: GATE decides the
# status and stores only the word "needs_review", so the reason has to be worked
# out again here from the same inputs GATE used. The customer's complaint was
# that the old report listed flagged variable names with no reason at all.
#
# Each one is a short phrase, not a sentence, because the same reason lands on
# many rows — six "Timeline components" rows in r-20260923-131601 share one of
# them. A 27-word sentence repeated six times in one column is the repetition the
# same customer asked us to cut. What each phrase means is spelled out once, in
# the lead paragraph of pdf_renderer._flagged, and the phrases below are the
# keys that paragraph explains: changing one means changing that paragraph too.
REASON_DERIVED = "Worked out by the tool, not read from a document."
REASON_DISAGREE = "The documents disagree"  # "— see Disagreement 3." is appended
REASON_DISMISSED_DISAGREEMENT = "Budget driver; the documents word it differently."
REASON_MANY_VALUES = "Budget driver holding more than one value."
REASON_LOW_CONFIDENCE = f"Confidence below {CONFIDENCE_CONFIRMED:.0%}."
REASON_IN_SCHEDULE = "Some entries in the schedule need review."
# Must be unreachable on a real run. Printed rather than guessed at if some
# future change to GATE flags a value by a rule this function does not know:
# a report that cannot explain a flag says so instead of inventing a reason.
# No test asserts it is unreachable yet — that assertion is still to be written
# (see tests/render/test_report_model.py::assert_nothing_lost), so today the
# only evidence is that it appears in none of the runs under runs/.
REASON_UNEXPLAINED = (
    "Flagged by the review checks; the reason is recorded in extraction.json."
)


def _review_reasons(
    fd: FieldDef,
    entries: list[ResolvedField],
    decision_no: int | None,
    follows_label: str | None = None,
) -> list[str]:
    """Why each flagged entry in this row is flagged, de-duplicated, in GATE's order.

    One entry can only be flagged by one rule, because _gate_field returns at the
    first rule that matches — so the branches below are checked in that same order
    and the first match wins. A row holding several entries can carry several
    reasons, which is why the result is a list.
    """
    out: list[str] = []
    for rf in entries:
        if rf.status != "needs_review":
            continue
        if rf.derived_from:
            reason = REASON_DERIVED
        elif (
            rf.contradiction is not None
            and rf.contradiction.verdict not in DISMISSED_VERDICTS
        ):
            reason = REASON_DISAGREE + (
                f" on {follows_label}" if follows_label else ""
            ) + (
                f" — see Disagreement {decision_no}." if decision_no is not None else "."
            )
        elif rf.contradiction is not None and fd.budget_driver:
            reason = REASON_DISMISSED_DISAGREEMENT
        elif fd.budget_driver and isinstance(rf.value, list) and len(rf.value) > 1:
            reason = REASON_MANY_VALUES
        elif rf.confidence < CONFIDENCE_CONFIRMED:
            reason = REASON_LOW_CONFIDENCE
        else:
            reason = REASON_UNEXPLAINED
        if reason not in out:
            out.append(reason)
    return out


def _pointer_row(fd: FieldDef, rows: list[Row]) -> Row:
    review = sum(1 for r in rows if r.status == "needs_review")
    note = f"{len(rows)} entries — see Schedules"
    if review:
        note += f" ({review} to review)"
    return Row(
        field_id=fd.id, label=fd.label, scope=None, budget_driver=fd.budget_driver,
        status=min((r.status for r in rows), key=lambda s: _STATUS_RANK.get(s, 0)),
        values=[ValueLine(items=[note], status="confirmed", confidence=1.0, sources="")],
        reasons=[REASON_IN_SCHEDULE] if review else [],
        is_pointer=True,
    )


def _label(field_by_id: dict[str, FieldDef], field_id: str) -> str:
    fd = field_by_id.get(field_id)
    return fd.label if fd else field_id


def _decisions(
    state: RunState, field_by_id: dict[str, FieldDef], docs: _Docs
) -> list[Decision]:
    out: list[Decision] = []
    for c in state.contradictions:
        if c.verdict not in NEEDS_DECISION:
            continue
        fd = field_by_id.get(c.field_id)
        out.append(Decision(
            no=len(out) + 1,
            field_id=c.field_id,
            label=_label(field_by_id, c.field_id),
            scope=c.records[0].scope if c.records else None,
            verdict=c.verdict,
            severity=c.severity,
            positions=_positions(c, fd, docs),
            provisional=_provisional(c, fd, docs),
            explanation=docs.name_ids(c.explanation) if c.explanation else None,
            signature=_signature(c),
        ))
    # High severity first. Since 2026-10-07 only "conflict" reaches this list, so
    # the first sort key no longer separates two verdicts; it is kept so the order
    # does not change if another verdict is ever added to NEEDS_DECISION.
    sev = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda d: (d.verdict != "conflict", sev.get(d.severity or "", 3)))
    for i, d in enumerate(out, 1):
        d.no = i
    return out


def _signature(c: Contradiction) -> tuple[object, ...]:
    """Identifies one disagreement. ResolvedField.contradiction is a copy of the
    entry in state.contradictions, so identity comparison would not find it."""
    return (c.field_id, c.verdict, tuple(
        (r.provenance.doc_id, r.provenance.page, r.raw_value) for r in c.records
    ))


def _positions(c: Contradiction, fd: FieldDef | None, docs: _Docs) -> list[Position]:
    grouped: dict[str, tuple[str, list[Provenance]]] = {}
    for r in c.records:
        items = format_value(r.value if r.value is not None else r.raw_value, fd)
        text = "; ".join(items)
        key = _same_key(items)
        if key not in grouped:
            grouped[key] = (text, [])
        grouped[key][1].append(r.provenance)
    return [Position(text=text, sources=docs.pages(provs)) for text, provs in grouped.values()]


def _provisional(c: Contradiction, fd: FieldDef | None, docs: _Docs) -> str | None:
    if c.resolved_value is None:
        return None
    text = "; ".join(format_value(c.resolved_value, fd))
    if c.winning_doc_id:
        text += f" (from {docs.code(c.winning_doc_id)})"
    return text


def _dismissed(
    state: RunState, field_by_id: dict[str, FieldDef], docs: _Docs
) -> list[Dismissed]:
    """Appendix B's dismissed list: every adjudicated verdict except "conflict".

    That is every verdict in `gate.DISMISSED_VERDICTS` today, because
    `Contradiction.verdict` allows only "conflict", "reconcilable",
    "not_a_conflict" and None. The loop below is written as the complement of
    `NEEDS_DECISION` rather than as a test against `DISMISSED_VERDICTS`, so a
    fourth verdict added to that Literal and to neither tuple would be printed
    here as checked and dismissed while GATE flagged its field for review.
    Reported to Oliver on 2026-10-08 and deliberately not changed: the two are
    equivalent today, and which verdicts reach this appendix is change 1's
    subject, not something to alter unasked.

    Since 2026-10-07 the list includes "reconcilable", which used to be in the
    Disagreements table. The values are still printed, and still with the pages
    they came from, so moving a reconcilable verdict out of the main table does
    not take a wording out of the report — it only stops the report asking for a
    decision the reviewer already made.

    The reviewer's explanation comes with them, added 2026-10-08. Without it the
    nine variables change 1 moved here read as two bare numbers: the report
    usability review of run r-20261008-review2-run3 found
    `Total number of patients / subjects` showing "260 / 236" with nothing to say
    that 236 is the power calculation's minimum and 260 the enrolment the budget
    is built on. ADJUDICATE wrote that sentence; it was being thrown away.
    """
    out = []
    for c in state.contradictions:
        if c.verdict is None or c.verdict in NEEDS_DECISION:
            continue
        fd = field_by_id.get(c.field_id)
        out.append(Dismissed(
            field_id=c.field_id,
            label=_label(field_by_id, c.field_id),
            scope=c.records[0].scope if c.records else None,
            values=_positions(c, fd, docs),
            # Through `name_ids`, like the Disagreements explanations in
            # `_decisions`: ADJUDICATE's prompt prints `doc_id=` for every
            # candidate and asks it to name the document, so its sentences carry
            # internal ids such as `doc-2ea4cd79` routinely. On the first render
            # of this line, Appendix B of r-20261008-review2-run3 showed four
            # of them where the Disagreements table shows "Amendment" and "RFP".
            explanation=docs.name_ids(c.explanation) if c.explanation else "",
        ))
    return out


def _evidence(
    state: RunState, field_by_id: dict[str, FieldDef], docs: _Docs
) -> list[Evidence]:
    """Every entry's quote, once. Registry order, so it reads like the main table.

    An entry chosen from a disagreement (_own_disagreement) is listed as its
    records instead, each with its own value, page and quote. Before fix 2 of
    the 2026-10-09 brief only the chosen value's quote was here, beside every
    record's page, so in run r-20261009-225414 the RFP's "1" interim analysis
    had no quote anywhere in the appendix and its page sat beside the
    amendment's "0". The entry itself is then listed only if no record already
    shows its value and quote. Usually one does; on three of the 29 runs under
    runs/ on 2026-10-11 a conflict's value and quote came from different records
    of one document, because adjudicate/_handle_conflict looks the quote up by
    document, and there the entry is listed as well.
    """
    order = {f.id: i for i, f in enumerate(field_by_id.values())}
    seen: dict[tuple[str, str | None, str, str, str], Evidence] = {}
    out = []

    def add(e: Evidence) -> None:
        # The pages are in the key so that two records with one wording on
        # different pages are both listed. Two that are the same line keep the
        # lower confidence, the rule the main table follows (_rows_for).
        key = (e.field_id, normalize_scope(e.scope), e.value.casefold(),
               (e.quote or "").strip(), e.sources)
        if key in seen:
            seen[key].confidence = min(seen[key].confidence, e.confidence)
        else:
            seen[key] = e
            out.append(e)

    for rf in sorted(state.resolved, key=lambda r: order.get(r.field_id, len(order))):
        fd = field_by_id.get(rf.field_id)
        label = _label(field_by_id, rf.field_id)
        value = "; ".join(format_value(rf.value, fd))
        c = _own_disagreement(rf)
        shown: set[tuple[str, str]] = set()
        for r in c.records if c is not None else []:
            said = "; ".join(_record_items(r, fd))
            shown.add((said.casefold(), r.quote.strip()))
            add(Evidence(
                field_id=rf.field_id,
                label=label,
                scope=rf.scope,
                value=said,
                sources=docs.pages([r.provenance]),
                confidence=r.confidence,
                quote=r.quote,
                derived_from=[],
                # ADJUDICATE's explanation, which is all `notes` holds on these
                # rows, is printed in Appendix B; see the comment below.
                notes=None,
            ))
        if not rf.quote and not rf.derived_from:
            continue
        if (value.casefold(), (rf.quote or "").strip()) in shown:
            continue
        add(Evidence(
            field_id=rf.field_id,
            label=label,
            scope=rf.scope,
            value=value,
            sources=docs.pages(_stating(rf, fd)),
            confidence=rf.confidence,
            quote=rf.quote,
            derived_from=rf.derived_from,
            # A note is printed for a value the pipeline worked out — a derived
            # field, or one a rule in config/fields.yaml decided (see
            # reconcile/rules.py), where the note is the only thing that explains
            # why the quote beside it answers this variable. ADJUDICATE's
            # explanation is also kept in `notes`, and is not printed here: it is
            # printed in full in Appendix B, under the disagreement it belongs to.
            #
            # `rf.contradiction is None` reads broader than those two sources,
            # and the plan-conformance reviewer read it that way on 2026-10-08:
            # `config/fields.yaml` asks the extraction model for `notes` on some
            # fields, `interim.count` among them. Those notes never arrive here.
            # DERIVE, ADJUDICATE and reconcile/rules.py are the only three
            # writers of `ResolvedField.notes`; the model's note is written to
            # `FieldRecord.notes`, and nothing in RECONCILE copies it across —
            # the four `ResolvedField` calls in reconcile/__init__.py pass no
            # `notes` at all, and the fifth, reconcile/rules.py:102, passes the
            # rule's own `because` sentence. Pinned by
            # tests/reconcile/test_reconcile.py::TestARecordsOwnNoteStaysOnTheRecord,
            # which covers the three of those five that have a record to copy a
            # note from, so if that ever changes a test says so rather than a
            # report.
            notes=rf.notes if (rf.derived_from or rf.contradiction is None) else None,
        ))
    return out
