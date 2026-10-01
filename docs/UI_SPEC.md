# User interface specification — Cloudera AI Application

Version 2, 31 August 2026. Supersedes version 1 in full; delete version 1.
Applies to: `app.py` only.
Companion file: `docs/mockups/app_ui.html` — open it in a browser to see the
four page states this document describes.

Version 2 adds section 6 (a run that fails) and section 7 (a run that finishes
with a document missing). Everything else is unchanged from version 1.

---

## 0. Scope boundary — read this before anything else

**This is a front-end-only change. It touches `app.py` and nothing else.**

Functional work on this repository is in progress in a separate workstream at
the same time. If you change engine behaviour while implementing this
specification, you will collide with that work. So:

**Do not modify any of these, for any reason, including reasons that seem
obviously correct while you are working:**

- anything under `src/rfp_intake/` — the graph, the nodes, the extraction
  prompts, the adjudication prompts, the parsers, the normalisers, the
  renderers, the domain schemas
- `config/fields.yaml` and `config/models.yaml`
- `run_job.py`, `launch_app.py`
- `docs/ARCHITECTURE.md`
- anything under `tests/` or `eval/`
- `requirements.txt` and `pyproject.toml` — this change needs no new
  dependency

**Do not do any of the following, even though a reasonable engineer might:**

- do not change the shape of `extraction.json` or `status.json`
- do not add a field, a status value, a node, or a status key so the page can
  display something more conveniently
- do not "fix" extraction, adjudication, or parsing behaviour you notice while
  reading the code
- do not refactor `src/rfp_intake/` for readability
- do not add tests for engine code
- do not change how the CML Job is triggered, other than moving the existing
  trigger call from its own button into the submit button, which is described
  in section 3 below

The page must read whatever `extraction.json` and `status.json` already
contain and present it well. Where a value the design wants is not available,
degrade the display as described — do not go and produce the value upstream.

If, while implementing this, you conclude that something in `src/rfp_intake/`
is wrong or missing, **stop and say so in your response**. Do not fix it.

The only file you may write to is `app.py`. You may add a `.streamlit/`
configuration file if section 9 requires it, and nothing else.

---

## 1. What this application is for

The application does three jobs, in this order of importance:

1. It takes documents in — an RFP (Request for Proposal, the sponsor's
   solicitation document) and its accompanying clinical protocol.
2. It tells the user honestly where the run is while it is running.
3. It hands back the finished files.

It is **not** a review tool. The extracted content lives in `report.pdf`, which
runs to more than twenty pages, and in `report.xlsx` and `extraction.json`. The
page does not render that content, does not offer a field-by-field table, and
does not offer any way to edit or accept a value. Adjudication by a human is
out of scope for this release.

The one exception is section 5: when a run finishes, the page states in a few
numbers what came back, so the user knows what they are about to open. That is
a summary, not the results.

The user is a Delivery Strategy and Budgeting analyst at IQVIA. They are not a
platform operator. They should not need to know what a CML Job, a graph node,
or a parser rung is in order to use the page.

---

## 2. Page structure

The page has four states. There are no numbered section headings and no step
headings.

| State | When it shows | Section |
| --- | --- | --- |
| A | Before a run, and while a run is going | 3 and 4 |
| B | A run finished and produced a report | 5 |
| C | A run failed and produced nothing | 6 |
| D | A run finished but a document could not be read | 7 |

The current numbered headers `1. Upload Documents`, `2. Start Processing`,
`3. Pipeline Progress` and `4. Results` are removed. They are the reason the
page currently appears to skip from 1 to 3: section 2 only renders in the
window between upload and trigger.

Every state keeps the same title, the same sidebar, and the same run details
expander at the foot of the page. Only the middle of the page changes.

---

## 3. Submit

Keep `st.file_uploader` as it is, including `accept_multiple_files=True` and
the PDF type restriction. Change its label to `Upload the RFP and the protocol`.

The submit button becomes a single action. Today `app.py` writes the uploaded
files to `runs/{run_id}/inputs/` under a button labelled `Submit for
Processing`, and then calls `trigger_job()` under a second button labelled
`Launch Pipeline`. Move the `trigger_job()` call into the first button's
handler, immediately after the files are written, inside the same `try`. Label
the button `Start review`. Delete the second button and the block that draws
it.

This is the only change to job-triggering behaviour that is in scope. The body
of `trigger_job()`, including the environment variable and the arguments it
passes, does not change.

If `trigger_job()` raises, show the error and leave the uploaded files in
place, so the user can retry without re-uploading.

---

## 4. Status panel, while a run is going

Draw this with `st.status`, which gives a labelled panel that can be expanded
and whose label changes as the run proceeds. Keep the existing two-second poll
and `st.rerun()` loop, and keep the existing arrangement where the CML Jobs
API is consulted for process liveness and `status.json` for work progress.
None of the error handling in `check_job_run_status()` changes; only its
presentation does.

**The panel label is a plain-English step name, not the node name.** Map the
`node` value in `status.json`, which is written uppercase by
`src/rfp_intake/job/__init__.py`, to these labels:

| `node` value | Step label | Position |
| --- | --- | --- |
| `INGEST` | Reading documents | 1 |
| `CLASSIFY` | Identifying document types | 2 |
| `PLAN` | Planning the extraction | 3 |
| `EXTRACT` | Pulling out study details | 4 |
| `NORMALIZE` | Standardising values | 5 |
| `RECONCILE` | Comparing the documents | 6 |
| `ADJUDICATE` | Resolving disagreements | 7 |
| `DERIVE` | Calculating derived values | 8 |
| `GATE` | Final checks | 9 |
| `DONE` | Review complete | — |
| `ERROR` | Run failed | — |

Put this mapping in a single module-level dictionary in `app.py`. **Section 6
reuses the same dictionary**, so define it once. Any node name not in the
dictionary falls back to the raw value, so a node added by the other workstream
does not crash the page.

Inside the panel, in this order:

- A progress bar, from `status["progress"]["tasks_done"]` and
  `tasks_total`, exactly as `app.py` reads them today.
- One line of caption text: `{tasks_done} of {tasks_total} fields extracted`,
  followed by the step position from the table above — `step 6 of 9`.
- The per-document list that `app.py` already draws from
  `status["documents"]`. Each row shows the file name, and to the right the
  document state and page count in plain words: `read, 12 pages` for state
  `parsed`, `reading, 148 pages` for `parsing`, `waiting` for `pending`,
  `could not be read` for `failed`. The `parser_rung` value is an internal
  detail and is removed from this row.

Show the run identifier at the top right of the panel in small monospace text.

The existing warnings — the CML Jobs API being unreachable, an unrecognised
CML status — keep their current wording and their current conditions. Draw
them above the status panel.

---

## 5. State B — a run that finished and produced a report

When `status["state"] == "completed"` and every document in
`status["documents"]` has state `parsed`, show this. If any document has state
`failed`, show state D instead, described in section 7.

**Delete `st.balloons()`.** This tool prices pharmaceutical clinical trials for
an IQVIA team; the animation reads as consumer software.

First line, with a green tick: `Review complete` followed by the document
count, the total page count, and the elapsed time. Total pages is the sum of
`pages` across `status["documents"]`. Elapsed time is `heartbeat_at` minus
`started_at`, both of which `RunStatus` in `src/rfp_intake/job/status.py`
already writes as ISO timestamps — no new status key is needed.

Then three `st.metric` cards, drawn from `extraction.json` and computed from
the `status` value on each entry in `resolved_fields`. That value is one of
`confirmed`, `needs_review`, `not_found`, `not_specified`, per `ResolvedField`
in `src/rfp_intake/domain/schemas.py`.

| Card label | Value |
| --- | --- |
| Fields filled | count of `confirmed` plus `needs_review`, shown as `38 of 45` where the denominator is `len(resolved_fields)` |
| Need a human check | count of `needs_review` |
| Disagreements found | count of entries in `contradictions` whose `verdict` is `conflict` or `reconcilable` |

The denominator matters. `Confirmed 38` alone does not tell the analyst whether
the run was complete; `38 of 45` does. Use the caption slot or the value string
for the denominator, whichever Streamlit renders more legibly at this size.

Below the cards, when the disagreement count is greater than zero, one
`st.warning` containing a single sentence: how many disagreements there are,
the label of the first one, and where to find them in the report. For example:
`The RFP and the protocol disagree in 3 places, including study duration. Each
one is listed with its page references in the report.` Take the label from the
`label` on the matching entry in `resolved_fields`, falling back to `field_id`.

**This sentence is the only report content the page displays.** It exists so
the analyst knows what is waiting for them before they open a twenty-page PDF.
Do not add a list of the disagreements, do not add their values, do not add
their page numbers, and do not add an expander containing them.

If `extraction.json` is missing, unreadable, or contains an empty
`resolved_fields`, skip the cards and the sentence, show one line of text
saying the run finished but produced no extracted fields, and still show the
downloads. A completed run showing three zeros with no explanation is the
current behaviour and is worse than saying nothing.

If `data["errors"]` is non-empty, keep the existing warning about failed
fields, reworded to `{n} field(s) failed during the run — see the JSON file.`

Then the downloads and the footer, described in section 8.

---

## 6. State C — a run that failed and produced nothing

The design principle for this state: one plain sentence at the top saying what
happened and what to do about it, and every technical detail collapsed behind
the run details expander. The analyst reading this screen is not the person who
will debug it.

`app.py` today distinguishes four failure conditions and prints a different
red block for each. Keep all four conditions exactly as they are — the logic
that produces them is correct and is not being changed. Replace only how they
are displayed. All four share the same layout, and differ only in the headline
and the sentence beneath it.

| Condition in `app.py` today | Headline | Sentence beneath |
| --- | --- | --- |
| `status["state"] == "failed"` | `Review stopped while {step label, lowercased}` — using the same dictionary as section 4, so `RECONCILE` gives `Review stopped while comparing the documents` | `No report was produced. Your files are still here — press try again. If it stops a second time, send the run identifier below to your platform contact.` |
| `status.json` says starting or running, but the CML job run reached a terminal failure | `The review stopped unexpectedly` | `The process ended before it could record why. No report was produced. Press try again.` |
| No `status.json` and the CML job run failed | `The review never started` | `Nothing ran, so nothing was read or extracted. Press try again.` |
| No `status.json` and the CML job run succeeded | `The review finished without producing a report` | `The job ended cleanly but wrote no results. Treat this as a failure. Send the run identifier below to your platform contact.` |

The second headline does not name a step on purpose: the process died without
recording where it was, so naming one would be a guess.

Below the headline block, in this order:

- The list of uploaded files, in the same visual treatment as the per-document
  list in section 4, showing how far each one got.
- Two buttons. `Try again`, described below. `Start over with different files`,
  which clears session state and returns to the upload state.
- The run details expander from section 8, which in this state also contains
  the node the run stopped at, the CML status string, and the raw error text
  from `status["error"]` — the same string `app.py` prints in red across the
  page today. Beneath it, one caption: `Full logs are in the Job Runs tab of
  the Cloudera AI project.`

**`Try again` must not ask the user to upload a 148-page protocol a second
time.** Implement it as: create a new run identifier, copy the files from the
failed run's `inputs/` directory into the new run's `inputs/` directory, then
call `trigger_job()` with the new identifier. Copy rather than reuse the
existing directory, so that one run identifier continues to mean one attempt,
which is what the per-run audit record assumes. This is filesystem work inside
`app.py` using `shutil.copy2`; it needs no change to `run_job.py` or to any
engine code.

If the failed run's `inputs/` directory is missing or empty, hide `Try again`
and show only `Start over with different files`.

---

## 7. State D — a run that finished with a document missing

This is not a failure and must not be drawn in red. `status["state"]` is
`completed`, a report exists and is downloadable, but at least one entry in
`status["documents"]` has state `failed`, so the report was built from less
than the full set of documents.

Draw state B exactly as section 5 describes, with two additions.

The first line changes from a green tick to an amber one, and reads
`Review complete, with 1 document missing` — the count being the number of
documents whose state is `failed`.

Directly beneath the three metric cards, and above the disagreement sentence,
add one `st.warning` naming the affected files: `Example_Protocol_2.pdf could
not be read, so the report was built from the remaining document only. Fields
that would have come from it are marked not found.`

Do not suppress the downloads, do not suppress the metric cards, and do not
attempt to describe which specific fields were affected — that information is
in the report.

---

## 8. Downloads and footer

Keep the three `st.download_button` calls and the `_DOWNLOADS` tuple exactly as
they are, including the ordering comment explaining why the PDF comes first and
the JSON last, and including the `not produced` caption when a file is absent.
Keep `use_container_width=True` and the `{run_id}-{filename}` download names.
Downloads appear in states B and D, and not in state C.

Below the downloads, a divider and a footer row containing:

- an `st.expander` labelled `Run details`, holding the run identifier, the job
  run identifier, the run directory path, and — in state C only — the node,
  the CML status, and the raw error text
- a button labelled `Start another review` in states B and D, which clears
  `run_id`, `job_triggered` and `job_run_id` from `st.session_state` and calls
  `st.rerun()`. The application currently has no way to start a second run
  without a browser refresh.

---

## 9. Sidebar

The sidebar currently draws the privacy mode, the external services warning,
and one caption per model role, from `describe_active_routing()`. It is the
loudest thing on the page and most of it is operator information.

Reduce it to: a heading reading `Environment`, the privacy mode indicator
(`st.success` for private mode, `st.warning` otherwise, with the existing
wording), the external-services `st.error` and its accompanying caption about
synthetic documents when `external_services` is non-empty, and then an
`st.expander` labelled `Model routing` containing the per-role bindings and the
existing caption pointing at `config/models.yaml`.

The privacy warning stays visible because it genuinely matters. The per-role
bindings go behind the expander because an analyst never needs them.

Do not change `describe_active_routing()` or anything it reads.

If a `.streamlit/config.toml` is needed to set the theme's base and accent
colour, that file may be created. Nothing else.

---

## 10. Wording

Every user-facing string uses ordinary words, sentence case, and no exclamation
marks. Specifically:

- `Start review`, not `Submit for Processing`
- `Reading documents`, not `INGEST`
- `Review complete`, not `Pipeline complete.`
- `Fields filled`, not `Confirmed`
- `Need a human check`, not `Needs review`
- `Disagreements found`, not `Disagreements to decide`
- `could not be read`, not `failed`, in the per-document list
- `Review stopped while comparing the documents`, not
  `Pipeline failed: KeyError...`

The raw error text still appears, unchanged, inside the run details expander.
It moves; it is not removed. The person who needs it is troubleshooting and
needs the detail.

---

## 11. How to check the result

Run the application and confirm all of the following:

1. The page shows no numbered section headings.
2. Uploading two PDFs and pressing `Start review` writes the inputs and
   triggers the job in one action, with no second button.
3. While the run is going, the status panel shows a plain-English step name,
   a progress bar, and one row per document.
4. On completion, the three summary numbers appear with a denominator on the
   first, and the disagreement sentence appears when there are disagreements.
5. The three download buttons produce the same three files with the same
   names as before this change.
6. `Start another review` returns the page to the upload state without a
   browser refresh.
7. A failed run shows a plain-English headline naming the step, keeps the
   uploaded files visible, offers `Try again`, and holds the raw error text
   inside the run details expander rather than printing it across the page.
8. `Try again` starts a new run without re-uploading the files.
9. `git status` shows exactly one modified file, `app.py`, plus at most a new
   `.streamlit/config.toml`. If it shows anything else, that is a defect in
   this implementation, not an improvement.
