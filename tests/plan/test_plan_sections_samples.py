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
from functools import lru_cache
from pathlib import Path

import pytest

from rfp_intake.domain.registry import get_registry
from rfp_intake.domain.schemas import Document, ExtractionTask
from rfp_intake.extract.prompt import build_excerpt, build_extract_prompt
from rfp_intake.plan import plan_extraction
from rfp_intake.sections import find_sections, section_text

PROTOCOL = "Example protocol 2.pdf"
SYNTHETIC_RFP = "Synthetic_RFP_NEOD001.pdf"

# Long enough to be unmistakably one section's text and not a shared phrase.
LEAK_PROBE_CHARS = 300
MIN_SECTION_CHARS_TO_PROBE = 400


@lru_cache(maxsize=4)
def _document(pdf: Path, doc_id: str, kind: str) -> Document:
    """Parsed and sectioned once per session, as FIND_SECTIONS leaves it."""
    from rfp_intake.ingest.parsers.rung1 import Rung1Parser

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

    def test_the_other_studys_phase_is_not_sent_to_the_phase_group(
        self, protocol: Document, registry
    ) -> None:  # type: ignore[no-untyped-def]
        """Known problem 1: study.phase read the phase of a referenced study.

        Section 1.3.2 "Clinical Experience" describes study NEOD001-001. PLAN no
        longer chooses it for the phase_population group, and the one-page margin
        that used to drag it in with its neighbour on page 39 is gone, so its text
        does not reach that group's extraction call at all.

        This is not the whole of known problem 1. Another section can still
        mention another study, which is what the MARK_OTHER_STUDY node in stage 4
        of docs/PLAN_2026-10-02.md is for.
        """
        tasks = [
            t
            for t in plan_extraction([protocol], registry)
            if t.group == "phase_population"
        ]
        assert tasks, "the phase_population group has at least one task"

        for task in tasks:
            assert "ongoing, open-label" not in build_excerpt(task, protocol)

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
