"""CML Job script — entry point for the RFP extraction pipeline."""

import os
import sys
from pathlib import Path

# CML runs scripts through an IPython kernel where __file__ is not defined.
# Use absolute path to set working directory to the project root.
os.chdir("/home/cdsw/rfp-intake-agent")

from rfp_intake.job import main  # noqa: E402

if len(sys.argv) < 2:
    raise SystemExit("Usage: run_job.py <run_id>")
main(sys.argv[1])
