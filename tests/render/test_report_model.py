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
    Schedule,
)
from rfp_intake.normalize.scope import normalize_scope
from rfp_intake.render.report_model import (
    _STATUS_RANK,
    NEEDS_DECISION,
    REASON_DISMISSED_DISAGREEMENT,
    REMOVED_EXTRACT_CHARS,
    SCHEDULE_THRESHOLD,
    ReportModel,
    Row,
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

    def named(scope: str | None) -> str | None:
        # The report names document ids in a scope — RECONCILE writes one into
        # the scope of an entry it kept out of the cross-document match.
        if scope is None:
            return None
        for doc_id, code in codes.items():
            scope = re.sub(rf"\b{re.escape(doc_id)}\b", code, scope, flags=re.IGNORECASE)
        return scope

    # A counted visit table keeps the model's own visit frequencies, and a "not
    # found" total beside the stage totals, in extraction.json only (Prompt C
    # follow-ups 1 and 2; quoted or not since 2026-10-19). Nothing else may be
    # kept out, and no total a document states.
    for k in model.kept_out:
        kind = field_by_id[k.field_id].from_schedule
        assert kind == "table" or (
            kind == "stage_totals" and k.status == "not_found" and not k.derived_from
        ), k.field_id

    for rf in state.resolved:
        if any(rf is k for k in model.kept_out):
            continue
        fd = field_by_id.get(rf.field_id)
        if fd is None:
            # A variable config/fields.yaml no longer defines (visit intensity,
            # the placebo assumption, since 29 October 2026): a saved run's
            # rows for it are left out of the report, which prints only the
            # registry's variables.
            assert rf.field_id not in {r.field_id for r in rows}, rf.field_id
            continue
        if rf.status == "not_found":
            want = "—"
        elif rf.status == "not_applicable":
            # Prompt B item 4: a question a config rule says does not arise reads
            # as `report_wording.not_applicable`. Run r-20261016-prompt-b is the
            # first saved run with such a row.
            want = registry.report_wording.not_applicable
        else:
            want = "; ".join(format_value(rf.value, fd))
        homes = [
            (row, line)
            for row in rows
            if row.field_id == rf.field_id
            and normalize_scope(row.scope) == normalize_scope(named(rf.scope))
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
                # Appendix A prints a quote without the spaces around it, so two
                # passages differing only by a trailing space are one line.
                assert any(
                    e.field_id == rf.field_id and e.value == said
                    and e.quote.strip() == r.quote.strip()
                    and _parse_sources(e.sources)
                    == {codes[r.provenance.doc_id]: {r.provenance.page}}
                    for e in model.evidence
                ), (
                    f"{rf.field_id}: {said!r} from {codes[r.provenance.doc_id]} "
                    f"p.{r.provenance.page} has no Appendix A line of its own"
                )
        # Each document's own statement of an entry has its quote and page in
        # Appendix A: the entries and periods rows say other wordings are there.
        for r in rf.statements:
            assert any(
                e.field_id == rf.field_id and e.quote == r.quote
                and codes[r.provenance.doc_id] in _parse_sources(e.sources)
                and r.provenance.page in _parse_sources(e.sources)[codes[r.provenance.doc_id]]
                for e in model.evidence
            ), (
                f"{rf.field_id}: {codes[r.provenance.doc_id]} p.{r.provenance.page}'s "
                "statement has no Appendix A line"
            )
        # A folded rewording was checked, and its text and quote are in Appendix A.
        if line in row.folded:
            assert row.verdict == "not_a_conflict" and not row.budget_driver
            assert any(e.field_id == rf.field_id and e.value == want for e in model.evidence)
        # Each page's own quote is in Appendix A beside that page (fix 11 of the
        # 2026-10-12 brief), so no page is quoted with another page's words.
        if len(rf.source_quotes) == len(rf.sources):
            for source, quote in zip(rf.sources, rf.source_quotes, strict=True):
                assert any(
                    e.field_id == rf.field_id and e.quote == quote
                    and source.page in _parse_sources(e.sources).get(codes[source.doc_id], set())
                    for e in model.evidence
                ), f"{rf.field_id}: p.{source.page}'s own quote is not in Appendix A"
        # A record ADJUDICATE called off-topic has no row but its own line in
        # Appendix A, marked (fix 5 of the 2026-10-12 brief).
        c = rf.contradiction
        if c is not None and c.field_id == rf.field_id:
            for i in c.off_topic_record_indexes:
                r = c.records[i - 1]
                assert not any(e is r for row in rows for line in row.values for e in line.entries)
                assert any(
                    e.field_id == rf.field_id and e.quote == r.quote
                    and e.marker == registry.report_wording.off_topic
                    and _parse_sources(e.sources)
                    == {codes[r.provenance.doc_id]: {r.provenance.page}}
                    for e in model.evidence
                ), f"{rf.field_id}: off-topic record p.{r.provenance.page} not in Appendix A"
        # Its quote is in Appendix A: beside its value; or, for a list printed
        # once, as one of the passages under it; or, for a row of statements,
        # as the statement it is (Prompt B item 8(d)).
        if rf.quote:
            lines = [e for e in model.evidence if e.field_id == rf.field_id]
            beside = any(e.quote == rf.quote and e.value == want for e in lines)
            under = any(e.value == want and e.quote is None for e in lines) and any(
                e.value == "" and e.quote == rf.quote for e in lines
            )
            on_statement = any(r.quote == rf.quote for r in rf.statements) and any(
                e.quote == rf.quote for e in lines
            )
            assert beside or under or on_statement, (
                f"quote for {rf.field_id} missing from the evidence appendix"
            )

    # A row's sides show every value of its disagreement, and a side's pages are
    # pages that state its value (pipeline-rules-reviewer, 2026-10-12).
    for row in rows:
        if not row.sides:
            continue
        own = next(
            c for line in row.values for e in line.entries
            if (c := _own_disagreement_of(e)) is not None
        )
        fd = field_by_id.get(row.field_id)
        by_side: dict[tuple[str, int], list[str]] = {}
        for side in row.sides:
            for code, pages in _parse_sources(side.sources).items():
                for page in pages:
                    by_side.setdefault((code, page), []).append(_same_key([side.text]))
        stated: set[tuple[tuple[str, int], str]] = set()
        for r in own.records:
            said = _same_key(["; ".join(format_value(
                r.value if r.value is not None else r.raw_value, fd))])
            at = (codes[r.provenance.doc_id], r.provenance.page)
            stated.add((at, said))
            assert said in by_side.get(at, []), (
                f"{row.field_id}: {said!r} on {at} is on no side of the row"
            )
        for at, texts in by_side.items():
            for text in texts:
                assert (at, text) in stated, (
                    f"{row.field_id}: a side cites {at} for {text!r}, which it does not state"
                )

    # Every adjudicated disagreement is either a decision or listed as dismissed,
    # except one of a variable whose every entry is kept out above (2026-10-19).
    printed = {rf.field_id for rf in state.resolved if not any(rf is k for k in model.kept_out)}
    hidden = {k.field_id for k in model.kept_out} - printed
    # A disagreement about a variable the registry no longer defines is left
    # out with that variable's rows.
    adjudicated = [
        c for c in state.contradictions
        if c.verdict is not None and c.field_id not in hidden and c.field_id in field_by_id
    ]
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
    if c is None or c.field_id != rf.field_id:
        return None
    # A not_a_conflict written as one row names its record (Prompt B item 2).
    one_row = c.verdict in ("conflict", "reconcilable") or (
        c.verdict == "not_a_conflict" and c.winning_record_index is not None
    )
    return c if one_row else None


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
    off = set(c.off_topic_record_indexes)
    return {
        (codes[r.provenance.doc_id], r.provenance.page)
        for i, r in enumerate(c.records, start=1)
        if i not in off
        # Compared joined the way `want` was, so a value of several items
        # matches itself rather than failing on the separator.
        and _same_key(["; ".join(format_value(
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

    def test_a_field_no_longer_in_the_registry_is_left_out(self) -> None:
        # 29 October 2026: the visit intensity and placebo assumption rows are
        # gone from config/fields.yaml, and a saved run's extraction.json still
        # holds them. The report prints the registry's variables only, and a
        # re-render of such a run does not fail.
        c = _contradiction("retired.field", "conflict", [
            ("a", 3, "d-prot"), ("b", 4, "d-rfp"),
        ])
        state = RunState(run_id="r-1", resolved=[
            _rf("retired.field", "kept"), _rf("ops.sites_total", 75, pages=(5,)),
        ], contradictions=[c])
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        assert not any(r.field_id == "retired.field" for r in model.all_rows())
        assert not any(e.field_id == "retired.field" for e in model.evidence)
        assert model.decisions == []
        assert model.variables_total == len(get_registry().fields)


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
            field_id="ops.sites_total", verdict="conflict", severity="high",
            explanation="The documents disagree.",
            records=[
                FieldRecord(
                    field_id="ops.sites_total", group="operational_metrics", raw_value="42",
                    value=42, quote="42 sites", confidence=0.9,
                    provenance=Provenance(doc_id="d-prot", doc_kind="protocol",
                                          page=11, printed_page="9"),
                ),
                FieldRecord(
                    field_id="ops.sites_total", group="operational_metrics", raw_value="40",
                    value=40, quote="40 sites", confidence=0.9,
                    provenance=Provenance(doc_id="d-rfp", doc_kind="rfp", page=2),
                ),
            ],
        )
        state = RunState(
            run_id="r-1",
            resolved=[_rf("ops.sites_total", 42, status="needs_review",
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
        # Prompt B item 8: "Every" comes from `amount_wording`, not the field id.
        # The requested monitoring frequency has none since 29 October 2026: it
        # is given in the document's own words.
        assert format_value({"n": 8, "unit": "weeks"}, reg["monitoring.frequency_spec"]) == [
            "8 weeks"
        ]
        assert format_value({"n": 42, "unit": "months"}, reg["timeline.total_duration"]) == [
            "42 months"
        ]
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
        schedules=[Schedule.model_validate(s) for s in d.get("schedules", [])],
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
    for field_id, scope in (("timeline.total_duration", "Study"),
                            ("blinding.placebo_matching", None)):
        [row] = [r for r in model.all_rows()
                 if r.field_id == field_id and r.scope == scope]
        assert [(v.text, v.sources) for v in row.values] == [("—", "")]


class TestArmsMatchedAcrossDocuments:
    """design.parts after the 2026-10-12 arm-matching change, through RECONCILE,
    GATE and the report: run r-20261009-225414's entries give two arm rows, each
    citing the amendment and the RFP."""

    @staticmethod
    def _report(records: list[FieldRecord]) -> tuple[RunState, ReportModel]:
        from rfp_intake.gate import gate_node
        from rfp_intake.reconcile import reconcile_node
        from tests.reconcile.test_scopes_across_documents import AMENDMENT, RFP

        state = RunState(
            run_id="r-1",
            documents=[
                Document(id=AMENDMENT, path="/runs/r-1/inputs/Amendment.pdf",
                         kind="amendment", pages=120),
                Document(id=RFP, path="/runs/r-1/inputs/RFP.pdf", kind="rfp", pages=6),
            ],
            records=records,
        )
        state.resolved = reconcile_node(state)["resolved"]
        state.resolved = gate_node(state)["resolved"]
        return state, build_report_model(state, get_registry())

    @staticmethod
    def _parts_rows(model: ReportModel) -> list:  # type: ignore[type-arg]
        return [r for r in model.all_rows() if r.field_id == "design.parts"]

    def test_two_arm_rows_each_citing_both_documents(self) -> None:
        from tests.reconcile.test_scopes_across_documents import run_20261009_225414_records

        state, model = self._report(run_20261009_225414_records())
        rows = self._parts_rows(model)

        assert [r.scope for r in rows] == ["Arm: NEOD001", "Arm: Placebo"]
        for row in rows:
            [line] = row.values
            assert "Amendment p." in line.sources and "RFP p.3" in line.sources
        assert_nothing_lost(state, get_registry(), model)

    def test_an_arm_one_document_lists_says_so_by_document_name(self) -> None:
        from tests.reconcile.test_scopes_across_documents import AMENDMENT, _entry, _rfp

        state, model = self._report([
            _entry("Arm A: DrugX 10 mg daily", "arm:DrugX", AMENDMENT, 4),
            _rfp("Arm A: DrugX 10 mg daily", "arm:DrugX"),
            _rfp("Arm B: Placebo", "arm:Placebo"),
        ])
        by_scope = {r.scope: r for r in self._parts_rows(model)}

        assert by_scope["Arm: DrugX"].status == "confirmed"
        placebo = by_scope["Arm: Placebo"]
        assert placebo.status == "needs_review"
        assert placebo.reasons == ["Listed only in RFP, not in Amendment."]
        assert_nothing_lost(state, get_registry(), model)

    def test_an_unmatched_entry_is_named_by_document(self) -> None:
        from tests.reconcile.test_scopes_across_documents import AMENDMENT, _entry, _rfp

        state, model = self._report([
            _entry("Control arm: normal saline IV", "arm:Placebo", AMENDMENT, 20),
            _rfp("Group 2: Placebo + standard of care", "arm:Placebo"),
        ])
        scopes = sorted(r.scope or "" for r in self._parts_rows(model))

        assert scopes == ["Arm: Placebo", "Arm: Placebo (not matched, Amendment)"]
        assert_nothing_lost(state, get_registry(), model)


class TestAppendixAMarkers:
    """CLAUDE.md open item 3: each Appendix A passage behind a numbered
    Disagreement says which side the report used. The wording is read from
    `report_wording` in config/fields.yaml, never from Python."""

    SHOWN = "Shown in the report (provisional, by document precedence)"

    def _state(self, *, resolved_value: object, index: int | None,
               verdict: str = "conflict") -> RunState:
        # The amendment states 0 on two pages, the RFP 1 on one.
        c = _contradiction("interim.count", verdict,
                           [("0", 98, "d-prot"), ("1", 4, "d-rfp"), ("0", 50, "d-prot")])
        c = c.model_copy(update={
            "resolved_value": resolved_value,
            "winning_doc_id": "d-prot" if resolved_value is not None else None,
            "winning_record_index": index,
        })
        rf = _rf("interim.count", resolved_value, status="needs_review", contradiction=c,
                 quote="0" if resolved_value is not None else None)
        rf.sources = [r.provenance for r in c.records]
        return RunState(run_id="r-1", resolved=[rf], contradictions=[c])

    def _markers(self, model: ReportModel) -> dict[str, str | None]:
        return {e.sources: e.marker for e in model.evidence if e.field_id == "interim.count"}

    def test_the_chosen_side_is_shown_and_the_other_is_not_used(self) -> None:
        model = build_report_model(self._state(resolved_value="0", index=1), get_registry())
        [d] = model.decisions
        assert self._markers(model) == {
            "Protocol p.98": self.SHOWN,
            # Another page stating the shown value agrees with the report, so it
            # is not "not used".
            "Protocol p.50": self.SHOWN,
            "RFP p.4": f"Not used — see Disagreement {d.no}",
        }

    def test_a_run_made_before_the_record_was_named_is_marked_by_value(self) -> None:
        """Runs before 2026-10-12 carry no winning_record_index for a conflict."""
        model = build_report_model(self._state(resolved_value="0", index=None), get_registry())
        markers = self._markers(model)
        assert markers["Protocol p.98"] == markers["Protocol p.50"] == self.SHOWN
        assert markers["RFP p.4"] == "Not used — see Disagreement 1"

    def test_every_passage_says_no_value_was_chosen(self) -> None:
        model = build_report_model(self._state(resolved_value=None, index=None), get_registry())
        assert set(self._markers(model).values()) == {"No value chosen — see Disagreement 1"}

    def test_a_reconcilable_disagreement_has_no_number_and_no_marker(self) -> None:
        """Oliver, 2026-10-12: the 236 subjects and the schedule "No" stay unmarked."""
        model = build_report_model(
            self._state(resolved_value="0", index=1, verdict="reconcilable"), get_registry(),
        )
        assert model.decisions == []
        assert set(self._markers(model).values()) == {None}

    def test_the_number_is_the_disagreements_own(self) -> None:
        """A second, higher-severity disagreement takes number 1."""
        state = self._state(resolved_value=None, index=None)
        first = _contradiction("timeline.total_duration", "conflict",
                               [("42 months", 24, "d-prot"), ("40 months", 3, "d-rfp")])
        state.contradictions.insert(0, first)
        state.contradictions[1] = state.contradictions[1].model_copy(update={"severity": "medium"})
        state.resolved[0].contradiction = state.contradictions[1]
        model = build_report_model(state, get_registry())
        [no] = [d.no for d in model.decisions if d.field_id == "interim.count"]
        assert no == 2
        assert set(self._markers(model).values()) == {"No value chosen — see Disagreement 2"}

    def test_the_wording_comes_from_the_registry(self) -> None:
        wording = get_registry().report_wording.model_copy(update={
            "shown_provisional": "Used", "not_used": "Set aside ({n})",
            "no_value_chosen": "Open ({n})",
        })
        registry = get_registry().model_copy(update={"report_wording": wording})
        model = build_report_model(self._state(resolved_value="0", index=1), registry)
        assert self._markers(model) == {
            "Protocol p.98": "Used", "Protocol p.50": "Used", "RFP p.4": "Set aside (1)",
        }

    def test_a_passage_behind_no_disagreement_has_no_marker(self) -> None:
        state = RunState(run_id="r-1", resolved=[_rf("ops.sites_total", 75, quote="75 sites")])
        model = build_report_model(state, get_registry())
        assert [e.marker for e in model.evidence] == [None]


class TestANotAConflictWrittenAsOneRow:
    """Prompt B item 2: "Schedule of activities present" was nine rows in run
    r-20261013-prompt-c. ADJUDICATE now writes one; the report cites only the
    pages that state its answer and lists every record in Appendix A."""

    def _state(self) -> RunState:
        c = _contradiction("visits.schedule_present", "not_a_conflict", [
            ("yes_body", 13, "d-prot"), ("yes_body", 26, "d-prot"),
            ("yes_appendix", 111, "d-prot"), ("no", 1, "d-rfp"),
        ], severity="low")
        c = c.model_copy(update={
            "resolved_value": "yes_body", "winning_doc_id": "d-prot", "winning_record_index": 2,
        })
        rf = _rf("visits.schedule_present", "yes_body", status="needs_review",
                 contradiction=c, quote="yes_body")
        rf.sources = [r.provenance for r in c.records]
        return RunState(run_id="r-1", resolved=[rf], contradictions=[c])

    def test_one_row_citing_only_the_pages_that_state_it(self) -> None:
        state = self._state()
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        [row] = [r for r in model.all_rows() if r.field_id == "visits.schedule_present"]
        [line] = row.values
        assert line.sources == "Protocol p.13, 26"
        assert row.where_notes == [get_registry().report_wording.others_checked]
        assert row.decision_no is None

    def test_every_record_is_in_appendix_a(self) -> None:
        model = build_report_model(self._state(), get_registry())
        listed = {(e.value, e.sources) for e in model.evidence
                  if e.field_id == "visits.schedule_present"}
        assert {sources for _, sources in listed} == {
            "Protocol p.13", "Protocol p.26", "Protocol p.111", "RFP p.1",
        }

    def test_a_row_per_record_is_unchanged(self) -> None:
        state = self._state()
        c = state.contradictions[0].model_copy(update={
            "resolved_value": None, "winning_doc_id": None, "winning_record_index": None,
        })
        state.contradictions[0] = c
        state.resolved = [
            _rf("visits.schedule_present", r.value, status="needs_review", contradiction=c,
                pages=(r.provenance.page,), doc=r.provenance.doc_id)
            for r in c.records
        ]
        model = build_report_model(state, get_registry())
        # No note on this variable's rows (a not-found variable's own note, in
        # its Where cell since 29 October 2026, is another matter).
        assert not any(r.where_notes for r in model.all_rows()
                       if r.field_id == "visits.schedule_present")


class TestBothSidesOnTheRow:
    """Fix 2 of the 2026-10-12 brief: a Disagreement row states both sides."""

    def _state(self, *, resolved_value: object, records: list[tuple[str, int, str]],
               field_id: str = "interim.count") -> RunState:
        c = _contradiction(field_id, "conflict", records)
        c = c.model_copy(update={
            "resolved_value": resolved_value,
            "winning_doc_id": "d-prot" if resolved_value is not None else None,
            "winning_record_index": 1 if resolved_value is not None else None,
        })
        rf = _rf(field_id, resolved_value, status="needs_review", contradiction=c,
                 quote=c.records[0].quote if resolved_value is not None else None)
        rf.sources = [r.provenance for r in c.records]
        return RunState(run_id="r-1", resolved=[rf], contradictions=[c])

    def _row(self, state: RunState):  # type: ignore[no-untyped-def]
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        [row] = [r for r in model.all_rows() if r.field_id == state.resolved[0].field_id]
        return row

    def test_each_document_is_one_side_with_its_value_page_and_quote(self) -> None:
        row = self._row(self._state(
            resolved_value="0", records=[("0", 98, "d-prot"), ("1", 4, "d-rfp")],
        ))
        assert [(s.sources, s.text, s.quote) for s in row.sides] == [
            ("Protocol p.98", "0", "0"), ("RFP p.4", "1", "1"),
        ]
        assert row.decision_line == (
            "Shown provisionally: 0 (the protocol normally takes precedence). "
            "See Disagreement 1."
        )
        assert [v.on_sides for v in row.values] == [True]

    def test_the_line_names_the_rule_that_chose_the_value(self) -> None:
        # Item 1 of Prompt B, 2026-10-16: the line said the document "normally
        # takes precedence" when the highest confidence had chosen it.
        state = self._state(
            resolved_value="0", records=[("0", 98, "d-prot"), ("1", 4, "d-rfp")],
        )
        c = state.contradictions[0].model_copy(update={"precedence_rule": "specificity"})
        state.contradictions[0] = c
        state.resolved[0].contradiction = c
        # main's config/fields.yaml words `specificity` without "confidence"
        # (29 October 2026); this branch's copy still has the older wording, so
        # this test passes once the two are merged.
        assert self._row(state).decision_line == (
            "Shown provisionally: 0 (the protocol states it most clearly). "
            "See Disagreement 1."
        )

    def test_no_value_chosen_says_so(self) -> None:
        row = self._row(self._state(
            resolved_value=None, records=[("0", 98, "d-prot"), ("1", 4, "d-rfp")],
        ))
        assert row.decision_line == "No value chosen. See Disagreement 1."

    def test_each_wording_is_its_own_side_with_only_its_pages(self) -> None:
        """Overall study duration: the amendment says 42 months two ways."""
        state = self._state(resolved_value=None, field_id="timeline.total_duration", records=[
            ("approximately 42 months", 15, "d-prot"),
            ("42 months (24 months accrual + 18 months follow-up)", 24, "d-prot"),
            ("40 months", 3, "d-rfp"),
        ])
        state.resolved[0].scope = "study"
        # all_rows() gives the scopes row's part, the "Whole study" one.
        study = self._row(state)
        assert study.heading == "Whole study"
        assert [(s.sources, s.text, s.quote) for s in study.sides] == [
            ("Protocol p.15", "approximately 42 months", None),
            ("Protocol p.24", "42 months (24 months accrual + 18 months follow-up)", None),
            ("RFP p.3", "40 months", "40 months"),
        ]

    def test_a_document_giving_two_values_shows_both(self) -> None:
        """Probed by the pipeline-rules-reviewer on 2026-10-12: one side per
        document printed 0 beside p.50, which says 2, and dropped the 2."""
        row = self._row(self._state(
            resolved_value="0",
            records=[("0", 98, "d-prot"), ("2", 50, "d-prot"), ("1", 4, "d-rfp")],
        ))
        assert [(s.sources, s.text) for s in row.sides] == [
            ("Protocol p.98", "0"), ("Protocol p.50", "2"), ("RFP p.4", "1"),
        ]

    def test_one_passage_on_two_records_is_one_quote(self) -> None:
        """The amendment's placebo sentence came back twice from page 53."""
        row = self._row(self._state(
            resolved_value=None, field_id="blinding.placebo_matching", records=[
                ("not_matching_stated", 53, "d-prot"), ("not_matching_stated", 53, "d-prot"),
                ("matching_stated", 3, "d-rfp"),
            ],
        ))
        assert [(s.text, s.quote) for s in row.sides] == [
            ("No, not matching as supplied", "not_matching_stated"),
            ("Yes, matching as supplied", "matching_stated"),
        ]

    def test_a_long_quote_is_cut_to_its_first_sentence(self) -> None:
        from rfp_intake.render.report_model import _first_sentence

        assert _first_sentence(
            "A matching placebo will not be provided.   Subjects who are randomized"
        ) == ("A matching placebo will not be provided.", False)
        assert _first_sentence("Interim Efficacy Analysis\n   Performed after 156 events") == (
            "Interim Efficacy Analysis Performed after 156 events", False
        )
        # Cut at a word, and marked as cut, so the renderer puts the ellipsis
        # outside the quotation marks.
        text, cut = _first_sentence("word " * 50)
        assert cut and len(text) <= 160 and text.endswith("word")

    def test_a_row_with_no_disagreement_has_no_sides(self) -> None:
        state = RunState(run_id="r-1", resolved=[_rf("ops.sites_total", 75)])
        assert self._row(state).sides == []


class TestARowFollowingAnotherDisagreement:
    """Fix 2 of the 2026-10-12 brief: the interim timing row."""

    def _state(self, timing_doc: str) -> RunState:
        c = _contradiction("interim.count", "conflict", [("0", 98, "d-prot"), ("1", 4, "d-rfp")])
        c = c.model_copy(update={
            "resolved_value": "0", "winning_doc_id": "d-prot", "winning_record_index": 1,
        })
        count = _rf("interim.count", "0", status="needs_review", contradiction=c, quote="0")
        count.sources = [r.provenance for r in c.records]
        kind = "rfp" if timing_doc == "d-rfp" else "protocol"
        timing = _rf("interim.timing", ["after 156 events"], status="needs_review",
                     scope="total", pages=(4,), doc=timing_doc, kind=kind, contradiction=c)
        return RunState(run_id="r-1", resolved=[count, timing], contradictions=[c])

    def _row(self, state: RunState):  # type: ignore[no-untyped-def]
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        [row] = [r for r in model.all_rows() if r.field_id == "interim.timing"]
        return row

    def test_only_one_side_gives_a_timing(self) -> None:
        row = self._row(self._state("d-rfp"))
        assert row.depends_on == "Depends on Disagreement 1"
        # The pages are in the Where cell, not in the sentence (29 October 2026).
        assert row.follow_lines == [
            "Only the RFP gives a timing: after 156 events.",
            "If the protocol is right, there is no interim analysis to time.",
        ]
        assert row.follow_pages == "RFP p.4"
        [printed] = row.table
        assert (printed.values, printed.alert, printed.status, printed.where) == (
            row.follow_lines, "Depends on Disagreement 1", "Review", "RFP p.4",
        )
        # The row's sides are the count's, so it has none of its own.
        assert row.sides == []

    def test_the_second_sentence_needs_the_other_side_to_say_none(self) -> None:
        """Probed by the pipeline-rules-reviewer on 2026-10-12: a protocol count
        of 2 printed "If the protocol is right, there is no interim analysis"."""
        state = self._state("d-rfp")
        c = state.contradictions[0]
        records = [c.records[0].model_copy(update={"value": "2"}), c.records[1]]
        c = c.model_copy(update={"records": records, "resolved_value": "2"})
        for rf in state.resolved:
            rf.contradiction = c
        state.contradictions = [c]
        state.resolved[0].value = "2"
        assert self._row(state).follow_lines == [
            "Only the RFP gives a timing: after 156 events.",
        ]

    def test_the_wording_comes_from_the_registry(self) -> None:
        from rfp_intake.domain.registry import FollowWording

        registry = get_registry()
        fd = registry.get_field("interim.timing")
        fields = [
            f.model_copy(update={"follow_wording": FollowWording(
                only_some="{docs}: {value}", if_others_right="not {others}",
                when_others_say=["0"],
            )}) if f is fd else f
            for f in registry.fields
        ]
        model = build_report_model(
            self._state("d-rfp"), registry.model_copy(update={"fields": fields}),
        )
        [row] = [r for r in model.all_rows() if r.field_id == "interim.timing"]
        assert row.follow_lines == ["RFP: after 156 events", "not protocol"]


def _whole_study_parts(model: ReportModel) -> list[Row]:
    """The whole study duration's rows: printed under the patient timeline
    (`shown_under`) since 29 October 2026, so not rows of a group of their own."""
    assert not [r for g in model.groups for r in g.rows
                if r.field_id == "timeline.total_duration"]
    return [u for g in model.groups for r in g.rows for u in r.under
            if u.field_id == "timeline.total_duration"]


class TestScopesDisplay:
    """Fix 6 of the 2026-10-12 brief: Overall study duration as one row, its
    parts printed under the patient timeline since 29 October 2026."""

    def test_the_whole_study_comes_first_under_its_heading(self) -> None:
        state = RunState(run_id="r-1", resolved=[
            _rf("timeline.total_duration", "about 4 years", scope="subject", pages=(47,)),
            _rf("timeline.total_duration", "42 months", scope="study", pages=(15,)),
        ])
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        parts = _whole_study_parts(model)
        assert [(p.heading, p.values[0].text) for p in parts] == [
            ("Whole study", "42 months"),
            ("Time each patient spends in the study", "about 4 years"),
        ]
        # The parts are the rows; the row holding them is not counted twice.
        # Capitalised as every label the documents word is (stage 6, 2026-10-12).
        assert [r.scope for r in model.all_rows()
                if r.field_id == "timeline.total_duration"] == ["Study", "Subject"]
        # Printed as rows under the patient timeline's, each with its page.
        [timeline] = [r for g in model.groups for r in g.rows
                      if r.field_id == "timeline.periods"]
        assert [(t.variable, t.values, t.where) for t in timeline.table[-2:]] == [
            ("Whole study", ["42 months"], "Protocol p.15"),
            ("Time each patient spends in the study", ["about 4 years"], "Protocol p.47"),
        ]

    def test_a_scope_no_heading_names_comes_last_under_its_own_words(self) -> None:
        state = RunState(run_id="r-1", resolved=[
            _rf("timeline.total_duration", "6 months", scope="Part B", pages=(9,)),
            _rf("timeline.total_duration", "42 months", scope="study", pages=(15,)),
        ])
        model = build_report_model(state, get_registry())
        assert [p.heading for p in _whole_study_parts(model)] == ["Whole study", "Part B"]

    def test_the_row_takes_its_disagreement(self) -> None:
        c = _contradiction("timeline.total_duration", "conflict",
                           [("42 months", 24, "d-prot"), ("40 months", 3, "d-rfp")])
        study = _rf("timeline.total_duration", None, status="needs_review", scope="study",
                    contradiction=c, quote=None, confidence=0.0)
        study.sources = [r.provenance for r in c.records]
        state = RunState(run_id="r-1", contradictions=[c], resolved=[
            study, _rf("timeline.total_duration", "about 4 years", scope="subject", pages=(47,)),
        ])
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        whole, _ = _whole_study_parts(model)
        assert whole.decision_no == 1
        assert whole.status == "needs_review"
        assert whole.decision_line == "No value chosen. See Disagreement 1."
        # The disagreement keeps its number, under the variable's own label.
        [d] = model.decisions
        assert (d.no, d.label) == (1, "Whole study duration")


@pytest.fixture
def _with_the_blinding_block(fields_yaml_path: Path, tmp_path: Path):  # type: ignore[no-untyped-def]
    """config/fields.yaml with the blinding group's block of the 2026-10-12
    brief put back. No group in config uses `block` since 29 October 2026
    (Angus Gray), and the code path stays working for a registry that does
    (layout rule 15), so these tests give it one. The block is as it was less
    blinding.placebo_assumption, which is gone from the registry."""
    import yaml

    data = yaml.safe_load(fields_yaml_path.read_text())
    group = next(g for g in data["groups"] if g["id"] == "blinding_monitoring")
    group["block"] = {
        "label": "Unblinded staff and monitoring",
        "headline": "monitoring.unblinded_required",
        "replaces": ["blinding.placebo_matching", "blinding.unblinded_staff_stated",
                     "monitoring.unblinded_required", "monitoring.unblinded_factors",
                     "monitoring.unblinded_rationale"],
        "lines": [
            {"field": "blinding.unblinded_staff_stated", "label": "Stated in the documents"},
            {"field": "blinding.placebo_matching"},
            {"field": "ops.visits_pharmacy_monitoring",
             "wording": "{doc} asks for pharmacy monitoring visits: {value} ({pages})."},
        ],
    }
    path = tmp_path / "fields_with_block.yaml"
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False))
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(path)
    get_registry.cache_clear()
    yield
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    get_registry.cache_clear()


@pytest.mark.usefixtures("_with_the_blinding_block")
class TestTheBlindingBlock:
    """Fix 9 of the 2026-10-12 brief: the blinding rows are one row, with the
    variables kept as its members for the counts and in Appendix A."""

    FIVE = {
        "blinding.placebo_matching", "blinding.unblinded_staff_stated",
        "monitoring.unblinded_required", "monitoring.unblinded_factors",
        "monitoring.unblinded_rationale",
    }

    def _model(self) -> tuple[RunState, ReportModel]:
        c = _contradiction("blinding.placebo_matching", "conflict", [
            ("not_matching_stated", 53, "d-prot"), ("matching_stated", 3, "d-rfp"),
        ])
        matching = _rf("blinding.placebo_matching", None, status="needs_review",
                       contradiction=c, quote=None)
        matching.sources = [r.provenance for r in c.records]
        state = RunState(run_id="r-1", resolved=[
            _rf("blinding.unblinded_staff_stated", "true", status="needs_review", pages=(52,),
                quote="The Unblinded Pharmacist will be provided access to the treatment "
                      "assignment. Nobody else will."),
            matching,
            _rf("monitoring.unblinded_required", "likely", status="needs_review", pages=(12,)),
            _rf("ops.visits_pharmacy_monitoring", "4 visits per site", pages=(4,),
                doc="d-rfp", kind="rfp"),
            _rf("ip.form", ["infusion_iv"], status="needs_review", pages=(54,),
                quote="Study drug consists of IV NEOD001 or placebo."),
            _rf("blinding.design", "double_blind", status="needs_review", pages=(11,)),
        ], contradictions=[c])
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        return state, model

    @staticmethod
    def _block(model: ReportModel) -> Row:
        [group] = [g for g in model.groups if g.group.id == "blinding_monitoring"]
        [row] = [r for r in group.rows if r.members]
        return row

    def test_one_row_takes_the_place_of_the_six(self) -> None:
        _, model = self._model()
        [group] = [g for g in model.groups if g.group.id == "blinding_monitoring"]
        assert [r.label for r in group.rows] == [
            "Pharmacy monitoring visits", "Pharmacy monitoring frequency", "Blinding design",
            "Unblinded staff and monitoring",
        ]
        assert {m.field_id for m in self._block(model).members} == self.FIVE

    def test_the_headline_is_the_monitoring_answer_in_words(self) -> None:
        _, model = self._model()
        row = self._block(model)
        assert row.field_id == "monitoring.unblinded_required"
        assert [v.text for v in row.values] == ["Likely needed"]
        assert row.budget_driver

    def test_the_lines_read_as_the_brief_lays_them_out(self) -> None:
        _, model = self._model()
        lines = [(t.indent, t.text) for t in self._block(model).block_lines]
        assert lines[:4] == [
            (0, 'Stated in the documents: Yes — "The Unblinded Pharmacist will be provided '
                'access to the treatment assignment." (Protocol p.52)'),
            (0, "Placebo matches the study drug: documents disagree (Disagreement 1)"),
            (1, 'Protocol p.53: "not_matching_stated"'),
            (1, 'RFP p.3: "matching_stated"'),
        ]
        assert lines[4] == (
            0, "RFP asks for pharmacy monitoring visits: 4 visits per site (RFP p.4).",
        )

    def test_a_member_with_no_line_says_it_was_not_found(self) -> None:
        _, model = self._model()
        texts = [t.text for t in self._block(model).block_lines]
        assert "Factors driving unblinded monitoring: not found" in texts

    def test_the_block_reads_disagreement_and_the_counts_still_see_each_member(self) -> None:
        _, model = self._model()
        row = self._block(model)
        assert (row.status, row.decision_no) == ("needs_review", 1)
        reviewed = {r.field_id for r in model.review_rows()}
        assert {"blinding.unblinded_staff_stated", "monitoring.unblinded_required"} <= reviewed
        assert "blinding.placebo_matching" not in reviewed  # a page-1 decision

    def test_appendix_a_keeps_every_member(self) -> None:
        _, model = self._model()
        kept = {e.field_id for e in model.evidence}
        assert {"blinding.placebo_matching", "blinding.unblinded_staff_stated",
                "monitoring.unblinded_required"} <= kept

    def test_the_block_prints_as_its_label_row_with_its_lines(self) -> None:
        """The 29 October 2026 table: the block's label in bold, its headline
        as the value, its lines under it in the Value cell."""
        _, model = self._model()
        [label, *_] = self._block(model).table
        assert (label.variable, label.values, label.status) == (
            "Unblinded staff and monitoring", ["Likely needed"], "Review",
        )
        assert label.lines[0].text.startswith("Stated in the documents: Yes")


@pytest.mark.usefixtures("_with_the_blinding_block")
class TestABlockDescribesEveryDisagreementItNames:
    """A block member with no line of its own, the headline included, still
    gives both sides of its numbered disagreement (fix 2), and a scoped row read
    through a line's wording says which part it is."""

    @staticmethod
    def _block_lines(
        resolved: list[ResolvedField], contradictions: list[Contradiction]
    ) -> list[tuple[int, str]]:
        state = RunState(run_id="r-1", resolved=resolved, contradictions=contradictions)
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        [group] = [g for g in model.groups if g.group.id == "blinding_monitoring"]
        [row] = [r for r in group.rows if r.members]
        return [(t.indent, t.text) for t in row.block_lines]

    @staticmethod
    def _disputed(
        fid: str, values: list[tuple[str, int, str]]
    ) -> tuple[ResolvedField, Contradiction]:
        c = _contradiction(fid, "conflict", values)
        rf = _rf(fid, None, status="needs_review", contradiction=c, quote=None)
        rf.sources = [r.provenance for r in c.records]
        return rf, c

    def test_the_headline_in_a_disagreement_gives_both_sides(self) -> None:
        rf, c = self._disputed("monitoring.unblinded_required", [
            ("true", 12, "d-prot"), ("false", 4, "d-rfp"),
        ])
        lines = self._block_lines([rf], [c])
        start = lines.index(
            (0, "Unblinded monitoring likely required: documents disagree (Disagreement 1)")
        )
        assert [indent for indent, _ in lines[start + 1:start + 3]] == [1, 1]
        assert lines[start + 1][1].startswith("Protocol p.12")
        assert lines[start + 2][1].startswith("RFP p.4")

    def test_a_member_with_no_line_in_a_disagreement_gives_both_sides(self) -> None:
        rf, c = self._disputed("monitoring.unblinded_factors", [
            ("pharmacy", 53, "d-prot"), ("dosing", 3, "d-rfp"),
        ])
        lines = self._block_lines([rf], [c])
        label = get_registry().get_field("monitoring.unblinded_factors").label
        assert (0, f"{label}: documents disagree (Disagreement 1)") in lines
        assert f"{label}: not found" not in [t for _, t in lines]

    def test_a_scoped_row_read_through_a_wording_says_its_part(self) -> None:
        lines = self._block_lines([
            _rf("ops.visits_pharmacy_monitoring", "4 visits per site", pages=(4,),
                doc="d-rfp", kind="rfp", scope="part:Part A"),
        ], [])
        assert (
            0, "Part: Part A: RFP asks for pharmacy monitoring visits: 4 visits per site (RFP p.4)."
        ) in lines

    def test_a_line_read_through_a_wording_cites_only_the_pages_stating_its_value(self) -> None:
        # "reconcilable": one row with a value and no numbered disagreement, so
        # the line's wording is used, and its sources hold both records' pages.
        fid = "ops.visits_pharmacy_monitoring"
        c = _contradiction(fid, "reconcilable", [
            ("4 visits per site", 4, "d-rfp"), ("2 visits per site", 9, "d-prot"),
        ])
        rf = _rf(fid, "4 visits per site", status="needs_review", contradiction=c,
                 pages=(4,), doc="d-rfp", kind="rfp")
        rf.sources = [r.provenance for r in c.records]
        lines = self._block_lines([rf], [c])
        assert (
            0, "RFP asks for pharmacy monitoring visits: 4 visits per site (RFP p.4)."
        ) in lines
        assert not any("p.9" in t for _, t in lines)

    def test_a_line_with_a_wording_in_a_disagreement_gives_both_sides(self) -> None:
        fid = "ops.visits_pharmacy_monitoring"
        rf, c = self._disputed(fid, [("4 visits per site", 4, "d-rfp"), ("2", 9, "d-prot")])
        lines = self._block_lines([rf], [c])
        assert not any("asks for" in t for _, t in lines)
        assert any("documents disagree (Disagreement 1)" in t for _, t in lines)

    def test_a_line_with_a_wording_does_not_say_a_document_asks_for_not_specified(
        self,
    ) -> None:
        fid = "ops.visits_pharmacy_monitoring"
        lines = self._block_lines([
            _rf(fid, None, status="not_specified", pages=(4,), doc="d-rfp", kind="rfp"),
        ], [])
        assert not any("asks for" in t for _, t in lines)
        assert any("Not specified" in t and "RFP p.4" in t for _, t in lines)

    def test_a_scoped_headline_value_says_its_part(self) -> None:
        state = RunState(run_id="r-1", resolved=[
            _rf("monitoring.unblinded_required", True, scope="cohort:Part A", pages=(12,)),
            _rf("monitoring.unblinded_required", False, scope="cohort:Part B", pages=(13,)),
        ])
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        [group] = [g for g in model.groups if g.group.id == "blinding_monitoring"]
        [row] = [r for r in group.rows if r.members]
        assert [v.text.split(": ")[0] for v in row.values] == ["Cohort", "Cohort"]
        assert {v.text.split(": ")[1] for v in row.values} == {"Part A", "Part B"}


class TestScopeWords:
    """Fix 11 of the 2026-10-12 brief: no raw `<kind>:` prefix and no
    "Part not stated" in the main table."""

    def test_a_listed_kind_is_a_word_and_an_unlisted_scope_is_kept(self) -> None:
        from rfp_intake.render.report_model import _scope_words

        wording = get_registry().report_wording
        assert _scope_words("cohort:NEOD001 arm", wording) == "Cohort: NEOD001 arm"
        assert _scope_words("Dose-Level: 3", wording) == "Dose level: 3"
        assert _scope_words("ITT Population", wording) == "ITT Population"
        assert _scope_words("part:", wording) == "part:"
        assert _scope_words("note: see below", wording) == "note: see below"

    def test_the_unscoped_row_comes_first_and_appendix_a_keeps_the_stored_scope(self) -> None:
        state = RunState(run_id="r-1", resolved=[
            _rf("study.population", "Adults with AL amyloidosis", scope="cohort:Part A",
                pages=(5,)),
            _rf("study.population", "Adults with AL amyloidosis", pages=(4,)),
        ])
        fid = "study.population"
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        assert [r.scope for r in model.all_rows() if r.field_id == fid] == [None, "Cohort: Part A"]
        # As stored, with a capital (addition 3 to stage 6 of the 2026-10-12 plan).
        assert "Cohort:Part A" in {e.scope for e in model.evidence if e.field_id == fid}


class TestTheTwoStatusWords:
    """Angus Gray, 29 October 2026: the Status column says Confirmed or Review,
    nothing else. Rule 3 stays in the value, not in the status."""

    @pytest.mark.parametrize(("status", "word"), [
        ("confirmed", "Confirmed"),
        ("not_specified", "Confirmed"),
        ("not_applicable", "Confirmed"),
        ("needs_review", "Review"),
        ("not_found", "Review"),
    ])
    def test_each_status_is_one_of_two_words(self, status: str, word: str) -> None:
        from rfp_intake.render.report_model import status_word

        assert status_word(status) == word

    def test_the_three_terminal_states_stay_apart_in_the_value(self) -> None:
        state = RunState(run_id="r-1", resolved=[
            _rf("ops.sites_total", None, status="not_specified", pages=(5,)),
            _rf("interim.count", 0, pages=(98,)),
        ])
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        printed = {r.field_id: r.table[0] for g in model.groups for r in g.rows}
        assert (printed["ops.sites_total"].values, printed["ops.sites_total"].status) == (
            ["Not specified"], "Confirmed",
        )
        assert (printed["interim.count"].values, printed["interim.count"].status) == (
            ["0"], "Confirmed",
        )
        assert (printed["ops.crf_pages"].values, printed["ops.crf_pages"].status) == (
            ["Not found in the documents"], "Review",
        )

    def test_no_other_word_is_printed_in_the_status_column(self) -> None:
        state = _status_mix_state()
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        words = {t.status for g in model.groups for r in g.rows for t in r.table}
        words |= {t.status for g in model.groups for s in g.schedules for r in s.rows
                  for t in r.table}
        assert words <= {"", "Confirmed", "Review"}
        assert {"Confirmed", "Review"} <= words

    def test_no_value_cell_holds_a_page_reference(self) -> None:
        state = _status_mix_state()
        model = build_report_model(state, get_registry())
        for g in model.groups:
            for r in g.rows:
                for t in r.table:
                    cell = " ".join(t.values + [b.text for b in t.lines] + t.grey)
                    assert not re.search(r"\b(Protocol|RFP|Amendment) p\.\d", cell), cell
                    assert "%" not in cell


def _status_mix_state() -> RunState:
    """A disagreement, a row following it, a value for review, a confirmed
    one, a not-specified one and a whole-study duration under the timeline."""
    c = _contradiction("interim.count", "conflict", [("0", 98, "d-prot"), ("1", 4, "d-rfp")])
    count = _rf("interim.count", None, status="needs_review", contradiction=c, quote=None)
    count.sources = [r.provenance for r in c.records]
    timing = _rf("interim.timing", ["after 156 events"], status="needs_review",
                 contradiction=c, pages=(4,), doc="d-rfp", kind="rfp")
    return RunState(run_id="r-1", contradictions=[c], resolved=[
        count, timing,
        _rf("ops.sites_total", 75, pages=(5,)),
        _rf("ops.crf_pages", 20800, status="needs_review", confidence=0.5, pages=(4,)),
        _rf("study.phase", None, status="not_specified", pages=(1,)),
        _rf("timeline.total_duration", "42 months", scope="study", pages=(15,)),
    ])
