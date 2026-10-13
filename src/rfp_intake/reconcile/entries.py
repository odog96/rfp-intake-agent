"""One `object[]` entry's keys merged across the documents that state it.

Added 2026-10-12, fix 1 of that day's brief. RECONCILE folds a collection's
records into one row per scope (`_merge_collection`), so an arm the amendment
and the RFP both describe is one row holding both documents' entries. This
module reads those entries key by key: for each key, which value to show, which
document each value came from, and whether the documents agree. GATE reads the
agreement (`one_answer_when_documents_agree`) and RENDER the shown values, so
both read the same merge. Pure functions over records; no I/O, no LLM.

The value shown for a key is chosen by `reconcile/precedence.py`, the same
rules a disagreement is settled by, over each document's most confident record
stating the key. Where no rule decides, the most confident of those records is
shown, and the first of them on a tie. A text key's other wordings are not
dropped: they stay in `by_doc` and in Appendix A.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rfp_intake.domain.registry import EntryKey, FieldDef
from rfp_intake.domain.schemas import FieldRecord
from rfp_intake.reconcile.precedence import apply_precedence


def same_key(value: Any) -> str:
    """Two values read the same if they differ only in case and spacing."""
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


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
            if doc not in best or r.confidence > best[doc].confidence:
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
    top = max(r.confidence for r in candidates)
    return next(r for r in candidates if r.confidence == top)


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
