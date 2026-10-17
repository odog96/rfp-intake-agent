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
