# OpenClaw Adapter

OpenClaw agents can use this skill as a local project skill. The runtime remains file and SQLite based, so OpenClaw, Claude Code, and Copilot can all exchange state through the same run directory.

For OpenClaw delegation:

- Use the packet JSON as the subagent prompt input.
- Dispatch scout and verifier subagents in parallel whenever the host supports it; use sequential leasing only as the fallback.
- Write outputs into `<run_dir>/outputs/`.
- Use the record scripts to validate and persist agent results.
- Do not bypass the verifier/judge gate.
- After judging, present all verified findings and all decision questions in one batch.
- After the user decides, implement all selected fixes together, record resolutions, and emit a summary.
- The summary must state exactly which agents ran, what they found, what was fixed, and whether work was real subagents or simulated JSON.
