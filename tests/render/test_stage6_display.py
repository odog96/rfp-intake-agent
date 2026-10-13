"""The report side of stage 6 of the 2026-10-12 plan: fixes 4, 5 and 11, and
addition 3.

- Fix 4: a variable no document gives prints its `when_not_found` note, the
  document's own sentence saying why, and where to look instead.
- Fix 5: a record ADJUDICATE called off-topic has no row and its own marked
  line in Appendix A; wordings tied for the most entries are all shown.
- Fix 11: Appendix A quotes each page with its own words.
- Addition 3: a label the documents worded starts with a capital letter, in
  the variable table, the Disagreements, Appendix A and Appendix B.

Every state here goes through `assert_nothing_lost`.
"""

from __future__ import annotations

import os

import pytest

from rfp_intake.adjudicate import AdjudicationResult, _handle_not_a_conflict, _handle_reconcilable
from rfp_intake.domain.registry import get_registry
from rfp_intake.domain.schemas import Contradiction, FieldRecord, ResolvedField, RunState
from rfp_intake.render.report_model import ReportModel, Row, _capital, build_report_model
from tests.render.test_report_model import _prov, _rf, assert_nothing_lost


@pytest.fixture(autouse=True)
def _real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    get_registry.cache_clear()


def _model(resolved: list[ResolvedField], contradictions: list[Contradiction] | None = None,
           ) -> ReportModel:
    state = RunState(run_id="r-1", resolved=resolved, contradictions=contradictions or [])
    model = build_report_model(state, get_registry())
    assert_nothing_lost(state, get_registry(), model)
    return model


def _row(model: ReportModel, field_id: str) -> Row:
    return next(r for g in model.groups for r in g.rows if r.field_id == field_id)


# --------------------------------------------------------------------------- #
# Fix 4: "No document gives a total."
# --------------------------------------------------------------------------- #

_WHY = "Visits will continue until disease progression or withdrawal."


def _why_no_total() -> ResolvedField:
    return _rf("visits.total_count", None, status="not_specified", scope="why no total",
               pages=(42,), quote=_WHY)


class TestNoDocumentGivesATotal:
    def test_the_note_the_reason_and_where_to_look(self) -> None:
        row = _row(_model([_why_no_total()]), "visits.total_count")
        assert row.status == "not_found"
        assert [v.text for v in row.values] == ["No document gives a total."]
        assert [b.text for b in row.block_lines] == [
            f'"{_WHY}" (Protocol p.42)',
            "See Visit frequency by study period.",
        ]
        # The record saying why stays the row's member, so it is still counted
        # and quoted in Appendix A.
        assert [e for m in row.members for v in m.values for e in v.entries] != []

    def test_the_reason_is_quoted_with_its_spacing_tidied_only(self) -> None:
        rf = _why_no_total()
        rf.quote = "Visits will continue\nuntil  disease progression"
        row = _row(_model([rf]), "visits.total_count")
        assert row.block_lines[0].text == '"Visits will continue until disease progression" ' \
                                          "(Protocol p.42)"

    def test_with_no_record_there_is_no_reason_line(self) -> None:
        row = _row(_model([]), "visits.total_count")
        assert [v.text for v in row.values] == ["No document gives a total."]
        assert [b.text for b in row.block_lines] == ["See Visit frequency by study period."]

    def test_a_noted_variable_is_not_counted_as_found(self) -> None:
        assert _model([_why_no_total()]).variables_found == 0

    def test_a_value_scoped_as_a_reason_is_still_a_value(self) -> None:
        model = _model([_rf("visits.total_count", 24, scope="why no total", pages=(79,))])
        row = _row(model, "visits.total_count")
        assert row.block_lines == []
        assert "24" in [v.text for v in row.values]
        assert model.variables_found == 1

    def test_a_document_giving_a_total_is_printed_as_usual(self) -> None:
        model = _model([_why_no_total(), _rf("visits.total_count", 24, pages=(79,))])
        row = _row(model, "visits.total_count")
        assert row.block_lines == []
        assert "24" in [v.text for v in row.values]
        assert model.variables_found == 1


# --------------------------------------------------------------------------- #
# Fix 5: off-topic records, and tied wordings
# --------------------------------------------------------------------------- #

_FIELD = "monitoring.strategy_guidance"
_LAB = "Samples will be measured by a central laboratory."


def _record(value: str, page: int, confidence: float = 0.9) -> FieldRecord:
    return FieldRecord(field_id=_FIELD, group="g", raw_value=value, value=value, quote=value,
                       provenance=_prov(page), confidence=confidence)


def _off_topic(verdict: str) -> Contradiction:
    return Contradiction(
        field_id=_FIELD, verdict=verdict, severity="low",  # type: ignore[arg-type]
        explanation="Record 2 is a laboratory instruction, not site monitoring.",
        records=[_record("Risk-based monitoring.", 60), _record(_LAB, 70, 0.95),
                 _record("Remote and on-site monitoring.", 61)],
        off_topic_record_indexes=[2],
    )


class TestOffTopicRecords:
    def _lab_lines(self, model: ReportModel) -> list[tuple[str, str | None]]:
        return [(e.sources, e.marker) for e in model.evidence if e.quote == _LAB]

    def test_not_a_conflict_marks_the_record_in_appendix_a_only(self) -> None:
        c, fields = _handle_not_a_conflict(_off_topic("not_a_conflict"))
        model = _model(fields, [c])
        assert _LAB not in [v.text for v in _row(model, _FIELD).values]
        assert self._lab_lines(model) == [
            ("Protocol p.70", "Not about this variable — see Appendix B"),
        ]

    def test_reconcilable_marks_the_record_and_cites_the_rest(self) -> None:
        c, fields = _handle_reconcilable(_off_topic("reconcilable"), AdjudicationResult(
            verdict="reconcilable", explanation="x", winning_record=3,
        ))
        model = _model(fields, [c])
        [line] = _row(model, _FIELD).values
        assert line.text == "Remote and on-site monitoring."
        assert "70" not in line.sources
        assert self._lab_lines(model) == [
            ("Protocol p.70", "Not about this variable — see Appendix B"),
        ]

    def test_the_marker_comes_from_the_registry(self) -> None:
        c, fields = _handle_not_a_conflict(_off_topic("not_a_conflict"))
        wording = get_registry().report_wording.model_copy(update={"off_topic": "Set aside"})
        registry = get_registry().model_copy(update={"report_wording": wording})
        state = RunState(run_id="r-1", resolved=fields, contradictions=[c])
        model = build_report_model(state, registry)
        assert self._lab_lines(model) == [("Protocol p.70", "Set aside")]


class TestTiedWordings:
    def _rows(self, values: list[str]) -> Row:
        c = Contradiction(
            field_id=_FIELD, verdict="not_a_conflict", severity="low",
            explanation="The same strategy, worded differently.",
            records=[_record(v, 60 + i) for i, v in enumerate(values)],
        )
        c, fields = _handle_not_a_conflict(c)
        return _row(_model(fields, [c]), _FIELD)

    def test_every_wording_tied_for_the_most_is_shown(self) -> None:
        row = self._rows(["Risk-based monitoring.", "Remote monitoring."])
        assert [v.text for v in row.values] == ["Risk-based monitoring.", "Remote monitoring."]
        assert row.folded == []

    def test_a_wording_with_fewer_entries_is_folded(self) -> None:
        row = self._rows(["Remote monitoring.", "Risk-based monitoring.", "Remote monitoring."])
        assert [v.text for v in row.values] == ["Remote monitoring."]
        assert [v.text for v in row.folded] == ["Risk-based monitoring."]


# --------------------------------------------------------------------------- #
# Fix 11: each page quoted with its own words
# --------------------------------------------------------------------------- #

_ANTIBODY = "NEOD001 is a humanized monoclonal antibody."
_ROUTE = "NEOD001 will be administered by intravenous infusion."


class TestEachPageItsOwnQuote:
    def _form(self, source_quotes: list[str]) -> ReportModel:
        rf = _rf("ip.form", ["infusion_iv"], quote=_ROUTE)
        rf.sources = [_prov(1, "d-rfp", "rfp"), _prov(54)]
        rf.source_quotes = source_quotes
        return _model([rf])

    def test_appendix_a_quotes_each_page_with_its_own_words(self) -> None:
        model = self._form([_ANTIBODY, _ROUTE])
        lines = {e.quote: e.sources for e in model.evidence if e.field_id == "ip.form"}
        assert lines == {_ANTIBODY: "RFP p.1", _ROUTE: "Protocol p.54"}

    def test_extraction_json_keeps_each_pages_quote_for_a_rerender(self) -> None:
        import json

        from rfp_intake.render.json_renderer import build_extraction_document

        rf = _rf("ip.form", ["infusion_iv"], quote=_ROUTE)
        rf.sources = [_prov(1, "d-rfp", "rfp"), _prov(54)]
        rf.source_quotes = [_ANTIBODY, _ROUTE]
        state = RunState(run_id="r-1", resolved=[rf])
        saved = json.loads(json.dumps(build_extraction_document(state, get_registry())))
        again = [ResolvedField.model_validate(r) for r in saved["resolved_fields"]]
        assert again[0].source_quotes == [_ANTIBODY, _ROUTE]
        model = _model(again)
        lines = {e.quote: e.sources for e in model.evidence if e.field_id == "ip.form"}
        assert lines == {_ANTIBODY: "RFP p.1", _ROUTE: "Protocol p.54"}

    def test_a_row_saved_before_source_quotes_keeps_one_line(self) -> None:
        model = self._form([])
        lines = [(e.quote, e.sources) for e in model.evidence if e.field_id == "ip.form"]
        assert lines == [(_ROUTE, "RFP p.1; Protocol p.54")]


# --------------------------------------------------------------------------- #
# Addition 3: labels start with a capital letter
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("text", "want"), [
    ("accrual period", "Accrual period"),
    ("  follow-up after treatment", "  Follow-up after treatment"),
    ("arm: placebo", "Arm: placebo"),
    ("cohort:NEOD001 arm", "Cohort:NEOD001 arm"),
    ("\u00e9tude", "\u00c9tude"),
    ("Screening", "Screening"),
    ("mL per dose", "mL per dose"),
    ("eCRF pages", "eCRF pages"),
    ("12 weeks", "12 weeks"),
    ("", ""),
])
def test_capital(text: str, want: str) -> None:
    assert _capital(text) == want


def test_a_scope_the_documents_worded_is_printed_with_a_capital() -> None:
    model = _model([_rf("timeline.total_duration", "18 months", scope="accrual period")])
    # timeline.total_duration has a `scopes` display, so the scope is a part's.
    assert [r.scope for r in model.all_rows() if r.field_id == "timeline.total_duration"] == [
        "Accrual period",
    ]
    assert {e.scope for e in model.evidence} == {"Accrual period"}


@pytest.mark.parametrize("verdict", ["conflict", "not_a_conflict"])
def test_a_disagreement_scope_is_printed_with_a_capital(verdict: str) -> None:
    records = [_record("Risk-based monitoring.", 60), _record("Remote monitoring.", 61)]
    for r in records:
        r.scope = "treatment period"
    c = Contradiction(field_id=_FIELD, verdict=verdict, severity="low",  # type: ignore[arg-type]
                      explanation="x", records=records)
    model = _model([_rf(_FIELD, "Risk-based monitoring.", scope="treatment period")], [c])
    shown = model.decisions if verdict == "conflict" else model.dismissed
    assert [d.scope for d in shown] == ["Treatment period"]
    assert {e.scope for e in model.evidence if e.field_id == _FIELD} == {"Treatment period"}


@pytest.mark.parametrize("verdict", ["conflict", "not_a_conflict"])
def test_a_kind_prefix_is_printed_as_on_the_variable_table(verdict: str) -> None:
    # Appendix A prints the scope as stored, with a capital; the Disagreements
    # and Appendix B print the kind as a word, as the variable table does.
    records = [_record("Risk-based monitoring.", 60), _record("Remote monitoring.", 61)]
    for r in records:
        r.scope = "cohort:NEOD001 arm"
    c = Contradiction(field_id=_FIELD, verdict=verdict, severity="low",  # type: ignore[arg-type]
                      explanation="x", records=records)
    model = _model([_rf(_FIELD, "Risk-based monitoring.", scope="cohort:NEOD001 arm")], [c])
    shown = model.decisions if verdict == "conflict" else model.dismissed
    assert [d.scope for d in shown] == ["Cohort: NEOD001 arm"]
    assert {e.scope for e in model.evidence if e.field_id == _FIELD} == {"Cohort:NEOD001 arm"}
