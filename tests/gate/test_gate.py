

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
