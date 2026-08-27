"""CML Application launcher — starts Streamlit on the correct port."""

import os
import subprocess
import sys

port = os.environ.get("CDSW_APP_PORT", "8100")
project_root = "/home/cdsw/rfp-intake-agent"
app_path = f"{project_root}/app.py"

# Streamlit inherits this process's working directory, and Settings resolves
# config/ and runs/ relative to it. app.py sets this again for safety when it is
# launched some other way.
os.chdir(project_root)

subprocess.run(
    [
        sys.executable, "-m", "streamlit", "run",
        app_path,
        f"--server.port={port}",
        "--server.address=127.0.0.1",
        "--server.headless=true",
        "--browser.serverAddress=0.0.0.0",
        "--browser.gatherUsageStats=false",
    ],
    check=True,
)
