"""Work out which run the CML Job was asked to process.

CML executes a job's script through an IPython kernel, which puts its own
arguments into `sys.argv` — typically `-f /path/to/kernel-<id>.json`. So
`sys.argv[1]` is `-f`, not the run id, and on 2026-08-27 the CML Job named
"RFP Pipeline Executor" failed with "No inputs directory: runs/-f/inputs" while
the Jobs API showed the argument had been passed correctly.

The environment variable is therefore the primary channel: the Cloudera AI
Application sets RFP_RUN_ID when it creates the job run, and an environment
variable cannot be rewritten by whatever interpreter wraps the script. Command
line parsing stays as a fallback so `python run_job.py <run_id>` still works when
run by hand, and it skips the arguments IPython injects.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

RUN_ID_ENV_VAR = "RFP_RUN_ID"

# Options IPython adds to sys.argv, each consuming the token that follows it.
_IPYTHON_OPTIONS_WITH_VALUE = ("-f", "--f", "--HistoryManager.hist_file")


class MissingRunIdError(Exception):
    """The job was started without saying which run to process."""


def resolve_run_id(
    argv: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Return the run id, or raise MissingRunIdError naming both channels."""
    env = os.environ if env is None else env

    from_env = (env.get(RUN_ID_ENV_VAR) or "").strip()
    if from_env:
        return from_env

    candidate = _first_positional(argv if argv is not None else [])
    if candidate:
        return candidate

    raise MissingRunIdError(
        f"No run id. Set {RUN_ID_ENV_VAR}, or pass it as the first argument: "
        f"python run_job.py <run_id>. (CML runs this script through an IPython "
        f"kernel whose own -f argument occupies sys.argv[1], which is why the "
        f"environment variable is the channel the application uses.)"
    )


def _first_positional(argv: Sequence[str]) -> str | None:
    """First real argument in argv[1:], skipping the interpreter's own options."""
    rest = list(argv[1:])
    index = 0
    while index < len(rest):
        token = rest[index]
        if token in _IPYTHON_OPTIONS_WITH_VALUE:
            index += 2  # skip the option and the value it consumes
            continue
        if token.startswith("-"):
            index += 1  # a flag we do not recognise; it takes no value
            continue
        return token
    return None
