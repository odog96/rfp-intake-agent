"""reconcile/entries.py: one entry's keys merged across the documents that
state it. Fix 1 of the 2026-10-12 brief."""

from __future__ import annotations

import os
from typing import Any, Literal

import pytest

from rfp_intake.domain.registry import FieldDef, get_registry
from rfp_intake.domain.schemas import FieldRecord, Provenance
from rfp_intake.reconcile.entries import MergedEntry, group_entries, merge_entry, same_key

DocKind = Literal["rfp", "protocol", "amendment"]


@pytest.fixture(autouse=True)
def _real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    get_registry.cache_clear()


def _rec(value: Any, doc: str, kind: DocKind, page: int,
         confidence: float = 0.9) -> FieldRecord:
    r = FieldRecord(
        field_id="design.parts", group="study_design", raw_value=str(value), quote="q",
        provenance=Provenance(doc_id=doc, doc_kind=kind, page=page), confidence=confidence,
        scope="arm:NEOD001",
    )
    r.value = value
    return r


def _parts() -> FieldDef:
    return get_registry().get_field("design.parts")


def test_same_key_ignores_case_and_spacing() -> None:
    assert same_key("  NEOD001  plus\nSoC ") == same_key("neod001 plus soc")
    assert same_key(130) == "130"


def test_two_documents_agreeing_on_every_counted_key_agree() -> None:
    merged = merge_entry(_parts(), [
        _rec({"name": "NEOD001 plus standard of care", "kind": "arm", "dose": "24 mg/kg",
              "n_subjects": 130}, "d-am", "amendment", 15),
        _rec({"name": "NEOD001 + SoC", "kind": "arm", "document_label": "Group 1",
              "n_subjects": 130}, "d-rfp", "rfp", 3),
    ])
    assert merged.agrees
    assert merged.docs == ["d-am", "d-rfp"]
    # Keys come in schema order, only those some record states.
    assert list(merged.keys) == ["name", "kind", "document_label", "dose", "n_subjects"]
    assert merged.keys["dose"].stated_by == ["d-am"]
    assert merged.keys["n_subjects"].agreed
    # The text key differs without the documents disagreeing; the protocol-side
    # document is shown, by design.parts' source_priority.
    assert not merged.keys["name"].agreed
    assert merged.value("name") == "NEOD001 plus standard of care"
    assert merged.value("schedule") is None


def test_one_answer_needs_every_key_two_documents_state_but_the_names_to_agree() -> None:
    def merged(rfp_dose: str) -> MergedEntry:
        return merge_entry(_parts(), [
            _rec({"name": "NEOD001 plus SoC", "dose": "24 mg/kg", "n_subjects": 130},
                 "d-am", "amendment", 15),
            _rec({"name": "NEOD001 + SoC", "dose": rfp_dose, "n_subjects": 130},
                 "d-rfp", "rfp", 3),
        ])

    assert merged("24 mg/kg").one_answer(["name"])
    assert not merged("12 mg/kg").one_answer(["name"])
    # The names differ, so without naming them the entry is not one answer.
    assert not merged("24 mg/kg").one_answer([])
    # Text keys never stop `agrees`, which the display's agree lines read.
    assert merged("12 mg/kg").agrees
    # The keys the report prints as "documents differ", as GATE reads them.
    assert merged("12 mg/kg").differing(["name"]) == ["dose"]
    assert merged("12 mg/kg").differing(None) == []


def test_a_counted_key_with_two_values_disagrees_and_keeps_both() -> None:
    merged = merge_entry(_parts(), [
        _rec({"n_subjects": 130}, "d-am", "amendment", 15),
        _rec({"n_subjects": 118}, "d-rfp", "rfp", 3),
    ])
    assert not merged.agrees
    by_doc = merged.keys["n_subjects"].by_doc
    assert {d: [v for v, _ in vs] for d, vs in by_doc.items()} == {
        "d-am": [130], "d-rfp": [118],
    }


def test_one_document_saying_two_values_keeps_the_first_record_of_each() -> None:
    first = _rec({"n_subjects": 130}, "d-am", "amendment", 15)
    again = _rec({"n_subjects": 130}, "d-am", "amendment", 43)
    other = _rec({"n_subjects": 131}, "d-am", "amendment", 50)
    merged = merge_entry(_parts(), [first, again, other])
    [(v1, r1), (v2, r2)] = merged.keys["n_subjects"].by_doc["d-am"]
    assert (v1, v2) == (130, 131)
    assert r1 is first and r2 is other
    assert not merged.agrees


def test_within_one_document_the_most_confident_record_is_shown() -> None:
    low = _rec({"dose": "24 mg/kg"}, "d-am", "amendment", 15, confidence=0.7)
    high = _rec({"dose": "24 mg/kg IV infusion"}, "d-am", "amendment", 16, confidence=0.95)
    merged = merge_entry(_parts(), [low, high])
    assert merged.keys["dose"].shown is high
    assert merged.shown_records == [high]


def test_within_one_document_the_record_whose_quote_states_the_key_is_shown() -> None:
    """Run r-20261123-oct29-run4: the p.15 record gave the NEOD001 arm a dose its
    passage, about enrolment, never states, and was shown for being the more
    confident."""
    enrolment = _rec({"dose": "administered intravenously", "n_subjects": 130},
                     "d-am", "amendment", 15, confidence=0.95)
    enrolment.quote = (
        "Up to 260 subjects will be enrolled in the study, with approximately 130 subjects "
        "per arm."
    )
    drug = _rec({"dose": "NEOD001 (24 mg/kg) IV infusion"}, "d-am", "amendment", 19,
                confidence=0.85)
    drug.quote = "Study drug consists of NEOD001 (24 mg/kg) or placebo."
    for order in ([enrolment, drug], [drug, enrolment]):
        merged = merge_entry(_parts(), order)
        assert merged.value("dose") == "NEOD001 (24 mg/kg) IV infusion"
        # The count is still the enrolment passage's, which states it.
        assert merged.keys["n_subjects"].shown is enrolment


def test_a_record_that_is_not_an_entry_states_no_key() -> None:
    merged = merge_entry(_parts(), [
        _rec("Arm A: NEOD001", "d-rfp", "rfp", 3),
        _rec({"n_subjects": 130}, "d-am", "amendment", 15),
    ])
    assert merged.docs == ["d-am"]
    assert list(merged.keys) == ["n_subjects"]


def test_group_entries_by_their_one_per_keys() -> None:
    a = _rec({"period": "treatment", "label": "Months 1–3"}, "d-am", "amendment", 26)
    b = _rec({"period": "treatment", "label": "Month 4 onwards"}, "d-am", "amendment", 26)
    c = _rec({"period": "Treatment", "label": "months  1–3"}, "d-rfp", "rfp", 4)
    d = _rec({"period": "screening"}, "d-am", "amendment", 26)
    e = _rec("not an entry", "d-am", "amendment", 26)
    assert group_entries([a, b, c, d, e], ["period", "label"]) == [[a, c], [b], [d]]


def _visit(value: Any, page: int, confidence: float, section: str | None) -> FieldRecord:
    r = FieldRecord(
        field_id="visits.frequency_by_period", group="visits", raw_value=str(value),
        quote="q", confidence=confidence,
        provenance=Provenance(doc_id="d-am", doc_kind="amendment", page=page, section=section),
    )
    r.value = value
    return r


def test_a_record_from_the_schedule_table_outranks_a_more_confident_summary() -> None:
    # Addition 1 to stage 6 of the 2026-10-12 plan: the field's prefer_sections.
    fields = get_registry().get_field("visits.frequency_by_period")
    summary = _visit({"period": "follow_up", "frequency": "every 3 months"}, 12, 0.95,
                     "1.2 Synopsis")
    table = _visit({"period": "follow_up", "frequency": "every 12 weeks"}, 79, 0.8,
                   "Appendix 1 Schedule of Assessments")
    for order in ([summary, table], [table, summary]):
        merged = merge_entry(fields, order)
        assert merged.keys["frequency"].shown is table
        assert merged.value("frequency") == "every 12 weeks"


def test_with_no_schedule_record_the_most_confident_is_still_shown() -> None:
    fields = get_registry().get_field("visits.frequency_by_period")
    low = _visit({"period": "follow_up", "frequency": "every 3 months"}, 12, 0.7, None)
    high = _visit({"period": "follow_up", "frequency": "every 12 weeks"}, 42, 0.9,
                  "7.1.8 Follow-up")
    assert merge_entry(fields, [low, high]).keys["frequency"].shown is high
