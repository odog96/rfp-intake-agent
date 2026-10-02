"""Rebuild report.pdf from a finished run's extraction.json, with no model calls.

Layout work on render/pdf_renderer.py needs a real run to look at — the synthetic
pair produces 100-odd values, 12 disagreements and two schedule tables, and no
hand-built state exercises all of that at once. Re-running the pipeline to see a
layout change would cost twelve minutes and a set of Bedrock calls, so this
script reads the values the pipeline already wrote and renders them again.

    python scripts/rerender_report.py r-20260923-131601
    python scripts/rerender_report.py r-20260923-131601 --pages

It writes runs/<run_id>/report-rerender.pdf and never touches report.pdf, which
is the artefact the Cloudera AI Application offers for download and the record of
what that run actually produced.

Two things it does not reproduce:
- `extraction.json` does not record which file each doc_id came from, so a
  re-rendered report names documents by kind ("Protocol", "RFP") rather than by
  filename. report_model.py:_Docs says the same. Nothing else differs.
- Only resolved values and contradictions are read back. The graph's other state
  (excerpts, the extraction plan) is not in extraction.json and the report does
  not use it.

`generated_at` is taken from the saved extraction.json rather than stamped with
today's date, so re-rendering the same run twice produces the same bytes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rfp_intake.domain.registry import get_registry  # noqa: E402
from rfp_intake.domain.schemas import (  # noqa: E402
    Contradiction,
    RemovedPassage,
    ResolvedField,
    RunState,
)
from rfp_intake.render import build_report_pdf  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_run_state(run_path: Path) -> tuple[RunState, str]:
    """A RunState carrying everything report.pdf reads, plus the run's timestamp."""
    doc = json.loads((run_path / "extraction.json").read_text())
    state = RunState(
        run_id=doc["run_id"],
        resolved=[ResolvedField.model_validate(r) for r in doc["resolved_fields"]],
        contradictions=[Contradiction.model_validate(c) for c in doc["contradictions"]],
        # .get, because a run from before 2026-10-02 has no such key and this
        # script's whole point is re-rendering an old run. Appendix C is then
        # absent, which is correct: nothing was removed, because the node that
        # removes did not exist.
        removed_passages=[
            RemovedPassage.model_validate(r) for r in doc.get("removed_passages", [])
        ],
    )
    return state, str(doc.get("generated_at", "unknown"))


def describe(pdf_bytes: bytes) -> str:
    """Page count, where Appendix A starts, and the words before it.

    The measurement the restructuring is judged against: the part a reader is
    expected to read straight through is everything before Appendix A.
    """
    try:
        import pymupdf
    except ImportError:
        return "pymupdf not installed — page counts not measured"

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:  # type: ignore[no-untyped-call]
        pages = [page.get_text() for page in pdf]
    # The heading, not a mention of it: the header block's how-to-read lines name
    # Appendix A on page 1, which would otherwise be measured as the appendix.
    appendix = next(
        (
            i for i, text in enumerate(pages)
            if any(line.startswith("Appendix A —") for line in text.splitlines())
        ),
        len(pages),
    )
    words = sum(len(text.split()) for text in pages[:appendix])
    return (
        f"{len(pages)} pages total · Appendix A starts on page {appendix + 1} · "
        f"{words:,} words before it"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", help="a folder name under runs/")
    parser.add_argument(
        "--pages", action="store_true", help="also measure the rendered PDF with pymupdf"
    )
    args = parser.parse_args()

    run_path = PROJECT_ROOT / "runs" / args.run_id
    if not (run_path / "extraction.json").is_file():
        print(f"No extraction.json in {run_path}", file=sys.stderr)
        return 1

    state, generated_at = load_run_state(run_path)
    pdf_bytes = build_report_pdf(state, get_registry(), generated_at=generated_at)

    out = run_path / "report-rerender.pdf"
    out.write_bytes(pdf_bytes)
    print(f"{out} — {len(pdf_bytes):,} bytes")
    print(
        f"  {len(state.resolved)} resolved values, {len(state.contradictions)} contradictions, "
        f"{sum(1 for rf in state.resolved if rf.status == 'needs_review')} needs_review"
    )
    if args.pages:
        print(f"  {describe(pdf_bytes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
