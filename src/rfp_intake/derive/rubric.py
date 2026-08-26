"""Transparent, table-driven rubrics for DERIVE. Pure Python, zero LLM.

Each rubric is a scored sum over already-resolved evidence, never a "vibe
call" — ARCHITECTURE.md §4.8. A reviewer must be able to see why a rating
landed where it did and disagree with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from rfp_intake.domain.schemas import Provenance, ResolvedField

# visits.intensity_evidence weights, per ARCHITECTURE.md §4.8's rubric table.
# fields.yaml's `visits.intensity_evidence` enum has evolved past the exact
# evidence names in that table (e.g. `overnight_stay`, `complex_procedures`
# were added; "visit window < 3 days" became `long_visit_windows`, which
# inverts the original wording — a *narrow* window signals tighter, more
# intensive monitoring, which is what `long_visit_windows` is standing in
# for here). Weights below are a best-effort mapping onto the current enum,
# not a re-derivation from source. Recalibrate against the golden set per
# ARCHITECTURE.md §9 rather than trusting these numbers as-is.
VISIT_INTENSITY_WEIGHTS: dict[str, int] = {
    "pk_pd_sampling": 2,
    "biomarker_sampling": 1,
    "imaging": 2,
    "ecgs": 1,
    "safety_labs": 1,
    "questionnaires": 1,
    "infusion_observation_period": 2,
    "multiple_assessments_per_visit": 2,
    "frequent_early_visits": 2,
    "complex_procedures": 2,
    "overnight_stay": 2,
    "long_visit_windows": 1,
    "none_found": 0,
}

VISIT_INTENSITY_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (8, "high"),
    (4, "moderate"),
    (0, "low"),
)


@dataclass
class RubricResult:
    value: str | None
    status: Literal["not_specified", "needs_review"]
    confidence: float
    sources: list[Provenance] = field(default_factory=list)
    explanation: str = ""


def compute_visit_intensity(inputs: dict[str, ResolvedField | None]) -> RubricResult:
    """Score `visits.intensity_evidence` flags into a low/moderate/high rating.

    `inputs` is keyed by the field ids in visits.intensity_rating's
    `derived_from` (ARCHITECTURE.md §4.8: evidence, frequency, count).
    Only `visits.intensity_evidence` currently drives the score — see the
    module docstring on VISIT_INTENSITY_WEIGHTS for why frequency/count
    aren't independently scored: the evidence enum already carries a
    `frequent_early_visits` flag for that signal.
    """
    evidence = inputs.get("visits.intensity_evidence")
    if evidence is None or evidence.status != "needs_review":
        return RubricResult(value="not_specified", status="not_specified", confidence=0.0)
    if not isinstance(evidence.value, list):
        return RubricResult(value="not_specified", status="not_specified", confidence=0.0)

    flags: list[str] = [f for f in evidence.value if isinstance(f, str)]
    if not flags or flags == ["none_found"]:
        return RubricResult(value="not_specified", status="not_specified", confidence=0.0)

    score = sum(VISIT_INTENSITY_WEIGHTS.get(flag, 0) for flag in flags)
    rating = next(label for floor, label in VISIT_INTENSITY_THRESHOLDS if score >= floor)

    contributing = sorted(
        (f for f in flags if VISIT_INTENSITY_WEIGHTS.get(f, 0) > 0),
        key=lambda f: -VISIT_INTENSITY_WEIGHTS.get(f, 0),
    )
    explanation = (
        f"score={score} from {', '.join(contributing) or 'no weighted evidence'} "
        f"(thresholds: 0-3 low, 4-7 moderate, 8+ high)"
    )

    return RubricResult(
        value=rating,
        status="needs_review",  # GATE always sends derived fields to needs_review
        confidence=evidence.confidence,
        sources=list(evidence.sources),
        explanation=explanation,
    )
