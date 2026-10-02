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


class TestTheStageFiveFields:
    """The five fields docs/PLAN_2026-10-02.md stage 5 adds, items 4 to 7 and 9.

    Asserted against the shipped `config/fields.yaml` and not a fixture, because
    the point is that the file in the repository loads and carries what the plan
    asked for. A field that loaded but lost its budget-driver flag or its group
    would never reach the report's budget-driver list.
    """

    def test_all_five_load(self, fields_yaml_path: Path) -> None:
        registry = load_registry(fields_yaml_path)
        ids = {f.id for f in registry.fields}
        for field_id in (
            "study.primary_objective",
            "blinding.placebo_matching",
            "blinding.unblinded_staff_stated",
            "visits.schedule_present",
            "blinding.placebo_assumption",
        ):
            assert field_id in ids, f"{field_id} did not load"

    def test_the_primary_objective_is_copied_from_the_protocol(
        self, fields_yaml_path: Path
    ) -> None:
        """Item 4. Angus asked for the objective in the report as written."""
        field = load_registry(fields_yaml_path).get_field("study.primary_objective")
        assert field.type == "text"
        assert field.group == "phase_population"
        assert field.source_priority == "protocol"
        assert field.hint is not None
        assert "VERBATIM" in field.hint

    @pytest.mark.parametrize(
        ("field_id", "expected_values"),
        [
            # `no` and `yes` are quoted in the YAML on purpose: unquoted, YAML
            # reads them as booleans and the registry hands the model `false`
            # beside two string values. These assertions are what keeps the
            # quoting from being removed as noise.
            (
                "blinding.placebo_matching",
                ["matching_stated", "not_matching_stated", "not_specified"],
            ),
            ("blinding.unblinded_staff_stated", ["true", "false", "not_specified"]),
            ("visits.schedule_present", ["yes_body", "yes_appendix", "no"]),
        ],
    )
    def test_the_enums_are_exactly_as_the_plan_sets_them(
        self, fields_yaml_path: Path, field_id: str, expected_values: list[str]
    ) -> None:
        field = load_registry(fields_yaml_path).get_field(field_id)
        assert field.values == expected_values

    def test_all_four_new_extracted_fields_drive_the_budget(
        self, fields_yaml_path: Path
    ) -> None:
        """Each one changes a price, so each one belongs on the budget-driver list."""
        registry = load_registry(fields_yaml_path)
        for field_id in (
            "blinding.placebo_matching",
            "blinding.unblinded_staff_stated",
            "visits.schedule_present",
            "blinding.placebo_assumption",
        ):
            assert registry.get_field(field_id).budget_driver is True, field_id

    def test_the_placebo_assumption_is_derived_from_the_four_inputs(
        self, fields_yaml_path: Path
    ) -> None:
        """Item 9. The order matters only to a reader; the set is what the rubric reads."""
        field = load_registry(fields_yaml_path).get_field("blinding.placebo_assumption")
        assert field.derived is True
        assert set(field.derived_from or []) == {
            "blinding.design",
            "ip.form",
            "blinding.placebo_matching",
            "blinding.unblinded_staff_stated",
        }

    def test_every_derived_field_has_a_registered_rubric(
        self, fields_yaml_path: Path
    ) -> None:
        """`derive/__init__.py` degrades a rubric-less derived field to not_specified.

        That degradation is deliberate — it keeps a run from crashing — but it also
        means a forgotten registration shows up as a missing number in the report
        rather than an error. This test is the check that would otherwise not happen.
        """
        from rfp_intake.derive import DERIVE_RUBRICS

        derived = [f.id for f in load_registry(fields_yaml_path).fields if f.derived]
        assert derived, "no derived fields found at all"
        missing = [field_id for field_id in derived if field_id not in DERIVE_RUBRICS]
        assert not missing, f"derived fields with no rubric in DERIVE_RUBRICS: {missing}"

    def test_the_schedule_hints_cover_the_abbreviations(self, fields_yaml_path: Path) -> None:
        """Item 7. "SoA" and "SoE" are how the table is named in a draft protocol."""
        field = load_registry(fields_yaml_path).get_field("visits.schedule_present")
        assert "SoA" in field.aliases
        assert "SoE" in field.aliases

    def test_sites_total_accepts_both_spellings_of_centre(self, fields_yaml_path: Path) -> None:
        """Item 8. A British protocol says "centres" and an American one "centers"."""
        aliases = {a.lower() for a in load_registry(fields_yaml_path).get_field(
            "ops.sites_total"
        ).aliases}
        assert "centres" in aliases
        assert "centers" in aliases


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
