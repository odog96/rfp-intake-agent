# CLAUDE.md — RFP Intake Agent

## What this is
A deterministic LangGraph pipeline that reads clinical study RFP and protocol documents and
produces a provenance-backed variable set for the Delivery Strategy & Budgeting (DSB) team.

**Read `docs/ARCHITECTURE.md` before writing code. It is the build contract, not background reading.**
`config/fields.yaml` is the single source of truth for what gets extracted.

**Current work: `docs/PLAN_2026-10-02.md`.** Read it before anything else. It adds three nodes between
CLASSIFY and PLAN, changes PLAN, EXTRACT and DERIVE, and gives the build order and the test for each
stage. Where it disagrees with `docs/ARCHITECTURE.md`, the plan is the newer decision. The domain rules
behind it are in `docs/ANALYST_PROCEDURE_PROTOCOL.md` (a draft awaiting Angus Gray's sign-off; build
against it anyway).

## Current status (as of 2026-10-02)

Phases 0–4 of ARCHITECTURE.md §10 are done, and stages 1 and 2 of `docs/PLAN_2026-10-02.md`. Graph
topology today:
`INGEST → CLASSIFY → FIND_SECTIONS → PLAN → EXTRACT → NORMALIZE → RECONCILE → ADJUDICATE → DERIVE → GATE`,
then RENDER runs after the graph finishes (see the deviations below). 635 tests passing, 1 skipped.
Pushed to https://github.com/odog96/rfp-intake-agent.git. The push is done from a terminal by
Oliver, from inside the repository directory — this session's credentials cannot do it.

**The whole path works end to end through the Cloudera AI Application.** A user loads documents,
the application creates a run of the CML Job named "RFP Pipeline Executor", the job runs the
pipeline and writes `extraction.json`, `report.pdf` and `report.xlsx`, and the application polls
and reports the outcome. Verified 2026-08-27 with job run `pde6ypjc38gypgy2`.

### Current model
Claude Sonnet 4.6 on AWS Bedrock (`us.anthropic.claude-sonnet-4-6`), `privacy_mode: mixed`.
**Testing only** — Bedrock is outside the customer boundary, so synthetic and publicly-registered
documents only, per rule 5 below. Production is CAII and `privacy_mode: private`.

On the two-document test pair it produced 108 confirmed fields, 27 contradictions and no errors in
about twelve minutes, and caught the planted `timeline.total_duration` disagreement. It is the only
model tried that extracts the budget drivers reliably. Claude models needed the Anthropic use-case
form submitted in the Bedrock console for account 240534893097; everything else in that account
except `us.meta.llama3-1-70b-instruct-v1:0` is blocked by an AWS service control policy (a
company-wide rule an AWS administrator controls), `p-dlt9r6fc`.

Two other models were tried and are worse. CAII Nemotron 3 Super 120B is served without vLLM's
`--enable-auto-tool-choice` and `--tool-call-parser`, so it cannot return structured output through
tool calling at all and 5 of 9 field groups failed. Bedrock's Llama 3.1 70B has tool calling
available but does not use it on long extraction prompts, and failed 1 of 9 groups.

### Deviations from the ARCHITECTURE.md text — read before assuming the doc is exact
`docs/ARCHITECTURE.md` was corrected on 2026-08-27 to match the code, and each deviation is now
recorded inline in its own section rather than only here. The load-bearing ones:
- **RENDER is not a LangGraph node.** Pure functions in `render/`, called from `job/__init__.py`
  after `compiled.stream()` finishes.
- **GATE is stricter than the §4.9 table**: a `budget_driver` field forces `needs_review` on any
  adjudicated verdict, including `not_a_conflict`.
- **ADJUDICATE never picks the winning value for a `conflict`.** Tie-break is deterministic, in
  `reconcile/precedence.py`.
- **DERIVE's visit-intensity rubric weights are a best-effort remap** of §4.8's table onto
  `fields.yaml`'s current enum. See `derive/rubric.py` before recalibrating.

### Known problems, in the order they hurt
1. **`study.phase` reads the phase of a different study.** In run `r-listfix-175318` it confirmed
   `phase_1_2` with the scope "Study NEOD001-001 (referenced study)" — the phase of an earlier study
   the protocol mentions. The correct `phase_3` is present but marked `needs_review`. Nothing in the
   pipeline knows that a value about another study should be discarded. Repeated in run
   `r-20261001-032936`, from section 1.3.2 "Clinical Experience" (PDF page 39). The fix is stage 4 of
   `docs/PLAN_2026-10-02.md` (a new MARK_OTHER_STUDY node), not a prompt change in EXTRACT.
   Two causes found on 2026-10-01 by reading the code. The first — PLAN sends whole pages plus one page
   of margin, so text from sections it did not choose reaches the model — **was fixed by stage 2 on
   2026-10-02**: the excerpt now holds only the chosen sections' text, and on the real protocol the
   `phase_population` task whose window is pages 35–41 contains no text from section 1.3.2 on page 39.
   The second cause stands: the `phase_population` search hints name the headings "Background" and
   "Rationale". Offline only — this has not yet been shown on a live Bedrock run.
1a. ~~**PLAN depends on PDF bookmarks.** A PDF with no bookmarks gets pages 1 to 5 for every field
   group.~~ **Fixed on 2026-10-02** by stages 1 and 2 of `docs/PLAN_2026-10-02.md`. FIND_SECTIONS gives
   `samples/Synthetic_RFP_NEOD001.pdf` one whole-document section, and PLAN now sends sections rather than
   the first five pages, so all nine of that document's extraction tasks include page 6 — the services
   requested, which no run before 2026-10-02 ever read. Shown offline on the real PDF by
   `tests/plan/test_plan_sections_samples.py`; not yet shown on a live Bedrock run.
2. **`timeline.total_duration` splits by enrolment timing.** Same run: "approximately 3.5-4 years"
   for early enrollers and "1.5-2 years" for late ones, both confirmed, with the study-level 42
   months absent. Correct per-subject, wrong as the study duration a budget needs.
3. **`audit.json` (§6.4) and the janitor job (§6.5) are not built.** Without the janitor, a run whose
   job process dies leaves `status.json` saying "running" forever.
4. **`python -m rfp_intake.eval` does not exist.** Only the library functions in `eval/` are built.
5. **The confidence percentage is not calibrated and the report now says so.** It is the extraction
   model's own rating of how clearly a document stated the value, plus `CORROBORATION_BOOST_PER_SOURCE`
   (0.05) for each extra passage that agreed, capped at 1.0. Nothing checks it against hand-marked
   answers, so it is not a probability of being correct, and GATE only compares it to
   `CONFIDENCE_CONFIRMED` (0.80). Angus Gray asked how the number is produced; report.pdf answers in
   those terms on page 1. Calibrating it needs the golden set and the `eval/` work in §9.

### Deploying into a fresh Cloudera AI project
`.project-metadata.yaml` makes this project an AMP (Applied ML Prototype), so a customer deploys it
from the GitHub URL rather than following instructions by hand. It installs the dependencies
(`scripts/amp_install_dependencies.py`), creates the CML Job named "RFP Pipeline Executor", starts
the Cloudera AI Application, and prompts for four environment variables. Added 2026-08-28; not yet
run against a fresh project.

Two things it depends on, both of which will break quietly if changed:
- **The job name in `.project-metadata.yaml` must equal `Settings.job_name`.** `app.py` finds the
  job by name. `tests/config/test_project_metadata.py` fails if they drift apart.
- **Nothing may hardcode `/home/cdsw/rfp-intake-agent`.** An AMP clones the repository into
  `/home/cdsw` itself, so `run_job.py`, `app.py` and `launch_app.py` now find the project root
  instead (`src/rfp_intake/config/paths.py`, and a deliberate cut-down copy inside `run_job.py`
  because that file runs before the package is importable).

**Never pin `version:` in the `runtimes:` block.** The first deployment attempt, into project
`test-rfp-deploy` on 2026-08-28, failed at task 1 with no log and no session, because
`version: "2026.04"` matched nothing — the workspace's runtime catalog lists only `2026.04.1-b7`.
A runtime block that matches nothing stops the AMP before there is a session to write a log to,
which is the worst way for this to fail. `version` is optional; leaving it out lets the workspace
supply it, which is what Cloudera's own published AMPs do. The block now names only
PBJ Workbench / Python 3.11 / Standard.

The same attempt also carried a `long_summary` on the `run_session` task, which the AMP
specification does not define for that task type. `tests/config/test_project_metadata.py` now
checks every task's field names against the specification, so neither mistake can come back.

### The list of things to do
1. **Bring in more test documents.** Expected to expose gaps in EXTRACT that the current
   two-document pair does not. Asked for 2026-08-27.
2. **Test with Nemotron on CAII once that endpoint is reachable again**, so the model used in
   production is the model that was tested. Blocked on CAII access; the token is short-lived and the
   endpoint is served without the two tool-calling flags named above.
3. **Improve the front end.** A screenshot, `8-27-app-screenshot.jpg`, was mentioned as the starting
   point. Asked for 2026-08-27. The Results section with downloads is done. Stage cards are done in
   `app_v2.py` but not yet live — see the 2026-10-01 entry below. Still unspecified: a past-run
   browser, the 101 extracted fields shown as a table, and the contradictions shown individually.
4. **Fix the two extraction problems above** — the phase of a referenced study, and the study
   duration splitting per subject. The first is being fixed by `docs/PLAN_2026-10-02.md`; do that
   plan's five stages in order before anything else on this list.
5. **Return `privacy_mode` to `private` and the models to CAII before any customer document.**
   `config/models.yaml` is on `mixed` with Claude Sonnet 4.6 on Bedrock for testing.
6. Build `audit.json`, the janitor job, and the `rfp_intake.eval` command line.

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
1.3.2's words. `select_sections` takes the top `DEFAULT_TOP_K` (3) scoring above zero and returns them in
document order; a zero-scoring section is never chosen to fill k. `select_windows` and `merge_windows` are
deleted along with the one-page margin they applied, and so is the "first 5 pages" fallback. When nothing
scores, PLAN sends every section if the whole document fits one call and otherwise the first three, logged
as `plan_no_section_scored`.

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

**PLAN writes sections back onto a `Document` that has none.** `_sections_for` calls the same
`find_sections` and assigns the result to `doc.sections` and `doc.section_source`. Computing them and
keeping them local handed EXTRACT tasks naming sections the document did not carry, and every excerpt came
back empty — which is how `tests/graph/test_pipeline_integration.py` failed while the PLAN and EXTRACT
tests all passed.

On the two real sample PDFs, offline: 26 tasks across 9 groups (protocol 17, synthetic RFP 9), down from
the 37 the old whole-page planner produced; largest task 3,932 estimated tokens, under the 4,000 budget;
all 9 synthetic-RFP tasks include page 6; no protocol task contains a 300-character probe from a section
PLAN did not choose; and `phase_population` chooses PROTOCOL SYNOPSIS (split into pages 11–17 and 18–26)
plus sections 1.2 and 1.4 — never 1.3.2.

Unchanged, deliberately: NORMALIZE, RECONCILE, ADJUDICATE, DERIVE, GATE, every parser, `extraction.json`,
`report.pdf`, `report.xlsx`, and `privacy_mode`.

**635 tests passing, 1 skipped** — 15 more than the 620 after stage 1. `tests/plan/test_scoring.py` was
rewritten against the new signature (the margin test is now `test_no_page_margin_is_applied`),
`tests/plan/test_plan.py` had `test_fallback_without_outline` replaced by
`test_a_document_without_an_outline_is_read_whole` and gained `test_tasks_fit_the_token_budget`,
`tests/extract/test_prompt.py` gained a `TestExcerptFromSections` class of 7 tests, and
`tests/plan/test_plan_sections_samples.py` is new — 10 tests on the two real PDFs, marked `slow`, no model
call. No existing test was deleted. **No live model run was made for this stage**, so everything above is
offline evidence; the next live Bedrock run on the synthetic pair is what would show the effect on
`study.phase` itself.

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

## Non-negotiable rules

1. **The LLM never decides control flow.** All graph edges are static Python or `Send`. No agent
   delegation, no tool-choosing agents, no LLM-routed conditional edges. If you find yourself
   writing "let the model decide which node runs next", stop — that is the bug we are rebuilding to fix.

2. **No value without evidence.** Every `FieldRecord` carries a verbatim `quote`, `doc_id`, and `page`.
   `quote` is validated as a substring of the source excerpt in code, after every extraction call.
   A record that fails validation is dropped and logged — never repaired by hand-waving.

3. **`not_found` ≠ `not_specified` ≠ `0`.** Keep the three terminal states distinct everywhere:
   schema, normalizer, report, and eval metrics.

4. **Never hardcode a field.** Fields come from `config/fields.yaml`. Prompts, schemas, validation,
   and report columns are all generated from the registry. If a change requires touching Python to
   add a variable, the design has drifted.

5. **Vendor SDKs live only in `llm/`.** The platform is Cloudera end to end: Cloudera AI Inference
   (CAII) is the inference layer for POC, demo and production; a LiteLLM proxy is used in local dev.
   Both are OpenAI-compatible, so the backend is a base URL in config.

   **AWS Bedrock is permitted for testing on non-sensitive data only — never in production.**
   Production runs on CAII. Bedrock exists so development and demos are not blocked on endpoint
   capacity, and it may only ever see synthetic or otherwise non-sensitive documents. This is not
   left to discipline: `config/models.yaml`'s `privacy_mode` enforces it in code
   (`domain/model_routing.py`), and `private` — the default and the production posture — refuses to
   construct any off-box provider at all. Do not introduce Textract or other off-box managed
   services for parsing; the boundary rule in #9 still governs document content.

6. **The test suite runs offline.** `LLM_BACKEND=mock` by default in tests. Deterministic fixtures.
   No network in CI.

8. **Normalizers are pure functions with table-driven tests.** `graph/normalize` and `domain/units`
   contain zero LLM calls and zero I/O.

9. **Contradiction detection is code first.** Candidate detection is set logic over normalized values.
   The LLM only adjudicates a specific pair you already found. Never prompt "find contradictions".

10. **Nothing leaves the customer boundary without an explicit, recorded decision.** All parsing is
   in-process (PyMuPDF/pdfplumber, Docling, local OCR). Any component that would transmit document
   content off-box sits behind a default-off switch — `parser.allow_external` for parsing,
   `privacy_mode` for inference — and its use is recorded in the run's `audit.json`. An empty
   `external_services` array is the evidence that nothing left. Sensitive customer documents are
   processed in `private` mode, always.

11. **The app never runs the graph.** The Cloudera AI Application triggers a CML Job and polls the run
    directory. One scheduled janitor job reaps stale runs — never one watcher per run.

## Scale reality check
6 concurrent users, 3–4 documents each. The CML Jobs API is the queue.
**Do not build a queue, a worker pool, a vector database, or Kubernetes manifests.**
If you think you need one, you have misread the requirements.

The throughput ceiling is our own CAII endpoint capacity, not a vendor rate limit — an over-eager
fan-out queues against ourselves and degrades latency for every other concurrent user.

## Commands
**Use the container's Python. Do not create a virtual environment.** The CML Job named
"RFP Pipeline Executor" and the Cloudera AI Application both run the container's Python, so a
virtual environment tests a different interpreter than the one that runs in production — and it
cost 5.4 GB, which was 99% of this project's disk use. Everything the project needs is already
installed in the container. Just `python`, never `.venv/bin/python`.

```bash
pip install -r requirements.txt   # only if a dependency is genuinely missing
pip install -e .                  # install package in editable mode
pytest                            # offline test suite (mock LLM)
pytest -m integration             # requires live LLM endpoint
ruff check . && mypy .            # lint + types
python -m rfp_intake.job <run_id> # run pipeline for a single RFP package
python -m rfp_intake.eval         # golden-set scoring
```

## Running the pipeline
```bash
mkdir -p runs/<run_id>/inputs
cp <your-pdfs> runs/<run_id>/inputs/
python -m rfp_intake.job <run_id>
# Outputs: runs/<run_id>/status.json, runs/<run_id>/extraction.json
```

## Style
- Python 3.11+, ruff, mypy strict on `domain/` and `graph/`.
- Pydantic v2 everywhere data crosses a boundary.
- Structured logging keyed by `run_id` / `task_id`. No print statements.
- Type-annotate every function. `Any` requires a comment justifying it.

## How to respond in chat
These rules govern chat replies, not the prose inside documents you produce.
Source: `/home/cdsw/how_to_resond.txt`, adapted for this project.

1. **Answer first.** The first sentence is the answer — the yes, the no, the number,
   the recommendation. Reasoning comes after. No preamble, no restating the question.
2. **Answer only what was asked.** No unrequested analysis, next steps, or risk
   assessments. If something else is worth raising, finish answering, then ask in one
   sentence whether it is wanted.
3. **Plain words.** Assume it is being read quickly between meetings. A longer sentence
   understood on the first read beats a short one that needs decoding.
4. **Never invent a label for something with an ordinary name.** Say "the place in the
   code where the parser can be swapped out", not "the parser seam". Say "the list of
   parsing methods from cheapest to most expensive", not "the fidelity ladder". The
   documents in `docs/` keep their existing names; chat does not.
5. **Define every technical term and acronym on first use in a conversation**, in the
   same sentence — every conversation, no carry-over assumed. This includes: tool
   calling, structured output, token ceiling, egress, privacy mode, service control
   policy, reducer, fan-out.
6. **Never use a pronoun for a system component. Name it.** Not "its environment
   variables are empty" but "the CML Job named 'RFP Pipeline Executor' has no
   environment variables set". Not "it failed" but "the extract step failed". This
   is the single most common way these replies become unreadable: `it`, `its`,
   `this`, `that`, and `the above` all have several possible referents in a system
   with an application, a job, a container, a graph, a node and a model in it.
   Repeating the full name is never too long.
   **The same applies to any vague reference, not only pronouns for components.**
   "Both", "these", "those", "the two", "the latter" and "the earlier one" must name what they
   refer to in the same sentence. Not "both pass" but "the find-sections test and the PLAN test
   both pass". A reader who has to scroll up to find the referent has been failed.
7. **Disambiguate overloaded words every time.** "The field schema" (`config/fields.yaml`)
   or "the graph state schema" (`RunState`), never just "schema". "The Cloudera AI
   Application" or "the CML Job", never just "the app". **Parse** means reading text off
   a page; **extract** means pulling a field value out of that text. Say which.
8. **Every sentence carries information.** Cut hedges. State uncertainty concretely:
   "I am not sure because I only ran this on the synthetic RFP, not the protocol."
9. **Name the source every time.** Any claim about a file gets a name and a location:
   "`config/models.yaml` line 60 pins the strategy". Never "the config says". If the
   claim comes from a test run, name the run id. If you cannot locate it, say so.
10. **Quote before you disagree.** Quote the actual line before arguing with it.
11. **One ask per response.** Close with exactly one question or one proposed next
    action. Not a menu.
12. **Concise means fewer points, not compressed points.** Cut whole sections; never cut
    the words that make a sentence understandable.
13. **Ask before writing anything long**, or before producing a document.
14. **No tables, headers, or bullets in short answers.** Use them only for comparing
    several things at once.
15. **Back-references stand alone.** Restate the earlier decision in full rather than
    pointing at it.
16. **Deferred items are recorded completely** — what it is, why deferred, when it returns.
17. **Name what you read** before producing analysis from project files.
18. **Complete, or say what is missing.** If a breakdown has five items, all five appear.
19. **When told "unclear" or "too dense", rewrite with the missing pieces filled in.**
    Do not defend the original or apologise at length.

## When you are unsure
Ask rather than assume, especially about clinical domain semantics. Wrong assumptions about
whether "40 sites" means per-country or total propagate silently into a budget. The domain
expert is Angus Gray (IQVIA DSB); route open questions to Oliver for him.
