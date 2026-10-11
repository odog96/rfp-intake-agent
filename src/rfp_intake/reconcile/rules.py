"""Answers that follow from another field's answer, written in configuration.

Change 3 of the 2026-10-07 review. Angus Gray's rule, from the call transcript
at 00:29-00:32: a Phase 2, 3 or 4 study is never first-in-human, so the answer
is No at full confidence and nothing needs to be searched for. Only Phase 1 and
Phase 1/2 need checking.

The rule itself is in config/fields.yaml (`resolution_rules` on the field it
answers, see domain/registry.ResolutionRule), not here: non-negotiable rule 4
forbids keying pipeline behaviour to a field name in Python, and nothing in this
module names a field. Adding a second rule of the same shape — "a single-arm
study has no placebo", say — is a YAML edit and no code change.

Why not a derived field: DERIVE is the other place in the pipeline where one
field's answer is worked out from others, and it would be the natural home. But
every rubric in derive/rubric.py returns status="needs_review", and GATE then
keeps a derived field at needs_review whatever its confidence (gate/__init__.py).
A derived First-in-human could therefore never read "Confirmed", which is what
the review asked for. These rules resolve a field the ordinary way instead, so
GATE gates it the ordinary way.

Known limitation, deliberate: the rule reads the trigger field from RECONCILE's
own output, so it cannot fire when the documents disagree about the trigger —
a contradiction carries no resolved value until ADJUDICATE has judged it. With
the study phase in dispute the pipeline falls back to extracting the answer, and
a reviewer sees the phase disagreement either way.
"""

from __future__ import annotations

import structlog

from rfp_intake.domain.registry import FieldDef, Registry, ResolutionRule
from rfp_intake.domain.schemas import Contradiction, ResolvedField

logger = structlog.get_logger()

# A rule never fires off one of these: the trigger field has no answer to read.
_NO_ANSWER = ("not_found", "not_specified")


def apply_resolution_rules(
    registry: Registry,
    resolved: list[ResolvedField],
    contradictions: list[Contradiction],
) -> tuple[list[ResolvedField], list[Contradiction]]:
    """Rewrite the fields whose answer a rule in config/fields.yaml decides.

    The rule's answer replaces whatever was extracted for that field, including a
    candidate contradiction: a clinical rule a reviewer has stated is not
    something the documents can outvote, and asking an analyst to settle a
    disagreement the rule already settles is the noise this change removes. The
    records themselves are untouched and stay in extraction.json.
    """
    for field_def in registry.fields:
        for rule in field_def.resolution_rules:
            source = _trigger(resolved, rule)
            if source is None:
                continue
            resolved = [rf for rf in resolved if rf.field_id != field_def.id]
            resolved.append(_answer(field_def, rule, source))
            contradictions = [c for c in contradictions if c.field_id != field_def.id]
            logger.info(
                "resolution_rule_applied",
                field_id=field_def.id,
                when_field=rule.when_field,
                value=rule.value,
            )
            break  # One field, one answer: the first rule that matches decides.
    return resolved, contradictions


def _trigger(resolved: list[ResolvedField], rule: ResolutionRule) -> ResolvedField | None:
    """The entry for the rule's trigger field whose value the rule names."""
    return next(
        (
            rf
            for rf in resolved
            if rf.field_id == rule.when_field
            and rf.status not in _NO_ANSWER
            and _token(rf.value) in rule.when_value_in
        ),
        None,
    )


def _token(value: object) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _answer(
    field_def: FieldDef, rule: ResolutionRule, source: ResolvedField
) -> ResolvedField:
    """The rule's answer, carrying the trigger field's passage as its evidence.

    There is no passage about the field itself to quote — the whole point is that
    none is needed — so the quote is the one that established the trigger's
    answer. Non-negotiable rule 2 is kept: the value still has evidence behind
    it, a reviewer can still turn to the page, and `notes` says in words why that
    page answers this field.
    """
    return ResolvedField(
        field_id=field_def.id,
        value=rule.value,
        status="needs_review",  # GATE decides confirmed vs needs_review, as ever
        confidence=rule.confidence,
        sources=list(source.sources),
        quote=source.quote,
        scope=None,
        notes=rule.because,
    )
