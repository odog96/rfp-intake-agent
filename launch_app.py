"""CML Application launcher — starts Streamlit on the correct port."""

import os
import subprocess
import sys
from pathlib import Path

port = os.environ.get("CDSW_APP_PORT", "8100")

# The repository root is this script's own directory. Not hardcoded, because a
# Cloudera AI project deployed from .project-metadata.yaml clones the repository
# into /home/cdsw itself while a manual clone puts it in a subfolder of
# /home/cdsw, and both layouts have to work.
project_root = Path(__file__).resolve().parent
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
