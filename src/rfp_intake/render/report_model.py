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
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import PurePath
from typing import Any

from rfp_intake.domain.registry import (
    Block,
    BlockLine,
    BreakdownDisplay,
    EntriesDisplay,
    EntryKey,
    FieldDef,
    GroupDef,
    PeriodsDisplay,
    Registry,
    ReportWording,
    ScopesDisplay,
    placeholders,
    scope_has_phrase,
)
from rfp_intake.domain.schemas import (
    Contradiction,
    FieldRecord,
    Provenance,
    ResolvedField,
    RunState,
)
from rfp_intake.gate import CONFIDENCE_CONFIRMED, DISMISSED_VERDICTS
from rfp_intake.normalize.scope import normalize_scope
from rfp_intake.reconcile.entries import MergedEntry, group_entries, merge_entry, same_key

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
# A value that answers "the document does not say", as reconcile/__init__.py's
# _SAYS_NOTHING reads it.
_NOT_SPECIFIED = frozenset({"not_specified", "not specified"})

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
    # True when every entry was chosen from the row's own numbered disagreement,
    # whose sides (Row.sides) then say this value and where. The renderer prints
    # the sides instead of this line; the line stays, so the entries keep a home.
    on_sides: bool = False

    @property
    def text(self) -> str:
        return "; ".join(self.items)


@dataclass
class Side:
    """One document's side of a disagreement, printed on the variable's own row.

    Fix 2 of the 2026-10-12 brief: a row marked Disagreement used to show only
    the provisional value, so a reader saw "0" interim analyses and had to turn
    to the Disagreements table to learn the RFP says 1.
    """

    sources: str  # "Amendment p.15, 24"
    text: str
    # The passage's first sentence, when the side rests on one passage. Where a
    # document states its side in several different passages there is no one
    # quote to print; each is in Appendix A with its page.
    quote: str | None
    quote_cut: bool = False  # the sentence was longer, and is cut short


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
    # The two sides of this row's own numbered disagreement, one per document,
    # and the line under them: the value precedence chose, or that none was
    # chosen. Fix 2 of the 2026-10-12 brief. See _sides.
    sides: list[Side] = field(default_factory=list)
    decision_line: str | None = None
    # The disagreement `sides` were drawn from, so a line pointing at this row
    # (a `periods` display's see_also) names the same one.
    disagreement: Contradiction | None = None
    # A row following another variable's disagreement (follows_label): its
    # status, report_wording.depends_on, and the sentences of the field's
    # `follow_wording` when only some documents in that disagreement give this
    # row a value. The value lines stay in `values`; these sentences say them.
    depends_on: str | None = None
    follow_lines: list[str] = field(default_factory=list)
    # A `scopes` display (Display in domain/registry.py): one row whose parts
    # are the variable's rows, one per scope, each under its heading. `parts` is
    # set on that one row and `heading` on each part. Fix 6 of the 2026-10-12
    # brief.
    parts: list[Row] = field(default_factory=list)
    heading: str | None = None
    # A derived row (`derived: true` in config/fields.yaml), fix 8 of the
    # 2026-10-12 brief: its status in `report_wording.derived_status`, and under
    # its value the rubric's reasons in plain words and the pages they rest on,
    # each already in that wording. See DERIVE's ResolvedField.why.
    derived_status: str | None = None
    why: str | None = None
    evidence_pages: str | None = None
    # A group's `block` (Block in domain/registry.py), fix 9 of the same brief:
    # one row for the variables the block replaces, each kept in `members` so
    # the review list still sees it, and the lines printed under its value,
    # each already in report_wording's words.
    members: list[Row] = field(default_factory=list)
    block_lines: list[BlockText] = field(default_factory=list)


@dataclass
class BlockText:
    """One line of a block, and how far it is indented under the line above."""

    text: str
    indent: int = 0


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
    # Which side of a numbered Disagreement this passage is on, in the wording of
    # `report_wording` in config/fields.yaml. None for a passage that is not
    # behind one. CLAUDE.md open item 3, 2026-10-12.
    marker: str | None = None


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
        """Every row with a value, a row of a `scopes` display as its parts and
        a block as the rows it replaced."""
        rows: list[Row] = []
        for g in self.groups:
            for r in g.rows:
                rows += r.parts or r.members or [r]
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
        if field_def is not None and text in field_def.value_words:
            return [field_def.value_words[text]]
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

    # Every variable's rows first: a block can quote a variable of another group.
    rows_by_field: dict[str, list[Row]] = {}
    for fd in registry.fields:
        leader = field_by_id.get(fd.follows_conflict_of or "")
        rows_by_field[fd.id] = _rows_for(
            fd, by_field.get(fd.id, []), docs, decision_no, registry.report_wording,
            leader.label if leader else None,
        )

    # A variable with an `entries` or `periods` display is one row, built from
    # its entries' keys (fixes 1, 3 and 7 of the 2026-10-12 brief). A run made
    # before RECONCILE kept those keys (ResolvedField.statements) has none, and
    # keeps the default rows. A variable printed in an entries row's headline
    # has no row of its own, but only when that row was built.
    shown_as: dict[str, Row] = {}
    in_headline: set[str] = set()
    for fd in registry.fields:
        # A `breakdown` display reads the rows themselves, so it needs no keys.
        if isinstance(fd.display, BreakdownDisplay):
            if any(r.status != "not_found" for r in rows_by_field[fd.id]):
                shown_as[fd.id] = _breakdown_row(
                    fd, fd.display, rows_by_field, by_field, field_by_id, docs,
                    registry.report_wording,
                )
            continue
        entries = by_field.get(fd.id, [])
        if not any(rf.statements for rf in entries):
            continue
        if isinstance(fd.display, EntriesDisplay):
            shown_as[fd.id] = _entries_row(
                fd, fd.display, entries, rows_by_field, field_by_id, docs,
                registry.report_wording,
            )
            in_headline |= {h.field for h in fd.display.headline_fields}
        elif isinstance(fd.display, PeriodsDisplay):
            shown_as[fd.id] = _periods_row(
                fd, fd.display, entries, rows_by_field, field_by_id, docs,
                registry.report_wording,
            )

    sections: list[GroupSection] = []
    found = 0
    for group in registry.groups:
        rows: list[Row] = []
        schedules: list[Schedule] = []
        taken = set(group.block.replaces) if group.block is not None else set()
        for fd in (f for f in registry.fields if f.group == group.id):
            field_rows = rows_by_field[fd.id]
            if any(r.status != "not_found" for r in field_rows):
                found += 1
            if fd.id in in_headline:
                continue
            if fd.id in shown_as and fd.id not in taken:
                rows.append(shown_as[fd.id])
                continue
            if fd.id in taken:
                # The block's row stands where the first variable it replaces stood.
                if group.block is not None and not any(r.members for r in rows):
                    rows.append(_block_row(
                        group.block, rows_by_field, by_field, field_by_id, docs,
                        registry.report_wording,
                    ))
                continue
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
            rows += _rows_for(fd, by_field[fid], docs, decision_no, registry.report_wording)
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
        evidence=_evidence(state, field_by_id, docs, decision_no, registry.report_wording),
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
    wording: ReportWording,
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
    # The row with no scope first, so that it is the one drawn beside the
    # variable's label and needs no label of its own (fix 11 of the 2026-10-12
    # brief: it used to come later, labelled "Part not stated").
    for scope_key, group in sorted(by_scope.items(), key=lambda kv: kv[0] is not None):
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
        row = _fold_rewordings(fd, Row(
            field_id=fd.id,
            label=fd.label,
            # name_ids: an entry RECONCILE kept out of the arm match carries its
            # document's id in its scope (reconcile/__init__.py:_scope_checks).
            scope=_scope_words(docs.name_ids(group[0].scope), wording)
            if scope_key is not None and group[0].scope else None,
            budget_driver=fd.budget_driver,
            status=min((ln.status for ln in lines), key=lambda s: _STATUS_RANK.get(s, 0)),
            values=lines,
            verdict=verdict,
            decision_no=no,
            reasons=_review_reasons(fd, group, no, borrowed, docs.name_ids),
            follows_label=borrowed,
        ))
        _add_sides(row, fd, docs, decision_no, wording)
        if row.status == "needs_review" and all(rf.derived_from for rf in group):
            _add_derived(row, group, docs, wording)
        if borrowed is not None and no is not None:
            _add_follow_lines(row, fd, group, docs, wording, no)
        rows.append(row)
    if isinstance(fd.display, ScopesDisplay):
        return [_scope_parts(fd, fd.display, rows)]
    return rows


def _scope_words(scope: str, wording: ReportWording) -> str:
    """A scope as the report prints it: its `<kind>:` prefix, as the design.parts
    hint defines the kinds, as a word from report_wording.scope_kinds, so that
    "cohort:NEOD001 arm" reads "Cohort: NEOD001 arm". A scope with no listed
    prefix is printed as stored; Appendix A always prints it as stored."""
    kind, sep, name = scope.partition(":")
    word = wording.scope_kinds.get(re.sub(r"[\s-]+", "_", kind.strip().lower()))
    if not sep or word is None or not name.strip():
        return scope
    return ReportWording.fill(wording.scope_kind, kind=word, name=name.strip())


def _block_row(
    block: Block,
    rows_by_field: dict[str, list[Row]],
    by_field: dict[str, list[ResolvedField]],
    field_by_id: dict[str, FieldDef],
    docs: _Docs,
    wording: ReportWording,
) -> Row:
    """A group's `block`: one row with the headline variable's value and a line
    for each of `block.lines`. The rows it replaces are its `members`, so the
    review list and the counts still see each one; Appendix A is built from the
    resolved values and never sees the block.

    Its status is its most-attention member's, as a `scopes` row's is its
    most-attention part's, and it carries a member's disagreement number, so a
    block holding a disagreement reads Disagreement.
    """
    members = [r for fid in block.replaces for r in rows_by_field.get(fid, [])]
    lines: list[BlockText] = []
    for line in block.lines:
        fd = field_by_id[line.field]
        label = line.label or fd.label
        for row in rows_by_field.get(line.field, []):
            named = f"{label} ({row.scope})" if row.scope else label
            lines += _block_lines(
                line, named, row, by_field.get(line.field, []), fd, docs, wording,
            )
    # A variable the block takes with no line of its own is in Appendix A when
    # it was found; when it was not, that is said here, as its row said it.
    # When it is in a numbered disagreement — the headline included — both sides
    # are said here too, as they are on every Disagreement row (fix 2), so the
    # block never names a disagreement it does not describe.
    on_lines = {line.field for line in block.lines}
    for fid in block.replaces:
        if fid in on_lines:
            continue
        own = rows_by_field.get(fid, [])
        for row in own:
            if row.sides and row.decision_no is not None:
                named = f"{field_by_id[fid].label} ({row.scope})" if row.scope else (
                    field_by_id[fid].label
                )
                lines += _block_lines(
                    BlockLine(field=fid), named, row, by_field.get(fid, []), field_by_id[fid],
                    docs, wording,
                )
        if fid != block.headline and all(r.status == "not_found" for r in own):
            lines.append(BlockText(ReportWording.fill(
                wording.block_not_found, label=field_by_id[fid].label,
            )))
    # The block's own scope is None, so a scoped headline value says its part,
    # or two parts' values would read "Needed; Not needed" with no way to tell
    # which is which.
    head = [
        replace(v, items=[f"{r.scope}: {v.items[0]}", *v.items[1:]]) if r.scope else v
        for r in rows_by_field.get(block.headline, []) for v in r.values
    ]
    return Row(
        field_id=block.headline,
        label=block.label,
        scope=None,
        budget_driver=any(field_by_id[f].budget_driver for f in block.replaces),
        status=min((m.status for m in members), key=lambda st: _STATUS_RANK.get(st, 0)),
        values=head,
        decision_no=next((m.decision_no for m in members if m.decision_no is not None), None),
        members=members,
        block_lines=lines,
    )


def _block_lines(
    line: BlockLine,
    label: str,
    row: Row,
    entries: list[ResolvedField],
    fd: FieldDef,
    docs: _Docs,
    wording: ReportWording,
) -> list[BlockText]:
    """One variable's lines in a block, in report_wording's sentences: what it
    was found to be and where, each document's side where the documents
    disagree, or a derived variable's reasons."""
    fill = ReportWording.fill
    if row.status == "not_found":
        return [BlockText(fill(wording.block_not_found, label=label))]
    if row.status == "not_specified":
        # Its value is None, printed "—"; the line says what the status says.
        pages = docs.pages([p for v in row.values for rf in v.entries for p in rf.sources])
        return [BlockText(fill(
            wording.block_value, label=label, value=_ENUM_WORDS["not_specified"], pages=pages,
        ))]
    # Only the pages that state the value (fix 2), not every record's page.
    stating = [p for v in row.values for rf in v.entries for p in _stating(rf, fd)]
    # A line's wording says a document asks for something, so it is used only
    # where a document states a value: not on a disagreement, which gives both
    # sides below, and not where the document says it does not specify one.
    states = not (row.sides and row.decision_no is not None) and bool(stating) and all(
        rf.status != "not_specified" and rf.value is not None
        and str(rf.value).strip().casefold() not in _NOT_SPECIFIED
        for v in row.values for rf in v.entries
    )
    if line.wording is not None and states:
        codes = list(dict.fromkeys(docs.code(p.doc_id) for p in stating))
        text = fill(
            line.wording, doc=_join_words(codes), value="; ".join(v.text for v in row.values),
            pages=docs.pages(stating),
        )
        # The wording names no variable, so a scoped row says its part first.
        return [BlockText(f"{row.scope}: {text}" if row.scope else text)]
    if row.sides and row.decision_no is not None:
        out = [BlockText(fill(wording.block_disagree, n=row.decision_no, label=label))]
        for side in row.sides:
            text = (
                fill(wording.block_side, pages=side.sources, quote=side.quote,
                     cut="…" if side.quote_cut else "")
                if side.quote
                else fill(wording.block_side_value, pages=side.sources, value=side.text)
            )
            out.append(BlockText(text, indent=1))
        return out
    why = next((rf.why for rf in entries if rf.why), None)
    if why is not None:
        return [BlockText(docs.name_ids(why))]
    out = []
    for v in row.values:
        quote = next((rf.quote for rf in v.entries if rf.quote), None)
        if quote is None:
            out.append(BlockText(fill(
                wording.block_value, label=label, value=v.text, pages=v.sources,
            )))
            continue
        first, cut = _first_sentence(quote)
        out.append(BlockText(fill(
            wording.block_stated, label=label, value=v.text,
            quote=first, cut="…" if cut else "", pages=v.sources,
        )))
    return out


def _add_derived(
    row: Row, group: list[ResolvedField], docs: _Docs, wording: ReportWording
) -> None:
    """A derived row says it was worked out, why, and from which pages."""
    row.derived_status = wording.derived_status
    why = next((rf.why for rf in group if rf.why), None)
    if why is None:
        return
    # name_ids: the reasons can carry an evidence row's scope, which the model
    # wrote and which may hold a document id, as every other scope may.
    row.why = ReportWording.fill(wording.derived_why, why=docs.name_ids(why))
    pages = docs.pages([p for rf in group for p in rf.sources])
    if pages:
        row.evidence_pages = ReportWording.fill(wording.evidence_pages, pages=pages)


def _numbered_own(
    rf: ResolvedField, decision_no: dict[tuple[object, ...], int]
) -> Contradiction | None:
    """The entry's own disagreement, when it has a number in the Disagreements table."""
    c = _own_disagreement(rf)
    return c if c is not None and _signature(c) in decision_no else None


def _add_sides(
    row: Row,
    fd: FieldDef,
    docs: _Docs,
    decision_no: dict[tuple[object, ...], int],
    wording: ReportWording,
) -> None:
    """Both sides of the row's own numbered disagreement, and the line under them.

    Fix 2 of the 2026-10-12 brief. A borrowed disagreement (`follows_conflict_of`)
    has no sides here: its records are about another variable.
    """
    own = next(
        (c for line in row.values for rf in line.entries
         if (c := _numbered_own(rf, decision_no)) is not None),
        None,
    )
    if own is None:
        return
    for line in row.values:
        line.on_sides = all(_numbered_own(rf, decision_no) is not None for rf in line.entries)
    n = decision_no[_signature(own)]
    row.sides = _sides(own, fd, docs)
    row.disagreement = own
    if own.resolved_value is None:
        row.decision_line = ReportWording.fill(wording.row_no_value_chosen, n)
    else:
        row.decision_line = ReportWording.fill(
            wording.row_shown_provisional, n,
            value="; ".join(format_value(own.resolved_value, fd)),
            # A run made before precedence recorded its winner has no document
            # to name.
            doc=_doc_word(docs.code(own.winning_doc_id))
            if own.winning_doc_id else "first-ranked document",
        )


def _sides(c: Contradiction, fd: FieldDef | None, docs: _Docs) -> list[Side]:
    """One side per document and value, in the order the records came.

    A side's pages are the pages of the records giving that value, and no
    other: a page beside a value must state it (fix 2 of 2026-10-09). So a
    document that gives two values is two sides, and neither value leaves the
    row (pipeline-rules-reviewer, 2026-10-12). The text is the chosen record's
    when it is among them, else the most confident. Each record, with its own
    value, page and quote, is also in Appendix A (_evidence).
    """
    groups: dict[tuple[str, str], list[tuple[int, FieldRecord]]] = {}
    for i, r in enumerate(c.records, 1):
        key = (r.provenance.doc_id, _same_key(_record_items(r, fd)))
        groups.setdefault(key, []).append((i, r))
    # A document giving several wordings prints them without quotes, which would
    # make the row the length of Appendix A; each quote is there with its page.
    sides_of = Counter(doc_id for doc_id, _ in groups)
    out = []
    for (doc_id, _), recs in groups.items():
        chosen = next((r for i, r in recs if i == c.winning_record_index), None)
        best = chosen or max((r for _, r in recs), key=lambda r: r.confidence)
        quotes = {" ".join(r.quote.split()) for _, r in recs}
        quote, cut = (
            _first_sentence(quotes.pop())
            if len(quotes) == 1 and sides_of[doc_id] == 1 else (None, False)
        )
        out.append(Side(
            sources=docs.pages([r.provenance for _, r in recs]),
            text="; ".join(_record_items(best, fd)),
            quote=quote,
            quote_cut=cut,
        ))
    return out


# Longest a side's quote may be before it is cut short. The whole passage is in
# Appendix A.
SIDE_QUOTE_CHARS = 160
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def _first_sentence(quote: str) -> tuple[str, bool]:
    """A quote's first sentence, flattened onto one line, and whether it was cut
    short at SIDE_QUOTE_CHARS. The brief allows a quote on a row to be
    shortened to one sentence; the full quote stays in Appendix A. A cut is
    marked outside the quotation marks, so what is inside them is the
    document's own words."""
    flat = " ".join(quote.split())
    first = _SENTENCE_END.split(flat, maxsplit=1)[0]
    if len(first) <= SIDE_QUOTE_CHARS:
        return first, False
    return first[:SIDE_QUOTE_CHARS].rsplit(" ", 1)[0].rstrip(), True


def _doc_word(code: str) -> str:
    """A document's name inside a sentence: "the amendment", but "the RFP"."""
    return code if sum(ch.isupper() for ch in code) > 1 else code.lower()


def _join_words(words: list[str]) -> str:
    return " and ".join(words)


def _add_follow_lines(
    row: Row,
    fd: FieldDef,
    entries: list[ResolvedField],
    docs: _Docs,
    wording: ReportWording,
    n: int,
) -> None:
    """A row following another variable's disagreement: its status, and where
    only some documents in that disagreement give it a value, the field's
    `follow_wording` sentences instead of its value lines.

    Fix 2 of the 2026-10-12 brief; the field's example is beside its
    follow_wording in config/fields.yaml. The second sentence, what the other
    documents' side means for this row, is printed only when every record those
    documents have in the leading disagreement gives a `when_others_say` value.
    """
    row.depends_on = ReportWording.fill(wording.depends_on, n)
    if fd.follow_wording is None:
        return
    leader = next((rf.contradiction for rf in entries if rf.contradiction is not None), None)
    if leader is None:
        return
    leading = list(dict.fromkeys(r.provenance.doc_id for r in leader.records))
    stating = [
        p for line in row.values if line.text != "—"
        for rf in line.entries for p in _stating(rf, fd)
    ]
    giving = list(dict.fromkeys(p.doc_id for p in stating))
    others = [d for d in leading if d not in giving]
    if not giving or not others:
        return
    row.follow_lines = [
        ReportWording.fill(
            fd.follow_wording.only_some, n,
            docs=_join_words([_doc_word(docs.code(d)) for d in giving]),
            value="; ".join(line.text for line in row.values if line.text != "—"),
            pages=docs.pages(stating),
        ),
    ]
    none_values = set(fd.follow_wording.when_others_say)
    if all(
        str(r.value).strip() in none_values
        for r in leader.records if r.provenance.doc_id in others
    ):
        row.follow_lines.append(ReportWording.fill(
            fd.follow_wording.if_others_right, n,
            others=_join_words([_doc_word(docs.code(d)) for d in others]),
        ))


def _scope_parts(fd: FieldDef, display: ScopesDisplay, rows: list[Row]) -> Row:
    """A `scopes` display: the variable's rows as the parts of one row, each under
    its heading, in the order config/fields.yaml lists the headings. A scope no
    heading names comes last, under its own words.

    The row's status is its most-attention part's, as a row's is its
    most-attention value's, and it carries a part's disagreement number so its
    status reads Disagreement.
    """
    headings = display.scopes

    def place(row: Row) -> int:
        return next(
            (i for i, h in enumerate(headings) if scope_has_phrase(row.scope, h.phrases)),
            len(headings),
        )

    parts = sorted(rows, key=place)
    for part in parts:
        i = place(part)
        part.heading = headings[i].label if i < len(headings) else part.scope
    return Row(
        field_id=fd.id, label=fd.label, scope=None, budget_driver=fd.budget_driver,
        status=min((p.status for p in parts), key=lambda s: _STATUS_RANK.get(s, 0)),
        values=[],
        decision_no=next((p.decision_no for p in parts if p.decision_no is not None), None),
        parts=parts,
    )


# --------------------------------------------------------------------------- #
# `entries` and `periods` displays (fixes 1, 3 and 7 of the 2026-10-12 brief)
# --------------------------------------------------------------------------- #
#
# Each is one row whose `members` are the variable's own rows, so the review
# list, the counts and assert_nothing_lost see every value as before, and whose
# `block_lines` say the entries key by key, merged across the documents by
# reconcile/entries.py. Every word printed comes from config/fields.yaml: the
# display's wordings, `report_wording`, or the documents' own values.


def _key_text(key: EntryKey, value: Any, as_kind: bool, wording: ReportWording) -> str:
    """One key's value as the report prints it. An enum value is in words, and
    the kind of part (`as_kind`) in `scope_kinds`' word for it ("Arm")."""
    if key.type == "enum":
        token = str(value)
        return (wording.scope_kinds.get(token) if as_kind else None) or humanize_enum(token)
    return format_value(value, None)[0]


def _entry_words(
    entry: MergedEntry, kind_key: str | None, wording: ReportWording,
    leave_out: set[str] | None = None,
) -> dict[str, str]:
    """Each key's shown value as the report prints it, but the keys `leave_out`
    names."""
    out: dict[str, str] = {}
    for name, km in entry.keys.items():
        if name in (leave_out or set()):
            continue
        text = _key_text(km.key, km.value, name == kind_key, wording)
        if text and text != "—":
            out[name] = text
    return out


def _pick(alternatives: list[str], words: dict[str, str]) -> str | None:
    """The first wording whose every `{key}` has a value, filled in."""
    for alt in alternatives:
        if all(k in words for k in placeholders(alt)):
            for k in placeholders(alt):
                alt = alt.replace("{" + k + "}", words[k])
            return alt
    return None


def _differ_lines(
    entry: MergedEntry, names: list[str], records: list[FieldRecord],
    key_words: dict[str, str], docs: _Docs, wording: ReportWording, indent: int,
) -> list[BlockText]:
    """A line for each key in `names`, the keys the records give different
    values for (`MergedEntry.differing`), each value with the pages that state
    it. Such a key is left out of the entry's own lines, which would otherwise
    print one value as settled."""
    out = []
    for name in names:
        km = entry.keys[name]
        by_value: dict[str, tuple[Any, list[FieldRecord]]] = {}
        for r in records:
            if isinstance(r.value, dict) and name in r.value:
                said = r.value[name]
                by_value.setdefault(same_key(said), (said, []))[1].append(r)
        values = "; ".join(
            ReportWording.fill(
                wording.entries_differ_value,
                value=_key_text(km.key, said, False, wording),
                pages=docs.pages([r.provenance for r in rs]),
            )
            for said, rs in by_value.values()
        )
        # key_words are written for the middle of a sentence; this line starts with one.
        label = key_words.get(name) or humanize_enum(name)
        out.append(BlockText(ReportWording.fill(
            wording.entries_differ, label=label[:1].upper() + label[1:], values=values,
        ), indent))
    return out


def _reworded(entry: MergedEntry, leave_out: set[str]) -> bool:
    """A text key the documents word in more than one way."""
    return any(
        len({same_key(v) for vs in km.by_doc.values() for v, _ in vs}) > 1
        for name, km in entry.keys.items() if km.key.type == "text" and name not in leave_out
    )


def _join_list(items: list[str], wording: ReportWording) -> str:
    """"a", "a and b", "a, b and c", with `entries_and` before the last."""
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + wording.entries_and + items[-1]


def _leftovers(entries: list[ResolvedField], fd: FieldDef) -> list[BlockText]:
    """A value of the variable that is not an entry: a record NORMALIZE could
    not read as one keeps its text, and is printed as it is rather than lost."""
    return [
        BlockText(format_value(member, fd)[0])
        for rf in entries if isinstance(rf.value, list)
        for member in rf.value if not isinstance(member, dict)
    ]


def _display_row(
    fd: FieldDef, members: list[Row], head: str, sources: str, lines: list[BlockText],
) -> Row:
    """The one row of an `entries` or `periods` display. Its status is its
    most-attention member's, and its figure the lowest member's, as a block's."""
    scored = [
        v.confidence for m in members for v in m.values if v.status != "not_found"
    ]
    status = min((m.status for m in members), key=lambda st: _STATUS_RANK.get(st, 0))
    return Row(
        field_id=fd.id,
        label=fd.label,
        scope=None,
        budget_driver=fd.budget_driver,
        status=status,
        values=[ValueLine(
            items=[head], status=status, confidence=min(scored, default=0.0), sources=sources,
        )],
        decision_no=next((m.decision_no for m in members if m.decision_no is not None), None),
        members=members,
        block_lines=lines,
    )


def _part_title(display: EntriesDisplay, words: dict[str, str], n: int) -> str:
    """The title an `entries` display gives its `n`th part, from the part's
    keys as `_entry_words` prints them."""
    return _pick(display.title, {**words, "n": str(n)}) or str(n)


def _titled_parts(
    fd: FieldDef, display: EntriesDisplay, entries: list[ResolvedField], wording: ReportWording,
) -> list[tuple[str | None, str]]:
    """Each part of an `entries` display, in its order, as its scope (as
    normalize_scope reads it) and its title, so another display naming the same
    parts names them as this one does."""
    may_differ = fd.may_word_differently if fd.one_answer_when_documents_agree else None
    out = []
    parts = [rf for rf in entries if rf.statements]
    for n, rf in enumerate(parts, 1):
        entry = merge_entry(fd, rf.statements)
        words = _entry_words(entry, display.kind_key, wording, set(entry.differing(may_differ)))
        out.append((normalize_scope(rf.scope), _part_title(display, words, n)))
    return out


def _entries_row(
    fd: FieldDef,
    display: EntriesDisplay,
    entries: list[ResolvedField],
    rows_by_field: dict[str, list[Row]],
    field_by_id: dict[str, FieldDef],
    docs: _Docs,
    wording: ReportWording,
) -> Row:
    """An `entries` display: a block of lines per part, then what the documents
    agree on and what only one of them states (fix 1 of the 2026-10-12 brief).

    A part's lines are its keys' shown values, in the display's wordings; a
    key the documents give different values for (`MergedEntry.differing`: a
    counted key, or a text key two documents state that is not in
    `may_word_differently`) is a line naming each value and its pages instead;
    each document's own name for the part
    (`label_key`) is printed where the title does not already say it; and
    RECONCILE's note on a part one document alone lists is printed under it.
    """
    fill = ReportWording.fill
    parts = [rf for rf in entries if rf.statements]
    merged = [merge_entry(fd, rf.statements) for rf in parts]
    label = {display.label_key} if display.label_key else set()
    lines: list[BlockText] = []
    # A key two documents state differently is a conflict, but the naming keys
    # this field's documents may word differently, as GATE reads it.
    may_differ = fd.may_word_differently if fd.one_answer_when_documents_agree else None
    differing = [e.differing(may_differ) for e in merged]
    # The titles a `breakdown` display naming these parts prints too.
    titles = [title for _, title in _titled_parts(fd, display, entries, wording)]
    for rf, entry, unsettled, title in zip(parts, merged, differing, titles, strict=True):
        words = _entry_words(entry, display.kind_key, wording, set(unsettled))
        lines.append(BlockText(title))
        for alternatives in display.lines:
            line = _pick(alternatives, words)
            if line is not None:
                lines.append(BlockText(line, 1))
        lines += _differ_lines(
            entry, unsettled, rf.statements, display.key_words, docs, wording, indent=1,
        )
        if display.label_key and display.label_key in entry.keys:
            for doc, said in entry.keys[display.label_key].by_doc.items():
                for value, _ in said:
                    if same_key(value) in same_key(title):
                        continue
                    lines.append(BlockText(fill(
                        display.label_line, doc=docs.code(doc), value=str(value).strip(),
                    ), 1))
        if rf.review_note:
            lines.append(BlockText(docs.name_ids(rf.review_note), 1))

    # How many parts, of which kind.
    # One kind only when every part states it and all agree.
    kind: str | None = None
    kind_key = display.kind_key
    if kind_key and all(kind_key in e.keys and e.keys[kind_key].agreed for e in merged):
        kinds = {str(e.keys[kind_key].value) for e in merged}
        kind = kinds.pop() if len(kinds) == 1 else None
    one_kind = (wording.scope_kinds.get(kind) or humanize_enum(kind)).lower() if kind else None
    if len(merged) == 1 and one_kind:
        kinds_word = one_kind
    else:
        kinds_word = display.count.get(kind or "", display.count.get("mixed", ""))
    count = fill(wording.entries_count, len(merged), kinds=kinds_word).strip()

    # What every document listing a part agrees on: the count when each lists
    # every part, and each counted key every document states the same in each.
    listing = list(dict.fromkeys(d for e in merged for d in e.docs))
    if len(listing) > 1 and all(set(e.docs) == set(listing) for e in merged):
        agreed = [count]
        # "per arm" needs one kind of part to name.
        for name, word in display.key_words.items() if one_kind else ():
            kms = [km for e in merged if (km := e.keys.get(name)) is not None]
            if len(kms) < len(merged) or not all(
                km.key.type != "text" and km.agreed and set(km.stated_by) == set(listing)
                for km in kms
            ):
                continue
            if len({same_key(km.value) for km in kms}) == 1:
                agreed.append(fill(
                    wording.entries_same_each, what=word, kind=one_kind or "",
                    value=_key_text(kms[0].key, kms[0].value, False, wording),
                ))
            else:
                agreed.append(fill(wording.entries_each, what=word, kind=one_kind or ""))
        who = wording.entries_two_docs if len(listing) == 2 else fill(
            wording.entries_all_docs, len(listing),
        )
        lines.append(BlockText(fill(
            wording.entries_agree, docs=who, what=_join_list(agreed, wording),
        )))

    # Keys one document alone states, wherever any document states them.
    if len(listing) > 1:
        alone: dict[str, list[str]] = {}
        for name, word in display.key_words.items():
            stating = {d for e in merged if name in e.keys for d in e.keys[name].stated_by}
            if len(stating) == 1:
                alone.setdefault(next(iter(stating)), []).append(word)
        for doc, words_of in alone.items():
            what = _join_list(words_of, wording)
            lines.append(BlockText(fill(
                wording.entries_only_one if len(words_of) == 1 else wording.entries_only_many,
                what=what[:1].upper() + what[1:], doc=_doc_word(docs.code(doc)),
            )))
    if any(_reworded(e, label | set(d)) for e, d in zip(merged, differing, strict=True)):
        lines.append(BlockText(wording.other_wordings))
    lines += _leftovers(entries, fd)

    # The headline: the count, then each headline variable's value.
    head = [count]
    members = list(rows_by_field.get(fd.id, []))
    for h in display.headline_fields:
        own = rows_by_field.get(h.field, [])
        members += own
        texts = list(dict.fromkeys(
            line.text for r in own for line in r.values
            if r.status != "not_found" and line.text != "—"
        ))
        if texts:
            head.append(fill(h.wording, value="; ".join(texts)))
    sources = docs.pages(
        [r.provenance for rf in parts for r in rf.statements]
        + [p for h in display.headline_fields for r in rows_by_field.get(h.field, [])
           for v in r.values for e in v.entries for p in _stating(e, field_by_id[h.field])]
    )
    return _display_row(fd, members, ", ".join(head), sources, lines)


def _breakdown_row(
    fd: FieldDef,
    display: BreakdownDisplay,
    rows_by_field: dict[str, list[Row]],
    by_field: dict[str, list[ResolvedField]],
    field_by_id: dict[str, FieldDef],
    docs: _Docs,
    wording: ReportWording,
) -> Row:
    """A `breakdown` display (fix 10 and the CRF half of fix 11 of the
    2026-10-12 brief): the total and the figure for each part in the headline,
    and under it a line per part, named as the `parts_from` display names it,
    and the total.

    Every figure is a row of the variable as the documents state it; nothing
    is added up or divided. A row that is neither a part, the each figure nor
    the total keeps its own line, under its scope words, and a row whose
    documents disagree, or say they do not specify it, gives its lines as a
    block line does (`_block_lines`), so its sides are on the row. The rows
    are the display's `members`, so the review list still sees each one.
    """
    fill = ReportWording.fill
    rows = rows_by_field.get(fd.id, [])

    titles: dict[str | None, tuple[int, str]] = {}
    if display.parts_from is not None:
        pfd = field_by_id[display.parts_from]
        if isinstance(pfd.display, EntriesDisplay):
            for i, (key, title) in enumerate(_titled_parts(
                pfd, pfd.display, by_field.get(pfd.id, []), wording,
            )):
                titles.setdefault(key, (i, title))

    def scope_of(row: Row) -> str | None:
        return next((rf.scope for v in row.values for rf in v.entries), None)

    def plain(row: Row) -> bool:
        return row.status not in ("not_found", "not_specified") and not (
            row.sides and row.decision_no is not None
        )

    def text(row: Row) -> str:
        return "; ".join(v.text for v in row.values)

    def figure(row: Row) -> str | None:
        return _same_key(row.values[0].items) if plain(row) and len(row.values) == 1 else None

    def own_lines(name: str, row: Row) -> list[BlockText]:
        entries = [rf for v in row.values for rf in v.entries]
        return _block_lines(BlockLine(field=fd.id), name, row, entries, fd, docs, wording)

    def named(row: Row) -> str:
        name = row.scope or fd.label
        return name[:1].upper() + name[1:]

    parts: list[tuple[int, str, Row]] = []
    each: list[Row] = []
    total: list[Row] = []
    other: list[Row] = []
    for row in rows:
        if row.status == "not_found":
            continue
        scope = scope_of(row)
        key = normalize_scope(scope)
        if key is not None and key in titles:
            parts.append((*titles[key], row))
        elif scope_has_phrase(scope, display.each):
            each.append(row)
        # "total" is the whole-study scope as RECONCILE and GATE read it
        # (normalize_scope: "all arms", "overall"), whatever the words.
        elif key == "total" or scope_has_phrase(scope, display.total):
            total.append(row)
        else:
            other.append(row)
    parts.sort(key=lambda t: t[0])

    lines: list[BlockText] = []
    for _, title, row in parts:
        if plain(row):
            lines.append(BlockText(fill(display.part_line, part=title, value=text(row))))
        else:
            lines += own_lines(title, row)

    # The each figure is said once, in the headline, where every part's figure
    # reads the same as it; one that does not agree is printed as it is.
    each_figures = {figure(r) for r in each}
    folds = len(each_figures) == 1 and None not in each_figures and all(
        figure(r) in each_figures for _, _, r in parts
    )
    each_text = "; ".join(dict.fromkeys(text(r) for r in each)) if folds else None
    for row in each if not folds else []:
        if plain(row):
            lines.append(BlockText(fill(display.each_differs, value=text(row))))
        else:
            lines += own_lines(named(row), row)

    for row in other:
        if not plain(row):
            lines += own_lines(named(row), row)
        elif row.scope:
            lines.append(BlockText(fill(display.part_line, part=named(row), value=text(row))))
        else:
            lines.append(BlockText(text(row)))

    # The total closes the part lines; with no part lines the headline says it.
    stated = [r for r in total if plain(r)]
    for row in total:
        if not plain(row):
            lines += own_lines(named(row), row)
        elif parts:
            lines.append(BlockText(fill(display.total_line, value=text(row))))

    words: dict[str, str] = {}
    if stated:
        words["total"] = "; ".join(dict.fromkeys(text(r) for r in stated))
    if each_text is not None:
        words["each"] = each_text
    also = []
    for h in display.also:
        found = list(dict.fromkeys(
            v.text for r in rows_by_field.get(h.field, [])
            for v in r.values if r.status != "not_found" and v.text != "—"
        ))
        if found:
            also.append(fill(h.wording, value="; ".join(found)))
    if also:
        words["also"] = ", ".join(also)
    head = _pick(display.headline, words) or "—"
    chosen = next(
        (w for w in display.headline if all(k in words for k in placeholders(w))), "",
    )
    # A figure the chosen headline does not say, and no line says, gets a line,
    # so no wording in config/fields.yaml can take a stated figure out of view.
    if "each" in words and "each" not in placeholders(chosen):
        lines += [BlockText(fill(display.part_line, part=named(r), value=text(r))) for r in each]
    if "total" in words and "total" not in placeholders(chosen) and not parts:
        lines += [BlockText(fill(display.total_line, value=text(r))) for r in stated]

    # The pages of every figure, and of the `also` values where the headline says them.
    sources = docs.pages(
        [p for r in rows for v in r.values for rf in v.entries for p in _stating(rf, fd)]
        + [p for h in display.also if "also" in placeholders(chosen)
           for r in rows_by_field.get(h.field, [])
           for v in r.values for e in v.entries for p in _stating(e, field_by_id[h.field])]
    )
    return _display_row(fd, rows, head, sources, lines)


def _word_key(text: str) -> str:
    """Two headings read the same if they differ only in case, spacing and
    punctuation: a label "Follow-up" repeats the period "Follow up"."""
    return " ".join(re.sub(r"[\W_]+", " ", text).split()).casefold()


def _amounts(c: Contradiction) -> str | None:
    """The disputed values as amounts of one unit, "42 vs 40 months", where
    every document in the disagreement has a record NORMALIZE read as an amount
    and all those amounts are in one unit; else None. A document's other
    wordings of its value ("approximately 42 months, including …") are on the
    variable's own row and in Appendix A; this is the short form for a line
    that only points there. Nothing is parsed: the amounts are NORMALIZE's."""
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for r in c.records:
        amounts = by_doc.setdefault(r.provenance.doc_id, [])
        if isinstance(r.value, dict) and set(r.value) == {"n", "unit"}:
            amounts.append(r.value)
    found = [a for amounts in by_doc.values() for a in amounts]
    if not all(by_doc.values()) or len({str(a["unit"]).casefold() for a in found}) != 1:
        return None
    ns = dict.fromkeys(format_value(a["n"], None)[0] for a in found)
    return " vs ".join(ns) + f" {found[0]['unit']}"


def _periods_row(
    fd: FieldDef,
    display: PeriodsDisplay,
    entries: list[ResolvedField],
    rows_by_field: dict[str, list[Row]],
    field_by_id: dict[str, FieldDef],
    docs: _Docs,
    wording: ReportWording,
) -> Row:
    """A `periods` display: a line per stretch of the study, in the order of
    `order_key`'s values, each merged across the documents (fixes 3 and 7 of
    the 2026-10-12 brief). Entries whose scope names a part of the study
    (`scope_kinds`) are lines under that part's name; every other entry is one
    of the study's own lines, whatever scope the model gave it."""
    fill = ReportWording.fill
    order_key = next(k for k in fd.entry_keys() if k.name == display.order_key)

    def place(records: list[FieldRecord]) -> int:
        value = next(
            (r.value[display.order_key] for r in records
             if isinstance(r.value, dict) and display.order_key in r.value),
            None,
        )
        return order_key.values.index(value) if value in order_key.values else len(
            order_key.values
        )

    by_part: dict[str | None, list[FieldRecord]] = {}
    for rf in entries:
        part = rf.scope if rf.scope and _scope_words(rf.scope, wording) != rf.scope else None
        by_part.setdefault(part, []).extend(rf.statements)
    lines: list[BlockText] = []
    reworded = False
    for part, statements in sorted(by_part.items(), key=lambda kv: kv[0] is not None):
        indent = 0
        if part is not None:
            lines.append(BlockText(_scope_words(docs.name_ids(part), wording) + ":"))
            indent = 1
        for group in sorted(group_entries(statements, display.one_per), key=place):
            entry = merge_entry(fd, group)
            unsettled = entry.differing(None)
            words = _entry_words(entry, None, wording, set(unsettled))
            heading = _pick(
                [w for w in display.heading
                 if len({_word_key(words.get(k, "")) for k in placeholders(w)})
                 == len(placeholders(w))],
                words,
            ) or "—"
            text = _pick(display.lines, {**words, "heading": heading}) or heading
            note = display.notes.get(str(entry.value(display.order_key)))
            lines.append(BlockText(f"{text} {note}" if note else text, indent))
            lines += _differ_lines(entry, unsettled, group, {}, docs, wording, indent + 1)
            reworded = reworded or _reworded(entry, set(display.one_per))
    for see in display.see_also:
        other = field_by_id[see.field]
        rows = [p for r in rows_by_field.get(see.field, []) for p in (r.parts or [r])]
        disputed = next((r for r in rows if r.sides and r.decision_no is not None), None)
        if disputed is not None and disputed.decision_no is not None:
            own = disputed.disagreement
            values = (own and _amounts(own)) or " vs ".join(
                dict.fromkeys(s.text for s in disputed.sides)
            )
            lines.append(BlockText(fill(
                see.disagree, disputed.decision_no, label=other.label, values=values,
            )))
        else:
            lines.append(BlockText(fill(see.wording, label=other.label)))
    if reworded:
        lines.append(BlockText(wording.other_wordings))
    lines += _leftovers(entries, fd)
    pages = docs.pages([r.provenance for rf in entries for r in rf.statements])
    return _display_row(
        fd, list(rows_by_field.get(fd.id, [])), f"({pages})" if pages else "", "", lines,
    )


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
    name_ids: Callable[[str], str] = lambda text: text,
) -> list[str]:
    """Why each flagged entry in this row is flagged, de-duplicated, in GATE's order.

    One entry can only be flagged by one rule, because _gate_field returns at the
    first rule that matches — so the branches below are checked in that same order
    and the first match wins. A row holding several entries can carry several
    reasons, which is why the result is a list.

    RECONCILE's `review_note` is the exception: it is printed whenever it is
    there, after the rule that flagged the entry, because it says something no
    rule's wording does (which document lists an arm the others do not). Its
    document ids are swapped for the report's names by `name_ids`.
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
        elif rf.review_note:
            reason = None
        elif rf.confidence < CONFIDENCE_CONFIRMED:
            reason = REASON_LOW_CONFIDENCE
        else:
            reason = REASON_UNEXPLAINED
        for text in (reason, name_ids(rf.review_note) if rf.review_note else None):
            if text is not None and text not in out:
                out.append(text)
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
    state: RunState,
    field_by_id: dict[str, FieldDef],
    docs: _Docs,
    decision_no: dict[tuple[object, ...], int],
    wording: ReportWording,
) -> list[Evidence]:
    """Every entry's quote, once. Registry order, so it reads like the main table.

    An entry chosen from a disagreement (_own_disagreement) is listed as its
    records instead, each with its own value, page and quote. Before fix 2 of
    the 2026-10-09 brief only the chosen value's quote was here, beside every
    record's page, so in run r-20261009-225414 the RFP's "1" interim analysis
    had no quote anywhere in the appendix and its page sat beside the
    amendment's "0". The entry itself is then listed only if no record already
    shows its value and quote. Every run since commit bf038be (2026-10-03) has
    one that does. In three earlier runs a conflict's value and quote came from
    different records of one document (CLAUDE.md open item 7), and there the
    entry is listed as well.

    Each passage behind a numbered Disagreement carries a marker saying which
    side the report used (CLAUDE.md open item 3, 2026-10-12); see `_marker`.
    A "reconcilable" disagreement has no number, so its passages carry none.
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
        marker = _marker(c, fd, decision_no, wording)
        shown: set[tuple[str, str]] = set()
        for r in c.records if c is not None else []:
            said = "; ".join(_record_items(r, fd))
            shown.add((said.casefold(), r.quote.strip()))
            add(Evidence(
                field_id=rf.field_id,
                label=label,
                scope=docs.name_ids(rf.scope) if rf.scope else None,
                value=said,
                sources=docs.pages([r.provenance]),
                confidence=r.confidence,
                quote=r.quote,
                derived_from=[],
                # ADJUDICATE's explanation, which is all `notes` holds on these
                # rows, is printed in Appendix B; see the comment below.
                notes=None,
                marker=marker(r),
            ))
        # Each document's own statement of an entry, with its own page and quote:
        # the row's `quote` is only the most confident one, and the entries and
        # periods rows say the other wordings are quoted here (pipeline-rules-
        # reviewer, 2026-10-12). A disagreement's records are listed above.
        for r in rf.statements if c is None else []:
            said = "; ".join(_record_items(r, fd))
            if r.quote.strip() == (rf.quote or "").strip():
                continue
            add(Evidence(
                field_id=rf.field_id,
                label=label,
                scope=docs.name_ids(rf.scope) if rf.scope else None,
                value=said,
                sources=docs.pages([r.provenance]),
                confidence=r.confidence,
                quote=r.quote,
                derived_from=[],
                notes=None,
                marker=marker(None),
            ))
        if not rf.quote and not rf.derived_from:
            continue
        if (value.casefold(), (rf.quote or "").strip()) in shown:
            continue
        add(Evidence(
            field_id=rf.field_id,
            label=label,
            scope=docs.name_ids(rf.scope) if rf.scope else None,
            value=value,
            sources=docs.pages(_stating(rf, fd)),
            confidence=rf.confidence,
            quote=rf.quote,
            # By label: Appendix A is read by the same people as the tables above.
            derived_from=[_label(field_by_id, d) for d in rf.derived_from],
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
            # The row's own value, so the side the report used.
            marker=marker(None),
        ))
    return out


def _marker(
    c: Contradiction | None,
    fd: FieldDef | None,
    decision_no: dict[tuple[object, ...], int],
    wording: ReportWording,
) -> Callable[[FieldRecord | None], str | None]:
    """The Appendix A marker for each passage of one row's own disagreement.

    Only a disagreement with a number in the Disagreements table gets markers.
    Where precedence chose no value, every passage says so. Where it chose one,
    "shown" goes on the record precedence chose (`winning_record_index`, set
    since 2026-10-12) and on every other passage stating the same value, as the
    table above groups them (`_same_key`): calling a passage that agrees with
    the report "not used" would be wrong. Runs made before 2026-10-12 have no
    `winning_record_index` for a conflict, and the value alone finds the side.
    Passing None asks for the marker of the row's own entry, the shown value.
    """
    n = decision_no.get(_signature(c)) if c is not None else None
    if c is None or n is None:
        return lambda _record: None
    if c.resolved_value is None:
        return lambda _record: ReportWording.fill(wording.no_value_chosen, n)
    shown_key = _same_key(format_value(c.resolved_value, fd))
    index = c.winning_record_index
    chosen = c.records[index - 1] if index is not None and 1 <= index <= len(c.records) else None

    def marker(record: FieldRecord | None) -> str:
        if (
            record is None
            or record is chosen
            or _same_key(_record_items(record, fd)) == shown_key
        ):
            return ReportWording.fill(wording.shown_provisional, n)
        return ReportWording.fill(wording.not_used, n)

    return marker
