"""STATE.md 读取与解析 + feature 目录查找 — 复用 hooks/state_checkpoint + hooks/paths。"""

from __future__ import annotations

from pathlib import Path

from hooks.paths import (
    get_features_active_dir,
    get_features_archive_dir,
    get_state_md_path,
)
from hooks.state_checkpoint import parse_state_record_table, parse_state_rows, parse_state_table


StateResult = tuple[dict[str, str], list[str], bool]  # rows, errors, file_exists
StateRecordsResult = tuple[dict[str, dict[str, str]], list[str], bool]


def load_state_md(workspace: Path) -> StateResult:
    """读取 STATE.md 并返回 (feature→checkpoint 映射, 错误列表, 文件是否存在)。

    内部使用 hooks/state_checkpoint.parse_state_table()，不重复实现解析逻辑。
    """
    state_path = get_state_md_path(workspace)
    if not state_path.is_file():
        return {}, [], False

    content = state_path.read_text(encoding="utf-8")
    rows, errors = parse_state_table(content)
    return rows, errors, True


def load_state_rows(workspace: Path) -> dict[str, str]:
    """快速读取 STATE.md 的 feature→checkpoint 映射，忽略错误。

    与 load_state_md 的区别：不返回错误列表，适用于不需要精确错误的场景。
    内部使用 hooks/state_checkpoint.parse_state_rows()。
    """
    state_path = get_state_md_path(workspace)
    if not state_path.is_file():
        return {}
    content = state_path.read_text(encoding="utf-8")
    return parse_state_rows(content)


def load_state_records(workspace: Path) -> StateRecordsResult:
    """读取 STATE.md 并返回完整 feature 行记录。

    与 load_state_md 一样通过 hooks/state_checkpoint 解析 Markdown 表格；
    额外保留迭代等 project 看板需要的列。
    """
    state_path = get_state_md_path(workspace)
    if not state_path.is_file():
        return {}, [], False

    content = state_path.read_text(encoding="utf-8")
    rows, errors = parse_state_record_table(content)
    return rows, errors, True


def list_active_feature_names(workspace: Path) -> list[str]:
    """Return active feature directory names under .autobizdevops/features."""
    active = get_features_active_dir(workspace)
    if not active.is_dir():
        return []
    return sorted(entry.name for entry in active.iterdir() if entry.is_dir())


def find_feature_dir(workspace: Path, feature: str) -> Path | None:
    """Return the feature directory path (active first, fall back to archive)."""
    active = get_features_active_dir(workspace) / feature
    if active.is_dir():
        return active
    archive_base = get_features_archive_dir(workspace)
    if archive_base.is_dir():
        exact_archive = archive_base / feature
        if exact_archive.is_dir():
            return exact_archive
        for entry in sorted(archive_base.iterdir()):
            if entry.is_dir() and entry.name.startswith(f"{feature}-iter"):
                return entry
    return None
