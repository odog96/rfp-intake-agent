"""Deterministic precedence tie-break for adjudicated "conflict" verdicts.

Per config/precedence.yaml's own header: this policy is "applied by
RECONCILE after ADJUDICATE returns a 'conflict' verdict" — the LLM judges
*whether* a disagreement is real (ARCHITECTURE.md §4.7); which value wins
is a code decision, not a model one, so an analyst can trace a resolved
value to a named rule instead of a black box. Called from adjudicate/.

Rules run in the order declared in precedence.yaml: recency, then
domain_authority, then specificity. If none is decisive, no_silent_resolution
applies — return no winner rather than guess (precedence.yaml rule 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from rfp_intake.domain.registry import FieldDef
from rfp_intake.domain.schemas import FieldRecord

# amendment/soa are protocol-family documents for domain-authority purposes —
# an amendment amends a protocol, an SoA is a protocol artifact.
_AUTHORITY_CAMP: dict[str, str | None] = {
    "protocol": "protocol",
    "amendment": "protocol",
    "soa": "protocol",
    "rfp": "rfp",
    "other": None,
}


@dataclass
class PrecedenceResult:
    # The record itself, not just its document: a caller that has only the
    # document id has to guess which of that document's records won, and can
    # pair one record's value with another's quote (CLAUDE.md open item 7).
    winner: FieldRecord | None
    rule_applied: str  # "recency" | "domain_authority" | "specificity" | "no_silent_resolution"

    @property
    def value(self) -> Any:  # Any: a record's normalized value has no single type
        return self.winner.value if self.winner else None

    @property
    def winning_doc_id(self) -> str | None:
        return self.winner.provenance.doc_id if self.winner else None

    @property
    def confidence(self) -> float:
        return self.winner.confidence if self.winner else 0.0


def apply_precedence(field_def: FieldDef, records: list[FieldRecord]) -> PrecedenceResult:
    """Pick a winning record among genuinely conflicting values, or none at all."""
    for rule_name, winner_fn in (
        ("recency", _recency_winner),
        ("domain_authority", lambda rs: _domain_authority_winner(field_def, rs)),
        ("specificity", _specificity_winner),
    ):
        winner = winner_fn(records)
        if winner is not None:
            return PrecedenceResult(winner=winner, rule_applied=rule_name)

    return PrecedenceResult(winner=None, rule_applied="no_silent_resolution")


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _recency_winner(records: list[FieldRecord]) -> FieldRecord | None:
    """Decisive only if every record has a parseable date and the latest is unique."""
    dated = [(r, _parse_date(r.provenance.doc_date)) for r in records]
    if any(d is None for d in (d for _, d in dated)):
        return None
    dated.sort(key=lambda pair: pair[1], reverse=True)  # type: ignore[arg-type,return-value]
    if len(dated) > 1 and dated[0][1] == dated[1][1]:
        return None  # tie — not decisive
    return dated[0][0]


def _domain_authority_winner(field_def: FieldDef, records: list[FieldRecord]) -> FieldRecord | None:
    """Decisive only if exactly one record's doc_kind matches the field's authoritative camp."""
    if field_def.source_priority not in ("protocol", "rfp"):
        return None  # source_priority: any — this rule does not fire
    matching = [
        r for r in records
        if _AUTHORITY_CAMP.get(r.provenance.doc_kind) == field_def.source_priority
    ]
    return matching[0] if len(matching) == 1 else None


def _specificity_winner(records: list[FieldRecord]) -> FieldRecord | None:
    """Extraction confidence as a proxy for "explicit value vs. implied by prose" —
    the closest signal already on hand; not a literal parse of source phrasing.
    Decisive only if every record with the highest confidence comes from one
    document; then the first of them wins.

    A tie inside one document is still that document's answer: run
    r-20261013-prompt-c gave two amendment records of 42 months 0.97 each, and
    the whole-study duration was left with no value although only one document
    stood against the RFP (item 1 of Oliver's Prompt B, 2026-10-16). A tie
    between documents is not decisive.
    """
    max_conf = max(r.confidence for r in records)
    top = [r for r in records if r.confidence == max_conf]
    return top[0] if len({r.provenance.doc_id for r in top}) == 1 else None
