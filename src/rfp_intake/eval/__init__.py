"""Evaluation harness — golden set scoring for extraction quality."""

from rfp_intake.eval.golden import (
    GoldenContradiction,
    GoldenDocument,
    GoldenField,
    load_golden_contradictions,
    load_golden_set,
)
from rfp_intake.eval.scoring import (
    ContradictionScore,
    ContradictionSetScore,
    score_contradiction,
    score_contradiction_set,
    score_document,
    score_field,
)

__all__ = [
    "ContradictionScore",
    "ContradictionSetScore",
    "GoldenContradiction",
    "GoldenDocument",
    "GoldenField",
    "load_golden_contradictions",
    "load_golden_set",
    "score_contradiction",
    "score_contradiction_set",
    "score_document",
    "score_field",
]
