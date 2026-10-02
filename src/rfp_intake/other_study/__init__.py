"""MARK_OTHER_STUDY — take out text that describes a different study.

Stage 4 of `docs/PLAN_2026-10-02.md`, and the fix for the oldest known problem in
`CLAUDE.md`: `study.phase` came back as the phase of an earlier study the
protocol mentions in passing. In baseline run `r-20261002-200521-stage2` the
spurious `phase_1_2` row was still there, at confidence 0.8 from page 40 —
section 1.3.2 "Clinical Experience", which describes study NEOD001-001, not the
study being costed.

**Why this needs a model and the two stages before it did not.** FIND_SECTIONS
and SET_ASIDE_SECTIONS both decide by heading. "1.3.2 Clinical Experience" is an
ordinary heading in an ordinary place, and the sentences under it read like any
other description of a trial. Only the content says it is about a different
study. Keyword matching on study numbers was tried and failed, because the
protocol prints its own number beside the other one.

One model call per batch of sections, under the same token budget PLAN uses, so a
137-page protocol costs roughly ten calls rather than one per section.

**Nothing is removed on the model's word alone.** A sentence the model returns
must be found in the section's own text, by `rfp_intake.sections.locate_text`,
exactly as EXTRACT validates a quote. A sentence that cannot be found is kept and
the failure is logged, because the direction of the mistake matters: keeping a
passage costs tokens, removing one wrongly loses a number nobody budgets for.

**Sections are not deleted, their text is.** `Document.removed` holds the spans
and `rfp_intake.sections.section_page_texts` cuts them out, so PLAN's scoring and
EXTRACT's excerpt both stop seeing the text from one place. A section whose text
is entirely removed then scores zero in PLAN and is never chosen. Keeping the
section in `Document.sections` means `extraction.json` and the report can still
say which heading a removal came from.
"""

from __future__ import annotations

from typing import Any

import structlog

from rfp_intake.domain.budget import DEFAULT_TOKEN_BUDGET, estimate_text_tokens
from rfp_intake.domain.schemas import (
    Document,
    RemovedPassage,
    RunError,
    RunState,
    Section,
    TextSpan,
)
from rfp_intake.llm.provider import LLMRole, get_llm
from rfp_intake.llm.structured import get_structured_output_for_role
from rfp_intake.other_study.prompt import (
    OtherStudyBatch,
    SectionVerdict,
    build_other_study_prompt,
    describe_identity,
)
from rfp_intake.sections import locate_text, section_page_texts

logger = structlog.get_logger()

# Annotated, not bare: get_llm takes the closed LLMRole literal, so a role name
# that config/models.yaml does not define is a type error here rather than a
# KeyError part-way into a run.
ROLE: LLMRole = "other_study_check"

# A section shorter than this is not worth a verdict. A heading with one line
# under it cannot state a different study's design, and including them would
# spend most of the batch budget on cross-references and page furniture.
MIN_SECTION_CHARS = 200


def study_identity(documents: list[Document]) -> str:
    """The lines naming the study being costed, for the prompt.

    The protocol's own protocol number wins when the documents disagree, which is
    what `docs/PLAN_2026-10-02.md` stage 4 asks for: an RFP quotes the protocol
    number and sometimes quotes it wrongly, while the protocol is the document
    that defines it.
    """
    protocol = next((d for d in documents if d.kind in ("protocol", "amendment")), None)
    ordered = [protocol, *documents] if protocol else list(documents)
    protocol_id = next((d.protocol_id for d in ordered if d and d.protocol_id), None)
    title = next((d.title for d in ordered if d and d.title), None)
    return describe_identity(protocol_id, title)


def batch_sections(
    doc: Document, budget: int = DEFAULT_TOKEN_BUDGET
) -> list[list[tuple[Section, str]]]:
    """Group a document's sections into batches that fit one call.

    A section longer than the whole budget gets a batch of its own rather than
    being split: half a section is not something a model can judge, and a verdict
    on half of one would be recorded against all of it.

    Sections shorter than `MIN_SECTION_CHARS` are left out entirely and never get
    a verdict, so they are never removed. `docs/PLAN_2026-10-02.md` says not to
    pre-filter with keywords and this does not — it filters on length only, which
    cannot single out a topic.
    """
    batches: list[list[tuple[Section, str]]] = []
    current: list[tuple[Section, str]] = []
    used = 0

    for section in doc.sections:
        text = "".join(
            section_page_texts(doc, section)[page]
            for page in sorted(section_page_texts(doc, section))
        )
        if len(text.strip()) < MIN_SECTION_CHARS:
            continue
        cost = estimate_text_tokens(text)
        if current and used + cost > budget:
            batches.append(current)
            current = []
            used = 0
        current.append((section, text))
        used += cost

    if current:
        batches.append(current)
    return batches


def _whole_section_spans(doc: Document, section: Section) -> list[TextSpan]:
    """Spans covering every page of one section, cut at the section's own bounds."""
    spans: list[TextSpan] = []
    for page in range(section.page_start, section.page_end + 1):
        start = section.start_offset if page == section.page_start else 0
        end = section.end_offset if page == section.page_end else None
        spans.append(TextSpan(page=page, start_offset=start, end_offset=end))
    return spans


def locate_sentence(doc: Document, section: Section, sentence: str) -> TextSpan | None:
    """Where one sentence sits in one section, or None if it is not there.

    Searched page by page, because offsets are into `Document.page_texts[page]`.
    A sentence broken across a page boundary is therefore not found and not
    removed — rare, logged, and the safe direction.
    """
    pages = section_page_texts(doc, section)
    for page in sorted(pages):
        found = locate_text(pages[page], sentence)
        if found is None:
            continue
        # `pages[page]` is already cut at the section's start on its first page,
        # so an offset into it has to be shifted back into page coordinates.
        shift = section.start_offset if page == section.page_start else 0
        return TextSpan(page=page, start_offset=found[0] + shift, end_offset=found[1] + shift)
    return None


def removals_for_verdict(
    doc: Document, section: Section, verdict: SectionVerdict
) -> tuple[RemovedPassage | None, list[str]]:
    """One section's verdict turned into a removal, plus any rejected sentences.

    Returns (passage, unverified). `passage` is None when nothing is removed —
    `this_study`, or `mixed` with no sentence that could be found in the text.
    """
    if verdict.verdict == "this_study":
        return None, []

    if verdict.verdict == "other_study":
        pages = section_page_texts(doc, section)
        return (
            RemovedPassage(
                doc_id=doc.id,
                section_id=section.id,
                heading=section.heading,
                page_start=section.page_start,
                page_end=section.page_end,
                verdict="other_study",
                reason=verdict.reason,
                text="".join(pages[page] for page in sorted(pages)),
                spans=_whole_section_spans(doc, section),
            ),
            [],
        )

    spans: list[TextSpan] = []
    kept_text: list[str] = []
    unverified: list[str] = []
    for sentence in verdict.other_study_sentences:
        span = locate_sentence(doc, section, sentence)
        if span is None:
            unverified.append(sentence)
            continue
        spans.append(span)
        kept_text.append(sentence)

    if not spans:
        return None, unverified

    return (
        RemovedPassage(
            doc_id=doc.id,
            section_id=section.id,
            heading=section.heading,
            page_start=min(s.page for s in spans),
            page_end=max(s.page for s in spans),
            verdict="mixed",
            reason=verdict.reason,
            text="\n".join(kept_text),
            spans=spans,
        ),
        unverified,
    )


def mark_other_study(doc: Document, identity: str, structured: Any) -> list[RemovedPassage]:
    """Judge one document's sections and return what should be removed.

    Pure with respect to `doc`: the caller assigns `doc.removed`. One failed batch
    does not stop the others — the sections in it simply keep all their text,
    which is the same outcome as the node not running.
    """
    removed: list[RemovedPassage] = []

    for batch in batch_sections(doc):
        by_id = {section.id: section for section, _ in batch}
        messages = build_other_study_prompt(
            identity, [(section.id, section.heading, text) for section, text in batch]
        )
        result: OtherStudyBatch = structured.extract(OtherStudyBatch, messages)

        for verdict in result.sections:
            section = by_id.get(verdict.section_id)
            if section is None:
                # The model answered about a section that was not in this batch.
                # Acting on it would remove text nobody asked about.
                logger.warning(
                    "other_study_unknown_section",
                    doc_id=doc.id,
                    section_id=verdict.section_id,
                    batch=sorted(by_id),
                )
                continue

            passage, unverified = removals_for_verdict(doc, section, verdict)
            for sentence in unverified:
                logger.warning(
                    "other_study_sentence_not_found",
                    doc_id=doc.id,
                    section_id=section.id,
                    heading=section.heading,
                    sentence=sentence[:200],
                )
            if passage is None:
                continue
            removed.append(passage)
            logger.info(
                "other_study_removed",
                doc_id=doc.id,
                section_id=section.id,
                heading=section.heading,
                verdict=passage.verdict,
                chars=len(passage.text),
                reason=passage.reason,
            )

    return removed


def mark_other_study_node(state: RunState) -> dict[str, Any]:
    """MARK_OTHER_STUDY graph node. One model call per batch of sections."""
    llm = get_llm(ROLE)
    structured = get_structured_output_for_role(llm, ROLE)
    identity = study_identity(state.documents)

    updated: list[Document] = []
    all_removed: list[RemovedPassage] = []
    errors: list[RunError] = []

    for doc in state.documents:
        try:
            removed = mark_other_study(doc, identity, structured)
        except Exception as e:  # noqa: BLE001 - one document must not end the run
            logger.error("mark_other_study_failed", doc_id=doc.id, error=str(e))
            errors.append(
                RunError(
                    node="MARK_OTHER_STUDY",
                    task_id=doc.id,
                    error=f"Could not check {doc.id} for other studies: {e}",
                )
            )
            updated.append(doc)
            continue

        if _removes_everything(doc, removed):
            # Every section judged to be about a different study. That is a
            # broken identity or a broken call, not a document: PLAN would have
            # no text to score and every field would come back not found.
            logger.error(
                "other_study_would_empty_document",
                doc_id=doc.id,
                sections=len(doc.sections),
                removed=len(removed),
            )
            errors.append(
                RunError(
                    node="MARK_OTHER_STUDY",
                    task_id=doc.id,
                    error=(
                        f"Every section of {doc.id} was judged to describe a different "
                        f"study, so nothing was removed. Check the study identity in the "
                        f"prompt."
                    ),
                    kind="validation",
                )
            )
            updated.append(doc)
            continue

        doc.removed = removed
        all_removed.extend(removed)
        logger.info(
            "other_study_checked",
            run_id=state.run_id,
            doc_id=doc.id,
            sections=len(doc.sections),
            removed=len(removed),
        )
        updated.append(doc)

    return {"documents": updated, "removed_passages": all_removed, "errors": errors}


def _removes_everything(doc: Document, removed: list[RemovedPassage]) -> bool:
    """True when every section with text in it was removed whole."""
    whole = {p.section_id for p in removed if p.verdict == "other_study"}
    with_text = {
        section.id
        for section in doc.sections
        if any(text.strip() for text in section_page_texts(doc, section).values())
    }
    return bool(with_text) and with_text <= whole


__all__ = [
    "MIN_SECTION_CHARS",
    "ROLE",
    "batch_sections",
    "locate_sentence",
    "mark_other_study",
    "mark_other_study_node",
    "removals_for_verdict",
    "study_identity",
]
