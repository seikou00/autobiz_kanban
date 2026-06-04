#!/usr/bin/env python3
"""Resolve the next action for a feature checkpoint from the effective workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.state_store import get_state_json_path, load_state_json_records_result  # noqa: E402
from board_core.workflow import derive_node_status, find_current_node  # noqa: E402
from board_core.workflow_compiler import BASE_WORKFLOW_PROFILE, load_effective_board_config  # noqa: E402


BOARD_CONFIG_PATH = ROOT / "board_core" / "board_config.json"


def _state_next_action(node: dict, node_status: str) -> dict[str, str]:
    for state in node.get("states", []):
        if isinstance(state, dict) and state.get("nodeStatus", state.get("id")) == node_status:
            action = state.get("nextAction", {})
            return action if isinstance(action, dict) else {}
    return {}


def resolve_route(workspace: Path, feature: str) -> tuple[dict, int]:
    workspace = workspace.resolve()
    result = load_state_json_records_result(workspace)
    errors = list(result.errors)
    if not result.exists:
        errors.append(f"state.json 未找到: {get_state_json_path(workspace)}")
    record = result.records.get(feature)
    if record is None and not errors:
        errors.append(f"feature '{feature}' 未在 state.json 中找到")
    if errors or record is None:
        return {"ok": False, "feature": feature, "errors": errors}, 1

    workflow_profile = record.get("workflowProfile", BASE_WORKFLOW_PROFILE)
    checkpoint = record.get("checkpoint", "")
    try:
        config = load_effective_board_config(
            BOARD_CONFIG_PATH,
            repo_root=ROOT,
            workspace=workspace,
            profile=workflow_profile,
        )
    except Exception as exc:
        return {
            "ok": False,
            "feature": feature,
            "workflowProfile": workflow_profile,
            "checkpoint": checkpoint,
            "errors": [str(exc)],
        }, 1

    nodes = config["workflow"]["nodes"]
    current_idx, current_node_id = find_current_node(nodes, checkpoint)
    if current_idx < 0:
        return {
            "ok": False,
            "feature": feature,
            "workflowProfile": workflow_profile,
            "checkpoint": checkpoint,
            "errors": [f"未知 checkpoint: {checkpoint}"],
        }, 1

    node_status = derive_node_status(
        current_idx,
        current_idx,
        checkpoint,
        nodes[current_idx],
        config["checkpointSuffixState"],
    )
    next_action = _state_next_action(nodes[current_idx], node_status)
    return {
        "ok": True,
        "feature": feature,
        "workflowProfile": workflow_profile,
        "checkpoint": checkpoint,
        "currentNodeId": current_node_id,
        "currentNodeStatus": node_status,
        "currentStateId": node_status,
        "nextAction": next_action,
    }, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve next workflow action for a feature")
    parser.add_argument("--workspace", "-w", required=True)
    parser.add_argument("--feature", "-f", required=True)
    args = parser.parse_args(argv)

    payload, exit_code = resolve_route(Path(args.workspace), args.feature)
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
