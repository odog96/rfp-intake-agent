import pytest


class TestBudgetDriverCollections:
    """A budget driver holding several values must not confirm itself."""

    @staticmethod
    def _resolved(field_id: str, value, confidence: float = 0.99):  # type: ignore[no-untyped-def]
        from rfp_intake.domain.schemas import ResolvedField

        return ResolvedField(
            field_id=field_id,
            value=value,
            status="needs_review",
            confidence=confidence,
            sources=[],
            quote="q",
        )

    def test_several_values_stays_needs_review(self) -> None:
        # ops.monitoring_visits came back as ["75", "750", "300"] in run
        # r-listfix-175318 — three kinds of monitoring visit. Which number belongs
        # in a budget is a human judgement. That field was replaced on 2026-10-07
        # by five single-visit-type rows, so the field named here is ip.form, the
        # budget-driving list field the registry still has.
        from rfp_intake.gate import _gate_field

        out = _gate_field(
            self._resolved("ip.form", ["infusion_iv", "injection_sc", "oral_tablet"]),
            None,
            is_budget_driver=True,
        )
        assert out.status == "needs_review"

    def test_a_single_value_in_a_list_still_confirms(self) -> None:
        from rfp_intake.gate import _gate_field

        out = _gate_field(
            self._resolved("ip.form", ["infusion_iv"]), None, is_budget_driver=True
        )
        assert out.status == "confirmed"

    def test_a_non_budget_driver_collection_still_confirms(self) -> None:
        # visits.intensity_evidence legitimately holds many members and is not a
        # number anyone budgets from directly.
        from rfp_intake.gate import _gate_field

        out = _gate_field(
            self._resolved("visits.intensity_evidence", ["safety_labs", "ecgs"]),
            None,
            is_budget_driver=False,
        )
        assert out.status == "confirmed"


class TestReviewNote:
    """A row RECONCILE wrote a review_note on stays in review (2026-10-12)."""

    @staticmethod
    def _resolved(value, review_note: str | None):  # type: ignore[no-untyped-def]
        from rfp_intake.domain.schemas import ResolvedField

        return ResolvedField(
            field_id="design.parts", value=value, status="needs_review", confidence=0.99,
            sources=[], quote="q", scope="arm:DrugY", review_note=review_note,
        )

    def test_a_note_keeps_a_confident_row_in_review(self) -> None:
        from rfp_intake.gate import _gate_field

        out = _gate_field(
            self._resolved(["Arm B: DrugY"], "Listed only in doc-a, not in doc-b."),
            None,
            is_budget_driver=True,
        )
        assert out.status == "needs_review"
        assert out.review_note == "Listed only in doc-a, not in doc-b."

    def test_without_a_note_the_same_row_confirms(self) -> None:
        from rfp_intake.gate import _gate_field

        out = _gate_field(self._resolved(["Arm B: DrugY"], None), None, is_budget_driver=True)
        assert out.status == "confirmed"

    def test_the_multi_value_rule_is_unchanged(self) -> None:
        from rfp_intake.gate import _gate_field

        out = _gate_field(
            self._resolved(["Arm B: DrugY", "DrugY arm"], None), None, is_budget_driver=True
        )
        assert out.status == "needs_review"


class TestArmsAsOneAnswer:
    """Fix 1 of the 2026-10-12 brief, approved by Oliver: where every document
    lists the same arms and agrees on each one's counted keys, design.parts
    (`one_answer_when_documents_agree` in config/fields.yaml) may confirm.
    Through RECONCILE, so the entries are merged as in a run."""

    @pytest.fixture(autouse=True)
    def _real_registry(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        import os

        from rfp_intake.domain.registry import get_registry

        os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
        get_registry.cache_clear()

    @staticmethod
    def _arm(  # type: ignore[no-untyped-def]
        entry: dict,  # type: ignore[type-arg]
        scope: str, doc: str, page: int, confidence: float = 0.95,
        field_id: str = "design.parts",
    ):
        from rfp_intake.domain.schemas import FieldRecord, Provenance

        r = FieldRecord(
            field_id=field_id, group="g", raw_value=str(entry),
            quote=f"{entry.get('name', '')} {entry.get('n_subjects', '')}",
            provenance=Provenance(doc_id=doc, doc_kind="rfp" if doc == "d-rfp" else "amendment",
                                  page=page),
            confidence=confidence, scope=scope,
        )
        r.value = entry
        return r

    def _statuses(self, records) -> dict[str | None, str]:  # type: ignore[no-untyped-def]
        from rfp_intake.domain.schemas import RunState
        from rfp_intake.gate import gate_node
        from rfp_intake.reconcile import reconcile_node

        state = RunState(run_id="t", records=records)
        state.resolved = reconcile_node(state)["resolved"]
        gated = gate_node(state)["resolved"]
        return {rf.scope: rf.status for rf in gated if rf.field_id == records[0].field_id}

    def _two_arms(self, rfp_placebo_n: int = 130, confidence: float = 0.95):  # type: ignore[no-untyped-def]
        return [
            self._arm({"name": "NEOD001 plus SoC", "kind": "arm", "n_subjects": 130},
                      "arm:NEOD001", "d-am", 15),
            self._arm({"name": "Placebo plus SoC", "kind": "arm", "n_subjects": 130},
                      "arm:placebo", "d-am", 15),
            self._arm({"name": "NEOD001 + SoC", "kind": "arm", "n_subjects": 130,
                       "document_label": "Group 1"}, "arm:NEOD001", "d-rfp", 3),
            self._arm({"name": "Placebo + SoC", "kind": "arm", "n_subjects": rfp_placebo_n,
                       "document_label": "Group 2"}, "arm:placebo", "d-rfp", 3,
                      confidence=confidence),
        ]

    def test_documents_agreeing_confirm(self) -> None:
        assert self._statuses(self._two_arms()) == {
            "arm:NEOD001": "confirmed", "arm:placebo": "confirmed",
        }

    def test_a_counted_key_the_documents_disagree_on_is_reviewed(self) -> None:
        statuses = self._statuses(self._two_arms(rfp_placebo_n=118))
        assert statuses == {"arm:NEOD001": "confirmed", "arm:placebo": "needs_review"}

    @pytest.mark.parametrize(("rfp_dose", "status"), [
        # A dose both documents state must read the same.
        ("12 mg/kg", "needs_review"),
        ("24  MG/KG", "confirmed"),
    ])
    def test_a_dose_both_documents_state_must_agree(self, rfp_dose: str, status: str) -> None:
        records = self._two_arms()
        records[0].value = dict(records[0].value, dose="24 mg/kg")
        records[2].value = dict(records[2].value, dose=rfp_dose)
        assert self._statuses(records)["arm:NEOD001"] == status

    def test_a_dose_only_one_document_states_disagrees_with_nothing(self) -> None:
        records = self._two_arms()
        records[0].value = dict(records[0].value, dose="24 mg/kg")
        assert self._statuses(records)["arm:NEOD001"] == "confirmed"

    def test_an_arm_one_document_does_not_list_is_reviewed(self) -> None:
        records = self._two_arms()[:3]
        assert self._statuses(records) == {
            "arm:NEOD001": "confirmed", "arm:placebo": "needs_review",
        }

    def test_low_confidence_is_still_reviewed(self) -> None:
        records = [self._arm(r.value, r.scope or "", r.provenance.doc_id, r.provenance.page,
                             confidence=0.6) for r in self._two_arms()]
        assert set(self._statuses(records).values()) == {"needs_review"}

    def test_a_field_without_the_flag_keeps_the_rule(self) -> None:
        # visits.frequency_by_period is a budget-driving object[] field with no
        # `one_answer_when_documents_agree`; agreeing documents still review it.
        records = [
            self._arm({"period": "screening", "label": "Screening"}, "", "d-am", 26,
                      field_id="visits.frequency_by_period"),
            self._arm({"period": "treatment", "label": "Months 1–3"}, "", "d-am", 26,
                      field_id="visits.frequency_by_period"),
            self._arm({"period": "screening", "label": "Screening"}, "", "d-rfp", 4,
                      field_id="visits.frequency_by_period"),
            self._arm({"period": "treatment", "label": "Months 1–3"}, "", "d-rfp", 4,
                      field_id="visits.frequency_by_period"),
        ]
        for r in records:
            r.scope = None
        assert set(self._statuses(records).values()) == {"needs_review"}
