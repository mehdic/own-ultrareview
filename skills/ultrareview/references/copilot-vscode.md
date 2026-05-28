# Copilot in VS Code Adapter

Use the master Copilot chat as the orchestrator and Copilot sub-agents as sequential workers.

Hard rule: Do not use `gh`, GitHub APIs, GitHub connectors, or browser PR access. Use `git` only.

Workflow:

1. Run `own-ultrareview start --repo . --base origin/main --head HEAD`.
2. Run `own-ultrareview next --db <db_path>`.
3. Give the returned packet to one Copilot sub-agent.
4. Save the sub-agent JSON under `<run_dir>/outputs/`.
5. Record it with `own-ultrareview record-output` for scout tasks or `own-ultrareview record-verification` for verifier tasks.
6. Repeat until scout tasks are done.
7. Run verifier preparation, verifier tasks, judge, and report.

The SQLite database is the communication bus. The conversation does not need to remember everything.

