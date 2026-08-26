"""Tests for plan generation end-to-end."""

from __future__ import annotations

import os

from rfp_intake.domain.schemas import Document, OutlineEntry, RunState


def _make_doc_with_outline() -> Document:
    return Document(
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
    )


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
            assert task.page_window[1] <= doc.pages + 1  # margin can add 1

    def test_fallback_without_outline(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Documents without outline fall back to first N pages."""
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        from rfp_intake.plan import plan_extraction
        registry = get_registry()

        doc = Document(
            id="doc-002",
            path="/tmp/no-outline.pdf",
            kind="rfp",
            pages=30,
            page_texts={i: f"Page {i} content" for i in range(1, 31)},
            outline=[],
        )

        tasks = plan_extraction([doc], registry)
        assert len(tasks) >= 9  # at least one per group
        # All should use fallback window
        for task in tasks:
            assert task.page_window[0] == 1


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
