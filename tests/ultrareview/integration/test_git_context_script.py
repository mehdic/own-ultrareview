from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def test_git_context_script_writes_artifact_after_init(tmp_path):
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
    init_payload = json.loads(init_result.stdout)

    script = Path("skills/ultrareview/scripts/ultrareview_git_context.py").resolve()
    result = subprocess.run(
        [sys.executable, str(script), "--db", init_payload["db_path"]],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    artifact = Path(payload["artifact_path"])
    context = json.loads(artifact.read_text(encoding="utf-8"))

    assert payload["run_id"] == init_payload["run_id"]
    assert payload["next"] == "run ultrareview_next_task.py"
    assert artifact.exists()
    assert context["changed_files"][0]["path"] == "app.py"
