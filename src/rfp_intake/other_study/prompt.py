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

**The alongside test, added 2026-10-07 (change 6 of that day's review).** Angus
Gray said (call transcript 00:04–00:10) that text about a study the sponsor is
running alongside this one must stay: a sister study, an extension study, or
another study in the same programme that IQVIA is also budgeting. "Study A has
80% site overlap with Study B" is used to apply a cost saving across the two
studies, and removing it loses the saving. The prompt below now removes a
passage only when it is about an EARLIER or COMPLETED study, and keeps a passage
about a study run alongside this one, a follow-on or extension study, or a study
sharing sites, staff or patients with this one. Nothing new is extracted from
those passages — a variable for programme details belongs to the two-studies
work, which is deferred.

**Tightened after live run `r-20261008-review2-run1`.** The first version of the
alongside test lost section 1.3.2 "Clinical Experience": the protocol calls the
earlier Phase 1/2 study NEOD001-001 "an ongoing, open-label study", the model
read "ongoing" as "alongside", kept the sentence, and Phase 1/2 reached the
Study phase row as a second value. The prompt now says that being ongoing does
not make a study alongside this one, and that a passage counts as alongside only
when it states a link to how THIS study will be run — a shared site, member of
staff, pharmacy, laboratory, vendor, committee or patient, a roll-over, or a
programme costed together. Reporting what another study found is not such a link.

**Tightened again after live run `r-20261008-review2-run2`.** That run still kept
the section's first sentence — "The safety and tolerability of NEOD001 are being
investigated in an ongoing, open-label, dose escalation Phase 1/2 study (Study
NEOD001-001)…" — with the reason "an ongoing companion study that may be running
alongside this one", so `study.phase` carried Phase 1/2 again. The rules alone
were not enough, so the prompt now carries that sentence and two others from the
same section as worked examples of text to REMOVE, says that another study's own
design is not a link to this one, and says that a section whose every sentence is
about other studies is `other_study` rather than `mixed`.

**Two of those worked examples were then corrected**, after
`tests/other_study/test_other_study_real_document.py` compared them against the
real section 1.3.2 instead of against a hand-written copy of it. One ended
"…treated in the Escalation Phase of the study." where the document goes on "…and
42 subjects were enrolled and treated in the Expansion Phase.", so the example
was a shortened sentence in a prompt that forbids shortening. The other wrote
"Investigator's Brochure" with a straight apostrophe where the document has a
typographic one, and `sections.locate_text` folds whitespace and case but not
punctuation — a model copying that example would have had its sentence rejected
as unverified and the passage KEPT. Both now match the document
character-for-character, and the test fails if either drifts again.
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

Remove a passage ONLY when it is about an EARLIER or COMPLETED study — a study \
that has already been run. A passage is about an earlier or completed study \
when it:
- describes a study that finished before this one, or that is reported in the \
past tense,
- reports results, enrolment counts or a data cutoff date that have already \
happened,
- gives a study number other than this study's AND reports what that study \
found,
- gives a phase or a design different from this study's AND reports what that \
study found,
- is a "clinical experience", "prior experience", "previous studies" or \
"background" passage reporting what has been seen with the drug so far.

"ONGOING" DOES NOT MAKE A STUDY ALONGSIDE THIS ONE. An earlier study of the \
same drug is often still recruiting when this protocol is written, and a \
passage reporting what that study has found so far is still earlier-study \
text. Remove it.

Prior drug experience, nonclinical work and published literature are about \
earlier studies when the passage is reporting what those studies did or \
found, even when they are about the same drug and even when that work is still \
running.

KEEP every passage about a study that is being run ALONGSIDE this one. The team \
is costing this study next to the sponsor's other current work, and a shared \
site, a shared member of staff or a shared patient is a saving the budget has to \
show. So keep a passage when it:
- describes a companion, sister, parallel or partner study the sponsor is \
running at the same time as this one,
- describes a follow-on, extension, rollover, open-label extension or \
continuation study that patients from this study move into,
- describes another study in the same programme, platform or master protocol as \
this one,
- says that this study shares, or will share, sites, investigators, staff, \
pharmacies, patients, laboratories, vendors or committees with another study, \
including a figure for how much is shared.

A passage is ALONGSIDE this one ONLY when it states a link to how THIS study \
will be run: a shared site, member of staff, pharmacy, laboratory, vendor, \
committee or patient, patients moving from this study into another study, or a \
programme whose studies are being costed together. A passage that only reports \
what another study did or found states no such link, however recently that \
study ran and whatever it is called.

A passage like "This study will share approximately 80% of its sites with the \
companion study NEOD001-CL003" is KEPT. It names another study, but it states \
something about how this study will be run, and the overlap is exactly what the \
budget needs. The same goes for a sentence naming an extension study this \
study's patients will roll into.

When a passage states a link of that kind and you cannot tell whether the other \
study is alongside this one or unrelated, KEEP it. Where no such link is stated \
and the passage reports what the other study did or found, remove it.

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
- "This study will share approximately 80% of its sites with the companion \
study NEOD001-CL003." — a study run alongside this one, and a saving the budget \
has to show.

These real sentences must all be REMOVED. Each names another study and none of \
them says anything about how this study will be run:
- "The safety and tolerability of NEOD001 are being investigated in an ongoing, \
open-label, dose escalation Phase 1/2 study (Study NEOD001-001) of the IV \
administration of single-agent NEOD001 in subjects with AL amyloidosis in the \
US." — this gives another study's own design and nothing else. "Ongoing" and \
"the same drug" do not make it alongside this one, and the Phase 1/2 in it is \
not this study's phase. Remove it.
- "As of an interim analysis with a data cutoff date of 30 September 2015, 27 \
subjects were enrolled and treated in the Escalation Phase of the study and 42 \
subjects were enrolled and treated in the Expansion Phase." — another study's \
enrolment.
- "Further details can be found in the latest edition of the NEOD001 \
Investigator’s Brochure." — a pointer to the earlier work, carrying nothing \
this study needs.

ANOTHER STUDY'S OWN DESIGN IS NOT A LINK TO THIS ONE. A phase, a blinding, a \
dose-escalation scheme, a population, a country or a sample size given for \
another study is that study's design. It belongs to that study, and a reader \
who keeps it will read it as this study's own.

WHERE EVERY SENTENCE IN A SECTION IS ABOUT OTHER STUDIES, ANSWER other_study, \
NOT mixed. A "Clinical Experience", "Prior Experience" or "Background" section \
that opens by naming the earlier study and then reports its results is \
other_study as a whole. Answer `mixed` only when the section has sentences \
about THIS study in it that must be kept.

Answer for each section with one of:
- this_study: every part of the section is to be kept — it is about this study, \
or about a study being run alongside this one.
- other_study: the section as a whole is about an earlier or completed study.
- mixed: the section is mostly to be kept but contains specific sentences about \
an earlier or completed study.

For `mixed`, and ONLY for `mixed`, also return those sentences in \
`other_study_sentences`, copied character-for-character from the section text. \
Each is checked against the text and a sentence that does not match is kept, not \
removed. Copy whole sentences. Do not paraphrase and do not shorten.

Return a sentence only when that sentence, read on its own, is about an earlier \
or completed study. If the sentence would still be true and still be needed for \
this study with the other study's name taken out of it, do not return it.

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
