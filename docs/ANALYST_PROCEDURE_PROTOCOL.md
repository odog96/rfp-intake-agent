# How a DSB analyst reviews a clinical study protocol for budgeting

**Status: DRAFT, awaiting sign-off by Angus Gray (IQVIA DSB).** Oliver decided on 2026-10-02 that the
build does not wait for that sign-off. Build against this document as written; correct it when Angus
replies.

**Source.** Every rule below comes from Angus Gray's walk-through of `samples/Example protocol 2.pdf`
(protocol NEOD001-CL002, Amendment 3) on the Zoom call of 2026-10-01. The recording's transcript is
`GMT20261001-140359_Recording.transcript.vtt`, which is held in the claude.ai project, not in this
repository. The time in brackets after each rule is where Angus says it in that transcript
(hours:minutes:seconds), so any rule can be checked against his own words.

**Scope.** This covers a protocol only. Angus said an RFP that is not a protocol is reviewed
differently [02:08:12]; that walk-through has not happened yet.

**What "ignore" means here.** Ignore means "not needed for the clinical review of the budget." It does
not mean the information is unimportant. Other IQVIA teams use some of it, and they may want different
rules later [01:57:08, 01:58:50].

---

## 1. Before reading: know what kind of document it is

A protocol tells you how the study must be run: the patient population, the dosing, the drug. It
normally does not say how many monitoring visits are needed or how many case report form pages there
are; those usually come from the RFP documents [00:55:52].

But do not skip anything only because "a protocol would not normally contain it." Inexperienced
sponsors put information in unusual places, for example the number of interim monitoring visits inside
what they call a protocol. If it is there, record it [02:10:48].

## 2. Read the title first

The title usually gives most of the study design at once [00:57:47 to 01:01:10]. From the title, record:

- **Study phase.** Phase drives the level of monitoring, how complex the data pages are, and the risk.
  Phase 1 suggests first-in-human, a safety focus, a small population and close monitoring. Phase 3
  suggests plenty of prior data and an efficacy and safety focus [00:58:16].
- **Blinding.** Double-blind, single-blind, blinded, or open-label [00:59:26].
- **Control.** Placebo-controlled, or compared against standard of care [00:59:57].
- **Number of arms.** Arms are the groups patients are randomized into. In this protocol there are two:
  the study drug plus standard of care, and placebo plus standard of care [01:00:10].
- **Patients or healthy volunteers.** "Subjects with light chain amyloidosis" means patients who have
  the disease, so the study does not use healthy volunteers [01:00:29].

When the answer to "healthy volunteers" is no, the report should say no and show the phrase that proves
it, for example "No — subjects with AL amyloidosis" [00:50:20].

## 3. Indication and patient population are two different things

The **indication** is the disease the drug is meant to treat. The **patient population** is who
actually takes part in this study. They are often the same, but not always [01:02:03 to 01:04:41].

Example from Angus: a new headache drug has the indication "acute headache," but its first study may
give it to healthy people without headaches, so that the drug's side effects are not confused with
headache symptoms. Indication: acute headache. Population: healthy volunteers [01:03:16].

In this protocol both are AL amyloidosis.

## 4. Sections to ignore entirely

Skip these sections. Each entry gives where Angus says so.

- Amendment dates and the overview of changes between protocol versions; only the latest version
  matters [01:01:23]
- Sponsor details, sponsor representative and confidentiality statements [01:04:41]
- Repeated title pages [01:01:35]
- Table of contents [01:05:16]
- Secondary and exploratory objectives and endpoints [01:05:55, 01:07:57]
- Stratification factors and adverse-event reporting rules inside the study design section [01:08:35]
- Detailed treatment-cycle descriptions, for this first version of the tool [01:09:33]
- Inclusion and exclusion criteria (eligibility) [01:11:37]
- What the standard of care consists of [01:13:15]
- Glossary and list of abbreviations [01:34:28]
- Introduction, background, rationale, information about the drug's chemistry, and nonclinical
  (animal) safety data [01:35:05]
- Rationale for dose selection [01:37:07]
- Study termination rules [01:38:06]
- Shipping, storage and temperature of the drug, except for the unblinded-staff wording described in
  section 6 below [01:38:44]
- Vial contents and volumes [01:46:03]
- Prior and concomitant medication (any other drug patients take that is not part of the study),
  chemotherapy given as standard of care, withholding of study drug, management of adverse events,
  dose reductions, treatment compliance [01:46:16 to 01:47:47]
- Detailed descriptions of each study procedure and assessment; these are counted from the schedule of
  activities instead [01:47:53, 01:55:30]
- Information about randomization procedures [01:49:00]
- Order of assessments and laboratory evaluation details [01:55:30]
- Emergency unblinding, early treatment discontinuation, early termination, replacement of subjects
  [01:56:07]
- Adverse event (AE) and serious adverse event (SAE) reporting, pregnancy reporting, urgent safety
  measures [01:56:20, 01:58:40]
- Sample size calculation; only the final patient number matters [01:59:33]
- Data monitoring committee, clinical events committee and any other committee [02:00:02]
- Case report form details, unless the text gives a number of case report forms [02:00:16]
- Record retention, quality assurance, subject confidentiality, ethics, informed consent, subject
  compensation, finance, publication, references [02:02:52]
- Example contraception methods, diagnostic criteria, and sample questionnaires in the appendices
  [02:06:25]

## 5. Sections to scan, not skip

These sections are mostly irrelevant but can hide one thing that matters. Read them only for that one
thing.

- **Statistical considerations: look only for interim analyses.** Record whether any interim analysis
  is planned and how many. In this protocol the answer is "no interim analysis is planned"
  [01:19:00, 01:59:05].
- **Drug packaging and storage: look only for unblinded staff.** For example, "access to the study
  drug should be strictly limited to the unblinded pharmacy staff." Sometimes this is the only place
  the document says unblinded staff are needed [01:39:05, 01:40:30].
- **Appendices: look for the schedule of activities.** It is sometimes in an appendix instead of the
  main body [02:03:39].

## 6. The study drug and the placebo

Record:

- **How the drug is given** (oral tablet, oral capsule, injection under the skin, infusion into a
  vein, and so on). It may be stated early, for example in the primary objective, and repeated later.
  Record it wherever it first appears, because a draft protocol may never repeat it
  [01:06:54, 01:12:13].
- **Dosing frequency**, for example "once every 28 days" [01:12:03].
- **Infusion length** only when it spans several days, such as back-to-back 24-hour infusions, because
  then the patient stays on site. The difference between 10 minutes and 2 hours does not matter
  [01:49:40].

**Whether the placebo matches the study drug.** This decides whether unblinded staff are needed, so it
drives cost. Rules [01:13:28 to 01:18:56]:

1. If the study is open-label, whether the placebo matches does not matter. Still record how the drug
   is given, because an infusion takes more monitoring work than a tablet.
2. If the document says whether the placebo matches, use what it says.
3. If the document does not say, and the drug is injected or infused: assume the placebo does **not**
   match, because liquids are hard to colour-match. Unblinded staff are then likely needed.
4. If the document does not say, and the drug is oral: do not assume unblinded monitoring is needed.
   Report that matching is not confirmed and that the drug is oral, and let the analyst decide.
5. "Matching" means matching **as supplied to the site**, in vials or bottles. A sentence like "the
   saline bag will look identical to the drug infusion bag" describes the drug **after** unblinded
   pharmacy staff have prepared it. It does not mean the placebo matches as supplied. This protocol says
   both "a matching placebo will not be provided" and "will look identical"; together they mean the
   supplies do not match and unblinded pharmacy staff are needed [01:40:49 to 01:45:01].
6. If the document says unblinded staff are needed **and** says the placebo matches, flag it as a
   contradiction to clarify with the sponsor. One of the two is often a typo [01:39:42].

## 7. Numbers of sites and subjects

- Record the number of sites and the number of subjects [01:10:07].
- "Center" and "site" mean the same thing. "Multi-center" only means more than one site; ignore the
  phrase itself [01:10:13].
- Subjects per arm can be worked out from the total and the randomization ratio. 260 subjects
  randomized 1:1 gives 130 per arm [01:10:43].
- When the same number appears again later, read it to confirm it is consistent. Agreement raises
  confidence [01:37:40].

## 8. Another study described inside this protocol

This is the mistake the tool made in run `r-20261001-032936`, which reported the study phase as
"Phase 1/2" [00:49:00 to 00:53:58, 01:35:05 to 01:37:05].

Section 1.3.2 ("Clinical Experience") describes a different study: "an ongoing, open-label, dose
escalation Phase 1/2 study (Study NEOD001-001)", with "an interim analysis with a data cutoff date of
30 September 2015." None of those values belong to the study being budgeted.

How an analyst recognises such a passage:

- The study is described as **ongoing** or already completed. A study already running cannot be the
  one the sponsor is asking IQVIA to budget for.
- It has a **different study number** from the title page.
- It has a **different design or phase** from the title page (open-label against double-blind, Phase
  1/2 against Phase 3).
- It reports **past results or a past data cutoff**, such as an interim analysis from 2015. That
  describes old data, not an interim analysis planned for this study.
- It usually sits inside an introduction, background or clinical experience section.

The start and end of such a passage are different in every document, so it cannot be cut out by a fixed
rule [00:54:37].

## 9. The schedule of activities

Also called the schedule of events, SoA or SoE. Documents often use the abbreviation without spelling it
out [01:32:46].

**For now, record only whether a schedule of activities exists, and where it is** (main body or
appendix) [01:27:40]. It matters even without being counted: when the sponsor gives no case report form
count, IQVIA's data management team can work one out from the schedule of activities [02:00:40 to
02:02:51].

**Later (deferred), count assessments per study period.** Angus's description of the target, recorded
here so it is not lost [01:20:21 to 01:31:57, 02:04:14 to 02:06:23]:

- Assessments run down the left column, study days or visits across the top, and an X marks an
  assessment done at that visit.
- One column can cover several days, for example "Days 8, 15 and 22," so one X there counts three times.
- The goal is a table of how many assessments each patient has in each period (screening, month 1, and
  so on), to see which periods are busy. The names of the assessments do not matter.
- Study drug administration is not an assessment. Count administrations separately, with their days.
- Randomization is not an assessment; ignore it.
- A drug taken daily at home is usually drawn as one long line with a single X, and means daily dosing.
- A separate follow-up schedule for patients who stopped treatment early (in this protocol, a phone call
  every three months, in an appendix) must not be added to the main count. IQVIA budgets dropouts as an
  average, usually half the assessments of a completing patient.
- Only a rough count is needed; data management does the exact one.

## 10. Open questions for Angus

- He called the "approximately 42 months" study duration "not very helpful" and said to ignore it
  [01:11:19]. The field registry (`config/fields.yaml`) treats `timeline.total_duration` as a budget
  driver. Should the tool keep extracting it?
- How should a single document that is half protocol and half RFP be handled? He called this rare
  [02:09:46].
