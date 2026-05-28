#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ultrareview.gitcontext.collect import collect_git_context  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect git-only review context for an Own UltraReview run."
    )
    parser.add_argument("--db", required=True, help="Path to review.sqlite.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = conn.execute("select * from runs order by created_at limit 1").fetchone()
    if run is None:
        raise SystemExit(f"no run found in {db_path}")

    context = collect_git_context(run["repo_path"], run["base_ref"], run["head_ref"])
    conn.execute(
        "update runs set base_sha = ?, head_sha = ?, updated_at = datetime('now') where id = ?",
        (context["base_sha"], context["head_sha"], run["id"]),
    )
    conn.commit()
    conn.close()

    run_dir = db_path.parent
    artifact_path = run_dir / "artifacts" / "git-context.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(context, indent=2, sort_keys=True), encoding="utf-8")

    print(
        json.dumps(
            {
                "run_id": run["id"],
                "artifact_path": str(artifact_path),
                "next": "run ultrareview_next_task.py",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
