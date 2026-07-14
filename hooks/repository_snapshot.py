#!/usr/bin/env python3
"""Shared Git repository snapshot helpers."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any


RepositoryMap = dict[str, Path]
REQUIRED_IGNORED_RUNTIME_PATHS = (".cmbdevclaw/large_tool_results/",)


class RepositorySnapshotError(ValueError):
    pass


def _run_text(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def resolve_git_root(code_workspace: Path) -> Path:
    completed = _run_text(code_workspace, "rev-parse", "--show-toplevel")
    if completed.returncode != 0 or not completed.stdout.strip():
        raise RepositorySnapshotError(f"code_workspace_not_git_repository:{code_workspace}")
    return Path(completed.stdout.strip()).resolve()


def resolve_repositories(code_workspaces: Path | list[Path]) -> RepositoryMap:
    values = code_workspaces if isinstance(code_workspaces, list) else [code_workspaces]
    repositories: RepositoryMap = {}
    seen_roots: set[Path] = set()
    for workspace in values:
        root = resolve_git_root(workspace)
        if root in seen_roots:
            continue
        repository_id = root.name
        if repository_id in repositories:
            raise RepositorySnapshotError(f"duplicate_repository_id:{repository_id}")
        repositories[repository_id] = root
        seen_roots.add(root)
    if not repositories:
        raise RepositorySnapshotError("code_workspace_missing")
    return repositories


def unignored_runtime_artifact_paths(
    repo: Path,
    paths: tuple[str, ...] = REQUIRED_IGNORED_RUNTIME_PATHS,
) -> list[str]:
    unignored: list[str] = []
    for raw in paths:
        relative = raw.rstrip("/")
        probe = f"{relative}/.task-runner-ignore-probe"
        completed = _run_text(repo, "check-ignore", "--quiet", "--no-index", "--", probe)
        if completed.returncode == 1:
            unignored.append(raw)
        elif completed.returncode != 0:
            raise RepositorySnapshotError("git_ignore_check_failed")
    return unignored


def hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_file_snapshot(repo: Path) -> dict[str, str | None]:
    completed = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-co", "--exclude-standard", "-z"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RepositorySnapshotError("git_snapshot_failed")
    paths = sorted(
        {
            raw.decode("utf-8", errors="surrogateescape")
            for raw in completed.stdout.split(b"\0")
            if raw
        }
    )
    return {path: hash_file(repo / path) for path in paths}


def capture_untracked_files(repo: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-o", "--exclude-standard", "-z"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RepositorySnapshotError("git_snapshot_failed")
    return sorted(
        {
            raw.decode("utf-8", errors="surrogateescape")
            for raw in completed.stdout.split(b"\0")
            if raw
        }
    )


def capture_repository_snapshot(repo: Path) -> dict[str, Any]:
    head = _run_text(repo, "rev-parse", "HEAD")
    index = _run_text(repo, "write-tree")
    if head.returncode != 0 or index.returncode != 0:
        raise RepositorySnapshotError("git_snapshot_failed")
    return {
        "headCommit": head.stdout.strip(),
        "indexTree": index.stdout.strip(),
        "files": capture_file_snapshot(repo),
    }


def file_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".md", ".rst", ".txt"}:
        return "docs"
    if suffix in {".json", ".yaml", ".yml", ".toml", ".ini", ".properties"}:
        return "config"
    if "test" in Path(path).parts or Path(path).name.lower().startswith("test"):
        return "test"
    return "source"


def snapshot_changes(
    before: dict[str, str | None],
    after: dict[str, str | None],
) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    deleted = {
        path: digest
        for path, digest in before.items()
        if (path not in after or after.get(path) is None) and isinstance(digest, str)
    }
    created = {
        path: digest
        for path, digest in after.items()
        if path not in before and isinstance(digest, str)
    }
    renamed_from: set[str] = set()
    renamed_to: set[str] = set()
    for old_path, old_digest in sorted(deleted.items()):
        new_path = next(
            (
                candidate
                for candidate, digest in sorted(created.items())
                if candidate not in renamed_to and digest == old_digest
            ),
            None,
        )
        if new_path is None:
            continue
        renamed_from.add(old_path)
        renamed_to.add(new_path)
        changes.append(
            {
                "path": new_path,
                "fromPath": old_path,
                "operation": "renamed",
                "kind": file_kind(new_path),
                "summary": f"Task execution renamed {old_path} to {new_path}",
                "reason": "Detected from matching task run file hashes",
            }
        )
    for path in sorted(set(before) | set(after)):
        if path in renamed_from or path in renamed_to:
            continue
        old = before.get(path)
        new = after.get(path)
        if old == new:
            continue
        if path not in before:
            operation = "created"
        elif path not in after or new is None:
            operation = "deleted"
        else:
            operation = "modified"
        changes.append(
            {
                "path": path,
                "operation": operation,
                "kind": file_kind(path),
                "summary": f"Task execution {operation} {path}",
                "reason": "Detected from the task run Git snapshot",
            }
        )
    return changes
