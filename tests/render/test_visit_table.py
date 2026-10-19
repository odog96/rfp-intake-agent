"""The Schedule of Events in the report (Prompt C stage 3, Angus Gray's 13 October
request). Offline: the real protocol is read from `samples/` and nothing calls a model.

READ_SCHEDULE counts; DERIVE adds up the stage totals (exception A, approved by
Oliver on 2026-10-17); the report model only puts both into words. These tests
check the words report.pdf and report.xlsx print: the 11 counts in both
settings, the key considerations at the visits Prompt C names, the totals with
their parts, the "Not counted" line, and the two fallbacks.
"""

from __future__ import annotations

import io
import os
from functools import lru_cache
from pathlib import Path

import pytest

from rfp_intake.derive import stage_totals
from rfp_intake.domain.registry import Registry, get_registry
from rfp_intake.domain.schemas import (
    Contradiction,
    FieldRecord,
    Provenance,
    ResolvedField,
    RunState,
    Schedule,
    ScheduleColumn,
)
from rfp_intake.render.report_model import Row, build_report_model, visit_schedule
from tests.render.test_report_model import assert_nothing_lost

PROTOCOL = "Example protocol 2.pdf"
ASSESSMENTS = ["26", "16", "13", "7 each", "23", "6 each", "28", "7 each", "30 each",
               "22 each", "31"]
ALL_MARKS = ["26", "16", "17", "8 each", "26", "7 each", "31", "8 each", "33 each",
             "25 each", "31"]
TABLE = "visits.frequency_by_period"
TOTALS = "visits.total_count"


@pytest.fixture(autouse=True)
def _real_registry(fields_yaml_path: Path) -> None:
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    get_registry.cache_clear()


def _periods_row() -> ResolvedField:
    """What EXTRACT gives the visit-frequency variable today: the model's own words."""
    return ResolvedField(
        field_id=TABLE,
        value=[{"period": "early_treatment", "label": "Months 1–3",
                "frequency": "weekly"}],
        status="confirmed", confidence=0.9, scope=None,
        sources=[Provenance(doc_id="doc-protocol", doc_kind="protocol", page=26,
                            printed_page="24")],
        quote="Month 1",
    )


def _with(state: RunState) -> RunState:
    """The state as DERIVE leaves it: the stage totals added."""
    return state.model_copy(
        update={"resolved": state.resolved + stage_totals(state, get_registry())})


def _row(registry: Registry, state: RunState, field_id: str) -> Row:
    model = build_report_model(state, registry)
    return next(r for g in model.groups for r in g.rows if r.field_id == field_id)


def _every_mark(registry: Registry) -> Registry:
    assert registry.schedule_table is not None
    return registry.model_copy(update={"schedule_table": registry.schedule_table.model_copy(
        update={"count_non_assessments": True})})


# --------------------------------------------------------------------------- #
# The real protocol
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _protocol_schedules(pdf: Path) -> list[Schedule]:
    """READ_SCHEDULE on the protocol, with its printed page numbers, as INGEST reads them."""
    from rfp_intake.ingest.page_labels import read_page_labels
    from rfp_intake.schedule import read_schedules
    from tests.plan.test_plan_sections_samples import _document

    doc = _document(pdf, "doc-protocol", "protocol")
    doc = doc.model_copy(update={"page_labels": read_page_labels(pdf, doc.page_texts)})
    config = get_registry().schedule_table
    assert config is not None
    return read_schedules(doc, config)


@pytest.fixture
def protocol_state(samples_dir: Path) -> RunState:
    pdf = samples_dir / PROTOCOL
    if not pdf.exists():
        pytest.skip(f"{PROTOCOL} not available")
    return _with(RunState(run_id="r-test", resolved=[_periods_row()],
                          schedules=_protocol_schedules(pdf)))


@pytest.mark.slow
class TestTheRealProtocol:
    def test_the_assessments_counts(self, protocol_state: RunState) -> None:
        row = _row(get_registry(), protocol_state, TABLE)
        [table] = row.visit_tables
        assert table.heading == "Table 1: Schedule of Events"
        assert table.head[3] == "Assessments"
        assert [cells[3] for cells in table.rows] == ASSESSMENTS

    def test_every_mark_counted_is_the_other_setting(self, protocol_state: RunState) -> None:
        row = _row(_every_mark(get_registry()), protocol_state, TABLE)
        [table] = row.visit_tables
        assert table.head[3] == "Activities"
        assert [cells[3] for cells in table.rows] == ALL_MARKS

    def test_the_visits_are_the_headers_own(self, protocol_state: RunState) -> None:
        [table] = _row(get_registry(), protocol_state, TABLE).visit_tables
        assert [cells[:3] for cells in table.rows[:4]] == [
            ["Screening", "Day-28 to Day -1", "1"],
            ["Screening", "Day -2 or Day -1", "1"],
            ["Month 1", "Day 1", "1"],
            ["Month 1", "Days 8, 15, 22 (±2)", "3"],
        ]
        assert table.rows[9][2] == "1 a month"
        # Decision B: EOT/ETD's header has no timing, so its footnote is printed.
        assert table.rows[10][1].startswith("From the footnote on Protocol p.28 (printed p.26): ")

    def test_the_key_considerations(self, protocol_state: RunState) -> None:
        [table] = _row(get_registry(), protocol_state, TABLE).visit_tables
        considerations = [cells[4] for cells in table.rows]
        assert considerations[0] == "Informed consent, Eligibility review"
        assert considerations[2].startswith("Randomization")
        day_1 = [i for i, cells in enumerate(table.rows)
                 if cells[1].startswith("Day 1") and cells[0] != "Screening"]
        assert day_1 == [2, 4, 6, 8, 9]
        assert all("Study drug infusion" in considerations[i] for i in day_1)
        assert all("Premedication" in considerations[i] for i in day_1)
        assert "Study drug infusion" not in considerations[3]

    def test_every_count_cites_its_page(self, protocol_state: RunState) -> None:
        [table] = _row(get_registry(), protocol_state, TABLE).visit_tables
        assert table.sources == "Protocol p.26 (printed p.24), 27 (printed p.25)"
        assert all(cells[5] == table.sources for cells in table.rows)

    def test_the_stopped_early_schedule_is_one_line(self, protocol_state: RunState) -> None:
        row = _row(get_registry(), protocol_state, TABLE)
        assert row.visit_lines == [
            "Not counted: patients who stop treatment early follow Appendix 3, "
            "Protocol p.111 (printed p.109), budgeted separately."
        ]

    def test_the_row_is_for_review(self, protocol_state: RunState) -> None:
        row = _row(get_registry(), protocol_state, TABLE)
        assert row.derived_status == "Worked out by the tool — confirm"
        assert row.status == "needs_review"
        assert row.no_figure

    def test_the_stage_totals_print_their_parts(self, protocol_state: RunState) -> None:
        row = _row(get_registry(), protocol_state, TOTALS)
        assert row.values[0].items == ["Counted from Table 1: Schedule of Events:"]
        assert [t.text for t in row.block_lines] == [
            "Screening: 2 (1 + 1)",
            "Months 1–3: 12 (1 + 3 + 1 + 3 + 1 + 3)",
            "From Month 4: 1 a month until the study ends",
            "End of treatment: 1",
        ]
        assert row.derived_status == "Worked out by the tool — confirm"

    def test_nothing_is_lost(self, protocol_state: RunState) -> None:
        registry = get_registry()
        assert_nothing_lost(protocol_state, registry, build_report_model(protocol_state, registry))

    def test_report_pdf_prints_the_table(self, protocol_state: RunState) -> None:
        import pymupdf

        from rfp_intake.render import build_report_pdf

        pdf = build_report_pdf(protocol_state, get_registry(), generated_at="2026-10-17")
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
            text = " ".join(" ".join(page.get_text().split()) for page in doc)
        assert "Table 1: Schedule of Events — Protocol p.26" in text
        assert "Months 1–3: 12 (1 + 3 + 1 + 3 + 1 + 3)" in text
        assert "Not counted: patients who stop treatment early follow Appendix 3" in text

    def test_report_xlsx_has_the_sheet(self, protocol_state: RunState) -> None:
        import openpyxl

        from rfp_intake.render.xlsx_renderer import build_report_xlsx

        wb = openpyxl.load_workbook(io.BytesIO(build_report_xlsx(protocol_state, get_registry())))
        rows = [r for r in wb["Schedule of Events"].iter_rows(values_only=True)]
        assert rows[0][:2] == ("Table 1: Schedule of Events",
                               "Protocol p.26 (printed p.24), 27 (printed p.25)")
        assert [r[3] for r in rows[2:13]] == ASSESSMENTS
        assert rows[-3][:1] == ("Months 1–3: 12 (1 + 3 + 1 + 3 + 1 + 3)",)

    def test_the_table_says_what_the_assessments_count_leaves_out(
        self, protocol_state: RunState,
    ) -> None:
        [table] = _row(get_registry(), protocol_state, TABLE).visit_tables
        assert table.note == (
            "The Assessments count leaves out randomization, premedication, study drug "
            "infusion and chemotherapy."
        )
        # Counting every mark leaves nothing out, so says nothing.
        [table] = _row(_every_mark(get_registry()), protocol_state, TABLE).visit_tables
        assert table.note is None

    def test_key_considerations_is_in_the_word_list(self, protocol_state: RunState) -> None:
        model = build_report_model(protocol_state, get_registry())
        [term] = [t for t in model.glossary if t.label == "Key considerations"]
        assert term.plain.startswith("The activities a visit includes")


@pytest.fixture
def review_state(samples_dir: Path) -> RunState:
    """As run r-20261019-prompt-c2 left the two visit variables: the model's own
    visit frequencies for review, and an empty total beside the stage totals."""
    pdf = samples_dir / PROTOCOL
    if not pdf.exists():
        pytest.skip(f"{PROTOCOL} not available")
    periods = _periods_row().model_copy(update={"status": "needs_review", "confidence": 0.7})
    no_total = ResolvedField(field_id=TOTALS, status="not_found", confidence=0.0)
    return _with(RunState(run_id="r-test", resolved=[periods, no_total],
                          schedules=_protocol_schedules(pdf)))


@pytest.mark.slow
class TestPrintedOnce:
    """Prompt C follow-ups 1 to 3 (Oliver, 2026-10-19): with the visit table shown,
    the model's own visit frequencies and the empty total are in extraction.json
    only, and each visit variable is flagged once."""

    def test_the_model_s_visit_frequencies_and_the_empty_total_are_kept_out(
        self, review_state: RunState,
    ) -> None:
        model = build_report_model(review_state, get_registry())
        assert [(k.field_id, k.status) for k in model.kept_out] == [
            (TABLE, "needs_review"), (TOTALS, "not_found"),
        ]
        assert TABLE not in {e.field_id for e in model.evidence}
        # Appendix A keeps the four stage totals and nothing for the empty total.
        # From Month 4 has no sum and reads as its stage's words, as in Flagged
        # for review, not "—" (2026-10-19).
        totals = [e for e in model.evidence if e.field_id == TOTALS]
        assert [(e.scope, e.value, e.notes) for e in totals] == [
            ("Screening", "2", "1 + 1"),
            ("Months 1–3", "12", "1 + 3 + 1 + 3 + 1 + 3"),
            ("From Month 4", "1 a month until the study ends", None),
            ("End of treatment", "1", "1"),
        ]
        assert all(e.derived_from for e in totals)
        assert_nothing_lost(review_state, get_registry(), model)

    def test_no_table_counted_keeps_everything(self, review_state: RunState) -> None:
        state = review_state.model_copy(update={"schedules": []})
        model = build_report_model(state, get_registry())
        assert model.kept_out == []
        assert TABLE in {e.field_id for e in model.evidence}

    def test_each_visit_variable_is_flagged_once(self, review_state: RunState) -> None:
        flagged = build_report_model(review_state, get_registry()).review_rows()
        visits = [r for r in flagged if r.field_id in (TABLE, TOTALS)]
        assert [(r.field_id, r.review_text) for r in visits] == [
            (TABLE, "Counted from Table 1: Schedule of Events; the table is under this "
                    "variable in All variables."),
            (TOTALS, "Screening: 2; Months 1–3: 12; From Month 4: 1 a month until the study "
                     "ends; End of treatment: 1"),
        ]
        assert all(r.reasons[0].startswith("Worked out by the tool") for r in visits)

    def test_report_pdf(self, review_state: RunState) -> None:
        import pymupdf

        from rfp_intake.render import build_report_pdf

        model = build_report_model(review_state, get_registry())
        pdf = build_report_pdf(review_state, get_registry(), generated_at="2026-10-19")
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
            text = " ".join(" ".join(page.get_text().split()) for page in doc)
        n = len({r.field_id for r in model.review_rows()})
        assert f"{n} other variables have values flagged for review" in text
        assert "Period: early_treatment" not in text
        assert "The Assessments count leaves out randomization" in text
        flagged = text[text.index("Flagged for review The documents"):]
        assert "From Month 4: 1 a month until the study ends; End of treatment: 1" in flagged

    def test_report_xlsx(self, review_state: RunState) -> None:
        import openpyxl

        from rfp_intake.render.xlsx_renderer import build_report_xlsx

        wb = openpyxl.load_workbook(io.BytesIO(build_report_xlsx(review_state, get_registry())))
        for sheet in ("Fields", "Review Queue"):
            labels = [r[1] for r in wb[sheet].iter_rows(values_only=True)]
            assert "Visit frequency by study period" not in labels
            assert [lab for lab in labels if lab.startswith("Total number of subject visits")] == [
                f"Total number of subject visits [{stage}]"
                for stage in ("Screening", "Months 1–3", "From Month 4", "End of treatment")
            ], sheet
        schedule = [r[0] for r in wb["Schedule of Events"].iter_rows(values_only=True)]
        note = schedule.index(
            "The Assessments count leaves out randomization, premedication, study drug "
            "infusion and chemotherapy."
        )
        # Straight under the table's last row, EOT/ETD.
        assert schedule[note - 1] == "EOT/ETD"

    @pytest.mark.parametrize("verdict", ["conflict", "not_a_conflict"])
    def test_the_model_s_visit_frequencies_get_no_disagreement(
        self, review_state: RunState, verdict: str,
    ) -> None:
        """Oliver, 2026-10-19: with the table shown, the kept-out visit
        frequencies give no numbered disagreement, nor a line in Appendix B."""
        c = _frequency_disagreement(verdict)
        resolved = [rf.model_copy(update={"contradiction": c}) if rf.field_id == TABLE else rf
                    for rf in review_state.resolved]
        state = review_state.model_copy(update={"resolved": resolved, "contradictions": [c]})
        model = build_report_model(state, get_registry())
        assert model.decisions == [] and model.dismissed == []
        assert_nothing_lost(state, get_registry(), model)
        # With no table counted the disagreement is printed, as before.
        before = build_report_model(state.model_copy(update={"schedules": []}), get_registry())
        listed = before.decisions if verdict == "conflict" else before.dismissed
        assert [d.field_id for d in listed] == [TABLE]

    def test_appendix_a_prints_from_month_4_in_words(self, review_state: RunState) -> None:
        import pymupdf

        from rfp_intake.render import build_report_pdf

        pdf = build_report_pdf(review_state, get_registry(), generated_at="2026-10-19")
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
            text = " ".join(" ".join(page.get_text().split()) for page in doc)
        appendix = text[text.index("Appendix A — Evidence"):]
        at = appendix.index("From Month 4")
        assert appendix[at:at + 60].startswith("From Month 4] 1 a month until the study ends —"), (
            appendix[at:at + 120])

    def test_a_total_with_a_quote_is_still_printed(self, review_state: RunState) -> None:
        """Decision of 2026-10-19: only the empty "not found" total goes; a
        passage on why there is no total is evidence, and stays."""
        quoted = ResolvedField(
            field_id=TOTALS, status="not_found", confidence=0.6,
            quote="The number of visits depends on how long each subject is treated.",
            sources=[Provenance(doc_id="doc-protocol", doc_kind="protocol", page=26)],
        )
        state = review_state.model_copy(update={"resolved": [*review_state.resolved, quoted]})
        model = build_report_model(state, get_registry())
        assert not any(k is quoted for k in model.kept_out)
        assert quoted.quote in {e.quote for e in model.evidence if e.field_id == TOTALS}
        assert_nothing_lost(state, get_registry(), model)


def _frequency_disagreement(verdict: str) -> Contradiction:
    return Contradiction(
        field_id=TABLE, verdict=verdict, severity="high",  # type: ignore[arg-type]
        explanation="The synopsis and the schedule give different visit frequencies.",
        records=[
            FieldRecord(field_id=TABLE, group="visits", raw_value=v, value=v, quote=v,
                        provenance=Provenance(doc_id="doc-protocol", doc_kind="protocol",
                                              page=page),
                        confidence=0.7)
            for v, page in (("weekly", 26), ("every 2 weeks", 12))
        ],
    )


@pytest.mark.parametrize(("label", "inside"), [
    ("Randomization", "randomization"),
    ("Study drug infusion", "study drug infusion"),
    ("IV infusion", "IV infusion"),
    ("ECG", "ECG"),
])
def test_a_label_inside_a_sentence(label: str, inside: str) -> None:
    from rfp_intake.render.report_model import _lower_first

    assert _lower_first(label) == inside


# --------------------------------------------------------------------------- #
# Made-up schedules: the fallbacks and the sums
# --------------------------------------------------------------------------- #


def _column(period: str, timing: str, *, visits: int | None = 1, says: str | None = None,
            stage: str | None = None) -> ScheduleColumn:
    return ScheduleColumn(period=period, timing=timing, visits=visits, visits_says=says,
                          stage=stage, marks_all=5, marks_assessments=4, pages=[26])


def _schedule(columns: list[ScheduleColumn], *, counted: bool = True,
              heading: str = "Table 1: Schedule of Events") -> Schedule:
    return Schedule(doc_id="doc-protocol", heading=heading, pages=[26],
                    printed_pages=["24"], counted=counted, columns=columns)


class TestFallbacks:
    def test_no_schedule_table_found(self) -> None:
        row = _row(get_registry(), _with(RunState(resolved=[_periods_row()])), TABLE)
        assert row.visit_tables == []
        assert row.block_lines[0].text == "No schedule table found."
        # Then today's text, as before Prompt C.
        before = _row(get_registry().model_copy(update={"schedule_table": None}),
                      RunState(resolved=[_periods_row()]), TABLE)
        assert row.values == before.values
        assert row.block_lines[1:] == before.block_lines
        assert row.derived_status is None

    def test_a_table_that_could_not_be_read(self) -> None:
        state = _with(RunState(resolved=[_periods_row()], schedules=[_schedule([])]))
        row = _row(get_registry(), state, TABLE)
        assert row.block_lines[0].text == (
            "Schedule table on Protocol p.26 (printed p.24) could not be read.")
        assert row.visit_tables == []

    def test_no_totals_without_a_table(self) -> None:
        assert stage_totals(RunState(resolved=[_periods_row()]), get_registry()) == []
        schedule = visit_schedule(RunState(resolved=[_periods_row()]), get_registry())
        assert schedule is not None and schedule.totals == []


class TestStageTotals:
    def test_a_stage_is_the_sum_of_its_columns_with_the_parts(self) -> None:
        state = RunState(resolved=[_periods_row()], schedules=[_schedule([
            _column("Screening", "Day -28", stage="Screening"),
            _column("Screening", "Day -1", stage="Screening"),
            _column("Month 1", "Days 8, 15", visits=2, stage="Months 1–3"),
        ])])
        rows = stage_totals(state, get_registry())
        assert [(r.scope, r.value, r.notes, r.status) for r in rows] == [
            ("Screening", 2, "1 + 1", "needs_review"),
            ("Months 1–3", 2, "2", "needs_review"),
        ]
        assert all(r.derived_from == [TABLE] for r in rows)
        assert all(r.field_id == TOTALS for r in rows)
        assert rows[0].sources[0].page == 26 and rows[0].sources[0].printed_page == "24"

    def test_a_stage_that_repeats_has_its_words_not_a_number(self) -> None:
        state = RunState(schedules=[_schedule([
            _column("Month 4", "Day 1", visits=None, says="1 a month", stage="From Month 4"),
        ])])
        [row] = stage_totals(state, get_registry())
        # The table states these visits, so not not_specified: a person reads the words.
        assert row.value is None and row.status == "needs_review"
        assert row.notes == "1 a month until the study ends"

    def test_a_stage_that_mixes_both_keeps_its_fixed_visits(self) -> None:
        state = RunState(schedules=[_schedule([
            _column("Screening", "Day -28", stage="Screening"),
            _column("Month 4", "Day 1", visits=None, says="1 a month", stage="Screening"),
        ])])
        [row] = stage_totals(state, get_registry())
        assert (row.value, row.notes, row.status) == (None, "1 + 1 a month", "needs_review")

    def test_a_failure_loses_no_other_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import rfp_intake.derive as derive

        def fail(state: RunState, registry: Registry) -> list[ResolvedField]:
            raise ValueError("unreadable")

        monkeypatch.setattr(derive, "stage_totals", fail)
        result = derive.derive_node(RunState(resolved=[_periods_row()]))
        assert [e.error for e in result["errors"]] == ["Stage totals failed: unreadable"]
        assert result["errors"][0].task_id == TOTALS
        derived = {f.id for f in get_registry().fields if f.derived}
        assert derived <= {r.field_id for r in result["resolved"]}

    def test_a_column_in_no_stage_is_named(self) -> None:
        state = _with(RunState(resolved=[_periods_row()], schedules=[_schedule([
            _column("Screening", "Day -28", stage="Screening"),
            _column("Unscheduled", ""),
        ])]))
        row = _row(get_registry(), state, TOTALS)
        assert [t.text for t in row.block_lines][:2] == [
            "Screening: 1",
            "Not in any stage, so not in the totals: Unscheduled.",
        ]

    def test_a_not_counted_schedule_adds_nothing(self) -> None:
        state = RunState(schedules=[_schedule(
            [_column("Week 1", "Day 1", stage="Screening")], counted=False,
            heading="Appendix 3: Schedule Of Events For Subjects Who Stop Early")])
        assert stage_totals(state, get_registry()) == []
