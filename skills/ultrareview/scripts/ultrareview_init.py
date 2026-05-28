#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ultrareview.runtime import db  # noqa: E402


RUN_SUBDIRS = (
    "artifacts",
    "packets",
    "outputs",
    "validation",
    "temp/external-repos",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize an Own UltraReview run workspace and SQLite state database."
    )
    parser.add_argument("--repo", required=True, help="Repository path to review.")
    parser.add_argument("--base", required=True, help="Base git ref, e.g. origin/main.")
    parser.add_argument("--head", default="HEAD", help="Head git ref, default: HEAD.")
    parser.add_argument(
        "--mode",
        default="copilot-git-only",
        help="Runtime mode. Default: copilot-git-only.",
    )
    parser.add_argument(
        "--runs-root",
        help="Optional run storage root. Default: <repo>/.ultrareview/runs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_path = Path(args.repo).expanduser().resolve()
    runs_root = (
        Path(args.runs_root).expanduser().resolve()
        if args.runs_root
        else repo_path / ".ultrareview" / "runs"
    )

    bootstrap_db_path = runs_root / "_bootstrap" / "review.sqlite"
    conn = db.connect(bootstrap_db_path)
    db.init_schema(conn)
    run = db.create_run(
        conn,
        repo_path=str(repo_path),
        base_ref=args.base,
        head_ref=args.head,
        mode=args.mode,
    )
    conn.close()

    run_dir = runs_root / run["id"]
    for subdir in RUN_SUBDIRS:
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)

    db_path = run_dir / "review.sqlite"
    if bootstrap_db_path != db_path:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        bootstrap_db_path.replace(db_path)
        try:
            bootstrap_db_path.parent.rmdir()
        except OSError:
            pass

    payload = {
        "run_id": run["id"],
        "run_dir": str(run_dir),
        "db_path": str(db_path),
        "next": "run ultrareview_git_context.py",
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
