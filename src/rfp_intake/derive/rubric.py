"""Transparent, table-driven rubrics for DERIVE. Pure Python, zero LLM.

Each rubric is a scored sum over already-resolved evidence, never a "vibe
call" — ARCHITECTURE.md §4.8. A reviewer must be able to see why a rating
landed where it did and disagree with it.

Each rubric says why twice. `why` is the reasons in plain words, for the row in
report.pdf: the words come from `value_words` and the labels in
config/fields.yaml, never a field id or an enum spelling (fix 8 of the
2026-10-12 brief, CLAUDE.md open item 5). `explanation` is the full account for
Appendix A — the same reasons, then the score or every input with its quote.

No rubric is registered today. The two there were, the visit-intensity rating
and what to assume about the placebo, were removed on 2026-11-23 with their
fields: Angus Gray said on 29 October 2026 that visit intensity was not needed,
and of the placebo that "the tool shouldn't assume". Their code is in git
history before that date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from rfp_intake.domain.schemas import Provenance


@dataclass
class RubricResult:
    value: str | None
    status: Literal["not_specified", "needs_review"]
    confidence: float
    sources: list[Provenance] = field(default_factory=list)
    explanation: str = ""
    why: str = ""
