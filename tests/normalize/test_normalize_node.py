"""Tests for the NORMALIZE graph node."""

from __future__ import annotations

from rfp_intake.domain.schemas import FieldRecord, Provenance, RunState
from rfp_intake.normalize import normalize_node


def _make_record(field_id: str, group: str, raw_value: str) -> FieldRecord:
    return FieldRecord(
        field_id=field_id,
        group=group,
        raw_value=raw_value,
        quote=f"the document states {raw_value}",
        provenance=Provenance(
            doc_id="doc-001",
            doc_kind="protocol",
            page=5,
        ),
        confidence=0.9,
    )


class TestNormalizeNode:
    def test_normalizes_int_field(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Integer fields get normalized to int values."""
        import os
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)

        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        record = _make_record("ops.sites_total", "operational_metrics", "75")
        state = RunState(
            run_id="test-run",
            records=[record],
        )

        result = normalize_node(state)
        assert len(result["records"]) == 1
        assert result["records"][0].value == 75
        assert result["errors"] == []

    def test_normalizes_enum_field(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Enum fields get normalized to canonical enum values."""
        import os
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)

        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        record = _make_record("study.phase", "phase_population", "Phase 1/2")
        state = RunState(
            run_id="test-run",
            records=[record],
        )

        result = normalize_node(state)
        assert result["records"][0].value == "phase_1_2"

    def test_normalizes_duration_field(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Duration-aware text fields parse to structured dicts."""
        import os
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)

        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        record = _make_record("timeline.total_duration", "timelines", "40 months")
        state = RunState(
            run_id="test-run",
            records=[record],
        )

        result = normalize_node(state)
        assert result["records"][0].value == {"n": 40, "unit": "months"}
        assert result["records"][0].unit == "months"

    def test_not_specified_stays_none(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Records with status=not_specified get value=None."""
        import os
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)

        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        record = _make_record("ops.sites_total", "operational_metrics", "N/A")
        record.status = "not_specified"
        state = RunState(
            run_id="test-run",
            records=[record],
        )

        result = normalize_node(state)
        assert result["records"][0].value is None

    def test_multiple_records(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Multiple records all get normalized."""
        import os
        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)

        from rfp_intake.domain.registry import get_registry
        get_registry.cache_clear()

        records = [
            _make_record("ops.sites_total", "operational_metrics", "75"),
            _make_record("ops.subjects_total", "operational_metrics", "120"),
            _make_record("study.phase", "phase_population", "Phase III"),
        ]
        state = RunState(run_id="test-run", records=records)

        result = normalize_node(state)
        assert len(result["records"]) == 3
        assert result["records"][0].value == 75
        assert result["records"][1].value == 120
        assert result["records"][2].value == "phase_3"


def test_two_wordings_of_one_page_count_are_one_value(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    """CLAUDE.md open item 10: run r-20261019-prompt-c3 read RFP p.4's
    "Approximately 20,800 CRF pages total." twice, as 20800 and as the words,
    and RECONCILE saw two values where the document states one."""
    import os
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    from rfp_intake.domain.registry import get_registry
    get_registry.cache_clear()

    records = [
        _make_record("ops.crf_pages", "operational_metrics", "20800"),
        _make_record("ops.crf_pages", "operational_metrics", "20,800 CRF pages total"),
    ]
    result = normalize_node(RunState(run_id="test-run", records=records))
    assert [r.value for r in result["records"]] == [20800, 20800]
