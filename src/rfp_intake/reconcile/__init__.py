"""RECONCILE — group records by (field_id, scope), resolve agreements,
flag disagreements as contradiction candidates. Pure Python, no LLM.

Per ARCHITECTURE.md §4.6:
  1 record                          -> resolved, confidence carried through
  n records, canonical values equal -> resolved, confidence boosted
  n records, canonical values differ -> Contradiction candidate, not resolved

Status handling: this node never writes "confirmed" — that is GATE's call
(ARCHITECTURE.md §4.9). A field with an agreed value is written here with
status="needs_review" as a safe default; GATE promotes it to "confirmed"
once the confidence threshold is met. not_found/not_specified are already
terminal and GATE leaves them untouched.

A field with a genuine value disagreement gets a Contradiction candidate
(verdict=None) appended to state.contradictions, and no ResolvedField for
it here — ADJUDICATE (the next node) is what turns each candidate into one
or more ResolvedFields, once it has judged whether the disagreement is
real. Emitting a value here, next to a contradiction nobody has judged
yet, would look more resolved than it is.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import structlog

from rfp_intake.domain.registry import FieldDef, get_registry
from rfp_intake.domain.schemas import (
    Contradiction,
    FieldRecord,
    ResolvedField,
    RunError,
    RunState,
)
from rfp_intake.normalize.scope import normalize_scope

logger = structlog.get_logger()

# Bonus per additional independent corroborating source, capped at 1.0.
# Not calibrated against the golden set yet — see ARCHITECTURE.md §9.
CORROBORATION_BOOST_PER_SOURCE = 0.05


def reconcile_node(state: RunState) -> dict[str, Any]:
    """RECONCILE graph node. Reads state.records, writes state.resolved / contradictions."""
    registry = get_registry()
    errors: list[RunError] = []
    resolved: list[ResolvedField] = []
    contradictions: list[Contradiction] = []

    records_by_field: dict[str, list[FieldRecord]] = defaultdict(list)
    for record in state.records:
        records_by_field[record.field_id].append(record)

    non_derived_fields = [f for f in registry.fields if not f.derived]

    for field_def in non_derived_fields:
        try:
            field_resolved, field_contradictions = _reconcile_field(
                field_def, records_by_field.get(field_def.id, [])
            )
            resolved.extend(field_resolved)
            contradictions.extend(field_contradictions)
        except Exception as e:
            logger.error("reconcile_field_failed", field_id=field_def.id, error=str(e))
            errors.append(RunError(
                node="RECONCILE",
                task_id=field_def.id,
                error=f"Reconciliation failed for {field_def.id}: {e}",
            ))

    logger.info(
        "reconcile_complete",
        run_id=state.run_id,
        resolved=len(resolved),
        contradictions=len(contradictions),
    )

    return {"resolved": resolved, "contradictions": contradictions, "errors": errors}


def _reconcile_field(
    field_def: FieldDef, records: list[FieldRecord]
) -> tuple[list[ResolvedField], list[Contradiction]]:
    """Reconcile all records for one field across every scope it appears with."""
    if not records:
        return [_not_found(field_def.id)], []

    found = [r for r in records if r.status == "found"]
    not_specified = [r for r in records if r.status == "not_specified"]

    # An explicit value from any document is informative and wins over another
    # document merely saying "not applicable" for the same field/scope.
    if not found:
        if not_specified:
            best = max(not_specified, key=lambda r: r.confidence)
            return [ResolvedField(
                field_id=field_def.id,
                value=None,
                status="not_specified",
                confidence=best.confidence,
                sources=[best.provenance],
                quote=best.quote,
                scope=best.scope,
            )], []
        return [_not_found(field_def.id)], []

    # Group on the canonical scope, not the literal label. The documents name the
    # same arm differently on purpose and recognising that is this node's job —
    # grouping on raw text resolved the placebo arm three times in run
    # r-sonnet46-pair-114917 (cohort:Placebo, cohort:placebo, cohort:Placebo+SoC).
    by_scope: dict[str | None, list[FieldRecord]] = defaultdict(list)
    display_scope: dict[str | None, str | None] = {}
    for r in found:
        key = normalize_scope(r.scope)
        by_scope[key].append(r)
        # Keep the first label seen for the reader; the canonical form is an
        # internal grouping key and is not what belongs in a report.
        display_scope.setdefault(key, r.scope)

    resolved: list[ResolvedField] = []
    contradictions: list[Contradiction] = []

    for key, scope_records in by_scope.items():
        scope = display_scope[key]

        # A field the registry declares as a collection cannot have its members
        # disagree with each other: two records naming different pieces of
        # evidence are both right, and are one answer with several parts. Only
        # single-valued fields can contradict.
        if _is_collection(field_def):
            resolved.append(_merge_collection(field_def, scope_records, scope))
            continue

        value_groups = _group_by_canonical_value(scope_records)

        if len(value_groups) == 1:
            group = value_groups[0]
            best = max(group, key=lambda r: r.confidence)
            confidence = best.confidence
            if len(group) > 1:
                boost = CORROBORATION_BOOST_PER_SOURCE * (len(group) - 1)
                confidence = min(1.0, confidence + boost)
            resolved.append(ResolvedField(
                field_id=field_def.id,
                value=best.value,
                status="needs_review",  # GATE decides confirmed vs needs_review
                confidence=confidence,
                sources=[r.provenance for r in group],
                quote=best.quote,
                scope=scope,
            ))
        else:
            # Genuine disagreement within the same scope — candidate only.
            # No ResolvedField emitted for this (field_id, scope) until
            # ADJUDICATE exists and judges it.
            contradictions.append(Contradiction(
                field_id=field_def.id,
                records=scope_records,
            ))

    return resolved, contradictions


# Types in config/fields.yaml whose value is a collection rather than one answer.
# `list[enum]` and `list[text]` hold members; `object[]` holds entries such as one
# row per study period. Any other type — int, text, enum, bool — is a single
# answer, and two different values for it are a genuine disagreement.
_COLLECTION_TYPES = ("list[enum]", "list[text]", "object[]")


def _is_collection(field_def: FieldDef) -> bool:
    return field_def.type in _COLLECTION_TYPES


def _merge_collection(
    field_def: FieldDef, records: list[FieldRecord], scope: str | None
) -> ResolvedField:
    """Fold every record for one collection field and scope into a single answer.

    Before this, RECONCILE compared collection members as if they competed:
    `visits.intensity_evidence` produced 38 of the 158 rows in run
    r-20260827-205037 on its own, one row per piece of evidence found, and
    ADJUDICATE was asked to judge disagreements that were never disagreements.

    Members are deduplicated while preserving the order they were found in, which
    keeps the report reading in document order rather than an arbitrary one.
    """
    members: list[Any] = []
    for record in records:
        value = record.value if record.value is not None else record.raw_value
        for member in value if isinstance(value, list) else [value]:
            if member is None:
                continue
            if member not in members:
                members.append(member)

    best = max(records, key=lambda r: r.confidence)
    confidence = best.confidence
    if len(records) > 1:
        confidence = min(1.0, confidence + CORROBORATION_BOOST_PER_SOURCE * (len(records) - 1))

    return ResolvedField(
        field_id=field_def.id,
        value=members,
        status="needs_review",  # GATE decides confirmed vs needs_review
        confidence=confidence,
        sources=[r.provenance for r in records],
        quote=best.quote,
        scope=scope,
    )


def _group_by_canonical_value(records: list[FieldRecord]) -> list[list[FieldRecord]]:
    """Bucket records by equal `.value` without requiring it to be hashable."""
    groups: list[list[FieldRecord]] = []
    for r in records:
        for g in groups:
            if g[0].value == r.value:
                g.append(r)
                break
        else:
            groups.append([r])
    return groups


def _not_found(field_id: str) -> ResolvedField:
    return ResolvedField(
        field_id=field_id,
        value=None,
        status="not_found",
        confidence=0.0,
        sources=[],
        quote=None,
    )
