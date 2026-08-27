"""Classify the status string the CML Jobs API returns for a job run.

ARCHITECTURE.md §6.3 says a run's state has two sources and both must be used:
`status.json` is authoritative for how far the work got, and the CML Jobs API is
authoritative for whether the process is still alive. This module covers the
second one.

It exists because the Cloudera AI Application was matching the API's status
against the literal strings "failed", "timedout" and "killed", while the API
actually returns `ENGINE_FAILED`, `ENGINE_TIMEDOUT` and so on. Nothing matched,
so a job run that had already failed left the application showing "Waiting for
pipeline to start..." indefinitely — the failure mode that is worse than an
error, because it looks like patience.

Matching is deliberately permissive about the `ENGINE_` prefix and about case,
and anything unrecognised is reported as unknown rather than quietly treated as
"still running". A status this module cannot classify is a status the operator
needs to see.
"""

from __future__ import annotations

from typing import Literal

CmlRunState = Literal["pending", "running", "succeeded", "failed", "unknown"]

# Substrings, checked against the status with any ENGINE_ prefix removed. The
# API has added values over time (ENGINE_STOPPING, ENGINE_TIMEDOUT), so matching
# on a substring rather than an exact set means a new spelling of an existing
# concept still lands in the right bucket.
_FAILED = ("failed", "timedout", "timed_out", "killed", "stopped", "aborted", "error")
_SUCCEEDED = ("succeeded", "success", "finished", "completed")
_RUNNING = ("running", "starting", "restarting")
_PENDING = ("scheduling", "queued", "pending", "created", "waiting")


def classify_cml_status(status: str | None) -> CmlRunState:
    """Bucket a raw CML job-run status into a state the application can act on.

    Returns "unknown" for anything unrecognised, including None. Callers must
    treat "unknown" as "cannot tell" and keep the run visible, never as "fine".
    """
    if not status:
        return "unknown"

    text = status.strip().lower()
    if text.startswith("engine_"):
        text = text[len("engine_") :]

    # Failure is checked first: a status that mentions both (a "stopped" run that
    # had been "running") is a failure from the operator's point of view.
    for needle in _FAILED:
        if needle in text:
            return "failed"
    for needle in _SUCCEEDED:
        if needle in text:
            return "succeeded"
    for needle in _RUNNING:
        if needle in text:
            return "running"
    for needle in _PENDING:
        if needle in text:
            return "pending"
    return "unknown"


def is_terminal(state: CmlRunState) -> bool:
    """Whether the job run has stopped and will not change state again."""
    return state in ("succeeded", "failed")
