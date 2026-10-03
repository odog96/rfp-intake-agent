#!/usr/bin/env python3
"""Stop a commit that touches the pipeline before the two reviewers have run.

Wired to PreToolUse on Bash. Claude Code sends the event as JSON on standard
input; this hook reads `tool_input.command`, `cwd` and `session_id`.

What it does, in order:
  1. Ignores anything that is not a git commit, and any repository other than
     rfp-intake-agent.
  2. Lets the commit through if nothing in it is under src/rfp_intake/ or
     config/ — docs, tests and notes are not gated.
  3. Otherwise requires a record from both pipeline-rules-reviewer and
     plan-conformance-reviewer, made in this session, since the last commit,
     and after the last edit to the files being committed.
  4. If a record is missing or stale, denies the commit and says which.

Writing GATE_OFF=1 at the front of the command skips the check. The hook reads
that from the command text, not from the environment, so the bypass is visible
in the transcript and in the gate log.

Deciding nothing is the quiet path: the hook prints nothing and exits 0, which
leaves the normal permission rules in charge. It never prints an "allow"
decision, because that would skip those rules.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_state  # noqa: E402

COMMIT = re.compile(r"\bgit\b(?:\s+-C\s+\S+|\s+--\S+(?:=\S+)?)*\s+commit\b")
COMMIT_ALL = re.compile(r"\bcommit\b.*?(?:\s-[a-zA-Z]*a[a-zA-Z]*\b|\s--all\b)")


def deny(reason):
    """Print a PreToolUse denial and stop the tool call."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


def guarded_files(command, root):
    """Return the files in this commit that sit under a guarded path."""
    staged = gate_state.git(["diff", "--cached", "--name-only"], root).splitlines()
    files = list(staged)
    if COMMIT_ALL.search(command):
        # -a / -am / --all also commits tracked files that were never staged.
        files += gate_state.git(["diff", "--name-only"], root).splitlines()
    return sorted({
        f for f in files if f.startswith(gate_state.GUARDED_PREFIXES)
    })


def newest_edit(files, root):
    """Return the time the most recently edited of these files changed."""
    newest = 0.0
    name = ""
    for f in files:
        try:
            stamp = (Path(root) / f).stat().st_mtime
        except OSError:
            continue
        if stamp > newest:
            newest, name = stamp, f
    return newest, name


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0

    command = (event.get("tool_input") or {}).get("command") or ""
    if not COMMIT.search(command):
        return 0

    cwd = event.get("cwd") or Path.cwd()
    root = gate_state.repo_root(cwd)
    if not gate_state.is_guarded_repo(root):
        return 0

    session_id = event.get("session_id") or ""
    short = session_id[:8]

    if gate_state.already_decided(event.get("tool_use_id")):
        return 0

    if "GATE_OFF=1" in command:
        gate_state.log(f"BYPASS session={short} GATE_OFF=1 in: {command[:120]}")
        return 0

    files = guarded_files(command, root)
    if not files:
        where = ", ".join(gate_state.GUARDED_PREFIXES)
        gate_state.log(f"allow session={short} nothing under {where}")
        return 0

    head = gate_state.head_sha(root)
    records = gate_state.load_records(session_id)
    edited_at, edited_file = newest_edit(files, root)

    missing = []
    stale = []
    for reviewer in gate_state.REQUIRED_REVIEWERS:
        record = records.get(reviewer)
        if not record:
            missing.append(reviewer)
        elif record.get("head") != head:
            stale.append(f"{reviewer} (ran before the last commit)")
        elif record.get("finished_at", 0) < edited_at:
            stale.append(f"{reviewer} (ran before {edited_file} was last edited)")

    if not missing and not stale:
        gate_state.log(f"allow session={short} both reviewers current, {len(files)} file(s)")
        return 0

    problems = missing + stale
    gate_state.log(f"DENY session={short} files={len(files)} problems={problems}")
    file_list = "\n".join(f"  {f}" for f in files[:10])
    if len(files) > 10:
        file_list += f"\n  ... and {len(files) - 10} more"
    return deny(
        "This commit changes the pipeline, and the review that has to happen first has not.\n\n"
        f"Guarded files in this commit:\n{file_list}\n\n"
        "Not satisfied: " + "; ".join(problems) + ".\n\n"
        "Run the missing reviewer with the Agent tool, read what it says, fix what it finds, "
        "then commit again. If the review genuinely does not apply, say so to Oliver and ask "
        "him to approve the commit; he can skip the check by putting GATE_OFF=1 at the front "
        "of the command, which is recorded in the gate log.\n\n"
        f"Gate log: {gate_state.state_dir() / 'log.txt'}"
    )


if __name__ == "__main__":
    sys.exit(main())
