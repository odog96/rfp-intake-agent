"""SET_ASIDE_SECTIONS — drop the sections an analyst ignores. Pure Python, no LLM.

Stage 3 of `docs/PLAN_2026-10-02.md`. `docs/ANALYST_PROCEDURE_PROTOCOL.md`
section 4 lists two dozen kinds of section Angus Gray skips entirely when
costing a protocol. Reading them costs tokens and, worse, invites the extraction
model to answer a question from the wrong place.

Four rules, in this order:

1. **Only remove what is positively listed.** A heading nobody anticipated
   survives. The opposite design — keep an expected list, drop the rest — would
   hide exactly the unbudgeted cost this tool exists to find.
2. **A listed section takes its children with it**, down to the next heading at
   the same or a higher level, because "4.3 Inclusion Criteria" means the whole
   of 4.3.
3. **`keep_if_contains` overrides both.** A section whose text names unblinded
   staff, an interim analysis, a schedule of activities or a case report form
   count is kept however its heading reads. Keeping a section nobody needed
   costs tokens; dropping one that held a cost driver costs a number nobody
   budgets for, so the asymmetry is deliberate.
4. **A document with one section is left alone.** That is FIND_SECTIONS rule 2,
   `whole_document`: there is nothing to set aside, and the only section is the
   whole document.

Removed sections come off `Document.sections`, so PLAN never scores them, and
are recorded on `RunState.set_aside` for `extraction.json`.
"""

from __future__ import annotations

from typing import Any

import structlog

from rfp_intake.domain.schemas import Document, RunError, RunState, Section, SetAsideSection
from rfp_intake.domain.section_policy import SectionsPolicy, get_sections_policy, strip_heading
from rfp_intake.sections import FRONT_MATTER_HEADING, WHOLE_DOCUMENT_HEADING, section_text

logger = structlog.get_logger()

# Synthetic headings FIND_SECTIONS invents, not headings the document wrote.
# "Front matter" is the text before the first heading — a title page, but also
# anything a bookmark missed — and "Whole document" is every page of a rule 2
# document. Neither can be matched against a list of real section names, and
# dropping either would throw away text on the strength of a word this codebase
# chose itself.
NEVER_SET_ASIDE = frozenset({FRONT_MATTER_HEADING, WHOLE_DOCUMENT_HEADING})

# FIND_SECTIONS rules that produce exactly one section covering the document.
_WHOLE_DOCUMENT_SOURCES = frozenset({"whole_document", "whole_document_fallback"})


def set_aside_sections(
    doc: Document, policy: SectionsPolicy
) -> tuple[list[Section], list[SetAsideSection]]:
    """Split one document's sections into the ones to keep and the ones to drop.

    Returns (kept, dropped). Pure: it does not touch `doc`.
    """
    if doc.section_source in _WHOLE_DOCUMENT_SOURCES or len(doc.sections) <= 1:
        return list(doc.sections), []

    kept: list[Section] = []
    dropped: list[SetAsideSection] = []

    # The level of the outermost section currently being dropped, and its id, so
    # a child can say which parent took it. Sections arrive in document order
    # from `_sections_from_boundaries`, so one forward pass is enough: nesting
    # only ever deepens until a heading at the same or a higher level closes it.
    parent_level: int | None = None
    parent_id: str | None = None

    for section in doc.sections:
        if parent_level is not None and section.level <= parent_level:
            parent_level = None
            parent_id = None

        if section.heading in NEVER_SET_ASIDE:
            kept.append(section)
            continue

        inherited = parent_level is not None
        matched = None if inherited else policy.matching_set_aside(section.heading)
        if not inherited and matched is None:
            kept.append(section)
            continue

        # An index section goes whatever its text says, because its text is the
        # document's own headings. `rescuing_phrase` reads the body only: the
        # heading already had its say, and "8.1 Emergency Unblinding" would
        # otherwise rescue itself with the phrase `unblinded`.
        rescue = (
            None
            if policy.is_always_set_aside(section.heading)
            else policy.rescuing_phrase(
                strip_heading(section_text(doc, section), section.heading)
            )
        )
        if rescue is not None:
            # Kept, but a rescued child does not reopen its parent: the parent's
            # other children still go. A rescued *parent* never opened a drop in
            # the first place, so its children are judged on their own headings.
            logger.info(
                "section_kept_despite_heading",
                doc_id=doc.id,
                section_id=section.id,
                heading=section.heading,
                matched=matched,
                via_parent=parent_id,
                kept_by=rescue,
            )
            kept.append(section)
            continue

        dropped.append(
            SetAsideSection(
                doc_id=doc.id,
                section_id=section.id,
                heading=section.heading,
                page_start=section.page_start,
                page_end=section.page_end,
                matched=matched,
                via_parent=parent_id if inherited else None,
            )
        )
        if not inherited:
            parent_level = section.level
            parent_id = section.id

    return kept, dropped


def set_aside_sections_node(state: RunState) -> dict[str, Any]:
    """SET_ASIDE_SECTIONS graph node. Pure Python — no model call, no I/O."""
    policy = get_sections_policy()
    updated: list[Document] = []
    all_dropped: list[SetAsideSection] = []
    errors: list[RunError] = []

    for doc in state.documents:
        try:
            kept, dropped = set_aside_sections(doc, policy)
        except Exception as e:  # noqa: BLE001 - one bad document must not end the run
            logger.error("set_aside_sections_failed", doc_id=doc.id, error=str(e))
            errors.append(
                RunError(
                    node="SET_ASIDE_SECTIONS",
                    task_id=doc.id,
                    error=f"Could not set sections aside for {doc.id}: {e}",
                    kind="validation",
                )
            )
            updated.append(doc)
            continue

        if not kept and dropped:
            # Every section matched. Almost certainly the policy, not the
            # document: PLAN has nothing to score and the run would report every
            # field as not found with no hint why. Keep the document whole and
            # say so loudly instead.
            logger.error(
                "set_aside_would_empty_document",
                doc_id=doc.id,
                sections=len(doc.sections),
            )
            errors.append(
                RunError(
                    node="SET_ASIDE_SECTIONS",
                    task_id=doc.id,
                    error=(
                        f"Every one of {doc.id}'s {len(doc.sections)} sections matched "
                        f"config/sections.yaml, so none were set aside. Check the policy."
                    ),
                    kind="validation",
                )
            )
            updated.append(doc)
            continue

        doc.sections = kept
        all_dropped.extend(dropped)
        logger.info(
            "sections_set_aside",
            run_id=state.run_id,
            doc_id=doc.id,
            kept=len(kept),
            set_aside=len(dropped),
            source=doc.section_source,
        )
        updated.append(doc)

    return {"documents": updated, "set_aside": all_dropped, "errors": errors}


__all__ = [
    "NEVER_SET_ASIDE",
    "set_aside_sections",
    "set_aside_sections_node",
]
