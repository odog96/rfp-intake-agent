"""Tests for the golden set loader."""

import json
import tempfile
from pathlib import Path

import yaml

from rfp_intake.eval.golden import (
    GoldenContradiction,
    GoldenDocument,
    GoldenField,
    load_golden_contradictions,
    load_golden_set,
)


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


def test_load_golden_contradictions_from_yaml():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "contradictions.yaml"
        data = {
            "contradictions": [
                {
                    "field_id": "timeline.total_duration",
                    "description": "RFP says 40 months, protocol implies 42.",
                    "documents": ["rfp1", "protocol1"],
                    "expected_verdict": "reconcilable",
                    "severity": "high",
                },
            ]
        }
        path.write_text(yaml.dump(data))

        result = load_golden_contradictions(path)
        assert len(result) == 1
        assert result[0].field_id == "timeline.total_duration"
        assert result[0].expected_verdict == "reconcilable"
        assert result[0].severity == "high"


def test_load_golden_contradictions_missing_file():
    result = load_golden_contradictions(Path("/nonexistent/contradictions.yaml"))
    assert result == []


def test_golden_contradiction_defaults():
    c = GoldenContradiction(field_id="x", expected_verdict="conflict")
    assert c.description == ""
    assert c.documents == []
    assert c.severity is None


def test_real_contradictions_golden_set_loads():
    """Sanity check against the actual eval/golden/contradictions.yaml in the repo."""
    path = Path(__file__).parent.parent.parent / "eval" / "golden" / "contradictions.yaml"
    result = load_golden_contradictions(path)
    assert len(result) == 3
    assert {c.field_id for c in result} == {
        "timeline.total_duration", "ops.sites_total", "timeline.periods",
    }
