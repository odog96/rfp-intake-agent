# CLAUDE.md — RFP Intake Agent

## What this is
A deterministic LangGraph pipeline that reads clinical study RFP and protocol documents and
produces a provenance-backed variable set for the Delivery Strategy & Budgeting (DSB) team.

**Read `docs/ARCHITECTURE.md` before writing code. It is the build contract, not background reading.**
`config/fields.yaml` is the single source of truth for what gets extracted.

## Current status (as of 2026-08-27)

Phases 0–4 of ARCHITECTURE.md §10 are done. Graph topology today:
`INGEST → CLASSIFY → PLAN → EXTRACT → NORMALIZE → RECONCILE → ADJUDICATE → DERIVE → GATE`,
then RENDER runs after the graph finishes (see the deviations below). 467 tests passing, 1 skipped.
Nothing has been pushed to a remote yet; `origin` points at
https://github.com/odog96/rfp-intake-agent.git and the push needs credentials this environment
does not have.

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
   pipeline knows that a value about another study should be discarded; this needs a prompt change,
   or a rule that a scope naming a different study disqualifies the record.
2. **`timeline.total_duration` splits by enrolment timing.** Same run: "approximately 3.5-4 years"
   for early enrollers and "1.5-2 years" for late ones, both confirmed, with the study-level 42
   months absent. Correct per-subject, wrong as the study duration a budget needs.
3. **`audit.json` (§6.4) and the janitor job (§6.5) are not built.** Without the janitor, a run whose
   job process dies leaves `status.json` saying "running" forever.
4. **`python -m rfp_intake.eval` does not exist.** Only the library functions in `eval/` are built.

### The list of things to do
1. **Bring in more test documents.** Expected to expose gaps in EXTRACT that the current
   two-document pair does not. Asked for 2026-08-27.
2. **Test with Nemotron on CAII once that endpoint is reachable again**, so the model used in
   production is the model that was tested. Blocked on CAII access; the token is short-lived and the
   endpoint is served without the two tool-calling flags named above.
3. **Improve the front end.** A screenshot, `8-27-app-screenshot.jpg`, was mentioned as the starting
   point. Asked for 2026-08-27. The Results section with downloads is done; nothing else is specified.
4. **Fix the two extraction problems above** — the phase of a referenced study, and the study
   duration splitting per subject.
5. **Return `privacy_mode` to `private` and the models to CAII before any customer document.**
   `config/models.yaml` is on `mixed` with Claude Sonnet 4.6 on Bedrock for testing.
6. Build `audit.json`, the janitor job, and the `rfp_intake.eval` command line.

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
