"""CAI Streamlit Application — RFP Intake Agent UI."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

# Resolve every relative path in Settings — config/models.yaml, config/fields.yaml,
# runs/ — against the project root rather than whatever directory the Cloudera AI
# Application was launched from. run_job.py does the same thing on line 9; without
# it here the application read /home/cdsw/config/models.yaml and wrote run folders
# to /home/cdsw/runs/ while the CML Job used /home/cdsw/rfp-intake-agent/runs/.
# Must run before get_settings() below.
#
# __file__ is absent when CML runs a script through an IPython kernel (see the
# same note in run_job.py), so fall back to the known project path rather than
# raising NameError on startup.
try:
    _PROJECT_ROOT = Path(__file__).resolve().parent
except NameError:  # pragma: no cover - depends on the CML runtime
    _PROJECT_ROOT = Path("/home/cdsw/rfp-intake-agent")
os.chdir(_PROJECT_ROOT)

from rfp_intake.config.settings import get_settings  # noqa: E402
from rfp_intake.job.args import RUN_ID_ENV_VAR  # noqa: E402
from rfp_intake.job.cml_status import classify_cml_status  # noqa: E402
from rfp_intake.llm.discovery import describe_active_routing  # noqa: E402

settings = get_settings()

st.set_page_config(page_title="RFP Intake Agent", layout="wide")
st.title("RFP Intake Agent")


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


# Order matters: report.pdf is what the analyst reads and is offered first.
# extraction.json is the machine-readable one a downstream budget service
# consumes (ARCHITECTURE.md §4.10), so it is offered but listed last.
_DOWNLOADS = (
    ("report.pdf", "Report (PDF)", "application/pdf"),
    ("report.xlsx", "Report (Excel)",
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("extraction.json", "Extracted data (JSON)", "application/json"),
)


def _results_section(run_path) -> None:  # type: ignore[no-untyped-def]
    """Offer the finished outputs for download, and say what is in them."""
    st.header("4. Results")

    extraction_path = run_path / "extraction.json"
    if extraction_path.exists():
        try:
            data = json.loads(extraction_path.read_text())
            fields = data.get("resolved_fields") or []
            contradictions = data.get("contradictions") or []
            needs_decision = [
                c for c in contradictions
                if c.get("verdict") in ("conflict", "reconcilable")
            ]
            counts: dict[str, int] = {}
            for f in fields:
                counts[f.get("status", "unknown")] = counts.get(f.get("status", "unknown"), 0) + 1

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Confirmed", counts.get("confirmed", 0))
            with col2:
                st.metric("Needs review", counts.get("needs_review", 0))
            with col3:
                st.metric("Disagreements to decide", len(needs_decision))
            if data.get("errors"):
                st.warning(f"{len(data['errors'])} field(s) failed during the run — see the JSON.")
        except (OSError, ValueError) as exc:  # noqa: BLE001 - summary is a nicety
            st.info(f"Could not summarise the results: {exc}")

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
                file_name=f"{st.session_state.run_id}-{filename}",
                mime=mime,
                use_container_width=True,
            )


# --- Session state init ---
if "run_id" not in st.session_state:
    st.session_state.run_id = None
if "job_triggered" not in st.session_state:
    st.session_state.job_triggered = False
if "job_run_id" not in st.session_state:
    st.session_state.job_run_id = None


# --- Sidebar: model routing (read-only) ---
# Scaffold for the admin surface. Editing privacy_mode and per-role bindings from
# here needs a write-back path and an authorisation check on who counts as an
# admin, so for now this makes the active routing visible — which is the
# prerequisite for changing it safely, and enough to catch a demo accidentally
# pointed at an external provider.
with st.sidebar:
    st.subheader("Model routing")
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

        for role, binding in routing_info["roles"].items():
            st.caption(f"**{role}** → `{binding['provider']}` / `{binding['model']}`")

        st.caption("Edit `config/models.yaml` to change bindings.")


# --- Section 1: Upload ---
st.header("1. Upload Documents")
uploaded_files = st.file_uploader(
    "Upload RFP and Protocol PDFs",
    type=["pdf"],
    accept_multiple_files=True,
)

if st.button("Submit for Processing", disabled=not uploaded_files):
    run_id = f"r-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    inputs_dir = settings.run_dir / run_id / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    for f in uploaded_files:
        (inputs_dir / f.name).write_bytes(f.getvalue())

    st.session_state.run_id = run_id
    st.session_state.job_triggered = False
    st.success(f"Uploaded {len(uploaded_files)} file(s) to run `{run_id}`")


# --- Section 2: Trigger ---
if st.session_state.run_id and not st.session_state.job_triggered:
    st.header("2. Start Processing")
    if st.button("Launch Pipeline"):
        try:
            job_run_id = trigger_job(st.session_state.run_id)
            st.session_state.job_triggered = True
            st.session_state.job_run_id = job_run_id
            st.success("Job triggered — watching progress below.")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to trigger job: {e}")


# --- Section 3: Progress ---
if st.session_state.run_id and st.session_state.job_triggered:
    st.header("3. Pipeline Progress")

    status_path = settings.run_dir / st.session_state.run_id / "status.json"
    progress_container = st.empty()

    # Two sources, per ARCHITECTURE.md §6.3: the CML Jobs API is authoritative
    # for process liveness, status.json is authoritative for work progress.
    # A run is only "succeeded"/"failed" when both agree — status.json alone
    # can go stale if the job process dies without writing a terminal state
    # (crash before the try/except, OOM kill, container restart, ...).
    cml_status, cml_error = check_job_run_status()
    cml_state = classify_cml_status(cml_status)
    cml_terminal_failure = cml_state == "failed"

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

    if status_path.exists():
        status = json.loads(status_path.read_text())

        col1, col2 = st.columns(2)
        with col1:
            st.metric("State", status["state"].upper())
        with col2:
            st.metric("Current Node", status["node"])

        if status.get("progress"):
            done = status["progress"].get("tasks_done", 0)
            total = status["progress"].get("tasks_total", 0)
            if total > 0:
                st.progress(done / total, text=f"{done}/{total} tasks")

        if status.get("documents"):
            st.subheader("Documents")
            for doc in status["documents"]:
                st.text(f"  {doc['name']} — {doc['state']}"
                        + (f" (rung {doc['parser_rung']}, {doc['pages']}p)"
                           if doc.get("pages") else ""))

        if status["state"] == "completed":
            st.balloons()
            st.success("Pipeline complete.")
            _results_section(settings.run_dir / st.session_state.run_id)
        elif status["state"] == "failed":
            st.error(f"Pipeline failed: {status.get('error', 'unknown error')}")
        elif cml_terminal_failure:
            # status.json still says starting/running, but the CML job run
            # has already reached a terminal failure state — the process
            # died without getting a chance to write "failed" itself.
            st.error(
                f"Job process ended unexpectedly (CML status: {cml_status}) "
                f"while status.json still reported '{status['state']}' at "
                f"node {status['node']}. Check the Job Runs tab in the CML "
                "UI for logs."
            )
        else:
            time.sleep(2)
            st.rerun()
    else:
        # No status.json yet. The job may not have started writing, or it may
        # have died before it could — the CML Jobs API is the only witness.
        run_ref = (
            f"run id `{st.session_state.run_id}`, "
            f"job run `{st.session_state.get('job_run_id')}`"
        )
        if cml_terminal_failure:
            st.error(
                f"The job run failed before the pipeline wrote any status "
                f"(CML status: {cml_status}). Nothing ran. Open the Job Runs tab "
                f"in the CML UI for the log — {run_ref}."
            )
        elif cml_state == "succeeded":
            st.error(
                f"The job run finished but the pipeline never wrote a status file "
                f"at {status_path}. Treat this as a failure, not a success — "
                f"{run_ref}."
            )
        else:
            st.info(f"Waiting for the pipeline to start… (CML status: {cml_status or 'unknown'})")
            time.sleep(2)
            st.rerun()
