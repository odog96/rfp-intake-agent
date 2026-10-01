"""Tests for render/report_model.py — above all, that shortening the report
never loses anything an analyst would otherwise have found.

`assert_nothing_lost` is the guarantee, written once and run against both the
hand-built states here and every real run folder present on the machine.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rfp_intake.domain.registry import Registry, get_registry
from rfp_intake.domain.schemas import (
    Contradiction,
    Document,
    FieldRecord,
    Provenance,
    ResolvedField,
    RunState,
)
from rfp_intake.normalize.scope import normalize_scope
from rfp_intake.render.report_model import (
    _STATUS_RANK,
    SCHEDULE_THRESHOLD,
    ReportModel,
    _same_key,
    _signature,
    build_report_model,
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
        # Every page the entry was found on is cited, against the right document.
        cited = _parse_sources(line.sources)
        for p in rf.sources:
            assert p.page in cited.get(codes[p.doc_id], set()), (
                f"{rf.field_id}: {codes[p.doc_id]} p.{p.page} not in {line.sources!r}"
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
        if c.verdict in ("conflict", "reconcilable"):
            assert c.field_id in decided
            d = next(d for d in model.decisions if d.signature == _signature(c))
            cited: dict[str, set[int]] = {}
            for pos in d.positions:
                for code, pages in _parse_sources(pos.sources).items():
                    cited.setdefault(code, set()).update(pages)
            for r in c.records:
                assert r.provenance.page in cited[codes[r.provenance.doc_id]]
        else:
            assert c.field_id in dismissed
    needing = [c for c in adjudicated if c.verdict in ("conflict", "reconcilable")]
    assert len(model.decisions) == len(needing)

    # A row that carries a decision says so.
    for row in rows:
        if row.verdict in ("conflict", "reconcilable"):
            assert row.decision_no is not None


def _parse_sources(text: str) -> dict[str, set[int]]:
    """"Protocol p.11, 42; RFP p.2" -> {"Protocol": {11, 42}, "RFP": {2}}."""
    out: dict[str, set[int]] = {}
    for part in filter(None, (t.strip() for t in text.split(";"))):
        code, _, pages = part.rpartition(" p.")
        out[code] = {int(n) for n in pages.split(",")}
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
        c = _contradiction("interim.planned", "conflict",
                           [("false", 98, "d-prot"), ("true", 4, "d-rfp")])
        state = RunState(
            run_id="r-1",
            resolved=[_rf("interim.planned", "false", status="needs_review", pages=(98, 4),
                          contradiction=c)],
            contradictions=[c],
        )
        model = build_report_model(state, get_registry())
        assert_nothing_lost(state, get_registry(), model)
        [d] = model.decisions
        assert [(p.text, p.sources) for p in d.positions] == [
            ("No", "Protocol p.98"), ("Yes", "RFP p.4"),
        ]
        [row] = [r for r in model.all_rows() if r.field_id == "interim.planned"]
        assert row.decision_no == d.no == 1

    def test_conflicts_come_before_reconcilable_ones(self) -> None:
        a = _contradiction("ops.sites_total", "reconcilable",
                           [("75", 1, "d-prot"), ("70", 2, "d-rfp")])
        b = _contradiction("interim.planned", "conflict",
                           [("false", 3, "d-prot"), ("true", 4, "d-rfp")], severity="low")
        state = RunState(run_id="r-1", contradictions=[a, b])
        model = build_report_model(state, get_registry())
        assert [d.field_id for d in model.decisions] == ["interim.planned", "ops.sites_total"]

    def test_document_ids_in_the_reasoning_become_names(self) -> None:
        c = _contradiction("interim.planned", "conflict",
                           [("false", 98, "d-prot"), ("true", 4, "d-rfp")])
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
    )


@pytest.mark.skipif(not _real_runs(), reason="no run folders on this machine")
@pytest.mark.parametrize("run", _real_runs(), ids=lambda p: p.name)
def test_nothing_lost_on_a_real_run(run: Path) -> None:
    state = load_run_state(run)
    model = build_report_model(state, get_registry())
    assert_nothing_lost(state, get_registry(), model)
