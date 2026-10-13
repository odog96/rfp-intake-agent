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
from rfp_intake.normalize.scope import normalize_scope, scope_named_in
from rfp_intake.reconcile.rules import apply_resolution_rules

logger = structlog.get_logger()

# Bonus per additional independent corroborating source, capped at 1.0.
# Not calibrated against the golden set yet — see ARCHITECTURE.md §9.
CORROBORATION_BOOST_PER_SOURCE = 0.05

# Values that answer "the document does not say", written as a value rather than
# as a record status. Several yes/no variables in config/fields.yaml offer
# `not_specified` among their allowed values (design.first_in_human is one), so a
# passage that does not mention the subject comes back as a *found* record whose
# value is this token — status "not_specified" is only used when EXTRACT found no
# passage to read at all. The normalizer writes the underscored form for enum
# fields (normalize/enum_maps.py YES_NO_MAP); a text field keeps whatever the
# document said, hence the spaced form too.
#
# Non-negotiable rule 3 still holds: this is not a claim that `not_specified` is
# the same as `not_found` or as `0`. It stays a distinct value, it is still
# recorded, and it is still cited in the report. It simply does not compete with
# a real answer — see _competing_records.
_SAYS_NOTHING = frozenset({"not_specified", "not specified"})


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
        errors.extend(_dropped_not_specified(field_def, records_by_field.get(field_def.id, [])))
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

    # Last, because a rule reads the answer this node just worked out for the
    # field it depends on. See reconcile/rules.py.
    resolved, contradictions = apply_resolution_rules(registry, resolved, contradictions)

    logger.info(
        "reconcile_complete",
        run_id=state.run_id,
        resolved=len(resolved),
        contradictions=len(contradictions),
    )

    return {"resolved": resolved, "contradictions": contradictions, "errors": errors}


def _dropped_not_specified(
    field_def: FieldDef, records: list[FieldRecord]
) -> list[RunError]:
    """One run error for each not_specified record `_reconcile_field` drops.

    A field with a found record anywhere keeps no not_specified record, so a
    document's "none" vanished without a trace. Item 3 of Oliver's 2026-10-13
    prompt: run r-20261012-angus2 confirmed RFP p.4's one interim analysis, and
    whether the amendment's "No interim analyses are planned" was read at all
    could not be told. The record is still dropped; it is now written down with
    its page and quote. kind="validation", because the model call worked.
    """
    if not any(r.status == "found" for r in records):
        return []
    return [
        RunError(
            node="RECONCILE",
            task_id=f"{r.provenance.doc_id}:{r.group}:{field_def.id}",
            error="Dropped: not_specified, because another record gives this field a value",
            kind="validation",
            rejected_value=r.raw_value,
            rejected_quote=r.quote,
            rejected_page=r.provenance.page,
        )
        for r in records
        if r.status == "not_specified"
    ]


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
    unmatched: set[str | None] = set()
    for r in found:
        label = r.scope
        if field_def.check_scopes_across_documents and not _names_its_scope(r):
            # Kept out of the match: its own document's row, under a label that
            # says so. See _scope_checks.
            label = f"{r.scope} (not matched, {r.provenance.doc_id})"
            unmatched.add(normalize_scope(label))
        key = normalize_scope(label)
        by_scope[key].append(r)
        # Keep the first label seen for the reader; the canonical form is an
        # internal grouping key and is not what belongs in a report.
        display_scope.setdefault(key, label)

    review_notes = (
        _scope_checks(by_scope, display_scope, unmatched)
        if field_def.check_scopes_across_documents
        else {}
    )

    resolved: list[ResolvedField] = []
    contradictions: list[Contradiction] = []

    for key, scope_records in by_scope.items():
        scope = display_scope[key]
        review_note = review_notes.get(key)

        # A field the registry declares as a collection cannot have its members
        # disagree with each other: two records naming different pieces of
        # evidence are both right, and are one answer with several parts. Only
        # single-valued fields can contradict.
        if _is_collection(field_def):
            resolved.append(_merge_collection(field_def, scope_records, scope, review_note))
            continue

        competing, silent = _competing_records(scope_records)
        value_groups = _group_by_canonical_value(competing)

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
                # The silent records are cited too, so the pages where a document
                # does not answer stay in the report's evidence appendix. They
                # are not corroboration, so they do not raise the confidence.
                sources=[r.provenance for r in group] + [r.provenance for r in silent],
                quote=best.quote,
                scope=scope,
                review_note=review_note,
            ))
        else:
            # Genuine disagreement within the same scope — candidate only.
            # No ResolvedField emitted for this (field_id, scope) until
            # ADJUDICATE exists and judges it.
            contradictions.append(Contradiction(
                field_id=field_def.id,
                records=competing,
            ))

    return resolved, contradictions


def _names_its_scope(record: FieldRecord) -> bool:
    """Step 3 of the arm-matching rule Oliver approved on 2026-10-12: the entry's
    own value or quote must say what its scope names."""
    value = record.value if record.value is not None else record.raw_value
    texts = [str(v) for v in value] if isinstance(value, list) else [str(value)]
    return scope_named_in(record.scope, record.raw_value, record.quote, *texts)


def _scope_checks(
    by_scope: dict[str | None, list[FieldRecord]],
    display_scope: dict[str | None, str | None],
    unmatched: set[str | None],
) -> dict[str | None, str]:
    """The review note for each scope of a field whose scopes are matched across
    documents (`check_scopes_across_documents` in config/fields.yaml).

    Scopes are matched on what they name — for an arm, the treatment it
    receives, which the extraction model is asked to write as the scope (the
    field's hint). So the same arm in two documents is one row citing both,
    whatever each document calls it, and two arms are never one row because
    they share a label. What this function adds is that a difference between
    the documents is never quiet:

    - an entry whose own text does not say what its scope names was kept out of
      the match (`unmatched`), and its row says so;
    - when more than one document lists parts of the study, a part only one of
      them lists says which document lists it and which do not — the case of an
      amendment dropping an arm the RFP still lists.

    Rows with no scope, or the whole study, are not parts and get no note.
    Document ids in the notes are named by the report (render/report_model.py).
    """
    def is_part(key: str | None) -> bool:
        return key is not None and key != "total"

    def docs_of(key: str | None) -> list[str]:
        return list(dict.fromkeys(r.provenance.doc_id for r in by_scope[key]))

    listing = list(dict.fromkeys(
        doc for key in by_scope if is_part(key) for doc in docs_of(key)
    ))
    notes: dict[str | None, str] = {}
    for key in by_scope:
        if not is_part(key):
            continue
        if key in unmatched:
            scope = display_scope[key] or ""
            named = scope.partition(" (not matched, ")[0]
            named = named.partition(":")[2].strip() or named
            notes[key] = (
                f'Not matched with other documents: this entry\'s own words do not '
                f'say "{named}", which its scope names.'
            )
            continue
        docs = docs_of(key)
        if len(listing) > 1 and len(docs) == 1:
            others = [d for d in listing if d not in docs]
            notes[key] = f"Listed only in {docs[0]}, not in {', '.join(others)}."
    return notes


# Types in config/fields.yaml whose value is a collection rather than one answer.
# `list[enum]` and `list[text]` hold members; `object[]` holds entries such as one
# row per study period. Any other type — int, text, enum, bool — is a single
# answer, and two different values for it are a genuine disagreement.
_COLLECTION_TYPES = ("list[enum]", "list[text]", "object[]")


def _is_collection(field_def: FieldDef) -> bool:
    return field_def.type in _COLLECTION_TYPES


def _merge_collection(
    field_def: FieldDef,
    records: list[FieldRecord],
    scope: str | None,
    review_note: str | None = None,
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
        # Each page's own words, so Appendix A shows what every cited page says
        # rather than one quote beside all of them (fix 11 of the 2026-10-12 brief).
        source_quotes=[r.quote for r in records],
        scope=scope,
        review_note=review_note,
        statements=(
            [r for r in records if isinstance(r.value, dict)]
            if field_def.type == "object[]" and field_def.schema_def else []
        ),
    )


def _competing_records(
    records: list[FieldRecord],
) -> tuple[list[FieldRecord], list[FieldRecord]]:
    """Split one scope's records into rival answers and records that say nothing.

    "Not specified" is not a competing answer. One document saying "the protocol
    does not state whether this is a first-in-human study" does not disagree with
    another document saying it is not — the first says nothing on the subject.
    Comparing it like any other value is what put First-in-human (Disagreement 9)
    and Unblinded staff stated as required (Disagreement 5) in the Disagreements
    table of run r-20261007-140411, and sent both to ADJUDICATE as if a person
    had to settle them. Change 2 of the 2026-10-07 review.

    When every record for the scope says nothing, nothing is filtered: the
    records are returned as the competing set and resolve to Not specified
    exactly as before, so the distinction rule 3 draws between `not_specified`,
    `not_found` and `0` is untouched.
    """
    answered = [r for r in records if not _says_nothing(r)]
    if not answered:
        return records, []
    return answered, [r for r in records if _says_nothing(r)]


def _says_nothing(record: FieldRecord) -> bool:
    value = record.value if record.value is not None else record.raw_value
    return isinstance(value, str) and value.strip().casefold() in _SAYS_NOTHING


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
