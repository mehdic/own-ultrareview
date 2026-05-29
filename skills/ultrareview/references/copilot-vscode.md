# Copilot in VS Code Custom Agent

Use a workspace custom agent, not a plain prompt file. The custom agent gives Copilot a persistent UltraReview persona, tool permissions, subagent access, and detailed workflow instructions.

Hard rule: Do not use `gh`, GitHub APIs, GitHub connectors, or browser PR access. Use `git` only.

Hard rule: do not edit `.ultrareview/runs/<run_id>/review.sqlite` directly. Scout and verifier outputs must be recorded only through `own-ultrareview record-output` and `own-ultrareview record-verification`.

## Install the Custom Agent

Copy:

```text
.github/tools/ultrareview/skills/ultrareview/references/ultrareview.agent.md
```

to:

```text
<repo>/.github/agents/ultrareview.agent.md
```

VS Code detects workspace custom agents from `.github/agents`. Open the Chat agent dropdown and select `UltraReview`.

Use Chat Diagnostics if the agent does not appear. Custom agents use `.agent.md`; old `.chatmode.md` files should be renamed.

## Install the Runtime in the Repository

The `own-ultrareview` command does not exist until the full package is installed. Unzip the package inside the repository you want Copilot to review:

```text
<repo>/
  .github/
    agents/
      ultrareview.agent.md
    tools/
      ultrareview/
        pyproject.toml
        src/
        skills/
        tests/
```

Install it from the VS Code terminal.

macOS/Linux:

```bash
cd .github/tools/ultrareview
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

Windows PowerShell:

```powershell
cd .github\tools\ultrareview
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

After install, the command is created by `pyproject.toml`:

```toml
[project.scripts]
own-ultrareview = "ultrareview.cli:main"
```

Use the explicit venv path when Copilot runs commands so PATH activation is not ambiguous.

macOS/Linux:

```bash
./.github/tools/ultrareview/.venv/bin/own-ultrareview --help
```

Windows PowerShell:

```powershell
.\.github\tools\ultrareview\.venv\Scripts\own-ultrareview.exe --help
```

Workflow:

1. Select the `UltraReview` custom agent in VS Code Chat.
2. Ask it to review the current branch against the desired base, for example: `Review this branch against origin/main`.
3. The agent must verify the runtime command works with the explicit venv path above.
4. The agent must run the start command from the repo root:
   - macOS/Linux: `./.github/tools/ultrareview/.venv/bin/own-ultrareview start --repo . --base origin/main --head HEAD`
   - Windows PowerShell: `.\.github\tools\ultrareview\.venv\Scripts\own-ultrareview.exe start --repo . --base origin/main --head HEAD`
5. The agent must start available scout packets as parallel Copilot sub-agents if VS Code exposes that capability. If not, it must lease packets one at a time with the explicit runtime path and `next --db <db_path>`.
6. The agent must save each sub-agent JSON under `<run_dir>/outputs/`.
7. The agent must record each output with `record-output` for scout tasks or `record-verification` for verifier tasks, again using the explicit runtime path.
8. A verifier task is not complete unless `record-verification` reports `inserted_verifications: 1`; never repair this by updating SQLite task status manually.
9. The agent must run verifier preparation, verifier tasks, judge, and report.
10. Verifier JSON must include `candidate_id`, `verdict`, `reason`, and non-empty `evidence` on the first attempt. Each evidence item must include `path`, positive integer `line`, and exact `quote`.
11. The agent must create a self-contained HTML decision report at `<run_dir>/reports/ultrareview-report.html`.
12. Decision gate: do not ask the user for choices until every scout/verifier task is completed through the CLI, `judge` has completed, `report` has returned `html_path`, the HTML report exists, and `actions` has completed successfully.
13. The agent must present the HTML report path/link before asking for user decisions.
14. The agent must present all verified findings and decision questions in one batch, with no empty action column. Each row must include recommended action, concrete suggested fix, fix group, risk if not fixed, risk of fix, and effort.
15. If the user chooses any fix, the agent must create `<run_dir>/plans/ultrareview-implementation-plan.md` and `<run_dir>/plans/ultrareview-implementation-plan.html` before touching source code.
16. The implementation plan must cover scope, root cause, technical approach, alternatives, ordered steps, test creation/updates, exact verification commands, security considerations, operational risk, rollback, and acceptance criteria.
17. The agent must present both implementation plan paths and wait for explicit user approval.
18. Only after plan approval may the agent implement selected fixes, record resolutions, and run `summary` through the explicit runtime path.

The SQLite database is the communication bus. The conversation does not need to remember everything.

The HTML report must include executive summary, risk matrix, decision table, consolidated fix groups, finding detail, verification plan, and decision checklist. It must explain each issue's risk, how the fix would work, the risk of the fix, possible impact, verification commands, and rollback plan.

Implementation plans must follow recognized engineering standards: Google Engineering Practices for small reviewable changes and test/readability/design discipline; NIST SSDF SP 800-218 for secure SDLC and vulnerability risk mitigation; OWASP Secure Coding Practices and OWASP Code Review Guide for security-sensitive implementation checks.

The summary must state exactly which agents ran, what they found, what was fixed, and whether the run used real subagents or simulated JSON. Consolidated fix groups must include proposed patch, risk if not fixed, risk of patch, effort, verification, and rollback notes.
