"""CLASSIFY node — label documents by type using a cheap LLM call."""

from __future__ import annotations

from typing import Any, Literal

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from rfp_intake.domain.schemas import Document, RunError, RunState
from rfp_intake.llm.provider import get_llm
from rfp_intake.llm.structured import get_structured_output

logger = structlog.get_logger()

CLASSIFY_SYSTEM_PROMPT = """\
You are classifying a clinical study document for a delivery-budgeting team.

Based on the first pages and outline headings provided, determine the document type.

Document types:
- rfp: Request for Proposal — asks a CRO to bid on running a study
- protocol: Clinical study protocol — describes the scientific design
- amendment: A change to an existing protocol
- soa: Schedule of Assessments — a standalone visit/procedure grid
- other: Does not fit the above categories

Also extract metadata if visible: version label, document date (ISO format),
sponsor name, protocol ID.
"""

MAX_CHARS_PER_PAGE = 2000
MAX_PAGES = 3
MAX_OUTLINE_HEADINGS = 20


class ClassificationResult(BaseModel):
    kind: Literal["rfp", "protocol", "amendment", "soa", "other"]
    confidence: float = Field(ge=0.0, le=1.0)
    version_label: str | None = None
    document_date: str | None = None
    sponsor: str | None = None
    protocol_id: str | None = None


def classify_node(state: RunState) -> dict[str, Any]:
    """Classify all documents in the run. Returns state update dict."""
    llm = get_llm("classify")
    structured = get_structured_output(llm)
    errors: list[RunError] = []

    updated_docs: list[Document] = []
    for doc in state.documents:
        try:
            result = _classify_single(doc, structured)
            doc.kind = result.kind
            doc.confidence = result.confidence
            doc.version_label = result.version_label
            doc.document_date = result.document_date
            doc.sponsor = result.sponsor
            doc.protocol_id = result.protocol_id
            logger.info(
                "document_classified",
                run_id=state.run_id,
                doc_id=doc.id,
                kind=result.kind,
                confidence=result.confidence,
            )
        except Exception as e:
            logger.error("classify_failed", doc_id=doc.id, error=str(e))
            errors.append(RunError(
                node="CLASSIFY",
                task_id=doc.id,
                error=f"Classification failed for {doc.id}: {e}",
            ))
        updated_docs.append(doc)

    return {"documents": updated_docs, "errors": errors}


def _classify_single(doc: Document, structured: Any) -> ClassificationResult:
    """Classify a single document."""
    content_parts: list[str] = []

    # First 3 pages, up to 2000 chars each
    sorted_pages = sorted(doc.page_texts.keys())[:MAX_PAGES]
    for page_num in sorted_pages:
        text = doc.page_texts[page_num][:MAX_CHARS_PER_PAGE]
        content_parts.append(f"--- Page {page_num} ---\n{text}")

    # Outline headings
    if doc.outline:
        headings = [e.heading for e in doc.outline[:MAX_OUTLINE_HEADINGS]]
        content_parts.append("--- Outline ---\n" + "\n".join(headings))

    human_content = "\n\n".join(content_parts)

    messages = [
        SystemMessage(content=CLASSIFY_SYSTEM_PROMPT),
        HumanMessage(content=human_content),
    ]

    result: ClassificationResult = structured.extract(ClassificationResult, messages)
    return result
