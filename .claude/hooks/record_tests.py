#!/usr/bin/env python3
"""Remember the last real pytest result, so the status line can show it.

Wired to PostToolUse on Bash. It reads the command's own output and keeps the
summary line pytest printed — never a count worked out from anything else.
`.pytest_cache` is not usable for this: after a green run it still listed 896
node ids against 871 tests and eight failures that now pass.

Optional. Without it the status line simply leaves the test field out.

This hook never blocks anything. It writes a file and exits 0.
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_state  # noqa: E402

COUNT = re.compile(r"(\d+) (passed|failed|error|errors|skipped)")


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0

    command = (event.get("tool_input") or {}).get("command") or ""
    if "pytest" not in command:
        return 0

    root = gate_state.repo_root(event.get("cwd") or Path.cwd())
    if not gate_state.is_guarded_repo(root):
        return 0

    response = event.get("tool_response")
    if isinstance(response, dict):
        text = f"{response.get('stdout') or ''}\n{response.get('stderr') or ''}"
    else:
        text = str(response or "")

    # Take the last line that carries a pytest tally; earlier lines may be
    # progress output or a different command in the same shell.
    tally = {}
    for line in text.splitlines():
        found = dict((kind, int(number)) for number, kind in COUNT.findall(line))
        if found and ("passed" in found or "failed" in found or "error" in found
                      or "errors" in found):
            tally = found
    if not tally:
        return 0

    record = {
        "passed": tally.get("passed", 0),
        "failed": tally.get("failed", 0) + tally.get("error", 0) + tally.get("errors", 0),
        "skipped": tally.get("skipped", 0),
        "at": time.time(),
        "command": command[:200],
    }
    try:
        directory = gate_state.state_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / "statusline-tests.json"
        path.write_text(json.dumps(record, indent=2))
        path.chmod(0o600)
    except Exception:
        return 0
    gate_state.log(
        f"tests recorded: {record['passed']} passed, {record['failed']} failed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
