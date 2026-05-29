# Claude Code Adapter for macOS

Claude Code should use Own UltraReview as a project-level custom subagent plus a project slash command.

This is different from the VS Code Copilot package:

- VS Code uses `.github/agents/ultrareview.agent.md`.
- Claude Code uses `.claude/agents/ultrareview.md`.
- Claude Code can also expose a slash command from `.claude/commands/my-ultrareview.md`.

Claude Code custom subagents are loaded from `.claude/agents/` or `~/.claude/agents/`. Slash commands are loaded from `.claude/commands/` or `~/.claude/commands/`.

## Repository Layout

Install into the repository to be reviewed:

```text
<repo>/
  .claude/
    agents/
      ultrareview.md
    commands/
      my-ultrareview.md
  tools/
    ultrareview/
      pyproject.toml
      src/
      skills/
      tests/
```

## Mac Install

From the repository root:

```bash
cd tools/ultrareview
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cd ../..
./tools/ultrareview/.venv/bin/own-ultrareview --help
```

Use the explicit venv executable path in Claude Code instructions:

```bash
./tools/ultrareview/.venv/bin/own-ultrareview
```

Do not rely on shell PATH activation.

## How To Run

Option A, run the whole session as the UltraReview agent:

```bash
cd <repo>
claude --agent ultrareview
```

Then ask:

```text
Review this branch against origin/main.
```

Option B, use the project slash command:

```bash
cd <repo>
claude
```

Then inside Claude Code:

```text
/my-ultrareview origin/main
```

Use Option A for serious reviews because the agent prompt becomes the main session policy. Use Option B for convenience.

## Workflow Requirements

The Claude Code agent must:

1. Verify the runtime exists and install it if needed.
2. Run `start --repo . --base <base> --head HEAD`.
3. Read task packets from `<run_dir>/packets/`.
4. Use Claude Code subagents for scout and verifier work when available; otherwise run sequentially and disclose the fallback.
5. Save each scout/verifier JSON output under `<run_dir>/outputs/`.
6. Record outputs through the CLI.
7. Never update `review.sqlite` directly; use only `record-output` and `record-verification` for scout/verifier writes.
8. A verifier task is not complete unless `record-verification` reports `inserted_verifications: 1`.
9. Run `prepare-verifiers`, verifier tasks, `judge`, `report`, and `actions`.
10. Verifier JSON must include `candidate_id`, `verdict`, `reason`, and non-empty `evidence` on the first attempt. Each evidence item must include `path`, positive integer `line`, and exact `quote`.
11. Create `<run_dir>/reports/ultrareview-report.html` before asking for decisions.
12. Decision gate: do not ask the user for choices until every scout/verifier task is completed through the CLI, `judge` has completed, `report` has returned `html_path`, the HTML report exists, and `actions` has completed successfully.
13. Give the HTML report path first, then the concise decision table.
14. Present all user decisions in one batch.
15. If any fix is selected, create Markdown and HTML implementation plans under `<run_dir>/plans/`.
16. Present both implementation plan paths and wait for explicit user approval.
17. Apply only fixes approved through that implementation plan.
18. Record decisions and resolutions.
19. Emit `summary`.

## HTML Report Requirements

The report must be a self-contained HTML file with inline CSS. It should open directly from Finder or a browser.

Required sections:

- executive summary;
- risk matrix;
- actionable decision table;
- consolidated fix groups;
- detailed findings;
- verification plan;
- decision checklist.

Each fix group must include:

- root cause;
- affected findings;
- proposed patch;
- implementation steps;
- risk if not fixed;
- risk of patch;
- expected blast radius;
- verification commands;
- rollback plan.

## Output Contract

Before asking the user for choices, the agent must show:

```text
HTML report: <run_dir>/reports/ultrareview-report.html
Open this first if you want the readable version; the table below is the decision summary.
```

The chat table must include:

```text
Recommended action | Suggested fix | Fix group | Risk if not fixed | Risk of fix | Effort
```

Every duplicate must point to the canonical fix group and use `ignore_duplicate`.

## Implementation Plan Gate

Fixes must not be applied directly after the decision table. For any selected fix, Claude Code must first write:

```text
<run_dir>/plans/ultrareview-implementation-plan.md
<run_dir>/plans/ultrareview-implementation-plan.html
```

The implementation plan must cover:

- scope and grouped findings;
- root cause and proposed technical approach;
- alternatives considered;
- ordered implementation steps;
- new or updated tests;
- exact verification commands;
- security considerations;
- operational/release risk;
- rollback plan;
- acceptance criteria.

Use Google Engineering Practices for small reviewable changes and test/readability/design discipline; NIST SSDF SP 800-218 for secure SDLC and vulnerability mitigation; and OWASP Secure Coding Practices / Code Review Guide for security-sensitive fixes.

Wait for explicit user approval before editing source code.

## Official Claude Code References

- Subagents: https://code.claude.com/docs/en/sub-agents
- Slash commands: https://docs.anthropic.com/en/docs/claude-code/slash-commands
- Settings/subagent storage: https://docs.anthropic.com/en/docs/claude-code/settings
- Google Engineering Practices: https://google.github.io/eng-practices/
- NIST SSDF SP 800-218: https://csrc.nist.gov/pubs/sp/800/218/final
- OWASP Secure Coding Practices: https://owasp.org/www-project-secure-coding-practices-quick-reference-guide/
- OWASP Code Review Guide: https://owasp.org/www-project-code-review-guide/
