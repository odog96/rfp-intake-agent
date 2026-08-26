"""Tests for extraction prompt builder."""

from __future__ import annotations

from rfp_intake.domain.schemas import Document, ExtractionTask, OutlineEntry, TableData
from rfp_intake.extract.prompt import build_excerpt, build_extract_prompt, build_repair_prompt


def _make_doc() -> Document:
    return Document(
        id="doc-001",
        path="/tmp/test.pdf",
        kind="protocol",
        pages=10,
        page_texts={
            1: "This is page 1 with some text.",
            2: "Page 2 discusses study design and Phase III trial.",
            3: "Page 3 has the schedule of assessments.",
            4: "Page 4 continues with visit frequency details.",
            5: "Page 5 describes the treatment dosing regimen.",
        },
        outline=[
            OutlineEntry(heading="Synopsis", page_start=1, page_end=2, level=1),
            OutlineEntry(heading="Study Design", page_start=2, page_end=3, level=1),
        ],
        tables=[
            TableData(
                page=3,
                headers=["Visit", "Week", "Assessments"],
                rows=[["V1", "0", "Screening"], ["V2", "4", "Treatment"]],
            ),
        ],
    )


def _make_task() -> ExtractionTask:
    return ExtractionTask(
        doc_id="doc-001",
        group="study_design",
        page_window=(2, 4),
    )


class TestBuildExtractPrompt:
    def test_returns_two_messages(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        import os
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        doc = _make_doc()
        task = _make_task()
        from rfp_intake.domain.registry import get_registry
        registry = get_registry()

        messages = build_extract_prompt(task, doc, registry)
        assert len(messages) == 2
        assert messages[0].type == "system"
        assert messages[1].type == "human"

    def test_system_message_contains_rules(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        import os
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        doc = _make_doc()
        task = _make_task()
        registry = get_registry()

        messages = build_extract_prompt(task, doc, registry)
        system_content = messages[0].content
        assert "RULES" in system_content
        assert "quote" in system_content
        assert "FIELDS" in system_content

    def test_human_message_contains_excerpt(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        import os
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        doc = _make_doc()
        task = _make_task()
        registry = get_registry()

        messages = build_extract_prompt(task, doc, registry)
        human_content = messages[1].content
        assert "Page 2" in human_content
        assert "Page 3" in human_content
        assert "Page 4" in human_content
        assert "<excerpt>" in human_content

    def test_includes_tables_in_range(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        import os
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        doc = _make_doc()
        task = _make_task()
        registry = get_registry()

        messages = build_extract_prompt(task, doc, registry)
        human_content = messages[1].content
        assert "Table (page 3)" in human_content
        assert "Screening" in human_content


class TestBuildExcerpt:
    def test_pages_in_window(self) -> None:
        doc = _make_doc()
        task = _make_task()
        excerpt = build_excerpt(task, doc)
        assert "Page 2" in excerpt
        assert "Page 3" in excerpt
        assert "Page 4" in excerpt
        assert "Page 1" not in excerpt
        assert "Page 5" not in excerpt

    def test_includes_table(self) -> None:
        doc = _make_doc()
        task = _make_task()
        excerpt = build_excerpt(task, doc)
        assert "V1" in excerpt
        assert "Screening" in excerpt


class TestBuildRepairPrompt:
    def test_appends_repair_message(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        import os
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        doc = _make_doc()
        task = _make_task()
        registry = get_registry()

        messages = build_extract_prompt(task, doc, registry)
        violations = ["field_x: quote_not_found_in_excerpt"]
        repair = build_repair_prompt(messages, violations)

        assert len(repair) == 3
        assert "VALIDATION FAILURE" in repair[2].content
        assert "quote_not_found_in_excerpt" in repair[2].content
