"""One `object[]` entry's keys merged across the documents that state it.

Added 2026-10-12, fix 1 of that day's brief. RECONCILE folds a collection's
records into one row per scope (`_merge_collection`), so an arm the amendment
and the RFP both describe is one row holding both documents' entries. This
module reads those entries key by key: for each key, which value to show, which
document each value came from, and whether the documents agree. GATE reads the
agreement (`one_answer_when_documents_agree`) and RENDER the shown values, so
both read the same merge. Pure functions over records; no I/O, no LLM.

The value shown for a key is chosen by `reconcile/precedence.py`, the same
rules a disagreement is settled by, over each document's best record stating
the key: one read from a section the field's `prefer_sections` names, else one
whose own quote states the key's value (`quote_states`, since 23 November
2026), else the most confident (since 2026-10-12). Where no rule
decides, the best of those records is shown, by the section and confidence
ranking, and the first of them on a tie. A text key's other wordings are not
dropped: they stay in `by_doc` and in Appendix A.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from rfp_intake.domain.registry import (
    EntryKey,
    FieldDef,
    PeriodsDisplay,
    placeholders,
    scope_has_phrase,
)
from rfp_intake.domain.schemas import FieldRecord
from rfp_intake.normalize.duration import amounts_in
from rfp_intake.reconcile.precedence import apply_precedence


def same_key(value: Any) -> str:
    """Two values read the same if they differ only in case and spacing."""
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def length_key(value: Any) -> str:
    """A text value as `MergedEntry.stated_two_ways` compares it: the lengths
    it gives, in order ("28 days" of "a 28-day period"), a range by both its
    ends, where it gives any, else its words as `same_key` reads them. Nothing
    is converted: 4 weeks and 28 days are two lengths."""
    amounts = amounts_in(str(value))
    return repr(amounts) if amounts else same_key(value)


@dataclass(frozen=True)
class KeyMerge:
    key: EntryKey
    # The record whose value is shown, and that value.
    shown: FieldRecord
    value: Any
    # Each document's distinct values for the key, in the order stated, each
    # with the first record stating it.
    by_doc: dict[str, list[tuple[Any, FieldRecord]]]

    @property
    def agreed(self) -> bool:
        """Every record stating the key gives the same value."""
        return len({same_key(v) for vs in self.by_doc.values() for v, _ in vs}) == 1

    @property
    def stated_by(self) -> list[str]:
        return list(self.by_doc)


@dataclass(frozen=True)
class MergedEntry:
    # Only the keys some record states, in `schema` order.
    keys: dict[str, KeyMerge]
    # Every document that states the entry, in the order first stated.
    docs: list[str]

    def value(self, name: str) -> Any | None:
        merged = self.keys.get(name)
        return merged.value if merged is not None else None

    @property
    def agrees(self) -> bool:
        """Every key but text has one value across every record. A text key's
        wordings differ between documents without the documents disagreeing."""
        return all(m.agreed for m in self.keys.values() if m.key.type != "text")

    def differing(self, may_word_differently: list[str] | None) -> list[str]:
        """The keys the records give different values for: every key but text,
        and, where `may_word_differently` is given, a text key two or more
        documents state that it does not name. A key one document alone states
        disagrees with nothing."""
        return [
            name for name, m in self.keys.items()
            if not m.agreed and (
                m.key.type != "text"
                or (may_word_differently is not None and len(m.by_doc) > 1
                    and name not in may_word_differently)
            )
        ]

    def stated_two_ways(
        self, may_word_differently: list[str], names: Collection[str] = (),
        grouped_by: Collection[str] = (),
    ) -> list[str]:
        """The keys some two records give different values for, whether two
        documents state them or one document states them twice. Unlike
        `differing`, one document can disagree with itself here: a treatment
        period of 24 months on one page and 36 months on another is a difference
        a person must see (pipeline-rules-reviewer, 23 November 2026). A text
        key is compared by the lengths it gives where it gives any
        (`length_key`), so "28 days" and "a 28-day period" read the same and
        "28 days" and "4 weeks" do not.

        The text keys `may_word_differently` names (a period's name, the day or
        visit it starts and ends at) may be worded differently while one
        document states them: "Day -1" and "Month 1-Day 1" for the end of the
        amendment's screening. Two documents stating one of them agree only
        where they share a wording of it, compared word for word (`same_key`),
        not by its lengths: an `end` can hold the event a treatment runs until,
        "approximately 300 primary endpoint events" in one document and
        "approximately 250" in the other, or "24 months after the last subject
        is randomized" and "24 months after the first" (pipeline-rules-reviewer,
        second reading, 23 November 2026).

        The text keys `names` names only name the entry ("Screening",
        "Screening Period") and are no difference, whoever words them, where
        another key is stated by two documents, so the documents' passages are
        compared on something: a "Treatment Period" of 24 months and a "Study
        drug treatment" until 156 events share no key but the name, and their
        names are then compared as `may_word_differently` keys are
        (pipeline-rules-reviewer, 23 November 2026). The caller passes them
        only where the report prints the entry under a heading of its own
        (`PeriodsDisplay.names_of`). The keys the entries are grouped by
        (`grouped_by`), which every record of the entry states alike, compare
        nothing."""
        compared = any(
            len(m.by_doc) > 1 for name, m in self.keys.items()
            if name not in names and name not in grouped_by
        )
        out = []
        for name, m in self.keys.items():
            if name in names and compared:
                continue
            if m.key.type != "text":
                if not m.agreed:
                    out.append(name)
            elif name in may_word_differently or name in names:
                wordings = [{same_key(v) for v, _ in vs} for vs in m.by_doc.values()]
                if len(wordings) > 1 and not set.intersection(*wordings):
                    out.append(name)
            elif len({length_key(v) for vs in m.by_doc.values() for v, _ in vs}) > 1:
                out.append(name)
        return out

    def one_answer(self, may_word_differently: list[str]) -> bool:
        """The documents state this entry as one answer: every key two or more
        documents state reads the same in each, but the keys that only name the
        entry."""
        return not self.differing(may_word_differently)

    @property
    def shown_records(self) -> list[FieldRecord]:
        out: list[FieldRecord] = []
        for m in self.keys.values():
            if m.shown not in out:
                out.append(m.shown)
        return out


def _entry(record: FieldRecord) -> dict[str, Any]:
    """A record's entry, or no keys when its value is not one."""
    return record.value if isinstance(record.value, dict) else {}


def merge_entry(field_def: FieldDef, statements: list[FieldRecord]) -> MergedEntry:
    """The records of one entry, merged key by key. A record whose value is not
    an entry (a dict) states no key."""
    entries = [r for r in statements if isinstance(r.value, dict)]
    docs: list[str] = []
    for r in entries:
        if r.provenance.doc_id not in docs:
            docs.append(r.provenance.doc_id)

    keys: dict[str, KeyMerge] = {}
    for key in field_def.entry_keys():
        stating = [r for r in entries if key.name in _entry(r)]
        if not stating:
            continue
        by_doc: dict[str, list[tuple[Any, FieldRecord]]] = {}
        best: dict[str, FieldRecord] = {}
        for r in stating:
            doc = r.provenance.doc_id
            said = _entry(r)[key.name]
            seen = by_doc.setdefault(doc, [])
            if all(same_key(v) != same_key(said) for v, _ in seen):
                seen.append((said, r))
            if doc not in best or (
                _key_rank(field_def, r, key.name) > _key_rank(field_def, best[doc], key.name)
            ):
                best[doc] = r
        shown = _shown(field_def, key, list(best.values()))
        keys[key.name] = KeyMerge(
            key=key, shown=shown, value=_entry(shown)[key.name], by_doc=by_doc,
        )
    return MergedEntry(keys=keys, docs=docs)


def _shown(field_def: FieldDef, key: EntryKey, candidates: list[FieldRecord]) -> FieldRecord:
    """Precedence over each document's best record, read as this key's value."""
    as_key = [r.model_copy(update={"value": _entry(r)[key.name]}) for r in candidates]
    winner = apply_precedence(field_def, as_key).winner
    if winner is not None:
        return candidates[as_key.index(winner)]
    top = max(record_rank(field_def, r) for r in candidates)
    return next(r for r in candidates if record_rank(field_def, r) == top)


_WORD = re.compile(r"[a-z0-9]+")


def quote_support(record: FieldRecord, said: Any) -> float:
    """The share of the words of `said`, one key's value, that the record's own
    quote contains. EXTRACT checks an entry's counts against its quote but not
    its text keys (extract/validate.py:validate_entry), so a record can carry a
    dose its passage never states: in run r-20261123-oct29-run4 the amendment's
    p.15 record gave the NEOD001 arm "administered intravenously" beside a quote
    about enrolment, and was shown, being the more confident, over p.19's
    "NEOD001 (24 mg/kg) IV infusion", whose quote states the 24 mg/kg."""
    words = _WORD.findall(str(said).casefold())
    if not words:
        return 0.0
    quoted = set(_WORD.findall(record.quote.casefold()))
    return sum(w in quoted for w in words) / len(words)


# A record's quote states a value when it holds at least half of the value's
# words. Only a record whose quote does not is passed over; among those whose
# quotes do, the more confident is still shown, as before.
QUOTE_STATES = 0.5


def quote_states(record: FieldRecord, said: Any) -> bool:
    """Whether the record's own quote states `said` (`QUOTE_STATES`)."""
    return quote_support(record, said) >= QUOTE_STATES


def _key_rank(field_def: FieldDef, record: FieldRecord, name: str) -> tuple[bool, bool, float]:
    """One document's records stating a key, best first: read from a preferred
    section, then one whose quote states the key's value, then the more
    confident."""
    preferred, confidence = record_rank(field_def, record)
    return preferred, quote_states(record, _entry(record)[name]), confidence


def record_rank(field_def: FieldDef, record: FieldRecord) -> tuple[bool, float]:
    """A record read from one of the field's `prefer_sections` (the schedule
    table) before any other, whatever their confidence; then the more confident.
    Addition 1 to stage 6 of the 2026-10-12 plan."""
    preferred = scope_has_phrase(record.provenance.section, field_def.prefer_sections)
    return preferred, record.confidence


def group_entries(statements: list[FieldRecord], one_per: list[str]) -> list[list[FieldRecord]]:
    """Records grouped by their values of the `one_per` keys, read as `same_key`
    reads them, in the order each group is first stated. A key a record does not
    state reads as empty."""
    groups: dict[tuple[str, ...], list[FieldRecord]] = {}
    for r in statements:
        if not isinstance(r.value, dict):
            continue
        k = tuple(same_key(r.value.get(name, "")) for name in one_per)
        groups.setdefault(k, []).append(r)
    return list(groups.values())


def period_groups(records: list[FieldRecord], display: PeriodsDisplay) -> list[list[FieldRecord]]:
    """`records` grouped one per stretch, by the `one_per` keys. Where the
    display lists the stretches it `always` prints, a stretch it names in
    neither `always` nor `when_stated` (`other`) is also told apart by the keys
    its heading names: two stretches
    the documents name differently, such as an enrolment period and an end of
    treatment visit, are two rows, each with its own pages, not one row
    citing the other's page."""
    groups = group_entries(records, display.one_per)
    if not display.always:
        return groups
    named = [k for w in display.heading for k in placeholders(w) if k not in display.one_per]
    out: list[list[FieldRecord]] = []
    for group in groups:
        value = group[0].value.get(display.order_key) if isinstance(group[0].value, dict) else None
        if named and display.named(str(value)) is None:
            out += group_entries(group, display.one_per + list(dict.fromkeys(named)))
        else:
            out.append(group)
    return out
