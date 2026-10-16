"""Prompt B item 8(d): Appendix A prints a list once.

- A row of statements (an `object[]` field) is listed as its statements, each
  with its own value, page and quote, and not again as one line joining them
  all: that line ran over pages 10-11 and 14 of report-oct-13-v2.pdf.
- A list with a passage for each page prints the list once, then the passages,
  each with no value of its own: report-oct-13-v2.pdf printed the full visit
  intensity list beside each of its 36 passages.

Every state here goes through `assert_nothing_lost` (in `_report`).
"""

from __future__ import annotations

import os

import pymupdf
import pytest

from rfp_intake.domain.registry import get_registry
from rfp_intake.domain.schemas import FieldRecord
from rfp_intake.render.pdf_renderer import build_report_pdf
from rfp_intake.render.report_model import Evidence, _record_items
from tests.render.test_entries_display import AM, RFP, _rec, _report, _timeline


@pytest.fixture(autouse=True)
def _real_registry(fields_yaml_path) -> None:  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    get_registry.cache_clear()


_F = "visits.intensity_evidence"


def _intensity() -> list[FieldRecord]:
    return [
        _rec(_F, "imaging", None, AM, 13, "MRI of the heart at baseline and week 24"),
        _rec(_F, "ecgs", None, AM, 20, "A 12-lead ECG at every visit"),
        _rec(_F, "pk_pd_sampling, imaging", None, RFP, 3, "PK samples and imaging"),
    ]


def _lines(records: list[FieldRecord], field_id: str) -> list[Evidence]:
    _, model = _report(records)
    return [e for e in model.evidence if e.field_id == field_id]


def _evidence_text(records: list[FieldRecord]) -> str:
    state, _ = _report(records)
    pdf = build_report_pdf(state, get_registry(), generated_at="t")
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
        text = " ".join(" ".join(p.get_text().split()) for p in doc)
    start = text.index("Appendix A — Evidence")
    end = text.find("Appendix B", start)
    return text[start:end if end != -1 else None]


class TestStatements:
    def test_each_statement_is_one_entry_and_none_joins_them(self) -> None:
        records = _timeline()
        state, model = _report(records)
        [rf] = [r for r in state.resolved if r.field_id == "timeline.periods"]
        fd = get_registry().get_field("timeline.periods")
        own = {r.quote: "; ".join(_record_items(r, fd)) for r in rf.statements}
        lines = [e for e in model.evidence if e.field_id == "timeline.periods"]
        # One line per statement, each with that statement's own value only.
        assert {(e.quote, e.value) for e in lines} == set(own.items())
        assert len(lines) == len(records)
        for e in lines:
            assert not any(v in e.value for q, v in own.items() if q != e.quote)

    def test_the_rows_own_quote_is_printed_once(self) -> None:
        records = _timeline()
        lines = _lines(records, "timeline.periods")
        for r in records:
            assert [e.quote for e in lines].count(r.quote) == 1


class TestAListWithAPassageForEachPage:
    def test_the_list_is_printed_once_then_each_passage(self) -> None:
        lines = _lines(_intensity(), _F)
        [listed] = [e for e in lines if e.value]
        assert listed.value == "Imaging; ECGs; PK/PD sampling"
        assert listed.quote is None
        assert lines[0] is listed
        assert [(e.value, e.sources, e.quote) for e in lines[1:]] == [
            ("", "Amendment p.13", "MRI of the heart at baseline and week 24"),
            ("", "Amendment p.20", "A 12-lead ECG at every visit"),
            ("", "RFP p.3", "PK samples and imaging"),
        ]

    def test_a_list_of_one_keeps_its_value_beside_each_passage(self) -> None:
        records = [
            _rec(_F, "imaging", None, AM, 13, "MRI of the heart at baseline"),
            _rec(_F, "imaging", None, RFP, 3, "Cardiac imaging"),
        ]
        lines = _lines(records, _F)
        assert {(e.value, e.quote) for e in lines} == {
            ("Imaging", "MRI of the heart at baseline"),
            ("Imaging", "Cardiac imaging"),
        }

    def test_the_pdf_prints_the_list_once_and_no_dash_before_a_passage(self) -> None:
        text = _evidence_text(_intensity())
        assert text.count("Imaging; ECGs; PK/PD sampling") == 1
        assert '"A 12-lead ECG at every visit"' in text
        # A passage's line starts with its pages, not a dash before them.
        assert "— Amendment p.20" not in text
        assert "• Amendment p.20" in text
        # The list's own line keeps its dash.
        assert "Imaging; ECGs; PK/PD sampling —" in text
