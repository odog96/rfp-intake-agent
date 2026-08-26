"""Tests for eval scoring logic."""

import pytest

from rfp_intake.domain.schemas import Provenance, ResolvedField
from rfp_intake.eval.golden import GoldenField
from rfp_intake.eval.scoring import DocumentScore, FieldScore, score_field


def _make_resolved(value, status="confirmed", page=3):
    return ResolvedField(
        field_id="test",
        value=value,
        status=status,
        confidence=0.9,
        sources=[Provenance(doc_id="d1", doc_kind="rfp", page=page)],
    )


def test_score_field_correct():
    golden = GoldenField(expected_value=75, expected_status="found", expected_page=3)
    resolved = _make_resolved(75, page=3)
    score = score_field("ops.sites_total", golden, resolved)
    assert score.value_correct is True
    assert score.status_correct is True
    assert score.citation_correct is True
    assert score.was_extracted is True


def test_score_field_wrong_value():
    golden = GoldenField(expected_value=75, expected_status="found", expected_page=3)
    resolved = _make_resolved(80, page=3)
    score = score_field("ops.sites_total", golden, resolved)
    assert score.value_correct is False
    assert score.citation_correct is True


def test_score_field_wrong_page():
    golden = GoldenField(expected_value=75, expected_status="found", expected_page=3)
    resolved = _make_resolved(75, page=7)
    score = score_field("ops.sites_total", golden, resolved)
    assert score.value_correct is True
    assert score.citation_correct is False


def test_score_field_not_found():
    golden = GoldenField(expected_value=75, expected_status="found")
    score = score_field("ops.sites_total", golden, None)
    assert score.was_extracted is False
    assert score.status_correct is False


def test_score_field_correctly_not_found():
    golden = GoldenField(expected_status="not_found")
    score = score_field("ops.crf_pages", golden, None)
    assert score.status_correct is True
    assert score.value_correct is False


def test_score_field_not_specified_match():
    golden = GoldenField(expected_status="not_specified")
    resolved = ResolvedField(
        field_id="test", value=None, status="not_specified", confidence=0.9
    )
    score = score_field("f1", golden, resolved)
    assert score.status_correct is True
    assert score.value_correct is True


def test_score_field_numeric_tolerance():
    golden = GoldenField(expected_value=100, expected_status="found")
    resolved = _make_resolved(100.5)
    score = score_field("f1", golden, resolved)
    assert score.value_correct is True


def test_score_field_string_case_insensitive():
    golden = GoldenField(expected_value="Phase 1", expected_status="found")
    resolved = _make_resolved("phase 1")
    score = score_field("f1", golden, resolved)
    assert score.value_correct is True


def test_document_score_precision():
    scores = [
        FieldScore(field_id="f1", value_correct=True, was_extracted=True, expected_status="found"),
        FieldScore(field_id="f2", value_correct=False, was_extracted=True, expected_status="found"),
        FieldScore(field_id="f3", value_correct=True, was_extracted=True, expected_status="found"),
    ]
    ds = DocumentScore(document_id="d1", field_scores=scores)
    assert ds.precision == pytest.approx(2 / 3)


def test_document_score_recall():
    scores = [
        FieldScore(field_id="f1", was_extracted=True, expected_status="found"),
        FieldScore(field_id="f2", was_extracted=False, expected_status="found"),
        FieldScore(field_id="f3", was_extracted=True, expected_status="not_specified"),
    ]
    ds = DocumentScore(document_id="d1", field_scores=scores)
    # Only f1, f2 are expected "found"; 1 of 2 extracted
    assert ds.recall == pytest.approx(0.5)


def test_document_score_confusion_matrix():
    scores = [
        FieldScore(
            field_id="f1", expected_status="found",
            actual_status="found", status_correct=True,
        ),
        FieldScore(
            field_id="f2", expected_status="found",
            actual_status="not_found", status_correct=False,
        ),
        FieldScore(
            field_id="f3", expected_status="not_specified",
            actual_status="not_specified", status_correct=True,
        ),
    ]
    ds = DocumentScore(document_id="d1", field_scores=scores)
    cm = ds.confusion_matrix()
    assert cm["found"]["found"] == 1
    assert cm["found"]["not_found"] == 1
    assert cm["not_specified"]["not_specified"] == 1
