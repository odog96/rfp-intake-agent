"""Tests for the quality gate logic."""

from rfp_intake.ingest.models import QualityMetrics
from rfp_intake.ingest.parsers.quality import QualityGate


def test_quality_gate_passes():
    metrics = QualityMetrics(
        total_pages=10,
        pages_with_text=10,
        avg_chars_per_page=1200.0,
        alpha_ratio=0.75,
        table_count=3,
        has_outline=True,
        suspected_scanned=False,
    )
    gate = QualityGate()
    passed, reason = gate.check(metrics)
    assert passed is True
    assert reason is None


def test_quality_gate_fails_no_pages():
    metrics = QualityMetrics(total_pages=0)
    gate = QualityGate()
    passed, reason = gate.check(metrics)
    assert passed is False
    assert reason == "no_pages"


def test_quality_gate_fails_suspected_scanned():
    metrics = QualityMetrics(
        total_pages=10,
        pages_with_text=2,
        avg_chars_per_page=100.0,
        alpha_ratio=0.8,
        suspected_scanned=True,
    )
    gate = QualityGate()
    passed, reason = gate.check(metrics)
    assert passed is False
    assert reason == "suspected_scanned"


def test_quality_gate_fails_low_chars():
    metrics = QualityMetrics(
        total_pages=10,
        pages_with_text=10,
        avg_chars_per_page=200.0,
        alpha_ratio=0.75,
        suspected_scanned=False,
    )
    gate = QualityGate()
    passed, reason = gate.check(metrics)
    assert passed is False
    assert "low_chars_per_page" in reason


def test_quality_gate_fails_low_alpha():
    metrics = QualityMetrics(
        total_pages=10,
        pages_with_text=10,
        avg_chars_per_page=1200.0,
        alpha_ratio=0.30,
        suspected_scanned=False,
    )
    gate = QualityGate()
    passed, reason = gate.check(metrics)
    assert passed is False
    assert "low_alpha_ratio" in reason


def test_quality_gate_custom_thresholds():
    metrics = QualityMetrics(
        total_pages=5,
        pages_with_text=5,
        avg_chars_per_page=300.0,
        alpha_ratio=0.55,
        suspected_scanned=False,
    )
    gate = QualityGate(min_chars_per_page=200, min_alpha_ratio=0.5)
    passed, reason = gate.check(metrics)
    assert passed is True
