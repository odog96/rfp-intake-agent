"""MARK_OTHER_STUDY against the real protocol amendment — change 6 of the 2026-10-07 review.

`test_other_study.py` beside this file builds its documents by hand, and change 6
is the one change of that review where the gap mattered: live runs
`r-20261008-review2-run1` and `r-20261008-review2-run2` both let section 1.3.2
"Clinical Experience" back into the report, carrying the earlier study's Phase
1/2 into the Study phase row, and `pytest` passed throughout both. A hand-written
section cannot catch that, because the characters it is written from are the ones
the test author typed rather than the ones PyMuPDF reads off the page.

**What this file can check and what it cannot.** The model's judgement — whether
it answers `other_study`, `mixed` or `this_study` for a real section — can only
be measured by a live run, and these tests do not pretend otherwise: the verdict
here is stubbed. What they do check is everything either side of that judgement
on real characters: that section 1.3.2 is found and is actually put to the model,
that the sentences the prompt holds up as worked examples of text to remove are
really in the document it quotes them from, and that a `mixed` verdict naming
them removes exactly them.

The second of those found two defects on its first run, both in the prompt's own
worked examples and both of which would have silently kept a passage the model
had correctly asked to remove — see the module docstring of
`rfp_intake.other_study.prompt`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rfp_intake.config.settings import reset_settings
from rfp_intake.domain.schemas import Document, RunState, Section
from rfp_intake.graph.nodes.ingest import ingest_node
from rfp_intake.other_study import (
    batch_sections,
    locate_sentence,
    removals_for_verdict,
)
from rfp_intake.other_study.prompt import OTHER_STUDY_SYSTEM_PROMPT, SectionVerdict
from rfp_intake.sections import find_sections, section_text

PROTOCOL = Path(__file__).parent.parent.parent / "samples" / "Example protocol 2.pdf"

OTHER_STUDY_SECTION = "1.3.2 Clinical Experience"

# The three sentences `OTHER_STUDY_SYSTEM_PROMPT` quotes from section 1.3.2 as
# worked examples of text to REMOVE. Spelled out here rather than parsed out of
# the prompt, and checked against BOTH the prompt and the document, so the pair
# cannot drift apart: an example that stops being a real sentence of the
# document stops being an example a model can copy.
#
# Note the typographic apostrophe in "Investigator’s" and the full length of the
# enrolment sentence. `sections.locate_text` folds whitespace and case but not
# punctuation, so a straight apostrophe here does not match the page.
PROMPT_REMOVE_EXAMPLES = [
    "The safety and tolerability of NEOD001 are being investigated in an ongoing, "
    "open-label, dose escalation Phase 1/2 study (Study NEOD001-001) of the IV "
    "administration of single-agent NEOD001 in subjects with AL amyloidosis in the US.",
    "As of an interim analysis with a data cutoff date of 30 September 2015, 27 "
    "subjects were enrolled and treated in the Escalation Phase of the study and 42 "
    "subjects were enrolled and treated in the Expansion Phase.",
    "Further details can be found in the latest edition of the NEOD001 "
    "Investigator’s Brochure.",
]

# The prompt's worked example of text to KEEP. Made up for the review, so it is
# deliberately NOT in the protocol; asserted absent to keep the checks above
# honest about what "found in the document" is measuring.
PROMPT_KEEP_EXAMPLE = (
    "This study will share approximately 80% of its sites with the companion "
    "study NEOD001-CL003."
)


@pytest.fixture(scope="module")
def protocol(tmp_path_factory: pytest.TempPathFactory) -> Document:
    """The real 137-page protocol amendment, through INGEST and FIND_SECTIONS.

    Module-scoped: the parse takes about twelve seconds and nothing here writes
    to the document. Offline — INGEST and FIND_SECTIONS are both code, so no
    model is called and `samples/` is the only input.
    """
    runs = tmp_path_factory.mktemp("runs")
    inputs = runs / "real-other-study" / "inputs"
    inputs.mkdir(parents=True)
    shutil.copy(PROTOCOL, inputs / PROTOCOL.name)

    # `monkeypatch` is function-scoped and this fixture is not, so the context
    # manager form does the same job with the same undo.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("RFP_INTAKE_RUN_DIR", str(runs))
        reset_settings()
        # Annotated because the node returns an untyped state mapping.
        ingested: list[Document] = ingest_node(RunState(run_id="real-other-study"))["documents"]
    reset_settings()

    [doc] = ingested

    doc.sections, _ = find_sections(doc)
    return doc


@pytest.fixture(scope="module")
def clinical_experience(protocol: Document) -> Section:
    """Section 1.3.2, the section the live runs kept by mistake."""
    matching = [s for s in protocol.sections if s.heading == OTHER_STUDY_SECTION]
    assert matching, f"{OTHER_STUDY_SECTION} not found among {len(protocol.sections)} sections"
    return matching[0]


class TestTheSectionReachesTheModel:
    def test_section_1_3_2_is_long_enough_to_be_asked_about(
        self, protocol: Document, clinical_experience: Section
    ) -> None:
        """A section under MIN_SECTION_CHARS is never judged, so never removed.

        Section 1.3.2 is about 1,500 characters. If a future change to the
        heading scan were to cut it shorter than the floor, the report would
        quietly go back to carrying the earlier study's Phase 1/2 and no other
        test would say so.
        """
        asked_about = {
            section.id for batch in batch_sections(protocol) for section, _ in batch
        }
        assert clinical_experience.id in asked_about

    def test_the_section_is_the_earlier_study_in_the_real_text(
        self, protocol: Document, clinical_experience: Section
    ) -> None:
        """The characters the model is shown, read off the page rather than typed."""
        text = section_text(protocol, clinical_experience)
        assert "Study NEOD001-001" in text, "the earlier study is named"
        assert "Phase 1/2" in text, "and its phase is in the text, which is why it leaked"


class TestThePromptQuotesTheDocumentExactly:
    @pytest.mark.parametrize("sentence", PROMPT_REMOVE_EXAMPLES)
    def test_each_remove_example_is_in_the_prompt(self, sentence: str) -> None:
        # The prompt wraps with backslash continuations, so it holds each
        # sentence on one line exactly as spelled here.
        assert sentence in OTHER_STUDY_SYSTEM_PROMPT

    @pytest.mark.parametrize("sentence", PROMPT_REMOVE_EXAMPLES)
    def test_each_remove_example_is_in_the_real_section(
        self, protocol: Document, clinical_experience: Section, sentence: str
    ) -> None:
        """A worked example a model copies must be locatable, or the removal is dropped.

        `removals_for_verdict` keeps any sentence it cannot find in the text —
        the safe direction for a paraphrase, but it means a prompt that misquotes
        the document teaches the model to produce sentences that are thrown away.
        """
        assert locate_sentence(protocol, clinical_experience, sentence) is not None

    def test_the_keep_example_is_not_in_the_protocol(
        self, protocol: Document, clinical_experience: Section
    ) -> None:
        assert locate_sentence(protocol, clinical_experience, PROMPT_KEEP_EXAMPLE) is None


class TestAMixedVerdictOnTheRealSection:
    def test_the_prompts_three_sentences_are_removed_and_nothing_else(
        self, protocol: Document, clinical_experience: Section
    ) -> None:
        """The removal machinery driven by a stubbed verdict over real characters.

        The verdict is stubbed because the model's judgement is a live-run
        question. Everything after it is not: the sentences are located in the
        page text PyMuPDF produced, and the spans cut out of that text are
        compared back with what the verdict asked for.
        """
        verdict = SectionVerdict(
            section_id=clinical_experience.id,
            verdict="mixed",
            reason="Reports the earlier Phase 1/2 study NEOD001-001.",
            other_study_sentences=list(PROMPT_REMOVE_EXAMPLES),
        )

        passage, unverified = removals_for_verdict(protocol, clinical_experience, verdict)

        assert unverified == [], "every sentence the prompt quotes is found in the document"
        assert passage is not None
        assert len(passage.spans) == len(PROMPT_REMOVE_EXAMPLES)

        # What the spans actually cover on the page, whitespace folded the way
        # `locate_text` folds it, against what was asked for.
        cut = [
            " ".join(
                protocol.page_texts[span.page][span.start_offset : span.end_offset].split()
            )
            for span in passage.spans
        ]
        assert cut == [" ".join(s.split()) for s in PROMPT_REMOVE_EXAMPLES]

    def test_the_rest_of_the_section_survives_the_removal(
        self, protocol: Document, clinical_experience: Section
    ) -> None:
        """Only the three sentences go: the adverse-event text stays.

        The sentences sit at the start of the section, which is where an
        off-by-one in the offsets would take the rest of it with them.
        """
        verdict = SectionVerdict(
            section_id=clinical_experience.id,
            verdict="mixed",
            reason="Reports the earlier Phase 1/2 study NEOD001-001.",
            other_study_sentences=list(PROMPT_REMOVE_EXAMPLES),
        )
        passage, _ = removals_for_verdict(protocol, clinical_experience, verdict)
        assert passage is not None

        before = section_text(protocol, clinical_experience)
        protocol.removed = [passage]
        try:
            after = section_text(protocol, clinical_experience)
        finally:
            # Module-scoped fixture: leave the document as the other tests expect.
            protocol.removed = []

        assert "the most frequently reported TEAEs" in after, "kept text is still there"
        assert "Phase 1/2" not in after, "the earlier study's phase is gone"
        assert len(after) < len(before)

    def test_appendix_c_records_the_printed_page_as_well(
        self, protocol: Document, clinical_experience: Section
    ) -> None:
        """Change 7 reaches a removed passage's own page reference.

        Section 1.3.2 starts on PDF page 39 of the amendment, which prints 37.
        Appendix C cites a removed passage by page like every other reference, so
        it has to carry both numbers.
        """
        verdict = SectionVerdict(
            section_id=clinical_experience.id,
            verdict="other_study",
            reason="The section as a whole reports the earlier study.",
            other_study_sentences=[],
        )
        passage, _ = removals_for_verdict(protocol, clinical_experience, verdict)
        assert passage is not None

        assert passage.page_start == 39
        assert passage.printed_page_start == protocol.page_labels[39]
        assert passage.printed_page_start == "37"
