"""CML Application launcher — starts Streamlit on the correct port."""

import os
import subprocess
import sys

port = os.environ.get("CDSW_APP_PORT", "8100")
app_path = "/home/cdsw/rfp-intake-agent/app.py"

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
