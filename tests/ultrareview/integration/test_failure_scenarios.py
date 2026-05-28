from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from ultrareview.runtime import db
from ultrareview.runtime.packets import build_scout_tasks


def cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("src").resolve())
    return env


def create_running_scout(tmp_path: Path) -> tuple[Path, str]:
    db_path = tmp_path / "run" / "review.sqlite"
    run_dir = db_path.parent
    conn = db.connect(db_path)
    db.init_schema(conn)
    run = db.create_run(conn, str(tmp_path / "repo"), "origin/main", "HEAD", "copilot-git-only")
    git_context = run_dir / "artifacts" / "git-context.json"
    git_context.parent.mkdir(parents=True)
    git_context.write_text("{}", encoding="utf-8")
    build_scout_tasks(conn, run["id"], run_dir, git_context)
    task = db.next_task(conn, run["id"])
    db.mark_task_running(conn, task["id"])
    conn.close()
    return db_path, task["id"]


def test_cli_record_output_rejects_invalid_candidate_and_marks_task_failed(tmp_path):
    db_path, task_id = create_running_scout(tmp_path)
    output = tmp_path / "bad-output.json"
    output.write_text(
        json.dumps({"candidates": [{"severity": "must_change", "confidence": 90}]}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ultrareview.cli",
            "record-output",
            "--db",
            str(db_path),
            "--task-id",
            task_id,
            "--output",
            str(output),
        ],
        cwd=Path.cwd(),
        env=cli_env(),
        text=True,
        capture_output=True,
    )

    conn = sqlite3.connect(db_path)
    row = conn.execute("select status, error from agent_tasks where id = ?", (task_id,)).fetchone()

    assert result.returncode != 0
    assert row[0] == "failed"
    assert "missing required field" in row[1]


def test_cli_prepare_verifiers_is_idempotent(tmp_path):
    db_path, task_id = create_running_scout(tmp_path)
    output = tmp_path / "good-output.json"
    output.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "title": "Bug",
                        "category": "correctness",
                        "severity": "must_change",
                        "confidence": 80,
                        "file": "app.py",
                        "line": 1,
                        "introduced_by_diff": "The diff changed the function output for this edge case.",
                        "claim": "The value is wrong.",
                        "failure_mode": "The caller receives an invalid value.",
                        "evidence": [{"path": "app.py", "line": 1, "quote": "return 0"}],
                        "suggested_fix": "Return the expected value.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "ultrareview.cli",
            "record-output",
            "--db",
            str(db_path),
            "--task-id",
            task_id,
            "--output",
            str(output),
        ],
        cwd=Path.cwd(),
        env=cli_env(),
        check=True,
        capture_output=True,
        text=True,
    )

    first = subprocess.run(
        [sys.executable, "-m", "ultrareview.cli", "prepare-verifiers", "--db", str(db_path)],
        cwd=Path.cwd(),
        env=cli_env(),
        check=True,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        [sys.executable, "-m", "ultrareview.cli", "prepare-verifiers", "--db", str(db_path)],
        cwd=Path.cwd(),
        env=cli_env(),
        check=True,
        capture_output=True,
        text=True,
    )

    conn = sqlite3.connect(db_path)
    count = conn.execute("select count(*) from agent_tasks where phase = 'verification'").fetchone()[0]

    assert json.loads(first.stdout)["verifier_task_count"] == 1
    assert json.loads(second.stdout)["verifier_task_count"] == 0
    assert count == 1
