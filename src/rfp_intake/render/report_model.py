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
from rfp_intake.domain.schemas import Contradiction, Provenance, ResolvedField, RunState
from rfp_intake.gate import CONFIDENCE_CONFIRMED
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

NEEDS_DECISION = ("conflict", "reconcilable")

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
        """Each document once with its pages in order: "Amendment p.12, 42; RFP p.2"."""
        pages: dict[str, set[int]] = {}
        for p in provenance:
            pages.setdefault(p.doc_id, set()).add(p.page)
        return "; ".join(
            f"{self.code(doc_id)} p.{', '.join(str(n) for n in sorted(ns))}"
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
            field_rows = _rows_for(fd, by_field.get(fd.id, []), docs, decision_no)
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
            pages=(
                f"p.{p.page_start}"
                if p.page_start == p.page_end
                else f"p.{p.page_start}-{p.page_end}"
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
            line.sources = docs.pages([p for rf in line.entries for p in rf.sources])

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
        rows.append(_fold_rewordings(fd, Row(
            field_id=fd.id,
            label=fd.label,
            scope=group[0].scope if scope_key is not None else None,
            budget_driver=fd.budget_driver,
            status=min((ln.status for ln in lines), key=lambda s: _STATUS_RANK.get(s, 0)),
            values=lines,
            verdict=verdict,
            decision_no=no,
            reasons=_review_reasons(fd, group, no),
        )))
    return rows


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
    fd: FieldDef, entries: list[ResolvedField], decision_no: int | None
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
        elif rf.contradiction is not None and rf.contradiction.verdict != "not_a_conflict":
            reason = REASON_DISAGREE + (
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
    # Conflicts before reconcilable ones, high severity first within each.
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
        ))
    return out


def _evidence(
    state: RunState, field_by_id: dict[str, FieldDef], docs: _Docs
) -> list[Evidence]:
    """Every entry's quote, once. Registry order, so it reads like the main table."""
    order = {f.id: i for i, f in enumerate(field_by_id.values())}
    seen: set[tuple[str, str | None, str, str]] = set()
    out = []
    for rf in sorted(state.resolved, key=lambda r: order.get(r.field_id, len(order))):
        if not rf.quote and not rf.derived_from:
            continue
        fd = field_by_id.get(rf.field_id)
        value = "; ".join(format_value(rf.value, fd))
        key = (rf.field_id, normalize_scope(rf.scope), value.casefold(), (rf.quote or "").strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(Evidence(
            field_id=rf.field_id,
            label=_label(field_by_id, rf.field_id),
            scope=rf.scope,
            value=value,
            sources=docs.pages(rf.sources),
            confidence=rf.confidence,
            quote=rf.quote,
            derived_from=rf.derived_from,
            notes=rf.notes if rf.derived_from else None,
        ))
    return out
