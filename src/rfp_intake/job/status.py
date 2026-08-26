"""Status writer — atomic status.json updates per ARCHITECTURE.md §6.2."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class DocumentStatus(BaseModel):
    """Per-document progress in status.json."""

    name: str
    state: Literal["pending", "parsing", "parsed", "failed"]
    parser_rung: int | None = None
    pages: int | None = None
    note: str | None = None


class RunStatus(BaseModel):
    """Top-level status.json schema — what the CAI Application polls."""

    run_id: str
    state: Literal["starting", "running", "completed", "failed"]
    node: str
    started_at: str
    heartbeat_at: str
    progress: dict[str, int] | None = None
    documents: list[DocumentStatus] = Field(default_factory=list)
    error: str | None = None


class StatusWriter:
    """Writes status.json atomically on each node transition."""

    def __init__(self, run_path: Path) -> None:
        self._path = run_path / "status.json"
        self._run_id = run_path.name
        self._started_at = datetime.now(UTC).isoformat()

    def write(
        self,
        state: Literal["starting", "running", "completed", "failed"],
        node: str,
        *,
        progress: dict[str, int] | None = None,
        documents: list[DocumentStatus] | None = None,
        error: str | None = None,
    ) -> None:
        """Write status.json atomically (write .tmp then rename)."""
        status = RunStatus(
            run_id=self._run_id,
            state=state,
            node=node,
            started_at=self._started_at,
            heartbeat_at=datetime.now(UTC).isoformat(),
            progress=progress,
            documents=documents or [],
            error=error,
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(status.model_dump_json(indent=2))
        tmp.rename(self._path)
