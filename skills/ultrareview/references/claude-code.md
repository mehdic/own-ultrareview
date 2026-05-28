# Claude Code Adapter

Claude Code can use the same scripts and packets. Run scout and verifier subagents in parallel when available; if not, lease tasks sequentially with `own-ultrareview next`.

Keep the same rule as Copilot mode: prefer local `git` commands and local artifacts. Do not require GitHub API access.

After judging, present all verified findings and decision questions in one batch. After the user decides, implement all selected fixes together, record resolutions, and emit a summary stating which agents ran, what they found, what was fixed, and whether the run used real subagents or simulated JSON.

For implementation work, run tests from the project root:

```bash
.venv/bin/python -m pytest -q
```
