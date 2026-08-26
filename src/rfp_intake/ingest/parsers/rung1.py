"""Rung 1 — PyMuPDF text + pdfplumber tables. Fast path for native-text PDFs."""

from __future__ import annotations

from pathlib import Path

import fitz
import pdfplumber

from rfp_intake.domain.schemas import OutlineEntry, TableData
from rfp_intake.ingest.models import PageText, ParsedDoc, ParserMetadata


class Rung1Parser:
    rung: int = 1

    def can_parse(self, path: Path) -> bool:
        """Check if the PDF has a meaningful text layer on first 3 pages."""
        try:
            doc = fitz.open(str(path))
        except Exception:
            return False
        try:
            pages_to_check = min(3, len(doc))
            return all(
                len(doc[i].get_text().strip()) >= 100
                for i in range(pages_to_check)
            )
        finally:
            doc.close()

    def parse(self, path: Path) -> ParsedDoc:
        pages = self._extract_text(path)
        outline = self._extract_outline(path)
        tables = self._extract_tables(path)

        return ParsedDoc(
            path=str(path),
            pages=pages,
            outline=outline,
            tables=tables,
            metadata=ParserMetadata(rung_used=1, parser_name="Rung1Parser"),
        )

    def _extract_text(self, path: Path) -> list[PageText]:
        doc = fitz.open(str(path))
        pages: list[PageText] = []
        try:
            for i in range(len(doc)):
                text = doc[i].get_text()
                has_text = len(text.strip()) > 0
                pages.append(PageText(
                    page_num=i + 1,
                    text=text,
                    has_text_layer=has_text,
                ))
        finally:
            doc.close()
        return pages

    def _extract_outline(self, path: Path) -> list[OutlineEntry]:
        doc = fitz.open(str(path))
        entries: list[OutlineEntry] = []
        try:
            toc = doc.get_toc()
            total_pages = len(doc)
            for i, (level, title, page) in enumerate(toc):
                page_end = toc[i + 1][2] if i + 1 < len(toc) else total_pages
                entries.append(OutlineEntry(
                    heading=title,
                    page_start=page,
                    page_end=page_end,
                    level=level,
                ))
        finally:
            doc.close()
        return entries

    def _extract_tables(self, path: Path) -> list[TableData]:
        tables: list[TableData] = []
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                page_tables = page.extract_tables()
                if not page_tables:
                    continue
                for raw_table in page_tables:
                    if not raw_table or len(raw_table) < 2:
                        continue
                    headers = [str(c) if c else "" for c in raw_table[0]]
                    rows = [
                        [str(c) if c else "" for c in row]
                        for row in raw_table[1:]
                    ]
                    tables.append(TableData(page=i, headers=headers, rows=rows))
        return tables
