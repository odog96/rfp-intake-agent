"""Tests for render/pdf_renderer.py.

PDF content isn't practical to assert on directly, so these check what's
actually verifiable: valid output, no crashes across the shapes RENDER will
see in practice (empty state, contradictions, unescaped special characters,
missing quotes/sources), and byte-size sensitivity to content growth.
"""

from __future__ import annotations

import os
from pathlib import Path

from rfp_intake.domain.registry import Registry
from rfp_intake.domain.schemas import (
    Contradiction,
    FieldRecord,
    Provenance,
    RemovedPassage,
    ResolvedField,
    RunState,
)
from rfp_intake.render.pdf_renderer import build_report_pdf
from rfp_intake.render.report_model import SCHEDULE_THRESHOLD


def _use_real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    from rfp_intake.domain.registry import get_registry
    get_registry.cache_clear()


def _registry() -> Registry:
    from rfp_intake.domain.registry import get_registry
    return get_registry()


class TestBuildReportPdf:
    def test_empty_state_produces_valid_pdf(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        state = RunState(run_id="r-1")
        pdf = build_report_pdf(state, _registry(), generated_at="2026-01-01T00:00:00Z")
        assert pdf.startswith(b"%PDF")
        assert pdf.endswith(b"%%EOF\n") or b"%%EOF" in pdf[-32:]

    def test_resolved_fields_produce_larger_pdf_than_empty(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        empty = build_report_pdf(RunState(run_id="r-1"), _registry(), generated_at="2026-01-01")

        rf = ResolvedField(
            field_id="ops.sites_total", value=75, status="confirmed", confidence=0.92,
            sources=[Provenance(doc_id="rfp1", doc_kind="rfp", page=5)], quote="75 sites total",
        )
        with_fields = build_report_pdf(
            RunState(run_id="r-1", resolved=[rf]), _registry(), generated_at="2026-01-01",
        )
        assert len(with_fields) >= len(empty)

    def test_contradiction_section_does_not_crash(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        record = FieldRecord(
            field_id="ops.sites_total", group="operational_metrics", raw_value="75",
            quote="75 sites", provenance=Provenance(doc_id="rfp1", doc_kind="rfp", page=5),
            confidence=0.9,
        )
        contradiction = Contradiction(
            field_id="ops.sites_total", records=[record], verdict="conflict",
            explanation="RFP and protocol disagree on site count.", severity="high",
        )
        rf = ResolvedField(
            field_id="ops.sites_total", value=None, status="needs_review", confidence=0.5,
            contradiction=contradiction,
        )
        state = RunState(run_id="r-1", resolved=[rf], contradictions=[contradiction])

        pdf = build_report_pdf(state, _registry(), generated_at="2026-01-01")
        assert pdf.startswith(b"%PDF")

    def test_unadjudicated_contradiction_omitted_from_section(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """A verdict=None candidate shouldn't appear in the Contradictions
        section — ADJUDICATE hasn't judged it yet."""
        _use_real_registry(fields_yaml_path)
        record = FieldRecord(
            field_id="ops.sites_total", group="operational_metrics", raw_value="75",
            quote="75 sites", provenance=Provenance(doc_id="rfp1", doc_kind="rfp", page=5),
            confidence=0.9,
        )
        contradiction = Contradiction(field_id="ops.sites_total", records=[record])  # verdict=None
        state = RunState(run_id="r-1", contradictions=[contradiction])

        # Should not raise despite the pending candidate.
        pdf = build_report_pdf(state, _registry(), generated_at="2026-01-01")
        assert pdf.startswith(b"%PDF")

    def test_special_characters_in_quote_do_not_crash(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Quotes are verbatim source text and may contain XML-special chars
        (&, <, >) that would break reportlab's mini-markup parser if unescaped."""
        _use_real_registry(fields_yaml_path)
        rf = ResolvedField(
            field_id="ops.sites_total", value=75, status="confirmed", confidence=0.9,
            sources=[Provenance(doc_id="rfp1", doc_kind="rfp", page=5)],
            quote='Sites & Subjects: "75" <total> per protocol v1 > v0',
        )
        state = RunState(run_id="r-1", resolved=[rf])
        pdf = build_report_pdf(state, _registry(), generated_at="2026-01-01")
        assert pdf.startswith(b"%PDF")

    def test_not_found_field_does_not_crash(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        rf = ResolvedField(
            field_id="ops.sites_total", value=None, status="not_found", confidence=0.0,
        )
        state = RunState(run_id="r-1", resolved=[rf])
        pdf = build_report_pdf(state, _registry(), generated_at="2026-01-01")
        assert pdf.startswith(b"%PDF")

    def test_field_with_no_resolved_entry_at_all_renders_not_found(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Every registry field should get a line even with zero resolved entries."""
        _use_real_registry(fields_yaml_path)
        state = RunState(run_id="r-1")  # no resolved fields whatsoever
        pdf = build_report_pdf(state, _registry(), generated_at="2026-01-01")
        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 1000  # non-trivial — every field in the registry got a line


def _pdf_text(pdf: bytes) -> str:
    import pymupdf

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
        return " ".join(" ".join(p.get_text().split()) for p in doc)


def _headings(pdf: bytes) -> list[str]:
    """The section headings, in printed order, as standalone lines.

    Substring search over the flattened text cannot do this: the header block
    names "Words used in this report" and the disagreements lead names
    "Appendix B", so `text.index(...)` finds the mention rather than the heading
    and reports them in the wrong order. scripts/rerender_report.py matches lines
    for the same reason.
    """
    import pymupdf

    wanted = {
        "All variables",
        "Disagreements between the documents",
        "Flagged for review",
        "Schedules",
        "Words used in this report",
    }
    out: list[str] = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
        for page in doc:
            for line in page.get_text().splitlines():
                line = line.strip()
                if line in wanted or line.startswith(
                    ("Appendix A —", "Appendix B —", "Appendix C —")
                ):
                    out.append(line)
    return out


def _conflict_state() -> RunState:
    from rfp_intake.domain.schemas import Contradiction, FieldRecord, Provenance

    def rec(value: int, page: int, doc: str, kind: str) -> FieldRecord:
        return FieldRecord(
            field_id="interim.count", group="interim_analyses", raw_value=str(value),
            value=value,
            quote=f"quote on page {page}", confidence=0.9,
            provenance=Provenance(doc_id=doc, doc_kind=kind, page=page),  # type: ignore[arg-type]
        )

    conflict = Contradiction(
        field_id="interim.count", verdict="conflict", severity="high",
        explanation="The RFP (d-rfp) plans an interim analysis; the protocol (d-prot) does not.",
        records=[rec(0, 98, "d-prot", "protocol"), rec(1, 4, "d-rfp", "rfp")],
    )
    dismissed = Contradiction(
        field_id="study.indication", verdict="not_a_conflict",
        # Appendix B prints this sentence beside the two wordings it settles, so
        # the fixture has to carry one. Written the way ADJUDICATE writes them,
        # with the internal document id in it: the renderer has to swap that for
        # the report's own name. See
        # `test_reasoning_and_dismissed_entries_are_in_the_appendix`.
        explanation="Both records name the same disease; the protocol (d-prot) abbreviates it.",
        records=[
            FieldRecord(field_id="study.indication", group="phase_population", raw_value=v,
                        value=v, quote=v, confidence=0.9,
                        provenance=Provenance(doc_id=d, doc_kind=k, page=pg))  # type: ignore[arg-type]
            for v, d, k, pg in (("AL amyloidosis", "d-prot", "protocol", 11),
                                ("Light chain amyloidosis", "d-rfp", "rfp", 1))
        ],
    )
    return RunState(
        run_id="r-1",
        resolved=[
            ResolvedField(field_id="interim.count", value=0, status="needs_review",
                          confidence=0.9, contradiction=conflict,
                          sources=[r.provenance for r in conflict.records]),
            ResolvedField(field_id="study.indication", value="AL amyloidosis", status="confirmed",
                          confidence=0.9, contradiction=dismissed, quote="AL amyloidosis",
                          sources=[dismissed.records[0].provenance]),
        ],
        contradictions=[conflict, dismissed],
    )


class TestReportText:
    """What an analyst sees, read back out of the finished PDF."""

    def test_every_registry_variable_is_named(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        text = _pdf_text(build_report_pdf(RunState(run_id="r-1"), _registry(), generated_at="t"))
        for f in _registry().fields:
            assert " ".join(f.label.split()) in text, f.label

    def test_the_five_sections_are_in_the_order_the_customer_asked_for(
        self, fields_yaml_path: Path,
    ) -> None:
        """Variables first, then the two review sections. See pdf_renderer's docstring.

        Until 2026-09-30 the review sections came first and this test asserted a
        conflict was on page 1. The customer asked for the opposite order: see
        everything that was read before being asked to adjudicate any of it.
        """
        _use_real_registry(fields_yaml_path)
        headings = _headings(build_report_pdf(_conflict_state(), _registry(), generated_at="t"))
        # "Schedules" is absent here on purpose: _conflict_state holds no variable
        # with enough entries to become a schedule, and _schedules then renders
        # nothing rather than an empty heading.
        # Appendix C is absent for the same reason: _conflict_state has no removed
        # passage, and _removed_appendix returns nothing rather than an empty
        # heading. A run before MARK_OTHER_STUDY existed gets exactly this report.
        assert [h.split(" —")[0] for h in headings] == [
            "All variables",
            "Disagreements between the documents",
            "Flagged for review",
            "Appendix A",
            "Appendix B",
            "Words used in this report",
        ]

    def test_appendix_c_appears_after_appendix_b_and_before_the_word_list(
        self, fields_yaml_path: Path,
    ) -> None:
        """Where stage 4's appendix sits, and that it does not displace anything.

        Appendices are what a reader turns to after reading the front; a removal is
        a judgement to check rather than a number to read, so it goes behind
        Appendix A (the quotes) and Appendix B (the disagreements), and the word
        list stays last.
        """
        _use_real_registry(fields_yaml_path)
        state = _conflict_state()
        state.removed_passages = [
            RemovedPassage(
                doc_id="doc-1",
                section_id="doc-1:s042",
                heading="1.3.2 Clinical Experience",
                page_start=39,
                page_end=40,
                verdict="other_study",
                reason="Describes completed study NEOD001-001.",
                text="Study NEOD001-001 was an ongoing, open-label Phase 1/2 study.",
            )
        ]
        headings = _headings(build_report_pdf(state, _registry(), generated_at="t"))

        assert [h.split(" —")[0] for h in headings] == [
            "All variables",
            "Disagreements between the documents",
            "Flagged for review",
            "Appendix A",
            "Appendix B",
            "Appendix C",
            "Words used in this report",
        ]

    def test_appendix_c_names_the_section_the_pages_and_the_reason(
        self, fields_yaml_path: Path,
    ) -> None:
        """What a reviewer needs to overturn the decision: where it was, and why.

        PLAN_2026-10-02.md stage 4 asks for document, section heading, pages and
        reason. The extract is also printed, so the passage can be recognised
        without opening the PDF it came from.
        """
        _use_real_registry(fields_yaml_path)
        state = _conflict_state()
        state.removed_passages = [
            RemovedPassage(
                doc_id="doc-1",
                section_id="doc-1:s042",
                heading="1.3.2 Clinical Experience",
                page_start=39,
                page_end=40,
                verdict="other_study",
                reason="Describes completed study NEOD001-001, not this study.",
                text="Study NEOD001-001 was an ongoing, open-label\nPhase 1/2 study.",
            )
        ]
        text = _pdf_text(build_report_pdf(state, _registry(), generated_at="t"))

        assert "1.3.2 Clinical Experience" in text
        assert "p.39-40" in text
        assert "whole section" in text
        assert "Describes completed study NEOD001-001, not this study." in text
        # The line break in the stored text is collapsed, because a passage comes
        # straight out of a PDF page where a sentence breaks wherever the line did.
        assert "was an ongoing, open-label Phase 1/2 study." in text

    def test_schedules_are_printed_before_the_appendices(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """A variable with many entries becomes its own table, still ahead of Appendix A.

        Only the word list moved behind the appendices on 2026-09-30. The visit
        schedule and the timeline components are budget drivers, so they stay in
        the part a reader reads straight through.
        """
        _use_real_registry(fields_yaml_path)
        state = RunState(
            run_id="r-1",
            resolved=[
                ResolvedField(
                    field_id="visits.frequency_by_period", value=f"every {n} weeks",
                    status="confirmed", confidence=0.9, scope=f"period {n}",
                    sources=[Provenance(doc_id="p1", doc_kind="protocol", page=n)],
                    quote=f"every {n} weeks",
                )
                for n in range(1, SCHEDULE_THRESHOLD + 3)
            ],
        )
        headings = [h.split(" —")[0] for h in _headings(build_report_pdf(state, _registry(),
                                                                        generated_at="t"))]
        assert headings.index("Schedules") < headings.index("Appendix A")
        # Appendix B is absent here — this state has no contradiction to reason
        # about. The word list is still behind the appendix that is printed.
        assert headings.index("Appendix A") < headings.index("Words used in this report")

    def test_a_disagreement_is_named_on_its_variable_row_and_tabled_in_full(
        self, fields_yaml_path: Path,
    ) -> None:
        """The variables table flags the row and points at the numbered table."""
        _use_real_registry(fields_yaml_path)
        text = _pdf_text(build_report_pdf(_conflict_state(), _registry(), generated_at="t"))
        assert "Number of interim analyses" in text
        # The row in All variables states both sides, each with its page and
        # quote, and says where to read the detail (fix 2 of the 2026-10-12 brief).
        assert 'Protocol p.98: 0 — "quote on page 98' in text
        assert 'RFP p.4: 1 — "quote on page 4' in text
        assert "No value chosen. See Disagreement 1." in text
        # The numbered table carries both sides with their pages, and the verdict.
        assert "0 — Protocol p.98" in text
        assert "1 — RFP p.4" in text
        assert "Conflict" in text

    def test_a_row_following_the_disagreement_and_a_scopes_row_are_printed(
        self, fields_yaml_path: Path,
    ) -> None:
        """Fixes 2 and 6 of the 2026-10-12 brief, read back out of the PDF.

        tests/render/test_report_model.py checks the same lines on the report
        model; this checks that pdf_renderer.py draws them (plan-conformance
        reviewer, 2026-10-12).
        """
        _use_real_registry(fields_yaml_path)
        state = _conflict_state()
        count = state.resolved[0]
        timing = ResolvedField(
            field_id="interim.timing", value=["after 156 events"], status="needs_review",
            confidence=0.9, contradiction=count.contradiction, quote="after 156 events",
            sources=[Provenance(doc_id="d-rfp", doc_kind="rfp", page=4)],
        )
        durations = [
            ResolvedField(
                field_id="timeline.total_duration", value=v, status="confirmed",
                confidence=0.95, scope=scope, quote=v,
                sources=[Provenance(doc_id="d-prot", doc_kind="protocol", page=pg)],
            )
            for v, scope, pg in (("about 4 years", "subject", 47), ("42 months", "study", 15))
        ]
        state.resolved += [timing, *durations]
        text = _pdf_text(build_report_pdf(state, _registry(), generated_at="t"))
        assert "Depends on Disagreement 1" in text
        assert "Only the RFP gives a timing: after 156 events (RFP p.4)." in text
        assert "If the protocol is right, there is no interim analysis to time." in text
        # A part with one value names its pages in its heading.
        whole = text.index("Whole study (Protocol p.15):")
        each = text.index("Time each patient spends in the study (Protocol p.47):")
        assert whole < text.index("42 months", whole) < each < text.index("about 4 years", each)

    def test_confidence_is_printed_and_explained(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Angus Gray asked how the percentage is produced, so the report says.

        The figure is printed on every row, not only when it is low — the example
        the customer praised read "(Confirmed, 100% confidence)".
        """
        _use_real_registry(fields_yaml_path)
        rf = ResolvedField(
            field_id="ops.sites_total", value=75, status="confirmed", confidence=1.0,
            scope="total", sources=[Provenance(doc_id="rfp1", doc_kind="rfp", page=5)],
            quote="75 sites total",
        )
        text = _pdf_text(build_report_pdf(RunState(run_id="r-1", resolved=[rf]), _registry(),
                                          generated_at="t"))
        assert "Confidence" in text
        assert "100%" in text
        assert "the extraction model's own rating" in text
        assert "not a probability of being correct" in text

    def test_a_flagged_value_carries_its_reason(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        """Before 2026-09-30 this section printed names only, under "Also check"."""
        _use_real_registry(fields_yaml_path)
        rf = ResolvedField(
            field_id="ops.sites_total", value=75, status="needs_review", confidence=0.5,
            scope="total", sources=[Provenance(doc_id="rfp1", doc_kind="rfp", page=5)],
            quote="75 sites total",
        )
        text = _pdf_text(build_report_pdf(RunState(run_id="r-1", resolved=[rf]), _registry(),
                                          generated_at="t"))
        assert "Why it is flagged" in text
        assert "Confidence below 80%" in text

    def test_reasoning_and_dismissed_entries_are_in_the_appendix(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        text = _pdf_text(build_report_pdf(_conflict_state(), _registry(), generated_at="t"))
        assert "Appendix B" in text
        # Document ids in the adjudicator's reasoning are replaced by names.
        assert "The RFP (RFP) plans an interim analysis; the protocol (Protocol) does not." in text
        assert "Checked and dismissed (1)" in text
        assert "Light chain amyloidosis" in text
        # The dismissed pair carries its reason too, and that reason is renamed
        # the same way. Change 1 of the 2026-10-07 review moved nine variables
        # into this list and they first arrived as bare values; the reason was
        # then printed, but unrenamed, so Appendix B of
        # r-20261008-review2-run3 showed the analyst "doc-2ea4cd79".
        assert (
            "Both records name the same disease; the protocol (Protocol) abbreviates it."
            in text
        )
        # Scoped to the appendix: the header block on page 1 names both document
        # ids on purpose, as the key that explains "Amendment" and "RFP".
        appendix_b = text[text.index("Checked and dismissed (1)"):]
        assert "d-prot" not in appendix_b, "no internal document id reaches the appendix"

    def test_quotes_are_in_the_evidence_appendix(self, fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
        _use_real_registry(fields_yaml_path)
        rf = ResolvedField(
            field_id="ops.sites_total", value=75, status="confirmed", confidence=0.92,
            scope="total", sources=[Provenance(doc_id="rfp1", doc_kind="rfp", page=5)],
            quote="75 sites total",
        )
        text = _pdf_text(build_report_pdf(RunState(run_id="r-1", resolved=[rf]), _registry(),
                                          generated_at="t"))
        # The heading, not a mention of it: the header block names Appendix A on
        # page 1, so slicing at the first "Appendix A" would return the whole
        # document and the test would pass without the quote being in the appendix.
        evidence = text[text.index("Appendix A — Evidence"):]
        assert '"75 sites total"' in evidence
        assert "RFP p.5, 92%" in evidence

    def test_each_side_of_a_disagreement_says_whether_the_report_used_it(
        self, fields_yaml_path: Path,
    ) -> None:
        """CLAUDE.md open item 3. The fixture's conflict has no provisional value."""
        _use_real_registry(fields_yaml_path)
        text = _pdf_text(build_report_pdf(_conflict_state(), _registry(), generated_at="t"))
        start = text.index("Appendix A — Evidence")
        evidence = text[start:text.index("Appendix B — Disagreements", start)]
        assert evidence.count("No value chosen — see Disagreement 1") == 2

    def test_a_derived_row_prints_why_and_its_pages_in_plain_words(
        self, fields_yaml_path: Path,
    ) -> None:
        """Fix 8 of the 2026-10-12 brief: the status says the tool worked it out,
        the value cell says why and from which pages, and no field id is printed."""
        from rfp_intake.derive import derive_node

        _use_real_registry(fields_yaml_path)
        state = RunState(run_id="r-1", resolved=[ResolvedField(
            field_id="visits.intensity_evidence",
            value=["frequent_early_visits", "infusion_observation_period", "imaging",
                   "pk_pd_sampling"],
            status="needs_review", confidence=0.9,
            sources=[Provenance(doc_id="p", doc_kind="protocol", page=13)],
            quote="q",
        )])
        state.resolved = derive_node(state)["resolved"]
        text = " ".join(_pdf_text(build_report_pdf(state, _registry(), generated_at="t")).split())
        assert "Worked out by the tool — confirm" in text
        assert (
            "Why: frequent visits early in the study; IV infusion with an observation period; "
            "imaging; PK/PD sampling."
        ) in text
        assert "Evidence pages: Protocol p.13." in text
        evidence = text[text.index("Appendix A — Evidence"):]
        assert "Derived from Evidence of visit intensity, Visit frequency by study period" in (
            evidence
        )
        assert "visits.intensity" not in text
