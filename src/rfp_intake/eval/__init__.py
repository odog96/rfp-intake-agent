"""Evaluation harness — golden set scoring for extraction quality."""

from rfp_intake.eval.golden import GoldenDocument, GoldenField, load_golden_set
from rfp_intake.eval.scoring import score_document, score_field

__all__ = [
    "GoldenDocument",
    "GoldenField",
    "load_golden_set",
    "score_document",
    "score_field",
]
