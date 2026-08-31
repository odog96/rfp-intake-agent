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

app_path = project_root / "app.py"

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
