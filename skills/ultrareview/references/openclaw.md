# OpenClaw Adapter

OpenClaw agents can use this skill as a local project skill. The runtime remains file and SQLite based, so OpenClaw, Claude Code, and Copilot can all exchange state through the same run directory.

For OpenClaw delegation:

- Use the packet JSON as the subagent prompt input.
- Write outputs into `<run_dir>/outputs/`.
- Use the record scripts to validate and persist agent results.
- Do not bypass the verifier/judge gate.

