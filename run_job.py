"""CML Job script — entry point for the RFP extraction pipeline."""

import os
import sys

# CML runs scripts through an IPython kernel where __file__ is not defined.
# Use absolute path to set working directory to the project root.
os.chdir("/home/cdsw/rfp-intake-agent")

sys.path.insert(0, "/home/cdsw/rfp-intake-agent/src")

from rfp_intake.job import main  # noqa: E402
from rfp_intake.job.args import resolve_run_id  # noqa: E402

# Prefers RFP_RUN_ID over sys.argv, because that same IPython kernel puts its own
# "-f <connection-file>" into sys.argv and sys.argv[1] is therefore "-f".
main(resolve_run_id(sys.argv))
