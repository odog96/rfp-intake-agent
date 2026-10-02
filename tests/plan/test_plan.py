"""Tests for plan generation end-to-end, on a hand-built document.

Updated for stage 2 of docs/PLAN_2026-10-02.md: PLAN chooses sections, not pages
widened by a one-page margin, and there is no first-5-pages fallback. The document
below keeps its `outline`, because FIND_SECTIONS turns an outline into sections, so
these tests still exercise the bookmarks path end to end.

Every document here is passed through the real `find_sections` first. PLAN reads
`Document.sections` and raises `MissingSectionsError` without them — FIND_SECTIONS
is the only producer of sections, and `test_plan_requires_find_sections_to_have_run`
is what holds that line.
"""

from __future__ import annotations

import os

import pytest

from rfp_intake.domain.schemas import Document, OutlineEntry, RunState
from rfp_intake.sections import find_sections


def _sectioned(doc: Document) -> Document:
    """The document as FIND_SECTIONS leaves it. PLAN requires sections."""
    doc.sections, doc.section_source = find_sections(doc)
    return doc


def _make_doc_with_outline() -> Document:
    return _sectioned(Document(
        id="doc-001",
        path="/tmp/test.pdf",
        kind="protocol",
        pages=20,
        page_texts={
            1: "Protocol Title Page",
            2: "Synopsis: Phase III randomised double-blind study of DrugX",
            3: "Study Design section. This is a randomised parallel group trial.",
            4: "The study is double-blind with 1:1 allocation.",
            5: "Study Population: Adults 18-75 with moderate disease.",
            6: "Interim Analysis section. Two interim analyses are planned.",
            7: "The first interim analysis occurs at 50% enrollment.",
            8: "Treatment: DrugX 200mg oral tablet QD.",
            9: "Schedule of Assessments follows.",
            10: "Visit 1 screening, Visit 2 baseline, Visit 3 Week 4.",
            11: "Study Duration: approximately 40 months total.",
            12: "Blinding: Double-blind design with unblinded pharmacist.",
            13: "Number of Sites: 75 sites across 6 countries.",
            14: "Monitoring: Every 8 weeks on-site monitoring.",
        },
        outline=[
            OutlineEntry(heading="Synopsis", page_start=2, page_end=2, level=1),
            OutlineEntry(heading="Study Design", page_start=3, page_end=4, level=1),
            OutlineEntry(heading="Study Population", page_start=5, page_end=5, level=1),
            OutlineEntry(heading="Interim Analysis", page_start=6, page_end=7, level=1),
            OutlineEntry(heading="Treatment", page_start=8, page_end=8, level=1),
            OutlineEntry(heading="Schedule of Assessments", page_start=9, page_end=10, level=1),
            OutlineEntry(heading="Study Duration", page_start=11, page_end=11, level=1),
            OutlineEntry(heading="Blinding", page_start=12, page_end=12, level=1),
            OutlineEntry(heading="Number of Sites", page_start=13, page_end=13, level=1),
            OutlineEntry(heading="Monitoring", page_start=14, page_end=14, level=1),
        ],
    ))


class TestPlanExtraction:
    def test_generates_tasks_for_all_groups(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        from rfp_intake.plan import plan_extraction
        registry = get_registry()

        doc = _make_doc_with_outline()
        tasks = plan_extraction([doc], registry)

        # Should generate at least one task per group (9 groups)
        groups_covered = {t.group for t in tasks}
        assert len(groups_covered) == 9

    def test_all_tasks_reference_valid_doc(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        from rfp_intake.plan import plan_extraction
        registry = get_registry()

        doc = _make_doc_with_outline()
        tasks = plan_extraction([doc], registry)

        for task in tasks:
            assert task.doc_id == "doc-001"

    def test_page_windows_within_doc_bounds(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        from rfp_intake.plan import plan_extraction
        registry = get_registry()

        doc = _make_doc_with_outline()
        tasks = plan_extraction([doc], registry)

        for task in tasks:
            assert task.page_window[0] >= 1
            # No margin any more: a window cannot reach past the last page.
            assert task.page_window[1] <= doc.pages
            assert task.section_ids, "every task names the sections it reads"

    def test_a_document_without_an_outline_is_read_whole(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Replaces test_fallback_without_outline, which asserted the first 5 pages.

        A document with no bookmarks gets one whole-document section from
        FIND_SECTIONS when its text fits one extraction call, and PLAN sends that
        one section to every group. This is the shape of
        samples/Synthetic_RFP_NEOD001.pdf, whose page 6 the old fallback never read.
        """
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        from rfp_intake.plan import plan_extraction
        registry = get_registry()

        doc = _sectioned(Document(
            id="doc-002",
            path="/tmp/no-outline.pdf",
            kind="rfp",
            pages=30,
            page_texts={i: f"Page {i} content" for i in range(1, 31)},
            outline=[],
        ))

        tasks = plan_extraction([doc], registry)
        assert len(tasks) >= 9  # at least one per group

        for task in tasks:
            assert task.page_window == (1, 30), "every page, including the last"
            assert len(task.section_ids) == 1

    def test_tasks_fit_the_token_budget(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """A section too big for one call becomes several tasks naming that section."""
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        from rfp_intake.domain.budget import DEFAULT_TOKEN_BUDGET
        from rfp_intake.plan import plan_extraction
        registry = get_registry()

        doc = _make_doc_with_outline()
        # Make one chosen section far too long for a single extraction call, then
        # re-section, so the section offsets match the text they were cut from.
        doc.page_texts[3] = "Study Design \n" + "randomised parallel group trial. " * 900
        doc.page_texts[4] = "double-blind 1:1 allocation. " * 900
        _sectioned(doc)

        tasks = plan_extraction([doc], registry)
        design = [t for t in tasks if t.group == "study_design"]

        assert len(design) > 1, "the oversized section was split"
        for task in design:
            pages_in_task = task.page_window[1] - task.page_window[0] + 1
            # A page is the smallest unit PLAN can split by, so a task is either
            # inside the budget or a single page that is over it on its own.
            assert (task.budget_tokens or 0) <= DEFAULT_TOKEN_BUDGET or pages_in_task == 1
        # The split tasks still name the section they came from.
        assert all(task.section_ids for task in design)


class TestPlanRequiresSections:
    """FIND_SECTIONS is the only producer of sections; PLAN refuses to improvise."""

    def test_plan_extraction_raises_when_a_document_has_no_sections(
        self, fields_yaml_path
    ) -> None:  # type: ignore[no-untyped-def]
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        from rfp_intake.plan import MissingSectionsError, plan_extraction
        registry = get_registry()

        # The same document, but FIND_SECTIONS never ran on it.
        doc = Document(
            id="doc-003",
            path="/tmp/unsectioned.pdf",
            kind="protocol",
            pages=2,
            page_texts={1: "Synopsis: a Phase III study.", 2: "75 sites in 6 countries."},
        )
        assert doc.sections == []

        with pytest.raises(MissingSectionsError) as excinfo:
            plan_extraction([doc], registry)

        # The message has to name the document and the node that did not run,
        # because job/__init__.py puts it straight into status.json.
        assert "doc-003" in str(excinfo.value)
        assert "FIND_SECTIONS" in str(excinfo.value)

    def test_plan_does_not_write_to_the_document(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """PLAN reads Document.sections and changes nothing on the document."""
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        from rfp_intake.plan import plan_extraction
        registry = get_registry()

        doc = _make_doc_with_outline()
        before = doc.model_dump()

        plan_extraction([doc], registry)

        assert doc.model_dump() == before

    def test_plan_node_returns_tasks_only(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """No "documents" key: PLAN has nothing to write back."""
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        from rfp_intake.plan import plan_node

        state = RunState(run_id="test-run", documents=[_make_doc_with_outline()])
        assert set(plan_node(state)) == {"tasks"}


class TestPlanNode:
    def test_plan_node(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        from rfp_intake.plan import plan_node

        doc = _make_doc_with_outline()
        state = RunState(run_id="test-run", documents=[doc])

        result = plan_node(state)
        assert "tasks" in result
        assert len(result["tasks"]) >= 9
