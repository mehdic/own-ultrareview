# UltraReview Test Scenario Inventory

This suite tests the runtime around the LLM instead of trying to test model intelligence.

Covered scenarios:

- SQLite schema creation and foreign-key enforcement.
- Run, task, candidate, verification, and final-finding persistence.
- Pending/running/completed/failed task state transitions.
- Candidate and verification contract validation.
- Git-only context collection: refs, merge-base, changed files, stats, diff, commit log, instruction files.
- Init script run directory and database creation.
- Git context script artifact creation.
- Scout packet generation for all reviewer roles.
- Sequential task leasing for Copilot/VS Code style sub-agents.
- Scout output recording with valid and invalid simulated LLM outputs.
- Verifier task generation and idempotency.
- Verifier output recording.
- Judge behavior for verified, rejected, and uncertain verdicts.
- Report generation with findings and with no findings.
- Full CLI chain from start to final report.
- Distribution assets: schemas, prompts, adapters, release gate.
- Self-review: a copied Own UltraReview repo is intentionally broken, then reviewed end-to-end with simulated LLM scout/verifier outputs.

The LLM work is represented by deterministic JSON files. Everything else is real runtime code, real SQLite state, real git repositories, and real CLI calls.

