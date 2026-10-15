"""The must-hold check's KNOWN verdict, approved by Oliver on 2026-10-15.

A row of a `rows_say` fact may carry `known`, and a `coverage` fact may list
`known_missing` fields. Where only that named part is missing the fact is KNOWN,
which does not fail the run; anything else missing still fails.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def check() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_must_hold", ROOT / "scripts" / "check_must_hold.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(field_id: str, value: Any, doc_kind: str = "rfp") -> dict[str, Any]:
    return {"field_id": field_id, "value": value, "status": "confirmed",
            "sources": [{"doc_kind": doc_kind, "page": 3}]}


ROWS_FACT = {
    "kind": "rows_say",
    "rows": [
        {"field": "ops.a", "from": "rfp", "says": ["80", "visits"], "known": "open item 1"},
        {"field": "ops.b", "from": "rfp", "says": ["75", "visits"]},
    ],
}


class TestRowsSay:
    def test_every_row_present_passes(self, check: ModuleType) -> None:
        run = check.Run({"resolved_fields": [_row("ops.a", "80 visits"),
                                             _row("ops.b", "75 visits")]})
        assert check.rows_say(run, ROWS_FACT)[0] == "PASS"

    def test_only_the_known_row_missing_is_known(self, check: ModuleType) -> None:
        run = check.Run({"resolved_fields": [_row("ops.b", "75 visits")]})
        verdict, detail = check.rows_say(run, ROWS_FACT)
        assert verdict == "KNOWN"
        assert "a: KNOWN MISSING, open item 1" in detail

    def test_a_strict_row_missing_fails_even_beside_a_known_one(self, check: ModuleType) -> None:
        run = check.Run({"resolved_fields": [_row("ops.b", "70 visits")]})
        assert check.rows_say(run, ROWS_FACT)[0] == "FAIL"

    def test_a_known_row_with_the_wrong_figure_is_still_only_known(
        self, check: ModuleType
    ) -> None:
        run = check.Run({"resolved_fields": [_row("ops.a", "8 visits"),
                                             _row("ops.b", "75 visits")]})
        assert check.rows_say(run, ROWS_FACT)[0] == "KNOWN"


class TestCoverage:
    @pytest.fixture()
    def registry(self, check: ModuleType, tmp_path: Path,
                 monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "fields.yaml"
        path.write_text("fields:\n" + "".join(f"  - {{id: f.{i}}}\n" for i in range(5)))
        monkeypatch.setattr(check, "FIELDS_PATH", path)

    FACT = {"kind": "coverage", "at_least": 4,
            "known_missing": [{"field": "f.0", "known": "open item 1"}]}

    def _run(self, check: ModuleType, answered: list[int]) -> Any:
        return check.Run({"resolved_fields": [_row(f"f.{i}", "x") for i in answered]})

    @pytest.mark.usefixtures("registry")
    def test_enough_fields_passes(self, check: ModuleType) -> None:
        assert check.coverage(self._run(check, [1, 2, 3, 4]), self.FACT)[0] == "PASS"

    @pytest.mark.usefixtures("registry")
    def test_one_short_because_of_the_known_field_is_known(self, check: ModuleType) -> None:
        verdict, detail = check.coverage(self._run(check, [1, 2, 3]), self.FACT)
        assert verdict == "KNOWN"
        assert "known: f.0, open item 1" in detail

    @pytest.mark.usefixtures("registry")
    def test_one_short_with_the_known_field_present_fails(self, check: ModuleType) -> None:
        assert check.coverage(self._run(check, [0, 1, 2]), self.FACT)[0] == "FAIL"

    @pytest.mark.usefixtures("registry")
    def test_the_allowance_is_one_field_not_more(self, check: ModuleType) -> None:
        assert check.coverage(self._run(check, [1, 2]), self.FACT)[0] == "FAIL"


def test_the_facts_file_marks_only_site_selection_visits_as_known() -> None:
    import yaml

    facts = yaml.safe_load((ROOT / "eval" / "golden" / "sample_pair_must_hold.yaml").read_text())
    known_rows = [(f["id"], r["field"]) for f in facts["facts"] for r in f.get("rows", [])
                  if r.get("known")]
    known_fields = [(f["id"], k["field"]) for f in facts["facts"]
                    for k in f.get("known_missing", [])]
    assert known_rows == [(8, "ops.visits_site_selection")]
    assert known_fields == [(9, "ops.visits_site_selection")]
