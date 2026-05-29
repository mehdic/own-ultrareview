from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "app.py").write_text("print('old')\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "base")
    base_sha = git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("print('new')\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "head")
    return repo, base_sha


def cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("src").resolve())
    return env


def test_cli_start_collects_context_and_prepares_first_task(tmp_path):
    repo, base_sha = make_repo(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ultrareview.cli",
            "start",
            "--repo",
            str(repo),
            "--base",
            base_sha,
            "--head",
            "HEAD",
        ],
        cwd=Path.cwd(),
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    run_dir = Path(payload["run_dir"])

    assert payload["task_count"] == 8
    assert payload["next"] == "own-ultrareview next --db <db_path>"
    assert Path(payload["db_path"]).exists()
    assert (run_dir / "artifacts" / "git-context.json").exists()
    assert (run_dir / "packets" / "scout-diff_cartographer.json").exists()


def test_cli_next_leases_first_task_after_start(tmp_path):
    repo, base_sha = make_repo(tmp_path)
    start = subprocess.run(
        [
            sys.executable,
            "-m",
            "ultrareview.cli",
            "start",
            "--repo",
            str(repo),
            "--base",
            base_sha,
        ],
        cwd=Path.cwd(),
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    db_path = json.loads(start.stdout)["db_path"]

    result = subprocess.run(
        [sys.executable, "-m", "ultrareview.cli", "next", "--db", db_path],
        cwd=Path.cwd(),
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)

    assert payload["role"] == "diff_cartographer"
    assert payload["status"] == "running"
    assert Path(payload["packet_path"]).exists()


def test_cli_next_routes_verification_task_to_record_verification(tmp_path):
    repo, base_sha = make_repo(tmp_path)
    start = subprocess.run(
        [
            sys.executable,
            "-m",
            "ultrareview.cli",
            "start",
            "--repo",
            str(repo),
            "--base",
            base_sha,
        ],
        cwd=Path.cwd(),
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(start.stdout)

    import sqlite3
    from ultrareview.runtime import db as runtime_db

    conn = runtime_db.connect(payload["db_path"])
    conn.row_factory = sqlite3.Row
    first_task = conn.execute("select * from agent_tasks order by rowid limit 1").fetchone()
    runtime_db.mark_task_completed(conn, first_task["id"], "outputs/task.json")
    candidate = runtime_db.insert_candidate(
        conn,
        payload["run_id"],
        first_task["id"],
        {
            "category": "correctness",
            "severity": "must_change",
            "confidence": 90,
            "file": "app.py",
            "line": 1,
            "claim": "The printed value changed.",
            "failure_mode": "Callers receive the wrong value.",
            "evidence": [{"path": "app.py", "line": 1, "quote": "print('new')"}],
        },
    )
    for (pending_id,) in conn.execute("select id from agent_tasks where status = 'pending'").fetchall():
        runtime_db.mark_task_completed(conn, pending_id, f"outputs/{pending_id}.json")
    verifier_packet = Path(payload["db_path"]).parent / "packets" / f"verify-{candidate['id']}.json"
    verifier_packet.parent.mkdir(parents=True, exist_ok=True)
    verifier_packet.write_text("{}", encoding="utf-8")
    runtime_db.create_task(
        conn,
        payload["run_id"],
        "verifier_agent",
        "verification",
        str(verifier_packet.relative_to(Path(payload["db_path"]).parent)),
    )
    conn.close()

    result = subprocess.run(
        [sys.executable, "-m", "ultrareview.cli", "next", "--db", payload["db_path"]],
        cwd=Path.cwd(),
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )

    next_payload = json.loads(result.stdout)

    assert next_payload["phase"] == "verification"
    assert "record-verification" in next_payload["next"]


def test_cli_next_flags_completed_verifier_without_verification_row(tmp_path):
    repo, base_sha = make_repo(tmp_path)
    start = subprocess.run(
        [
            sys.executable,
            "-m",
            "ultrareview.cli",
            "start",
            "--repo",
            str(repo),
            "--base",
            base_sha,
        ],
        cwd=Path.cwd(),
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(start.stdout)

    import sqlite3
    from ultrareview.runtime import db as runtime_db

    conn = runtime_db.connect(payload["db_path"])
    conn.row_factory = sqlite3.Row
    first_task = conn.execute("select * from agent_tasks order by rowid limit 1").fetchone()
    runtime_db.mark_task_completed(conn, first_task["id"], "outputs/task.json")
    candidate = runtime_db.insert_candidate(
        conn,
        payload["run_id"],
        first_task["id"],
        {
            "category": "correctness",
            "severity": "must_change",
            "confidence": 90,
            "file": "app.py",
            "line": 1,
            "claim": "The printed value changed.",
            "failure_mode": "Callers receive the wrong value.",
            "evidence": [{"path": "app.py", "line": 1, "quote": "print('new')"}],
        },
    )
    for (pending_id,) in conn.execute("select id from agent_tasks where status = 'pending'").fetchall():
        runtime_db.mark_task_completed(conn, pending_id, f"outputs/{pending_id}.json")
    verifier_packet = Path(payload["db_path"]).parent / "packets" / f"verify-{candidate['id']}.json"
    verifier_packet.parent.mkdir(parents=True, exist_ok=True)
    verifier_packet.write_text(json.dumps({"candidate": {"id": candidate["id"]}}), encoding="utf-8")
    verifier = runtime_db.create_task(
        conn,
        payload["run_id"],
        "verifier_agent",
        "verification",
        str(verifier_packet.relative_to(Path(payload["db_path"]).parent)),
    )
    runtime_db.mark_task_completed(conn, verifier["id"], "outputs/verifier.json")
    conn.close()

    result = subprocess.run(
        [sys.executable, "-m", "ultrareview.cli", "next", "--db", payload["db_path"]],
        cwd=Path.cwd(),
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )

    next_payload = json.loads(result.stdout)

    assert next_payload["status"] == "invalid_state"
    assert next_payload["task_id"] == verifier["id"]
    assert "record-verification" in next_payload["next"]
    assert "Do not edit review.sqlite directly" in next_payload["rule"]


def test_cli_next_points_to_verifier_preparation_after_scouts_find_candidates(tmp_path):
    repo, base_sha = make_repo(tmp_path)
    start = subprocess.run(
        [
            sys.executable,
            "-m",
            "ultrareview.cli",
            "start",
            "--repo",
            str(repo),
            "--base",
            base_sha,
        ],
        cwd=Path.cwd(),
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    db_path = json.loads(start.stdout)["db_path"]

    import sqlite3
    from ultrareview.runtime import db as runtime_db

    conn = sqlite3.connect(db_path)
    task_id = conn.execute("select id from agent_tasks order by rowid limit 1").fetchone()[0]
    conn.close()
    conn = runtime_db.connect(db_path)
    runtime_db.mark_task_completed(conn, task_id, "outputs/task.json")
    runtime_db.insert_candidate(
        conn,
        json.loads(start.stdout)["run_id"],
        task_id,
        {
            "category": "correctness",
            "severity": "must_change",
            "confidence": 90,
            "file": "app.py",
            "line": 1,
            "claim": "The printed value changed.",
            "failure_mode": "Callers receive the wrong value.",
            "evidence": [{"path": "app.py", "line": 1, "quote": "print('new')"}],
        },
    )
    for (pending_id,) in conn.execute("select id from agent_tasks where status = 'pending'").fetchall():
        runtime_db.mark_task_completed(conn, pending_id, f"outputs/{pending_id}.json")
    conn.close()

    result = subprocess.run(
        [sys.executable, "-m", "ultrareview.cli", "next", "--db", db_path],
        cwd=Path.cwd(),
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)

    assert payload["status"] == "needs_verification_setup"
    assert payload["next"] == "own-ultrareview prepare-verifiers --db <db_path>"


def test_cli_judge_rejects_completed_verifier_without_verification_row(tmp_path):
    repo, base_sha = make_repo(tmp_path)
    start = subprocess.run(
        [
            sys.executable,
            "-m",
            "ultrareview.cli",
            "start",
            "--repo",
            str(repo),
            "--base",
            base_sha,
        ],
        cwd=Path.cwd(),
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(start.stdout)

    from ultrareview.runtime import db as runtime_db

    conn = runtime_db.connect(payload["db_path"])
    conn.row_factory = sqlite3.Row
    first_task = conn.execute("select * from agent_tasks order by rowid limit 1").fetchone()
    runtime_db.mark_task_completed(conn, first_task["id"], "outputs/task.json")
    candidate = runtime_db.insert_candidate(
        conn,
        payload["run_id"],
        first_task["id"],
        {
            "category": "correctness",
            "severity": "must_change",
            "confidence": 90,
            "file": "app.py",
            "line": 1,
            "claim": "The printed value changed.",
            "failure_mode": "Callers receive the wrong value.",
            "evidence": [{"path": "app.py", "line": 1, "quote": "print('new')"}],
        },
    )
    verifier_packet = Path(payload["db_path"]).parent / "packets" / f"verify-{candidate['id']}.json"
    verifier_packet.parent.mkdir(parents=True, exist_ok=True)
    verifier_packet.write_text(json.dumps({"candidate": {"id": candidate["id"]}}), encoding="utf-8")
    verifier = runtime_db.create_task(
        conn,
        payload["run_id"],
        "verifier_agent",
        "verification",
        str(verifier_packet.relative_to(Path(payload["db_path"]).parent)),
    )
    runtime_db.mark_task_completed(conn, verifier["id"], "outputs/verifier.json")
    conn.close()

    result = subprocess.run(
        [sys.executable, "-m", "ultrareview.cli", "judge", "--db", payload["db_path"]],
        cwd=Path.cwd(),
        env=cli_env(),
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "completed verification task(s) have no verifier rows" in result.stderr
    assert "Do not edit review.sqlite directly" in result.stderr
