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

Runtime source location depends on the platform package:

```text
VS Code package: <repo>/.github/tools/ultrareview
Claude Code package: <repo>/tools/ultrareview
OpenClaw development checkout: the local own-ultrareview project directory
```

Preferred command after runtime installation:

```bash
own-ultrareview
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

When this skill is loaded from a platform package, use that package's explicit venv command. The `python skills/...` commands are for development from inside this project repository.

Generic quick start:

```bash
own-ultrareview start --repo "$PWD" --base origin/main --head HEAD
own-ultrareview next --db <run_dir>/review.sqlite
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
own-ultrareview actions --db <run_dir>/review.sqlite
own-ultrareview decide --db <run_dir>/review.sqlite --finding-id <finding_id> --decision fix --note "Patch before merge."
own-ultrareview resolve --db <run_dir>/review.sqlite --finding-id <finding_id> --status fixed --summary "Patched before merge." --evidence "commit abc123"
own-ultrareview summary --db <run_dir>/review.sqlite
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

## Configuration Inventory Continuity

Every scout must run a configuration inventory continuity check when the diff changes dependencies, frameworks, bootstrapping, auth/security libraries, or config loading. Compare before/after config-backed behavior across `application*.yml`, `application*.yaml`, `application*.properties`, Helm values, environment templates, secrets templates, and deployment overlays when present.

Flag deleted or silently reduced inventories of configured users, accounts, groups, roles, permissions, feature flags, endpoints, scheduled jobs, queues, credentials, tenants, or environment-specific overrides. Treat removed config namespaces, renamed properties, changed defaults, and fallback behavior introduced by dependency/framework migration as concrete regression evidence when they alter runtime behavior.

Security reviewers must specifically catch Spring Boot/Spring Security migrations where old auth configuration was replaced and configured users, passwords, roles, groups, per-environment account overrides, or authorization mappings were lost. Regression reviewers must specifically catch dependency/framework migration changes where removed config namespaces, deleted accounts or roles, environment-specific drift, or changed defaults alter runtime behavior without an explicit migration.

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

The decision presentation must be actionable. Do not show an empty or vague `Action?` column. Every row must include:

- recommended action: `fix_before_merge`, `accept_risk`, `defer`, `needs_human`, or `ignore_duplicate`;
- suggested fix: a concrete engineering change;
- fix group: shared root cause for deduplication;
- risk if not fixed;
- risk of the fix;
- effort: `S`, `M`, `L`, or `XL` with a reason.

After the findings table, include consolidated fix groups with proposed patch, risk if not fixed, risk of patch, effort, verification, and rollback notes.

Before asking the user for decisions, create a self-contained HTML report at:

```text
<run_dir>/reports/ultrareview-report.html
```

The report must be readable by opening the file directly in a browser and must include executive summary, risk matrix, decision table, consolidated fix groups, finding detail, verification plan, and decision checklist. It must explain each issue's risk, how the fix would work, the risk of the fix, likely impact, verification commands, and rollback plan. Provide the report path/link before the chat decision table so the user can inspect the richer report first.

Do not show any issues breakdown, findings table, action table, or fix-group summary until `actions` returns `decision_gate_complete: true` and an `html_path`. The first human-visible line of the decision message must be `HTML report: <html_path>`.

The `#` column in chat must use each finding's `display_index` from `actions` and from `final-report.json`. Never renumber, sort, filter, or regroup findings independently after `actions` returns. If the user asks to fix `#N`, map it only to the finding whose `display_index` is `N`, show that finding ID in the implementation plan scope, and never infer the target from the HTML row position or a locally regenerated table number.

After the user chooses decisions, do not implement selected fixes directly. Any user request to fix one issue, multiple issues, a fix group, or all issues means selected scope for an implementation plan only; it is not approval to edit. If any finding or fix group is selected for `fix` / `fix_before_merge`, first create an implementation plan in both Markdown and HTML:

```text
<run_dir>/plans/ultrareview-implementation-plan.md
<run_dir>/plans/ultrareview-implementation-plan.html
```

The plan must cover scope, grouped findings, root causes, proposed technical approach, alternatives considered, ordered implementation steps, new/updated tests, exact verification commands, security considerations, operational/release risk, rollback plan, and acceptance criteria. Base the plan on Google Engineering Practices for small reviewable changes, NIST SSDF SP 800-218 for secure SDLC/vulnerability mitigation, and OWASP secure coding/code review guidance for security-sensitive fixes.

Present both plan paths to the user:

```text
Implementation plan MD: <run_dir>/plans/ultrareview-implementation-plan.md
Implementation plan HTML: <run_dir>/plans/ultrareview-implementation-plan.html
```

Wait for explicit approval before editing source code. Do not edit source files, use edit/write tools, or run apply_patch before both implementation plan files exist and the user has explicitly approved the plan after seeing both paths. Only after the user approves the implementation plan should you implement selected fixes together, record each resolution, and emit the summary.

The summary must tell the user exactly:

- which tasks/agents ran and their statuses,
- what each agent found,
- what candidates and verified findings were produced,
- what the user decided for each finding,
- what was fixed or why it was not fixed,
- whether scout/verifier work used real subagents or simulated JSON.

If scout or verifier work was simulated, say that plainly in the human-facing summary. Do not imply real LLM sub-agents ran when the runtime only consumed deterministic JSON.
