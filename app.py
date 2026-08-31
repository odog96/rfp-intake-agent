"""CAI Streamlit Application — RFP Intake Agent UI."""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

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

        if data.get("errors"):
            st.warning(
                f"{len(data['errors'])} field(s) failed during the run — see the JSON file."
            )
    else:
        # A completed run showing three zeros with no explanation is worse than
        # saying nothing, so the cards are skipped rather than drawn empty.
        st.write("The run finished but produced no extracted fields.")

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
finished = bool(status and status.get("state") in ("completed", "failed"))
terminal = finished or (
    st.session_state.job_triggered
    and (cml_terminal_failure or (status is None and cml_state == "succeeded"))
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
        with st.status(_step_label(node), expanded=True):
            _, id_col = st.columns([3, 1])
            with id_col:
                st.caption(f"`{run_id}`")

            if status:
                progress = status.get("progress") or {}
                done = progress.get("tasks_done", 0)
                total = progress.get("tasks_total", 0)
                if total > 0:
                    st.progress(done / total)

                # Both halves of this caption are drawn only when their source
                # exists. status.json currently carries no `progress` key, so
                # today only the step position appears.
                caption_parts: list[str] = []
                if total > 0:
                    caption_parts.append(f"{done} of {total} fields extracted")
                position = _STEP_POSITION.get(node or "")
                if position:
                    caption_parts.append(f"step {position} of {len(_STEPS)}")
                if caption_parts:
                    st.caption(" · ".join(caption_parts))

                _document_rows(status.get("documents") or [])
            else:
                st.caption(
                    "Waiting for the pipeline to start "
                    f"(CML status: {cml_status or 'unknown'})"
                )

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
    _failure_view(
        run_path,
        f"Review stopped while {_step_label(node).lower()}",
        "No report was produced. Your files are still here — press try again. If "
        "it stops a second time, send the run identifier below to your platform "
        "contact.",
        documents=status.get("documents") or [],
        node=node,
        cml_status=cml_status,
        error=status.get("error"),
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
