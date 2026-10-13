"""Git identity and file scope shared by merge resolution and its write guard."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from hooks.repository_snapshot import PLATFORM_RUNTIME_DIRECTORY


PLATFORM_EXCLUDE = f":(exclude){PLATFORM_RUNTIME_DIRECTORY}**"


def git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Keep normal Git hooks enabled; only disable interactive prompts/editors.
    env = dict(os.environ, GIT_EDITOR="true", GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(
            ["git", *args], cwd=path, env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"candidate_git_timeout:{args[0]}") from exc


def git_output(path: Path, *args: str) -> str:
    result = git(path, *args)
    if result.returncode != 0:
        raise ValueError(f"candidate_git_failed:{args[0]}:{result.stderr.strip()}")
    return result.stdout.strip()


def candidate_binding(manifest: dict[str, Any], record: dict[str, Any]) -> Path:
    raw_path = record.get("worktreePath")
    binding = manifest.get("repositories", {}).get(record.get("repositoryRef"))
    if not isinstance(raw_path, str) or not raw_path or not isinstance(binding, dict):
        raise ValueError("candidate_repository_binding_missing")
    path = Path(raw_path).resolve()
    repo = Path(str(binding.get("gitRoot") or "")).resolve()
    if not path.is_dir() or not binding.get("gitRoot"):
        raise ValueError("candidate_worktree_missing")
    if Path(git_output(path, "rev-parse", "--show-toplevel")).resolve() != path:
        raise ValueError("candidate_worktree_root_mismatch")
    if git_output(path, "symbolic-ref", "--quiet", "--short", "HEAD") != record.get("branchName"):
        raise ValueError("candidate_branch_mismatch")
    common_dirs = []
    for root in (repo, path):
        common = Path(git_output(root, "rev-parse", "--git-common-dir"))
        common_dirs.append((root / common).resolve() if not common.is_absolute() else common.resolve())
    if common_dirs[0] != common_dirs[1] or path == repo:
        raise ValueError("candidate_repository_mismatch")
    return path


def merge_parents(path: Path) -> list[str]:
    head = git_output(path, "rev-parse", "HEAD")
    merge_head = git(path, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    if merge_head.returncode != 0:
        return [head]
    return [head, merge_head.stdout.strip()]


def resolution_files(path: Path, raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("candidate_resolution_files_missing")
    files = []
    for item in raw:
        if not isinstance(item, str) or not item or "\\" in item or "\x00" in item:
            raise ValueError("candidate_resolution_path_invalid")
        relative = PurePosixPath(item)
        if relative.is_absolute() or any(part in {"..", ".git", ".autobizdevops", ".cmbdevclaw"} for part in relative.parts):
            raise ValueError(f"candidate_resolution_path_invalid:{item}")
        if str(relative) != item or relative == PurePosixPath("."):
            raise ValueError(f"candidate_resolution_path_invalid:{item}")
        target = path / item
        if target.is_symlink() or not target.resolve().is_relative_to(path):
            raise ValueError(f"candidate_resolution_symlink:{item}")
        files.append(item)
    return sorted(set(files))


def outside_diff_digest(path: Path, files: list[str]) -> str:
    exclusions = [PLATFORM_EXCLUDE, *(f":(exclude,literal){name}" for name in files)]
    digest = hashlib.sha256()
    # Check the index as well as the working copy: an unrelated staged edit
    # must not be hidden by restoring only its working-tree content.
    for options in ([], ["--cached"]):
        result = git(path, "diff", *options, "--binary", "HEAD", "--", ".", *exclusions)
        if result.returncode != 0:
            raise ValueError("candidate_resolution_scope_check_failed")
        digest.update(result.stdout.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def business_status(path: Path) -> str:
    return git_output(path, "status", "--porcelain", "--untracked-files=all", "--", ".", PLATFORM_EXCLUDE)


def resolution_write_error(manifest: dict[str, Any], record: dict[str, Any], target: Path) -> str | None:
    """Authorize only the recorded conflict files while the model owns editing."""
    try:
        path = candidate_binding(manifest, record)
        resolution = record.get("resolution") or {}
        if not isinstance(resolution, dict):
            return "candidate_resolution_invalid"
        if record.get("status") not in {"candidate_conflicted", "needs_resolution"} or resolution.get("status") != "running":
            return "candidate_resolution_not_running"
        files = resolution_files(path, resolution.get("files"))
        if target.resolve().relative_to(path).as_posix() not in files:
            return "candidate_resolution_file_not_authorized"
        if merge_parents(path) != resolution.get("parents"):
            return "candidate_resolution_git_state_changed"
    except (ValueError, OSError) as exc:
        return str(exc)
    return None


def has_conflict_markers(path: Path) -> bool:
    if not path.exists():  # A deletion can be a valid conflict resolution.
        return False
    return bool(re.search(rb"(?m)^(?:<{7}(?: |\r?$)|={7}\r?$|>{7}(?: |\r?$)|\|{7}(?: |\r?$))", path.read_bytes()))
