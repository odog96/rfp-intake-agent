"""EXTRACT — structured LLM extraction with post-call validation."""

from __future__ import annotations

from typing import Any

import structlog

from rfp_intake.domain.dynamic import (
    FieldExtractionItem,
    asked_in,
    build_extraction_model,
    extraction_response_to_records,
    fields_to_extract,
)
from rfp_intake.domain.registry import FieldDef, Registry, get_registry
from rfp_intake.domain.schemas import (
    Document,
    ExtractionTask,
    FieldRecord,
    RunError,
    RunState,
)
from rfp_intake.extract.prompt import (
    build_excerpt,
    build_extract_prompt,
    build_repair_prompt,
    describe_violation,
    quote_section,
)
from rfp_intake.extract.validate import answers_field, nearest_passage, validate_record
from rfp_intake.llm.provider import get_llm
from rfp_intake.llm.structured import get_structured_output_for_role

logger = structlog.get_logger()

MAX_RETRIES = 1


def extract_group(
    task: ExtractionTask,
    doc: Document,
    registry: Registry | None = None,
) -> tuple[list[FieldRecord], list[RunError]]:
    """Extract fields for one (doc, group) task.

    Returns (records, errors). Performs post-call validation with one retry.
    """
    if registry is None:
        registry = get_registry()

    doc_kind = doc.kind or "other"
    if not fields_to_extract(task.group, registry, doc_kind):
        # Every field of the group is read only from other kinds of document
        # (`only_from`), so there is nothing to ask this one.
        logger.info(
            "extract_group_skipped", doc_id=task.doc_id, group=task.group, doc_kind=doc_kind,
        )
        return [], []

    llm = get_llm("extract")
    structured = get_structured_output_for_role(llm, "extract")

    schema = build_extraction_model(task.group, registry, doc_kind)
    messages = build_extract_prompt(task, doc, registry)
    excerpt = build_excerpt(task, doc)

    # Every field of the group, `only_from` or not, so a record for a field this
    # document was not asked for is recognised and rejected below.
    fields_in_group = [
        f for f in registry.get_fields_for_group(task.group) if not f.derived
    ]
    field_map = {f.id: f for f in fields_in_group}

    response = structured.extract(schema, messages)

    raw_records = extraction_response_to_records(
        task.group, doc.id, doc_kind, response, registry, doc.page_labels
    )

    # Validate each record
    valid_records: list[FieldRecord] = []
    violations: list[str] = []
    failed_records: list[FieldRecord] = []
    errors: list[RunError] = []

    def accept(record: FieldRecord, item: FieldExtractionItem, field_def: FieldDef) -> None:
        """Keep a record that passed validate_record, unless answers_field
        rejects it. That rejection gets no repair retry: the passage does not
        say what the field asks, and a retry cannot change the passage."""
        rejected = answers_field(item, field_def)
        if rejected is not None:
            errors.append(_rejected(task, record, f"Rejected: {rejected}"))
            return
        record.provenance.section = quote_section(task, doc, record.quote, record.provenance.page)
        valid_records.append(record)

    for record in raw_records:
        field_def = field_map.get(record.field_id)
        if field_def is None:
            valid_records.append(record)
            continue
        if not asked_in(field_def, doc_kind):
            errors.append(_rejected(task, record, _not_asked(field_def, doc_kind)))
            continue

        # Build a FieldExtractionItem-like for validation
        item_status = "found" if record.status == "not_found" else record.status
        item = FieldExtractionItem(
            raw_value=record.raw_value,
            quote=record.quote,
            status=item_status,
            confidence=record.confidence,
            scope=record.scope,
            page=record.provenance.page,
            notes=record.notes,
        )

        is_valid, reason = validate_record(item, excerpt, task.page_window, field_def)
        if is_valid:
            accept(record, item, field_def)
        else:
            reason = reason or "invalid"
            passage = (
                nearest_passage(record.quote, excerpt)
                if reason == "quote_not_found_in_excerpt" else None
            )
            violations.append(describe_violation(record.field_id, reason, passage))
            failed_records.append(record)

    # One repair retry if there were violations
    if violations and len(violations) <= len(raw_records):
        repair_messages = build_repair_prompt(messages, violations)
        try:
            repair_response = structured.extract(schema, repair_messages)
            repair_records = extraction_response_to_records(
                task.group, doc.id, doc_kind, repair_response, registry, doc.page_labels
            )

            for record in repair_records:
                field_def = field_map.get(record.field_id)
                if field_def is None:
                    valid_records.append(record)
                    continue
                if not asked_in(field_def, doc_kind):
                    errors.append(_rejected(task, record, _not_asked(field_def, doc_kind)))
                    continue

                item_status2 = "found" if record.status == "not_found" else record.status
                item = FieldExtractionItem(
                    raw_value=record.raw_value,
                    quote=record.quote,
                    status=item_status2,
                    confidence=record.confidence,
                    scope=record.scope,
                    page=record.provenance.page,
                    notes=record.notes,
                )

                is_valid, reason = validate_record(item, excerpt, task.page_window, field_def)
                if is_valid:
                    accept(record, item, field_def)
                else:
                    errors.append(
                        _rejected(task, record, f"Validation failed after retry: {reason}")
                    )
        except Exception as e:
            logger.error("repair_retry_failed", error=str(e), task=task.group)
            for v in violations:
                errors.append(RunError(
                    node="EXTRACT",
                    task_id=f"{task.doc_id}:{task.group}",
                    error=f"Repair retry failed: {v}",
                    kind="validation",
                ))
    elif violations:
        for v in violations:
            errors.append(RunError(
                node="EXTRACT",
                task_id=f"{task.doc_id}:{task.group}",
                error=f"Validation failed: {v}",
                kind="validation",
            ))

    logger.info(
        "extract_group_complete",
        doc_id=task.doc_id,
        group=task.group,
        records=len(valid_records),
        errors=len(errors),
    )

    return valid_records, errors


def extract_node(state: RunState) -> dict[str, Any]:
    """EXTRACT graph node — fan-out leaf. Processes one ExtractionTask."""
    registry = get_registry()
    all_records: list[FieldRecord] = []
    all_errors: list[RunError] = []

    for task in state.tasks:
        doc = _find_doc(state, task.doc_id)
        if doc is None:
            all_errors.append(RunError(
                node="EXTRACT",
                task_id=f"{task.doc_id}:{task.group}",
                error=f"Document {task.doc_id} not found in state",
            ))
            continue

        try:
            records, errors = extract_group(task, doc, registry)
            all_records.extend(records)
            all_errors.extend(errors)
        except Exception as e:
            logger.error(
                "extract_task_failed",
                doc_id=task.doc_id,
                group=task.group,
                error=str(e),
            )
            all_errors.append(RunError(
                node="EXTRACT",
                task_id=f"{task.doc_id}:{task.group}",
                error=f"Extraction failed: {e}",
            ))

    return {"records": all_records, "errors": all_errors}


def _not_asked(field_def: FieldDef, doc_kind: str) -> str:
    """Why a record for a field this kind of document is not asked for was dropped."""
    return (
        f"Rejected: {field_def.id} is read only from {', '.join(field_def.only_from)} "
        f"documents (only_from), and this document is {doc_kind}"
    )


def _rejected(task: ExtractionTask, record: FieldRecord, error: str) -> RunError:
    """A dropped record as an error, keeping its value, quote and page."""
    return RunError(
        node="EXTRACT",
        task_id=f"{task.doc_id}:{task.group}:{record.field_id}",
        error=error,
        kind="validation",
        rejected_value=record.raw_value,
        rejected_quote=record.quote,
        rejected_page=record.provenance.page,
    )


def _find_doc(state: RunState, doc_id: str) -> Document | None:
    """Find a document by ID in the state."""
    for doc in state.documents:
        if doc.id == doc_id:
            return doc
    return None
