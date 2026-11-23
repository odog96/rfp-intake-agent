# Build journal — RFP Intake Agent

Every `### Done` entry that was in `CLAUDE.md`, moved here unedited on 2026-10-03 because the
entries were 74% of `CLAUDE.md` and were loaded into every session. Newest first. Nothing in this
file is current state: `CLAUDE.md` holds current state, and where the two disagree, `CLAUDE.md` wins.

### Done 2026-11-23: the report Angus Gray asked for on 29 October

Oliver asked on the evening of 22 November for a report that answers every request Angus Gray has
made, above all on the 29 October call (report `r-20261029-140643`, transcript
GMT20261029-142148), with the workflow still deployable on Cloudera AI. The list of Angus's asks
this work was checked against (131 asks from the four call transcripts, the meeting notes, his
variable list and his deck) is `angus-asks.md` in the project's shared files, not in the
repository, because it quotes the customer's calls.

**Model.** Oliver chose an Opus model on Bedrock for the working prototype; `config/models.yaml`
names it for all four roles. The newer Opus on Bedrock refuses the forced tool choice that native
structured output relies on (`llm/structured.py`), so it is not used. The first run on the chosen
model, `r-20261123-opus5-base` (the 19 October code), passed 5 of the 14 must-hold facts. Three
prompt wordings were the cause and were changed in `b5e54a4`: the extraction schema called the page
"1-indexed page number within the excerpt", which the model followed literally; CLASSIFY's
definitions let it call the amended protocol a protocol; and ADJUDICATE judged 42 against 40 months
reconcilable and kept the statistical sample size of 236 beside the enrolment figure of 260.

**Registry (`config/fields.yaml`), 45 variables in the 29 October report, 42 fields now.**
- Removed: `visits.intensity_evidence` and `visits.intensity_rating` (visit intensity),
  `blinding.placebo_assumption` (the tool's own placebo guess), `monitoring.frequency_requested`,
  `monitoring.frequency_basis`, `monitoring.sdv_expectation`, `monitoring.sdr_expectation`. The
  blinding group's `block` is gone: every variable is its own row.
- New: `study.title` (first row), `timeline.budget_window` ("Time window to budget for"),
  `monitoring.pharmacy_frequency` ("Pharmacy monitoring frequency"),
  `monitoring.source_data_expectations` (SDV, SDR and CRF percentages in one row, no arithmetic).
- Changed: `timeline.periods` is the one "Patient timeline"; `timeline.total_duration` ("Whole
  study duration") and `timeline.budget_window` are printed as lines under it; the requested
  monitoring frequency is the RFP's own words, not a number of days; the pharmacy visits row moved
  to the top of the blinding group with its frequency after it, and placebo matching sits directly
  under the blinding design; placebo matching gained `no_placebo` and the words Yes / No / N/A; the
  phase and population group, with the title, comes first, in the protocol synopsis's order.
- Hints changed after reading live runs: the timeline asks for day ranges, a length as "28 days",
  no phone calls to patients who stopped treatment and no statistical accrual assumptions; the
  budget window leaves out the enrolment period; "Multi-part study" says when to answer No; the
  monitoring guidance and the unblinded-monitoring reason no longer repeat the visit counts; the
  dosing frequency leaves out infusion lengths and exceptions; the design summary leaves out the
  stratification factors; the drug form is never read from what the drug is ("an investigational
  monoclonal antibody" gave "Another route" in run 3); a sentence referring to the schedule of
  activities is no evidence that the document lacks one (run 3 answered "no" from Amendment p.13).
- New registry keys, each validated in `domain/registry.py`: `shown_under` (a field printed as a
  line under another field's row), `only_from` (the document kinds EXTRACT asks a field of),
  periods `always`, `when_stated` and `may_word_differently`, and a schedule consideration's `show`
  (premedication and chemotherapy are left out of Key considerations but still out of the
  Assessments count). `docs/ARCHITECTURE.md` §4.2, §4.9 and §4.10 describe them.

**Pipeline.**
- RECONCILE, GATE and ADJUDICATE share one scope rule, `reconcile.scope_key`: on a field that is
  not `scoped`, "total" and no scope are one group. Run `r-20261123-oct29-run1` kept the
  amendment's "not matching" placebo (no scope) apart from the RFP's "matching" (scope "total"),
  so must-hold fact 6 failed and four variables printed twice.
- GATE: a budget driver whose disagreement was dismissed is Confirmed where the on-topic records
  come from two or more documents and every one of them gives the row's value
  (`documents_state_it`), which answers Angus's question about the 260 subjects. The patient
  timeline is one answer where no period's length reads two ways, in one document or two
  (`periods_agree`, `MergedEntry.stated_two_ways`); one document may word a day, a visit or the
  period's own name two ways, two documents only where they share one wording (changed after
  run 4, below).
- ADJUDICATE writes one row where every on-topic record gives the same value; the same answer in
  other words is now described as reconcilable (one row, "worded differently"); a call that fails
  is tried once more, and a disagreement still not judged is shown as a disagreement for a person
  to settle, never as "Not found" (`r-20261123-oct29-run2` lost the 42 against 40 months that way).
  The guided parse reads only a response's text blocks. The candidate list no longer shows the
  model each record's confidence, so Appendix B quotes none.
- NORMALIZE reads "20,800 CRF pages total" as 20800 and "260 (130 per arm)" as 260 (CLAUDE.md open
  item 10). It now gives None, and keeps the raw text, for a number followed by words and then
  more digits outside parentheses: "10 visits per site for a total of 750 visits", "40 sites in 5
  countries" and "260 subjects (130 per arm), 236 evaluable" gave 10, 40 and 260 before. A length
  written as one hyphenated word ("28-day") reads as a number and a unit ("28 days").
- EXTRACT asks a document CLASSIFY could not label for every field, and asks for the budget window
  in an RFP and in any document labelled `other` (an email), not in a protocol, an amendment or a
  schedule of activities.
- DERIVE has no rubric left: the visit-intensity and placebo-assumption rubrics are removed.

**Report.** Every row is Variable | Value | Status | Where, with every page reference in Where.
Status is Confirmed or Review only; no confidence figure is printed anywhere, in `report.pdf` or
`report.xlsx`. A disagreement row reads "Documents disagree — see Disagreement N", with one line
per document under it, each with its own pages. Grey notes and review reasons sit under the pages.
A patient-timeline line takes its words from one passage where one passage gives them all and cites
every passage whose words it prints; an open-label extension or long-term follow-up line starts
with the document's own name for it ("Separate open-label extension study of NEOD001, from study
completion"); a length stated two ways is printed under its line with each value's pages. A
flagged timeline is one row of Flagged for review, in its lines' words. The visit table's footnote
page moved from the timing cell to the Where column. A budget driver Flagged for review because
ADJUDICATE dismissed a difference now reads "Budget driver; the passages differ, but the tool's
check found no contradiction — see Appendix B." instead of "the documents word it differently". Run
4 printed an intermediate wording, "the documents answer differently", beside the grey note "Worded
differently; checked, the same answer" on the same row ("Schedule of activities present",
"Unblinded monitoring likely required"); the two contradict each other.
`scripts/check_review_2026_10_07.py` no longer applies (its table signatures include the Confidence
column); `scripts/check_review_2026_10_29.py` checks Angus's 29 October asks on `report.pdf`.
`scripts/check_flag_reasons.py` counts `reconcilable` as a dismissed verdict, as GATE does, skips the
schedule's own rows and finds a row whose scope the report capitalises or leaves out: 13 of 13 on
run 4.

**Reviewers.** pipeline-rules-reviewer and plan-conformance-reviewer read `6615f70..761bc0a` after
run 3. pipeline-rules-reviewer found three breaches of `docs/ARCHITECTURE.md`: GATE confirmed 260
beside an amendment's on-topic 280 on one model call (§4.9; fixed: every on-topic record must give
the value); `only_from` let CLASSIFY's label decide which fields are attempted (§4.2; narrowed, the
choice between narrowing and removing is Oliver's); and GATE confirmed a timeline one document
states two ways (fixed: `may_word_differently`). It also noted that ADJUDICATE stores an unjudged
candidate as `conflict`, not None (recorded in §4.7). plan-conformance-reviewer found 8 of 39 items
short: timeline lines citing one page while their words came from several (fixed), the placebo row
not directly under the blinding design (fixed), a confidence figure in Appendix B (fixed), a
flagged timeline printing raw keys (fixed), the footnote page in a value cell (fixed), a test
comparing quotes with trailing spaces (fixed); the "Whole study" line inside the timeline and the
schedule-against-text check wait for Angus's answers.

**After run 4: the reviewers' second reading and the report fixes.** pipeline-rules-reviewer and
plan-conformance-reviewer read `761bc0a..60e968c`, and report-usability-reviewer and
run-comparison-reviewer read run 4. What changed:
- GATE's predicates are public (`documents_state_it`, `periods_agree`, `rival_values`,
  `entry_listings`), and `render/report_model.py` calls them to print why a row is in review, so
  the printed reason is GATE's own rule; `scripts/check_flag_reasons.py` gives 13 of 13 on run 4.
- `MergedEntry.stated_two_ways` lets one document word a period's name, start or end two ways.
  Two documents may word it differently only where they share one wording: an `end` can hold the
  event a treatment runs until, and "approximately 300 primary endpoint events" in one document
  against "approximately 250" in the other is a difference; "Month 1-Day 1" in both, with "Day -1"
  as well in one, is not. "24 months after the last subject is randomized" against "first" is a
  difference, whatever the lengths say.
- `normalize/duration.py:amounts_in` reads a range by both ends, so "1-2 years" and "2 years" are
  two lengths; a number after "Day " or a hyphen is not a range's start. It also reads the
  number words one to nineteen and the tens, alone or with a unit ("twenty-eight days" is 28
  days, not a range from twenty, and was "eight days" before), digits with thousands separators
  ("1,000 days" read as 0 days before, so 1,000 and 2,000 days agreed), and ranges written with
  "or", "and" and "between" ("1 or 2 years", "between 1 and 2 years"). "hundred" is not read:
  "two hundred days" gives no amount and is compared by its words, rather than read as 100 days.
- A patient-timeline line takes the period's name from the passage it cites, and where several
  passages give the line, its words come from one whose quote states them.
- Within one document, the record shown for an entry's key is one whose own quote states the key
  (at least half of the key's words, `reconcile/entries.py:quote_states`), then the more
  confident. EXTRACT checks an entry's counts against its quote but not its text, and run 4's arms
  showed "Dose: administered intravenously" from a p.15 record whose quote is about enrolment; they
  now show "NEOD001 (24 mg/kg) IV infusion" and "normal saline IV infusion", from p.19 and p.20.
  GATE reads no shown value, so no status moves. An arm row in review now prints the rule GATE
  used for it (the documents list the arms differently), not the general reason.
- The report: page 1 names each document's file and page count; a review reason is one line;
  "provisional" is worded one way on the row and in the Disagreements table, with the rule that
  chose the value ("the tool read the amendment's passage as the clearest"); "the reviewer" is "the
  tool" or "the tool's check"; no blank page before Appendix A. A hedge before a number
  ("approximately 42 months") is the same value as the bare amount (`_value_key`), so
  Disagreement 2 lists the amendment once, citing p.15 and p.24, and Appendix A marks p.24 "Shown
  in the report" rather than "Not used". Where an Appendix A line cites several pages, it names the
  page the quote is from (`_Docs.quote_page`): run 4's "Study Phase Phase 3" sat beside "Amendment
  p.11; RFP p.2". ADJUDICATE's text reads "the amendment", not "the amendment Amendment", and the
  report's words, not code words such as `not_matching_stated`; an entry's code words
  (`follow_up`) read as words. Appendix B gives each passage ADJUDICATE names by number its
  document and page ("Record 3 (RFP p.3)"); the reader is never shown the numbers otherwise. A
  budget driver in review because a difference was dismissed lists the other values under its
  pages ("Also stated: Yes, in an appendix — Amendment p.111 (printed p.109)."), so the reason
  says what to check. Appendix A prints no "[Total]" before a whole-study passage. Page 1 gives
  the date in words and no longer prints the registry's id.
- Hints (these take effect from run 5): no hint names a person, because ADJUDICATE is shown the
  hint and run 4's Appendix B told Angus the stratification factors were left out "as Angus Gray
  asked"; "Reason / trigger for unblinded monitoring" no longer answers whether the placebo
  matches, which run 4 printed as Confirmed beside a disagreement on exactly that.
- `scripts/rerender_report.py` reads the run's input PDFs, so a re-rendered report names the files
  and the quote pages as a live run's does.

Today's GATE, run again over runs 3 and 4, changes no status, so the re-rendered run 4 report is
what the current code makes of run 4's extraction.

**Runs** (all on `Example protocol 2.pdf` and `Synthetic_RFP_NEOD001.pdf`):

| Run | Code | Must-hold facts | 29 October check | Clusters | EXTRACT errors | What it showed |
| --- | --- | --- | --- | --- | --- | --- |
| `r-20261123-opus5-base` | `6615f70`, the 19 October code | 5 of 14 | not yet written | 14 | 22 | the three prompt wordings fixed in `b5e54a4` |
| `r-20261123-oct29-run1` | the new registry and pipeline | 13 of 14 (fact 6 fails) | not kept | 9 | 5 | placebo "matching" (scope "total") kept apart from "not matching" (no scope): `scope_key` |
| `r-20261123-oct29-run2` | run 1's fixes | 11 of 14 (facts 5, 6, 9 fail) | not kept | 10 | 2 | a failed ADJUDICATE call lost the 42 against 40 months to "Not found": retry and the unjudged fallback |
| `r-20261123-oct29-run3` | `761bc0a` | 14 of 14 | 18 pass, 1 look (after the check script stopped counting a disagreement's sides as rows) | 11 | 4 | 429 s; the reviewers' findings and the content fixes of `60e968c` |
| `r-20261123-oct29-run4` | `60e968c` | 14 of 14 | 19 pass, 0 fail, 0 look | 10 | 2 | 398 s; "Values found for 41 of 42 variables"; the reviewers' second reading and report-usability-reviewer's reading of it gave the fixes "After run 4" |
| `r-20261123-oct29-run5` | the fixes after run 4 | 14 of 14 | 19 pass, 0 fail, 0 look | 10 | 2 | 393 s; the unblinded-monitoring reason no longer restates the placebo, and Appendix B no longer names Angus; but the treatment line read the power assumption "a treatment/follow-up period of 18 months" (Amendment p.24) as its length, printed Confirmed, and the open-label extension read "from At the time of study completion": the timeline hint changed |
| `r-20261123-oct29-run6` | run 5's code with the new timeline hint | 14 of 14 | 19 pass, 0 fail, 0 look | 11 | 3 | 410 s; the final run, "Values found for 41 of 42 variables"; the treatment line reads "From Month 1-Day 1 until approximately 156 primary endpoint events" and the extension "…, from study completion". EXTRACT scoped the 236 subjects "total" and ADJUDICATE called it off-topic, so the "Statistical minimum: 236" line of runs 4 and 5 is only in Appendices A and B (decision 2 in CLAUDE.md). The report to send is its `report-rerender.pdf`: two Appendix B wording fixes were made while it ran |

**Must-hold fact 9 is translated, to be confirmed by Oliver.** It was "40 of 44 fields have a
value": three fields these documents never give (healthy-volunteer scope, SDV, SDR) and one field
of slack. SDV and SDR are now one field, so the same one field of slack is 39 of 42
(`eval/golden/sample_pair_must_hold.yaml`).

**Still open after this work**, in Angus's terms:
- Not testable on this document pair: whether a visit type the RFP does not specify reads "not
  specified" (M2), the on-site or remote split per visit type (M4), placebo N/A on an open-label
  study (B3), take-home tablets (S13).
- Not built: rows for booster, remote, centralised and co-monitoring visits (M15, asked as a
  question); marking which words of a quote a value relies on (L12); a check that the schedule of
  activities matches the text (S16, asked as a question in the follow-up email); several studies
  in one RFP (V22) and scanned pages (P10), both deferred by agreement.
- Waiting for Angus: whether the whole-study duration belongs inside the patient timeline (T8;
  on 1 October he said to ignore the 42 months).
- `eval/golden/Synthetic_RFP_NEOD001.json` still names `monitoring.frequency_requested`, and
  `scripts/rerender_report.py --rederive` keeps an old run's derived rows; a re-render of a run
  made before 2026-11-23 leaves out variables the registry no longer defines.
- Read in run 4 and not fixed: the population row cites Amendment p.12 for a summary also drawn
  from p.20; the treatment line's start ("Month 1-Day 1") is from p.13, which the line does not
  cite; Table 1 gives 7 assessments to each of the Month 1 visits on Days 8, 15 and 22, where the
  table's PK marks differ; "Factors driving unblinded monitoring" lost one item between runs 3
  and 4. The first two are EXTRACT's choice of passage, the third READ_SCHEDULE's, the fourth the
  model's. ("Reason / trigger for unblinded monitoring" restating the placebo is fixed from run 5.)
- Run to run, the extraction model's choices move: run 5 took a power assumption as the treatment
  length, which the hint now forbids; run 6 scoped the 236 subjects as a total, which the hint
  already forbids. The must-hold facts and the 29 October checks both passed run 5, because
  neither checked a period's length. Check 5 of `scripts/check_review_2026_10_29.py` now fails a
  treatment line that gives a fixed length beside an end that is a number of events: run 5 now
  gives 18 pass, 1 fail; runs 4 and 6 still give 19 pass. A must-hold fact for the same thing is
  Oliver's to add.
- Recorded by pipeline-rules-reviewer's third reading, not changed: `quote_states` counts every
  word, so an enrolment quote naming "NEOD001", "IV" and "infusion" counts as stating the dose
  "NEOD001 (24 mg/kg) IV infusion", and across documents `_shown` still ranks without it;
  `amount_count` reads "twenty-eight days" as the one amount "eight days" (it only counts);
  `amounts_in` reads "3 and 6 months" as the range 3-6 and "Month 1 to 12 months" as 1-12;
  `scripts/check_flag_reasons.py` imports GATE's predicates, so its 13 of 13 shows the report
  prints the rule GATE applied, not that GATE's rules are right; `_name_values` swaps a value word
  in quotation marks even where ADJUDICATE was quoting the document's own word.
- `report.xlsx` still prints raw values and `rf.sources[0]` (CLAUDE.md open item 6); none of the
  report fixes above reached it.

### Done 2026-10-12: one row per arm, matched across documents by treatment

Run `r-20261009-225414` listed four arms for a two-arm study under "Cohorts / arms / dose levels /
parts" (`design.parts`): `arm:NEOD001` and `arm:Placebo` from the amendment, `arm:Group 1` and
`arm:Group 2` from the RFP. `r-20261011-fix1` and the baseline `r-20261008-review2-run3` show the same
four. RECONCILE groups rows by scope, and the two documents named the same arms differently. The rule
Oliver approved on 2026-10-12:
- The `design.parts` hint in `config/fields.yaml` asks for arms scoped by treatment
  ("arm:<treatment>"): no arm number, group letter or background therapy, a dose only when the arms
  differ by dose, and parts and cohorts keep their own names. Oliver's addition: a control arm is
  named by its role as the document names it ("placebo", "standard of care alone", "active
  comparator: <drug>"), never by its substance ("normal saline", "vehicle").
- Code makes the comparison, with `normalize_scope` as before.
- A new registry key, `check_scopes_across_documents` (`FieldDef` in `domain/registry.py`, rejected on
  a derived field), set only on `design.parts`, turns on two checks in RECONCILE. First, an entry
  whose own value or quote does not contain the words its scope names (`scope_named_in` in
  `normalize/scope.py`, a pure function; case and punctuation ignored, so "NEOD-001" counts as
  "NEOD001") is not merged: it gets the scope "<scope> (not matched, <document>)" and a
  `review_note`. Second, when more than one document lists parts, an arm only one of them lists gets
  the note "Listed only in <document>, not in <others>."
- Labels such as "Group 1" never count, in either direction. Subject count is not used.
- GATE keeps any row with a `review_note` in review, after the multi-value rule; the report prints the
  note as the row's reason with document ids replaced by names, and `extraction.json` carries it.

**Live run `r-20261012-arms2`** (2026-10-12, 6m45s): 40 of the 43 registry fields found, 112 resolved
rows, 14 contradiction clusters, 12 EXTRACT errors, against `r-20261008-review2-run3`'s 40 of 43, 115,
12 and 16. `design.parts` is two rows, `arm:NEOD001` (amendment pp. 11, 15, 19, 42 and RFP p.3) and
`arm:placebo` (amendment pp. 11, 15, 20, 42 and RFP p.3). Both are in review because each holds several
values (the existing budget-driver rule), not because of a note: no row in the run has a
`review_note` or a "(not matched" scope, so only the matching path ran live; the two negative paths
are covered by `tests/reconcile/test_scopes_across_documents.py`. General study design says two-arm,
NEOD001 plus standard of care against placebo plus standard of care, which agrees with the arm rows.
`scripts/check_review_2026_10_07.py r-20261012-arms2` gives 8 pass, 0 fail (check 6 LOOK, its live
regression half passing). Passing `--baseline r-20261008-review2-run3` makes check 9 print a false
FAIL ("36 … down from 40"): that baseline already holds change 5's four new fields, so the script
subtracts them from one side only. Use the default baseline `r-20261007-140411`.

The run-comparison-reviewer found nothing it could tie to this change as a regression. The other
differences from the baseline (fewer EXTRACT errors, `visits.intensity_evidence` now four scoped rows,
two extra `not_a_conflict` clusters on `monitoring.strategy_guidance` and `ops.crf_pages`, four RFP
monitor-visit rows wrongly in `visits.frequency_by_period`) are model variation or the three
2026-10-11 commits that came after the baseline. The first attempt, `r-20261012-arms`, stopped
because the Bedrock bearer token had expired; its folder is left as the record.

Three things this does not fix, recorded so nobody assumes otherwise:
- `ops.subjects_total` still has its separate "per arm" row. It is out of scope and does not set the
  new key.
- The check in step 3 reads the model-written value as well as the verbatim quote, as approved
  ("value or quote"). So a saline-only passage the model labelled "Placebo" in its value would pass;
  the protection there is the hint. These two documents cannot test it: every arm sentence that says
  saline also says placebo, and the RFP never says saline. `extraction.json` keeps rows, not records,
  so the record behind each value cannot be checked after a run.
- `report.xlsx` prints the raw scope, so an unmatched row would show "(not matched, doc-…)" with the
  raw document id, and the workbook does not print `review_note` (`render/xlsx_renderer.py:81`).

### Done 2026-10-11: the visit-intensity rating reads every row

Live run `r-20261011-fix1` rated visit intensity low. Its study-wide `visits.intensity_evidence` row
named nine kinds of evidence (score 14, high), and a second row, scope "Months 1-3", named one (score
2). **The cause was in DERIVE:** `derive/__init__.py` built `{r.field_id: r for r in state.resolved}`,
so of several rows for one input the last one silently won. It was not new: 15 of the 29 runs under
`runs/` have several evidence rows, and 13 of them reported lower than their rows together support
(12 low, one moderate; all 13 are high with every row). The rule Oliver approved:
- Every rubric now receives every row of each input. The visit-intensity rubric combines the flags of
  all evidence rows, each counted once; its confidence is the weakest row's, its sources every row's
  pages once each, and its note names the scopes it combined, each scope once with a count.
- Oliver's one exception: rows whose scope is patients who stopped treatment early are held back,
  because `docs/ANALYST_PROCEDURE_PROTOCOL.md` section 9 says that schedule is budgeted apart from the
  main count. The phrases are `leave_out_scopes` on `visits.intensity_rating` in `config/fields.yaml`
  (new registry key, `ScopeLeftOut` in `domain/registry.py`; valid only on a derived field, and a
  phrase must have words). A phrase matches when its words appear in the scope one after another.
  The note names each row held back, grouped by field and scope, with its pages and the reason.
  Bare "early termination" and "early treatment discontinuation" are deliberately **not** phrases:
  "End of Treatment / Early Termination visit" and "early treatment discontinuation visit" name the
  last visit of the main schedule, which every patient has, and those rows still count. Checked
  against every `visits.intensity_evidence` and `visits.frequency_by_period` scope in `runs/`: 114
  rows (92 distinct scopes) are held back, every one a dropout schedule, and none of the
  end-of-treatment visit scopes ("End of Treatment / Early Termination", "EOT/ETD", "EOT/ETD Visit")
  is. One scope is **not** held back and may be one: "Post-treatment follow-up" in
  `r-20261001-032936` (a frequency row, amendment p.79, quoting phone calls every 3 months to subjects
  "no longer receiving study drug"). Whether that wording means dropouts only is Oliver's call; it
  feeds no score, so no rating moves either way.
- The visit-intensity rubric used to read the evidence row only when its status was `needs_review`;
  it now reads any row whose status is not `not_found` or `not_specified`, as the placebo rubric
  already did. On a live run nothing changes, because DERIVE runs before GATE and every row is then
  `needs_review` or one of those two. It lets `--rederive` work on a saved run, whose rows GATE has
  mostly marked `confirmed`. Re-deriving all 29 runs, every run with one evidence row keeps its rating.
- The placebo rubric uses an input's rows when they agree (ignoring case and list order). When they
  give different answers and the rule reached needs that input, the value is
  `PLACEBO_INPUTS_DISAGREE` ("the documents give more than one answer to a question this depends on:
  ask the sponsor") and the explanation names every answer. A split the rule reached does not need,
  such as `ip.form` on an open-label study, changes nothing.

**Effect on saved runs**, re-derived with no model call: leaving the dropout rows out changes none of
the 13 ratings, and none of their scores (14 to 17). Evidence rows were held back in three runs
(`r-20260827-180418`, `r-20260901-172918`, `r-20260923-131601`); in each, every flag on the dropout
rows also appears on a main-schedule row. In the other runs the rows held back are
`visits.frequency_by_period` rows, which the score does not read, but they are named in the note all
the same. The placebo answer is unchanged in all 10 runs that have one. `scripts/rerender_report.py`
gained `--rederive`, which drops the saved derived rows and runs DERIVE again; the re-rendered
`runs/r-20261011-fix1/report-rerender.pdf` reads High, score 14.

### Done 2026-10-11: fix 2 of the 2026-10-09 brief — pages beside a value must state it

Run `r-20261009-225414`'s report printed "Number of interim analyses: 0 — Amendment p.98; RFP p.4",
and RFP p.4 says 1. Appendix A held only the amendment's quote, so the RFP's 1 had no quote anywhere
in the report. **The cause is in ADJUDICATE's output, and the fix is in RENDER.** For a `conflict` or
`reconcilable` verdict, `adjudicate/_handle_conflict` and `_handle_reconcilable` write one
`ResolvedField` with the chosen value and quote but `sources` naming every record's page, whichever
value the record held; `render/report_model.py` printed `sources` as the pages of the value. Nine rows
in that run cited a page that does not state their value: study.population, study.primary_objective,
interim.count, visits.schedule_present, timeline.total_duration [study] and [subject],
blinding.placebo_matching, monitoring.unblinded_rationale and ops.subjects_total (amendment p.24 says
236). On two more, both ops.crf_pages rows, the RFP's "80 pages per subject" record had no Appendix A
line. The fix is in `render/report_model.py` only, so it shows on a re-render of the existing
`extraction.json` with no run:
- `_stating` keeps, beside a value chosen from a disagreement, only the pages of records whose value
  reads the same. A row where no value won (`timeline.total_duration` [study],
  `blinding.placebo_matching`) prints "—" with no page.
- `_evidence` lists each record of such a disagreement in Appendix A with its own value, page, quote
  and confidence. Appendix A on that run grew from 100 lines to 122; the report stayed 17 pages.
- A borrowed disagreement (`interim.timing` carries `interim.count`'s) and a `not_a_conflict` verdict,
  which already gets one row per record, are unchanged.

**Why `assert_nothing_lost` missed it.** It checked one direction only: every page in `rf.sources`
is cited beside the value. It never checked that a cited page states the value, and since
`rf.sources` held the other side's page, the check *required* the wrong citation. It also looked for
the chosen value's quote in Appendix A and never for the other records'. Its hand-built conflict
tests built the row with both pages, which encoded the bug as expected. It now checks both
directions and every record's Appendix A line; before the fix it fails on 23 of the 29 runs under
`runs/` and on four hand-built tests, and `test_each_side_of_a_disagreement_keeps_its_own_page_and_quote`
pins the brief's done-condition on `r-20261009-225414`. Because `runs/` is not in git, that test is
skipped on a clean checkout, so `TestNothingLost.test_each_side_of_a_conflict_keeps_its_own_page_and_quote`
builds the same case by hand; it too fails without the fix. The check also now fails a value that
came with pages but is shown with none, so it cannot pass by the code dropping every page.

**What the check still does not catch.** "Both directions" is exact only for a row ADJUDICATE wrote
from a conflict or reconcilable verdict, where the records say which page states which value. For
any other row the check takes `rf.sources` as the pages that state it, so a page RECONCILE added for
a document that said "not specified" (`reconcile/__init__.py:176`) still passes. That is open item 3
in `CLAUDE.md`.

The report-usability reviewer read `runs/r-20261009-225414/report-rerender.pdf` and confirmed the
done-condition. Its findings that are not fix 2's to change, recorded as open items in `CLAUDE.md`:
Appendix A now shows a losing value (the RFP's 1, the protocol's 236, "No" for the schedule) with
nothing saying it was not used, beside a row that says "checked, the same answer"; and the derived
row "What to assume about the placebo" cites RFP p.3, which says the opposite of its value, which
predates fix 2. Its finding that two identical records at one page showed the lower confidence was
made deterministic: duplicate Appendix A lines now always keep the lower one.

Seen while measuring and **not** changed: on three older runs (`r-20260918-141940`,
`r-20260923-131601`, `r-20261001-032936`) a conflict's value and quote come from different records
of one document, because `_handle_conflict` looks the quote up by document. Appendix A lists the
row's own line as well there.

### Built 2026-10-11, failed its live run: fix 1 of the 2026-10-09 brief — Site selection visits

**Live run `r-20261011-fix1` (2026-10-11): Site selection visits is still Not found.** The record
failed again after the retry; `RunError.rejected_quote` now shows what the model wrote —
"1 visit per site; a total of 80 visits to\n\n qualify 75 sites; an average of 5 hours\n\n per visit",
the label's "Time on Site" left out of the middle. Run offline on page 3, `nearest_passage` returns
the passage with "Time on Site" in it, but the retry prompt is not logged, so whether the model was
shown it and ignored it is not observed. EXTRACT errors fell from 16 to 11, and
`scripts/check_review_2026_10_07.py` reports 8 pass, 0 fail. 23 of the 43 fields changed value,
wording or pages against `r-20261008-review2-run3`. One of them, `visits.intensity_rating` going from
high to low, is a DERIVE defect and not model variation: see the open item in `CLAUDE.md`. Oliver's
decision for the second attempt is to look at the parsing side — whether the RFP's two-column table
can be parsed as a table — rather than at another model retry.

Run `r-20261009-225414` reported Site selection visits as Not found, though the synthetic RFP states
them. **The cause was quote validation, not the hints.** EXTRACT's RFP call for `operational_metrics`
did return a record for `ops.visits_site_selection`, in that run and in the baseline
`r-20261008-review2-run3`, and both its first quote and its repair-retry quote failed with
`quote_not_found_in_excerpt`. The alias "site qualification visit" was already in `config/fields.yaml`
and in the prompt. The row is on PDF page 3 (printed p.10), not page 4 as the brief said, and it is
two columns of preformatted text: the label's second line, "Time on Site", sits between the value's
words in the extracted text, so "a total of 80 visits to qualify 75 sites" is not in the excerpt. The
rejected quote was not saved anywhere, so what the model wrote is inferred, not observed. PyMuPDF puts
the whole overview in one block with no consistent column edge, so rebuilding the columns was not
attempted.

Built, on Oliver's condition that code never makes a quote for the model:
- **The retry shows the nearest passage.** `extract/validate.py:nearest_passage` finds the unbroken
  stretch of the excerpt sharing most words with the failed quote — never across a page marker, and
  nothing at all below half the quote's words — and `extract/prompt.py:describe_violation` adds it to
  the retry's violation line as "reads exactly: …". The retry's quote goes through `validate_quote`
  unchanged; a mutation that let the passage stand in as the quote fails
  `tests/extract/test_repair_retry.py`.
- **A dropped record keeps its answer.** `RunError.rejected_value` and `rejected_quote` are set when a
  record fails after the retry, so the next error of this kind can be read rather than inferred.

Two gaps in the same retry were seen and **not** changed, because neither is in the brief: a field
whose first record failed and which the retry does not return at all is lost with no error, and the
retry's records are added to the first call's valid ones without removing repeats.

**Model calls made by mistake on 2026-10-11.** The first offline INGEST-to-PLAN script set
`LLM_BACKEND=mock`, which the settings ignore — the variable is `RFP_INTAKE_LLM_BACKEND`. CLASSIFY and
MARK_OTHER_STUDY therefore sent the two sample documents' text to Bedrock. Every call was refused
because the access credentials had expired; none was retried, and Oliver was told.

### Done 2026-10-08: the seven changes Oliver and Angus agreed after reviewing report `r-20261007-140411`

Oliver Zarate and Angus Gray (IQVIA Delivery Strategy & Budgeting) reviewed the report from run
`r-20261007-140411` on 2026-10-07 and agreed seven changes. Nothing else was in scope. Two things
Angus asked for were deliberately **not** started and are still deferred: grouping related
information into fewer rows, which Oliver is putting to Angus as a proposal first, and supporting two
studies in one RFP, which waits until the single-study report is accepted.

Three full live runs were made, in this order, each on the same two documents as the 2026-10-07
report — `samples/Example protocol 2.pdf` (the 137-page protocol amendment) and
`samples/Synthetic_RFP_NEOD001.pdf` (the 6-page synthetic RFP):

| run | what it showed |
| --- | --- |
| `r-20261008-review2-run1` | three problems: the five new monitoring-visit fields all failed `quote_not_found_in_excerpt`; MARK_OTHER_STUDY read "ongoing" as "alongside" and let Phase 1/2 back into `study.phase`; and 56 false failures from the check script's own regex |
| `r-20261008-review2-run2` | the visit-type quotes were right but the values dropped the hours; MARK_OTHER_STUDY still kept section 1.3.2's first sentence |
| `r-20261008-review2-run3` | checks 1 to 9 all pass. 40 of the 43 registry fields found, 115 resolved rows, 12 contradiction clusters, 16 EXTRACT errors, 9m09s |

**What each change was, and where it lives.**

1. *A `reconcilable` verdict is not a disagreement.* `gate.DISMISSED_VERDICTS` is now
   `("not_a_conflict", "reconcilable")` and `render/report_model.py` imports it rather than keeping
   its own copy; `NEEDS_DECISION` there is `("conflict",)`. A reconcilable variable shows the value
   ADJUDICATE chose and takes its status from the existing GATE rules, so a budget driver still goes
   to Flagged for review. `Total number of patients / subjects` (260 against 236) moved from
   Disagreements to Flagged for review rather than being dropped. This overturns the old
   `_disagreements` docstring in `render/pdf_renderer.py`, which is rewritten.
2. *"Not specified" is not a competing answer.* `reconcile/_competing_records` splits a scope's
   records into those that answered and those that said nothing; a `not_specified` record no longer
   competes where any real answer exists. Where every record says nothing, the variable still
   resolves to Not specified, so `not_found`, `not_specified` and `0` stay three different answers.
3. *A Phase 3 study is never first-in-human.* Angus said on the call (00:29-00:32) that a Phase 2, 3
   or 4 study is never first-in-human. The rule is in `config/fields.yaml` as
   `design.first_in_human.resolution_rules`, read generically by `reconcile/rules.py`; no field name
   appears in Python. `domain/registry.py` validates the trigger field, the triggering values and the
   rule's own value against each field's allowed values, and refuses the mechanism on a scoped field.
   Phase 1 and Phase 1/2 keep the ordinary extraction.
4. *Interim analyses: one count row, with the conflict flagged on it.* `interim.planned` is gone from
   `config/fields.yaml`, from the tests and from `eval/golden/Example_protocol_2.json`.
   `interim.count`'s hint now says an explicit statement that none are planned is a real 0.
   `interim.timing` gained `follows_conflict_of: interim.count`, which `gate/_contradiction_for`
   reads generically: a field with no disagreement of its own borrows the named field's
   `Contradiction` by reference, so one numbered disagreement serves both rows.
5. *Monitoring visits: one row per visit type, in the sponsor's own words.* `ops.monitoring_visits`
   and `monitoring.onsite_remote_mix` are replaced by five `text` fields in Operational Metrics and
   Volumes — site selection, site initiation, interim monitoring, close-out and pharmacy monitoring —
   each a budget driver with `source_priority: rfp`. Every hint says to do no arithmetic, and no code
   path calculates on them. Each row gives whether the sponsor specified that visit type, how many
   exactly as the document states it, and the sponsor's own wording including any on-site against
   remote split.
6. *Keep text about related studies the sponsor is running alongside this one.*
   `other_study/prompt.py` now removes a passage only when it is about an earlier or completed study,
   and keeps a companion, sister, parallel, follow-on, extension or rollover study, or one sharing
   sites, staff, pharmacies, laboratories, vendors, committees or patients with this one. Nothing new
   is extracted from those passages; a variable for programme details belongs to the deferred
   two-studies work.
7. *Show the page number printed on the page as well as the PDF page.* The new
   `ingest/page_labels.py` reads the PDF's own page labels where a document declares them and
   otherwise finds the number printed on the page, trusting it only when the numbers rise across the
   document. `Provenance.printed_page`, `RemovedPassage.printed_page_start/_end` and
   `Document.page_labels` carry it, and `format_page_ref`/`format_page_range` in
   `render/report_model.py` write "p.11 (printed p.9)" where the two differ and "p.11" where they
   agree. Neither sample PDF declares page labels: the amendment prints its number from PDF page 5
   onward, two lower than the PDF position throughout.

**Two tightenings of `other_study/prompt.py` that the runs forced.** After run 1 the prompt gained
the rule that being ongoing does not make a study alongside this one, and that a passage counts as
alongside only when it states a link to how *this* study will be run. Run 2 still kept the first
sentence of section 1.3.2 "Clinical Experience", with the reason "an ongoing companion study that may
be running alongside this one", so `study.phase` carried Phase 1/2 again. Two rounds of abstract
rules having failed, the prompt now carries that sentence and two others from the same section as
worked examples of text to **remove**, says that another study's own design is not a link to this
one, and says that a section whose every sentence is about other studies is `other_study` rather than
`mixed`. Run 3 removed the section and `study.phase` is one row, Phase 3, Confirmed.

**Two defects in the check script, both found by refusing to accept a FAIL without checking the
data.** The loop's rule was that a check passing on the starting run is written wrongly; the converse
turned out to matter as much. First, `_ENTRY` read Appendix A's confidence percentage as a page
number, producing 56 false failures of check 7 on run 1; it gained a `(?![\d%])` lookahead. Second,
`parse_report` applied a section heading only to a table header band on the same page, and on run 3
the "Disagreements between the documents" heading sat at the foot of page 4 with its table beginning
on page 5. Every row of that table was counted as part of All variables, and checks 4 and 8 reported
three conflicts missing from a report that in fact printed all three. `parse_report` now carries a
heading over to the table that starts on the next page.

**Four changes made after run 3, all render-only and all shown with `scripts/rerender_report.py`
rather than a fourth run, which the review's three-run limit forbids.** Taken in the order they were
found: the borrowed-disagreement row below; Appendix B's lost reasoning; the raw document ids in that
restored reasoning; and §4.9 of `docs/ARCHITECTURE.md`, which still specified the behaviour change 1
removed.

**Appendix B had been printing bare values.** Change 1 moved nine variables out of the Disagreements
table and into Appendix B's dismissed list, and they arrived there as two numbers with nothing to say
which to budget from — `Total number of patients / subjects` read "260 / 236". The adjudicating
reviewer had written the sentence explaining that 236 is the power calculation's floor and 260 the
planned enrolment cap, and the report was throwing it away. `Dismissed.explanation` now carries it and
`pdf_renderer` prints it under the bullet.

**That restored sentence showed the analyst raw internal document ids.** `_decisions` has always put
the adjudicator's words through `_Docs.name_ids`, which turns `doc-2ea4cd79` into "Amendment";
`_dismissed` did not, and ADJUDICATE's prompt prints `doc_id=` for every candidate and asks it to name
the document, so seven of those ids reached pages 16 and 17 of the re-rendered report. Both the
pipeline-rules reviewer and the plan-conformance reviewer found it independently. One call fixed it.
`tests/render/test_report_model.py::test_a_dismissed_explanation_names_the_documents_not_their_ids`
and the Appendix B assertions in `tests/render/test_pdf_renderer.py` both fail without it — the
renderer fixture had been building its dismissed pair with no explanation at all, which is why nothing
caught this.

**A real-document test for change 6, and two defects it found in change 6's own prompt.**
`tests/other_study/test_other_study_real_document.py` runs the real `samples/Example protocol 2.pdf`
through INGEST and FIND_SECTIONS — both code, so it stays offline — locates section 1.3.2 "Clinical
Experience", and checks that the three sentences `other_study/prompt.py` holds up as worked examples
of text to REMOVE are in that section character-for-character. Two were not. One ended "…treated in
the Escalation Phase of the study." where the document goes on "…and 42 subjects were enrolled and
treated in the Expansion Phase.", so the example was a shortened sentence in a prompt that forbids
shortening. The other wrote "Investigator's Brochure" with a straight apostrophe where the page has a
typographic one, and `sections.locate_text` folds whitespace and case but not punctuation — a model
copying that example would have had its sentence rejected as unverified and the passage KEPT, which is
the exact failure runs 1 and 2 showed. Both examples now match the document, and the test fails if
either drifts again. The model's own judgement is still only measurable on a live run, and the test
says so rather than pretending otherwise. The plan-conformance reviewer had reported the missing
real-document test as a problem; this closes it.

**`docs/ARCHITECTURE.md` is the build contract, so it was brought back in line in five places**, all
found by the plan-conformance reviewer. §4.9's GATE table said a `reconcilable` verdict forces
`needs_review`, which is the behaviour change 1 removed; the row is corrected rather than annotated,
with a paragraph saying why. §4.9 gained a row and a paragraph for `follows_conflict_of`, which the
code's own copy of that table already had. §4.6 now states that a `not_specified` record does not
compete and that RECONCILE ends by applying the registry's resolution rules. §3's `Provenance` block
said `page: int  # 1-indexed, as printed if detectable`, which after change 7 says the opposite of
what the code does: `page` is the position in the file and `printed_page` is the number on the paper.
And §3 now records the three fields change 7 added to the graph state, in the same form as every
earlier state addition.

**One change made after run 3, from the report-usability reviewer.** The "Timing of each interim
analysis" row said only "Sources disagree — see Disagreement 1", while Disagreement 1 describes two
interim-analysis counts and says nothing about timing. A row that borrows another variable's
disagreement number now names that variable: "The documents disagree on Number of interim analyses —
see Disagreement 1". `Row.follows_label` in `render/report_model.py` holds the label, looked up
through `follows_conflict_of` in the registry, and is set only where the row's `Contradiction` carries
a different field's id. This is render-only, so it was shown with `scripts/rerender_report.py` rather
than a fourth run; the re-rendered report is `runs/r-20261008-review2-run3-rerender/report.pdf` and
all eight measurable checks pass on it too. Run 3's own `report.pdf` is left exactly as the pipeline
produced it.

**Both gate reviewers were then run a second time, because the four changes above were made after
their first pass.** Check 10 of the review asks for both to have run after the last edit, and a fix
made in answer to a review is still an edit. The pipeline-rules reviewer found no violation of any of
the ten non-negotiable rules on either pass. The plan-conformance reviewer confirmed all seven changes
met on both passes, and confirmed the four problems from its first pass fixed. Between them the second
pass raised one code defect and six smaller items, and six of the seven are recorded below. The
seventh is the `report.xlsx` page column, which is left alone on purpose and is in the list given to
Oliver Zarate.

**`ingest/page_labels.py` kept a page whose printed number equals its PDF position.** The PDF-label
path dropped those; the page-text path did not. Measured on the samples: `samples/Example Protocol.pdf`
produced 97 entries, all 97 of them a page printing its own position, and
`samples/Synthetic_RFP_NEOD001.pdf` — half of the current test pair — produced 5, of which pages 1 and
2 were such pages. `report.pdf` was right anyway, because `format_page_ref` compares the two numbers
before printing a second one, but `extraction.json` dumps the whole `Provenance`, so run 3 carries
`"printed_page": "1"` on 29 of its 374 provenance entries, against a schema comment that promises the
field is None whenever the two numbers agree; and INGEST's log line counted those pages as printed
numbers found. The filter now runs at the end of `read_page_labels`, **after** the rise check and not
inside the comprehension, because a document whose pages 1 and 2 both print "1" has been misread and
dropping page 1 first would leave "page 2 prints 1, page 3 prints 2", which rises, so the misread map
would have been kept. `tests/ingest/test_page_labels.py::test_a_repeated_number_keeps_nothing` is the
test that failed and said so, and two new tests cover a document numbered from its first page. After
the fix: `Example Protocol.pdf` 0 entries, the synthetic RFP 3, the amendment 133 and unchanged. No
page reference in `report.pdf` moves, so this needed no run and no re-render.

**`docs/ARCHITECTURE.md` was stale for change 6, which the first pass had missed.** §4.2c is where a
reader goes to learn what MARK_OTHER_STUDY removes and it recorded nothing about the narrowing Angus
Gray asked for, while two lines elsewhere were now wrong: §2's summary called the node one that removes
passages describing "a different study", which after change 6 is too broad, and §4.2b's paragraph on
section 1.3.2 said "the section is not meant to go whole: MARK_OTHER_STUDY cuts the five sentences
about study NEOD001-001", which the rewritten prompt and run 3's own `other_study` verdict both
contradict. §4.2c gained a paragraph stating the criterion, why it is a budget question, that "ongoing"
alone does not make a study a companion one, and why the worked examples have to match the document
punctuation included. The other two lines are corrected, the second with its history, because it was
written from the two stage-4 runs of 2026-10-02 where the model answered `mixed`.

**Two more documentation corrections, both about the same sentence.** `gate/__init__.py`'s module
docstring said the budget_driver row is "deliberately stricter than ARCHITECTURE.md's own table, which
only names 'budget_driver with any disagreement' alongside conflict/reconcilable" — a cross-reference
to wording this very change set deleted from §4.9. §4.9's matching paragraph said "stricter than the
table's last row", and the clause is now in the table's third row, which change 4 moved by inserting
the `follows_conflict_of` row after it. Both now say the same thing: the table states the rule, "any"
means any adjudicated verdict including `not_a_conflict` and `reconcilable`, and there is nothing left
for the code to be stricter than. §3's `Document` stub also gained `page_labels` and `removed`, the two
fields a reader skimming that stub would otherwise not know exist.

**Three tests added for rules that only a comment was holding up.** Nothing asserted that an integer
`0` is not read as a record saying nothing: non-negotiable rule 3 rested on the `isinstance(value,
str)` guard in `_says_nothing` and a comment. Two tests now use the real pair run 3 found — 0 interim
analyses against 1 — and a `0` against a `not_specified`. The third pins the premise that keeps
Appendix A's note line narrow. The plan-conformance reviewer read `notes=rf.notes if (rf.derived_from
or rf.contradiction is None)` as broad enough to start printing the extraction model's own notes, which
`config/fields.yaml` asks for on some fields including `interim.count`. It is not: DERIVE, ADJUDICATE
and `reconcile/rules.py` are the only three writers of `ResolvedField.notes`, ADJUDICATE's rows all
carry a contradiction, and none of RECONCILE's four `ResolvedField` calls copies a record's note
across. The condition is therefore left as it is and
`tests/reconcile/test_reconcile.py::TestARecordsOwnNoteStaysOnTheRecord` makes that a guarantee rather
than an accident. One test class was also renamed: `TestTheTwoSampleDocuments` in
`tests/ingest/test_page_labels.py` opened neither sample document, and a name claiming coverage it does
not give is the thing the real-document rule exists to prevent.

**Both gate reviewers were then run a third time, for the same reason as the second.** Answering the
second pass was itself an edit, and the review asks for both reviewers to have run after the last one.
Neither found a rule broken or a change unmet. Six things came out of it, none of them a defect in the
pipeline. A figure was wrong in two comments: the 29 redundant `printed_page` entries in run 3 do not all
read `"1"` — 18 read `"2"` and 11 read `"1"`, because pages 1 and 2 of the synthetic RFP both print their
own position — and the sentence read as if 29 of the file's 374 provenance entries were affected when the
file holds 431, of which 374 carry a printed number at all. `domain/schemas.py` dated the empty
`page_labels` map "before 2026-10-07" when the change landed on the 8th, so the starting run is included
too. `docs/ARCHITECTURE.md` §3 said three printed-number fields "are `str`, not `int`" when there are
four of them across three classes, and the only `int` is a `page_labels` key.

Two gaps in coverage were real and are closed. The page-label fix had no real-document coverage at all:
`tests/graph/test_ingest_run_dir.py::test_ingest_records_the_number_printed_on_each_page` asserted pages
3, 5 and 4 and said nothing about pages 1 and 2, which are exactly the two the fix removes, so it passed
identically with and without the fix. One line asserting both are absent closes it, and it has teeth —
`_printed_on_page` reads "1" and "2" off those pages. And `TestARecordsOwnNoteStaysOnTheRecord` claimed
to pin four code paths while testing one. RECONCILE builds a `ResolvedField` in five places, not four:
the four in `reconcile/__init__.py` pass no `notes`, and `reconcile/rules.py:102` passes the rule's own
`because` sentence. Two tests were added for the all-silent path and the collection path — the one where
a note would be worst, because the members come from several records — leaving only `_not_found`, which
has no record to copy from. The claim in `render/report_model.py`'s comment was corrected to match.
**The fourth pass, on 2026-10-11, found the all-silent test was not one.** It built its records with the
class's `_record` helper, which marks every record found, so they went down the single-answer path at
line 168 and the test asserted the `"not_specified"` string that path leaves as the value. Line 120 had
still never run under a test. The test now builds `status="not_specified"` records with no value and
asserts the `value=None` only line 120 produces; it fails if those records are marked found again.

**Two latent risks were left in the code on purpose, each recorded where it lives.** `_from_pdf_page_labels`
drops a declared label equal to its own page position while reading, before `_rises` sees it, so the
misread that the page-text path's ordering guards against would still slip through a PDF *declaring*
labels 1, 1, 2. Neither sample document declares any labels, and the comparison cannot simply be deleted
because PyMuPDF returns the page position as a string for a file with no label tree, which is how "no
labels at all" is detected. Separating those two meanings is a change to how PDFs are read, which nothing
in this review asked for. And `render/report_model.py:_dismissed` selects Appendix B's rows as the
complement of `NEEDS_DECISION` while its docstring named `gate.DISMISSED_VERDICTS`; the two are
equivalent, because `Contradiction.verdict` allows only three verdicts and None, but a fourth added to
neither tuple would be printed as dismissed while GATE flagged its field for review. Which verdicts reach
that appendix is change 1's own subject, so the docstring was corrected to describe the code and the risk
reported rather than the selector quietly rewritten.

**Measured at the end.** `scripts/check_review_2026_10_07.py` reports 8 pass, 0 fail on both run 3
and the re-rendered report, and still correctly fails checks 1, 2, 3, 4, 5 and 7 on the starting run
`r-20261007-140411`. `ruff check .` passes. The test suite is 962 passed, 1 skipped — 942 when run 3
was made, plus the twelve in the new real-document test, one for the renamed Appendix B reason, two for
a page that prints its own position, three for the rules the second reviewer pass found resting on a
comment, and two for the two `ResolvedField` paths the third pass found uncovered. On 2026-10-11 it is
963: run `r-20261009-225414` added a folder under `runs/`, and `test_nothing_lost_on_a_real_run` runs
once per folder.
`mypy src/rfp_intake/domain src/rfp_intake/graph`, which is what `.github/workflows/ci.yml` line 41
runs, finds no issues; `mypy .` reports 184 errors, the same total as before this work, so none of them
is new. **An earlier draft of this paragraph said none of the 184 is in a file this work changed, and
that was wrong**: `tests/render/test_pdf_renderer.py`, `tests/render/test_json_renderer.py` and
`tests/render/test_report_model.py` all carry some. Every one is the pattern those files already used —
an untyped pytest fixture parameter, a `type: ignore` mypy now calls unused, a call to an untyped
helper such as `_conflict_state`, and one name redefined inside `assert_nothing_lost` — and none is in
a line this work added. The repository is `strict = true` in `pyproject.toml` while
`.github/workflows/ci.yml` line 41 runs mypy over `src/rfp_intake/domain` and `src/rfp_intake/graph`
only, which is why a suite this size can sit at 184 and CI still pass.

**Coverage did not fall.** The starting run found 39 of its 41 fields; run 3 finds 40 of 43. Three
fields found in the starting run were removed by changes 4 and 5 (Interim analyses planned, Number of
monitoring visits by type, On-site vs remote monitoring mix) and four fields added by change 5 were
found in run 3 (site initiation, interim monitoring, close-out and pharmacy monitoring visits). So
39 - 3 = 36 kept variables found before, and 40 - 4 = 36 kept variables found now.

**Two deliberate losses of evidence, both worth revisiting.** Where a real conflict remains, the
`Contradiction` built in `reconcile/__init__.py` carries only the competing records, so the page where
a document said "not specified" is no longer cited in the Disagreements positions — the record itself
is untouched in `extraction.json`. And a resolution rule discards the target field's extracted records
and contradiction from `resolved` and `contradictions`, which is how First-in-human reads No at 100%
with the study-phase passage as its evidence. Neither breaks a non-negotiable rule; both were flagged
by the pipeline-rules reviewer as inconsistencies rather than violations.

**`Site selection visits` reads Not found on run 3.** The synthetic RFP calls them "Site
Qualification Visits" and its figure spans three lines of a two-column table. The alias is in
`config/fields.yaml`; the extraction still misses it.

### Done 2026-10-03: ADJUDICATE picks a record, not a document — approved, built, and one live run

**Oliver approved changing ADJUDICATE on 2026-10-03, and that approval overrides
`docs/PLAN_2026-10-02.md` section 5, which lists ADJUDICATE under "Do not change".** This paragraph is
the record of that decision, which Oliver asked for in the same instruction. The override is specific to
picking the winning record; everything else section 5 holds back is still held back. The two changes
Oliver asked for were: add "Placebo" to the `blinding_monitoring` headings and "placebo" and "matching"
to its keywords in `config/fields.yaml`; and make ADJUDICATE pick the winning **record** rather than the
winning **document**, using the record numbers its prompt already shows, falling back to highest
confidence.

**What changed in ADJUDICATE.** `AdjudicationResult.winning_doc_id` is replaced by
`winning_record: int | None`, the 1-based number of a candidate in the list the prompt already numbers
with `enumerate(..., start=1)`. `_handle_reconcilable` resolves it through a new `_winning_index`, which
takes the named record when `1 <= winning_record <= len(records)` and otherwise falls back to the
highest-confidence record, earlier record winning a tie, so the outcome is deterministic. 0 and negative
numbers are rejected rather than used as Python indices, which would silently pick a record.
`Contradiction` gained `winning_record_index`, so `extraction.json` now records which record a
`reconcilable` value came from; `winning_doc_id` is still written beside it and still carries the
`conflict` path's answer. The prompt now says to name a record and not a document, and its candidate
header reads "CANDIDATE RECORDS, numbered — several may come from the same document".

**One change beyond what Oliver asked for, made deliberately: `_find_record` now returns the
highest-confidence record from the winning document rather than the first one.** `_find_record` is only
reached on the `conflict` path, where `reconcile/precedence.py` names a document and not a record, so the
record-number change does not touch it — but taking the first match is the same defect in the same
function family, and it printed the earliest page's quote against a value precedence had chosen. Nothing
else in the pipeline depends on `_find_record` returning the first match.

**The claim in the old to-do item 4 that this needed "changing the model, ADJUDICATE's prompt and GATE's
reading of the result" was wrong about GATE.** GATE does not read `winning_doc_id` or
`winning_record_index` at all — `gate/__init__.py` reads the verdict, the confidence and whether the
field is a `budget_driver`. No GATE change was made or needed.

**The record-level fix works, and a live run proves it.** In `r-20261003-001602-stage5b`,
`visits.schedule_present` resolves to `yes_appendix` at confidence 0.99 with `winning_record_index: 3` of
5 records. The same field in `r-20261002-232830-stage5` resolved to `no` from page 13 at 0.95, beating
page 111's `yes_appendix` at 0.99 purely because page 13 came first in the same document. This is the
defect, fixed, on real documents.

**The live run `r-20261003-001602-stage5b` met one of Oliver's four criteria.** 00:29:55 → 00:39:02 UTC
on 2026-10-03, about 9m07s, same two documents. 92 resolved rows, 11 contradiction clusters, 21 EXTRACT
errors, 26 of the original 36 fields with a confirmed row.
1. `blinding.placebo_matching` is `not_matching_stated` — **failed.** It is `None`, `needs_review`.
2. `blinding.unblinded_staff_stated` is `yes` — **failed.** It is `not_specified`, confirmed at 0.85.
   (Note that this field has no value `yes`: `config/fields.yaml` gives it `"true"`, `"false"` and
   `not_specified`, because `yes` and `no` are YAML booleans and every other yes-or-no field in the file
   uses `true`/`false`. The criterion was read as asking for the affirmative, `"true"`.)
3. `study.phase` is still one confirmed Phase 3 — **passed.** One row, `phase_3`, confirmed, confidence
   1.0.
4. At least 27 of the 36 original fields confirmed — **failed at 26**, up from stage 5's 25 and one below
   stage 4b's 27.

**Criterion A failed for a new reason, and it is the more interesting one: the two test documents
genuinely disagree about the placebo.** The hint change did its job — protocol page 53's "A matching
placebo will not be provided for this study." was extracted at confidence 1.00, where stage 5 never saw
it. But the synthetic RFP page 3 says "Placebo:  Matching placebo for NEOD001" at confidence 1.00.
ADJUDICATE correctly returned `conflict`, and `reconcile/precedence.py` declined to choose under
`no_silent_resolution`, so the field is `None` and flagged. **That is the pipeline behaving correctly on
contradictory inputs, not a defect.** Criterion A as written cannot pass while the RFP says the opposite
of the protocol: either `samples/Synthetic_RFP_NEOD001.pdf` is wrong and should be corrected, or the
criterion should be "a high-severity conflict naming both sentences".

**Criterion B failed because of the approved `config/fields.yaml` change itself.** The ADJUDICATE fix
could not help, because there was nothing left to adjudicate: `blinding.unblinded_staff_stated` has one
record in this run, against four in stage 5, and no contradiction cluster at all. Adding "Placebo" to the
group's headings pushed `5 SUBJECT SCREENING AND RANDOMIZATION` and `6.5.1 Study Drug` out of the top
three sections PLAN keeps, and pages 52 and 54 — where "the Unblinded Pharmacist or their designee" is
written — went with them. The one surviving record answers `not_specified` while quoting the placebo
sentence from page 53, which is EXTRACT attaching the wrong sentence to the field; it is confirmed at
0.85 because nothing contradicted it, so **this field is now confidently wrong rather than flagged,
which is worse than stage 5's answer.** The measurement is in to-do item 4c above. The 2026-10-02 to-do
item 4b called this config change "safe"; that was wrong, and item 4b above now says so.

**Tests: 855 passing, 1 skipped**, `ruff check .` clean, `mypy` clean on the three changed source files
(the 2 pre-existing mypy errors in `llm/mock.py` lines 25 and 43 are untouched). Eight new tests, all in
`tests/adjudicate/test_adjudicate_node.py` and `tests/plan/test_plan_sections_samples.py`:
`TestHandleReconcilable` is rewritten to six cases covering the chosen record, a later record from the
same document (the stage 5 `blinding.unblinded_staff_stated` case, as a regression test), the
no-record and out-of-range fallbacks, the 0-and-negative guard, and the tie going to the earlier record;
`test_the_quote_is_the_best_record_from_the_winning_document` covers the `_find_record` change; and
`TestThePrompt` asserts the prompt asks for `winning_record` and never for `winning_doc_id`.
`tests/plan/test_plan_sections_samples.py::test_the_placebo_sentence_reaches_the_blinding_group` asserts
the config change on the real protocol PDF, offline. `llm/mock.py`'s adjudicate fixture was updated to
`winning_record`, so the suite stays network-free. Pre-change run folders still re-render:
`scripts/rerender_report.py r-20260827-180418` ran clean, because `winning_record_index` defaults to
`None`.

**Two things found while doing this that are not defects in this change.**
First, **the earlier claim that two fields lost their confirmed value between `r-20261002-222824-stage4b`
and `r-20261002-232830-stage5` was wrong — three lost one and one gained one**, which the stage 5 entry
below states correctly; "two" is only true if you count the two of the three that are budget drivers.
The cause of each was checked and **none of the three was ADJUDICATE's doing**, so this fix was never
going to recover them: `design.parts` lost it because EXTRACT wrote a differently worded scope label that
`normalize/scope.py` folded into the protocol's `arm:NEOD001` bucket, and `gate/__init__.py:94` forbids a
budget driver holding more than one value from confirming; `monitoring.unblinded_rationale` lost it
because EXTRACT returned a different passage from the same RFP page 4 and rated itself 0.75 against
`CONFIDENCE_CONFIRMED` 0.80; and `timeline.total_duration` lost it because the two confirmed
protocol-page-47 records are simply absent, with no validation error, no set-aside section and no removed
passage covering page 47. Run-to-run variation in EXTRACT is the whole of the first two.
Second, **the golden set in `eval/golden/` is stale and gives no signal between runs.** It expects Phase
1, multi-part, first-in-human and 120 subjects; the two sample documents are a Phase 3, two-arm,
260-subject trial that is not first-in-human. Scoring a run against it today measures the fixture, not
the pipeline.

### Done 2026-10-03 (second change): per-group `top_k`, a corrected placebo criterion, and the credential question

Six things Oliver asked for on 2026-10-03, after reading the stage 5b report above. The first four are
changes; the fifth is a correction to how accuracy may be quoted; the sixth was a question whose answer
turned out to be "your assumption is wrong", and is written up last.

**1. The number of sections PLAN keeps is now a per-group setting in `config/fields.yaml`, not a constant
in Python.** `DEFAULT_TOP_K` is gone from `plan/__init__.py`. `GroupDef` in `domain/registry.py` gained
`top_k: int = Field(default=3, ge=1)`, `_plan_group` reads `registry.get_group(group_id).top_k`, and
`_nothing_scored` takes it as an argument. The default lives in one place, the `GroupDef` field, so the
registry is the only thing that says how many sections a group gets — this is non-negotiable rule 4
applied to a number that was behaving like a hardcoded field. The `config/fields.yaml` header block now
documents the five group keys, which it never did.

**`blinding_monitoring` is `top_k: 7`, and 7 is measured rather than chosen.** Ranked by score for this
group on `samples/Example protocol 2.pdf`, after SET_ASIDE_SECTIONS: (1) title page, (2) `6.3 Placebo`
p53, (3) PROTOCOL SYNOPSIS, (4) `5 SUBJECT SCREENING AND RANDOMIZATION` p52–53, (5) `6.5.1 Study Drug`
p54–55, (6) `3.1 Study Design` p42–43, (7) `6.2 Shipping, Storage, and Handling` p53. Oliver asked for
"high enough that pages 52, 53 and 54 are all read". **5 satisfies that literally and is still not
enough**, because page 53's text is divided among four sections and PLAN chooses sections, not pages: at
5 the group reads parts of page 53 but not section 6.2's part of it. 7 is the smallest value that reaches
all three sentences the two failing criteria depend on. It costs this group a 45,601-character excerpt
against 34,760 at 3 on the protocol, a 31% increase for one of nine groups — and **no extra model calls**:
PLAN still makes 33 tasks across the two documents, 5 of them for this group, because the four extra
sections pack into the tasks that already existed under the token budget. Measured both ways on 2026-10-03.

**2. The placebo criterion is now "a high-severity conflict naming both sentences".** Oliver's reason, in
his words, is that "the two documents genuinely disagree", which is what the stage 5b run showed. The
assertion is `TestThePlaceboDisagreement` in `tests/adjudicate/test_adjudicate_node.py`: given the two
real records, the outcome is verdict `conflict`, severity `high`, both sentences still on the
contradiction's records, resolved value `None`, status `needs_review` and `winning_record_index` `None`.
The precondition — that EXTRACT is shown both sentences — is asserted separately and offline on both
PDFs, protocol and synthetic RFP.

**Severity is not enforced in code, and this change did not start enforcing it.** The `config/fields.yaml`
header says a `budget_driver` disagreement "is severity=high and forces needs_review". The second half is
enforced, in `gate/__init__.py`. The first half is not: `adjudicate/__init__.py:111` copies
`result.severity` straight from the model, and the only thing asking for `high` is a sentence in
`adjudicate/prompt.py`. So a budget driver can be adjudicated `low` and nothing corrects it. The test
above supplies `severity="high"` as the model's answer rather than deriving it, which is honest about
what is checked. **Not fixed here** — it changes how the report orders disagreements, and that is a
separate decision.

**3. The `blinding.unblinded_staff_stated` hint is tightened, and not in the words Oliver used.** He asked
that `not_specified` be used "only when the document explicitly says no unblinded staff are needed". That
sentence is the definition of `false` in this field's enum, so taking it literally would make
`not_specified` and `false` the same answer and leave nothing for a document that is simply silent —
against non-negotiable rule 3 (`not_found` ≠ `not_specified` ≠ `0`), and it would break the
`blinding.placebo_assumption` rubric, which reads this field precisely to tell a stated fact from silence.
What the hint now says instead, which fixes the failure actually observed: answer only from a sentence
about who may know the allocation or who handles unblinded drug; use `not_specified` **only** where the
text given does discuss blinding, randomisation or drug handling and still never says who may be
unblinded; where nothing in the excerpt is about unblinded staff, return status `not_found` and no value;
and never quote a sentence that does not mention staff, unblinding or drug handling in support of
`not_specified`. The last clause is the stage 5b failure written as a prohibition — the model answered
`not_specified` while quoting the placebo sentence, and GATE confirmed it at 0.85.

**4. The confirmed-fields floor is 26.** Four live runs, in order, gave **26, 27, 25, 26** — stage 2's
baseline `r-20261002-200521-stage2` 26, stage 4b `r-20261002-222824-stage4b` 27, stage 5
`r-20261002-232830-stage5` 25, stage 5b `r-20261003-001602-stage5b` 26. The spread is 2 fields across
four runs whose differences were not meant to touch most of them, so a floor of
27 — which stage 5b was judged against and failed by one — was set at the top of the observed range and
would fail about half of all runs that changed nothing. 26 is the bottom of the range excluding stage 5's
25, which had a known cause. `docs/PLAN_2026-10-02.md:224` already said 26; this records why it stays
there and that stage 5's 27 was the anomaly. **A run below 26 is a real regression and should stop the
work.** Rows are not fields: count fields holding at least one `confirmed` row.

**5. The golden set in `eval/golden/` describes a different study and must be rebuilt before any accuracy
figure is quoted from it.** It expects Phase 1, multi-part, first-in-human and 120 subjects. The two
sample documents are a Phase 3, two-arm, 260-subject trial that is not first-in-human. Every number
`rfp_intake.eval` produces against it today measures the fixture, not the pipeline. **No accuracy,
precision, recall or severity figure may be quoted from `eval/golden/` — to Angus, to Oliver, in a report
or in this file — until the fixture is rebuilt from the two sample documents.** Until then the only
honest measures of a run are the ones used above: fields with a confirmed row, contradiction clusters,
EXTRACT errors, and named field-by-field comparisons between runs. Rebuilding it is not in any stage of
`docs/PLAN_2026-10-02.md` and needs to be scheduled.

**A test was passing for the wrong reason, found by accident and now fixed.**
`tests/plan/test_plan_sections_samples.py` built its documents by running FIND_SECTIONS and stopping,
but the graph runs SET_ASIDE_SECTIONS between FIND_SECTIONS and PLAN — so every test in that file scored
a section list production never sees. With set-aside applied, 57 of the protocol's 176 sections go and the
ranking changes, which is why `top_k: 7` measured on the real pipeline looked wrong against the test.
Correcting the fixture broke `test_the_other_studys_phase_is_not_sent_to_the_phase_group`, and the break
is the real finding: **SET_ASIDE_SECTIONS does not drop section 1.3.2 "Clinical Experience", and once the
57 sections are gone 1.3.2 rises into `phase_population`'s top three, so PLAN does choose it.** PLAN is
not what protects `study.phase` from the referenced study's phase; MARK_OTHER_STUDY is, by putting the
passage in `Document.removed`, which `section_page_texts` cuts out of both PLAN's scoring and EXTRACT's
excerpt. The test is renamed `test_the_other_studys_phase_is_kept_out_by_mark_other_study_not_by_plan`
and now asserts both halves — that PLAN chooses the section, and that removing the passage the way that
node does takes the text out — so the claim it makes is one that holds in production. The protection was
never missing; what was missing was any test that located it correctly.

**6. The credential question: the assumption is wrong, and there is a worse problem behind it.** Oliver
asked whether the project works only with `AWS_BEARER_TOKEN_BEDROCK` and fails with the standard
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` that Angus uses. It does not. **Standard AWS access keys
already work and always have**, and the code that decides is `llm/provider.py` `_build_bedrock`, which
passes a model and a region and no credential at all:

```python
# Credentials come from the standard AWS chain (env, profile, instance role).
# Deliberately not re-plumbed through Settings — one credential source.
model: BaseChatModel = ChatBedrockConverse(
    model=binding.model, region_name=settings.bedrock_region
)
```

Nothing in `src/rfp_intake/` reads `AWS_BEARER_TOKEN_BEDROCK`, by grep. botocore reads it, at
`botocore/utils.py:3616` and `botocore/client.py:619` (`_evaluate_client_specific_token`), per Bedrock
operation. Measured with dummy values, the request intercepted at `before-send` so nothing was sent, and
only the first word of the Authorization header read: access keys alone → `AWS4-HMAC-SHA256`; bearer
alone → `Bearer`; **both set → `Bearer`**; neither → sigv4, which then fails. So botocore already
implements exactly the preference Oliver asked for, and **the requested change to credential selection
was not made, because the behaviour it would add is the behaviour that already exists.** Writing it into
`llm/` would be a second credential source for the same decision, which is what the comment above warns
against.

**The real problem is the third line of that measurement: a bearer token silently beats standard access
keys, and no run said which kind it used.** A stale `AWS_BEARER_TOKEN_BEDROCK` left in a CML project's
environment makes Angus's correct access keys look rejected, and the analyst-facing failure is
`credential_rejected` either way — the same message runs r-20260831-150720 .. r-20260901-140857 produced
when the bearer token expired. So the reporting half was built: `llm/credentials.py`
(`bedrock_credential_report`) names which of the four cases applies, and PREFLIGHT logs it as
`model_service_credential` before the probe, warning rather than informing when a credential is being
shadowed or when there is none at all. A Bedrock probe failure also carries the sentence in its operator
`detail`. **It reports the names of environment variables that are set and never any part of a value**,
asserted by a test that rejects every 8-character fragment of each dummy secret from every field of the
report. Both changes are inside `llm/`, as Oliver required.

**7. The live run `r-20261003-143058-topk7` cleared the floor: 27 of the original 36 confirmed.** 14:31:08
to 14:41:39 UTC, 10m31s, on `samples/Example protocol 2.pdf` and `samples/Synthetic_RFP_NEOD001.pdf`,
`state: completed`, no run-level error. Measured by `scripts/check_run_acceptance.py`, written for this
run because the count had been read off reports by eye; it reproduces the 26 recorded above for
`r-20261003-001602-stage5b`, so the two numbers are comparable. That script holds the only list of the
five stage-5 field names anywhere outside `config/fields.yaml`; it is a diagnostic that feeds nothing in
the pipeline, so non-negotiable rule 4 stands, and its docstring says why the set cannot be derived (the
registry carries no stage marker).

Field by field: `study.phase` is one confirmed `phase_3`, unchanged. `blinding.placebo_matching` is a
**high-severity `conflict`, 4 records, `winning_record_index: None`, value `None`, `needs_review`** —
the criterion of item 2 above, met on live evidence, and the explanation names both sentences: "A
matching placebo will not be provided for this study" (amendment p.53) against "Matching placebo for
NEOD001" (RFP p.3). `blinding.unblinded_staff_stated` is **`true`** — note the registry's values are
`true`/`false`/`not_specified`, not the `yes`/`no` of docs/PLAN_2026-10-02.md stage 5 item 6, so `true`
is this field's spelling of Oliver's "yes".

**`top_k: 7` did what it was raised to do, and the run cannot prove it did it alone.** The field's cited
pages went from `[53]` in `r-20261003-001602-stage5b` to **`[4, 19, 52, 53, 54]`** here, so pages 52 and
54 were read for the first time. But item 1 (`top_k`) and item 3 (the hint) both bear on this one field
and both changed before this run, and the earlier run already saw page 53 and still answered
`not_specified`. So the new pages and the tightened hint are confounded: this run shows the pair works
and attributes the fix to neither. Separating them needs a run with `top_k: 7` and the old hint, which
has not been done.

**It is `needs_review`, not `confirmed`, and that is the designed behaviour, not a regression.**
`blinding.unblinded_staff_stated` is a `budget_driver` and drew a contradiction (8 records, verdict
`reconcilable`, winner 2), and `gate/__init__.py` sends any budget driver with a contradiction to
`needs_review` whatever its confidence. The previous run had this field **`confirmed` at 0.85 with the
wrong value**; this run has the right value flagged for a human. That is the better of the two outcomes
and the reason the floor is counted over the original 36 rather than this field alone.

**Unchanged and pre-existing: 21 `EXTRACT` validation errors, all `quote_not_found_in_excerpt`** (for
example `doc-0119597c:visits:visits.frequency_by_period`) — the same count as `r-20261003-001602-stage5b`,
so nothing here made it worse and nothing here fixed it. Contradictions went 11 → 12. `report.pdf` and
`report.xlsx` both rendered. PREFLIGHT logged `model_service_credential credential_kind=bearer_token
env_vars_set=['AWS_BEARER_TOKEN_BEDROCK'] shadowed=[]`, which is item 6 working on a real environment
with no part of a value printed.

### Done 2026-10-02: Stage 5 — five new fields and the placebo rubric; the live test FAILED
Stage 5 of `docs/PLAN_2026-10-02.md`, all ten items built, 40 new tests, tree green —
**and the live Bedrock run did not meet either of the stage's two pass criteria.** Both failures
are in code the plan told me not to change, so the fix is a decision for Angus, not something I did.

**What was built.** Four new extracted fields in `config/fields.yaml` — `study.primary_objective`
(copied verbatim from the protocol), `blinding.placebo_matching`, `blinding.unblinded_staff_stated`,
`visits.schedule_present` — plus a fifth derived field, `blinding.placebo_assumption`, with a rubric in
`src/rfp_intake/derive/rubric.py` (`compute_placebo_assumption`) registered in `DERIVE_RUBRICS`.
The rubric follows `docs/ANALYST_PROCEDURE_PROTOCOL.md` section 6, rules 1 to 4, with rule 6's
contradiction checked before rule 2 so that a stated matching placebo and stated unblinded pharmacy
staff cannot both be reported as settled. Item 1 of the stage, done first at Angus's instruction,
added "Background on", "Nonclinical" and "Rationale for Dose Selection" to `config/sections.yaml`
rather than making MARK_OTHER_STUDY cut the background.

**The placebo check is a rubric and not a RECONCILE check on purpose.** RECONCILE compares values of
the same field across documents. A stated matching placebo contradicting stated unblinded staff is a
disagreement between two different fields, which RECONCILE cannot see.

**Test count: 848, not 806.** The 806 recorded for stage 4 was one short — HEAD collects 807 in a
clean worktree, measured by checking HEAD out into `git worktree` and collecting there. Stage 5 adds 40
in three blocks (derive 22, registry 10, sections 8) and removes none, which is 847; the 848th is the
stage 5 run folder itself, because
`tests/render/test_report_model.py::test_nothing_lost_on_a_real_run` is parametrised over every folder
in `runs/` holding an `extraction.json` — 20 of 27 after this run, 19 before it, not 17. The earlier
count of 17 filtered on the name prefix `r-2026` and so dropped `r-demo-rfp-protocol-pair` and
`r-listfix-175318`. Nothing stopped being collected. Final state `847 passed, 1 skipped`,
`ruff check .` clean, `mypy` clean.

**The live run: `r-20261002-232830-stage5`**, 23:29:19 to 23:38:30 UTC, about 9m11s, on
`samples/Synthetic_RFP_NEOD001.pdf` plus `samples/Example protocol 2.pdf`. 99 resolved rows across all
41 fields; 25 fields have a confirmed row; 19 errors (16 `quote_not_found_in_excerpt`, 3
`numeric_field_no_digits_not_specified`); 13 contradiction clusters; 57 sections set aside; one removed
passage of 1,515 characters.

**Criterion A failed.** `blinding.placebo_matching` came back `not_specified`; the plan wanted
`not_matching_stated`, because protocol section 6.3 Placebo says "A matching placebo will not be
provided for this study." **Two independent causes, both proven:**

1. *EXTRACT never saw the sentence.* It is in section `6.3 Placebo`, which scores **0.000** against the
   `blinding_monitoring` group's `search_hints` — neither "Placebo" as a heading nor "placebo" as a
   keyword is in that group's hints, and the section's own two sentences contain none of the sixteen
   keywords that are. PLAN keeps the top three sections scoring above zero, so 6.3 was never sent. The
   blinding task that covers pages 52 to 55 reaches them through sections `5 SUBJECT SCREENING AND
   RANDOMIZATION` and `6.5.1 Study Drug`, which tile around 6.3 and leave its text out, so the page
   window is misleading: page 53 is in the window and the sentence is not in the excerpt. Reproduced
   offline by re-running INGEST, FIND_SECTIONS, SET_ASIDE_SECTIONS and PLAN on the same inputs. There is
   no page-number offset between the PDF and the provenance — three known quotes matched their cited
   pages exactly.
2. *ADJUDICATE then discarded the best record anyway.* See below.

**Criterion B failed, on cause 2 alone.** `blinding.unblinded_staff_stated` came back `not_specified`;
the plan wanted `true`. All four candidate records are from the one protocol: page 20 `not_specified` at
confidence 0.9, and pages 52, 54 and 54 all `true` at confidence 1.0, quoting "the Unblinded Pharmacist
or their designee (henceforth collectively referred to as the Unblinded Pharmacy Staff) will be provided
access to the treatment assignment." The adjudicator's own explanation said the three later extractions
were the right ones. The answer was still `not_specified`.

**The defect, which I did not fix: `src/rfp_intake/adjudicate/__init__.py::_handle_reconcilable`
identifies the winner by document, not by record.** It calls `_find_record`, which returns the *first*
record whose `provenance.doc_id` matches `winning_doc_id`. When several records for one field come from
the same document — the ordinary case for a 137-page protocol extracted per group over several excerpts
— the earliest page silently wins and the `max(confidence)` fallback never runs. `Contradiction` has a
`winning_doc_id` and no way to name a record, so this is a model change, not a one-line fix. The same
defect also decided `visits.schedule_present` (page 13 `no` at 0.95 beat page 111 `yes_appendix` at
0.99) and the second `timeline.total_duration` cluster. **`docs/PLAN_2026-10-02.md` section 5 lists
ADJUDICATE under "Do not change", so I stopped and reported rather than change it.**

**The rubric itself behaved correctly** on the inputs it was given: `blinding.placebo_assumption` =
"probably not matching; unblinded handling likely", explained by `ip.form=['infusion_iv'] ("Study drug
consists of IV NEOD001 or placebo.")`. That is the right practical answer for a budget, but it was
reached by rule 3's route inference rather than from the document's own sentence.

**SET_ASIDE_SECTIONS worked as item 1 intended.** 1.3 Background on NEOD001 was set aside by
`matched: background on`; Figure 1 and 1.3.1 Nonclinical Safety went with it as children; 1.4 Rationale
for Dose Selection was set aside too. The title page, the synopsis and 1.3.2's surviving sentences were
untouched, which `tests/sections/test_set_aside_samples.py` asserts.

**One number got worse: 25 of the original 36 fields have a confirmed row, against a floor of 26 and
27 at stage 4b.** `design.parts`, `monitoring.unblinded_rationale` and `timeline.total_duration` lost
their confirmed rows; `study.population` gained one. The 16 `quote_not_found_in_excerpt` errors are the
same symptom as deferred item 4a, and item 4a points at SET_ASIDE_SECTIONS, not MARK_OTHER_STUDY.

### Done 2026-10-02: Stage 4 — MARK_OTHER_STUDY, the text about a different study
Stage 4 of `docs/PLAN_2026-10-02.md`, and the fix for known problem 1. A twelfth node,
MARK_OTHER_STUDY, sits between SET_ASIDE_SECTIONS and PLAN and removes text that describes a study
other than the one being budgeted. Code in `src/rfp_intake/other_study/` — `mark_other_study_node` and
`other_study/prompt.py`. The node contract is `docs/ARCHITECTURE.md` §4.2c.

**This is the one of the three new nodes that calls a model**, under a new role `other_study_check` in
`config/models.yaml`, bound exactly like `classify`. The reason it needs a model: the passage that
breaks `study.phase` is section 1.3.2 "Clinical Experience", an ordinary heading in an ordinary place
inside the background, where the neighbouring sections are about this study and are needed. No list of
headings can catch it, and matching study numbers in the text does not work either because the protocol
prints its own number beside the other one.

**Removal happens in `sections/section_page_texts()`**, the single function PLAN's scoring and EXTRACT's
excerpt both go through to read a section's text, so neither PLAN nor EXTRACT knows MARK_OTHER_STUDY
exists. `Document.page_texts` is never edited: a `RemovedPassage` carries `TextSpan` offsets into it,
the same coordinates `Section` already uses. A removed span leaves `"\n\n"` — not nothing, because the
sentences either side would fuse into one nobody wrote, and not a marker like `[removed]`, because
EXTRACT validates quotes against this same text and any word injected here becomes quotable.

**Sections are not deleted; their text is.** A section whose text is wholly removed stays on
`Document.sections`, scores zero in PLAN and is never chosen. Keeping it is what lets `extraction.json`
and `report.pdf` name the heading each removal came from.

**One model call per batch of sections**, grouped up to `DEFAULT_TOKEN_BUDGET` — roughly ten calls for
the 137-page sample protocol, the same order of magnitude PLAN already spends. A section longer than the
whole budget gets its own batch rather than being split, and a section under 200 characters is never
asked about, on length alone and never on keywords (keyword pre-filtering is what the plan says failed).

**Nothing is removed on the model's word alone.** Three verdicts: `this_study` removes nothing,
`other_study` removes the whole section's text, and `mixed` removes only the sentences the model copied
out — each of which must be located in the section's own text by `locate_text`, the same
whitespace-tolerant search FIND_SECTIONS uses to place a bookmark. **A sentence that cannot be located
is kept, not removed**, and logged as `other_study_sentence_not_found`, so a paraphrase costs nothing.
The prompt also says to answer `this_study` when unsure, because keeping a passage costs some tokens
while removing one wrongly loses a number the budget needs.

**`CLASSIFY` now also extracts the study title**, and `Document` gained `title`. MARK_OTHER_STUDY names
the study in its prompt from `Document.protocol_id` and `Document.title`, preferring the protocol
document's values over an RFP's, because a model given two study descriptions and no statement of which
one is being asked about has no way to answer. No extra model call: the title comes back in the call
that already classifies the document, and the CLASSIFY prompt says it is the title of the study this
document is about, not any other study it mentions.

**A document is never emptied**, the same guard SET_ASIDE_SECTIONS has and for the same reason: if every
section with text came back `other_study`, nothing is removed and a `kind="validation"` `RunError` says
so (`other_study_would_empty_document`). PLAN would otherwise have nothing to score and every field
would be reported not found with no hint why.

**Removals are in `report.pdf` as Appendix C, while set-aside sections are not.** Appendix C lists each
removal with document, heading, pages, whether a whole section or part of one went, the model's
one-sentence reason and the first 300 characters of the removed text. Skipping the references is
housekeeping; deciding that a passage describes a different study is a judgement a reviewer may want to
overturn, and nobody can overturn what they cannot see. The whole text and the character offsets are in
`extraction.json` under `removed_passages`. Both readers of `extraction.json` —
`scripts/rerender_report.py` and `tests/render/test_report_model.py:load_run_state` — read that key with
`.get`, so a run from before 2026-10-02 re-renders exactly as it did before, with no Appendix C.

**Adding a role means editing five places**, which is worth knowing before adding the next one:
`LLM_ROLES` in `domain/model_routing.py`, `LLMRole` in `llm/provider.py`, `PROBE_ROLES` in
`llm/health.py`, `Settings.model_other_study_check` in `config/settings.py`, and `_routing_from_settings`
in `domain/model_routing.py`. `PROBE_ROLES` matters most: a role left out of it is a role whose first
failure happens deep into a paid run instead of at PREFLIGHT. `Settings` matters because
`_routing_from_settings` hardcodes the role list for the legacy path taken when `config/models.yaml` is
absent, so without that entry `get_llm("other_study_check")` would fail only in that configuration.

Both Streamlit pages now show twelve stages: `("MARK_OTHER_STUDY", "Removing text about other studies")`
was inserted into `_STEPS` in `app.py` and `app_v2.py`, which is the whole change.

**800 tests passing, 1 skipped** — 38 more than the 762 after stage 3, of which 37 are new tests and one
is `tests/render/test_report_model.py` being parametrised once per folder in `runs/`, where this stage's
live run is a new folder. `tests/other_study/test_other_study.py` is the new file: the plan's own offline acceptance test (a hand-built section in
the style of 1.3.2 is removed and recorded, asserted through `section_text` because that is the function
PLAN and EXTRACT actually read), plus the mixed-sentence rules, what is never removed, batching, the
study identity, the prompt and the node's error handling. `tests/graph/test_topology.py` gained a test
that `set_aside_sections → plan` is **gone** as well as that the two new edges exist — without that
assertion the graph would still run with the old edge in place, MARK_OTHER_STUDY might never be reached,
and the only sign would be `study.phase` still carrying another study's phase. `tests/render/`
test files gained Appendix C coverage in all three renderers. `ruff check .` passes and mypy strict is
clean on the files this stage changed.

**The live run passed all three of the plan's criteria. Run `r-20261002-213531-stage4` is the new
baseline**, on `samples/Example protocol 2.pdf` plus `samples/Synthetic_RFP_NEOD001.pdf`, 9m10s
(21:36:25 → 21:45:35 UTC). MARK_OTHER_STUDY itself took 92 seconds, checked 121 sections of the protocol
and 1 of the synthetic RFP, and removed 6,458 characters in 8 passages.

1. **`study.phase` is now a single row: `phase_3`, `confirmed`, confidence 1.0, asserted by both
   documents** (protocol page 11 and RFP page 2). The `phase_1_2` row is gone. In the previous baseline
   `r-20261002-200521-stage2` there were three rows, all `needs_review`, one of them `phase_1_2` from
   page 40, and `resolved_value` was `None`. **This is known problem 1 fixed**, and fixed by removing
   the text rather than by a prompt change.
2. **Appendix C of `report.pdf` names section 1.3.2 "Clinical Experience", Amendment p.39-40, whole
   section**, with the model's reason: "describes Study NEOD001-001, a different ongoing Phase 1/2
   open-label dose-escalation study with a data cutoff of 30 September 2015". Page 13 of 14.
3. **27 of the 36 registry fields have at least one `confirmed` row**, against the 26 the plan set as
   the floor and the baseline figure. 97 resolved rows, 74 confirmed, 8 contradiction clusters.

The planted `timeline.total_duration` disagreement is still caught — 42 months against 40, verdict
`conflict`, severity high — and **the study-level 42 months is now present as a value**, which known
problem 2 records as missing. `interim.planned` is still a high-severity conflict. Both are unchanged
behaviour, listed here because a run that removes text has to be shown not to have removed these.

**The eight removals, all on the protocol, none on the synthetic RFP.** Three whole sections — 1.3
Background on NEOD001 and Figure 1 (both nonclinical mouse and cynomolgus monkey work) and 1.3.2
Clinical Experience — and five single sentences out of PROTOCOL SYNOPSIS, 3.4.1.4.8.5 Pharmacokinetics,
10.5 Pharmacokinetic Analyses, Appendix 1 and Appendix 2. Three of those five are the same sentence
about pooling this study's serum concentrations with data from other NEOD001 studies. Read Appendix C of
`runs/r-20261002-213531-stage4/report.pdf` before deciding whether any of the eight was wrong; nothing
about them looks wrong to me, but the nonclinical sections are the judgement call — they describe prior
studies, which is what the prompt asks for, and they are also where a reader might expect the drug's
mechanism to be described.

**EXTRACT errors went from 5 to 27, and the cause is not established.** All 27 are the same
`quote_not_found_in_excerpt` that the 5 were, so no new kind of failure appeared, and 9 of the 27 are
still `visits.frequency_by_period`. Two things to keep straight before blaming MARK_OTHER_STUDY.
**This is the first live run that includes SET_ASIDE_SECTIONS**, which had no live run of its own, so the
comparison against `r-20261002-200521-stage2` spans two stages; PLAN produced 32 tasks here against 26
in the baseline, which is a planning change, not a removal. And **8 of the 27 errors are on the
synthetic RFP, where not one character was removed**, so removal cannot explain those. The one
mechanism worth checking first is the removal gap: a `mixed` removal inside PROTOCOL SYNOPSIS leaves
`"\n\n"` in a section the visits and timelines groups both read, and a quote that spanned the cut can no
longer be found. That is a hypothesis, not a finding — nothing has been measured. The field-level
outcome went up, not down (27 fields against 26, 74 confirmed rows against 62), which is why this is
recorded as the next thing to chase rather than as a regression that blocks the stage.

### Done 2026-10-02: Stage 4, second pass — the subject test in MARK_OTHER_STUDY's prompt
**Three of the five single-sentence removals above were about this study, not a different one.** Read
one by one, the five `mixed` removals from `r-20261002-213531-stage4` split three to two: the pooled
pharmacokinetic analysis sentence (PROTOCOL SYNOPSIS p.24, section 3.4.1.4.8.5 p.46, section 10.5
p.97), this study's own progression-confirmation procedure (Appendix 1 p.109, 370 characters) and this
study's own 0.03 ng/mL stratification threshold (Appendix 2 p.110, 158 characters) all have this study
as their subject and each one names outside work; the rest were bare citations. The model had read
"mentions another study" as "is about another study". The progression sentence is the one that mattered
most — it requires a repeated assessment at an interval the investigator sets, which is a visit a budget
carries.

**What changed: one prompt, no code.** `other_study/prompt.py` now says to judge a sentence by what it
is about rather than by what it mentions, lists the four shapes that keep a sentence with this study
(this study's samples including pooled ones, its procedures, its own numbers however they are cited, and
the sources of its own tables), carries those three real sentences as worked examples of what to keep,
and narrows the published-literature rule from "even when about the same drug" to "when the passage is
reporting what those studies did or found" — which still catches 1.3.2. Six tests in
`tests/other_study/test_other_study.py::TestThePrompt` hold each rule, including one that the
literature rule still bites, so a later edit cannot quietly lose 1.3.2. The five cases are written into
the stage 4 test in `docs/PLAN_2026-10-02.md`, which is the version a person should read.

**Live run `r-20261002-222824-stage4b` passed all four criteria.** None of the three sentences was
removed. Section 1.3.2 is still removed and still in the report's Appendix C. `study.phase` is one
`phase_3` row, `confirmed`, confidence 1.0, from Amendment p.11 and RFP p.2, with no `phase_1_2` row
anywhere. 27 of 36 fields keep a confirmed row, equal to the previous run and above the floor of 26.

**One real change beyond the three sentences: the run now removes 1,084 characters, not 6,458.** 1.3.2
is cut as five sentences on p.39 rather than as a whole section across p.39–40, and the two nonclinical
sections — 1.3 Background on NEOD001 and Figure 1 — are now kept. The five sentences cut from 1.3.2 are
the ones that carry the harm: the Phase 1/2 study number NEOD001-001, the 30 September 2015 data cutoff,
the 27 and 42 subject counts and the adverse-event summary. Keeping 1.3 and Figure 1 is a loosening,
and stage 5's first step — removing "Background" and "Rationale" from the `phase_population` search
hints — is the other half of the same fix, so it now matters more than it did. Resolved rows fell from
97 to 88 and clusters went 8 to 9; the new cluster is `monitoring.strategy_guidance`, verdict
`not_a_conflict`, so nothing was broken by it.

**806 tests passing, 1 skipped** — six more than the 800 after the first stage 4 pass, and the six are
the new `TestThePrompt` cases.

**The count was one short, and the gap is explained.** Collecting the suite at this commit in a clean
`git worktree` gives 807, not 806, so the 806 above was miscounted by one; the stage 5 entry records 847
measured the same way. `tests/render/test_report_model.py::test_nothing_lost_on_a_real_run` is
parametrised per run folder and takes every folder in `runs/` that has an `extraction.json` — 19 of the
27 folders, not the 17 counted earlier. The missing two are `r-demo-rfp-protocol-pair` and
`r-listfix-175318`, which were counted out by a search for folder names beginning `r-2026` rather than by
anything in the test. Nothing stopped being collected. Recount from the pytest summary line rather than
adding to this number.

### Done 2026-10-02: Stage 3 — SET_ASIDE_SECTIONS, the sections an analyst skips
Stage 3 of `docs/PLAN_2026-10-02.md`. An eleventh node, SET_ASIDE_SECTIONS, sits between FIND_SECTIONS
and PLAN and removes the sections `docs/ANALYST_PROCEDURE_PROTOCOL.md` section 4 says Angus Gray skips
when costing a protocol — amendment history, contents, glossary, eligibility criteria, adverse events,
committees, ethics, consent, publication, references, questionnaire appendices and about fifty more.
Pure Python, no model call. The node is `src/rfp_intake/sections/set_aside.py`; the loader and all the
matching rules are `src/rfp_intake/domain/section_policy.py`, reading `config/sections.yaml`.

**On `samples/Example protocol 2.pdf` it keeps 121 of 176 sections and sets aside 55 — 174,016
characters kept, 95,298 set aside, so 35% of the text no longer reaches PLAN.** The title page, the
protocol synopsis, "Table 1: Schedule of Events", the study design and section 1.3.2 all survive.

Four rules, in `set_aside.py`'s docstring and in `docs/ARCHITECTURE.md` §4.2b. Only a positively listed
heading is removed, so an unanticipated section survives; a listed section takes its children with it,
down to the next heading at the same or a higher level; `keep_if_contains` overrides both; and a
document with one section is left alone. `FRONT_MATTER_HEADING` and `WHOLE_DOCUMENT_HEADING` are never
set aside — FIND_SECTIONS invents both, so no list of real section names owns them. If every section
matched, the document is kept whole and a `validation` `RunError` says so, because PLAN would otherwise
have nothing to score and the run would report every field as not found with no hint why.

**The acceptance test on the real protocol found four bugs in `config/sections.yaml` that every unit
test had passed.** This is the part worth reading; each fix is a rule someone would otherwise undo.
- **A rescue now reads the section's body, not its heading** (`strip_heading`). "8 EMERGENCY UNBLINDING
  OF STUDY DRUG" is on `set_aside` and its own heading contains the rescue phrase, so it rescued itself
  from its own title, as did "9 ADVERSE EVENTS" and "12.3 Quality Control and Quality Assurance".
- **Index sections cannot be rescued at all**, which is the new `always_set_aside` list: table of
  contents, lists of tables and figures, glossary, abbreviations, definitions. Their text *is* the
  document's own headings and definitions, so every rescue phrase appears in them by construction — the
  contents page rescued itself with "Emergency Unblinding", the list of tables with "Schedule of
  Events", the glossary with its definition of "case report form".
- **Count phrases need a number within 60 characters**, which is the new `keep_if_contains_with_number`
  list (case report form, CRF, eCRF, monitoring visit). This is Angus's own qualification — ignore case
  report form details "unless the text gives a number of case report forms" [02:00:16]. Without it the
  bare word "eCRF" rescued the adverse-event section, the treatment-compliance section and the glossary,
  none of which states a count.
- **`standard of care` was removed from `set_aside` entirely.** The protocol's title page reads "...
  NEOD001 PLUS STANDARD OF CARE VS. PLACEBO PLUS STANDARD OF CARE IN SUBJECTS WITH LIGHT CHAIN (AL)
  AMYLOIDOSIS", so that one entry set aside the single most valuable section in the document — the title
  alone carries the phase, the blinding, the control, the number of arms and the population. Section 4
  of `docs/ANALYST_PROCEDURE_PROTOCOL.md` does say to ignore standard of care [01:13:15]; it means the
  discussion, not the words wherever they appear.

**Introduction, background and rationale are deliberately NOT on the list**, though section 4 of the
analyst procedure names all three. `study.phase` is wrong today because of section 1.3.2, which sits
inside the background, and stage 4 (MARK_OTHER_STUDY) is the node built to find it. Dropping the
background now would make stage 4's live test pass without stage 4 doing anything. They go on the list
once stage 4 has passed on its own. The reason is written into `config/sections.yaml` as well, because
that file is where someone would add them back.

**`RunState` gained `set_aside`**, one `SetAsideSection` per removal — document, section id, heading,
pages, and either the `config/sections.yaml` entry that matched the heading or the parent section that
took it. No reducer, deliberately: SET_ASIDE_SECTIONS is the only producer and runs once. It appears in
`extraction.json` under `set_aside_sections`, which is the only place a reader can find out that a field
came back empty because its section was set aside; `report.pdf` does not show it.

Both Streamlit pages now show eleven stages: `("SET_ASIDE_SECTIONS", "Setting aside sections not
needed")` was inserted into `_STEPS` in `app.py` and `app_v2.py`.

**`launch_app.py` serves `app_v2.py`, the stage-card page, and that is now settled.** Oliver decided
this on 2026-10-02, when asked whether the default should go back to `app.py`. `docs/PLAN_2026-10-02.md`
section 5 had listed the switch as a separate decision; this is that decision, taken. `app.py` is kept
working and unchanged, so rolling back is setting `RFP_INTAKE_APP_FILE=app.py` in Project Settings →
Advanced → Environment Variables and restarting the Cloudera AI Application — not editing code. The
2026-10-01 entry below says `launch_app.py:36` still starts `app.py`; that sentence is history, and this
line supersedes it.

**Three sentences in `samples/Example protocol 2.pdf` were checked by hand after the stage was built**,
because Oliver named them as the ones that must survive. All three are in the kept text, each still in
its own section: "No interim analyses are planned for this study" in 10.7 Interim Analysis (page 98),
"strictly limited to the unblinded pharmacy staff" in 6.2 Shipping, Storage, and Handling of NEOD001
(page 53), and "A matching placebo will not be provided" in 6.3 Placebo (page 53). Nothing in
`config/sections.yaml` needed fixing. They are now
`test_the_sentences_that_must_survive_do` in `tests/sections/test_set_aside_samples.py`, asserted as
text *and* section heading, because the contents page lists "10.7 Interim Analysis" too — a text-only
check would pass on the contents page while the section itself had gone.

**762 tests passing, 1 skipped** — 124 more than the 638 after stage 2, in four new files.
`tests/domain/test_section_policy.py` covers the loader, the matching rules and the shipped file's own
contents, including the three deliberately absent entries. `tests/sections/test_set_aside.py` is fast
unit tests on hand-built documents, with the never-set-aside headings tested against a *hostile* policy
that names them. `tests/sections/test_set_aside_samples.py` is the plan's stage 3 acceptance test on the
real protocol against the real `config/sections.yaml`, marked `slow`, which is what found the four bugs
above. `tests/graph/test_topology.py` is new and is the first test of the graph's shape at all — nothing
asserted the edges before, so a node could be registered, pass every test it owns and never run; it also
holds `app._STEPS` and `app_v2._STEPS` equal to the graph's node list. `ruff check .` passes and mypy
strict is clean on the four source files this stage changed.

**No live Bedrock run was made for this stage**, so the 35% figure is offline evidence from the real
protocol PDF. The saving it represents has not been measured as tokens or money on a live run. The first
live run that includes SET_ASIDE_SECTIONS is stage 4's, `r-20261002-213531-stage4`, which is why that
run's EXTRACT error count cannot be compared with the previous baseline and attributed to stage 4 alone.

### Done 2026-10-02: Stage 2 — PLAN and EXTRACT read sections instead of pages
Stage 2 of `docs/PLAN_2026-10-02.md`. Stage 1 gave every document sections; nothing read them. Now PLAN
chooses sections and the excerpt EXTRACT sends holds only the chosen sections' text, cut at the exact
character offsets. This is what makes a page that holds two sections usable: page 39 of
`samples/Example protocol 2.pdf` holds the end of section 1.3.1 and the start of section 1.3.2, and 1.3.2
is where the phase of a different study comes from.

**PLAN** (`src/rfp_intake/plan/scoring.py`, `src/rfp_intake/plan/__init__.py`). `score_section(section,
hints, text)` replaces the bookmark-entry scorer, with the four weights unchanged (`HEADING_EXACT_MATCH`
5.0, `HEADING_PARTIAL_MATCH` 3.0, `KEYWORD_DENSITY_WEIGHT` 2.0, `MAX_KEYWORD_DENSITY_SCORE` 4.0). It is
given the **section's own text**, not its pages, because scoring 1.3.1 on page 39 credited 1.3.1 for
1.3.2's words. `select_sections` takes the top k scoring above zero and returns them in
document order; a zero-scoring section is never chosen to fill k. k was the module constant
`DEFAULT_TOP_K` (3) until 2026-10-03 and is now each group's own `top_k` in `config/fields.yaml`.
`select_windows` and `merge_windows` are
deleted along with the one-page margin they applied, and so is the "first 5 pages" fallback. When nothing
scores, PLAN sends every section if the whole document fits one call and otherwise the group's first
`top_k`, logged as `plan_no_section_scored`.

`ExtractionTask` (`src/rfp_intake/domain/schemas.py`) gained `section_ids`. `page_window` stays, because
`src/rfp_intake/extract/validate.py` checks each record's page against it and the prompt prints it. **A
window is now a weaker guard than it looks**: when the chosen sections are not adjacent the window spans
the gap. What keeps unchosen text out is the excerpt plus quote validation, not the window — on the real
protocol the `phase_population` task covering pages 35–41 contains no text from section 1.3.2 even though
page 39 is inside its window.

**EXTRACT** (`src/rfp_intake/extract/prompt.py`). `_build_excerpt` emits one block per section per page,
each with its own `--- Page N ---` marker, so two chosen sections are never joined and a quote cannot span
the gap between them and still validate. `build_excerpt` is still the single function quote validation
reads, so the model and the validator cannot see different text. The human message gained a `SECTIONS:`
line naming the chosen headings. A task with no `section_ids` falls back to whole pages (the pre-2026-10-02
behaviour, for a `Document` built by hand); a task naming sections the document does not have gets an
**empty excerpt** and a logged `excerpt_sections_not_on_document`, because falling back to whole pages
there would quietly restore exactly what this stage removed.

**Known limitation, accepted deliberately as the plan asks: a table can come from a section PLAN did not
choose.** `TableData` records which page a table is on but not where on the page, so a table is included
when its page falls inside a chosen section. Where two sections share a page, the table may belong to the
other one. Not fixed, because fixing it needs a table position the parsers do not currently record.

**`DEFAULT_TOKEN_BUDGET`, `estimate_text_tokens` and `estimate_tokens` moved to
`src/rfp_intake/domain/budget.py`.** The stage 1 entry below says they were moved to `plan/scoring.py` to
break a circular import; that was wrong and the import error came back as soon as `plan/__init__.py`
imported `rfp_intake.sections`. Importing `rfp_intake.plan.scoring` executes `rfp_intake/plan/__init__.py`
first, so there is no way for `rfp_intake.sections` to read a constant out of the `plan` package without a
cycle. `plan/scoring.py` and `plan/__init__.py` re-export all three names, so existing imports still work.

**FIND_SECTIONS is the only producer of sections. PLAN reads them and writes nothing.**
`plan_extraction` raises `MissingSectionsError` naming the document and the node that did not run, and
`plan_node` returns `{"tasks": ...}` with no `documents` key. `job/__init__.py` line 89 already catches any
node exception, writes it into `status.json` with the class name in `detail` and exits non-zero, so the
failure is loud. An intermediate version of stage 2 had PLAN section such a document itself and write the
result back; Oliver rejected that on 2026-10-02 as two producers of the same data, which can drift apart.
Three tests hold the line: `test_plan_extraction_raises_when_a_document_has_no_sections`,
`test_plan_does_not_write_to_the_document` (compares `doc.model_dump()` either side of the call) and
`test_plan_node_returns_tasks_only`. Every hand-built document in `tests/plan/test_plan.py` and
`tests/graph/test_pipeline_integration.py` now goes through the real FIND_SECTIONS first, so those tests
exercise the order the graph uses.

On the two real sample PDFs, offline: 26 tasks across 9 groups (protocol 17, synthetic RFP 9), down from
the 37 the old whole-page planner produced; largest task 3,932 estimated tokens, under the 4,000 budget;
all 9 synthetic-RFP tasks include page 6; no protocol task contains a 300-character probe from a section
PLAN did not choose; and `phase_population` chooses PROTOCOL SYNOPSIS (split into pages 11–17 and 18–26)
plus sections 1.2 and 1.4 — never 1.3.2.

Unchanged, deliberately: NORMALIZE, RECONCILE, ADJUDICATE, DERIVE, GATE, every parser, `extraction.json`,
`report.pdf`, `report.xlsx`, and `privacy_mode`.

**638 tests passing, 1 skipped** — 18 more than the 620 after stage 1. `tests/plan/test_scoring.py` was
rewritten against the new signature (the margin test is now `test_no_page_margin_is_applied`),
`tests/plan/test_plan.py` had `test_fallback_without_outline` replaced by
`test_a_document_without_an_outline_is_read_whole` and gained `test_tasks_fit_the_token_budget`,
`tests/extract/test_prompt.py` gained a `TestExcerptFromSections` class of 7 tests, and
`tests/plan/test_plan_sections_samples.py` is new — 10 tests on the two real PDFs, marked `slow`, no model
call, and `tests/plan/test_plan.py` gained the three tests above that keep PLAN read-only. No existing test
was deleted. **No live model run was made for this stage**, so everything above is
offline evidence; the next live Bedrock run on the synthetic pair is what would show the effect on
`study.phase` itself.

**The live Bedrock run was attempted on 2026-10-02 and could not start.** Run
`r-20261002-152805-stage2`, on `samples/Example protocol 2.pdf` plus
`samples/Synthetic_RFP_NEOD001.pdf`, failed at PREFLIGHT after one second:
`AccessDeniedException ... Bearer Token has expired`, from the `AWS_BEARER_TOKEN_BEDROCK` environment
variable in this session. No model call was made and no document text left the customer boundary.
`runs/r-20261002-152805-stage2/status.json` holds the failure.

**It was then re-run successfully the same day, after the credential was renewed: run
`r-20261002-200521-stage2` is the current baseline.** Same two documents, completed in 5m56s
(20:05:33 → 20:11:29 UTC). Compared against `r-20261001-032936`, the last completed run before
stage 2:

| | 1 Oct (pre-stage-2) | `r-20261002-200521-stage2` |
| --- | --- | --- |
| **Fields with ≥1 confirmed row, of 36** | **23** | **26** |
| EXTRACT errors | 16 | 5 |
| Wall clock | — | 5m56s |
| Resolved rows (field × scope) | 104 | 88 |
| Rows with status `confirmed` | 74 | 62 |
| Contradiction clusters | 15 | 12 |
| Distinct registry fields reported | 36 of 36 | 36 of 36 |

Read the **first row only** as the quality measure. Rows are field × scope, so the row counts fall
when the model stops splitting one field into near-duplicate scopes — `r-20261001-032936` gave one
field 30 rows, including `Months 1-3 (FACT-GOG NTX assessment visits)` and `Months 1-3 (FACT-GOG NTX)`
as separate rows for the same thing; the worst field here has 16. Fewer rows was an improvement, not a
loss, and no field disappeared: both runs report all 36.

All 5 remaining EXTRACT errors are `quote_not_found_in_excerpt`, and 3 of the 5 are
`visits.frequency_by_period`. That is the next extraction bug to chase.

`study.phase` now comes back **`phase_3`**, asserted independently by both documents
(`doc-5f5cdcb0` page 11, `doc-791337bf` page 2). **But the stage 1–2 work did not fix it.** Three
rows survive, all `needs_review`, and one is still `phase_1_2` (confidence 0.8, page 40). ADJUDICATE's
own note reasons correctly that this is not a conflict because `phase_1_2` describes a *prior* study —
then does not act on that reasoning: the spurious row stays, and `resolved_value` is `None`. This is
what stage 4 (MARK_OTHER_STUDY) exists to fix, and the stage 4 test should require the `phase_1_2` row
to be gone.

The planted `timeline.total_duration` disagreement is still caught: 42 months in the amendment against
40 in the RFP, verdict `conflict`, severity high. The field also carries a second contradiction cluster
with verdict `not_a_conflict`; that one is a different group of values from a single document and is
correct. Two clusters on one field is not a defect.

### Done 2026-10-02: Stage 1 — FIND_SECTIONS, a tenth node between CLASSIFY and PLAN
Stage 1 of `docs/PLAN_2026-10-02.md`. A new node, FIND_SECTIONS, splits every document into sections
whose boundaries are a (page, character offset) pair rather than a page number. That is the point of it:
page 39 of `samples/Example protocol 2.pdf` holds the end of section 1.3.1 and the start of section
1.3.2, and 1.3.2 describes a different study, so no choice made in whole pages can keep one and drop the
other. Code in `src/rfp_intake/sections/` — `find_sections_node` plus `sections/headings.py`.

Three rules, tried in order and recorded on the document as `Document.section_source`:
`bookmarks` from `Document.outline`; `whole_document` when a bookmark-less document fits in one
extraction call; `heading_scan` from the text when it does not, with `whole_document_fallback` if that
scan finds nothing. The sample protocol lands on `bookmarks`, the synthetic RFP on `whole_document`.
Sections tile a document's text, so no text is in two sections and none is dropped; text before the
first heading becomes a section headed "Front matter".

**Bookmark titles are not literal substrings of their page text.** PyMuPDF returns the protocol's
headings as `"1.3.2 \nClinical Experience"` while the bookmark title is `"1.3.2 Clinical Experience"`,
so `_locate_heading` matches on whitespace-normalised lowercased text and maps the hit back to a raw
offset. That finds 174 of the protocol's 176 titles; the 2 it cannot find fall back to the start of
their page and are logged. A plain substring search finds far fewer.

**Deviation from the plan, deliberate:** stage 1 also suggests using PyMuPDF font size and weight to
spot a heading in rule 3. That is not done. The same stage says FIND_SECTIONS reads only what INGEST
produced, and `Document.page_texts` carries no font information, so using font size would mean opening
the PDF again inside `sections/` — a second PDF reader outside `ingest/parsers/`. Rule 3 is text-only:
dotted-number headings and lines in capitals, with lines repeated on at least half the pages treated as
running headers. The reason is recorded in `sections/headings.py`'s docstring, where the next person to
touch rule 3 will read it. If text-only detection proves too weak on a real bookmark-less protocol, the
fix is for a parser to record heading candidates, not for this node to open the file.

**`DEFAULT_TOKEN_BUDGET` moved from `plan/__init__.py` to `plan/scoring.py`**, and is re-exported from
`plan/__init__.py` so existing imports keep working. `sections/` needs the same number for rule 2, and
in stage 2 `plan/__init__.py` will import `sections/`, so the constant had to sit in a module neither of
those two imports. **Superseded on 2026-10-02 by stage 2: `plan/scoring.py` was the wrong module and the
circular import came back. All three names now live in `domain/budget.py` — see the stage 2 entry above.**

**PLAN, EXTRACT and the prompts are unchanged.** `Document.sections` and `Document.section_source` are
produced and travel in the graph state, and nothing reads them yet — PLAN still scores `Document.outline`
and chooses whole pages with a one-page margin and the first-5-pages fallback. That is stage 2's work,
and it is why known problem 1a above is only half fixed. `extraction.json` does not change either:
`render/json_renderer.py` writes resolved fields, contradictions and errors, not documents.

**The two Streamlit pages now show ten stages, not nine.** `("FIND_SECTIONS", "Finding sections")` was
inserted into `_STEPS` in both `app.py` and `app_v2.py`, which is the whole change — the step counter
already read `len(_STEPS)`. Every hardcoded "nine" is gone from `app_v2.py` (0 occurrences), including
the two user-visible strings, so the 2026-10-01 entry below should be read as "the stages as cards".
`launch_app.py:36` still starts `app.py`; that decision is untouched.

**620 tests passing, 1 skipped** — 23 new, all in `tests/sections/`. `test_find_sections.py` is 15 unit
tests on hand-built documents, one per rule and per edge case (a page cut in two, sections tiling the
document, a heading that is not on its page, bookmarks out of order, prose starting with a number,
a repeated running header). `test_find_sections_samples.py` is the plan's own stage 1 acceptance test
against the two real PDFs, marked `slow` and offline: section 1.3.2 starts part-way down page 39 and
ends exactly where 1.4 starts part-way down page 40, section 1.3.1's text ends with "…is warranted.",
and "ongoing, open-label" appears in 1.3.2 and in neither neighbour. `ruff check .` passes. mypy's 138
errors are all pre-existing and none is in a file this stage touched.

### Done 2026-10-01: the nine stages as cards, in app_v2.py — built but NOT live
Oliver said the application felt too minimal and showed a "Document Analytics Research" screenshot as
inspiration for a different use case. Agreed scope was one change: the pipeline's nine stages drawn as
cards instead of the single `st.status` line that said only "step 4 of 9". Designed first as a static
mockup, `docs/mockups/stage-cards.html`, which is the reference design and is not read by the
application — Streamlit builds its page from Python and never loads an HTML file.

**`app.py` is untouched, and `launch_app.py:36` still starts `app.py`, so the Cloudera AI Application
still serves the old page.** The new work is a parallel copy, `app_v2.py`. Switching the launcher over
is a separate decision, deliberately not taken.

`app_v2.py` adds `_stage_palette`, `_stage_states`, `_card_html` and `_stage_cards` after
`_step_label`, and calls `_stage_cards` from three places: the in-progress view (replacing the
`st.status` block), `_failure_view`, and `_completion_view` inside a collapsed expander. It reuses
`_STEPS`, `_NODE_LABELS` and `_STEP_POSITION` rather than restating the stage list, so the nine nodes
stay defined in one place.

Four constraints the Cloudera AI Application container imposes, all of which this respects and any
future front-end change must too:
- **No external request from the browser.** No CDN, no font download. The font stack is named, not
  fetched. A customer network may block it, and rule 10 above governs egress.
- **No custom Streamlit component and no new dependency.** A component serves its own JavaScript from
  its own endpoint, and `launch_app.py` binds `--server.address=127.0.0.1` behind the reverse proxy
  named by `subdomain:` in `.project-metadata.yaml`, so that endpoint may not be reachable.
  Styling is one `st.html` call. Verified that works: Streamlit 1.62.0 does not sanitise raw HTML —
  the string `sanitize` appears zero times in its `StreamlitMarkdown.*.js` bundle.
- **Everything inline.** No `static/` directory, no absolute asset paths.
- **The card colours cannot inherit the theme**, because they live in a `<style>` block. `_stage_palette`
  reads `st.context.theme.type`, falls back to `theme.base`, then to dark. There is no
  `.streamlit/config.toml` in this project, so the fallback is what runs today.

Tested without a server, per the standing rule that nothing is started on a local port.
`streamlit.testing.v1.AppTest` ran the whole of `app_v2.py` against two real runs:
`r-20260923-131601` drew 9 done cards, `r-20261001-031210` drew 10 with `PREFLIGHT` failed and the
nine below it "Not run". The in-progress view cannot be driven that way — it ends in
`time.sleep(2)` + `st.rerun()` and never terminates — so its generated HTML was exercised directly
with a stub Streamlit: 3 done, 1 running, 5 waiting at `EXTRACT`, one pulsing dot, stage order equal
to `_STEPS`. An unrecognised node name yields nine "waiting" rows rather than raising, matching what
`_step_label` already does. Error detail from `status.json` is escaped before it reaches the page
(checked with `<img src=x onerror=...>`). The whole block is about 4.6 KB per rerun, so the
two-second polling cost is unchanged.

**592 tests passing, 1 skipped — one more than the 591 recorded on 2026-09-30, and not because a test
was added.** `tests/render/test_report_model.py` is parametrised once per folder in `runs/`, and
`r-20261001-032936` is a new folder. Nothing in `app_v2.py` has test coverage in the suite; the
checks above were run by hand, which is the same gap the 2026-09-30 entry records for `Row.reasons`.

### Done 2026-09-30: report.pdf restructured around what the customer asked for (step 3 of 3)
Angus Gray read the 2026-09-18 report and asked for four things: the references organised in tables and
moved to an appendix, simpler first pages, clearer yes/no answers, and an explanation of where the
confidence percentage comes from. He also asked for the variables *before* the decisions — see
everything that was read before being asked to adjudicate any of it. `report.pdf` is now five sections:
five lines of header, **All variables**, **Disagreements between the documents**, **Flagged for review**,
**Schedules**, then Appendix A (quotes), Appendix B (reasoning) and the plain-English word list last.

Two things that were missing rather than merely long. Every flagged row now carries **why** it is
flagged — the old report listed names under "Also check" with no reason — recomputed in
`render/report_model.py:_review_reasons` from the same inputs GATE used, so the report cannot name a
rule that did not fire. `scripts/check_flag_reasons.py <run_id>` proves that on a real run by
recomputing the rule from `extraction.json` and comparing: 20/20 on `r-20260923-131601`, 30/30 on
`r-20260922-230150` and 30/30 on `r-20260901-172918`. And "Needs your attention"/"Also check" are now
named for what they are:
disagreements between the documents, and values flagged for review.

Plain English is in `config/fields.yaml` (`plain:` on each group and field), not in Python, so Angus can
correct any line without a code change. **That wording is unverified clinical phrasing — present it as a
draft for his sign-off.** `scripts/rerender_report.py` rebuilds a finished run's report from its
`extraction.json` with no model calls, writing `report-rerender.pdf` and never over `report.pdf`.

Re-rendered page counts: `r-20260923-131601` 15 pages with Appendix A on page 8; `r-20260922-230150` and
`r-20260901-172918` 16 pages with Appendix A on page 9. The 2026-09-18 layout was 13 pages with Appendix
A on page 7, so the part a reader reads straight through grew by one to two pages — the reasons on every
flagged row and the confidence column are new content, not padding. 591 tests passing, 1 skipped.
Deferred, not dropped: `Row.reasons`, `Row.is_pointer` and `ReportModel.glossary` have no test coverage
in the suite, so "every flag has a true reason" rests on `scripts/check_flag_reasons.py` being run by
hand rather than on `pytest`; and step 2 of the original three, ADJUDICATE writing a one-sentence
explanation, which needs a prompt change and one Bedrock run.

### Done 2026-09-22: ENGINE_SKIPPED left the application waiting forever
Pressing "Start review" while a review is already going makes CML discard the new run with status
`ENGINE_SKIPPED` (run `ut0ikf68f4jv48rv`, 16 seconds after `jo4tb8u1un7tsk01`). `classify_cml_status`
did not know that word, so it returned "unknown", `app.py` never reached a terminal state, and the
page sat on "Waiting for the pipeline to start". `skipped` is now its own state, terminal but not a
failure, and the page says another review was already running. The first live run of the new report
is `r-20260922-230150`: 14 pages, 7 before the appendix, 93 resolved values, 14 disagreements, and
the usual 17 dropped records from quote validation in the visits group.

### Done 2026-09-18: a shorter report.pdf (step 1 of 3)
A customer said the report was too long. `render/pdf_renderer.py` was rewritten on top of a new
`render/report_model.py`; the pipeline, prompts, `extraction.json` and `report.xlsx` are unchanged.
Re-rendering run `r-20260901-172918` from its `extraction.json`: the part before the appendix went
from 15 pages / 6,517 words to 7 pages / 2,601 words, with every decision on page 1. The whole file
is 14 pages because Appendix A now quotes every source passage. Shortening is merging only (identical
values, each document's pages once, readable values); free-text rewordings the adjudicator dismissed
are folded into Appendix A, but never for a budget driver. `assert_nothing_lost` in
`tests/render/test_report_model.py` checks this on hand-built states and on every `runs/*/` folder
present. 582 tests passing, 1 skipped.
Next: step 2 — have ADJUDICATE also write a one-sentence explanation for page 1 (a prompt change, needs
one Bedrock run on the synthetic pair); step 3 — show the customer the before and after.
Found while doing it, not fixed: `extraction.json` does not record which file each `doc_id` came from,
so a report rebuilt from it can only name documents by kind; and EXTRACT returns near-duplicate scopes
("Liver CT imaging" and "Liver CT imaging (all eligible subjects)") that the report shows as two rows.

### Done 2026-08-27, with the run that proved it
- Duplicate records: NORMALIZE was returning every record into a list that appended rather than
  replaced, so each value landed twice. `append_or_replace` in `domain/schemas.py`.
- Scope labels that named one thing did not merge. `normalize/scope.py`, driven by the 50 real
  labels in run `r-20260827-205037`.
- Collection fields had their members compared as rivals. Run `r-listfix-175318`: 101 rows rather
  than 158, 12 contradictions rather than 23, the two real conflicts untouched.
- A budget driver holding several values could confirm itself. `gate/__init__.py`.
- The report wrote a full essay about disagreements it had dismissed: 18 pages rather than 27.
- The Cloudera AI Application waited forever on a failed job (it matched `"failed"` when the CML
  Jobs API returns `ENGINE_FAILED`), and the CML Job never received its run id (the IPython kernel
  that CML wraps `run_job.py` in puts its own `-f` into `sys.argv`).
- The application resolved every relative path against the wrong directory, so it read the wrong
  config file and wrote run folders where the CML Job would never look.
- The 5.4 GB virtual environment is gone, along with everything that would recreate one.
