"""How much text one extraction call is given, and how it is estimated.

Both FIND_SECTIONS (`rfp_intake.sections`) and PLAN (`rfp_intake.plan`) need
these, and PLAN imports `rfp_intake.sections`. They lived in `plan/scoring.py`
until 2026-10-02, which looks far enough away but is not: importing
`rfp_intake.plan.scoring` executes `rfp_intake/plan/__init__.py` first, so
`rfp_intake.sections` reading the constant from there made importing
`rfp_intake.sections` before `rfp_intake.plan` fail with a circular import.
`rfp_intake.domain` imports neither package, so here is safe.

The estimate is characters / 4. It is deliberately crude: it decides how to split
work, not what is sent to a model, and a real tokeniser would tie this to one
model's vocabulary.
"""

from __future__ import annotations

# The most text one extraction call is given, estimated as characters / 4.
DEFAULT_TOKEN_BUDGET = 4000


def estimate_text_tokens(text: str) -> int:
    """Estimate the token count of a piece of text.

    One place for the chars-per-token rule, so a section's estimate and a page
    window's estimate cannot drift apart.
    """
    return len(text) // 4


def estimate_tokens(page_texts: dict[int, str], page_window: tuple[int, int]) -> int:
    """Estimate the token count of every page in an inclusive page window."""
    return sum(
        estimate_text_tokens(page_texts.get(page, ""))
        for page in range(page_window[0], page_window[1] + 1)
    )
