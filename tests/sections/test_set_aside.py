"""SET_ASIDE_SECTIONS on hand-built documents — stage 3 of PLAN_2026-10-02.md.

Offline and fast: no PDF, no model. The stage 3 acceptance test on the real
protocol is in `test_set_aside_samples.py`.

The rule worth testing hardest is the nesting one. "7 Eligibility" means the
whole of section 7, so its subsections go with it — but only down to the next
heading at the same or a higher level, and a subsection that names a cost driver
is still rescued.
"""

from __future__ import annotations

from typing import Any

import pytest

from rfp_intake.domain.schemas import Document, RunState, Section
from rfp_intake.domain.section_policy import SectionsPolicy
from rfp_intake.sections import FRONT_MATTER_HEADING, WHOLE_DOCUMENT_HEADING
from rfp_intake.sections.set_aside import set_aside_sections, set_aside_sections_node


def build_doc(
    *sections: tuple[str, int, str],
    doc_id: str = "doc1",
    source: str = "bookmarks",
) -> Document:
    """A one-page document from (heading, level, body) triples, laid out in order.

    Offsets are real: each section's text is sliced back out of `page_texts` by
    `section_text`, so a `keep_if_contains` test here exercises the same slicing
    the pipeline uses rather than a convenient string.
    """
    text = ""
    built: list[Section] = []
    for index, (heading, level, body) in enumerate(sections):
        start = len(text)
        text += f"{heading}\n{body}\n"
        built.append(
            Section(
                id=f"{doc_id}:s{index:03d}",
                heading=heading,
                level=level,
                page_start=1,
                page_end=1,
                start_offset=start,
            )
        )
    for earlier, later in zip(built, built[1:], strict=False):
        earlier.end_offset = later.start_offset

    return Document(
        id=doc_id,
        path=f"{doc_id}.pdf",
        kind="protocol",
        pages=1,
        page_texts={1: text},
        sections=built,
        section_source=source,  # type: ignore[arg-type]
    )


@pytest.fixture
def policy() -> SectionsPolicy:
    return SectionsPolicy(
        set_aside=["eligibility", "glossary", "drug storage", "references"],
        keep_if_contains=["unblinded", "schedule of activities"],
    )


def headings(sections: list[Section]) -> list[str]:
    return [s.heading for s in sections]


class TestOnlyWhatIsListed:
    def test_a_listed_heading_goes_and_an_unlisted_one_stays(
        self, policy: SectionsPolicy
    ) -> None:
        doc = build_doc(
            ("4 Study Design", 1, "A double-blind study."),
            ("7 Eligibility", 1, "Subjects must be 18 or over."),
            ("8 Study Drug", 1, "Given by infusion."),
        )
        kept, dropped = set_aside_sections(doc, policy)
        assert headings(kept) == ["4 Study Design", "8 Study Drug"]
        assert [s.heading for s in dropped] == ["7 Eligibility"]
        assert dropped[0].matched == "eligibility"
        assert dropped[0].via_parent is None

    def test_an_unanticipated_heading_survives(self, policy: SectionsPolicy) -> None:
        """The rule that makes the whole design safe: never drop the unexpected.

        PLAN_2026-10-02.md stage 3: "only remove what is positively on the list."
        A heading nobody anticipated is exactly where an unbudgeted cost hides.
        """
        doc = build_doc(
            ("9 Sponsor's Unusual Monitoring Requirements", 1, "Weekly visits."),
            ("10 References", 1, "Smith et al."),
        )
        kept, dropped = set_aside_sections(doc, policy)
        assert headings(kept) == ["9 Sponsor's Unusual Monitoring Requirements"]
        assert [s.heading for s in dropped] == ["10 References"]

    def test_the_record_carries_document_heading_and_pages(
        self, policy: SectionsPolicy
    ) -> None:
        doc = build_doc(
            ("1 Synopsis", 1, "Summary."),
            ("2 Glossary", 1, "AE: adverse event."),
            doc_id="doc-protocol",
        )
        _, dropped = set_aside_sections(doc, policy)
        assert len(dropped) == 1
        assert dropped[0].doc_id == "doc-protocol"
        assert dropped[0].section_id == "doc-protocol:s001"
        assert dropped[0].heading == "2 Glossary"
        assert (dropped[0].page_start, dropped[0].page_end) == (1, 1)


class TestNesting:
    def test_children_go_with_their_parent(self, policy: SectionsPolicy) -> None:
        doc = build_doc(
            ("7 Eligibility", 1, "Overview."),
            ("7.1 Inclusion Criteria", 2, "Aged 18 or over."),
            ("7.2 Exclusion Criteria", 2, "Prior therapy."),
            ("7.2.1 Prior Therapy", 3, "Any anthracycline."),
            ("8 Study Drug", 1, "Given by infusion."),
        )
        kept, dropped = set_aside_sections(doc, policy)
        assert headings(kept) == ["8 Study Drug"]
        assert [s.heading for s in dropped] == [
            "7 Eligibility",
            "7.1 Inclusion Criteria",
            "7.2 Exclusion Criteria",
            "7.2.1 Prior Therapy",
        ]

    def test_a_child_records_the_parent_that_took_it(self, policy: SectionsPolicy) -> None:
        doc = build_doc(
            ("7 Eligibility", 1, "Overview."),
            ("7.1 Inclusion Criteria", 2, "Aged 18 or over."),
            ("8 Study Drug", 1, "Infusion."),
        )
        _, dropped = set_aside_sections(doc, policy)
        child = next(s for s in dropped if s.heading == "7.1 Inclusion Criteria")
        assert child.via_parent == "doc1:s000"
        # The child's own heading was never on the list, and saying so is the
        # difference between a deliberate removal and an accidental one.
        assert child.matched is None

    def test_the_drop_stops_at_the_next_heading_of_the_same_level(
        self, policy: SectionsPolicy
    ) -> None:
        doc = build_doc(
            ("4 Study Design", 1, "Overview."),
            ("4.1 Eligibility", 2, "Aged 18 or over."),
            ("4.1.1 Inclusion", 3, "Confirmed diagnosis."),
            ("4.2 Endpoints", 2, "Overall survival."),
            ("4.3 Duration", 2, "42 months."),
        )
        kept, dropped = set_aside_sections(doc, policy)
        assert headings(kept) == ["4 Study Design", "4.2 Endpoints", "4.3 Duration"]
        assert [s.heading for s in dropped] == ["4.1 Eligibility", "4.1.1 Inclusion"]

    def test_a_shallower_heading_also_closes_the_drop(self, policy: SectionsPolicy) -> None:
        doc = build_doc(
            ("7.3 Eligibility", 3, "Overview."),
            ("7.3.1 Inclusion", 4, "Aged 18 or over."),
            ("8 Study Drug", 1, "Infusion."),
        )
        kept, _ = set_aside_sections(doc, policy)
        assert headings(kept) == ["8 Study Drug"]


class TestKeepIfContains:
    def test_a_listed_section_is_kept_when_its_text_names_a_cost_driver(
        self, policy: SectionsPolicy
    ) -> None:
        """ANALYST_PROCEDURE_PROTOCOL.md section 5, the storage-section case.

        "Sometimes this is the only place the document says unblinded staff are
        needed." Keeping a section nobody needed costs tokens; dropping this one
        costs a staffing line nobody budgets for.
        """
        doc = build_doc(
            ("6.4 Drug Storage", 1, "Store at 2-8C. Access is limited to unblinded staff."),
            ("7 Eligibility", 1, "Aged 18 or over."),
        )
        kept, dropped = set_aside_sections(doc, policy)
        assert headings(kept) == ["6.4 Drug Storage"]
        assert [s.heading for s in dropped] == ["7 Eligibility"]

    def test_the_phrase_is_found_across_a_line_break(self, policy: SectionsPolicy) -> None:
        # Two sections, because a one-section document is left alone anyway.
        doc = build_doc(
            ("1 Synopsis", 1, "Summary."),
            ("6.4 Drug Storage", 1, "Access is limited to the\nunblinded pharmacy staff."),
        )
        kept, dropped = set_aside_sections(doc, policy)
        assert headings(kept) == ["1 Synopsis", "6.4 Drug Storage"]
        assert dropped == []

    def test_a_rescued_parent_does_not_take_its_children(self, policy: SectionsPolicy) -> None:
        """A kept parent leaves its children to be judged on their own headings.

        The parent matched the list but was rescued, so it is not the deliberate
        "drop the whole of section 7" case that the nesting rule exists for.
        Judging the children separately keeps more text, which is the safe
        direction.
        """
        doc = build_doc(
            ("6 Drug Storage", 1, "Handled by unblinded pharmacy staff."),
            ("6.1 Temperature", 2, "Store at 2-8C."),
            ("6.2 Eligibility", 2, "Aged 18 or over."),
        )
        kept, dropped = set_aside_sections(doc, policy)
        assert headings(kept) == ["6 Drug Storage", "6.1 Temperature"]
        assert [s.heading for s in dropped] == ["6.2 Eligibility"]

    def test_a_rescued_child_is_kept_and_its_siblings_still_go(
        self, policy: SectionsPolicy
    ) -> None:
        doc = build_doc(
            ("7 Eligibility", 1, "Overview."),
            ("7.1 Screening", 2, "See the schedule of activities in Appendix 1."),
            ("7.2 Rescreening", 2, "Permitted once."),
        )
        kept, dropped = set_aside_sections(doc, policy)
        assert headings(kept) == ["7.1 Screening"]
        assert [s.heading for s in dropped] == ["7 Eligibility", "7.2 Rescreening"]


class TestDocumentsLeftAlone:
    @pytest.mark.parametrize("source", ["whole_document", "whole_document_fallback"])
    def test_a_whole_document_is_untouched(self, policy: SectionsPolicy, source: str) -> None:
        """FIND_SECTIONS rule 2: one section covering every page, nothing to set aside.

        This is the synthetic RFP. Its one section is headed "Whole document",
        so there is no heading to match — but the source is checked too, because
        a rule 2 document must be sent whole to every group (stage 2).
        """
        doc = build_doc(("Glossary", 1, "AE: adverse event."), source=source)
        kept, dropped = set_aside_sections(doc, policy)
        assert len(kept) == 1
        assert dropped == []

    def test_a_single_section_document_is_untouched(self, policy: SectionsPolicy) -> None:
        doc = build_doc(("Glossary", 1, "AE: adverse event."))
        kept, dropped = set_aside_sections(doc, policy)
        assert len(kept) == 1
        assert dropped == []

    @pytest.mark.parametrize("heading", [FRONT_MATTER_HEADING, WHOLE_DOCUMENT_HEADING])
    def test_the_synthetic_headings_are_never_set_aside(self, heading: str) -> None:
        """FIND_SECTIONS invents these two, so no list of real section names owns them.

        Tested against a policy that names them explicitly, because the point is
        that the code refuses, not that the shipped file happens not to ask.
        """
        hostile = SectionsPolicy(
            set_aside=[heading.lower(), "glossary"], keep_if_contains=[]
        )
        doc = build_doc(
            (heading, 1, "Protocol NEOD001-CL002, Amendment 3."),
            ("2 Glossary", 1, "AE: adverse event."),
        )
        kept, dropped = set_aside_sections(doc, hostile)
        assert headings(kept) == [heading]
        assert [s.heading for s in dropped] == ["2 Glossary"]

    def test_the_source_document_is_not_mutated(self, policy: SectionsPolicy) -> None:
        doc = build_doc(
            ("1 Synopsis", 1, "Summary."),
            ("2 Glossary", 1, "AE: adverse event."),
        )
        set_aside_sections(doc, policy)
        assert len(doc.sections) == 2


class TestNode:
    @pytest.fixture(autouse=True)
    def _use_test_policy(self, monkeypatch: pytest.MonkeyPatch, policy: SectionsPolicy) -> None:
        monkeypatch.setattr(
            "rfp_intake.sections.set_aside.get_sections_policy", lambda: policy
        )

    def test_it_removes_the_sections_from_the_document_so_plan_never_sees_them(self) -> None:
        doc = build_doc(
            ("1 Synopsis", 1, "Summary."),
            ("2 Glossary", 1, "AE: adverse event."),
        )
        out = set_aside_sections_node(RunState(run_id="r1", documents=[doc]))
        assert headings(out["documents"][0].sections) == ["1 Synopsis"]
        assert [s.heading for s in out["set_aside"]] == ["2 Glossary"]
        assert out["errors"] == []

    def test_it_collects_removals_from_every_document(self) -> None:
        state = RunState(
            run_id="r1",
            documents=[
                build_doc(("1 Synopsis", 1, "S."), ("2 Glossary", 1, "AE."), doc_id="d1"),
                build_doc(("1 Scope", 1, "S."), ("9 References", 1, "Smith."), doc_id="d2"),
            ],
        )
        out = set_aside_sections_node(state)
        assert {s.doc_id for s in out["set_aside"]} == {"d1", "d2"}

    def test_it_keeps_the_document_whole_when_every_section_matches(self) -> None:
        """A policy that empties a document is a policy bug, not a document.

        PLAN would have nothing to score and the run would report every field as
        not found with no hint why, so the document is kept and the run carries a
        validation error that names the cause.
        """
        doc = build_doc(
            ("2 Glossary", 1, "AE: adverse event."),
            ("9 References", 1, "Smith et al."),
        )
        out = set_aside_sections_node(RunState(run_id="r1", documents=[doc]))
        assert len(out["documents"][0].sections) == 2
        assert out["set_aside"] == []
        assert len(out["errors"]) == 1
        assert out["errors"][0].node == "SET_ASIDE_SECTIONS"
        assert out["errors"][0].kind == "validation"
        assert "Check the policy" in out["errors"][0].error

    def test_one_broken_document_does_not_end_the_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(doc: Document, policy: Any) -> Any:
            if doc.id == "d1":
                raise RuntimeError("offsets are nonsense")
            return list(doc.sections), []

        monkeypatch.setattr("rfp_intake.sections.set_aside.set_aside_sections", explode)
        state = RunState(
            run_id="r1",
            documents=[
                build_doc(("1 S", 1, "S."), ("2 Glossary", 1, "AE."), doc_id="d1"),
                build_doc(("1 S", 1, "S."), ("2 Glossary", 1, "AE."), doc_id="d2"),
            ],
        )
        out = set_aside_sections_node(state)
        assert len(out["documents"]) == 2
        assert len(out["errors"]) == 1
        assert out["errors"][0].task_id == "d1"
        assert out["errors"][0].kind == "validation"

    def test_a_run_with_no_documents_is_fine(self) -> None:
        out = set_aside_sections_node(RunState(run_id="r1"))
        assert out == {"documents": [], "set_aside": [], "errors": []}
