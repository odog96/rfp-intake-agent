"""Tests for ingest/models.py — model construction and conversion."""

from rfp_intake.domain.schemas import OutlineEntry, TableData
from rfp_intake.ingest.models import PageText, ParsedDoc, ParserMetadata, QualityMetrics


def test_page_text_char_count_auto():
    p = PageText(page_num=1, text="hello world")
    assert p.char_count == 11


def test_page_text_char_count_explicit():
    p = PageText(page_num=1, text="hello", char_count=99)
    assert p.char_count == 99


def test_quality_metrics_defaults():
    q = QualityMetrics(total_pages=10)
    assert q.pages_with_text == 0
    assert q.suspected_scanned is False


def test_parsed_doc_to_document():
    pages = [
        PageText(page_num=1, text="page one content"),
        PageText(page_num=2, text="page two content"),
    ]
    outline = [OutlineEntry(heading="Intro", page_start=1, level=1)]
    tables = [TableData(page=1, headers=["A", "B"], rows=[["1", "2"]])]
    metadata = ParserMetadata(rung_used=1, parser_name="TestParser", parse_duration_ms=42)

    parsed = ParsedDoc(
        path="/tmp/test.pdf",
        pages=pages,
        outline=outline,
        tables=tables,
        metadata=metadata,
    )

    doc = parsed.to_document("doc-001")
    assert doc.id == "doc-001"
    assert doc.path == "/tmp/test.pdf"
    assert doc.pages == 2
    assert len(doc.outline) == 1
    assert len(doc.tables) == 1
    assert doc.page_texts[1] == "page one content"
    assert doc.page_texts[2] == "page two content"
    assert doc.parsing_metadata["rung_used"] == 1
    assert doc.parsing_metadata["parser_name"] == "TestParser"
    assert doc.parsing_metadata["parse_duration_ms"] == 42


def test_parsed_doc_to_document_empty():
    metadata = ParserMetadata(rung_used=3, parser_name="EmptyParser")
    parsed = ParsedDoc(path="/tmp/empty.pdf", metadata=metadata)
    doc = parsed.to_document("doc-empty")
    assert doc.pages == 0
    assert doc.page_texts == {}
