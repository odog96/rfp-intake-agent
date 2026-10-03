#!/usr/bin/env python3
"""Record that a reviewer subagent finished, so the commit gate can see it.

Wired to SubagentStop. Claude Code sends the event as JSON on standard input;
the fields used here are `session_id` and `agent_type`. The record stores the
commit hash that HEAD pointed at when the reviewer ran and the time it
finished, which is what lets the gate tell a fresh review from a stale one:
after a commit HEAD moves, so every record from before it stops counting.

This hook never blocks anything. It writes a record and exits 0.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_state  # noqa: E402


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0

    reviewer = event.get("agent_type") or ""
    if reviewer not in gate_state.REQUIRED_REVIEWERS:
        return 0

    cwd = event.get("cwd") or Path.cwd()
    root = gate_state.repo_root(cwd)
    if not gate_state.is_guarded_repo(root):
        return 0

    session_id = event.get("session_id") or ""
    records = gate_state.load_records(session_id)
    records[reviewer] = {
        "head": gate_state.head_sha(root),
        "finished_at": time.time(),
    }
    gate_state.save_records(session_id, records)
    head = records[reviewer]["head"][:8]
    gate_state.log(f"recorded {reviewer} session={session_id[:8]} head={head}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
