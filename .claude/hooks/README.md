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
"hooks": {
  "PreToolUse": [
    {"matcher": "Bash", "hooks": [{"type": "command",
      "command": "python3 /home/cdsw/rfp-intake-agent/.claude/hooks/gate_commit.py", "timeout": 20}]}
  ],
  "SubagentStop": [
    {"matcher": "pipeline-rules-reviewer|plan-conformance-reviewer", "hooks": [{"type": "command",
      "command": "python3 /home/cdsw/rfp-intake-agent/.claude/hooks/record_reviewer.py", "timeout": 20}]}
  ]
}
```

## Skipping the gate

Put `GATE_OFF=1` at the front of the command. The gate reads that from the command text, not from
the environment, so the bypass is visible in the transcript and is written to the log with the whole
command. It is for Oliver to authorise, not for Claude to reach for.

## Not tested

Whether `--permission-mode bypassPermissions` still runs the hook. If it does not, that mode skips
the gate silently.
