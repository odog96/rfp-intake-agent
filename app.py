"""CAI Streamlit Application — RFP Intake Agent UI."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, UTC
from pathlib import Path

import streamlit as st

from rfp_intake.config.settings import get_settings

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

    run_request = cmlapi.CreateJobRunRequest(
        project_id=project_id,
        job_id=job_id,
        arguments=run_id,
    )
    job_run = client.create_job_run(
        body=run_request,
        project_id=project_id,
        job_id=job_id,
    )
    return job_run.id


def check_job_run_status() -> str | None:
    """Check the CML job run status via API. Returns state string or None."""
    try:
        client = get_cml_client()
        project_id = os.environ["CDSW_PROJECT_ID"]
        job_id = find_job_id(client, project_id)
        job_run_id = st.session_state.get("job_run_id")
        if not job_run_id:
            return None
        run = client.get_job_run(
            project_id=project_id,
            job_id=job_id,
            run_id=job_run_id,
        )
        return run.status
    except Exception:
        return None


# --- Session state init ---
if "run_id" not in st.session_state:
    st.session_state.run_id = None
if "job_triggered" not in st.session_state:
    st.session_state.job_triggered = False
if "job_run_id" not in st.session_state:
    st.session_state.job_run_id = None


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
    cml_status = check_job_run_status()
    cml_terminal_failure = bool(cml_status) and cml_status.lower() in (
        "failed", "timedout", "killed",
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
        if cml_terminal_failure:
            st.error(
                f"Job run failed (CML status: {cml_status}). "
                "Check the Job Runs tab in the CML UI for logs."
            )
        else:
            st.info("Waiting for pipeline to start...")
            time.sleep(2)
            st.rerun()
