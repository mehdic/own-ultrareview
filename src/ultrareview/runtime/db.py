from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _row_to_dict(cursor: sqlite3.Cursor, row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {description[0]: value for description, value in zip(cursor.description, row)}


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("pragma foreign_keys = on")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists runs (
          id text primary key,
          repo_path text not null,
          base_ref text not null,
          head_ref text not null,
          base_sha text,
          head_sha text,
          mode text not null,
          status text not null,
          created_at text not null,
          updated_at text not null
        );

        create table if not exists agent_tasks (
          id text primary key,
          run_id text not null references runs(id) on delete cascade,
          role text not null,
          phase text not null,
          status text not null,
          input_path text not null,
          output_path text,
          started_at text,
          finished_at text,
          error text,
          created_at text not null
        );

        create table if not exists candidates (
          id text primary key,
          run_id text not null references runs(id) on delete cascade,
          source_task_id text not null references agent_tasks(id),
          category text not null,
          severity text not null,
          confidence integer not null,
          file_path text not null,
          line integer not null,
          introduced_by_diff text,
          claim text not null,
          failure_mode text not null,
          evidence_json text not null,
          suggested_fix text,
          status text not null,
          created_at text not null
        );

        create table if not exists verifications (
          id text primary key,
          run_id text not null references runs(id) on delete cascade,
          candidate_id text not null references candidates(id) on delete cascade,
          verifier_task_id text not null references agent_tasks(id),
          verdict text not null,
          evidence_json text not null,
          reason text not null,
          created_at text not null
        );

        create table if not exists final_findings (
          id text primary key,
          run_id text not null references runs(id) on delete cascade,
          candidate_id text not null references candidates(id),
          final_severity text not null,
          confidence integer not null,
          report_json text not null,
          created_at text not null
        );

        create table if not exists finding_decisions (
          id text primary key,
          run_id text not null references runs(id) on delete cascade,
          final_finding_id text not null references final_findings(id) on delete cascade,
          decision text not null,
          note text,
          created_at text not null,
          updated_at text not null,
          unique(final_finding_id)
        );

        create table if not exists finding_resolutions (
          id text primary key,
          run_id text not null references runs(id) on delete cascade,
          final_finding_id text not null references final_findings(id) on delete cascade,
          status text not null,
          summary text not null,
          evidence_json text not null,
          created_at text not null,
          updated_at text not null,
          unique(final_finding_id)
        );
        """
    )
    _ensure_column(conn, "candidates", "introduced_by_diff", "text")
    _ensure_column(conn, "candidates", "suggested_fix", "text")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"pragma table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"alter table {table} add column {column} {definition}")


def create_run(
    conn: sqlite3.Connection,
    repo_path: str,
    base_ref: str,
    head_ref: str,
    mode: str,
    *,
    base_sha: str | None = None,
    head_sha: str | None = None,
) -> dict[str, Any]:
    now = _now()
    run = {
        "id": _id("run"),
        "repo_path": repo_path,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "mode": mode,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
    }
    conn.execute(
        """
        insert into runs (
          id, repo_path, base_ref, head_ref, base_sha, head_sha,
          mode, status, created_at, updated_at
        ) values (
          :id, :repo_path, :base_ref, :head_ref, :base_sha, :head_sha,
          :mode, :status, :created_at, :updated_at
        )
        """,
        run,
    )
    conn.commit()
    return run


def create_task(
    conn: sqlite3.Connection,
    run_id: str,
    role: str,
    phase: str,
    input_path: str,
) -> dict[str, Any]:
    task = {
        "id": _id("task"),
        "run_id": run_id,
        "role": role,
        "phase": phase,
        "status": "pending",
        "input_path": input_path,
        "output_path": None,
        "started_at": None,
        "finished_at": None,
        "error": None,
        "created_at": _now(),
    }
    conn.execute(
        """
        insert into agent_tasks (
          id, run_id, role, phase, status, input_path, output_path,
          started_at, finished_at, error, created_at
        ) values (
          :id, :run_id, :role, :phase, :status, :input_path, :output_path,
          :started_at, :finished_at, :error, :created_at
        )
        """,
        task,
    )
    conn.commit()
    return task


def next_task(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    now = _now()
    cursor = conn.execute(
        """
        update agent_tasks
        set status = 'running', started_at = ?, error = null
        where id = (
          select id from agent_tasks
          where run_id = ? and status = 'pending'
          order by created_at, rowid
          limit 1
        )
        returning *
        """,
        (now, run_id),
    )
    task = _row_to_dict(cursor, cursor.fetchone())
    conn.commit()
    return task


def mark_task_running(conn: sqlite3.Connection, task_id: str) -> None:
    now = _now()
    conn.execute(
        "update agent_tasks set status = 'running', started_at = ?, error = null where id = ?",
        (now, task_id),
    )
    conn.commit()


def mark_task_completed(conn: sqlite3.Connection, task_id: str, output_path: str) -> None:
    now = _now()
    conn.execute(
        """
        update agent_tasks
        set status = 'completed', output_path = ?, finished_at = ?, error = null
        where id = ?
        """,
        (output_path, now, task_id),
    )
    conn.commit()


def mark_task_failed(conn: sqlite3.Connection, task_id: str, error: str) -> None:
    now = _now()
    conn.execute(
        """
        update agent_tasks
        set status = 'failed', finished_at = ?, error = ?
        where id = ?
        """,
        (now, error, task_id),
    )
    conn.commit()


def insert_candidate(
    conn: sqlite3.Connection,
    run_id: str,
    source_task_id: str,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    row = {
        "id": _id("cand"),
        "run_id": run_id,
        "source_task_id": source_task_id,
        "category": candidate["category"],
        "severity": candidate["severity"],
        "confidence": int(candidate["confidence"]),
        "file_path": candidate["file"],
        "line": int(candidate["line"]),
        "introduced_by_diff": candidate.get("introduced_by_diff"),
        "claim": candidate["claim"],
        "failure_mode": candidate["failure_mode"],
        "evidence_json": json.dumps(candidate["evidence"], sort_keys=True),
        "suggested_fix": candidate.get("suggested_fix"),
        "status": candidate.get("status", "accepted"),
        "created_at": _now(),
    }
    conn.execute(
        """
        insert into candidates (
          id, run_id, source_task_id, category, severity, confidence,
          file_path, line, introduced_by_diff, claim, failure_mode, evidence_json,
          suggested_fix, status, created_at
        ) values (
          :id, :run_id, :source_task_id, :category, :severity, :confidence,
          :file_path, :line, :introduced_by_diff, :claim, :failure_mode, :evidence_json,
          :suggested_fix, :status, :created_at
        )
        """,
        row,
    )
    conn.commit()
    return row


def candidate_rows_match_output(
    conn: sqlite3.Connection,
    source_task_id: str,
    candidates: list[dict[str, Any]],
) -> bool:
    cursor = conn.execute(
        """
        select category, severity, confidence, file_path, line, introduced_by_diff,
               claim, failure_mode, evidence_json, suggested_fix, status
        from candidates
        where source_task_id = ?
        order by created_at, rowid
        """,
        (source_task_id,),
    )
    rows = [_row_to_dict(cursor, row) for row in cursor.fetchall()]
    if len(rows) != len(candidates):
        return False

    for row, candidate in zip(rows, candidates):
        if row["category"] != candidate["category"]:
            return False
        if row["severity"] != candidate["severity"]:
            return False
        if int(row["confidence"]) != int(candidate["confidence"]):
            return False
        if row["file_path"] != candidate["file"]:
            return False
        if int(row["line"]) != int(candidate["line"]):
            return False
        if row["introduced_by_diff"] != candidate.get("introduced_by_diff"):
            return False
        if row["claim"] != candidate["claim"]:
            return False
        if row["failure_mode"] != candidate["failure_mode"]:
            return False
        if json.loads(row["evidence_json"]) != candidate["evidence"]:
            return False
        if row["suggested_fix"] != candidate.get("suggested_fix"):
            return False
        if row["status"] != candidate.get("status", "accepted"):
            return False
    return True


def insert_verification(
    conn: sqlite3.Connection,
    run_id: str,
    candidate_id: str,
    verifier_task_id: str,
    verification: dict[str, Any],
) -> dict[str, Any]:
    row = {
        "id": _id("ver"),
        "run_id": run_id,
        "candidate_id": candidate_id,
        "verifier_task_id": verifier_task_id,
        "verdict": verification["verdict"],
        "evidence_json": json.dumps(verification["evidence"], sort_keys=True),
        "reason": verification["reason"],
        "created_at": _now(),
    }
    conn.execute(
        """
        insert into verifications (
          id, run_id, candidate_id, verifier_task_id, verdict,
          evidence_json, reason, created_at
        ) values (
          :id, :run_id, :candidate_id, :verifier_task_id, :verdict,
          :evidence_json, :reason, :created_at
        )
        """,
        row,
    )
    conn.commit()
    return row


def insert_final_finding(
    conn: sqlite3.Connection,
    run_id: str,
    candidate_id: str,
    final_finding: dict[str, Any],
) -> dict[str, Any]:
    row = {
        "id": _id("finding"),
        "run_id": run_id,
        "candidate_id": candidate_id,
        "final_severity": final_finding["final_severity"],
        "confidence": int(final_finding["confidence"]),
        "report_json": json.dumps(final_finding["report"], sort_keys=True),
        "created_at": _now(),
    }
    conn.execute(
        """
        insert into final_findings (
          id, run_id, candidate_id, final_severity, confidence,
          report_json, created_at
        ) values (
          :id, :run_id, :candidate_id, :final_severity, :confidence,
          :report_json, :created_at
        )
        """,
        row,
    )
    conn.commit()
    return row


def upsert_finding_decision(
    conn: sqlite3.Connection,
    run_id: str,
    final_finding_id: str,
    decision: str,
    note: str | None,
) -> dict[str, Any]:
    now = _now()
    existing = conn.execute(
        "select id, created_at from finding_decisions where final_finding_id = ?",
        (final_finding_id,),
    ).fetchone()
    row = {
        "id": existing[0] if existing else _id("decision"),
        "run_id": run_id,
        "final_finding_id": final_finding_id,
        "decision": decision,
        "note": note,
        "created_at": existing[1] if existing else now,
        "updated_at": now,
    }
    conn.execute(
        """
        insert into finding_decisions (
          id, run_id, final_finding_id, decision, note, created_at, updated_at
        ) values (
          :id, :run_id, :final_finding_id, :decision, :note, :created_at, :updated_at
        )
        on conflict(final_finding_id) do update set
          decision = excluded.decision,
          note = excluded.note,
          updated_at = excluded.updated_at
        """,
        row,
    )
    conn.commit()
    return row


def upsert_finding_resolution(
    conn: sqlite3.Connection,
    run_id: str,
    final_finding_id: str,
    status: str,
    summary: str,
    evidence: list[str],
) -> dict[str, Any]:
    now = _now()
    existing = conn.execute(
        "select id, created_at from finding_resolutions where final_finding_id = ?",
        (final_finding_id,),
    ).fetchone()
    row = {
        "id": existing[0] if existing else _id("resolution"),
        "run_id": run_id,
        "final_finding_id": final_finding_id,
        "status": status,
        "summary": summary,
        "evidence_json": json.dumps(evidence, sort_keys=True),
        "created_at": existing[1] if existing else now,
        "updated_at": now,
    }
    conn.execute(
        """
        insert into finding_resolutions (
          id, run_id, final_finding_id, status, summary, evidence_json, created_at, updated_at
        ) values (
          :id, :run_id, :final_finding_id, :status, :summary, :evidence_json, :created_at, :updated_at
        )
        on conflict(final_finding_id) do update set
          status = excluded.status,
          summary = excluded.summary,
          evidence_json = excluded.evidence_json,
          updated_at = excluded.updated_at
        """,
        row,
    )
    conn.commit()
    return row
