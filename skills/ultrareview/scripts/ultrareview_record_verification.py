#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from json import JSONDecodeError
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

    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    task = conn.execute("select * from agent_tasks where id = ?", (args.task_id,)).fetchone()
    if task is None:
        raise SystemExit(f"unknown task id: {args.task_id}")
    if task["phase"] != "verification":
        raise SystemExit("record-verification only accepts verification tasks; use record-output for scouting tasks")

    packet_path = db_path.parent / task["input_path"]
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    expected_candidate_id = packet["candidate"]["id"]

    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        db.mark_task_failed(conn, args.task_id, f"output file not found: {output_path}")
        raise SystemExit(f"output file not found: {output_path}") from exc
    except JSONDecodeError as exc:
        db.mark_task_failed(conn, args.task_id, f"output JSON invalid: {exc.msg}")
        raise SystemExit(f"output JSON invalid: {exc.msg}") from exc
    if not isinstance(payload, dict):
        db.mark_task_failed(conn, args.task_id, "output JSON must be an object")
        raise SystemExit("output JSON must be an object")

    if "verifications" not in payload:
        db.mark_task_failed(conn, args.task_id, "output missing required top-level 'verifications' array")
        raise SystemExit("output missing required top-level 'verifications' array")
    verifications = payload["verifications"]
    if not isinstance(verifications, list):
        db.mark_task_failed(conn, args.task_id, "output field 'verifications' must be a list")
        raise SystemExit("output field 'verifications' must be a list")
    if len(verifications) != 1:
        db.mark_task_failed(conn, args.task_id, "verification task output must contain exactly one verification")
        raise SystemExit("verification task output must contain exactly one verification")
    if verifications[0].get("candidate_id") != expected_candidate_id:
        db.mark_task_failed(conn, args.task_id, "verification candidate_id does not match task packet")
        raise SystemExit("verification candidate_id does not match task packet")

    for index, verification in enumerate(verifications):
        result = validate_verification(verification)
        if not result.valid:
            db.mark_task_failed(conn, args.task_id, f"verification[{index}] invalid: {'; '.join(result.errors)}")
            raise SystemExit(f"verification[{index}] invalid: {'; '.join(result.errors)}")

    if task["status"] == "completed":
        if task["output_path"] == str(output_path) and db.verification_rows_match_output(conn, args.task_id, verifications):
            conn.close()
            print(
                json.dumps(
                    {
                        "run_id": task["run_id"],
                        "task_id": args.task_id,
                        "inserted_verifications": 0,
                        "next": "run ultrareview_next_task.py",
                    },
                    sort_keys=True,
                )
            )
            return 0
        raise SystemExit(f"task {args.task_id} is already completed with different recorded output")

    inserted = []
    for verification in verifications:
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
                "next": "run ultrareview_next_task.py",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
