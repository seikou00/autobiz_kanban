#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Path helpers for autobizdevops workspace bootstrap hooks."""

import re
from pathlib import Path
from typing import Optional, Union


PathLike = Union[str, Path]
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_NO_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")


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


def get_state_json_path(workspace: Optional[PathLike] = None) -> Path:
    return get_autobizdevops_dir(workspace) / "state.json"


def get_project_md_path(workspace: Optional[PathLike] = None) -> Path:
    return get_autobizdevops_dir(workspace) / "PROJECT.md"


def get_sys_agents_md_path(system_no: str, plugin_root: Optional[PathLike] = None) -> Path:
    root = Path(plugin_root).resolve() if plugin_root is not None else PLUGIN_ROOT
    sys_root = root / "sys"
    if sys_root.is_dir():
        candidates = sorted(sys_root.iterdir(), key=lambda item: (item.name.casefold(), item.name))
        for candidate in candidates:
            if candidate.is_dir() and candidate.name == system_no:
                return candidate / "AGENTS.md"

        folded_system_no = system_no.casefold()
        for candidate in candidates:
            if candidate.is_dir() and candidate.name.casefold() == folded_system_no:
                return candidate / "AGENTS.md"

        normalized_system_no = normalize_system_no(system_no)
        if normalized_system_no:
            for candidate in candidates:
                if candidate.is_dir() and normalize_system_no(candidate.name) == normalized_system_no:
                    return candidate / "AGENTS.md"

            for candidate in candidates:
                candidate_system_no = normalize_system_no(candidate.name)
                if candidate.is_dir() and candidate_system_no and (
                    candidate_system_no.startswith(normalized_system_no)
                    or normalized_system_no.startswith(candidate_system_no)
                ):
                    return candidate / "AGENTS.md"

    return sys_root / system_no / "AGENTS.md"


def normalize_system_no(system_no: str) -> str:
    return "".join(SYSTEM_NO_TOKEN_PATTERN.findall(system_no)).casefold()


def ensure_dir(path: PathLike) -> Path:
    resolved = Path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def is_initialized(workspace: Optional[PathLike] = None) -> bool:
    ws = get_workspace(workspace)
    return (
        get_autobizdevops_dir(ws).exists()
        and get_project_md_path(ws).exists()
        and get_state_json_path(ws).exists()
    )
