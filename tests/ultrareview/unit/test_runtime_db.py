from __future__ import annotations

import json
import sqlite3

from ultrareview.runtime import db


def test_schema_initializes_from_empty_db(tmp_path):
    conn = db.connect(tmp_path / "review.sqlite")
    db.init_schema(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "select name from sqlite_master where type = 'table'"
        ).fetchall()
    }

    assert {
        "runs",
        "agent_tasks",
        "candidates",
        "verifications",
        "final_findings",
    }.issubset(tables)


def test_create_run_creates_queued_run(tmp_path):
    conn = db.connect(tmp_path / "review.sqlite")
    db.init_schema(conn)

    run = db.create_run(
        conn,
        repo_path="/repo",
        base_ref="origin/main",
        head_ref="HEAD",
        mode="copilot-git-only",
    )

    row = conn.execute("select id, status, mode from runs").fetchone()
    assert row == (run["id"], "queued", "copilot-git-only")


def test_next_task_returns_oldest_pending_task(tmp_path):
    conn = db.connect(tmp_path / "review.sqlite")
    db.init_schema(conn)
    run = db.create_run(conn, "/repo", "origin/main", "HEAD", "copilot-git-only")
    first = db.create_task(conn, run["id"], "diff_cartographer", "scouting", "one.json")
    db.create_task(conn, run["id"], "correctness_scout", "scouting", "two.json")

    task = db.next_task(conn, run["id"])

    assert task["id"] == first["id"]
    assert task["role"] == "diff_cartographer"


def test_completed_task_is_not_returned_again(tmp_path):
    conn = db.connect(tmp_path / "review.sqlite")
    db.init_schema(conn)
    run = db.create_run(conn, "/repo", "origin/main", "HEAD", "copilot-git-only")
    first = db.create_task(conn, run["id"], "diff_cartographer", "scouting", "one.json")
    second = db.create_task(conn, run["id"], "correctness_scout", "scouting", "two.json")

    db.mark_task_completed(conn, first["id"], "one.out.json")
    task = db.next_task(conn, run["id"])

    assert task["id"] == second["id"]


def test_failed_task_stores_error(tmp_path):
    conn = db.connect(tmp_path / "review.sqlite")
    db.init_schema(conn)
    run = db.create_run(conn, "/repo", "origin/main", "HEAD", "copilot-git-only")
    task = db.create_task(conn, run["id"], "diff_cartographer", "scouting", "one.json")

    db.mark_task_failed(conn, task["id"], "bad json")

    row = conn.execute("select status, error from agent_tasks where id = ?", (task["id"],)).fetchone()
    assert row == ("failed", "bad json")


def test_candidate_insert_preserves_evidence_json(tmp_path):
    conn = db.connect(tmp_path / "review.sqlite")
    db.init_schema(conn)
    run = db.create_run(conn, "/repo", "origin/main", "HEAD", "copilot-git-only")
    task = db.create_task(conn, run["id"], "security_scout", "scouting", "input.json")
    candidate = {
        "category": "security",
        "severity": "must_change",
        "confidence": 90,
        "file": "app.py",
        "line": 12,
        "claim": "tenant check compares the user to itself",
        "failure_mode": "cross-tenant invoice access",
        "evidence": [{"path": "app.py", "line": 12, "quote": "user.company_id == user.company_id"}],
    }

    inserted = db.insert_candidate(conn, run["id"], task["id"], candidate)

    row = conn.execute("select evidence_json from candidates where id = ?", (inserted["id"],)).fetchone()
    assert json.loads(row[0]) == candidate["evidence"]


def test_verification_insert_links_to_candidate(tmp_path):
    conn = db.connect(tmp_path / "review.sqlite")
    db.init_schema(conn)
    run = db.create_run(conn, "/repo", "origin/main", "HEAD", "copilot-git-only")
    scout = db.create_task(conn, run["id"], "security_scout", "scouting", "input.json")
    verifier = db.create_task(conn, run["id"], "adversarial_verifier", "verification", "verify.json")
    candidate = db.insert_candidate(
        conn,
        run["id"],
        scout["id"],
        {
            "category": "security",
            "severity": "must_change",
            "confidence": 90,
            "file": "app.py",
            "line": 12,
            "claim": "tenant check compares the user to itself",
            "failure_mode": "cross-tenant invoice access",
            "evidence": [{"path": "app.py", "line": 12, "quote": "user.company_id == user.company_id"}],
        },
    )

    verification = db.insert_verification(
        conn,
        run["id"],
        candidate["id"],
        verifier["id"],
        {
            "verdict": "verified",
            "reason": "invoice.company_id is never compared",
            "evidence": [{"path": "app.py", "line": 12, "quote": "user.company_id == user.company_id"}],
        },
    )

    row = conn.execute(
        "select candidate_id, verifier_task_id, verdict from verifications where id = ?",
        (verification["id"],),
    ).fetchone()
    assert row == (candidate["id"], verifier["id"], "verified")

