---
name: ultrareview
description: Use when deeply reviewing a local PR, branch, or diff with specialist agents, verifier checks, batched user decisions, and a final bug-focused report.
---

# UltraReview

Use this skill when the user asks to deeply review a PR, branch, or local diff with an UltraReview-style workflow: context mapping, specialist reviewers, adversarial verification, judging, and a final report.

## Interface

Ask for only what is missing:

- `repo`: local repository path; default to the current workspace repo.
- `base`: base git ref; default to `origin/main`.
- `head`: head git ref; default to `HEAD`.
- `mode`: default to `copilot-git-only`.

The workflow must use `git` commands only. Do not require `gh`, GitHub API access, GitHub connectors, or browser access. If another repository is needed for comparison, clone or fetch it into the run's temporary folder, never into the user's working tree.

## Runtime

Installed OpenClaw runtime:

```text
/Users/mehdichaouachi/.openclaw/workspace-roque/projects/own-ultrareview
```

Preferred command inside OpenClaw:

```bash
/Users/mehdichaouachi/.openclaw/bin/own-ultrareview
```

Run state lives under:

```text
<repo>/.ultrareview/runs/<run_id>/
```

Each run contains:

- `review.sqlite`: shared state database for run metadata, tasks, candidates, validations, and final findings.
- `artifacts/`: git context, dependency maps, static-analysis outputs, and generated indexes.
- `packets/`: task packets for sequential sub-agents.
- `outputs/`: sub-agent JSON outputs.
- `validation/`: verifier inputs and results.
- `temp/external-repos/`: temporary clones and external references.

## First Commands

When this skill is loaded from OpenClaw, use the installed CLI above. The `python skills/...` commands are for development from inside this project repository.

OpenClaw quick start:

```bash
/Users/mehdichaouachi/.openclaw/bin/own-ultrareview start --repo "$PWD" --base origin/main --head HEAD
/Users/mehdichaouachi/.openclaw/bin/own-ultrareview next --db <run_dir>/review.sqlite
```

Initialize:

```bash
python skills/ultrareview/scripts/ultrareview_init.py --repo "$PWD" --base origin/main --head HEAD --mode copilot-git-only
```

Collect git context:

```bash
python skills/ultrareview/scripts/ultrareview_git_context.py --db <run_dir>/review.sqlite
```

Prepare scout packets:

```bash
python skills/ultrareview/scripts/ultrareview_prepare_tasks.py --db <run_dir>/review.sqlite
```

Lease the next sequential sub-agent task:

```bash
python skills/ultrareview/scripts/ultrareview_next_task.py --db <run_dir>/review.sqlite
```

Record a scout sub-agent output:

```bash
python skills/ultrareview/scripts/ultrareview_record_output.py --db <run_dir>/review.sqlite --task-id <task_id> --output <agent-output.json>
```

Prepare verifier tasks after scout tasks produce candidates:

```bash
python skills/ultrareview/scripts/ultrareview_prepare_verifiers.py --db <run_dir>/review.sqlite
```

Record verifier output, judge verified candidates, and emit the report:

```bash
python skills/ultrareview/scripts/ultrareview_record_verification.py --db <run_dir>/review.sqlite --task-id <task_id> --output <verifier-output.json>
python skills/ultrareview/scripts/ultrareview_judge.py --db <run_dir>/review.sqlite
python skills/ultrareview/scripts/ultrareview_report.py --db <run_dir>/review.sqlite
```

List verified issues and record the user's decision:

```bash
/Users/mehdichaouachi/.openclaw/bin/own-ultrareview actions --db <run_dir>/review.sqlite
/Users/mehdichaouachi/.openclaw/bin/own-ultrareview decide --db <run_dir>/review.sqlite --finding-id <finding_id> --decision fix --note "Patch before merge."
/Users/mehdichaouachi/.openclaw/bin/own-ultrareview resolve --db <run_dir>/review.sqlite --finding-id <finding_id> --status fixed --summary "Patched before merge." --evidence "commit abc123"
/Users/mehdichaouachi/.openclaw/bin/own-ultrareview summary --db <run_dir>/review.sqlite
```

## Agent Sequence

Run scout and verifier subagents in parallel whenever the host supports parallelism. Use sequential leasing with `next` only as the fallback for hosts that cannot parallelize. Each agent reads a packet from `packets/`, writes JSON to `outputs/`, and records findings through the runtime scripts.

1. Diff Cartographer
2. Instruction Reviewer
3. History Reviewer
4. Correctness Reviewer
5. Security Reviewer
6. Regression Reviewer
7. Edge-Case Reviewer
8. Docs/Comment Verifier
9. Verifier Agents
10. Judge/Aggregator

In Copilot/VS Code, this runs as one master conversation plus parallel sub-agent calls when available, or sequential calls when the host cannot parallelize. The database is the communication bus. Each sub-agent gets one packet, returns one JSON file, and the scripts validate and persist the result.

## Severity Taxonomy

- `critical`: exploitable security issue, data loss, serious outage, irreversible corruption, or a bug that must block release immediately.
- `must_change`: real correctness, security, compatibility, or migration problem that should block merge.
- `better_to_change`: valid improvement with concrete risk reduction, but not a merge blocker.
- `acceptable`: intentional tradeoff, harmless issue, style-only note, or verified false alarm.

## Reporting Rule

Do not report a finding unless it has:

- file and line,
- concrete failure mode,
- evidence from code or tests,
- explanation of why the diff introduced or exposed it,
- verifier result: `verified`, `rejected`, or `uncertain`.

Prefer no finding over speculative noise.

## Decision Rule

After verified findings are produced, show the user all findings and all decision questions in one batch. Do not ask one issue at a time, and do not silently fix or ignore findings. Persist one of these decisions per finding:

- `fix`: implement or request a patch before merge.
- `accept_risk`: user accepts the risk for this PR.
- `ignore`: verified as non-actionable after human review.
- `defer`: valid issue but intentionally moved to later work.
- `needs_human`: requires product/security/domain owner decision.

After the user chooses decisions, implement all selected fixes together. Then record each resolution and emit the summary. The summary must tell the user exactly:

- which tasks/agents ran and their statuses,
- what each agent found,
- what candidates and verified findings were produced,
- what the user decided for each finding,
- what was fixed or why it was not fixed,
- whether scout/verifier work used real subagents or simulated JSON.

If scout or verifier work was simulated, say that plainly in the human-facing summary. Do not imply real LLM sub-agents ran when the runtime only consumed deterministic JSON.
