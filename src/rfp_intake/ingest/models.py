"""Intermediate parse representations before domain Document."""

from __future__ import annotations

from pydantic import BaseModel, Field

from rfp_intake.domain.schemas import Document, OutlineEntry, TableData


class PageText(BaseModel):
    page_num: int  # 1-indexed
    text: str
    char_count: int = 0
    has_text_layer: bool = True

    def model_post_init(self, __context: object) -> None:
        if not self.char_count:
            self.char_count = len(self.text)


class QualityMetrics(BaseModel):
    total_pages: int
    pages_with_text: int = 0
    avg_chars_per_page: float = 0.0
    alpha_ratio: float = 0.0
    table_count: int = 0
    has_outline: bool = False
    suspected_scanned: bool = False


class ParserMetadata(BaseModel):
    rung_used: int
    parser_name: str
    escalated_from: int | None = None
    escalation_reason: str | None = None
    parse_duration_ms: int = 0
    quality: QualityMetrics | None = None


class ParsedDoc(BaseModel):
    path: str
    pages: list[PageText] = Field(default_factory=list)
    outline: list[OutlineEntry] = Field(default_factory=list)
    tables: list[TableData] = Field(default_factory=list)
    metadata: ParserMetadata

    def to_document(self, doc_id: str) -> Document:
        page_texts = {p.page_num: p.text for p in self.pages}
        return Document(
            id=doc_id,
            path=self.path,
            pages=len(self.pages),
            outline=self.outline,
            tables=self.tables,
            page_texts=page_texts,
            parsing_metadata={
                "rung_used": self.metadata.rung_used,
                "parser_name": self.metadata.parser_name,
                "escalated_from": self.metadata.escalated_from,
                "escalation_reason": self.metadata.escalation_reason,
                "parse_duration_ms": self.metadata.parse_duration_ms,
                "quality": self.metadata.quality.model_dump() if self.metadata.quality else None,
            },
        )
