from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from ultrareview.runtime import db
from ultrareview.runtime.packets import build_scout_tasks


def create_running_task(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    db_path = tmp_path / "run" / "review.sqlite"
    run_dir = db_path.parent
    conn = db.connect(db_path)
    db.init_schema(conn)
    run = db.create_run(conn, str(tmp_path / "repo"), "origin/main", "HEAD", "copilot-git-only")
    git_context_path = run_dir / "artifacts" / "git-context.json"
    git_context_path.parent.mkdir(parents=True)
    git_context_path.write_text('{"changed_files": []}', encoding="utf-8")
    build_scout_tasks(conn, run["id"], run_dir, git_context_path)
    task = db.next_task(conn, run["id"])
    db.mark_task_running(conn, task["id"])
    conn.close()
    return db_path, {"run_id": run["id"], "task_id": task["id"]}


def candidate_output(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "title": "Tenant check compares user to itself",
                        "category": "security",
                        "severity": "must_change",
                        "confidence": 91,
                        "file": "app.py",
                        "line": 12,
                        "introduced_by_diff": "The diff changed the tenant guard to compare the user to itself.",
                        "claim": "The invoice tenant is not checked.",
                        "failure_mode": "A user can view another tenant's invoice.",
                        "evidence": [
                            {
                                "path": "app.py",
                                "line": 12,
                                "quote": "user.company_id == user.company_id",
                            }
                        ],
                        "suggested_fix": "Compare user.company_id to invoice.company_id.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_record_output_inserts_candidates_and_completes_task(tmp_path):
    db_path, ids = create_running_task(tmp_path)
    output_path = tmp_path / "agent-output.json"
    candidate_output(output_path)

    script = Path("skills/ultrareview/scripts/ultrareview_record_output.py").resolve()
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db_path),
            "--task-id",
            ids["task_id"],
            "--output",
            str(output_path),
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    conn = sqlite3.connect(db_path)
    task_row = conn.execute("select status, output_path from agent_tasks where id = ?", (ids["task_id"],)).fetchone()
    candidate_row = conn.execute(
        "select severity, claim, introduced_by_diff, suggested_fix from candidates"
    ).fetchone()

    assert payload["inserted_candidates"] == 1
    assert payload["next"] == "run ultrareview_next_task.py"
    assert task_row == ("completed", str(output_path))
    assert candidate_row == (
        "must_change",
        "The invoice tenant is not checked.",
        "The diff changed the tenant guard to compare the user to itself.",
        "Compare user.company_id to invoice.company_id.",
    )


def test_record_output_replay_same_completed_scout_output_is_idempotent(tmp_path):
    db_path, ids = create_running_task(tmp_path)
    output_path = tmp_path / "agent-output.json"
    candidate_output(output_path)
    script = Path("skills/ultrareview/scripts/ultrareview_record_output.py").resolve()
    command = [
        sys.executable,
        str(script),
        "--db",
        str(db_path),
        "--task-id",
        ids["task_id"],
        "--output",
        str(output_path),
    ]

    subprocess.run(command, cwd=Path.cwd(), text=True, capture_output=True, check=True)
    replay = subprocess.run(command, cwd=Path.cwd(), text=True, capture_output=True, check=True)

    payload = json.loads(replay.stdout)
    conn = sqlite3.connect(db_path)
    candidate_count = conn.execute("select count(*) from candidates where source_task_id = ?", (ids["task_id"],)).fetchone()[0]

    assert payload["inserted_candidates"] == 0
    assert candidate_count == 1


def test_record_output_replay_different_completed_scout_output_fails(tmp_path):
    db_path, ids = create_running_task(tmp_path)
    output_path = tmp_path / "agent-output.json"
    candidate_output(output_path)
    script = Path("skills/ultrareview/scripts/ultrareview_record_output.py").resolve()
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db_path),
            "--task-id",
            ids["task_id"],
            "--output",
            str(output_path),
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )
    changed_output = tmp_path / "changed-output.json"
    changed_output.write_text(json.dumps({"candidates": []}), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(db_path),
            "--task-id",
            ids["task_id"],
            "--output",
            str(changed_output),
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
    )

    conn = sqlite3.connect(db_path)
    candidate_count = conn.execute("select count(*) from candidates where source_task_id = ?", (ids["task_id"],)).fetchone()[0]

    assert result.returncode != 0
    assert "already completed" in result.stderr
    assert candidate_count == 1


def test_prepare_verifiers_creates_one_verifier_task_per_candidate(tmp_path):
    db_path, ids = create_running_task(tmp_path)
    output_path = tmp_path / "agent-output.json"
    candidate_output(output_path)
    record_script = Path("skills/ultrareview/scripts/ultrareview_record_output.py").resolve()
    subprocess.run(
        [
            sys.executable,
            str(record_script),
            "--db",
            str(db_path),
            "--task-id",
            ids["task_id"],
            "--output",
            str(output_path),
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )

    script = Path("skills/ultrareview/scripts/ultrareview_prepare_verifiers.py").resolve()
    result = subprocess.run(
        [sys.executable, str(script), "--db", str(db_path)],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "select role, phase, status, input_path from agent_tasks where phase = 'verification'"
    ).fetchone()
    packet = json.loads((db_path.parent / row[3]).read_text(encoding="utf-8"))

    assert payload["verifier_task_count"] == 1
    assert payload["next"] == "run ultrareview_next_task.py"
    assert row[:3] == ("verifier_agent", "verification", "pending")
    assert packet["candidate"]["claim"] == "The invoice tenant is not checked."
    assert packet["verdict_contract"]["allowed"] == ["verified", "rejected", "uncertain"]
