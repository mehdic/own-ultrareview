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
    parser = argparse.ArgumentParser(description="Promote verified UltraReview candidates into final findings.")
    parser.add_argument("--db", required=True, help="Path to review.sqlite.")
    return parser.parse_args()


def _report(candidate: sqlite3.Row, verification: sqlite3.Row) -> dict[str, object]:
    return {
        "category": candidate["category"],
        "severity": candidate["severity"],
        "confidence": candidate["confidence"],
        "file": candidate["file_path"],
        "line": candidate["line"],
        "claim": candidate["claim"],
        "failure_mode": candidate["failure_mode"],
        "evidence": json.loads(candidate["evidence_json"]),
        "verification_verdict": verification["verdict"],
        "verification_reason": verification["reason"],
        "verification_evidence": json.loads(verification["evidence_json"]),
    }


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = conn.execute("select * from runs order by created_at limit 1").fetchone()
    if run is None:
        raise SystemExit(f"no run found in {db_path}")

    rows = conn.execute(
        """
        select c.*, v.id as verification_id, v.verdict, v.reason, v.evidence_json as verification_evidence_json
        from candidates c
        join verifications v on v.candidate_id = c.id
        where v.verdict = 'verified'
          and not exists (
            select 1 from final_findings f where f.candidate_id = c.id
          )
        order by c.created_at, c.rowid
        """
    ).fetchall()

    inserted = []
    for row in rows:
        verification = {
            "verdict": row["verdict"],
            "reason": row["reason"],
            "evidence_json": row["verification_evidence_json"],
        }
        inserted.append(
            db.insert_final_finding(
                conn,
                run["id"],
                row["id"],
                {
                    "final_severity": row["severity"],
                    "confidence": row["confidence"],
                    "report": _report(row, verification),
                },
            )
        )

    conn.close()
    print(
        json.dumps(
            {
                "run_id": run["id"],
                "final_finding_count": len(inserted),
                "next": "run ultrareview_report.py",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
