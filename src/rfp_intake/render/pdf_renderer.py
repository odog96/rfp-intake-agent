"""report.pdf — the human deliverable. Pure function returning bytes; no
filesystem access (job/output.py owns that). See ARCHITECTURE.md §4.10.

Deliberately simple by design: a flowing document of headings and labeled
text, no tables or figures. reportlab's Platypus flowables (Paragraph,
Spacer, PageBreak) are exactly suited to that and add no native-dependency
risk in a locked-down CML environment — see the PDF-library discussion this
module resulted from.
"""

from __future__ import annotations

import io
from collections import defaultdict
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from rfp_intake.domain.registry import Registry
from rfp_intake.domain.schemas import Contradiction, ResolvedField, RunState

_STATUS_COLORS = {
    "confirmed": colors.HexColor("#2e7d32"),
    "needs_review": colors.HexColor("#e65100"),
    "not_specified": colors.HexColor("#757575"),
    "not_found": colors.HexColor("#9e9e9e"),
}
_CONFLICT_COLOR = colors.HexColor("#c62828")

_STATUS_LABELS = {
    "confirmed": "Confirmed",
    "needs_review": "Needs review",
    "not_specified": "Not specified in source documents",
    "not_found": "Not found in source documents",
}


def _status_color(rf: ResolvedField) -> colors.HexColor:
    if rf.contradiction is not None and rf.contradiction.verdict == "conflict":
        return _CONFLICT_COLOR
    return _STATUS_COLORS.get(rf.status, _STATUS_COLORS["not_found"])


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": base["Title"],
        "section": base["Heading2"],
        "group": base["Heading3"],
        "body": base["BodyText"],
        "meta": ParagraphStyle(
            "meta", parent=base["BodyText"], fontSize=9, textColor=colors.HexColor("#616161"),
        ),
        "quote": ParagraphStyle(
            "quote", parent=base["BodyText"], fontSize=9, leftIndent=18,
            textColor=colors.HexColor("#424242"), spaceAfter=8,
        ),
        "field_label": ParagraphStyle(
            "field_label", parent=base["BodyText"], fontSize=10.5, spaceBefore=6, spaceAfter=2,
        ),
    }


def build_report_pdf(state: RunState, registry: Registry, *, generated_at: str) -> bytes:
    """Render the PDF report. `generated_at` is passed in (not computed here)
    so this stays a pure function of its inputs, like the other renderers.
    """
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    )

    story: list[object] = []
    story += _title_block(state, registry, generated_at, styles)
    story += _executive_summary(state, styles)

    adjudicated = [c for c in state.contradictions if c.verdict is not None]
    if adjudicated:
        story.append(PageBreak())
        story += _contradictions_section(adjudicated, registry, styles)

    story.append(PageBreak())
    story += _variables_section(state, registry, styles)

    doc.build(story)
    return buf.getvalue()


def _title_block(state: RunState, registry: Registry, generated_at: str, styles: dict) -> list:  # type: ignore[type-arg]
    return [
        Paragraph("RFP Intake Report", styles["title"]),
        Paragraph(f"Run: {escape(state.run_id)}", styles["meta"]),
        Paragraph(f"Field registry: {escape(registry.registry_version)}", styles["meta"]),
        Paragraph(f"Generated: {escape(generated_at)}", styles["meta"]),
        Spacer(1, 0.3 * inch),
    ]


def _executive_summary(state: RunState, styles: dict) -> list:  # type: ignore[type-arg]
    counts: dict[str, int] = defaultdict(int)
    for rf in state.resolved:
        counts[rf.status] += 1

    adjudicated = [c for c in state.contradictions if c.verdict is not None]
    severity_counts: dict[str, int] = defaultdict(int)
    for c in adjudicated:
        if c.verdict != "not_a_conflict":
            severity_counts[c.severity or "unknown"] += 1

    lines = [
        f"{counts.get('confirmed', 0)} confirmed, "
        f"{counts.get('needs_review', 0)} need review, "
        f"{counts.get('not_specified', 0)} not specified in source, "
        f"{counts.get('not_found', 0)} not found.",
    ]
    if severity_counts:
        lines.append(
            "Unresolved disagreements: "
            + ", ".join(f"{n} {sev}" for sev, n in sorted(severity_counts.items()))
            + "."
        )

    story: list[object] = [Paragraph("Executive summary", styles["section"])]
    for line in lines:
        story.append(Paragraph(escape(line), styles["body"]))
    story.append(Spacer(1, 0.2 * inch))
    return story


def _contradictions_section(
    adjudicated: list[Contradiction], registry: Registry, styles: dict  # type: ignore[type-arg]
) -> list:  # type: ignore[type-arg]
    field_by_id = {f.id: f for f in registry.fields}
    story: list[object] = [Paragraph("Contradictions", styles["section"])]

    for c in adjudicated:
        field_def = field_by_id.get(c.field_id)
        label = field_def.label if field_def else c.field_id
        verdict_color = _CONFLICT_COLOR if c.verdict == "conflict" else colors.HexColor("#e65100")

        story.append(Paragraph(
            f'{escape(label)} — <font color="{verdict_color.hexval()}">'
            f'{escape((c.verdict or "").replace("_", " "))}</font>'
            f' ({escape(c.severity or "unspecified")} severity)',
            styles["field_label"],
        ))
        if c.explanation:
            story.append(Paragraph(escape(c.explanation), styles["body"]))
        for r in c.records:
            story.append(Paragraph(
                f'{escape(r.provenance.doc_id)} ({escape(r.provenance.doc_kind)}, '
                f'p.{r.provenance.page}): "{escape(r.quote)}"',
                styles["quote"],
            ))
        story.append(Spacer(1, 0.1 * inch))

    return story


def _variables_section(state: RunState, registry: Registry, styles: dict) -> list:  # type: ignore[type-arg]
    by_field: dict[str, list[ResolvedField]] = defaultdict(list)
    for rf in state.resolved:
        by_field[rf.field_id].append(rf)

    story: list[object] = [Paragraph("Variables by group", styles["section"])]

    for group in registry.groups:
        group_fields = [f for f in registry.fields if f.group == group.id]
        if not group_fields:
            continue

        story.append(Paragraph(escape(group.label), styles["group"]))
        for field_def in group_fields:
            entries = by_field.get(field_def.id, [])
            if not entries:
                story += _field_lines(field_def.label, None, styles)
                continue
            for rf in entries:
                story += _field_lines(field_def.label, rf, styles)

    return story


def _field_lines(label: str, rf: ResolvedField | None, styles: dict) -> list:  # type: ignore[type-arg]
    """One flowable for the field header, plus one more if there's a quote to cite."""
    if rf is None:
        status_text = _STATUS_LABELS["not_found"]
        color = _STATUS_COLORS["not_found"]
        return [Paragraph(
            f'<b>{escape(label)}:</b> <font color="{color.hexval()}">{status_text}</font>',
            styles["field_label"],
        )]

    color = _status_color(rf)
    status_text = _STATUS_LABELS.get(rf.status, rf.status)
    scope_suffix = f" [{escape(rf.scope)}]" if rf.scope else ""
    value_text = escape(str(rf.value)) if rf.value is not None else "—"

    header = (
        f'<b>{escape(label)}{scope_suffix}:</b> {value_text} '
        f'(<font color="{color.hexval()}">{status_text}</font>, '
        f"{rf.confidence:.0%} confidence)"
    )
    lines = [Paragraph(header, styles["field_label"])]

    if rf.quote and rf.sources:
        src = rf.sources[0]
        detail = f'Source: {escape(src.doc_id)} p.{src.page} — "{escape(rf.quote)}"'
        lines.append(Paragraph(detail, styles["quote"]))

    return lines
