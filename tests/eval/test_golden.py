"""Tests for the golden set loader."""

import json
import tempfile
from pathlib import Path

from rfp_intake.eval.golden import GoldenDocument, GoldenField, load_golden_set


def test_load_golden_set_from_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        golden_dir = Path(tmpdir)
        data = {
            "document_id": "test-doc-1",
            "document_kind": "rfp",
            "fields": {
                "ops.sites_total": {
                    "expected_value": 75,
                    "expected_status": "found",
                    "expected_page": 3,
                },
                "ops.crf_pages": {
                    "expected_status": "not_specified",
                },
            },
        }
        (golden_dir / "test_doc_1.json").write_text(json.dumps(data))

        result = load_golden_set(golden_dir)
        assert "test-doc-1" in result
        doc = result["test-doc-1"]
        assert doc.document_kind == "rfp"
        assert len(doc.fields) == 2
        assert doc.fields["ops.sites_total"].expected_value == 75
        assert doc.fields["ops.crf_pages"].expected_status == "not_specified"


def test_load_golden_set_empty_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        result = load_golden_set(Path(tmpdir))
        assert result == {}


def test_load_golden_set_nonexistent_dir():
    result = load_golden_set(Path("/nonexistent/path"))
    assert result == {}


def test_golden_field_defaults():
    f = GoldenField()
    assert f.expected_value is None
    assert f.expected_status == "found"
    assert f.expected_page is None


def test_golden_document_model():
    doc = GoldenDocument(
        document_id="d1",
        document_kind="protocol",
        fields={"study.phase": GoldenField(expected_value="phase_1")},
    )
    assert doc.document_id == "d1"
    assert doc.fields["study.phase"].expected_value == "phase_1"
