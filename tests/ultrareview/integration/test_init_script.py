from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_init_script_creates_run_database_and_directories(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    script = Path("skills/ultrareview/scripts/ultrareview_init.py").resolve()

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--repo",
            str(repo),
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--mode",
            "copilot-git-only",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    run_dir = Path(payload["run_dir"])
    db_path = Path(payload["db_path"])

    assert payload["run_id"]
    assert payload["next"] == "run ultrareview_git_context.py"
    assert db_path.exists()
    assert (run_dir / "artifacts").is_dir()
    assert (run_dir / "packets").is_dir()
    assert (run_dir / "outputs").is_dir()
    assert (run_dir / "validation").is_dir()
    assert (run_dir / "temp" / "external-repos").is_dir()

    conn = sqlite3.connect(db_path)
    row = conn.execute("select repo_path, base_ref, head_ref, mode, status from runs").fetchone()
    assert row == (str(repo), "origin/main", "HEAD", "copilot-git-only", "queued")

