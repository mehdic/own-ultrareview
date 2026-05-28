from __future__ import annotations

import sqlite3

from ultrareview.runtime import db


def test_foreign_keys_prevent_orphan_task(tmp_path):
    conn = db.connect(tmp_path / "review.sqlite")
    db.init_schema(conn)

    try:
        db.create_task(conn, "missing_run", "correctness_reviewer", "scouting", "packet.json")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("orphan task was accepted")


def test_next_task_skips_running_completed_and_failed_tasks(tmp_path):
    conn = db.connect(tmp_path / "review.sqlite")
    db.init_schema(conn)
    run = db.create_run(conn, "/repo", "origin/main", "HEAD", "copilot-git-only")
    running = db.create_task(conn, run["id"], "diff_cartographer", "scouting", "one.json")
    failed = db.create_task(conn, run["id"], "security_reviewer", "scouting", "two.json")
    completed = db.create_task(conn, run["id"], "regression_reviewer", "scouting", "three.json")
    pending = db.create_task(conn, run["id"], "correctness_reviewer", "scouting", "four.json")

    db.mark_task_running(conn, running["id"])
    db.mark_task_failed(conn, failed["id"], "bad output")
    db.mark_task_completed(conn, completed["id"], "three.out.json")

    assert db.next_task(conn, run["id"])["id"] == pending["id"]


def test_deleting_run_cascades_runtime_rows(tmp_path):
    conn = db.connect(tmp_path / "review.sqlite")
    db.init_schema(conn)
    run = db.create_run(conn, "/repo", "origin/main", "HEAD", "copilot-git-only")
    scout = db.create_task(conn, run["id"], "security_reviewer", "scouting", "packet.json")
    verifier = db.create_task(conn, run["id"], "verifier_agent", "verification", "verify.json")
    candidate = db.insert_candidate(
        conn,
        run["id"],
        scout["id"],
        {
            "category": "security",
            "severity": "must_change",
            "confidence": 90,
            "file": "app.py",
            "line": 1,
            "claim": "Missing tenant check.",
            "failure_mode": "Cross-tenant access.",
            "evidence": [{"path": "app.py", "line": 1, "quote": "return invoice"}],
        },
    )
    db.insert_verification(
        conn,
        run["id"],
        candidate["id"],
        verifier["id"],
        {
            "candidate_id": candidate["id"],
            "verdict": "verified",
            "reason": "No guard exists.",
            "evidence": [{"path": "app.py", "line": 1, "quote": "return invoice"}],
        },
    )
    db.insert_final_finding(
        conn,
        run["id"],
        candidate["id"],
        {
            "final_severity": "must_change",
            "confidence": 90,
            "report": {"claim": "Missing tenant check."},
        },
    )

    conn.execute("delete from runs where id = ?", (run["id"],))
    conn.commit()

    for table in ("agent_tasks", "candidates", "verifications", "final_findings"):
        assert conn.execute(f"select count(*) from {table}").fetchone()[0] == 0

