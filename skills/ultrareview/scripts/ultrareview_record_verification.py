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
from ultrareview.validation.contracts import validate_verification  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record a verifier agent output.")
    parser.add_argument("--db", required=True, help="Path to review.sqlite.")
    parser.add_argument("--task-id", required=True, help="Verifier task id being recorded.")
    parser.add_argument("--output", required=True, help="Path to verifier JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    verifications = payload.get("verifications", [])
    if not isinstance(verifications, list):
        raise SystemExit("output field 'verifications' must be a list")

    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    task = conn.execute("select * from agent_tasks where id = ?", (args.task_id,)).fetchone()
    if task is None:
        raise SystemExit(f"unknown task id: {args.task_id}")

    inserted = []
    for index, verification in enumerate(verifications):
        result = validate_verification(verification)
        if not result.valid:
            db.mark_task_failed(conn, args.task_id, f"verification[{index}] invalid: {'; '.join(result.errors)}")
            raise SystemExit(f"verification[{index}] invalid: {'; '.join(result.errors)}")
        inserted.append(
            db.insert_verification(
                conn,
                task["run_id"],
                verification["candidate_id"],
                args.task_id,
                verification,
            )
        )

    db.mark_task_completed(conn, args.task_id, str(output_path))
    conn.close()

    print(
        json.dumps(
            {
                "run_id": task["run_id"],
                "task_id": args.task_id,
                "inserted_verifications": len(inserted),
                "next": "run ultrareview_judge.py",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

