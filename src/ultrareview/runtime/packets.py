from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ultrareview.runtime import db


@dataclass(frozen=True)
class ScoutRole:
    name: str
    objective: str
    focus: tuple[str, ...]


SCOUT_ROLES = (
    ScoutRole(
        "diff_cartographer",
        "Map changed files, touched symbols, call/dependency edges, and API/config/schema surfaces.",
        ("changed files", "symbols", "dependency edges", "public surfaces"),
    ),
    ScoutRole(
        "instruction_reviewer",
        "Apply local repository instructions and review rules to the changed scope.",
        ("AGENTS.md", "CLAUDE.md", "REVIEW.md", "repo conventions"),
    ),
    ScoutRole(
        "history_reviewer",
        "Inspect relevant recent history for repeated mistakes and hidden behavioral context.",
        ("recent commits", "similar changes", "past regressions"),
    ),
    ScoutRole(
        "correctness_reviewer",
        "Find concrete logic, state, control-flow, and data-shape bugs introduced by the diff.",
        ("logic errors", "bad conditions", "state bugs", "invalid assumptions"),
    ),
    ScoutRole(
        "security_reviewer",
        "Find concrete security and privacy bugs introduced or exposed by the diff.",
        ("auth", "injection", "SSRF", "path traversal", "tenant isolation", "PII logs"),
    ),
    ScoutRole(
        "regression_reviewer",
        "Check backward compatibility, migrations, rollback paths, and public API behavior.",
        ("compatibility", "migrations", "public APIs", "rollback"),
    ),
    ScoutRole(
        "edge_case_reviewer",
        "Hunt null, empty, large, concurrent, timezone, retry, idempotency, and error-path cases.",
        ("null", "empty", "large input", "concurrency", "timezone", "retries"),
    ),
    ScoutRole(
        "docs_comment_verifier",
        "Flag docs, comments, tests, and examples that now lie because of the change.",
        ("comments", "docs", "tests", "examples"),
    ),
)


SEVERITY_TAXONOMY = {
    "critical": "Blocks release immediately: exploitable security, data loss, outage, or irreversible corruption.",
    "must_change": "Blocks merge: real correctness, security, compatibility, migration, or operational defect.",
    "better_to_change": "Concrete risk reduction or maintainability improvement; not a merge blocker.",
    "acceptable": "Intentional tradeoff, harmless issue, style-only note, or verified false alarm.",
}


def _packet_for(
    run_id: str,
    role: ScoutRole,
    git_context_path: Path,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "phase": "scouting",
        "role": role.name,
        "objective": role.objective,
        "focus": list(role.focus),
        "inputs": {
            "git_context_path": str(git_context_path),
            "rules": [
                "Use git-derived evidence only.",
                "Report only issues introduced or exposed by the diff.",
                "Prefer no finding over speculative noise.",
                "Every candidate must include file, line, failure mode, and evidence.",
                "Return top-level JSON object with exactly the `candidates` array; do not use `findings`.",
                "confidence must be an integer from 0 to 100, never a string.",
                "evidence must be a non-empty array of objects with path, positive integer line, and exact quote.",
                "introduced_by_diff must explain why the diff introduced or exposed the issue.",
                "suggested_fix must explain the concrete remediation.",
            ],
        },
        "severity_taxonomy": SEVERITY_TAXONOMY,
        "output_contract": {
            "format": "candidate_findings_json",
            "top_level_key": "candidates",
            "required_candidate_fields": [
                "title",
                "category",
                "severity",
                "confidence",
                "file",
                "line",
                "introduced_by_diff",
                "claim",
                "failure_mode",
                "evidence",
                "suggested_fix",
            ],
            "field_contract": {
                "confidence": "Integer from 0 to 100, for example 91.",
                "evidence": "Non-empty array of objects; every object must include repo-relative path, positive integer line, and exact quote.",
                "introduced_by_diff": "Non-empty string explaining why the diff introduced or exposed the issue.",
                "suggested_fix": "Non-empty string with the concrete remediation.",
            },
            "example": {
                "candidates": [
                    {
                        "title": "Tenant guard compares user to itself",
                        "category": "security",
                        "severity": "must_change",
                        "confidence": 91,
                        "file": "app.py",
                        "line": 12,
                        "introduced_by_diff": "The diff changed the tenant guard to compare the user company to itself.",
                        "claim": "The invoice tenant is not checked.",
                        "failure_mode": "A user can view another tenant's invoice.",
                        "evidence": [
                            {
                                "path": "app.py",
                                "line": 12,
                                "quote": "user.company_id == user.company_id",
                            }
                        ],
                        "suggested_fix": "Compare user.company_id to invoice.company_id.",
                    }
                ]
            },
        },
    }


def build_scout_tasks(
    conn,
    run_id: str,
    run_dir: str | Path,
    git_context_path: str | Path,
) -> list[dict[str, Any]]:
    root = Path(run_dir)
    context_path = Path(git_context_path)
    tasks: list[dict[str, Any]] = []
    for role in SCOUT_ROLES:
        packet_path = root / "packets" / f"scout-{role.name}.json"
        packet_path.parent.mkdir(parents=True, exist_ok=True)
        packet_path.write_text(
            json.dumps(_packet_for(run_id, role, context_path), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        task = db.create_task(
            conn,
            run_id,
            role.name,
            "scouting",
            str(packet_path.relative_to(root)),
        )
        tasks.append(task)
    return tasks
