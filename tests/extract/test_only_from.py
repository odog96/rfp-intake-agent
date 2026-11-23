"""The registry key `only_from` (config/fields.yaml): EXTRACT asks for such a
field only in documents of the kinds it lists.

Added 2026-11-23 for the time window the RFP asks the budget to cover (Angus
Gray, 29 October 2026). The field is found here by its key, not by its id, so
these tests follow config/fields.yaml (rule 4). The field is left out of the
extraction prompt's field list and of the response schema for any other kind
of document, and a record the model still returns for it is rejected and kept
in the run's errors. The model is a scripted stand-in, as in
tests/extract/test_answers_field.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from langchain_core.messages import BaseMessage
from pydantic import ValidationError

from rfp_intake.domain.dynamic import (
    FieldExtractionItem,
    _field_id_to_attr,
    build_extraction_model,
    fields_to_extract,
)
from rfp_intake.domain.registry import (
    FieldDef,
    Registry,
    RegistryValidationError,
    get_registry,
    load_registry,
)
from rfp_intake.domain.schemas import Document, ExtractionTask
from rfp_intake.extract.prompt import build_extract_prompt

_QUOTE = "The budget should cover 40 months from first patient in."


@pytest.fixture()
def registry(monkeypatch: pytest.MonkeyPatch, fields_yaml_path: Path) -> Registry:
    monkeypatch.setenv("RFP_INTAKE_FIELDS_YAML_PATH", str(fields_yaml_path))
    get_registry.cache_clear()
    return get_registry()


def _rfp_only(registry: Registry) -> FieldDef:
    """The registry's field read only from the RFP."""
    [field] = [f for f in registry.fields if f.only_from == ["rfp"]]
    return field


def _doc(kind: str) -> Document:
    return Document(
        id=f"doc-{kind}", path=f"/tmp/{kind}.pdf", kind=kind,  # type: ignore[arg-type]
        pages=1, page_texts={1: _QUOTE},
    )


def _task(field: FieldDef, kind: str) -> ExtractionTask:
    return ExtractionTask(doc_id=f"doc-{kind}", group=field.group, page_window=(1, 1))


def _fields_section(messages: list[BaseMessage]) -> str:
    return str(messages[0].content).split("FIELDS")[1]


class TestTheRegistry:
    def test_the_key_is_read(self, registry: Registry) -> None:
        field = _rfp_only(registry)
        assert field.only_from == ["rfp"]
        assert not field.derived
        # Every other field is asked of every kind of document.
        assert all(f.only_from == [] for f in registry.fields if f is not field)

    def _load(self, fields_yaml_path: Path, tmp_path: Path, **keys: Any) -> Registry:
        data = yaml.safe_load(fields_yaml_path.read_text())
        rfp_only = next(f for f in data["fields"] if f.get("only_from") == ["rfp"])
        rfp_only.update(keys)
        p = tmp_path / "fields.yaml"
        p.write_text(yaml.dump(data, allow_unicode=True))
        return load_registry(p)

    def test_a_derived_field_cannot_have_it(self, fields_yaml_path: Path, tmp_path: Path) -> None:
        with pytest.raises(RegistryValidationError, match="has only_from but is derived"):
            self._load(fields_yaml_path, tmp_path, derived=True, derived_from=["study.title"])

    def test_a_kind_named_twice_is_an_error(self, fields_yaml_path: Path, tmp_path: Path) -> None:
        with pytest.raises(RegistryValidationError, match="names a document kind twice"):
            self._load(fields_yaml_path, tmp_path, only_from=["rfp", "rfp"])

    def test_an_unknown_kind_is_an_error(self, fields_yaml_path: Path, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="only_from"):
            self._load(fields_yaml_path, tmp_path, only_from=["rfp", "sponsor_letter"])

    def test_several_kinds_load(self, fields_yaml_path: Path, tmp_path: Path) -> None:
        registry = self._load(fields_yaml_path, tmp_path, only_from=["rfp", "soa"])
        assert ["rfp", "soa"] in [f.only_from for f in registry.fields]


class TestThePromptAndTheSchema:
    def test_in_the_prompt_for_an_rfp_and_not_for_a_protocol(self, registry: Registry) -> None:
        field = _rfp_only(registry)
        rfp = _fields_section(build_extract_prompt(_task(field, "rfp"), _doc("rfp"), registry))
        protocol = _fields_section(
            build_extract_prompt(_task(field, "protocol"), _doc("protocol"), registry)
        )
        assert f"- {field.id} (" in rfp
        assert field.id not in protocol
        # The group's other fields are still asked of the protocol.
        others = [f for f in fields_to_extract(field.group, registry) if f is not field]
        assert others and all(f"- {f.id} (" in protocol for f in others)

    def test_in_the_schema_for_an_rfp_and_not_for_a_protocol(self, registry: Registry) -> None:
        field = _rfp_only(registry)
        attr = _field_id_to_attr(field.id)
        assert attr in build_extraction_model(field.group, registry, "rfp").model_fields
        assert attr not in build_extraction_model(field.group, registry, "protocol").model_fields
        # With no kind, every field: the behaviour before only_from.
        assert attr in build_extraction_model(field.group, registry).model_fields

    @pytest.mark.parametrize("kind", ["amendment", "soa", "other"])
    def test_absent_for_every_other_kind(self, registry: Registry, kind: str) -> None:
        field = _rfp_only(registry)
        assert field not in fields_to_extract(field.group, registry, kind)


class _ScriptedModel:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.schemas: list[type] = []

    def extract(self, schema: type, messages: list[BaseMessage]) -> Any:
        self.schemas.append(schema)
        return self.responses.pop(0)


def _extract(
    monkeypatch: pytest.MonkeyPatch, registry: Registry, kind: str,
) -> tuple[Any, Any, _ScriptedModel, FieldDef]:
    """A response that still holds the field, from a schema built for every kind."""
    import rfp_intake.extract as extract

    field = _rfp_only(registry)
    item = FieldExtractionItem(raw_value="40 months", quote=_QUOTE, confidence=0.9, page=1)
    full = build_extraction_model(field.group, registry)
    model = _ScriptedModel([full(**{_field_id_to_attr(field.id): [item]})])
    monkeypatch.setattr(extract, "get_llm", lambda role: None)
    monkeypatch.setattr(extract, "get_structured_output_for_role", lambda llm, role: model)
    records, errors = extract.extract_group(_task(field, kind), _doc(kind), registry)
    return records, errors, model, field


class TestExtractGroup:
    def test_a_record_from_another_kind_of_document_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, registry: Registry,
    ) -> None:
        records, errors, model, field = _extract(monkeypatch, registry, "protocol")

        assert _field_id_to_attr(field.id) not in model.schemas[0].model_fields
        assert [r for r in records if r.field_id == field.id] == []
        [error] = errors
        assert error.error == (
            f"Rejected: {field.id} is read only from rfp documents (only_from), "
            "and this document is protocol"
        )
        assert error.kind == "validation"
        assert error.task_id == f"doc-protocol:{field.group}:{field.id}"
        assert (error.rejected_value, error.rejected_quote, error.rejected_page) == (
            "40 months", _QUOTE, 1,
        )
        assert len(model.schemas) == 1, "no repair retry for a field not asked for"

    def test_the_same_record_from_an_rfp_is_kept(
        self, monkeypatch: pytest.MonkeyPatch, registry: Registry,
    ) -> None:
        records, errors, model, field = _extract(monkeypatch, registry, "rfp")

        assert _field_id_to_attr(field.id) in model.schemas[0].model_fields
        assert errors == []
        [record] = [r for r in records if r.field_id == field.id]
        assert record.provenance.doc_kind == "rfp"

    def test_a_group_with_nothing_to_ask_makes_no_call(
        self, monkeypatch: pytest.MonkeyPatch, fields_yaml_path: Path, tmp_path: Path,
    ) -> None:
        """Every field of the group read only from the RFP, and a protocol."""
        import rfp_intake.extract as extract

        data = yaml.safe_load(fields_yaml_path.read_text())
        group = next(f for f in data["fields"] if f.get("only_from") == ["rfp"])["group"]
        for f in data["fields"]:
            if f["group"] == group and not f.get("derived"):
                f["only_from"] = ["rfp"]
        p = tmp_path / "fields.yaml"
        p.write_text(yaml.dump(data, allow_unicode=True))
        registry = load_registry(p)

        model = _ScriptedModel([])
        monkeypatch.setattr(extract, "get_llm", lambda role: None)
        monkeypatch.setattr(extract, "get_structured_output_for_role", lambda llm, role: model)
        task = ExtractionTask(doc_id="doc-protocol", group=group, page_window=(1, 1))
        assert extract.extract_group(task, _doc("protocol"), registry) == ([], [])
        assert model.schemas == []
