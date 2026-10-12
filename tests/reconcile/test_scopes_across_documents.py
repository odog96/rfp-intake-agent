"""RECONCILE matches the arms of `design.parts` across documents by treatment.

The rule Oliver approved on 2026-10-12, after run r-20261009-225414 listed four
arms for a two-arm study: the protocol amendment's arms were scoped
"arm:NEOD001" and "arm:Placebo", the RFP's "arm:Group 1" and "arm:Group 2". The
design.parts hint in config/fields.yaml now asks the extraction model to scope an
arm by its treatment, and `check_scopes_across_documents` turns on two checks:

- an entry whose own value and quote do not say what its scope names is not
  matched with other documents, and its row says so (step 3);
- when more than one document lists arms, an arm only one of them lists says
  which document lists it.
"""

from __future__ import annotations

import os
from typing import Literal

import pytest

from rfp_intake.domain.schemas import FieldRecord, Provenance, ResolvedField, RunState

DocKind = Literal["rfp", "protocol", "amendment", "soa", "other"]

AMENDMENT = "doc-62022c6f"
RFP = "doc-62809b04"


@pytest.fixture(autouse=True)
def _real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    from rfp_intake.domain.registry import get_registry
    get_registry.cache_clear()


def _entry(
    value: str, scope: str, doc_id: str, page: int, quote: str | None = None,
    doc_kind: DocKind = "amendment",
) -> FieldRecord:
    r = FieldRecord(
        field_id="design.parts",
        group="study_design",
        raw_value=value,
        quote=quote if quote is not None else value,
        provenance=Provenance(doc_id=doc_id, doc_kind=doc_kind, page=page),
        confidence=0.95,
        scope=scope,
    )
    r.value = value  # bypass NORMALIZE — RECONCILE reads .value directly
    return r


def _rfp(value: str, scope: str, page: int = 3, quote: str | None = None) -> FieldRecord:
    return _entry(value, scope, RFP, page, quote, doc_kind="rfp")


def _parts(records: list[FieldRecord]) -> list[ResolvedField]:
    from rfp_intake.reconcile import reconcile_node

    result = reconcile_node(RunState(run_id="t", records=records))
    return [rf for rf in result["resolved"] if rf.field_id == "design.parts"]


def _pages(rf: ResolvedField) -> set[tuple[str, int]]:
    return {(p.doc_id, p.page) for p in rf.sources}


def run_20261009_225414_records() -> list[FieldRecord]:
    """The design.parts entries of run r-20261009-225414, as its extraction.json
    saved them, with the RFP's two entries scoped by treatment as the new hint asks
    in place of the "arm:Group 1" and "arm:Group 2" the run gave them.

    extraction.json keeps resolved rows, not records, so the record behind each
    value is rebuilt from its row: the values and pages are the run's, in the
    order the row lists them. Only one quote per row is saved; that quote is
    used for the record it plausibly came from, and the other records quote
    their own value. Run r-20261011-fix1, on the same two documents, shows the
    same four arm rows; r-20261009-225414 was the run the brief was found on.
    """
    return [
        _entry("NEOD001 plus standard of care arm (~130 subjects)", "arm:NEOD001",
               AMENDMENT, 12,
               "Newly diagnosed subjects with AL amyloidosis will be randomized in a 1:1 "
               "ratio to NEOD001 or placebo."),
        _entry("NEOD001 arm: NEOD001 24 mg/kg IV once every 28 days, with concomitant "
               "standard of care chemotherapy including bortezomib", "arm:NEOD001",
               AMENDMENT, 19),
        _entry("NEOD001 arm", "arm:NEOD001", AMENDMENT, 42),
        _entry("Placebo plus standard of care arm (~130 subjects)", "arm:Placebo",
               AMENDMENT, 15,
               "Up to 260 subjects will be enrolled in the study, with approximately "
               "130 subjects per arm."),
        _entry("Placebo arm: Normal saline IV once every 28 days, with concomitant "
               "standard of care chemotherapy including bortezomib", "arm:Placebo",
               AMENDMENT, 20),
        _entry("Placebo arm", "arm:Placebo", AMENDMENT, 42),
        _rfp("Group 1: NEOD001 + standard of care — 130 subjects",
             "arm:NEOD001 + standard of care",
             quote="Group 1: NEOD001 + standard of care    130 subjects"),
        _rfp("Group 2: Placebo + standard of care — 130 subjects",
             "arm:Placebo + standard of care",
             quote="Group 2: Placebo + standard of care     130 subjects"),
    ]


class TestRun20261009225414:
    def test_two_arms_each_citing_both_documents(self) -> None:
        parts = _parts(run_20261009_225414_records())

        assert len(parts) == 2
        by_scope = {rf.scope: rf for rf in parts}
        assert set(by_scope) == {"arm:NEOD001", "arm:Placebo"}
        assert _pages(by_scope["arm:NEOD001"]) == {
            (AMENDMENT, 12), (AMENDMENT, 19), (AMENDMENT, 42), (RFP, 3)
        }
        assert _pages(by_scope["arm:Placebo"]) == {
            (AMENDMENT, 15), (AMENDMENT, 20), (AMENDMENT, 42), (RFP, 3)
        }
        # Both documents list both arms, so neither arm has a note.
        assert all(rf.review_note is None for rf in parts)

    def test_the_rfp_group_labels_the_run_gave_stay_four_rows_and_say_so(self) -> None:
        # What the run itself did, before the hint changed: the RFP's entries
        # scoped by their labels. The check does not merge them — a label says
        # nothing about the arm — but the difference is no longer quiet.
        records = run_20261009_225414_records()
        records[6].scope = "arm:Group 1"
        records[7].scope = "arm:Group 2"

        notes = {rf.scope: rf.review_note for rf in _parts(records)}

        assert notes == {
            "arm:NEOD001": f"Listed only in {AMENDMENT}, not in {RFP}.",
            "arm:Placebo": f"Listed only in {AMENDMENT}, not in {RFP}.",
            "arm:Group 1": f"Listed only in {RFP}, not in {AMENDMENT}.",
            "arm:Group 2": f"Listed only in {RFP}, not in {AMENDMENT}.",
        }


class TestDifferentTreatmentsAreNotMerged:
    def test_an_arm_one_document_drops_is_its_own_row_with_a_note(self) -> None:
        # The case the "listed in one document only" note exists for: an
        # amendment replacing an arm the RFP still lists.
        parts = _parts([
            _entry("Arm A: DrugX 10 mg daily", "arm:DrugX", AMENDMENT, 4),
            _entry("Arm B: DrugY 5 mg daily", "arm:DrugY", AMENDMENT, 4),
            _rfp("Arm A: DrugX 10 mg daily", "arm:DrugX"),
            _rfp("Arm B: Placebo", "arm:Placebo"),
        ])

        by_scope = {rf.scope: rf for rf in parts}
        assert set(by_scope) == {"arm:DrugX", "arm:DrugY", "arm:Placebo"}
        assert by_scope["arm:DrugX"].review_note is None
        assert _pages(by_scope["arm:DrugX"]) == {(AMENDMENT, 4), (RFP, 3)}
        assert by_scope["arm:DrugY"].review_note == f"Listed only in {AMENDMENT}, not in {RFP}."
        assert by_scope["arm:Placebo"].review_note == f"Listed only in {RFP}, not in {AMENDMENT}."

    def test_labels_that_look_alike_do_not_merge_arms_with_different_treatments(self) -> None:
        # Both documents call an arm "Arm B", but they mean different arms.
        # The scopes name the treatment, so "Arm B" plays no part in the match.
        parts = _parts([
            _entry("Arm B: DrugY 5 mg daily", "arm:DrugY", AMENDMENT, 4),
            _rfp("Arm B: Placebo to match DrugY", "arm:Placebo"),
        ])

        assert sorted(rf.scope or "" for rf in parts) == ["arm:DrugY", "arm:Placebo"]
        assert all(rf.review_note for rf in parts)

    def test_different_labels_do_not_keep_one_treatment_apart(self) -> None:
        # The other direction: "Arm 1" in one document and "Group A" in the
        # other are one arm when both give it the same treatment.
        [arm] = _parts([
            _entry("Arm 1: DrugX 10 mg daily", "arm:DrugX", AMENDMENT, 4),
            _rfp("Group A: DrugX 10 mg daily", "arm:DrugX"),
        ])
        assert _pages(arm) == {(AMENDMENT, 4), (RFP, 3)}
        assert arm.review_note is None


class TestAnEntryThatDoesNotNameItsScope:
    def test_saline_scoped_placebo_without_the_word_placebo_is_not_matched(self) -> None:
        # The control-arm case of the 2026-10-12 approval: the scope says
        # placebo, as the hint asks, but this entry's own words only say saline.
        saline = _entry("Control arm: normal saline IV once every 28 days", "arm:Placebo",
                        AMENDMENT, 20, "Subjects receive normal saline by IV infusion.")
        parts = _parts([saline, _rfp("Group 2: Placebo + standard of care", "arm:Placebo")])

        assert len(parts) == 2
        unmatched = next(rf for rf in parts if rf.sources[0].doc_id == AMENDMENT)
        matched = next(rf for rf in parts if rf.sources[0].doc_id == RFP)
        assert unmatched.scope == f"arm:Placebo (not matched, {AMENDMENT})"
        assert unmatched.review_note == (
            'Not matched with other documents: this entry\'s own words do not say '
            '"Placebo", which its scope names.'
        )
        # The RFP's placebo arm is now the only placebo arm listed.
        assert matched.scope == "arm:Placebo"
        assert matched.review_note == f"Listed only in {RFP}, not in {AMENDMENT}."

    def test_the_same_entry_matches_when_its_value_says_placebo(self) -> None:
        saline = _entry("Placebo arm: normal saline IV once every 28 days", "arm:Placebo",
                        AMENDMENT, 20, "Subjects receive normal saline by IV infusion.")
        [arm] = _parts([saline, _rfp("Group 2: Placebo + standard of care", "arm:Placebo")])
        assert _pages(arm) == {(AMENDMENT, 20), (RFP, 3)}
        assert arm.review_note is None

    def test_the_same_entry_matches_when_its_quote_says_placebo(self) -> None:
        saline = _entry("Control arm: normal saline IV once every 28 days", "arm:Placebo",
                        AMENDMENT, 20, "Placebo (normal saline) will be given by IV infusion.")
        [arm] = _parts([saline, _rfp("Group 2: Placebo + standard of care", "arm:Placebo")])
        assert _pages(arm) == {(AMENDMENT, 20), (RFP, 3)}


class TestOnlyWhereConfigSaysSo:
    def test_one_document_alone_gets_no_listed_only_note(self) -> None:
        parts = _parts([
            _entry("Arm A: DrugX", "arm:DrugX", AMENDMENT, 4),
            _entry("Arm B: Placebo", "arm:Placebo", AMENDMENT, 4),
        ])
        assert all(rf.review_note is None for rf in parts)

    def test_a_whole_study_row_is_not_a_part(self) -> None:
        parts = _parts([
            _entry("Two-arm parallel design", "total", AMENDMENT, 4),
            _entry("Arm A: DrugX", "arm:DrugX", AMENDMENT, 4),
            _rfp("Arm A: DrugX", "arm:DrugX"),
        ])
        assert all(rf.review_note is None for rf in parts)

    def test_a_field_without_the_key_is_untouched(self) -> None:
        # ops.subjects_total does not set check_scopes_across_documents: an
        # entry whose words do not repeat its scope still groups by scope.
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.reconcile import reconcile_node

        assert not get_registry().get_field("ops.subjects_total").check_scopes_across_documents
        records = [
            FieldRecord(field_id="ops.subjects_total", group="operational_metrics",
                        raw_value="130", value=130, quote="130 subjects per arm",
                        provenance=Provenance(doc_id=doc, doc_kind="rfp", page=3),
                        confidence=0.9, scope="arm:Placebo")
            for doc in (AMENDMENT, RFP)
        ]
        result = reconcile_node(RunState(run_id="t", records=records))
        [rf] = [r for r in result["resolved"] if r.field_id == "ops.subjects_total"]
        assert rf.scope == "arm:Placebo"
        assert rf.review_note is None
        assert len(rf.sources) == 2
