#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

AVAILABLE_ACTIONS = ["fix", "accept_risk", "ignore", "defer", "needs_human"]
SEVERITY_LABELS = {
    "critical": "Critical",
    "must_change": "Must change",
    "better_to_change": "Better to change",
    "acceptable": "Acceptable",
}
SEVERITY_RANK = {
    "critical": 4,
    "must_change": 3,
    "better_to_change": 2,
    "acceptable": 1,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render final UltraReview report files.")
    parser.add_argument("--db", required=True, help="Path to review.sqlite.")
    return parser.parse_args()


def _severity_slug(severity: object) -> str:
    return str(severity or "unknown").strip().lower().replace("_", "-").replace(" ", "-")


def _default_effort(severity: str) -> str:
    if severity == "critical":
        return "L - high-risk fix needs focused regression coverage"
    if severity in {"must_change", "better_to_change"}:
        return "M - targeted implementation and tests"
    return "S - narrow validation"


def _default_recommended_action(severity: str) -> str:
    if severity in {"critical", "must_change"}:
        return "fix_before_merge"
    if severity == "better_to_change":
        return "defer"
    return "accept_risk"


def _default_risk_of_fix(severity: str) -> str:
    if severity == "critical":
        return "High: isolate the patch and require regression coverage before merging."
    if severity in {"must_change", "better_to_change"}:
        return "Medium: verify related behavior with targeted tests before merging."
    return "Low: verify the narrow behavior touched by the change."


def _normalize_finding(finding: dict[str, object]) -> dict[str, object]:
    normalized = dict(finding)
    severity = str(normalized.get("severity") or normalized.get("final_severity") or "acceptable")
    category = str(normalized.get("category") or "general")
    file_path = str(normalized.get("file") or "unknown")
    if normalized.get("display_index") is not None:
        normalized["display_index"] = int(normalized["display_index"])
    normalized["severity"] = severity
    normalized["criticality"] = normalized.get("criticality") or SEVERITY_LABELS.get(severity, severity.replace("_", " ").title())
    normalized["recommended_action"] = normalized.get("recommended_action") or _default_recommended_action(severity)
    normalized["suggested_fix"] = normalized.get("suggested_fix") or "Review and decide from fix group."
    normalized["fix_group"] = normalized.get("fix_group") or f"{category}: {file_path}"
    normalized["risk_if_not_fixed"] = (
        normalized.get("risk_if_not_fixed")
        or normalized.get("failure_mode")
        or normalized.get("claim")
        or "Risk not recorded."
    )
    normalized["risk_of_fix"] = normalized.get("risk_of_fix") or _default_risk_of_fix(severity)
    normalized["effort"] = normalized.get("effort") or _default_effort(severity)
    return normalized


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
        display_index = int(finding.get("display_index") or index)
        severity = str(finding["severity"]).upper()
        location = f"{finding['file']}:{finding['line']}"
        lines.extend(
            [
                f"## {display_index}. {severity} - {location}",
                "",
                f"**Finding ID:** `{finding['id']}`",
                "",
                f"**Claim:** {finding['claim']}",
                "",
                f"**Failure mode:** {finding['failure_mode']}",
                "",
                f"**Verification:** {finding.get('verification_verdict', 'unknown')} - {finding.get('verification_reason', '')}",
                "",
                f"**Available actions:** {', '.join(AVAILABLE_ACTIONS)}",
                "",
            ]
        )
    return "\n".join(lines)


def _cell(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _html(run: sqlite3.Row, findings: list[dict[str, object]]) -> str:
    rows = []
    for index, finding in enumerate(findings, start=1):
        display_index = int(finding.get("display_index") or index)
        severity = str(finding.get("severity") or "unknown")
        severity_slug = _severity_slug(severity)
        rows.append(
            f"<tr data-display-index=\"{display_index}\">"
            f"<td>{display_index}</td>"
            f"<td><code>{_cell(finding.get('id'))}</code></td>"
            f"<td><span class=\"severity-badge severity-{severity_slug}\">{_cell(SEVERITY_LABELS.get(severity, severity))}</span></td>"
            f"<td><span class=\"criticality criticality-{severity_slug}\">{_cell(finding.get('criticality'))}</span></td>"
            f"<td>{_cell(finding.get('file'))}:{_cell(finding.get('line'))}</td>"
            f"<td>{_cell(finding.get('claim'))}</td>"
            f"<td>{_cell(finding.get('recommended_action'))}</td>"
            f"<td>{_cell(finding.get('risk_if_not_fixed'))}</td>"
            f"<td>{_cell(finding.get('risk_of_fix'))}</td>"
            f"<td>{_cell(finding.get('effort'))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan=\"10\">No verified findings.</td></tr>")

    details = []
    for index, finding in enumerate(findings, start=1):
        display_index = int(finding.get("display_index") or index)
        severity = str(finding.get("severity") or "unknown")
        severity_slug = _severity_slug(severity)
        evidence = finding.get("evidence") or []
        evidence_items = "".join(
            f"<li><code>{_cell(item.get('path'))}:{_cell(item.get('line'))}</code> {_cell(item.get('quote'))}</li>"
            for item in evidence
            if isinstance(item, dict)
        )
        details.append(
            f"<article class=\"finding-detail\" id=\"finding-{display_index}\" data-display-index=\"{display_index}\">"
            f"<h3><span>{display_index}. {_cell(finding.get('claim'))}</span> <span class=\"severity-badge severity-{severity_slug}\">{_cell(SEVERITY_LABELS.get(severity, severity))}</span></h3>"
            f"<p><strong>Criticality:</strong> {_cell(finding.get('criticality'))}</p>"
            f"<p><strong>Failure mode:</strong> {_cell(finding.get('failure_mode'))}</p>"
            f"<p><strong>Introduced by diff:</strong> {_cell(finding.get('introduced_by_diff') or 'Not recorded.')}</p>"
            f"<p><strong>Recommended action:</strong> {_cell(finding.get('recommended_action'))}</p>"
            f"<p><strong>Suggested fix:</strong> {_cell(finding.get('suggested_fix'))}</p>"
            f"<p><strong>Risk if not fixed:</strong> {_cell(finding.get('risk_if_not_fixed'))}</p>"
            f"<p><strong>Risk of fix:</strong> {_cell(finding.get('risk_of_fix'))}</p>"
            f"<p><strong>Effort:</strong> {_cell(finding.get('effort'))}</p>"
            f"<p><strong>Verification:</strong> {_cell(finding.get('verification_verdict'))} - {_cell(finding.get('verification_reason'))}</p>"
            f"<ul>{evidence_items}</ul>"
            "</article>"
        )

    fix_groups: dict[str, list[dict[str, object]]] = {}
    for finding in findings:
        fix_groups.setdefault(str(finding.get("fix_group")), []).append(finding)
    group_cards = []
    for group, group_findings in sorted(
        fix_groups.items(),
        key=lambda item: max(SEVERITY_RANK.get(str(f.get("severity")), 0) for f in item[1]),
        reverse=True,
    ):
        group_cards.append(
            "<article class=\"fix-group\">"
            f"<h3>{_cell(group)}</h3>"
            f"<p>{len(group_findings)} finding(s) | recommended action: {_cell(group_findings[0].get('recommended_action'))} | effort: {_cell(group_findings[0].get('effort'))}</p>"
            f"<p>{_cell(group_findings[0].get('suggested_fix'))}</p>"
            "</article>"
        )
    if not group_cards:
        group_cards.append("<article class=\"fix-group\"><h3>No fix groups</h3><p>No verified findings.</p></article>")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UltraReview Report - {html.escape(run['id'])}</title>
  <style>
    :root {{ color-scheme: light; --ink: #17202a; --muted: #52616f; --line: #d8dee4; --panel: #ffffff; --wash: #f5f7fa; }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; max-width: 100%; overflow-x: hidden; }}
    body {{ font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--wash); }}
    main {{ width: min(1180px, calc(100vw - 24px)); margin: 0 auto; padding: 24px 0 40px; }}
    h1, h2, h3 {{ margin: 0; line-height: 1.2; }}
    h1 {{ font-size: clamp(1.55rem, 3vw, 2.25rem); letter-spacing: 0; }}
    h2 {{ font-size: 1rem; margin-bottom: 10px; text-transform: uppercase; color: #263442; }}
    h3 {{ font-size: 0.98rem; margin-bottom: 8px; }}
    p {{ margin: 0 0 8px; }}
    .meta {{ color: var(--muted); margin-top: 8px; overflow-wrap: anywhere; }}
    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 10px; margin: 18px 0; }}
    .metric, section, .finding-detail, .fix-group {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }}
    .metric strong {{ display: block; font-size: 1.4rem; }}
    .metric span {{ color: var(--muted); font-size: 0.82rem; }}
    section {{ margin: 12px 0; }}
    .table-wrap {{ width: 100%; max-width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; border: 1px solid var(--line); border-radius: 8px; background: white; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 9px; text-align: left; vertical-align: top; font-size: 13px; overflow-wrap: anywhere; word-break: break-word; }}
    th {{ background: #eef2f6; color: #263442; }}
    code {{ background: #eef2f6; padding: 1px 4px; border-radius: 4px; white-space: normal; }}
    .severity-badge, .criticality {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 8px; font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0; }}
    .severity-critical, .criticality-critical {{ background: #fee2e2; color: #991b1b; }}
    .severity-must-change, .criticality-must-change {{ background: #ffedd5; color: #9a3412; }}
    .severity-better-to-change, .criticality-better-to-change {{ background: #fef3c7; color: #92400e; }}
    .severity-acceptable, .criticality-acceptable {{ background: #dcfce7; color: #166534; }}
    .fix-groups {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }}
    .finding-detail + .finding-detail {{ margin-top: 10px; }}
    .finding-detail h3 {{ display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; }}
    .checklist {{ display: grid; gap: 8px; padding-left: 0; list-style: none; }}
    .checklist li {{ background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 6px; padding: 8px; }}
    @media (max-width: 720px) {{
      main {{ width: min(100% - 16px, 1180px); padding-top: 14px; }}
      th, td {{ font-size: 12px; padding: 7px; }}
      .summary-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
<main>
  <h1>UltraReview Report</h1>
  <p class="meta">Run <code>{html.escape(run['id'])}</code> | {html.escape(run['base_ref'])}..{html.escape(run['head_ref'])} | Findings: {len(findings)}</p>
  <div class="summary-grid">
    <div class="metric"><strong>{len(findings)}</strong><span>verified findings</span></div>
    <div class="metric"><strong>{sum(1 for finding in findings if finding.get('recommended_action') == 'fix')}</strong><span>recommended fixes</span></div>
    <div class="metric"><strong>{len(fix_groups)}</strong><span>fix groups</span></div>
  </div>
  <section><h2>Executive Summary</h2><p>This compact report is generated after verifier recording and judging. Use it as the decision source before choosing fixes.</p></section>
  <section><h2>Risk Matrix And Decision Table</h2>
    <div class="risk-matrix table-wrap">
      <table>
        <thead><tr><th>#</th><th>ID</th><th>Severity</th><th>Criticality</th><th>File</th><th>Claim</th><th>Action</th><th>Risk if not fixed</th><th>Risk of fix</th><th>Effort</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
  </section>
  <section><h2>Consolidated Fix Groups</h2><div class="fix-groups">{''.join(group_cards)}</div></section>
  <section><h2>Finding Detail</h2>{''.join(details) or '<p>No verified findings.</p>'}</section>
  <section><h2>Decision Checklist</h2><ul class="checklist"><li>Review the severity badge and criticality for each finding.</li><li>Choose one action per finding or fix group: fix, accept risk, ignore, defer, or needs human.</li><li>For selected fixes, produce an implementation plan with exact tests and rollback notes before editing source code.</li></ul></section>
</main>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    run_dir = db_path.parent
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = conn.execute("select * from runs order by created_at limit 1").fetchone()
    if run is None:
        raise SystemExit(f"no run found in {db_path}")

    rows = conn.execute(
        """
        select f.*, c.category as candidate_category
        from final_findings f
        left join candidates c on c.id = f.candidate_id
        order by f.created_at, f.rowid
        """
    ).fetchall()
    findings = []
    for display_index, row in enumerate(rows, start=1):
        raw_finding = json.loads(row["report_json"])
        raw_finding.setdefault("category", row["candidate_category"])
        raw_finding["display_index"] = display_index
        finding = _normalize_finding(raw_finding)
        finding["id"] = row["id"]
        finding["available_actions"] = AVAILABLE_ACTIONS
        findings.append(finding)
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
    html_path = run_dir / "reports" / "ultrareview-report.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_markdown(run, findings), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    html_path.write_text(_html(run, findings), encoding="utf-8")
    conn.close()

    print(
        json.dumps(
            {
                "run_id": run["id"],
                "finding_count": len(findings),
                "markdown_path": str(markdown_path),
                "json_path": str(json_path),
                "html_path": str(html_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
