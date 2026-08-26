"""Tests for io/inputs.py — input resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from rfp_intake.io.inputs import (
    LocalInputResolver,
    ObjectStoreInputResolver,
    get_resolver,
)


def test_local_resolver_finds_pdfs(samples_dir: Path) -> None:
    resolver = LocalInputResolver()
    pdfs = resolver.resolve(str(samples_dir))
    assert len(pdfs) == 5
    assert all(p.suffix.lower() == ".pdf" for p in pdfs)


def test_local_resolver_file_uri(samples_dir: Path) -> None:
    resolver = LocalInputResolver()
    pdfs = resolver.resolve(f"file://{samples_dir}")
    assert len(pdfs) == 5


def test_local_resolver_sorted(samples_dir: Path) -> None:
    resolver = LocalInputResolver()
    pdfs = resolver.resolve(str(samples_dir))
    names = [p.name for p in pdfs]
    assert names == sorted(names)


def test_local_resolver_empty_dir(tmp_path: Path) -> None:
    resolver = LocalInputResolver()
    with pytest.raises(FileNotFoundError, match="No PDF files"):
        resolver.resolve(str(tmp_path))


def test_local_resolver_missing_dir() -> None:
    resolver = LocalInputResolver()
    with pytest.raises(FileNotFoundError, match="not found"):
        resolver.resolve("/nonexistent/path")


def test_local_resolver_rejects_non_file_scheme() -> None:
    resolver = LocalInputResolver()
    with pytest.raises(ValueError, match="only handles file://"):
        resolver.resolve("https://example.com/docs")


def test_object_store_resolver_not_implemented() -> None:
    resolver = ObjectStoreInputResolver()
    with pytest.raises(NotImplementedError):
        resolver.resolve("abfss://container@storage/path")


def test_get_resolver_local() -> None:
    resolver = get_resolver("/some/path")
    assert isinstance(resolver, LocalInputResolver)


def test_get_resolver_file_uri() -> None:
    resolver = get_resolver("file:///some/path")
    assert isinstance(resolver, LocalInputResolver)


def test_get_resolver_abfss() -> None:
    resolver = get_resolver("abfss://container@account.dfs.core.windows.net/data")
    assert isinstance(resolver, ObjectStoreInputResolver)
