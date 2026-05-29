# Own UltraReview

Local UltraReview-style code review runtime for Copilot in VS Code, Claude Code, and OpenClaw.

The design is intentionally boring: git-only context collection, JSON task packets, SQLite state, parallel sub-agent handoff when supported, verifier gating, batched user decisions, and final Markdown/JSON reports. Sequential task leasing is the fallback for hosts that cannot parallelize.

Claude Code packages expose the slash command as `/my-ultrareview` so it does not collide with the runtime executable name `own-ultrareview`.

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/own-ultrareview start --repo /path/to/repo --base origin/main --head HEAD
.venv/bin/own-ultrareview next --db /path/to/repo/.ultrareview/runs/<run_id>/review.sqlite
```

Give packets to AI sub-agents in parallel when your host supports it. If it does not, lease one packet at a time with `next`. Save each JSON output, then record it:

```bash
.venv/bin/own-ultrareview record-output --db <db_path> --task-id <task_id> --output <output.json>
```

After scout tasks produce candidates:

```bash
.venv/bin/own-ultrareview prepare-verifiers --db <db_path>
.venv/bin/own-ultrareview next --db <db_path>
.venv/bin/own-ultrareview record-verification --db <db_path> --task-id <task_id> --output <verifier.json>
.venv/bin/own-ultrareview next --db <db_path>
.venv/bin/own-ultrareview judge --db <db_path>
.venv/bin/own-ultrareview report --db <db_path>
.venv/bin/own-ultrareview actions --db <db_path>
.venv/bin/own-ultrareview decide --db <db_path> --finding-id <finding_id> --decision fix --note "Patch before merge."
.venv/bin/own-ultrareview summary --db <db_path>
```

After verified findings are produced, present all findings and all decision questions in one batch. If the user chooses any fixes, first create implementation-plan Markdown and HTML, present both paths, and wait for explicit approval before editing source code. Only after approval should selected fixes be implemented and recorded with `resolve`.

## Rules

- No `gh`.
- No GitHub API.
- No browser dependency.
- Use local `git` and local files.
- External repositories, if needed, belong under `<run_dir>/temp/external-repos/`.
- No finding reaches the report unless it survives verifier review.
- Every verified finding can be listed with available actions and assigned a persisted decision: `fix`, `accept_risk`, `ignore`, `defer`, or `needs_human`.
- Every run can emit a post-review audit summary showing exactly which agents ran, what they found, what was decided, what was fixed, and whether the work was real subagents or simulated JSON.

## Development Gate

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src
.venv/bin/python -m pip install -e .
.venv/bin/own-ultrareview --help
```
