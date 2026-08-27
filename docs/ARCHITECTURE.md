# RFP Intake Agent — Architectural Scaffolding

> **How to read this document.** It is the build contract, and it describes the target design —
> not every part is built. Sections marked *Not built yet* are design intent. Where the running
> code deviates deliberately from the design, the deviation is recorded inline in the relevant
> section rather than left for the reader to discover; those notes carry the date they were made.
> `CLAUDE.md` carries current build status. `config/fields.yaml` remains the single source of
> truth for what gets extracted.

**System:** AI-powered RFP + Protocol review for Clinical Delivery Strategy & Budgeting (DSB)
**Framework:** LangGraph (deterministic StateGraph) on Cloudera — Cloudera AI Inference (CAII) for inference,
Cloudera AI Application for the UI, CML Jobs for execution.
**Status:** Phase 2 design. Supersedes the Phase 1 CrewAI / Agent Studio prototype.
**Audience:** Engineers building this repo. This document is the build contract.

---

## 0. Why we are rebuilding

Phase 1 (Agent Studio / CrewAI) proved the concept: classify → extract → synthesize → detect contradictions,
producing a PDF report. It has three defects that block production:

| Defect | Phase 1 cause | Phase 2 fix |
|---|---|---|
| **Non-determinism** — same document, different report | LLM-driven agent delegation decides control flow | Control flow is Python. The LLM only fills bounded, schema-constrained slots. |
| **Fragile ingestion** — file-path and format failures | Framework-owned file tooling, single parser | Parser is an interface behind the graph, with a fidelity ladder and a page-preserving contract |
| **No auditability** — values with no provenance | Free-text agent output | Every value carries a verbatim quote, doc id and page. No provenance, no value. |

Plus a hard requirement that recurs in every design sync: **documents must not leave the customer boundary.**
This is not a constraint the architecture works around — it is the point of the project. The platform is
Cloudera end to end, and Cloudera AI Inference is the inference layer for POC, demo, and production alike.
The `get_llm` seam exists so that local development and deployed execution differ by a base URL, not by a
code path.

**The thesis:** an RFP intake agent is not an autonomous agent problem. It is a *deterministic extraction
pipeline with LLM-shaped leaves*. Every place we let the model decide *what to do next* is a place the
report changes between runs. Every place we let it decide *what this sentence says* is a place it adds value.
The architecture is the discipline of keeping those two separate.

---

## 1. The five load-bearing principles

### P1 — Evidence or nothing
No extracted value exists without a `quote`, a `doc_id`, and a `page`. A field the model cannot ground in
verbatim text is emitted as `not_found`, never inferred, never silently dropped. This is what makes the tool
trustworthy to a DSB reviewer who is accountable for the budget that comes out the other end.

### P2 — `not_specified` is an answer
RFPs are incomplete by nature. "Number of CRF pages: not stated in source documents" is a *correct and useful*
output — it tells the analyst to go ask the sponsor. Conflating "absent" with "zero" or with "unknown" is the
single most expensive failure mode in a budgeting tool. Three distinct terminal states per field:
`found` / `not_specified` (source explicitly says none/N/A) / `not_found` (searched, absent).

### P3 — Extraction is many small jobs, not one big one
One prompt asking for 35 variables across a 200-page protocol will hallucinate and will not repeat.
Instead: fan out to one bounded task per `(document × field group)`, each with a narrow schema, a targeted
page window, and a single job. Nine groups × N docs, run in parallel via LangGraph's `Send`. Small context,
small schema, small failure blast radius.

### P4 — The field registry is the product
Field definitions live in `config/fields.yaml`, not in code and not in prompts. Adding a variable is a YAML
edit reviewed by Angus, not a code change reviewed by an engineer. Angus explicitly said the current list is
"a decent starting point... there are definitely other details." Design for that sentence.

### P5 — Contradiction detection is deterministic first, LLM second
Finding *candidate* disagreements is set logic over normalized values — code, fast, exhaustive, repeatable.
Judging whether a candidate is a real conflict needs domain reasoning — that, and only that, is an LLM call.
Never ask a model to "look for contradictions"; ask it to adjudicate a specific pair you already found.

---

## 2. Graph topology

```
                        ┌──────────────┐
   files ──────────────▶│   INGEST     │  parse → page-preserving text + outline + tables
                        └──────┬───────┘
                               │  Document[]
                        ┌──────▼───────┐
                        │  CLASSIFY    │  RFP | Protocol | Amendment | SoA | Other  (+ version, date)
                        └──────┬───────┘
                               │
                        ┌──────▼───────┐
                        │    PLAN      │  build extraction tasks = doc × field_group,
                        └──────┬───────┘  select candidate page windows per task
                               │
                    Send(...)  │  fan-out, parallel
              ┌────────┬───────┼───────┬────────┐
              ▼        ▼       ▼       ▼        ▼
           ┌─────┐  ┌─────┐ ┌─────┐ ┌─────┐  ┌─────┐
           │EXTRACT (one bounded structured-output call each)│
           └──┬──┘  └──┬──┘ └──┬──┘ └──┬──┘  └──┬──┘
              └────────┴───────┼───────┴────────┘
                               │  FieldRecord[]   (reducer: append_or_replace, §3)
                        ┌──────▼───────┐
                        │  NORMALIZE   │  units, enums, dates, counts → canonical form  (pure Python)
                        └──────┬───────┘
                        ┌──────▼───────┐
                        │  RECONCILE   │  group records by field_id; apply precedence; mark disagreements
                        └──────┬───────┘
                        ┌──────▼───────┐
                        │  ADJUDICATE  │  LLM, only on candidate conflicts → conflict|reconcilable|not-a-conflict
                        └──────┬───────┘
                        ┌──────▼───────┐
                        │   DERIVE     │  computed fields (e.g. visit intensity) from extracted fields + rubric
                        └──────┬───────┘
                        ┌──────▼───────┐
                        │    GATE      │  confidence policy → confirmed | needs_review | not_found
                        └──────┬───────┘
                               │
                        ┌ ─ ─ ─▼─ ─ ─ ─┐
                          REVIEW          interrupt() — PHASE TWO, DEFERRED. Default off.
                        └ ─ ─ ─┬─ ─ ─ ─┘  Not in MVP. See §11.
                               │
                        └ ─ ─ ─┬─ ─ ─ ─┘
                          ═══════▼═══════   graph ends here; compiled.stream() returns
                        ┌──────────────┐
                        │   RENDER     │  canonical JSON + PDF report (primary) · XLSX (renderer)
                        └──────────────┘  NOT a graph node — pure functions called by
                                          job/__init__.py after the graph finishes. See §4.10.
```

**Every edge is a static Python edge or a `Send`.** There are no LLM-chosen routes and no agent delegation.
In MVP there is exactly one conditional edge: `ADJUDICATE` is skipped when the candidate set is empty.
That is the whole of the non-determinism budget. The `GATE → REVIEW` edge is designed for and left
unimplemented — see §11.

### Why the map-reduce shape matters
`PLAN` emits **at least** `N_docs × 9` tasks — a group whose candidate pages exceed the token budget is
split into several windows, so a 137-page protocol produces more than one task per group (2 documents
× 9 groups gave 37 tasks in practice, not 18). On a typical 3-document RFP package that is ~27+ small calls, each with
3–15k tokens of targeted context instead of one call with 300k. Parallel, so wall-clock is bounded by the
slowest single call, not the sum. This is the direct fix for the "5 minutes to run" complaint from the
Warsaw feedback — the Phase 1 runtime was sequential agent turns.

---

## 3. State

```python
# graph/state.py
from typing import Annotated, Literal
import operator
from pydantic import BaseModel, Field

class Provenance(BaseModel):
    doc_id: str
    doc_kind: Literal["rfp", "protocol", "amendment", "soa", "other"]
    doc_version: str | None = None      # amendment number / version label if detected
    doc_date: str | None = None         # ISO; drives recency precedence
    page: int                            # 1-indexed, as printed if detectable
    section: str | None = None           # nearest enclosing heading
    char_span: tuple[int, int] | None = None

class FieldRecord(BaseModel):
    """One assertion about one field, from one place in one document."""
    field_id: str
    group: str
    raw_value: str                       # exactly as the model read it
    value: object | None = None          # canonical, set by NORMALIZE
    unit: str | None = None
    quote: str                           # VERBATIM span from the source. Validated as substring.
    provenance: Provenance
    status: Literal["found", "not_specified", "not_found"] = "found"
    confidence: float                    # 0..1, model-reported, calibrated in GATE
    scope: str | None = None             # "total" | "cohort:A" | "country:DE" — prevents false conflicts
    notes: str | None = None

class Contradiction(BaseModel):
    """A candidate disagreement (RECONCILE) or an adjudicated one (ADJUDICATE)."""
    field_id: str
    records: list[FieldRecord]
    # None until ADJUDICATE judges it. RECONCILE produces the candidate; these
    # three are §4.7's output, so None means "not yet judged", NOT "no conflict".
    verdict: Literal["conflict", "reconcilable", "not_a_conflict"] | None = None
    explanation: str | None = None
    resolved_value: object | None = None
    winning_doc_id: str | None = None
    severity: Literal["high", "medium", "low"] | None = None   # high = changes the budget

class ResolvedField(BaseModel):
    field_id: str
    value: object | None
    status: Literal["confirmed", "needs_review", "not_found", "not_specified"]
    confidence: float
    sources: list[Provenance]
    quote: str | None
    scope: str | None = None              # carried from FieldRecord.scope, so a
                                          # scoped field can resolve to several
                                          # entries that do not conflict
    contradiction: Contradiction | None = None
    derived_from: list[str] = []          # non-empty ⇒ computed, not extracted
    notes: str | None = None              # DERIVE's and ADJUDICATE's explanation,
                                          # printed in the report

class Replace(list):
    """Marker: this update overwrites the collection instead of appending."""

def append_or_replace(current: list, update: list) -> list:
    return list(update) if isinstance(update, Replace) else current + update

class RunState(BaseModel):
    run_id: str
    documents: list[Document] = []
    tasks: list[ExtractionTask] = []
    records: Annotated[list[FieldRecord], append_or_replace] = []
    contradictions: list[Contradiction] = []
    resolved: list[ResolvedField] = []
    report_paths: dict[str, str] = {}
    errors: Annotated[list[RunError], operator.add] = []
```

**`records` is both fanned into and rewritten, so a plain append reducer is wrong.**
EXTRACT runs one branch per (document, group) and each returns only its own share —
those must accumulate. NORMALIZE rewrites every record it was handed and returns the
whole list; under a plain `operator.add` that list was appended to the one already in
state and **every value appeared twice** (a 45-field registry reported 64 resolved
fields). A node that rewrites returns `Replace(...)` and says so at the return site;
a fan-in branch returns a plain list. Fixed 2026-08-27.

**The `scope` field is not optional decoration.** "40 sites" (total) and "12 sites" (Germany) are not a
contradiction. Without scope, the reconciler generates false positives on every multi-cohort study — and
multi-part studies are exactly the ones DSB cares most about. Extraction prompts must set scope explicitly.

---

## 4. Node contracts

### 4.1 INGEST
**Input:** paths from the input resolver (§5.2). **Output:** `Document` with page-indexed text, outline, tables.

Parser is an interface with a fidelity ladder — pick the cheapest rung that clears a quality bar:

```python
class Parser(Protocol):
    def parse(self, path: Path) -> ParsedDoc: ...
```

| Rung | Implementation | Runs where | Use when |
|---|---|---|---|
| 1 | `PyMuPDF` / `pdfplumber` | in-process | Native text layer present, layout simple |
| 2 | **Docling** (layout + table structure) | in-process | Tables matter, or layout is multi-column. **This is the workhorse rung.** |
| 3 | Local OCR — Tesseract, or Docling's OCR path | in-process | No text layer — scanned. Example RFP 1 in the corpus is this case. |
| 4 | External high-fidelity service (Pulse.ai or equivalent) | **off-box** | Gated. See below. |

**Rung 2 is where Group 5 lives or dies.** Visit frequency and visit intensity are read off the Schedule of
Assessments grid. `pdfplumber` alone does not reliably recover a complex SoA table; Docling's table-structure
model does. When validating the parser choice, measure Group 5 field accuracy specifically — a parser that
scores well on prose and badly on grids will look fine in aggregate and fail at the thing DSB cares about.

**The privacy gate — one rule, applied consistently.** Any parser that transmits document content outside
the customer boundary is a rung-4 parser, regardless of vendor. Pulse.ai, AWS Textract, Google Document AI,
Azure Document Intelligence — the rule does not care which. All of them sit behind a single feature flag,
`parser.allow_external`, which is **off by default** and cannot be enabled by config alone in a deployed
environment without an explicit privacy sign-off recorded in the run's audit record (§6.4). Rungs 1–3 all
run in-process on Cloudera infrastructure and are the only rungs the MVP uses.

**Hard contract regardless of rung:**
- Page numbers are preserved and 1-indexed. Citations are worthless otherwise.
- Tables survive as structured rows, not flattened prose. The Schedule of Assessments grid *is* the visit
  frequency and visit intensity evidence — flatten it and Group 5 becomes unextractable.
- An `outline` of `(heading_text, page_start, page_end, level)` is produced. PLAN depends on it.
- Quality gate: `chars_per_page`, `alpha_ratio`, table count. Below threshold → escalate a rung, and record
  the escalation in `errors` and in `status.json` so the run is explainable while it is still running.

### 4.2 CLASSIFY
One cheap structured call per document. Returns `kind`, `confidence`, `version_label`, `document_date`,
`sponsor`, `protocol_id`. Classify from the **first 3 pages + outline headings only** — not the whole doc.

Phase 1's routing bug (RFPs sent to the protocol extractor) came from classification being entangled with
extraction. Here classification only *labels*; it never gates which fields are attempted. Every field group
is attempted against every document. Precedence sorts it out later. Misclassification therefore degrades
ranking, not recall — a much safer failure mode.

### 4.3 PLAN
Pure Python. For each `(doc, group)`:
1. Look up the group's `search_hints` (headings, keywords, regex) in `fields.yaml`.
2. Score every outline section; take top-k sections plus a configurable page margin.
3. If keyword scoring finds nothing, fall back to embedding similarity over page chunks.
4. If still nothing, take the document abstract/synopsis pages — never the whole document.
5. Emit `ExtractionTask(doc_id, group, page_window, budget_tokens)`.

Cap `page_window` by token budget. A window that would exceed the budget is split into multiple tasks and
their records merge naturally at fan-in (that is what the `operator.add` reducer is for).

### 4.4 EXTRACT (the fan-out leaf)
One structured-output call per task. Bounded schema = only that group's fields. Prompt skeleton:

```
You are extracting {group_label} from a clinical study document for a delivery-budgeting team.

RULES
- Return a record ONLY if the document states it. Never infer, never estimate, never use outside knowledge.
- `quote` must be copied character-for-character from the excerpt. It is validated.
- If the document explicitly says none/not applicable → status="not_specified".
- If you cannot find it → omit the field entirely. Do not guess.
- Set `scope` when the value applies to a cohort/arm/part/country rather than the whole study.
- If a value differs by cohort, emit one record PER cohort. Do not average or merge.

FIELDS
{rendered from fields.yaml: id, label, type, enum values, aliases, hint}

DOCUMENT: {doc_kind}, pages {p_start}–{p_end}
<excerpt>{page-tagged text and tables}</excerpt>
```

**Post-call validation, in code, non-negotiable:**
1. `quote` must be a substring of the excerpt (normalized whitespace). Fail → one repair retry with the
   violation named → still fail → drop the record and log to `errors`.
2. `page` must fall inside the task window.
3. Enum values must be in the registry's allowed set.
4. Numeric fields must parse.

Record-level rejection, not response-level. One bad field does not discard 8 good ones.

This validation layer matters more on CAII than it would on a frontier hosted model: see §5.1 on
structured-output strategy. Validation is the safety net that makes a less reliable schema-follower usable.

### 4.5 NORMALIZE
Pure Python, zero LLM, fully unit-tested. `"every 3 weeks"`, `"Q3W"`, `"21 days"` → `{n: 21, unit: "days"}`.
`"forty (40) sites"` → `40`. `"Phase 1/2"`, `"Ph I/II"` → `PHASE_1_2`. This layer is why contradiction
detection can be deterministic. Every normalizer is a pure function with a table-driven test.

**Pure means non-mutating, including `normalize_record`.** It returns a copy with `value` (and
`unit`) set and leaves its argument alone. It used to edit the record in place and return the same
object, which is what let the doubling described in §3 hide — the node was handing back the very
records it had been given. The node returns `Replace(...)` because it rewrites the whole
collection.

### 4.6 RECONCILE
Group records by `(field_id, scope)`. Then:
- **1 record** → resolved, confidence carried through.
- **n records, canonical values equal** → resolved, confidence *boosted* (independent corroboration across
  documents is real signal — reward it).
- **n records, canonical values differ** → emit a `Contradiction` candidate. Do not resolve yet.

Precedence policy (`config/precedence.yaml`), applied only after adjudication:
1. **Recency** — a later amendment beats an earlier protocol version. Always first.
2. **Domain authority** — Protocol wins on scientific/design facts (phase, design, dosing, interim analyses,
   blinding). RFP wins on commercial/operational scope (site counts, enrolment targets, monitoring
   frequency asked of the CRO, CRF pages).
3. **Specificity** — an explicit number beats prose that implies one.

Angus flagged that interim analyses can appear in *either* document — rule 2 is precisely why the policy is
per-field in YAML rather than a blanket "protocol always wins."

### 4.7 ADJUDICATE
LLM, invoked once per candidate, never on the corpus at large. Input: field definition, both/all records with
full quotes and provenance, the applicable precedence rule. Output: verdict + explanation + resolved value +
severity. Three verdicts:

- `not_a_conflict` — different scope, different unit, different study period. **Expect this to be the most
  common verdict.** A detector that cannot say "these don't actually disagree" floods DSB with noise and
  gets switched off.
- `reconcilable` — both true, one is a subset or restatement. Explain and pick.
- `conflict` — genuinely incompatible. Surface it loudly, do not auto-resolve, force `needs_review`.

`severity: high` when the field feeds a budget driver (site count, subject count, visit count, monitoring
frequency, duration). Mark this in `fields.yaml` with `budget_driver: true`.

**Because in-app adjudication is deferred (§11), the report is the only place a contradiction gets resolved.**
That raises the bar on this node's output: the explanation must be complete enough for an analyst to act on
offline, without the tool. Both values, both quotes, both page citations, the precedence rule that applies,
and a recommended resolution with its reasoning. "Values disagree" is not an acceptable explanation in MVP.

### 4.8 DERIVE
Computed fields get their own node so they are never confused with extracted ones. Currently one:
`visits.intensity_rating` — Angus asks for a low/moderate/high/not-specified judgement. Implement as a
**transparent rubric over extracted evidence**, not a vibe call:

```
score = Σ weights over present evidence:
  PK/PD sampling +2, biomarker sampling +1, imaging +2, ECGs +1, safety labs +1,
  questionnaires +1, infusion observation period +2, visit window < 3 days +1,
  ≥ 8 assessments per visit +2, visits more frequent than weekly in treatment period +2
0–3 low | 4–7 moderate | 8+ high | no evidence at all → not_specified
```
`ResolvedField.derived_from` lists the field ids that fed it, and the report prints the contributing
evidence. A DSB reviewer must be able to see *why* it said "high" and disagree with it.

### 4.9 GATE
Maps confidence and contradiction state to a reviewer-facing status:

| Condition | Status |
|---|---|
| single or corroborated source, conf ≥ 0.80, quote validated | `confirmed` |
| conf 0.50–0.80, or derived, or `reconcilable` verdict | `needs_review` |
| `conflict` verdict, or conf < 0.50, or budget_driver with any disagreement | `needs_review` (flagged) |
| explicit none/N/A in source | `not_specified` |
| searched, absent | `not_found` |

Calibrate the thresholds against the golden set — do not ship the numbers above as gospel, ship the mechanism.

**The implementation is deliberately stricter than the table's last row.** A `budget_driver`
field forces `needs_review` on *any* adjudicated verdict, including `not_a_conflict` — not just
on `conflict`/`reconcilable`. A misjudged "these don't really disagree" on a site count is the
costliest place ADJUDICATE's single LLM call can be wrong, and that call is the only
non-deterministic step upstream of the number a budget is built from. See `gate/__init__.py`.

### 4.10 RENDER — pure functions, not a graph node
Canonical JSON is the source of truth; renderers are pure functions over it.

**RENDER is not `graph.add_node("render", ...)`, and the topology diagram in §2 shows it
outside the graph for that reason.** `RunState` carries `run_id` and no filesystem path, so a
render node would have to reconstruct the output location from run_id anyway — and `job/output.py`
already wrote files outside the graph before RENDER existed. The renderers live in `render/`
(json/pdf/xlsx) with a thin I/O wrapper in `job/output.py`, called from `job/__init__.py` once
`compiled.stream()` finishes. Keeping it out of the graph also keeps the graph free of I/O, which
is what makes the whole pipeline testable without a filesystem.

- **`extraction.json` (primary)** — the machine-readable contract. This is what a downstream budget service
  consumes. Full fidelity: every `ResolvedField` with value, status, confidence, all `sources`, quotes,
  contradictions, and `derived_from`. Versioned by the `fields.yaml` registry version that produced it.
- **`report.pdf` (primary)** — the human deliverable, and the artifact Angus reviews. Executive summary,
  contradictions section up front (see §4.7 — this is where conflicts get resolved in MVP), then variables
  by group with value, status, source document, page and quote.
- **`report.xlsx` (renderer)** — a renderer alongside the two above, not the primary path. One row per field:
  `Group | Variable | Value | Status | Confidence | Source Doc | Page | Quote | Contradiction`.
  Conditional formatting: red = conflict, amber = needs_review, green = confirmed, grey = not found.
  Keeps the `Review Queue` sheet — non-confirmed rows only, budget drivers first — as an XLSX feature for
  analysts who prefer to work in a spreadsheet.

**Budget creation is out of scope.** This tool produces the inputs. A downstream service consumes
`extraction.json` and populates the budget model. Keeping that boundary clean is why JSON is a primary
deliverable rather than an afterthought.

---

## 5. The three seams

Three places where the implementation is swapped by configuration and nowhere else. Each is a Protocol with
a local implementation now and a documented future one. Nothing outside the seam's package may import the
implementation's dependencies.

### 5.1 LLM — `llm/provider.py`

```python
def get_llm(role: Literal["classify","extract","adjudicate"]) -> BaseChatModel
```

Roles map to models via config, so the cheap job and the hard job are not forced onto the same model:

| Role | Model tier | Why |
|---|---|---|
| classify | smallest served model | 3 pages, 5-way label |
| extract | mid-tier workhorse | volume × precision; the bulk of all calls |
| adjudicate | largest served model | low call count, high reasoning demand |

**Routing lives in `config/models.yaml`, not in code.** `domain/model_routing.py` loads and validates
it; `llm/provider.py` builds the model. Each role names a provider, a model id, and — see below — the
structured-output strategy that endpoint actually honours.

| Provider | Egress | Used for |
|---|---|---|
| `caii` | `none` | **POC, demo, and production.** The strategic target and the default. |
| `mock` | `none` | Deterministic fixtures. CI runs here, with no network. |
| `litellm` | `unverifiable` | Local dev proxy. Routes wherever the developer's credentials point, which cannot be checked from inside this process — so it is **treated as external**. |
| `bedrock` | `external` | Off-box managed service. Testing on non-sensitive data only, never production. Behind the optional `aws` extra so private deployments never install a vendor SDK. |

**`privacy_mode` is an enforced invariant, not a UI preference.** `private` (the default and the
production posture) permits only `egress: none` providers and refuses to construct anything else.
`mixed` permits an external provider only for a role that *also* sets `allow_external: true` — the
recorded consent for document egress. `open` permits anything. It is checked at load **and again at
provider construction**, deliberately duplicated so a routing built or mutated in memory cannot reach
an external service by skipping the loader. It fails closed: an unknown provider, an unknown mode, or
a role that has not opted in is an error, never a silent downgrade.

**Every LLM role here receives verbatim document excerpts.** There is no metadata-only role, so
`mixed` narrows *which* documents leave, never *whether* they do.

`RFP_INTAKE_LLM_BACKEND=mock` short-circuits routing entirely — the offline test escape hatch.

**Structured output strategy — the risk to design for.** The whole extraction approach depends on
reliable schema-constrained output. `llm/structured.py` implements two strategies behind one interface:

1. **Native tool-calling** (`with_structured_output`) — a JSON schema is sent as a tool definition, the
   server constrains generation to match it, and the result comes back parsed and separate from any prose.
   Note that **no tool is ever executed**; this is a transport for structured output, and using it does
   not violate rule 1 — the LLM still chooses no control flow.
2. **Schema-in-prompt** — the schema goes in the system message and the JSON is parsed out of the reply.

**Do not auto-detect the strategy, and do not trust a passing smoke test.** Two failures found on
2026-08-27, both of which had produced false confidence:

- **Endpoints lie by omission.** A vLLM-backed CAII endpoint deployed without
  `--enable-auto-tool-choice` and `--tool-call-parser` accepts a tool-calling request, returns HTTP 200,
  and simply emits no tool call — prose instead, on every extraction group. Only a *streaming* request
  surfaces the real error. Constrained-decoding flags fail the same silent way: both `guided_json` and
  `nvext.guided_json` were accepted and ignored, and `response_format: json_schema`, which that endpoint
  does honour, degenerated into unbounded whitespace on the nested per-group schemas.
- **A fallback masks the thing you are testing.** `StructuredOutput` falls back native→guided once, so a
  smoke test that reports "native works" may be reporting the fallback. **Verify with
  `allow_fallback=False`** before pinning a strategy.

So the strategy is pinned per role in `models.yaml`, with the observation that justified it written
beside it. Both strategies return the same validated records, and the §4.4 validation layer runs
identically either way.

**Bound every call.** `llm_timeout_s` and `llm_max_tokens` are not tuning knobs, they are safety rails.
Constructed without them, one runaway generation held a CML job open for 21 minutes with no janitor to
reap it. A response stopped at the token ceiling is truncated JSON, which the parser would otherwise
report as malformed — sending the reader to look at the model when the answer was correct and our cap
cut it off. `_hit_token_ceiling` checks the finish reason first and says so, across both spellings
(`finish_reason: length`, `stopReason: max_tokens`).

Never hand-roll free-text JSON parsing, and never hand-roll the message wire format — prior syncs burned
time on `assistant`/`user` role mismatches and prefill incompatibilities, which are symptoms of exactly that.

### 5.2 Input resolution — `io/inputs.py`

```python
class InputResolver(Protocol):
    def resolve(self, source: str) -> list[Path]: ...
```

`source` is a URI. The resolver returns local filesystem paths, fetching remote content if needed, and the
graph never knows the difference.

| Implementation | Scheme | Status |
|---|---|---|
| `LocalInputResolver` | `file://` | **Build now.** Reads `runs/{run_id}/inputs/`. |
| `ObjectStoreInputResolver` | `abfss://` (ADLS) | Seam marked, not implemented. |

Implement local, define the interface, leave the object-store class as a documented stub that raises
`NotImplementedError`. The point of writing the seam now is that the graph never grows a hardcoded
filesystem assumption that has to be unpicked later.

### 5.3 Parser — `ingest/parsers/`
Covered in §4.1. Same rule: the ladder is config, the interface is fixed, and any implementation that moves
content off-box sits behind the privacy gate.

---

## 6. Execution model

**The UI and the graph run in separate processes.** This is the single most important operational decision
in the document, and it is not negotiable for a long-running job with a browser front end.

### 6.1 The two processes

**Cloudera AI Application — the UI.** It generates the `run_id`, stages uploaded documents into the run
directory, triggers the CML Job with that `run_id`, and polls for status. **It never executes the graph
in-process.** A web worker that runs a multi-minute LangGraph pipeline inside a request handler will block,
time out, and lose state on restart — and it makes concurrent runs a resource-contention problem in the one
process the user's browser depends on.

**CML Job — the executor.** Invoked with `run_id` as its argument. Reads inputs from the run directory,
executes the graph, writes all outputs back to the run directory, exits. One job run per RFP package.
The job is the only process that touches the graph.

### 6.2 The run directory — the coordination contract

Neither process calls the other. They coordinate through a directory:

```
runs/{run_id}/
  inputs/              # APP writes, JOB reads.        Uploaded documents, original filenames.
  status.json          # JOB writes, APP polls.        Current node, heartbeat, per-document outcomes.
  audit.json           # JOB writes.                   Per-run audit record (§6.4).
  state.json           # JOB writes.                   RunState snapshot for debugging and resume.
  extraction.json      # JOB writes.                   Canonical resolved output (§4.10).
  report.pdf           # JOB writes.
  report.xlsx          # JOB writes, if the renderer is enabled.
  logs/                # JOB writes.
```

**Ownership is strict:** the app owns `inputs/` and reads everything else. The job owns everything except
`inputs/`. No file has two writers. This is what makes the contract safe without a lock.

`status.json` carries enough for the UI to render a real progress view, not a spinner:

```json
{
  "run_id": "r-2026-08-21-a3f9",
  "state": "running",
  "node": "EXTRACT",
  "started_at": "2026-08-21T14:02:11Z",
  "heartbeat_at": "2026-08-21T14:03:47Z",
  "progress": { "tasks_total": 27, "tasks_done": 19 },
  "documents": [
    { "name": "RFP_v3.pdf", "state": "parsed", "parser_rung": 2, "pages": 42 },
    { "name": "Protocol_v1.pdf", "state": "parsed", "parser_rung": 3, "pages": 188,
      "note": "no text layer — escalated to OCR" }
  ],
  "error": null
}
```

The job writes `status.json` on every node transition, and refreshes `heartbeat_at` at least every 30
seconds during long fan-out so a wedged run is distinguishable from a slow one. Per-document outcomes are
part of status, not just of the final report — when a 200-page protocol takes four minutes to OCR, the user
needs to see that happening.

### 6.3 Status has two sources. Use both.

| Source | Answers | Authoritative for |
|---|---|---|
| CML Jobs API | Is the process alive? Did it exit? With what code? | **Process liveness** |
| `status.json` | What work has been done? Where is it? | **Work progress** |

Neither is sufficient alone, and the failure modes are the reason:

- A job can be **alive but wedged** — the Jobs API says `running`, but `heartbeat_at` is ten minutes old.
  Only the combination detects this.
- A job can **die mid-node** — the Jobs API says `failed`, and `status.json` tells you which node it died in
  and which documents had already been parsed. Only the combination is diagnosable.
- A job can **finish and fail to report** — the Jobs API says `succeeded`, but `status.json` never reached a
  terminal state. Treat as failed; a run with no terminal status is not a completed run.

Resolution rule: the Jobs API decides whether the process is running. `status.json` decides what happened.
A run is `succeeded` only when both agree.

### 6.4 The audit record — `audit.json`

> **Not built yet (as of 2026-08-27).** Designed here, not implemented. The routing half of it
> exists — `llm/discovery.py:describe_active_routing()` returns the privacy mode, each role's
> provider/model/egress, and the `external_services` list ready to drop into this shape.

Distinct from per-field provenance, and required because this feeds clinical pricing. Per-field provenance
answers "where did this number come from in the document?" The audit record answers "what exactly was run,
by whom, against which models, and can we reproduce it?"

```json
{
  "run_id": "r-2026-08-21-a3f9",
  "submitted_by": "agray@…",
  "submitted_at": "2026-08-21T14:02:09Z",
  "completed_at": "2026-08-21T14:06:31Z",
  "documents": [
    { "name": "RFP_v3.pdf", "sha256": "…", "bytes": 1840221, "pages": 42,
      "parser_rung": 2, "parser": "docling" }
  ],
  "models": [
    { "role": "extract", "endpoint": "https://…caii…/v1", "model": "…",
      "structured_output": "guided_decoding", "calls": 27,
      "tokens_in": 214880, "tokens_out": 19442 }
  ],
  "external_services": [],
  "outputs": [ { "path": "extraction.json", "sha256": "…" },
               { "path": "report.pdf", "sha256": "…" } ],
  "code_version": "git:8f21c4e",
  "registry_version": "fields.yaml v1 sha256:…"
}
```

Two fields carry more weight than they look like they do. `registry_version` makes a run reproducible — a
pricing input is worthless if you cannot say which field definitions produced it. `external_services` is
empty in every normal run; a non-empty value is the record that the privacy gate (§4.1) was opened, and is
what a security review will ask for.

### 6.5 One janitor, not one watcher per run

A single **scheduled CML Job** — the janitor — runs on a fixed interval. For every run directory it:
1. Reads `status.json`. If `state == "running"` and `heartbeat_at` is older than the stale threshold,
   cross-checks the Jobs API. If the process is not running, marks the run `failed` with reason `stale`.
2. Applies the retention TTL: purges `inputs/` and, past a longer horizon, the whole run directory.

**Do not spawn a monitor process per run.** N concurrent runs must not mean N watchdog processes — that
scales the wrong thing and turns a six-user tool into a process-management problem. One janitor sees all runs.

### 6.6 The checkpointer complements this; it does not replace it

The LangGraph checkpointer (`SqliteSaver` in the run directory, `PostgresSaver` if a shared store is
available) operates *inside* the job process: which nodes completed, what the state was at each. It is what
makes a re-invoked job resume rather than restart.

The run directory operates *between* processes: it is the only thing the app can see. The checkpointer
cannot serve that role — the app has no LangGraph runtime and should not acquire one.

Both are needed. Resume flow: the janitor or a user marks a run for retry → the job is re-invoked with the
same `run_id` → the checkpointer restores state from the last completed node → the job continues and keeps
writing the same run directory.

---

## 7. Persistence, scale, ops

- **Scale is small and known**: 6 concurrent users × 3–4 documents. **Do not build a queue, a cluster, or a
  microservice mesh.** The CML Jobs API is the queue.
- **The throughput ceiling is CAII endpoint capacity, not a vendor rate limit.** This is a materially
  different constraint from a hosted-API quota, and it cuts both ways. We own the serving capacity, so it can
  be sized — but it is finite and shared, and an over-eager fan-out will queue against our own endpoint and
  degrade latency for every concurrent user. Tune `max_concurrency` to the endpoint's replica count and
  batching behaviour, measure it, and treat it as a deployment parameter rather than a constant. Back off on
  429/503 from the endpoint the same way you would a vendor limit.
- **Cost/latency budget**: target < 90s wall clock for a 3-document package, excluding OCR. Instrument
  per-node timing from day one; the Phase 1 five-minute runtime was the loudest user complaint. OCR-heavy
  documents will exceed this — which is why per-document parser rung is surfaced in `status.json`.
- **Observability**: structured logs keyed by `run_id`/`task_id`, written to `runs/{run_id}/logs/`; every LLM
  call logs role, model, endpoint, token counts, latency, and a hash (not the content) of the prompt.

---

## 8. Security & compliance

RFPs and protocols are study-design documents, not patient records — PHI exposure is low but sponsor
confidentiality is absolute, and the output feeds a commercial bid. Posture:

- **Data stays inside the customer boundary.** Documents are uploaded into the run directory on Cloudera
  storage, parsed in-process by CML Jobs, and inferred against CAII endpoints inside the same environment.
  There is no egress to a third-party model provider on the processing path. This is the design centre of
  the project, not a mitigation applied to it.
- **Private inference is the destination and the default.** CAII serves POC, demo, and production. The only
  non-CAII inference path is the local LiteLLM proxy used during development, which never sees customer
  documents — developers work against the corpus in `eval/golden/` and synthetic material.
- **External services are gated, uniformly.** Any component that transmits document content off-box — an
  external parser above all — is behind `parser.allow_external`, off by default, and its use is recorded in
  `audit.json.external_services`. An empty `external_services` array is the evidence that nothing left.
- **Encryption**: at rest on Cloudera storage, TLS in transit to the CAII endpoint and between app and job.
- **Tenancy and access**: run artifacts are scoped by `run_id` and owner. The app authorises on the
  submitting user; no cross-user read of another run directory. `submitted_by` in the audit record is the
  accountable identity.
- **Retention**: uploaded documents in `inputs/` carry a TTL and are purged by the janitor (§6.5). Derived
  artifacts have their own, longer horizon. Neither is indefinite.
- **Two-layer audit**: per-field provenance (doc, page, quote) for "where did this number come from",
  and the per-run audit record (§6.4) for "what was run, by whom, on which models, reproducible how".
  A clinical pricing context needs both, and they are not substitutes.

---

## 9. Evaluation — build this in Phase 1, not later

Without a golden set you cannot tell a prompt improvement from a prompt regression, and every future model
swap becomes a guess.

- `eval/golden/` — the corpus already in hand (Example RFP 1, Protocols 1–3) plus synthetic messy variants.
  Angus offered to produce redacted/synthetic hard cases; that offer is on the critical path, ask for it.
- Hand-label each `field_id` per document: expected value + expected source page.
- **Score per field, not per report**: precision, recall, `not_found` rate, citation accuracy
  (does the cited page actually contain the quote?), and contradiction precision/recall separately.
- Report a confusion matrix over `found` / `not_specified` / `not_found` — P2 says these are different
  answers, so measure them as different answers.
- Run the suite in CI against `mock`, and on demand against real endpoints.

**Score against the CAII endpoint, not only the dev proxy.** If prompts are developed against a LiteLLM-routed
model and shipped against a CAII-served one, the two are different models and the golden-set numbers will
differ. Track the delta between dev backend and CAII as a standing metric, and gate any release on the CAII
number. This is the same discipline as the old "validate before switching to private" note, except that CAII
is now the destination rather than the alternative — so it is the dev backend that is the deviation.

**Citation accuracy is the metric to watch.** A wrong value with an honest citation gets caught by a reviewer
in seconds. A right value with a fabricated citation destroys trust in the whole tool.

---

## 10. Build order

| Phase | Deliverable | Done when |
|---|---|---|
| 0 | Repo, config, `fields.yaml` loader, Pydantic schemas, `mock` LLM, the three seams, CI | `pytest` green; registry loads and validates; `resolve_inputs` local impl works |
| 1 | INGEST (rungs 1–3) + CLASSIFY + eval harness | Every corpus doc parses with page fidelity; SoA tables survive as rows; classifier ≥ 95% on the corpus |
| 2 | PLAN + EXTRACT + NORMALIZE, both structured-output strategies | End-to-end records with validated quotes on one document, against a real CAII endpoint |
| 3 | RECONCILE + ADJUDICATE + DERIVE + GATE | Multi-document run produces a resolved field set with fully-explained contradictions |
| 4 | RENDER — `extraction.json` + `report.pdf` (+ XLSX renderer) | A report Angus can open and mark up, and a JSON a downstream service could consume |
| 5 | Execution model — CAI Application UI, CML Job entrypoint, run directory, `status.json`, `audit.json`, janitor | Two users run concurrently; UI shows live per-document progress; a killed job is reaped and resumable |
| 6 | CAII validation and tuning | Golden-set scores on the CAII endpoint; `max_concurrency` tuned to endpoint capacity; dev-vs-CAII delta recorded |

Ship Phase 4 to Angus before starting Phase 5. Feedback on the report shape is worth more than infrastructure —
and until Phase 5 exists, the graph runs perfectly well from a CLI, which is enough to iterate on output.

---

## 11. Deferred to Phase Two

Designed for, deliberately not built in MVP.

- **The REVIEW node and `interrupt()`.** In-app adjudication — an analyst resolving a contradiction inside
  the tool and the graph resuming — is out of MVP scope. The node is absent from the compiled graph and the
  config flag defaults off. The consequence is deliberate and load-bearing: **clearly framed contradictions
  in the report are sufficient for v1**, which is why §4.7 sets a high bar on the explanation. Build the
  state model so the interrupt boundary is reachable later — the checkpointer already gives us that — but do
  not build the UI, the resume-from-review flow, or the edit-and-re-render path.
- **Object-store input resolution** (`abfss://`). Interface defined in §5.2, implementation deferred.
- **External high-fidelity parsing** (rung 4). Gated off; needs procurement and privacy sign-off before it
  is even an engineering question.

---

## 12. Explicit non-goals

- No autonomous agents, no agent-chooses-the-next-tool delegation. Determinism is the feature.
- No RAG over a persistent vector store. Documents are per-run and small; targeted page selection beats
  a retrieval index and stays citable.
- **No budget creation.** This tool produces `extraction.json`. A downstream service consumes it and builds
  the budget. That boundary is the reason JSON is a primary deliverable.
- No in-app contradiction resolution in MVP. See §11.
- No graph execution inside the web application process. See §6.1.
- No per-run watchdog processes. See §6.5.
- No fine-tuning. The schema-and-prompt surface is nowhere near exhausted.
