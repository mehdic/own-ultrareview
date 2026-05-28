from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def env() -> dict[str, str]:
    value = os.environ.copy()
    value["PYTHONPATH"] = str(Path("src").resolve())
    return value


def cli(*args: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-m", "ultrareview.cli", *args],
        cwd=Path.cwd(),
        env=env(),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_full_cli_chain_from_start_to_report(tmp_path):
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

    start = cli("start", "--repo", str(repo), "--base", base_sha)
    first = cli("next", "--db", str(start["db_path"]))

    scout_output = Path(start["run_dir"]) / "outputs" / "scout.json"
    scout_output.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "title": "Print changed behavior",
                        "category": "correctness",
                        "severity": "must_change",
                        "confidence": 88,
                        "file": "app.py",
                        "line": 1,
                        "introduced_by_diff": True,
                        "claim": "The printed value changed.",
                        "failure_mode": "Callers expecting old output receive new output.",
                        "evidence": [{"path": "app.py", "line": 1, "quote": "print('new')"}],
                        "suggested_fix": "Restore old output or update callers.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    recorded = cli("record-output", "--db", str(start["db_path"]), "--task-id", str(first["task_id"]), "--output", str(scout_output))
    verifiers = cli("prepare-verifiers", "--db", str(start["db_path"]))
    verifier_task = None
    while True:
        leased = cli("next", "--db", str(start["db_path"]))
        if leased.get("phase") == "verification":
            verifier_task = leased
            break
        empty_output = Path(start["run_dir"]) / "outputs" / f"{leased['task_id']}.json"
        empty_output.write_text('{"candidates": []}', encoding="utf-8")
        cli("record-output", "--db", str(start["db_path"]), "--task-id", str(leased["task_id"]), "--output", str(empty_output))

    packet = json.loads(Path(str(verifier_task["packet_path"])).read_text(encoding="utf-8"))
    verifier_output = Path(start["run_dir"]) / "outputs" / "verifier.json"
    verifier_output.write_text(
        json.dumps(
            {
                "verifications": [
                    {
                        "candidate_id": packet["candidate"]["id"],
                        "verdict": "verified",
                        "reason": "The diff changes the only statement in the file.",
                        "evidence": [{"path": "app.py", "line": 1, "quote": "print('new')"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    verification = cli(
        "record-verification",
        "--db",
        str(start["db_path"]),
        "--task-id",
        str(verifier_task["task_id"]),
        "--output",
        str(verifier_output),
    )
    judged = cli("judge", "--db", str(start["db_path"]))
    report = cli("report", "--db", str(start["db_path"]))

    assert recorded["inserted_candidates"] == 1
    assert verifiers["verifier_task_count"] == 1
    assert verification["inserted_verifications"] == 1
    assert judged["final_finding_count"] == 1
    assert report["finding_count"] == 1
    assert Path(str(report["markdown_path"])).exists()
