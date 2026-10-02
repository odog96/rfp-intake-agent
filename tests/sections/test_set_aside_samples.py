"""SET_ASIDE_SECTIONS on the real protocol — the stage 3 test in PLAN_2026-10-02.md.

Quoting that test in full:

    On the protocol, the glossary, the inclusion and exclusion criteria and the
    references are set aside; "Statistical Considerations" and the study-drug
    storage section are kept; a hand-built section headed "Storage" that
    contains "unblinded pharmacy staff" is kept.

Offline: the PDF is read from `samples/` with the same parser INGEST used on it,
and nothing here calls a model. The document fixture is reused from
`test_find_sections_samples`, so the 137-page protocol is parsed once for both
files rather than twice.

The real policy is deliberately used, not a stub. `config/sections.yaml` is the
entire behaviour of this stage, so a test against a stub would prove the loop
works and nothing about whether the shipped list is right.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rfp_intake.domain.schemas import Document, Section
from rfp_intake.domain.section_policy import SectionsPolicy, load_sections_policy
from rfp_intake.sections import find_sections, section_text
from rfp_intake.sections.set_aside import set_aside_sections
from tests.sections.test_find_sections_samples import PROTOCOL, _document


@pytest.fixture
def policy(sections_yaml_path: Path) -> SectionsPolicy:
    return load_sections_policy(sections_yaml_path)


@pytest.fixture
def protocol(samples_dir: Path) -> Document:
    pdf = samples_dir / PROTOCOL
    if not pdf.exists():
        pytest.skip(f"{PROTOCOL} not available")
    doc = _document(pdf, "doc-protocol", "protocol")
    sections, source = find_sections(doc)
    # A copy, because `_document` is cached for the whole session and the
    # sections list must not leak between tests.
    return doc.model_copy(update={"sections": sections, "section_source": source})


@pytest.mark.slow
class TestTheStageThreeAcceptanceTest:
    def test_the_sections_angus_ignores_are_set_aside(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        _, dropped = set_aside_sections(protocol, policy)
        gone = " | ".join(s.heading.lower() for s in dropped)
        for required in ("glossary", "inclusion criteria", "exclusion criteria", "reference"):
            assert required in gone, f"{required!r} was not set aside"

    def test_statistical_considerations_is_kept(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        """Scan, do not skip — it is the only place an interim analysis is stated.

        ANALYST_PROCEDURE_PROTOCOL.md section 5. Keeping it is why
        `statistical considerations` is absent from `set_aside` even though the
        section is mostly irrelevant, and why the parent is absent too: the
        nesting rule would have taken the interim-analysis subsection with it.
        """
        kept, _ = set_aside_sections(protocol, policy)
        survived = [s.heading for s in kept if "statistical" in s.heading.lower()]
        assert survived, "no statistical section survived"

    def test_the_study_drug_storage_section_is_kept(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        """The heading is on the list; the unblinded-staff sentence rescues it.

        This is the case Angus called out twice [01:39:05, 01:40:30]: in this
        protocol the storage section is the only place the document admits that
        unblinded pharmacy staff are needed, which is a direct staffing cost.
        """
        kept, dropped = set_aside_sections(protocol, policy)
        kept_headings = [s.heading.lower() for s in kept]
        assert any("storage" in h for h in kept_headings), (
            "every storage section was set aside; the set-aside headings were "
            f"{[s.heading for s in dropped if 'storage' in s.heading.lower()]}"
        )

    def test_most_of_the_protocol_still_survives(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        """A guard on the list as a whole, not on any one entry.

        Stage 4's live test needs at least 26 of the 36 fields confirmed. If a
        future edit to `config/sections.yaml` quietly removed most of the
        document, that test would fail with no clue why. The bound is loose on
        purpose — it catches a runaway entry, not a judgement call.
        """
        kept, dropped = set_aside_sections(protocol, policy)
        total = len(kept) + len(dropped)
        assert total == len(protocol.sections)
        assert len(kept) > total * 0.5, f"only {len(kept)} of {total} sections survived"

    @pytest.mark.parametrize(
        ("phrase", "heading"),
        [
            # Oliver named these three on 2026-10-02 as the sentences that must
            # survive stage 3, so they are asserted as text rather than as
            # headings: a future edit to `config/sections.yaml` could keep a
            # section with the right name and still lose the sentence, because
            # the nesting rule takes children.
            ("No interim analyses are planned for this study", "10.7 Interim Analysis"),
            (
                "strictly limited to the unblinded pharmacy staff",
                "6.2 Shipping, Storage, and Handling of NEOD001",
            ),
            ("A matching placebo will not be provided", "6.3 Placebo"),
        ],
    )
    def test_the_sentences_that_must_survive_do(
        self, protocol: Document, policy: SectionsPolicy, phrase: str, heading: str
    ) -> None:
        """Each sentence is still readable, and still in the section it came from.

        Each one is a cost driver the budget needs and nothing else in the
        protocol states: whether a second analysis has to be staffed and
        unblinded, whether unblinded pharmacy staff are needed at every site,
        and whether a placebo has to be manufactured and distributed.

        The heading is asserted as well as the text, because the same words
        appearing somewhere else — the contents page lists "10.7 Interim
        Analysis" — would satisfy a text-only check while the section itself had
        gone.
        """
        kept, _ = set_aside_sections(protocol, policy)
        wanted = " ".join(phrase.split()).lower()
        holding = [
            section
            for section in kept
            if wanted in " ".join(section_text(protocol, section).split()).lower()
        ]
        assert holding, f"{phrase!r} is not in any kept section"
        assert heading in [section.heading for section in holding], (
            f"{phrase!r} survived, but in {[s.heading for s in holding]} rather than {heading!r}"
        )

    def test_nothing_is_lost_or_counted_twice(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        kept, dropped = set_aside_sections(protocol, policy)
        ids = [s.id for s in kept] + [s.section_id for s in dropped]
        assert sorted(ids) == sorted(s.id for s in protocol.sections)
        assert len(set(ids)) == len(ids)


@pytest.mark.slow
class TestTheBackgroundAndNonclinicalSections:
    """Stage 5 item 1: the background sections Angus skips, now on `set_aside`.

    `docs/ANALYST_PROCEDURE_PROTOCOL.md` section 4 says to ignore the
    introduction, the background, the drug's chemistry and the nonclinical
    (animal) safety data [01:35:05], and the rationale for dose selection
    [01:37:07]. `config/sections.yaml` held `background on` back until stage 4
    had passed on its own, so that MARK_OTHER_STUDY's live test could not pass by
    accident — see the comment on the entry.

    Oliver asked for this test on 2026-10-02 after live run
    `r-20261002-222824-stage4b` kept 1.3 and Figure 1: MARK_OTHER_STUDY is no
    longer the thing that removes nonclinical background, SET_ASIDE_SECTIONS is.
    """

    def test_the_background_section_is_set_aside(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        """1.3 Background on NEOD001 (p.36), matched on its own heading."""
        _, dropped = set_aside_sections(protocol, policy)
        gone = {s.heading: s for s in dropped}
        assert "1.3 Background on NEOD001" in gone
        assert gone["1.3 Background on NEOD001"].matched == "background on"

    def test_the_mechanism_figure_goes_with_it(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        """Figure 1 is a level-4 child of 1.3 and has no heading phrase of its own.

        It is here because of the nesting rule, which is the only thing that
        removes it — `matched` is None and `via_parent` names 1.3. A future edit
        that narrowed the nesting rule would lose this without touching the
        entry.
        """
        _, dropped = set_aside_sections(protocol, policy)
        gone = {s.heading: s for s in dropped}
        figure = "Figure 1: Proposed Mechanism of Action for NEOD001"
        assert figure in gone
        assert gone[figure].matched is None
        assert gone[figure].via_parent is not None

    def test_the_nonclinical_safety_section_is_set_aside(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        """1.3.1 Nonclinical Safety. On the list before this change and still gone."""
        _, dropped = set_aside_sections(protocol, policy)
        assert "1.3.1 Nonclinical Safety" in {s.heading for s in dropped}

    def test_the_dose_selection_rationale_is_set_aside(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        """1.4 Rationale for Dose Selection, matched on `rationale for dose selection`.

        Asserted on the match and not just the absence, because the bare word
        `rationale` is deliberately not on the list: it would also take 1.2
        Rationale for Clinical Study, which states the comparison being made.
        """
        _, dropped = set_aside_sections(protocol, policy)
        gone = {s.heading: s for s in dropped}
        assert "1.4 Rationale for Dose Selection" in gone
        assert gone["1.4 Rationale for Dose Selection"].matched == "rationale for dose selection"

    def test_clinical_experience_survives_so_its_other_sentences_can_be_read(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        """1.3.2 Clinical Experience is nested under 1.3 and must NOT go whole.

        MARK_OTHER_STUDY cuts the five sentences in it that describe study
        NEOD001-001 — the Phase 1/2 study number, the 30 September 2015 data
        cutoff, the subject counts and the adverse-event list — and what is left
        is this study's text. Setting the whole section aside would take both.

        **It survives for an incidental reason.** `keep_if_contains` rescues it on
        the phrase "interim analysis", and that phrase sits in one of the very
        sentences MARK_OTHER_STUDY then removes. The rescue is real — stage 3
        runs before stage 4, so the sentence is still there when the policy reads
        it — but a protocol that worded its interim analysis differently would
        lose the section. This test is the alarm for that day.
        """
        kept, dropped = set_aside_sections(protocol, policy)
        assert "1.3.2 Clinical Experience" in {s.heading for s in kept}
        assert "1.3.2 Clinical Experience" not in {s.heading for s in dropped}

    def test_the_sentences_left_in_clinical_experience_are_still_readable(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        """Asserted as text, not as a heading, for the reason the stage 3 test gives.

        This sentence is the last one in 1.3.2, on page 40, and all five of
        MARK_OTHER_STUDY's spans in run `r-20261002-222824-stage4b` are on page
        39 — so it is text about this study's drug that survives both stages. A
        kept section with the right name and the wrong text would satisfy a
        heading-only check.
        """
        kept, _ = set_aside_sections(protocol, policy)
        wanted = "NEOD001 is safe and well-tolerated in subjects with AL amyloidosis"
        holding = [
            section.heading
            for section in kept
            if wanted in " ".join(section_text(protocol, section).split())
        ]
        assert "1.3.2 Clinical Experience" in holding, (
            f"the surviving sentence is in {holding} rather than 1.3.2"
        )

    def test_the_title_page_is_untouched(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        """The level-1 title carries the phase, the blinding, the control and the arms.

        `docs/ANALYST_PROCEDURE_PROTOCOL.md` section 2. It is the single most
        valuable heading in the document, it contains the word "STANDARD OF
        CARE", and `study.phase` reads `phase_3` off it, so a background entry
        that reached it would undo stage 4.
        """
        kept, _ = set_aside_sections(protocol, policy)
        titles = [s.heading for s in kept if s.level == 1 and "PHASE 3" in s.heading]
        assert titles, "the title page section did not survive"
        assert "LIGHT CHAIN (AL) AMYLOIDOSIS" in titles[0]

    def test_the_synopsis_and_the_rest_of_section_one_are_untouched(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        """The synopsis, 1 INTRODUCTION, 1.1 and 1.2 all stay.

        `background on` is narrow on purpose. A bare `introduction` would take
        the whole of section 1 under the nesting rule, and a bare `background`
        or `rationale` would take 1.2 Rationale for Clinical Study.
        """
        kept, _ = set_aside_sections(protocol, policy)
        survived = {s.heading for s in kept}
        for heading in (
            "PROTOCOL SYNOPSIS",
            "1 INTRODUCTION",
            "1.1 Light Chain (AL) Amyloidosis",
            "1.2 Rationale for Clinical Study",
        ):
            assert heading in survived, f"{heading!r} was set aside"


@pytest.mark.slow
def test_a_storage_section_naming_unblinded_staff_is_kept(
    protocol: Document, policy: SectionsPolicy
) -> None:
    """The hand-built section the stage 3 test asks for, added to the real protocol.

    The sentence is Angus's own example [01:39:05]. It is appended as a real
    section over the protocol's last page so that `section_text` slices it out
    of `page_texts` exactly as it would any other section — a bare string would
    not test the slicing.
    """
    page = max(protocol.page_texts)
    text = protocol.page_texts[page]
    added = (
        "Storage\nAccess to the study drug should be strictly limited to the "
        "unblinded pharmacy staff.\n"
    )
    doc = protocol.model_copy(
        update={
            "page_texts": {**protocol.page_texts, page: text + added},
            "sections": [
                *protocol.sections,
                Section(
                    id="doc-protocol:s999",
                    heading="Storage",
                    level=1,
                    page_start=page,
                    page_end=page,
                    start_offset=len(text),
                ),
            ],
        }
    )

    kept, dropped = set_aside_sections(doc, policy)
    assert "doc-protocol:s999" in {s.id for s in kept}
    assert "doc-protocol:s999" not in {s.section_id for s in dropped}
