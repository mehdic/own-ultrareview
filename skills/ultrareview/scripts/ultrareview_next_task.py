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
    parser = argparse.ArgumentParser(description="Lease the next pending sequential UltraReview task.")
    parser.add_argument("--db", required=True, help="Path to review.sqlite.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    run_dir = db_path.parent
    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = conn.execute("select * from runs order by created_at limit 1").fetchone()
    if run is None:
        raise SystemExit(f"no run found in {db_path}")

    task = db.next_task(conn, run["id"])
    if task is None:
        candidates_without_verifiers = conn.execute(
            """
            select count(*) from candidates c
            where c.run_id = ?
              and not exists (
                select 1 from agent_tasks t
                where t.run_id = c.run_id
                  and t.phase = 'verification'
                  and t.input_path like '%' || c.id || '%'
              )
            """,
            (run["id"],),
        ).fetchone()[0]
        if candidates_without_verifiers:
            print(
                json.dumps(
                    {
                        "run_id": run["id"],
                        "status": "needs_verification_setup",
                        "next": "run ultrareview_prepare_verifiers.py",
                    },
                    sort_keys=True,
                )
            )
            return 0
        print(json.dumps({"run_id": run["id"], "status": "complete", "next": "run ultrareview_judge.py"}, sort_keys=True))
        return 0

    db.mark_task_running(conn, task["id"])
    packet_path = run_dir / task["input_path"]
    payload = {
        "run_id": run["id"],
        "task_id": task["id"],
        "role": task["role"],
        "phase": task["phase"],
        "status": "running",
        "packet_path": str(packet_path),
        "handoff": "Give this packet to the next sequential sub-agent, then record its JSON output.",
        "next": "run ultrareview_record_output.py",
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
