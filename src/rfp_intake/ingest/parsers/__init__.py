"""Parser factory with auto-escalation through the fidelity ladder."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import structlog

from rfp_intake.ingest.models import ParsedDoc, ParserMetadata, QualityMetrics
from rfp_intake.ingest.parsers.quality import QualityGate, detect_scanned

logger = structlog.get_logger()


def parse_with_escalation(path: Path, start_rung: int = 1) -> ParsedDoc:
    """Parse a document, escalating through rungs until quality clears."""
    if detect_scanned(path):
        logger.info("scanned_document_detected", path=str(path))
        start_rung = max(start_rung, 3)

    gate = QualityGate()
    current_rung = start_rung
    escalated_from: int | None = None
    escalation_reason: str | None = None

    while current_rung <= 3:
        parser = _get_parser(current_rung)
        t0 = time.perf_counter_ns()
        result: ParsedDoc = parser.parse(path)
        duration_ms = (time.perf_counter_ns() - t0) // 1_000_000

        quality = _compute_quality(result)
        passed, reason = gate.check(quality)

        if passed or current_rung == 3:
            if not passed:
                logger.warning(
                    "quality_gate_failed_at_terminal_rung",
                    path=str(path),
                    reason=reason,
                    rung=current_rung,
                )
            result.metadata = ParserMetadata(
                rung_used=current_rung,
                parser_name=parser.__class__.__name__,
                escalated_from=escalated_from,
                escalation_reason=escalation_reason,
                parse_duration_ms=duration_ms,
                quality=quality,
            )
            return result

        logger.info(
            "escalating_parser",
            path=str(path),
            from_rung=current_rung,
            to_rung=current_rung + 1,
            reason=reason,
        )
        escalated_from = current_rung
        escalation_reason = reason
        current_rung += 1

    # Should not reach here, but satisfy type checker
    raise RuntimeError("Parser escalation exhausted without result")


def _get_parser(rung: int) -> Any:  # Returns a Parser protocol impl
    if rung == 1:
        from rfp_intake.ingest.parsers.rung1 import Rung1Parser
        return Rung1Parser()
    elif rung == 2:
        from rfp_intake.ingest.parsers.rung2 import Rung2Parser
        return Rung2Parser()
    elif rung == 3:
        from rfp_intake.ingest.parsers.rung3 import Rung3Parser
        return Rung3Parser()
    raise ValueError(f"Unknown rung: {rung}")


def _compute_quality(result: ParsedDoc) -> QualityMetrics:
    total_pages = len(result.pages)
    if total_pages == 0:
        return QualityMetrics(total_pages=0, suspected_scanned=True)

    pages_with_text = sum(1 for p in result.pages if p.char_count > 50)
    total_chars = sum(p.char_count for p in result.pages)
    avg_chars = total_chars / total_pages if total_pages > 0 else 0.0

    total_alpha = sum(c.isalpha() for p in result.pages for c in p.text)
    total_len = sum(len(p.text) for p in result.pages)
    alpha_ratio = total_alpha / total_len if total_len > 0 else 0.0

    return QualityMetrics(
        total_pages=total_pages,
        pages_with_text=pages_with_text,
        avg_chars_per_page=avg_chars,
        alpha_ratio=alpha_ratio,
        table_count=len(result.tables),
        has_outline=len(result.outline) > 0,
        suspected_scanned=pages_with_text < total_pages * 0.5,
    )
