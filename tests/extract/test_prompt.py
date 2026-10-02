"""Tests for extraction prompt builder."""

from __future__ import annotations

from rfp_intake.domain.schemas import (
    Document,
    ExtractionTask,
    OutlineEntry,
    Section,
    TableData,
)
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


def _sectioned_doc() -> Document:
    """One page holding the end of one section and the start of the next.

    The shape of page 39 of samples/Example protocol 2.pdf, which is why
    section boundaries carry a character offset.
    """
    page_2 = (
        "Nonclinical toxicology in the mouse, which is warranted. \n"
        "1.3.2 \nClinical Experience \nAn ongoing, open-label study of NEOD001. \n"
    )
    boundary = page_2.index("1.3.2")
    doc = Document(
        id="doc-001",
        path="/tmp/test.pdf",
        kind="protocol",
        pages=3,
        page_texts={
            1: "1.3.1 \nNonclinical Safety \nMouse data follows. \n",
            2: page_2,
            3: "1.4 \nRationale for Dose Selection \nThe dose is 24 mg/kg. \n",
        },
        tables=[
            TableData(page=2, headers=["Species", "Dose"], rows=[["Mouse", "100"]]),
            TableData(page=3, headers=["Cohort", "Dose"], rows=[["1", "24"]]),
        ],
    )
    doc.sections = [
        Section(
            id="doc-001:s001",
            heading="1.3.1 Nonclinical Safety",
            level=3,
            page_start=1,
            page_end=2,
            start_offset=0,
            end_offset=boundary,
        ),
        Section(
            id="doc-001:s002",
            heading="1.3.2 Clinical Experience",
            level=3,
            page_start=2,
            page_end=2,
            start_offset=boundary,
            end_offset=None,
        ),
        Section(
            id="doc-001:s003",
            heading="1.4 Rationale for Dose Selection",
            level=2,
            page_start=3,
            page_end=3,
        ),
    ]
    doc.section_source = "bookmarks"
    return doc


class TestExcerptFromSections:
    """Stage 2 of docs/PLAN_2026-10-02.md: the excerpt is the chosen sections."""

    def test_only_the_chosen_sections_text_is_included(self) -> None:
        doc = _sectioned_doc()
        task = ExtractionTask(
            doc_id="doc-001",
            group="study_design",
            page_window=(1, 2),
            section_ids=["doc-001:s001"],
        )

        excerpt = build_excerpt(task, doc)

        assert "Mouse data follows" in excerpt
        assert "is warranted" in excerpt
        # The other half of page 2 is the section that names a different study.
        assert "ongoing, open-label" not in excerpt
        assert "Rationale for Dose Selection" not in excerpt

    def test_the_page_marker_still_names_the_page(self) -> None:
        doc = _sectioned_doc()
        task = ExtractionTask(
            doc_id="doc-001",
            group="study_design",
            page_window=(2, 2),
            section_ids=["doc-001:s002"],
        )

        excerpt = build_excerpt(task, doc)

        assert "--- Page 2 ---" in excerpt
        assert "ongoing, open-label" in excerpt
        assert "Mouse data follows" not in excerpt

    def test_two_chosen_sections_are_not_joined_into_one_block(self) -> None:
        """A quote must not be able to span the gap between two chosen sections."""
        doc = _sectioned_doc()
        task = ExtractionTask(
            doc_id="doc-001",
            group="study_design",
            page_window=(1, 3),
            section_ids=["doc-001:s001", "doc-001:s003"],
        )

        excerpt = build_excerpt(task, doc)

        assert "is warranted" in excerpt
        assert "24 mg/kg" in excerpt
        assert "ongoing, open-label" not in excerpt
        # The two blocks are separated by a page marker, so no sentence crosses
        # from one section straight into the other.
        assert excerpt.index("is warranted") < excerpt.index("--- Page 3 ---")
        assert excerpt.index("--- Page 3 ---") < excerpt.index("24 mg/kg")

    def test_a_table_is_included_when_its_page_is_in_a_chosen_section(self) -> None:
        doc = _sectioned_doc()
        task = ExtractionTask(
            doc_id="doc-001",
            group="visits",
            page_window=(3, 3),
            section_ids=["doc-001:s003"],
        )

        excerpt = build_excerpt(task, doc)

        assert "Table (page 3)" in excerpt
        assert "Table (page 2)" not in excerpt

    def test_a_task_with_no_section_ids_still_reads_whole_pages(self) -> None:
        """What a task built by hand, without sections, means."""
        doc = _sectioned_doc()
        task = ExtractionTask(doc_id="doc-001", group="study_design", page_window=(1, 2))

        excerpt = build_excerpt(task, doc)

        assert "Mouse data follows" in excerpt
        assert "ongoing, open-label" in excerpt

    def test_a_task_naming_an_unknown_section_yields_an_empty_excerpt(self) -> None:
        """Never a silent fall back to whole pages: that is what stage 2 removed."""
        doc = _sectioned_doc()
        task = ExtractionTask(
            doc_id="doc-001",
            group="study_design",
            page_window=(1, 3),
            section_ids=["doc-001:s404"],
        )

        assert build_excerpt(task, doc) == ""

    def test_the_prompt_names_the_chosen_sections(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        import os
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()
        registry = get_registry()

        doc = _sectioned_doc()
        task = ExtractionTask(
            doc_id="doc-001",
            group="study_design",
            page_window=(1, 2),
            section_ids=["doc-001:s001"],
        )

        human = build_extract_prompt(task, doc, registry)[1].content

        assert "SECTIONS: 1.3.1 Nonclinical Safety" in human
        assert build_excerpt(task, doc) in human
