"""Install this project's dependencies during an AMP deployment.

Run by the first task in `.project-metadata.yaml`. Cloudera AI runs AMP scripts
with the working directory set to the project root, which is where the
repository is cloned, so the relative paths below resolve there. The check at the
top turns a wrong working directory into a clear message rather than a confusing
pip error.

Installs the `aws` extra as well as the base dependencies. That extra pulls in
`langchain-aws`, which is only needed when `config/models.yaml` routes a role to
AWS Bedrock. It is installed here because Bedrock is the backend a customer
evaluates with; a production deployment on Cloudera AI Inference (CAII) never
calls it, and rule 5 in CLAUDE.md keeps `privacy_mode: private` from constructing
an off-box provider at all.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path.cwd()
REQUIRED_MARKERS = (Path("run_job.py"), Path("config") / "fields.yaml")


def check_working_directory() -> None:
    missing = [str(m) for m in REQUIRED_MARKERS if not (PROJECT_ROOT / m).is_file()]
    if missing:
        raise SystemExit(
            f"Not in the project root: {PROJECT_ROOT} is missing "
            f"{', '.join(missing)}. Run this script from the directory holding "
            f"run_job.py."
        )


def pip(*args: str) -> None:
    command = [sys.executable, "-m", "pip", "install", *args]
    print(f"$ {' '.join(command)}", flush=True)
    subprocess.run(command, check=True)


def main() -> None:
    check_working_directory()
    pip("--upgrade", "pip")
    pip("-r", "requirements.txt")
    # Editable, so the Cloudera AI Application and the CML Job both import the
    # package straight from src/ and a `git pull` takes effect without a
    # reinstall.
    pip("-e", ".[aws]")

    # Fail here rather than three tasks later inside the CML Job, where the only
    # symptom would be an import error in a job log nobody is watching.
    subprocess.run(
        [sys.executable, "-c", "import rfp_intake, streamlit, cmlapi; print('imports OK')"],
        check=True,
    )
    print("Dependencies installed.", flush=True)


if __name__ == "__main__":
    main()
