from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from ultrareview.runtime import db


def cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("src").resolve())
    return env


def make_review_with_finding(tmp_path: Path) -> tuple[Path, str]:
    db_path = tmp_path / "run" / "review.sqlite"
    conn = db.connect(db_path)
    db.init_schema(conn)
    run = db.create_run(conn, str(tmp_path / "repo"), "origin/main", "HEAD", "copilot-git-only")
    scout = db.create_task(conn, run["id"], "correctness_reviewer", "scouting", "packet.json")
    candidate = db.insert_candidate(
        conn,
        run["id"],
        scout["id"],
        {
            "category": "correctness",
            "severity": "must_change",
            "confidence": 92,
            "file": "src/ultrareview/cli.py",
            "line": 114,
            "introduced_by_diff": "The changed next-step routing skipped verifier preparation.",
            "claim": "The CLI skips verifier setup.",
            "failure_mode": "Findings are never verified.",
            "evidence": [{"path": "src/ultrareview/cli.py", "line": 114, "quote": "judge"}],
            "suggested_fix": "Route the next command to prepare-verifiers before judge.",
        },
    )
    finding = db.insert_final_finding(
        conn,
        run["id"],
        candidate["id"],
        {
            "final_severity": "must_change",
            "confidence": 92,
            "report": {
                "file": "src/ultrareview/cli.py",
                "line": 114,
                "severity": "must_change",
                "claim": "The CLI skips verifier setup.",
                "failure_mode": "Findings are never verified.",
                "introduced_by_diff": "The changed next-step routing skipped verifier preparation.",
                "suggested_fix": "Route the next command to prepare-verifiers before judge.",
                "verification_verdict": "verified",
                "verification_reason": "The next command points to judge.",
            },
        },
    )
    conn.close()
    return db_path, finding["id"]


def run_cli(*args: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-m", "ultrareview.cli", *args],
        cwd=Path.cwd(),
        env=cli_env(),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_actions_lists_findings_and_available_decisions(tmp_path):
    db_path, finding_id = make_review_with_finding(tmp_path)

    payload = run_cli("actions", "--db", str(db_path))

    assert payload["open_finding_count"] == 1
    assert payload["findings"][0]["id"] == finding_id
    assert payload["findings"][0]["claim"] == "The CLI skips verifier setup."
    assert payload["findings"][0]["introduced_by_diff"] == "The changed next-step routing skipped verifier preparation."
    assert payload["findings"][0]["suggested_fix"] == "Route the next command to prepare-verifiers before judge."
    assert payload["findings"][0]["decision_question"] == "How should UltraReview handle this finding?"
    assert payload["findings"][0]["decision_options"] == [
        {"value": "fix", "label": "Fix before merge"},
        {"value": "accept_risk", "label": "Accept the risk"},
        {"value": "ignore", "label": "Ignore after review"},
        {"value": "defer", "label": "Defer to later work"},
        {"value": "needs_human", "label": "Needs human decision"},
    ]
    assert payload["findings"][0]["available_actions"] == [
        "fix",
        "accept_risk",
        "ignore",
        "defer",
        "needs_human",
    ]
    assert payload["findings"][0]["decision"] is None


def test_actions_and_summary_migrate_older_databases_without_decision_tables(tmp_path):
    db_path, finding_id = make_review_with_finding(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute("drop table finding_resolutions")
    conn.execute("drop table finding_decisions")
    conn.commit()
    conn.close()

    actions = run_cli("actions", "--db", str(db_path))
    summary_payload = run_cli("summary", "--db", str(db_path))
    summary_json = json.loads(Path(summary_payload["json_path"]).read_text(encoding="utf-8"))

    assert actions["findings"][0]["id"] == finding_id
    assert actions["findings"][0]["decision"] is None
    assert actions["findings"][0]["resolution"] is None
    assert summary_json["findings"][0]["id"] == finding_id
    assert summary_json["findings"][0]["introduced_by_diff"] == "The changed next-step routing skipped verifier preparation."
    assert summary_json["findings"][0]["suggested_fix"] == "Route the next command to prepare-verifiers before judge."
    assert summary_json["findings"][0]["decision"] is None
    assert summary_json["findings"][0]["resolution"] is None


def test_decide_records_user_decision_and_actions_reflects_it(tmp_path):
    db_path, finding_id = make_review_with_finding(tmp_path)

    decision = run_cli(
        "decide",
        "--db",
        str(db_path),
        "--finding-id",
        finding_id,
        "--decision",
        "fix",
        "--note",
        "Patch before merge.",
    )
    actions = run_cli("actions", "--db", str(db_path))

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "select decision, note from finding_decisions where final_finding_id = ?",
        (finding_id,),
    ).fetchone()

    assert decision["status"] == "recorded"
    assert row == ("fix", "Patch before merge.")
    assert actions["open_finding_count"] == 0
    assert actions["findings"][0]["decision"]["decision"] == "fix"


def test_resolve_records_fix_outcome_and_summary_explains_run(tmp_path):
    db_path, finding_id = make_review_with_finding(tmp_path)

    run_cli(
        "decide",
        "--db",
        str(db_path),
        "--finding-id",
        finding_id,
        "--decision",
        "fix",
        "--note",
        "Patch before merge.",
    )
    resolution = run_cli(
        "resolve",
        "--db",
        str(db_path),
        "--finding-id",
        finding_id,
        "--status",
        "fixed",
        "--summary",
        "Changed next-step routing so verifier setup happens before judge.",
        "--evidence",
        "commit abc123",
    )
    summary_payload = run_cli("summary", "--db", str(db_path))

    markdown = Path(summary_payload["markdown_path"]).read_text(encoding="utf-8")
    summary_json = json.loads(Path(summary_payload["json_path"]).read_text(encoding="utf-8"))

    assert resolution["status"] == "recorded"
    assert summary_payload["finding_count"] == 1
    assert summary_json["run"]["id"] == summary_payload["run_id"]
    assert summary_json["tasks"]["total"] == 1
    assert summary_json["findings"][0]["decision"]["decision"] == "fix"
    assert summary_json["findings"][0]["resolution"]["status"] == "fixed"
    assert summary_json["findings"][0]["resolution"]["summary"] == "Changed next-step routing so verifier setup happens before judge."
    assert summary_json["findings"][0]["resolution"]["evidence"] == ["commit abc123"]
    assert "## What Ran" in markdown
    assert "## What Was Found" in markdown
    assert "## What Was Decided" in markdown
    assert "## What Was Fixed" in markdown
