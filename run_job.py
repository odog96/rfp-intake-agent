"""CML Job script — entry point for the RFP extraction pipeline."""

import os
import sys
from pathlib import Path

# This runs before `rfp_intake` is importable, so the project root has to be
# found without importing anything from the package. The logic is deliberately a
# cut-down copy of rfp_intake.config.paths.find_project_root, which is the tested
# one; keep the two in step.
#
# CML runs job scripts through an IPython kernel where `__file__` is not defined,
# which is why this searches rather than deriving the path from the script's own
# location. It must not be hardcoded either: a project deployed from
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


_PROJECT_ROOT = _find_project_root()
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from rfp_intake.job import main  # noqa: E402
from rfp_intake.job.args import resolve_run_id  # noqa: E402

# Prefers RFP_RUN_ID over sys.argv, because that same IPython kernel puts its own
# "-f <connection-file>" into sys.argv and sys.argv[1] is therefore "-f".
main(resolve_run_id(sys.argv))
