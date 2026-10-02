"""CML Application launcher — starts Streamlit on the correct port."""

import os
import subprocess
import sys
from pathlib import Path

port = os.environ.get("CDSW_APP_PORT", "8100")

# This runs before `rfp_intake` is importable, so the project root has to be
# found without importing anything from the package. The logic is deliberately a
# cut-down copy of rfp_intake.config.paths.find_project_root, which is the tested
# one; keep the two in step. run_job.py carries the same copy for the same reason.
#
# Two separate Cloudera AI failures are being guarded here. The AMP deployment of
# 2026-08-28 died with `NameError: name '__file__' is not defined`, because
# Cloudera AI starts an Application through a PBJ/IPython kernel where __file__
# does not exist. The Application created on 2026-10-01 then died with
# `ModuleNotFoundError: No module named 'rfp_intake'`, because the earlier
# workaround imported the package to recover from that NameError — and the package
# is only installed in one runtime's site-packages, so an Application on a
# different runtime cannot import it. Searching for the root needs neither.
#
# The path must not be hardcoded either: a project deployed from
# .project-metadata.yaml puts the repository in /home/cdsw itself, while a manual
# clone puts it in a subfolder.
_MARKERS = ("run_job.py", "config/fields.yaml")


def _find_project_root() -> Path:
    home = Path("/home/cdsw")
    candidates = [Path.cwd(), *Path.cwd().parents, home]
    if home.is_dir():
        candidates += sorted(child for child in home.iterdir() if child.is_dir())
    for candidate in candidates:
        if all((candidate / marker).is_file() for marker in _MARKERS):
            return candidate
    raise SystemExit(
        f"Could not find the rfp-intake project root: no directory containing "
        f"{' and '.join(_MARKERS)} under {Path.cwd()} or {home}."
    )


project_root = _find_project_root()

# Which page the Application serves. app_v2.py is the current one, by Oliver's
# decision on 2026-10-02: it draws the pipeline's eleven stages as cards. app.py
# is the previous page, kept working and unchanged, so rolling back is setting
# this variable rather than editing code — in Project Settings → Advanced →
# Environment Variables, then restarting the Application.
#
# Not declared in .project-metadata.yaml on purpose. An AMP prompts for every
# variable it declares, and a customer deploying this project should not be asked
# to choose a page.
app_filename = os.environ.get("RFP_INTAKE_APP_FILE", "app_v2.py")
app_path = project_root / app_filename
if not app_path.is_file():
    # Fail here with the path, rather than letting Streamlit start and serve an
    # error page the Application's own log will not explain.
    raise SystemExit(
        f"RFP_INTAKE_APP_FILE is {app_filename!r}, which is not a file in "
        f"{project_root}. Set it to app_v2.py (the stage-card page) or app.py "
        "(the previous page)."
    )

# Streamlit inherits this process's working directory, and Settings resolves
# config/ and runs/ relative to it. app.py sets this again for safety when it is
# launched some other way.
os.chdir(project_root)

# The page Streamlit runs imports rfp_intake at module level, and finding the
# project root above does nothing for that import. An editable install only puts
# the package on the path of the one runtime that ran `pip install -e .`, so an
# Application on any other runtime raises ModuleNotFoundError the moment the page
# loads. Putting src/ on the child's PYTHONPATH makes the import work either way,
# and an existing install still wins because it is already imported by name.
environment = dict(os.environ)
source_dir = str(project_root / "src")
existing_path = environment.get("PYTHONPATH")
environment["PYTHONPATH"] = (
    f"{source_dir}{os.pathsep}{existing_path}" if existing_path else source_dir
)

subprocess.run(
    [
        sys.executable, "-m", "streamlit", "run",
        str(app_path),
        f"--server.port={port}",
        "--server.address=127.0.0.1",
        "--server.headless=true",
        "--browser.serverAddress=0.0.0.0",
        "--browser.gatherUsageStats=false",
    ],
    check=True,
    env=environment,
)
