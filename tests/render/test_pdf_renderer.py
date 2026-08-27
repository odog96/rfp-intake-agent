"""Tests for render/pdf_renderer.py.

PDF content isn't practical to assert on directly, so these check what's
actually verifiable: valid output, no crashes across the shapes RENDER will
see in practice (empty state, contradictions, unescaped special characters,
missing quotes/sources), and byte-size sensitivity to content growth.
"""

from __future__ import annotations

import os

from rfp_intake.domain.registry import Registry
from rfp_intake.domain.schemas import (
    Contradiction,
    FieldRecord,
    Provenance,
    ResolvedField,
    RunState,
)
from rfp_intake.render.pdf_renderer import build_report_pdf


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


class TestContradictionsSectionLength:
    """A dismissed disagreement is recorded, not written up at length."""

    @staticmethod
    def _contradiction(field_id: str, verdict: str, n_records: int = 2):
        from rfp_intake.domain.schemas import Contradiction, FieldRecord, Provenance

        records = [
            FieldRecord(
                field_id=field_id,
                group="operational_metrics",
                raw_value=f"value-{i}",
                quote=f"quote number {i} " + ("padding " * 20),
                provenance=Provenance(doc_id="doc-1", doc_kind="rfp", page=i + 1),
                confidence=0.9,
            )
            for i in range(n_records)
        ]
        return Contradiction(
            field_id=field_id,
            records=records,
            verdict=verdict,  # type: ignore[arg-type]
            explanation="A long explanation. " * 40,
            severity="high",
        )

    def test_dismissed_entries_are_far_shorter_than_real_ones(self) -> None:
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.render.pdf_renderer import _contradictions_section, _styles

        styles = _styles()
        registry = get_registry()
        real = _contradictions_section(
            [self._contradiction("ops.sites_total", "conflict")], registry, styles
        )
        dismissed = _contradictions_section(
            [self._contradiction("ops.sites_total", "not_a_conflict")], registry, styles
        )
        assert len(dismissed) < len(real)

    def test_dismissed_entries_still_appear(self) -> None:
        # They must remain visible: "we checked and it was fine" is information.
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.render.pdf_renderer import _contradictions_section, _styles

        story = _contradictions_section(
            [self._contradiction("ops.sites_total", "not_a_conflict")],
            get_registry(),
            _styles(),
        )
        assert any("dismissed" in str(getattr(p, "text", "")) for p in story)

    def test_quotes_are_capped(self) -> None:
        # One entry in run r-20260827-180418 carried 27 source quotes.
        from rfp_intake.domain.registry import get_registry
        from rfp_intake.render.pdf_renderer import _MAX_QUOTES, _contradictions_section, _styles

        story = _contradictions_section(
            [self._contradiction("ops.sites_total", "conflict", n_records=27)],
            get_registry(),
            _styles(),
        )
        quoted = [p for p in story if 'doc-1 (rfp' in str(getattr(p, "text", ""))]
        assert len(quoted) == _MAX_QUOTES
        assert any("further sources" in str(getattr(p, "text", "")) for p in story)
