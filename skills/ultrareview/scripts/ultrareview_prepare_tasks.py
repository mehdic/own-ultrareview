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
from ultrareview.runtime.packets import build_scout_tasks  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare sequential UltraReview scout task packets.")
    parser.add_argument("--db", required=True, help="Path to review.sqlite.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    run_dir = db_path.parent
    git_context_path = run_dir / "artifacts" / "git-context.json"
    if not git_context_path.exists():
        raise SystemExit(f"missing git context artifact: {git_context_path}")

    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = conn.execute("select * from runs order by created_at limit 1").fetchone()
    if run is None:
        raise SystemExit(f"no run found in {db_path}")

    existing = conn.execute("select count(*) from agent_tasks where run_id = ?", (run["id"],)).fetchone()[0]
    if existing:
        tasks = [dict(row) for row in conn.execute("select * from agent_tasks where run_id = ? order by rowid", (run["id"],))]
    else:
        tasks = build_scout_tasks(conn, run["id"], run_dir, git_context_path)
    conn.close()

    print(
        json.dumps(
            {
                "run_id": run["id"],
                "task_count": len(tasks),
                "next": "run ultrareview_next_task.py",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

