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
    status = conn.execute("select status from agent_tasks where id = ?", (first["id"],)).fetchone()[0]
    assert status == "running"


def test_next_task_claims_atomically_across_connections(tmp_path):
    db_path = tmp_path / "review.sqlite"
    setup = db.connect(db_path)
    db.init_schema(setup)
    run = db.create_run(setup, "/repo", "origin/main", "HEAD", "copilot-git-only")
    task = db.create_task(setup, run["id"], "diff_cartographer", "scouting", "one.json")
    setup.close()

    first_conn = db.connect(db_path)
    second_conn = db.connect(db_path)

    first_claim = db.next_task(first_conn, run["id"])
    second_claim = db.next_task(second_conn, run["id"])

    assert first_claim["id"] == task["id"]
    assert second_claim is None


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
        "introduced_by_diff": "The changed tenant check now compares the user to itself.",
        "claim": "tenant check compares the user to itself",
        "failure_mode": "cross-tenant invoice access",
        "evidence": [{"path": "app.py", "line": 12, "quote": "user.company_id == user.company_id"}],
        "suggested_fix": "Compare user.company_id to invoice.company_id.",
    }

    inserted = db.insert_candidate(conn, run["id"], task["id"], candidate)

    row = conn.execute("select evidence_json from candidates where id = ?", (inserted["id"],)).fetchone()
    assert json.loads(row[0]) == candidate["evidence"]


def test_candidate_insert_preserves_diff_rationale_and_suggested_fix(tmp_path):
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
        "introduced_by_diff": "The diff removed the invoice tenant comparison.",
        "claim": "tenant check compares the user to itself",
        "failure_mode": "cross-tenant invoice access",
        "evidence": [{"path": "app.py", "line": 12, "quote": "user.company_id == user.company_id"}],
        "suggested_fix": "Compare user.company_id to invoice.company_id.",
    }

    inserted = db.insert_candidate(conn, run["id"], task["id"], candidate)

    row = conn.execute(
        "select introduced_by_diff, suggested_fix from candidates where id = ?",
        (inserted["id"],),
    ).fetchone()
    assert row == (
        "The diff removed the invoice tenant comparison.",
        "Compare user.company_id to invoice.company_id.",
    )


def test_schema_migrates_candidate_metadata_columns_for_old_databases(tmp_path):
    conn = db.connect(tmp_path / "review.sqlite")
    conn.executescript(
        """
        create table candidates (
          id text primary key,
          run_id text not null,
          source_task_id text not null,
          category text not null,
          severity text not null,
          confidence integer not null,
          file_path text not null,
          line integer not null,
          claim text not null,
          failure_mode text not null,
          evidence_json text not null,
          status text not null,
          created_at text not null
        );
        """
    )
    conn.commit()

    db.init_schema(conn)

    columns = {row[1] for row in conn.execute("pragma table_info(candidates)").fetchall()}
    assert "introduced_by_diff" in columns
    assert "suggested_fix" in columns


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
