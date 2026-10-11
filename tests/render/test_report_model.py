"""Tests for render/report_model.py — above all, that shortening the report
never loses anything an analyst would otherwise have found.

`assert_nothing_lost` is the guarantee, written once and run against both the
hand-built states here and every real run folder present on the machine.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from rfp_intake.domain.registry import FieldDef, Registry, get_registry
from rfp_intake.domain.schemas import (
    Contradiction,
    Document,
    FieldRecord,
    Provenance,
    RemovedPassage,
    ResolvedField,
    RunState,
)
from rfp_intake.normalize.scope import normalize_scope
from rfp_intake.render.report_model import (
    _STATUS_RANK,
    NEEDS_DECISION,
    REASON_DISMISSED_DISAGREEMENT,
    REMOVED_EXTRACT_CHARS,
    SCHEDULE_THRESHOLD,
    ReportModel,
    _same_key,
    _signature,
    build_report_model,
    format_page_range,
    format_page_ref,
    format_value,
    humanize_enum,
)

RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"


@pytest.fixture(autouse=True)
def _real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    get_registry.cache_clear()


# --------------------------------------------------------------------------- #
# The guarantee
# --------------------------------------------------------------------------- #


def assert_nothing_lost(state: RunState, registry: Registry, model: ReportModel) -> None:
    rows = model.all_rows()
    field_by_id = {f.id: f for f in registry.fields}
    codes = {d.doc_id: d.code for d in model.documents}

    # Every variable in config/fields.yaml has a row, found or not.
    assert {f.id for f in registry.fields} <= {r.field_id for r in rows}

    for rf in state.resolved:
        fd = field_by_id.get(rf.field_id)
        want = "; ".join(format_value(rf.value, fd)) if rf.status != "not_found" else "—"
        homes = [
            (row, line)
            for row in rows
            if row.field_id == rf.field_id
            and normalize_scope(row.scope) == normalize_scope(rf.scope)
            for line in row.values + row.folded
            if any(e is rf for e in line.entries)
        ]
        # Every entry lands in exactly one value line.
        assert len(homes) == 1, f"{rf.field_id} [{rf.scope}] {want!r} is in {len(homes)} places"
        row, line = homes[0]
        # Case and spacing are the only differences a merge may paper over.
        assert _same_key([line.text]) == _same_key([want])
        # Merging never makes anything look more certain than it was.
        assert _STATUS_RANK[line.status] <= _STATUS_RANK[rf.status]
        assert _STATUS_RANK[row.status] <= _STATUS_RANK[rf.status]
        assert line.confidence <= rf.confidence
        # The pages beside a value are exactly the pages that state it, checked
        # both ways. Until fix 2 of the 2026-10-09 brief this checked only that
        # every page in `rf.sources` was cited — never that a cited page says
        # the value — and ADJUDICATE puts every record's page in `rf.sources`.
        # So run r-20261009-225414 passed while printing "0" interim analyses
        # beside RFP p.4, which says 1: the old check asked for that page.
        cited = _parse_sources(line.sources)
        # A value that came with pages keeps at least one, so the check below
        # cannot pass by the code dropping every page.
        if rf.value is not None and rf.sources:
            assert cited, f"{rf.field_id} [{rf.scope}] {want!r} cites no page"
        stating = _stating_pages(rf, fd, codes, want)
        for code, page in stating:
            assert page in cited.get(code, set()), (
                f"{rf.field_id}: {code} p.{page} not in {line.sources!r}"
            )
        allowed = {
            p for e in line.entries
            for p in _stating_pages(
                e, fd, codes,
                "; ".join(format_value(e.value, fd)) if e.status != "not_found" else "—",
            )
        }
        for code, pages in cited.items():
            for page in pages:
                assert (code, page) in allowed, (
                    f"{rf.field_id} [{rf.scope}] {line.text!r}: {code} p.{page} "
                    "is cited but does not state this value"
                )
        # Every record of the disagreement this value was chosen from has its
        # own Appendix A line: its value, its page and its quote, whichever
        # value won — the losing side included, and on a row where none did.
        if _own_disagreement_of(rf) is not None:
            assert rf.contradiction is not None
            for r in rf.contradiction.records:
                said = "; ".join(format_value(
                    r.value if r.value is not None else r.raw_value, fd))
                assert any(
                    e.field_id == rf.field_id and e.value == said and e.quote == r.quote
                    and _parse_sources(e.sources)
                    == {codes[r.provenance.doc_id]: {r.provenance.page}}
                    for e in model.evidence
                ), (
                    f"{rf.field_id}: {said!r} from {codes[r.provenance.doc_id]} "
                    f"p.{r.provenance.page} has no Appendix A line of its own"
                )
        # A folded rewording was checked, and its text and quote are in Appendix A.
        if line in row.folded:
            assert row.verdict == "not_a_conflict" and not row.budget_driver
            assert any(e.field_id == rf.field_id and e.value == want for e in model.evidence)
        # Its quote is in Appendix A.
        if rf.quote:
            assert any(
                e.field_id == rf.field_id and e.quote == rf.quote and e.value == want
                for e in model.evidence
            ), f"quote for {rf.field_id} missing from the evidence appendix"

    # Every adjudicated disagreement is either a decision or listed as dismissed.
    adjudicated = [c for c in state.contradictions if c.verdict is not None]
    decided = {d.field_id for d in model.decisions}
    dismissed = {d.field_id for d in model.dismissed}
    for c in adjudicated:
        if c.verdict in NEEDS_DECISION:
            assert c.field_id in decided
            d = next(d for d in model.decisions if d.signature == _signature(c))
            in_positions: dict[str, set[int]] = {}
            for pos in d.positions:
                for code, pages in _parse_sources(pos.sources).items():
                    in_positions.setdefault(code, set()).update(pages)
            for r in c.records:
                assert r.provenance.page in in_positions[codes[r.provenance.doc_id]]
        else:
            assert c.field_id in dismissed
    needing = [c for c in adjudicated if c.verdict in NEEDS_DECISION]
    assert len(model.decisions) == len(needing)

    # A row that carries a decision says so.
    for row in rows:
        if row.verdict in NEEDS_DECISION:
            assert row.decision_no is not None


def _own_disagreement_of(rf: ResolvedField) -> Contradiction | None:
    """The disagreement ADJUDICATE wrote this entry from as one row citing every
    record's page. Written out here rather than imported, so the test does not
    take the rule from the code it is checking."""
    c = rf.contradiction
    if c is None or c.field_id != rf.field_id or c.verdict not in ("conflict", "reconcilable"):
        return None
    return c


def _stating_pages(
    rf: ResolvedField, fd: FieldDef | None, codes: dict[str, str], want: str,
) -> set[tuple[str, int]]:
    """(document code, page) for every page that states `want`, this entry's value.

    For an entry written from a disagreement, the pages of the records whose
    value reads as `want` — none when no value won and `want` is "—". For any
    other entry, the pages in its own `sources`, which each say its value.
    """
    c = _own_disagreement_of(rf)
    if c is None:
        return {(codes[p.doc_id], p.page) for p in rf.sources}
    if rf.value is None:
        return set()
    return {
        (codes[r.provenance.doc_id], r.provenance.page)
        for r in c.records
        # Compared joined the way `want` was, so a value of several items
        # matches itself rather than failing on the separator.
        if _same_key(["; ".join(format_value(
            r.value if r.value is not None else r.raw_value, fd))]) == _same_key([want])
    }


# One cited page: its position in the file, and the number printed on it where
# the two differ — "11" or "11 (printed p.9)". Change 7 of the 2026-10-07 review.
_CITED_PAGE = re.compile(r"(\d+)(?:\s*\(printed p\.[^)]*\))?")


def _parse_sources(text: str) -> dict[str, set[int]]:
    """"Protocol p.11 (printed p.9), 42; RFP p.2" -> {"Protocol": {11, 42}, "RFP": {2}}.

    Only the file positions come back. A printed number beside one is extra, for
    a reader holding the paper, and the checks below are about which pages are
    cited at all.
    """
    out: dict[str, set[int]] = {}
    for part in filter(None, (t.strip() for t in text.split(";"))):
        head = re.match(r"(.*?) p\.(.*)", part)
        assert head, f"not a page reference: {part!r}"
        out[head.group(1)] = {int(m.group(1)) for m in _CITED_PAGE.finditer(head.group(2))}
    return out


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _prov(page: int, doc: str = "d-prot", kind: str = "protocol") -> Provenance:
    return Provenance(doc_id=doc, doc_kind=kind, page=page)  # type: ignore[arg-type]


def _rf(field_id: str, value: object, *, status: str = "confirmed", scope: str | None = None,
        pages: tuple[int, ...] = (1,), doc: str = "d-prot", kind: str = "protocol",
        quote: str | None = "q", contradiction: Contradiction | None = None,
        confidence: float = 0.9) -> ResolvedField:
    return ResolvedField(
        field_id=field_id, value=value, status=status, confidence=confidence,  # type: ignore[arg-type]
        scope=scope, sources=[_prov(p, doc, kind) for p in pages], quote=quote,
        contradiction=contradiction,
    )


def _contradiction(field_id: str, verdict: str, values: list[tuple[str, int, str]],
                   severity: str = "high") -> Contradiction:
    return Contradiction(
        field_id=field_id,
        verdict=verdict,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        explanation="Doc d-prot says one thing and d-rfp another.",
        records=[
            FieldRecord(field_id=field_id, group="g", raw_value=v, value=v, quote=v,
                        provenance=_prov(page, doc, "rfp" if doc == "d-rfp" else "protocol"),
                        confidence=0.9)
            for v, page, doc in values
        ],
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


class TestNothingLost:
    def test_empty_run_still_lists_every_variable_as_not_found(self) -> None:
        state = RunState(run_id="r-1")
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        assert all(r.status == "not_found" for r in model.all_rows())
        assert model.variables_found == 0

    def test_identical_values_merge_into_one_line_citing_every_page(self) -> None:
        state = RunState(run_id="r-1", resolved=[
            _rf("design.first_in_human", "false", pages=(11,)),
            _rf("design.first_in_human", "false", pages=(42,)),
            _rf("design.first_in_human", "false", pages=(2,), doc="d-rfp", kind="rfp"),
        ])
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        [row] = [r for r in model.all_rows() if r.field_id == "design.first_in_human"]
        [line] = row.values
        assert line.text == "No"
        assert line.sources == "Protocol p.11, 42; RFP p.2"

    def test_different_values_are_never_merged(self) -> None:
        state = RunState(run_id="r-1", resolved=[
            _rf("design.first_in_human", "false", pages=(11,)),
            _rf("design.first_in_human", "not_specified", pages=(17,)),
        ])
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        [row] = [r for r in model.all_rows() if r.field_id == "design.first_in_human"]
        assert [v.text for v in row.values] == ["No", "Not specified"]

    def test_a_merged_row_takes_the_least_certain_status(self) -> None:
        state = RunState(run_id="r-1", resolved=[
            _rf("ops.sites_total", 75, scope="total", pages=(15,)),
            _rf("ops.sites_total", 75, scope="total", pages=(4,), status="needs_review",
                confidence=0.5),
        ])
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        [row] = [r for r in model.all_rows() if r.field_id == "ops.sites_total"]
        assert row.status == "needs_review"
        assert row.values[0].confidence == 0.5

    def test_no_scope_and_whole_study_stay_separate_rows(self) -> None:
        # normalize_scope keeps these apart on purpose: an unqualified number is
        # not the same claim as a study-wide total.
        state = RunState(run_id="r-1", resolved=[
            _rf("ops.subjects_total", 260, scope="total"),
            _rf("ops.subjects_total", 260, scope=None),
        ])
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        assert len([r for r in model.all_rows() if r.field_id == "ops.subjects_total"]) == 2

    def test_a_conflict_becomes_a_numbered_decision_linked_from_its_row(self) -> None:
        c = _contradiction("interim.count", "conflict",
                           [("0", 98, "d-prot"), ("1", 4, "d-rfp")])
        state = RunState(
            run_id="r-1",
            resolved=[_rf("interim.count", 0, status="needs_review", pages=(98, 4),
                          contradiction=c)],
            contradictions=[c],
        )
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        [d] = model.decisions
        assert [(p.text, p.sources) for p in d.positions] == [
            ("0", "Protocol p.98"), ("1", "RFP p.4"),
        ]
        [row] = [r for r in model.all_rows() if r.field_id == "interim.count"]
        assert row.decision_no == d.no == 1

    def test_each_side_of_a_conflict_keeps_its_own_page_and_quote(self) -> None:
        """Fix 2 of the 2026-10-09 brief, built by hand so it runs on a clean
        checkout, where `runs/` is not present. ADJUDICATE cites both records'
        pages on the one row; the row shows only the page that states 0."""
        c = _contradiction("interim.count", "conflict",
                           [("0", 98, "d-prot"), ("1", 4, "d-rfp")])
        rf = _rf("interim.count", 0, status="needs_review", contradiction=c, quote="0")
        rf.sources = [_prov(98), _prov(4, "d-rfp", "rfp")]
        state = RunState(run_id="r-1", resolved=[rf], contradictions=[c])
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        [row] = [r for r in model.all_rows() if r.field_id == "interim.count"]
        assert [(v.text, v.sources) for v in row.values] == [("0", "Protocol p.98")]
        assert sorted(
            (e.value, e.sources, e.quote) for e in model.evidence if e.field_id == "interim.count"
        ) == [("0", "Protocol p.98", "0"), ("1", "RFP p.4", "1")]

    def test_two_rows_sharing_one_disagreement_share_its_number(self) -> None:
        """Change 4 of the 2026-10-07 review: the timing row follows the count.

        GATE hands the same Contradiction object to both rows (see
        gate/_contradiction_for), so the report must print one numbered
        disagreement and point both rows at it.
        """
        c = _contradiction("interim.count", "conflict",
                           [("0", 98, "d-prot"), ("1", 4, "d-rfp")])
        state = RunState(
            run_id="r-1",
            resolved=[
                _rf("interim.count", 0, status="needs_review", pages=(98, 4),
                    contradiction=c),
                _rf("interim.timing", ["After about 156 events"], status="needs_review",
                    pages=(4,), doc="d-rfp", kind="rfp", contradiction=c),
            ],
            contradictions=[c],
        )
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        assert len(model.decisions) == 1, "one disagreement, not one per row"
        rows = {r.field_id: r for r in model.all_rows()
                if r.field_id in ("interim.count", "interim.timing")}
        assert {k: r.decision_no for k, r in rows.items()} == {
            "interim.count": 1, "interim.timing": 1,
        }

        # The timing row names the variable the documents actually disagree
        # about. The report-usability reviewer of run r-20261008-review2-run2
        # followed the timing row's bare "Sources disagree — see Disagreement 1"
        # to a disagreement that describes two interim-analysis COUNTS and
        # nothing about timing at all, and could not tell what had been disputed.
        assert rows["interim.timing"].follows_label == "Number of interim analyses"
        assert rows["interim.timing"].reasons == [
            "The documents disagree on Number of interim analyses "
            "— see Disagreement 1."
        ]
        # The row that owns the disagreement still says it plainly, with no
        # variable named: the Disagreements table is about that row's own values.
        assert rows["interim.count"].follows_label is None
        assert rows["interim.count"].reasons == [
            "The documents disagree — see Disagreement 1."
        ]

    def test_the_worst_conflict_comes_first(self) -> None:
        a = _contradiction("ops.sites_total", "conflict",
                           [("75", 1, "d-prot"), ("70", 2, "d-rfp")], severity="low")
        b = _contradiction("interim.count", "conflict",
                           [("0", 3, "d-prot"), ("1", 4, "d-rfp")], severity="high")
        state = RunState(run_id="r-1", contradictions=[a, b])
        model = build_report_model(state, get_registry())
        assert [d.field_id for d in model.decisions] == ["interim.count", "ops.sites_total"]

    def test_a_reconcilable_verdict_is_not_a_disagreement_to_settle(self) -> None:
        """Change 1 of the 2026-10-07 review: follow the reviewer's conclusion.

        ADJUDICATE says "reconcilable" when both values are true and one restates
        the other. That is not something a person has to settle, so the variable
        is not in the Disagreements table and its row does not say
        "Disagreement" — it shows the value ADJUDICATE chose. Both wordings are
        still in the report, in Appendix B.
        """
        c = _contradiction("study.population", "reconcilable",
                           [("Adults with AL amyloidosis", 12, "d-prot"),
                            ("Subjects with AL amyloidosis", 3, "d-rfp")])
        state = RunState(
            run_id="r-1",
            resolved=[_rf("study.population", "Adults with AL amyloidosis",
                          pages=(12, 3), contradiction=c)],
            contradictions=[c],
        )
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        assert model.decisions == []
        assert [d.field_id for d in model.dismissed] == ["study.population"]
        [row] = [r for r in model.all_rows() if r.field_id == "study.population"]
        assert row.decision_no is None
        assert row.verdict == "reconcilable"
        assert [v.text for v in row.values] == ["Adults with AL amyloidosis"]

    def test_a_dismissed_explanation_names_the_documents_not_their_ids(self) -> None:
        """Appendix B's reasons go through the same renaming as the Disagreements table.

        ADJUDICATE's prompt prints `doc_id=` for every candidate and asks it to
        name the document, so its sentences carry internal ids such as
        `doc-2ea4cd79`. `_decisions` has always passed them through
        `_Docs.name_ids`; when change 1 of the 2026-10-07 review started printing
        the dismissed reasons too, they went out unrenamed, and Appendix B of
        r-20261008-review2-run3 showed four raw ids to the analyst.
        """
        c = _contradiction("study.population", "reconcilable",
                           [("Adults with AL amyloidosis", 12, "d-prot"),
                            ("Subjects with AL amyloidosis", 3, "d-rfp")])
        state = RunState(
            run_id="r-1",
            resolved=[_rf("study.population", "Adults with AL amyloidosis",
                          pages=(12, 3), contradiction=c)],
            contradictions=[c],
        )
        model = build_report_model(state, get_registry())

        [dismissed] = model.dismissed
        assert "d-prot" not in dismissed.explanation
        assert "d-rfp" not in dismissed.explanation
        # The same names the rest of the report uses for those two documents.
        codes = {ref.code for ref in model.documents}
        assert all(code in dismissed.explanation for code in codes), dismissed.explanation

    def test_a_reconcilable_budget_driver_is_flagged_without_a_decision_number(self) -> None:
        """The other half of change 1: a budget driver still reaches a person.

        ops.subjects_total was Disagreement 7 in run r-20261007-140411 (260
        against 236). It must not simply disappear: GATE keeps it at
        needs_review because it is a budget driver, so it moves to Flagged for
        review, with a reason that does not cite a disagreement number that no
        longer exists.
        """
        c = _contradiction("ops.subjects_total", "reconcilable",
                           [("260", 15, "d-prot"), ("236", 4, "d-rfp")])
        state = RunState(
            run_id="r-1",
            resolved=[_rf("ops.subjects_total", 260, status="needs_review",
                          pages=(15, 4), contradiction=c)],
            contradictions=[c],
        )
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        assert model.decisions == []
        [row] = [r for r in model.all_rows() if r.field_id == "ops.subjects_total"]
        assert row.decision_no is None
        assert row in model.review_rows()
        assert row.reasons == [REASON_DISMISSED_DISAGREEMENT]

    def test_document_ids_in_the_reasoning_become_names(self) -> None:
        c = _contradiction("interim.count", "conflict",
                           [("0", 98, "d-prot"), ("1", 4, "d-rfp")])
        model = build_report_model(RunState(run_id="r-1", contradictions=[c]), get_registry())
        assert model.decisions[0].explanation == "Doc Protocol says one thing and RFP another."

    def test_free_text_rewordings_fold_but_budget_drivers_never_do(self) -> None:
        text = _contradiction("study.population", "not_a_conflict",
                              [("Adults with AL", 12, "d-prot"), ("Subjects with AL", 3, "d-rfp")])
        budget = _contradiction("timeline.total_duration", "not_a_conflict",
                                [("3.5-4 years", 47, "d-prot"), ("about 4 years", 3, "d-rfp")])
        state = RunState(run_id="r-1", contradictions=[text, budget], resolved=[
            _rf("study.population", "Adults with AL", pages=(12,), contradiction=text),
            _rf("study.population", "Adults with AL", pages=(20,), contradiction=text),
            _rf("study.population", "Subjects with AL", pages=(3,), doc="d-rfp", kind="rfp",
                contradiction=text),
            _rf("timeline.total_duration", "3.5-4 years", pages=(47,), status="needs_review",
                contradiction=budget),
            _rf("timeline.total_duration", "about 4 years", pages=(3,), doc="d-rfp", kind="rfp",
                status="needs_review", contradiction=budget),
        ])
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)

        [pop] = [r for r in model.all_rows() if r.field_id == "study.population"]
        assert [v.text for v in pop.values] == ["Adults with AL"]  # the most-cited wording
        assert [v.text for v in pop.folded] == ["Subjects with AL"]

        [dur] = [r for r in model.all_rows() if r.field_id == "timeline.total_duration"]
        assert len(dur.values) == 2 and not dur.folded

    def test_many_rows_move_to_a_schedule_and_leave_a_pointer(self) -> None:
        state = RunState(run_id="r-1", resolved=[
            _rf("timeline.periods", [f"{n} months"], scope=f"period {n}", pages=(n,))
            for n in range(1, SCHEDULE_THRESHOLD + 2)
        ])
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        [section] = [g for g in model.groups if g.schedules]
        [schedule] = section.schedules
        assert len(schedule.rows) == SCHEDULE_THRESHOLD + 1
        pointer = [r for r in section.rows if r.field_id == "timeline.periods"]
        assert len(pointer) == 1 and "see Schedules" in pointer[0].values[0].text

    def test_a_field_no_longer_in_the_registry_is_still_printed(self) -> None:
        state = RunState(run_id="r-1", resolved=[_rf("retired.field", "kept")])
        model = build_report_model(state, get_registry())
        assert any(r.field_id == "retired.field" for r in model.all_rows())


class TestThePageNumberPrintedOnThePage:
    """Change 7 of the 2026-10-07 review.

    The report cites a page by its position in the PDF file, which is what gets
    a reader there in a viewer. The protocol amendment prints numbers two lower,
    so "Amendment p.11" sent Oliver Zarate to the wrong page. Where the two
    differ the reference now carries both.
    """

    def test_one_page_shows_both_numbers(self) -> None:
        assert format_page_ref(11, "9") == "11 (printed p.9)"

    def test_a_page_whose_numbers_agree_shows_one(self) -> None:
        assert format_page_ref(4, "4") == "4"

    def test_a_page_with_no_printed_number_shows_one(self) -> None:
        assert format_page_ref(4, None) == "4"

    def test_a_range_shows_both_ends(self) -> None:
        assert format_page_range(39, 41, "37", "39") == "39-41 (printed p.37-39)"

    def test_a_range_missing_its_printed_start_falls_back_to_the_file_position(self) -> None:
        assert format_page_range(3, 6, None, "4") == "3-6 (printed p.3-4)"

    def test_a_range_missing_its_printed_end_shows_the_start_alone(self) -> None:
        """Nothing is inferred from the offset: a document can renumber anywhere."""
        assert format_page_range(5, 7, "3", None) == "5-7 (printed p.3)"

    def test_a_range_with_no_printed_numbers_is_unchanged(self) -> None:
        assert format_page_range(39, 41, None, None) == "39-41"

    def test_a_range_of_one_page_is_written_as_one_page(self) -> None:
        assert format_page_range(11, 11, "9", "9") == "11 (printed p.9)"

    def test_a_value_row_cites_both_numbers(self) -> None:
        state = RunState(
            run_id="r-1",
            documents=[Document(id="d-prot", path="/in/Amendment.pdf",
                                kind="amendment", pages=137)],
            resolved=[ResolvedField(
                field_id="study.phase", value="phase_3", status="confirmed", confidence=0.95,
                quote="This is a Phase 3 study.",
                sources=[
                    Provenance(doc_id="d-prot", doc_kind="amendment", page=11, printed_page="9"),
                    Provenance(doc_id="d-prot", doc_kind="amendment", page=18, printed_page="16"),
                ],
            )],
        )
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        row = next(r for r in model.all_rows() if r.field_id == "study.phase")
        assert row.values[0].sources == "Amendment p.11 (printed p.9), 18 (printed p.16)"

    def test_a_page_without_a_printed_number_keeps_its_bare_reference(self) -> None:
        """Mixed is normal: the amendment's cover pages print nothing at all."""
        state = RunState(
            run_id="r-1",
            documents=[Document(id="d-prot", path="/in/Amendment.pdf",
                                kind="amendment", pages=137)],
            resolved=[ResolvedField(
                field_id="study.phase", value="phase_3", status="confirmed", confidence=0.95,
                quote="q",
                sources=[
                    Provenance(doc_id="d-prot", doc_kind="amendment", page=2),
                    Provenance(doc_id="d-prot", doc_kind="amendment", page=11, printed_page="9"),
                ],
            )],
        )
        row = next(r for r in build_report_model(state, get_registry()).all_rows()
                   if r.field_id == "study.phase")
        assert row.values[0].sources == "Amendment p.2, 11 (printed p.9)"

    def test_a_disagreements_positions_cite_both_numbers(self) -> None:
        c = Contradiction(
            field_id="study.duration_months", verdict="conflict", severity="high",
            explanation="The documents disagree.",
            records=[
                FieldRecord(
                    field_id="study.duration_months", group="study_design", raw_value="42",
                    value=42, quote="42 months", confidence=0.9,
                    provenance=Provenance(doc_id="d-prot", doc_kind="protocol",
                                          page=11, printed_page="9"),
                ),
                FieldRecord(
                    field_id="study.duration_months", group="study_design", raw_value="40",
                    value=40, quote="40 months", confidence=0.9,
                    provenance=Provenance(doc_id="d-rfp", doc_kind="rfp", page=2),
                ),
            ],
        )
        state = RunState(
            run_id="r-1",
            resolved=[_rf("study.duration_months", 42, status="needs_review",
                          pages=(11,), contradiction=c)],
            contradictions=[c],
        )
        [d] = build_report_model(state, get_registry()).decisions
        assert [p.sources for p in d.positions] == ["Protocol p.11 (printed p.9)", "RFP p.2"]


class TestRemovedPassages:
    """Appendix C — what MARK_OTHER_STUDY took out, as the report prints it."""

    def _passage(self, **kw: object) -> RemovedPassage:
        base: dict[str, object] = dict(
            doc_id="d-prot",
            section_id="d-prot:s042",
            heading="1.3.2 Clinical Experience",
            page_start=39,
            page_end=40,
            verdict="other_study",
            reason="Describes completed study NEOD001-001, not this study.",
            text="Study NEOD001-001 was an ongoing, open-label Phase 1/2 study.",
        )
        base.update(kw)
        return RemovedPassage.model_validate(base)

    def test_a_whole_section_is_named_with_its_document_and_page_range(self) -> None:
        state = RunState(
            run_id="r-1",
            documents=[Document(id="d-prot", path="/runs/r-1/inputs/Protocol v3.pdf",
                                kind="protocol", pages=112)],
            removed_passages=[self._passage()],
        )
        [r] = build_report_model(state, get_registry()).removed
        assert (r.doc_code, r.heading) == ("Protocol", "1.3.2 Clinical Experience")
        assert r.pages == "p.39-40"
        assert r.verdict == "whole section"
        assert r.reason == "Describes completed study NEOD001-001, not this study."

    def test_one_page_is_printed_as_one_page(self) -> None:
        state = RunState(run_id="r-1",
                         removed_passages=[self._passage(page_start=53, page_end=53)])
        [r] = build_report_model(state, get_registry()).removed
        assert r.pages == "p.53"

    def test_the_page_range_carries_the_printed_numbers(self) -> None:
        """Change 7: Appendix C cites a page like every other reference."""
        state = RunState(run_id="r-1", removed_passages=[self._passage(
            page_start=39, page_end=41, printed_page_start="37", printed_page_end="39",
        )])
        [r] = build_report_model(state, get_registry()).removed
        assert r.pages == "p.39-41 (printed p.37-39)"

    def test_a_removed_sentence_says_part_of_the_section(self) -> None:
        """`mixed` means the section stayed and one sentence went. A reader
        deciding whether to overturn needs to know which of the two happened."""
        state = RunState(run_id="r-1", removed_passages=[self._passage(verdict="mixed")])
        [r] = build_report_model(state, get_registry()).removed
        assert r.verdict == "part of the section"

    def test_line_breaks_in_the_removed_text_are_collapsed(self) -> None:
        state = RunState(run_id="r-1", removed_passages=[
            self._passage(text="Study NEOD001-001 was an\nongoing, open-label  Phase 1/2 study."),
        ])
        [r] = build_report_model(state, get_registry()).removed
        assert r.extract == "Study NEOD001-001 was an ongoing, open-label Phase 1/2 study."

    def test_a_long_passage_is_cut_and_marked_as_cut(self) -> None:
        """The whole passage is in extraction.json; Appendix C prints an opening."""
        state = RunState(run_id="r-1",
                         removed_passages=[self._passage(text="word " * 200)])
        [r] = build_report_model(state, get_registry()).removed
        assert len(r.extract) == REMOVED_EXTRACT_CHARS
        assert r.extract.endswith("…")

    def test_passages_are_ordered_by_document_then_page(self) -> None:
        state = RunState(run_id="r-1", removed_passages=[
            self._passage(doc_id="d-rfp", page_start=2, page_end=2, heading="1 Background"),
            self._passage(page_start=60, page_end=60, heading="7.1 Prior Experience"),
            self._passage(page_start=39, page_end=40),
        ])
        removed = build_report_model(state, get_registry()).removed
        assert [(r.doc_code, r.pages) for r in removed] == [
            ("d-prot", "p.39-40"), ("d-prot", "p.60"), ("d-rfp", "p.2"),
        ]

    def test_a_run_with_no_removals_has_no_appendix_c_content(self) -> None:
        """Every run before 2026-10-02 is this run, and gets its old report."""
        model = build_report_model(RunState(run_id="r-1"), get_registry())
        assert model.removed == []


class TestDocumentNames:
    def test_named_from_the_run_documents(self) -> None:
        state = RunState(
            run_id="r-1",
            documents=[Document(id="d-prot", path="/runs/r-1/inputs/Protocol v3.pdf",
                                kind="protocol", pages=112)],
            resolved=[_rf("ops.sites_total", 75)],
        )
        [ref] = build_report_model(state, get_registry()).documents
        assert (ref.code, ref.filename, ref.pages) == ("Protocol", "Protocol v3.pdf", 112)

    def test_two_documents_of_one_kind_are_numbered(self) -> None:
        state = RunState(run_id="r-1", resolved=[
            _rf("ops.sites_total", 75, doc="d-a", kind="rfp"),
            _rf("ops.crf_pages", 80, doc="d-b", kind="rfp"),
        ])
        codes = [d.code for d in build_report_model(state, get_registry()).documents]
        assert codes == ["RFP 1", "RFP 2"]


class TestValueWords:
    @pytest.mark.parametrize(("token", "words"), [
        ("true", "Yes"),
        ("false", "No"),
        ("not_specified", "Not specified"),
        ("phase_3", "Phase 3"),
        ("phase_1_2", "Phase 1/2"),
        ("double_blind", "Double-blind"),
        ("infusion_iv", "IV infusion"),
        ("pk_pd_sampling", "PK/PD sampling"),
        ("time_based", "Time-based"),
        ("ip_accountability", "IP accountability"),
        ("separate_blinded_unblinded_staff", "Separate blinded unblinded staff"),
    ])
    def test_humanize_enum(self, token: str, words: str) -> None:
        assert humanize_enum(token) == words

    def test_numbers_bools_durations_and_lists(self) -> None:
        reg = {f.id: f for f in get_registry().fields}
        assert format_value(20800, None) == ["20,800"]
        assert format_value(False, None) == ["No"]
        assert format_value({"n": 28, "unit": "days"}, reg["dosing.frequency"]) == ["Every 28 days"]
        assert format_value(["infusion_iv"], reg["ip.form"]) == ["IV infusion"]
        assert format_value(["a", "b"], None) == ["a", "b"]
        assert format_value(None, None) == ["—"]

    def test_free_text_is_left_as_written(self) -> None:
        reg = {f.id: f for f in get_registry().fields}
        # An enum-looking word in a free-text field is somebody's wording, not a code.
        population = reg["study.population"]
        assert format_value("confirmatory_trials", population) == ["confirmatory_trials"]
        assert format_value("confirmatory trials", reg["study.phase"]) == ["confirmatory trials"]


# --------------------------------------------------------------------------- #
# Real runs on this machine. runs/ is not in git, so this is skipped elsewhere.
# --------------------------------------------------------------------------- #


def _real_runs() -> list[Path]:
    if not RUNS_DIR.is_dir():
        return []
    return sorted(p.parent for p in RUNS_DIR.glob("*/extraction.json"))


def load_run_state(run: Path) -> RunState:
    d = json.loads((run / "extraction.json").read_text())
    return RunState(
        run_id=d["run_id"],
        resolved=[ResolvedField.model_validate(r) for r in d["resolved_fields"]],
        contradictions=[Contradiction.model_validate(c) for c in d["contradictions"]],
        # Kept in step with scripts/rerender_report.py's own load_run_state. Both
        # rebuild a RunState from extraction.json, and a key either forgets is a
        # section of report.pdf that silently disappears on a re-render.
        removed_passages=[
            RemovedPassage.model_validate(r) for r in d.get("removed_passages", [])
        ],
    )


@pytest.mark.skipif(not _real_runs(), reason="no run folders on this machine")
@pytest.mark.parametrize("run", _real_runs(), ids=lambda p: p.name)
def test_nothing_lost_on_a_real_run(run: Path) -> None:
    state = load_run_state(run)
    model = build_report_model(state, get_registry())
    assert_nothing_lost(state, get_registry(), model)


# The run fix 2 of the 2026-10-09 brief was found on. Kept by name because the
# brief's "done" is stated against it: Appendix A lists the amendment's 0 and the
# RFP's 1 separately, each with its own quote, and the All variables row lists
# only the page that says 0.
BRIEF_RUN = RUNS_DIR / "r-20261009-225414"


@pytest.mark.skipif(not (BRIEF_RUN / "extraction.json").is_file(),
                    reason="run r-20261009-225414 is not on this machine")
def test_each_side_of_a_disagreement_keeps_its_own_page_and_quote() -> None:
    state = load_run_state(BRIEF_RUN)
    model = build_report_model(state, get_registry())
    [c] = [c for c in state.contradictions if c.field_id == "interim.count"]
    quotes = {r.raw_value: r.quote for r in c.records}

    [row] = [r for r in model.all_rows() if r.field_id == "interim.count"]
    assert [(v.text, _parse_sources(v.sources)) for v in row.values] == [
        ("0", {"Amendment": {98}}),
    ]
    assert [
        (e.value, _parse_sources(e.sources), e.quote)
        for e in model.evidence if e.field_id == "interim.count"
    ] == [
        ("0", {"Amendment": {98}}, quotes["0"]),
        ("1", {"RFP": {4}}, quotes["1"]),
    ]

    # The two rows where no value won print "—" with no page beside it, and
    # their records are all in Appendix A.
    for field_id, scope in (("timeline.total_duration", "study"),
                            ("blinding.placebo_matching", None)):
        [row] = [r for r in model.all_rows()
                 if r.field_id == field_id and r.scope == scope]
        assert [(v.text, v.sources) for v in row.values] == [("—", "")]
