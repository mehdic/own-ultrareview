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


def test_scout_packets_require_configuration_inventory_continuity(tmp_path):
    conn = db.connect(tmp_path / "review.sqlite")
    db.init_schema(conn)
    run = db.create_run(conn, "/repo", "origin/main", "HEAD", "copilot-git-only")
    run_dir = tmp_path / "run"
    git_context_path = run_dir / "artifacts" / "git-context.json"
    git_context_path.parent.mkdir(parents=True)
    git_context_path.write_text('{"changed_files": []}', encoding="utf-8")

    tasks = build_scout_tasks(conn, run["id"], run_dir, git_context_path)
    packets = {
        task["role"]: json.loads((run_dir / task["input_path"]).read_text(encoding="utf-8"))
        for task in tasks
    }

    for packet in packets.values():
        rules = "\n".join(packet["inputs"]["rules"]).lower()
        assert "configuration inventory continuity" in rules
        assert "application*.yml" in rules
        assert "helm values" in rules
        assert "environment-specific" in rules

    security_packet = packets["security_reviewer"]
    security_text = "\n".join(
        [security_packet["objective"], *security_packet["focus"], *security_packet["inputs"]["rules"]]
    ).lower()
    assert "spring boot" in security_text
    assert "spring security" in security_text
    assert "configured users" in security_text
    assert "roles" in security_text
    assert "per-environment" in security_text

    regression_packet = packets["regression_reviewer"]
    regression_text = "\n".join(
        [regression_packet["objective"], *regression_packet["focus"], *regression_packet["inputs"]["rules"]]
    ).lower()
    assert "dependency/framework migration" in regression_text
    assert "removed config namespaces" in regression_text
    assert "deleted accounts" in regression_text
    assert "changed defaults" in regression_text
