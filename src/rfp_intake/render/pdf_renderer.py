"""report.pdf — the human deliverable. Pure function returning bytes; no
filesystem access (job/output.py owns that). See ARCHITECTURE.md §4.10.

Laid out for an analyst who is checking the pipeline's work instead of reading
the RFP and protocol themselves. Five sections, in this order:

    1  Header       Documents reviewed, the counts, and how to read the report.
    2  Variables    Every variable, grouped: value, status, confidence, pages.
                    Variables with many entries (visit schedules, timeline
                    components) follow in section 5 as their own tables.
    3  Disagreements  Where the documents say different things, numbered, both
                    values side by side.
    4  Flagged      Every other value needing a look, each with the reason.
    5  Schedules    The many-entry variables, one table each.

Then Appendix A (every source quote), Appendix B (the reviewer's reasoning) and
last of all the words used in this report.

Variables come before the two review sections because that is the order the
customer asked for: the reader wants to see what was extracted before being
asked to adjudicate any of it.

What goes in each place is decided in report_model.py; this module only lays
it out. reportlab's Platypus tables and paragraphs add no native dependency,
which matters in a locked-down CML environment.

Before 2026-09-18 this was a flowing document of paragraphs, one per extracted
entry with its quote inline. Run r-20260901-172918 came to fifteen pages that
way, and a customer said the summary was too long to read. The five sections
above replaced the 2026-09-18 layout on 2026-09-30, which put the two review
sections on page 1 and printed flagged variables as a run-on list of names with
no value, no confidence and no reason.
"""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from rfp_intake.domain.registry import Registry
from rfp_intake.domain.schemas import RunState
from rfp_intake.gate import CONFIDENCE_CONFIRMED
from rfp_intake.render.report_model import (
    DECISION_VALUE_CHARS,
    Decision,
    ReportModel,
    Row,
    build_report_model,
)

# What the Confidence column means, in the report itself. Angus Gray asked how
# the percentage is produced, and the honest answer is short enough to print:
# the number is the extraction model's own self-rating, which nothing in the
# pipeline checks against known-correct answers. Saying so in the report is the
# difference between a number a reviewer can weigh and one they might trust.
_CONFIDENCE_NOTE = (
    "<b>Confidence</b> is the extraction model's own rating of how clearly the document "
    "stated the value, plus 5 points for each extra passage that agreed. It has not been "
    "checked against hand-marked answers, so it is not a probability of being correct."
)

_GREEN = colors.HexColor("#2e7d32")
_AMBER = colors.HexColor("#e65100")
_RED = colors.HexColor("#c62828")
_GREY = colors.HexColor("#757575")
_BLUE = colors.HexColor("#1565c0")
_RULE = colors.HexColor("#d0d0d0")
_HEADER_BG = colors.HexColor("#e4e4e4")
_BAND_BG = colors.HexColor("#f4f4f4")

_STATUS_WORDS = {
    "confirmed": ("Confirmed", _GREEN),
    "needs_review": ("Review", _AMBER),
    "not_specified": ("Not specified", _GREY),
    "not_found": ("Not found", _GREY),
}

_MARGIN = 0.6 * inch
_WIDTH = LETTER[0] - 2 * _MARGIN

# Variable | Value | Status | Confidence | Where
_MAIN_COLS = [1.6 * inch, 2.44 * inch, 1.0 * inch, 0.78 * inch, _WIDTH - 5.82 * inch]
# Applies to | Value | Status | Confidence | Where — schedule rows have short
# pages and long labels, so the first column is wider and the last narrower.
_SCHEDULE_COLS = [2.05 * inch, 2.57 * inch, 1.0 * inch, 0.78 * inch, _WIDTH - 6.4 * inch]
# # | Variable | What the documents say | Assessment
_DECISION_COLS = [0.25 * inch, 1.45 * inch, 4.0 * inch, _WIDTH - 5.7 * inch]
# Variable | Value | Confidence | Why it is flagged | Where
_FLAGGED_COLS = [1.55 * inch, 1.72 * inch, 0.78 * inch, 2.13 * inch, _WIDTH - 6.18 * inch]
# Term | What it means
_GLOSSARY_COLS = [1.6 * inch, _WIDTH - 1.6 * inch]


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["BodyText"], fontSize=9, leading=11.5)
    cell = ParagraphStyle("cell", parent=body, fontSize=8.5, leading=10.0, spaceBefore=0,
                          spaceAfter=0)
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=17, leading=21,
                                alignment=0, spaceAfter=2),
        "section": ParagraphStyle("section", parent=base["Heading2"], fontSize=12.5,
                                  spaceBefore=10, spaceAfter=4),
        "group": ParagraphStyle("group", parent=base["Heading3"], fontSize=10.5,
                                spaceBefore=7, spaceAfter=2.5),
        "body": body,
        "meta": ParagraphStyle("meta", parent=body, fontSize=8.5, textColor=_GREY),
        "cell": cell,
        "cell_head": ParagraphStyle("cell_head", parent=cell, fontName="Helvetica-Bold"),
        "cell_small": ParagraphStyle("cell_small", parent=cell, fontSize=7.5, leading=8.6,
                                     textColor=colors.HexColor("#555555")),
        "evidence": ParagraphStyle("evidence", parent=body, fontSize=7.5, leading=9.2,
                                   leftIndent=10, firstLineIndent=-10, spaceAfter=2.5),
    }


def build_report_pdf(state: RunState, registry: Registry, *, generated_at: str) -> bytes:
    """Render the PDF report. `generated_at` is passed in (not computed here)
    so this stays a pure function of its inputs, like the other renderers.
    """
    model = build_report_model(state, registry)
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        topMargin=_MARGIN, bottomMargin=0.7 * inch,
        leftMargin=_MARGIN, rightMargin=_MARGIN,
        title="RFP Intake Summary", author="RFP Intake Agent",
    )

    story: list[object] = []
    story += _header_block(model, generated_at, styles)
    story += _variables(model, styles)
    story += _disagreements(model, styles)
    story += _flagged(model, styles)
    story += _schedules(model, styles)
    story.append(PageBreak())
    story += _evidence_appendix(model, styles)
    story += _reasoning_appendix(model, styles)
    story += _removed_appendix(model, styles)
    # The word list sits at the back, after every appendix. It was between the
    # schedules and Appendix A until 2026-09-30, where its two pages pushed
    # Appendix A to page 10 — past the eight-page budget for the part a reader is
    # expected to read straight through. A glossary at the back is also where a
    # reader looks for one.
    story += _glossary(model, styles)

    def footer(canvas, doc_):  # type: ignore[no-untyped-def]
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(_GREY)
        canvas.drawString(_MARGIN, 0.4 * inch, f"RFP Intake Summary · run {model.run_id}")
        canvas.drawRightString(LETTER[0] - _MARGIN, 0.4 * inch, f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# 1. Header block
# --------------------------------------------------------------------------- #


def _header_block(model: ReportModel, generated_at: str, styles: dict) -> list:  # type: ignore[type-arg]
    """Title, the documents, the counts, and how to read what follows.

    Five short lines and nothing else. Everything a reader has to decide is in
    sections 3 and 4, after the variables, so nothing here asks for a judgement.
    """
    story: list[object] = [
        Paragraph("RFP Intake Summary", styles["title"]),
        Paragraph(
            f"Run {escape(model.run_id)} · generated {escape(generated_at)} · "
            f"field registry {escape(model.registry_version)}",
            styles["meta"],
        ),
        Spacer(1, 6),
    ]

    if model.documents:
        lines = []
        for d in model.documents:
            name = escape(d.filename) if d.filename else f"document {escape(d.doc_id)}"
            pages = f", {d.pages} pages" if d.pages else ""
            lines.append(f"<b>{escape(d.code)}</b> = {name}{pages}")
        story.append(Paragraph("Documents reviewed: " + " · ".join(lines), styles["body"]))

    review = model.review_rows()
    review_bd = [r for r in review if r.budget_driver]
    counts = (
        f"Values found for <b>{model.variables_found} of {model.variables_total}</b> variables. "
        f"<b>{len(model.decisions)}</b> disagreement{'s' if len(model.decisions) != 1 else ''} "
        f"between the documents need{'s' if len(model.decisions) == 1 else ''} a decision; "
        f"<b>{len(review)}</b> other value{'s' if len(review) != 1 else ''} "
        f"{'is' if len(review) == 1 else 'are'} flagged for review"
        + (f", {len(review_bd)} of them budget drivers." if review_bd else ".")
    )
    story.append(Paragraph(counts, styles["body"]))

    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "<b>How to read this report.</b> <b>All variables</b> lists everything the tool read, "
        "grouped, with where in the documents each value came from; "
        '<font color="#1565c0">BUDGET</font> marks a variable that drives the budget. '
        "<b>Disagreements between the documents</b> and <b>Flagged for review</b> then list the "
        "values a person has to settle, each with the reason it is there. Nothing is left out of "
        "the tables: every value's source passage is quoted in Appendix A, and any clinical term "
        "used here is explained in <b>Words used in this report</b> on the last pages.",
        styles["body"],
    ))
    story.append(Paragraph(_CONFIDENCE_NOTE, styles["body"]))
    return story


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------- #
# 3. Disagreements between the documents
#    The numbers in these banners are the printed section order, which
#    build_report_pdf sets. They are not the order the functions appear in this
#    file: section 3 is defined before section 2 because _variables is long.
# --------------------------------------------------------------------------- #


def _disagreements(model: ReportModel, styles: dict) -> list:  # type: ignore[type-arg]
    """Every disagreement the reviewer did not dismiss, whatever its severity.

    Low-severity disagreements are not demoted into section 4. Severity is the
    reviewing model's own rating rather than something the code works out, so
    splitting on it would move a mis-rated disagreement into the quieter list.
    """
    story: list[object] = [
        Paragraph("Disagreements between the documents", styles["section"]),
    ]
    if not model.decisions:
        story.append(Paragraph(
            "The documents do not contradict each other on any variable.", styles["body"],
        ))
        return story
    story.append(Paragraph(
        "Each row is one variable where the documents say different things. Pick the value that "
        "belongs in the budget, or ask the sponsor. The tool does not choose for you — where it "
        "shows a provisional value, that is only which document normally takes precedence. The "
        "reviewer's full reasoning for each is in Appendix B.",
        styles["body"],
    ))
    story.append(Spacer(1, 3))
    story.append(_decision_table(model.decisions, styles))
    return story


def _decision_table(decisions: list[Decision], styles: dict) -> Table:  # type: ignore[type-arg]
    head = ["#", "Variable", "What the documents say", "Assessment"]
    data = [[Paragraph(h, styles["cell_head"]) for h in head]]
    for d in decisions:
        said = "<br/>".join(
            f"• {escape(_clip(p.text, DECISION_VALUE_CHARS))} "
            f'<font color="#757575">— {escape(p.sources)}</font>'
            for p in d.positions
        )
        color = _RED if d.verdict == "conflict" else _AMBER
        assessment = (
            f'<font color="{color.hexval()}"><b>{escape(d.verdict.replace("_", " ").capitalize())}'
            f"</b></font>, {escape(d.severity or 'unrated')} severity"
        )
        if d.provisional:
            assessment += (
                f"<br/><font size=\"7.5\">Provisional, by document precedence: "
                f"{escape(_clip(d.provisional, 80))}</font>"
            )
        else:
            assessment += '<br/><font size="7.5">No value chosen.</font>'
        label = escape(d.label) + (f" ({escape(d.scope)})" if d.scope else "")
        data.append([
            Paragraph(str(d.no), styles["cell"]),
            Paragraph(label, styles["cell"]),
            Paragraph(said, styles["cell"]),
            Paragraph(assessment, styles["cell"]),
        ])
    return _table(data, _DECISION_COLS)


# --------------------------------------------------------------------------- #
# 2. All variables
# --------------------------------------------------------------------------- #


def _variables(model: ReportModel, styles: dict) -> list:  # type: ignore[type-arg]
    """One table for every variable, a shaded band naming each group.

    One table rather than one per group: nine repeated header rows and headings
    cost most of a page, and the header still repeats at the top of each page.

    The band carries the group's one-line explanation from `plain` in
    config/fields.yaml, where one is written. That is the only place the report
    explains a whole group of variables, and it costs one line per group.
    """
    head = ["Variable", "Value", "Status", "Confidence", "Where"]
    data: list[list[object]] = [[Paragraph(h, styles["cell_head"]) for h in head]]
    bands: list[int] = []
    for section in model.groups:
        if not section.rows:
            continue
        bands.append(len(data))
        band_text = f"<b>{escape(section.group.label)}</b>"
        if section.group.plain:
            band_text += (
                f' <font size="7.5" color="#555555">— {escape(section.group.plain.strip())}</font>'
            )
        band = Paragraph(band_text, styles["cell"])
        data.append([band, "", "", "", ""])
        prev = None
        for r in section.rows:
            data.append(_row_cells(r, styles, scope_only=r.field_id == prev))
            prev = r.field_id
    table = _table(data, _MAIN_COLS)
    extra = []
    for i in bands:
        extra += [
            ("SPAN", (0, i), (-1, i)),
            ("BACKGROUND", (0, i), (-1, i), _BAND_BG),
            ("TOPPADDING", (0, i), (-1, i), 4),
        ]
    table.setStyle(TableStyle(extra))
    return [Paragraph("All variables", styles["section"]), table]


def _schedules(model: ReportModel, styles: dict) -> list:  # type: ignore[type-arg]
    schedules = [s for g in model.groups for s in g.schedules]
    if not schedules:
        return []
    story: list[object] = [Paragraph("Schedules", styles["section"])]
    for s in schedules:
        title = escape(s.label)
        if s.budget_driver:
            title += ' <font size="7" color="#1565c0">BUDGET</font>'
        story.append(Paragraph(title, styles["group"]))
        head = ["Applies to", "Value", "Status", "Confidence", "Where"]
        data = [[Paragraph(h, styles["cell_head"]) for h in head]]
        data += [_row_cells(r, styles, scope_only=True) for r in s.rows]
        story.append(_table(data, _SCHEDULE_COLS))
    return story


def _row_cells(r: Row, styles: dict, *, scope_only: bool) -> list[object]:  # type: ignore[type-arg]
    return [
        Paragraph(_first_cell(r, scope_only), styles["cell"]),
        Paragraph(_value_cell(r), styles["cell"]),
        Paragraph(_status_cell(r), styles["cell"]),
        Paragraph(_confidence_cell(r), styles["cell"]),
        Paragraph(_where_cell(r), styles["cell_small"]),
    ]


def _first_cell(r: Row, scope_only: bool) -> str:
    # scope_only: a schedule row, or a further row of the variable just above it.
    if scope_only:
        return f'<font color="#555555">{escape(r.scope or "Part not stated")}</font>'
    text = escape(r.label)
    if r.budget_driver:
        text += ' <font size="6.5" color="#1565c0">BUDGET</font>'
    if r.scope:
        text += f'<br/><font size="7.5" color="#555555">{escape(r.scope)}</font>'
    return text


def _value_cell(r: Row) -> str:
    multi = len(r.values) > 1
    parts = []
    for v in r.values:
        if len(v.items) > 1 and any(len(i) > 40 for i in v.items):
            text = "<br/>".join(f"• {escape(i)}" for i in v.items)
        elif len(v.items) > 1:
            # Short items — enum lists such as the evidence of visit intensity —
            # read fine as one line, and a bullet each took up to eleven lines.
            text = escape(", ".join(v.items))
        else:
            text = escape(v.items[0])
        if multi and v.status != r.status:
            word, color = _STATUS_WORDS.get(v.status, (v.status, _GREY))
            text += f' <font size="7" color="{color.hexval()}">({word.lower()})</font>'
        parts.append(text)
    if multi:
        # Each value carries its own pages, since they differ from value to value.
        parts = [
            p + (f' <font size="7" color="#757575">— {escape(v.sources)}</font>'
                 if v.sources else "")
            for p, v in zip(parts, r.values, strict=True)
        ]
    body = "<br/>".join(parts)
    if r.decision_no is not None:
        body += (
            f'<br/><font size="7" color="{_RED.hexval()}">Sources disagree — '
            f"see Disagreement {r.decision_no}</font>"
        )
    elif r.folded:
        n = len(r.folded)
        body += (
            f'<br/><font size="7" color="#757575">{n} other wording{"s" if n != 1 else ""} '
            "in the documents; checked, not a conflict. Each is quoted in Appendix A.</font>"
        )
    elif r.verdict == "not_a_conflict" and multi:
        body += (
            '<br/><font size="7" color="#757575">'
            "Worded differently; checked, not a conflict.</font>"
        )
    return body


def _status_cell(r: Row) -> str:
    if r.decision_no is not None:
        word, color = "Disagreement", _RED
    else:
        word, color = _STATUS_WORDS.get(r.status, (r.status, _GREY))
    return f'<font color="{color.hexval()}"><b>{escape(word)}</b></font>'


def _confidence_cell(r: Row) -> str:
    """The lowest confidence among the row's values, on every row that has one.

    The lowest rather than an average: merging entries must never make a row look
    more certain than its least certain value. Printed amber below
    CONFIDENCE_CONFIRMED, which is the figure GATE uses to decide between
    confirming a value and sending it for review, so the colour and the status
    word in the cell to its left can never tell different stories.
    """
    scored = [v.confidence for v in r.values if v.status != "not_found"]
    if r.is_pointer or not scored:
        return ""
    low = min(scored)
    color = _GREY if low >= CONFIDENCE_CONFIRMED else _AMBER
    return f'<font color="{color.hexval()}">{low:.0%}</font>'


# --------------------------------------------------------------------------- #
# 4. Flagged for review
# --------------------------------------------------------------------------- #


def _flagged(model: ReportModel, styles: dict) -> list:  # type: ignore[type-arg]
    """Every value marked Review that is not already one of the disagreements.

    The reason on each row is worked out in report_model._review_reasons by
    replaying the rules in rfp_intake/gate/__init__.py. Before 2026-09-30 this
    section printed variable names only, under the heading "Also check", with no
    value, no confidence and no reason — four different reasons for being flagged
    looked identical to a reader.
    """
    rows = model.review_rows()
    story: list[object] = [Paragraph("Flagged for review", styles["section"])]
    if not rows:
        story.append(Paragraph("No other values need review.", styles["body"]))
        return story
    story.append(Paragraph(
        "The documents do not contradict each other on these; each is here for the reason in the "
        "<b>Why it is flagged</b> column. A value stays flagged until a person confirms it — the "
        "tool never clears one by itself. What the reasons mean: "
        "<b>budget driver holding more than one value</b> — which of them belongs in a budget is a "
        "person's judgement, so the tool will not pick; "
        "<b>budget driver, the documents word it differently</b> — the reviewer judged the "
        "wordings not to disagree, and a person still confirms that; "
        "<b>confidence below "
        f"{CONFIDENCE_CONFIRMED:.0%}</b> — read the quoted passage in Appendix A before using it.",
        styles["body"],
    ))
    story.append(Spacer(1, 3))
    head = ["Variable", "Value", "Confidence", "Why it is flagged", "Where"]
    data: list[list[object]] = [[Paragraph(h, styles["cell_head"]) for h in head]]
    for r in rows:
        label = escape(r.label)
        if r.budget_driver:
            label += ' <font size="6.5" color="#1565c0">BUDGET</font>'
        if r.scope:
            label += f'<br/><font size="7.5" color="#555555">{escape(r.scope)}</font>'
        value = _clip("; ".join(v.text for v in r.values), DECISION_VALUE_CHARS)
        why = "<br/>".join(
            ("• " if len(r.reasons) > 1 else "") + escape(reason) for reason in r.reasons
        )
        data.append([
            Paragraph(label, styles["cell"]),
            Paragraph(escape(value), styles["cell"]),
            Paragraph(_confidence_cell(r), styles["cell"]),
            Paragraph(why, styles["cell"]),
            Paragraph(_where_cell(r) or escape(r.values[0].sources), styles["cell_small"]),
        ])
    story.append(_table(data, _FLAGGED_COLS))
    return story


# --------------------------------------------------------------------------- #
# Words used in this report (last, behind both appendices)
# --------------------------------------------------------------------------- #


def _glossary(model: ReportModel, styles: dict) -> list:  # type: ignore[type-arg]
    """The plain-English wording from config/fields.yaml, for the jargon only.

    Omitted entirely when no field sets `plain`, so a registry that explains
    nothing produces no empty heading.
    """
    if not model.glossary:
        return []
    data: list[list[object]] = [[
        Paragraph(h, styles["cell_head"]) for h in ("Term", "What it means")
    ]]
    for term in model.glossary:
        data.append([
            Paragraph(escape(term.label), styles["cell"]),
            Paragraph(escape(term.plain.strip()), styles["cell_small"]),
        ])
    return [
        Paragraph("Words used in this report", styles["section"]),
        Paragraph(
            "Written for a reader without a clinical background. The wording lives in "
            "config/fields.yaml and can be corrected there.",
            styles["meta"],
        ),
        Spacer(1, 3),
        _table(data, _GLOSSARY_COLS),
    ]


def _where_cell(r: Row) -> str:
    # With several values, each carries its own pages inline in the value cell.
    if len(r.values) > 1:
        return ""
    return escape(r.values[0].sources)


def _table(data: list, widths: list[float]) -> Table:  # type: ignore[type-arg]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, _RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 1.8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
    ]))
    return t


# --------------------------------------------------------------------------- #
# Appendices
# --------------------------------------------------------------------------- #


def _evidence_appendix(model: ReportModel, styles: dict) -> list:  # type: ignore[type-arg]
    story: list[object] = [
        Paragraph("Appendix A — Evidence", styles["section"]),
        Paragraph(
            "The passage each value was read from, in the order of the tables above.",
            styles["meta"],
        ),
    ]
    if not model.evidence:
        story.append(Paragraph("No values were extracted.", styles["body"]))
        return story
    current = None
    for e in model.evidence:
        if e.label != current:
            current = e.label
            story.append(Paragraph(f"<b>{escape(e.label)}</b>", styles["evidence"]))
        head = escape(e.value)
        if e.scope:
            head = f"[{escape(e.scope)}] " + head
        line = f"• {head} <font color=\"#757575\">— {escape(e.sources)}, {e.confidence:.0%}</font>"
        if e.quote:
            line += f': <i>"{escape(e.quote)}"</i>'
        if e.derived_from:
            line += f" <font color=\"#757575\">Derived from {escape(', '.join(e.derived_from))}"
            line += f"; {escape(e.notes)}</font>" if e.notes else "</font>"
        story.append(Paragraph(line, styles["evidence"]))
    return story


def _reasoning_appendix(model: ReportModel, styles: dict) -> list:  # type: ignore[type-arg]
    if not model.decisions and not model.dismissed:
        return []
    story: list[object] = [Paragraph("Appendix B — Disagreements", styles["section"])]
    for d in model.decisions:
        story.append(Paragraph(f"<b>{d.no}. {escape(d.label)}</b>", styles["body"]))
        for p in d.positions:
            story.append(Paragraph(
                f"• {escape(p.text)} <font color=\"#757575\">— {escape(p.sources)}</font>",
                styles["evidence"],
            ))
        if d.explanation:
            story.append(Paragraph(escape(d.explanation), styles["evidence"]))
        story.append(Spacer(1, 3))
    if model.dismissed:
        story.append(Paragraph(
            f"<b>Checked and dismissed ({len(model.dismissed)})</b> — the documents word these "
            "differently but do not disagree. Full reasoning is in extraction.json.",
            styles["body"],
        ))
        for x in model.dismissed:
            label = escape(x.label) + (f" ({escape(x.scope)})" if x.scope else "")
            said = " / ".join(
                f'{escape(p.text)} <font color="#757575">({escape(p.sources)})</font>'
                for p in x.values
            )
            story.append(Paragraph(f"• {label}: {said}", styles["evidence"]))
    return story


def _removed_appendix(model: ReportModel, styles: dict) -> list:  # type: ignore[type-arg]
    """Appendix C — the passages MARK_OTHER_STUDY judged to be about another study.

    Returns [] when nothing was removed, so a report for a run with no removals is
    exactly the report it was before stage 4 of docs/PLAN_2026-10-02.md. Written as
    a list rather than a table, like Appendix A and Appendix B, because the reason
    and the extract are sentences and a five-column table would wrap both of them
    into columns an inch wide.
    """
    if not model.removed:
        return []
    story: list[object] = [
        Paragraph("Appendix C — Text read as describing a different study", styles["section"]),
        Paragraph(
            "A protocol often describes earlier studies of the same drug. A number taken "
            "from one of those is a wrong number, so these passages were not read when the "
            "variables above were extracted. Each says where it is in the documents, so it "
            "can be checked. The full text of each is in extraction.json.",
            styles["meta"],
        ),
    ]
    for r in model.removed:
        story.append(Paragraph(
            f"<b>{escape(r.heading)}</b> "
            f"<font color=\"#757575\">— {escape(r.doc_code)} {escape(r.pages)}, "
            f"{escape(r.verdict)}</font>",
            styles["evidence"],
        ))
        if r.reason:
            story.append(Paragraph(f"• {escape(r.reason)}", styles["evidence"]))
        if r.extract:
            story.append(Paragraph(f'<i>"{escape(r.extract)}"</i>', styles["evidence"]))
        story.append(Spacer(1, 3))
    return story
