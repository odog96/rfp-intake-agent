"""Shared state for the commit gate.

Two hooks use this file. `record_reviewer.py` writes a record when a reviewer
subagent finishes; `gate_commit.py` reads those records when a commit is
attempted. Both also append to one log, so a session where the hooks never
fired is distinguishable from a session where they fired and allowed the
commit — absence of a log line is itself the signal that the hooks are not
loaded.

State lives under the user's Claude configuration directory, not /tmp (which
is world-writable on a shared host) and not inside the repository (where it
would be committed).
"""
import json
import os
import subprocess
import time
from pathlib import Path

# The two reviewers a change under the guarded paths must pass.
REQUIRED_REVIEWERS = ("pipeline-rules-reviewer", "plan-conformance-reviewer")

# Paths inside the repository that the gate guards.
GUARDED_PREFIXES = ("src/rfp_intake/", "config/")

# The repository this gate applies to. The hooks can be wired in the user's
# settings file so they load wherever a session starts, so each hook checks
# this itself and stays out of the way in every other project.
GUARDED_REPO_NAME = "rfp-intake-agent"


def state_dir() -> Path:
    """Return the directory holding the per-session records and the log."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
    return Path(base).expanduser() / "rfp-intake-gate"


def log(message: str) -> None:
    """Append one line to the gate's log. Never raises."""
    try:
        directory = state_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        path = directory / "log.txt"
        with path.open("a") as handle:
            handle.write(f"[{stamp}] {message}\n")
        path.chmod(0o600)
    except Exception:
        pass


def record_path(session_id: str) -> Path:
    """Return the record file for one Claude Code session."""
    safe = "".join(c for c in (session_id or "unknown") if c.isalnum() or c in "-_")
    return state_dir() / f"reviewers-{safe or 'unknown'}.json"


def load_records(session_id: str) -> dict:
    """Return this session's reviewer records as a dict, or an empty dict."""
    try:
        loaded = json.loads(record_path(session_id).read_text())
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def save_records(session_id: str, records: dict) -> None:
    """Write this session's reviewer records. Never raises."""
    try:
        state_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
        path = record_path(session_id)
        path.write_text(json.dumps(records, indent=2))
        path.chmod(0o600)
    except Exception:
        pass


def already_decided(tool_use_id: str) -> bool:
    """Say whether this gate already ruled on this one tool call.

    The hooks can be wired in two places — the user's settings file, which
    loads wherever a session starts, and the repository's, which travels with
    the repository — and then both copies fire for the same commit. The first
    one to rule leaves a marker, and the second stays quiet, so Oliver reads
    one message instead of two.
    """
    if not tool_use_id:
        return False
    try:
        directory = state_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        cutoff = time.time() - 86400
        for old in directory.glob("decided-*"):
            if old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
        safe = "".join(c for c in tool_use_id if c.isalnum() or c in "-_")
        marker = directory / f"decided-{safe}"
        marker.touch(mode=0o600, exist_ok=False)
        return False
    except FileExistsError:
        return True
    except Exception:
        return False


def git(args: list, cwd) -> str:
    """Run one git command and return its stripped output, or "" on failure."""
    try:
        done = subprocess.run(  # noqa: S603
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10, check=False
        )
        return done.stdout.strip() if done.returncode == 0 else ""
    except Exception:
        return ""


def repo_root(cwd) -> str:
    """Return the git repository root containing cwd, or "" if there is none."""
    return git(["rev-parse", "--show-toplevel"], cwd)


def head_sha(cwd) -> str:
    """Return the current commit's full hash, or "" before the first commit."""
    return git(["rev-parse", "HEAD"], cwd)


def is_guarded_repo(root: str) -> bool:
    """Say whether this repository is the one the gate applies to."""
    return bool(root) and Path(root).name == GUARDED_REPO_NAME
