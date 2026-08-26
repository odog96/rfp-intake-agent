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
    value: Any
    winning_doc_id: str | None
    confidence: float
    rule_applied: str  # "recency" | "domain_authority" | "specificity" | "no_silent_resolution"


def apply_precedence(field_def: FieldDef, records: list[FieldRecord]) -> PrecedenceResult:
    """Pick a winning record among genuinely conflicting values, or none at all."""
    for rule_name, winner_fn in (
        ("recency", _recency_winner),
        ("domain_authority", lambda rs: _domain_authority_winner(field_def, rs)),
        ("specificity", _specificity_winner),
    ):
        winner = winner_fn(records)
        if winner is not None:
            return PrecedenceResult(
                value=winner.value,
                winning_doc_id=winner.provenance.doc_id,
                confidence=winner.confidence,
                rule_applied=rule_name,
            )

    return PrecedenceResult(
        value=None, winning_doc_id=None, confidence=0.0, rule_applied="no_silent_resolution",
    )


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
    Decisive only if the highest confidence is unique.
    """
    max_conf = max(r.confidence for r in records)
    top = [r for r in records if r.confidence == max_conf]
    return top[0] if len(top) == 1 else None
