"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from rfp_intake.config.settings import reset_settings


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure all tests use the mock LLM backend."""
    monkeypatch.setenv("RFP_INTAKE_LLM_BACKEND", "mock")
    reset_settings()


@pytest.fixture
def fields_yaml_path() -> Path:
    """Path to the real fields.yaml for registry tests."""
    return Path(__file__).parent.parent / "config" / "fields.yaml"


@pytest.fixture
def samples_dir() -> Path:
    """Path to the samples directory with test PDFs."""
    return Path(__file__).parent.parent / "samples"


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a run directory root and point settings at it.

    Returns the runs/ root (tmp_path/runs). Callers create run_id subdirs under it.
    """
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("RFP_INTAKE_RUN_DIR", str(runs))
    reset_settings()
    return runs
