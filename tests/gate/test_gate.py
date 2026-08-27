

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
        # in a budget is a human judgement.
        from rfp_intake.gate import _gate_field

        out = _gate_field(
            self._resolved("ops.monitoring_visits", ["75", "750", "300"]),
            None,
            is_budget_driver=True,
        )
        assert out.status == "needs_review"

    def test_a_single_value_in_a_list_still_confirms(self) -> None:
        from rfp_intake.gate import _gate_field

        out = _gate_field(
            self._resolved("ops.monitoring_visits", ["75"]), None, is_budget_driver=True
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
