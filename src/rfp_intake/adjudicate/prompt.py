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
period are accounted for, because they are about different things (e.g. one is a \
total and one is per-country; one is per-cycle and one is per-course; two \
different items of sponsor guidance).
- reconcilable: both values are true; one is a subset, restatement, or more \
specific version of the other. The same answer worded differently (the same \
primary objective or population in each document's own words) is reconcilable, \
not not_a_conflict. Set winning_record to the NUMBER of the one \
candidate record below whose value should be shown as the field's resolved \
value — "3" for the third candidate in the list. Several candidates often come \
from the same document and say different things, so naming a document is not \
enough: name the record. If you cannot choose a single record, leave \
winning_record unset and the highest-confidence record is used.
- conflict: the values are genuinely incompatible and cannot both be correct. Two \
different amounts for the same quantity at the same scope are a conflict even when \
they are close or one says "approximately" (36 months against 30 months for the whole \
study; 120 subjects against 110): a budget is built on one number, so a person must choose. \
Do not set winning_record for a conflict verdict — a separate deterministic \
precedence rule decides that, not you.

off_topic_records: the NUMBERS of any candidate records that are not about \
this field at all — a passage about something else that was read as this \
field (for example, an instruction for measuring a laboratory sample read as \
a site monitoring strategy). Judge your verdict on the other records only, and \
say in the explanation why each listed record is not about the field. A \
record that is about the field but gives a different value is never \
off-topic. But a record for a different quantity that uses the same words IS \
off-topic: for example a sample size the statistical analysis needs (from a power \
calculation) where the field asks for the number of subjects to be enrolled, which the \
field's hint gives its own scope. Leave the list empty when every record is about the field.

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
        *([f"What the field asks for: {' '.join(field_def.hint.split())}"]
          if field_def.hint else []),
        f"Budget driver: {'yes' if field_def.budget_driver else 'no'}",
        f"Domain authority for this field: {authority} — {authority_note}",
        "",
        "CANDIDATE RECORDS, numbered — several may come from the same document:",
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
