from __future__ import annotations

import argparse
import json
import sqlite3
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


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True))


def _first_run(conn: sqlite3.Connection) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    run = conn.execute("select * from runs order by created_at limit 1").fetchone()
    if run is None:
        raise SystemExit("no run found")
    return run


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
    conn.row_factory = sqlite3.Row
    run = _first_run(conn)
    task = db.next_task(conn, run["id"])
    if task is None:
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
    db.mark_task_running(conn, task["id"])
    _print(
        {
            "run_id": run["id"],
            "task_id": task["id"],
            "role": task["role"],
            "phase": task["phase"],
            "status": "running",
            "packet_path": str(run_dir / task["input_path"]),
            "handoff": "Give this packet to the next sequential sub-agent, then record its JSON output.",
            "next": "own-ultrareview record-output --db <db_path> --task-id <task_id> --output <output.json>",
        }
    )
    return 0


def command_record_output(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        raise SystemExit("output field 'candidates' must be a list")

    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    task = conn.execute("select * from agent_tasks where id = ?", (args.task_id,)).fetchone()
    if task is None:
        raise SystemExit(f"unknown task id: {args.task_id}")

    inserted = []
    for index, candidate in enumerate(candidates):
        result = validate_candidate(candidate)
        if not result.valid:
            db.mark_task_failed(conn, args.task_id, f"candidate[{index}] invalid: {'; '.join(result.errors)}")
            raise SystemExit(f"candidate[{index}] invalid: {'; '.join(result.errors)}")
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
                "claim": candidate["claim"],
                "failure_mode": candidate["failure_mode"],
                "evidence": json.loads(candidate["evidence_json"]),
            },
            "instructions": [
                "Assume the scout may be wrong.",
                "Look for code paths, guards, tests, or constraints that invalidate the claim.",
                "Return verified only when the failure mode is concrete and diff-related.",
                "Return rejected for false positives. Return uncertain when evidence is insufficient.",
            ],
            "verdict_contract": {
                "allowed": ["verified", "rejected", "uncertain"],
                "required_fields": ["candidate_id", "verdict", "reason", "evidence"],
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
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    verifications = payload.get("verifications", [])
    if not isinstance(verifications, list):
        raise SystemExit("output field 'verifications' must be a list")

    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    task = conn.execute("select * from agent_tasks where id = ?", (args.task_id,)).fetchone()
    if task is None:
        raise SystemExit(f"unknown task id: {args.task_id}")
    inserted = []
    for index, verification in enumerate(verifications):
        result = validate_verification(verification)
        if not result.valid:
            db.mark_task_failed(conn, args.task_id, f"verification[{index}] invalid: {'; '.join(result.errors)}")
            raise SystemExit(f"verification[{index}] invalid: {'; '.join(result.errors)}")
        inserted.append(db.insert_verification(conn, task["run_id"], verification["candidate_id"], args.task_id, verification))
    db.mark_task_completed(conn, args.task_id, str(output_path))
    conn.close()
    _print(
        {
            "run_id": task["run_id"],
            "task_id": args.task_id,
            "inserted_verifications": len(inserted),
            "next": "own-ultrareview judge --db <db_path>",
        }
    )
    return 0


def _finding_report(candidate: sqlite3.Row, verification: sqlite3.Row) -> dict[str, object]:
    return {
        "category": candidate["category"],
        "severity": candidate["severity"],
        "confidence": candidate["confidence"],
        "file": candidate["file_path"],
        "line": candidate["line"],
        "claim": candidate["claim"],
        "failure_mode": candidate["failure_mode"],
        "evidence": json.loads(candidate["evidence_json"]),
        "verification_verdict": verification["verdict"],
        "verification_reason": verification["reason"],
        "verification_evidence": json.loads(verification["evidence_json"]),
    }


def command_judge(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = _first_run(conn)
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
                f"**Verification:** {finding.get('verification_verdict', 'unknown')} - {finding.get('verification_reason', '')}",
                "",
                f"**Available actions:** {', '.join(AVAILABLE_ACTIONS)}",
                "",
            ]
        )
    return "\n".join(lines)


def command_report(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    run_dir = db_path.parent
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = _first_run(conn)
    findings = []
    for row in conn.execute("select * from final_findings order by created_at, rowid").fetchall():
        finding = json.loads(row["report_json"])
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
    markdown_path.write_text(_render_markdown(run, findings), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    conn.close()
    _print({"run_id": run["id"], "finding_count": len(findings), "markdown_path": str(markdown_path), "json_path": str(json_path)})
    return 0


def _decision_payload(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "decision": row["decision"],
        "note": row["note"],
        "updated_at": row["updated_at"],
    }


def command_actions(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    run = _first_run(conn)
    rows = conn.execute(
        """
        select f.*, d.decision, d.note, d.updated_at
        from final_findings f
        left join finding_decisions d on d.final_finding_id = f.id
        where f.run_id = ?
        order by f.created_at, f.rowid
        """,
        (run["id"],),
    ).fetchall()

    findings = []
    open_count = 0
    for row in rows:
        report = json.loads(row["report_json"])
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
                "verification": {
                    "verdict": report.get("verification_verdict"),
                    "reason": report.get("verification_reason"),
                },
                "available_actions": AVAILABLE_ACTIONS,
                "decision": decision,
            }
        )
    conn.close()
    _print({"run_id": run["id"], "open_finding_count": open_count, "findings": findings})
    return 0


def command_decide(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    conn = db.connect(db_path)
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
