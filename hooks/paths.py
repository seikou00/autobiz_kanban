#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Path helpers for autobizdevops workspace bootstrap hooks."""

from pathlib import Path
from typing import Optional, Union


PathLike = Union[str, Path]


def get_workspace(workspace: Optional[PathLike] = None) -> Path:
    if workspace is None:
        return Path.cwd().resolve()
    return Path(workspace).resolve()


def get_autobizdevops_dir(workspace: Optional[PathLike] = None) -> Path:
    return get_workspace(workspace) / ".autobizdevops"


def get_features_active_dir(workspace: Optional[PathLike] = None) -> Path:
    return get_autobizdevops_dir(workspace) / "features"


def get_feature_active_dir(workspace: Optional[PathLike], feature: str) -> Path:
    return get_features_active_dir(workspace) / feature


def get_feature_hook_log_path(workspace: Optional[PathLike], feature: str) -> Path:
    return get_feature_active_dir(workspace, feature) / "hooks.ndjson"


def get_features_archive_dir(workspace: Optional[PathLike] = None) -> Path:
    return get_autobizdevops_dir(workspace) / "archive"


def get_issues_active_dir(workspace: Optional[PathLike] = None) -> Path:
    return get_autobizdevops_dir(workspace) / "issues" / "active"


def get_issues_completed_dir(workspace: Optional[PathLike] = None) -> Path:
    return get_autobizdevops_dir(workspace) / "issues" / "completed"


def get_reviews_dir(workspace: Optional[PathLike] = None) -> Path:
    return get_autobizdevops_dir(workspace) / "review"


def get_logs_dir(workspace: Optional[PathLike] = None) -> Path:
    return get_autobizdevops_dir(workspace) / "logs"


def get_state_md_path(workspace: Optional[PathLike] = None) -> Path:
    return get_autobizdevops_dir(workspace) / "STATE.md"


def get_project_md_path(workspace: Optional[PathLike] = None) -> Path:
    return get_autobizdevops_dir(workspace) / "PROJECT.md"


def ensure_dir(path: PathLike) -> Path:
    resolved = Path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def is_initialized(workspace: Optional[PathLike] = None) -> bool:
    ws = get_workspace(workspace)
    return (
        get_autobizdevops_dir(ws).exists()
        and get_project_md_path(ws).exists()
        and get_state_md_path(ws).exists()
    )
