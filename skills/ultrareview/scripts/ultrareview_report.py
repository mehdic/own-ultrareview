#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render final UltraReview report files.")
    parser.add_argument("--db", required=True, help="Path to review.sqlite.")
    return parser.parse_args()


def _markdown(run: sqlite3.Row, findings: list[dict[str, object]]) -> str:
    lines = [
        "# UltraReview Report",
        "",
        f"- Run: `{run['id']}`",
        f"- Repo: `{run['repo_path']}`",
        f"- Range: `{run['base_ref']}..{run['head_ref']}`",
        f"- Findings: {len(findings)}",
        "",
    ]
    if not findings:
        lines.extend(["No verified findings.", ""])
        return "\n".join(lines)

    for index, finding in enumerate(findings, start=1):
        severity = str(finding["severity"]).upper()
        location = f"{finding['file']}:{finding['line']}"
        lines.extend(
            [
                f"## {index}. {severity} - {location}",
                "",
                f"**Claim:** {finding['claim']}",
                "",
                f"**Failure mode:** {finding['failure_mode']}",
                "",
                f"**Verification:** {finding.get('verification_verdict', 'unknown')} - {finding.get('verification_reason', '')}",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    run_dir = db_path.parent
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = conn.execute("select * from runs order by created_at limit 1").fetchone()
    if run is None:
        raise SystemExit(f"no run found in {db_path}")

    rows = conn.execute("select * from final_findings order by created_at, rowid").fetchall()
    findings = [json.loads(row["report_json"]) for row in rows]
    report = {
        "run": {
            "id": run["id"],
            "repo_path": run["repo_path"],
            "base_ref": run["base_ref"],
            "head_ref": run["head_ref"],
            "mode": run["mode"],
        },
        "findings": findings,
    }

    markdown_path = run_dir / "final-report.md"
    json_path = run_dir / "final-report.json"
    markdown_path.write_text(_markdown(run, findings), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    conn.close()

    print(
        json.dumps(
            {
                "run_id": run["id"],
                "finding_count": len(findings),
                "markdown_path": str(markdown_path),
                "json_path": str(json_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
