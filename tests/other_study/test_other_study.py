"""MARK_OTHER_STUDY on hand-built documents — stage 4 of PLAN_2026-10-02.md.

Offline and fast: no PDF, and no real model. The model's answer is supplied by a
stub rather than by `MockChatModel`, because the mock is keyed by a hash of the
prompt and a test that had to compute that hash would stop testing the node and
start testing the hash.

The acceptance test the plan asks for is
`test_a_section_in_the_style_of_1_3_2_is_removed_and_recorded`: a section written
the way the real protocol's section 1.3.2 "Clinical Experience" is written, taken
out and recorded. The rules worth testing hardest are the two that decide what is
NOT removed — a sentence the model paraphrased, and a verdict about a section that
was not in the batch — because a wrong removal loses a number silently while a
wrong keep only costs tokens.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from rfp_intake.domain.schemas import Document, RunState, Section
from rfp_intake.other_study import (
    MIN_SECTION_CHARS,
    batch_sections,
    locate_sentence,
    mark_other_study,
    mark_other_study_node,
    removals_for_verdict,
    study_identity,
)
from rfp_intake.other_study.prompt import (
    OtherStudyBatch,
    SectionVerdict,
    build_other_study_prompt,
    describe_identity,
)
from rfp_intake.sections import section_text

# Long enough to clear MIN_SECTION_CHARS, so a test section is one the node will
# actually ask about. Written out rather than generated so each test reads as the
# document it is about.
THIS_STUDY_BODY = (
    "This is a randomized, double-blind, placebo-controlled Phase 3 study of "
    "NEOD001 plus standard of care in subjects with light chain amyloidosis. "
    "Approximately 260 subjects will be enrolled at 75 sites in 20 countries. "
    "Subjects will be randomized 1:1 to receive NEOD001 or a matching placebo "
    "every 28 days until the end of the study. The primary endpoint is all-cause "
    "mortality. "
)

OTHER_STUDY_BODY = (
    "Study NEOD001-001 was an ongoing, open-label Phase 1/2 dose-escalation "
    "study of NEOD001 in 27 subjects with light chain amyloidosis. As of the data "
    "cutoff of 01 March 2015, 22 of 27 subjects had completed at least four "
    "cycles of treatment. The maximum tolerated dose was not reached. These "
    "results supported the dose selected for the present study. "
)


def build_doc(
    *sections: tuple[str, int, str],
    doc_id: str = "doc-protocol",
    kind: str = "protocol",
    protocol_id: str | None = "NEOD001-002",
    title: str | None = "A Phase 3 Study of NEOD001 in AL Amyloidosis",
) -> Document:
    """A one-page document from (heading, level, body) triples, laid out in order.

    Offsets are real, as in `tests/sections/test_set_aside.py`: each section's text
    is sliced back out of `page_texts`, so a span this node records is checked
    against the same slicing the pipeline uses.
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
        kind=kind,  # type: ignore[arg-type]
        pages=1,
        page_texts={1: text},
        sections=built,
        section_source="bookmarks",
        protocol_id=protocol_id,
        title=title,
    )


class StubStructured:
    """Returns a prepared answer per call and records the prompts it was given.

    `answers` is consumed in order; running out raises, so a test that expects two
    batches and gets three fails loudly rather than silently reusing an answer.
    """

    def __init__(self, *answers: OtherStudyBatch | Exception) -> None:
        self.answers = list(answers)
        self.prompts: list[list[Any]] = []

    def extract(self, model: type[BaseModel], messages: list[Any]) -> Any:
        self.prompts.append(messages)
        if not self.answers:
            raise AssertionError("StubStructured was called more times than it has answers")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def verdicts(*items: SectionVerdict) -> OtherStudyBatch:
    return OtherStudyBatch(sections=list(items))


class TestTheStageFourAcceptanceTest:
    """The plan's own offline test: a section like 1.3.2 is removed and recorded."""

    def test_a_section_in_the_style_of_1_3_2_is_removed_and_recorded(self) -> None:
        doc = build_doc(
            ("1.1 Background", 2, THIS_STUDY_BODY),
            ("1.3.2 Clinical Experience", 3, OTHER_STUDY_BODY),
            ("1.4 Study Rationale", 2, THIS_STUDY_BODY),
        )
        clinical_experience = doc.sections[1]
        stub = StubStructured(
            verdicts(
                SectionVerdict(
                    section_id=doc.sections[0].id, verdict="this_study", reason="This study."
                ),
                SectionVerdict(
                    section_id=clinical_experience.id,
                    verdict="other_study",
                    reason="Describes completed study NEOD001-001, not this study.",
                ),
                SectionVerdict(
                    section_id=doc.sections[2].id, verdict="this_study", reason="This study."
                ),
            )
        )

        removed = mark_other_study(doc, study_identity([doc]), stub)

        assert len(removed) == 1
        passage = removed[0]
        assert passage.heading == "1.3.2 Clinical Experience"
        assert passage.section_id == clinical_experience.id
        assert passage.verdict == "other_study"
        assert "NEOD001-001" in passage.reason
        assert "Phase 1/2 dose-escalation" in passage.text

    def test_the_removed_text_is_gone_from_what_plan_and_extract_read(self) -> None:
        """The whole point of the stage: not the record, but the text going away.

        `section_text` is the function PLAN's scoring and EXTRACT's excerpt both go
        through, so asserting on it is asserting on both.
        """
        doc = build_doc(
            ("1.1 Background", 2, THIS_STUDY_BODY),
            ("1.3.2 Clinical Experience", 3, OTHER_STUDY_BODY),
        )
        clinical_experience = doc.sections[1]
        assert "Phase 1/2" in section_text(doc, clinical_experience)

        doc.removed = mark_other_study(
            doc,
            study_identity([doc]),
            StubStructured(
                verdicts(
                    SectionVerdict(
                        section_id=clinical_experience.id,
                        verdict="other_study",
                        reason="A different study.",
                    )
                )
            ),
        )

        assert section_text(doc, clinical_experience).strip() == ""
        # And the neighbour is untouched — a removal must not take text with it.
        assert "Phase 3 study of NEOD001" in section_text(doc, doc.sections[0])


class TestMixedSections:
    def test_only_the_named_sentences_go(self) -> None:
        body = THIS_STUDY_BODY + OTHER_STUDY_BODY
        doc = build_doc(("1.3 Drug Experience", 2, body))
        section = doc.sections[0]
        sentence = "The maximum tolerated dose was not reached."

        doc.removed = mark_other_study(
            doc,
            study_identity([doc]),
            StubStructured(
                verdicts(
                    SectionVerdict(
                        section_id=section.id,
                        verdict="mixed",
                        reason="One sentence reports an earlier study's result.",
                        other_study_sentences=[sentence],
                    )
                )
            ),
        )

        kept = section_text(doc, section)
        assert sentence not in kept
        assert "Approximately 260 subjects will be enrolled" in kept
        assert "The primary endpoint is all-cause mortality." in kept

    def test_a_sentence_the_model_paraphrased_is_kept_not_removed(self) -> None:
        """The validation rule, and the direction it fails in.

        Quoting PLAN_2026-10-02.md stage 4: "A sentence that fails validation is
        not removed, and the failure is logged." Removing a near-miss would take
        out whichever text happened to be nearby.
        """
        doc = build_doc(("1.3 Drug Experience", 2, THIS_STUDY_BODY + OTHER_STUDY_BODY))
        section = doc.sections[0]

        passage, unverified = removals_for_verdict(
            doc,
            section,
            SectionVerdict(
                section_id=section.id,
                verdict="mixed",
                reason="An earlier study.",
                other_study_sentences=["The MTD was never reached in the first study."],
            ),
        )

        assert passage is None
        assert unverified == ["The MTD was never reached in the first study."]

    def test_a_sentence_whose_line_breaks_moved_is_still_found(self) -> None:
        """A model copying out of an excerpt returns line breaks as spaces.

        `locate_text` matches on whitespace-normalised text for exactly this, so a
        faithful copy is not rejected over a newline. Same rule EXTRACT's
        `validate_quote` applies to a quote.
        """
        doc = build_doc(("1.3 Drug Experience", 2, "The maximum tolerated\ndose was not reached. "))
        section = doc.sections[0]

        span = locate_sentence(doc, section, "The maximum tolerated dose was not reached.")

        assert span is not None
        page_text = doc.page_texts[1]
        assert page_text[span.start_offset : span.end_offset] == (
            "The maximum tolerated\ndose was not reached."
        )

    def test_a_mixed_verdict_with_no_sentences_removes_nothing(self) -> None:
        doc = build_doc(("1.3 Drug Experience", 2, OTHER_STUDY_BODY))
        section = doc.sections[0]

        passage, unverified = removals_for_verdict(
            doc,
            section,
            SectionVerdict(section_id=section.id, verdict="mixed", reason="Mixed."),
        )

        assert passage is None
        assert unverified == []

    def test_two_sentences_from_one_section_are_one_passage(self) -> None:
        doc = build_doc(("1.3 Drug Experience", 2, THIS_STUDY_BODY + OTHER_STUDY_BODY))
        section = doc.sections[0]

        passage, _ = removals_for_verdict(
            doc,
            section,
            SectionVerdict(
                section_id=section.id,
                verdict="mixed",
                reason="Two sentences report an earlier study.",
                other_study_sentences=[
                    "The maximum tolerated dose was not reached.",
                    "These results supported the dose selected for the present study.",
                ],
            ),
        )

        assert passage is not None
        assert len(passage.spans) == 2
        assert passage.verdict == "mixed"


class TestWhatIsNotRemoved:
    def test_a_this_study_verdict_removes_nothing(self) -> None:
        doc = build_doc(("1.1 Background", 2, THIS_STUDY_BODY))
        passage, unverified = removals_for_verdict(
            doc,
            doc.sections[0],
            SectionVerdict(
                section_id=doc.sections[0].id, verdict="this_study", reason="This study."
            ),
        )
        assert passage is None
        assert unverified == []

    def test_a_verdict_about_a_section_not_in_the_batch_is_ignored(self) -> None:
        """A model answering about an id it was not given must not remove anything.

        Acting on it would delete text nobody asked about, and the id could belong
        to another document entirely.
        """
        doc = build_doc(("1.1 Background", 2, THIS_STUDY_BODY))
        stub = StubStructured(
            verdicts(
                SectionVerdict(
                    section_id="doc-other:s999",
                    verdict="other_study",
                    reason="Not a section in this batch.",
                )
            )
        )

        assert mark_other_study(doc, study_identity([doc]), stub) == []

    def test_a_short_section_is_never_asked_about(self) -> None:
        doc = build_doc(
            ("1.1 Background", 2, "See Section 1.3."),
            ("1.3.2 Clinical Experience", 3, OTHER_STUDY_BODY),
        )
        batches = batch_sections(doc)
        asked = [section.id for batch in batches for section, _ in batch]

        assert doc.sections[0].id not in asked
        assert doc.sections[1].id in asked
        assert len("See Section 1.3.") < MIN_SECTION_CHARS


class TestBatching:
    def test_sections_are_grouped_rather_than_asked_one_at_a_time(self) -> None:
        """Ten calls for a 137-page protocol, not one per section.

        PLAN_2026-10-02.md stage 4 asks for batching explicitly, and the cost is
        the reason: the real protocol keeps 121 sections after stage 3.
        """
        doc = build_doc(*[(f"{n} Section", 2, THIS_STUDY_BODY) for n in range(1, 21)])
        batches = batch_sections(doc)

        assert len(batches) < 20
        assert sum(len(batch) for batch in batches) == 20

    def test_no_batch_is_over_the_budget_except_a_single_long_section(self) -> None:
        doc = build_doc(*[(f"{n} Section", 2, THIS_STUDY_BODY * 4) for n in range(1, 11)])

        for batch in batch_sections(doc, budget=1000):
            if len(batch) == 1:
                continue
            assert sum(len(text) for _, text in batch) // 4 <= 1000

    def test_a_section_longer_than_the_budget_is_not_split(self) -> None:
        """A verdict on half a section would be recorded against all of it."""
        doc = build_doc(("1 Long", 2, THIS_STUDY_BODY * 20))
        batches = batch_sections(doc, budget=100)

        assert len(batches) == 1
        assert len(batches[0]) == 1

    def test_every_section_appears_in_exactly_one_batch(self) -> None:
        doc = build_doc(*[(f"{n} Section", 2, THIS_STUDY_BODY) for n in range(1, 16)])
        asked = [section.id for batch in batch_sections(doc, budget=500) for section, _ in batch]

        assert sorted(asked) == sorted(s.id for s in doc.sections)
        assert len(asked) == len(set(asked))


class TestStudyIdentity:
    def test_the_protocols_own_number_wins_over_an_rfp_that_quotes_it_wrongly(self) -> None:
        rfp = build_doc(
            ("1 Services", 2, THIS_STUDY_BODY),
            doc_id="doc-rfp",
            kind="rfp",
            protocol_id="NEOD001-999",
            title=None,
        )
        protocol = build_doc(("1.1 Background", 2, THIS_STUDY_BODY))

        identity = study_identity([rfp, protocol])

        assert "NEOD001-002" in identity
        assert "NEOD001-999" not in identity

    def test_a_document_with_no_number_or_title_says_so(self) -> None:
        doc = build_doc(
            ("1 Services", 2, THIS_STUDY_BODY), protocol_id=None, title=None, kind="other"
        )
        identity = study_identity([doc])

        assert "does not state a protocol number or title" in identity

    def test_an_empty_protocol_number_is_not_printed_as_an_empty_label(self) -> None:
        """An empty "Protocol number:" line invites a match against any number."""
        assert "Protocol number" not in describe_identity(None, "A Phase 3 Study")


class TestThePrompt:
    def test_each_section_is_labelled_with_its_full_id(self) -> None:
        """Ids are echoed back, so a renumbered one would point at another section."""
        messages = build_other_study_prompt(
            "- Protocol number: NEOD001-002",
            [("doc-protocol:s042", "1.3.2 Clinical Experience", OTHER_STUDY_BODY)],
        )
        human = str(messages[-1].content)

        assert "--- Section doc-protocol:s042 ---" in human
        assert "Heading: 1.3.2 Clinical Experience" in human
        assert "Phase 1/2 dose-escalation" in human

    def test_the_identity_reaches_the_system_message(self) -> None:
        messages = build_other_study_prompt("- Protocol number: NEOD001-002", [])
        assert "NEOD001-002" in str(messages[0].content)

    def test_the_tie_break_is_stated(self) -> None:
        """The prompt must say which way to fail, or the model picks its own."""
        messages = build_other_study_prompt("- Title: A Study", [])
        assert "If you cannot tell, answer this_study" in str(messages[0].content)

    def test_the_subject_test_is_stated(self) -> None:
        """A sentence is judged by what it is about, not by what it names.

        Live run `r-20261002-213531-stage4` removed three sentences whose subject
        is this study because each one named outside work: the pooled PK analysis,
        the progression-confirmation procedure and the 0.03 ng/mL threshold. See
        the stage 4 test in `docs/PLAN_2026-10-02.md`.
        """
        system = str(build_other_study_prompt("- Title: A Study", [])[0].content)
        assert "JUDGE A SENTENCE BY WHAT IT IS ABOUT, NOT BY WHAT IT MENTIONS." in system
        assert "whenever this study is its subject" in system

    def test_the_three_wrongly_removed_sentences_are_in_the_prompt_as_keeps(self) -> None:
        """The three real mistakes are carried as worked examples.

        They are quoted in `docs/PLAN_2026-10-02.md` stage 4 as well. Both copies
        exist on purpose: the plan is the pass criterion a person reads, the
        prompt is what the model reads.
        """
        system = str(build_other_study_prompt("- Title: A Study", [])[0].content)
        assert "pooled with data from similar samples from other studies" in system
        assert "required to confirm the progression" in system
        assert "0.03 ng/mL" in system

    def test_pooling_a_studys_own_samples_is_not_grounds_for_removal(self) -> None:
        """The prompt must say so, because the sentence names other studies."""
        system = str(build_other_study_prompt("- Title: A Study", [])[0].content)
        assert "including pooling them with samples or data from other studies" in system

    def test_a_citation_for_this_studys_own_number_is_not_grounds_for_removal(self) -> None:
        """Appendix 2's threshold cites Kumar et al and is still this study's number."""
        system = str(build_other_study_prompt("- Title: A Study", [])[0].content)
        assert "even when it cites the paper the number was taken or adapted from" in system

    def test_reporting_literature_is_still_grounds_for_removal(self) -> None:
        """The subject test must not save section 1.3.2.

        1.3.2 Clinical Experience reports another study's design and results, so
        narrowing the published-literature rule has to stop at reporting.
        """
        system = str(build_other_study_prompt("- Title: A Study", [])[0].content)
        assert "reporting what those studies did or found" in system


class TestTheNode:
    def _patched(
        self, monkeypatch: pytest.MonkeyPatch, *answers: OtherStudyBatch | Exception
    ) -> StubStructured:
        stub = StubStructured(*answers)
        monkeypatch.setattr("rfp_intake.other_study.get_llm", lambda role: object())
        monkeypatch.setattr(
            "rfp_intake.other_study.get_structured_output_for_role", lambda llm, role: stub
        )
        return stub

    def test_the_node_records_the_removal_on_the_document_and_on_the_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = build_doc(
            ("1.1 Background", 2, THIS_STUDY_BODY),
            ("1.3.2 Clinical Experience", 3, OTHER_STUDY_BODY),
        )
        self._patched(
            monkeypatch,
            verdicts(
                SectionVerdict(
                    section_id=doc.sections[1].id,
                    verdict="other_study",
                    reason="Completed study NEOD001-001.",
                )
            ),
        )

        update = mark_other_study_node(RunState(run_id="r-test", documents=[doc]))

        assert update["errors"] == []
        assert len(update["removed_passages"]) == 1
        # On the document, because that is what makes the removal real, and on the
        # state, because that is what extraction.json and Appendix C read.
        assert len(update["documents"][0].removed) == 1
        assert update["removed_passages"][0].heading == "1.3.2 Clinical Experience"

    def test_a_failed_call_is_an_error_and_the_document_keeps_its_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = build_doc(("1.3.2 Clinical Experience", 3, OTHER_STUDY_BODY))
        self._patched(monkeypatch, RuntimeError("Bearer Token has expired"))

        update = mark_other_study_node(RunState(run_id="r-test", documents=[doc]))

        assert len(update["errors"]) == 1
        assert update["errors"][0].node == "MARK_OTHER_STUDY"
        assert "Bearer Token has expired" in update["errors"][0].error
        assert update["documents"][0].removed == []
        assert "Phase 1/2" in section_text(doc, doc.sections[0])

    def test_removing_every_section_removes_nothing_and_logs_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A document emptied is a broken identity, not a document about nothing.

        Same guard as SET_ASIDE_SECTIONS: PLAN would have no text to score and
        every field would come back not found with no hint why.
        """
        doc = build_doc(
            ("1.1 Background", 2, THIS_STUDY_BODY),
            ("1.3.2 Clinical Experience", 3, OTHER_STUDY_BODY),
        )
        self._patched(
            monkeypatch,
            verdicts(
                *[
                    SectionVerdict(
                        section_id=section.id, verdict="other_study", reason="Everything is."
                    )
                    for section in doc.sections
                ]
            ),
        )

        update = mark_other_study_node(RunState(run_id="r-test", documents=[doc]))

        assert update["removed_passages"] == []
        assert update["documents"][0].removed == []
        assert len(update["errors"]) == 1
        assert update["errors"][0].kind == "validation"
        assert "study identity" in update["errors"][0].error

    def test_one_broken_document_does_not_stop_the_other(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = build_doc(("1.3.2 Clinical Experience", 3, OTHER_STUDY_BODY), doc_id="doc-a")
        # Two sections, because a document whose every section is removed trips the
        # never-empty-a-document guard tested above and removes nothing.
        second = build_doc(
            ("1.1 Background", 2, THIS_STUDY_BODY),
            ("1.3.2 Clinical Experience", 3, OTHER_STUDY_BODY),
            doc_id="doc-b",
        )
        self._patched(
            monkeypatch,
            RuntimeError("timeout"),
            verdicts(
                SectionVerdict(
                    section_id=second.sections[0].id,
                    verdict="other_study",
                    reason="A different study.",
                )
            ),
        )

        update = mark_other_study_node(RunState(run_id="r-test", documents=[first, second]))

        assert len(update["errors"]) == 1
        assert update["errors"][0].task_id == "doc-a"
        assert len(update["removed_passages"]) == 1
        assert update["removed_passages"][0].doc_id == "doc-b"

    def test_a_document_with_no_sections_is_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = Document(id="doc-empty", path="doc-empty.pdf", kind="rfp", pages=0)
        self._patched(monkeypatch)

        update = mark_other_study_node(RunState(run_id="r-test", documents=[doc]))

        assert update["removed_passages"] == []
        assert update["errors"] == []
