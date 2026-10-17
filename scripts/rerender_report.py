"""Rebuild report.pdf from a finished run's extraction.json, with no model calls.

Layout work on render/pdf_renderer.py needs a real run to look at — the synthetic
pair produces 100-odd values, 12 disagreements and two schedule tables, and no
hand-built state exercises all of that at once. Re-running the pipeline to see a
layout change would cost twelve minutes and a set of Bedrock calls, so this
script reads the values the pipeline already wrote and renders them again.

    python scripts/rerender_report.py r-20260923-131601
    python scripts/rerender_report.py r-20260923-131601 --pages
    python scripts/rerender_report.py r-20261011-fix1 --rederive

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

`--rederive` throws away the saved derived rows (every field `derived: true` in
config/fields.yaml) and computes them again with today's DERIVE, which is pure
Python and calls no model. It shows what a DERIVE change does to a run already
on disk — the visit-intensity fix of 2026-10-11 was checked this way. Without
it, the derived rows are the ones the run wrote.

The visit table (Prompt C) is read from extraction.json's `schedules`. A run
made before READ_SCHEDULE existed has no such key, so its schedules are read
again from the PDFs in runs/<run_id>/inputs/, in code, exactly as READ_SCHEDULE
would: the first-rung parser, FIND_SECTIONS, SET_ASIDE_SECTIONS, and the run's
own MARK_OTHER_STUDY removals from extraction.json. No model is called. Each
file is matched to the run's document id by the quotes the run cites from it.
Two limits follow. The sections come from today's config and today's
FIND_SECTIONS on rung 1's text, so a run whose PDFs needed rung 2 or 3, or whose
config has changed since, can get different sections from the live run's, and
the saved removals are matched to them by section id only. And two input files
can be given the same document id if the run's quotes are found in both; the
re-render does not check for that.

`generated_at` is taken from the saved extraction.json rather than stamped with
today's date, so re-rendering the same run twice produces the same bytes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rfp_intake.derive import derive_node  # noqa: E402
from rfp_intake.domain.registry import get_registry  # noqa: E402
from rfp_intake.domain.schemas import (  # noqa: E402
    Contradiction,
    Document,
    RemovedPassage,
    ResolvedField,
    RunState,
    Schedule,
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
    if "schedules" in doc:
        state.schedules = [Schedule.model_validate(s) for s in doc["schedules"]]
    else:
        state.schedules = read_schedules_from_inputs(run_path, state)
    return state, str(doc.get("generated_at", "unknown"))


def read_schedules_from_inputs(run_path: Path, state: RunState) -> list[Schedule]:
    """READ_SCHEDULE again, on the run's own input PDFs, for a run made before
    the node existed. Code only; see the module docstring."""
    from rfp_intake.domain.section_policy import get_sections_policy
    from rfp_intake.ingest.page_labels import read_page_labels
    from rfp_intake.ingest.parsers.rung1 import Rung1Parser
    from rfp_intake.schedule import read_schedules
    from rfp_intake.sections import find_sections
    from rfp_intake.sections.set_aside import set_aside_sections

    config = get_registry().schedule_table
    inputs = run_path / "inputs"
    if config is None or not inputs.is_dir():
        return []
    schedules: list[Schedule] = []
    for pdf in sorted(inputs.glob("*.pdf")):
        # The first rung only: it is in-process and downloads nothing.
        doc = Rung1Parser().parse(pdf).to_document("doc-unmatched")
        doc_id = _doc_id_for(doc, state)
        if doc_id is None:
            print(f"  {pdf.name}: no quote of this run is on its pages; schedule not read")
            continue
        doc = doc.model_copy(update={"id": doc_id})
        doc.page_labels = read_page_labels(pdf, doc.page_texts)
        doc.sections, doc.section_source = find_sections(doc)
        doc.sections, _set_aside = set_aside_sections(doc, get_sections_policy())
        doc.removed = [p for p in state.removed_passages if p.doc_id == doc_id]
        found = read_schedules(doc, config)
        print(f"  {pdf.name} is {doc_id}: {len(found)} schedule(s) read from the PDF")
        schedules.extend(found)
    return schedules


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _doc_id_for(doc: Document, state: RunState) -> str | None:
    """The run's document id whose cited quotes are on this file's pages."""
    pages = {n: _flat(t) for n, t in doc.page_texts.items()}
    votes: Counter[str] = Counter()
    for rf in state.resolved:
        # `source_quotes` is in the order of `sources` when RECONCILE filled it.
        quotes = rf.source_quotes if len(rf.source_quotes) == len(rf.sources) else []
        for i, src in enumerate(rf.sources):
            quote = quotes[i] if quotes else rf.quote
            if quote and _flat(quote) in pages.get(src.page, ""):
                votes[src.doc_id] += 1
    return votes.most_common(1)[0][0] if votes else None


def rederive(state: RunState) -> RunState:
    """The same state with its derived rows computed again by DERIVE."""
    derived = {f.id for f in get_registry().fields if f.derived}
    kept = [rf for rf in state.resolved if rf.field_id not in derived]
    result = derive_node(state.model_copy(update={"resolved": kept}))
    return state.model_copy(update={"resolved": result["resolved"]})


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
    parser.add_argument(
        "--rederive", action="store_true",
        help="compute the derived rows again with today's DERIVE (no model calls)",
    )
    args = parser.parse_args()

    run_path = PROJECT_ROOT / "runs" / args.run_id
    if not (run_path / "extraction.json").is_file():
        print(f"No extraction.json in {run_path}", file=sys.stderr)
        return 1

    state, generated_at = load_run_state(run_path)
    if args.rederive:
        state = rederive(state)
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
