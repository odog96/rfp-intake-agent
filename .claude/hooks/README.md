# The commit gate

Two hooks stop a commit that changes `src/rfp_intake/` or `config/` before
`pipeline-rules-reviewer` and `plan-conformance-reviewer` have both run on it.

- `gate_commit.py` — PreToolUse on Bash. Blocks the commit and says which reviewer is missing.
- `record_reviewer.py` — SubagentStop. Records a reviewer when it finishes.
- `gate_state.py` — shared helpers; no hook of its own.

Records and a log of every decision live in `~/.claude/rfp-intake-gate/`. The log is the way to
check the gate afterwards: compare it against `git log` and every commit touching a guarded path
should have an `allow`, a `DENY` or a `BYPASS` line.

## The limit you need to know about

`.claude/settings.json` in this repository **only loads if the Claude Code session was started
inside the repository.** Tested on 2026-10-03 with Claude Code 2.1.284:

| Session started in | Repository hooks |
|---|---|
| `/home/cdsw/rfp-intake-agent` | load |
| `/home/cdsw`, then `cd` into the repository | never load |

Starting elsewhere makes the gate **fail open**: it does not block, it does not warn, and nothing
is written to the log. The same is true of the four reviewers in `.claude/agents/` — a session
started in `/home/cdsw` cannot see them at all, which is why earlier sessions reported
`Agent type 'run-comparison-reviewer' not found`.

Two things keep that from biting:

1. Start sessions in the repository. The alias `cc='cd ~/rfp-intake-agent && claude'` does this.
2. For a gate that loads wherever a session starts, add the same two hooks to
   `~/.claude/settings.json`, which is read for every session. Oliver has to do this himself; the
   scripts already check that they are in this repository before acting, so they stay out of the
   way of every other project, and they ignore a repeated call for the same commit, so wiring them
   in both places produces one message, not two:

```json
"statusLine": {
  "type": "command",
  "command": "python3 /home/cdsw/rfp-intake-agent/.claude/statusline.py",
  "padding": 0
},
"hooks": {
  "PreToolUse": [
    {"matcher": "Bash", "hooks": [{"type": "command",
      "command": "python3 /home/cdsw/rfp-intake-agent/.claude/hooks/gate_commit.py", "timeout": 20}]}
  ],
  "PostToolUse": [
    {"matcher": "Bash", "hooks": [{"type": "command",
      "command": "python3 /home/cdsw/rfp-intake-agent/.claude/hooks/record_tests.py", "timeout": 20}]}
  ],
  "SubagentStop": [
    {"matcher": "pipeline-rules-reviewer|plan-conformance-reviewer", "hooks": [{"type": "command",
      "command": "python3 /home/cdsw/rfp-intake-agent/.claude/hooks/record_reviewer.py", "timeout": 20}]}
  ]
}
```

## The status line

`../statusline.py` prints one line under the prompt:

```
main +3↑ · r-20261003-143058-topk7 27 of 36 fields · 870 tests · gate on · Opus
```

- Branch, and commits ahead of `origin/main` that are not pushed, plus `dirty` for uncommitted work.
- The newest run, and its confirmed fields straight from `scripts/check_run_acceptance.py` — the
  same number the acceptance checks print, cached against the run's `extraction.json` and
  recomputed when that file changes. About 0.6s on the first draw after a new run, 0.1s after.
- The newest run is dated from the timestamp in its id, or from the directory for a hand-named run
  such as `r-listfix-175318`. Sorting the names does not work: a letter sorts above a digit, so
  `r-listfix-175318` would otherwise beat every dated run.
- `870 tests`, or `3 failing` in red. This comes from `record_tests.py`, which keeps the summary
  line a real `pytest` command printed. The field is simply absent until something records one.
  `.pytest_cache` cannot be used for this: after a green run it still listed 896 node ids against
  871 tests and eight failures that now pass.
- **`gate on` or `GATE OFF` in red** — whether the gate can actually run in this session. This is
  the part that makes the fail-open case visible instead of silent. It reads `gate on` when the
  hook is in `~/.claude/settings.json`, `gate on (this session only)` when it is only in this
  repository and the session started here, and `GATE OFF` otherwise.
- The model.

Outside this repository it prints the directory name and the model, and nothing else.

## Skipping the gate

Put `GATE_OFF=1` at the front of the command. The gate reads that from the command text, not from
the environment, so the bypass is visible in the transcript and is written to the log with the whole
command. It is for Oliver to authorise, not for Claude to reach for.

## Not tested

Whether `--permission-mode bypassPermissions` still runs the hook. If it does not, that mode skips
the gate silently.
