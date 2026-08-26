"""Per-field scoring — precision, recall, citation accuracy, status match."""

from __future__ import annotations

from dataclasses import dataclass, field

from rfp_intake.domain.schemas import ResolvedField
from rfp_intake.eval.golden import GoldenDocument, GoldenField


@dataclass
class FieldScore:
    """Score for a single field comparison."""

    field_id: str
    value_correct: bool = False
    status_correct: bool = False
    citation_correct: bool = False
    was_extracted: bool = False
    expected_status: str = "found"
    actual_status: str | None = None


@dataclass
class DocumentScore:
    """Aggregate scores for one document."""

    document_id: str
    field_scores: list[FieldScore] = field(default_factory=list)

    @property
    def precision(self) -> float:
        """Of fields we extracted, how many are correct."""
        extracted = [s for s in self.field_scores if s.was_extracted]
        if not extracted:
            return 0.0
        correct = sum(1 for s in extracted if s.value_correct)
        return correct / len(extracted)

    @property
    def recall(self) -> float:
        """Of fields expected to be found, how many did we find."""
        expected_found = [s for s in self.field_scores if s.expected_status == "found"]
        if not expected_found:
            return 1.0
        found = sum(1 for s in expected_found if s.was_extracted)
        return found / len(expected_found)

    @property
    def citation_accuracy(self) -> float:
        """Of fields with expected pages, how many cited the right page."""
        with_page = [
            s for s in self.field_scores
            if s.citation_correct is not None and s.was_extracted
        ]
        if not with_page:
            return 0.0
        correct = sum(1 for s in with_page if s.citation_correct)
        return correct / len(with_page)

    @property
    def status_accuracy(self) -> float:
        """How often we got the terminal state right (found/not_specified/not_found)."""
        if not self.field_scores:
            return 0.0
        correct = sum(1 for s in self.field_scores if s.status_correct)
        return correct / len(self.field_scores)

    def confusion_matrix(self) -> dict[str, dict[str, int]]:
        """3x3 matrix over found/not_specified/not_found."""
        states = ["found", "not_specified", "not_found"]
        matrix: dict[str, dict[str, int]] = {s: {t: 0 for t in states} for s in states}
        for s in self.field_scores:
            expected = s.expected_status
            actual = s.actual_status or "not_found"
            if expected in matrix and actual in matrix[expected]:
                matrix[expected][actual] += 1
        return matrix


def score_field(
    field_id: str,
    golden: GoldenField,
    resolved: ResolvedField | None,
) -> FieldScore:
    """Score a single field against its golden answer."""
    if resolved is None:
        return FieldScore(
            field_id=field_id,
            was_extracted=False,
            expected_status=golden.expected_status,
            actual_status="not_found",
            status_correct=golden.expected_status == "not_found",
        )

    was_extracted = resolved.status in ("confirmed", "needs_review")
    actual_status = _map_resolved_status(resolved.status)
    status_correct = golden.expected_status == actual_status

    value_correct = False
    if golden.expected_value is not None and resolved.value is not None:
        value_correct = _values_match(golden.expected_value, resolved.value)
    elif golden.expected_status != "found" and actual_status != "found":
        value_correct = True

    citation_correct = False
    if golden.expected_page is not None and resolved.sources:
        citation_correct = any(s.page == golden.expected_page for s in resolved.sources)

    return FieldScore(
        field_id=field_id,
        value_correct=value_correct,
        status_correct=status_correct,
        citation_correct=citation_correct,
        was_extracted=was_extracted,
        expected_status=golden.expected_status,
        actual_status=actual_status,
    )


def score_document(
    golden: GoldenDocument,
    resolved_fields: dict[str, ResolvedField],
) -> DocumentScore:
    """Score all golden fields against resolved output for one document."""
    scores = []
    for field_id, golden_field in golden.fields.items():
        resolved = resolved_fields.get(field_id)
        scores.append(score_field(field_id, golden_field, resolved))
    return DocumentScore(document_id=golden.document_id, field_scores=scores)


def _map_resolved_status(status: str) -> str:
    """Map resolved field status to the 3 golden terminal states."""
    if status in ("confirmed", "needs_review"):
        return "found"
    return status


def _values_match(expected: object, actual: object) -> bool:
    """Compare values with tolerance for numeric and string normalization."""
    if expected == actual:
        return True
    # Numeric comparison with tolerance
    try:
        e_float = float(str(expected))
        a_float = float(str(actual))
        return abs(e_float - a_float) < 0.01 * max(abs(e_float), 1.0)
    except (ValueError, TypeError):
        pass
    # Case-insensitive string comparison
    return str(expected).lower().strip() == str(actual).lower().strip()
