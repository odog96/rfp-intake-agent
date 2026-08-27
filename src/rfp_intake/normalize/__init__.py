"""NORMALIZE — pure Python normalization of extracted field records."""

from __future__ import annotations

from typing import Any

import structlog

from rfp_intake.domain.registry import Registry, get_registry
from rfp_intake.domain.schemas import FieldRecord, Replace, RunError, RunState
from rfp_intake.normalize.duration import normalize_duration
from rfp_intake.normalize.types import normalize_value

logger = structlog.get_logger()


class NormalizationError(Exception):
    """Raised when a value cannot be normalized (non-fatal)."""


def normalize_record(record: FieldRecord, registry: Registry | None = None) -> FieldRecord:
    """Normalize a single FieldRecord's raw_value into canonical form.

    Returns a NEW record with `value` set (and `unit` for duration-like fields);
    the input is left untouched, per rule 7 — normalizers are pure functions.
    Non-fatal: if normalization fails, `value` stays None and it is logged.
    """
    if registry is None:
        registry = get_registry()

    record = record.model_copy(deep=True)

    try:
        field_def = registry.get_field(record.field_id)
    except KeyError:
        return record

    field_type = field_def.type
    allowed_values = field_def.values
    raw = record.raw_value

    if record.status == "not_specified":
        record.value = None
        return record

    # Duration-aware text fields (dosing frequency, timelines)
    if field_type == "text" and _is_duration_field(record.field_id):
        duration = normalize_duration(raw)
        if duration is not None:
            record.value = duration
            record.unit = duration.get("unit")
            return record

    # Standard type dispatch
    normalized = normalize_value(raw, field_type, allowed_values)
    if normalized is not None:
        record.value = normalized
    else:
        record.value = raw

    return record


def _is_duration_field(field_id: str) -> bool:
    """Check if a field should attempt duration parsing."""
    duration_fields = {
        "dosing.frequency",
        "timeline.total_duration",
        "monitoring.frequency_spec",
    }
    return field_id in duration_fields


def normalize_node(state: RunState) -> dict[str, Any]:
    """NORMALIZE graph node — normalize all field records. Pure Python, no LLM."""
    registry = get_registry()
    errors: list[RunError] = []
    normalized_records: list[FieldRecord] = []

    for record in state.records:
        try:
            normalized = normalize_record(record, registry)
            normalized_records.append(normalized)
        except Exception as e:
            logger.error(
                "normalization_failed",
                field_id=record.field_id,
                raw_value=record.raw_value,
                error=str(e),
            )
            errors.append(RunError(
                node="NORMALIZE",
                task_id=record.field_id,
                error=f"Normalization failed for {record.field_id}: {e}",
            ))
            normalized_records.append(record)

    logger.info(
        "normalize_complete",
        run_id=state.run_id,
        total_records=len(normalized_records),
        errors=len(errors),
    )

    # Replace, not append: this node rewrites every record it was handed, and
    # returning the full list under a plain append reducer doubled the output.
    return {"records": Replace(normalized_records), "errors": errors}
