"""Print the text of one run's report.pdf, page by page.

    python scripts/report_text.py r-20261012-angus2
    python scripts/report_text.py r-20261012-angus2 --pages 4-6

The text is what PyMuPDF reads off each page, in its reading order, under a
"===== page N =====" line. It is for checking by eye what a reader of the report
sees on a given page; scripts/check_review_2026_10_07.py measures the tables.

Reads only. Writes nothing, and makes no model calls.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _page_range(spec: str, last: int) -> range:
    """ "4" is page 4, "4-6" pages 4 to 6, "4-" page 4 to the end, "" every page."""
    first, dash, end = spec.partition("-")
    return range(int(first or 1), int(end or (last if dash or not first else first)) + 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id")
    parser.add_argument("--pages", default="", help="one page (4) or a range (4-6); default all")
    args = parser.parse_args()

    path = PROJECT_ROOT / "runs" / args.run_id / "report.pdf"
    if not path.exists():
        print(f"no report.pdf at {path}", file=sys.stderr)
        return 2
    doc = pymupdf.open(path)  # type: ignore[no-untyped-call]
    for number in _page_range(args.pages, doc.page_count):
        if 1 <= number <= doc.page_count:
            print(f"===== page {number} =====")
            print(doc[number - 1].get_text())  # type: ignore[no-untyped-call]
    return 0


if __name__ == "__main__":
    sys.exit(main())
