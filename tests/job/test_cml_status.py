"""Table-driven tests for CML job-run status classification."""

from __future__ import annotations

import pytest

from rfp_intake.job.cml_status import classify_cml_status, is_terminal


class TestClassifyCmlStatus:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # The value actually observed on a failed run of the CML Job named
            # "RFP Pipeline Executor" on 2026-08-27. The application was matching
            # against "failed" and never saw it.
            ("ENGINE_FAILED", "failed"),
            ("ENGINE_TIMEDOUT", "failed"),
            ("ENGINE_KILLED", "failed"),
            ("ENGINE_STOPPED", "failed"),
            ("ENGINE_SUCCEEDED", "succeeded"),
            ("ENGINE_RUNNING", "running"),
            ("ENGINE_SCHEDULING", "pending"),
            # Without the prefix, and in other cases.
            ("failed", "failed"),
            ("Failed", "failed"),
            ("  SUCCEEDED  ", "succeeded"),
            ("running", "running"),
            # Unrecognised must not be mistaken for healthy.
            ("ENGINE_SOMETHING_NEW", "unknown"),
            ("", "unknown"),
            (None, "unknown"),
        ],
    )
    def test_classification(self, raw: str | None, expected: str) -> None:
        assert classify_cml_status(raw) == expected

    def test_unknown_is_never_treated_as_running(self) -> None:
        # The bug being guarded: an unclassifiable status left the application
        # showing "Waiting for pipeline to start..." forever, which reads as
        # patience rather than as a problem.
        assert classify_cml_status("ENGINE_WHATEVER") != "running"
        assert classify_cml_status(None) != "running"


class TestIsTerminal:
    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ("succeeded", True),
            ("failed", True),
            ("running", False),
            ("pending", False),
            # Unknown is not terminal: the application must keep looking rather
            # than declare an outcome it cannot support.
            ("unknown", False),
        ],
    )
    def test_terminal_states(self, state: str, expected: bool) -> None:
        assert is_terminal(state) is expected  # type: ignore[arg-type]
