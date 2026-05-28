from __future__ import annotations

import subprocess
from pathlib import Path

from ultrareview.gitcontext.collect import collect_git_context


def run(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def test_collect_git_context_uses_git_diff_only(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init")
    run(repo, "config", "user.email", "test@example.com")
    run(repo, "config", "user.name", "Test User")
    (repo / "app.py").write_text("print('old')\n", encoding="utf-8")
    run(repo, "add", "app.py")
    run(repo, "commit", "-m", "base")
    base_sha = run(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("print('new')\n", encoding="utf-8")
    run(repo, "add", "app.py")
    run(repo, "commit", "-m", "head")
    head_sha = run(repo, "rev-parse", "HEAD")

    context = collect_git_context(repo, base_sha, head_sha)

    assert context["base_sha"] == base_sha
    assert context["head_sha"] == head_sha
    assert context["changed_files"] == [
        {"path": "app.py", "status": "M", "additions": 1, "deletions": 1}
    ]
    assert "print('new')" in context["diff"]
    assert context["merge_base"] == base_sha


def test_collect_git_context_includes_recent_commits_and_instruction_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init")
    run(repo, "config", "user.email", "test@example.com")
    run(repo, "config", "user.name", "Test User")
    (repo / "AGENTS.md").write_text("Review only concrete bugs.\n", encoding="utf-8")
    (repo / "app.py").write_text("print('old')\n", encoding="utf-8")
    run(repo, "add", "AGENTS.md", "app.py")
    run(repo, "commit", "-m", "base")
    base_sha = run(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("print('new')\n", encoding="utf-8")
    run(repo, "add", "app.py")
    run(repo, "commit", "-m", "change app behavior")

    context = collect_git_context(repo, base_sha, "HEAD")

    assert context["commit_log"][0]["subject"] == "change app behavior"
    assert context["instruction_files"] == [
        {"path": "AGENTS.md", "content": "Review only concrete bugs.\n"}
    ]


def test_collect_git_context_uses_merge_base_for_review_diff(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init")
    run(repo, "config", "user.email", "test@example.com")
    run(repo, "config", "user.name", "Test User")
    (repo / "shared.py").write_text("print('common')\n", encoding="utf-8")
    run(repo, "add", "shared.py")
    run(repo, "commit", "-m", "common ancestor")
    common_sha = run(repo, "rev-parse", "HEAD")

    run(repo, "checkout", "-b", "feature")
    (repo / "feature.py").write_text("print('feature')\n", encoding="utf-8")
    run(repo, "add", "feature.py")
    run(repo, "commit", "-m", "feature change")
    feature_sha = run(repo, "rev-parse", "HEAD")

    run(repo, "checkout", "-b", "base", common_sha)
    (repo / "base_only.py").write_text("print('base only')\n", encoding="utf-8")
    run(repo, "add", "base_only.py")
    run(repo, "commit", "-m", "base branch change")
    base_sha = run(repo, "rev-parse", "HEAD")

    context = collect_git_context(repo, "base", "feature")

    assert context["base_ref"] == "base"
    assert context["head_ref"] == "feature"
    assert context["base_sha"] == base_sha
    assert context["head_sha"] == feature_sha
    assert context["merge_base"] == common_sha
    assert context["changed_files"] == [
        {"path": "feature.py", "status": "A", "additions": 1, "deletions": 0}
    ]
    assert "feature.py" in context["diff_stat"]
    assert "base_only.py" not in context["diff_stat"]
    assert "diff --git a/feature.py b/feature.py" in context["diff"]
    assert "base_only.py" not in context["diff"]


def test_collect_git_context_skips_symlinked_instruction_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init")
    run(repo, "config", "user.email", "test@example.com")
    run(repo, "config", "user.name", "Test User")
    (repo / "app.py").write_text("print('old')\n", encoding="utf-8")
    run(repo, "add", "app.py")
    run(repo, "commit", "-m", "base")
    base_sha = run(repo, "rev-parse", "HEAD")

    external = tmp_path / "outside-instructions.md"
    external.write_text("do not leak this\n", encoding="utf-8")
    for name in ("AGENTS.md", "CLAUDE.md", "REVIEW.md"):
        (repo / name).symlink_to(external)

    (repo / "app.py").write_text("print('new')\n", encoding="utf-8")
    run(repo, "add", "app.py")
    run(repo, "commit", "-m", "head")

    context = collect_git_context(repo, base_sha, "HEAD")

    assert context["instruction_files"] == []
