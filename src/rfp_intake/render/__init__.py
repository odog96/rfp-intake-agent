"""RENDER — pure functions turning RunState into extraction.json, report.pdf,
and report.xlsx. See ARCHITECTURE.md §4.10.

Not a LangGraph node: the graph's RunState carries no filesystem path (only
run_id), and this codebase's established pattern (job/output.py) already
treats file-writing as job-level orchestration, not graph-node logic. These
renderers are the pure half of that split; job/output.py is the thin I/O
wrapper that calls them and writes the results into the run directory.
"""

from rfp_intake.render.json_renderer import build_extraction_document
from rfp_intake.render.pdf_renderer import build_report_pdf
from rfp_intake.render.xlsx_renderer import build_report_xlsx

__all__ = [
    "build_extraction_document",
    "build_report_pdf",
    "build_report_xlsx",
]
