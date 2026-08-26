"""Rung 2 — Docling with table structure. The workhorse for complex layouts."""

from __future__ import annotations

from pathlib import Path

import structlog

from rfp_intake.domain.schemas import OutlineEntry, TableData
from rfp_intake.ingest.models import PageText, ParsedDoc, ParserMetadata

logger = structlog.get_logger()


class Rung2Parser:
    rung: int = 2

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def parse(self, path: Path) -> ParsedDoc:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(str(path))
        doc = result.document

        pages = self._extract_pages(doc)
        outline = self._extract_outline(doc)
        tables = self._extract_tables(doc)

        return ParsedDoc(
            path=str(path),
            pages=pages,
            outline=outline,
            tables=tables,
            metadata=ParserMetadata(rung_used=2, parser_name="Rung2Parser"),
        )

    def _extract_pages(self, doc: object) -> list[PageText]:
        """Extract page-indexed text from Docling document."""
        pages: list[PageText] = []
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
            # Fall back to export_to_markdown split by pages
            md = doc.export_to_markdown()  # type: ignore[attr-defined]
            pages.append(PageText(page_num=1, text=md))
            return pages

        if not page_texts:
            md = doc.export_to_markdown()  # type: ignore[attr-defined]
            pages.append(PageText(page_num=1, text=md))
            return pages

        for page_num in sorted(page_texts.keys()):
            text = "\n".join(page_texts[page_num])
            pages.append(PageText(page_num=page_num, text=text))

        return pages

    def _extract_outline(self, doc: object) -> list[OutlineEntry]:
        """Extract headings from the Docling document."""
        entries: list[OutlineEntry] = []
        try:
            for element in doc.iterate_items():  # type: ignore[attr-defined]
                item = element if not isinstance(element, tuple) else element[1]
                label = getattr(item, "label", "")
                if "heading" in str(label).lower() or "title" in str(label).lower():
                    text = getattr(item, "text", "")
                    prov = getattr(item, "prov", [])
                    page = 1
                    level = 1
                    if prov:
                        page = getattr(prov[0], "page_no", 1) or getattr(prov[0], "page", 1) or 1
                    if "section" in str(label).lower():
                        level = 2
                    if text:
                        entries.append(OutlineEntry(
                            heading=text,
                            page_start=page,
                            level=level,
                        ))
        except (AttributeError, TypeError):
            pass
        return entries

    def _extract_tables(self, doc: object) -> list[TableData]:
        """Extract structured tables from Docling document."""
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

                # Try to get structured table data
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
                        continue

                # Fallback: export table as markdown and parse
                export_fn = getattr(item, "export_to_markdown", None)
                if export_fn:
                    md = export_fn()
                    parsed = self._parse_markdown_table(md, page)
                    if parsed:
                        tables.append(parsed)
        except (AttributeError, TypeError):
            pass
        return tables

    def _parse_markdown_table(self, md: str, page: int) -> TableData | None:
        """Parse a markdown table into structured data."""
        lines = [ln.strip() for ln in md.strip().split("\n") if ln.strip()]
        data_lines = [
            ln for ln in lines if "|" in ln and not all(c in "-| " for c in ln)
        ]
        if len(data_lines) < 2:
            return None

        def parse_row(line: str) -> list[str]:
            cells = line.split("|")
            cells = [c.strip() for c in cells]
            if cells and not cells[0]:
                cells = cells[1:]
            if cells and not cells[-1]:
                cells = cells[:-1]
            return cells

        headers = parse_row(data_lines[0])
        rows = [parse_row(ln) for ln in data_lines[1:]]
        return TableData(page=page, headers=headers, rows=rows)
