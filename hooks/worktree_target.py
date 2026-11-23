"""Native Git worktree target argument selection within Windows GIT_DIR bounds."""
from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PREFERRED_SHORT_ARG_BYTES = 215
HARD_GIT_DIR_BYTES = 220
WORKTREE_GIT_DIR_TOO_LONG = "worktree_git_dir_too_long"


@dataclass(frozen=True)
class WorktreeTargetPlan:
    """Selected target argument and diagnostics for a Git worktree command."""

    canonical_path: Path
    git_arg: str
    strategy: str
    git_dir_bytes: int


def git_dir_utf8_bytes(target_arg: str) -> int:
    """Compute UTF-8 byte length of the GIT_DIR string Git 2.27 constructs."""
    base = target_arg.rstrip("/\\") or target_arg
    return len(f"{base}/.git".encode("utf-8"))


def is_git_dir_too_big_output(output: str) -> bool:
    """Detect Git setup_explicit_git_dir() PATH_MAX overflow diagnostics."""
    lowered = (output or "").lower()
    return "'$git_dir' too big" in lowered or ("git_dir" in lowered and "too big" in lowered)


def _windows_short_path(path: Path) -> str | None:
    """Return the Windows 8.3 short path for an existing directory, if available."""
    if sys.platform != "win32":
        return None
    try:
        get_short = ctypes.windll.kernel32.GetShortPathNameW
        raw = str(path)
        required = int(get_short(raw, None, 0))
        if required <= 0:
            return None
        buffer = ctypes.create_unicode_buffer(required)
        written = int(get_short(raw, buffer, required))
        if 0 < written < required and buffer.value:
            return buffer.value
    except (AttributeError, OSError):
        return None
    return None


def _candidate_8_3_path(
    target: Path,
    *,
    short_path_fn: Callable[[Path], str | None] | None = None,
) -> str | None:
    """Resolve an 8.3-shortened path and verify physical identity via samefile."""
    resolver = short_path_fn if short_path_fn is not None else _windows_short_path
    if target.exists():
        short = resolver(target)
        if not short:
            return None
        try:
            if os.path.samefile(short, target):
                return short
        except OSError:
            return None
        return None
    if target.parent.exists():
        short_parent = resolver(target.parent)
        if not short_parent:
            return None
        try:
            if os.path.samefile(short_parent, target.parent):
                return os.path.join(short_parent, target.name)
        except OSError:
            return None
        return None
    return None


def resolve_worktree_target_arg(
    git_cwd: Path,
    target_path: Path,
    *,
    enforce_limit: bool | None = None,
    preferred_bytes: int = PREFERRED_SHORT_ARG_BYTES,
    hard_limit_bytes: int = HARD_GIT_DIR_BYTES,
    allow_fail_fast: bool = True,
    short_path_fn: Callable[[Path], str | None] | None = None,
) -> WorktreeTargetPlan:
    """Choose the shortest verified native target argument for git worktree commands.

    Prefer an unchanged absolute path up to `preferred_bytes`. Above that, try
    a same-drive relative target path from `git_cwd` and then a physical 8.3
    shortened parent path, keeping any valid argument up to `hard_limit_bytes`.
    """
    enforce = (sys.platform == "win32") if enforce_limit is None else enforce_limit
    cwd_resolved = git_cwd.resolve()
    target_resolved = target_path.resolve()
    abs_str = str(target_resolved)
    best = WorktreeTargetPlan(
        canonical_path=target_resolved,
        git_arg=abs_str,
        strategy="absolute",
        git_dir_bytes=git_dir_utf8_bytes(abs_str),
    )
    if not enforce or best.git_dir_bytes <= preferred_bytes:
        return best

    try:
        rel = os.path.relpath(abs_str, str(cwd_resolved))
    except ValueError:
        rel = ""
    if rel and not os.path.isabs(rel):
        rel_bytes = git_dir_utf8_bytes(rel)
        if rel_bytes < best.git_dir_bytes:
            best = WorktreeTargetPlan(
                canonical_path=target_resolved,
                git_arg=rel,
                strategy="relative",
                git_dir_bytes=rel_bytes,
            )

    if best.git_dir_bytes > preferred_bytes:
        short_str = _candidate_8_3_path(target_resolved, short_path_fn=short_path_fn)
        if short_str:
            short_bytes = git_dir_utf8_bytes(short_str)
            if short_bytes < best.git_dir_bytes:
                best = WorktreeTargetPlan(
                    canonical_path=target_resolved,
                    git_arg=short_str,
                    strategy="short_8_3",
                    git_dir_bytes=short_bytes,
                )

    if allow_fail_fast and best.git_dir_bytes > hard_limit_bytes:
        raise ValueError(
            f"{WORKTREE_GIT_DIR_TOO_LONG}:bytes={best.git_dir_bytes}:limit={hard_limit_bytes}"
            f":strategy={best.strategy}:target={target_resolved}"
        )
    return best
