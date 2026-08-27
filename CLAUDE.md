# CLAUDE.md — RFP Intake Agent

## What this is
A deterministic LangGraph pipeline that reads clinical study RFP and protocol documents and
produces a provenance-backed variable set for the Delivery Strategy & Budgeting (DSB) team.

**Read `docs/ARCHITECTURE.md` before writing code. It is the build contract, not background reading.**
`config/fields.yaml` is the single source of truth for what gets extracted.

## Current status (as of 2026-08-26)

Phases 0–4 of ARCHITECTURE.md §10 are done. Graph topology today:
`INGEST → CLASSIFY → PLAN → EXTRACT → NORMALIZE → RECONCILE → ADJUDICATE → DERIVE → GATE`,
then RENDER runs at the job level (see below, not a graph node). 343 tests passing, 1 skipped.
This repo had no git history before 2026-08-25 — `git init` and the first 6 commits (`202fec4`
through `6e9b479`) all happened last session; nothing has been pushed anywhere.

### Deviations from the ARCHITECTURE.md text — read before assuming the doc is exact
- **RENDER is not a LangGraph node.** `RunState` carries no filesystem path, only `run_id`, and
  `job/output.py` already wrote files outside the graph before RENDER existed. RENDER lives as pure
  functions in `render/` (json/pdf/xlsx renderers) plus a thin I/O wrapper in `job/output.py`, called
  from `job/__init__.py` after `compiled.stream()` finishes — not `graph.add_node("render", ...)`.
- **Schema additions beyond §3**: `ResolvedField.scope` (a scoped field can have multiple
  non-conflicting resolved entries) and `ResolvedField.notes` (DERIVE's and ADJUDICATE's explanations
  need somewhere to live for the report). `Contradiction.verdict/explanation/severity` are now
  `Optional` — `None` means "RECONCILE found it, ADJUDICATE hasn't judged it yet," not "not a conflict."
- **GATE is stricter than the literal §4.9 table**: a `budget_driver` field forces `needs_review` even
  on a `not_a_conflict` verdict, not just `conflict`/`reconcilable` — see `gate/__init__.py`'s docstring
  for why (a misjudged "these don't really disagree" on a site count is the costliest place ADJUDICATE's
  LLM call could be wrong).
- **ADJUDICATE never picks the winning value for a `conflict` verdict.** `config/precedence.yaml` states
  that tie-break is "applied by RECONCILE after ADJUDICATE returns a conflict verdict" — it's a
  deterministic function (`reconcile/precedence.py`: recency → domain_authority → specificity →
  no_silent_resolution), not a model's black box.
- **DERIVE's visit-intensity rubric weights are a best-effort remap** of §4.8's table onto
  `fields.yaml`'s current `visits.intensity_evidence` enum, which has evolved past the doc's original
  evidence names (`overnight_stay`, `complex_procedures` are new; "visit window < 3 days" became
  `long_visit_windows`). See the comment in `derive/rubric.py` before recalibrating.

### Model routing and privacy mode (added 2026-08-26)
`config/models.yaml` binds each LLM role (classify/extract/adjudicate) to a provider and model, and
carries `privacy_mode`. Loader and invariant: `domain/model_routing.py`. Construction:
`llm/provider.py`. Discovery + audit summary: `llm/discovery.py`.

- **`private` (default, production)** allows only `egress: none` providers — CAII and mock. **`mixed`**
  allows external providers per-role, but only where the role also sets `allow_external: true`.
  **`open`** allows anything. Enforced in code and re-checked at provider construction, so an
  in-memory routing that skipped the loader still can't reach an external service in private mode.
- `litellm` is classed `egress: unverifiable` and therefore treated as external — a dev proxy routes
  wherever the developer's credentials point, which cannot be checked from inside the process.
- **Every LLM role here receives verbatim document excerpts.** There is no metadata-only role, so
  `mixed` narrows *which* documents leak, never *whether* they do. It is for synthetic/eval corpora.
- `RFP_INTAKE_LLM_BACKEND=mock` still short-circuits routing entirely — the offline test escape hatch.
- Bedrock sits behind the optional `aws` extra (`langchain-aws`), so private-mode deployments never
  install an off-box vendor SDK. `scripts/smoke_caii.py` validates any OpenAI-compatible endpoint
  across four rungs before it is wired to the graph.
- **Live endpoint as of 2026-08-26: CAII Nemotron 3 Super 120B** (`nvidia/nemotron-3-super-120b-a12b`),
  `privacy_mode: private`, `external_services: []`. The endpoint URL lives in `models.yaml` under
  `providers.caii.base_url`; the JWT comes from `RFP_INTAKE_CAII_API_KEY` (or `..._API_KEY_FILE`) and
  never from the config file. It needs `strategy: native` — see the verified notes in `models.yaml`
  for why guided decoding is unusable there, and note the uneven latency (~4s to ~248s per group).
- **Still scaffold, not product:** the admin UI is read-only (sidebar panel in `app.py`); editing
  bindings needs a write-back path and an authorisation check on who counts as an admin. Bedrock
  discovery is unimplemented (`ListFoundationModels` is a different call shape). No model setup job.

### What's genuinely untested
Everything above has run against real PDFs (`Synthetic_RFP_NEOD001.pdf` + `Example protocol 2.pdf` —
**these two are a matched pair**, the only two documents the user has that go together; the other
sample PDFs are standalone) via `python -m rfp_intake.job`, using the **mock** LLM backend. That proves
the pipeline runs end-to-end on real files without crashing and produces a valid `report.pdf`/
`report.xlsx`. It does **not** prove extraction accuracy or contradiction-detection quality — mock
returns canned fixtures that don't match real document text, so that run came back 35/36 fields
`not_found`. That demo run's outputs are saved at `runs/r-demo-rfp-protocol-pair/` for reference.

**The pipeline has now run against a live LLM.** Run `r-bedrock-final-161930` (AWS Bedrock,
`us.meta.llama3-1-70b-instruct-v1:0`, synthetic + publicly-registered documents only) produced 108
records, 15 contradictions, 15 adjudicated, 0 errors, all three reports — and **caught the planted
`timeline.total_duration` contradiction** (verdict `conflict`, 42 months vs 40 months). Known bug in
that output: every record appears exactly twice (162 resolved fields from a 45-field registry).
NORMALIZE returns `{"records": ...}` while `RunState.records` carries an `operator.add` reducer, so
LangGraph appends instead of replacing. **Not fixed** — the clean fix changes `RunState` (§3 of the
build contract), which is the user's architectural call. It predates the LLM work and was invisible
under mock only because mock produced no records.

### Not built yet
- **REVIEW node** — deferred to Phase 2 per §11, not MVP scope. Don't build unless asked.
- **`audit.json`** (§6.4) and the **janitor job** (§6.5) — Phase 5 execution-model pieces, not started.
- **`python -m rfp_intake.eval`** is listed under Commands below as documented intent, but the CLI
  entrypoint doesn't exist yet — only the library functions (`eval/golden.py`, `eval/scoring.py`) are
  built and tested. `score_contradiction_set` (verdict-accuracy scoring against
  `eval/golden/contradictions.yaml`'s 3 planted cases) has only run against hand-built test fixtures,
  never a real pipeline output.
- `app.py` (Streamlit UI) had two status-desync bugs fixed last session (see commit `2eb401a`), but
  hasn't been re-verified by actually clicking through it — only the underlying job/status logic was
  tested.

### Likely next steps
1. Decide on the NORMALIZE duplication fix above, then re-run the paired-document test on CAII to see
   actual extraction quality against `eval/golden/contradictions.yaml` (`timeline.total_duration`
   expected verdict: `reconcilable`; the Bedrock run returned `conflict`).
2. Wire `score_document`/`score_contradiction_set` into an actual `python -m rfp_intake.eval` CLI.
3. `audit.json` + janitor job (Phase 5) if execution-model work resumes before eval work does.

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
```bash
pip install -r requirements.txt   # install dependencies
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
- Python 3.11+, `uv`, ruff, mypy strict on `domain/` and `graph/`.
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
