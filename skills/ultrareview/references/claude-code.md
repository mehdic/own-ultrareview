# Claude Code Adapter

Claude Code can use the same scripts and packets. If parallel subagents are available, scout roles may run concurrently; if not, lease tasks sequentially with `own-ultrareview next`.

Keep the same rule as Copilot mode: prefer local `git` commands and local artifacts. Do not require GitHub API access.

For implementation work, run tests from the project root:

```bash
.venv/bin/python -m pytest -q
```

