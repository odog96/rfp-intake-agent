"""PLAN — page targeting for extraction tasks. Pure Python, no LLM."""

from __future__ import annotations

from typing import Any

import structlog

from rfp_intake.domain.registry import Registry, get_registry
from rfp_intake.domain.schemas import Document, ExtractionTask, RunState
from rfp_intake.plan.scoring import (
    estimate_tokens,
    merge_windows,
    score_section,
    select_windows,
)

logger = structlog.get_logger()

DEFAULT_TOP_K = 3
DEFAULT_MARGIN = 1
DEFAULT_TOKEN_BUDGET = 4000
FALLBACK_PAGES = 5


def plan_extraction(
    docs: list[Document],
    registry: Registry | None = None,
) -> list[ExtractionTask]:
    """Generate extraction tasks for all (doc, group) combinations.

    For each document and each non-derived field group:
    1. Score outline sections by search_hints
    2. Select top-k + margin pages
    3. Fallback to first N pages if no good matches
    4. Split by token budget if needed
    """
    if registry is None:
        registry = get_registry()

    tasks: list[ExtractionTask] = []

    for doc in docs:
        for group_def in registry.groups:
            group_tasks = _plan_group(doc, group_def.id, registry)
            tasks.extend(group_tasks)

    logger.info(
        "plan_complete",
        total_tasks=len(tasks),
        docs=len(docs),
        groups=len(registry.groups),
    )

    return tasks


def _plan_group(
    doc: Document,
    group_id: str,
    registry: Registry,
) -> list[ExtractionTask]:
    """Plan extraction tasks for one (doc, group) pair."""
    group_def = registry.get_group(group_id)
    hints = group_def.search_hints

    # Score outline sections
    if doc.outline:
        scores = [
            score_section(entry, hints, doc.page_texts)
            for entry in doc.outline
        ]

        windows = select_windows(
            doc.outline, scores,
            k=DEFAULT_TOP_K,
            margin=DEFAULT_MARGIN,
            max_page=doc.pages,
        )
    else:
        windows = []

    # Fallback: if no scored windows, use first N pages
    if not windows:
        end_page = min(FALLBACK_PAGES, doc.pages) if doc.pages > 0 else FALLBACK_PAGES
        windows = [(1, end_page)]

    merged = merge_windows(windows)

    # Split windows that exceed token budget
    tasks: list[ExtractionTask] = []
    for window in merged:
        tokens = estimate_tokens(doc.page_texts, window)
        if tokens > DEFAULT_TOKEN_BUDGET:
            sub_windows = _split_window(window, doc.page_texts, DEFAULT_TOKEN_BUDGET)
            for sw in sub_windows:
                tasks.append(ExtractionTask(
                    doc_id=doc.id,
                    group=group_id,
                    page_window=sw,
                    budget_tokens=estimate_tokens(doc.page_texts, sw),
                ))
        else:
            tasks.append(ExtractionTask(
                doc_id=doc.id,
                group=group_id,
                page_window=window,
                budget_tokens=tokens,
            ))

    return tasks


def _split_window(
    window: tuple[int, int],
    page_texts: dict[int, str],
    budget: int,
) -> list[tuple[int, int]]:
    """Split a window into sub-windows that fit within the token budget."""
    start, end = window
    sub_windows: list[tuple[int, int]] = []
    current_start = start
    current_tokens = 0

    for p in range(start, end + 1):
        page_tokens = len(page_texts.get(p, "")) // 4
        if current_tokens + page_tokens > budget and p > current_start:
            sub_windows.append((current_start, p - 1))
            current_start = p
            current_tokens = page_tokens
        else:
            current_tokens += page_tokens

    sub_windows.append((current_start, end))
    return sub_windows


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
