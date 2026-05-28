# Own UltraReview

Local UltraReview-style code review runtime for Copilot in VS Code, Claude Code, and OpenClaw.

The design is intentionally boring: git-only context collection, JSON task packets, SQLite state, parallel sub-agent handoff when supported, verifier gating, batched user decisions, and final Markdown/JSON reports. Sequential task leasing is the fallback for hosts that cannot parallelize.

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
own-ultrareview start --repo /path/to/repo --base origin/main --head HEAD
own-ultrareview next --db /path/to/repo/.ultrareview/runs/<run_id>/review.sqlite
```

Give packets to AI sub-agents in parallel when your host supports it. If it does not, lease one packet at a time with `next`. Save each JSON output, then record it:

```bash
own-ultrareview record-output --db <db_path> --task-id <task_id> --output <output.json>
```

After scout tasks produce candidates:

```bash
own-ultrareview prepare-verifiers --db <db_path>
own-ultrareview next --db <db_path>
own-ultrareview record-verification --db <db_path> --task-id <task_id> --output <verifier.json>
own-ultrareview judge --db <db_path>
own-ultrareview report --db <db_path>
own-ultrareview actions --db <db_path>
own-ultrareview decide --db <db_path> --finding-id <finding_id> --decision fix --note "Patch before merge."
own-ultrareview resolve --db <db_path> --finding-id <finding_id> --status fixed --summary "Patched tenant guard." --evidence "commit abc123"
own-ultrareview summary --db <db_path>
```

After verified findings are produced, present all findings and all decision questions in one batch. After the user chooses decisions, implement all selected fixes together, then record resolutions and emit the summary.

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
