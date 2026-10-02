"""Tests for section scoring and section selection.

Rewritten for stage 2 of docs/PLAN_2026-10-02.md, which made PLAN score sections
rather than bookmark entries. `select_windows` and `merge_windows` are gone with
the page margin they existed to apply, so their tests are gone too; `TestScore`
and `TestEstimateTokens` are the same cases against the new signature.
"""

from __future__ import annotations

from rfp_intake.domain.budget import estimate_text_tokens, estimate_tokens
from rfp_intake.domain.registry import SearchHints
from rfp_intake.domain.schemas import Section
from rfp_intake.plan.scoring import score_section, select_sections


def _make_hints(
    headings: list[str] | None = None,
    keywords: list[str] | None = None,
) -> SearchHints:
    return SearchHints(headings=headings or [], keywords=keywords or [])


def _section(heading: str, page_start: int = 1, page_end: int | None = None) -> Section:
    return Section(
        id=f"doc-001:{heading}",
        heading=heading,
        level=1,
        page_start=page_start,
        page_end=page_end if page_end is not None else page_start,
    )


class TestScoreSection:
    def test_exact_heading_match(self) -> None:
        score = score_section(_section("Study Design", 3, 5), _make_hints(["Study Design"]), "")
        assert score >= 5.0

    def test_partial_heading_match(self) -> None:
        section = _section("Overview of Study Design and Methods", 3, 5)
        score = score_section(section, _make_hints(["Study Design"]), "")
        assert score >= 3.0

    def test_no_match(self) -> None:
        hints = _make_hints(headings=["Study Design"], keywords=["randomised"])
        score = score_section(_section("References", 50, 55), hints, "bibliography")
        assert score == 0.0

    def test_keyword_density(self) -> None:
        hints = _make_hints(keywords=["randomised", "double-blind", "Phase III"])
        text = "This is a randomised, double-blind Phase III study."
        assert score_section(_section("Study Overview", 1, 2), hints, text) > 0

    def test_combined_heading_and_keywords(self) -> None:
        hints = _make_hints(
            headings=["Study Design"],
            keywords=["randomised", "double-blind"],
        )
        text = "This is a randomised, double-blind study. Subjects are randomised 1:1."
        assert score_section(_section("Study Design", 3, 4), hints, text) >= 5.0

    def test_a_keyword_in_a_neighbouring_section_does_not_count(self) -> None:
        """The reason scoring takes the section's text and not the whole page.

        Section 1.3.1 of samples/Example protocol 2.pdf shares page 39 with
        section 1.3.2, which names another study's phase. Scoring 1.3.1 on the
        page would credit 1.3.1 for 1.3.2's words.
        """
        hints = _make_hints(keywords=["open-label"])
        own_text = "Nonclinical toxicology studies in the mouse."
        page_text = own_text + " An ongoing, open-label study of the drug."

        assert score_section(_section("1.3.1 Nonclinical Safety", 39), hints, own_text) == 0.0
        assert score_section(_section("1.3.1 Nonclinical Safety", 39), hints, page_text) > 0.0


class TestSelectSections:
    def test_selects_top_k(self) -> None:
        sections = [
            _section("A", 1, 3),
            _section("B", 5, 7),
            _section("C", 10, 12),
            _section("D", 15, 17),
        ]
        chosen = select_sections(sections, [2.0, 5.0, 1.0, 4.0], k=2)

        # B scored 5 and D scored 4, and they come back in document order.
        assert [s.heading for s in chosen] == ["B", "D"]

    def test_chosen_sections_are_returned_in_document_order(self) -> None:
        sections = [_section("A", 1), _section("B", 2), _section("C", 3)]
        chosen = select_sections(sections, [1.0, 9.0, 5.0], k=3)
        assert [s.heading for s in chosen] == ["A", "B", "C"]

    def test_no_page_margin_is_applied(self) -> None:
        """Replaces the old test_margin_expands_window: the margin is gone."""
        chosen = select_sections([_section("A", 5, 7)], [3.0], k=1)
        assert (chosen[0].page_start, chosen[0].page_end) == (5, 7)

    def test_empty_sections(self) -> None:
        assert select_sections([], [], k=3) == []

    def test_all_zero_scores(self) -> None:
        assert select_sections([_section("A", 1, 3)], [0.0], k=3) == []

    def test_a_zero_scoring_section_is_never_chosen_to_fill_k(self) -> None:
        sections = [_section("A", 1), _section("B", 2)]
        chosen = select_sections(sections, [4.0, 0.0], k=3)
        assert [s.heading for s in chosen] == ["A"]


class TestEstimateTokens:
    def test_estimates(self) -> None:
        page_texts = {1: "a" * 400, 2: "b" * 400, 3: "c" * 400}
        assert estimate_tokens(page_texts, (1, 3)) == 300  # 1200 chars / 4

    def test_missing_pages(self) -> None:
        assert estimate_tokens({1: "a" * 100}, (1, 3)) == 25  # only page 1 has content

    def test_text_and_page_estimates_agree(self) -> None:
        page_texts = {1: "a" * 400, 2: "b" * 404}
        assert estimate_tokens(page_texts, (1, 2)) == estimate_text_tokens("a" * 400) + (
            estimate_text_tokens("b" * 404)
        )
