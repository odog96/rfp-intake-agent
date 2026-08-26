"""Quality gate for parsed documents — decides whether to escalate."""

from __future__ import annotations

from pathlib import Path

import fitz

from rfp_intake.ingest.models import QualityMetrics

MIN_AVG_CHARS_PER_PAGE = 500
MIN_ALPHA_RATIO = 0.65
SCAN_SAMPLE_PAGES = 3
SCAN_MIN_CHARS = 100


class QualityGate:
    """Evaluate parse quality against thresholds."""

    def __init__(
        self,
        min_chars_per_page: float = MIN_AVG_CHARS_PER_PAGE,
        min_alpha_ratio: float = MIN_ALPHA_RATIO,
    ):
        self.min_chars_per_page = min_chars_per_page
        self.min_alpha_ratio = min_alpha_ratio

    def check(self, metrics: QualityMetrics) -> tuple[bool, str | None]:
        """Returns (passed, reason_if_failed)."""
        if metrics.total_pages == 0:
            return False, "no_pages"

        if metrics.suspected_scanned:
            return False, "suspected_scanned"

        if metrics.avg_chars_per_page < self.min_chars_per_page:
            return False, f"low_chars_per_page:{metrics.avg_chars_per_page:.0f}"

        if metrics.alpha_ratio < self.min_alpha_ratio:
            return False, f"low_alpha_ratio:{metrics.alpha_ratio:.2f}"

        return True, None


def detect_scanned(path: Path) -> bool:
    """Sample first pages with PyMuPDF to detect scanned (no text layer) PDFs."""
    try:
        doc = fitz.open(str(path))
    except Exception:
        return False

    try:
        pages_to_check = min(SCAN_SAMPLE_PAGES, len(doc))
        pages_with_text = 0
        for i in range(pages_to_check):
            text = doc[i].get_text()
            if len(text.strip()) >= SCAN_MIN_CHARS:
                pages_with_text += 1
        return pages_with_text == 0
    finally:
        doc.close()
