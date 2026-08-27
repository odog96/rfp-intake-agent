# Claude Code kickoff prompt

## Before you paste

```bash
mkdir rfp-intake-agent && cd rfp-intake-agent && git init
mkdir -p docs config samples
# copy in: docs/ARCHITECTURE.md, config/fields.yaml, config/precedence.yaml, CLAUDE.md
# drop the sample corpus (Example RFP 1.pdf, Example Protocol 1-3.pdf) into samples/
claude
```

Then paste everything below the line, in one go.

---

I'm building an RFP intake agent for clinical study budgeting, on Cloudera. This repo has a design
contract already written — read it first, then build to it.

**Read these before writing any code:**
- `docs/ARCHITECTURE.md` — the build contract. Graph topology, state schema, node contracts, the three
  seams, execution model, security posture, build order, deferred scope, non-goals. Follow it.
- `config/fields.yaml` — the field registry. 36 fields across 9 groups. This is the single source of
  truth for what gets extracted; prompts and schemas are generated from it, never hardcoded.
- `config/precedence.yaml` — conflict resolution policy.
- `CLAUDE.md` — non-negotiable rules for this codebase.

## Context in one paragraph

Delivery Strategy & Budgeting analysts at a CRO manually read clinical study RFPs and protocols
(3–4 documents per opportunity, 50–300 pages each) to pull out ~36 variables that drive a budget
model — site counts, subject counts, visit schedules, monitoring frequency, unblinded monitoring
requirements. It's slow and things get missed, especially contradictions between the RFP and the
protocol. A CrewAI prototype proved the concept but was non-deterministic, had no provenance, and took
five minutes per run. This rebuild fixes those three things. Determinism is the headline feature: same
documents in, same report out, every time.

## Platform — this is not negotiable, and it shapes the code

**Cloudera end to end.** Cloudera AI Inference (CAII) is the inference layer for POC, demos, and
production. It is the strategic point of the project, not one option among several — documents must not
leave the customer boundary. During local development, models are reached through a LiteLLM proxy. Both
are OpenAI-compatible, so `get_llm(role)` points at one base URL or the other by config and nothing
outside `llm/` changes.

**Do not introduce AWS Bedrock, Textract, or any other off-box managed service**, and do not import a
vendor SDK anywhere outside `llm/`. If a design question seems to want one, that is a signal you have
misread the constraint — ask me.

## The core architectural commitment

Control flow is Python. The LLM is used only to fill bounded, schema-constrained slots inside a node —
never to decide which node runs next. Extraction fans out to one small call per `(document × field
group)` via LangGraph's `Send`, each with a targeted page window and a narrow schema, then fans back in
through an `operator.add` reducer. Every extracted value carries a verbatim quote plus doc id and page,
validated in code as a substring of the source excerpt.

## What I want you to do now

Work through the phases in `docs/ARCHITECTURE.md` §10. **Stop at the end of each phase, show me what you
built and how you verified it, and wait for my go-ahead before starting the next one.** Don't run ahead —
I'd rather correct the shape early than review 4,000 lines at once.

### Phase 0 — foundation (start here)

1. Scaffold with the container's Python (3.11+) — no virtual environment, see CLAUDE.md. Package
   `rfp_intake`. Layout per ARCHITECTURE.md §10, plus `tests/`
   and `eval/`.
2. `domain/registry.py` — load and validate `fields.yaml` into typed Pydantic models. Validate on load:
   unique ids, group references resolve, enum fields declare `values`, `derived` fields declare
   `derived_from` and reference real field ids. Fail loudly at import time on a bad registry. Expose a
   `registry_version` (file version + content hash) — the audit record needs it.
3. `domain/schemas.py` — the Pydantic models from ARCHITECTURE.md §3: `Provenance`, `FieldRecord`,
   `Contradiction`, `ResolvedField`, `Document`, `ExtractionTask`, `RunState`. Include the `operator.add`
   reducers.
4. `domain/dynamic.py` — build a Pydantic extraction model **at runtime** for a given field group from the
   registry. This is the piece that keeps fields.yaml authoritative: adding a field to the YAML must change
   the extraction schema with no Python edit. Get this right before anything else; everything downstream
   depends on it.
5. **The three seams** (ARCHITECTURE.md §5) — define all three interfaces now, even where the
   implementation comes later:
   - `llm/provider.py` — `get_llm(role)` over three OpenAI-compatible backends: `caii` (the default and
     the deployment target), `litellm` (local dev), `mock` (deterministic fixtures). Roles
     `classify` / `extract` / `adjudicate` map to model names via settings.
   - `llm/structured.py` — **two** structured-output strategies behind one interface: native tool-calling,
     and JSON-schema-guided decoding for vLLM-backed endpoints. Detect endpoint capability once at
     startup. Do not assume tool-calling works; on open-weight models it often doesn't, and guided
     decoding is frequently the more reliable path. This is the highest-risk piece of the whole build.
   - `io/inputs.py` — `InputResolver` protocol with `resolve(source) -> list[Path]`. Implement
     `LocalInputResolver` for `file://`. Leave `ObjectStoreInputResolver` (`abfss://`, ADLS) as a stub
     that raises `NotImplementedError`. The point is that the graph never grows a hardcoded filesystem
     assumption.
6. `config/settings.py` — pydantic-settings, env-driven.
7. pytest, ruff, mypy (strict on `domain/` and `graph/`), and a CI workflow that runs offline with
   `LLM_BACKEND=mock`.

**Verification for Phase 0:** registry loads and validates and reports a `registry_version`; a dynamic
extraction model built from the `visits` group round-trips a sample JSON payload; `LocalInputResolver`
resolves a directory of PDFs; `pytest`, `ruff`, `mypy` all green; CI file present. Show me the generated
schema for one group so I can see the dynamic builder works.

### Then, on my go-ahead

- **Phase 1** — INGEST (parser interface, rungs 1–3, page-preserving contract, table extraction, quality
  gate) and CLASSIFY, plus the eval harness skeleton. All parsing is in-process: PyMuPDF/pdfplumber,
  **Docling** for layout and table structure, local OCR for scanned documents. At least one sample PDF has
  no text layer, so the OCR rung is required, not optional. Anything that would send document content
  off-box is rung 4, behind `parser.allow_external`, off by default — do not implement it.
- **Phase 2** — PLAN (outline-based page targeting) + EXTRACT (the fan-out) + NORMALIZE. Exercise both
  structured-output strategies against a real CAII endpoint.
- **Phase 3** — RECONCILE + ADJUDICATE + DERIVE + GATE.
- **Phase 4** — RENDER: `extraction.json` and `report.pdf` are the primary deliverables; XLSX is a
  renderer alongside them, keeping the Review Queue sheet as an XLSX feature.
- **Phase 5** — the execution model (ARCHITECTURE.md §6): Cloudera AI Application UI, CML Job entrypoint,
  run directory, `status.json`, `audit.json`, and the single janitor job.
- **Phase 6** — CAII validation and concurrency tuning.

## Things I care about disproportionately, so don't cut them

- **Quote validation.** Substring check against the excerpt after every extraction call, one repair retry
  naming the violation, then drop the record. Record-level rejection, not response-level — one bad field
  must not discard eight good ones. This matters more here than it would against a frontier hosted model,
  because it's the safety net under a less reliable schema-follower.
- **The `scope` field.** "40 sites total" and "12 sites in Germany" are not a contradiction. Without
  scope, every multi-cohort study generates false conflicts and the analysts stop trusting the tool.
  Extraction prompts must set it; the reconciler must group by `(field_id, scope)`.
- **`not_specified` as a first-class answer.** "Not stated in source documents" is a correct and useful
  output — it tells the analyst to go ask the sponsor.
- **Contradiction explanations that stand alone.** There is no in-app adjudication in v1 (deferred, see
  ARCHITECTURE.md §11), so the report is the only place a conflict gets resolved. Both values, both
  quotes, both page citations, the precedence rule, and a recommended resolution with reasoning. "Values
  disagree" is not acceptable output.
- **Eval from Phase 1, not at the end.** Score per field: precision, recall, `not_found` rate, and citation
  accuracy (does the cited page actually contain the quote?). Contradiction precision and recall scored
  separately. Score against the CAII endpoint, not only the dev proxy — they are different models and the
  numbers will differ.
- **Latency.** Target under 90 seconds for a 3-document package, excluding OCR. Instrument per-node timing
  from the start. Bound fan-out concurrency and back off on 429/503 — note that the ceiling is our own CAII
  endpoint capacity, so an over-eager fan-out degrades latency for every other concurrent user rather than
  hitting someone else's quota.

## Things I explicitly do not want

- No AWS Bedrock, Textract, or other off-box managed services.
- No autonomous agents or agent delegation of any kind.
- No vector database or persistent RAG index. Documents are per-run; targeted page selection beats
  retrieval here and stays citable.
- No queue, worker pool, or Kubernetes. Six concurrent users; the CML Jobs API is the queue.
- **No graph execution inside the web application process.** The app triggers a job and polls; it never
  runs the graph in-process.
- **No per-run watchdog processes.** One scheduled janitor job reaps stale runs, not one checker per run.
- No budget creation — this produces `extraction.json`, and a downstream service builds the budget.
- No in-app contradiction resolution in v1.
- No fine-tuning.

## Sample corpus

`samples/` contains five documents. They are NOT a uniform set — read this
before using them.

MATCHED PAIR — these two belong together and are the primary test case:
- Synthetic_RFP_NEOD001.pdf — a fabricated RFP, marked synthetic on every
  page. Written specifically to pair with Example_protocol_2.pdf.
- Example_protocol_2.pdf — real protocol. NEOD001-CL002, sponsor Prothena,
  Phase 3, AL amyloidosis, 260 subjects, 75 sites.

This pair has a known answer key, in eval/golden/. Three contradictions
were planted deliberately:
  1. Study duration — RFP says 40 months, protocol says about 42 months.
  2. Site qualification visits — RFP asks for 80 visits to qualify 75 sites.
     Internally inconsistent within the RFP alone.
  3. Enrolment period — RFP says roughly 24 months of enrolment inside a
     40-month study. Only fails when checked against the protocol timeline.

These fields agree across both documents and must NOT be flagged as
contradictions: indication, phase, sponsor, protocol number, subject count
(260), site count (75), randomisation design.

UNPAIRED DOCUMENTS — use these for parsing and extraction tests only. They
are unrelated to each other and to the pair above. Do not attempt
cross-document reconciliation across them.
- Example_RFP_1.pdf — real RFP, 14 pages. Scanned, no text layer. This is
  the only real RFP in the set and it requires OCR.
- Example_Protocol_1.pdf — real protocol, 97 pages. Scanned, no text layer.
- Example_protocol_3.pdf — real protocol, 130 pages. AstraZeneca AZD1222,
  Amendment 6. Has a text layer. Useful for testing version and amendment
  handling, and for multi-cohort scope handling.

## How to work with me

Ask before assuming, particularly on clinical domain semantics — whether a stated subject count means
screened, enrolled or randomised changes the budget, and guessing silently is the failure mode I'm most
worried about. Collect domain questions as you go and give them to me in a batch at each phase boundary;
I'll take them to our IQVIA domain expert.

Start with Phase 0.
