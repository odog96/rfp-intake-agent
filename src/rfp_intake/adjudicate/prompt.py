"""Prompt construction for ADJUDICATE. See ARCHITECTURE.md §4.7."""

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from rfp_intake.domain.precedence import PrecedencePolicy
from rfp_intake.domain.registry import FieldDef
from rfp_intake.domain.schemas import Contradiction

ADJUDICATE_SYSTEM_PROMPT = """\
You are adjudicating one specific disagreement between clinical study documents \
for a delivery-budgeting team. You are given every candidate value already found \
for ONE field — do not look for other contradictions, only judge this one.

Decide exactly one verdict:
- not_a_conflict: the values don't actually disagree once scope, unit, or study \
period are accounted for (e.g. one is a total and one is per-country; one is \
per-cycle and one is per-course). Expect this to be the most common verdict.
- reconcilable: both values are true; one is a subset, restatement, or more \
specific version of the other. Set winning_doc_id to the document whose value \
should be shown as the field's resolved value.
- conflict: the values are genuinely incompatible and cannot both be correct. \
Do not set winning_doc_id for a conflict verdict — a separate deterministic \
precedence rule decides that, not you.

Your explanation must be complete enough for an analyst to act on without \
re-reading the source documents: state both values, and name the specific \
distinguishing detail (or the specific absence of one) that drove your verdict. \
"Values disagree" is not an acceptable explanation.

severity: "high" if this field directly drives the delivery budget (site counts, \
subject counts, visit counts, monitoring frequency, study duration); "medium" if \
it shapes the delivery approach but not the headline cost; "low" if descriptive \
or contextual.
"""


def build_adjudicate_prompt(
    contradiction: Contradiction, field_def: FieldDef, policy: PrecedencePolicy
) -> list[BaseMessage]:
    domain_rule = policy.get_rule("domain_authority")
    authority = field_def.source_priority or "any"
    authority_note = (domain_rule.mapping or {}).get(authority, "no automatic winner")

    lines = [
        f"FIELD: {field_def.label} ({field_def.id})",
        f"Budget driver: {'yes' if field_def.budget_driver else 'no'}",
        f"Domain authority for this field: {authority} — {authority_note}",
        "",
        "CANDIDATE VALUES FROM DIFFERENT DOCUMENTS:",
    ]
    for i, r in enumerate(contradiction.records, start=1):
        lines.append(
            f"{i}. doc_id={r.provenance.doc_id} kind={r.provenance.doc_kind} "
            f"date={r.provenance.doc_date or 'unknown'} page={r.provenance.page} "
            f"scope={r.scope or 'unscoped'} confidence={r.confidence:.2f}\n"
            f"   value: {r.value!r}\n"
            f'   quote: "{r.quote}"'
        )

    return [
        SystemMessage(content=ADJUDICATE_SYSTEM_PROMPT),
        HumanMessage(content="\n".join(lines)),
    ]
