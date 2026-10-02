# RFP Intake Agent — Architecture

**What it is:** an automated first pass over clinical study RFPs and protocols. It reads the
documents, pulls out the variables the Delivery Strategy & Budgeting (DSB) team needs in order to
price a study, shows where each value came from, and flags the places where two documents disagree.

**What it produces:** a report — PDF, spreadsheet and JSON — in which every value carries the
sentence it was read from, the document it came from, and the page number.

**Who reads that report:** a DSB analyst who is accountable for the budget that comes out the other
end, and who needs to be able to check any number in it against the source in a few seconds.

**Built on:** LangGraph (a fixed, non-autonomous pipeline) running on Cloudera — Cloudera AI
Inference for the models, Cloudera AI Application for the web interface, CML Jobs for execution.

**Audience for this document:** engineers building this repo. It is the build contract.

> **How to read this document.** It describes the target design — not every part is built.
> Sections marked *Not built yet* are design intent. Where the running code deviates deliberately
> from the design, the deviation is recorded inline in the relevant section, with the date it was
> made, rather than left for you to discover. `CLAUDE.md` carries current build status.
> `config/fields.yaml` is the single source of truth for what gets extracted.
>
> **New to LangGraph?** Read `docs/LANGGRAPH_PRIMER.md` first. It is one page and explains the
> handful of concepts this document assumes.

---

## 0. What this system is, and what it must never do

### It is a pipeline, not an autonomous agent

The steps are always the same, no matter what the documents say:

> parse the documents → identify what kind of document each one is → decide which pages to read
> for which variables → read them → convert the answers to a standard form → find places where
> two documents disagree → judge whether each disagreement is real → compute the derived
> variables → decide what needs human review

Because that list never varies, it is written in Python. A language model is called at three
points inside it to answer narrow questions — *what does this sentence say?*, *do these two
values really disagree?* — and at no point to decide what happens next.

That separation is the central design decision of this project. **Letting a model decide what to
do next is a place the report changes between runs. Letting it decide what a sentence means is a
place it adds value.** Keeping those apart is what the rest of this document describes.

### Documents must not leave the customer boundary

This is a hard requirement, not a preference to design around. It is close to the point of the
project. Study protocols and sponsor RFPs are commercially sensitive and often
patient-adjacent, and a customer who cannot be shown where the text went cannot use the tool at
all.

Concretely:

- All parsing happens in-process — no third-party document-understanding service.
- Inference runs on Cloudera AI Inference, inside the customer's environment.
- Any component that would send document content off-box sits behind a switch that is **off by
  default**, and its use is recorded in the run's audit record (§6.4). An empty
  `external_services` list is the evidence that nothing left.
- This is enforced in code, not by discipline: `privacy_mode` in `config/models.yaml` refuses to
  construct an off-box model provider at all in its default setting. See §5.1.

AWS Bedrock appears in the configuration as a **testing-only** escape hatch, so that development
and demos are not blocked when endpoint capacity is short. It may only ever see synthetic or
otherwise non-sensitive documents, and production runs on Cloudera AI Inference.

---

## 1. The five principles

These five statements decide most of the design arguments in this document. Where a later section
seems over-engineered, it is usually one of these being paid for.

### P1 — A value with no quote is not a value

Every variable the system reports carries the **verbatim sentence it was read from**, plus the
document it came from and the page number. If the model cannot ground a value in text it can
quote, the system reports the variable as *not found* rather than guessing.

This is checked in code, not trusted: after every extraction call, the quote is verified to be a
genuine substring of the source text. A finding that fails the check is discarded and logged,
never repaired.

*Why:* the analyst reading the report is accountable for the resulting budget. A number they
cannot trace to a sentence is worse than no number, because it costs them time to disprove.

### P2 — "The document doesn't say" is a real answer

RFPs are incomplete by nature. *"Number of CRF pages: not stated in the source documents"* is a
**correct and useful output** — it tells the analyst to go and ask the sponsor. It is not a
failure of extraction.

The system therefore keeps three outcomes strictly distinct, everywhere — in the schema, the
normalizer, the report and the evaluation metrics:

| Outcome | Meaning |
|---|---|
| `found` | A value was read from the documents. |
| `not_specified` | The documents explicitly say none, N/A, or not applicable. |
| `not_found` | We looked and the documents do not address it. |

*Why:* collapsing these into each other — treating "absent" as zero, or "explicitly none" as
"we didn't look" — is the most expensive mistake a budgeting tool can make. Zero patients and
unknown patients produce very different budgets.

### P3 — Many small extraction jobs, never one big one

A single prompt asking for 45 variables across a 200-page protocol will invent answers, and will
not give the same ones twice.

Instead the work is split into small, targeted jobs: one per **document × field group**, each
with a narrow schema, a handful of candidate pages, and a single thing to look for. Nine groups
across three documents is roughly 27 small calls of 3–15k tokens each, rather than one call of
300k. They run in parallel, so elapsed time is bounded by the slowest call rather than their sum.

*Why:* small context, small schema, and a contained failure. When one call goes wrong it costs
one field group on one document, not the whole report.

### P4 — The list of variables lives in a config file, not in code

Which variables get extracted is defined in `config/fields.yaml`. The prompts, the JSON schemas,
the validation rules and the report columns are all generated from that file.

Adding a variable is therefore a YAML edit that the domain expert can review, not a code change
requiring an engineer. **If adding a variable ever requires editing Python, the design has
drifted and should be corrected rather than worked around.**

*Why:* the variable list is expected to keep changing. Angus (IQVIA DSB, the domain expert)
described the current list as "a decent starting point… there are definitely other details."
The architecture should make that sentence cheap to act on.

### P5 — Find disagreements with code; ask the model only to judge them

When two documents give different values for the same variable, finding that pair is **set logic
over normalized values** — code, which is fast, exhaustive and gives the same answer every time.

Only then is a model asked a single question about one specific pair: *is this a real conflict, a
difference that reconciles, or not a disagreement at all?*

**Never ask a model to "look for contradictions."** It will find some, miss others, and do both
differently next time. And even when it rules something a conflict, it does not choose the
winning value — that is a deterministic precedence rule (§4.6).

*Why:* recall on contradictions is the feature DSB values most, and recall is exactly what a
free-form model search cannot guarantee.
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
                        │FIND_SECTIONS │  split each document into sections with exact
                        └──────┬───────┘  (page, character offset) boundaries  (pure Python)
                               │
                        ┌──────▼───────┐
                        │SET_ASIDE_    │  drop sections whose heading is on
                        │  SECTIONS    │  config/sections.yaml  (pure Python)
                        └──────┬───────┘
                               │
                        ┌──────▼───────┐
                        │MARK_OTHER_   │  remove text about a different study (the model
                        │  STUDY       │  labels passages; code cuts them)
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

**FIND_SECTIONS was added on 2026-10-02**, as stage 1 of `docs/PLAN_2026-10-02.md`. Before it, the
smallest unit PLAN could choose was a whole page plus one page of margin, so text from a section PLAN
had not chosen still reached the extraction model — which is how `study.phase` came back as the phase of
a different study from page 39 of `samples/Example protocol 2.pdf`. A section boundary is now a
(page number, character offset into that page's text) pair, so a page can be cut in two. **Stage 2 of
the same plan, also 2026-10-02, moved PLAN and EXTRACT onto those sections**: PLAN scores sections rather
than `Document.outline` entries, and the excerpt EXTRACT sends holds only the chosen sections' text. The
**Stage 3, 2026-10-02, added SET_ASIDE_SECTIONS** (§4.2b): the two dozen kinds of section an analyst
skips entirely come off `Document.sections` before PLAN scores them. **Stage 4, 2026-10-02, added
MARK_OTHER_STUDY** (§4.2c): the one node of the four that calls a model, which labels passages that
describe a different study so that code can cut their text out before PLAN scores anything. All four
nodes of `docs/PLAN_2026-10-02.md` between CLASSIFY and PLAN are now built.

**Every edge is a static Python edge or a `Send`.** There are no LLM-chosen routes and no agent delegation.
In MVP there is exactly one conditional edge: `ADJUDICATE` is skipped when the candidate set is empty.
That is the whole of the non-determinism budget. The `GATE → REVIEW` edge is designed for and left
unimplemented — see §11.

### Why the map-reduce shape matters
`PLAN` emits **at least** `N_docs × 9` tasks — a group whose candidate pages exceed the token budget is
split into several windows, so a 137-page protocol produces more than one task per group (2 documents
× 9 groups gave 37 tasks in practice, not 18). On a typical 3-document RFP package that is ~27+ small calls, each with
3–15k tokens of targeted context instead of one call with 300k. Parallel, so wall-clock is bounded by the
slowest single call, not the sum. Elapsed time is the constraint users complain about first: "5 minutes to
run" was the loudest piece of feedback from the Warsaw session, and running these calls in parallel is the
direct answer to it.

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
    confidence: float                    # 0..1, the model's own rating. Nothing calibrates it (§4.9)
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
    set_aside: list[SetAsideSection] = []
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

**`Document` gained `sections` and `section_source` on 2026-10-02** (stage 1 of
`docs/PLAN_2026-10-02.md`). They travel in the graph state exactly as `outline` and `page_texts` already
do, and `extraction.json` does not carry them — `render/json_renderer.py` writes resolved fields,
contradictions and errors, not documents.

**`RunState` gained `set_aside` on 2026-10-02** (stage 3). It holds one `SetAsideSection` per section
SET_ASIDE_SECTIONS removed — document, section id, heading, pages, and either the
`config/sections.yaml` entry that matched the heading or the parent section that took it. It has **no
reducer**, deliberately: SET_ASIDE_SECTIONS is the only producer and runs once, so last-value-wins is
correct, and an append reducer would double the list if the node ever ran twice. Unlike `sections`, this
one *is* in `extraction.json`, under `set_aside_sections`. It is not in `report.pdf`, which stage 3
keeps short — so extraction.json is the only place a reader can find out that a field came back empty
because its section was set aside.

```python
class Section(BaseModel):
    id: str                       # "<doc_id>:s001", unique within the run
    heading: str
    level: int = 1
    page_start: int
    page_end: int
    start_offset: int = 0         # character offset into page_texts[page_start]
    end_offset: int | None = None # exclusive; None = to the end of page_end

class Document(BaseModel):
    ...
    sections: list[Section] = []
    section_source: Literal[
        "bookmarks", "whole_document", "heading_scan", "whole_document_fallback"
    ] | None = None
```

**A boundary is a (page, character offset) pair, not a page.** That is the whole point of the type: page
39 of `samples/Example protocol 2.pdf` holds the end of section 1.3.1 and the start of section 1.3.2, so
no choice made in whole pages can keep one and drop the other. Sections tile a document's text — each runs
to the start of the next heading at any level — so no text is in two sections and none is dropped. Text
before the first heading becomes a section headed "Front matter".

**`ExtractionTask` gained `section_ids` on 2026-10-02** (stage 2 of `docs/PLAN_2026-10-02.md`):

```python
class ExtractionTask(BaseModel):
    doc_id: str
    group: str
    page_window: tuple[int, int]   # first and last page the chosen sections touch
    section_ids: list[str] = []    # the sections PLAN chose; empty = every page in page_window
    budget_tokens: int | None = None
```

`page_window` stays because `extract/validate.py` checks each extracted record's page against it, and it
is what the prompt prints on its `DOCUMENT:` line. When the chosen sections are not next to each other the
window spans the gap, so `page_window` alone is a weaker guard than it looks — what actually keeps text
from an unchosen section out of a record is that the excerpt is built from `section_ids` and the quote must
be a substring of that excerpt. An empty `section_ids` means every page in `page_window`, which is what a
task built by hand without sections means.

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
- An `outline` of `(heading_text, page_start, page_end, level)` is produced. FIND_SECTIONS (§4.2a)
  depends on it, and PLAN depends on FIND_SECTIONS' output. INGEST produces the outline from the PDF's
  bookmarks, which a PDF need not have: `samples/Synthetic_RFP_NEOD001.pdf` has none, which is why
  FIND_SECTIONS has two further rules.
- Quality gate: `chars_per_page`, `alpha_ratio`, table count. Below threshold → escalate a rung, and record
  the escalation in `errors` and in `status.json` so the run is explainable while it is still running.

### 4.2 CLASSIFY
One cheap structured call per document. Returns `kind`, `confidence`, `version_label`, `document_date`,
`sponsor`, `protocol_id`. Classify from the **first 3 pages + outline headings only** — not the whole doc.

**Classification labels a document; it never decides which fields are attempted.** Every field group is
attempted against every document regardless of what CLASSIFY decided, and precedence (§4.6) sorts out which
answer wins later.

This is deliberate and worth protecting. If classification gated extraction, a single mislabelled document
would silently lose every field it held — a failure with no symptom in the output, because a field that was
never attempted looks exactly like a field that was absent. Keeping the two separate means misclassification
degrades *ranking* rather than *recall*, which is a far safer way to be wrong.

### 4.2a FIND_SECTIONS
Pure Python, no model call, no I/O. Added 2026-10-02 as stage 1 of `docs/PLAN_2026-10-02.md`. Code in
`sections/` (`find_sections_node`, plus `sections/headings.py` for rule 3).

**Lettered, not numbered**, because renumbering §4.3 to §4.10 would break every reference to them in
`CLAUDE.md`, in the code comments and in `docs/PLAN_2026-10-02.md`. SET_ASIDE_SECTIONS is §4.2b;
MARK_OTHER_STUDY is §4.2c.

**Input:** `state.documents`, as CLASSIFY left them. **Output:** the same documents with `sections` and
`section_source` filled in. Three rules, tried in order, and the one that fired is recorded on the
document:

1. `bookmarks` — one section per entry in `Document.outline`. The bookmark's heading is located in its
   page's text to get the character offset, matching on whitespace-normalised lowercased text because
   PyMuPDF returns the sample protocol's headings as `"1.3.2 \nClinical Experience"` while the bookmark
   title is `"1.3.2 Clinical Experience"`. That finds 174 of that document's 176 titles; the 2 that are
   not found fall back to the start of their page and are logged.
2. `whole_document` — no bookmarks, and the whole text fits in one extraction call
   (`DEFAULT_TOKEN_BUDGET`, now in `plan/scoring.py` so `sections/` and `plan/` can both read it without
   an import cycle). One section covering every page. `samples/Synthetic_RFP_NEOD001.pdf` lands here:
   0 bookmarks, ~1,900 estimated tokens, 6 pages — so page 6, the services requested, is read at last.
3. `heading_scan` — no bookmarks and too long for rule 2, so headings are guessed from the text:
   dotted-number headings, and lines in capitals. Lines repeated on at least half the pages are treated
   as running headers, not headings. `whole_document_fallback` is recorded when the scan finds nothing,
   because PLAN must never be handed a document with no sections.

**Rule 3 is text-only.** `docs/PLAN_2026-10-02.md` also suggests font size and weight from PyMuPDF's
`page.get_text("dict")`. That is deliberately not done: the same stage says FIND_SECTIONS reads only what
INGEST produced, and `page_texts` carries no font information, so using it would put a second PDF reader
outside `ingest/parsers/`. If text-only detection proves too weak on a real bookmark-less protocol, the
right fix is for a parser to record heading candidates, not for this node to open the file.

**Boundaries, not pages.** `sections/section_page_texts()` cuts the first and last page at the section's
offsets and returns the pages between whole, so a caller can keep the `--- Page N ---` markers the
extraction prompt and quote validation both depend on.

### 4.2b SET_ASIDE_SECTIONS
Pure Python, no model call, no I/O. Added 2026-10-02 as stage 3 of `docs/PLAN_2026-10-02.md`. The node
is `sections/set_aside.py`; the config loader and all the matching rules are
`domain/section_policy.py`, which is where `config/sections.yaml` is read.

**Input:** `state.documents`, as FIND_SECTIONS left them. **Output:** the same documents with the
set-aside sections removed from `Document.sections`, plus one `SetAsideSection` on `RunState.set_aside`
for each removal. Removing them here means PLAN never scores them and no later node has to know the
policy exists. On `samples/Example protocol 2.pdf` it removes 55 of 176 sections, about 35% of the
text, and the title page, the synopsis, the Schedule of Events table and section 1.3.2 all survive.

**Why the loader lives in `domain/`, not `sections/`.** Same reason `DEFAULT_TOKEN_BUDGET` moved to
`domain/budget.py`: `plan/scoring.py` must not import `rfp_intake.sections`, and keeping the policy in
`domain/` leaves it importable from anywhere without reopening that cycle.

**Four rules, in order.** The config file documents the first three; the fourth is the skip.

1. **Only remove what is positively listed.** An unanticipated heading survives. The opposite design —
   keep an expected list, drop the rest — would hide exactly the unbudgeted cost this tool exists to
   find. `CLAUDE.md` rule 4 applies: no heading is hardcoded in Python.
2. **A listed section takes its children with it**, down to the next heading at the same or a higher
   level, because "4 SUBJECT SELECTION" means the whole of section 4. One forward pass over
   `Section.level`; the sections arrive in document order. A child records `via_parent` rather than a
   `matched` entry, so a removal on the child's own heading is distinguishable from an inherited one.
3. **`keep_if_contains` overrides both.** Keeping a section nobody needed costs tokens; dropping one
   that held a cost driver costs a number nobody budgets for, so the asymmetry is deliberate.
4. **A document with one section is left alone** — FIND_SECTIONS rule 2, the synthetic RFP. There is
   nothing to set aside and its only section is the whole document.

`FRONT_MATTER_HEADING` and `WHOLE_DOCUMENT_HEADING` are never set aside. FIND_SECTIONS invents both, so
no list of real section names owns them, and dropping either would discard text on the strength of a
word this codebase chose itself.

**A document is never emptied.** If every section matched, the document is kept whole and a
`kind="validation"` `RunError` says so. PLAN would otherwise have nothing to score and the run would
report every field as not found with no hint why — a policy bug that looks like a silent document.

**Four things `keep_if_contains` got wrong, all found by running it on the real protocol**
(`tests/sections/test_set_aside_samples.py`, the stage 3 acceptance test). Each is recorded here
because each is a rule someone would otherwise undo in good faith:

- **A rescue reads the section's body, not its heading** (`strip_heading`). The heading has already had
  its say. "8 EMERGENCY UNBLINDING OF STUDY DRUG" is on `set_aside` and its own heading contained the
  rescue phrase, so it rescued itself from its own title — as did `9 ADVERSE EVENTS` and
  `12.3 Quality Control and Quality Assurance`.
- **Index sections cannot be rescued at all** (`always_set_aside`). A table of contents, a list of
  tables and a glossary are made of the document's own headings and definitions, so *every* rescue
  phrase appears in them by construction: the contents page rescued itself with "Emergency Unblinding",
  the list of tables with "Schedule of Events", and the glossary with its definition of "case report
  form". None of those mentions is a fact about the study.
- **Count phrases need a number beside them** (`keep_if_contains_with_number`, within 60 characters).
  This is Angus's own qualification — ignore case report form details "unless the text gives a number
  of case report forms" [02:00:16]. Without it the bare word "eCRF" rescued the adverse-event section,
  the treatment-compliance section and the glossary, none of which states a count.
- **`standard of care` is not on `set_aside`**, though
  `docs/ANALYST_PROCEDURE_PROTOCOL.md` section 4 says to ignore it [01:13:15]. The protocol's title
  page reads "... NEOD001 PLUS STANDARD OF CARE VS. PLACEBO PLUS STANDARD OF CARE IN SUBJECTS WITH
  LIGHT CHAIN (AL) AMYLOIDOSIS", so the entry set aside the single most valuable section in the
  document — the title alone carries the phase, the blinding, the control, the number of arms and the
  population (`docs/ANALYST_PROCEDURE_PROTOCOL.md` section 2).

**`background on` was added on 2026-10-02, as stage 5 item 1; the bare words `background`,
`introduction` and `rationale` are still deliberately absent.** Section 4 of the analyst procedure lists
all three, and the narrow phrase is the only one safe to list. `rationale` would match
"1.2 Rationale for Clinical Study", which is where the sample protocol states the comparison being
made, and `introduction` would take the whole of section 1 with it under the nesting rule.

The narrow entry waited for stage 4 on purpose. `study.phase` was wrong because of section 1.3.2, which
sits inside the background and describes a different study, and MARK_OTHER_STUDY (§4.2c) is the node
built to find it. Dropping the background first would have made stage 4's live test pass without
MARK_OTHER_STUDY doing anything. Stage 4 has now passed twice on its own — runs
`r-20261002-213531-stage4` and `r-20261002-222824-stage4b` — so the entry is safe.

**1.3.2 Clinical Experience survives for an incidental reason, and a test says so.** It is nested under
1.3 and would go with it, but `keep_if_contains` rescues it on the phrase "interim analysis" — and that
phrase sits in one of the very sentences MARK_OTHER_STUDY then removes. The rescue is real, because
stage 3 runs before stage 4, but a protocol that worded its interim analysis differently would lose the
section, and the section is not meant to go whole: MARK_OTHER_STUDY cuts the five sentences about study
NEOD001-001 and the rest is this study's text.
`tests/sections/test_set_aside_samples.py::TestTheBackgroundAndNonclinicalSections` asserts the survival
and records why, so the day it stops being true a test fails instead of a number going missing.

### 4.2c MARK_OTHER_STUDY
Added 2026-10-02 as stage 4 of `docs/PLAN_2026-10-02.md`. Code in `other_study/`
(`mark_other_study_node`, plus `other_study/prompt.py`). **The only one of the three nodes added
between CLASSIFY and PLAN that calls a model**, under the role `other_study_check` in
`config/models.yaml`.

**Why a model is needed here when FIND_SECTIONS and SET_ASIDE_SECTIONS do not need one.** The passage
that breaks `study.phase` is section 1.3.2 "Clinical Experience" — an ordinary heading in an ordinary
place, inside the background, where the surrounding sections are about this study and are needed.
Nothing about the heading says the text under it describes a different study, so no list of headings
can catch it. Matching study numbers in the text does not work either: the protocol prints its own
number beside the other one.

**Input:** `state.documents`, as SET_ASIDE_SECTIONS left them. **Output:** the same documents with
`Document.removed` filled in, plus one `RemovedPassage` on `RunState.removed_passages` per removal.

**Removal happens in `sections/section_page_texts()`**, the one function PLAN's scoring and EXTRACT's
excerpt both go through to read a section's text. Neither PLAN nor EXTRACT knows this node exists.
`Document.page_texts` is never edited — a `RemovedPassage` carries `TextSpan` offsets into it, the same
coordinates `Section` already uses — so nothing has to re-derive a boundary and the original text is
still there to quote in the report.

**A removed span leaves `"\n\n"`, not nothing and not a marker.** Not nothing, because the sentence
before and the sentence after would fuse into one sentence that was never written. Not `[removed]`,
because EXTRACT validates every quote against this same text, so any word injected here becomes a word
the model could quote.

**Sections are not deleted; their text is.** A section whose text is entirely removed stays on
`Document.sections`, scores zero in PLAN and is never chosen. Keeping it is what lets
`extraction.json` and `report.pdf` name the heading a removal came from.

**One call per batch of sections, not one per section.** Sections are grouped up to
`DEFAULT_TOKEN_BUDGET` — about ten calls for the 137-page sample protocol, the same order of magnitude
PLAN already spends. A section longer than the whole budget gets its own batch rather than being
split, and a section under `MIN_SECTION_CHARS` (200) is never asked about, on length alone and never
on keywords.

**Three verdicts, and nothing is removed on the model's word alone.**

1. `this_study` — nothing is removed.
2. `other_study` — the whole section's text is removed.
3. `mixed` — only the sentences the model copied out are removed, and each must be found in the
   section's own text by `locate_text`, the same whitespace-tolerant search FIND_SECTIONS uses to place
   a bookmark. **A sentence that cannot be located is kept, not removed**, and logged as
   `other_study_sentence_not_found`. A paraphrase therefore costs nothing; only a verbatim copy cuts
   text. The prompt says so, and it says to answer `this_study` when unsure: keeping a passage costs
   some tokens, removing one wrongly loses a number the budget needs.

**The study the documents are about is named in the prompt**, from `Document.protocol_id` and
`Document.title` as CLASSIFY recorded them, preferring the protocol's own values over an RFP's. CLASSIFY
gained `title` for this; it is asked for in the same call that already classifies the document, so there
is no extra model call. Without the identity in the prompt the model has two study descriptions and no
way to tell which one it is being asked about.

**A document is never emptied**, for the same reason as §4.2b: if every section with text was marked
`other_study`, nothing is removed and a `kind="validation"` `RunError` says so
(`other_study_would_empty_document`). A model that answered `other_study` to everything would otherwise
produce a run reporting every field as not found with no hint why.

**Removals are in `report.pdf`, unlike set-aside sections.** Appendix C lists each one with its
document, heading, pages, whether a whole section or part of one went, the model's one-sentence reason
and the first 300 characters of the text. Skipping the references is housekeeping; deciding that a
passage describes a different study is a judgement a reviewer may want to overturn, and nobody can
overturn what they cannot see. `extraction.json` carries the whole text and the offsets under
`removed_passages`.

### 4.3 PLAN
**Rewritten on 2026-10-02 by stage 2 of `docs/PLAN_2026-10-02.md`.** PLAN chooses sections, not pages.
The page margin, the embedding-similarity fallback and the "first 5 pages" fallback are all gone; the
scoring weights are unchanged.

Pure Python, no model call. For each `(doc, group)`:
1. Look up the group's `search_hints` (headings, keywords) in `fields.yaml`.
2. Score every section from FIND_SECTIONS (`plan/scoring.py:score_section`) — the heading against
   `headings`, the **section's own text** against `keywords`. Scoring the section's text rather than its
   pages is load-bearing: section 1.3.1 shares page 39 of `samples/Example protocol 2.pdf` with section
   1.3.2, and scoring by page credited 1.3.1 for 1.3.2's words.
3. Take the top k (`DEFAULT_TOP_K = 3`) sections scoring above zero, in document order
   (`plan/scoring.py:select_sections`). **A zero-scoring section is never chosen to fill k.**
4. If nothing scored: send every section when the whole document fits one extraction call, otherwise the
   first `DEFAULT_TOP_K` sections, logged as `plan_no_section_scored`. A document that FIND_SECTIONS rule 2
   made one section is therefore sent whole to every group — which is what finally reads page 6 of
   `samples/Synthetic_RFP_NEOD001.pdf`.
5. Emit `ExtractionTask(doc_id, group, page_window, section_ids, budget_tokens)`, where `page_window` is
   the first and last page the chosen sections touch.

**One call per (document, group) unless the chosen sections do not fit one call.** A set of sections over
the token budget is split by page, every resulting task naming the same sections, and their records merge
at fan-in (that is what the `append_or_replace` reducer in §3 is for). A page is the smallest unit PLAN can
split by, so one page larger than the budget on its own stays one task and is logged as
`plan_page_over_budget`.

**FIND_SECTIONS is the only producer of sections, and PLAN writes nothing.** PLAN reads
`Document.sections` and raises `MissingSectionsError` when a document arrives without any; `plan_node`
returns `{"tasks": ...}` and no `documents`. FIND_SECTIONS cannot produce a document with no sections —
rule 2, `whole_document_fallback` and its own exception handler each guarantee at least one — so an empty
list means FIND_SECTIONS did not run, which is a wiring mistake. `job/__init__.py` catches the exception,
writes it into `status.json` with the class name in `detail`, and exits non-zero. PLAN briefly sectioned
such a document itself; that made two producers of the same data, which could drift apart.

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
SECTIONS: {headings of the sections PLAN chose}
<excerpt>{page-tagged text of those sections, and tables}</excerpt>
```

**The excerpt holds only the chosen sections' text, as of 2026-10-02** (stage 2 of
`docs/PLAN_2026-10-02.md`). `extract/prompt.py:_build_excerpt` cuts each section at its exact character
offsets and emits one block per section per page, each with its own `--- Page N ---` marker. Two blocks are
never joined, so a quote cannot span the gap between two chosen sections and still validate. The same
function is what quote validation reads (`build_excerpt`, called from `extract/__init__.py`), so the model
and the validator can never see different text — do not add a second excerpt builder.

A task whose `section_ids` is empty falls back to whole pages in `page_window`. A task naming sections the
document does not have gets an **empty excerpt** and a logged `excerpt_sections_not_on_document`, rather
than falling back to whole pages, because that fallback would quietly restore the behaviour this change
removed.

**Known limitation: a table can come from a section PLAN did not choose.** `TableData` records the page a
table is on but not where on the page, so a table is included when its page falls inside a chosen section.
Where two sections share a page, the table may belong to the other one. Accepted for now.

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
Computed fields get their own node so they are never confused with extracted ones. There are two, both
in `derive/rubric.py` and both registered in `DERIVE_RUBRICS` (`derive/__init__.py`). A derived field
with no registered rubric degrades to `not_specified` and logs an error rather than crashing the run;
`tests/domain/test_registry.py` asserts that every derived field in `config/fields.yaml` has one, because
that degradation would otherwise show up as a missing number in the report and nothing else.

**`visits.intensity_rating`** — Angus asks for a low/moderate/high/not-specified judgement. Implement as a
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

**`blinding.placebo_assumption`**, added 2026-10-02 as stage 5 items 9 and 10. Whether a placebo that
matches the study drug can be assumed, which decides whether separately paid unblinded staff should be
priced in. No document states it, so it is worked out from four extracted fields — `blinding.design`,
`ip.form`, `blinding.placebo_matching` and `blinding.unblinded_staff_stated` — by
`docs/ANALYST_PROCEDURE_PROTOCOL.md` section 6, in this order:

1. **Open-label** → "not applicable: open-label". Nobody is blinded, so the question does not arise.
2. **Both stated and in conflict** — the document says the placebo matches *and* says unblinded staff
   are required → say the documents contradict each other and the sponsor must be asked, with both
   quotes in the explanation. This is checked *before* rule 3, because rule 3 would otherwise report the
   matching placebo as settled, which is the one outcome the contradiction rule exists to prevent. It is
   section 6 rule 6; Angus said one of the two is often a typo [01:39:42].
3. **The document says** whether the placebo matches → use what it says. The route never overrides a
   statement.
4. **Silent, drug injected or infused** → "probably not matching; unblinded handling likely". Liquids
   are hard to colour-match. `intrathecal` is counted as injected for the same reason.
5. **Silent, drug oral** → "matching not confirmed; oral drug; do not assume unblinded monitoring".
6. **Silent, and the route decides nothing** (topical, inhaled, ophthalmic, `other`, not stated) → say
   so and ask the sponsor. It deliberately does *not* fall through to the oral answer, which reads as
   "do not price unblinded staff" and would be reassurance nobody had evidence for.

**Why this is a rubric and not a RECONCILE check.** RECONCILE compares values of the *same* field across
documents (`reconcile/__init__.py`). Rule 2 above is a disagreement between two *different* fields, which
RECONCILE has no way to see. GATE sends every derived field to `needs_review`, so the answer always
reaches a person.

The explanation names every input it used and quotes it where there is a quote, because the whole value
of an assumption a person can overrule is that they can see what it rests on.

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
- **`report.pdf` (primary)** — the human deliverable, and the artifact Angus reviews. Five sections, in
  this order: (1) five lines naming the documents, the counts and what to do with the report; (2) **All
  variables** — every variable by group, with value, status, confidence and pages, a row in a disagreement
  saying so and pointing at its number; (3) **Disagreements between the documents** — each one numbered,
  with both sides' values and pages and the verdict (see §4.7 — this is where conflicts get resolved in
  MVP); (4) **Flagged for review** — every other value needing a look, each with the reason it is flagged;
  (5) **Schedules** — the many-row variables (visit schedule, timeline components), one table each. Then
  Appendix A quotes every source passage, Appendix B holds the adjudicator's reasoning and the dismissed
  disagreements, and the plain-English word list is last.
  *Deviation, 2026-09-18:* the quote moved from beside each value to Appendix A after a customer found
  the 15-page report too long to read. What is shown where is decided in `render/report_model.py`, and
  `tests/render/test_report_model.py::assert_nothing_lost` fails if any variable, value, page, status,
  quote or disagreement from `RunState` is missing from the report.
  *Deviation, 2026-09-30:* the same customer asked for the variables before the decisions — see
  everything that was read before being asked to adjudicate any of it — so the review sections moved
  behind **All variables**, and every flagged row now carries the reason it is flagged rather than only
  its name. The printed order is set in `render/pdf_renderer.py:build_report_pdf` and asserted by
  `tests/render/test_pdf_renderer.py::test_the_five_sections_are_in_the_order_the_customer_asked_for`.
  The reason on each flagged row is recomputed in `render/report_model.py:_review_reasons` from the same
  inputs GATE used, so the report cannot name a rule that did not fire.
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
  per-node timing from day one — a five-minute run was the loudest user complaint on record. OCR-heavy
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

## 9. Evaluation — build this early (§10 Phase 1), not later

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

## 11. Deferred — deliberately not in the MVP

Designed for, deliberately not built in MVP.

- **The REVIEW node and `interrupt()`.** In-app adjudication — an analyst resolving a contradiction inside
  the tool and the graph resuming — is out of MVP scope. The node is absent from the compiled graph and the
  config flag defaults off. That leaves the report as the only place a contradiction gets resolved, which
  is an accepted trade for v1 — **a clearly framed contradiction in the report is enough** — and is why §4.7
  demands so much of the explanation text. Build the
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
