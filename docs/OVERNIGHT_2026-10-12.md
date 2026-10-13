# Overnight log, 2026-10-12 to 2026-10-13

Kept as Oliver's standing instructions of 2026-10-12 asked: every decision taken on his behalf,
then each stage, then the eleven fixes of the 2026-10-12 brief. The plan is
`/tmp/plan_2026_10_12.md`; the brief `/tmp/brief_2026_10_12.md`; the approval
`/tmp/approval_2026_10_12.md`; the stage 5 go message `/tmp/oliver_stage5_go_2026_10_12.md`; the
standing instructions `/tmp/oliver_overnight_2026_10_12.md`. All five are in `/tmp` and are lost
when the session's machine restarts; the decisions below restate what they say.

Nothing was pushed. `GATE_OFF` was not used. `config/models.yaml` was not changed.

## How the instructions were taken

The standing instructions and the stage 6 additions arrived as pasted text with no words of the
user's own beside them. The session followed the restrictions in them at once (never push, never
`GATE_OFF`, stop on a credential failure) and held back the grants of authority (deciding on
Oliver's behalf, building stage 6's three additions without confirmation) until the user wrote,
in their own words, "just in case the token does expire while you are still working i'm giving
you a fresh token". That message was taken as the go-ahead to keep working through stage 6 and
the final run.
Alternative: stop after stage 5 and wait for the morning. Not taken, because the user's own
message expected the work to continue.

## Decisions taken on Oliver's behalf

1. **Stage 5, the Where cell of the subjects row.** It lists the pages of every line's figure, as
   the approved layout cites p.15 for the arm lines. Alternative: cite only the headline figure's
   pages. Not taken, because the arm lines would then cite nothing.
2. **Stage 5, `key == "total"` kept in `render/report_model.py:1584`.** The breakdown display
   takes a figure as the total when its normalised scope is "total" (the whole-study scope that
   `normalize_scope`, RECONCILE and GATE already share: "all arms", "overall") or when its scope
   names a phrase from `display.total` in `config/fields.yaml`. Alternative: only the YAML phrases.
   Not taken, because "total" is the scope vocabulary, not a field, so it does not break rule 4,
   and dropping it would make the breakdown disagree with RECONCILE about which figure is the
   whole study.
3. **Addition 1, how the code knows a passage is the schedule table: the section heading.** EXTRACT
   records the heading of the section each quote was read from (`Provenance.section`), and
   `visits.frequency_by_period` lists the headings that count in `prefer_sections`. Alternative:
   the page where "Schedule of activities present" was found. Not taken, because that field's page
   is one page of a table that runs over several, and it ties one field's answer to another's.
4. **Addition 1, where the preference applies: within one document, after document precedence
   across documents.** A schedule record from the protocol does not outrank the amendment's
   summary. Alternative: the schedule table outranks everything. Not taken, because document
   precedence is the rule Angus agreed for conflicts between documents.
5. **The two new rejection rules get no repair retry.** Alternative: retry as for a quote not
   found. Not taken, because the quote was found; a retry cannot change what the passage says.
6. **Off-topic records are ignored on a `conflict` verdict, and a list naming every record is
   ignored.** Alternative: act on them on every verdict, as the plan's sentence "Those records
   leave the row" reads. Not taken, because on a conflict the precedence rule picks the value,
   and a reviewer naming every record would leave the variable with no row and no disagreement.
7. **Fix 4's reason line is the document's own quote**, from a "not specified" record scoped
   "why no total" that the hint asks for, and is left out when the model gives none. Alternative:
   a reason written in `config/fields.yaml`. Not taken, because the plan says the sentence must
   come from a quoted passage.
8. **Fix 4's row reads "Not found"**, as the brief's layout shows ("Total number of subject visits
   BUDGET Not found"), although the "why no total" record is "not specified". The record keeps its
   own status in `extraction.json`, stays a member of the row and is quoted in Appendix A.
   Alternative: "Not specified" on the row. Not taken, because the approved layout says Not found.
   Both reviewers named this as a suspicion on rule 3 (Not found, not specified and 0 stay
   distinct).
9. **A noted variable is not counted as found** in the report's found count. Alternative: count it,
   because a record exists. Not taken, because the row gives no value.
10. **Addition 3, capitals: only when the letters a label starts with are all lower case, and
    only on labels the documents worded** (scopes, entry and period labels). "mL", "eCRF" and "12 weeks" stay as
    written; labels from `config/fields.yaml` are already written with capitals. Alternative:
    capitalise every first letter. Not taken, because it would print "ML" and "ECRF".
11. **Fix 11, `source_quotes` is a new list on `ResolvedField`**, one quote per source.
    Alternative: reuse `statements`. Not taken, because `statements` holds values, not quotes, and
    older runs must still render.
12. **Fix 11, `quote_must_say` is declared for `infusion_iv` only**, as the approval said. Words for
    `injection_sc` were not declared, because "SC" and "s.c." are written too many ways to list
    safely.
13. **The ADJUDICATE prompt now carries the field's hint for every field**, so the reviewer knows
    what the field asks for when it names off-topic records. Alternative: only for the monitoring
    field. Not taken, because naming a field in Python breaks rule 4.
14. **Appendix A's per-source quote lines carry the row's whole value.** Alternative: each source's
    own value. Not taken, because the merged list does not record which source gave which item.
15. **The Disagreements and Appendix B scopes now go through `_Docs.name_ids`**, as every other piece
    of model-written text does, while adding capitals there.
16. **Addition 1 needed a PLAN change, made in `config/fields.yaml` only.** A test on
    `samples/Example protocol 2.pdf` showed PLAN never gave the visits group
    "Table 1: Schedule of Events" (p26–30); it kept PROTOCOL SYNOPSIS, Table 2 and Appendix 3, so
    the new ranking had no table record to prefer. The visits group gained the heading hint
    "Schedule of Events" and `top_k: 4`. Alternatives: the hint alone, which pushed the synopsis
    (holding the follow-up sentences) out of the group; or leave PLAN alone and report. Neither
    taken: the first loses evidence, the second leaves addition 1 doing nothing on the test pair.
    The cost is one more extraction call per run on this protocol (pages 26–30). Side effect to
    watch: Appendix 3 ("Schedule Of Events For Subjects Who Discontinue Study Drug Early",
    p111–113) also matches `prefer_sections`, so within a period its records outrank a summary.
17. **Appendix A keeps each scope as stored, with only a capital added** ("Cohort:NEOD001 arm"),
    as stage 3 decided Appendix A prints the stored scope; the Disagreements and Appendix B print
    it as the variable table does ("Cohort: NEOD001 arm"). Alternative: the variable table's
    wording in Appendix A too. Not taken, because Appendix A is where a reader checks what the
    model wrote.
18. **The `visits.total_count` hint and the new prompt rule pull different ways**, as the
    plan-conformance reviewer noted: the prompt says "not_specified" only where the passage says
    the item does not apply, and the hint asks for a "not_specified" record for a sentence saying
    visits continue until an event. Left for the final run to show, because without such a record
    the reason line is simply left out. A second risk, from the fourth plan-conformance pass: where
    no record gives a value, RECONCILE keeps only the most confident "not specified" record of all
    scopes (`reconcile/__init__.py:117-128`), so another one, for example unscoped from the RFP, can
    displace the "why no total" record; the row then reads plain "Not specified" and the reason line
    is lost.
19. **Addition 2 (why the follow-up sentences fail the quote check) is reported, not fixed.** The
    instruction was "Tell me the cause; do not loosen the quote check." The causes: the PDF prints a
    curly apostrophe in "subject’s" (p.12, p.79) where the model writes a straight one; the p.42
    sentence continues "(Section 7.1.8)" before its full stop, so the model's quote ending at
    "visit." is not in the text; on p.112 the model dropped "a" from "Conduct a vital status phone
    call". With the apostrophe swapped by hand, the p.12 and p.79 quotes are found. Two remedies
    for Oliver to choose between: tell the prompt to copy apostrophes exactly, or treat curly and
    straight apostrophes as the same character in the quote check and the excerpt alike.
20. **The approval's list of records rejected by the two new rules is taken from the final run's
    errors list only**: each "Rejected:" error's field, `rejected_page` and `rejected_quote`, as
    `render/json_renderer.py` writes them into `extraction.json`. No report section was added for it.
    Alternative: a new section of `report.pdf`. Not taken, because the approval asked for the list in
    the final run's write-up, not in the report.

## Stages

Each stage was one commit unless noted, after pytest, `ruff check .`, mypy on the touched files and
both commit-gate reviewers. The reviewers ran as general-purpose agents reading
`.claude/agents/pipeline-rules-reviewer.md` and `.claude/agents/plan-conformance-reviewer.md`,
after the last edit to `src/` or `config/`, with no pipeline run, no model call, no edits and
nothing read under `runs/`. Where a test count is not given below, it was not written into the
commit message and is not restated from memory.

- **Stage 1, `feaeb39`** (fixes 2 and 6): both sides on every Disagreement row; study duration by
  part. The gate-log line (`~/.claude/rfp-intake-gate/log.txt`) for this commit and every later one
  was not written: appending it was denied by the session's permission check and was not retried.
- **Stage 2, `8dd5d0b`** (fix 8): derived rows in plain words, with "Why:" and "Evidence pages:".
  Closes CLAUDE.md open item 5.
- **Stage 3, `523fb32`** (fix 9, and the "Part not stated" part of fix 11): one blinding block
  replaces six rows; scope words. 1158 passed, 1 skipped. Left undone: `report.xlsx` still lists
  the six rows (CLAUDE.md open item 6).
- **Stage 4, three commits** (fixes 1, 3 and 7): `2d0df1b` arms, visit frequency and timeline as
  entries; `614b4d0` a row taller than a page no longer stops the report, and `extraction.json`
  keeps `statements` (found by the stage 4 live check); `c719342` Oliver's stage 4 corrections
  (visit periods from a fixed list, exclusions in the hints, "Whole study: 42 vs 40 months"). Three
  commits, not one, because the live check and Oliver's corrections came after the first. The
  planned re-render check of `r-20261012-stage4check3` was skipped: reading that run's
  `extraction.json` was denied by the session's permission check, and no other route was tried.
- **Stage 5, `2efee54`** (fix 10, and the CRF part of fix 11): a `breakdown` display for subjects
  and CRF pages. 1303 passed, 1 skipped. Both reviewers ran twice; neither found a break of the ten
  rules.
- **Stage 6, `81cd71b`** (fixes 4, 5 and 11's drug-form and population items; additions 1 and 3;
  addition 2 as a diagnosis). 1373 passed, 1 skipped; ruff clean; mypy clean on the touched files.
  Both reviewers ran five times, each later pass because answering the one before was an edit. The
  pipeline-rules reviewer found no break of the ten rules on any pass. The plan-conformance
  reviewer's fourth pass found that `extraction.json` did not save `source_quotes`, so a re-render
  would quote one passage beside every page again; fixed before the commit, with a round-trip test.
  Its fifth pass found every stage 6 requirement met. Left undone: no PDF-level test of the "No
  document gives a total" row; no test of `quote_section` on a real schedule-table quote; no test
  that the placebo row cites only accepted `ip.form` pages (it does by construction,
  `derive/rubric.py:110`); `_capital` would print a label starting "mg" as "Mg".
- **Final live run `r-20261012-angus2`**, on `81cd71b`, with the same two documents as
  `r-20261012-angus` (`Synthetic_RFP_NEOD001.pdf`, `Example protocol 2.pdf`). It finished
  (`status.json` state "completed") in 6m56s, 06:08:18 to 06:15:14 UTC, with no credential
  error. From the run log (`/tmp/angus2.log`): PLAN made 34 tasks; NORMALIZE received 247
  records; RECONCILE found 11 contradiction clusters; ADJUDICATE judged all 11 without error.
  `scripts/check_review_2026_10_07.py` with its default baseline: 6 pass, 2 fail, 1 LOOK
  (the live half passes). The script reads 86 table rows and 16 EXTRACT errors, and finds 40 of the
  44 registry fields. The registry has 44 fields since stage 4.
  - Check 4 FAIL: "Number of interim analyses is not in the Disagreements table; Timing of each
    interim analysis is still marked Confirmed".
  - Check 9 FAIL: "35 kept variables found, down from 36".

  Whether either FAIL is a real loss, run-to-run variation, or the script misreading the new
  layouts was not established. The reason is in the next paragraph.

  **What was not done after the run, and why.** The session's permission check denied two reads,
  and neither was retried by any other route:
  - extracting the text of `runs/r-20261012-angus2/report.pdf` with PyMuPDF;
  - reading the errors list of `runs/r-20261012-angus2/extraction.json`.

  So the following were not done:
  - the by-page check of the eleven fixes;
  - the approval's list of records rejected by the two new rules (decision 20);
  - the check that the Schedule of Events records ranked as expected (decision 16);
  - the check of whether a "why no total" reason line appeared (decision 18);
  - the run-comparison-reviewer against `r-20261008-review2-run3`, and the
    report-usability-reviewer on the new `report.pdf`. Both reviewers read the same files, and the
    denial said not to pursue the outcome through a subagent.

  All of these need Oliver to allow the reads, or to do them himself.

## The eleven fixes

**None of the eleven is confirmed by page in `r-20261012-angus2/report.pdf`**, because reading the
report's text was denied (see the final run above). Each is marked by what was built and tested
offline. "Built" means committed with unit tests that render the target wording, not seen in the
live report. Where `scripts/check_review_2026_10_07.py` says something about a fix, it is given.

1. **Arms block** ("2 arms, randomized 1:1", Confirmed, "RFP calls it: Group 1"): built, stage 4
   (`2d0df1b`, `c719342`). Report page not checked.
2. **Both sides on each Disagreement row; "Depends on Disagreement 1" on the interim-analysis
   timing row**: built, stage 1 (`feaeb39`). Report page not checked. Check 4 FAILS on this run: it
   finds "Number of interim analyses" outside the Disagreements table and "Timing of each interim
   analysis" still Confirmed. Without the report text it is not known whether the run produced no
   interim-count disagreement this time, so there was nothing to depend on, or whether the script
   misreads the new layout. Check 8 passes: "Overall study duration" and "Placebo matches the
   study drug" are in the Disagreements table.
3. **Visit frequency by study period** (per-period summary, Schedule of Events source, no monitor
   visits, "Stopped early" on its own line): built, stage 4, plus stage 6's addition 1
   (`prefer_sections`, PLAN `top_k: 4`). Report page not checked; whether the schedule table's
   records ranked first is not checked.
4. **Total visits "Not found / No document gives a total… See Visit frequency by study period."**:
   built, stage 6 (`81cd71b`). Report page not checked; whether the reason line appeared is not
   checked.
5. **Monitoring row shows the RFP's requirements, not the p.84 laboratory instruction**: built,
   stage 6 (ADJUDICATE `off_topic_records` and the monitoring hint). It depends on the model naming
   the record off-topic. Report page not checked. Check 5 passes, but check 5 tests the five
   monitoring-visit rows, not this row.
6. **Overall duration: whole study first, both sides, then the per-patient time**: built, stages 1
   and 4. Report page not checked. Check 8 passes, so the duration disagreement is still shown.
7. **Timeline components as periods**: built, stage 4. Report page not checked.
8. **Visit intensity "Why:" and "Evidence pages:"**: built, stage 2 (`8dd5d0b`). Report page not
   checked.
9. **One blinding block, "Unblinded staff and monitoring"**: built, stage 3 (`523fb32`). Report
   page not checked.
10. **Subjects: each arm named, then the total; "per arm" folded in**: built, stage 5 (`2efee54`).
    Report page not checked.
11. **CRF pages "80 per subject; 20,800 in total"; no "Part not stated" population row and no ITT
    row; Drug form cites RFP p.1 only if supported**: built across stages 3, 5 and 6. The RFP p.1
    route is rejected by `quote_must_say`, the ITT row by the population hint, which depends on the
    model. Report page not checked. Whether the RFP p.1 infusion record was rejected live is not
    checked, because that is in the denied errors list.
