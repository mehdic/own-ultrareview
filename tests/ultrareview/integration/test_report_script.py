from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ultrareview.runtime import db


def test_report_script_writes_markdown_and_json(tmp_path):
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
            "claim": "The invoice tenant is not checked.",
            "failure_mode": "A user can view another tenant's invoice.",
            "evidence": [
                {"path": "app.py", "line": 12, "quote": "user.company_id == user.company_id"}
            ],
        },
    )
    db.insert_final_finding(
        conn,
        run["id"],
        candidate["id"],
        {
            "final_severity": "must_change",
            "confidence": 91,
            "report": {
                "file": "app.py",
                "line": 12,
                "severity": "must_change",
                "claim": "The invoice tenant is not checked.",
                "failure_mode": "A user can view another tenant's invoice.",
                "verification_verdict": "verified",
                "verification_reason": "No tenant guard exists.",
            },
        },
    )
    conn.close()

    script = Path("skills/ultrareview/scripts/ultrareview_report.py").resolve()
    result = subprocess.run(
        [sys.executable, str(script), "--db", str(db_path)],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    markdown = Path(payload["markdown_path"]).read_text(encoding="utf-8")
    report_json = json.loads(Path(payload["json_path"]).read_text(encoding="utf-8"))
    html = Path(payload["html_path"]).read_text(encoding="utf-8")

    assert payload["finding_count"] == 1
    assert "# UltraReview Report" in markdown
    assert "<h1>UltraReview Report</h1>" in html
    assert "Decision Checklist" in html
    assert 'class="severity-badge severity-must-change"' in html
    assert 'class="criticality criticality-must-change"' in html
    assert 'class="risk-matrix table-wrap"' in html
    assert 'class="fix-group"' in html
    assert "overflow-x: auto" in html
    assert "word-break: break-word" in html
    assert "max-width: 100%" in html
    assert "MUST_CHANGE" in markdown
    assert "app.py:12" in markdown
    assert "Finding ID" in markdown
    assert "Available actions" in markdown
    assert report_json["findings"][0]["id"]
    assert report_json["findings"][0]["available_actions"] == ["fix", "accept_risk", "ignore", "defer", "needs_human"]
    assert report_json["findings"][0]["claim"] == "The invoice tenant is not checked."
    assert report_json["findings"][0]["recommended_action"] == "fix_before_merge"
    assert report_json["findings"][0]["suggested_fix"] == "Review and decide from fix group."
    assert report_json["findings"][0]["fix_group"] == "security: app.py"
    assert report_json["findings"][0]["risk_if_not_fixed"] == "A user can view another tenant's invoice."
    assert report_json["findings"][0]["effort"].startswith("M - ")
    assert report_json["findings"][0]["risk_of_fix"] == "Medium: verify related behavior with targeted tests before merging."


def test_report_script_handles_no_verified_findings(tmp_path):
    db_path = tmp_path / "run" / "review.sqlite"
    conn = db.connect(db_path)
    db.init_schema(conn)
    db.create_run(conn, str(tmp_path / "repo"), "origin/main", "HEAD", "copilot-git-only")
    conn.close()

    script = Path("skills/ultrareview/scripts/ultrareview_report.py").resolve()
    result = subprocess.run(
        [sys.executable, str(script), "--db", str(db_path)],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    markdown = Path(payload["markdown_path"]).read_text(encoding="utf-8")
    report_json = json.loads(Path(payload["json_path"]).read_text(encoding="utf-8"))
    html = Path(payload["html_path"]).read_text(encoding="utf-8")

    assert payload["finding_count"] == 0
    assert "No verified findings." in markdown
    assert "No verified findings." in html
    assert report_json["findings"] == []
