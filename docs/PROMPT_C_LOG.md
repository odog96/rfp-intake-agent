# Prompt C log — simplified Schedule of Events (from 2026-10-17)

Oliver Zarate's Prompt C, from Angus Gray's 13 October request: replace the text of the "Visit
frequency by study period" row with a simplified Schedule of Events — the protocol's own visit
columns, each column's marks counted as one number, a Key considerations column, and total subject
visits by stage. There are no notes of the 13 October call in `docs/`; the work follows the prompt
text.

Oliver approved the plan on 2026-10-17, including exception A and decisions B, C, D and F, with a
change to E, and left standing instructions while away: build stages 1 to 4 in order, one commit each,
after pytest, `ruff check .`, mypy on the touched files and both gate reviewers; re-render
`r-20261016-prompt-b` after stage 3 and save the visit table here; then try the live run
`r-20261017-prompt-c2`.

The target, counted by Oliver on `samples/Example protocol 2.pdf` Table 1 (PDF p.26–27):

| Setting | Counts per column |
| --- | --- |
| Assessments only (the default, `count_non_assessments: false`) | 26, 16, 13, 7, 23, 6, 28, 7, 30, 22, 31 |
| Every mark | 26, 16, 17, 8, 26, 7, 31, 8, 33, 25, 31 |

## Decisions approved by Oliver (2026-10-17)

- **A.** Stage totals are sums done by code, an exception to "do no calculation". Each is shown with
  its parts, for example "12 (1 + 3 + 1 + 3 + 1 + 3)", and marked "Worked out by the tool — confirm".
- **B.** The EOT/ETD column's timing comes from the header's own footnote, and only when the header
  gives no timing. Footnote 3 on PDF p.28: "EOT/ETD Visit to occur 28-35 days after the last study
  drug administration."
- **C.** "1 a month until the study ends" is a wording rule in `config/fields.yaml`
  (`schedule_table.stages`), not Python.
- **D.** Chemotherapy is placed where its row's marks are, which includes Month 2 Day 1, Month 3
  Day 1 and both repeating columns.
- **E, as Oliver changed it.** Study drug administration is matched on "study drug", "infusion",
  "dosing" and "injection", never on the bare word "administration" (which would match
  "Premedication Administration"). Angus will extend the list. Premedication is its own
  consideration, `assessment: false`, shown as "Premedication".
- **F.** The labels "Study drug infusion" and "Chemotherapy" are set in config.

## Decisions taken for Oliver while he was away

Each gives what was chosen, the alternative, and the reason.

1. **Where the header ends.** Chosen: the header is every table row above the first row with a cell
   that says only a mark word ("X", or "X" with a footnote number). Alternative: a fixed number of
   header rows, or the first row with a label in column 0. Reason: Table 1's header is three rows on
   p.26 and its column 0 has a header label ("Assessments"), so neither alternative finds the boundary
   on every page; the first row of marks does.
2. **Which section a table belongs to.** Chosen: the section whose heading is printed above the table,
   on the same page or earlier. Alternative: every table on any page of a schedule section. Reason: a
   page can hold the end of one section and the start of the next; the alternative would read another
   section's table as a schedule.
3. **A mark in a merged cell.** Chosen: it counts once, in the column under the centre of its words.
   Alternative: count it in every column the cell spans. Reason: "Per Section 7.4 & Table 2" spans the
   two repeating columns on Table 1, and Oliver's target counts it once, in "Months 6, 9, 12, etc."
   only. Counting it in both would give 34 and 26, not 33 and 25 (every-mark setting).
4. **Marks on a line with no row label** (a label that wraps onto the line above or below its marks).
   Chosen: they join the one labelled neighbouring row that has no marks of its own; if there is no
   such row, or two, they are listed as not placed and still counted in their column. Alternative:
   leave them out of the count. Reason: a mark printed in a column is a visit activity whatever its
   row; leaving it out would under-count silently. Table 1 needed neither case; nothing was unplaced.
5. **How far a header footnote is looked for.** Chosen: the table's last page and the 3 pages after it
   (`FOOTNOTE_PAGES_AFTER` in `src/rfp_intake/schedule/__init__.py`), never past the schedule section's
   last page, and only in its kept text. Alternative: the whole section, or the whole document. Reason: Table 1's footnotes are on the two pages after it (p.28–29); a wider
   search risks finding another table's footnote 3.
6. **"Day-28" is printed as the protocol prints it.** Alternative: insert a space ("Day -28"). Reason:
   code never rewrites the protocol's words.
7. **A footnote mark is told from the header's words by letter size**: a word in letters under 0.8 of
   the table's usual size. Measured on Table 1: 9.96 pt body, 6.48 pt footnote marks. Alternative:
   strip trailing digits. Reason: "Day 1" and "Days 8, 15, 22" end in digits that are not footnotes.
   An ordinal suffix set small ("3" + "rd") is kept when it touches a digit.

## Stages

### Stage 1 — the reader, its config and its tests

What it adds:
- `schedule_table` in `config/fields.yaml`: the heading wording, the stopped-early heading wording,
  the mark words, the timing words, the visit separators, the repeating-visit rules, the stages, the
  counting setting (default `false`, assessments only), and the six considerations.
- `ScheduleTable`, `Consideration`, `ScheduleStage` and `RepeatingVisit` in
  `src/rfp_intake/domain/registry.py`; `Registry.schedule_table` is optional.
- `Schedule`, `ScheduleColumn` and `ScheduleRow` in `src/rfp_intake/domain/schemas.py`.
- `src/rfp_intake/schedule/__init__.py`, `read_schedules(doc, config)`: reads ruled tables with
  pdfplumber in-process; counts in code; no model call.
- Tests: `tests/schedule/test_read_schedule_samples.py` (slow, offline, on the real PDF),
  `tests/schedule/test_schedule_words.py`, and two in `tests/domain/test_registry.py`.

Result on `samples/Example protocol 2.pdf`, Table 1 (PDF p.26–27), offline:

| Period | Timing | Visits | Stage | Every mark | Assessments |
| --- | --- | --- | --- | --- | --- |
| Screening | Day-28 to Day -1 | 1 | Screening | 26 | 26 |
| Screening | Day -2 or Day -1 | 1 | Screening | 16 | 16 |
| Month 1 | Day 1 | 1 | Months 1–3 | 17 | 13 |
| Month 1 | Days 8, 15, 22 (±2) | 3 | Months 1–3 | 8 | 7 |
| Month 2 | Day 1 (±2) | 1 | Months 1–3 | 26 | 23 |
| Month 2 | Days 8, 15, 22 (±2) | 3 | Months 1–3 | 7 | 6 |
| Month 3 | Day 1 (±2) | 1 | Months 1–3 | 31 | 28 |
| Month 3 | Days 8, 15, 22 (±2) | 3 | Months 1–3 | 8 | 7 |
| Months 6, 9, 12, etc. (Every 3rd Month) | Day 1 (±5) | 1 every 3rd month | From Month 4 | 33 | 30 |
| All Other Months | Day 1 (±5) | 1 a month | From Month 4 | 25 | 22 |
| EOT/ETD | (footnote 3, p.28) | 1 | End of treatment | 31 | 31 |

Both count sets match the target exactly. No mark was left unplaced. Appendix 3 (p.111) is found and
marked not counted.

Checks:
- pytest, full offline suite: 1527 passed, 1 skipped (before the reviewer fixes below);
  `tests/schedule` and `tests/domain/test_registry.py` after them: 171 passed.
- `ruff check .`: clean. mypy on the touched source files: clean. mypy on
  `tests/domain/test_registry.py` reports 2 errors, both in tests that existed before Prompt C
  (`TestReportWording`, `TestABreakdownDisplay`); not fixed, as outside Prompt C.
- One test failed that predates Prompt C and was fixed in its own commit, `902f53f`:
  `tests/render/test_report_model.py::test_nothing_lost_on_a_real_run[r-20261016-prompt-b]`
  expected "—" on a not-applicable row, which Prompt B item 4 (`e514bec`) prints as "N/A".
  `r-20261016-prompt-b` is the first saved run with such a row.
- Gate reviewers, first pass. The pipeline-rules reviewer found no violation of the ten rules and
  one style gap: an `Any` without its comment (`schedule/__init__.py`, `_body_top`); fixed. It also
  raised, and these were fixed: a header footnote was looked for in raw page text, so a "3." line in
  removed text or in the next section could be taken; it is now looked for only in the schedule
  section's own kept text (`section_page_texts`) and only up to the section's last page. And the
  comment over `schedule_table` in `config/fields.yaml` said every list is matched as words in
  order; it now says how each list is matched. Recorded, not fixed: a table printed under a
  set-aside heading on a schedule section's last page would be read as the schedule's (`_owner`'s
  docstring says so; none of the three sample protocols has that layout). The plan-conformance
  reviewer found stage 1 conforms, and noted that `count_non_assessments` has no effect until the
  report reads it (stage 3), and that the stage 2 node must run after SET_ASIDE_SECTIONS.
- Gate reviewers, second pass after the fixes: the pipeline-rules reviewer found no violation; the
  plan-conformance reviewer found stage 1 conforms and the eleven counts hold. Notes carried to
  stage 2: the reader's log line has no `run_id` (the node supplies one); the comment on
  `repeating` says "period" where the code matches period and timing together; `_says` is a second
  word matcher beside `scope_has_phrase`, kept because of `{nth}`; the header words and footnote
  are joined text, not quotes, and must be shown as the table's header, never validated as quotes.

Commit: `5b72739`. A test-only commit before it, `902f53f`, fixed the pre-existing failure above.

### Stage 2 — the READ_SCHEDULE node, saved in `extraction.json`

What it adds:
- `read_schedule_node` in `src/rfp_intake/schedule/__init__.py`: the READ_SCHEDULE graph node. It
  calls `read_schedules` on every document and writes `RunState.schedules` (a new field in
  `src/rfp_intake/domain/schemas.py`, written once, no reducer). No model call.
- Static edges in `src/rfp_intake/graph/__init__.py`: MARK_OTHER_STUDY → READ_SCHEDULE → PLAN; the
  old MARK_OTHER_STUDY → PLAN edge is gone. READ_SCHEDULE sits after MARK_OTHER_STUDY so that a
  footnote is looked for only in kept text.
- `extraction.json` gets a `schedules` key (`src/rfp_intake/render/json_renderer.py`), an empty list
  when no schedule was found.
- The step list in `app.py` and `app_v2.py` gets READ_SCHEDULE, "Counting the schedule of events".
- `scripts/rerender_report.py` loads the saved schedules, or, for a run made before this node,
  reads them again from the run's own `inputs/` PDFs.
- The topology diagram in `docs/ARCHITECTURE.md` §2 shows READ_SCHEDULE. The topology line in
  `CLAUDE.md` "Current status" is not changed; that section is Oliver's status record.
- Tests: `tests/schedule/test_read_schedule_node.py`, and the topology tests in
  `tests/graph/test_topology.py`.

Decisions taken for Oliver in stage 2:

8. **A failure in READ_SCHEDULE is a `validation` error on that document, and the run goes on.**
   Alternative: `call_failed`, or letting the exception end the run. Reason: no model is called, so
   `call_failed` would be wrong; SET_ASIDE_SECTIONS uses `validation` for its own code-only failures;
   and one unreadable table must not lose every other field. The report's fallback (stage 3) then says
   the table could not be read.
9. **A re-render of a run made before READ_SCHEDULE reads the schedule again from the run's inputs
   with the cheapest parser only** (rung 1, PyMuPDF), so a re-render never reaches Docling and never
   downloads a model (CLAUDE.md open item 9). Alternative: the full parser ladder, as INGEST uses.
   Reason: the reader needs only the PDF and the section boundaries, and the stage 1 tests already
   read Table 1 from rung 1's text. A run whose PDFs needed rung 2 or 3 would re-render with a
   different text from the live run's; `extraction.json` does not record which rung a document used,
   so this cannot be checked from a saved run.
10. **The re-render finds each input's document id by the cited quotes** (document ids are random per
    run, so the file name does not give it): each saved row's quote is looked for on its cited page of
    each input, and the input takes the id with the most hits. Alternative: match by file name order.
    Reason: input order is not recorded in `extraction.json`. On `r-20261016-prompt-b`: "Example
    protocol 2.pdf is doc-94d4c6c8", "Synthetic_RFP_NEOD001.pdf is doc-d906eabd".

Result: on `r-20261016-prompt-b`, the re-render reads 2 schedules from the protocol (Table 1, counted;
Appendix 3 p.111, not counted) and none from the synthetic RFP, with the eleven counts unchanged.

Checks:
- pytest, full offline suite: 1535 passed, 1 skipped.
- `ruff check .`: clean. mypy on `schedule/__init__.py`, `graph/__init__.py`,
  `render/json_renderer.py`, `domain/schemas.py`, `tests/schedule` and `tests/graph/test_topology.py`:
  clean.
- Gate reviewers, first pass. The pipeline-rules reviewer found no violation of the ten rules. It
  raised, and these were fixed: `docs/ARCHITECTURE.md` lacked the `RunState.schedules` line and the
  node's contract (now §3 and a new §4.2d); §2 still said "All four nodes"; the `graph/__init__.py`
  docstring and a test docstring said a schedule in another study's text is never read, when only a
  section MARK_OTHER_STUDY removed whole is skipped; §4.2d said an unreadable table gives a
  `RunError`, when a schedule section with no readable table gives a schedule with `problem` set
  (an unexpected failure gives the `RunError`); and the re-render's limits were not written down
  (now in the `scripts/rerender_report.py` docstring: today's config and FIND_SECTIONS on rung 1's
  text, removals matched by section id only, and two inputs could be given the same document id).
  The plan-conformance reviewer found stage 2 conforms, and noted that the `RunError.kind` comment
  said "validation" means a model answer was rejected; it now says code-only steps use it too. Both
  also noted that a `validation` error from READ_SCHEDULE is counted in `extraction.json`'s errors
  and in `scripts/check_review_2026_10_07.py`'s error count; this matters when comparing runs.
- Gate reviewers, second pass after the fixes: the pipeline-rules reviewer found no violation, and
  the plan-conformance reviewer found stage 2 conforms, with the eleven counts holding. Recorded,
  not changed: a re-render of a pre-node run with no `inputs/` folder, or whose quotes match no
  file, gets no schedule and falls back to the old text, with only a line on stdout to say so; and
  nothing yet runs the whole compiled graph with the new node offline, so the first proof that
  `schedules` survives into the final state is the live run.
- Also in this stage: a comment-only edit in `config/fields.yaml` (`repeating` now says "period or
  timing", as the code matches both).

Commit: `51de1fe`.

### Stage 3 — the visit table and the stage totals in the report

What it adds:
- `config/fields.yaml`: `from_schedule: table` on `visits.frequency_by_period` and
  `from_schedule: stage_totals` on `visits.total_count`, so no field id is named in Python;
  `schedule_table.wording`, every header and sentence the report prints for a schedule.
  `src/rfp_intake/domain/registry.py` checks both: one field of each kind, totals only with a table and
  only into a scoped int, and each wording names only its own placeholders.
- DERIVE (`src/rfp_intake/derive/__init__.py:stage_totals`): after the rubrics, one row per stage of
  `visits.total_count`, the sum with its parts in `notes` (exception A); a stage with `says`, or with
  a repeating column, has no number and its words instead (decision C).
- The report model (`src/rfp_intake/render/report_model.py`, "The Schedule of Events (Prompt C)"):
  `_visit_schedule` builds the table and lines, `_show_schedule` puts the table in the
  visit-frequency row and the totals in the total-visits row; both read "Worked out by the tool —
  confirm". With no table: "No schedule table found." or "Schedule table on p.N could not be read."
  above the row's usual text.
- report.pdf (`render/pdf_renderer.py:_visit_cell`): the table spans the main table's width under
  the row. report.xlsx (`render/xlsx_renderer.py`): a third sheet, "Schedule of Events".
- `scripts/rerender_report.py --rederive` still throws away and recomputes every `derived: true`
  field, and now also DERIVE's stage-total rows of `visits.total_count`, never the documents' own rows
  of that field.
- `docs/ARCHITECTURE.md` §4.8 (stage totals) and §4.10 (the two rows and the sheet).
- Tests: `tests/render/test_visit_table.py` (the real protocol: both settings, considerations,
  pages, the not-counted line, the totals, nothing lost, report.pdf and report.xlsx; made-up
  schedules: both fallbacks and the sums), and four registry tests in `tests/domain/test_registry.py`.
  Seven existing tests changed because the report now says what it did not before:
  `tests/render/test_entries_display.py::TestVisitFrequency` (six tests; their states hold no
  counted schedule, so each row now starts "No schedule table found.") and
  `tests/render/test_xlsx_renderer.py` (three sheets, not two).

Decisions taken for Oliver in stage 3:

11. **The not-counted line reads "Not counted: patients who stop treatment early follow Appendix 3,
    Amendment p.111 (printed p.109), budgeted separately."** Prompt C's target is "… follow Appendix 3
    (p.111), budgeted separately." Alternative: the target's brackets. Reason: the report's usual page
    style is "Amendment p.111 (printed p.109)", and inside brackets it would nest brackets. The name
    "Appendix 3" is the heading before its first colon.
12. **The count column is headed "Assessments", or "Activities" when every mark is counted.**
    Alternative: "Marks". Reason: "Activities" is the word the table itself uses for its rows; a mark
    is how the count is made, not what is counted.
13. **The stage totals are added after the rubrics.** Alternative: before, so visit intensity could
    read them. Reason: the intensity rating has `visits.total_count` among its inputs, and it must not
    be moved by a number the tool worked out; its result on `r-20261016-prompt-b` is unchanged.
14. **A stage with no number ("From Month 4") is a `needs_review` row with no value and its words in
    `notes`.** Alternatives: no row; or `not_specified`, which the first draft used. Reason:
    `extraction.json` then records every stage, and `not_specified` means the documents say none
    (ARCHITECTURE.md line 101), but Table 1 does state these visits ("1 a month"), only not as one
    number. Changed after the pipeline-rules reviewer's first pass. A stage that mixes fixed and
    repeating columns and has no `says` keeps every part in `notes`, for example "1 + 1 a month"
    (none does in today's config); the first draft dropped the fixed ones.
15. **"End of treatment: 1" is printed**, though one visit is not a sum. Alternative: leave it out.
    Reason: Prompt C asks for total subject visits by stage, and End of treatment is a stage in config.
16. **DERIVE stores confidence 1.0 on the total rows, and the report prints no confidence figure on
    either row.** Alternative: print 100%. Reason: the confidence is the model's rating of how clearly
    a document stated a value (CLAUDE.md known problem 6); a count by code has no such rating, and
    100% would read as certainty. Appendix A still prints the stored 100%.
17. **The visit-frequency row keeps the model's rows as its members**, so Appendix A still quotes them,
    the nothing-lost check finds them, and Flagged for review lists them with their own reason
    ("Budget driver holding more than one value"). Alternative: drop them from the report. Reason:
    the report never loses a value the documents gave (`assert_nothing_lost`).
18. **The Where column repeats each column's pages.** Alternative: pages only in the caption. Reason:
    Prompt C says every count cites its page. Table 1 runs over two pages and every column is on
    both, so every row reads "Amendment p.26 (printed p.24), 27 (printed p.25)".
19. **A header footnote's page is printed with its printed number** (`ScheduleColumn` gained
    `timing_footnote_printed_page`). Alternative: the PDF page alone. Reason: every other page in the
    report gives both.
20. **Marks that could not be placed in a row are listed under their table**, not under the totals.
    Table 1 has none.
21. **A total row that a document states itself is printed under the counted totals as "A document
    states: …"**, and the document's sentence saying why it gives no total is quoted there (as the
    `when_not_found` note did). Alternative: hide the document's own rows once the tool counts.
    Reason: a reader must see where the count and the document differ. On the sample protocol no
    document states a total. Known edge, accepted: such a line prints "()" if the row cites no page.
22. **`visits.total_count` now counts as found** when the tool counted it. So the first page's "N of
    M variables found" moves by one against `r-20261016-prompt-b` (42 of 45 on the re-render), and
    check 9 of `scripts/check_review_2026_10_07.py` may move with it.
23. **report.xlsx carries the table on a third sheet, "Schedule of Events"; its "Fields" sheet rows
    for the two variables are not reworded.** Alternative: put the table lines, the fallback line and
    "Worked out by the tool — confirm" into the Fields rows. Reason: the Fields sheet prints one line
    per stored row and has no place for a table or block lines. It already lags the PDF layouts
    (CLAUDE.md open item 6), so the stage-total rows there show their stored value, status and
    confidence (100%, which decision 16 keeps off report.pdf), and the "From Month 4" words, held in
    `notes`, are not printed there. The third sheet has all of them.
25. **A column the stage words place in no stage is named under the totals**: "Not in any stage, so
    not in the totals: {periods}." (`schedule_table.wording.no_stage`). Alternative: leave it out
    silently. Reason: a reader adding up the table would otherwise find visits the totals do not
    include, with no reason given. Table 1 has none; `test_a_column_in_no_stage_is_named` covers it.
24. **The stage totals are caught like a rubric.** If adding them up fails, DERIVE records a
    `RunError` and keeps every other field. Alternative: let the run stop. Reason: decision 8, one
    unreadable table must not lose every other field. Added after the pipeline-rules reviewer's first
    pass.

Known limits, recorded and not fixed (from the reviewers' two passes):
- A stage with `says` in config that also holds a fixed column prints only the `says` words, so that
  column's count is not in the totals line (the table above it still shows it). None does in today's
  config: "From Month 4" is the only stage with `says`, and its columns all repeat.
- A stage in config that no column falls in has no row and no line, so it is silently absent from the
  totals. The "Not in any stage" line covers only the opposite case, a column in no stage.
- The "From Month 4" words are fixed in config (decision C, approved). They print for whatever
  columns the period words place in that stage: a protocol with an "Every 6th month" column in that
  stage would still print "1 a month until the study ends". The table's own words for each column are
  in the Visits column beside it.
- The first page's "N of M variables found" counts "Visit frequency by study period" only when the
  model found a row for it. If the model found none and the tool read a table, the table prints but
  the count does not include it.
- "No schedule table found." is about the tool: it prints even where "Schedule of events present"
  says the documents have one, as on `Example Protocol.pdf` (gaps section below).
- A header footnote is read up to the next numbered line, so unrelated text after it on the page
  (such as a running header) could be printed with it. Table 1's footnote 3 is clean.

The visit table as it prints, from `python scripts/rerender_report.py r-20261016-prompt-b
--rederive` (report.pdf page 3, `runs/r-20261016-prompt-b/report-rerender.pdf`). The caption reads
"Table 1: Schedule of Events — Amendment p.26 (printed p.24), 27 (printed p.25)", and every row's
Where cell reads "Amendment p.26 (printed p.24), 27 (printed p.25)":

| Period | Visit timing | Visits | Assessments | Key considerations |
| --- | --- | --- | --- | --- |
| Screening | Day-28 to Day -1 | 1 | 26 | Informed consent, Eligibility review |
| Screening | Day -2 or Day -1 | 1 | 16 | — |
| Month 1 | Day 1 | 1 | 13 | Randomization, Premedication, Study drug infusion, Chemotherapy |
| Month 1 | Days 8, 15, 22 (±2) | 3 | 7 each | Chemotherapy |
| Month 2 | Day 1 (±2) | 1 | 23 | Premedication, Study drug infusion, Chemotherapy |
| Month 2 | Days 8, 15, 22 (±2) | 3 | 6 each | Chemotherapy |
| Month 3 | Day 1 (±2) | 1 | 28 | Premedication, Study drug infusion, Chemotherapy |
| Month 3 | Days 8, 15, 22 (±2) | 3 | 7 each | Chemotherapy |
| Months 6, 9, 12, etc. (Every 3rd Month) | Day 1 (±5) | 1 every 3rd month | 30 each | Premedication, Study drug infusion, Chemotherapy |
| All Other Months | Day 1 (±5) | 1 a month | 22 each | Premedication, Study drug infusion, Chemotherapy |
| EOT/ETD | From the footnote on Amendment p.28 (printed p.26): EOT/ETD Visit to occur 28-35 days after the last study drug administration. | 1 | 31 | — |

Under it: "Not counted: patients who stop treatment early follow Appendix 3, Amendment p.111 (printed
p.109), budgeted separately." The row reads "Counted from Table 1: Schedule of Events, below." and
"Worked out by the tool — confirm".

The next row, "Total number of subject visits", reads "Counted from Table 1: Schedule of Events:",
then "Screening: 2 (1 + 1)", "Months 1–3: 12 (1 + 3 + 1 + 3 + 1 + 3)", "From Month 4: 1 a month
until the study ends", "End of treatment: 1", then the protocol's sentence "This is an event-driven
trial that will continue until approximately 156 primary endpoint events have occurred. …" (Amendment
p.15 (printed p.13)), and "Worked out by the tool — confirm".

The eleven counts are 26, 16, 13, 7, 23, 6, 28, 7, 30, 22, 31, as Oliver's target. With
`count_non_assessments: true` the test suite reads 26, 16, 17, 8, 26, 7, 31, 8, 33, 25, 31.

How the other two protocols would print (offline, reported, not fixed): `Example Protocol.pdf`
prints "No schedule table found." above today's text. `Example protocol 3.pdf` prints three tables
(Tables 2, 3 and 4) with periods such as "Treatment and Follow-up Period 1 NA", no timing, and no
column in a stage, so no totals are printed and the total-visits row is as before.

Checks:
- pytest, full offline suite: 1560 passed, 1 skipped before the reviewers' first pass; 1562 passed,
  1 skipped after the fixes from that pass (decisions 14 and 24, two new tests).
- Re-render of `r-20261016-prompt-b --rederive` after those fixes: the visit table and totals above
  are unchanged.
- `ruff check .`: clean. mypy on `render/report_model.py`, `render/pdf_renderer.py`,
  `render/xlsx_renderer.py`, `derive/__init__.py`, `domain/registry.py`, `domain/schemas.py`,
  `schedule/__init__.py`, `scripts/rerender_report.py` and `tests/render/test_visit_table.py`: clean
  (one error, a loop variable `kind` reused with another type in `domain/registry.py`, was fixed).
- Gate reviewers, two passes each. First pass: pipeline-rules found no violation and five
  suspicions, of which three were fixed in code (decisions 14 and 24) and the rest recorded as known
  limits; plan-conformance found three log problems (placeholders, the xlsx Fields sheet, the
  `--rederive` sentence), all fixed in the log (decision 23). Second pass, after the last edit to
  src/: pipeline-rules found no violation, safe to commit, and two latent limits now recorded above;
  plan-conformance found the stage conforms with no code problem, and three record gaps, now fixed
  (decision 23's confidence, decision 25, and an ARCHITECTURE.md §4.10 sentence on the Fields sheet).
  Pipeline-rules noted 6 mypy errors in `llm/mock.py` and `llm/provider.py`, files stage 3 does not
  touch.

Commit: `13b0823`.

### Stage 4 — the must-hold facts for the Schedule of Events

What it adds:
- `eval/golden/sample_pair_must_hold.yaml`: facts 10 to 14, read from Table 1 of the amendment
  (PDF p.26–27):
  - 10: each visit column's assessments are 26, 16, 13, 7, 23, 6, 28, 7, 30, 22, 31;
  - 11: each column's marks of every row are 26, 16, 17, 8, 26, 7, 31, 8, 33, 25, 31;
  - 12: informed consent and eligibility review are marked at the first screening visit;
  - 13: randomization is marked at Month 1 Day 1;
  - 14: study drug is marked at every Day 1 visit from Month 1 (five columns).
- `scripts/check_must_hold.py`: two new kinds of fact. `schedule_counts` compares one count per
  column, in order, in the setting the fact names. `schedule_marks` chooses columns by their header
  words and checks each has a mark in a row of every listed consideration. Both read the one counted
  schedule with that heading from that kind of document in `extraction.json`'s `schedules`.
- Tests in `tests/test_check_must_hold.py`: facts 10 to 14 pass on READ_SCHEDULE's tables for the real
  protocol, saved as `extraction.json` saves them; and made-up runs fail where they should (a run from
  before READ_SCHEDULE, another document kind, another heading, a not-counted schedule, a Day 1
  without study drug, a header read so that fewer columns are chosen).

Decisions taken for Oliver in stage 4:

26. **Five facts, not one.** Alternative: one fact for the whole table. Reason: a failure then names
    what broke — a count, or which consideration at which visit.
27. **The facts check the counts READ_SCHEDULE saved, not the text report.pdf prints.** Alternative:
    read report.pdf. Reason: the script's own rule (its docstring): whether the pipeline produced the
    answer, read from `extraction.json`. The report's printing of the same counts is covered by
    `tests/render/test_visit_table.py`.
28. **A run made before READ_SCHEDULE fails facts 10 to 14**, saying "no schedules in
    extraction.json: the run was made before READ_SCHEDULE". Alternatives: skip the facts, or KNOWN.
    Reason: KNOWN is kept for failures tied to an open item in CLAUDE.md (Oliver, 2026-10-15), and a
    skipped fact could hide a run that lost its schedules. A consequence: from this stage on
    `scripts/check_must_hold.py` gives `r-20261016-prompt-b` 8 pass, 1 known, 5 fail (facts 10 to 14),
    so the commit gate denies a commit message that names that run. It is still the baseline for the
    run-comparison reviewer.
29. **Fact 14 says how many columns it must choose (5)**, and fact 13 one. Alternative: any number.
    Reason: a header read differently ("Day 15" for "Day 1") would otherwise pass on fewer columns.
30. **Columns are chosen by the header's words, not by position.** Alternative: column numbers.
    Reason: the facts then say what Prompt C says ("every Day 1 visit from Month 1") and can be
    checked by eye against the PDF.

Checks:
- `python scripts/check_must_hold.py r-20261016-prompt-b`: facts 1 to 9 as before (8 pass, 1 known),
  facts 10 to 14 fail as decision 28 says.
- pytest, full offline suite: 1573 passed, 1 skipped (11 new tests).
- `ruff check .`: clean. mypy on `scripts/check_must_hold.py` and `tests/test_check_must_hold.py`:
  clean.
- Gate reviewers, one pass each (no file under `src/` or `config/` changed, so the commit gate does
  not apply, but Oliver asked for both). Pipeline-rules found no violation, and that facts 1 to 9 and
  KNOWN are unchanged and that no fact passes on an empty match. Five suspicions were left as they
  are: facts 10 and 11 check the counts but not the column headers (facts 12 to 14 do check them); a
  misspelt `marked` id makes a fact fail, not pass, but with an unclear message; "first screening
  visit" means the first column whose period is "Screening"; the real-protocol test sets the document
  kind to amendment itself; and the gate now refuses a commit message naming `r-20261016-prompt-b`
  (decision 28). Plan-conformance found the stage conforms, with no code problem. Two record gaps
  were fixed: the facts file's legend now names every key the schedule facts use, and says that
  `columns` is a list of counts in `schedule_counts` but a number of columns in `schedule_marks`; and
  the commit line below no longer points at a section that did not exist. Both reviewers ran the
  real-protocol test alone, and it passed.

Commit: stage 4's hash is in the section "Live run r-20261017-prompt-c2" below, written after the
commit, because a commit cannot hold its own hash.

## Live run r-20261017-prompt-c2

Stage 4's commit: `aac658a`.

**The run failed on credentials, before reading any document.** Started on 2026-10-17 at 20:26:49
with `python -m rfp_intake.job r-20261017-prompt-c2`, on `samples/Example protocol 2.pdf` and
`samples/Synthetic_RFP_NEOD001.pdf`, using the token already in the environment
(`AWS_BEARER_TOKEN_BEDROCK` was set and had the expected prefix; it was not printed). PREFLIGHT, the
step that checks that the model service answers, got "AccessDeniedException … Bearer Token has
expired" from AWS Bedrock two seconds later. `runs/r-20261017-prompt-c2/status.json` says
`"state": "failed"`, `"node": "PREFLIGHT"`, failure kind `model_service`. The folder holds only
`inputs/` and `status.json`, with no `extraction.json`, `report.pdf` or `report.xlsx`.

As Oliver instructed, the run was not retried and no token was looked for anywhere else.

Not done, because each needs the run's output:
- `scripts/check_must_hold.py r-20261017-prompt-c2`: facts 10 to 14 have not yet been seen to pass
  on a live run. They pass offline on READ_SCHEDULE's tables for the real protocol (stage 4's slow
  test).
- `scripts/check_review_2026_10_07.py` on the run.
- The run-comparison reviewer against `r-20261016-prompt-b`.
- The report-usability reviewer.

**Second attempt, `r-20261018-prompt-c2`, failed the same way.** Started on 2026-10-18 at 19:54:51
after Oliver said a new token had been added. `AWS_BEARER_TOKEN_BEDROCK` was set with the expected
prefix, but PREFLIGHT got "Bearer Token has expired" again at 19:54:54
(`runs/r-20261018-prompt-c2/status.json`: failed at PREFLIGHT). Not retried, and no token looked for
elsewhere.

**Third attempt, `r-20261019-prompt-c2`, completed.** Oliver added a new token on 2026-10-19. The
pipeline's own model service check (`probe_model_service`, `src/rfp_intake/llm/health.py:217`) was
run once on its own first and answered. The run started at 12:44:58 and finished at 12:53:09
(8m11s), on the same two documents: 59 resolved rows, 14 contradiction clusters, 10 errors.
READ_SCHEDULE saved two schedules: "Table 1: Schedule of Events" (counted, 11 visit columns) and
Appendix 3, the schedule for subjects who stop study drug early (not counted, 3 columns).

- `scripts/check_must_hold.py r-20261019-prompt-c2`: **14 pass, 0 known, 0 fail.**
  - Facts 10 and 11 give the target counts: assessments 26, 16, 13, 7, 23, 6, 28, 7, 30, 22, 31, and
    every mark 26, 16, 17, 8, 26, 7, 31, 8, 33, 25, 31.
  - Facts 12 to 14 hold.
  - Fact 8 passes in full this time: site selection visits reads "a total of 80 visits", so open item
    1 of CLAUDE.md did not occur on this run.
  - Fact 9: 42 of 45 fields have a value.
- `scripts/check_review_2026_10_07.py r-20261019-prompt-c2`: 8 pass, 0 fail. Check 6 is LOOK, and
  its live half is ok. Check 9: 38 kept variables found, against 36.
- Run-comparison reviewer against `r-20261016-prompt-b`: **mixed, leaning better.** No field lost a
  value. Fields with a value went from 40 to 42: "Total number of subject visits" and "Site
  selection visits" gained one. Fields with a confirmed row stayed at 30. The removed passage
  (1.3.2 Clinical Experience) and the 57 set-aside sections are the same in both runs. Study phase
  is one confirmed Phase 3 row in both. The reviewer checked the four stage totals against the
  saved schedule, and the sums match the table. Findings, recorded and **not fixed**:
  1. "Dosing duration", a budget driver, went from confirmed to needs review. The value is
     unchanged (Amendment p.54). A new cluster filed the RFP's "40 month study duration" (RFP p.3)
     as a dosing duration. ADJUDICATE called it not a conflict and marked that record off-topic,
     but GATE's budget-driver rule still marks the field for review. This is the one budget
     driver that is worse than in `r-20261016-prompt-b`, and it is not caused by Prompt C.
  2. "Total number of subject visits" still keeps its old not-found row, with no source, beside
     the four stage totals. report.xlsx's Fields sheet prints that row ("not_found") directly above
     them. I checked report.pdf p.11 (Appendix A) with pymupdf: it prints only the four stage rows,
     so the contradiction is in the spreadsheet only. This one is Prompt C's: DERIVE's
     stage totals should replace the not-found row.
  3. Errors rose from 9 to 10, all EXTRACT quote checks. The site selection error is gone; two new
     `quote_not_found_in_excerpt` rejections appeared, on "Evidence of visit intensity" (Amendment
     p.14) and "Visit frequency by study period" (a quote copied from the p.26 table with its
     separator characters). Nothing was lost through them.
  4. The scores against the old golden files in `eval/golden/` are the same in both runs, and those
     files describe a different study, so they say nothing here. "Verdict accuracy" reads 1.00 then
     0.00, but only because the scorer keeps the last cluster per field, which this time is the
     subject-level duration cluster. The study-level disagreement (42 against 40 months) is caught
     in both runs.
  The reviewer was refused permission to read report.pdf, did not retry, and based its layout
  remarks on extraction.json and report.xlsx only.
- Report-usability reviewer: Table 1 on report.pdf p.3 prints the 11 target counts and reads
  clearly. It handles repeating visits well ("3", "7 each", "1 a month"), shows each sum with its
  parts, and puts the "Not counted" line, naming Appendix 3 (Amendment p.111), directly under the
  table. The report is 20 pages, the same as `r-20261016-prompt-b`, with about 10% more text
  before Appendix A. Findings that touch Prompt C, recorded and **not fixed** (Oliver: nothing
  beyond Prompt C without him):
  1. The model-written "Visit frequency by study period" records still print in Flagged for review
     (p.6, "Budget driver holding more than one value", citing eight pages) and in Appendix A
     (p.10–11, about 28 bullets with raw period tokens such as `early_treatment` and table scraps as
     quotes). They now sit beside Table 1 and disagree with the main row on p.2.
  2. "Total number of subject visits" prints as four rows in Flagged for review (p.6–7), and "From
     Month 4" shows a bare dash there. Page 1's "13 other values are flagged for review" counts
     those rows, not variables (10 variables; 8 in `r-20261016-prompt-b`).
  3. Nothing says the Assessments count leaves out randomization, premedication, study drug and
     chemotherapy. "Key considerations" is not in the word list, and the word list entry for "Visit
     frequency by study period" still describes the old row.
  4. Every row prints the same "Where" text (Amendment p.26 and 27), because the table spans both
     pages. That costs width and does not say which page holds which count.
  5. The EOT/ETD timing cell is a whole footnote sentence wrapped over six lines. "EOT/ETD" is not
     defined. "—" in Key considerations could mean either "none" or "not found".
  6. There are no per-stage assessment totals, and no caveat that not all weekly visits need to be
     at the site (Amendment p.13).
  Findings not about Prompt C, recorded only: yes-or-no answers lack their proof phrase in the main
  table; raw tokens show ("Yes body", `yes_body`, `ops.subjects_total`); several values are cut off
  with "…"; the Timeline components value is garbled; and several abbreviations are missing from
  the word list.

## Follow-ups of 2026-10-19

Oliver's instruction, after the review of `r-20261019-prompt-c2`, verbatim:

> 1. When the visit table is shown, the old model-written Visit frequency records do not print in
>    Flagged for review or Appendix A. Keep them in extraction.json only.
> 2. Total number of subject visits: drop the old "not found" row when the four stage totals exist,
>    in report.pdf and in report.xlsx.
> 3. Flagged for review shows Total number of subject visits as one row, not four, and never a bare
>    dash for "From Month 4". The page 1 count counts variables, not rows.
> 4. Under the visit table, one grey line saying the Assessments count leaves out randomization,
>    premedication, study drug and chemotherapy, with the wording from config/fields.yaml. Add "Key
>    considerations" to the word list.
> 5. Dosing duration: find why the RFP p.3 study-length sentence was read as a dosing duration, and
>    stop it. Tell me the cause before fixing.

Order taken, as agreed with Oliver: item 5's cause first, read-only, reported before any fix; then
items 1 to 4 as one render-only commit, shown by re-rendering `r-20261019-prompt-c2`; the live run
waits until item 5's fix is approved and made.

### Item 5 — the cause (found, not fixed; waits for Oliver)

In `r-20261019-prompt-c2`, EXTRACT, reading the RFP's one whole-document section, returned for
`dosing.duration` the value "40 month study duration; enrollment over approximately 24 months"
(RFP p.3, confidence 0.6); the model's own note says the sentence is not a dosing duration. Two
things let it through:
- the field's aliases in `config/fields.yaml` include "treatment duration" and "treatment period",
  which invite reading a study length as a dosing length;
- no code check keeps a free-text field's quote on its subject: `extract/validate.py`'s
  `answers_field` applies `quote_must_say` only to enum values.

ADJUDICATE then did the right thing — it named the record off-topic (`off_topic_record_indexes`
[2], verdict `not_a_conflict`) — but GATE's budget-driver rule turns any adjudicated field into
needs_review, so Flagged for review prints the row with "Budget driver; the documents word it
differently." GATE's rule is deliberate (CLAUDE.md, deviations) and is not the thing to change.

Fix proposed to Oliver, not built: (a) a config rule that a text field's quote must contain one of a
list of words, enforced in `answers_field` and keyed by config, never by field id; for
`dosing.duration`: dose, dosed, dosing, administered, drug, treatment, treated, cycle, cycles,
infusion. The RFP p.3 quote has none of them; the protocol's quote has "drug" and "administered".
(b) one hint sentence: the overall study duration or the enrolment period is not a dosing duration.

### Items 1 to 4 — what was built

All render-only; nothing in EXTRACT, RECONCILE, ADJUDICATE, DERIVE or GATE changed, and
`extraction.json` is unchanged.
- `render/report_model.py:report_entries` decides what the two reports print. Only when a Schedule
  of Events was counted with columns, and only by `from_schedule` in config/fields.yaml: every entry
  of the `table` variable, and each entry of the `stage_totals` variable that is not found, has no
  quote and is not derived, once DERIVE has written stage totals, is kept out. They are listed on
  `ReportModel.kept_out`, and the nothing-lost test (`tests/render/test_report_model.py:
  assert_nothing_lost`) now skips them and checks that nothing else is kept out.
- Flagged for review lists the counted table's row and the totals row once each (`Row.review_as_one`),
  with `Row.review_text` as its value: the new wording `schedule_table.wording.flagged_table` for the
  table, and the totals in short form, without their parts, for the totals. Page 1 counts distinct
  variables: "N other variables have values flagged for review, M of them budget drivers."
- Under the visit table, in grey (`VisitTable.note`): `schedule_table.wording.left_out`, "The
  {column} count leaves out {items}.", with the labels of the considerations marked
  `assessment: false`. Printed only while `count_non_assessments` is false. In report.xlsx's Schedule
  of Events sheet too.
- "Words used in this report" gains "Key considerations" (`wording.considerations_plain`), after
  Visit frequency by study period's own entry, only when a visit table is printed.
- report.xlsx's Fields and Review Queue sheets read `report_entries` too.

Decisions taken for Oliver:
- **Item 1 also covers report.xlsx.** The kept-out visit-frequency entries leave the Fields and
  Review Queue sheets as well. Alternative: keep them in the Fields sheet. Reason: "Keep them in
  extraction.json only", and the Schedule of Events sheet carries the table.
- **Item 2 drops only an empty "not found".** An entry of the totals variable that quotes why there
  is no total, or one a document states, is still printed. Alternative: drop every non-derived
  totals entry. Reason: a quoted passage is evidence; only the empty row repeats what the stage totals
  already answer.
- **Item 4's list uses the config labels**, lower-cased where the label starts with an ordinary
  capital, so it reads "study drug infusion", not Oliver's "study drug". Alternative: new short
  names in config. Reason: the instruction says to use the wording from config/fields.yaml.
- **Item 3, the table row.** The visit-frequency row is flagged once, with the new `flagged_table`
  wording ("Counted from Table 1: Schedule of Events; the table is under this variable in All
  variables.") in place of the main table's "below". Alternative: drop it from Flagged. Reason: GATE
  marks it for review, and every review row must be listed.
- **Item 3, the reasons.** The totals row's reasons are "Worked out by the tool" plus any reason a
  member row had (such as a document-stated total at low confidence), each once.

Known limits, recorded:
- Appendix B and the Disagreements table still read every entry. A numbered disagreement on the
  visit frequency itself would still print there, but its passages would be missing from Appendix A,
  which reads only the printed entries (pipeline-rules reviewer, 2026-10-19). `r-20261019-prompt-c2`
  has none: its only visits contradiction is on `visits.schedule_present`. Closing it (keeping an
  entry with its own numbered disagreement in the report) needs Oliver's decision, as item 1 says the
  model's entries as a whole.
- A document-stated total that is already a numbered disagreement would be listed twice: once in
  Disagreements and once inside the one totals row in Flagged for review, which is listed whole. No
  saved run has one.
- Appendix A still prints the derived "From Month 4" row with value "—" and the note "1 a month
  until the study ends". Item 3 named Flagged for review only.
- report.xlsx's Fields sheet shows the "From Month 4" value as an empty cell, as before.

### Re-render of `r-20261019-prompt-c2`

`python scripts/rerender_report.py r-20261019-prompt-c2` wrote
`runs/r-20261019-prompt-c2/report-rerender.pdf` (18 pages, against 20); report.xlsx was built from the
same state into `report-rerender.xlsx`. The run's own report.pdf and report.xlsx are untouched.
Checked with pymupdf and openpyxl:
- Page 1: "10 other variables have values flagged for review, 10 of them budget drivers" (was "13
  other values are flagged for review, 13 of them budget drivers").
- Flagged for review (p.6): one "Visit frequency by study period" row, "Counted from Table 1:
  Schedule of Events; the table is under this variable in All variables." (was the model's own list,
  "Period: screening; Label: Screening; Frequency: …"), and one "Total number of subject visits" row,
  "Screening: 2; Months 1–3: 12; From Month 4: 1 a month until the study ends; End of treatment: 1"
  (was four rows, "From Month 4" reading "—").
- Appendix A: no entry of Visit frequency by study period's own (was 5 lines of the model's
  readings); the four stage totals are kept; the "not found" total is gone.
- Under the visit table: "The Assessments count leaves out randomization, premedication, study drug
  infusion and chemotherapy."
- Words used in this report: "Key considerations — The activities a visit includes that matter most
  for a budget, …".
- report-rerender.xlsx, Fields and Review Queue: no Visit frequency row and no "not found" total row,
  four stage-total rows each; Schedule of Events sheet: the grey line's sentence under the table.

Checks: full offline pytest 1586 passed, 1 skipped (new tests in `tests/render/test_visit_table.py`: two in
`TestTheRealProtocol`, six in `TestPrintedOnce`, and four cases of `test_a_label_inside_a_sentence`).
Gate reviewers, two passes each. First pass: pipeline-rules found no violation and five suspicions;
plan-conformance found all four items met, two untrue sentences in `docs/ARCHITECTURE.md` §4.10 and
four test gaps. Answered: the nothing-lost test now also requires a kept-out total to be not found
and not derived; new tests for a quoted total staying printed, for `_lower_first`, and for the Review
Queue sheet; the xlsx note is found by its words, not its row number; `_lower_first`'s docstring is
rewritten; §4.10 is corrected; the two limits above are recorded. The found count ("43 of 45") is
the same before and after on `r-20261019-prompt-c2`.
Second pass, after the last edit to src/: pipeline-rules found no violation of the ten rules and
safe to commit, naming the two limits above as Oliver's decisions; plan-conformance found items 1 to
4 met and its first-pass problems fixed, and asked that §4.10 say the Disagreements table and
Appendix B still read every entry, which it now does.
`ruff check .` clean. mypy on the changed files: no new error (one older error at
`tests/render/test_report_model.py:194`, an identity check between a ResolvedField and a FieldRecord,
was there before this change).

## Gaps on the other two protocols (reported, not fixed, as Oliver instructed)

Measured offline with the stage 1 reader on 2026-10-17.

**`samples/Example Protocol.pdf`: no schedule found.** No section heading matches
`schedule_table.headings`. Its schedule is "Table 1 Time and Events Schedule" on PDF p.44–45, but the
table title is not a section heading, and the section it sits in has another heading (the nearest
schedule-like heading is "Trial Schedule", p.29). Its marks are bullets ("•"), which are in
`schedule_table.marks`, so the reader would count them if it reached the table.

**`samples/Example protocol 3.pdf`: three tables found, none read correctly.**
- Found: Table 2 (p.20 only — its p.21 continuation was not joined to it), Table 3 (p.22–23) and
  Table 4 (p.25–26).
- Periods read as, for example, "Treatment and Follow-up Period 1 NA", with no timing: the row
  "Study Day" sits in the label column, so no header line starts with a timing word and the timing is
  never split from the period. So every column reads 1 visit and no column is in a stage.
- A column headed "For details see Section" is not a visit, but is read as one.
- The category rows ("Efficacy assessments", "Safety assessments" and similar) are listed as "in no
  visit column".
- Table 1, the screening schedule on p.19, is not found: its heading does not match, or its section
  was set aside.

## Not done

- Footnote notes, home dosing and schedules printed as images: out of scope in Prompt C.
- Tables without ruled lines are not read (the plan's decision; none of the three protocols' target
  tables needs it).
- The gaps on `samples/Example Protocol.pdf` and `samples/Example protocol 3.pdf`: reported above,
  not fixed, as Oliver instructed.
