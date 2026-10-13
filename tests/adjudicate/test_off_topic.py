"""ADJUDICATE's off-topic records, fix 5 of the 2026-10-12 brief.

Run r-20261012-angus showed a laboratory sample instruction as a site
monitoring strategy. ADJUDICATE may now name records that are not about the
field at all; those records get no row and are not cited as the row's sources,
and the report keeps them in Appendix A under their own marker
(tests/render/test_stage6_display.py). Two safeguards: a `conflict` verdict acts
on none of them, and a list naming every record is ignored.

The model is a scripted stand-in returning one prepared AdjudicationResult.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import BaseMessage

from rfp_intake.adjudicate import AdjudicationResult, _adjudicate_one
from rfp_intake.domain.precedence import get_precedence_policy
from rfp_intake.domain.registry import get_registry
from rfp_intake.domain.schemas import Contradiction, FieldRecord, Provenance

_FIELD = "monitoring.strategy_guidance"


@pytest.fixture(autouse=True)
def _real_registry(fields_yaml_path: Path) -> None:
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    get_registry.cache_clear()


class _Scripted:
    def __init__(self, result: AdjudicationResult) -> None:
        self.result = result
        self.messages: list[BaseMessage] = []

    def extract(self, schema: type, messages: list[BaseMessage]) -> Any:
        self.messages = messages
        return self.result


def _record(value: str, page: int, confidence: float = 0.9) -> FieldRecord:
    r = FieldRecord(
        field_id=_FIELD, group="clinical_monitoring", raw_value=value, quote=f"quote {value}",
        provenance=Provenance(doc_id="d-am", doc_kind="amendment", page=page),
        confidence=confidence,
    )
    r.value = value
    return r


def _records() -> list[FieldRecord]:
    return [
        _record("risk-based monitoring", 60),
        _record("samples measured by a central laboratory", 70, confidence=0.95),
        _record("remote and on-site monitoring", 61),
    ]


def _adjudicate(
    verdict: str, off_topic: list[int], winning_record: int | None = None,
) -> tuple[Contradiction, list[Any], _Scripted]:
    model = _Scripted(AdjudicationResult(
        verdict=verdict,  # type: ignore[arg-type]
        explanation="record 2 is a laboratory instruction, not site monitoring",
        winning_record=winning_record, off_topic_records=off_topic,
    ))
    contradiction, fields = _adjudicate_one(
        Contradiction(field_id=_FIELD, records=_records()),
        get_registry().get_field(_FIELD), get_precedence_policy(), model,  # type: ignore[arg-type]
    )
    return contradiction, fields, model


def test_not_a_conflict_gives_no_row_to_an_off_topic_record() -> None:
    contradiction, fields, _ = _adjudicate("not_a_conflict", [2])
    assert contradiction.off_topic_record_indexes == [2]
    assert [f.value for f in fields] == ["risk-based monitoring", "remote and on-site monitoring"]
    assert all(f.contradiction is contradiction for f in fields)


def test_reconcilable_neither_shows_nor_cites_an_off_topic_record() -> None:
    contradiction, [field], _ = _adjudicate("reconcilable", [2], winning_record=3)
    assert field.value == "remote and on-site monitoring"
    assert [s.page for s in field.sources] == [60, 61]
    assert contradiction.winning_record_index == 3


def test_a_winner_also_called_off_topic_falls_back_to_the_most_confident_usable() -> None:
    # Record 2 is the most confident, but it is off-topic, so record 1 is shown.
    contradiction, [field], _ = _adjudicate("reconcilable", [2], winning_record=2)
    assert field.value == "risk-based monitoring"
    assert contradiction.winning_record_index == 1


def test_a_conflict_acts_on_no_off_topic_record() -> None:
    contradiction, _, _ = _adjudicate("conflict", [2])
    assert contradiction.off_topic_record_indexes == []


def test_a_list_naming_every_record_is_ignored() -> None:
    contradiction, fields, _ = _adjudicate("not_a_conflict", [1, 2, 3])
    assert contradiction.off_topic_record_indexes == []
    assert len(fields) == 3


def test_numbers_outside_the_list_are_dropped() -> None:
    contradiction, fields, _ = _adjudicate("not_a_conflict", [0, 2, 2, 9])
    assert contradiction.off_topic_record_indexes == [2]
    assert len(fields) == 2


def test_the_prompt_asks_for_off_topic_records_and_gives_the_hint() -> None:
    _, _, model = _adjudicate("not_a_conflict", [])
    system, human = (str(m.content) for m in model.messages)
    assert "off_topic_records" in system
    hint = get_registry().get_field(_FIELD).hint
    assert hint and f"What the field asks for: {' '.join(hint.split())}" in human
