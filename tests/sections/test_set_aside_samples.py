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
from rfp_intake.sections import find_sections
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

    def test_nothing_is_lost_or_counted_twice(
        self, protocol: Document, policy: SectionsPolicy
    ) -> None:
        kept, dropped = set_aside_sections(protocol, policy)
        ids = [s.id for s in kept] + [s.section_id for s in dropped]
        assert sorted(ids) == sorted(s.id for s in protocol.sections)
        assert len(set(ids)) == len(ids)


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
