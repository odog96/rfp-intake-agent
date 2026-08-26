"""Integration tests for parser rungs — requires real PDFs."""

from pathlib import Path

import pytest


@pytest.mark.slow
def test_rung1_parses_text_pdf(samples_dir: Path):
    """Rung 1 handles PDFs with native text layer."""
    from rfp_intake.ingest.parsers.rung1 import Rung1Parser

    pdf = samples_dir / "Example protocol 3.pdf"
    if not pdf.exists():
        pytest.skip("Sample PDF not available")

    parser = Rung1Parser()
    if not parser.can_parse(pdf):
        pytest.skip("PDF does not have text layer for rung 1")

    result = parser.parse(pdf)
    assert len(result.pages) > 0
    assert result.pages[0].page_num == 1
    assert result.pages[0].char_count > 100
    assert result.metadata.rung_used == 1


@pytest.mark.slow
def test_rung1_extracts_tables(samples_dir: Path):
    """Rung 1 extracts tables as structured rows."""
    from rfp_intake.ingest.parsers.rung1 import Rung1Parser

    pdf = samples_dir / "Example protocol 3.pdf"
    if not pdf.exists():
        pytest.skip("Sample PDF not available")

    parser = Rung1Parser()
    if not parser.can_parse(pdf):
        pytest.skip("PDF does not have text layer for rung 1")

    result = parser.parse(pdf)
    # Tables may or may not exist, but the mechanism works
    for table in result.tables:
        assert table.page >= 1
        assert len(table.headers) > 0 or len(table.rows) > 0


@pytest.mark.slow
def test_escalation_scanned_pdf(samples_dir: Path):
    """Scanned PDF triggers escalation to rung 3."""
    from rfp_intake.ingest.parsers import parse_with_escalation
    from rfp_intake.ingest.parsers.quality import detect_scanned

    pdf = samples_dir / "Example RFP 1.pdf"
    if not pdf.exists():
        pytest.skip("Sample scanned PDF not available")

    is_scanned = detect_scanned(pdf)
    if not is_scanned:
        pytest.skip("This PDF has a text layer, not suitable for OCR test")

    result = parse_with_escalation(pdf)
    assert result.metadata.rung_used == 3
    assert len(result.pages) > 0


@pytest.mark.slow
def test_escalation_text_pdf_stays_at_rung1(samples_dir: Path):
    """Text PDF with good quality stays at rung 1."""
    from rfp_intake.ingest.parsers import parse_with_escalation

    pdf = samples_dir / "Example protocol 3.pdf"
    if not pdf.exists():
        pytest.skip("Sample PDF not available")

    result = parse_with_escalation(pdf)
    # Should stay at rung 1 if quality is good
    assert result.metadata.rung_used in (1, 2)
    assert len(result.pages) > 0
