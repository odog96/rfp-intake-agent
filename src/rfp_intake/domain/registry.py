"""Load and validate config/fields.yaml into typed Pydantic models."""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from rfp_intake.config.settings import get_settings


class SearchHints(BaseModel):
    headings: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    requires_tables: bool = False


class BlockLine(BaseModel):
    """One line of a group's `block`: one variable, printed as a sentence.

    `wording` is for a variable read from another group, which the block quotes
    without taking its row: `{doc}` the documents that state it, `{value}` its
    value, `{pages}` where. Without it the line is the variable's label and
    value, its two sides where the documents disagree, or a derived variable's
    reasons; report_wording holds those sentences.
    """

    field: str
    label: str | None = None
    wording: str | None = None


class Block(BaseModel):
    """Several of a group's variables drawn as one row of report.pdf, a line
    each, where separately they read as unconnected answers to one question.
    Added 2026-10-12, fix 9 of that day's brief: six blinding rows became one,
    "Unblinded staff and monitoring". Declared here so that no field is named in
    the renderer (non-negotiable rule 4).

    `headline` gives the row its value. `replaces` are the rows the block takes
    out of the main table; each stays in extraction.json, Appendix A and the
    review list. `lines` are printed in order under the value.
    """

    label: str = Field(min_length=1)
    headline: str
    replaces: list[str] = Field(min_length=1)
    lines: list[BlockLine] = Field(default_factory=list)


class GroupDef(BaseModel):
    id: str
    label: str
    # One line of plain English for a reader without a clinical background,
    # printed under the group's heading in report.pdf. See FieldDef.plain.
    plain: str | None = None
    search_hints: SearchHints = Field(default_factory=SearchHints)
    # How many of this group's highest-scoring sections PLAN keeps. Per group and
    # not one constant for the whole registry, because how widely a group's
    # evidence is scattered is a property of the group: most groups are answered
    # by one or two sections, while blinding_monitoring needs four sections of the
    # sample protocol that sit on three different pages. Three was a module
    # constant in plan/__init__.py until 2026-10-03, and raising it there would
    # have raised it for all nine groups and sent every group more text than it
    # needs. See CLAUDE.md item 4c for the measurement behind the value.
    top_k: int = Field(default=3, ge=1)
    # Several of this group's variables drawn as one row. See Block.
    block: Block | None = None


class ObjectFieldSchema(BaseModel):
    """Schema definition for object[] typed fields."""

    fields: dict[str, str] = Field(default_factory=dict)


def _as_value_token(v: Any) -> str:
    """YAML's `no` is a boolean; config/fields.yaml means the enum token "no"."""
    return str(v).lower() if isinstance(v, bool) else str(v)


class ResolutionRule(BaseModel):
    """One answer a clinical reviewer says follows from another field's answer.

    Added 2026-10-07 for change 3 of that day's review: Angus Gray's rule that a
    Phase 2, 3 or 4 study is never first-in-human, so the answer is No at full
    confidence and no passage needs to be searched for. The rule lives in
    config/fields.yaml next to the field it answers, because it is clinical
    knowledge rather than code — non-negotiable rule 4 forbids keying pipeline
    behaviour to a field name in Python, and a rule written here is read by
    reconcile/rules.py without naming any field.

    `because` is printed as the resolved field's note, so a reviewer reading
    report.pdf sees why the pipeline answered without quoting a passage about
    the field itself. The evidence shown is the trigger field's own passage.
    """

    when_field: str
    when_value_in: list[str]
    # The answer, or, with `status: not_applicable`, none: the question does not
    # arise. Prompt B item 4 (Angus Gray, 13 October call): with no healthy
    # volunteers, "Which cohort/part includes healthy volunteers" reads N/A, not
    # Not found. Exactly one of the two is given.
    value: str | None = None
    status: Literal["not_applicable"] | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    because: str | None = None

    @model_validator(mode="after")
    def _value_or_status(self) -> ResolutionRule:
        if (self.value is None) == (self.status is None):
            raise ValueError("a resolution rule gives exactly one of `value` and `status`")
        return self

    @field_validator("when_value_in", mode="before")
    @classmethod
    def _coerce_tokens(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [_as_value_token(item) for item in v]
        return v

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, v: Any) -> Any:
        return _as_value_token(v) if v is not None else None

    @field_validator("because", mode="before")
    @classmethod
    def _one_line(cls, v: Any) -> Any:
        # YAML's folded block scalar leaves a trailing newline, and this text is
        # printed in a report line.
        return " ".join(str(v).split()) if v is not None else None


class ScopeLeftOut(BaseModel):
    """Rows a derived field's rubric must not read, named by words in their scope.

    Added 2026-10-11, approved by Oliver, for the visit-intensity rating: a
    separate schedule for patients who stopped treatment early must not be added
    to the main count, because IQVIA budgets dropouts separately
    (docs/ANALYST_PROCEDURE_PROTOCOL.md section 9). Which scopes those are is
    clinical knowledge, so it lives in config/fields.yaml; derive/__init__.py
    reads it without naming any field.

    A phrase matches a scope when its words appear in the scope one after
    another, ignoring case and punctuation: "post etd" matches "Post-ETD
    (early discontinuation)". `reason` is printed in the derived row's note
    beside each row left out, so a reviewer sees what was not counted and why.
    """

    phrases: list[str]
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _one_line(cls, v: Any) -> Any:
        return " ".join(str(v).split())


def scope_has_phrase(scope: str | None, phrases: list[str]) -> bool:
    """Whether one of the phrases' words appear in the scope one after another,
    ignoring case and punctuation: "post etd" matches "Post-ETD (early
    discontinuation)". The one rule for every key below that names scopes by
    phrase (ScopeLeftOut, ScopeHeading), so a phrase means the same thing in each.
    """
    if scope is None:
        return False
    words = _words(scope)
    for phrase in phrases:
        want = _words(phrase)
        if want and any(
            words[i:i + len(want)] == want for i in range(len(words) - len(want) + 1)
        ):
            return True
    return False


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class FollowWording(BaseModel):
    """What a `follows_conflict_of` row says when only some of the documents in
    the leading disagreement give it a value.

    Added 2026-10-12, fix 2 of that day's brief. What a missing value means is
    the study's own knowledge, so the sentences live in config/fields.yaml, with
    the field's example beside them.

    `{docs}` is replaced by the documents that give this row's value,
    `{value}` by the value and `{pages}` by its pages; `{others}` by the
    documents in the leading disagreement that give none.

    `if_others_right` says what those other documents' side means for this
    row, so it is printed only when every one of their records in the leading
    disagreement gives one of the `when_others_say` values. Otherwise the
    sentence would assert something no document said (pipeline-rules-reviewer,
    2026-10-12).
    """

    only_some: str = Field(min_length=1)
    if_others_right: str = Field(min_length=1)
    when_others_say: list[str] = Field(min_length=1)

    @field_validator("only_some")
    @classmethod
    def _says_the_value(cls, v: str) -> str:
        if "{value}" not in v:
            raise ValueError("must contain {value}")
        return " ".join(v.split())

    @field_validator("if_others_right")
    @classmethod
    def _names_the_others(cls, v: str) -> str:
        if "{others}" not in v:
            raise ValueError("must contain {others}")
        return " ".join(v.split())

    @field_validator("when_others_say", mode="before")
    @classmethod
    def _values_as_words(cls, v: object) -> object:
        # YAML reads an unquoted 0 as a number; the check compares words.
        return [str(x).strip() for x in v] if isinstance(v, list) else v


class ScopeHeading(BaseModel):
    """One part of a `scopes` display: the scopes it holds, named by phrase
    (see `scope_has_phrase`), and the heading printed above them."""

    phrases: list[str]
    label: str = Field(min_length=1)


class ScopesDisplay(BaseModel):
    """How report.pdf lays out one variable's rows, where the default of one
    row per scope reads wrong. Declared in config/fields.yaml so that no field
    is named in the renderer (non-negotiable rule 4). See `Display` for the
    other kinds.

    `scopes` (added 2026-10-12, fix 6 of that day's brief): every scope of the
    variable in one row, a heading above each, in the order `scopes` lists them;
    a scope no heading names comes last, under its own words. Overall study
    duration is the case: the whole study's length and one patient's time in it
    are different answers, and as two rows the confirmed one read as settling
    the disagreement beside it.
    """

    kind: Literal["scopes"]
    scopes: list[ScopeHeading] = Field(default_factory=list)


class HeadlineField(BaseModel):
    """Another variable an `entries` row prints in its headline, after the
    count: `{value}` is that variable's value. The variable gets no row of its
    own; its rows are members of this one and stay in Appendix A."""

    field: str
    wording: str = Field(min_length=1)

    @field_validator("wording")
    @classmethod
    def _says_the_value(cls, v: str) -> str:
        if "{value}" not in v:
            raise ValueError("must contain {value}")
        return v


class EntriesDisplay(BaseModel):
    """An `object[]` variable whose rows are the study's parts, one block of
    lines per part, each key of its entry merged across the documents
    (reconcile/entries.py). Added 2026-10-12, fix 1 of that day's brief, for
    "Cohorts / arms / dose levels / parts".

    Every wording names the entry's keys by `{key}`. `title` and each of
    `lines` are alternatives: the first whose keys the entry states is printed,
    and none is printed when it states none of them. `{n}` in `title` is the
    entry's position, and `{<kind_key>}` prints as `report_wording.scope_kinds`
    words it. `count` is the plural word for each `kind_key` value, and `mixed`
    the word for several kinds at once. `label_key` is each document's own name
    for the part, printed with `label_line` where it is not the name shown.
    `key_words` names the keys the summary lines under the parts speak of.
    """

    kind: Literal["entries"]
    title: list[str] = Field(min_length=1)
    kind_key: str | None = None
    count: dict[str, str] = Field(default_factory=dict)
    headline_fields: list[HeadlineField] = Field(default_factory=list)
    lines: list[list[str]] = Field(default_factory=list)
    label_key: str | None = None
    label_line: str = Field(default="{doc} calls it: {value}", min_length=1)
    key_words: dict[str, str] = Field(default_factory=dict)


class NotFoundNote(BaseModel):
    """What a variable's row says under "Not found" when no document gives its
    value (fix 4 of the 2026-10-12 brief), `when_not_found` in
    config/fields.yaml. `lines` are printed as written. A record whose scope
    has one of `because_scopes` (scope_has_phrase) is a passage saying why
    there is no value: it is printed as `because_line`, `{quote}` its own
    words and `{pages}` its page, never reworded. Each field in `see` gets
    `see_line`, `{label}` that field's label. Where any other record gives a
    value, the row is printed as usual and none of this is used."""

    lines: list[str] = Field(default_factory=list)
    because_scopes: list[str] = Field(default_factory=list)
    because_line: str = Field(default='"{quote}" ({pages})', min_length=1)
    see: list[str] = Field(default_factory=list)
    see_line: str = Field(default="See {label}.", min_length=1)

    @field_validator("because_line")
    @classmethod
    def _quotes(cls, v: str) -> str:
        if "{quote}" not in v:
            raise ValueError("must contain {quote}, the passage's own words")
        return v

    @field_validator("see_line")
    @classmethod
    def _names(cls, v: str) -> str:
        if "{label}" not in v:
            raise ValueError("must contain {label}")
        return v


class SeeAlso(BaseModel):
    """A line of a `periods` row sending the reader to another variable's row.
    `{label}` is that variable's label; `disagree` is printed instead where its
    row is a Disagreement, `{values}` the sides' values and `{n}` its number."""

    field: str
    wording: str = Field(min_length=1)
    disagree: str = Field(min_length=1)

    @field_validator("disagree")
    @classmethod
    def _names_the_disagreement(cls, v: str) -> str:
        if "{n}" not in v:
            raise ValueError("must contain {n}, the Disagreement number")
        return v


class PeriodsDisplay(BaseModel):
    """An `object[]` variable printed as one row, a line per stretch of the
    study, in the order of `order_key`'s values. Added 2026-10-12, fixes 3 and
    7 of that day's brief, for visit frequency and timeline components.

    Entries with the same values of the `one_per` keys make one line, merged
    across the documents like an `entries` part. The line is named, as
    `{heading}`, by the first of the `heading` wordings whose keys are all
    stated and read differently ("{period} ({label})" is passed over where the
    label only repeats the period); `lines` are alternatives, as in
    `EntriesDisplay`. `notes` adds a note to the line of an `order_key` value.

    Since 29 October 2026 (Angus Gray) each line is its own row of the report's
    table, its heading in bold in the Variable column, so a line's wording no
    longer needs `{heading}`; where one still names it, the report leaves the
    heading out of the value. `always` maps `order_key` values to the heading
    their line is printed under: every value listed gets a line, in the order
    of `order_key`'s values, also where a document states it, and a value no
    document states reads "Not found in the documents". A value not listed gets
    a line only where a document states one, under the `heading` wordings.
    `when_stated` maps further values to their heading in the same way, but each
    gets a line only where a document states it: Angus Gray, 29 October 2026,
    listed screening, run-in, treatment and follow-up, "and then any additional
    timelines, whether there's an open-label extension, or a long-term
    follow-up".
    """

    kind: Literal["periods"]
    order_key: str
    one_per: list[str] = Field(min_length=1)
    heading: list[str] = Field(min_length=1)
    lines: list[str] = Field(min_length=1)
    notes: dict[str, str] = Field(default_factory=dict)
    see_also: list[SeeAlso] = Field(default_factory=list)
    always: dict[str, str] = Field(default_factory=dict)
    when_stated: dict[str, str] = Field(default_factory=dict)
    # The text keys that name a day, a visit or the stretch itself, which one
    # document may word two ways and still give one answer ("Day -1", "Month
    # 1-Day 1"). Every other key, a length above all, must read the same in
    # every record of the stretch, whichever document states it, for GATE to
    # confirm the variable (gate/__init__.py:_periods_agree), and a key that
    # does not is printed with each of its values under the line.
    may_word_differently: list[str] = Field(default_factory=list)

    @field_validator("always", "when_stated")
    @classmethod
    def _always_has_words(cls, v: dict[str, str]) -> dict[str, str]:
        # A line with no heading would print an empty bold cell.
        if any(not str(label).strip() for label in v.values()):
            raise ValueError("every value in always and when_stated needs a heading")
        return v

    @model_validator(mode="after")
    def _listed_once(self) -> PeriodsDisplay:
        both = sorted(set(self.always) & set(self.when_stated))
        if both:
            raise ValueError(f"listed in both always and when_stated: {', '.join(both)}")
        return self

    def named(self, value: str) -> str | None:
        """The heading `always` or `when_stated` gives a value, if either does."""
        return self.always.get(value) or self.when_stated.get(value)


class BreakdownDisplay(BaseModel):
    """A count printed as one row: a line per part of the study, then the
    total, every figure as a document states it. Added 2026-10-12, fix 10 and
    the CRF half of fix 11 of that day's brief, for the number of subjects and
    of CRF pages.

    A row of the variable is a part's where its scope is a part's of the
    `parts_from` variable, which must have an `entries` display, compared as
    RECONCILE compares scopes (normalize_scope); its line is `part_line`, the
    part named as that display titles it. A scope naming one of the `each`
    phrases (see `scope_has_phrase`) is the figure for each part, and one that
    is "total" or names a `total` phrase is the total. Nothing is added up:
    the total is the total a document states.

    `headline` is alternatives, as an entries display's lines are: `{total}`,
    `{each}` and `{also}`, which is each `also` variable's value in its wording.
    The each figure is in the headline only where it reads the same as every
    part's figure, or there are no parts; otherwise `each_differs` prints it,
    as a line saying it does not agree. A figure the chosen headline does not
    say gets a line of its own. An `also` variable keeps its own row, or its
    own place in another display.
    """

    kind: Literal["breakdown"]
    parts_from: str | None = None
    part_line: str = Field(default="{part}: {value}", min_length=1)
    total: list[str] = Field(default_factory=list)
    total_line: str = Field(default="Total: {value}", min_length=1)
    each: list[str] = Field(default_factory=list)
    each_differs: str = Field(min_length=1)
    headline: list[str] = Field(min_length=1)
    also: list[HeadlineField] = Field(default_factory=list)


# How report.pdf lays out one variable's rows, where the default of one row per
# scope reads wrong. The `kind` key says which.
Display = Annotated[
    ScopesDisplay | EntriesDisplay | PeriodsDisplay | BreakdownDisplay,
    Field(discriminator="kind"),
]


class EntryKey(BaseModel):
    """One key of an `object[]` field's `schema`, parsed from its spec: `text`,
    `int`, or `enum[a, b, …]` with its values."""

    name: str
    type: Literal["text", "int", "enum"]
    values: list[str] = Field(default_factory=list)


_ENUM_SPEC = re.compile(r"^enum\[(.*)\]$")


def parse_entry_key(name: str, spec: str) -> EntryKey:
    """A `schema` key's spec as an EntryKey. Raises ValueError on any other spec,
    so EXTRACT never sends a key whose type the validator cannot check."""
    spec = " ".join(str(spec).split())
    if spec == "text":
        return EntryKey(name=name, type="text")
    if spec == "int":
        return EntryKey(name=name, type="int")
    m =_ENUM_SPEC.match(spec)
    values = [v.strip() for v in m.group(1).split(",") if v.strip()] if m else []
    if not values:
        raise ValueError(f"schema key {name}: {spec!r} is not text, int or enum[...]")
    return EntryKey(name=name, type="enum", values=values)


def placeholders(wording: str) -> list[str]:
    """The `{name}`s a wording uses."""
    return re.findall(r"\{([a-z_]+)\}", wording)


class FieldDef(BaseModel):
    id: str
    group: str
    label: str
    type: str
    values: list[str] | None = None
    aliases: list[str] = Field(default_factory=list)

    @field_validator("values", mode="before")
    @classmethod
    def _coerce_values_to_str(cls, v: Any) -> list[str] | None:
        # YAML parses yes/no as booleans; coerce to strings
        if v is None:
            return None
        return [str(item).lower() if isinstance(item, bool) else str(item) for item in v]
    hint: str | None = None
    # What this variable means, for a reader without a clinical background.
    #
    # `plain` and `hint` are not interchangeable and must not be swapped.
    # `hint` is an instruction written for the extraction model ("2-4 sentences
    # maximum") and is never printed in a report. `plain` is written for the
    # person reading report.pdf and is never put in a prompt — the glossary in
    # render/pdf_renderer.py is its only consumer.
    plain: str | None = None
    scoped: bool = False
    source_priority: str | None = None
    # The kinds of document EXTRACT asks for this field in, as CLASSIFY names
    # them. A document of another kind is never asked: the field is left out of
    # the extraction prompt's field list and of the response schema for that
    # call, and a record the model still returns for it is rejected
    # (extract/__init__.py). Empty, the default, means every kind. Added
    # 2026-11-23 for timeline.budget_window, the time window the RFP asks the
    # budget to cover (Angus Gray, 29 October 2026). Not on a derived field,
    # which is never extracted.
    only_from: list[Literal["rfp", "protocol", "amendment", "soa", "other"]] = Field(
        default_factory=list
    )
    budget_driver: bool = False
    derived: bool = False
    derived_from: list[str] | None = None
    # Rows of the `derived_from` inputs that this field's rubric must not read.
    # See ScopeLeftOut.
    leave_out_scopes: ScopeLeftOut | None = None
    # Each scope names what that part of the study is — for an arm, the treatment
    # it receives — so RECONCILE matches scopes across documents on that name.
    # An entry whose own text does not say what its scope names is not matched,
    # and a scope only one of several documents lists is flagged. Added for
    # design.parts on 2026-10-12. See reconcile/__init__.py:_scope_checks.
    check_scopes_across_documents: bool = False
    schema_def: dict[str, str] | None = Field(default=None, alias="schema")
    # When every document lists the same set of this variable's entries and
    # agrees on each one's keys, the entries are one answer, which GATE may
    # confirm; otherwise a budget driver with more than one value is reviewed.
    # Approved by Oliver on 2026-10-12 (fix 1 of that day's brief) for
    # design.parts. See gate/__init__.py:_gate_field.
    one_answer_when_documents_agree: bool = False
    # The text keys that only name an entry, which two documents may word
    # differently and still agree ("NEOD001 + SoC", "NEOD001 plus standard of
    # care"). Every other key two documents state must read the same for the
    # one-answer rule: a dose of 24 mg/kg against 12 mg/kg is a difference a
    # person must see (plan-conformance-reviewer, 2026-10-12).
    may_word_differently: list[str] = Field(default_factory=list)
    # Answers that follow from another field's answer. See ResolutionRule.
    resolution_rules: list[ResolutionRule] = Field(default_factory=list)
    # Another field whose disagreement is this field's disagreement too, named
    # where a reviewer can see it rather than worked out by code that could not
    # know. A list field is the case that needs it: RECONCILE folds a list's
    # records into one answer instead of comparing them across documents, so a
    # list cannot notice that the documents disagree about it, however plainly
    # they do. GATE reads this and marks the field with the same disagreement
    # number, so the report points at one disagreement from both rows. Added
    # 2026-10-07 for change 4 of that day's review (interim.timing following
    # interim.count).
    follows_conflict_of: str | None = None
    # What this row says when only some documents in that disagreement give it a
    # value. See FollowWording.
    follow_wording: FollowWording | None = None
    # How the report lays out this variable's rows. See Display.
    display: Display | None = None
    # Another variable whose row this one is printed under, as further lines at
    # the end of it, instead of a row of its own in All variables (Angus Gray,
    # 29 October 2026: the whole study's duration and the time window to budget
    # for are lines of "Patient timeline"). Each line is labelled by this
    # variable's `scopes` display heading or, with none, its own label. A
    # disagreement about it keeps its number and its own label in the
    # Disagreements table, and a variable no document gives still gets its line.
    shown_under: str | None = None
    # The word report.pdf prints for an enum value whose own spelling reads as
    # software: `not_matching_stated` as "Not matching". Never sent to a model,
    # like `plain`. A value not listed is printed by render/report_model.py's
    # humanize_enum. Added 2026-10-12, fix 2 of that day's brief.
    value_words: dict[str, str] = Field(default_factory=dict)
    # Words a record's quote must contain for each enum value it gives, any one
    # of them, matched as whole words ignoring case. A record giving the value
    # with a quote saying none of them is rejected by EXTRACT
    # (extract/validate.py:answers_field): run r-20261012-angus read an IV
    # infusion from RFP p.1, which names only an antibody. Fix 11 of the
    # 2026-10-12 brief, declared for ip.form only.
    quote_must_say: dict[str, list[str]] = Field(default_factory=dict)
    # Words a record's quote must contain, any one of them, whatever the value,
    # matched as whole words ignoring case. A record whose quote says none of
    # them is rejected by EXTRACT (extract/validate.py:answers_field). Item 5 of
    # Oliver's 2026-10-19 follow-ups: run r-20261019-prompt-c2 read RFP p.3's
    # "40 month study duration" as how long patients are dosed.
    quote_must_mention: list[str] = Field(default_factory=list)
    # Section headings, as phrases (scope_has_phrase), whose records outrank
    # every other record of the same document for this field, whatever their
    # confidence: the schedule table over a summary paragraph. Read from the
    # heading EXTRACT records (Provenance.section) by reconcile/entries.py.
    # Addition 1 to stage 6 of the 2026-10-12 plan.
    prefer_sections: list[str] = Field(default_factory=list)
    # What the row says when no document gives a value. See NotFoundNote.
    when_not_found: NotFoundNote | None = None
    # How report.pdf prints an amount this variable's value is ({n, unit}),
    # `{amount}` standing for "28 days": "Every {amount}" for a schedule.
    # Before Prompt B item 8 the report chose "Every" by finding "frequency" in
    # the field id (rule 4).
    amount_wording: str = "{amount}"
    # The row the Schedule of Events READ_SCHEDULE counted is printed in
    # (Prompt C): `table`, the visit table under this row in place of its
    # extracted text; `stage_totals`, a sum of visits per `schedule_table.stages`
    # stage, which DERIVE writes as this field's rows. Keyed here so no field id
    # is named in Python (rule 4).
    from_schedule: Literal["table", "stage_totals"] | None = None

    model_config = {"populate_by_name": True}

    @field_validator("amount_wording")
    @classmethod
    def _states_the_amount(cls, v: str) -> str:
        if "{amount}" not in v:
            raise ValueError("must contain {amount}, the amount itself")
        return v

    @field_validator("schema_def")
    @classmethod
    def _keys_parse(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        for name, spec in (v or {}).items():
            parse_entry_key(name, spec)
        return v

    def entry_keys(self) -> list[EntryKey]:
        """The keys of one entry of an `object[]` field, in `schema` order."""
        return [parse_entry_key(k, s) for k, s in (self.schema_def or {}).items()]


class ReportWording(BaseModel):
    """Phrases report.pdf prints that belong to no one field, `report_wording` in
    config/fields.yaml.

    Added 2026-10-12 for CLAUDE.md open item 3: the Appendix A line saying which
    side of a disagreement the report used. It lives in YAML, like `plain`, so a
    clinical reviewer can have it corrected without a code change. `{n}` stands
    for the Disagreement number, and every phrase that sends the reader to a
    Disagreement must contain it.

    The three `row_` phrases and `depends_on` were added the same day for fix 2
    of the eleven-fixes brief: the main table's row for a disagreement now
    states both sides and then one of the two `row_` lines, and a row that
    follows another variable's disagreement says `depends_on` in its status.
    `{value}` is the value shown and `{doc}` the document precedence chose.

    `derived_status`, `derived_why` and `evidence_pages` are a derived row's, fix
    8 of the same brief: its status, then under its value the rubric's reasons in
    plain words (`{why}`) and the pages they rest on (`{pages}`).

    The `block_` phrases are the lines of a group's Block (fix 9), and
    `scope_kind` with `scope_kinds` prints a scope's `<kind>:` prefix as a word
    (fix 11): "cohort:NEOD001 arm" as "Cohort: NEOD001 arm". A prefix not listed
    is printed as stored.

    The `entries_` phrases and `other_wordings` are the summary lines of an
    `entries` or `periods` display (fixes 1, 3 and 7): how many parts, what
    only one document states, and where they differ. What only one document
    states and `other_wordings` are printed under the Where cell's pages
    (Prompt B item 3), as is `others_checked`, on a "not a conflict" cluster
    written as one row (Prompt B item 2).
    """

    shown_provisional: str = Field(min_length=1)
    not_used: str = Field(min_length=1)
    no_value_chosen: str = Field(min_length=1)
    row_shown_provisional: str = Field(min_length=1)
    # Keyed by the precedence rule that chose the value, plus `default`.
    precedence_why: dict[str, str]
    row_no_value_chosen: str = Field(min_length=1)
    depends_on: str = Field(min_length=1)
    derived_status: str = Field(min_length=1)
    derived_why: str = Field(min_length=1)
    evidence_pages: str = Field(min_length=1)
    block_stated: str = Field(min_length=1)
    block_value: str = Field(min_length=1)
    block_disagree: str = Field(min_length=1)
    block_side: str = Field(min_length=1)
    block_side_value: str = Field(min_length=1)
    block_not_found: str = Field(min_length=1)
    scope_kind: str = Field(min_length=1)
    scope_kinds: dict[str, str] = Field(default_factory=dict)
    entries_count: str = Field(min_length=1)
    entries_only_one: str = Field(min_length=1)
    entries_only_many: str = Field(min_length=1)
    entries_and: str = Field(min_length=1)
    entries_differ: str = Field(min_length=1)
    entries_differ_value: str = Field(min_length=1)
    other_wordings: str = Field(min_length=1)
    others_checked: str = Field(min_length=1)
    # The value printed for a question a resolution rule says does not arise
    # (`status: not_applicable`, Prompt B item 4).
    not_applicable: str = Field(min_length=1)
    # Appendix A's marker on a passage ADJUDICATE said is not about the
    # variable (fix 5 of the 2026-10-12 brief); its reason is in Appendix B.
    off_topic: str = Field(min_length=1)
    # Words a label's first word may be that are never capitalised: units,
    # "mg" not "Mg" (Prompt B item 8). Matched exactly; see
    # render/report_model.py:_capital.
    never_capitalised: list[str] = Field(default_factory=list)
    # The 29 October 2026 layout (Angus Gray): Variable | Value | Status | Where.
    # Defaulted, so a config/fields.yaml written before it still loads; each can
    # be reworded there. `not_found` is the value of a variable no document
    # gives (rule 3 keeps it apart from "Not specified" and "N/A");
    # `row_disagree` is the red line on a row with its own numbered
    # Disagreement, and `row_provisional` the grey line under it where
    # precedence chose a value; `counted_note` is the grey note under the
    # pages of a row the tool counted from a Schedule of Events, `{heading}`
    # the table's heading.
    not_found: str = Field(default="Not found in the documents", min_length=1)
    row_disagree: str = Field(default="Documents disagree — see Disagreement {n}", min_length=1)
    row_provisional: str = Field(default="Shown provisionally: {value} ({why})", min_length=1)
    counted_note: str = Field(
        default="Worked out by the tool from {heading}, not read from a document.",
        min_length=1,
    )

    @field_validator("entries_count")
    @classmethod
    def _says_how_many(cls, v: str) -> str:
        if "{n}" not in v:
            raise ValueError("must contain {n}, how many")
        return v

    @field_validator("entries_only_one", "entries_only_many")
    @classmethod
    def _says_what(cls, v: str) -> str:
        if "{what}" not in v:
            raise ValueError("must contain {what}, what the documents state")
        return v

    @field_validator("entries_differ")
    @classmethod
    def _names_the_key(cls, v: str) -> str:
        if "{label}" not in v or "{values}" not in v:
            raise ValueError("must contain {label} and {values}")
        return v

    @field_validator("entries_differ_value")
    @classmethod
    def _cites_the_value(cls, v: str) -> str:
        if "{value}" not in v or "{pages}" not in v:
            raise ValueError("must contain {value} and {pages}")
        return v

    @field_validator(
        "not_used", "no_value_chosen", "row_shown_provisional", "row_no_value_chosen",
        "depends_on", "block_disagree", "row_disagree",
    )
    @classmethod
    def _names_the_disagreement(cls, v: str) -> str:
        if "{n}" not in v:
            raise ValueError("must contain {n}, the Disagreement number")
        return v

    @field_validator("row_shown_provisional", "row_provisional")
    @classmethod
    def _says_the_value(cls, v: str) -> str:
        if "{value}" not in v or "{why}" not in v:
            raise ValueError("must contain {value}, the value shown, and {why}, the rule")
        return v

    @field_validator("precedence_why")
    @classmethod
    def _covers_every_rule(cls, v: dict[str, str]) -> dict[str, str]:
        # The rules that can choose a value (reconcile/precedence.py).
        missing = {"recency", "domain_authority", "specificity", "default"} - set(v)
        if missing:
            raise ValueError(f"has no wording for {sorted(missing)}")
        return v

    @field_validator("derived_why")
    @classmethod
    def _says_why(cls, v: str) -> str:
        if "{why}" not in v:
            raise ValueError("must contain {why}, the rubric's reasons")
        return v

    @field_validator("evidence_pages")
    @classmethod
    def _says_the_pages(cls, v: str) -> str:
        if "{pages}" not in v:
            raise ValueError("must contain {pages}, the pages the reasons rest on")
        return v

    @field_validator("block_stated", "block_value", "block_disagree", "block_not_found")
    @classmethod
    def _names_the_variable(cls, v: str) -> str:
        if "{label}" not in v:
            raise ValueError("must contain {label}, the variable the line is about")
        return v

    @field_validator("block_side")
    @classmethod
    def _names_the_side(cls, v: str) -> str:
        if "{pages}" not in v or "{quote}" not in v:
            raise ValueError("must contain {pages} and {quote}, the side's pages and words")
        return v

    @field_validator("block_stated", "block_side")
    @classmethod
    def _marks_a_cut(cls, v: str) -> str:
        # `{cut}` is "…" when the quote was cut short and empty when it was not.
        # It sits outside the quotation marks, so what is inside them is the
        # document's own words, as on a disagreement row's sides.
        if "{quote}" in v and "{cut}" not in v:
            raise ValueError("must contain {cut}, where a shortened quote is marked")
        return v

    @field_validator("block_side_value")
    @classmethod
    def _names_the_side_value(cls, v: str) -> str:
        if "{pages}" not in v or "{value}" not in v:
            raise ValueError("must contain {pages} and {value}, the side's pages and value")
        return v

    @field_validator("counted_note")
    @classmethod
    def _names_the_table(cls, v: str) -> str:
        if "{heading}" not in v:
            raise ValueError("must contain {heading}, the table the tool counted")
        return v

    @field_validator("scope_kind")
    @classmethod
    def _names_kind_and_name(cls, v: str) -> str:
        if "{kind}" not in v or "{name}" not in v:
            raise ValueError("must contain {kind} and {name}")
        return v

    @field_validator("scope_kinds")
    @classmethod
    def _kinds_have_words(cls, v: dict[str, str]) -> dict[str, str]:
        if any(not w.strip() for w in v.values()):
            raise ValueError("every scope kind needs a word")
        return v

    @staticmethod
    def fill(phrase: str, n: int | None = None, **values: str) -> str:
        """`{n}` and each `{name}` given, put in. replace, not str.format: a brace
        elsewhere in the wording must not break a report."""
        text = phrase if n is None else phrase.replace("{n}", str(n))
        for name, value in values.items():
            text = text.replace("{" + name + "}", value)
        return text


class RepeatingVisit(BaseModel):
    """A schedule column that repeats rather than naming its visits: "every 3rd
    month" shows "1 every 3rd month". `{nth}` stands for an ordinal in both."""

    wording: str
    says: str


class ScheduleStage(BaseModel):
    """One stage of "Total number of subject visits". `says` is printed instead of
    a sum for a stage that repeats until the study ends."""

    label: str
    periods: list[str]
    says: str | None = None


class Consideration(BaseModel):
    """What a schedule row is for, matched on its label by `wording`. A row whose
    consideration has `assessment: false` is not an assessment (randomization,
    dosing), so its marks are left out of the Assessments count by default.

    `show: false` keeps a consideration out of the visit table's Key
    considerations column (Angus Gray, 29 October 2026) and changes nothing
    else: it is still matched, still left out of the Assessments count where
    `assessment` is false, and still named in the sentence saying what that
    count leaves out."""

    id: str
    label: str
    wording: list[str]
    assessment: bool = True
    show: bool = True


class ScheduleWording(BaseModel):
    """What the report prints for a Schedule of Events, `schedule_table.wording`
    in config/fields.yaml: the visit table's column headers and the sentences
    around it (Prompt C). Each `{name}` is filled by ReportWording.fill."""

    period: str
    timing: str
    visits: str
    assessments: str
    every_mark: str
    considerations: str
    where: str
    each: str
    footnote: str
    not_counted: str
    none_found: str
    unreadable: str
    shown_below: str
    totals_head: str
    total_line: str
    total_one: str
    says_line: str
    no_stage: str
    unplaced: str
    stated: str
    left_out: str
    flagged_table: str
    considerations_plain: str

    @model_validator(mode="after")
    def _names_only_its_own(self) -> ScheduleWording:
        allowed = {
            "each": {"n"}, "footnote": {"pages", "text"}, "not_counted": {"name", "pages"},
            "unreadable": {"pages"}, "shown_below": {"heading"}, "totals_head": {"heading"},
            "total_line": {"stage", "total", "parts"}, "total_one": {"stage", "total"},
            "says_line": {"stage", "says"}, "no_stage": {"periods"}, "unplaced": {"text"},
            "stated": {"value", "pages"}, "left_out": {"column", "items"},
            "flagged_table": {"heading"},
        }
        for name, value in self:
            unknown = set(placeholders(value)) - allowed.get(name, set())
            if unknown:
                raise ValueError(f"wording.{name} names {', '.join(sorted(unknown))}")
            if not value.strip():
                raise ValueError(f"wording.{name} is empty")
        for name in ("each", "total_line", "total_one", "says_line", "stated", "left_out"):
            missing = allowed[name] - set(placeholders(getattr(self, name)))
            if missing:
                raise ValueError(f"wording.{name} must name {', '.join(sorted(missing))}")
        return self


class ScheduleTable(BaseModel):
    """`schedule_table` in config/fields.yaml: how READ_SCHEDULE reads a protocol's
    Schedule of Events (Prompt C). See the comments there for each key."""

    wording: ScheduleWording
    headings: list[str]
    not_counted_headings: list[str] = Field(default_factory=list)
    marks: list[str]
    timing_starts: list[str]
    visit_separators: list[str]
    repeating: list[RepeatingVisit] = Field(default_factory=list)
    stages: list[ScheduleStage] = Field(default_factory=list)
    count_non_assessments: bool = False
    considerations: list[Consideration] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_considerations(self) -> ScheduleTable:
        ids = [c.id for c in self.considerations]
        if len(ids) != len(set(ids)):
            raise ValueError("two considerations share an id")
        return self


class Registry(BaseModel):
    version: int
    groups: list[GroupDef]
    fields: list[FieldDef]
    registry_version: str
    report_wording: ReportWording
    # None when config/fields.yaml has no `schedule_table` block: READ_SCHEDULE
    # then reads nothing and the visit rows are the extracted text as before.
    schedule_table: ScheduleTable | None = None

    def get_group(self, group_id: str) -> GroupDef:
        for g in self.groups:
            if g.id == group_id:
                return g
        raise KeyError(f"Unknown group: {group_id}")

    def get_field(self, field_id: str) -> FieldDef:
        for f in self.fields:
            if f.id == field_id:
                return f
        raise KeyError(f"Unknown field: {field_id}")

    def get_fields_for_group(self, group_id: str) -> list[FieldDef]:
        return [f for f in self.fields if f.group == group_id]


class RegistryValidationError(Exception):
    pass


def _validate_registry(groups: list[GroupDef], fields: list[FieldDef]) -> None:
    """Validate registry invariants. Raises RegistryValidationError on failure."""
    errors: list[str] = []
    group_ids = {g.id for g in groups}

    # Unique field ids
    seen_ids: set[str] = set()
    for f in fields:
        if f.id in seen_ids:
            errors.append(f"Duplicate field id: {f.id}")
        seen_ids.add(f.id)

    # Group references resolve
    for f in fields:
        if f.group not in group_ids:
            errors.append(f"Field {f.id} references unknown group: {f.group}")

    # Enum fields declare values
    for f in fields:
        if f.type in ("enum", "list[enum]") and not f.values:
            errors.append(f"Enum field {f.id} has no values declared")

    # Derived fields declare derived_from with valid references
    for f in fields:
        if f.derived:
            if not f.derived_from:
                errors.append(f"Derived field {f.id} has no derived_from")
            else:
                for ref in f.derived_from:
                    if ref not in seen_ids:
                        errors.append(
                            f"Derived field {f.id} references unknown field: {ref}"
                        )

    # Only a derived field reads rows from other fields, so only one can leave
    # some out, and a phrase with no words would match every scope.
    for f in fields:
        if f.leave_out_scopes is None:
            continue
        if not f.derived:
            errors.append(f"Field {f.id} leaves out scopes but is not derived")
        if not f.leave_out_scopes.phrases or not all(
            re.findall(r"[a-z0-9]+", p.lower()) for p in f.leave_out_scopes.phrases
        ):
            errors.append(f"Field {f.id} has an empty leave_out_scopes phrase")

    # RECONCILE reads records, and a derived field has none.
    for f in fields:
        if f.check_scopes_across_documents and f.derived:
            errors.append(f"Field {f.id} checks scopes across documents but is derived")

    # EXTRACT reads only_from, and a derived field is never extracted; a kind
    # named twice is a slip.
    for f in fields:
        if f.only_from and f.derived:
            errors.append(f"Field {f.id} has only_from but is derived")
        if len(f.only_from) != len(set(f.only_from)):
            errors.append(f"Field {f.id} names a document kind twice in only_from")

    # A value's word names a value the field allows.
    for f in fields:
        unknown = [v for v in f.value_words if not f.values or v not in f.values]
        if unknown:
            errors.append(
                f"Field {f.id} has value_words for values it does not allow: {', '.join(unknown)}"
            )
        # Two values printed as one word would read as one answer, and the report
        # folds lines that read the same.
        words = [w.casefold() for w in f.value_words.values()]
        if len(words) != len(set(words)):
            errors.append(f"Field {f.id} prints two values with the same value_words word")

    # Required words name values the field allows, and say something.
    for f in fields:
        unknown = [v for v in f.quote_must_say if not f.values or v not in f.values]
        if unknown:
            errors.append(
                f"Field {f.id} has quote_must_say for values it does not allow: "
                f"{', '.join(unknown)}"
            )
        if any(not [w for w in ws if w.strip()] for ws in f.quote_must_say.values()):
            errors.append(f"Field {f.id} has a quote_must_say value with no words")
        if any(not w.strip() for w in f.quote_must_mention):
            errors.append(f"Field {f.id} has an empty quote_must_mention word")
        if any(not p.strip() for p in f.prefer_sections):
            errors.append(f"Field {f.id} has an empty prefer_sections phrase")

    # A not-found note sends the reader to fields that exist, and a passage
    # saying why can only be told apart by its scope.
    for f in fields:
        note = f.when_not_found
        if note is None:
            continue
        for other in note.see:
            if other not in seen_ids or other == f.id:
                errors.append(f"Field {f.id} when_not_found sees unknown field {other}")
        if note.because_scopes and not f.scoped:
            errors.append(f"Field {f.id} has because_scopes but is not scoped")
        if any(not p.strip() for p in note.because_scopes):
            errors.append(f"Field {f.id} has an empty because_scopes phrase")
        unknown_ph = set(placeholders(note.because_line)) - {"quote", "pages"}
        unknown_ph |= set(placeholders(note.see_line)) - {"label"}
        if unknown_ph:
            errors.append(
                f"Field {f.id} when_not_found uses unknown placeholders: "
                f"{', '.join(sorted(unknown_ph))}"
            )

    # One visit table and one set of stage totals: the report prints each once,
    # and the totals are counted from the table's columns.
    kinds = [f.from_schedule for f in fields if f.from_schedule is not None]
    for which in ("table", "stage_totals"):
        if kinds.count(which) > 1:
            errors.append(f"More than one field has from_schedule: {which}")
    if "stage_totals" in kinds and "table" not in kinds:
        errors.append("A field has from_schedule: stage_totals but none has from_schedule: table")
    for f in fields:
        if f.from_schedule == "stage_totals" and (f.derived or not f.scoped or f.type != "int"):
            errors.append(
                f"Field {f.id} has from_schedule: stage_totals but is not a scoped int "
                "extracted field"
            )

    # A variable printed under another is printed under one that has a row of
    # its own: a real other variable, not itself shown under a third.
    for f in fields:
        target = f.shown_under
        if target is None:
            continue
        if target == f.id:
            errors.append(f"Field {f.id} is shown under itself")
        elif target not in seen_ids:
            errors.append(f"Field {f.id} is shown under an unknown field: {target}")
        elif next(o for o in fields if o.id == target).shown_under is not None:
            errors.append(
                f"Field {f.id} is shown under {target}, which is itself shown under "
                "another field"
            )

    # A follower's wording needs a disagreement to follow.
    for f in fields:
        if f.follow_wording is not None and f.follows_conflict_of is None:
            errors.append(f"Field {f.id} has follow_wording but follows no conflict")

    # A scopes display names its scopes by words, and a phrase with none would
    # take every scope under its heading.
    for f in fields:
        if not isinstance(f.display, ScopesDisplay):
            continue
        if not f.display.scopes:
            errors.append(f"Field {f.id} has a scopes display with no scopes")
        for heading in f.display.scopes:
            if not heading.phrases or not all(_words(p) for p in heading.phrases):
                errors.append(f"Field {f.id} has a scopes heading with an empty phrase")

    # The entries and periods displays, and the one-answer rule, read an entry's
    # keys, so the field must have entries and every key named must be one.
    pre_by_id = {f.id: f for f in fields}
    headline_of: dict[str, str] = {}
    for f in fields:
        keyed = isinstance(f.display, (EntriesDisplay, PeriodsDisplay))
        if (keyed or f.one_answer_when_documents_agree) and (
            f.type != "object[]" or not f.schema_def
        ):
            errors.append(f"Field {f.id} reads entry keys but is not object[] with a schema")
            continue
        # RECONCILE's scope check is what says every document lists the same
        # entries, so the one-answer rule cannot hold without it.
        if f.one_answer_when_documents_agree and not f.check_scopes_across_documents:
            errors.append(
                f"Field {f.id} is one answer when documents agree but does not check "
                "scopes across documents"
            )
        keys = {k.name: k for k in f.entry_keys()}
        if f.may_word_differently and not f.one_answer_when_documents_agree:
            errors.append(
                f"Field {f.id} has may_word_differently but is not one answer when "
                "documents agree"
            )
        not_text = [k for k in f.may_word_differently if k not in keys or keys[k].type != "text"]
        if not_text:
            errors.append(
                f"Field {f.id} may_word_differently names keys that are not text keys of "
                f"its schema: {', '.join(not_text)}"
            )
        if isinstance(f.display, EntriesDisplay):
            d = f.display
            special = {"n"} | ({d.kind_key} if d.kind_key else set())
            named = [p for w in d.title for p in placeholders(w) if p not in special]
            named += [p for alts in d.lines for w in alts for p in placeholders(w)]
            named += list(d.key_words) + ([d.label_key] if d.label_key else [])
            named += [d.kind_key] if d.kind_key else []
            for name in named:
                if name not in keys:
                    errors.append(f"Field {f.id} has an entries display naming key {name}")
            if d.kind_key in keys:
                kind = keys[d.kind_key]
                if kind.type != "enum":
                    errors.append(f"Field {f.id} has an entries kind_key that is not an enum")
                missing = [v for v in [*kind.values, "mixed"] if v not in d.count]
                if missing:
                    errors.append(
                        f"Field {f.id} has an entries display with no count word for: "
                        f"{', '.join(missing)}"
                    )
            for h in d.headline_fields:
                if h.field not in pre_by_id or h.field == f.id:
                    errors.append(f"Field {f.id} has a headline field it cannot use: {h.field}")
                elif pre_by_id[h.field].group != f.group:
                    errors.append(f"Field {f.id} has a headline field of another group: {h.field}")
                elif h.field in headline_of:
                    errors.append(f"Field {h.field} is a headline field of two displays")
                else:
                    headline_of[h.field] = f.id
        if isinstance(f.display, PeriodsDisplay):
            d2 = f.display
            named = [d2.order_key, *d2.one_per]
            named += [p for w in d2.heading for p in placeholders(w)]
            named += [p for w in d2.lines for p in placeholders(w) if p != "heading"]
            if not all(placeholders(w) for w in d2.heading):
                errors.append(f"Field {f.id} has a periods heading that names no key")
            for name in named:
                if name not in keys:
                    errors.append(f"Field {f.id} has a periods display naming key {name}")
            order = keys.get(d2.order_key)
            if order is not None and order.type != "enum":
                errors.append(f"Field {f.id} has a periods order_key that is not an enum")
            unknown = [v for v in d2.notes if order is None or v not in order.values]
            if unknown:
                errors.append(
                    f"Field {f.id} has periods notes for values its order_key does not allow: "
                    f"{', '.join(unknown)}"
                )
            unknown = [v for v in d2.always if order is None or v not in order.values]
            if unknown:
                errors.append(
                    f"Field {f.id} has periods always for values its order_key does not allow: "
                    f"{', '.join(unknown)}"
                )
            unknown = [v for v in d2.when_stated if order is None or v not in order.values]
            if unknown:
                errors.append(
                    f"Field {f.id} has periods when_stated for values its order_key does not "
                    f"allow: {', '.join(unknown)}"
                )
            not_text = [k for k in d2.may_word_differently
                        if k not in keys or keys[k].type != "text"]
            if not_text:
                errors.append(
                    f"Field {f.id} has periods may_word_differently naming keys that are not "
                    f"text keys of its schema: {', '.join(not_text)}"
                )
            for s in d2.see_also:
                if s.field not in pre_by_id or s.field == f.id:
                    errors.append(
                        f"Field {f.id} has a see_also on a field it cannot use: {s.field}"
                    )

    # A breakdown's parts are another variable's entries, and its wordings name
    # only the figures it has.
    for f in fields:
        if not isinstance(f.display, BreakdownDisplay):
            continue
        bd = f.display
        if bd.parts_from is not None and (
            bd.parts_from not in pre_by_id
            or not isinstance(pre_by_id[bd.parts_from].display, EntriesDisplay)
        ):
            errors.append(
                f"Field {f.id} has a breakdown whose parts_from has no entries display: "
                f"{bd.parts_from}"
            )
        if not f.scoped:
            errors.append(f"Field {f.id} has a breakdown display but is not scoped")
        allowed = {
            "part_line": (bd.part_line, {"part", "value"}),
            "total_line": (bd.total_line, {"value"}),
            "each_differs": (bd.each_differs, {"value"}),
            **{f"headline {i + 1}": (w, {"total", "each", "also"})
               for i, w in enumerate(bd.headline)},
        }
        for where, (w, names) in allowed.items():
            unknown = [p for p in placeholders(w) if p not in names]
            if unknown:
                errors.append(
                    f"Field {f.id} has a breakdown {where} naming {', '.join(unknown)}"
                )
        if "{value}" not in bd.each_differs:
            errors.append(f"Field {f.id} has a breakdown each_differs with no {{value}}")
        for h in bd.also:
            if h.field not in pre_by_id or h.field == f.id:
                errors.append(f"Field {f.id} has a breakdown also on a field it cannot use: "
                              f"{h.field}")

    # A field follows a real other field's disagreement, and not its own.
    for f in fields:
        if f.follows_conflict_of is None:
            continue
        if f.follows_conflict_of == f.id:
            errors.append(f"Field {f.id} follows its own conflict")
        elif f.follows_conflict_of not in seen_ids:
            errors.append(
                f"Field {f.id} follows the conflict of an unknown field: "
                f"{f.follows_conflict_of}"
            )

    # Resolution rules reference a real field, a value the field allows, and a
    # target the pipeline can write in one row.
    by_id = {f.id: f for f in fields}
    for f in fields:
        for rule in f.resolution_rules:
            trigger = by_id.get(rule.when_field)
            if trigger is None:
                errors.append(
                    f"Field {f.id} has a resolution rule on unknown field: {rule.when_field}"
                )
            elif trigger.values:
                unknown = [v for v in rule.when_value_in if v not in trigger.values]
                if unknown:
                    errors.append(
                        f"Field {f.id} has a resolution rule on {rule.when_field} "
                        f"values it does not allow: {', '.join(unknown)}"
                    )
            if rule.when_field == f.id:
                errors.append(f"Field {f.id} has a resolution rule on itself")
            if f.values and rule.value is not None and rule.value not in f.values:
                errors.append(
                    f"Field {f.id} has a resolution rule setting a value it does not "
                    f"allow: {rule.value}"
                )
            # One rule writes one row, so it cannot answer a field that resolves
            # once per country, cohort or arm.
            if f.scoped or (trigger is not None and trigger.scoped):
                errors.append(
                    f"Field {f.id} has a resolution rule, which is not supported for "
                    "scoped fields"
                )

    # A block takes rows out of its own group's table and prints its lines in
    # their place, so what it takes must be there to take, its value must be one
    # of them, and a line naming a variable it does not take is a quotation
    # from elsewhere, which needs its own sentence.
    for g in groups:
        if g.block is None:
            continue
        b = g.block
        for fid in b.replaces:
            if fid not in by_id:
                errors.append(f"Group {g.id} has a block replacing an unknown field: {fid}")
            elif by_id[fid].group != g.id:
                errors.append(f"Group {g.id} has a block replacing {fid}, of another group")
        if b.headline not in b.replaces:
            errors.append(f"Group {g.id} has a block whose headline it does not replace")
        for line in b.lines:
            if line.field not in by_id:
                errors.append(f"Group {g.id} has a block line on an unknown field: {line.field}")
            elif line.field not in b.replaces and line.wording is None:
                errors.append(
                    f"Group {g.id} has a block line on {line.field}, which it does not "
                    "replace, with no wording"
                )
            if line.wording is not None and "{value}" not in line.wording:
                errors.append(f"Group {g.id} has a block line wording with no {{value}}")

    if errors:
        raise RegistryValidationError(
            "Registry validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )


def load_registry(path: Path | None = None) -> Registry:
    """Load fields.yaml, validate, and return a typed Registry."""
    if path is None:
        path = Path(get_settings().fields_yaml_path)

    if not path.exists():
        raise FileNotFoundError(f"Fields registry not found: {path}")

    raw_bytes = path.read_bytes()
    content_hash = hashlib.sha256(raw_bytes).hexdigest()[:12]

    data: dict[str, Any] = yaml.safe_load(raw_bytes.decode())

    version = data.get("version", 0)
    groups = [GroupDef(**g) for g in data.get("groups", [])]
    fields: list[FieldDef] = []
    for f_data in data.get("fields", []):
        fields.append(FieldDef.model_validate(f_data))

    _validate_registry(groups, fields)

    if "report_wording" not in data:
        raise RegistryValidationError(f"{path} has no report_wording section")
    try:
        report_wording = ReportWording.model_validate(data["report_wording"])
    except ValueError as e:
        raise RegistryValidationError(f"report_wording in {path}: {e}") from e
    schedule_table = None
    if "schedule_table" in data:
        try:
            schedule_table = ScheduleTable.model_validate(data["schedule_table"])
        except ValueError as e:
            raise RegistryValidationError(f"schedule_table in {path}: {e}") from e

    registry_version = f"v{version}:{content_hash}"

    return Registry(
        version=version,
        groups=groups,
        fields=fields,
        registry_version=registry_version,
        report_wording=report_wording,
        schedule_table=schedule_table,
    )


@lru_cache(maxsize=1)
def get_registry() -> Registry:
    """Singleton access to the validated registry."""
    return load_registry()
