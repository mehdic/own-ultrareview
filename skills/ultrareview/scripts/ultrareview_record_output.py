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
from ultrareview.validation.contracts import validate_candidate  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record a sequential UltraReview agent output.")
    parser.add_argument("--db", required=True, help="Path to review.sqlite.")
    parser.add_argument("--task-id", required=True, help="Agent task id being recorded.")
    parser.add_argument("--output", required=True, help="Path to agent JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        raise SystemExit("output field 'candidates' must be a list")

    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    task = conn.execute("select * from agent_tasks where id = ?", (args.task_id,)).fetchone()
    if task is None:
        raise SystemExit(f"unknown task id: {args.task_id}")
    if task["phase"] != "scouting":
        raise SystemExit("record-output only accepts scouting tasks; use ultrareview_record_verification.py for verification tasks")

    for index, candidate in enumerate(candidates):
        result = validate_candidate(candidate)
        if not result.valid:
            db.mark_task_failed(conn, args.task_id, f"candidate[{index}] invalid: {'; '.join(result.errors)}")
            raise SystemExit(f"candidate[{index}] invalid: {'; '.join(result.errors)}")

    if task["status"] == "completed":
        if task["output_path"] == str(output_path) and db.candidate_rows_match_output(conn, args.task_id, candidates):
            conn.close()
            print(
                json.dumps(
                    {
                        "run_id": task["run_id"],
                        "task_id": args.task_id,
                        "inserted_candidates": 0,
                        "next": "run ultrareview_next_task.py",
                    },
                    sort_keys=True,
                )
            )
            return 0
        raise SystemExit(f"task {args.task_id} is already completed with different recorded output")

    inserted = []
    for candidate in candidates:
        inserted.append(db.insert_candidate(conn, task["run_id"], args.task_id, candidate))

    db.mark_task_completed(conn, args.task_id, str(output_path))
    conn.close()

    print(
        json.dumps(
            {
                "run_id": task["run_id"],
                "task_id": args.task_id,
                "inserted_candidates": len(inserted),
                "next": "run ultrareview_next_task.py",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
