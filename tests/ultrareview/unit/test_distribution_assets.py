from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path


def test_skill_distribution_assets_exist_and_are_git_only():
    root = Path("skills/ultrareview")
    required = [
        root / "schemas" / "candidate.schema.json",
        root / "schemas" / "verification.schema.json",
        root / "references" / "copilot-vscode.md",
        root / "references" / "claude-code.md",
        root / "references" / "claude-code-command.md",
        root / "references" / "openclaw.md",
        root / "references" / "release-gate.md",
        root / "prompts" / "scout-system.md",
        root / "prompts" / "verifier-system.md",
    ]

    for path in required:
        assert path.exists(), path

    candidate_schema = json.loads((root / "schemas" / "candidate.schema.json").read_text())
    verification_schema = json.loads((root / "schemas" / "verification.schema.json").read_text())
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    copilot_doc = (root / "references" / "copilot-vscode.md").read_text(encoding="utf-8")

    assert "severity" in candidate_schema["required"]
    assert verification_schema["properties"]["verdict"]["enum"] == ["verified", "rejected", "uncertain"]
    assert skill.startswith("---\nname: ultrareview")
    assert "Do not use `gh`" in copilot_doc


def test_verifier_prompt_contains_complete_output_contract():
    prompt = Path("skills/ultrareview/prompts/verifier-system.md").read_text(encoding="utf-8")
    vscode_agent = Path("skills/ultrareview/references/ultrareview.agent.md").read_text(encoding="utf-8")
    claude_agent = Path("skills/ultrareview/references/claude-code-agent.md").read_text(encoding="utf-8")

    for text in (prompt, vscode_agent, claude_agent):
        assert '"candidate_id"' in text
        assert '"verdict"' in text
        assert '"reason"' in text
        assert '"evidence"' in text
        assert '"path"' in text
        assert '"line"' in text
        assert '"quote"' in text
        assert "Do not write a partial verifier output" in text or "Every verification object must include" in text


def test_agent_prompts_forbid_direct_sqlite_writes():
    vscode_agent = Path("skills/ultrareview/references/ultrareview.agent.md").read_text(encoding="utf-8")
    claude_agent = Path("skills/ultrareview/references/claude-code-agent.md").read_text(encoding="utf-8")
    copilot_install = Path("skills/ultrareview/references/copilot-vscode.md").read_text(encoding="utf-8")
    claude_install = Path("skills/ultrareview/references/claude-code.md").read_text(encoding="utf-8")

    for text in (vscode_agent, claude_agent, copilot_install, claude_install):
        assert "do not edit" in text.lower() or "never update" in text.lower()
        assert "review.sqlite" in text
        assert "record-verification" in text
        assert "inserted_verifications: 1" in text


def test_claude_code_slash_command_uses_my_ultrareview_name():
    claude_doc = Path("skills/ultrareview/references/claude-code.md").read_text(encoding="utf-8")
    claude_agent = Path("skills/ultrareview/references/claude-code-agent.md").read_text(encoding="utf-8")
    claude_command = Path("skills/ultrareview/references/claude-code-command.md").read_text(encoding="utf-8")
    build_script = Path("scripts/build_platform_packages.sh").read_text(encoding="utf-8")

    assert ".claude/commands/my-ultrareview.md" in claude_doc
    assert "/my-ultrareview" in claude_doc
    assert "my-ultrareview.md" in claude_agent
    assert "/my-ultrareview" in claude_agent
    assert "# My UltraReview" in claude_command
    assert "payload/claude/commands/my-ultrareview.md" in build_script
    assert ".claude/commands/own-ultrareview.md" not in claude_doc
    assert "/own-ultrareview origin/main" not in claude_doc


def test_platform_builder_excludes_autoresearch_local_logs():
    build_script = Path("scripts/build_platform_packages.sh").read_text(encoding="utf-8")

    assert "--exclude results.tsv" in build_script
    assert "--exclude run.log" in build_script


def test_platform_builder_zip_payload_excludes_autoresearch_local_logs(tmp_path):
    local_files = {
        Path("results.tsv"): "experiment\tcommit\tmetric\tstatus\tdescription\n",
        Path("run.log"): "local test output\n",
    }
    backups = {path: path.read_text(encoding="utf-8") if path.exists() else None for path in local_files}
    try:
        for path, content in local_files.items():
            path.write_text(content, encoding="utf-8")

        subprocess.run(
            ["scripts/build_platform_packages.sh", "vtest", str(tmp_path)],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=True,
        )

        for zip_path in tmp_path.glob("vtest/*.zip"):
            with zipfile.ZipFile(zip_path) as archive:
                names = archive.namelist()
            assert not any(name.endswith("/results.tsv") or name.endswith("/run.log") for name in names)
    finally:
        for path, original in backups.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(original, encoding="utf-8")


def test_scout_candidate_schema_is_consistent_across_prompts_and_packets():
    packet_source = Path("src/ultrareview/runtime/packets.py").read_text(encoding="utf-8")
    scout_prompt = Path("skills/ultrareview/prompts/scout-system.md").read_text(encoding="utf-8")
    vscode_agent = Path("skills/ultrareview/references/ultrareview.agent.md").read_text(encoding="utf-8")
    claude_agent = Path("skills/ultrareview/references/claude-code-agent.md").read_text(encoding="utf-8")

    for text in (packet_source, scout_prompt, vscode_agent, claude_agent):
        assert '"candidates"' in text or "`candidates`" in text
        assert '"confidence": 91' in text
        assert '"evidence": [' in text
        assert '"path": "app.py"' in text
        assert '"line": 12' in text
        assert '"quote": "user.company_id == user.company_id"' in text
        normalized = text.lower().replace("`", "")
        assert "confidence must be an integer" in normalized
        assert "do not use `findings`" in text.lower() or "do not use `findings`, `task_id`, `agent_role`" in text.lower()

    for text in (scout_prompt, vscode_agent, claude_agent):
        assert ("`findings`" + ": array") not in text
        assert "- `task_id`" not in text
        assert "- `agent_role`" not in text
        assert "why_diff_introduced_or_exposed_it" not in text


def test_decision_presentation_requires_html_report_path_first():
    skill = Path("skills/ultrareview/SKILL.md").read_text(encoding="utf-8")
    vscode_agent = Path("skills/ultrareview/references/ultrareview.agent.md").read_text(encoding="utf-8")
    claude_agent = Path("skills/ultrareview/references/claude-code-agent.md").read_text(encoding="utf-8")

    for text in (skill, vscode_agent, claude_agent):
        assert "decision_gate_complete: true" in text
        assert "html_path" in text
        assert "Do not show any issues breakdown" in text or "Never show an issues breakdown" in text
        assert "first human-visible line" in text
        assert "HTML report: <html_path" in text


def test_implementation_plan_gate_treats_fix_requests_as_scope_selection_only():
    skill = Path("skills/ultrareview/SKILL.md").read_text(encoding="utf-8")
    vscode_agent = Path("skills/ultrareview/references/ultrareview.agent.md").read_text(encoding="utf-8")
    claude_agent = Path("skills/ultrareview/references/claude-code-agent.md").read_text(encoding="utf-8")

    required_phrases = [
        "Any user request to fix one issue, multiple issues, a fix group, or all issues means selected scope for an implementation plan only; it is not approval to edit.",
        "Do not edit source files, use edit/write tools, or run apply_patch before both implementation plan files exist and the user has explicitly approved the plan after seeing both paths.",
        "Implementation plan MD: <run_dir>/plans/ultrareview-implementation-plan.md",
        "Implementation plan HTML: <run_dir>/plans/ultrareview-implementation-plan.html",
    ]

    for text in (skill, vscode_agent, claude_agent):
        for phrase in required_phrases:
            assert phrase in text
