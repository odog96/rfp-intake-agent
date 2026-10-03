"""PLAN and EXTRACT on the two real sample PDFs — the stage 2 test in PLAN_2026-10-02.md.

Offline: the PDFs are read from `samples/` and nothing here calls a model. Marked
`slow` with the other tests that parse real PDFs.

The three things stage 2 asks to be shown:
- every task for the synthetic RFP includes page 6, the services requested;
- no task for the protocol contains text from a section PLAN did not choose;
- the text the model is given is the text quote validation checks against.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import pytest

from rfp_intake.domain.registry import get_registry
from rfp_intake.domain.schemas import Document, ExtractionTask, RemovedPassage, TextSpan
from rfp_intake.extract.prompt import build_excerpt, build_extract_prompt
from rfp_intake.plan import plan_extraction
from rfp_intake.sections import find_sections, section_text

PROTOCOL = "Example protocol 2.pdf"
SYNTHETIC_RFP = "Synthetic_RFP_NEOD001.pdf"

# Long enough to be unmistakably one section's text and not a shared phrase.
LEAK_PROBE_CHARS = 300
MIN_SECTION_CHARS_TO_PROBE = 400


def _flat(text: str) -> str:
    """Collapse whitespace and case, for matching a sentence in extracted PDF text.

    Two things defeat a literal `in` test against PDF text, and both produced a
    false result while measuring the eviction in CLAUDE.md item 4c. A sentence
    that spans a line break arrives with a newline and the next line's
    indentation inside it. And this protocol defines "Unblinded Pharmacy Staff"
    as a capitalised term, so a needle copied from a lower-case hint does not
    match the document that plainly contains it.
    """
    return re.sub(r"\s+", " ", text).lower()


@lru_cache(maxsize=4)
def _document(pdf: Path, doc_id: str, kind: str) -> Document:
    """Parsed and sectioned once per session, as PLAN actually receives it.

    SET_ASIDE_SECTIONS runs between FIND_SECTIONS and PLAN in the graph, so the
    sections PLAN scores are the kept ones. Until 2026-10-03 this fixture stopped
    after FIND_SECTIONS and every test here scored a section list production never
    sees — which ranked this protocol's sections differently and made the
    `top_k: 7` measured on the real pipeline look wrong.
    """
    from rfp_intake.domain.section_policy import get_sections_policy
    from rfp_intake.ingest.parsers.rung1 import Rung1Parser
    from rfp_intake.sections.set_aside import set_aside_sections

    parser = Rung1Parser()
    pages = parser._extract_text(pdf)  # noqa: SLF001 - the real parser's text, without its tables
    doc = Document(
        id=doc_id,
        path=str(pdf),
        kind=kind,  # type: ignore[arg-type]
        pages=len(pages),
        page_texts={p.page_num: p.text for p in pages},
        outline=parser._extract_outline(pdf),  # noqa: SLF001
    )
    doc.sections, doc.section_source = find_sections(doc)
    doc.sections, _dropped = set_aside_sections(doc, get_sections_policy())
    return doc


@pytest.fixture
def registry(fields_yaml_path: Path):  # type: ignore[no-untyped-def]
    os.environ["RFP_INTAKE_FIELDS_YAML_PATH"] = str(fields_yaml_path)
    get_registry.cache_clear()
    return get_registry()


@pytest.fixture
def protocol(samples_dir: Path) -> Document:
    pdf = samples_dir / PROTOCOL
    if not pdf.exists():
        pytest.skip(f"{PROTOCOL} not available")
    return _document(pdf, "doc-protocol", "protocol")


@pytest.fixture
def synthetic_rfp(samples_dir: Path) -> Document:
    pdf = samples_dir / SYNTHETIC_RFP
    if not pdf.exists():
        pytest.skip(f"{SYNTHETIC_RFP} not available")
    return _document(pdf, "doc-rfp", "rfp")


@pytest.mark.slow
class TestSyntheticRfp:
    def test_every_task_includes_page_6(self, synthetic_rfp: Document, registry) -> None:  # type: ignore[no-untyped-def]
        """Known problem 1a in CLAUDE.md, now closed.

        This PDF has no bookmarks, so PLAN used to give every field group pages 1
        to 5 and page 6 — the services requested — was never read by anything.
        """
        tasks = plan_extraction([synthetic_rfp], registry)

        assert len(tasks) >= 9
        for task in tasks:
            assert "--- Page 6 ---" in build_excerpt(task, synthetic_rfp)

    def test_the_opposing_placebo_sentence_reaches_the_blinding_group(
        self, synthetic_rfp: Document, registry
    ) -> None:  # type: ignore[no-untyped-def]
        """The other half of the placebo disagreement.

        This RFP asks for "Matching placebo for NEOD001"; the protocol says a
        matching placebo will not be provided. Stage 5 criterion A expected
        blinding.placebo_matching to come back not_matching_stated, which was the
        wrong thing to ask for: the two documents genuinely disagree and the right
        output is a contradiction, not a value. That is asserted in
        tests/adjudicate/test_adjudicate_node.py; what this test holds is the
        precondition for it, that EXTRACT is shown both sentences.
        """
        tasks = [
            t
            for t in plan_extraction([synthetic_rfp], registry)
            if t.group == "blinding_monitoring"
        ]
        assert tasks, "the blinding_monitoring group has at least one task"

        excerpts = [_flat(build_excerpt(task, synthetic_rfp)) for task in tasks]
        assert any(_flat("Matching placebo") in e for e in excerpts)

    def test_the_whole_document_is_one_task_per_group(
        self, synthetic_rfp: Document, registry
    ) -> None:  # type: ignore[no-untyped-def]
        tasks = plan_extraction([synthetic_rfp], registry)

        assert len(tasks) == len(registry.groups)
        for task in tasks:
            assert task.page_window == (1, 6)
            assert len(task.section_ids) == 1


@pytest.mark.slow
class TestProtocol:
    def test_no_task_contains_an_unchosen_sections_text(
        self, protocol: Document, registry
    ) -> None:  # type: ignore[no-untyped-def]
        """The stage 2 acceptance test, and the point of the whole change."""
        tasks = plan_extraction([protocol], registry)
        assert tasks

        for task in tasks:
            excerpt = build_excerpt(task, protocol)
            chosen = set(task.section_ids)
            for section in protocol.sections:
                if section.id in chosen:
                    continue
                text = section_text(protocol, section).strip()
                if len(text) < MIN_SECTION_CHARS_TO_PROBE:
                    continue
                probe = text[:LEAK_PROBE_CHARS]
                assert probe not in excerpt, (
                    f"{task.group} task {task.page_window} contains text from "
                    f"the unchosen section {section.heading.strip()!r}"
                )

    def test_the_other_studys_phase_is_kept_out_by_mark_other_study_not_by_plan(
        self, protocol: Document, registry
    ) -> None:  # type: ignore[no-untyped-def]
        """Known problem 1: study.phase read the phase of a referenced study.

        Section 1.3.2 "Clinical Experience" describes study NEOD001-001 as
        "ongoing, open-label". Until 2026-10-03 this test asserted that PLAN does
        not choose that section for the phase_population group — and it passed,
        because this file's fixture stopped at FIND_SECTIONS. With
        SET_ASIDE_SECTIONS applied, as the graph applies it before PLAN, 57 of the
        protocol's 176 sections go and 1.3.2 rises into this group's top three. So
        PLAN *does* choose it, and PLAN is not what protects `study.phase`.

        What protects it is MARK_OTHER_STUDY, which runs between SET_ASIDE_SECTIONS
        and PLAN and puts the passage in `Document.removed`; `section_page_texts`
        cuts anything there out of both PLAN's scoring and EXTRACT's excerpt. That
        is asserted here directly, by removing the passage the way that node does,
        because it is an LLM call and cannot run offline.

        Both halves are asserted. If a future change makes PLAN stop choosing
        1.3.2, the first assertion fails and says so rather than quietly leaving
        this test checking nothing.
        """
        before = [
            t
            for t in plan_extraction([protocol], registry)
            if t.group == "phase_population"
        ]
        assert before, "the phase_population group has at least one task"
        assert any("ongoing, open-label" in build_excerpt(t, protocol) for t in before), (
            "PLAN no longer chooses section 1.3.2 for phase_population. That is an "
            "improvement, but this test is written to prove MARK_OTHER_STUDY is "
            "what removes the text — rewrite it rather than deleting the assertion."
        )

        clinical_experience = next(
            s for s in protocol.sections if s.heading.strip().startswith("1.3.2")
        )
        removed = protocol.model_copy(deep=True)
        removed.removed = [
            RemovedPassage(
                doc_id=removed.id,
                section_id=clinical_experience.id,
                heading=clinical_experience.heading,
                page_start=clinical_experience.page_start,
                page_end=clinical_experience.page_end,
                verdict="other_study",
                reason="describes study NEOD001-001, not this one",
                text=section_text(protocol, clinical_experience),
                spans=[
                    TextSpan(
                        page=page,
                        start_offset=(
                            clinical_experience.start_offset
                            if page == clinical_experience.page_start
                            else 0
                        ),
                        end_offset=(
                            clinical_experience.end_offset
                            if page == clinical_experience.page_end
                            else None
                        ),
                    )
                    for page in range(
                        clinical_experience.page_start, clinical_experience.page_end + 1
                    )
                ],
            )
        ]

        after = [
            t for t in plan_extraction([removed], registry) if t.group == "phase_population"
        ]
        for task in after:
            assert "ongoing, open-label" not in build_excerpt(task, removed)

    def test_the_placebo_sentence_reaches_the_blinding_group(
        self, protocol: Document, registry
    ) -> None:  # type: ignore[no-untyped-def]
        """Criterion A of stage 5 failed because EXTRACT never saw this sentence.

        Section "6.3 Placebo" scored 0.000 for the blinding_monitoring group — the
        group's search_hints named neither "Placebo" as a heading nor "placebo" as a
        keyword, and the section's own two sentences contain none of the sixteen
        keywords that were there. PLAN keeps the top `top_k` sections scoring above
        zero, so 6.3 was never sent, and blinding.placebo_matching came back
        not_specified. "Placebo", "placebo" and "matching" are now in that group's
        hints in config/fields.yaml.
        """
        sentence = "A matching placebo will not be provided"
        tasks = [
            t
            for t in plan_extraction([protocol], registry)
            if t.group == "blinding_monitoring"
        ]
        assert tasks, "the blinding_monitoring group has at least one task"

        excerpts = [build_excerpt(task, protocol) for task in tasks]
        assert any(sentence in e for e in excerpts), (
            "no blinding_monitoring excerpt contains the placebo sentence; "
            f"{len(tasks)} task(s), windows {[t.page_window for t in tasks]}"
        )

    @pytest.mark.parametrize(
        ("sentence", "section"),
        [
            ("Unblinded Pharmacist or their designee", "5 SUBJECT SCREENING AND RANDOMIZATION"),
            (
                "Access to the study drug should be strictly limited to the "
                "Unblinded Pharmacy Staff.",
                "6.2 Shipping, Storage",
            ),
            (
                "The Unblinded Pharmacy Staff will obtain the treatment assignment",
                "6.5.1 Study Drug",
            ),
        ],
    )
    def test_the_unblinded_staff_sentences_reach_the_blinding_group(
        self, protocol: Document, registry, sentence: str, section: str
    ) -> None:  # type: ignore[no-untyped-def]
        """The eviction CLAUDE.md item 4c records, now closed by `top_k: 7`.

        Adding the heading "Placebo" to this group on 2026-10-02 raised section
        "6.3 Placebo" into the top three and pushed these two sections out of it.
        blinding.unblinded_staff_stated fell from four records to one, that one
        answered not_specified, and GATE confirmed it at 0.85 — a wrong answer with
        nothing flagging it, which is worse than the needs_review it replaced.

        Both sentences are on pages 52 to 54 and sit in sections ranked 4th and 7th
        for this group, which is where the group's `top_k: 7` comes from. A hint
        added to this group in future can evict them again without failing any
        other test, so this one is parametrised per sentence to say which went.
        """
        tasks = [
            t
            for t in plan_extraction([protocol], registry)
            if t.group == "blinding_monitoring"
        ]
        assert tasks, "the blinding_monitoring group has at least one task"

        excerpts = [_flat(build_excerpt(task, protocol)) for task in tasks]
        assert any(_flat(sentence) in e for e in excerpts), (
            f"no blinding_monitoring excerpt contains {sentence!r} from section "
            f"{section!r}; {len(tasks)} task(s), windows "
            f"{[t.page_window for t in tasks]}. Has a search hint evicted it, or "
            "has top_k been lowered?"
        )

    def test_a_task_window_can_span_a_gap_but_its_excerpt_does_not(
        self, protocol: Document, registry
    ) -> None:
        """page_window is the first and last page the chosen sections touch.

        When the chosen sections are not next to each other the window spans the
        pages between them, and the text of those pages is deliberately not in the
        excerpt. Quote validation compares against the excerpt, so a value from a
        page inside the window but outside every chosen section cannot survive.
        """
        tasks = plan_extraction([protocol], registry)
        spanning = [
            task
            for task in tasks
            if len(task.section_ids) > 1
            and task.page_window[1] - task.page_window[0] + 1
            > sum(
                section.page_end - section.page_start + 1
                for section in protocol.sections
                if section.id in set(task.section_ids)
            )
        ]
        assert spanning, "at least one task's chosen sections are not adjacent"

        for task in spanning:
            excerpt = build_excerpt(task, protocol)
            chosen_pages = {
                page
                for section in protocol.sections
                if section.id in set(task.section_ids)
                for page in range(section.page_start, section.page_end + 1)
            }
            for page in range(task.page_window[0], task.page_window[1] + 1):
                if page not in chosen_pages:
                    assert f"--- Page {page} ---" not in excerpt

    def test_the_prompt_and_quote_validation_see_the_same_text(
        self, protocol: Document, registry
    ) -> None:  # type: ignore[no-untyped-def]
        """One excerpt function, so the model cannot be shown text the validator
        does not have. `extract/__init__.py` calls build_excerpt separately from
        build_extract_prompt; these two must not drift apart."""
        task = plan_extraction([protocol], registry)[0]

        excerpt = build_excerpt(task, protocol)
        human = build_extract_prompt(task, protocol, registry)[1].content

        assert excerpt
        assert excerpt in human

    def test_tasks_name_their_sections_and_stay_in_bounds(
        self, protocol: Document, registry
    ) -> None:  # type: ignore[no-untyped-def]
        tasks = plan_extraction([protocol], registry)
        known = {section.id for section in protocol.sections}
        last_page = max(protocol.page_texts)

        for task in tasks:
            assert task.section_ids
            assert set(task.section_ids) <= known
            assert 1 <= task.page_window[0] <= task.page_window[1] <= last_page


@pytest.mark.slow
class TestBothDocuments:
    def test_one_extraction_call_per_group_unless_it_did_not_fit(
        self, protocol: Document, synthetic_rfp: Document, registry
    ) -> None:  # type: ignore[no-untyped-def]
        """Stage 2 must not multiply the number of model calls.

        Every task beyond one per (document, group) exists because the chosen
        sections did not fit a single extraction call, which is the same reason
        PLAN split a window before 2026-10-02.
        """
        tasks = plan_extraction([protocol, synthetic_rfp], registry)
        pairs = {(task.doc_id, task.group) for task in tasks}

        assert pairs == {
            (doc_id, group.id)
            for doc_id in ("doc-protocol", "doc-rfp")
            for group in registry.groups
        }
        for doc_id, group in pairs:
            same = [t for t in tasks if (t.doc_id, t.group) == (doc_id, group)]
            if len(same) > 1:
                assert all((t.budget_tokens or 0) > 0 for t in same)

    def test_an_unmatched_task_is_never_produced(
        self, protocol: Document, synthetic_rfp: Document, registry
    ) -> None:  # type: ignore[no-untyped-def]
        """Every task's excerpt has text in it. An empty excerpt would be a model
        call that cannot produce anything and cannot fail visibly."""
        for doc in (protocol, synthetic_rfp):
            for task in plan_extraction([doc], registry):
                assert build_excerpt(task, doc).strip(), f"{task.group} {task.page_window}"


def test_a_task_naming_a_section_the_document_lacks_is_skipped(
    synthetic_rfp: Document,
) -> None:
    """A hand-built task cannot crash EXTRACT by naming an unknown section."""
    task = ExtractionTask(
        doc_id=synthetic_rfp.id,
        group="study_design",
        page_window=(1, 6),
        section_ids=["doc-rfp:s999"],
    )
    assert build_excerpt(task, synthetic_rfp) == ""
