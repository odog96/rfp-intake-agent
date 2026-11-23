import pytest


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
        # visits.intensity_evidence (removed 2026-11-23) produced 38 of 158 rows
        # in run r-20260827-205037, one per piece of evidence found. The list
        # field here has the same shape: list[enum], unscoped.
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        registry = get_registry()
        field_def = registry.get_field("monitoring.unblinded_factors")
        records = [
            self._record("monitoring.unblinded_factors", "blinding_monitoring", ["ip_accountability"], 1),
            self._record("monitoring.unblinded_factors", "blinding_monitoring", ["pharmacy_manual_requirements"], 2),
            self._record("monitoring.unblinded_factors", "blinding_monitoring", ["drug_preparation_requirements"], 3),
        ]
        resolved, contradictions = _reconcile_field(field_def, records)

        assert len(resolved) == 1, "three pieces of evidence are one answer"
        assert contradictions == [], "collection members never disagree"
        assert set(resolved[0].value) == {
            "ip_accountability",
            "pharmacy_manual_requirements",
            "drug_preparation_requirements",
        }
        assert len(resolved[0].sources) == 3, "every source is kept"

    def test_repeated_members_appear_once(self) -> None:
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        field_def = get_registry().get_field("monitoring.unblinded_factors")
        records = [
            self._record("monitoring.unblinded_factors", "blinding_monitoring", ["ip_accountability"], 1),
            self._record("monitoring.unblinded_factors", "blinding_monitoring", ["ip_accountability"], 7),
        ]
        resolved, _ = _reconcile_field(field_def, records)
        assert resolved[0].value == ["ip_accountability"]

    def test_members_keep_the_order_they_were_found_in(self) -> None:
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        field_def = get_registry().get_field("monitoring.unblinded_factors")
        records = [
            self._record("monitoring.unblinded_factors", "blinding_monitoring", ["blinded_vs_open_design"], 1),
            self._record("monitoring.unblinded_factors", "blinding_monitoring", ["ip_accountability"], 2),
        ]
        resolved, _ = _reconcile_field(field_def, records)
        assert resolved[0].value == ["blinded_vs_open_design", "ip_accountability"]

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


class TestARecordsOwnNoteStaysOnTheRecord:
    """RECONCILE never copies `FieldRecord.notes` onto a `ResolvedField`.

    Appendix A prints `ResolvedField.notes` for any row with no disagreement
    (`render/report_model.py`), which change 3 needs so a resolution rule's
    `because` sentence appears beside the passage it borrowed. On 2026-10-08 the
    plan-conformance reviewer read that condition as broad enough to start
    printing the extraction model's own notes, since `config/fields.yaml` asks
    for them on some fields including `interim.count`. It cannot: the model's
    note is written to `FieldRecord.notes` by `extract/__init__.py` and
    `domain/dynamic.py`, and nothing in RECONCILE carries it across.

    RECONCILE builds a `ResolvedField` in five places: four in
    `reconcile/__init__.py` (lines 120, 168, 230 and 284), none of which passes
    `notes` at all, and one in `reconcile/rules.py` (line 102), which does pass
    `notes` — but from the resolution rule's own `because` sentence, never from a
    record. The three tests below cover the three of those five that have a
    record to copy a note from: the single-answer path (line 168), the
    all-silent path (line 120) and the collection path (line 230). Line 284 is
    `_not_found`, which has no record at all. The third reviewer pass on
    2026-10-08 found this docstring claiming four paths while one test covered
    one of them; the two extra tests and this paragraph are the answer. The
    fourth pass on 2026-10-11 found the all-silent test built from found
    records, so it ran line 168 a second time; it now builds its own.
    """

    _record = staticmethod(TestCollectionFieldsDoNotContradict._record)

    _NOTE = "The protocol mentions a third analysis that was cancelled."

    def test_a_resolved_field_has_no_note_of_its_own(self) -> None:
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        field_def = get_registry().get_field("interim.count")
        record = self._record("interim.count", "interim_analyses", 2, 98)
        record.notes = self._NOTE
        resolved, contradictions = _reconcile_field(field_def, [record])

        assert contradictions == []
        [rf] = resolved
        assert rf.notes is None, (
            "the model's note stays in extraction.json and off the report's evidence page"
        )

    def test_a_field_every_record_calls_not_specified_has_no_note_either(self) -> None:
        """The status path at `reconcile/__init__.py:120`.

        `_record` marks every record found, which would send these down the
        single-answer path at line 168 instead; the fourth reviewer pass on
        2026-10-11 caught exactly that. So the records are rebuilt here with
        `status="not_specified"` and no value, and the assertions check the
        `value=None` that only line 120 produces.
        """
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        field_def = get_registry().get_field("design.first_in_human")
        records = [
            self._record("design.first_in_human", "study_design", "not_specified", page)
            .model_copy(update={"status": "not_specified", "value": None, "notes": self._NOTE})
            for page in (18, 4)
        ]
        resolved, contradictions = _reconcile_field(field_def, records)

        assert contradictions == []
        [rf] = resolved
        assert rf.status == "not_specified"
        assert rf.value is None
        assert rf.notes is None

    def test_a_collection_field_has_no_note_either(self) -> None:
        """The members path at `reconcile/__init__.py:230`.

        This is the path where a note would be worst: the members come from
        several records, so the report would have to pick one record's note and
        print it as if it described the merged answer.
        """
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        field_def = get_registry().get_field("monitoring.unblinded_factors")
        records = [
            self._record("monitoring.unblinded_factors", "blinding_monitoring", ["ip_accountability"], 1),
            self._record("monitoring.unblinded_factors", "blinding_monitoring", ["drug_preparation_requirements"], 2),
        ]
        for record in records:
            record.notes = self._NOTE
        resolved, contradictions = _reconcile_field(field_def, records)

        assert contradictions == []
        [rf] = resolved
        assert rf.notes is None


class TestNotSpecifiedIsNotACompetingAnswer:
    """Change 2 of the 2026-10-07 review.

    A passage that does not mention the subject does not disagree with one that
    answers it. In run r-20261007-140411 that reading produced Disagreement 9
    (First-in-human: "No" on amendment p.11 against "Not specified" on p.18) and
    Disagreement 5 (Unblinded staff stated as required).
    """

    _record = staticmethod(TestCollectionFieldsDoNotContradict._record)

    def test_a_real_answer_beats_not_specified_without_a_disagreement(self) -> None:
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        field_def = get_registry().get_field("design.first_in_human")
        records = [
            self._record("design.first_in_human", "study_design", "false", 11),
            self._record("design.first_in_human", "study_design", "not_specified", 18),
        ]
        resolved, contradictions = _reconcile_field(field_def, records)

        assert contradictions == [], "saying nothing is not a rival answer"
        [rf] = resolved
        assert rf.value == "false"
        assert [p.page for p in rf.sources] == [11, 18], (
            "the page that says nothing is still cited, so Appendix A is unchanged"
        )
        assert rf.confidence == 0.9, "silence is not corroboration, so no boost"

    def test_two_real_answers_still_disagree_while_silence_drops_out(self) -> None:
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        field_def = get_registry().get_field("design.first_in_human")
        records = [
            self._record("design.first_in_human", "study_design", "false", 11),
            self._record("design.first_in_human", "study_design", "not_specified", 18),
            self._record("design.first_in_human", "study_design", "true", 40),
        ]
        resolved, contradictions = _reconcile_field(field_def, records)

        assert resolved == []
        [c] = contradictions
        assert [r.value for r in c.records] == ["false", "true"], (
            "the reviewer is asked about the two real answers only"
        )

    def test_a_count_of_zero_is_an_answer_and_still_disagrees_with_one(self) -> None:
        """Non-negotiable rule 3, on the field change 2 could most easily break.

        `0` is a real count a document stated, not a document saying nothing, so
        change 2 must not quietly drop it out of a disagreement. This is the
        exact pair run r-20261008-review2-run3 found — 0 interim analyses on
        amendment p.98 against 1 in the RFP p.4 — and it is Disagreement 1 of
        the report, so losing it would be visible. Added 2026-10-08 after the
        plan-conformance reviewer pointed out that only a code comment and the
        `isinstance(value, str)` guard in `_says_nothing` stood behind it.
        """
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        field_def = get_registry().get_field("interim.count")
        records = [
            self._record("interim.count", "interim_analyses", 0, 98),
            self._record("interim.count", "interim_analyses", 1, 4),
        ]
        resolved, contradictions = _reconcile_field(field_def, records)

        assert resolved == [], "a real disagreement resolves nothing until ADJUDICATE"
        [c] = contradictions
        assert [r.value for r in c.records] == [0, 1], (
            "zero is one of the two rival answers, not a record that says nothing"
        )

    def test_a_count_of_zero_beats_a_record_that_says_nothing(self) -> None:
        """The other half of rule 3: `0` wins over `not_specified` without a disagreement."""
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        field_def = get_registry().get_field("interim.count")
        records = [
            self._record("interim.count", "interim_analyses", 0, 98),
            self._record("interim.count", "interim_analyses", "not_specified", 12),
        ]
        resolved, contradictions = _reconcile_field(field_def, records)

        assert contradictions == []
        [rf] = resolved
        assert rf.value == 0, "an explicit zero is the answer, not 'not specified'"
        assert [p.page for p in rf.sources] == [98, 12]

    def test_every_record_saying_nothing_still_resolves_to_not_specified(self) -> None:
        # Rule 3: not_specified stays distinct from not_found and from 0.
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        field_def = get_registry().get_field("design.first_in_human")
        records = [
            self._record("design.first_in_human", "study_design", "not_specified", 18),
            self._record("design.first_in_human", "study_design", "not_specified", 4),
        ]
        resolved, contradictions = _reconcile_field(field_def, records)

        assert contradictions == []
        [rf] = resolved
        assert rf.value == "not_specified"
        assert [p.page for p in rf.sources] == [18, 4]


class TestTheWholeStudyAndNoScopeOnAnUnscopedField:
    """Run r-20261123-oct29-run1: the amendment's placebo record had no scope and
    the RFP's had "total", so RECONCILE grouped them apart and the disagreement
    must-hold fact 6 looks for was two confirmed rows. On a field the registry
    says is not `scoped`, the whole study and no scope are one group; on a
    scoped field they stay apart, and other scopes stay apart on any field."""

    @staticmethod
    def _record(field_id: str, value: object, scope: str | None, doc: str, page: int):  # type: ignore[no-untyped-def]
        from rfp_intake.domain.schemas import FieldRecord, Provenance

        kind = "rfp" if doc == "rfp" else "amendment"
        return FieldRecord(
            field_id=field_id, group="g", raw_value=str(value), value=value,
            quote=f"quote p.{page}",
            provenance=Provenance(doc_id=doc, doc_kind=kind, page=page),  # type: ignore[arg-type]
            confidence=0.9, status="found", scope=scope,
        )

    @pytest.mark.parametrize(
        "field_id,scoped,first,second,clusters,rows",
        [
            # An unscoped enum: no scope against the whole study, two values,
            # is one candidate cluster for ADJUDICATE.
            ("blinding.placebo_matching", False,
             ("not_matching_stated", None), ("matching_stated", "total"), 1, 0),
            # The same with the values agreeing is one row citing both pages.
            ("blinding.placebo_matching", False,
             ("matching_stated", None), ("matching_stated", "Total"), 0, 1),
            # "study" is the whole study too.
            ("study.indication", False,
             ("AL amyloidosis", None), ("Light chain amyloidosis", "study"), 1, 0),
            # Another scope on an unscoped field stays apart.
            ("study.indication", False,
             ("AL amyloidosis", "arm:placebo"), ("Light chain amyloidosis", "total"), 0, 2),
            # A scoped int field of the same shape keeps two groups.
            ("ops.sites_total", True, (40, None), (52, "total"), 0, 2),
        ],
    )
    def test_grouping(  # type: ignore[no-untyped-def]
        self, fields_yaml_path, field_id, scoped, first, second, clusters, rows,
    ) -> None:
        import os

        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import _reconcile_field

        get_registry.cache_clear()
        field_def = get_registry().get_field(field_id)
        assert field_def.scoped is scoped
        records = [
            self._record(field_id, first[0], first[1], "amendment", 53),
            self._record(field_id, second[0], second[1], "rfp", 3),
        ]
        resolved, contradictions = _reconcile_field(field_def, records)
        assert len(contradictions) == clusters
        assert len(resolved) == rows
        if clusters:
            assert contradictions[0].records == records
        if rows == 1:
            assert [s.page for s in resolved[0].sources] == [53, 3]
