

class TestCollectionFieldsDoNotContradict:
    """Members of a list field are parts of one answer, not rival answers."""

    @staticmethod
    def _record(field_id: str, group: str, value, page: int):  # type: ignore[no-untyped-def]
        from rfp_intake.domain.schemas import FieldRecord, Provenance

        return FieldRecord(
            field_id=field_id,
            group=group,
            raw_value=str(value),
            value=value,
            quote=f"quote for {value}",
            provenance=Provenance(doc_id="doc-1", doc_kind="protocol", page=page),
            confidence=0.9,
            status="found",
        )

    def test_list_enum_members_merge_into_one_answer(self) -> None:
        # visits.intensity_evidence produced 38 of 158 rows in run
        # r-20260827-205037, one per piece of evidence found.
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        registry = get_registry()
        field_def = registry.get_field("visits.intensity_evidence")
        records = [
            self._record("visits.intensity_evidence", "visits", ["safety_labs"], 1),
            self._record("visits.intensity_evidence", "visits", ["biomarker_sampling"], 2),
            self._record("visits.intensity_evidence", "visits", ["questionnaires"], 3),
        ]
        resolved, contradictions = _reconcile_field(field_def, records)

        assert len(resolved) == 1, "three pieces of evidence are one answer"
        assert contradictions == [], "collection members never disagree"
        assert set(resolved[0].value) == {
            "safety_labs",
            "biomarker_sampling",
            "questionnaires",
        }
        assert len(resolved[0].sources) == 3, "every source is kept"

    def test_repeated_members_appear_once(self) -> None:
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        field_def = get_registry().get_field("visits.intensity_evidence")
        records = [
            self._record("visits.intensity_evidence", "visits", ["safety_labs"], 1),
            self._record("visits.intensity_evidence", "visits", ["safety_labs"], 7),
        ]
        resolved, _ = _reconcile_field(field_def, records)
        assert resolved[0].value == ["safety_labs"]

    def test_members_keep_the_order_they_were_found_in(self) -> None:
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        field_def = get_registry().get_field("visits.intensity_evidence")
        records = [
            self._record("visits.intensity_evidence", "visits", ["ecgs"], 1),
            self._record("visits.intensity_evidence", "visits", ["safety_labs"], 2),
        ]
        resolved, _ = _reconcile_field(field_def, records)
        assert resolved[0].value == ["ecgs", "safety_labs"]

    def test_a_single_valued_field_still_contradicts(self) -> None:
        # The change must not stop RECONCILE noticing a real disagreement:
        # ops.sites_total is an int in config/fields.yaml, so 75 and 40 conflict.
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        field_def = get_registry().get_field("ops.sites_total")
        records = [
            self._record("ops.sites_total", "operational_metrics", 75, 4),
            self._record("ops.sites_total", "operational_metrics", 40, 9),
        ]
        resolved, contradictions = _reconcile_field(field_def, records)
        assert len(contradictions) == 1
        assert resolved == []
