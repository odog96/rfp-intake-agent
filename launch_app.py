"""CML Application launcher — starts Streamlit on the correct port."""

import os
import subprocess
import sys
from pathlib import Path

port = os.environ.get("CDSW_APP_PORT", "8100")

# The repository root is this script's own directory when a runtime defines
# __file__. Not hardcoded, because a Cloudera AI project deployed from
# .project-metadata.yaml clones the repository into /home/cdsw itself while a
# manual clone puts it in a subfolder of /home/cdsw, and both layouts have to
# work.
#
# The fallback is what the AMP deployment of 2026-08-28 actually needs: Cloudera
# AI starts an Application by running this script through a PBJ/IPython kernel,
# where __file__ is not defined at all, so line 1 of the launcher raised
# NameError and the engine exited with status 1 before Streamlit was reached.
# run_job.py and app.py already carried this workaround; the launcher did not.
# find_project_root is the tested implementation and is importable here because
# the first AMP task pip-installs this package editable.
try:
    project_root = Path(__file__).resolve().parent
except NameError:  # pragma: no cover - depends on the CML runtime
    from rfp_intake.config.paths import find_project_root

    project_root = find_project_root()

# Which page the Application serves. app_v2.py is the current one: it draws the
# pipeline's nine stages as cards. app.py is the previous page, kept working and
# unchanged, so rolling back is setting this variable rather than editing code —
# in Project Settings → Advanced → Environment Variables, then restarting the
# Application.
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
)
