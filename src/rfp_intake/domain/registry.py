"""Load and validate config/fields.yaml into typed Pydantic models."""

from __future__ import annotations

import hashlib
import re
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
    # One line of plain English for a reader without a clinical background,
    # printed under the group's heading in report.pdf. See FieldDef.plain.
    plain: str | None = None
    search_hints: SearchHints = Field(default_factory=SearchHints)
    # How many of this group's highest-scoring sections PLAN keeps. Per group and
    # not one constant for the whole registry, because how widely a group's
    # evidence is scattered is a property of the group: most groups are answered
    # by one or two sections, while blinding_monitoring needs four sections of the
    # sample protocol that sit on three different pages. Three was a module
    # constant in plan/__init__.py until 2026-10-03, and raising it there would
    # have raised it for all nine groups and sent every group more text than it
    # needs. See CLAUDE.md item 4c for the measurement behind the value.
    top_k: int = Field(default=3, ge=1)


class ObjectFieldSchema(BaseModel):
    """Schema definition for object[] typed fields."""

    fields: dict[str, str] = Field(default_factory=dict)


def _as_value_token(v: Any) -> str:
    """YAML's `no` is a boolean; config/fields.yaml means the enum token "no"."""
    return str(v).lower() if isinstance(v, bool) else str(v)


class ResolutionRule(BaseModel):
    """One answer a clinical reviewer says follows from another field's answer.

    Added 2026-10-07 for change 3 of that day's review: Angus Gray's rule that a
    Phase 2, 3 or 4 study is never first-in-human, so the answer is No at full
    confidence and no passage needs to be searched for. The rule lives in
    config/fields.yaml next to the field it answers, because it is clinical
    knowledge rather than code — non-negotiable rule 4 forbids keying pipeline
    behaviour to a field name in Python, and a rule written here is read by
    reconcile/rules.py without naming any field.

    `because` is printed as the resolved field's note, so a reviewer reading
    report.pdf sees why the pipeline answered without quoting a passage about
    the field itself. The evidence shown is the trigger field's own passage.
    """

    when_field: str
    when_value_in: list[str]
    value: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    because: str | None = None

    @field_validator("when_value_in", mode="before")
    @classmethod
    def _coerce_tokens(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [_as_value_token(item) for item in v]
        return v

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, v: Any) -> Any:
        return _as_value_token(v)

    @field_validator("because", mode="before")
    @classmethod
    def _one_line(cls, v: Any) -> Any:
        # YAML's folded block scalar leaves a trailing newline, and this text is
        # printed in a report line.
        return " ".join(str(v).split()) if v is not None else None


class ScopeLeftOut(BaseModel):
    """Rows a derived field's rubric must not read, named by words in their scope.

    Added 2026-10-11, approved by Oliver, for the visit-intensity rating: a
    separate schedule for patients who stopped treatment early must not be added
    to the main count, because IQVIA budgets dropouts separately
    (docs/ANALYST_PROCEDURE_PROTOCOL.md section 9). Which scopes those are is
    clinical knowledge, so it lives in config/fields.yaml; derive/__init__.py
    reads it without naming any field.

    A phrase matches a scope when its words appear in the scope one after
    another, ignoring case and punctuation: "post etd" matches "Post-ETD
    (early discontinuation)". `reason` is printed in the derived row's note
    beside each row left out, so a reviewer sees what was not counted and why.
    """

    phrases: list[str]
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _one_line(cls, v: Any) -> Any:
        return " ".join(str(v).split())


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
    # What this variable means, for a reader without a clinical background.
    #
    # `plain` and `hint` are not interchangeable and must not be swapped.
    # `hint` is an instruction written for the extraction model ("2-4 sentences
    # maximum") and is never printed in a report. `plain` is written for the
    # person reading report.pdf and is never put in a prompt — the glossary in
    # render/pdf_renderer.py is its only consumer.
    plain: str | None = None
    scoped: bool = False
    source_priority: str | None = None
    budget_driver: bool = False
    derived: bool = False
    derived_from: list[str] | None = None
    # Rows of the `derived_from` inputs that this field's rubric must not read.
    # See ScopeLeftOut.
    leave_out_scopes: ScopeLeftOut | None = None
    # Each scope names what that part of the study is — for an arm, the treatment
    # it receives — so RECONCILE matches scopes across documents on that name.
    # An entry whose own text does not say what its scope names is not matched,
    # and a scope only one of several documents lists is flagged. Added for
    # design.parts on 2026-10-12. See reconcile/__init__.py:_scope_checks.
    check_scopes_across_documents: bool = False
    schema_def: dict[str, str] | None = Field(default=None, alias="schema")
    # Answers that follow from another field's answer. See ResolutionRule.
    resolution_rules: list[ResolutionRule] = Field(default_factory=list)
    # Another field whose disagreement is this field's disagreement too, named
    # where a reviewer can see it rather than worked out by code that could not
    # know. A list field is the case that needs it: RECONCILE folds a list's
    # records into one answer instead of comparing them across documents, so a
    # list cannot notice that the documents disagree about it, however plainly
    # they do. GATE reads this and marks the field with the same disagreement
    # number, so the report points at one disagreement from both rows. Added
    # 2026-10-07 for change 4 of that day's review (interim.timing following
    # interim.count).
    follows_conflict_of: str | None = None

    model_config = {"populate_by_name": True}


class ReportWording(BaseModel):
    """Phrases report.pdf prints that belong to no one field, `report_wording` in
    config/fields.yaml.

    Added 2026-10-12 for CLAUDE.md open item 3: the Appendix A line saying which
    side of a disagreement the report used. It lives in YAML, like `plain`, so a
    clinical reviewer can have it corrected without a code change. `{n}` stands
    for the Disagreement number, and the two phrases that send the reader to a
    Disagreement must contain it.
    """

    shown_provisional: str = Field(min_length=1)
    not_used: str = Field(min_length=1)
    no_value_chosen: str = Field(min_length=1)

    @field_validator("not_used", "no_value_chosen")
    @classmethod
    def _names_the_disagreement(cls, v: str) -> str:
        if "{n}" not in v:
            raise ValueError("must contain {n}, the Disagreement number")
        return v

    @staticmethod
    def fill(phrase: str, n: int) -> str:
        # replace, not str.format: a brace elsewhere in the wording must not break a report.
        return phrase.replace("{n}", str(n))


class Registry(BaseModel):
    version: int
    groups: list[GroupDef]
    fields: list[FieldDef]
    registry_version: str
    report_wording: ReportWording

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

    # Only a derived field reads rows from other fields, so only one can leave
    # some out, and a phrase with no words would match every scope.
    for f in fields:
        if f.leave_out_scopes is None:
            continue
        if not f.derived:
            errors.append(f"Field {f.id} leaves out scopes but is not derived")
        if not f.leave_out_scopes.phrases or not all(
            re.findall(r"[a-z0-9]+", p.lower()) for p in f.leave_out_scopes.phrases
        ):
            errors.append(f"Field {f.id} has an empty leave_out_scopes phrase")

    # RECONCILE reads records, and a derived field has none.
    for f in fields:
        if f.check_scopes_across_documents and f.derived:
            errors.append(f"Field {f.id} checks scopes across documents but is derived")

    # A field follows a real other field's disagreement, and not its own.
    for f in fields:
        if f.follows_conflict_of is None:
            continue
        if f.follows_conflict_of == f.id:
            errors.append(f"Field {f.id} follows its own conflict")
        elif f.follows_conflict_of not in seen_ids:
            errors.append(
                f"Field {f.id} follows the conflict of an unknown field: "
                f"{f.follows_conflict_of}"
            )

    # Resolution rules reference a real field, a value the field allows, and a
    # target the pipeline can write in one row.
    by_id = {f.id: f for f in fields}
    for f in fields:
        for rule in f.resolution_rules:
            trigger = by_id.get(rule.when_field)
            if trigger is None:
                errors.append(
                    f"Field {f.id} has a resolution rule on unknown field: {rule.when_field}"
                )
            elif trigger.values:
                unknown = [v for v in rule.when_value_in if v not in trigger.values]
                if unknown:
                    errors.append(
                        f"Field {f.id} has a resolution rule on {rule.when_field} "
                        f"values it does not allow: {', '.join(unknown)}"
                    )
            if rule.when_field == f.id:
                errors.append(f"Field {f.id} has a resolution rule on itself")
            if f.values and rule.value not in f.values:
                errors.append(
                    f"Field {f.id} has a resolution rule setting a value it does not "
                    f"allow: {rule.value}"
                )
            # One rule writes one row, so it cannot answer a field that resolves
            # once per country, cohort or arm.
            if f.scoped or (trigger is not None and trigger.scoped):
                errors.append(
                    f"Field {f.id} has a resolution rule, which is not supported for "
                    "scoped fields"
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

    if "report_wording" not in data:
        raise RegistryValidationError(f"{path} has no report_wording section")
    try:
        report_wording = ReportWording.model_validate(data["report_wording"])
    except ValueError as e:
        raise RegistryValidationError(f"report_wording in {path}: {e}") from e

    registry_version = f"v{version}:{content_hash}"

    return Registry(
        version=version,
        groups=groups,
        fields=fields,
        registry_version=registry_version,
        report_wording=report_wording,
    )


@lru_cache(maxsize=1)
def get_registry() -> Registry:
    """Singleton access to the validated registry."""
    return load_registry()
