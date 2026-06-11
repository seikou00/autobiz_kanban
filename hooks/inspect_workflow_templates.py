#!/usr/bin/env python3
"""Expose workflow template catalog, custom node catalog, and closure solving for the UI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.contracts import BoardConfigError, load_board_config  # noqa: E402
from board_core.workflow_closure import solve_node_closure  # noqa: E402
from board_core.workflow_compiler import (  # noqa: E402
    WorkflowCompileError,
    compile_node_subset,
    configured_template_options,
)


BOARD_CONFIG_PATH = ROOT / "board_core" / "board_config.json"
SCHEMA_VERSION = "autobizdevops.workflow.templates.v1"


def _node_catalog(base_config: dict) -> list[dict]:
    workflow = base_config.get("workflow", {})
    nodes = workflow.get("nodes", []) if isinstance(workflow, dict) else []
    catalog: list[dict] = []
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            continue
        artifacts = node.get("artifacts", {})
        artifacts = artifacts if isinstance(artifacts, dict) else {}
        catalog.append({
            "id": node["id"],
            "label": node.get("label", node["id"]),
            "group": node.get("group", ""),
            "skill": node.get("skill", ""),
            "description": node.get("description", ""),
            "inputs": [
                {
                    "path": artifact.get("path", ""),
                    "label": artifact.get("label", ""),
                    "required": artifact.get("required", True),
                }
                for artifact in artifacts.get("inputs", [])
                if isinstance(artifact, dict)
            ],
            "outputs": [
                {
                    "path": artifact.get("path", ""),
                    "label": artifact.get("label", ""),
                }
                for artifact in artifacts.get("outputs", [])
                if isinstance(artifact, dict)
            ],
        })
    return catalog


def _closure_payload(base_config: dict, node_ids: list[str], *, auto_include: bool) -> dict:
    result = solve_node_closure(base_config, node_ids, auto_include_producers=auto_include)
    externalized = {node_id: list(paths) for node_id, paths in result.externalized.items()}
    effective = compile_node_subset(base_config, list(result.nodes), externalized_inputs=externalized)
    checkpoints = effective.get("workflow", {}).get("checkpoints", {})
    return {
        "nodes": list(result.nodes),
        "added": list(result.added),
        "entryNodes": list(result.entry_nodes),
        "externalized": externalized,
        "suggestions": {node_id: dict(hints) for node_id, hints in result.suggestions.items()},
        "initialCheckpoints": checkpoints.get("initial", []),
        "transitions": checkpoints.get("transitions", {}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect workflow templates / node catalog / closure")
    parser.add_argument(
        "--mode",
        choices=("templates", "nodes", "closure"),
        default="templates",
        help="templates: 模板清单; nodes: 自定义可选节点目录; closure: 依赖闭包求解",
    )
    parser.add_argument(
        "--nodes",
        default=None,
        help="closure 模式的选中节点，逗号分隔，如 dev.specs,dev.code,ops.archive",
    )
    parser.add_argument(
        "--auto-include",
        action="store_true",
        help="closure 模式下自动补全上游 producer（默认不补全：缺失输入外部化并返回 suggestions 供 UI 可选添加）",
    )
    args = parser.parse_args(argv)

    try:
        base_config = load_board_config(BOARD_CONFIG_PATH)
        payload: dict = {"schemaVersion": SCHEMA_VERSION, "mode": args.mode, "ok": True}
        if args.mode == "templates":
            payload["templates"] = configured_template_options(base_config)
        elif args.mode == "nodes":
            payload["nodes"] = _node_catalog(base_config)
        else:
            node_ids = [item.strip() for item in (args.nodes or "").split(",") if item.strip()]
            if not node_ids:
                print("closure 模式需要 --nodes", file=sys.stderr)
                return 2
            payload["closure"] = _closure_payload(
                base_config,
                node_ids,
                auto_include=args.auto_include,
            )
    except (BoardConfigError, WorkflowCompileError) as exc:
        json.dump(
            {"schemaVersion": SCHEMA_VERSION, "mode": args.mode, "ok": False, "errors": [str(exc)]},
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        print()
        return 1

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
