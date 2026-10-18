"""report.xlsx — spreadsheet renderer alongside the primary JSON/PDF outputs.
Pure function returning bytes; no filesystem access. See ARCHITECTURE.md §4.10.

One row per field: Group | Variable | Value | Status | Confidence |
Source Doc | Page | Quote | Contradiction, conditional-formatted
red/amber/green/grey, plus a Review Queue sheet (non-confirmed rows,
budget drivers first), and a Schedule of Events sheet with the visit table
report.pdf prints (Prompt C).
"""

from __future__ import annotations

import io
import json
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from rfp_intake.domain.registry import FieldDef, Registry, ReportWording
from rfp_intake.domain.schemas import ResolvedField, RunState
from rfp_intake.render.report_model import VisitSchedule, visit_schedule

_HEADERS = [
    "Group", "Variable", "Value", "Status", "Confidence",
    "Source Doc", "Page", "Quote", "Contradiction",
]

_FILL_CONFIRMED = PatternFill("solid", fgColor="C8E6C9")   # green
_FILL_NEEDS_REVIEW = PatternFill("solid", fgColor="FFE0B2")  # amber
_FILL_CONFLICT = PatternFill("solid", fgColor="FFCDD2")   # red
_FILL_NOT_FOUND = PatternFill("solid", fgColor="E0E0E0")   # grey

_COLUMN_WIDTHS = [20, 32, 24, 14, 12, 20, 6, 50, 40]
# Period | Visit timing | Visits | Assessments | Key considerations | Where
_SCHEDULE_WIDTHS = [30, 34, 18, 14, 44, 40]


def _row_fill(rf: ResolvedField) -> PatternFill:
    if rf.contradiction is not None and rf.contradiction.verdict == "conflict":
        return _FILL_CONFLICT
    if rf.status == "confirmed":
        return _FILL_CONFIRMED
    if rf.status == "needs_review":
        return _FILL_NEEDS_REVIEW
    return _FILL_NOT_FOUND  # not_found, not_specified, not_applicable


def _excel_safe(value: Any) -> Any:
    """Coerce a resolved value into something openpyxl can write.

    Normalised values are not always scalars — a quantity arrives as
    {"n": 28, "unit": "days"} and a list-valued field as a list. openpyxl raises
    on those, which previously failed the whole job at RENDER after every LLM
    call had already been paid for. Scalars pass through untouched so numbers
    stay numbers and remain sortable in the sheet.
    """
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        # The common shape is a magnitude plus a unit; render it readably.
        if "n" in value and "unit" in value:
            return f"{value['n']} {value['unit']}"
        return json.dumps(value, default=str)
    if isinstance(value, (list, tuple)):
        return "; ".join(str(_excel_safe(v)) for v in value)
    return str(value)


def _row_values(
    rf: ResolvedField, field_def: FieldDef | None, wording: ReportWording
) -> list[Any]:
    label = field_def.label if field_def else rf.field_id
    group = field_def.group if field_def else ""
    source = rf.sources[0] if rf.sources else None
    contradiction_text = ""
    if rf.contradiction is not None:
        verdict = rf.contradiction.verdict or "pending"
        explanation = rf.contradiction.explanation or ""
        contradiction_text = f"{verdict}: {explanation}"

    return [
        group,
        f"{label} [{rf.scope}]" if rf.scope else label,
        # A question a rule says does not arise reads as the PDF reads it ("N/A").
        wording.not_applicable if rf.status == "not_applicable" else _excel_safe(rf.value),
        rf.status,
        round(rf.confidence, 2),
        source.doc_id if source else "",
        source.page if source else "",
        rf.quote or "",
        contradiction_text,
    ]


def _write_sheet(
    ws: Worksheet,
    rows: list[tuple[ResolvedField, FieldDef | None]],
    wording: ReportWording,
) -> None:
    ws.append(_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for rf, field_def in rows:
        ws.append(_row_values(rf, field_def, wording))
        fill = _row_fill(rf)
        for cell in ws[ws.max_row]:
            cell.fill = fill

    for i, width in enumerate(_COLUMN_WIDTHS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    ws.freeze_panes = "A2"


def build_report_xlsx(state: RunState, registry: Registry) -> bytes:
    """Render the XLSX report as an in-memory workbook, returned as bytes."""
    field_by_id = {f.id: f for f in registry.fields}
    budget_drivers = {f.id for f in registry.fields if f.budget_driver}

    all_rows = [(rf, field_by_id.get(rf.field_id)) for rf in state.resolved]

    wb = Workbook()
    fields_ws = wb.active
    fields_ws.title = "Fields"
    _write_sheet(fields_ws, all_rows, registry.report_wording)

    review_rows = sorted(
        # A question a rule says does not arise is settled, not waiting for review.
        (row for row in all_rows if row[0].status not in ("confirmed", "not_applicable")),
        key=lambda row: row[0].field_id not in budget_drivers,  # False (budget driver) sorts first
    )
    review_ws = wb.create_sheet("Review Queue")
    _write_sheet(review_ws, review_rows, registry.report_wording)

    schedule = visit_schedule(state, registry)
    if schedule is not None:
        totals = next((f for f in registry.fields if f.from_schedule == "stage_totals"), None)
        _write_schedule_sheet(
            wb.create_sheet("Schedule of Events"), schedule, totals.label if totals else None,
        )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _write_schedule_sheet(ws: Worksheet, schedule: VisitSchedule, totals_label: str | None) -> None:
    """The counted Schedule of Events in report.pdf's words: each table under its
    heading and pages, the lines report.pdf prints under it, then the stage totals
    DERIVE wrote. The counts are text, as printed ("7 each")."""
    if schedule.missing:
        ws.append([schedule.missing])
    for t in schedule.tables:
        ws.append([t.heading, t.sources])
        ws[ws.max_row][0].font = Font(bold=True)
        ws.append(t.head)
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)
        for cells in t.rows:
            ws.append(cells)
        for line in t.lines:
            ws.append([line])
        ws.append([])
    for line in schedule.lines:
        ws.append([line])
    if schedule.totals:
        ws.append([])
        ws.append([totals_label or ""])
        ws[ws.max_row][0].font = Font(bold=True)
        for line in schedule.totals:
            ws.append([line])
    for i, width in enumerate(_SCHEDULE_WIDTHS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
