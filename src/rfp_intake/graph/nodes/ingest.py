"""INGEST node — parse documents via the fidelity ladder."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from rfp_intake.config.settings import get_settings
from rfp_intake.domain.schemas import Document, RunError, RunState
from rfp_intake.ingest.page_labels import read_page_labels
from rfp_intake.ingest.parsers import parse_with_escalation
from rfp_intake.io.inputs import get_resolver

logger = structlog.get_logger()


def ingest_node(state: RunState) -> dict[str, Any]:
    """Parse all input documents. Returns state update dict for LangGraph."""
    settings = get_settings()
    source = str(settings.run_dir / state.run_id / "inputs")
    documents: list[Document] = []
    errors: list[RunError] = []

    try:
        resolver = get_resolver(source)
        paths = resolver.resolve(source)
    except (FileNotFoundError, ValueError) as e:
        errors.append(RunError(node="INGEST", error=str(e)))
        return {"documents": documents, "errors": errors}

    for path in paths:
        doc_id = f"doc-{uuid.uuid4().hex[:8]}"
        try:
            parsed = parse_with_escalation(path)
            doc = parsed.to_document(doc_id)
            # The number printed on each page, where it differs from the page's
            # position in the file. Read here rather than in a parser because
            # two of the three parsers do not open the PDF themselves, and the
            # page text they produce is all this needs. Change 7 of the
            # 2026-10-07 review.
            doc.page_labels = read_page_labels(path, doc.page_texts)
            documents.append(doc)
            logger.info(
                "document_ingested",
                run_id=state.run_id,
                doc_id=doc_id,
                path=str(path),
                pages=doc.pages,
                rung=parsed.metadata.rung_used,
                tables=len(doc.tables),
                printed_page_numbers=len(doc.page_labels),
                duration_ms=parsed.metadata.parse_duration_ms,
            )
        except Exception as e:
            logger.error("ingest_failed", doc_id=doc_id, path=str(path), error=str(e))
            errors.append(RunError(
                node="INGEST",
                task_id=doc_id,
                error=f"Failed to parse {path.name}: {e}",
            ))

    return {"documents": documents, "errors": errors}
