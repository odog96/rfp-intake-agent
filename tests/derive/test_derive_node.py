"""Tests for the DERIVE graph node.

No field in config/fields.yaml is derived and no rubric is registered since
2026-11-23, when the visit-intensity rating and what to assume about the
placebo were removed (Angus Gray, 29 October 2026). DERIVE still writes the
visit totals of each stage (`stage_totals`) and keeps its machinery for a
derived field: every row of each input reaches the rubric, the rows a field's
`leave_out_scopes` names are held back and named, and a field with no rubric,
or a rubric that fails, is not_specified with an error. These tests drive that
machinery with a derived field added to a copy of the real registry and a
stand-in rubric, so no removed field is named.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from rfp_intake.derive.rubric import RubricResult
from rfp_intake.domain.registry import Registry
from rfp_intake.domain.schemas import Provenance, ResolvedField, RunState

_INPUT = "ops.sites_total"
_DERIVED = "ops.sites_band"
_REASON = "it is about patients who stopped treatment early"


def _use_registry(path: Path) -> None:
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(path)
    from rfp_intake.domain.registry import get_registry
    get_registry.cache_clear()


@pytest.fixture()
def with_derived_field(fields_yaml_path: Path, tmp_path: Path) -> Path:
    """The real registry plus one derived field read from the site count, which
    leaves out rows about patients who stopped treatment early."""
    data = yaml.safe_load(fields_yaml_path.read_text())
    group = next(f for f in data["fields"] if f["id"] == _INPUT)["group"]
    data["fields"].append({
        "id": _DERIVED, "group": group, "label": "Sites band", "type": "enum",
        "values": ["low", "high", "not_specified"],
        "derived": True, "derived_from": [_INPUT],
        "leave_out_scopes": {
            "reason": _REASON,
            "phrases": ["discontinue study drug", "early treatment discontinuation subjects",
                        "early termination subjects", "post etd"],
        },
    })
    p = tmp_path / "fields.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True))
    _use_registry(p)
    return p


class _Rubric:
    """A stand-in rubric: keeps what it was given and reports the rows it read."""

    def __init__(self, fail: bool = False) -> None:
        self.inputs: dict[str, list[ResolvedField]] = {}
        self.fail = fail

    def __call__(self, inputs: dict[str, list[ResolvedField]], registry: Registry) -> RubricResult:
        if self.fail:
            raise ValueError("cannot score")
        self.inputs = inputs
        rows = inputs[_INPUT]
        if not rows:
            return RubricResult(value="not_specified", status="not_specified", confidence=0.0)
        return RubricResult(
            value="high",
            status="needs_review",
            confidence=min(r.confidence for r in rows),
            sources=[p for r in rows for p in r.sources],
            explanation=f"Read {len(rows)} rows",
            why="many sites",
        )


@pytest.fixture()
def rubric(monkeypatch: pytest.MonkeyPatch) -> _Rubric:
    import rfp_intake.derive as derive

    stand_in = _Rubric()
    monkeypatch.setitem(derive.DERIVE_RUBRICS, _DERIVED, stand_in)
    return stand_in


def _row(value: Any, scope: str | None, page: int, field_id: str = _INPUT) -> ResolvedField:
    return ResolvedField(
        field_id=field_id, value=value, status="needs_review", confidence=0.85, scope=scope,
        sources=[Provenance(doc_id="doc-1", doc_kind="protocol", page=page)],
    )


def _derive(rows: list[ResolvedField]) -> dict[str, Any]:
    from rfp_intake.derive import derive_node

    return derive_node(RunState(run_id="t", resolved=rows))


def _derived(result: dict[str, Any]) -> ResolvedField:
    row: ResolvedField = next(r for r in result["resolved"] if r.field_id == _DERIVED)
    return row


class TestDeriveNode:
    def test_runs_with_no_rubric_registered(self, fields_yaml_path: Path) -> None:
        """The real registry: nothing derived, no rubric, nothing to add."""
        _use_registry(fields_yaml_path)
        from rfp_intake.derive import DERIVE_RUBRICS
        from rfp_intake.domain.registry import get_registry

        assert DERIVE_RUBRICS == {}
        assert [f.id for f in get_registry().fields if f.derived] == []
        rows = [_row(75, None, 3)]
        result = _derive(rows)
        assert result["resolved"] == rows
        assert result["errors"] == []

    def test_preserves_prior_resolved_entries(
        self, with_derived_field: Path, rubric: _Rubric,
    ) -> None:
        result = _derive([_row(75, None, 3)])
        assert [r.field_id for r in result["resolved"]] == [_INPUT, _DERIVED]

    def test_a_registered_rubric_writes_a_derived_row(
        self, with_derived_field: Path, rubric: _Rubric,
    ) -> None:
        result = _derive([_row(75, None, 3)])
        derived = _derived(result)
        assert (derived.value, derived.status, derived.confidence) == ("high", "needs_review", 0.85)
        assert derived.derived_from == [_INPUT]
        assert derived.notes == "Read 1 rows"
        assert derived.why == "many sites"
        assert [p.page for p in derived.sources] == [3]
        assert result["errors"] == []

    def test_a_derived_field_with_no_rubric_is_not_specified_and_an_error(
        self, with_derived_field: Path,
    ) -> None:
        result = _derive([_row(75, None, 3)])
        derived = _derived(result)
        assert (derived.value, derived.status) == (None, "not_specified")
        [error] = result["errors"]
        assert error.task_id == _DERIVED
        assert "No rubric" in error.error

    def test_a_failing_rubric_is_not_specified_and_an_error(
        self, with_derived_field: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import rfp_intake.derive as derive

        monkeypatch.setitem(derive.DERIVE_RUBRICS, _DERIVED, _Rubric(fail=True))
        result = _derive([_row(75, None, 3)])
        assert _derived(result).status == "not_specified"
        [error] = result["errors"]
        assert "cannot score" in error.error


class TestDeriveNodeReadsEveryRow:
    def test_a_later_narrower_row_no_longer_replaces_the_wide_row(
        self, with_derived_field: Path, rubric: _Rubric,
    ) -> None:
        """Until 2026-10-11 the last row of an input replaced the others."""
        wide, narrow = _row(75, None, 40), _row(12, "Months 1-3", 41)
        derived = _derived(_derive([wide, narrow]))
        assert rubric.inputs[_INPUT] == [wide, narrow]
        assert derived.notes is not None
        assert "left out" not in derived.notes

    def test_a_row_about_patients_who_stopped_early_is_left_out(
        self, with_derived_field: Path, rubric: _Rubric,
    ) -> None:
        """docs/ANALYST_PROCEDURE_PROTOCOL.md section 9: dropouts are budgeted apart."""
        from rfp_intake.domain.registry import get_registry

        kept = _row(75, None, 40)
        derived = _derived(_derive([
            kept, _row(12, "subjects who discontinue study drug early", 52),
        ]))
        assert rubric.inputs[_INPUT] == [kept]
        label = get_registry().get_field(_INPUT).label
        assert derived.notes is not None
        assert (
            f'left out {label} for "subjects who discontinue study drug early" '
            f"(protocol p.52): {_REASON}"
        ) in derived.notes
        assert [p.page for p in derived.sources] == [40]

    def test_the_end_of_treatment_visit_is_not_left_out(
        self, with_derived_field: Path, rubric: _Rubric,
    ) -> None:
        """"Early Termination" also names the last visit of the main schedule."""
        derived = _derived(_derive([
            _row(75, None, 40), _row(3, "End of Treatment / Early Termination visit", 44),
        ]))
        assert len(rubric.inputs[_INPUT]) == 2
        assert derived.notes is not None
        assert "left out" not in derived.notes

    def test_rows_with_one_scope_are_named_once_with_each_page_once(
        self, with_derived_field: Path, rubric: _Rubric,
    ) -> None:
        """r-20260827-180418 split one dropout schedule into five rows on two pages."""
        scope = "Early Treatment Discontinuation subjects"
        derived = _derived(_derive([
            _row(75, None, 40), _row(1, scope, 111), _row(2, scope, 112), _row(3, scope, 111),
        ]))
        assert derived.notes is not None
        assert derived.notes.count(scope) == 1
        assert f'"{scope}" (protocol p.111, protocol p.112):' in derived.notes

    def test_every_row_left_out_is_not_specified_and_still_named(
        self, with_derived_field: Path, rubric: _Rubric,
    ) -> None:
        from rfp_intake.domain.registry import get_registry

        derived = _derived(_derive([_row(12, "Post-ETD follow-up", 60)]))
        assert rubric.inputs[_INPUT] == []
        assert derived.status == "not_specified"
        assert derived.notes is not None
        label = get_registry().get_field(_INPUT).label
        assert f'left out {label} for "Post-ETD follow-up"' in derived.notes
