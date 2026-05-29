from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from ultrareview.runtime import db


def setup_candidate_and_verifier(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    db_path = tmp_path / "run" / "review.sqlite"
    conn = db.connect(db_path)
    db.init_schema(conn)
    run = db.create_run(conn, str(tmp_path / "repo"), "origin/main", "HEAD", "copilot-git-only")
    scout = db.create_task(conn, run["id"], "security_reviewer", "scouting", "packets/scout.json")
    candidate = db.insert_candidate(
        conn,
        run["id"],
        scout["id"],
        {
            "category": "security",
            "severity": "must_change",
            "confidence": 91,
            "file": "app.py",
            "line": 12,
            "introduced_by_diff": "The diff changed the tenant comparison to compare user.company_id to itself.",
            "claim": "The invoice tenant is not checked.",
            "failure_mode": "A user can view another tenant's invoice.",
            "evidence": [
                {"path": "app.py", "line": 12, "quote": "user.company_id == user.company_id"}
            ],
            "suggested_fix": "Compare invoice.company_id to user.company_id.",
        },
    )
    verifier = db.create_task(
        conn,
        run["id"],
        "verifier_agent",
        "verification",
        f"packets/verify-{candidate['id']}.json",
    )
    packet_path = db_path.parent / verifier["input_path"]
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(json.dumps({"candidate": {"id": candidate["id"]}}), encoding="utf-8")
    db.mark_task_running(conn, verifier["id"])
    conn.close()
    return db_path, {"run_id": run["id"], "candidate_id": candidate["id"], "task_id": verifier["id"]}


def verification_output(path: Path, candidate_id: str) -> None:
    path.write_text(
        json.dumps(
            {
                "verifications": [
                    {
                        "candidate_id": candidate_id,
                        "verdict": "verified",
                        "reason": "No later guard compares invoice.company_id to the user tenant.",
                        "evidence": [
                            {
                                "path": "app.py",
                                "line": 12,
                                "quote": "user.company_id == user.company_id",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def write_verification_output(path: Path, candidate_id: str, verdict: str) -> None:
    path.write_text(
        json.dumps(
            {
                "verifications": [
                    {
                        "candidate_id": candidate_id,
                        "verdict": verdict,
                        "reason": f"Candidate is {verdict}.",
                        "evidence": [
                            {
                                "path": "app.py",
                                "line": 12,
                                "quote": "user.company_id == user.company_id",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_record_verification_and_judge_promotes_verified_candidate(tmp_path):
    db_path, ids = setup_candidate_and_verifier(tmp_path)
    output_path = tmp_path / "verifier-output.json"
    verification_output(output_path, ids["candidate_id"])

    record_script = Path("skills/ultrareview/scripts/ultrareview_record_verification.py").resolve()
    record_result = subprocess.run(
        [
            sys.executable,
            str(record_script),
            "--db",
            str(db_path),
            "--task-id",
            ids["task_id"],
            "--output",
            str(output_path),
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )
    recorded = json.loads(record_result.stdout)

    judge_script = Path("skills/ultrareview/scripts/ultrareview_judge.py").resolve()
    judge_result = subprocess.run(
        [sys.executable, str(judge_script), "--db", str(db_path)],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )
    judged = json.loads(judge_result.stdout)

    conn = sqlite3.connect(db_path)
    verification_row = conn.execute("select verdict, reason from verifications").fetchone()
    finding_row = conn.execute("select final_severity, confidence, report_json from final_findings").fetchone()
    report = json.loads(finding_row[2])

    assert recorded["inserted_verifications"] == 1
    assert recorded["next"] == "run ultrareview_next_task.py"
    assert judged["final_finding_count"] == 1
    assert judged["next"] == "run ultrareview_report.py"
    assert verification_row == (
        "verified",
        "No later guard compares invoice.company_id to the user tenant.",
    )
    assert finding_row[:2] == ("must_change", 91)
    assert report["claim"] == "The invoice tenant is not checked."
    assert report["verification_verdict"] == "verified"


def test_judge_does_not_promote_rejected_or_uncertain_candidates(tmp_path):
    for verdict in ("rejected", "uncertain"):
        case_dir = tmp_path / verdict
        case_dir.mkdir()
        db_path, ids = setup_candidate_and_verifier(case_dir)
        output_path = case_dir / "verifier-output.json"
        write_verification_output(output_path, ids["candidate_id"], verdict)

        subprocess.run(
            [
                sys.executable,
                str(Path("skills/ultrareview/scripts/ultrareview_record_verification.py").resolve()),
                "--db",
                str(db_path),
                "--task-id",
                ids["task_id"],
                "--output",
                str(output_path),
            ],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=True,
        )
        result = subprocess.run(
            [
                sys.executable,
                str(Path("skills/ultrareview/scripts/ultrareview_judge.py").resolve()),
                "--db",
                str(db_path),
            ],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=True,
        )

        payload = json.loads(result.stdout)
        conn = sqlite3.connect(db_path)

        assert payload["final_finding_count"] == 0
        assert conn.execute("select count(*) from final_findings").fetchone()[0] == 0
