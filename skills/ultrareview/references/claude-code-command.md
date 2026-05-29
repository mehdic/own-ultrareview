---
description: Run Own UltraReview against a local base ref using the project UltraReview agent.
allowed-tools: Bash(git status:*), Bash(git branch:*), Bash(git rev-parse:*), Bash(test:*), Bash(python3:*), Bash(pip:*), Bash(./tools/ultrareview/.venv/bin/own-ultrareview:*)
---

# My UltraReview

Run the local Own UltraReview workflow for this repository.

Base ref argument:

```text
$ARGUMENTS
```

If `$ARGUMENTS` is empty, use `origin/main`.

First verify the runtime:

```bash
test -x ./tools/ultrareview/.venv/bin/own-ultrareview || (
  cd tools/ultrareview &&
  python3 -m venv .venv &&
  . .venv/bin/activate &&
  pip install -e .
)
./tools/ultrareview/.venv/bin/own-ultrareview --help
```

Then follow the `ultrareview` project agent instructions exactly:

- start the run;
- perform scout review;
- perform verifier review;
- judge and produce actions;
- write `<run_dir>/reports/ultrareview-report.html`;
- present the HTML report path first;
- present one actionable decision table;
- wait for user decisions;
- if any fix is selected, write `<run_dir>/plans/ultrareview-implementation-plan.md` and `<run_dir>/plans/ultrareview-implementation-plan.html`;
- present both plan paths and wait for explicit user approval before editing code.

Do not use `gh`, GitHub APIs, or browser PR pages.

Use local `git` only.
