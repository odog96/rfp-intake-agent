"""Build extraction prompts from registry + document excerpts."""

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from rfp_intake.domain.registry import FieldDef, Registry
from rfp_intake.domain.schemas import Document, ExtractionTask, TableData

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
    human_content = (
        f"DOCUMENT: {doc_kind}, pages {task.page_window[0]}–{task.page_window[1]}\n"
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
    """Build page-tagged text excerpt + tables for the task window."""
    parts: list[str] = []
    start, end = task.page_window

    # Page text
    for page_num in range(start, end + 1):
        text = doc.page_texts.get(page_num, "")
        if text.strip():
            parts.append(f"--- Page {page_num} ---\n{text}")

    # Tables in range
    tables_in_range = [t for t in doc.tables if start <= t.page <= end]
    for table in tables_in_range:
        parts.append(_render_table(table))

    return "\n\n".join(parts)


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
