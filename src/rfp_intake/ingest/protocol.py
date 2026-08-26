"""Parser protocol — the contract all rungs implement."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from rfp_intake.ingest.models import ParsedDoc


class Parser(Protocol):
    """Interface for PDF parsers at each fidelity rung."""

    rung: int

    def parse(self, path: Path) -> ParsedDoc: ...

    def can_parse(self, path: Path) -> bool: ...
