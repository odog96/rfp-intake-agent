"""EXTRACT's repair retry shows the model the nearest passage and nothing more.

Fix 1 of the 2026-10-09 brief, built on Oliver's condition of 2026-10-11: the
retry may show the model the exact text near a failed quote, the model's new
quote must pass the same word-for-word check as any other, code must never use
that text as the quote itself, and a retry quote that still fails is dropped
with the rejected answer saved in the run's errors.

The model is a scripted stand-in that returns one prepared response per call
and keeps the messages it was sent, so nothing here reaches a real model.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import BaseMessage

from rfp_intake.domain.dynamic import FieldExtractionItem, _field_id_to_attr, build_extraction_model
from rfp_intake.domain.registry import FieldDef, Registry, get_registry
from rfp_intake.domain.schemas import Document, ExtractionTask

_GROUP = "operational_metrics"

_PAGE_3 = (
    "Site Qualification Visits &      1 visit per site; a total of 80 visits to \n \n"
    "Time on Site                     qualify 75 sites; an average of 5 hours \n \n"
    "                                 per visit"
)
_BROKEN_QUOTE = "a total of 80 visits to qualify 75 sites"
# The best-scoring stretch starts at "a total", plus four words either side.
_PASSAGE = (
    "1 visit per site; a total of 80 visits to "
    "Time on Site qualify 75 sites; an average of 5 hours per visit"
)
_GOOD_QUOTE = "1 visit per site; a total of 80 visits to"


class _ScriptedModel:
    """Returns the prepared responses in order and records every call's messages."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[list[BaseMessage]] = []

    def extract(self, schema: type, messages: list[BaseMessage]) -> Any:
        self.calls.append(list(messages))
        return self.responses.pop(0)


def _text_field(registry: Registry) -> FieldDef:
    # Any plain-text field in the group: the retry is the same for every field.
    return next(
        f for f in registry.get_fields_for_group(_GROUP) if not f.derived and f.type == "text"
    )


def _response(registry: Registry, field: FieldDef, quote: str) -> Any:
    schema = build_extraction_model(_GROUP, registry)
    item = FieldExtractionItem(raw_value=quote, quote=quote, confidence=0.9, page=3)
    return schema(**{_field_id_to_attr(field.id): [item]})


def _run(
    monkeypatch: pytest.MonkeyPatch, fields_yaml_path: Any, retry_quote: str
) -> tuple[Any, Any, _ScriptedModel, FieldDef]:
    monkeypatch.setenv("RFP_INTAKE_FIELDS_YAML_PATH", str(fields_yaml_path))
    get_registry.cache_clear()
    registry = get_registry()
    field = _text_field(registry)
    model = _ScriptedModel([
        _response(registry, field, _BROKEN_QUOTE),
        _response(registry, field, retry_quote),
    ])

    import rfp_intake.extract as extract

    monkeypatch.setattr(extract, "get_llm", lambda role: None)
    monkeypatch.setattr(extract, "get_structured_output_for_role", lambda llm, role: model)

    doc = Document(id="doc-1", path="/tmp/rfp.pdf", kind="rfp", pages=3, page_texts={3: _PAGE_3})
    task = ExtractionTask(doc_id="doc-1", group=_GROUP, page_window=(1, 3))
    records, errors = extract.extract_group(task, doc, registry)
    return records, errors, model, field


class TestTheRetryShowsTheNearestPassage:
    def test_the_retry_message_holds_the_exact_text(
        self, monkeypatch: pytest.MonkeyPatch, fields_yaml_path: Any
    ) -> None:
        _, _, model, field = _run(monkeypatch, fields_yaml_path, _GOOD_QUOTE)

        assert len(model.calls) == 2, "one call, then one repair retry"
        retry = str(model.calls[1][-1].content)
        assert f"{field.id}: quote_not_found_in_excerpt" in retry
        assert f'reads exactly: "{_PASSAGE}"' in retry

    def test_a_retry_quote_copied_from_the_passage_is_kept(
        self, monkeypatch: pytest.MonkeyPatch, fields_yaml_path: Any
    ) -> None:
        records, errors, _, field = _run(monkeypatch, fields_yaml_path, _GOOD_QUOTE)

        assert errors == []
        [record] = [r for r in records if r.field_id == field.id]
        assert record.quote == _GOOD_QUOTE, "the model's own quote, not the passage"


class TestARetryQuoteThatStillFailsIsDropped:
    """The case Oliver asked for: no quote is ever made for the model."""

    def test_the_record_is_dropped_and_the_rejected_answer_saved(
        self, monkeypatch: pytest.MonkeyPatch, fields_yaml_path: Any
    ) -> None:
        records, errors, _, field = _run(monkeypatch, fields_yaml_path, _BROKEN_QUOTE)

        assert [r for r in records if r.field_id == field.id] == [], (
            "the passage was there to copy, but code never uses it as the quote"
        )
        [error] = errors
        assert error.task_id == f"doc-1:{_GROUP}:{field.id}"
        assert error.error == "Validation failed after retry: quote_not_found_in_excerpt"
        assert error.kind == "validation"
        assert error.rejected_value == _BROKEN_QUOTE
        assert error.rejected_quote == _BROKEN_QUOTE

    def test_the_rejected_answer_reaches_extraction_json(
        self, monkeypatch: pytest.MonkeyPatch, fields_yaml_path: Any
    ) -> None:
        _, errors, _, _ = _run(monkeypatch, fields_yaml_path, _BROKEN_QUOTE)

        dumped = errors[0].model_dump(mode="json")
        assert dumped["rejected_quote"] == _BROKEN_QUOTE
        assert dumped["rejected_value"] == _BROKEN_QUOTE
