"""Dev harness: drive one pipeline run through the CML Jobs API and watch it
to a terminal state without going through the Streamlit app.

Polls both status sources every cycle and applies the resolution rule from
ARCHITECTURE.md §6.3: the CML Jobs API is authoritative for process
liveness, status.json is authoritative for work progress, and a run is only
reported "succeeded" when both agree. A run where status.json is stuck on a
non-terminal state while CML reports the job run has already failed/timed
out/been killed is reported as a desync, not as "still running" — that
mismatch is exactly the bug this script exists to catch fast.

Usage:
    python scripts/dev_run.py samples/Example*.pdf
    python scripts/dev_run.py --run-id r-debug-1 samples/*.pdf
    python scripts/dev_run.py --poll-interval 3 --timeout 300 samples/*.pdf
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rfp_intake.config.settings import get_settings  # noqa: E402

CML_TERMINAL_FAILURE = {"failed", "timedout", "killed"}
CML_TERMINAL_SUCCESS = {"succeeded"}
STATUS_JSON_TERMINAL = {"completed", "failed"}


def get_cml_client():  # type: ignore[no-untyped-def]
    import cmlapi

    return cmlapi.default_client()


def find_job_id(client, project_id: str, job_name: str) -> str:  # type: ignore[no-untyped-def]
    jobs = client.list_jobs(project_id=project_id)
    for job in jobs.jobs:
        if job.name == job_name:
            return job.id
    raise RuntimeError(
        f"CML Job {job_name!r} not found. Create it in the CML UI or via YAML manifest first."
    )


def trigger_job(client, project_id: str, job_id: str, run_id: str) -> str:  # type: ignore[no-untyped-def]
    import cmlapi

    run_request = cmlapi.CreateJobRunRequest(
        project_id=project_id, job_id=job_id, arguments=run_id,
    )
    job_run = client.create_job_run(body=run_request, project_id=project_id, job_id=job_id)
    return job_run.id  # type: ignore[no-any-return]


def get_cml_run_status(client, project_id: str, job_id: str, job_run_id: str) -> str | None:  # type: ignore[no-untyped-def]
    run = client.get_job_run(project_id=project_id, job_id=job_id, run_id=job_run_id)
    return run.status  # type: ignore[no-any-return]


def read_status_json(run_path: Path) -> dict | None:
    status_path = run_path / "status.json"
    if not status_path.exists():
        return None
    return json.loads(status_path.read_text())


def stage_inputs(run_path: Path, input_files: list[Path]) -> None:
    inputs_dir = run_path / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    for f in input_files:
        (inputs_dir / f.name).write_bytes(f.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_files", nargs="+", type=Path, help="PDFs to stage as run inputs")
    parser.add_argument("--run-id", default=None, help="Override generated run_id")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Seconds between polls")
    parser.add_argument("--timeout", type=float, default=600.0, help="Max seconds to wait")
    args = parser.parse_args()

    missing = [f for f in args.input_files if not f.exists()]
    if missing:
        print(f"Input file(s) not found: {missing}", file=sys.stderr)
        return 2

    settings = get_settings()
    run_id = args.run_id or f"r-dev-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    run_path = settings.run_dir / run_id

    print(f"[dev_run] staging {len(args.input_files)} file(s) into {run_path / 'inputs'}")
    stage_inputs(run_path, args.input_files)

    project_id = os.environ["CDSW_PROJECT_ID"]
    client = get_cml_client()
    job_id = find_job_id(client, project_id, settings.job_name)

    print(f"[dev_run] triggering job {settings.job_name!r} with run_id={run_id}")
    job_run_id = trigger_job(client, project_id, job_id, run_id)
    print(f"[dev_run] job_run_id={job_run_id}")

    start = time.monotonic()
    last_node: str | None = None
    while True:
        elapsed = time.monotonic() - start
        if elapsed > args.timeout:
            print(f"[dev_run] TIMEOUT after {elapsed:.0f}s waiting for a terminal state")
            return 1

        cml_status = get_cml_run_status(client, project_id, job_id, job_run_id)
        local_status = read_status_json(run_path)

        if local_status and local_status["node"] != last_node:
            last_node = local_status["node"]
            print(
                f"[dev_run] {elapsed:5.0f}s  status.json: {local_status['state']:9s} "
                f"node={local_status['node']}  |  cml: {cml_status}"
            )

        local_state = local_status["state"] if local_status else None

        if local_state in STATUS_JSON_TERMINAL:
            if local_state == "completed":
                print(f"[dev_run] SUCCESS — completed in {elapsed:.0f}s")
                return 0
            print(f"[dev_run] FAILED — {local_status.get('error')}")
            return 1

        cml_lower = (cml_status or "").lower()
        if cml_lower in CML_TERMINAL_FAILURE:
            # CML says the process is done and it did not succeed, but
            # status.json never reached a terminal state — the process died
            # without a chance to record why. This is the desync bug class.
            print(
                f"[dev_run] DESYNC — CML reports '{cml_status}' but status.json "
                f"still shows '{local_state or '<no file>'}' "
                f"at node={last_node or '<none>'}. The job process likely crashed "
                "before it could write a terminal status. Check CML job logs."
            )
            return 1

        if cml_lower in CML_TERMINAL_SUCCESS and local_state not in STATUS_JSON_TERMINAL:
            print(
                f"[dev_run] DESYNC — CML reports '{cml_status}' but status.json "
                f"never reached a terminal state (last: '{local_state or '<no file>'}'). "
                "Treating as failed per ARCHITECTURE.md §6.3: a run with no terminal "
                "status.json is not a completed run."
            )
            return 1

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
