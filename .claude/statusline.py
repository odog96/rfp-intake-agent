#!/usr/bin/env python3
"""The status line: the state Oliver kept having to ask for, on screen always.

Claude Code sends one JSON object on standard input and prints the first line
of our output under the prompt. The fields used here are `workspace.project_dir`
(the directory the session started in), `workspace.current_dir` and
`model.display_name`.

It shows, left to right:
  branch and how many commits are not pushed · the newest run and how many of
  the original 36 fields it confirmed · whether the commit gate is armed · the
  model.

Two rules it follows:

  Every number comes from a command, not from this file. The field count is
  whatever `scripts/check_run_acceptance.py` prints, which is the same number
  the acceptance checks report. Nothing is estimated and nothing is carried
  over from a previous run.

  A number it cannot establish is left out rather than guessed. The test count
  only appears once something has recorded a real `pytest` summary; until then
  the field is absent, because `.pytest_cache` disagrees with what pytest
  actually reported (896 node ids against 871 tests, and eight stale entries in
  `lastfailed` that now pass).

Anything unexpected falls back to a short plain line. A status line must never
be the thing that breaks a session.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path("/home/cdsw/rfp-intake-agent")

DIM = "\x1b[2m"
RED = "\x1b[31m"
YELLOW = "\x1b[33m"
GREEN = "\x1b[32m"
OFF = "\x1b[0m"
SEP = f" {DIM}·{OFF} "


def state_dir() -> Path:
    """The same directory the commit gate uses, so there is one place to look."""
    return Path("~/.claude").expanduser() / "rfp-intake-gate"


def run(args: list, cwd: Path, timeout: int = 5) -> str:
    """Run one command and return its output stripped, or "" if it fails."""
    try:
        done = subprocess.run(  # noqa: S603
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return done.stdout.strip() if done.returncode == 0 else ""
    except Exception:
        return ""


def git_part() -> str:
    """Branch, commits not pushed, and whether the working tree is dirty."""
    branch = run(["git", "branch", "--show-current"], REPO) or "detached"
    ahead = run(["git", "rev-list", "--count", "origin/main..HEAD"], REPO)
    dirty = bool(run(["git", "status", "--porcelain"], REPO))
    text = branch
    if ahead and ahead != "0":
        text += f" {YELLOW}+{ahead}↑{OFF}"
    if dirty:
        text += f" {DIM}dirty{OFF}"
    return text


def newest_run() -> str:
    """The newest run id, by when the run was actually made.

    Sorting the names does not work: most run ids carry a timestamp, as in
    `r-20261003-143058-topk7`, but some are hand-named, as in
    `r-listfix-175318`, and a letter sorts above a digit — so the names alone
    put a run from any date ahead of today's. Each run is dated from its
    timestamp where it has one and from the directory otherwise.
    """
    stamped = re.compile(r"^r-(\d{8})-(\d{6})")
    newest, newest_at = "", -1.0
    try:
        candidates = list((REPO / "runs").iterdir())
    except Exception:
        return ""
    for directory in candidates:
        if not directory.is_dir() or not directory.name.startswith("r-"):
            continue
        found = stamped.match(directory.name)
        made_at = None
        if found:
            try:
                made_at = time.mktime(
                    time.strptime(found.group(1) + found.group(2), "%Y%m%d%H%M%S")
                )
            except ValueError:
                made_at = None
        if made_at is None:
            try:
                made_at = directory.stat().st_mtime
            except OSError:
                continue
        if made_at > newest_at:
            newest, newest_at = directory.name, made_at
    return newest


def field_count(run_id: str) -> str:
    """Confirmed fields for this run, from scripts/check_run_acceptance.py.

    The script takes about a third of a second, which is too slow to repeat on
    every redraw, so the answer is cached against the run's extraction.json and
    recomputed only when that file changes.
    """
    extraction = REPO / "runs" / run_id / "extraction.json"
    try:
        stamp = extraction.stat().st_mtime
    except OSError:
        return ""

    cache_file = state_dir() / "statusline-fields.json"
    key = f"{run_id}:{stamp}"
    try:
        cache = json.loads(cache_file.read_text())
        if cache.get("key") == key:
            return cache["text"]
    except Exception:
        cache = {}

    out = run([sys.executable, "scripts/check_run_acceptance.py", run_id], REPO, timeout=30)
    text = ""
    for line in out.splitlines():
        # The line reads: "confirmed among the original 36: 27 (floor 26)"
        if line.startswith("confirmed among the original "):
            head, _, rest = line.partition(":")
            denominator = head.rsplit(" ", 1)[-1]
            numerator = rest.strip().split(" ", 1)[0]
            text = f"{numerator} of {denominator} fields"
            break
    if not text:
        return ""

    try:
        state_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"key": key, "text": text, "at": time.time()}))
        cache_file.chmod(0o600)
    except Exception:
        pass
    return text


def tests_part() -> str:
    """The last real pytest summary, if anything has recorded one."""
    try:
        cached = json.loads((state_dir() / "statusline-tests.json").read_text())
    except Exception:
        return ""
    passed = cached.get("passed")
    failed = cached.get("failed") or 0
    if passed is None:
        return ""
    if failed:
        return f"{RED}{failed} failing{OFF}"
    return f"{GREEN}{passed} tests{OFF}"


def gate_part(project_dir: str) -> str:
    """Whether the commit gate can actually run in this session.

    The repository's own .claude/settings.json only loads when the session was
    started inside the repository, so a session started elsewhere has no gate
    at all and says so. A copy of the hook in the user's settings file loads
    everywhere, and then the gate is armed wherever the session began.
    """
    try:
        user_settings = json.loads((Path("~/.claude/settings.json").expanduser()).read_text())
        wired_for_every_session = "gate_commit.py" in json.dumps(user_settings.get("hooks") or {})
    except Exception:
        wired_for_every_session = False

    if wired_for_every_session:
        return f"{DIM}gate on{OFF}"
    try:
        started_in_repo = Path(project_dir).resolve() == REPO.resolve()
    except Exception:
        started_in_repo = False
    if started_in_repo:
        return f"{DIM}gate on{OFF} {DIM}(this session only){OFF}"
    return f"{RED}GATE OFF{OFF}"


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        event = {}

    workspace = event.get("workspace") or {}
    project_dir = workspace.get("project_dir") or ""
    current_dir = workspace.get("current_dir") or ""
    model = ((event.get("model") or {}).get("display_name")) or ""

    # Stay out of the way of every other project.
    inside = False
    for candidate in (current_dir, project_dir):
        try:
            if candidate and (Path(candidate).resolve() == REPO.resolve()
                              or REPO.resolve() in Path(candidate).resolve().parents):
                inside = True
        except Exception:
            continue
    if not inside:
        print(f"{DIM}{Path(current_dir).name or '~'}{OFF}{SEP}{model}" if model else "")
        return 0

    parts = [git_part()]
    run_id = newest_run()
    if run_id:
        fields = field_count(run_id)
        parts.append(f"{run_id}{(' ' + fields) if fields else ''}")
    tests = tests_part()
    if tests:
        parts.append(tests)
    parts.append(gate_part(project_dir))
    if model:
        parts.append(f"{DIM}{model}{OFF}")
    print(SEP.join(p for p in parts if p))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("rfp-intake-agent")
        sys.exit(0)
