"""Rung 3 — OCR for scanned documents. Docling OCR primary, pytesseract fallback."""

from __future__ import annotations

from pathlib import Path

import structlog

from rfp_intake.domain.schemas import TableData
from rfp_intake.ingest.models import PageText, ParsedDoc, ParserMetadata

logger = structlog.get_logger()


class Rung3Parser:
    rung: int = 3

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def parse(self, path: Path) -> ParsedDoc:
        try:
            return self._parse_docling_ocr(path)
        except Exception as e:
            logger.warning("docling_ocr_failed_falling_back", error=str(e), path=str(path))
            return self._parse_tesseract(path)

    def _parse_docling_ocr(self, path: Path) -> ParsedDoc:
        """Primary: Docling with OCR enabled."""
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        pipeline_options = PdfPipelineOptions(do_ocr=True)
        converter = DocumentConverter(
            format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}  # type: ignore[dict-item]
        )
        result = converter.convert(str(path))
        doc = result.document

        pages = self._extract_pages_docling(doc)
        tables = self._extract_tables_docling(doc)

        return ParsedDoc(
            path=str(path),
            pages=pages,
            outline=[],
            tables=tables,
            metadata=ParserMetadata(rung_used=3, parser_name="Rung3Parser_docling_ocr"),
        )

    def _parse_tesseract(self, path: Path) -> ParsedDoc:
        """Fallback: PyMuPDF rendering + pytesseract."""
        import io

        import fitz
        import pytesseract
        from PIL import Image

        doc = fitz.open(str(path))
        pages: list[PageText] = []
        try:
            for i in range(len(doc)):
                pix = doc[i].get_pixmap(dpi=300)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                text = pytesseract.image_to_string(img)
                pages.append(PageText(
                    page_num=i + 1,
                    text=text,
                    has_text_layer=False,
                ))
        finally:
            doc.close()

        return ParsedDoc(
            path=str(path),
            pages=pages,
            outline=[],
            tables=[],
            metadata=ParserMetadata(rung_used=3, parser_name="Rung3Parser_tesseract"),
        )

    def _extract_pages_docling(self, doc: object) -> list[PageText]:
        """Extract page-indexed text from Docling OCR result."""
        page_texts: dict[int, list[str]] = {}
        try:
            for element in doc.iterate_items():  # type: ignore[attr-defined]
                item = element if not isinstance(element, tuple) else element[1]
                prov = getattr(item, "prov", None)
                if prov:
                    for p in prov:
                        page_no = getattr(p, "page_no", None) or getattr(p, "page", None)
                        if page_no:
                            text = getattr(item, "text", "") or ""
                            if text.strip():
                                page_texts.setdefault(page_no, []).append(text)
        except (AttributeError, TypeError):
            md = doc.export_to_markdown()  # type: ignore[attr-defined]
            return [PageText(page_num=1, text=md, has_text_layer=False)]

        if not page_texts:
            md = doc.export_to_markdown()  # type: ignore[attr-defined]
            return [PageText(page_num=1, text=md, has_text_layer=False)]

        pages: list[PageText] = []
        for page_num in sorted(page_texts.keys()):
            text = "\n".join(page_texts[page_num])
            pages.append(PageText(page_num=page_num, text=text, has_text_layer=False))
        return pages

    def _extract_tables_docling(self, doc: object) -> list[TableData]:
        """Extract tables from Docling OCR result."""
        tables: list[TableData] = []
        try:
            for element in doc.iterate_items():  # type: ignore[attr-defined]
                item = element if not isinstance(element, tuple) else element[1]
                label = getattr(item, "label", "")
                if "table" not in str(label).lower():
                    continue
                prov = getattr(item, "prov", [])
                page = 1
                if prov:
                    page = getattr(prov[0], "page_no", 1) or getattr(prov[0], "page", 1) or 1

                table_data = getattr(item, "data", None)
                if table_data and hasattr(table_data, "grid"):
                    grid = table_data.grid
                    if grid and len(grid) >= 2:
                        headers = [str(c.text) if hasattr(c, "text") else str(c) for c in grid[0]]
                        rows = [
                            [str(c.text) if hasattr(c, "text") else str(c) for c in row]
                            for row in grid[1:]
                        ]
                        tables.append(TableData(page=page, headers=headers, rows=rows))
        except (AttributeError, TypeError):
            pass
        return tables
