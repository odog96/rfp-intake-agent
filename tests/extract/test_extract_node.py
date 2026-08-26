"""Tests for the EXTRACT graph node with mock LLM."""

from __future__ import annotations

import os

from rfp_intake.domain.schemas import Document, ExtractionTask, RunState


def _make_doc_with_matching_quote() -> Document:
    """Create a document where the mock's fixture quote is present in page text."""
    return Document(
        id="doc-001",
        path="/tmp/test.pdf",
        kind="protocol",
        pages=10,
        page_texts={
            3: "Introduction to the study design.",
            4: "The study is a Phase III randomised double-blind trial.",
            5: "A total of 260 subjects will be enrolled across 75 sites in 6 countries.",
            6: "Treatment period is 52 weeks.",
            7: "The primary endpoint is assessed at Week 24.",
        },
    )


class TestExtractNode:
    def test_produces_records(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Extract node produces FieldRecords from mock LLM."""
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        os.environ["RFP_INTAKE_LLM_BACKEND"] = "mock"

        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        from rfp_intake.extract import extract_node

        doc = _make_doc_with_matching_quote()
        task = ExtractionTask(
            doc_id="doc-001",
            group="operational_metrics",
            page_window=(3, 7),
        )

        state = RunState(
            run_id="test-run",
            documents=[doc],
            tasks=[task],
        )

        result = extract_node(state)
        assert "records" in result
        assert "errors" in result
        # Mock returns at least one record for the "total_count" field
        assert len(result["records"]) >= 1

    def test_missing_doc_produces_error(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Missing document produces an error, not a crash."""
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        os.environ["RFP_INTAKE_LLM_BACKEND"] = "mock"

        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        from rfp_intake.extract import extract_node

        task = ExtractionTask(
            doc_id="nonexistent-doc",
            group="operational_metrics",
            page_window=(1, 5),
        )

        state = RunState(
            run_id="test-run",
            documents=[],
            tasks=[task],
        )

        result = extract_node(state)
        assert len(result["errors"]) == 1
        assert "not found" in result["errors"][0].error

    def test_extract_group_directly(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Test extract_group function directly."""
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        os.environ["RFP_INTAKE_LLM_BACKEND"] = "mock"

        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        from rfp_intake.extract import extract_group

        doc = _make_doc_with_matching_quote()
        task = ExtractionTask(
            doc_id="doc-001",
            group="operational_metrics",
            page_window=(3, 7),
        )
        registry = get_registry()

        records, errors = extract_group(task, doc, registry)
        # Mock returns data, records should be populated
        assert isinstance(records, list)
        assert isinstance(errors, list)
