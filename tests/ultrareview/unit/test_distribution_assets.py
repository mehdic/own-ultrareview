from __future__ import annotations

import json
from pathlib import Path


def test_skill_distribution_assets_exist_and_are_git_only():
    root = Path("skills/ultrareview")
    required = [
        root / "schemas" / "candidate.schema.json",
        root / "schemas" / "verification.schema.json",
        root / "references" / "copilot-vscode.md",
        root / "references" / "claude-code.md",
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
