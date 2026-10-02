"""The one question MARK_OTHER_STUDY asks a model, and the shape of the answer.

Stage 4 of `docs/PLAN_2026-10-02.md`. The rules in the system prompt are
`docs/ANALYST_PROCEDURE_PROTOCOL.md` section 8, which is Angus Gray describing
how he tells the study he is costing from the ones a protocol mentions in
passing.

**The subject test, added 2026-10-02 after live run `r-20261002-213531-stage4`.**
That run's five `mixed` removals included three sentences about this study: the
plan to pool this study's own serum samples in a population PK analysis
(synopsis p.24, section 3.4.1.4.8.5 p.46 and section 10.5 p.97), this study's
own progression-confirmation procedure (Appendix 1 p.109) and this study's own
0.03 ng/mL stratification threshold (Appendix 2 p.110). Each names outside work
and the model read "mentions another study" as "is about another study". The
prompt now says to judge a sentence by its subject, and carries those sentences
as worked examples of what to keep. The sentences are listed in this plan's
stage 4 test so a later run cannot lose the check.
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

A passage describes a DIFFERENT study when the passage is itself about that \
other study — when it:
- describes a study that is ongoing or already completed,
- gives a study number other than this study's,
- gives a phase or a design different from this study's,
- reports results, enrolment counts or a data cutoff date that have already \
happened.

Prior drug experience, nonclinical work and published literature are about \
different studies when the passage is reporting what those studies did or \
found, even when they are about the same drug.

JUDGE A SENTENCE BY WHAT IT IS ABOUT, NOT BY WHAT IT MENTIONS. A sentence \
belongs to THIS study whenever this study is its subject, however many other \
studies, authors or publications the sentence names. A sentence belongs to THIS \
study when it:
- says what will be done with this study's own samples, data or measurements, \
including pooling them with samples or data from other studies,
- states a procedure, assessment, visit or judgement this study's \
investigators or staff will carry out,
- states a number this study will use — a threshold, a limit, a criterion — \
even when it cites the paper the number was taken or adapted from,
- gives the source of one of this study's own tables, values or criteria.

These real sentences were all removed wrongly from a protocol. Every one of \
them belongs to THIS study and must be kept:
- "Serum concentrations from this study will be pooled with data from similar \
samples from other studies in a population PK analysis." — this study's own \
analysis plan.
- "A repeated assessment at an interval that is determined by the investigator \
is required to confirm the progression." — this study's own procedure.
- "Modified from the value of 0.025 ng/mL cited in Kumar et al, to 0.03 ng/mL, \
which is the lowest validated determination for this commercially available \
test." — this study's own threshold.
- "Source: Palladini 2014." and "Modified from Table 2 in Comenzo 2012." — a \
citation for this study's own table.

Answer for each section with one of:
- this_study: every part of the section is about this study.
- other_study: the section as a whole is about a different study.
- mixed: the section is mostly about this study but contains specific sentences \
about a different study.

For `mixed`, and ONLY for `mixed`, also return those sentences in \
`other_study_sentences`, copied character-for-character from the section text. \
Each is checked against the text and a sentence that does not match is kept, not \
removed. Copy whole sentences. Do not paraphrase and do not shorten.

Return a sentence only when that sentence, read on its own, is about the other \
study. If the sentence would still be true and still be needed for this study \
with the other study's name taken out of it, do not return it.

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
