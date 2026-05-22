#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
autobizdevops Workspace 初始化脚本
用法:
    python hooks/init_workspace.py --mode createProject --workspace <workspace> --project <project>
    python hooks/init_workspace.py --mode createFeature --workspace <workspace> --project <project> --feature <feature>
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    from paths import (
        ensure_dir,
        get_autobizdevops_dir,
        get_features_active_dir,
        get_features_archive_dir,
        get_feature_active_dir,
        get_issues_active_dir,
        get_issues_completed_dir,
        get_project_md_path,
        get_state_json_path,
        get_state_md_path,
        get_workspace,
        is_initialized,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from paths import (  # type: ignore[no-redef]
        ensure_dir,
        get_autobizdevops_dir,
        get_features_active_dir,
        get_features_archive_dir,
        get_feature_active_dir,
        get_issues_active_dir,
        get_issues_completed_dir,
        get_project_md_path,
        get_state_json_path,
        get_state_md_path,
        get_workspace,
        is_initialized,
    )


def _generate_project_md(workspace_name: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""# Project Metadata

- **Project Name**: {workspace_name}
- **Initialized At**: {now}
- **Skill Version**: autobizdevops v1.1.0
- **Schema Version**: 1

## Workflow Defaults

- **Auto Mode**: false (默认手动推进)

## Notes

- 本文件由 autobizdevops 初始化时自动生成
- 其他流程控制以各阶段 `SKILL.md`、`.autobizdevops/STATE.md` 与 `.autobizdevops/state.json` 为准
"""


def _generate_state_md() -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""# 工程状态

- **里程碑**: [待确定]
- **最后更新**: {now}

## Feature 进度

| Feature | 负责人 | checkpoint | 阶段 | 迭代 | 最后更新 |
|---------|--------|-----------|------|------|---------|
"""


def _generate_state_json() -> str:
    return "{}\n"


def _generate_state_json_from_state_md(state_md: Path) -> str:
    if not state_md.is_file():
        return _generate_state_json()

    rows: dict[str, str] = {}
    for raw_line in state_md.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if cells[0] == "Feature" or all(cell and set(cell) <= {"-", ":"} for cell in cells):
            continue
        if cells[0] and cells[2]:
            rows[cells[0]] = cells[2]
    return json.dumps(dict(sorted(rows.items())), ensure_ascii=False, indent=2) + "\n"


def _write_if_missing(path: Path, content: str, created: List[str]) -> None:
    if path.exists():
        return
    path.write_text(content, encoding="utf-8")
    created.append(str(path))


def _is_skill_root(path: Path) -> bool:
    """判断当前目录是否为 skill 自身根目录（含 hooks/init_workspace.py 与 SKILL.md）"""
    return (
        (path / "hooks" / "init_workspace.py").exists()
        and (path / "SKILL.md").exists()
    )


def init_workspace(workspace: Path, force: bool = False) -> Dict[str, object]:
    result = {
        "initialized": False,
        "created": [],
        "backup": None,
        "message": "",
    }

    workspace = workspace.resolve()
    # 如果传入的是 skill 自身根目录，自动退到父目录作为 workspace
    if _is_skill_root(workspace):
        workspace = workspace.parent
    abdo_dir = get_autobizdevops_dir(workspace)
    was_initialized = is_initialized(workspace)

    if abdo_dir.exists() and force:
        backup_name = f"{abdo_dir.name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        backup_path = abdo_dir.parent / backup_name
        abdo_dir.rename(backup_path)
        result["backup"] = str(backup_path)

    dirs = [
        get_features_active_dir(workspace),
        get_features_archive_dir(workspace),
        get_issues_active_dir(workspace),
        get_issues_completed_dir(workspace)
    ]
    for directory in dirs:
        if not directory.exists():
            ensure_dir(directory)
            result["created"].append(str(directory))
        else:
            ensure_dir(directory)

    project_md = get_project_md_path(workspace)
    _write_if_missing(project_md, _generate_project_md(workspace.name or "untitled"), result["created"])

    state_md = get_state_md_path(workspace)
    _write_if_missing(state_md, _generate_state_md(), result["created"])

    state_json = get_state_json_path(workspace)
    _write_if_missing(state_json, _generate_state_json_from_state_md(state_md), result["created"])

    result["initialized"] = is_initialized(workspace)
    if result["backup"]:
        result["message"] = f"Workspace re-initialized successfully at {abdo_dir}"
    elif result["created"]:
        result["message"] = f"Workspace initialized successfully at {abdo_dir}"
    elif was_initialized:
        result["message"] = f"Workspace already initialized: {abdo_dir}"
    else:
        result["message"] = f"Workspace bootstrap checked: {abdo_dir}"
    return result


def _resolve_target_workspace(workspace: Path, project: Optional[str] = None) -> Path:
    workspace = workspace.resolve()
    if _is_skill_root(workspace):
        workspace = workspace.parent
    if not project:
        return workspace

    project_workspace = (workspace / project).resolve()
    try:
        project_workspace.relative_to(workspace)
    except ValueError:
        print(f"ERROR: Project path escapes workspace: {project}", file=sys.stderr)
        sys.exit(1)
    return project_workspace


def _resolve_feature_dir(workspace: Path, feature: str) -> Path:
    feature = feature.strip()
    if not feature:
        print("ERROR: Feature is required for createFeature mode", file=sys.stderr)
        sys.exit(1)
    if "\0" in feature:
        print("ERROR: Feature contains invalid characters", file=sys.stderr)
        sys.exit(1)

    features_dir = get_features_active_dir(workspace).resolve()
    feature_dir = get_feature_active_dir(workspace, feature).resolve()
    try:
        feature_dir.relative_to(features_dir)
    except ValueError:
        print(f"ERROR: Feature path escapes features directory: {feature}", file=sys.stderr)
        sys.exit(1)
    if feature_dir == features_dir:
        print("ERROR: Feature resolves to features directory itself", file=sys.stderr)
        sys.exit(1)
    return feature_dir


def create_feature(workspace: Path, feature: str) -> Dict[str, object]:
    workspace = workspace.resolve()
    feature_dir = _resolve_feature_dir(workspace, feature)
    if feature_dir.exists():
        print(f"ERROR: 特性已存在：{feature_dir}", file=sys.stderr)
        sys.exit(1)

    ensure_dir(feature_dir)

    return {
        "initialized": feature_dir.is_dir(),
        "created": [str(feature_dir)],
        "backup": None,
        "message": f"Feature created successfully: {feature_dir}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize autobizdevops workspace")
    parser.add_argument("--mode", choices=("createProject", "createFeature"), default="createProject")
    parser.add_argument("--workspace", required=True, help="Workspace path")
    parser.add_argument("--project", help="Project code under workspace")
    parser.add_argument("--feature", help="Feature name for createFeature mode")
    parser.add_argument("--force", action="store_true", help="Force re-initialization (will backup existing)")
    args = parser.parse_args()

    workspace = _resolve_target_workspace(Path(args.workspace), args.project)
    if not workspace.exists():
        print(f"ERROR: Workspace does not exist: {workspace}", file=sys.stderr)
        sys.exit(1)

    if args.mode == "createFeature":
        result = create_feature(workspace, args.feature or "")
    else:
        result = init_workspace(workspace, force=args.force)

    print(result["message"])
    if result["backup"]:
        print(f"Backup created: {result['backup']}")
    if result["created"]:
        print("Created files/directories:")
        for item in result["created"]:
            print(f"  - {item}")

    sys.exit(0 if result["initialized"] else 1)


if __name__ == "__main__":
    main()
