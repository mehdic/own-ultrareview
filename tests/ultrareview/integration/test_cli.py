from __future__ import annotations

import json
import os
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
