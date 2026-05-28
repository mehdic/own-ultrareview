from __future__ import annotations

import json
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


def init_and_collect(repo: Path, base_sha: str) -> dict[str, str]:
    init_script = Path("skills/ultrareview/scripts/ultrareview_init.py").resolve()
    init_result = subprocess.run(
        [
            sys.executable,
            str(init_script),
            "--repo",
            str(repo),
            "--base",
            base_sha,
            "--head",
            "HEAD",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(init_result.stdout)
    context_script = Path("skills/ultrareview/scripts/ultrareview_git_context.py").resolve()
    subprocess.run(
        [sys.executable, str(context_script), "--db", payload["db_path"]],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )
    return payload


def test_prepare_tasks_and_next_task_scripts_create_sequential_handoff(tmp_path):
    repo, base_sha = make_repo(tmp_path)
    payload = init_and_collect(repo, base_sha)

    prepare_script = Path("skills/ultrareview/scripts/ultrareview_prepare_tasks.py").resolve()
    prepare_result = subprocess.run(
        [sys.executable, str(prepare_script), "--db", payload["db_path"]],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )
    prepared = json.loads(prepare_result.stdout)
    assert prepared["task_count"] == 8
    assert prepared["next"] == "run ultrareview_next_task.py"

    next_script = Path("skills/ultrareview/scripts/ultrareview_next_task.py").resolve()
    next_result = subprocess.run(
        [sys.executable, str(next_script), "--db", payload["db_path"]],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )
    leased = json.loads(next_result.stdout)
    packet = json.loads(Path(leased["packet_path"]).read_text(encoding="utf-8"))

    assert leased["role"] == "diff_cartographer"
    assert leased["status"] == "running"
    assert packet["role"] == "diff_cartographer"
    assert "sub-agent" in leased["handoff"]

    conn = sqlite3.connect(payload["db_path"])
    row = conn.execute("select status from agent_tasks where id = ?", (leased["task_id"],)).fetchone()
    assert row == ("running",)
