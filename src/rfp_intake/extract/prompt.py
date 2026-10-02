"""Build extraction prompts from registry + document excerpts."""

from __future__ import annotations

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from rfp_intake.domain.registry import FieldDef, Registry
from rfp_intake.domain.schemas import Document, ExtractionTask, Section, TableData
from rfp_intake.sections import section_page_texts

logger = structlog.get_logger()

EXTRACT_SYSTEM_TEMPLATE = """\
You are extracting {group_label} from a clinical study document for a delivery-budgeting team.

RULES
- Return a record ONLY if the document states it. Never infer, never estimate, never use \
outside knowledge.
- `quote` must be copied character-for-character from the excerpt. It is validated.
- If the document explicitly says none/not applicable, set status="not_specified".
- If you cannot find it, omit the field entirely. Do not guess.
- Set `scope` when the value applies to a cohort/arm/part/country rather than the whole study.
- If a value differs by cohort, emit one record PER cohort. Do not average or merge.

FIELDS
{fields_section}
"""

REPAIR_SUFFIX = """
VALIDATION FAILURE — one or more records failed post-call validation.
Fix the following issues and return the corrected extraction:
{violations}
"""


def build_extract_prompt(
    task: ExtractionTask,
    doc: Document,
    registry: Registry,
) -> list[BaseMessage]:
    """Build the extraction prompt messages for a single task."""
    group = registry.get_group(task.group)
    fields = [f for f in registry.get_fields_for_group(task.group) if not f.derived]

    # System message
    fields_section = _render_fields_section(fields)
    system_content = EXTRACT_SYSTEM_TEMPLATE.format(
        group_label=group.label,
        fields_section=fields_section,
    )

    # Human message: page-tagged excerpt + tables
    excerpt = _build_excerpt(task, doc)
    doc_kind = doc.kind or "unknown"
    headings = _chosen_headings(task, doc)
    sections_line = f"SECTIONS: {headings}\n" if headings else ""
    human_content = (
        f"DOCUMENT: {doc_kind}, pages {task.page_window[0]}–{task.page_window[1]}\n"
        f"{sections_line}"
        f"<excerpt>\n{excerpt}\n</excerpt>"
    )

    return [
        SystemMessage(content=system_content),
        HumanMessage(content=human_content),
    ]


def build_repair_prompt(
    messages: list[BaseMessage],
    violations: list[str],
) -> list[BaseMessage]:
    """Append a repair instruction to the original messages."""
    violations_text = "\n".join(f"- {v}" for v in violations)
    repair_msg = HumanMessage(
        content=REPAIR_SUFFIX.format(violations=violations_text)
    )
    return [*messages, repair_msg]


def _render_fields_section(fields: list[FieldDef]) -> str:
    """Render the FIELDS section of the extraction prompt."""
    lines: list[str] = []
    for f in fields:
        parts = [f"- {f.id} ({f.label}): type={f.type}"]
        if f.values:
            parts.append(f"  allowed: {f.values}")
        if f.aliases:
            parts.append(f"  aliases: {', '.join(f.aliases[:5])}")
        if f.hint:
            parts.append(f"  hint: {f.hint.strip()}")
        lines.append("\n".join(parts))
    return "\n".join(lines)


def build_excerpt(task: ExtractionTask, doc: Document) -> str:
    """Public wrapper for building the excerpt text. Used by validation too."""
    return _build_excerpt(task, doc)


def _build_excerpt(task: ExtractionTask, doc: Document) -> str:
    """The text one extraction call reads, tagged with the page each part is on.

    Built from the sections PLAN chose (`ExtractionTask.section_ids`), cut at
    their exact character offsets, so text from a section PLAN did not choose
    never reaches the model. Before 2026-10-02 this was every page in
    `page_window`, which is how section 1.3.2 of the sample protocol — describing
    a different study — reached a call that had chosen section 1.3.1 on the same
    page.

    One block per section per page, each with its own "--- Page N ---" marker, so
    the model can still report a page. Two blocks are never joined into one, so a
    quote cannot span a gap between two sections and still validate. A page
    appears twice if two chosen sections both have text on it.

    A task with no `section_ids` falls back to whole pages in `page_window`, which
    is what a task built by hand without sections means.
    """
    if not task.section_ids:
        return _build_page_excerpt(task, doc)

    sections = _chosen_sections(task, doc)
    if not sections:
        # The task names sections and the document has none of them. Falling back
        # to whole pages here would quietly restore the behaviour stage 2 removed,
        # so the excerpt is empty and the mismatch is logged. EXTRACT then
        # extracts nothing from this task rather than extracting from text PLAN
        # did not choose.
        logger.warning(
            "excerpt_sections_not_on_document",
            doc_id=doc.id,
            group=task.group,
            section_ids=task.section_ids,
            document_sections=len(doc.sections),
        )
        return ""

    start, end = task.page_window
    parts: list[str] = []
    for section in sections:
        for page in sorted(section_page_texts(doc, section)):
            if not start <= page <= end:
                continue
            text = section_page_texts(doc, section)[page]
            if text.strip():
                parts.append(f"--- Page {page} ---\n{text}")

    # TableData records the page a table is on but not where on the page, so a
    # table is included when its page falls inside a chosen section. That can let
    # through a table that belongs to a neighbouring section on the same page.
    # Recorded as a known limitation in CLAUDE.md.
    seen: set[int] = set()
    for table in doc.tables:
        if table.page in seen or not start <= table.page <= end:
            continue
        if any(s.page_start <= table.page <= s.page_end for s in sections):
            seen.add(table.page)
            parts.append(_render_table(table))

    return "\n\n".join(parts)


def _build_page_excerpt(task: ExtractionTask, doc: Document) -> str:
    """Whole pages in the task's window — the pre-2026-10-02 excerpt."""
    parts: list[str] = []
    start, end = task.page_window

    for page_num in range(start, end + 1):
        text = doc.page_texts.get(page_num, "")
        if text.strip():
            parts.append(f"--- Page {page_num} ---\n{text}")

    for table in doc.tables:
        if start <= table.page <= end:
            parts.append(_render_table(table))

    return "\n\n".join(parts)


def _chosen_sections(task: ExtractionTask, doc: Document) -> list[Section]:
    """The document's sections that this task names, in document order.

    A named section the document does not have is skipped rather than raising,
    because a task and a document always travel together in the graph state and
    a mismatch means a caller built one by hand.
    """
    if not task.section_ids:
        return []
    wanted = set(task.section_ids)
    return [section for section in doc.sections if section.id in wanted]


def _chosen_headings(task: ExtractionTask, doc: Document) -> str:
    """The chosen sections' headings, for the prompt's SECTIONS line."""
    return "; ".join(section.heading.strip() for section in _chosen_sections(task, doc))


def _render_table(table: TableData) -> str:
    """Render a table as readable text for the prompt."""
    lines = [f"--- Table (page {table.page}) ---"]
    if table.caption:
        lines.append(f"Caption: {table.caption}")
    if table.headers:
        lines.append(" | ".join(table.headers))
        lines.append("-" * (len(" | ".join(table.headers))))
    for row in table.rows:
        lines.append(" | ".join(row))
    return "\n".join(lines)
