"""Tests for domain/registry.py — field registry loading and validation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any  # Any: test inputs are arbitrary YAML fragments

import pytest
import yaml
from pydantic import ValidationError

from rfp_intake.domain.registry import (
    Registry,
    RegistryValidationError,
    ScopesDisplay,
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
    # "and Intensity" went with the visit intensity rows (Angus Gray, 29 October 2026).
    assert group.label == "Subject Visit Schedule"


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
    assert len(visit_fields) >= 3
    ids = [f.id for f in visit_fields]
    assert "visits.total_count" in ids
    assert "visits.schedule_present" in ids
    # Removed on 29 October 2026 (Angus Gray): the visit intensity was not needed.
    assert "visits.intensity_rating" not in ids
    assert "visits.intensity_evidence" not in ids


class TestTheStageFiveFields:
    """The five fields docs/PLAN_2026-10-02.md stage 5 adds, items 4 to 7 and 9.

    Asserted against the shipped `config/fields.yaml` and not a fixture, because
    the point is that the file in the repository loads and carries what the plan
    asked for. A field that loaded but lost its budget-driver flag or its group
    would never reach the report's budget-driver list.
    """

    def test_the_four_still_in_the_registry_load(self, fields_yaml_path: Path) -> None:
        # The fifth, blinding.placebo_assumption, was removed on 2026-11-23.
        registry = load_registry(fields_yaml_path)
        ids = {f.id for f in registry.fields}
        for field_id in (
            "study.primary_objective",
            "blinding.placebo_matching",
            "blinding.unblinded_staff_stated",
            "visits.schedule_present",
        ):
            assert field_id in ids, f"{field_id} did not load"
        assert "blinding.placebo_assumption" not in ids

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
            # `no_placebo` since 29 October 2026: Angus Gray asked for Yes, No or N/A.
            (
                "blinding.placebo_matching",
                ["matching_stated", "not_matching_stated", "no_placebo", "not_specified"],
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

    def test_the_new_fields_still_in_the_registry_drive_the_budget(
        self, fields_yaml_path: Path
    ) -> None:
        """Each one changes a price, so each one belongs on the budget-driver list.
        blinding.placebo_assumption, the fourth, was removed on 2026-11-23."""
        registry = load_registry(fields_yaml_path)
        for field_id in (
            "blinding.placebo_matching",
            "blinding.unblinded_staff_stated",
            "visits.schedule_present",
        ):
            assert registry.get_field(field_id).budget_driver is True, field_id

    def test_every_derived_field_has_a_registered_rubric(
        self, fields_yaml_path: Path
    ) -> None:
        """`derive/__init__.py` degrades a rubric-less derived field to not_specified.

        That degradation is deliberate — it keeps a run from crashing — but it also
        means a forgotten registration shows up as a missing number in the report
        rather than an error. This test is the check that would otherwise not happen.
        """
        from rfp_intake.derive import DERIVE_RUBRICS

        # Since 29 October 2026 the shipped registry may have no derived field at
        # all (the visit intensity and the placebo assumption went); any it has
        # needs its rubric.
        derived = [f.id for f in load_registry(fields_yaml_path).fields if f.derived]
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


def test_schedule_table_is_optional(fields_yaml_path: Path, tmp_path: Path) -> None:
    data = yaml.safe_load(fields_yaml_path.read_text())
    del data["schedule_table"]
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True))
    assert load_registry(p).schedule_table is None


def test_schedule_table_considerations_need_their_own_ids(
    fields_yaml_path: Path, tmp_path: Path
) -> None:
    # Prompt C: a consideration's id is what the report and the must-hold check name.
    data = yaml.safe_load(fields_yaml_path.read_text())
    considerations = data["schedule_table"]["considerations"]
    considerations.append(dict(considerations[0], label="Consent again"))
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True))
    with pytest.raises(RegistryValidationError, match="two considerations share an id"):
        load_registry(p)


def _with_fields_changed(fields_yaml_path: Path, tmp_path: Path, change: Any) -> Path:
    data = yaml.safe_load(fields_yaml_path.read_text())
    change(data)
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True))
    return p


def test_from_schedule_marks_one_field_each(fields_yaml_path: Path, tmp_path: Path) -> None:
    # Prompt C: the report prints one visit table and one set of stage totals.
    registry = load_registry(fields_yaml_path)
    assert [f.id for f in registry.fields if f.from_schedule == "table"] == [
        "visits.frequency_by_period"]
    assert [f.id for f in registry.fields if f.from_schedule == "stage_totals"] == [
        "visits.total_count"]

    def two_tables(data: dict[str, Any]) -> None:
        next(f for f in data["fields"] if f["id"] == "visits.schedule_present")[
            "from_schedule"] = "table"

    with pytest.raises(RegistryValidationError, match="More than one field has from_schedule"):
        load_registry(_with_fields_changed(fields_yaml_path, tmp_path, two_tables))


def test_stage_totals_need_a_table(fields_yaml_path: Path, tmp_path: Path) -> None:
    def no_table(data: dict[str, Any]) -> None:
        for f in data["fields"]:
            if f.get("from_schedule") == "table":
                del f["from_schedule"]

    with pytest.raises(RegistryValidationError, match="none has from_schedule: table"):
        load_registry(_with_fields_changed(fields_yaml_path, tmp_path, no_table))


def test_stage_totals_go_into_a_scoped_int(fields_yaml_path: Path, tmp_path: Path) -> None:
    def text_field(data: dict[str, Any]) -> None:
        for f in data["fields"]:
            if f.get("from_schedule") == "stage_totals":
                f["type"] = "text"

    with pytest.raises(RegistryValidationError, match="is not a scoped int"):
        load_registry(_with_fields_changed(fields_yaml_path, tmp_path, text_field))


@pytest.mark.parametrize(("key", "text", "message"), [
    ("each", "{count} each", "names count"),
    ("each", "each", "must name n"),
    ("total_line", "{stage}: {total}", "must name parts"),
    ("none_found", " ", "is empty"),
])
def test_schedule_wording_names_only_its_own(
    fields_yaml_path: Path, tmp_path: Path, key: str, text: str, message: str
) -> None:
    def reword(data: dict[str, Any]) -> None:
        data["schedule_table"]["wording"][key] = text

    with pytest.raises(RegistryValidationError, match=message):
        load_registry(_with_fields_changed(fields_yaml_path, tmp_path, reword))


def test_amount_wording_defaults_to_the_amount_alone() -> None:
    # Prompt B item 8: "Every" is a field's own amount_wording, not its id.
    from rfp_intake.domain.registry import FieldDef

    assert FieldDef(id="g1.a", group="g1", label="A", type="text").amount_wording == "{amount}"


def test_amount_wording_must_name_the_amount() -> None:
    from rfp_intake.domain.registry import FieldDef

    with pytest.raises(ValidationError, match="must contain {amount}"):
        FieldDef(id="g1.a", group="g1", label="A", type="text", amount_wording="Every time")


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


_WORDING = {
    "shown_provisional": "Shown",
    "not_used": "Not used, see {n}",
    "no_value_chosen": "None chosen, see {n}",
    "row_shown_provisional": "Shown {value} ({why}), see {n}",
    "precedence_why": {"recency": "newer {doc}", "domain_authority": "the {doc}'s variable",
                       "specificity": "clearest {doc}", "default": "the {doc} first"},
    "row_no_value_chosen": "None chosen, see {n}",
    "depends_on": "Depends on {n}",
    "derived_status": "Worked out",
    "derived_why": "Why: {why}",
    "evidence_pages": "Pages: {pages}",
    "block_stated": '{label}: {value} "{quote}"{cut} {pages}',
    "block_value": "{label}: {value} {pages}",
    "block_disagree": "{label}: see {n}",
    "block_side": '{pages}: "{quote}"{cut}',
    "block_side_value": "{pages}: {value}",
    "block_not_found": "{label}: none",
    "scope_kind": "{kind}: {name}",
    "entries_count": "{n} {kinds}",
    "entries_only_one": "{what} is in the {doc} only.",
    "entries_only_many": "{what} are in the {doc} only.",
    "entries_and": " and ",
    "entries_differ": "{label}: differ — {values}",
    "entries_differ_value": "{value} ({pages})",
    "other_wordings": "Other wordings in Appendix A.",
    "others_checked": "Other passages in Appendix A.",
    "not_applicable": "N/A",
    "off_topic": "Not about this variable — see Appendix B",
}


def _with_leave_out(tmp_path: Path, *, derived: bool, phrases: list[str]) -> Path:
    data = {
        "version": 1,
        "groups": [{"id": "g1", "label": "Group 1"}],
        "fields": [
            {"id": "g1.base", "group": "g1", "label": "Base", "type": "int"},
            {
                "id": "g1.rated",
                "group": "g1",
                "label": "Rated",
                "type": "enum",
                "values": ["low", "high"],
                "derived": derived,
                "derived_from": ["g1.base"],
                "leave_out_scopes": {"reason": "a separate schedule", "phrases": phrases},
            },
        ],
        "report_wording": _WORDING,
    }
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data))
    return p


def test_leave_out_scopes_is_read(tmp_path: Path) -> None:
    registry = load_registry(_with_leave_out(tmp_path, derived=True, phrases=["post etd"]))
    leave_out = registry.get_field("g1.rated").leave_out_scopes
    assert leave_out is not None
    assert leave_out.phrases == ["post etd"]


def test_leave_out_scopes_only_on_a_derived_field(tmp_path: Path) -> None:
    with pytest.raises(RegistryValidationError, match="leaves out scopes but is not derived"):
        load_registry(_with_leave_out(tmp_path, derived=False, phrases=["post etd"]))


@pytest.mark.parametrize("phrases", [[], ["post etd", " - "]])
def test_leave_out_scopes_phrase_must_have_words(tmp_path: Path, phrases: list[str]) -> None:
    """A phrase with no words would match every scope and leave every row out."""
    with pytest.raises(RegistryValidationError, match="empty leave_out_scopes phrase"):
        load_registry(_with_leave_out(tmp_path, derived=True, phrases=phrases))


def _with_scope_check(tmp_path: Path, *, derived: bool) -> Path:
    field: dict[str, object] = {
        "id": "g1.parts", "group": "g1", "label": "Parts", "type": "object[]",
        "check_scopes_across_documents": True,
    }
    if derived:
        field.update({"derived": True, "derived_from": ["g1.base"]})
    data = {
        "version": 1,
        "groups": [{"id": "g1", "label": "Group 1"}],
        "fields": [{"id": "g1.base", "group": "g1", "label": "Base", "type": "int"}, field],
        "report_wording": _WORDING,
    }
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data))
    return p


def test_check_scopes_across_documents_is_read(tmp_path: Path) -> None:
    registry = load_registry(_with_scope_check(tmp_path, derived=False))
    assert registry.get_field("g1.parts").check_scopes_across_documents
    assert not registry.get_field("g1.base").check_scopes_across_documents


def test_check_scopes_across_documents_not_on_a_derived_field(tmp_path: Path) -> None:
    """RECONCILE is what checks scopes, and it never sees a derived field."""
    with pytest.raises(
        RegistryValidationError, match="checks scopes across documents but is derived"
    ):
        load_registry(_with_scope_check(tmp_path, derived=True))


def test_only_design_parts_checks_scopes_across_documents(fields_yaml_path: Path) -> None:
    """Approved for design.parts alone on 2026-10-12; ops.subjects_total was
    deliberately left out of that change."""
    registry = load_registry(fields_yaml_path)
    assert [f.id for f in registry.fields if f.check_scopes_across_documents] == ["design.parts"]


class TestTheInterimAnalysisRowsAfterThe20261007Review:
    """Change 4: one count row, with the conflict flagged on it.

    Run r-20261007-140411 printed "Interim analyses planned: No" beside "Number
    of interim analyses: 1" — two rows answering the same question, with the
    disagreement on the yes/no row and the count confirmed. Angus Gray asked for
    the yes/no row to go, because a count of 0 already says none are planned.
    """

    def test_the_yes_no_row_is_gone(self, fields_yaml_path: Path) -> None:
        registry = load_registry(fields_yaml_path)
        assert "interim.planned" not in {f.id for f in registry.fields}

    def test_the_count_asks_for_an_explicit_zero(self, fields_yaml_path: Path) -> None:
        """Rule 3: 0 is a real answer and is not the same as finding nothing."""
        field = load_registry(fields_yaml_path).get_field("interim.count")
        hint = (field.hint or "").lower()
        assert "no interim analyses are planned" in hint
        assert "the answer is 0" in hint
        assert field.budget_driver, "the count drives a budget"

    def test_the_count_still_has_a_glossary_entry(self, fields_yaml_path: Path) -> None:
        field = load_registry(fields_yaml_path).get_field("interim.count")
        assert field.plain and "0 means" in field.plain

    def test_the_timing_row_follows_the_counts_disagreement(
        self, fields_yaml_path: Path
    ) -> None:
        field = load_registry(fields_yaml_path).get_field("interim.timing")
        assert field.follows_conflict_of == "interim.count"


def test_validates_follows_conflict_of(tmp_path: Path) -> None:
    data = {
        "version": 1,
        "groups": [{"id": "g1", "label": "Group 1"}],
        "fields": [
            {
                "id": "g1.follower",
                "group": "g1",
                "label": "Follower",
                "type": "text",
                "follows_conflict_of": "g1.nonexistent",
            },
        ],
    }
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data))
    with pytest.raises(RegistryValidationError, match="unknown field.*nonexistent"):
        load_registry(p)


def test_validates_follows_conflict_of_itself(tmp_path: Path) -> None:
    data = {
        "version": 1,
        "groups": [{"id": "g1", "label": "Group 1"}],
        "fields": [
            {
                "id": "g1.self",
                "group": "g1",
                "label": "Self",
                "type": "text",
                "follows_conflict_of": "g1.self",
            },
        ],
    }
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data))
    with pytest.raises(RegistryValidationError, match="follows its own conflict"):
        load_registry(p)


class TestTheMonitoringVisitRowsAfterThe20261007Review:
    """Change 5: one row per visit type, in the sponsor's own words.

    Run r-20261007-140411 printed "Number of monitoring visits by type:
    75 visits total (1 per site), 750 visits total (10 per site), 300 visits
    total (4 per site)" — three numbers with nothing saying which kind of visit
    each belonged to — and a separate "On-site vs remote monitoring mix" row that
    did not say which visit type it described.
    """

    # The order the review asked for, which is the order a reader sees. The
    # pharmacy visits moved to the head of Blinding and Unblinded Monitoring on
    # 29 October 2026 (Angus Gray); the other four stay here.
    FIVE = (
        ("ops.visits_site_selection", "Site selection visits"),
        ("ops.visits_site_initiation", "Site initiation visits"),
        ("ops.visits_interim_monitoring", "Interim monitoring visits"),
        ("ops.visits_close_out", "Close-out visits"),
        ("ops.visits_pharmacy_monitoring", "Pharmacy monitoring visits"),
    )

    def test_the_two_old_rows_are_gone(self, fields_yaml_path: Path) -> None:
        ids = {f.id for f in load_registry(fields_yaml_path).fields}
        assert "ops.monitoring_visits" not in ids
        assert "monitoring.onsite_remote_mix" not in ids

    def test_five_rows_in_the_asked_for_order(self, fields_yaml_path: Path) -> None:
        registry = load_registry(fields_yaml_path)
        group = registry.get_fields_for_group("operational_metrics")
        visits = [(f.id, f.label) for f in group if f.id.startswith("ops.visits_")]
        assert visits == list(self.FIVE[:4])

    def test_the_pharmacy_visits_lead_the_unblinded_section(
        self, fields_yaml_path: Path
    ) -> None:
        """Angus Gray, 29 October 2026: the RFP's pharmacy monitoring request goes at the
        top of the unblinded section, with its frequency straight after it."""
        registry = load_registry(fields_yaml_path)
        ids = [f.id for f in registry.get_fields_for_group("blinding_monitoring")]
        assert ids[:2] == ["ops.visits_pharmacy_monitoring", "monitoring.pharmacy_frequency"]
        assert registry.get_field("ops.visits_pharmacy_monitoring").label == self.FIVE[4][1]

    @pytest.mark.parametrize("field_id", [f for f, _ in FIVE])
    def test_each_row_carries_the_documents_own_wording(
        self, fields_yaml_path: Path, field_id: str
    ) -> None:
        field = load_registry(fields_yaml_path).get_field(field_id)
        assert field.type == "text", "the row holds wording, not a number"
        assert field.budget_driver, "each visit type drives a budget"
        assert field.source_priority == "rfp", "like the field it replaced"
        hint = (field.hint or "").lower()
        assert "do no arithmetic" in hint, "no percentage turned into a count"
        assert "remotely" in hint, "the on-site versus remote split lives on this row now"
        assert field.plain, "the glossary needs an entry for every row"


class TestReportWording:
    """CLAUDE.md open item 3: the Appendix A markers are worded in config/fields.yaml."""

    def _write(self, tmp_path: Path, wording: dict[str, str] | None) -> Path:
        data: dict[str, object] = {
            "version": 1,
            "groups": [{"id": "g1", "label": "Group 1"}],
            "fields": [{"id": "g1.a", "group": "g1", "label": "A", "type": "text"}],
        }
        if wording is not None:
            data["report_wording"] = wording
        p = tmp_path / "fields.yaml"
        p.write_text(yaml.dump(data, allow_unicode=True))
        return p

    def test_the_real_registry_carries_the_three_phrases(self, fields_yaml_path: Path) -> None:
        wording = load_registry(fields_yaml_path).report_wording
        assert wording.shown_provisional == (
            "Shown in the report (provisional, by document precedence)"
        )
        assert wording.not_used == "Not used — see Disagreement {n}"
        assert wording.no_value_chosen == "No value chosen — see Disagreement {n}"

    def test_a_registry_without_the_section_does_not_load(self, tmp_path: Path) -> None:
        with pytest.raises(RegistryValidationError, match="no report_wording section"):
            load_registry(self._write(tmp_path, None))

    @pytest.mark.parametrize("key", ["not_used", "no_value_chosen"])
    def test_a_pointer_phrase_must_name_the_disagreement(self, tmp_path: Path, key: str) -> None:
        wording = dict(_WORDING, **{key: "see the Disagreements table"})
        with pytest.raises(RegistryValidationError, match="must contain"):
            load_registry(self._write(tmp_path, wording))

    def test_fill_puts_in_the_number_and_leaves_other_braces(self) -> None:
        from rfp_intake.domain.registry import ReportWording

        assert ReportWording.fill("see {n} {not a field}", 3) == "see 3 {not a field}"


class TestTheDisplayKeysOfThe20261012Brief:
    """Fixes 2 and 6: follow_wording, a scopes display and value_words are read
    from config/fields.yaml and checked when it loads."""

    def _write(self, tmp_path: Path, **extra: object) -> Path:
        follower: dict[str, object] = {
            "id": "g1.b", "group": "g1", "label": "B", "type": "enum",
            "values": ["yes_stated", "no_stated"],
        }
        follower.update(extra)
        data = {
            "version": 1,
            "groups": [{"id": "g1", "label": "Group 1"}],
            "fields": [{"id": "g1.a", "group": "g1", "label": "A", "type": "int"}, follower],
            "report_wording": _WORDING,
        }
        p = tmp_path / "fields.yaml"
        p.write_text(yaml.dump(data))
        return p

    def test_all_three_are_read(self, tmp_path: Path) -> None:
        registry = load_registry(self._write(
            tmp_path,
            follows_conflict_of="g1.a",
            follow_wording={
                "only_some": "Only {docs}: {value}", "if_others_right": "{others}",
                "when_others_say": [0],
            },
            display={"kind": "scopes", "scopes": [{"phrases": ["study"], "label": "Whole"}]},
            value_words={"yes_stated": "Yes"},
        ))
        field = registry.get_field("g1.b")
        assert field.follow_wording is not None
        assert field.follow_wording.only_some == "Only {docs}: {value}"
        # A number in the YAML is read as the stored value's words.
        assert field.follow_wording.when_others_say == ["0"]
        assert isinstance(field.display, ScopesDisplay)
        assert [h.label for h in field.display.scopes] == ["Whole"]
        assert field.value_words == {"yes_stated": "Yes"}

    def test_a_value_word_names_a_value_the_field_allows(self, tmp_path: Path) -> None:
        with pytest.raises(RegistryValidationError, match="value_words"):
            load_registry(self._write(tmp_path, value_words={"maybe": "Maybe"}))

    def test_two_values_may_not_print_as_one_word(self, tmp_path: Path) -> None:
        with pytest.raises(RegistryValidationError, match="same value_words word"):
            load_registry(self._write(
                tmp_path, value_words={"yes_stated": "Stated", "no_stated": "stated"},
            ))

    def test_follow_wording_needs_a_disagreement_to_follow(self, tmp_path: Path) -> None:
        with pytest.raises(RegistryValidationError, match="follows no conflict"):
            load_registry(self._write(tmp_path, follow_wording={
                "only_some": "{value}", "if_others_right": "{others}", "when_others_say": ["0"],
            }))

    @pytest.mark.parametrize("wording", [
        {"only_some": "no value here", "if_others_right": "{others}", "when_others_say": ["0"]},
        {"only_some": "{value}", "if_others_right": "no others here", "when_others_say": ["0"]},
    ])
    def test_follow_wording_names_what_it_fills(
        self, tmp_path: Path, wording: dict[str, str]
    ) -> None:
        # A field that does not parse fails as pydantic's error, like every other
        # FieldDef error; only the cross-field checks raise RegistryValidationError.
        with pytest.raises(ValueError, match="must contain"):
            load_registry(self._write(
                tmp_path, follows_conflict_of="g1.a", follow_wording=wording,
            ))

    @pytest.mark.parametrize("scopes", [[], [{"phrases": [" - "], "label": "Dash"}]])
    def test_a_scopes_heading_needs_words(
        self, tmp_path: Path, scopes: list[dict[str, object]]
    ) -> None:
        with pytest.raises(RegistryValidationError, match="scopes"):
            load_registry(self._write(tmp_path, display={"kind": "scopes", "scopes": scopes}))

    def test_the_provisional_row_line_names_the_value(self, tmp_path: Path) -> None:
        data = {
            "version": 1,
            "groups": [{"id": "g1", "label": "Group 1"}],
            "fields": [{"id": "g1.a", "group": "g1", "label": "A", "type": "int"}],
            "report_wording": dict(_WORDING, row_shown_provisional="Shown, see {n}"),
        }
        p = tmp_path / "fields.yaml"
        p.write_text(yaml.dump(data))
        with pytest.raises(RegistryValidationError, match="must contain"):
            load_registry(p)

    def test_the_provisional_row_line_says_why(self, tmp_path: Path) -> None:
        data = {
            "version": 1,
            "groups": [{"id": "g1", "label": "Group 1"}],
            "fields": [{"id": "g1.a", "group": "g1", "label": "A", "type": "int"}],
            "report_wording": dict(_WORDING, row_shown_provisional="Shown {value}, see {n}"),
        }
        p = tmp_path / "fields.yaml"
        p.write_text(yaml.dump(data))
        with pytest.raises(RegistryValidationError, match="must contain"):
            load_registry(p)

    def test_every_precedence_rule_has_its_reason(self, tmp_path: Path) -> None:
        # Item 1 of Prompt B, 2026-10-16.
        why = {"recency": "newer", "domain_authority": "owner", "default": "first"}
        data = {
            "version": 1,
            "groups": [{"id": "g1", "label": "Group 1"}],
            "fields": [{"id": "g1.a", "group": "g1", "label": "A", "type": "int"}],
            "report_wording": dict(_WORDING, precedence_why=why),
        }
        p = tmp_path / "fields.yaml"
        p.write_text(yaml.dump(data))
        with pytest.raises(RegistryValidationError, match="specificity"):
            load_registry(p)

    @pytest.mark.parametrize(("key", "phrase"), [
        ("derived_why", "The reasons"), ("evidence_pages", "Evidence pages"),
    ])
    def test_a_derived_row_phrase_must_have_its_slot(
        self, tmp_path: Path, key: str, phrase: str
    ) -> None:
        """Fix 8 of the 2026-10-12 brief: a phrase without its slot would print
        the reasons or pages nowhere."""
        data = {
            "version": 1,
            "groups": [{"id": "g1", "label": "Group 1"}],
            "fields": [{"id": "g1.a", "group": "g1", "label": "A", "type": "int"}],
            "report_wording": dict(_WORDING, **{key: phrase}),
        }
        p = tmp_path / "fields.yaml"
        p.write_text(yaml.dump(data))
        with pytest.raises(RegistryValidationError, match="must contain"):
            load_registry(p)

    def test_fill_without_a_number_leaves_the_rest(self) -> None:
        from rfp_intake.domain.registry import ReportWording

        assert ReportWording.fill("Why: {why}", why="weekly visits") == "Why: weekly visits"

    def test_fill_puts_in_named_values(self) -> None:
        from rfp_intake.domain.registry import ReportWording

        assert ReportWording.fill("{value} from the {doc}, see {n}", 2, value="0", doc="rfp") == (
            "0 from the rfp, see 2"
        )


@pytest.mark.parametrize(("scope", "phrases", "expected"), [
    ("study", ["study"], True),
    ("Study-wide", ["study wide"], True),
    ("subject", ["study"], False),
    ("post-ETD follow up", ["post etd"], True),
    ("ETD post", ["post etd"], False),
    (None, ["study"], False),
])
def test_scope_has_phrase(scope: str | None, phrases: list[str], expected: bool) -> None:
    from rfp_intake.domain.registry import scope_has_phrase

    assert scope_has_phrase(scope, phrases) is expected


class TestAGroupBlock:
    """Fix 9 of the 2026-10-12 brief: a group's `block`, and the fix 11 scope
    words, are read from config/fields.yaml and checked when it loads."""

    def _write(self, tmp_path: Path, block: dict[str, object],
               wording: dict[str, object] | None = None) -> Path:
        data = {
            "version": 1,
            "groups": [{"id": "g1", "label": "Group 1", "block": block},
                       {"id": "g2", "label": "Group 2"}],
            "fields": [
                {"id": "g1.a", "group": "g1", "label": "A", "type": "text"},
                {"id": "g1.b", "group": "g1", "label": "B", "type": "text"},
                {"id": "g2.c", "group": "g2", "label": "C", "type": "text"},
            ],
            "report_wording": dict(_WORDING, **(wording or {})),
        }
        p = tmp_path / "fields.yaml"
        p.write_text(yaml.dump(data, allow_unicode=True))
        return p

    @staticmethod
    def _block(**extra: object) -> dict[str, object]:
        block: dict[str, object] = {"label": "AB", "headline": "g1.a", "replaces": ["g1.a", "g1.b"]}
        block.update(extra)
        return block

    def test_a_block_is_read(self, tmp_path: Path) -> None:
        registry = load_registry(self._write(tmp_path, self._block(lines=[
            {"field": "g1.b", "label": "Bee"},
            {"field": "g2.c", "wording": "{doc} says {value} ({pages})"},
        ])))
        block = registry.get_group("g1").block
        assert block is not None
        assert (block.label, block.headline, block.replaces) == ("AB", "g1.a", ["g1.a", "g1.b"])
        assert [(ln.field, ln.label, ln.wording) for ln in block.lines] == [
            ("g1.b", "Bee", None), ("g2.c", None, "{doc} says {value} ({pages})"),
        ]

    def test_the_real_blinding_group_has_no_block(self, fields_yaml_path: Path) -> None:
        """Each variable its own row since 29 October 2026 (Angus Gray): the block
        that held six of them, with references in the middle, is gone from
        config/fields.yaml. The block code path stays for other registries."""
        assert load_registry(fields_yaml_path).get_group("blinding_monitoring").block is None

    @pytest.mark.parametrize(("block", "match"), [
        ({"replaces": ["g1.a", "g1.z"]}, "replacing an unknown field"),
        ({"replaces": ["g1.a", "g2.c"]}, "of another group"),
        ({"headline": "g1.b", "replaces": ["g1.a"]}, "headline it does not replace"),
        ({"lines": [{"field": "g1.z"}]}, "line on an unknown field"),
        ({"lines": [{"field": "g2.c"}]}, "g2.c"),
        ({"lines": [{"field": "g2.c", "wording": "{doc} asks ({pages})"}]}, "no {value}"),
    ])
    def test_a_wrong_block_does_not_load(
        self, tmp_path: Path, block: dict[str, object], match: str
    ) -> None:
        with pytest.raises(RegistryValidationError, match=re.escape(match)):
            load_registry(self._write(tmp_path, self._block(**block)))

    @pytest.mark.parametrize(("key", "phrase"), [
        ("block_stated", '{value} "{quote}"{cut} {pages}'),
        ("block_stated", '{label}: {value} "{quote}" {pages}'),
        ("block_side", '"{quote}"{cut}'),
        ("block_side", '{pages}: "{quote}"'),
        ("block_side_value", "{pages}"),
        ("block_disagree", "{label}: documents disagree"),
        ("block_not_found", "not found"),
        ("scope_kind", "{kind}"),
    ])
    def test_a_block_phrase_must_have_its_slots(
        self, tmp_path: Path, key: str, phrase: str
    ) -> None:
        with pytest.raises(RegistryValidationError, match="must contain"):
            load_registry(self._write(tmp_path, self._block(), {key: phrase}))

    def test_a_scope_kind_needs_a_word(self, tmp_path: Path) -> None:
        with pytest.raises(RegistryValidationError):
            load_registry(self._write(tmp_path, self._block(), {"scope_kinds": {"arm": " "}}))


class TestEntryKeysAndTheirDisplays:
    """Fixes 1, 3 and 7 of the 2026-10-12 brief: an `object[]` field's `schema`
    keys, the `entries` and `periods` displays that name them, and the one-answer
    flag, checked when config/fields.yaml loads."""

    _SCHEMA = {"name": "text", "kind": "enum[arm, cohort]", "n": "int", "label": "text"}
    _ENTRIES = {
        "kind": "entries", "title": ["{kind} {n} — {name}"], "kind_key": "kind",
        "count": {"arm": "arms", "cohort": "cohorts", "mixed": "groups"},
        "lines": [["Subjects: {n}"]], "label_key": "label", "key_words": {"n": "subjects"},
    }
    _PERIODS = {
        "kind": "periods", "order_key": "kind", "one_per": ["kind"], "heading": ["{label}"],
        "lines": ["{heading}: {name}"],
    }

    def _write(self, tmp_path: Path, wording: dict[str, object] | None = None,
               **extra: object) -> Path:
        parts: dict[str, object] = {
            "id": "g1.parts", "group": "g1", "label": "Parts", "type": "object[]",
            "schema": self._SCHEMA,
        }
        parts.update(extra)
        data = {
            "version": 1,
            "groups": [{"id": "g1", "label": "Group 1"}, {"id": "g2", "label": "Group 2"}],
            "fields": [
                parts,
                {"id": "g1.ratio", "group": "g1", "label": "Ratio", "type": "text"},
                {"id": "g2.other", "group": "g2", "label": "Other", "type": "text"},
            ],
            "report_wording": dict(_WORDING, **(wording or {})),
        }
        p = tmp_path / "fields.yaml"
        p.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False))
        return p

    @pytest.mark.parametrize(("spec", "kind", "values"), [
        ("text", "text", []),
        ("int", "int", []),
        ("enum[arm, cohort]", "enum", ["arm", "cohort"]),
        ("enum[screening,\n  treatment]", "enum", ["screening", "treatment"]),
    ])
    def test_a_key_spec_parses(self, spec: str, kind: str, values: list[str]) -> None:
        from rfp_intake.domain.registry import parse_entry_key

        key = parse_entry_key("k", spec)
        assert (key.type, key.values) == (kind, values)

    @pytest.mark.parametrize("spec", ["float", "enum[]", "list[text]", "enum arm"])
    def test_any_other_key_spec_does_not_load(self, tmp_path: Path, spec: str) -> None:
        with pytest.raises(ValidationError, match="is not text, int or enum"):
            load_registry(self._write(tmp_path, schema={"k": spec}))

    def test_both_displays_and_the_flag_are_read(self, tmp_path: Path) -> None:
        from rfp_intake.domain.registry import EntriesDisplay

        registry = load_registry(self._write(
            tmp_path, display=dict(self._ENTRIES, headline_fields=[
                {"field": "g1.ratio", "wording": "randomized {value}"},
            ]),
            check_scopes_across_documents=True, one_answer_when_documents_agree=True,
        ))
        field = registry.get_field("g1.parts")
        assert isinstance(field.display, EntriesDisplay)
        assert field.display.headline_fields[0].field == "g1.ratio"
        assert field.one_answer_when_documents_agree
        assert [k.name for k in field.entry_keys()] == ["name", "kind", "n", "label"]

    @pytest.mark.parametrize(("extra", "match"), [
        ({"type": "text", "schema": None, "display": _PERIODS}, "is not object[] with a schema"),
        ({"one_answer_when_documents_agree": True}, "does not check scopes"),
        ({"may_word_differently": ["name"]}, "is not one answer when documents agree"),
        ({"check_scopes_across_documents": True, "one_answer_when_documents_agree": True,
          "may_word_differently": ["name", "n", "colour"]},
         "not text keys of its schema: n, colour"),
        ({"display": dict(_ENTRIES, lines=[["Dose: {dose}"]])}, "naming key dose"),
        ({"display": dict(_ENTRIES, kind_key="name")}, "kind_key that is not an enum"),
        ({"display": dict(_ENTRIES, count={"arm": "arms"})}, "no count word for: cohort, mixed"),
        ({"display": dict(_ENTRIES, headline_fields=[{"field": "g2.other",
                                                      "wording": "{value}"}])},
         "headline field of another group"),
        ({"display": dict(_ENTRIES, headline_fields=[{"field": "g1.parts",
                                                      "wording": "{value}"}])},
         "headline field it cannot use"),
        ({"display": dict(_PERIODS, one_per=["period"])}, "periods display naming key period"),
        ({"display": dict(_PERIODS, order_key="name")}, "order_key that is not an enum"),
        # A heading is wording now, "{period} ({label})" (Oliver, 2026-10-12): a
        # bare key name, as the headings were written before, would print as it is.
        ({"display": dict(_PERIODS, heading=["label"])}, "periods heading that names no key"),
        ({"display": dict(_PERIODS, heading=["{kind} ({colour})"])},
         "periods display naming key colour"),
        ({"display": dict(_PERIODS, notes={"dose_level": "(x)"})},
         "values its order_key does not allow: dose_level"),
        # 29 October 2026: `always` names values of the order_key, like `notes`.
        ({"display": dict(_PERIODS, always={"arm": "Arms", "dose_level": "Dose"})},
         "periods always for values its order_key does not allow: dose_level"),
        ({"display": dict(_PERIODS, see_also=[{"field": "g1.none", "wording": "{label}",
                                               "disagree": "{n}"}])},
         "see_also on a field it cannot use"),
    ])
    def test_a_wrong_display_does_not_load(
        self, tmp_path: Path, extra: dict[str, Any], match: str
    ) -> None:
        with pytest.raises(RegistryValidationError, match=re.escape(match)):
            load_registry(self._write(tmp_path, **extra))

    @pytest.mark.parametrize(("key", "phrase"), [
        ("entries_count", "{kinds}"),
        ("entries_only_one", "in the {doc} only."),
        ("entries_only_many", "in the {doc} only."),
        ("entries_differ", "{label}: documents differ"),
        ("entries_differ_value", "{value}"),
    ])
    def test_a_summary_phrase_must_have_its_slots(
        self, tmp_path: Path, key: str, phrase: str
    ) -> None:
        with pytest.raises(RegistryValidationError, match="must contain"):
            load_registry(self._write(tmp_path, {key: phrase}))

    def test_periods_always_is_read(self, tmp_path: Path) -> None:
        from rfp_intake.domain.registry import PeriodsDisplay

        registry = load_registry(self._write(
            tmp_path, display=dict(self._PERIODS, always={"cohort": "Cohorts", "arm": "Arms"}),
        ))
        display = registry.get_field("g1.parts").display
        assert isinstance(display, PeriodsDisplay)
        assert display.always == {"cohort": "Cohorts", "arm": "Arms"}

    def test_periods_always_defaults_to_nothing(self, tmp_path: Path) -> None:
        from rfp_intake.domain.registry import PeriodsDisplay

        display = load_registry(self._write(tmp_path, display=self._PERIODS)).get_field(
            "g1.parts").display
        assert isinstance(display, PeriodsDisplay)
        assert display.always == {}

    def test_a_periods_always_heading_needs_words(self, tmp_path: Path) -> None:
        """A line with no heading would print an empty bold Variable cell."""
        with pytest.raises(ValidationError, match="every value in always needs a heading"):
            load_registry(self._write(
                tmp_path, display=dict(self._PERIODS, always={"arm": " "}),
            ))

    def test_a_headline_wording_names_the_value(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="must contain"):
            load_registry(self._write(tmp_path, display=dict(self._ENTRIES, headline_fields=[
                {"field": "g1.ratio", "wording": "randomized"},
            ])))

    def test_the_real_registry_declares_the_three_displays(self, fields_yaml_path: Path) -> None:
        from rfp_intake.domain.registry import EntriesDisplay, PeriodsDisplay

        registry = load_registry(fields_yaml_path)
        assert isinstance(registry.get_field("design.parts").display, EntriesDisplay)
        assert isinstance(registry.get_field("visits.frequency_by_period").display, PeriodsDisplay)
        assert isinstance(registry.get_field("timeline.periods").display, PeriodsDisplay)
        # Oliver's fixed list of visit periods, 2026-10-12.
        [period] = [k for k in registry.get_field("visits.frequency_by_period").entry_keys()
                    if k.name == "period"]
        assert period.values == ["screening", "early_treatment", "later_treatment",
                                 "end_of_treatment", "follow_up", "stopped_early"]
        flagged = [f.id for f in registry.fields if f.one_answer_when_documents_agree]
        assert flagged == ["design.parts"]
        assert registry.get_field("design.parts").may_word_differently == [
            "name", "document_label", "description",
        ]


class TestABreakdownDisplay:
    """Stage 5 of the 2026-10-12 plan (fix 10 and the CRF half of fix 11): a
    `breakdown` display is read and checked when config/fields.yaml loads."""

    _DISPLAY: dict[str, Any] = {
        "kind": "breakdown", "parts_from": "g1.parts", "each": ["per arm"],
        "total": ["total"], "each_differs": "Per arm: {value}",
        "headline": ["{total} ({each} per arm, {also})", "{total}"],
        "also": [{"field": "g1.ratio", "wording": "randomized {value}"}],
    }

    def _write(self, tmp_path: Path, scoped: bool = True, **display: Any) -> Path:
        data = {
            "version": 1,
            "groups": [{"id": "g1", "label": "Group 1"}],
            "fields": [
                {"id": "g1.parts", "group": "g1", "label": "Arms", "type": "object[]",
                 "schema": {"name": "text"},
                 "display": {"kind": "entries", "title": ["{name}"]}},
                {"id": "g1.ratio", "group": "g1", "label": "Ratio", "type": "text"},
                {"id": "g1.n", "group": "g1", "label": "N", "type": "int", "scoped": scoped,
                 "display": dict(self._DISPLAY, **display)},
            ],
            "report_wording": _WORDING,
        }
        p = tmp_path / "fields.yaml"
        p.write_text(yaml.dump(data))
        return p

    def test_it_is_read(self, tmp_path: Path) -> None:
        from rfp_intake.domain.registry import BreakdownDisplay

        display = load_registry(self._write(tmp_path)).get_field("g1.n").display
        assert isinstance(display, BreakdownDisplay)
        assert display.parts_from == "g1.parts"
        assert display.part_line == "{part}: {value}"
        assert display.total_line == "Total: {value}"

    def test_its_parts_are_another_fields_entries(self, tmp_path: Path) -> None:
        with pytest.raises(RegistryValidationError, match="parts_from has no entries"):
            load_registry(self._write(tmp_path, parts_from="g1.ratio"))

    def test_the_field_is_scoped(self, tmp_path: Path) -> None:
        with pytest.raises(RegistryValidationError, match="not scoped"):
            load_registry(self._write(tmp_path, scoped=False))

    @pytest.mark.parametrize(("key", "wording"), [
        ("headline", ["{total} ({sum})"]), ("part_line", "{part}: {n}"),
        ("total_line", "{part}"), ("each_differs", "{total}"),
    ])
    def test_its_wordings_name_only_what_it_has(
        self, tmp_path: Path, key: str, wording: object
    ) -> None:
        with pytest.raises(RegistryValidationError, match="breakdown"):
            load_registry(self._write(tmp_path, **{key: wording}))

    def test_an_also_field_must_exist(self, tmp_path: Path) -> None:
        with pytest.raises(RegistryValidationError, match="also"):
            load_registry(self._write(
                tmp_path, also=[{"field": "g1.none", "wording": "{value}"}],
            ))

    def test_the_two_counts_have_one(self, fields_yaml_path: Path) -> None:
        from rfp_intake.domain.registry import BreakdownDisplay

        registry = load_registry(fields_yaml_path)
        shown = [f.id for f in registry.fields if isinstance(f.display, BreakdownDisplay)]
        assert shown == ["ops.subjects_total", "ops.crf_pages"]
        assert registry.get_field("ops.crf_pages").scoped


def _with_stage6_keys(tmp_path: Path, **keys: object) -> Path:
    """One enum field and one scoped int field, the second carrying `keys`
    where they name `g1.count`, the first otherwise. Stage 6 of the
    2026-10-12 plan."""
    form: dict[str, object] = {
        "id": "g1.form", "group": "g1", "label": "Form", "type": "enum",
        "values": ["tablet", "infusion"],
    }
    count: dict[str, object] = {
        "id": "g1.count", "group": "g1", "label": "Count", "type": "int", "scoped": True,
    }
    for name, value in keys.items():
        (count if name == "when_not_found" else form)[name] = value
    data = {
        "version": 1,
        "groups": [{"id": "g1", "label": "Group 1"}],
        "fields": [form, count],
        "report_wording": _WORDING,
    }
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data))
    return p


def test_the_stage6_keys_are_read(tmp_path: Path) -> None:
    registry = load_registry(_with_stage6_keys(
        tmp_path,
        quote_must_say={"infusion": ["IV", "infusion"]},
        quote_must_mention=["dose", "drug"],
        prefer_sections=["schedule of assessments"],
        when_not_found={"lines": ["No total."], "because_scopes": ["why no total"],
                        "see": ["g1.form"]},
    ))
    form, count = registry.get_field("g1.form"), registry.get_field("g1.count")
    assert form.quote_must_say == {"infusion": ["IV", "infusion"]}
    assert form.quote_must_mention == ["dose", "drug"]
    assert form.prefer_sections == ["schedule of assessments"]
    note = count.when_not_found
    assert note is not None and note.see == ["g1.form"]
    assert note.because_line == '"{quote}" ({pages})'
    assert note.see_line == "See {label}."


@pytest.mark.parametrize(("keys", "message"), [
    ({"quote_must_say": {"capsule": ["oral"]}}, "quote_must_say for values it does not allow"),
    ({"quote_must_say": {"infusion": [" "]}}, "quote_must_say value with no words"),
    ({"quote_must_mention": ["dose", " "]}, "empty quote_must_mention word"),
    ({"prefer_sections": ["schedule", " "]}, "empty prefer_sections phrase"),
    ({"when_not_found": {"see": ["g1.nothing"]}}, "when_not_found sees unknown field"),
    ({"when_not_found": {"see": ["g1.count"]}}, "when_not_found sees unknown field"),
    ({"when_not_found": {"because_scopes": [""]}}, "empty because_scopes phrase"),
    ({"when_not_found": {"see_line": "See {label} on {page}."}}, "unknown placeholders: page"),
])
def test_the_stage6_keys_are_checked(
    tmp_path: Path, keys: dict[str, object], message: str
) -> None:
    with pytest.raises(RegistryValidationError, match=message):
        load_registry(_with_stage6_keys(tmp_path, **keys))


@pytest.mark.parametrize(("note", "message"), [
    ({"because_line": "({pages})"}, "must contain {quote}"),
    ({"see_line": "See it."}, "must contain {label}"),
])
def test_a_not_found_note_keeps_the_quote_and_the_label(
    tmp_path: Path, note: dict[str, str], message: str
) -> None:
    with pytest.raises(Exception, match=message):
        load_registry(_with_stage6_keys(tmp_path, when_not_found=note))


def test_because_scopes_need_a_scoped_field(tmp_path: Path) -> None:
    p = _with_stage6_keys(tmp_path, when_not_found={"because_scopes": ["why no total"]})
    data = yaml.safe_load(p.read_text())
    data["fields"][1]["scoped"] = False
    p.write_text(yaml.dump(data))
    with pytest.raises(RegistryValidationError, match="has because_scopes but is not scoped"):
        load_registry(p)


class TestTheKeysOfThe29October2026Layout:
    """Angus Gray, 29 October 2026: a variable printed under another
    (`shown_under`), a periods display's `always` lines, and a schedule
    consideration kept out of the visit table (`show`)."""

    def _write(self, tmp_path: Path, **shown_under: str) -> Path:
        """Three fields in one group; `shown_under` maps a field id to the id
        it is printed under."""
        fields: list[dict[str, object]] = [
            {"id": f"g1.{name}", "group": "g1", "label": name.title(), "type": "text"}
            for name in ("a", "b", "c")
        ]
        for f in fields:
            if f["id"] in shown_under:
                f["shown_under"] = shown_under[str(f["id"])]
        data = {
            "version": 1,
            "groups": [{"id": "g1", "label": "Group 1"}],
            "fields": fields,
            "report_wording": _WORDING,
        }
        p = tmp_path / "fields.yaml"
        p.write_text(yaml.dump(data))
        return p

    def test_shown_under_is_read(self, tmp_path: Path) -> None:
        registry = load_registry(self._write(tmp_path, **{"g1.b": "g1.a", "g1.c": "g1.a"}))
        assert registry.get_field("g1.b").shown_under == "g1.a"
        assert registry.get_field("g1.c").shown_under == "g1.a"
        assert registry.get_field("g1.a").shown_under is None

    @pytest.mark.parametrize(("shown_under", "message"), [
        ({"g1.b": "g1.b"}, "Field g1.b is shown under itself"),
        ({"g1.b": "g1.nothing"}, "Field g1.b is shown under an unknown field: g1.nothing"),
        ({"g1.b": "g1.a", "g1.c": "g1.b"},
         "Field g1.c is shown under g1.b, which is itself shown under another field"),
    ])
    def test_a_wrong_shown_under_does_not_load(
        self, tmp_path: Path, shown_under: dict[str, str], message: str
    ) -> None:
        with pytest.raises(RegistryValidationError, match=re.escape(message)):
            load_registry(self._write(tmp_path, **shown_under))

    def test_the_real_registry_prints_two_variables_under_the_patient_timeline(
        self, fields_yaml_path: Path
    ) -> None:
        registry = load_registry(fields_yaml_path)
        under = [f.id for f in registry.fields if f.shown_under is not None]
        assert sorted(under) == ["timeline.budget_window", "timeline.total_duration"]
        assert {registry.get_field(i).shown_under for i in under} == {"timeline.periods"}

    def test_the_real_patient_timeline_lists_its_periods_always(
        self, fields_yaml_path: Path
    ) -> None:
        from rfp_intake.domain.registry import PeriodsDisplay

        registry = load_registry(fields_yaml_path)
        field = registry.get_field("timeline.periods")
        display = field.display
        assert isinstance(display, PeriodsDisplay)
        assert display.always["screening"] == "Screening"
        assert "other" not in display.always
        [order] = [k for k in field.entry_keys() if k.name == display.order_key]
        assert set(display.always) <= set(order.values)

    def test_a_consideration_is_shown_unless_it_says_not(
        self, fields_yaml_path: Path
    ) -> None:
        registry = load_registry(fields_yaml_path)
        assert registry.schedule_table is not None
        hidden = [c.id for c in registry.schedule_table.considerations if not c.show]
        assert hidden == ["premedication", "concomitant_chemotherapy"]
        # Still not assessments: the must-hold facts 10 and 11 count without them.
        assert not any(c.assessment for c in registry.schedule_table.considerations
                       if not c.show)

    def test_the_new_phrases_have_their_defaults(self, tmp_path: Path) -> None:
        wording = load_registry(self._write(tmp_path)).report_wording
        assert wording.not_found == "Not found in the documents"
        assert wording.row_disagree == "Documents disagree — see Disagreement {n}"
        assert wording.row_provisional == "Shown provisionally: {value} ({why})"
        assert wording.counted_note == (
            "Worked out by the tool from {heading}, not read from a document."
        )

    @pytest.mark.parametrize(("key", "phrase"), [
        ("row_disagree", "Documents disagree"),
        ("row_provisional", "Shown provisionally ({why})"),
        ("row_provisional", "Shown provisionally: {value}"),
        ("counted_note", "Worked out by the tool."),
    ])
    def test_a_new_phrase_must_have_its_slots(
        self, tmp_path: Path, key: str, phrase: str
    ) -> None:
        p = self._write(tmp_path)
        data = yaml.safe_load(p.read_text())
        data["report_wording"][key] = phrase
        p.write_text(yaml.dump(data))
        with pytest.raises(Exception, match="must contain"):
            load_registry(p)
