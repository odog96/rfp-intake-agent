"""Section scoring for extraction targeting.

Scores a section from FIND_SECTIONS against a field group's `search_hints`.
Rewritten on 2026-10-02 (stage 2 of `docs/PLAN_2026-10-02.md`) to take a
`Section` and that section's text instead of an `OutlineEntry` and the document's
pages. The weights below are unchanged.

**This module must not import `rfp_intake.sections`**, because `rfp_intake.plan`
imports `rfp_intake.sections` and importing any module of this package executes
`plan/__init__.py`. So `score_section` is handed the section's text rather than
computing it: `plan/__init__.py` imports both modules and does the joining.

The token budget itself lives in `domain/budget.py`, for the same reason, and is
re-exported here so that `from rfp_intake.plan.scoring import
DEFAULT_TOKEN_BUDGET` keeps working.
"""

from __future__ import annotations

from rfp_intake.domain.budget import (
    DEFAULT_TOKEN_BUDGET,
    estimate_text_tokens,
    estimate_tokens,
)
from rfp_intake.domain.registry import SearchHints
from rfp_intake.domain.schemas import Section

# Scoring weights
HEADING_EXACT_MATCH = 5.0
HEADING_PARTIAL_MATCH = 3.0
KEYWORD_DENSITY_WEIGHT = 2.0
MAX_KEYWORD_DENSITY_SCORE = 4.0


def score_section(
    section: Section,
    hints: SearchHints,
    text: str,
) -> float:
    """Score one section against a field group's search hints.

    Higher score = more likely to contain content for the field group. `text` is
    the section's own text, which the caller gets from
    `rfp_intake.sections.section_text`.
    """
    score = 0.0

    # Heading match scoring
    heading_lower = section.heading.lower().strip()
    for hint_heading in hints.headings:
        hint_lower = hint_heading.lower().strip()
        if hint_lower == heading_lower:
            score += HEADING_EXACT_MATCH
            break
        elif hint_lower in heading_lower or heading_lower in hint_lower:
            score += HEADING_PARTIAL_MATCH
            break

    # Keyword density in the section's own text. Before stage 2 this read whole
    # pages, so a keyword in a neighbouring section on the same page counted
    # towards this section's score.
    if hints.keywords and text.strip():
        text_lower = text.lower()
        keyword_hits = sum(1 for kw in hints.keywords if kw.lower() in text_lower)
        density = keyword_hits / len(hints.keywords)
        score += min(density * KEYWORD_DENSITY_WEIGHT * 10, MAX_KEYWORD_DENSITY_SCORE)

    return score


def select_sections(
    sections: list[Section],
    scores: list[float],
    k: int = 3,
) -> list[Section]:
    """The k highest-scoring sections, returned in document order.

    A section scoring zero is never chosen. There is no page margin: before
    stage 2 the chosen pages were widened by one page either side, which is how
    text from a section PLAN had not chosen reached the extraction model.
    Section boundaries are exact, so nothing needs widening.
    """
    if not sections or not scores:
        return []

    ranked = sorted(
        zip(scores, range(len(sections)), strict=True),
        key=lambda pair: (-pair[0], pair[1]),
    )
    chosen = [index for score, index in ranked if score > 0][:k]
    return [sections[index] for index in sorted(chosen)]


__all__ = [
    "DEFAULT_TOKEN_BUDGET",
    "HEADING_EXACT_MATCH",
    "HEADING_PARTIAL_MATCH",
    "KEYWORD_DENSITY_WEIGHT",
    "MAX_KEYWORD_DENSITY_SCORE",
    "estimate_text_tokens",
    "estimate_tokens",
    "score_section",
    "select_sections",
]
