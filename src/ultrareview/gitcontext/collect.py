from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _normalize_numstat_path(path: str) -> str:
    """Return the destination path for git numstat rename notation."""
    if " => " not in path:
        return path

    if "{" not in path:
        return path.rsplit(" => ", 1)[1]

    normalized = path
    while "{" in normalized and "}" in normalized:
        before, _, remainder = normalized.partition("{")
        rename, separator, after = remainder.partition("}")
        if not separator:
            break
        if " => " not in rename:
            normalized = before + rename + after
            continue
        _, destination = rename.rsplit(" => ", 1)
        normalized = before + destination + after
    return normalized


def _numstat(repo: Path, base_ref: str, head_ref: str) -> dict[str, tuple[int, int]]:
    output = _git(repo, "diff", "--numstat", f"{base_ref}..{head_ref}")
    stats: dict[str, tuple[int, int]] = {}
    for line in output.splitlines():
        additions, deletions, path = line.split("\t", 2)
        stats[_normalize_numstat_path(path)] = (
            0 if additions == "-" else int(additions),
            0 if deletions == "-" else int(deletions),
        )
    return stats


def _changed_files(repo: Path, base_ref: str, head_ref: str) -> list[dict[str, Any]]:
    stats = _numstat(repo, base_ref, head_ref)
    output = _git(repo, "diff", "--name-status", f"{base_ref}..{head_ref}")
    files: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        status = parts[0]
        path = parts[-1]
        additions, deletions = stats.get(path, (0, 0))
        files.append(
            {
                "path": path,
                "status": status,
                "additions": additions,
                "deletions": deletions,
            }
        )
    return files


def _commit_log(repo: Path, base_ref: str, head_ref: str) -> list[dict[str, str]]:
    output = _git(repo, "log", "--format=%H%x1f%an%x1f%ae%x1f%s", f"{base_ref}..{head_ref}")
    commits: list[dict[str, str]] = []
    for line in output.splitlines():
        sha, author_name, author_email, subject = line.split("\x1f", 3)
        commits.append(
            {
                "sha": sha,
                "author_name": author_name,
                "author_email": author_email,
                "subject": subject,
            }
        )
    return commits


def _instruction_files(repo: Path) -> list[dict[str, str]]:
    names = ("AGENTS.md", "CLAUDE.md", "REVIEW.md")
    files: list[dict[str, str]] = []
    for name in names:
        path = repo / name
        if path.is_symlink():
            continue
        if path.is_file():
            files.append({"path": name, "content": path.read_text(encoding="utf-8")})
    return files


def collect_git_context(repo_path: str | Path, base_ref: str, head_ref: str) -> dict[str, Any]:
    repo = Path(repo_path).expanduser().resolve()
    base_sha = _git(repo, "rev-parse", base_ref)
    head_sha = _git(repo, "rev-parse", head_ref)
    merge_base = _git(repo, "merge-base", base_ref, head_ref)
    review_base_ref = merge_base

    return {
        "repo_path": str(repo),
        "base_ref": base_ref,
        "head_ref": head_ref,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "merge_base": merge_base,
        "changed_files": _changed_files(repo, review_base_ref, head_ref),
        "commit_log": _commit_log(repo, base_ref, head_ref),
        "instruction_files": _instruction_files(repo),
        "diff_stat": _git(repo, "diff", "--stat", f"{review_base_ref}..{head_ref}"),
        "diff": _git(repo, "diff", "--find-renames", f"{review_base_ref}..{head_ref}"),
    }
