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
    PLACEBO_INPUTS_DISAGREE,
    PLACEBO_OPEN_LABEL,
    PLACEBO_ORAL_NOT_STATED,
    compute_placebo_assumption,
    compute_visit_intensity,
)
from rfp_intake.domain.schemas import Provenance, ResolvedField


def _evidence(
    flags: list[str],
    confidence: float = 0.85,
    *,
    scope: str | None = None,
    page: int = 7,
    status: str = "needs_review",
) -> ResolvedField:
    return ResolvedField(
        field_id="visits.intensity_evidence",
        value=flags,
        status=status,  # type: ignore[arg-type]
        confidence=confidence,
        scope=scope,
        sources=[Provenance(doc_id="doc-1", doc_kind="protocol", page=page)],
    )


def _visit(*rows: ResolvedField) -> dict[str, list[ResolvedField]]:
    return {"visits.intensity_evidence": list(rows)}


class TestComputeVisitIntensity:
    def test_no_evidence_field_is_not_specified(self) -> None:
        result = compute_visit_intensity(_visit())
        assert result.status == "not_specified"
        assert result.value == "not_specified"

    def test_none_found_flag_is_not_specified(self) -> None:
        result = compute_visit_intensity(_visit(_evidence(["none_found"])))
        assert result.status == "not_specified"

    def test_low_score(self) -> None:
        # questionnaires(1) = 1 -> low
        result = compute_visit_intensity(_visit(_evidence(["questionnaires"])))
        assert result.value == "low"
        assert result.status == "needs_review"

    def test_moderate_score(self) -> None:
        # biomarker_sampling(1) + ecgs(1) + safety_labs(1) + questionnaires(1) = 4 -> moderate
        flags = ["biomarker_sampling", "ecgs", "safety_labs", "questionnaires"]
        result = compute_visit_intensity(_visit(_evidence(flags)))
        assert result.value == "moderate"

    def test_high_score(self) -> None:
        # pk_pd_sampling(2) + imaging(2) + infusion_observation_period(2) + overnight_stay(2) = 8
        flags = ["pk_pd_sampling", "imaging", "infusion_observation_period", "overnight_stay"]
        result = compute_visit_intensity(_visit(_evidence(flags)))
        assert result.value == "high"

    def test_confidence_and_sources_carried_from_evidence(self) -> None:
        evidence = _evidence(["imaging"], confidence=0.77)
        result = compute_visit_intensity(_visit(evidence))
        assert result.confidence == 0.77
        assert result.sources == evidence.sources

    def test_explanation_lists_contributing_flags(self) -> None:
        result = compute_visit_intensity(_visit(_evidence(["imaging", "ecgs"])))
        assert "imaging" in result.explanation
        assert "ecgs" in result.explanation

    def test_evidence_not_yet_gated_is_still_read(self) -> None:
        """RECONCILE always writes status=needs_review pre-GATE; DERIVE runs before GATE."""
        evidence = _evidence(["imaging"])
        assert evidence.status == "needs_review"
        result = compute_visit_intensity(_visit(evidence))
        assert result.status == "needs_review"

    def test_confirmed_evidence_is_read_too(self) -> None:
        """A saved run's rows are mostly `confirmed`; re-deriving from them must score them."""
        result = compute_visit_intensity(_visit(_evidence(["imaging"], status="confirmed")))
        assert result.value == "low"

    def test_not_found_evidence_status_is_not_specified(self) -> None:
        evidence = ResolvedField(
            field_id="visits.intensity_evidence", value=None, status="not_found", confidence=0.0,
        )
        result = compute_visit_intensity(_visit(evidence))
        assert result.status == "not_specified"


class TestVisitIntensityCombinesEveryRow:
    """The bug fixed 2026-10-11: DERIVE kept only the last evidence row.

    In r-20261011-fix1 the study-wide row named nine kinds of evidence (score
    14, high) and a later "Months 1-3" row named one (score 2, low); the report
    said low. 13 of the 29 saved runs read lower than all their rows together.
    """

    def test_a_narrower_row_adds_to_the_study_wide_row_rather_than_replacing_it(self) -> None:
        wide = _evidence(
            ["pk_pd_sampling", "imaging", "infusion_observation_period", "overnight_stay"],
            page=40,
        )
        narrow = _evidence(["questionnaires"], scope="Months 1-3", page=41)
        result = compute_visit_intensity(_visit(wide, narrow))
        assert result.value == "high"
        assert result.explanation.startswith("score=9 ")

    def test_the_order_of_the_rows_does_not_matter(self) -> None:
        wide = _evidence(["pk_pd_sampling", "imaging", "infusion_observation_period", "ecgs"])
        narrow = _evidence(["questionnaires"], scope="Months 1-3")
        assert (
            compute_visit_intensity(_visit(wide, narrow)).value
            == compute_visit_intensity(_visit(narrow, wide)).value
            == "high"
        )

    def test_a_flag_named_by_two_rows_is_counted_once(self) -> None:
        # imaging(2) + ecgs(1) = 3 -> low; counting imaging twice would make it 5, moderate.
        first = _evidence(["imaging", "ecgs"])
        second = _evidence(["imaging"], scope="Months 1-3")
        result = compute_visit_intensity(_visit(first, second))
        assert result.value == "low"
        assert result.explanation.startswith("score=3 ")

    def test_confidence_is_the_weakest_row(self) -> None:
        result = compute_visit_intensity(
            _visit(_evidence(["imaging"], 0.9), _evidence(["ecgs"], 0.6, scope="Months 1-3"))
        )
        assert result.confidence == 0.6

    def test_sources_are_every_row_s_pages_once_each(self) -> None:
        result = compute_visit_intensity(
            _visit(
                _evidence(["imaging"], page=40),
                _evidence(["ecgs"], scope="Months 1-3", page=41),
                _evidence(["ecgs"], scope="Months 4-6", page=40),
            )
        )
        assert [p.page for p in result.sources] == [40, 41]

    def test_the_inputs_it_does_not_score_are_not_cited(self) -> None:
        """CLAUDE.md open item 4: a derived row cites only what its answer rests on.
        Every evidence row is combined into the rating, so each is cited; the
        frequency and count inputs are not scored, so neither is."""
        inputs = _visit(_evidence(["imaging"], page=40))
        inputs["visits.frequency_by_period"] = [
            _field("visits.frequency_by_period", "weekly", page=90)
        ]
        inputs["visits.total_count"] = [_field("visits.total_count", 12, page=91)]
        result = compute_visit_intensity(inputs)
        assert [p.page for p in result.sources] == [40]

    def test_the_explanation_names_the_rows_it_combined(self) -> None:
        result = compute_visit_intensity(
            _visit(_evidence(["imaging"]), _evidence(["ecgs"], scope="Months 1-3"))
        )
        assert 'combined 2 rows: no scope stated, "Months 1-3"' in result.explanation

    def test_a_scope_shared_by_several_rows_is_named_once_with_a_count(self) -> None:
        result = compute_visit_intensity(
            _visit(
                _evidence(["imaging"]),
                _evidence(["ecgs"]),
                _evidence(["safety_labs"], scope="total"),
                _evidence(["questionnaires"]),
            )
        )
        assert 'combined 4 rows: no scope stated (3), "total"' in result.explanation

    def test_a_none_found_row_does_not_hide_a_row_with_evidence(self) -> None:
        result = compute_visit_intensity(
            _visit(_evidence(["imaging"]), _evidence(["none_found"], scope="Months 1-3"))
        )
        assert result.value == "low"
        assert result.status == "needs_review"


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
        value=value,
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
) -> dict[str, list[ResolvedField]]:
    """The four `derived_from` inputs, any of them absent.

    Each input is every row DERIVE has for it; an absent input is an empty list,
    which is what DERIVE passes when the field was never resolved.
    """
    return {
        "blinding.design": (
            [_field("blinding.design", design, quote=design_quote)] if design else []
        ),
        "ip.form": [_field("ip.form", forms)] if forms else [],
        "blinding.placebo_matching": (
            [_field("blinding.placebo_matching", matching, quote=matching_quote)]
            if matching
            else []
        ),
        "blinding.unblinded_staff_stated": (
            [_field("blinding.unblinded_staff_stated", staff, quote=staff_quote)]
            if staff
            else []
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
        self, case: str, inputs: dict[str, list[ResolvedField]], expected: str
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
        self, case: str, inputs: dict[str, list[ResolvedField]]
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
        inputs["blinding.placebo_matching"] = [
            _field("blinding.placebo_matching", None, status="not_found", confidence=0.0)
        ]
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

    def test_sources_are_only_the_inputs_the_answer_rests_on(self) -> None:
        """A reader has to be able to turn to the pages the assumption rests on, and
        only those (CLAUDE.md open item 4)."""
        inputs = _inputs()
        inputs["blinding.design"] = [_field("blinding.design", "double_blind", page=4)]
        inputs["ip.form"] = [_field("ip.form", ["infusion_iv"], page=12)]
        inputs["blinding.placebo_matching"] = [
            _field("blinding.placebo_matching", "not_matching_stated", page=30)
        ]
        result = compute_placebo_assumption(inputs)
        assert [p.page for p in result.sources] == [30]
        assert "looked at but not used: blinding.design=double_blind; ip.form=" in (
            result.explanation
        )

    def test_an_input_nobody_settled_is_named_but_not_cited(self) -> None:
        """r-20261009-225414: the documents disagreed on whether the placebo matches,
        so the route decided, and RFP p.3's "Matching placebo" was cited all the same."""
        inputs = _inputs(design="double_blind")
        inputs["ip.form"] = [_field("ip.form", ["infusion_iv"], page=54)]
        inputs["blinding.placebo_matching"] = [
            _field(
                "blinding.placebo_matching", None, confidence=0.0, page=3,
                quote="Placebo: Matching placebo for NEOD001",
            )
        ]
        result = compute_placebo_assumption(inputs)
        assert result.value == PLACEBO_INJECTED_NOT_STATED
        assert [p.page for p in result.sources] == [54]
        assert result.explanation.startswith("ip.form=['infusion_iv']; looked at but not used: ")
        assert "Matching placebo for NEOD001" in result.explanation
        assert result.confidence == 0.0, "the unsettled input still lowers the confidence"

    def test_the_contradiction_cites_both_sides(self) -> None:
        inputs = _inputs(design="double_blind")
        inputs["blinding.placebo_matching"] = [
            _field("blinding.placebo_matching", "matching_stated", page=30)
        ]
        inputs["blinding.unblinded_staff_stated"] = [
            _field("blinding.unblinded_staff_stated", "true", page=31)
        ]
        result = compute_placebo_assumption(inputs)
        assert result.value == PLACEBO_CONTRADICTION
        assert [p.page for p in result.sources] == [30, 31]

    def test_a_split_staff_answer_cites_the_matching_that_made_it_matter(self) -> None:
        inputs = _inputs(design="double_blind")
        inputs["blinding.placebo_matching"] = [
            _field("blinding.placebo_matching", "matching_stated", page=30)
        ]
        inputs["blinding.unblinded_staff_stated"] = [
            _field("blinding.unblinded_staff_stated", "true", page=31),
            _field("blinding.unblinded_staff_stated", "false", page=32),
        ]
        result = compute_placebo_assumption(inputs)
        assert result.value == PLACEBO_INPUTS_DISAGREE
        assert [p.page for p in result.sources] == [30, 31, 32]

    @pytest.mark.parametrize(
        ("case", "rows", "pages"),
        [
            ("rule 1, open-label", {"design": ["open_label"], "staff": ["true"]}, [1]),
            ("split design", {"design": ["open_label", "double_blind"]}, [1, 11]),
            ("split matching", {"matching": ["matching_stated", "not_matching_stated"]}, [2, 12]),
            ("rule 2, matching", {"matching": ["matching_stated"], "staff": ["false"]}, [2]),
            ("split route", {"forms": [["oral_tablet"], ["infusion_iv"]]}, [4, 14]),
            ("rule 3, injected", {"forms": [["infusion_iv"]]}, [4]),
            ("rule 4, oral", {"forms": [["oral_tablet"]]}, [4]),
            ("no route stated", {"forms": [None]}, []),
            ("route decides nothing", {"forms": [["topical"]]}, [4]),
        ],
    )
    def test_each_rule_cites_only_the_inputs_it_used(
        self, case: str, rows: dict[str, list[object]], pages: list[int]
    ) -> None:
        """Every input is present on its own page (design 1, matching 2, staff 3,
        route 4; a second row 10 pages on), and an unsettled matching row sits on
        page 2 wherever the case gives none, like RFP p.3 in r-20261009-225414."""
        ids = {
            "design": ("blinding.design", 1), "matching": ("blinding.placebo_matching", 2),
            "staff": ("blinding.unblinded_staff_stated", 3), "forms": ("ip.form", 4),
        }
        inputs: dict[str, list[ResolvedField]] = {}
        for key, (field_id, page) in ids.items():
            values = rows.get(key, ["double_blind"] if key == "design" else [None])
            inputs[field_id] = [
                _field(
                    field_id, v, page=page + 10 * i,
                    status="not_found" if v is None and key == "forms" else "needs_review",
                )
                for i, v in enumerate(values)
            ]
        result = compute_placebo_assumption(inputs)
        assert [p.page for p in result.sources] == pages, case

    def test_an_absent_input_is_not_named(self) -> None:
        result = compute_placebo_assumption(
            _inputs(design="double_blind", forms=["infusion_iv"])
        )
        assert "placebo_matching" not in result.explanation
        assert "unblinded_staff_stated" not in result.explanation

    def test_a_page_two_inputs_share_is_listed_once(self) -> None:
        result = compute_placebo_assumption(
            _inputs(design="double_blind", forms=["infusion_iv"], matching="not_matching_stated")
        )
        assert [p.page for p in result.sources] == [12]

    def test_confidence_is_the_weakest_input_not_the_strongest(self) -> None:
        """An assumption is only as good as the shakiest answer under it."""
        inputs = _inputs(design="double_blind", forms=["infusion_iv"])
        inputs["blinding.design"] = [_field("blinding.design", "double_blind", confidence=0.9)]
        inputs["ip.form"] = [_field("ip.form", ["infusion_iv"], confidence=0.4)]
        result = compute_placebo_assumption(inputs)
        assert result.confidence == 0.4


class TestPlaceboInputsWithSeveralRows:
    """The rule approved 2026-10-11: rows that agree are one answer; rows that
    disagree decide nothing, and the sponsor is asked."""

    def test_rows_that_agree_are_used(self) -> None:
        inputs = _inputs(design="double_blind")
        inputs["ip.form"] = [
            _field("ip.form", ["infusion_iv"], page=12),
            _field("ip.form", ["INFUSION_IV"], page=30),
        ]
        result = compute_placebo_assumption(inputs)
        assert result.value == PLACEBO_INJECTED_NOT_STATED
        assert {p.page for p in result.sources} == {12, 30}

    def test_list_rows_in_a_different_order_agree(self) -> None:
        inputs = _inputs(design="double_blind")
        inputs["ip.form"] = [
            _field("ip.form", ["oral_tablet", "oral_capsule"]),
            _field("ip.form", ["oral_capsule", "oral_tablet"]),
        ]
        assert compute_placebo_assumption(inputs).value == PLACEBO_ORAL_NOT_STATED

    def test_rows_that_disagree_ask_the_sponsor_and_name_every_answer(self) -> None:
        inputs = _inputs(design="double_blind")
        inputs["ip.form"] = [
            _field("ip.form", ["oral_tablet"], page=12),
            _field("ip.form", ["infusion_iv"], page=30),
        ]
        result = compute_placebo_assumption(inputs)
        assert result.value == PLACEBO_INPUTS_DISAGREE
        assert "oral_tablet" in result.explanation
        assert "infusion_iv" in result.explanation

    def test_a_split_design_asks_the_sponsor_even_with_open_label_among_the_rows(self) -> None:
        """Choosing open-label from two answers would end the check on a guess."""
        inputs = _inputs()
        inputs["blinding.design"] = [
            _field("blinding.design", "open_label"),
            _field("blinding.design", "double_blind"),
        ]
        assert compute_placebo_assumption(inputs).value == PLACEBO_INPUTS_DISAGREE

    def test_a_split_the_rule_reached_does_not_need_does_not_matter(self) -> None:
        """On an open-label study the route decides nothing, so its rows may differ."""
        inputs = _inputs(design="open_label")
        inputs["ip.form"] = [
            _field("ip.form", ["oral_tablet"]),
            _field("ip.form", ["infusion_iv"]),
        ]
        assert compute_placebo_assumption(inputs).value == PLACEBO_OPEN_LABEL

    def test_a_split_staff_answer_matters_only_when_matching_is_stated(self) -> None:
        staff_rows = [
            _field("blinding.unblinded_staff_stated", "true"),
            _field("blinding.unblinded_staff_stated", "false"),
        ]
        stated = _inputs(design="double_blind", matching="matching_stated")
        stated["blinding.unblinded_staff_stated"] = staff_rows
        assert compute_placebo_assumption(stated).value == PLACEBO_INPUTS_DISAGREE

        silent = _inputs(design="double_blind", forms=["oral_tablet"])
        silent["blinding.unblinded_staff_stated"] = staff_rows
        assert compute_placebo_assumption(silent).value == PLACEBO_ORAL_NOT_STATED

    def test_a_not_found_row_beside_a_real_answer_is_not_a_disagreement(self) -> None:
        inputs = _inputs(design="double_blind")
        inputs["ip.form"] = [
            _field("ip.form", None, status="not_found", confidence=0.0),
            _field("ip.form", ["infusion_iv"]),
        ]
        assert compute_placebo_assumption(inputs).value == PLACEBO_INJECTED_NOT_STATED
