"""Tests for domain/registry.py — field registry loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rfp_intake.domain.registry import (
    Registry,
    RegistryValidationError,
    load_registry,
)


def test_registry_loads_valid_yaml(fields_yaml_path: Path) -> None:
    registry = load_registry(fields_yaml_path)
    assert isinstance(registry, Registry)
    assert registry.version == 1
    assert len(registry.groups) == 9
    assert len(registry.fields) > 30


def test_registry_version_format(fields_yaml_path: Path) -> None:
    registry = load_registry(fields_yaml_path)
    assert registry.registry_version.startswith("v1:")
    # Hash portion is 12 hex chars
    hash_part = registry.registry_version.split(":")[1]
    assert len(hash_part) == 12
    assert all(c in "0123456789abcdef" for c in hash_part)


def test_registry_version_stable(fields_yaml_path: Path) -> None:
    r1 = load_registry(fields_yaml_path)
    r2 = load_registry(fields_yaml_path)
    assert r1.registry_version == r2.registry_version


def test_get_group(fields_yaml_path: Path) -> None:
    registry = load_registry(fields_yaml_path)
    group = registry.get_group("visits")
    assert group.label == "Subject Visit Schedule and Intensity"


def test_get_group_unknown(fields_yaml_path: Path) -> None:
    registry = load_registry(fields_yaml_path)
    with pytest.raises(KeyError, match="Unknown group"):
        registry.get_group("nonexistent")


def test_get_field(fields_yaml_path: Path) -> None:
    registry = load_registry(fields_yaml_path)
    field = registry.get_field("ops.sites_total")
    assert field.budget_driver is True
    assert field.source_priority == "rfp"


def test_get_fields_for_group(fields_yaml_path: Path) -> None:
    registry = load_registry(fields_yaml_path)
    visit_fields = registry.get_fields_for_group("visits")
    assert len(visit_fields) >= 4
    ids = [f.id for f in visit_fields]
    assert "visits.total_count" in ids
    assert "visits.intensity_rating" in ids


def test_validates_duplicate_ids(tmp_path: Path) -> None:
    data = {
        "version": 1,
        "groups": [{"id": "g1", "label": "Group 1"}],
        "fields": [
            {"id": "g1.field_a", "group": "g1", "label": "A", "type": "text"},
            {"id": "g1.field_a", "group": "g1", "label": "A dup", "type": "text"},
        ],
    }
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data))
    with pytest.raises(RegistryValidationError, match="Duplicate field id"):
        load_registry(p)


def test_validates_group_refs(tmp_path: Path) -> None:
    data = {
        "version": 1,
        "groups": [{"id": "g1", "label": "Group 1"}],
        "fields": [
            {"id": "g2.field_a", "group": "g2", "label": "A", "type": "text"},
        ],
    }
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data))
    with pytest.raises(RegistryValidationError, match="unknown group"):
        load_registry(p)


def test_validates_enum_values(tmp_path: Path) -> None:
    data = {
        "version": 1,
        "groups": [{"id": "g1", "label": "Group 1"}],
        "fields": [
            {"id": "g1.status", "group": "g1", "label": "Status", "type": "enum"},
        ],
    }
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data))
    with pytest.raises(RegistryValidationError, match="no values"):
        load_registry(p)


def test_validates_derived_from(tmp_path: Path) -> None:
    data = {
        "version": 1,
        "groups": [{"id": "g1", "label": "Group 1"}],
        "fields": [
            {"id": "g1.base", "group": "g1", "label": "Base", "type": "int"},
            {
                "id": "g1.derived",
                "group": "g1",
                "label": "Derived",
                "type": "enum",
                "values": ["low", "high"],
                "derived": True,
                "derived_from": ["g1.base", "g1.nonexistent"],
            },
        ],
    }
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data))
    with pytest.raises(RegistryValidationError, match="unknown field.*nonexistent"):
        load_registry(p)


def test_validates_derived_missing_derived_from(tmp_path: Path) -> None:
    data = {
        "version": 1,
        "groups": [{"id": "g1", "label": "Group 1"}],
        "fields": [
            {
                "id": "g1.derived",
                "group": "g1",
                "label": "Derived",
                "type": "enum",
                "values": ["low", "high"],
                "derived": True,
            },
        ],
    }
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data))
    with pytest.raises(RegistryValidationError, match="no derived_from"):
        load_registry(p)
