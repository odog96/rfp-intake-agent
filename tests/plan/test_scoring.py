"""Tests for outline section scoring logic."""

from __future__ import annotations

from rfp_intake.domain.registry import SearchHints
from rfp_intake.domain.schemas import OutlineEntry
from rfp_intake.plan.scoring import (
    estimate_tokens,
    merge_windows,
    score_section,
    select_windows,
)


def _make_hints(
    headings: list[str] | None = None,
    keywords: list[str] | None = None,
) -> SearchHints:
    return SearchHints(
        headings=headings or [],
        keywords=keywords or [],
    )


def _make_entry(heading: str, page_start: int, page_end: int | None = None) -> OutlineEntry:
    return OutlineEntry(heading=heading, page_start=page_start, page_end=page_end, level=1)


class TestScoreSection:
    def test_exact_heading_match(self) -> None:
        hints = _make_hints(headings=["Study Design"])
        entry = _make_entry("Study Design", 3, 5)
        score = score_section(entry, hints, {})
        assert score >= 5.0

    def test_partial_heading_match(self) -> None:
        hints = _make_hints(headings=["Study Design"])
        entry = _make_entry("Overview of Study Design and Methods", 3, 5)
        score = score_section(entry, hints, {})
        assert score >= 3.0

    def test_no_match(self) -> None:
        hints = _make_hints(headings=["Study Design"], keywords=["randomised"])
        entry = _make_entry("References", 50, 55)
        score = score_section(entry, hints, {50: "bibliography"})
        assert score == 0.0

    def test_keyword_density(self) -> None:
        hints = _make_hints(keywords=["randomised", "double-blind", "Phase III"])
        entry = _make_entry("Study Overview", 1, 2)
        page_texts = {
            1: "This is a randomised, double-blind Phase III study.",
            2: "The study is Phase III and randomised.",
        }
        score = score_section(entry, hints, page_texts)
        assert score > 0

    def test_combined_heading_and_keywords(self) -> None:
        hints = _make_hints(
            headings=["Study Design"],
            keywords=["randomised", "double-blind"],
        )
        entry = _make_entry("Study Design", 3, 4)
        page_texts = {
            3: "This is a randomised, double-blind study.",
            4: "Subjects are randomised 1:1.",
        }
        score = score_section(entry, hints, page_texts)
        # Both heading match and keyword density should contribute
        assert score >= 5.0


class TestSelectWindows:
    def test_selects_top_k(self) -> None:
        entries = [
            _make_entry("A", 1, 3),
            _make_entry("B", 5, 7),
            _make_entry("C", 10, 12),
            _make_entry("D", 15, 17),
        ]
        scores = [2.0, 5.0, 1.0, 4.0]

        windows = select_windows(entries, scores, k=2, margin=0)
        assert len(windows) == 2
        # Should pick B (score=5) and D (score=4)
        assert (5, 7) in windows
        assert (15, 17) in windows

    def test_margin_expands_window(self) -> None:
        entries = [_make_entry("A", 5, 7)]
        scores = [3.0]

        windows = select_windows(entries, scores, k=1, margin=1)
        assert windows == [(4, 8)]

    def test_margin_clamps_to_page_1(self) -> None:
        entries = [_make_entry("A", 1, 3)]
        scores = [3.0]

        windows = select_windows(entries, scores, k=1, margin=2)
        assert windows[0][0] == 1

    def test_empty_entries(self) -> None:
        assert select_windows([], [], k=3) == []

    def test_all_zero_scores(self) -> None:
        entries = [_make_entry("A", 1, 3)]
        scores = [0.0]
        assert select_windows(entries, scores, k=3) == []


class TestMergeWindows:
    def test_overlapping(self) -> None:
        windows = [(1, 5), (3, 8), (10, 12)]
        assert merge_windows(windows) == [(1, 8), (10, 12)]

    def test_adjacent(self) -> None:
        windows = [(1, 3), (4, 6), (7, 9)]
        assert merge_windows(windows) == [(1, 9)]

    def test_no_overlap(self) -> None:
        windows = [(1, 3), (6, 8), (11, 13)]
        assert merge_windows(windows) == [(1, 3), (6, 8), (11, 13)]

    def test_single_window(self) -> None:
        assert merge_windows([(3, 7)]) == [(3, 7)]

    def test_empty(self) -> None:
        assert merge_windows([]) == []

    def test_unordered_input(self) -> None:
        windows = [(10, 12), (1, 5), (3, 8)]
        assert merge_windows(windows) == [(1, 8), (10, 12)]


class TestEstimateTokens:
    def test_estimates(self) -> None:
        page_texts = {1: "a" * 400, 2: "b" * 400, 3: "c" * 400}
        tokens = estimate_tokens(page_texts, (1, 3))
        assert tokens == 300  # 1200 chars / 4

    def test_missing_pages(self) -> None:
        page_texts = {1: "a" * 100}
        tokens = estimate_tokens(page_texts, (1, 3))
        assert tokens == 25  # only page 1 has content
