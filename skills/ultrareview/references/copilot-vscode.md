# Copilot in VS Code Adapter

Use the master Copilot chat as the orchestrator and Copilot sub-agents as parallel workers when the host supports it. Sequential leasing is only the fallback.

Hard rule: Do not use `gh`, GitHub APIs, GitHub connectors, or browser PR access. Use `git` only.

Workflow:

1. Run `own-ultrareview start --repo . --base origin/main --head HEAD`.
2. Start all available scout packets as parallel Copilot sub-agents. If parallelism is unavailable, lease packets one at a time with `own-ultrareview next --db <db_path>`.
3. Save each sub-agent JSON under `<run_dir>/outputs/`.
4. Record each output with `own-ultrareview record-output` for scout tasks or `own-ultrareview record-verification` for verifier tasks.
5. Run verifier preparation, verifier tasks, judge, and report.
6. Present all verified findings and decision questions in one batch.
7. After the user decides, implement all selected fixes together, record resolutions, and run `own-ultrareview summary`.

The SQLite database is the communication bus. The conversation does not need to remember everything.

The summary must state exactly which agents ran, what they found, what was fixed, and whether the run used real subagents or simulated JSON.
