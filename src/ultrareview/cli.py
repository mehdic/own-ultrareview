from __future__ import annotations

import argparse
import html
import json
import sqlite3
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from ultrareview.gitcontext.collect import collect_git_context
from ultrareview.runtime import db
from ultrareview.runtime.packets import build_scout_tasks
from ultrareview.validation.contracts import validate_candidate, validate_verification


RUN_SUBDIRS = (
    "artifacts",
    "packets",
    "outputs",
    "validation",
    "temp/external-repos",
)

AVAILABLE_ACTIONS = ["fix", "accept_risk", "ignore", "defer", "needs_human"]
DECISION_QUESTION = "How should UltraReview handle this finding?"
DECISION_OPTIONS = [
    {"value": "fix", "label": "Fix before merge"},
    {"value": "accept_risk", "label": "Accept the risk"},
    {"value": "ignore", "label": "Ignore after review"},
    {"value": "defer", "label": "Defer to later work"},
    {"value": "needs_human", "label": "Needs human decision"},
]
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


RESOLUTION_STATUSES = ["fixed", "not_fixed", "accepted", "deferred", "ignored", "needs_human"]


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True))


def _completed_verifier_tasks_without_rows(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        select t.* from agent_tasks t
        where t.run_id = ?
          and t.phase = 'verification'
          and t.status = 'completed'
          and not exists (
            select 1 from verifications v
            where v.verifier_task_id = t.id
          )
        order by t.finished_at, t.rowid
        """,
        (run_id,),
    ).fetchall()


def _incomplete_tasks(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        select * from agent_tasks
        where run_id = ?
          and status != 'completed'
        order by created_at, rowid
        """,
        (run_id,),
    ).fetchall()


def _blocked_tasks(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        select * from agent_tasks
        where run_id = ?
          and status in ('running', 'failed')
        order by created_at, rowid
        """,
        (run_id,),
    ).fetchall()


def _candidates_without_verifier_tasks(conn: sqlite3.Connection, run_id: str) -> int:
    return conn.execute(
        """
        select count(*) from candidates c
        where c.run_id = ?
          and not exists (
            select 1 from agent_tasks t
            where t.run_id = c.run_id
              and t.phase = 'verification'
              and t.input_path like '%' || c.id || '%'
          )
        """,
        (run_id,),
    ).fetchone()[0]


def _decision_gate_violations(conn: sqlite3.Connection, run: sqlite3.Row, run_dir: Path) -> list[str]:
    violations = []
    incomplete_tasks = _incomplete_tasks(conn, run["id"])
    if incomplete_tasks:
        task_summary = ", ".join(f"{task['id']}:{task['phase']}:{task['status']}" for task in incomplete_tasks)
        violations.append(f"agent tasks are not completed: {task_summary}")

    missing_verifier_count = _candidates_without_verifier_tasks(conn, run["id"])
    if missing_verifier_count:
        violations.append(f"{missing_verifier_count} candidate(s) do not have verifier tasks")

    incomplete_verifiers = _completed_verifier_tasks_without_rows(conn, run["id"])
    if incomplete_verifiers:
        task_ids = ", ".join(task["id"] for task in incomplete_verifiers)
        violations.append(f"completed verification task(s) have no verifier rows: {task_ids}")

    finding_count = conn.execute("select count(*) from final_findings where run_id = ?", (run["id"],)).fetchone()[0]
    if finding_count == 0:
        verified_count = conn.execute(
            "select count(*) from verifications where run_id = ? and verdict = 'verified'",
            (run["id"],),
        ).fetchone()[0]
        if verified_count:
            violations.append("verified candidates exist but judge has not promoted final findings")

    html_path = run_dir / "reports" / "ultrareview-report.html"
    if not html_path.exists():
        violations.append("HTML decision report is missing; run own-ultrareview report before actions")

    return violations


def _report_paths(run_dir: Path) -> dict[str, str]:
    return {
        "markdown_path": str(run_dir / "final-report.md"),
        "json_path": str(run_dir / "final-report.json"),
        "html_path": str(run_dir / "reports" / "ultrareview-report.html"),
    }


def _first_run(conn: sqlite3.Connection) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    run = conn.execute("select * from runs order by created_at limit 1").fetchone()
    if run is None:
        raise SystemExit("no run found")
    return run


def _read_task_output_json(conn: sqlite3.Connection, task: sqlite3.Row, output_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        db.mark_task_failed(conn, task["id"], f"output file not found: {output_path}")
        raise SystemExit(f"output file not found: {output_path}") from exc
    except JSONDecodeError as exc:
        db.mark_task_failed(conn, task["id"], f"output JSON invalid: {exc.msg}")
        raise SystemExit(f"output JSON invalid: {exc.msg}") from exc
    if not isinstance(payload, dict):
        db.mark_task_failed(conn, task["id"], "output JSON must be an object")
        raise SystemExit("output JSON must be an object")
    return payload


def _init_run(repo: Path, base: str, head: str, mode: str, runs_root: Path | None) -> dict[str, str]:
    root = runs_root if runs_root is not None else repo / ".ultrareview" / "runs"
    bootstrap_db_path = root / "_bootstrap" / "review.sqlite"
    conn = db.connect(bootstrap_db_path)
    db.init_schema(conn)
    run = db.create_run(conn, str(repo), base, head, mode)
    conn.close()

    run_dir = root / run["id"]
    for subdir in RUN_SUBDIRS:
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)

    db_path = run_dir / "review.sqlite"
    bootstrap_db_path.replace(db_path)
    try:
        bootstrap_db_path.parent.rmdir()
    except OSError:
        pass

    return {"run_id": run["id"], "run_dir": str(run_dir), "db_path": str(db_path)}


def _collect_context(db_path: Path) -> dict[str, str]:
    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = _first_run(conn)
    context = collect_git_context(run["repo_path"], run["base_ref"], run["head_ref"])
    conn.execute(
        "update runs set base_sha = ?, head_sha = ?, updated_at = datetime('now') where id = ?",
        (context["base_sha"], context["head_sha"], run["id"]),
    )
    conn.commit()
    conn.close()

    artifact_path = db_path.parent / "artifacts" / "git-context.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(context, indent=2, sort_keys=True), encoding="utf-8")
    return {"artifact_path": str(artifact_path)}


def _prepare_tasks(db_path: Path) -> dict[str, Any]:
    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = _first_run(conn)
    existing = conn.execute("select count(*) from agent_tasks where run_id = ?", (run["id"],)).fetchone()[0]
    if existing:
        task_count = existing
    else:
        tasks = build_scout_tasks(
            conn,
            run["id"],
            db_path.parent,
            db_path.parent / "artifacts" / "git-context.json",
        )
        task_count = len(tasks)
    conn.close()
    return {"task_count": task_count}


def command_start(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    runs_root = Path(args.runs_root).expanduser().resolve() if args.runs_root else None
    payload = _init_run(repo, args.base, args.head, args.mode, runs_root)
    payload.update(_collect_context(Path(payload["db_path"])))
    payload.update(_prepare_tasks(Path(payload["db_path"])))
    payload["next"] = "own-ultrareview next --db <db_path>"
    _print(payload)
    return 0


def command_next(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    run_dir = db_path.parent
    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.row_factory = sqlite3.Row
    run = _first_run(conn)
    blocked = _blocked_tasks(conn, run["id"])
    if blocked:
        blocked_task = blocked[0]
        next_command = "record-verification" if blocked_task["phase"] == "verification" else "record-output"
        _print(
            {
                "run_id": run["id"],
                "status": "invalid_state",
                "task_id": blocked_task["id"],
                "role": blocked_task["role"],
                "phase": blocked_task["phase"],
                "task_status": blocked_task["status"],
                "error": blocked_task["error"],
                "packet_path": str(run_dir / blocked_task["input_path"]),
                "next": f"own-ultrareview {next_command} --db <db_path> --task-id <task_id> --output <corrected-output.json>",
                "rule": "A running or failed task must be completed with corrected output before the run can advance.",
            }
        )
        return 0
    task = db.next_task(conn, run["id"])
    if task is None:
        incomplete_verifiers = _completed_verifier_tasks_without_rows(conn, run["id"])
        if incomplete_verifiers:
            task = incomplete_verifiers[0]
            _print(
                {
                    "run_id": run["id"],
                    "status": "invalid_state",
                    "task_id": task["id"],
                    "role": task["role"],
                    "phase": task["phase"],
                    "packet_path": str(run_dir / task["input_path"]),
                    "problem": "verification task is completed but no row exists in verifications",
                    "next": "own-ultrareview record-verification --db <db_path> --task-id <task_id> --output <verifier-output.json>",
                    "rule": "Do not edit review.sqlite directly. Use record-verification to insert verifier results and complete the task.",
                }
            )
            return 0
        candidates_without_verifiers = conn.execute(
            """
            select count(*) from candidates c
            where c.run_id = ?
              and not exists (
                select 1 from agent_tasks t
                where t.run_id = c.run_id
                  and t.phase = 'verification'
                  and t.input_path like '%' || c.id || '%'
              )
            """,
            (run["id"],),
        ).fetchone()[0]
        if candidates_without_verifiers:
            _print(
                {
                    "run_id": run["id"],
                    "status": "needs_verification_setup",
                    "next": "own-ultrareview prepare-verifiers --db <db_path>",
                }
            )
            return 0
        _print({"run_id": run["id"], "status": "complete", "next": "own-ultrareview judge --db <db_path>"})
        return 0
    next_command = "record-verification" if task["phase"] == "verification" else "record-output"
    _print(
        {
            "run_id": run["id"],
            "task_id": task["id"],
            "role": task["role"],
            "phase": task["phase"],
            "status": "running",
            "packet_path": str(run_dir / task["input_path"]),
            "handoff": "Give this packet to the next sequential sub-agent, then record its JSON output.",
            "next": f"own-ultrareview {next_command} --db <db_path> --task-id <task_id> --output <output.json>",
        }
    )
    return 0


def command_record_output(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    task = conn.execute("select * from agent_tasks where id = ?", (args.task_id,)).fetchone()
    if task is None:
        raise SystemExit(f"unknown task id: {args.task_id}")
    if task["phase"] != "scouting":
        raise SystemExit("record-output only accepts scouting tasks; use record-verification for verification tasks")

    payload = _read_task_output_json(conn, task, output_path)
    if "candidates" not in payload:
        db.mark_task_failed(conn, args.task_id, "output missing required top-level 'candidates' array")
        raise SystemExit("output missing required top-level 'candidates' array")
    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        db.mark_task_failed(conn, args.task_id, "output field 'candidates' must be a list")
        raise SystemExit("output field 'candidates' must be a list")

    for index, candidate in enumerate(candidates):
        result = validate_candidate(candidate)
        if not result.valid:
            db.mark_task_failed(conn, args.task_id, f"candidate[{index}] invalid: {'; '.join(result.errors)}")
            raise SystemExit(f"candidate[{index}] invalid: {'; '.join(result.errors)}")

    if task["status"] == "completed":
        if task["output_path"] == str(output_path) and db.candidate_rows_match_output(conn, args.task_id, candidates):
            conn.close()
            _print(
                {
                    "run_id": task["run_id"],
                    "task_id": args.task_id,
                    "inserted_candidates": 0,
                    "next": "own-ultrareview next --db <db_path>",
                }
            )
            return 0
        raise SystemExit(f"task {args.task_id} is already completed with different recorded output")

    inserted = []
    for candidate in candidates:
        inserted.append(db.insert_candidate(conn, task["run_id"], args.task_id, candidate))
    db.mark_task_completed(conn, args.task_id, str(output_path))
    conn.close()
    _print(
        {
            "run_id": task["run_id"],
            "task_id": args.task_id,
            "inserted_candidates": len(inserted),
            "next": "own-ultrareview next --db <db_path>",
        }
    )
    return 0


def command_prepare_verifiers(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    run_dir = db_path.parent
    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = _first_run(conn)
    candidates = conn.execute(
        """
        select c.* from candidates c
        where not exists (
          select 1 from agent_tasks t
          where t.run_id = c.run_id
            and t.phase = 'verification'
            and t.input_path like '%' || c.id || '%'
        )
        order by c.created_at, c.rowid
        """
    ).fetchall()

    created = []
    for candidate in candidates:
        packet_path = run_dir / "packets" / f"verify-{candidate['id']}.json"
        packet = {
            "run_id": run["id"],
            "phase": "verification",
            "role": "verifier_agent",
            "objective": "Try to disprove this candidate bug before it can be reported.",
            "candidate": {
                "id": candidate["id"],
                "category": candidate["category"],
                "severity": candidate["severity"],
                "confidence": candidate["confidence"],
                "file": candidate["file_path"],
                "line": candidate["line"],
                "introduced_by_diff": candidate["introduced_by_diff"],
                "claim": candidate["claim"],
                "failure_mode": candidate["failure_mode"],
                "evidence": json.loads(candidate["evidence_json"]),
                "suggested_fix": candidate["suggested_fix"],
            },
            "instructions": [
                "Assume the scout may be wrong.",
                "Look for code paths, guards, tests, or constraints that invalidate the claim.",
                "Return verified only when the failure mode is concrete and diff-related.",
                "Return rejected for false positives. Return uncertain when evidence is insufficient.",
                "Return JSON that satisfies the verifier_output_schema on the first attempt.",
                "Every verification must include candidate_id, verdict, reason, and a non-empty evidence array.",
                "Every evidence item must include repo-relative path, positive integer line, and exact quote.",
            ],
            "verdict_contract": {
                "allowed": ["verified", "rejected", "uncertain"],
                "required_fields": ["candidate_id", "verdict", "reason", "evidence"],
                "required_evidence_fields": ["path", "line", "quote"],
                "reason": "Non-empty explanation of why the verifier verdict is correct.",
                "evidence": "Non-empty array of local code/config/test evidence supporting the verifier verdict.",
            },
            "verifier_output_schema": {
                "top_level_key": "verifications",
                "example": {
                    "verifications": [
                        {
                            "candidate_id": candidate["id"],
                            "verdict": "verified",
                            "reason": "The cited code path still reaches the failure mode introduced by the diff.",
                            "evidence": [
                                {
                                    "path": candidate["file_path"],
                                    "line": candidate["line"],
                                    "quote": "<exact quote from the local file>",
                                }
                            ],
                        }
                    ]
                },
            },
        }
        packet_path.parent.mkdir(parents=True, exist_ok=True)
        packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")
        created.append(db.create_task(conn, run["id"], "verifier_agent", "verification", str(packet_path.relative_to(run_dir))))
    conn.close()
    _print({"run_id": run["id"], "verifier_task_count": len(created), "next": "own-ultrareview next --db <db_path>"})
    return 0


def command_record_verification(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    task = conn.execute("select * from agent_tasks where id = ?", (args.task_id,)).fetchone()
    if task is None:
        raise SystemExit(f"unknown task id: {args.task_id}")
    if task["phase"] != "verification":
        raise SystemExit("record-verification only accepts verification tasks; use record-output for scouting tasks")

    packet_path = db_path.parent / task["input_path"]
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    expected_candidate_id = packet["candidate"]["id"]

    payload = _read_task_output_json(conn, task, output_path)
    if "verifications" not in payload:
        db.mark_task_failed(conn, args.task_id, "output missing required top-level 'verifications' array")
        raise SystemExit("output missing required top-level 'verifications' array")
    verifications = payload["verifications"]
    if not isinstance(verifications, list):
        db.mark_task_failed(conn, args.task_id, "output field 'verifications' must be a list")
        raise SystemExit("output field 'verifications' must be a list")
    if len(verifications) != 1:
        db.mark_task_failed(conn, args.task_id, "verification task output must contain exactly one verification")
        raise SystemExit("verification task output must contain exactly one verification")
    if verifications[0].get("candidate_id") != expected_candidate_id:
        db.mark_task_failed(conn, args.task_id, "verification candidate_id does not match task packet")
        raise SystemExit("verification candidate_id does not match task packet")

    for index, verification in enumerate(verifications):
        result = validate_verification(verification)
        if not result.valid:
            db.mark_task_failed(conn, args.task_id, f"verification[{index}] invalid: {'; '.join(result.errors)}")
            raise SystemExit(f"verification[{index}] invalid: {'; '.join(result.errors)}")

    if task["status"] == "completed":
        if task["output_path"] == str(output_path) and db.verification_rows_match_output(conn, args.task_id, verifications):
            conn.close()
            _print(
                {
                    "run_id": task["run_id"],
                    "task_id": args.task_id,
                    "inserted_verifications": 0,
                    "next": "own-ultrareview next --db <db_path>",
                }
            )
            return 0
        raise SystemExit(f"task {args.task_id} is already completed with different recorded output")

    inserted = []
    for verification in verifications:
        inserted.append(db.insert_verification(conn, task["run_id"], verification["candidate_id"], args.task_id, verification))
    db.mark_task_completed(conn, args.task_id, str(output_path))
    conn.close()
    _print(
        {
            "run_id": task["run_id"],
            "task_id": args.task_id,
            "inserted_verifications": len(inserted),
            "next": "own-ultrareview next --db <db_path>",
        }
    )
    return 0


def _finding_report(candidate: sqlite3.Row, verification: sqlite3.Row) -> dict[str, object]:
    return _normalize_finding({
        "category": candidate["category"],
        "severity": candidate["severity"],
        "confidence": candidate["confidence"],
        "file": candidate["file_path"],
        "line": candidate["line"],
        "introduced_by_diff": candidate["introduced_by_diff"],
        "claim": candidate["claim"],
        "failure_mode": candidate["failure_mode"],
        "evidence": json.loads(candidate["evidence_json"]),
        "suggested_fix": candidate["suggested_fix"],
        "verification_verdict": verification["verdict"],
        "verification_reason": verification["reason"],
        "verification_evidence": json.loads(verification["evidence_json"]),
    })


def command_judge(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.row_factory = sqlite3.Row
    run = _first_run(conn)
    incomplete_verifiers = _completed_verifier_tasks_without_rows(conn, run["id"])
    if incomplete_verifiers:
        task_ids = ", ".join(task["id"] for task in incomplete_verifiers)
        raise SystemExit(
            "cannot judge: completed verification task(s) have no verifier rows. "
            f"Run record-verification for: {task_ids}. Do not edit review.sqlite directly."
        )
    rows = conn.execute(
        """
        select c.*, v.verdict, v.reason, v.evidence_json as verification_evidence_json
        from candidates c
        join verifications v on v.candidate_id = c.id
        where v.verdict = 'verified'
          and not exists (select 1 from final_findings f where f.candidate_id = c.id)
        order by c.created_at, c.rowid
        """
    ).fetchall()
    inserted = []
    for row in rows:
        verification = {
            "verdict": row["verdict"],
            "reason": row["reason"],
            "evidence_json": row["verification_evidence_json"],
        }
        inserted.append(
            db.insert_final_finding(
                conn,
                run["id"],
                row["id"],
                {
                    "final_severity": row["severity"],
                    "confidence": row["confidence"],
                    "report": _finding_report(row, verification),
                },
            )
        )
    conn.close()
    _print({"run_id": run["id"], "final_finding_count": len(inserted), "next": "own-ultrareview report --db <db_path>"})
    return 0


def _render_markdown(run: sqlite3.Row, findings: list[dict[str, object]]) -> str:
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
        lines.extend(
            [
                f"## {index}. {str(finding['severity']).upper()} - {finding['file']}:{finding['line']}",
                "",
                f"**Finding ID:** `{finding['id']}`",
                "",
                f"**Claim:** {finding['claim']}",
                "",
                f"**Failure mode:** {finding['failure_mode']}",
                "",
                f"**Introduced by diff:** {finding.get('introduced_by_diff') or 'Not recorded.'}",
                "",
                f"**Suggested fix:** {finding.get('suggested_fix') or 'Not recorded.'}",
                "",
                f"**Verification:** {finding.get('verification_verdict', 'unknown')} - {finding.get('verification_reason', '')}",
                "",
                f"**Decision question:** {DECISION_QUESTION}",
                "",
                f"**Available actions:** {', '.join(AVAILABLE_ACTIONS)}",
                "",
            ]
        )
    return "\n".join(lines)


def _html_cell(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _render_html(run: sqlite3.Row, findings: list[dict[str, object]]) -> str:
    rows = []
    for index, finding in enumerate(findings, start=1):
        severity = str(finding.get("severity") or "unknown")
        severity_slug = _severity_slug(severity)
        rows.append(
            "<tr>"
            f"<td>{index}</td>"
            f"<td><code>{_html_cell(finding.get('id'))}</code></td>"
            f"<td><span class=\"severity-badge severity-{severity_slug}\">{_html_cell(SEVERITY_LABELS.get(severity, severity))}</span></td>"
            f"<td><span class=\"criticality criticality-{severity_slug}\">{_html_cell(finding.get('criticality'))}</span></td>"
            f"<td>{_html_cell(finding.get('file'))}:{_html_cell(finding.get('line'))}</td>"
            f"<td>{_html_cell(finding.get('claim'))}</td>"
            f"<td>{_html_cell(finding.get('recommended_action'))}</td>"
            f"<td>{_html_cell(finding.get('risk_if_not_fixed'))}</td>"
            f"<td>{_html_cell(finding.get('risk_of_fix'))}</td>"
            f"<td>{_html_cell(finding.get('effort'))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan=\"10\">No verified findings.</td></tr>")

    details = []
    for index, finding in enumerate(findings, start=1):
        severity = str(finding.get("severity") or "unknown")
        severity_slug = _severity_slug(severity)
        evidence = finding.get("evidence") or []
        evidence_items = "".join(
            f"<li><code>{_html_cell(item.get('path'))}:{_html_cell(item.get('line'))}</code> {_html_cell(item.get('quote'))}</li>"
            for item in evidence
            if isinstance(item, dict)
        )
        details.append(
            f"<article class=\"finding-detail\" id=\"finding-{index}\">"
            f"<h3><span>{index}. {_html_cell(finding.get('claim'))}</span> <span class=\"severity-badge severity-{severity_slug}\">{_html_cell(SEVERITY_LABELS.get(severity, severity))}</span></h3>"
            f"<p><strong>Criticality:</strong> {_html_cell(finding.get('criticality'))}</p>"
            f"<p><strong>Failure mode:</strong> {_html_cell(finding.get('failure_mode'))}</p>"
            f"<p><strong>Introduced by diff:</strong> {_html_cell(finding.get('introduced_by_diff') or 'Not recorded.')}</p>"
            f"<p><strong>Recommended action:</strong> {_html_cell(finding.get('recommended_action'))}</p>"
            f"<p><strong>Suggested fix:</strong> {_html_cell(finding.get('suggested_fix'))}</p>"
            f"<p><strong>Risk if not fixed:</strong> {_html_cell(finding.get('risk_if_not_fixed'))}</p>"
            f"<p><strong>Risk of fix:</strong> {_html_cell(finding.get('risk_of_fix'))}</p>"
            f"<p><strong>Effort:</strong> {_html_cell(finding.get('effort'))}</p>"
            f"<p><strong>Verification:</strong> {_html_cell(finding.get('verification_verdict'))} - {_html_cell(finding.get('verification_reason'))}</p>"
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
            f"<h3>{_html_cell(group)}</h3>"
            f"<p>{len(group_findings)} finding(s) | recommended action: {_html_cell(group_findings[0].get('recommended_action'))} | effort: {_html_cell(group_findings[0].get('effort'))}</p>"
            f"<p>{_html_cell(group_findings[0].get('suggested_fix'))}</p>"
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
  <section><h2>Executive Summary</h2><p>This compact report is generated only after verifier recording and judging. Use it as the decision source before choosing fixes.</p></section>
  <section><h2>Risk Matrix</h2>
    <div class="risk-matrix table-wrap">
      <table>
        <thead><tr><th>#</th><th>ID</th><th>Severity</th><th>Criticality</th><th>File</th><th>Claim</th><th>Action</th><th>Risk if not fixed</th><th>Risk of fix</th><th>Effort</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
  </section>
  <h2>Consolidated Fix Groups</h2>
  <div class="fix-groups">{''.join(group_cards)}</div>
  <section><h2>Finding Detail</h2>{''.join(details) or '<p>No verified findings.</p>'}</section>
  <section><h2>Decision Checklist</h2><ul class="checklist"><li>Review the severity badge and criticality for each finding.</li><li>Choose one action per finding or fix group: fix, accept risk, ignore, defer, or needs human.</li><li>For selected fixes, produce an implementation plan with exact tests and rollback notes before editing source code.</li></ul></section>
</main>
</body>
</html>
"""


def command_report(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    run_dir = db_path.parent
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = _first_run(conn)
    findings = []
    for row in conn.execute(
        """
        select f.*, c.category as candidate_category
        from final_findings f
        left join candidates c on c.id = f.candidate_id
        order by f.created_at, f.rowid
        """
    ).fetchall():
        raw_finding = json.loads(row["report_json"])
        raw_finding.setdefault("category", row["candidate_category"])
        finding = _normalize_finding(raw_finding)
        finding["id"] = row["id"]
        finding["available_actions"] = AVAILABLE_ACTIONS
        finding["decision_question"] = DECISION_QUESTION
        finding["decision_options"] = DECISION_OPTIONS
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
    markdown_path.write_text(_render_markdown(run, findings), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    html_path.write_text(_render_html(run, findings), encoding="utf-8")
    conn.close()
    _print(
        {
            "run_id": run["id"],
            "finding_count": len(findings),
            "markdown_path": str(markdown_path),
            "json_path": str(json_path),
            "html_path": str(html_path),
        }
    )
    return 0


def _decision_payload(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "decision": row["decision"],
        "note": row["note"],
        "updated_at": row["updated_at"],
    }


def _resolution_payload(row: sqlite3.Row) -> dict[str, object] | None:
    if row["resolution_status"] is None:
        return None
    return {
        "status": row["resolution_status"],
        "summary": row["resolution_summary"],
        "evidence": json.loads(row["resolution_evidence_json"]),
        "updated_at": row["resolution_updated_at"],
    }


def command_actions(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    run_dir = db_path.parent
    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.row_factory = sqlite3.Row
    run = _first_run(conn)
    violations = _decision_gate_violations(conn, run, run_dir)
    if violations:
        conn.close()
        raise SystemExit(
            "cannot present decisions before the review decision gate is complete: "
            + "; ".join(violations)
            + ". Required order: complete scout tasks, prepare and record verifier tasks, run judge, run report, then run actions."
        )
    rows = conn.execute(
        """
        select f.*, d.decision, d.note, d.updated_at
             , r.status as resolution_status
             , r.summary as resolution_summary
             , r.evidence_json as resolution_evidence_json
             , r.updated_at as resolution_updated_at
             , c.category as candidate_category
        from final_findings f
        left join candidates c on c.id = f.candidate_id
        left join finding_decisions d on d.final_finding_id = f.id
        left join finding_resolutions r on r.final_finding_id = f.id
        where f.run_id = ?
        order by f.created_at, f.rowid
        """,
        (run["id"],),
    ).fetchall()

    findings = []
    open_count = 0
    for row in rows:
        raw_report = json.loads(row["report_json"])
        raw_report.setdefault("category", row["candidate_category"])
        report = _normalize_finding(raw_report)
        decision = None
        if row["decision"] is None:
            open_count += 1
        else:
            decision = _decision_payload(row)
        findings.append(
            {
                "id": row["id"],
                "severity": row["final_severity"],
                "confidence": row["confidence"],
                "file": report.get("file"),
                "line": report.get("line"),
                "claim": report.get("claim"),
                "failure_mode": report.get("failure_mode"),
                "introduced_by_diff": report.get("introduced_by_diff"),
                "recommended_action": report.get("recommended_action"),
                "suggested_fix": report.get("suggested_fix"),
                "fix_group": report.get("fix_group"),
                "criticality": report.get("criticality"),
                "risk_if_not_fixed": report.get("risk_if_not_fixed"),
                "risk_of_fix": report.get("risk_of_fix"),
                "effort": report.get("effort"),
                "verification": {
                    "verdict": report.get("verification_verdict"),
                    "reason": report.get("verification_reason"),
                },
                "available_actions": AVAILABLE_ACTIONS,
                "decision_question": DECISION_QUESTION,
                "decision_options": DECISION_OPTIONS,
                "decision": decision,
                "resolution": _resolution_payload(row),
            }
        )
    conn.close()
    _print(
        {
            "run_id": run["id"],
            "decision_gate_complete": True,
            **_report_paths(run_dir),
            "instruction": "Present html_path before any chat findings table or decision request.",
            "open_finding_count": open_count,
            "findings": findings,
        }
    )
    return 0


def command_decide(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.row_factory = sqlite3.Row
    finding = conn.execute(
        "select * from final_findings where id = ?",
        (args.finding_id,),
    ).fetchone()
    if finding is None:
        raise SystemExit(f"unknown finding id: {args.finding_id}")
    decision = db.upsert_finding_decision(
        conn,
        finding["run_id"],
        args.finding_id,
        args.decision,
        args.note,
    )
    conn.close()
    _print(
        {
            "run_id": finding["run_id"],
            "finding_id": args.finding_id,
            "decision": decision["decision"],
            "status": "recorded",
        }
    )
    return 0


def command_resolve(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.row_factory = sqlite3.Row
    finding = conn.execute(
        "select * from final_findings where id = ?",
        (args.finding_id,),
    ).fetchone()
    if finding is None:
        raise SystemExit(f"unknown finding id: {args.finding_id}")
    resolution = db.upsert_finding_resolution(
        conn,
        finding["run_id"],
        args.finding_id,
        args.status,
        args.summary,
        args.evidence,
    )
    conn.close()
    _print(
        {
            "run_id": finding["run_id"],
            "finding_id": args.finding_id,
            "resolution": resolution["status"],
            "status": "recorded",
        }
    )
    return 0


def _count_by(rows: list[sqlite3.Row], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row[key])
        counts[value] = counts.get(value, 0) + 1
    return counts


def _summary_findings(conn: sqlite3.Connection, run_id: str) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        select f.*, d.decision, d.note, d.updated_at
             , r.status as resolution_status
             , r.summary as resolution_summary
             , r.evidence_json as resolution_evidence_json
             , r.updated_at as resolution_updated_at
        from final_findings f
        left join finding_decisions d on d.final_finding_id = f.id
        left join finding_resolutions r on r.final_finding_id = f.id
        where f.run_id = ?
        order by f.created_at, f.rowid
        """,
        (run_id,),
    ).fetchall()
    findings = []
    for row in rows:
        report = json.loads(row["report_json"])
        findings.append(
            {
                "id": row["id"],
                "severity": row["final_severity"],
                "confidence": row["confidence"],
                "file": report.get("file"),
                "line": report.get("line"),
                "claim": report.get("claim"),
                "failure_mode": report.get("failure_mode"),
                "introduced_by_diff": report.get("introduced_by_diff"),
                "suggested_fix": report.get("suggested_fix"),
                "verification": {
                    "verdict": report.get("verification_verdict"),
                    "reason": report.get("verification_reason"),
                },
                "decision_question": DECISION_QUESTION,
                "decision_options": DECISION_OPTIONS,
                "decision": _decision_payload(row) if row["decision"] is not None else None,
                "resolution": _resolution_payload(row),
            }
        )
    return findings


def _build_summary(conn: sqlite3.Connection, run: sqlite3.Row) -> dict[str, object]:
    tasks = conn.execute(
        """
        select id, role, phase, status, input_path, output_path, error, started_at, finished_at
        from agent_tasks
        where run_id = ?
        order by created_at, rowid
        """,
        (run["id"],),
    ).fetchall()
    verifications = conn.execute(
        "select verdict from verifications where run_id = ?",
        (run["id"],),
    ).fetchall()
    candidate_count = conn.execute("select count(*) from candidates where run_id = ?", (run["id"],)).fetchone()[0]
    findings = _summary_findings(conn, run["id"])
    return {
        "run": {
            "id": run["id"],
            "repo_path": run["repo_path"],
            "base_ref": run["base_ref"],
            "head_ref": run["head_ref"],
            "base_sha": run["base_sha"],
            "head_sha": run["head_sha"],
            "mode": run["mode"],
        },
        "tasks": {
            "total": len(tasks),
            "by_status": _count_by(tasks, "status"),
            "by_phase": _count_by(tasks, "phase"),
            "items": [dict(task) for task in tasks],
        },
        "review": {
            "candidate_count": candidate_count,
            "verification_count": len(verifications),
            "verifications_by_verdict": _count_by(verifications, "verdict"),
            "final_finding_count": len(findings),
            "open_decision_count": sum(1 for finding in findings if finding["decision"] is None),
            "resolved_finding_count": sum(1 for finding in findings if finding["resolution"] is not None),
        },
        "findings": findings,
    }


def _summary_markdown(summary: dict[str, object]) -> str:
    run = summary["run"]
    tasks = summary["tasks"]
    review = summary["review"]
    findings = summary["findings"]
    lines = [
        "# UltraReview Run Summary",
        "",
        f"- Run: `{run['id']}`",
        f"- Repo: `{run['repo_path']}`",
        f"- Range: `{run['base_ref']}..{run['head_ref']}`",
        "",
        "## What Ran",
        "",
        f"- Tasks: {tasks['total']}",
        f"- By phase: `{json.dumps(tasks['by_phase'], sort_keys=True)}`",
        f"- By status: `{json.dumps(tasks['by_status'], sort_keys=True)}`",
        "",
        "## What Was Found",
        "",
        f"- Candidates: {review['candidate_count']}",
        f"- Verifications: {review['verification_count']}",
        f"- Final findings: {review['final_finding_count']}",
        "",
    ]
    if not findings:
        lines.extend(["No verified findings survived judge review.", ""])
    for index, finding in enumerate(findings, start=1):
        lines.extend(
            [
                f"### {index}. {str(finding['severity']).upper()} - {finding['file']}:{finding['line']}",
                "",
                f"- Finding ID: `{finding['id']}`",
                f"- Claim: {finding['claim']}",
                f"- Failure mode: {finding['failure_mode']}",
                f"- Introduced by diff: {finding.get('introduced_by_diff') or 'Not recorded.'}",
                f"- Suggested fix: {finding.get('suggested_fix') or 'Not recorded.'}",
                f"- Verification: {finding['verification']['verdict']} - {finding['verification']['reason']}",
                "",
            ]
        )

    lines.extend(["## What Was Decided", ""])
    if not findings:
        lines.extend(["No user decisions were needed.", ""])
    for finding in findings:
        decision = finding["decision"]
        value = "pending" if decision is None else f"{decision['decision']} - {decision.get('note') or ''}".strip()
        lines.append(f"- `{finding['id']}`: {value}")
    lines.append("")

    lines.extend(["## What Was Fixed", ""])
    if not findings:
        lines.extend(["No fixes were needed.", ""])
    for finding in findings:
        resolution = finding["resolution"]
        value = "pending" if resolution is None else f"{resolution['status']} - {resolution['summary']}"
        lines.append(f"- `{finding['id']}`: {value}")
    lines.append("")
    return "\n".join(lines)


def command_summary(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    run_dir = db_path.parent
    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.row_factory = sqlite3.Row
    run = _first_run(conn)
    summary = _build_summary(conn, run)
    markdown_path = run_dir / "run-summary.md"
    json_path = run_dir / "run-summary.json"
    markdown_path.write_text(_summary_markdown(summary), encoding="utf-8")
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    conn.close()
    _print(
        {
            "run_id": run["id"],
            "finding_count": summary["review"]["final_finding_count"],
            "markdown_path": str(markdown_path),
            "json_path": str(json_path),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="own-ultrareview")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Initialize run, collect git context, and prepare scout packets.")
    start.add_argument("--repo", default=".", help="Repository path.")
    start.add_argument("--base", default="origin/main", help="Base git ref.")
    start.add_argument("--head", default="HEAD", help="Head git ref.")
    start.add_argument("--mode", default="copilot-git-only", help="Runtime mode.")
    start.add_argument("--runs-root", help="Optional run storage root.")
    start.set_defaults(func=command_start)

    next_cmd = sub.add_parser("next", help="Lease the next sequential agent task.")
    next_cmd.add_argument("--db", required=True, help="Path to review.sqlite.")
    next_cmd.set_defaults(func=command_next)

    record = sub.add_parser("record-output", help="Record scout agent candidate JSON.")
    record.add_argument("--db", required=True, help="Path to review.sqlite.")
    record.add_argument("--task-id", required=True, help="Task id to complete.")
    record.add_argument("--output", required=True, help="Agent JSON output path.")
    record.set_defaults(func=command_record_output)

    verifiers = sub.add_parser("prepare-verifiers", help="Create verifier tasks for recorded candidates.")
    verifiers.add_argument("--db", required=True, help="Path to review.sqlite.")
    verifiers.set_defaults(func=command_prepare_verifiers)

    record_verification = sub.add_parser("record-verification", help="Record verifier agent JSON.")
    record_verification.add_argument("--db", required=True, help="Path to review.sqlite.")
    record_verification.add_argument("--task-id", required=True, help="Verifier task id to complete.")
    record_verification.add_argument("--output", required=True, help="Verifier JSON output path.")
    record_verification.set_defaults(func=command_record_verification)

    judge = sub.add_parser("judge", help="Promote verified candidates to final findings.")
    judge.add_argument("--db", required=True, help="Path to review.sqlite.")
    judge.set_defaults(func=command_judge)

    report = sub.add_parser("report", help="Render final report files.")
    report.add_argument("--db", required=True, help="Path to review.sqlite.")
    report.set_defaults(func=command_report)

    actions = sub.add_parser("actions", help="List verified findings and available user decisions.")
    actions.add_argument("--db", required=True, help="Path to review.sqlite.")
    actions.set_defaults(func=command_actions)

    decide = sub.add_parser("decide", help="Record a user decision for a finding.")
    decide.add_argument("--db", required=True, help="Path to review.sqlite.")
    decide.add_argument("--finding-id", required=True, help="Final finding id.")
    decide.add_argument("--decision", required=True, choices=AVAILABLE_ACTIONS, help="Decision to record.")
    decide.add_argument("--note", default=None, help="Optional decision note.")
    decide.set_defaults(func=command_decide)

    resolve = sub.add_parser("resolve", help="Record what happened after a finding decision.")
    resolve.add_argument("--db", required=True, help="Path to review.sqlite.")
    resolve.add_argument("--finding-id", required=True, help="Final finding id.")
    resolve.add_argument("--status", required=True, choices=RESOLUTION_STATUSES, help="Resolution status.")
    resolve.add_argument("--summary", required=True, help="What changed or why no change was made.")
    resolve.add_argument("--evidence", action="append", default=[], help="Commit, file, test, or note supporting the resolution.")
    resolve.set_defaults(func=command_resolve)

    summary = sub.add_parser("summary", help="Render the full audit trail: tasks, findings, decisions, and resolutions.")
    summary.add_argument("--db", required=True, help="Path to review.sqlite.")
    summary.set_defaults(func=command_summary)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
