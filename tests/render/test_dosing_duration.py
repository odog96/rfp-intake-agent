"""How long patients are dosed — Prompt B item 5 (Angus Gray, 13 October call,
14:28-14:37). The dosing row said "Every 28 days" and never until when. The
answer comes from a quoted passage, as every value does; with none, the row
reads "Duration not stated" (`when_not_found` in config/fields.yaml)."""

from __future__ import annotations

import os

import pytest

from rfp_intake.domain.registry import get_registry
from rfp_intake.domain.schemas import FieldRecord, Provenance, RunState
from rfp_intake.gate import gate_node
from rfp_intake.reconcile import reconcile_node
from rfp_intake.render.report_model import build_report_model

DURATION = "dosing.duration"
FREQUENCY = "dosing.frequency"
P54 = (
    "The study drug will be administered once every 28 days from the time the subject is "
    "randomized until the EOT/Early Treatment Discontinuation (ETD) Visit"
)


@pytest.fixture(autouse=True)
def _real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    get_registry.cache_clear()


def _record(field_id: str, value: str, quote: str, page: int) -> FieldRecord:
    return FieldRecord(
        field_id=field_id,
        group="treatment_dosing",
        raw_value=value,
        value=value,
        quote=quote,
        provenance=Provenance(doc_id="doc-1", doc_kind="amendment", page=page),
        confidence=0.95,
        status="found",
    )


def _rows(records: list[FieldRecord]):  # type: ignore[no-untyped-def]
    state = RunState(run_id="t", records=records)
    state = state.model_copy(update=reconcile_node(state))
    state = state.model_copy(update=gate_node(state))
    # The rows as printed: all_rows() would open a not-found row into its records.
    return [r for g in build_report_model(state, get_registry()).groups for r in g.rows]


def test_the_field_sits_straight_after_the_dosing_schedule() -> None:
    ids = [f.id for f in get_registry().fields]
    assert ids.index(DURATION) == ids.index(FREQUENCY) + 1
    assert get_registry().get_field(DURATION).group == get_registry().get_field(FREQUENCY).group


def test_a_stated_duration_is_shown_with_its_passage() -> None:
    rows = _rows([
        _record(FREQUENCY, "once every 28 days", P54, 54),
        _record(DURATION, "Until the EOT/ETD Visit, from randomization", P54, 54),
    ])
    ids = [r.field_id for r in rows]
    assert ids.index(DURATION) == ids.index(FREQUENCY) + 1, "read together in the report"
    [row] = [r for r in rows if r.field_id == DURATION]
    assert row.values[0].items == ["Until the EOT/ETD Visit, from randomization"]
    assert "54" in row.values[0].sources


def test_no_passage_reads_duration_not_stated() -> None:
    rows = _rows([_record(FREQUENCY, "once every 28 days", P54, 54)])
    [row] = [r for r in rows if r.field_id == DURATION]
    # Rule 3 in the value; the note in grey under the (empty) pages.
    assert row.status == "not_found"
    assert row.table[0].values == ["Not found in the documents"]
    assert row.table[0].notes == ["Duration not stated"]


def test_the_pdf_says_duration_not_stated() -> None:
    import pymupdf

    from rfp_intake.render.pdf_renderer import build_report_pdf

    state = RunState(run_id="t", records=[_record(FREQUENCY, "once every 28 days", P54, 54)])
    state = state.model_copy(update=reconcile_node(state))
    state = state.model_copy(update=gate_node(state))
    pdf = build_report_pdf(state, get_registry(), generated_at="2026-10-16")
    doc = pymupdf.open(stream=pdf, filetype="pdf")  # type: ignore[no-untyped-call]
    text = " ".join(
        " ".join(doc[i].get_text().split())  # type: ignore[no-untyped-call]
        for i in range(doc.page_count)
    )
    # 29 October 2026: the value reads "Not found in the documents" (rule 3),
    # the status Review, and the note is in grey in the Where column.
    assert ("How long patients are dosed BUDGET Not found in the documents Review "
            "Duration not stated") in text
