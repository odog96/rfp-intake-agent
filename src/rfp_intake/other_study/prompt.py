"""The one question MARK_OTHER_STUDY asks a model, and the shape of the answer.

Stage 4 of `docs/PLAN_2026-10-02.md`. The rules in the system prompt are
`docs/ANALYST_PROCEDURE_PROTOCOL.md` section 8, which is Angus Gray describing
how he tells the study he is costing from the ones a protocol mentions in
passing.
"""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

OTHER_STUDY_SYSTEM_PROMPT = """\
You are reading sections of a clinical study document for a delivery-budgeting \
team. The team is costing ONE study. The document also mentions OTHER studies — \
earlier studies of the same drug, studies by other sponsors, published \
literature — and a cost taken from one of those is a wrong cost.

For each section you are given, say whether it describes THIS study or a \
DIFFERENT study.

THIS STUDY is identified as:
{identity}

A passage describes a DIFFERENT study when it:
- describes a study that is ongoing or already completed,
- gives a study number other than this study's,
- gives a phase or a design different from this study's,
- reports results, enrolment counts or a data cutoff date that have already \
happened.

Prior drug experience, nonclinical work and published literature are about \
different studies even when they are about the same drug.

A passage still describes THIS study when it only refers to another study as \
background without taking any fact from it, and when it states this study's own \
design, population, visits, drug handling or procedures.

Answer for each section with one of:
- this_study: every part of the section is about this study.
- other_study: the section as a whole is about a different study.
- mixed: the section is mostly about this study but contains specific sentences \
about a different study.

For `mixed`, and ONLY for `mixed`, also return those sentences in \
`other_study_sentences`, copied character-for-character from the section text. \
Each is checked against the text and a sentence that does not match is kept, not \
removed. Copy whole sentences. Do not paraphrase, do not shorten, and do not \
return a sentence that states a fact about this study.

Give a `reason` of one sentence for every section, including `this_study`.

Judge only what the text says. If you cannot tell, answer this_study — keeping a \
passage costs some tokens, and removing one wrongly loses a number the budget \
needs.
"""


class SectionVerdict(BaseModel):
    """The model's answer about one section."""

    section_id: str
    verdict: Literal["this_study", "other_study", "mixed"]
    reason: str = ""
    # Only read when verdict is "mixed". Each is validated against the section's
    # own text before anything is removed — see `other_study/__init__.py`.
    other_study_sentences: list[str] = Field(default_factory=list)


class OtherStudyBatch(BaseModel):
    """One call's answer: a verdict for each section in the batch."""

    sections: list[SectionVerdict] = Field(default_factory=list)


def describe_identity(protocol_id: str | None, title: str | None) -> str:
    """The lines naming the study being costed, for the system prompt.

    Both can be missing: CLASSIFY extracts `protocol_id` from the first pages and
    a document may not show one. The model is told so explicitly rather than
    given an empty label, because an empty "Protocol number:" line reads like a
    study with no number and invites a match against any number in the text.
    """
    lines: list[str] = []
    if protocol_id:
        lines.append(f"- Protocol number: {protocol_id}")
    if title:
        lines.append(f"- Title: {title}")
    if not lines:
        return (
            "- The document does not state a protocol number or title on its "
            "first pages. Judge by design and by whether a passage reports "
            "something that has already happened."
        )
    return "\n".join(lines)


def build_other_study_prompt(
    identity: str, sections: list[tuple[str, str, str]]
) -> list[BaseMessage]:
    """The messages for one batch.

    `sections` is (section_id, heading, text). The ids are echoed back by the
    model, so they are printed in full rather than renumbered per batch: a
    renumbered id that comes back wrong would silently point at another section.
    """
    parts = [
        f"--- Section {section_id} ---\nHeading: {heading}\n\n{text}"
        for section_id, heading, text in sections
    ]
    return [
        SystemMessage(content=OTHER_STUDY_SYSTEM_PROMPT.format(identity=identity)),
        HumanMessage(content="\n\n".join(parts)),
    ]


__all__ = [
    "OTHER_STUDY_SYSTEM_PROMPT",
    "OtherStudyBatch",
    "SectionVerdict",
    "build_other_study_prompt",
    "describe_identity",
]
