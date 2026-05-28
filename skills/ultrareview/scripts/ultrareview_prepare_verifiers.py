#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ultrareview.runtime import db  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create verifier tasks for recorded UltraReview candidates.")
    parser.add_argument("--db", required=True, help="Path to review.sqlite.")
    return parser.parse_args()


def _candidate_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "category": row["category"],
        "severity": row["severity"],
        "confidence": row["confidence"],
        "file": row["file_path"],
        "line": row["line"],
        "claim": row["claim"],
        "failure_mode": row["failure_mode"],
        "evidence": json.loads(row["evidence_json"]),
    }


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    run_dir = db_path.parent
    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = conn.execute("select * from runs order by created_at limit 1").fetchone()
    if run is None:
        raise SystemExit(f"no run found in {db_path}")

    candidates = conn.execute(
        """
        select c.* from candidates c
        where not exists (
          select 1 from agent_tasks t
          where t.run_id = c.run_id
            and t.phase = 'verification'
            and t.input_path like '%' || c.id || '%'
        )
        order by c.created_at, c.rowid
        """
    ).fetchall()

    created = []
    for candidate in candidates:
        packet_path = run_dir / "packets" / f"verify-{candidate['id']}.json"
        packet = {
            "run_id": run["id"],
            "phase": "verification",
            "role": "verifier_agent",
            "objective": "Try to disprove this candidate bug before it can be reported.",
            "candidate": _candidate_payload(candidate),
            "instructions": [
                "Assume the scout may be wrong.",
                "Look for code paths, guards, tests, or constraints that invalidate the claim.",
                "Return verified only when the failure mode is concrete and diff-related.",
                "Return rejected for false positives. Return uncertain when evidence is insufficient.",
            ],
            "verdict_contract": {
                "allowed": ["verified", "rejected", "uncertain"],
                "required_fields": ["candidate_id", "verdict", "reason", "evidence"],
            },
        }
        packet_path.parent.mkdir(parents=True, exist_ok=True)
        packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")
        created.append(
            db.create_task(
                conn,
                run["id"],
                "verifier_agent",
                "verification",
                str(packet_path.relative_to(run_dir)),
            )
        )

    conn.close()
    print(
        json.dumps(
            {
                "run_id": run["id"],
                "verifier_task_count": len(created),
                "next": "run ultrareview_next_task.py",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
