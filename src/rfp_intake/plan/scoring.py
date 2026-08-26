"""Outline section scoring for page targeting."""

from __future__ import annotations

from rfp_intake.domain.registry import SearchHints
from rfp_intake.domain.schemas import OutlineEntry

# Scoring weights
HEADING_EXACT_MATCH = 5.0
HEADING_PARTIAL_MATCH = 3.0
KEYWORD_DENSITY_WEIGHT = 2.0
MAX_KEYWORD_DENSITY_SCORE = 4.0


def score_section(
    entry: OutlineEntry,
    hints: SearchHints,
    page_texts: dict[int, str],
) -> float:
    """Score an outline section against group search hints.

    Higher score = more likely to contain relevant content for the field group.
    """
    score = 0.0

    # Heading match scoring
    heading_lower = entry.heading.lower().strip()
    for hint_heading in hints.headings:
        hint_lower = hint_heading.lower().strip()
        if hint_lower == heading_lower:
            score += HEADING_EXACT_MATCH
            break
        elif hint_lower in heading_lower or heading_lower in hint_lower:
            score += HEADING_PARTIAL_MATCH
            break

    # Keyword density in the section's pages
    if hints.keywords:
        page_start = entry.page_start
        page_end = entry.page_end or entry.page_start
        section_text = ""
        for p in range(page_start, page_end + 1):
            section_text += " " + page_texts.get(p, "")

        if section_text.strip():
            section_lower = section_text.lower()
            keyword_hits = sum(
                1 for kw in hints.keywords if kw.lower() in section_lower
            )
            density = keyword_hits / len(hints.keywords)
            score += min(density * KEYWORD_DENSITY_WEIGHT * 10, MAX_KEYWORD_DENSITY_SCORE)

    return score


def select_windows(
    entries: list[OutlineEntry],
    scores: list[float],
    k: int = 3,
    margin: int = 1,
    max_page: int = 0,
) -> list[tuple[int, int]]:
    """Select top-k sections by score and expand by margin pages.

    Returns list of (start_page, end_page) windows, not yet merged.
    """
    if not entries or not scores:
        return []

    # Sort by score descending
    scored = sorted(zip(scores, entries, strict=True), key=lambda x: x[0], reverse=True)

    # Take top-k with score > 0
    top_k = [(s, e) for s, e in scored if s > 0][:k]

    if not top_k:
        return []

    windows: list[tuple[int, int]] = []
    for _, entry in top_k:
        start = max(1, entry.page_start - margin)
        end = entry.page_end or entry.page_start
        end = end + margin
        if max_page > 0:
            end = min(end, max_page)
        windows.append((start, end))

    return windows


def merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or adjacent page windows."""
    if not windows:
        return []

    sorted_windows = sorted(windows, key=lambda w: w[0])
    merged: list[tuple[int, int]] = [sorted_windows[0]]

    for start, end in sorted_windows[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    return merged


def estimate_tokens(page_texts: dict[int, str], page_window: tuple[int, int]) -> int:
    """Estimate token count for a page window (chars / 4 approximation)."""
    total_chars = 0
    for p in range(page_window[0], page_window[1] + 1):
        total_chars += len(page_texts.get(p, ""))
    return total_chars // 4
