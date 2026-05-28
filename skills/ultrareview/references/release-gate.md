# Release Gate

Before installing or copying this skill/runtime elsewhere:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src
.venv/bin/python -m pip install -e .
.venv/bin/own-ultrareview --help
```

Required result:

- all tests pass,
- Python files compile,
- editable install succeeds,
- console entrypoint is available,
- `skills/ultrareview/SKILL.md` has frontmatter,
- schema, prompt, and adapter files exist.
