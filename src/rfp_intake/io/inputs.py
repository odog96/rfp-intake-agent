"""Input resolution seam — resolves source URIs to local file paths."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


class InputResolver(Protocol):
    """Resolves a source URI to a list of local file paths."""

    def resolve(self, source: str) -> list[Path]: ...


class LocalInputResolver:
    """Resolves file:// URIs to PDF paths in a local directory."""

    def resolve(self, source: str) -> list[Path]:
        parsed = urlparse(source)
        if parsed.scheme and parsed.scheme != "file":
            raise ValueError(f"LocalInputResolver only handles file:// URIs, got: {parsed.scheme}://")

        dir_path = Path(parsed.path) if parsed.scheme == "file" else Path(source)

        if not dir_path.exists():
            raise FileNotFoundError(f"Input directory not found: {dir_path}")
        if not dir_path.is_dir():
            # Single file
            if dir_path.suffix.lower() == ".pdf":
                return [dir_path]
            raise ValueError(f"Not a PDF file or directory: {dir_path}")

        pdfs = sorted(dir_path.glob("*.pdf"), key=lambda p: p.name)
        if not pdfs:
            raise FileNotFoundError(f"No PDF files found in: {dir_path}")
        return pdfs


class ObjectStoreInputResolver:
    """ADLS (abfss://) input resolution — deferred to Phase 2. See ARCHITECTURE.md §11."""

    def resolve(self, source: str) -> list[Path]:
        raise NotImplementedError(
            "Object store input resolution is deferred. See ARCHITECTURE.md §11."
        )


def get_resolver(source: str) -> InputResolver:
    """Factory: dispatch to the appropriate resolver based on URI scheme."""
    parsed = urlparse(source)

    if parsed.scheme == "abfss":
        return ObjectStoreInputResolver()

    # Default to local — handles file:// and bare paths
    return LocalInputResolver()
