"""READ_SCHEDULE as a graph node, extraction.json's `schedules`, and the re-render
of a run made before the node existed (Prompt C stage 2). Offline."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path
from types import ModuleType

import pytest

from rfp_intake.domain.registry import get_registry
from rfp_intake.domain.schemas import Document, RunState, Schedule, ScheduleColumn, Section
from rfp_intake.render.json_renderer import build_extraction_document
from rfp_intake.schedule import read_schedule_node

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = "Example protocol 2.pdf"
ASSESSMENTS = [26, 16, 13, 7, 23, 6, 28, 7, 30, 22, 31]
# The Prompt B baseline: made before READ_SCHEDULE, so its extraction.json has
# no `schedules` and a re-render reads them from its inputs.
BASELINE = ROOT / "runs" / "r-20261016-prompt-b"


@pytest.fixture(autouse=True)
def _real_registry(fields_yaml_path: Path) -> None:
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    get_registry.cache_clear()


@pytest.fixture(scope="module")
def rerender() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "rerender_report", ROOT / "scripts" / "rerender_report.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.slow
def test_the_node_reads_the_protocols_schedules(samples_dir: Path) -> None:
    from tests.plan.test_plan_sections_samples import _document

    pdf = samples_dir / PROTOCOL
    if not pdf.exists():
        pytest.skip(f"{PROTOCOL} not available")
    doc = _document(pdf, "doc-protocol", "protocol")
    update = read_schedule_node(RunState(run_id="r-test", documents=[doc]))

    assert update["errors"] == []
    [table_1] = [s for s in update["schedules"] if s.counted]
    assert [c.marks_assessments for c in table_1.columns] == ASSESSMENTS
    assert [s.counted for s in update["schedules"]] == [True, False]


def test_a_document_that_cannot_be_read_is_an_error_not_a_crash(tmp_path: Path) -> None:
    doc = Document(
        id="doc-x",
        path=str(tmp_path / "missing.pdf"),
        pages=1,
        page_texts={1: "Schedule of Events"},
        sections=[Section(id="doc-x:s001", heading="Schedule of Events",
                          page_start=1, page_end=1)],
    )
    update = read_schedule_node(RunState(run_id="r-test", documents=[doc]))

    assert update["schedules"] == []
    [error] = update["errors"]
    assert (error.node, error.task_id, error.kind) == ("READ_SCHEDULE", "doc-x", "validation")


def test_a_document_with_no_schedule_heading_gives_none(tmp_path: Path) -> None:
    doc = Document(
        id="doc-y", path=str(tmp_path / "never-opened.pdf"), pages=1,
        sections=[Section(id="doc-y:s001", heading="Introduction", page_start=1, page_end=1)],
    )
    assert read_schedule_node(RunState(documents=[doc])) == {"schedules": [], "errors": []}


def _schedule() -> Schedule:
    return Schedule(
        doc_id="doc-p", heading="Table 1: Schedule of Events", pages=[26, 27],
        printed_pages=["24", "25"],
        columns=[ScheduleColumn(period="Month 1", timing="Day 1", marks_all=17,
                                marks_assessments=13, pages=[26, 27])],
        rows=[], unplaced=[],
    )


class TestExtractionJson:
    def test_schedules_are_recorded(self) -> None:
        state = RunState(run_id="r-1", schedules=[_schedule()])
        doc = build_extraction_document(state, get_registry())
        assert [Schedule.model_validate(s) for s in doc["schedules"]] == [_schedule()]

    def test_schedules_is_empty_not_absent_when_none_was_found(self) -> None:
        """An empty list says READ_SCHEDULE ran and found nothing; a missing key
        is a run from before the node, which the re-render reads again."""
        doc = build_extraction_document(RunState(run_id="r-1"), get_registry())
        assert doc["schedules"] == []


class TestRerender:
    def test_a_saved_schedule_is_loaded_not_read_again(
        self, rerender: ModuleType, tmp_path: Path
    ) -> None:
        doc = build_extraction_document(
            RunState(run_id="r-1", schedules=[_schedule()]), get_registry())
        (tmp_path / "extraction.json").write_text(json.dumps(doc))
        state, _ = rerender.load_run_state(tmp_path)
        assert state.schedules == [_schedule()]

    @pytest.mark.slow
    @pytest.mark.skipif(not (BASELINE / "inputs" / PROTOCOL).is_file(),
                        reason="run r-20261016-prompt-b is not on this machine")
    def test_a_run_from_before_the_node_is_read_from_its_inputs(
        self, rerender: ModuleType, tmp_path: Path
    ) -> None:
        # A copy, so the test never writes into a real run folder.
        shutil.copy(BASELINE / "extraction.json", tmp_path / "extraction.json")
        shutil.copytree(BASELINE / "inputs", tmp_path / "inputs")
        state, _ = rerender.load_run_state(tmp_path)

        [table_1] = [s for s in state.schedules if s.counted]
        assert [c.marks_assessments for c in table_1.columns] == ASSESSMENTS
        # The run's own id for the protocol, so the report names it as the run did.
        protocol_ids = {p.doc_id for rf in state.resolved for p in rf.sources
                        if p.doc_kind in ("protocol", "amendment")}
        assert table_1.doc_id in protocol_ids
