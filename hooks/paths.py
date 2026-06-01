#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Path helpers for autobizdevops workspace bootstrap hooks."""

import os
import re
from pathlib import Path
from typing import Iterable, Mapping, Optional, Union


PathLike = Union[str, Path]
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_NO_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")


STATE_SCRIPTS_WORKSPACE_ARGUMENT_ERROR = (
    "状态读写脚本不接受 --workspace/-w；请删除该参数，路径由 PLUGIN_OUTPUT_DIR 环境变量决定。"
)


def contains_workspace_argument(args: Iterable[str]) -> bool:
    for arg in args:
        if arg in {"--workspace", "-w"}:
            return True
        if arg.startswith("--workspace="):
            return True
        if arg.startswith("-w") and arg != "-w":
            return True
    return False


def get_plugin_output_workspace(env: Optional[Mapping[str, str]] = None) -> Path:
    values = os.environ if env is None else env
    raw = values.get("PLUGIN_OUTPUT_DIR", "")
    if not raw.strip():
        raise ValueError("PLUGIN_OUTPUT_DIR 未设置；状态读写脚本必须由插件环境提供项目插件根目录")

    workspace = Path(raw).expanduser().resolve(strict=False)
    if not workspace.is_dir():
        raise ValueError(f"PLUGIN_OUTPUT_DIR 指向的目录不存在: {workspace}")
    return workspace


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
