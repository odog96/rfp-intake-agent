# LangGraph in 5 minutes — for this repo

New to LangGraph? Read this before `ARCHITECTURE.md`. It covers only what this project uses.

## The idea

LangGraph runs **a fixed pipeline of Python functions**. Each function is called a *node*. The
order they run in is set by *edges*, which we write by hand and which never change while the
pipeline is running.

All the nodes share **one Python object**, called the *state*, which is passed from each node to
the next. A node reads what it needs out of that object and returns its results, which get merged
back into it before the next node runs.

A language model is called *inside* three of the nodes, to answer one narrow question each.
**The model never decides which node runs next.** That is the whole design.

## The state object

Our state object is a class called `RunState`, defined in `src/rfp_intake/domain/schemas.py`.
It has eight **state fields** — ordinary Python attributes, each holding one kind of result:

| State field | What it holds | Which node puts it there |
|---|---|---|
| `run_id` | the name of this run | set at startup |
| `documents` | the parsed PDFs — page text, headings, tables | INGEST |
| `tasks` | a list of jobs like "read pages 12–18 of doc-a1b2 for the visit variables" | PLAN |
| `records` | one finding per variable per place it was found | EXTRACT |
| `contradictions` | pairs of values that disagree, plus a verdict on each | RECONCILE, ADJUDICATE |
| `resolved` | the single final answer for each variable | RECONCILE, DERIVE |
| `report_paths` | where the PDF and spreadsheet were written | RENDER |
| `errors` | anything that went wrong, and in which node | any node |

> **Note on the word "field."** In this document, **state field** always means an attribute of
> `RunState`, as in the table above. The clinical values the system extracts from documents —
> site count, study duration, and so on — are called **variables** throughout, and they are
> defined in `config/fields.yaml`. The two are unrelated.

Read the right-hand column downward and you have the pipeline. Each node turns one state field
into the next: `documents` → `tasks` → `records` → `resolved`.

**A node can only see what is in the state object.** EXTRACT cannot ask PLAN a question, because
PLAN has already finished and returned. All EXTRACT has is `state.tasks`. This is why the data
shapes matter so much — they are the only contract between two nodes that never meet.

`RunState` is a Pydantic model, so the shape of each state field is checked as the pipeline runs.
A node that returns something malformed fails immediately, with a clear message, rather than
causing a confusing symptom three nodes later.

## Nodes are ordinary functions

A node takes the state object and returns **a dictionary naming only the state fields it changed**:

```python
def ingest_node(state: RunState) -> dict[str, Any]:
    documents = [parse(p) for p in find_input_files(state.run_id)]
    return {"documents": documents, "errors": errors}
```

That return value names two state fields. The other six are not mentioned, so LangGraph leaves
them exactly as they were. **A node never returns a whole new state object — only its changes.**

Six of our nine nodes call no model at all: INGEST, PLAN, NORMALIZE, RECONCILE, DERIVE and GATE
are plain Python.

## Edges are twenty boring lines

An **edge** is a rule saying which node runs after which. All of ours are written by hand, in
`src/rfp_intake/graph/__init__.py`:

```python
graph.set_entry_point("ingest")
graph.add_edge("ingest", "classify")
graph.add_edge("classify", "plan")
graph.add_edge("plan", "extract")
...
```

That file is the entire control flow of the system. To know what happens in what order, read
those twenty lines. There is nowhere else to look, because nothing else is able to change the
order.

## Reducers — the one tricky part

A node returns a dictionary like `{"records": [...]}`. But what if `state.records` already has
something in it? LangGraph needs a rule: **does the returned list replace what is there, or get
added to it?**

That rule is called a **reducer**. It is set once per state field, in the `RunState` class
definition, and it applies every time any node writes to that field.

```python
class RunState(BaseModel):
    documents: list[Document] = Field(default_factory=list)                        # no reducer → replace
    errors: Annotated[list[RunError], operator.add] = Field(default_factory=list)  # operator.add → append
```

- **`documents` replaces.** No reducer is specified, which is LangGraph's default. INGEST writes
  this field once, and the list it returns simply becomes the new value.
- **`errors` appends.** `operator.add` means "add the new list onto the existing one." Several
  nodes may each hit a problem and each return `{"errors": [one_error]}`. You want to end up with
  all of them, not just whichever node ran last.

### Why `records` needed something more careful

The `records` field has two kinds of writer, and they need opposite behaviour:

- **EXTRACT** will eventually run as many parallel copies, one per document per group of
  variables. Each copy returns only its own handful of findings. These must be **appended** —
  under "replace," whichever copy finished last would erase every other copy's work.
- **NORMALIZE** reads the entire `records` list, converts every entry to a standard form
  (`"forty (40) sites"` becomes `40`), and returns the whole converted list. This must
  **replace** — under "append," you end up with the raw originals *plus* the converted copies,
  and every finding appears twice.

`records` was originally set to plain append, so exactly that happened: a 45-variable registry
produced 162 resolved variables instead of 81. It survived 343 passing tests, because the offline
test backend produces no records at all, so the duplication was invisible until the first run
against a real model.

The fix (in `schemas.py`, 2026-08-27) lets each node state which behaviour it wants at the moment
it returns:

```python
class Replace(list):
    """Marker telling append_or_replace to overwrite rather than append."""

def append_or_replace(current: list, update: list) -> list:
    if isinstance(update, Replace):
        return list(update)      # NORMALIZE returns Replace(...) — it rewrote everything
    return current + update      # EXTRACT returns a plain list — it contributed one part
```

**The lesson:** when you add a state field, choose its reducer deliberately, and write down which
nodes write to it and whether each one is contributing a part or replacing the whole. "It seemed
to work" is not evidence — this bug ran green through the entire test suite.

## Where the model is actually called

| Node | The question put to the model | What it is *not* asked |
|---|---|---|
| CLASSIFY | "Is this an RFP, a protocol, an amendment, a schedule of assessments, or something else?" | Not asked which variables to extract. Every group is tried against every document regardless. |
| EXTRACT | "In these pages, what are the values of these variables? Quote the sentence you read each from." | Not asked to find variables we did not name, or to give a value it cannot quote. |
| ADJUDICATE | "These two values for the same variable disagree. Is that a real conflict, a difference that reconciles, or not a disagreement at all?" | Not asked to *find* contradictions — code does that. Not asked to pick the winner — a fixed rule in `reconcile/precedence.py` does that. |

Which model each of the three uses is **configuration, not code**. `config/models.yaml` binds each
one to a provider and a model name. Pointing CLASSIFY at a small cheap model and EXTRACT at a
large one is a two-line edit to that file, with no code change.

## Why not agents

An "agent," in the usual sense, is a language model that picks its own next action — which tool to
use, which other agent to hand work to, when to stop. That suits open-ended problems where you
cannot write the steps down in advance.

Ours is not open-ended. The steps are the same every time, regardless of what the documents say.
Letting a model choose the order would gain us nothing and cost three things:

- **Repeatability.** The same RFP has to produce the same report twice. If a model picks the
  route, it might not.
- **Debuggability.** When a number is wrong we need to know which step produced it. With a fixed
  pipeline that is one function to open. With delegation it is a transcript to reconstruct.
- **Containment.** If the model is wrong about a value, one *variable* is wrong. If the model is
  also choosing the route, the *entire run* can be wrong.

The model still does the part nothing else can: reading clinical prose and returning structured
facts. We just don't also let it drive.

## Read the code in this order

1. `graph/__init__.py` — the whole pipeline, twenty lines.
2. `domain/schemas.py` — `RunState` and the data shapes that move through it.
3. `graph/nodes/ingest.py` — the simplest complete node, to see the input and output shape.
4. `config/fields.yaml` — the variables being extracted. This file generates the prompts, the
   schemas, the validation and the report columns.
5. `docs/ARCHITECTURE.md` §4 — what each node promises to do.

## Glossary

**Node** — a Python function that is one step of the pipeline.

**Edge** — a hand-written rule saying which node runs after which.

**State object** — the single Python object passed from node to node. Ours is `RunState`.

**State field** — one attribute of that object, such as `documents` or `records`. Not to be
confused with the clinical *variables* in `config/fields.yaml`.

**Reducer** — the rule for combining a node's returned value with whatever is already in a state
field: replace it, or add to it.

**`Send`** — LangGraph's mechanism for running many copies of a node in parallel, one per item of
work. We will use it in EXTRACT to run one model call per document per group of variables.

**Structured output** — techniques for making a model return JSON matching a schema instead of
prose. Two are used here; see `strategy` in `config/models.yaml`.

**Provenance** — the record of where a value came from: document id, page number, and the
verbatim sentence it was read from.
