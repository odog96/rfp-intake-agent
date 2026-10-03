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

## Current status (as of 2026-10-02)

Phases 0–4 of ARCHITECTURE.md §10 are done, and stages 1, 2, 3, 4 and 5 of `docs/PLAN_2026-10-02.md`.
**Stage 5's two live criteria failed on 2026-10-02 and still fail after the 2026-10-03 fix**, though for
a different and now measured reason: the ADJUDICATE half of the problem is fixed and proven on a live
run, and what remains is PLAN keeping only three sections per field group. Read the 2026-10-03 entry
in `docs/JOURNAL.md` before trusting `blinding.placebo_matching` or `blinding.unblinded_staff_stated`.
Graph topology today:
`INGEST → CLASSIFY → FIND_SECTIONS → SET_ASIDE_SECTIONS → MARK_OTHER_STUDY → PLAN → EXTRACT → NORMALIZE → RECONCILE → ADJUDICATE → DERIVE → GATE`,
then RENDER runs after the graph finishes (see the deviations below). 870 tests passing, 1 skipped.
Pushed to https://github.com/odog96/rfp-intake-agent.git. The push is done from a terminal by
Oliver, from inside the repository directory — this session's credentials cannot do it.

Every `### Done` entry that used to be in this file is now in `docs/JOURNAL.md`, newest first, unedited.
Nothing in `docs/JOURNAL.md` is current state; where the two disagree, this file wins.

**The whole path works end to end through the Cloudera AI Application.** A user loads documents,
the application creates a run of the CML Job named "RFP Pipeline Executor", the job runs the
pipeline and writes `extraction.json`, `report.pdf` and `report.xlsx`, and the application polls
and reports the outcome. Verified 2026-08-27 with job run `pde6ypjc38gypgy2`.

### Current model
Claude Sonnet 4.6 on AWS Bedrock (`us.anthropic.claude-sonnet-4-6`), `privacy_mode: mixed`.
**Testing only** — Bedrock is outside the customer boundary, so synthetic and publicly-registered
documents only, per non-negotiable rule 5 below. Production is CAII and `privacy_mode: private`.

On the two-document test pair, the current baseline run `r-20261002-222824-stage4b` (2026-10-02) gave
**27 of the 36 registry fields at least one confirmed row**, in 9m03s, with 9 contradiction clusters and
22 EXTRACT errors, and caught the planted `timeline.total_duration` disagreement. Quote that first figure
when comparing runs. The stage 5 run `r-20261002-232830-stage5` gave **25 of those same 36**, below the
floor of 26, with 13 clusters and 19 errors; it is not the baseline because it failed its own criteria.
The stage 5b run `r-20261003-001602-stage5b` (2026-10-03, the ADJUDICATE fix) gave **26 of those same
36**, with 92 rows, 11 clusters and 21 errors; it is not the baseline either, for the same reason — see
the 2026-10-03 entry. Two earlier runs matter for comparison: `r-20261002-213531-stage4`, the first run
with MARK_OTHER_STUDY, gave 27 of 36 in 9m10s with 8 clusters and 27 EXTRACT errors but removed three
sentences about this study; and `r-20261002-200521-stage2` gave 26 of 36 in 5m56s with 12 clusters and
5 EXTRACT errors. The stage 2 entry in `docs/JOURNAL.md` has the stage 2 table, and the stage 4 entries
there explain why the error count moved and why it is still not attributed to a stage.

Always say which unit a count is in: `config/fields.yaml` defines 36 fields, and a field can hold many
scoped rows, so a field count and a row count are not comparable.

Claude Sonnet 4.6 is the only model tried that extracts the budget drivers reliably. Claude models
needed the Anthropic use-case
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
1. ~~**`study.phase` reads the phase of a different study.**~~ **Fixed on 2026-10-02 by stage 4
   (MARK_OTHER_STUDY), shown on live run `r-20261002-213531-stage4`**: `study.phase` is one row,
   `phase_3`, `confirmed`, confidence 1.0, asserted by both documents, and the `phase_1_2` row is gone.
   Section 1.3.2 "Clinical Experience" was removed as a whole section and is listed in Appendix C of
   that run's `report.pdf`. The history below is kept because it is the only record of how long this
   took to find and of the two separate causes.
   In run `r-listfix-175318` it confirmed
   `phase_1_2` with the scope "Study NEOD001-001 (referenced study)" — the phase of an earlier study
   the protocol mentions. The correct `phase_3` is present but marked `needs_review`. Nothing in the
   pipeline knows that a value about another study should be discarded. Repeated in run
   `r-20261001-032936`, from section 1.3.2 "Clinical Experience" (PDF page 39). The fix is stage 4 of
   `docs/PLAN_2026-10-02.md` (a new MARK_OTHER_STUDY node), not a prompt change in EXTRACT.
   Two causes found on 2026-10-01 by reading the code. The first — PLAN sends whole pages plus one page
   of margin, so text from sections it did not choose reaches the model — **was fixed by stage 2 on
   2026-10-02**: the excerpt now holds only the chosen sections' text, and on the real protocol the
   `phase_population` task whose window is pages 35–41 contains no text from section 1.3.2 on page 39.
   The second cause — the `phase_population` search hints name the headings "Background" and
   "Rationale" — was never removed, and stage 4 fixed the field anyway by taking the text out of the
   background rather than by steering PLAN away from it. Removing those two hints is step 1 of stage 5
   of `docs/PLAN_2026-10-02.md`, which the plan deliberately held back until stage 4 had passed on its
   own. It has now passed on its own.
2. ~~**PLAN depends on PDF bookmarks.** A PDF with no bookmarks gets pages 1 to 5 for every field
   group.~~ **Fixed on 2026-10-02** by stages 1 and 2 of `docs/PLAN_2026-10-02.md`. FIND_SECTIONS gives
   `samples/Synthetic_RFP_NEOD001.pdf` one whole-document section, and PLAN now sends sections rather than
   the first five pages, so all nine of that document's extraction tasks include page 6 — the services
   requested, which no run before 2026-10-02 ever read. Shown offline on the real PDF by
   `tests/plan/test_plan_sections_samples.py`; not yet shown on a live Bedrock run.
3. **`timeline.total_duration` splits by enrolment timing.** Same run: "approximately 3.5-4 years"
   for early enrollers and "1.5-2 years" for late ones, both confirmed, with the study-level 42
   months absent. Correct per-subject, wrong as the study duration a budget needs. **Half better as of
   run `r-20261002-213531-stage4`**: the study-level 42 months is now extracted and is in the
   high-severity `conflict` against the RFP's 40, which is the disagreement the test pair plants. The
   per-subject split is still there, as a second cluster on the same field with verdict
   `not_a_conflict`. Nothing was done to fix this; it is a side effect of stages 1 to 4 reading better
   text, and the per-subject rows are still what a reader sees alongside the study figure.
4. **`audit.json` (§6.4) and the janitor job (§6.5) are not built.** Without the janitor, a run whose
   job process dies leaves `status.json` saying "running" forever.
5. **`python -m rfp_intake.eval` does not exist.** Only the library functions in `eval/` are built.
6. **The confidence percentage is not calibrated and the report now says so.** It is the extraction
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
   point. Asked for 2026-08-27. The Results section with downloads is done, and the stage cards in
   `app_v2.py` are live — `launch_app.py` serves `app_v2.py`, decided by Oliver on 2026-10-02. Still
   unspecified: a past-run browser, the extracted variables shown as a table, and the contradictions
   shown individually. `app_v2.py` still has no coverage in the test suite and has never been seen in a
   browser.
4. ~~**Decide what to do about ADJUDICATE picking a winner by document instead of by record.**~~
   **Done 2026-10-03, with Oliver's approval overriding `docs/PLAN_2026-10-02.md` section 5. See the
   2026-10-03 entry in `docs/JOURNAL.md`.** ADJUDICATE now names a record by its number in its own
   prompt. Proven on
   live run `r-20261003-001602-stage5b`, where `visits.schedule_present` resolves to `yes_appendix` from
   record 3 of 5.
5. ~~**Add "Placebo" to the `blinding_monitoring` search hints in `config/fields.yaml`.**~~ **Done
   2026-10-03, and it is not safe after all.** With the then-current `DEFAULT_TOP_K` at 3 (the constant
   is gone; see item 6) a hint added to a group evicts a section that group was
   already reading. Measured on the real protocol: the placebo sentence came in and the "Unblinded
   Pharmacist" sentence went out. This is now item 6.
6. ~~**Raise the number of sections PLAN keeps for the `blinding_monitoring` group, or make the heading
   match stricter.**~~ **Done 2026-10-03 by the first option, as a per-group `top_k` in
   `config/fields.yaml` — Oliver chose both the option and the mechanism. See the second 2026-10-03 entry
   in `docs/JOURNAL.md`.** `blinding_monitoring` is now `top_k: 7`; every other group keeps the default 3.
   The measurement
   that follows is kept because it is the evidence for the value, and because the near-exact heading match
   named below is still an open option if this group's hints change again.
   The state it describes: `DEFAULT_TOP_K` was 3 for every group, and this protocol spreads
   the blinding evidence over at least five sections — the title page, `5 SUBJECT SCREENING AND
   RANDOMIZATION` (IXRS randomisation), `6.2 Shipping, Storage, and Handling` (unblinded pharmacy
   staff), `6.3 Placebo` and `6.5.1 Study Drug` (the Unblinded Pharmacist sentence). Three is not enough
   and no hint change can make it enough. Measured offline on 2026-10-03 by running INGEST,
   FIND_SECTIONS, SET_ASIDE_SECTIONS and PLAN on `samples/Example protocol 2.pdf` twice, with and
   without the new hints:

   | blinding_monitoring sections chosen | before the hint change | after it |
   | --- | --- | --- |
   | title page p1–3 | no | **yes** |
   | PROTOCOL SYNOPSIS p11–26 (two tasks) | yes | yes |
   | `5 SUBJECT SCREENING AND RANDOMIZATION` p52–53 | **yes** | no |
   | `6.5.1 Study Drug` p54–55 | **yes** | no |
   | `6.3 Placebo` p53 | no | **yes** |

   "A matching placebo will not be provided" is reachable only after the change; "Unblinded Pharmacist or
   their designee" only before it; "strictly limited to the unblinded pharmacy staff" (section 6.2) in
   neither. The second smallest fix is to require a near-exact heading match in `plan/scoring.py:46-52`,
   which would stop the title page — "A PHASE 3, RANDOMIZED, DOUBLE-BLIND, PLACEBO-CONTROLLED…" — from
   scoring 5.22 on a partial match against the one-word heading hint "Placebo". **Whichever is chosen,
   add a test that section 5 stays reachable for this group**; nothing asserted that, which is why the
   eviction was invisible until it was measured. That test now exists:
   `test_the_unblinded_staff_sentences_reach_the_blinding_group`, three parametrised cases.
   One line of the table above was wrong and is corrected in `docs/JOURNAL.md`: "strictly limited to the
   unblinded pharmacy staff" is in section 6.2 on page 53 and was reachable in neither configuration
   **because the protocol capitalises it** — "Access to the study drug should be strictly limited to the
   Unblinded Pharmacy Staff." The offline probe that produced the table matched case-sensitively against a
   needle copied from a lower-case hint.
7. **Find out why EXTRACT dropped 22 records in run `r-20261002-222824-stage4b` where the baseline
   before MARK_OTHER_STUDY dropped 5.** 21 of the 22 are `quote_not_found_in_excerpt`, 8 of them
   `visits.frequency_by_period` on the protocol and 5 `ops.monitoring_visits` on the RFP.
   **MARK_OTHER_STUDY is now largely ruled out as the cause.** Run `r-20261002-222824-stage4b` makes one
   removal of 1,084 characters, against the previous run's eight removals of 6,458 characters, and the
   count only fell from 27 to 22. The gap of 17 against the stage 2 baseline survives a run that removes
   almost nothing, and 5 of the 22 are on the synthetic RFP, where nothing is removed at all. So check
   SET_ASIDE_SECTIONS next, not MARK_OTHER_STUDY: `r-20261002-213531-stage4` was the first live run to
   include it, PLAN made 32 tasks against the stage 2 baseline's 26, and a section set aside between
   PLAN's scoring and EXTRACT's excerpt would produce exactly this error.
8. **Return `privacy_mode` to `private` and the models to CAII before any customer document.**
   `config/models.yaml` is on `mixed` with Claude Sonnet 4.6 on Bedrock for testing.
9. Build `audit.json`, the janitor job, and the `rfp_intake.eval` command line.

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

7. **Normalizers are pure functions with table-driven tests.** `graph/normalize` and `domain/units`
   contain zero LLM calls and zero I/O.

8. **Contradiction detection is code first.** Candidate detection is set logic over normalized values.
   The LLM only adjudicates a specific pair you already found. Never prompt "find contradictions".

9. **Nothing leaves the customer boundary without an explicit, recorded decision.** All parsing is
   in-process (PyMuPDF/pdfplumber, Docling, local OCR). Any component that would transmit document
   content off-box sits behind a default-off switch — `parser.allow_external` for parsing,
   `privacy_mode` for inference — and its use is recorded in the run's `audit.json`. An empty
   `external_services` array is the evidence that nothing left. Sensitive customer documents are
   processed in `private` mode, always.

10. **The app never runs the graph.** The Cloudera AI Application triggers a CML Job and polls the run
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

## When you are unsure
Ask rather than assume, especially about clinical domain semantics. Wrong assumptions about
whether "40 sites" means per-country or total propagate silently into a budget. The domain
expert is Angus Gray (IQVIA DSB); route open questions to Oliver for him.
