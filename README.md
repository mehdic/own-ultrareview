# Own UltraReview

Local UltraReview-style code review runtime for Copilot in VS Code, Claude Code, and OpenClaw.

The design is intentionally boring: git-only context collection, JSON task packets, SQLite state, sequential sub-agent handoff, verifier gating, and final Markdown/JSON reports.

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
own-ultrareview start --repo /path/to/repo --base origin/main --head HEAD
own-ultrareview next --db /path/to/repo/.ultrareview/runs/<run_id>/review.sqlite
```

Give the returned packet to one AI sub-agent, save the JSON output, then record it:

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

## Rules

- No `gh`.
- No GitHub API.
- No browser dependency.
- Use local `git` and local files.
- External repositories, if needed, belong under `<run_dir>/temp/external-repos/`.
- No finding reaches the report unless it survives verifier review.
- Every verified finding can be listed with available actions and assigned a persisted decision: `fix`, `accept_risk`, `ignore`, `defer`, or `needs_human`.
- Every run can emit a post-review audit summary showing what ran, what was found, what was decided, and what was fixed.

## Development Gate

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src
.venv/bin/python -m pip install -e .
.venv/bin/own-ultrareview --help
```
