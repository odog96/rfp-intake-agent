"""Tests for the DERIVE rubrics — pure functions, no model and no network.

Two rubrics: `compute_visit_intensity` (ARCHITECTURE.md §4.8) and
`compute_placebo_assumption` (docs/ANALYST_PROCEDURE_PROTOCOL.md section 6).
Only the placebo tests need the registry, and only to check that the enum value
the rubric looks for is the one the registry still offers.
"""

from __future__ import annotations

import pytest

from rfp_intake.derive.rubric import (
    PLACEBO_CONTRADICTION,
    PLACEBO_INJECTED_NOT_STATED,
    PLACEBO_OPEN_LABEL,
    PLACEBO_ORAL_NOT_STATED,
    compute_placebo_assumption,
    compute_visit_intensity,
)
from rfp_intake.domain.schemas import Provenance, ResolvedField


def _evidence(flags: list[str], confidence: float = 0.85) -> ResolvedField:
    return ResolvedField(
        field_id="visits.intensity_evidence",
        value=flags,
        status="needs_review",
        confidence=confidence,
        sources=[Provenance(doc_id="doc-1", doc_kind="protocol", page=7)],
    )


class TestComputeVisitIntensity:
    def test_no_evidence_field_is_not_specified(self) -> None:
        result = compute_visit_intensity({"visits.intensity_evidence": None})
        assert result.status == "not_specified"
        assert result.value == "not_specified"

    def test_none_found_flag_is_not_specified(self) -> None:
        result = compute_visit_intensity({"visits.intensity_evidence": _evidence(["none_found"])})
        assert result.status == "not_specified"

    def test_low_score(self) -> None:
        # questionnaires(1) = 1 -> low
        evidence = _evidence(["questionnaires"])
        result = compute_visit_intensity({"visits.intensity_evidence": evidence})
        assert result.value == "low"
        assert result.status == "needs_review"

    def test_moderate_score(self) -> None:
        # biomarker_sampling(1) + ecgs(1) + safety_labs(1) + questionnaires(1) = 4 -> moderate
        flags = ["biomarker_sampling", "ecgs", "safety_labs", "questionnaires"]
        result = compute_visit_intensity({"visits.intensity_evidence": _evidence(flags)})
        assert result.value == "moderate"

    def test_high_score(self) -> None:
        # pk_pd_sampling(2) + imaging(2) + infusion_observation_period(2) + overnight_stay(2) = 8
        flags = ["pk_pd_sampling", "imaging", "infusion_observation_period", "overnight_stay"]
        result = compute_visit_intensity({"visits.intensity_evidence": _evidence(flags)})
        assert result.value == "high"

    def test_confidence_and_sources_carried_from_evidence(self) -> None:
        evidence = _evidence(["imaging"], confidence=0.77)
        result = compute_visit_intensity({"visits.intensity_evidence": evidence})
        assert result.confidence == 0.77
        assert result.sources == evidence.sources

    def test_explanation_lists_contributing_flags(self) -> None:
        evidence = _evidence(["imaging", "ecgs"])
        result = compute_visit_intensity({"visits.intensity_evidence": evidence})
        assert "imaging" in result.explanation
        assert "ecgs" in result.explanation

    def test_evidence_not_yet_gated_is_still_read(self) -> None:
        """RECONCILE always writes status=needs_review pre-GATE; DERIVE runs before GATE."""
        evidence = _evidence(["imaging"])
        assert evidence.status == "needs_review"
        result = compute_visit_intensity({"visits.intensity_evidence": evidence})
        assert result.status == "needs_review"

    def test_not_found_evidence_status_is_not_specified(self) -> None:
        evidence = ResolvedField(
            field_id="visits.intensity_evidence", value=None, status="not_found", confidence=0.0,
        )
        result = compute_visit_intensity({"visits.intensity_evidence": evidence})
        assert result.status == "not_specified"


def _field(
    field_id: str,
    value: object,
    *,
    status: str = "needs_review",
    confidence: float = 0.8,
    quote: str | None = None,
    page: int = 12,
) -> ResolvedField:
    return ResolvedField(
        field_id=field_id,
        value=value,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        confidence=confidence,
        quote=quote,
        sources=[Provenance(doc_id="doc-1", doc_kind="protocol", page=page)],
    )


def _inputs(
    design: str | None = None,
    forms: list[str] | None = None,
    matching: str | None = None,
    staff: str | None = None,
    *,
    design_quote: str | None = None,
    matching_quote: str | None = None,
    staff_quote: str | None = None,
) -> dict[str, ResolvedField | None]:
    """The four `derived_from` inputs, any of them absent.

    An absent input is None, which is what DERIVE passes when the field was never
    resolved (`derive/__init__.py` builds `inputs` with `resolved_by_id.get`).
    """
    return {
        "blinding.design": (
            _field("blinding.design", design, quote=design_quote) if design else None
        ),
        "ip.form": _field("ip.form", forms) if forms else None,
        "blinding.placebo_matching": (
            _field("blinding.placebo_matching", matching, quote=matching_quote)
            if matching
            else None
        ),
        "blinding.unblinded_staff_stated": (
            _field("blinding.unblinded_staff_stated", staff, quote=staff_quote) if staff else None
        ),
    }


class TestComputePlaceboAssumption:
    """The five rules of docs/ANALYST_PROCEDURE_PROTOCOL.md section 6 that the tool decides.

    Rule 5 — that matching means matching as supplied to the site — is not here,
    because it is a reading instruction for the extraction model and lives in
    `blinding.placebo_matching`'s hint in `config/fields.yaml`. By the time the
    rubric runs, that judgement has already been made.
    """

    @pytest.mark.parametrize(
        ("case", "inputs", "expected"),
        [
            # Rule 1. Open-label beats everything, including a stated contradiction:
            # nobody is blinded, so unblinded staff and a matching placebo are not
            # in tension and there is nothing to ask the sponsor.
            ("rule 1, open-label", _inputs(design="open_label"), PLACEBO_OPEN_LABEL),
            (
                "rule 1 outranks the contradiction",
                _inputs(design="open_label", matching="matching_stated", staff="true"),
                PLACEBO_OPEN_LABEL,
            ),
            # Rule 3. Silent document, drug injected or infused.
            (
                "rule 3, infusion",
                _inputs(design="double_blind", forms=["infusion_iv"]),
                PLACEBO_INJECTED_NOT_STATED,
            ),
            (
                "rule 3, subcutaneous injection",
                _inputs(design="double_blind", forms=["injection_sc"]),
                PLACEBO_INJECTED_NOT_STATED,
            ),
            (
                "rule 3, intrathecal counts as injected",
                _inputs(design="double_blind", forms=["intrathecal"]),
                PLACEBO_INJECTED_NOT_STATED,
            ),
            (
                "rule 3 wins a document stating both routes",
                _inputs(design="double_blind", forms=["oral_tablet", "infusion_iv"]),
                PLACEBO_INJECTED_NOT_STATED,
            ),
            # Rule 4. Silent document, drug oral. Deliberately NOT the injected answer.
            (
                "rule 4, oral tablet",
                _inputs(design="double_blind", forms=["oral_tablet"]),
                PLACEBO_ORAL_NOT_STATED,
            ),
            (
                "rule 4, oral capsule",
                _inputs(design="double_blind", forms=["oral_capsule"]),
                PLACEBO_ORAL_NOT_STATED,
            ),
            # Rule 6. Both stated, and they cannot both be right.
            (
                "rule 6, contradiction",
                _inputs(design="double_blind", matching="matching_stated", staff="true"),
                PLACEBO_CONTRADICTION,
            ),
        ],
    )
    def test_each_rule(
        self, case: str, inputs: dict[str, ResolvedField | None], expected: str
    ) -> None:
        result = compute_placebo_assumption(inputs)
        assert result.value == expected, case

    def test_rule_two_uses_what_the_document_says_when_it_says_matching(self) -> None:
        """Rule 2. No unblinded-staff statement, so no contradiction to raise."""
        result = compute_placebo_assumption(
            _inputs(
                design="double_blind",
                forms=["infusion_iv"],
                matching="matching_stated",
                matching_quote="A matching placebo will be supplied in identical vials",
            )
        )
        assert result.value is not None
        assert "the document says the placebo matches" in result.value
        assert result.value != PLACEBO_INJECTED_NOT_STATED, (
            "the route must not override what the document states"
        )

    def test_rule_two_uses_what_the_document_says_when_it_says_not_matching(self) -> None:
        """Rule 2, and the sample protocol's own case.

        `samples/Example protocol 2.pdf` section 6.3 reads "A matching placebo
        will not be provided", which is the live test stage 5 asks for.
        """
        result = compute_placebo_assumption(
            _inputs(
                design="double_blind",
                forms=["infusion_iv"],
                matching="not_matching_stated",
                matching_quote="A matching placebo will not be provided",
            )
        )
        assert result.value is not None
        assert "does not match" in result.value
        assert "unblinded handling likely" in result.value

    def test_the_contradiction_explanation_carries_both_quotes(self) -> None:
        """Stage 5 item 10: a person must be able to see both sentences and judge.

        The value alone says to ask the sponsor; without the two quotes nobody
        knows what to ask about.
        """
        matching_quote = "The placebo will be supplied in matching vials"
        staff_quote = "limited to the unblinded pharmacy staff"
        result = compute_placebo_assumption(
            _inputs(
                design="double_blind",
                matching="matching_stated",
                staff="true",
                matching_quote=matching_quote,
                staff_quote=staff_quote,
            )
        )
        assert result.value == PLACEBO_CONTRADICTION
        assert matching_quote in result.explanation
        assert staff_quote in result.explanation

    def test_the_contradiction_rule_reads_the_enum_the_registry_offers(self) -> None:
        """The guard the comment in `config/fields.yaml` promises.

        YAML reads an unquoted `yes` as a boolean, so the enum is spelled `true`.
        If someone respells it, the contradiction rule would stop firing and no
        other test would notice — the value would quietly become the rule 3
        answer. This test fails instead.
        """
        from rfp_intake.derive.rubric import _STAFF_STATED_YES
        from rfp_intake.domain.registry import get_registry

        field_def = next(
            f for f in get_registry().fields if f.id == "blinding.unblinded_staff_stated"
        )
        offered = set(field_def.values or [])
        assert offered & _STAFF_STATED_YES, (
            f"blinding.unblinded_staff_stated offers {sorted(offered)}, none of which the "
            f"rubric treats as yes ({sorted(_STAFF_STATED_YES)})"
        )

    @pytest.mark.parametrize(
        ("case", "inputs"),
        [
            ("nothing resolved at all", _inputs()),
            ("silent, and the route is not stated", _inputs(design="double_blind")),
            (
                "silent, and the route decides nothing",
                _inputs(design="double_blind", forms=["topical"]),
            ),
            (
                "silent, and the route is itself not_specified",
                _inputs(design="double_blind", forms=["not_specified"]),
            ),
        ],
    )
    def test_an_undecidable_case_says_so_rather_than_reassuring(
        self, case: str, inputs: dict[str, ResolvedField | None]
    ) -> None:
        """A route in neither set must not fall through to the oral answer.

        The oral answer reads as "do not price unblinded staff", which would be
        reassurance nobody had evidence for. These cases ask the sponsor instead.
        """
        result = compute_placebo_assumption(inputs)
        assert result.value is not None
        assert "ask the sponsor" in result.value, case
        assert result.value != PLACEBO_ORAL_NOT_STATED, case

    def test_a_not_found_input_is_treated_as_absent(self) -> None:
        """A missing answer must not be read as a real one.

        RECONCILE writes `not_found` when no document answered. Reading that as
        an enum value is how an inference turns into a fact.
        """
        inputs = _inputs(design="double_blind", forms=["oral_tablet"])
        inputs["blinding.placebo_matching"] = _field(
            "blinding.placebo_matching", None, status="not_found", confidence=0.0
        )
        result = compute_placebo_assumption(inputs)
        assert result.value == PLACEBO_ORAL_NOT_STATED

    def test_every_answer_goes_to_needs_review(self) -> None:
        """GATE sends derived fields to needs_review; the rubric never claims confirmed."""
        for inputs in (
            _inputs(design="open_label"),
            _inputs(design="double_blind", forms=["infusion_iv"]),
            _inputs(design="double_blind", forms=["oral_tablet"]),
            _inputs(design="double_blind", matching="matching_stated", staff="true"),
            _inputs(),
        ):
            assert compute_placebo_assumption(inputs).status == "needs_review"

    def test_the_explanation_names_the_inputs_it_used(self) -> None:
        """Stage 5 item 9's last sentence, as a test."""
        result = compute_placebo_assumption(
            _inputs(design="double_blind", forms=["infusion_iv"])
        )
        assert "ip.form" in result.explanation
        assert "infusion_iv" in result.explanation

    def test_sources_come_from_every_input_that_had_one(self) -> None:
        """A reader has to be able to turn to the pages the assumption rests on."""
        result = compute_placebo_assumption(
            _inputs(design="double_blind", forms=["infusion_iv"], matching="not_matching_stated")
        )
        assert len(result.sources) == 3

    def test_confidence_is_the_weakest_input_not_the_strongest(self) -> None:
        """An assumption is only as good as the shakiest answer under it."""
        inputs = _inputs(design="double_blind", forms=["infusion_iv"])
        inputs["blinding.design"] = _field("blinding.design", "double_blind", confidence=0.9)
        inputs["ip.form"] = _field("ip.form", ["infusion_iv"], confidence=0.4)
        result = compute_placebo_assumption(inputs)
        assert result.confidence == 0.4
