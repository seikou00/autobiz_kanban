"""Filesystem helpers for workflow artifact paths."""

from __future__ import annotations

from pathlib import Path


GLOB_CHARS = frozenset("*?[")


def has_glob(path: str) -> bool:
    return any(char in path for char in GLOB_CHARS)


def resolve_exact_relative_path(root: Path, relative_path: str | Path) -> Path | None:
    """Resolve a child path only when every path component matches case exactly."""
    rel = Path(relative_path)
    if rel.is_absolute():
        return None

    current = root
    for part in rel.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            return None
        try:
            entries = {entry.name: entry for entry in current.iterdir()}
        except OSError:
            return None
        next_path = entries.get(part)
        if next_path is None:
            return None
        current = next_path
    return current


def is_nonempty_file_exact(root: Path, relative_path: str | Path) -> bool:
    target = resolve_exact_relative_path(root, relative_path)
    return target is not None and target.is_file() and target.stat().st_size > 0


def artifact_exists_exact(feature_dir: Path, artifact_path: str) -> bool:
    if has_glob(artifact_path):
        for match in feature_dir.glob(artifact_path):
            try:
                relative_match = match.relative_to(feature_dir)
            except ValueError:
                continue
            exact_match = resolve_exact_relative_path(feature_dir, relative_match)
            if exact_match is not None and exact_match.is_file() and exact_match.stat().st_size > 0:
                return True
        return False

    target = resolve_exact_relative_path(feature_dir, artifact_path)
    if target is None:
        return False
    if target.is_dir():
        return any(child.is_file() and child.stat().st_size > 0 for child in target.rglob("*"))
    return target.is_file() and target.stat().st_size > 0
