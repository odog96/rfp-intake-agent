"""Build Pydantic extraction models at runtime from the field registry.

This is what keeps fields.yaml authoritative: adding a field to the YAML
changes the extraction schema with no Python edit.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from rfp_intake.domain.registry import FieldDef, Registry, get_registry
from rfp_intake.domain.schemas import FieldRecord, Provenance


class FieldExtractionItem(BaseModel):
    """Single extracted value with provenance metadata, as returned by the LLM."""

    raw_value: str
    quote: str = Field(
        description="Verbatim span from the source document. Validated as substring."
    )
    status: Literal["found", "not_specified"] = "found"
    confidence: float = Field(ge=0.0, le=1.0)
    scope: str | None = Field(default=None, description="e.g. 'total', 'cohort:A', 'country:DE'")
    page: int = Field(description="1-indexed page number within the excerpt")
    notes: str | None = None


def _field_id_to_attr(field_id: str) -> str:
    """Convert dotted field id to a valid Python attribute name."""
    # design.overview -> design_overview, visits.total_count -> visits_total_count
    # But since fields within a group share the group prefix, we use the short name
    parts = field_id.split(".")
    if len(parts) == 2:
        return parts[1]
    return field_id.replace(".", "_")


def _build_field_description(field_def: FieldDef) -> str:
    """Build a description for the model field from the registry definition."""
    parts = [field_def.label]
    if field_def.hint:
        parts.append(field_def.hint.strip())
    if field_def.values:
        parts.append(f"Allowed values: {field_def.values}")
    if field_def.aliases:
        parts.append(f"Also known as: {', '.join(field_def.aliases[:5])}")
    return " | ".join(parts)


_model_cache: dict[str, type[BaseModel]] = {}


def build_extraction_model(group_id: str, registry: Registry | None = None) -> type[BaseModel]:
    """Generate a Pydantic model for extracting fields in the given group.

    Each non-derived field becomes an optional list of FieldExtractionItem
    (list because scoped fields may emit multiple records per field).
    """
    if registry is None:
        registry = get_registry()

    cache_key = f"{group_id}:{registry.registry_version}"
    if cache_key in _model_cache:
        return _model_cache[cache_key]

    registry.get_group(group_id)  # validate group exists

    fields_in_group = [
        f for f in registry.get_fields_for_group(group_id) if not f.derived
    ]

    field_definitions: dict[str, Any] = {}
    for field_def in fields_in_group:
        attr_name = _field_id_to_attr(field_def.id)
        description = _build_field_description(field_def)
        field_definitions[attr_name] = (
            list[FieldExtractionItem] | None,
            Field(default=None, description=description),
        )

    model_name = "".join(part.capitalize() for part in group_id.split("_")) + "Extraction"

    model: type[BaseModel] = create_model(model_name, **field_definitions)
    _model_cache[cache_key] = model
    return model


def extraction_response_to_records(
    group_id: str,
    doc_id: str,
    doc_kind: Literal["rfp", "protocol", "amendment", "soa", "other"],
    response: BaseModel,
    registry: Registry | None = None,
) -> list[FieldRecord]:
    """Transform a dynamic extraction model instance into canonical FieldRecord objects."""
    if registry is None:
        registry = get_registry()

    fields_in_group = [
        f for f in registry.get_fields_for_group(group_id) if not f.derived
    ]

    attr_to_field_id = {
        _field_id_to_attr(f.id): f.id for f in fields_in_group
    }

    records: list[FieldRecord] = []
    for attr_name, field_id in attr_to_field_id.items():
        items = getattr(response, attr_name, None)
        if items is None:
            continue
        for item in items:
            records.append(
                FieldRecord(
                    field_id=field_id,
                    group=group_id,
                    raw_value=item.raw_value,
                    quote=item.quote,
                    provenance=Provenance(
                        doc_id=doc_id,
                        doc_kind=doc_kind,
                        page=item.page,
                    ),
                    status=item.status,
                    confidence=item.confidence,
                    scope=item.scope,
                    notes=item.notes,
                )
            )

    return records
