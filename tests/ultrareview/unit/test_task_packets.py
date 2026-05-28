from __future__ import annotations

import json

from ultrareview.runtime import db
from ultrareview.runtime.packets import SCOUT_ROLES, build_scout_tasks


def test_build_scout_tasks_creates_packets_and_db_tasks(tmp_path):
    conn = db.connect(tmp_path / "review.sqlite")
    db.init_schema(conn)
    run = db.create_run(conn, "/repo", "origin/main", "HEAD", "copilot-git-only")
    run_dir = tmp_path / "run"
    git_context_path = run_dir / "artifacts" / "git-context.json"
    git_context_path.parent.mkdir(parents=True)
    git_context_path.write_text('{"changed_files": []}', encoding="utf-8")

    tasks = build_scout_tasks(conn, run["id"], run_dir, git_context_path)

    assert [task["role"] for task in tasks] == [role.name for role in SCOUT_ROLES]
    for task in tasks:
        packet_path = run_dir / task["input_path"]
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        assert packet["run_id"] == run["id"]
        assert packet["role"] == task["role"]
        assert packet["inputs"]["git_context_path"] == str(git_context_path)
        assert packet["output_contract"]["format"] == "candidate_findings_json"

    rows = conn.execute("select role, status from agent_tasks order by rowid").fetchall()
    assert rows == [(role.name, "pending") for role in SCOUT_ROLES]

