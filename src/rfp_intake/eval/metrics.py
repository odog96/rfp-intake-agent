"""Aggregate metrics across documents and fields."""

from __future__ import annotations

from dataclasses import dataclass, field

from rfp_intake.eval.scoring import DocumentScore


@dataclass
class AggregateMetrics:
    """Metrics aggregated across all documents in an eval run."""

    document_scores: list[DocumentScore] = field(default_factory=list)

    @property
    def mean_precision(self) -> float:
        if not self.document_scores:
            return 0.0
        return sum(d.precision for d in self.document_scores) / len(self.document_scores)

    @property
    def mean_recall(self) -> float:
        if not self.document_scores:
            return 0.0
        return sum(d.recall for d in self.document_scores) / len(self.document_scores)

    @property
    def mean_citation_accuracy(self) -> float:
        if not self.document_scores:
            return 0.0
        return sum(d.citation_accuracy for d in self.document_scores) / len(self.document_scores)

    @property
    def mean_status_accuracy(self) -> float:
        if not self.document_scores:
            return 0.0
        return sum(d.status_accuracy for d in self.document_scores) / len(self.document_scores)

    @property
    def total_fields(self) -> int:
        return sum(len(d.field_scores) for d in self.document_scores)

    @property
    def total_correct(self) -> int:
        return sum(
            sum(1 for s in d.field_scores if s.value_correct)
            for d in self.document_scores
        )

    def summary(self) -> dict[str, float | int]:
        return {
            "documents": len(self.document_scores),
            "total_fields": self.total_fields,
            "total_correct": self.total_correct,
            "mean_precision": round(self.mean_precision, 3),
            "mean_recall": round(self.mean_recall, 3),
            "mean_citation_accuracy": round(self.mean_citation_accuracy, 3),
            "mean_status_accuracy": round(self.mean_status_accuracy, 3),
        }


def compute_aggregate(scores: list[DocumentScore]) -> AggregateMetrics:
    """Compute aggregate metrics from a list of per-document scores."""
    return AggregateMetrics(document_scores=scores)
