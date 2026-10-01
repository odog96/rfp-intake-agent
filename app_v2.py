"""CAI Streamlit Application — RFP Intake Agent UI."""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from string import Template

import streamlit as st

# Resolve every relative path in Settings — config/models.yaml, config/fields.yaml,
# runs/ — against the project root rather than whatever directory the Cloudera AI
# Application was launched from. run_job.py does the same thing; without it here
# the application read /home/cdsw/config/models.yaml and wrote run folders to
# /home/cdsw/runs/ while the CML Job used the project's own runs/ directory.
# Must run before get_settings() below.
#
# Streamlit defines __file__, so the script's own directory is the answer here.
# The fallback covers a runtime that does not (CML runs job scripts through an
# IPython kernel where __file__ is absent — see the same note in run_job.py) and
# searches for the project rather than hardcoding where it was cloned, because an
# AMP-deployed project puts the repository in /home/cdsw itself.
try:
    _PROJECT_ROOT = Path(__file__).resolve().parent
except NameError:  # pragma: no cover - depends on the CML runtime
    from rfp_intake.config.paths import find_project_root

    _PROJECT_ROOT = find_project_root()
os.chdir(_PROJECT_ROOT)

from rfp_intake.config.settings import get_settings  # noqa: E402
from rfp_intake.job.args import RUN_ID_ENV_VAR  # noqa: E402
from rfp_intake.job.cml_status import classify_cml_status  # noqa: E402
from rfp_intake.llm.discovery import describe_active_routing  # noqa: E402
from rfp_intake.llm.health import probe_model_service  # noqa: E402

settings = get_settings()

st.set_page_config(page_title="RFP Intake Agent", layout="wide")


def get_cml_client():  # type: ignore[no-untyped-def]
    """Get authenticated CML API client."""
    import cmlapi
    return cmlapi.default_client()


def find_job_id(client, project_id: str) -> str:  # type: ignore[no-untyped-def]
    """Find the pipeline job by name."""
    jobs = client.list_jobs(project_id=project_id)
    for job in jobs.jobs:
        if job.name == settings.job_name:
            return job.id
    raise RuntimeError(
        f"CML Job '{settings.job_name}' not found. "
        "Create it in the CML UI or via YAML manifest first."
    )


def trigger_job(run_id: str) -> str:
    """Trigger the CML Job with the given run_id. Returns job_run_id."""
    import cmlapi

    client = get_cml_client()
    project_id = os.environ["CDSW_PROJECT_ID"]
    job_id = find_job_id(client, project_id)

    # The run id travels as an environment variable, not just as an argument.
    # CML runs run_job.py through an IPython kernel that puts its own
    # "-f <connection-file>" into sys.argv, so sys.argv[1] is "-f" and the job
    # failed with "No inputs directory: runs/-f/inputs". `arguments` is kept so a
    # run triggered from the CML UI still works.
    run_request = cmlapi.CreateJobRunRequest(
        project_id=project_id,
        job_id=job_id,
        arguments=run_id,
        environment={RUN_ID_ENV_VAR: run_id},
    )
    job_run = client.create_job_run(
        body=run_request,
        project_id=project_id,
        job_id=job_id,
    )
    return job_run.id


def check_job_run_status() -> tuple[str | None, str | None]:
    """Return (raw CML status, error message). Exactly one is set.

    Swallowing the error here and returning None was half of why a failed job run
    left this page saying "Waiting for pipeline to start..." forever: an
    unreachable API and a healthy queued job produced the same answer.
    """
    job_run_id = st.session_state.get("job_run_id")
    if not job_run_id:
        return None, None
    try:
        client = get_cml_client()
        project_id = os.environ["CDSW_PROJECT_ID"]
        job_id = find_job_id(client, project_id)
        run = client.get_job_run(
            project_id=project_id,
            job_id=job_id,
            run_id=job_run_id,
        )
        return run.status, None
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not raised
        return None, f"{type(exc).__name__}: {exc}"


# The nine pipeline steps, in order, with the words an analyst sees instead of
# the node name the engine writes. The position in this tuple is what the
# "step 6 of 9" caption counts, which is why DONE and ERROR are not in it.
_STEPS = (
    ("INGEST", "Reading documents"),
    ("CLASSIFY", "Identifying document types"),
    ("PLAN", "Planning the extraction"),
    ("EXTRACT", "Pulling out study details"),
    ("NORMALIZE", "Standardising values"),
    ("RECONCILE", "Comparing the documents"),
    ("ADJUDICATE", "Resolving disagreements"),
    ("DERIVE", "Calculating derived values"),
    ("GATE", "Final checks"),
)

# One dictionary, used both for the running panel's label and for the headline on
# a failed run. A node name that is not here falls back to the raw value, so a
# node added by another workstream changes the wording but never crashes the page.
_NODE_LABELS = dict(_STEPS)
# Written by the job before INGEST, while it checks that the model service
# answers at all. Not one of the nine steps, so it is not in _STEPS.
_NODE_LABELS["PREFLIGHT"] = "Checking the model service"
_NODE_LABELS["DONE"] = "Review complete"
_NODE_LABELS["ERROR"] = "Run failed"

_STEP_POSITION = {node: position for position, (node, _) in enumerate(_STEPS, start=1)}

# Plain words for the per-document rows. The engine's own state names are
# internal, and so is parser_rung, which is deliberately not shown.
_DOC_STATE_WORDS = {
    "parsed": "read",
    "parsing": "reading",
    "pending": "waiting",
    "failed": "could not be read",
}


def _step_label(node: str | None) -> str:
    """The analyst-facing name of a pipeline node."""
    if not node:
        return "Working"
    return _NODE_LABELS.get(node, node)


# --- Stage cards -------------------------------------------------------------
#
# Streamlit has no card widget, so the nine stages are one block of HTML written
# in a single st.html call. One call and not nine: Streamlit wraps every element
# it draws in its own container, so nine calls would put nine gaps between the
# cards that no stylesheet of ours could close.
#
# Everything here is inline. No stylesheet file, no font download, no custom
# Streamlit component — the Cloudera AI Application is served on 127.0.0.1
# behind the reverse proxy named by `subdomain:` in .project-metadata.yaml, so a
# component's own JavaScript endpoint may not be reachable, and a customer
# network may block an outbound font request. The font stack below is the one
# Streamlit already uses, named rather than fetched.
#
# Every class and the keyframes name is prefixed `rfp-` so none of it can
# collide with a Streamlit class now or after a Streamlit upgrade.

_BADGE_WORDS = {
    "done": "Done",
    "running": "Running",
    "waiting": "Waiting",
    "not_run": "Not run",
    "failed": "Failed",
}

_DARK_PALETTE = {
    "panel": "#161a23",
    "panel_active": "#1c212c",
    "line": "#2a3140",
    "text": "#fafafa",
    "dim": "#6b7484",
    "error_text": "#fca5a5",
}

_LIGHT_PALETTE = {
    "panel": "#ffffff",
    "panel_active": "#f6f8fa",
    "line": "#dfe3e8",
    "text": "#13171f",
    "dim": "#7a8494",
    "error_text": "#b42318",
}


def _stage_palette() -> dict[str, str]:
    """Card colours for the theme the browser is actually showing.

    The colours live in a <style> block, so unlike a built-in widget the cards
    cannot inherit the theme — they have to be told. `st.context.theme` reports
    the rendered theme, which is the only thing that knows the answer when the
    viewer is on "Use system setting" and this project configures no theme of its
    own (there is no .streamlit/config.toml). Falls back to the configured base,
    then to dark, which is what 8-27-app-screenshot.png shows.
    """
    rendered: str | None = None
    try:
        rendered = st.context.theme.type
    except Exception:  # noqa: BLE001 - no script run context, or an older runtime
        rendered = None
    if rendered not in ("light", "dark"):
        rendered = st.get_option("theme.base") or "dark"
    return _LIGHT_PALETTE if rendered == "light" else _DARK_PALETTE


_STAGE_CSS = Template("""
<style>
.rfp-stages { display: flex; flex-direction: column; gap: 8px; margin: 4px 0 2px; }
.rfp-card {
  display: grid; grid-template-columns: 32px 1fr auto; align-items: center;
  gap: 14px; padding: 11px 15px;
  background: $panel; color: $text;
  border: 1px solid $line; border-left: 3px solid $line; border-radius: 8px;
  font-family: "Source Sans Pro", -apple-system, BlinkMacSystemFont, "Segoe UI",
               Roboto, sans-serif;
}
.rfp-num {
  width: 25px; height: 25px; border-radius: 50%; display: grid; place-items: center;
  font-size: 0.76rem; font-weight: 600;
  background: $panel_active; color: $dim; border: 1px solid $line;
}
.rfp-label { font-size: 0.95rem; font-weight: 500; line-height: 1.35; }
/* The engine's own name for the stage, so the wording on this page and the
   wording in the CML Job log are visibly the same thing. */
.rfp-engine {
  display: block; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.70rem; color: $dim; letter-spacing: 0.04em; margin-top: 1px;
}
.rfp-badge {
  font-size: 0.70rem; font-weight: 600; letter-spacing: 0.06em;
  text-transform: uppercase; padding: 3px 9px; border-radius: 999px;
  border: 1px solid $line; color: $dim; white-space: nowrap;
}
.rfp-done { border-left-color: #21c55d; }
.rfp-done .rfp-num,
.rfp-done .rfp-badge {
  color: #21c55d; border-color: rgba(33,197,93,0.40); background: rgba(33,197,93,0.12);
}
.rfp-running { border-left-color: #3b82f6; background: $panel_active; }
.rfp-running .rfp-label { font-weight: 600; }
.rfp-running .rfp-num,
.rfp-running .rfp-badge {
  color: #3b82f6; border-color: rgba(59,130,246,0.45); background: rgba(59,130,246,0.12);
}
.rfp-waiting, .rfp-not_run { opacity: 0.55; }
.rfp-failed { border-left-color: #ef4444; background: rgba(239,68,68,0.07); }
.rfp-failed .rfp-num,
.rfp-failed .rfp-badge {
  color: #ef4444; border-color: rgba(239,68,68,0.45); background: rgba(239,68,68,0.12);
}
.rfp-dot {
  display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: #3b82f6; margin-right: 7px; vertical-align: 1px;
  animation: rfp-pulse 1.4s ease-in-out infinite;
}
@keyframes rfp-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
@media (prefers-reduced-motion: reduce) { .rfp-dot { animation: none; } }
.rfp-err {
  grid-column: 2 / -1; color: $error_text; font-size: 0.85rem; line-height: 1.5;
  border-top: 1px solid rgba(239,68,68,0.28); margin-top: 10px; padding-top: 10px;
}
.rfp-err code {
  display: block; margin-top: 6px; color: $dim; white-space: pre-wrap;
  word-break: break-word; background: none; padding: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.72rem;
}
.rfp-divider {
  color: $dim; font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase;
  margin: 14px 0 6px;
}
</style>
""")


def _stage_states(node: str | None, run_state: str | None) -> list[str]:
    """One state word per stage, in `_STEPS` order.

    status.json carries a single node name and nothing else per stage, so
    everything before the named stage is finished, everything after it has not
    started, and `_STEP_POSITION` (built from `_STEPS`) is what turns the name
    into that split. A node name the application does not recognise yields all
    nine waiting rather than an exception, which is the same choice
    `_step_label` makes.
    """
    failed = run_state == "failed"
    if run_state == "completed" or node == "DONE":
        return ["done"] * len(_STEPS)

    position = _STEP_POSITION.get(node or "")
    if position is None:
        # PREFLIGHT, ERROR, or a node from another workstream. Nothing in the
        # nine can be called finished.
        return ["not_run" if failed else "waiting"] * len(_STEPS)

    states: list[str] = []
    for index in range(1, len(_STEPS) + 1):
        if index < position:
            states.append("done")
        elif index == position:
            states.append("failed" if failed else "running")
        else:
            states.append("not_run" if failed else "waiting")
    return states


def _card_html(
    marker: str, label: str, engine: str, state: str, *, error: str | None = None
) -> str:
    """One card. `marker` is what sits in the circle — a tick, a cross, a number."""
    badge = _BADGE_WORDS[state]
    dot = '<span class="rfp-dot"></span>' if state == "running" else ""
    # Both of these are written by the engine into status.json, never typed by a
    # user, but they are being put into HTML, so they are escaped anyway.
    error_block = ""
    if error:
        error_block = f'<div class="rfp-err"><code>{escape(error)}</code></div>'
    return (
        f'<div class="rfp-card rfp-{state}">'
        f'<span class="rfp-num">{marker}</span>'
        f'<span class="rfp-label">{escape(label)}'
        f'<span class="rfp-engine">{escape(engine)}</span></span>'
        f'<span class="rfp-badge">{dot}{badge}</span>'
        f"{error_block}"
        "</div>"
    )


def _stage_cards(
    node: str | None, run_state: str | None, *, error: str | None = None
) -> None:
    """Draw the nine stages as cards, plus the PREFLIGHT card when it applies.

    PREFLIGHT ("Checking the model service") runs before INGEST and is not one of
    the nine, so it gets its own card above them and a divider, rather than being
    numbered as a tenth stage. It is drawn only while it is the current node —
    once INGEST starts there is nothing useful left to say about it.
    """
    palette = _stage_palette()
    parts = [_STAGE_CSS.substitute(palette)]

    if node == "PREFLIGHT":
        preflight_state = "failed" if run_state == "failed" else "running"
        parts.append('<div class="rfp-stages">')
        parts.append(
            _card_html(
                "✕" if preflight_state == "failed" else "⚡",
                _NODE_LABELS["PREFLIGHT"],
                "PREFLIGHT",
                preflight_state,
                error=error,
            )
        )
        parts.append("</div>")
        parts.append(
            '<div class="rfp-divider">'
            f"{'Never started' if run_state == 'failed' else 'Then nine stages'}"
            "</div>"
        )
        # The nine carry no error text; the failure belongs on the card it
        # happened on, which is PREFLIGHT above.
        error = None

    states = _stage_states(node, run_state)
    parts.append('<div class="rfp-stages">')
    for position, ((engine, label), state) in enumerate(
        zip(_STEPS, states, strict=True), start=1
    ):
        marker = {"done": "✓", "failed": "✕"}.get(state, str(position))
        parts.append(
            _card_html(
                marker, label, engine, state, error=error if state == "failed" else None
            )
        )
    parts.append("</div>")

    st.html("".join(parts))


def _document_rows(documents: list[dict]) -> None:
    """One row per document: the file name, and how far it got, in plain words."""
    for doc in documents:
        state = doc.get("state", "")
        words = _DOC_STATE_WORDS.get(state, state)
        pages = doc.get("pages")
        if pages and state in ("parsed", "parsing"):
            words = f"{words}, {pages} pages"
        name_col, state_col = st.columns([3, 1])
        with name_col:
            st.text(doc.get("name", "unnamed document"))
        with state_col:
            st.caption(words)


def _format_elapsed(started_at: str | None, heartbeat_at: str | None) -> str | None:
    """How long the run took, or None when the timestamps cannot be read."""
    try:
        start = datetime.fromisoformat(started_at or "")
        end = datetime.fromisoformat(heartbeat_at or "")
    except (TypeError, ValueError):
        return None
    seconds = int((end - start).total_seconds())
    if seconds < 0:
        return None
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes} min {remainder:02d} s" if minutes else f"{remainder} s"


def _completion_line(status: dict, documents: list[dict]) -> str:
    """`2 documents, 160 pages, 4 min 12 s` — omitting whatever is unavailable.

    `documents` is the documents the report was actually built from, so a run
    that could not read one of its files reports the one it did read, not both.

    status.json carries an empty `documents` list on every run written so far, so
    the document and page counts drop out and only the elapsed time remains.
    Stating a confident "0 documents, 0 pages" would be worse than saying less.
    """
    parts: list[str] = []
    if documents:
        count = len(documents)
        parts.append(f"{count} document{'' if count == 1 else 's'}")
        total_pages = sum(doc.get("pages") or 0 for doc in documents)
        if total_pages:
            parts.append(f"{total_pages} pages")
    elapsed = _format_elapsed(status.get("started_at"), status.get("heartbeat_at"))
    if elapsed:
        parts.append(elapsed)
    return ", ".join(parts)


def _disagreement_sentence(fields: list[dict], disagreements: list[dict]) -> str:
    """One sentence saying how many disagreements there are and naming the first.

    The only report content this page shows. It exists so the analyst knows what
    is waiting for them before they open a twenty-page PDF.
    """
    count = len(disagreements)
    places = "1 place" if count == 1 else f"{count} places"
    first_id = disagreements[0].get("field_id")
    label = next(
        (f.get("label") for f in fields if f.get("field_id") == first_id and f.get("label")),
        first_id or "one of the fields",
    )
    return (
        f"The RFP and the protocol disagree in {places}, including {label}. "
        "Each one is listed with its page references in the report."
    )


# Order matters: report.pdf is what the analyst reads and is offered first.
# extraction.json is the machine-readable one a downstream budget service
# consumes (ARCHITECTURE.md §4.10), so it is offered but listed last.
_DOWNLOADS = (
    ("report.pdf", "Report (PDF)", "application/pdf"),
    ("report.xlsx", "Report (Excel)",
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("extraction.json", "Extracted data (JSON)", "application/json"),
)


def _reset_run() -> None:
    """Return the page to the upload state without a browser refresh."""
    for key in ("run_id", "job_triggered", "job_run_id"):
        st.session_state.pop(key, None)
    st.rerun()


def _run_details(
    run_path: Path,
    *,
    node: str | None = None,
    cml_status: str | None = None,
    error: str | None = None,
) -> None:
    """The operator's detail, collapsed. Section 6's raw error text moves here."""
    with st.expander("Run details"):
        st.markdown(
            f"- Run identifier: `{run_path.name}`\n"
            f"- Job run identifier: `{st.session_state.get('job_run_id') or 'not recorded'}`\n"
            f"- Run directory: `{run_path}`"
        )
        if node:
            st.markdown(f"- Stopped at: `{node}`")
        if cml_status:
            st.markdown(f"- CML status: `{cml_status}`")
        if error:
            st.code(error)
        if node or cml_status or error:
            st.caption("Full logs are in the Job Runs tab of the Cloudera AI project.")


def _downloads(run_path: Path) -> None:
    """The three finished files, in the order the analyst wants them."""
    cols = st.columns(len(_DOWNLOADS))
    for col, (filename, label, mime) in zip(cols, _DOWNLOADS, strict=True):
        path = run_path / filename
        with col:
            if not path.exists():
                st.caption(f"{label} — not produced")
                continue
            st.download_button(
                label=label,
                data=path.read_bytes(),
                file_name=f"{run_path.name}-{filename}",
                mime=mime,
                use_container_width=True,
            )


def _footer(run_path: Path) -> None:
    """Run details on the left, a way to start again on the right."""
    st.divider()
    details_col, restart_col = st.columns([3, 1])
    with details_col:
        _run_details(run_path)
    with restart_col:
        if st.button("Start another review", use_container_width=True):
            _reset_run()


def _try_again(failed_run_id: str) -> None:
    """Re-run the same documents under a new run identifier.

    The files are copied rather than the directory reused, so one run identifier
    still means one attempt — which is what the per-run audit record assumes.
    Asking an analyst to upload a 148-page protocol again is not an option.
    """
    source = settings.run_dir / failed_run_id / "inputs"
    new_run_id = f"r-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    target = settings.run_dir / new_run_id / "inputs"
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.iterdir()):
        if path.is_file():
            shutil.copy2(path, target / path.name)

    job_run_id = trigger_job(new_run_id)
    st.session_state.run_id = new_run_id
    st.session_state.job_triggered = True
    st.session_state.job_run_id = job_run_id


def _has_inputs(run_id: str) -> bool:
    """Whether a failed run still has the files needed to retry it."""
    inputs_dir = settings.run_dir / run_id / "inputs"
    return inputs_dir.is_dir() and any(p.is_file() for p in inputs_dir.iterdir())


def _failure_words(status: dict) -> tuple[str, str]:
    """The headline and the sentence for a run that wrote `state: failed`.

    `status["failure"]` is written by the job (job/status.py, RunFailure) and
    says what kind of failure this was. A model service that would not answer
    and a set of documents nothing could be extracted from produce the same
    empty report, and telling an analyst to check their files when an access
    key has expired sends the wrong person to look in the wrong place — which
    is exactly what happened on runs r-20260831-150720 to r-20260901-140857.

    A status.json without the `failure` key — any run written before the key
    existed — falls back to the original wording, unchanged.
    """
    failure = status.get("failure") or {}
    kind = failure.get("kind")
    sentence = " ".join(part for part in (failure.get("reason"), failure.get("action")) if part)

    if kind == "model_service" and sentence:
        service = failure.get("service") or "the model service"
        return f"Review stopped — no answer from {service}", sentence

    if kind == "no_extraction" and sentence:
        return "Review stopped — nothing could be extracted", sentence

    return (
        f"Review stopped while {_step_label(status.get('node')).lower()}",
        "No report was produced. Your files are still here — press try again. If "
        "it stops a second time, send the run identifier below to your platform "
        "contact.",
    )


def _failure_view(
    run_path: Path,
    headline: str,
    sentence: str,
    *,
    documents: list[dict],
    node: str | None = None,
    cml_status: str | None = None,
    error: str | None = None,
) -> None:
    """State C. One plain sentence at the top, every technical detail collapsed.

    The analyst reading this screen is not the person who will debug it.
    """
    st.error(f"**{headline}**\n\n{sentence}")

    # The cards answer the question the headline cannot: how far it got before it
    # stopped. Drawn only when status.json named a stage — the branches that pass
    # no node (a CML run discarded as ENGINE_SKIPPED, a job that never started)
    # would otherwise show nine identical "not run" rows, which says nothing.
    if node:
        _stage_cards(node, "failed", error=error)

    if documents:
        _document_rows(documents)
        st.write("")

    run_id = run_path.name
    retry_col, restart_col, _ = st.columns([1, 1, 2])
    if _has_inputs(run_id):
        with retry_col:
            if st.button("Try again", type="primary", use_container_width=True):
                try:
                    _try_again(run_id)
                except Exception as exc:  # noqa: BLE001 - shown, not raised
                    st.error(f"Could not start the review again: {exc}")
                else:
                    st.rerun()
    with restart_col:
        if st.button("Start over with different files", use_container_width=True):
            _reset_run()

    _run_details(run_path, node=node, cml_status=cml_status, error=error)


def _completion_view(run_path: Path, status: dict) -> None:
    """States B and D. What came back, then the files."""
    documents = status.get("documents") or []
    missing = [doc for doc in documents if doc.get("state") == "failed"]
    read = [doc for doc in documents if doc.get("state") != "failed"]

    detail = _completion_line(status, read)
    if missing:
        count = len(missing)
        headline = f"Review complete, with {count} document{'' if count == 1 else 's'} missing"
        st.warning(f"{headline}{' · ' + detail if detail else ''}", icon="✅")
    else:
        st.success(f"Review complete{' · ' + detail if detail else ''}", icon="✅")

    extraction_path = run_path / "extraction.json"
    data: dict | None = None
    if extraction_path.exists():
        try:
            data = json.loads(extraction_path.read_text())
        except (OSError, ValueError) as exc:  # noqa: BLE001 - summary is a nicety
            st.info(f"Could not summarise the results: {exc}")

    fields = (data or {}).get("resolved_fields") or []
    if data is not None and fields:
        disagreements = [
            c for c in (data.get("contradictions") or [])
            if c.get("verdict") in ("conflict", "reconcilable")
        ]
        filled = sum(1 for f in fields if f.get("status") in ("confirmed", "needs_review"))
        needs_check = sum(1 for f in fields if f.get("status") == "needs_review")

        filled_col, check_col, disagree_col = st.columns(3)
        with filled_col:
            # The denominator is the point: "38" alone does not tell the analyst
            # whether the run was complete, and "38 of 45" does.
            st.metric("Fields filled", f"{filled} of {len(fields)}")
        with check_col:
            st.metric("Need a human check", needs_check)
        with disagree_col:
            st.metric("Disagreements found", len(disagreements))

        if missing:
            names = ", ".join(doc.get("name", "a document") for doc in missing)
            st.warning(
                f"{names} could not be read, so the report was built from the "
                f"remaining document{'' if len(read) == 1 else 's'} only. Fields "
                "that would have come from it are marked not found."
            )

        if disagreements:
            st.warning(_disagreement_sentence(fields, disagreements))

        errors = data.get("errors") or []
        # `kind` is written by the engine (domain/schemas.py, RunError). A step
        # that never got an answer from the model service is a different event
        # from one whose answer was rejected, and only the first is worth
        # telling someone to go and fix. Errors written before `kind` existed
        # default to call_failed, which is what they were.
        unanswered = [e for e in errors if e.get("kind", "call_failed") == "call_failed"]
        if unanswered:
            st.warning(
                f"{len(unanswered)} step(s) could not get an answer from the model "
                "service, so some fields are marked not found even though the "
                "documents may say otherwise. Check the model connection in the "
                "sidebar, then run the review again."
            )
        elif errors:
            st.warning(
                f"{len(errors)} field(s) failed during the run — see the JSON file."
            )
    else:
        # A completed run showing three zeros with no explanation is worse than
        # saying nothing, so the numbers are skipped rather than drawn empty.
        st.write("The run finished but produced no extracted fields.")

    # Collapsed, not open: on a finished run the nine stages are confirmation
    # that everything ran, which is worth being able to check and not worth
    # pushing the download buttons down the page for.
    with st.expander("All nine stages"):
        _stage_cards(status.get("node"), status.get("state"))

    _downloads(run_path)
    _footer(run_path)


# --- Session state init ---
if "run_id" not in st.session_state:
    st.session_state.run_id = None
if "job_triggered" not in st.session_state:
    st.session_state.job_triggered = False
if "job_run_id" not in st.session_state:
    st.session_state.job_run_id = None


# --- Sidebar: the environment this run is using ---
# The privacy warning stays visible because it genuinely matters. The per-role
# bindings go behind an expander because an analyst never needs them; making the
# routing visible at all is the prerequisite for changing it safely, and enough
# to catch a demo accidentally pointed at an external provider.
with st.sidebar:
    st.subheader("Environment")
    try:
        routing_info = describe_active_routing()
    except Exception as exc:  # noqa: BLE001 - never block the UI on config display
        st.error(f"Routing unavailable: {exc}")
    else:
        mode = routing_info["privacy_mode"]
        external = routing_info["external_services"]
        if mode == "private":
            st.success("Private mode — inference stays in-environment")
        else:
            st.warning(f"{mode} mode — document text may leave the environment")
        if external:
            st.error(f"External services in use: {', '.join(external)}")
            st.caption("Non-sensitive/synthetic documents only. Never production data.")

        with st.expander("Model routing"):
            for role, binding in routing_info["roles"].items():
                st.caption(f"**{role}** → `{binding['provider']}` / `{binding['model']}`")
            st.caption("Edit `config/models.yaml` to change bindings.")

            # On a button, never on every rerun: this page reruns every two
            # seconds while a run is going, and a check that ran on each of
            # those would be a call to a paid service twice a second.
            if st.button("Check the model connection", use_container_width=True):
                with st.spinner("Asking the model service one short question"):
                    probe = probe_model_service()
                if probe is None:
                    st.success("The model service answered.")
                else:
                    st.error(f"{probe.reason} {probe.action}")
                    st.caption(probe.detail)


st.title("RFP intake agent")

run_id = st.session_state.run_id
run_path = settings.run_dir / run_id if run_id else None

# Read both witnesses before drawing anything, so the page can pick one of the
# four states in one place. Per ARCHITECTURE.md §6.3 the CML Jobs API is
# authoritative for process liveness and status.json for work progress: a run is
# only finished when both agree, because status.json alone can go stale if the
# job process dies without writing a terminal state (crash before the
# try/except, OOM kill, container restart, ...).
status: dict | None = None
cml_status: str | None = None
cml_error: str | None = None
cml_state = "unknown"
if run_id and st.session_state.job_triggered:
    cml_status, cml_error = check_job_run_status()
    cml_state = classify_cml_status(cml_status)
    status_path = run_path / "status.json"
    if status_path.exists():
        # Deliberately unguarded, as it has always been: a status.json that
        # cannot be parsed is a real fault and must not be quietly reported as
        # "the review never started", which is what swallowing it here would do
        # to a run that is actually still going.
        status = json.loads(status_path.read_text())

cml_terminal_failure = cml_state == "failed"
# A skipped run never started and never will, so it ends the wait like a failure.
cml_skipped = cml_state == "skipped"
finished = bool(status and status.get("state") in ("completed", "failed"))
terminal = finished or (
    st.session_state.job_triggered
    and (cml_terminal_failure or cml_skipped or (status is None and cml_state == "succeeded"))
)


# --- State A: upload, and watch a run that is going ---
if not terminal:
    st.caption("Upload an RFP and its protocol. You get a review report back.")

    uploaded_files = st.file_uploader(
        "Upload the RFP and the protocol",
        type=["pdf"],
        accept_multiple_files=True,
    )

    if st.button("Start review", type="primary", disabled=not uploaded_files):
        new_run_id = f"r-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        inputs_dir = settings.run_dir / new_run_id / "inputs"
        try:
            inputs_dir.mkdir(parents=True, exist_ok=True)
            for f in uploaded_files:
                (inputs_dir / f.name).write_bytes(f.getvalue())
            # One action, not two. The files are already written when this runs,
            # so a failure here leaves them in place and the user can press the
            # button again without re-uploading.
            job_run_id = trigger_job(new_run_id)
        except Exception as exc:  # noqa: BLE001 - shown to the user, not raised
            st.error(f"Could not start the review: {exc}")
        else:
            st.session_state.run_id = new_run_id
            st.session_state.job_triggered = True
            st.session_state.job_run_id = job_run_id
            st.rerun()

    if run_id and st.session_state.job_triggered:
        if cml_error:
            st.warning(
                f"Cannot reach the CML Jobs API to check the job run: {cml_error}. "
                "Progress below comes from status.json alone and may be stale."
            )
        elif cml_state == "unknown" and cml_status:
            st.warning(
                f"Unrecognised job run status from CML: {cml_status!r}. Treating it as "
                "still running — check the Job Runs tab in the CML UI."
            )

        node = status.get("node") if status else None

        headline_col, meta_col = st.columns([3, 2])
        with headline_col:
            st.subheader(
                "Reviewing your documents" if status else "Starting the review",
                anchor=False,
            )
        with meta_col:
            # The run identifier and the position, on one line, right-aligned by
            # being the narrower column. Both halves are drawn only when their
            # source exists: status.json carries no `progress` key today, so the
            # field count never appears, and the position is absent during
            # PREFLIGHT because PREFLIGHT is not one of the nine stages.
            meta_parts: list[str] = [f"`{run_id}`"]
            if status:
                progress = status.get("progress") or {}
                done = progress.get("tasks_done", 0)
                total = progress.get("tasks_total", 0)
                if total > 0:
                    meta_parts.append(f"{done} of {total} fields extracted")
                position = _STEP_POSITION.get(node or "")
                if position:
                    meta_parts.append(f"step {position} of {len(_STEPS)}")
            else:
                meta_parts.append(f"CML status: {cml_status or 'unknown'}")
            st.caption(" · ".join(meta_parts))

        if status:
            _stage_cards(node, status.get("state"))
            _document_rows(status.get("documents") or [])
        else:
            # No status.json yet: the CML Job run exists but the pipeline has not
            # written its first heartbeat. Every stage is genuinely waiting, so
            # the cards are honest here rather than empty.
            _stage_cards(None, None)

        time.sleep(2)
        st.rerun()


# --- States B and D: the run finished ---
elif status and status.get("state") == "completed":
    _completion_view(run_path, status)


# --- State C: the run failed and produced nothing ---
# The four conditions below are exactly the four app.py has always distinguished.
# Only their presentation changes.
elif status and status.get("state") == "failed":
    node = status.get("node")
    headline, sentence = _failure_words(status)
    _failure_view(
        run_path,
        headline,
        sentence,
        documents=status.get("documents") or [],
        node=node,
        cml_status=cml_status,
        error=(status.get("failure") or {}).get("detail") or status.get("error"),
    )

elif status:
    # status.json still says starting or running, but the CML job run has already
    # reached a terminal failure state — the process died without getting a
    # chance to write "failed" itself. The headline does not name a step on
    # purpose: naming one would be a guess.
    _failure_view(
        run_path,
        "The review stopped unexpectedly",
        "The process ended before it could record why. No report was produced. "
        "Press try again.",
        documents=status.get("documents") or [],
        node=status.get("node"),
        cml_status=cml_status,
    )

elif cml_skipped:
    # CML runs one review at a time and discards a request that arrives while
    # another is going. Pressing the button twice is the way to get here.
    _failure_view(
        run_path,
        "The review never started — another review was already running",
        "Only one review runs at a time, so this one was discarded. Your files "
        "are still here — press try again once the other review has finished.",
        documents=[],
        cml_status=cml_status,
    )

elif cml_terminal_failure:
    _failure_view(
        run_path,
        "The review never started",
        "Nothing ran, so nothing was read or extracted. Press try again.",
        documents=[],
        cml_status=cml_status,
    )

else:
    _failure_view(
        run_path,
        "The review finished without producing a report",
        "The job ended cleanly but wrote no results. Treat this as a failure. "
        "Send the run identifier below to your platform contact.",
        documents=[],
        cml_status=cml_status,
    )
