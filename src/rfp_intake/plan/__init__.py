"""PLAN — choose which sections each extraction call reads. Pure Python, no LLM.

Rewritten on 2026-10-02 (stage 2 of `docs/PLAN_2026-10-02.md`). PLAN used to score
the PDF's bookmark entries and choose whole pages, widened by one page either
side, with "the first 5 pages" when nothing scored. All three are gone:

- It scores the sections FIND_SECTIONS produced, using the same `search_hints`
  and the same weights in `plan/scoring.py`.
- There is no page margin. A section boundary is a (page, character offset) pair,
  so there is nothing to widen; the margin is how text from an unchosen section
  reached the extraction model and produced the phase of a different study.
- There is no first-5-pages fallback. Every document has at least one section.

The number of extraction calls per (document, group) is unchanged: one, unless
the chosen sections do not fit in one call.
"""

from __future__ import annotations

from typing import Any

import structlog

from rfp_intake.domain.budget import DEFAULT_TOKEN_BUDGET, estimate_text_tokens
from rfp_intake.domain.registry import Registry, get_registry
from rfp_intake.domain.schemas import Document, ExtractionTask, RunState, Section
from rfp_intake.plan.scoring import score_section, select_sections
from rfp_intake.sections import find_sections, section_page_texts, section_text

logger = structlog.get_logger()

DEFAULT_TOP_K = 3

# DEFAULT_TOKEN_BUDGET is re-exported from domain/budget.py, where it lives so
# that rfp_intake.sections can read it without importing this module — this
# module imports rfp_intake.sections.
__all__ = ["DEFAULT_TOKEN_BUDGET", "DEFAULT_TOP_K", "plan_extraction", "plan_node"]


def plan_extraction(
    docs: list[Document],
    registry: Registry | None = None,
) -> list[ExtractionTask]:
    """Generate extraction tasks for all (document, field group) combinations.

    For each document and each field group:
    1. Score the document's sections by the group's `search_hints`.
    2. Keep the top `DEFAULT_TOP_K` sections that scored above zero.
    3. If none scored, send the whole document when it fits one call, otherwise
       the first `DEFAULT_TOP_K` sections.
    4. Pack the chosen sections into as few tasks as the token budget allows.
    """
    if registry is None:
        registry = get_registry()

    tasks: list[ExtractionTask] = []

    for doc in docs:
        sections = _sections_for(doc)
        # One text per section, reused by every group, because a 176-section
        # protocol scored against 9 groups would otherwise be rebuilt 9 times.
        texts = {section.id: section_text(doc, section) for section in sections}
        for group_def in registry.groups:
            tasks.extend(_plan_group(doc, sections, texts, group_def.id, registry))

    logger.info(
        "plan_complete",
        total_tasks=len(tasks),
        docs=len(docs),
        groups=len(registry.groups),
    )

    return tasks


def _sections_for(doc: Document) -> list[Section]:
    """The document's sections, computing them if FIND_SECTIONS did not run.

    FIND_SECTIONS fills `Document.sections` in the graph, so the fallback is for
    a caller that builds a `Document` by hand. It is the same code FIND_SECTIONS
    runs, so it cannot disagree with it.

    **The computed sections are written back onto the document**, because the
    tasks PLAN returns name sections by id and EXTRACT looks those ids up on the
    document. Computing them and keeping them local would hand EXTRACT tasks
    naming sections the document does not have, and every excerpt would come back
    empty.
    """
    if doc.sections:
        return list(doc.sections)
    sections, source = find_sections(doc)
    doc.sections = sections
    doc.section_source = source
    logger.info("plan_sectioned_document_itself", doc_id=doc.id, source=source)
    return sections


def _plan_group(
    doc: Document,
    sections: list[Section],
    texts: dict[str, str],
    group_id: str,
    registry: Registry,
) -> list[ExtractionTask]:
    """Plan the extraction tasks for one (document, field group) pair."""
    hints = registry.get_group(group_id).search_hints

    scores = [score_section(section, hints, texts[section.id]) for section in sections]
    chosen = select_sections(sections, scores, k=DEFAULT_TOP_K)

    if not chosen:
        chosen = _nothing_scored(doc, sections, texts, group_id)

    return _tasks_for(doc, chosen, texts, group_id)


def _nothing_scored(
    doc: Document,
    sections: list[Section],
    texts: dict[str, str],
    group_id: str,
) -> list[Section]:
    """Which sections to read when no section matched the group's hints.

    The whole document if it fits in one extraction call — which is the case for
    a document FIND_SECTIONS gave a single section, and is what stage 2 of
    `docs/PLAN_2026-10-02.md` asks for. Otherwise the first `DEFAULT_TOP_K`
    sections, because sending every section of a 137-page protocol to every field
    group would be a fan-out that queues against our own endpoint.
    """
    total = sum(estimate_text_tokens(texts[section.id]) for section in sections)
    if total <= DEFAULT_TOKEN_BUDGET:
        return sections

    logger.warning(
        "plan_no_section_scored",
        doc_id=doc.id,
        group=group_id,
        sections=len(sections),
        estimated_tokens=total,
        using_first=DEFAULT_TOP_K,
    )
    return sections[:DEFAULT_TOP_K]


def _tasks_for(
    doc: Document,
    chosen: list[Section],
    texts: dict[str, str],
    group_id: str,
) -> list[ExtractionTask]:
    """Pack the chosen sections into tasks that each fit one extraction call.

    Sections are filled in document order until the next one would not fit. A
    single section bigger than the budget becomes several tasks that name that
    same section and differ in `page_window`, so the excerpt is the part of the
    section inside each window.
    """
    tasks: list[ExtractionTask] = []
    batch: list[Section] = []
    batch_tokens = 0

    for section in chosen:
        tokens = estimate_text_tokens(texts[section.id])

        if tokens > DEFAULT_TOKEN_BUDGET:
            if batch:
                tasks.append(_task(doc, batch, batch_tokens, group_id))
                batch, batch_tokens = [], 0
            tasks.extend(_split_one_section(doc, section, group_id))
            continue

        if batch and batch_tokens + tokens > DEFAULT_TOKEN_BUDGET:
            tasks.append(_task(doc, batch, batch_tokens, group_id))
            batch, batch_tokens = [], 0

        batch.append(section)
        batch_tokens += tokens

    if batch:
        tasks.append(_task(doc, batch, batch_tokens, group_id))

    return tasks


def _split_one_section(
    doc: Document,
    section: Section,
    group_id: str,
) -> list[ExtractionTask]:
    """Split one over-long section into tasks by page, all naming that section.

    A page is the smallest unit here, so a single page whose own text exceeds the
    budget is sent whole and the budget is exceeded. That was also true of the
    page splitting PLAN did before 2026-10-02; it is now logged rather than
    silent. Splitting inside a page would need a character offset on
    `ExtractionTask`, and no real document has hit it: the largest page of
    `samples/Example protocol 2.pdf` is far under the budget.
    """
    pages = section_page_texts(doc, section)
    windows: list[tuple[int, int]] = []
    start = section.page_start
    tokens = 0

    for page in range(section.page_start, section.page_end + 1):
        page_tokens = estimate_text_tokens(pages.get(page, ""))
        if page_tokens > DEFAULT_TOKEN_BUDGET:
            logger.warning(
                "plan_page_over_budget",
                doc_id=doc.id,
                group=group_id,
                section_id=section.id,
                page=page,
                estimated_tokens=page_tokens,
                budget=DEFAULT_TOKEN_BUDGET,
            )
        if tokens + page_tokens > DEFAULT_TOKEN_BUDGET and page > start:
            windows.append((start, page - 1))
            start, tokens = page, page_tokens
        else:
            tokens += page_tokens
    windows.append((start, section.page_end))

    logger.info(
        "plan_split_section",
        doc_id=doc.id,
        group=group_id,
        section_id=section.id,
        tasks=len(windows),
    )
    return [
        ExtractionTask(
            doc_id=doc.id,
            group=group_id,
            page_window=window,
            section_ids=[section.id],
            budget_tokens=sum(
                estimate_text_tokens(pages.get(page, ""))
                for page in range(window[0], window[1] + 1)
            ),
        )
        for window in windows
    ]


def _task(
    doc: Document,
    sections: list[Section],
    tokens: int,
    group_id: str,
) -> ExtractionTask:
    return ExtractionTask(
        doc_id=doc.id,
        group=group_id,
        page_window=(
            min(section.page_start for section in sections),
            max(section.page_end for section in sections),
        ),
        section_ids=[section.id for section in sections],
        budget_tokens=tokens,
    )


def plan_node(state: RunState) -> dict[str, Any]:
    """PLAN graph node — generate extraction tasks. Pure Python."""
    registry = get_registry()
    tasks = plan_extraction(state.documents, registry)

    logger.info(
        "plan_node_complete",
        run_id=state.run_id,
        tasks_generated=len(tasks),
        documents=len(state.documents),
    )

    return {"tasks": tasks}
