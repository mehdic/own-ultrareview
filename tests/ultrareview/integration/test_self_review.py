from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def cli(*args: str) -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("src").resolve())
    result = subprocess.run(
        [sys.executable, "-m", "ultrareview.cli", *args],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def copy_project_for_self_review(tmp_path: Path) -> Path:
    source = Path.cwd()
    target = tmp_path / "own-ultrareview-copy"
    target.mkdir()
    for name in ("src", "skills", "tests"):
        shutil.copytree(
            source / name,
            target / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
    for name in ("pyproject.toml", "README.md", ".gitignore"):
        shutil.copy2(source / name, target / name)
    return target


def simulated_scout_output(packet: dict[str, object], output_path: Path) -> None:
    role = packet["role"]
    if role == "security_reviewer":
        payload = {
            "candidates": [
                {
                    "title": "SQLite foreign key enforcement was disabled",
                    "category": "correctness",
                    "severity": "must_change",
                    "confidence": 94,
                    "file": "src/ultrareview/runtime/db.py",
                    "line": 24,
                    "introduced_by_diff": "The diff removed foreign key enforcement from review database connections.",
                    "claim": "The runtime database no longer enforces foreign key constraints.",
                    "failure_mode": "Orphan tasks, candidates, or verifications can be inserted and later corrupt review state.",
                    "evidence": [
                        {
                            "path": "src/ultrareview/runtime/db.py",
                            "line": 24,
                            "quote": "conn.execute(\"pragma foreign_keys = off\")",
                        }
                    ],
                    "suggested_fix": "Restore pragma foreign_keys = on.",
                }
            ]
        }
    else:
        payload = {"candidates": []}
    output_path.write_text(json.dumps(payload), encoding="utf-8")


def test_ultrareview_can_review_its_own_runtime_with_simulated_llm(tmp_path):
    repo = copy_project_for_self_review(tmp_path)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base own ultrareview")
    base_sha = git(repo, "rev-parse", "HEAD")

    db_module = repo / "src" / "ultrareview" / "runtime" / "db.py"
    text = db_module.read_text(encoding="utf-8")
    db_module.write_text(
        text.replace('conn.execute("pragma foreign_keys = on")', 'conn.execute("pragma foreign_keys = off")'),
        encoding="utf-8",
    )
    git(repo, "add", "src/ultrareview/runtime/db.py")
    git(repo, "commit", "-m", "break runtime db foreign keys")

    start = cli("start", "--repo", str(repo), "--base", base_sha)
    db_path = str(start["db_path"])
    run_dir = Path(str(start["run_dir"]))

    while True:
        leased = cli("next", "--db", db_path)
        if leased["status"] in {"complete", "needs_verification_setup"}:
            break
        packet = json.loads(Path(str(leased["packet_path"])).read_text(encoding="utf-8"))
        output = run_dir / "outputs" / f"{leased['task_id']}.json"
        simulated_scout_output(packet, output)
        cli("record-output", "--db", db_path, "--task-id", str(leased["task_id"]), "--output", str(output))

    prepared = cli("prepare-verifiers", "--db", db_path)
    assert prepared["verifier_task_count"] == 1

    verifier_task = cli("next", "--db", db_path)
    verifier_packet = json.loads(Path(str(verifier_task["packet_path"])).read_text(encoding="utf-8"))
    verifier_output = run_dir / "outputs" / f"{verifier_task['task_id']}.json"
    verifier_output.write_text(
        json.dumps(
            {
                "verifications": [
                    {
                        "candidate_id": verifier_packet["candidate"]["id"],
                        "verdict": "verified",
                        "reason": "The changed line disables SQLite foreign key enforcement.",
                        "evidence": [
                            {
                                "path": "src/ultrareview/runtime/db.py",
                                "line": 24,
                                "quote": "conn.execute(\"pragma foreign_keys = off\")",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    cli(
        "record-verification",
        "--db",
        db_path,
        "--task-id",
        str(verifier_task["task_id"]),
        "--output",
        str(verifier_output),
    )
    judged = cli("judge", "--db", db_path)
    report = cli("report", "--db", db_path)
    report_json = json.loads(Path(str(report["json_path"])).read_text(encoding="utf-8"))

    assert judged["final_finding_count"] == 1
    assert report["finding_count"] == 1
    assert report_json["findings"][0]["file"] == "src/ultrareview/runtime/db.py"
    assert "foreign key" in report_json["findings"][0]["claim"]
