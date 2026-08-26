"""Load and validate config/fields.yaml into typed Pydantic models."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from rfp_intake.config.settings import get_settings


class SearchHints(BaseModel):
    headings: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    requires_tables: bool = False


class GroupDef(BaseModel):
    id: str
    label: str
    search_hints: SearchHints = Field(default_factory=SearchHints)


class ObjectFieldSchema(BaseModel):
    """Schema definition for object[] typed fields."""

    fields: dict[str, str] = Field(default_factory=dict)


class FieldDef(BaseModel):
    id: str
    group: str
    label: str
    type: str
    values: list[str] | None = None
    aliases: list[str] = Field(default_factory=list)

    @field_validator("values", mode="before")
    @classmethod
    def _coerce_values_to_str(cls, v: Any) -> list[str] | None:
        # YAML parses yes/no as booleans; coerce to strings
        if v is None:
            return None
        return [str(item).lower() if isinstance(item, bool) else str(item) for item in v]
    hint: str | None = None
    scoped: bool = False
    source_priority: str | None = None
    budget_driver: bool = False
    derived: bool = False
    derived_from: list[str] | None = None
    schema_def: dict[str, str] | None = Field(default=None, alias="schema")

    model_config = {"populate_by_name": True}


class Registry(BaseModel):
    version: int
    groups: list[GroupDef]
    fields: list[FieldDef]
    registry_version: str

    def get_group(self, group_id: str) -> GroupDef:
        for g in self.groups:
            if g.id == group_id:
                return g
        raise KeyError(f"Unknown group: {group_id}")

    def get_field(self, field_id: str) -> FieldDef:
        for f in self.fields:
            if f.id == field_id:
                return f
        raise KeyError(f"Unknown field: {field_id}")

    def get_fields_for_group(self, group_id: str) -> list[FieldDef]:
        return [f for f in self.fields if f.group == group_id]


class RegistryValidationError(Exception):
    pass


def _validate_registry(groups: list[GroupDef], fields: list[FieldDef]) -> None:
    """Validate registry invariants. Raises RegistryValidationError on failure."""
    errors: list[str] = []
    group_ids = {g.id for g in groups}

    # Unique field ids
    seen_ids: set[str] = set()
    for f in fields:
        if f.id in seen_ids:
            errors.append(f"Duplicate field id: {f.id}")
        seen_ids.add(f.id)

    # Group references resolve
    for f in fields:
        if f.group not in group_ids:
            errors.append(f"Field {f.id} references unknown group: {f.group}")

    # Enum fields declare values
    for f in fields:
        if f.type in ("enum", "list[enum]") and not f.values:
            errors.append(f"Enum field {f.id} has no values declared")

    # Derived fields declare derived_from with valid references
    for f in fields:
        if f.derived:
            if not f.derived_from:
                errors.append(f"Derived field {f.id} has no derived_from")
            else:
                for ref in f.derived_from:
                    if ref not in seen_ids:
                        errors.append(
                            f"Derived field {f.id} references unknown field: {ref}"
                        )

    if errors:
        raise RegistryValidationError(
            "Registry validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )


def load_registry(path: Path | None = None) -> Registry:
    """Load fields.yaml, validate, and return a typed Registry."""
    if path is None:
        path = Path(get_settings().fields_yaml_path)

    if not path.exists():
        raise FileNotFoundError(f"Fields registry not found: {path}")

    raw_bytes = path.read_bytes()
    content_hash = hashlib.sha256(raw_bytes).hexdigest()[:12]

    data: dict[str, Any] = yaml.safe_load(raw_bytes.decode())

    version = data.get("version", 0)
    groups = [GroupDef(**g) for g in data.get("groups", [])]
    fields: list[FieldDef] = []
    for f_data in data.get("fields", []):
        fields.append(FieldDef.model_validate(f_data))

    _validate_registry(groups, fields)

    registry_version = f"v{version}:{content_hash}"

    return Registry(
        version=version,
        groups=groups,
        fields=fields,
        registry_version=registry_version,
    )


@lru_cache(maxsize=1)
def get_registry() -> Registry:
    """Singleton access to the validated registry."""
    return load_registry()
