---
name: stage
description: Build one numbered stage from the newest docs/PLAN_*.md — list the files first, wait for Oliver's yes, build, run the two reviewers, then report in six lines. Use when Oliver says to start or continue a stage.
argument-hint: "[stage-number]"
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Edit, Write, Bash, Agent
---

Oliver has asked you to build stage **$ARGUMENTS**.

Follow these five steps in order. Do not skip step 2, and do not merge the steps into one reply.

## 1. Read before you plan

- The newest `docs/PLAN_*.md`, and in it the section for stage $ARGUMENTS only.
- `CLAUDE.md`: the "How to respond in chat" section, the non-negotiable rules, and the to-do list.
- The files that stage names.

If the newest PLAN has no stage $ARGUMENTS, say so and stop. Do not guess which stage was meant.

## 2. List the work, then stop

Reply with, and nothing else:

- Every file you will change or add, one per line, with one short phrase for each saying what changes.
- The acceptance test for this stage: the file it goes in, and in plain words what passing means.
  Oliver writes these himself. Propose yours in one or two sentences and ask him to confirm it or
  hand you the one he wants.
- Anything in the stage you think is wrong, now, before it is built.

Then stop and wait. Do not write code in this reply. Do not start building because the plan looks
obvious — Oliver has asked for this step more than once, and the point of it is that he gets to
change the shape of the work before it exists.

## 3. Build it

Only after Oliver says yes, and only what step 2 listed. If the work turns out to need a file that
was not on the list, stop and say so before touching it.

Then run, and fix what they report:

- `pytest -q`
- `ruff check .`

## 4. Run both reviewers

Launch both with the Agent tool, in one message so they run at the same time:

- `pipeline-rules-reviewer`
- `plan-conformance-reviewer` — tell it the stage number.

Read what they say. Fix what they find, or say plainly why you are not fixing it. Both must have run
before you commit anything under `src/rfp_intake/` or `config/`: a hook blocks that commit otherwise.

## 5. Report in exactly six lines

One line each, in this order, no headings, no bullets, nothing extra:

1. Whether the stage matches what the PLAN section said, and the one thing that differs if anything does.
2. Test counts from `pytest -q` — passed, failed, skipped — and the count before this stage.
3. The run id, and the confirmed fields phrased as **"N of the original 36 fields"** so the number stays
   comparable with earlier runs. If no run was made, say "no run" and why.
4. The single biggest thing still blocking this stage, or "nothing blocking".
5. Commits ahead of the remote, from `git rev-list --count origin/main..HEAD`, and whether they are pushed.
   You cannot push from this session; say that Oliver needs to push from a terminal.
6. One question for Oliver — the one whose answer changes what you do next. Only one.

Plain words. Expand any abbreviation the first time. Never say a count is confirmed unless a command
you ran in this session printed it.
