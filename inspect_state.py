#!/usr/bin/env python3
"""AutoBizDevOps inspect adapter — translates plugin state into board JSON.

Usage:
    python inspect_state.py --workspace <path> --mode run --project <name> --feature <slug>
    python inspect_state.py --workspace <path> --mode project --projects <name1> [name2 ...]

Protocol: skill-board-inspect-protocol.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HOOKS_DIR = ROOT / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from board_core.artifacts import scan_artifacts  # type: ignore[import-untyped]
from board_core.contracts import artifact_dicts  # type: ignore[import-untyped]
from board_core.state import (  # type: ignore[import-untyped]
    find_feature_dir,
    load_state_md,
    load_state_records,
)
from board_core.workflow import (  # type: ignore[import-untyped]
    derive_current_state_id,
    build_workflow_shell,
    derive_node_state_id,
    find_current_node,
)


BOARD_CONFIG_PATH = ROOT / "board_core" / "board_config.json"
def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_board_config() -> dict:
    if not BOARD_CONFIG_PATH.is_file():
        print(f"board_config.json not found: {BOARD_CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    return json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))


def _feature_ref_dir(workspace: Path, feature: str, feature_dir: Path | None) -> str:
    if feature_dir is not None:
        try:
            return feature_dir.resolve().relative_to(workspace.resolve()).as_posix()
        except ValueError:
            pass
    return f".autobizdevops/features/{feature}"


def _watch_refs(workspace: Path, feature: str, feature_dir: Path | None = None) -> list[dict]:
    feature_ref_dir = _feature_ref_dir(workspace, feature, feature_dir)
    return [
        {"path": ".autobizdevops/STATE.md", "purpose": "run-state"},
        {"path": ".autobizdevops/state.json", "purpose": "run-state-json"},
        {"path": feature_ref_dir, "purpose": "artifacts"},
        {"path": f"{feature_ref_dir}/hooks.ndjson", "purpose": "hook-log"},
    ]


def _hook_log_refs(workspace: Path, feature: str, feature_dir: Path | None = None) -> list[dict]:
    feature_ref_dir = _feature_ref_dir(workspace, feature, feature_dir)
    return [
        {"id": "default", "path": f"{feature_ref_dir}/hooks.ndjson", "format": "ndjson"},
    ]



def run_mode(workspace: Path, feature: str, config: dict) -> int:
    """Handle --mode run."""
    nodes_config = config["workflow"]["nodes"]
    suffix_states = config["checkpointSuffixState"]

    # Read STATE.md
    state_rows, state_errors, state_exists = load_state_md(workspace)
    checkpoint = state_rows.get(feature)
    feature_dir = find_feature_dir(workspace, feature)
    has_feature_dir = feature_dir is not None

    # Determine initial degraded state message
    summary_parts: list[str] = []
    if not state_exists:
        summary_parts.append("STATE.md 未找到，project 尚未初始化")
    elif not state_rows and not state_errors:
        summary_parts.append("STATE.md 中无 feature 记录")
    elif not state_rows and state_errors:
        # parse_state_table filtered all rows due to errors (e.g. unknown checkpoint)
        # state_errors will be appended below, don't add a generic "无记录" message
        pass
    elif checkpoint is None:
        summary_parts.append(f"feature '{feature}' 未在 STATE.md 中找到")
    if state_errors:
        summary_parts.extend(state_errors)

    # If there's no checkpoint, degrade gracefully: best-effort scan
    current_idx, current_node_id = -1, None
    if checkpoint:
        current_idx, current_node_id = find_current_node(nodes_config, checkpoint)

    if current_idx < 0 and checkpoint:
        summary_parts.append(f"未知 checkpoint '{checkpoint}'，adapter 无法映射到流程节点")

    # Build nodes
    run_nodes: list[dict] = []
    for idx, node in enumerate(nodes_config):
        state_id = derive_node_state_id(idx, current_idx, checkpoint or "", node, suffix_states)
        artifacts = scan_artifacts(
            feature_dir or (workspace / ".autobizdevops" / "features" / feature),
            workspace,
            artifact_dicts(node, "outputs"),
        )
        run_nodes.append({
            "id": node["id"],
            "stateId": state_id,
            "artifacts": artifacts,
        })

    # Assemble output
    output = {
        "schemaVersion": "cmbdevclaw_v1",
        "workflow": build_workflow_shell(config),
        "run": {
            "featureId": feature,
            "featureName": feature,
            "hookLogRefs": _hook_log_refs(workspace, feature, feature_dir),
            "watchRefs": _watch_refs(workspace, feature, feature_dir),
            "currentNodeId": current_node_id or "unknown",
            "nodes": run_nodes,
        },
    }

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


def _resolve_project_workspace(workspace: Path, project: str) -> Path:
    project_workspace = (workspace / project).resolve()
    try:
        project_workspace.relative_to(workspace)
    except ValueError:
        print(f"project 路径越界: {project}", file=sys.stderr)
        sys.exit(1)
    return project_workspace


def _collect_project_runs(project_workspace: Path, config: dict, project: str) -> list[dict]:
    """返回某个 project 下所有 feature 的 runs 摘要列表（不包含 schemaVersion/workflow 外壳）"""
    nodes_config = config["workflow"]["nodes"]
    suffix_states = config["checkpointSuffixState"]

    state_records, _state_errors, _state_exists = load_state_records(project_workspace)
    feature_names = sorted(state_records.keys())

    runs: list[dict] = []
    for feature in feature_names:
        record = state_records.get(feature, {})
        checkpoint = record.get("checkpoint", "")
        current_idx, current_node_id = (-1, None)
        if checkpoint:
            current_idx, current_node_id = find_current_node(nodes_config, checkpoint)

        current_state_id = derive_current_state_id(checkpoint, suffix_states, current_idx)

        runs.append({
            "featureName": feature,
            "featureId": feature,
            "currentNodeId": current_node_id or "unknown",
            "currentStateId": current_state_id,
        })

    return runs


def project_mode(workspace: Path, projects: list[str], config: dict) -> int:
    """Handle --mode project with one or more projects."""
    all_projects: dict[str, dict] = {}
    for project in projects:
        project_workspace = _resolve_project_workspace(workspace, project)
        if not project_workspace.is_dir():
            print(f"project 不存在: {project_workspace}", file=sys.stderr)
            continue
        runs = _collect_project_runs(project_workspace, config, project)
        all_projects[project] = {"runs": runs}

    output = {
        "schemaVersion": "cmbdevclaw_v1",
        "workflow": build_workflow_shell(config),
        "projects": all_projects,
    }

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoBizDevOps Inspect Adapter")
    parser.add_argument("--workspace", required=True, help="项目集合工作区路径")
    parser.add_argument("--mode", required=True, choices=("project", "run"), help="inspect mode")
    parser.add_argument("--feature", default=None, help="feature slug (required for run mode)")
    parser.add_argument("--project", default=None, help="project name (required for --mode run)")
    parser.add_argument("--projects", nargs="+", default=None,
                        help="one or more project names (for --mode project)")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"workspace 不存在: {workspace}", file=sys.stderr)
        return 1

    config = _load_board_config()

    if args.mode == "run":
        if not args.project:
            print("--mode run 需要 --project 参数", file=sys.stderr)
            return 1
        if not args.feature:
            print("--mode run 需要 --feature 参数", file=sys.stderr)
            return 1
        project_workspace = _resolve_project_workspace(workspace, args.project)
        if not project_workspace.is_dir():
            print(f"project 不存在: {project_workspace}", file=sys.stderr)
            return 1
        return run_mode(project_workspace, args.feature, config)

    if not args.projects:
        print("--mode project 需要 --projects 参数", file=sys.stderr)
        return 1
    return project_mode(workspace, args.projects, config)


if __name__ == "__main__":
    raise SystemExit(main())
