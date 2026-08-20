#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判断是否启用 Workflow 并行执行 Code 阶段的多个 Batch。

单 Batch 使用原有串行流程；多 Batch 启用 Workflow。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import feature_dir  # noqa: E402
from hooks.parallel_batch_scheduler import validate_plan_for_parallel  # noqa: E402
from hooks.parallel_runtime import batch_workspace_ref, plan_digest  # noqa: E402
from hooks.plan_json import BATCH_ID_RE, load_plan_bundle, plan_json_path  # noqa: E402


def analyze_batches(feature: str, plugin_path: Path | None = None) -> dict:
    """分析 feature 的 batch 结构，决定执行策略。

    Returns:
        {
            "useWorkflow": bool,
            "strategy": "serial" | "parallel" | "blocked",
            "batchCount": int,
            "batches": [{"id": "B001", "lane": "backend", "taskCount": 3}],
            "workflowScript": str | None,
            "reason": str
        }
    """
    try:
        if plugin_path is None:
            plugin_path = ROOT

        workspace = plugin_path
        feat_dir = feature_dir(workspace, feature)
        plan_path = plan_json_path(feat_dir)

        if not plan_path.exists():
            return {
                "useWorkflow": False,
                "strategy": "serial",
                "batchCount": 0,
                "batches": [],
                "workflowScript": None,
                "reason": "plan_not_found"
            }

        bundle = load_plan_bundle(feat_dir)
        plan = bundle.root
        batch_entries = [e for e in plan.get("batches", []) if isinstance(e, dict)]

        # 过滤出有效的 batch
        valid_batches = []
        for entry in batch_entries:
            batch_id = str(entry.get("id", ""))
            if not BATCH_ID_RE.fullmatch(batch_id):
                continue

            status = entry.get("status", "").lower()
            if status in {"done", "failed"}:
                continue  # 跳过已完成或失败的 batch

            batch_plan = bundle.batches.get(batch_id, {})
            task_count = len([t for t in batch_plan.get("tasks", []) if isinstance(t, dict)])

            valid_batches.append({
                "id": batch_id,
                "lane": entry.get("executionLane", entry.get("lane", "unknown")),
                "executionLane": entry.get("executionLane", entry.get("lane", "unknown")),
                "workspaceRef": entry.get("workspaceRef") or batch_workspace_ref(batch_plan),
                "executionStage": entry.get("executionStage", "parallel"),
                "deps": list(entry.get("deps", [])),
                "status": status,
                "taskCount": task_count
            })

        batch_count = len(valid_batches)

        # 决策逻辑
        if batch_count == 0:
            return {
                "useWorkflow": False,
                "strategy": "serial",
                "batchCount": 0,
                "batches": [],
                "workflowScript": None,
                "reason": "no_pending_batches"
            }

        if batch_count == 1:
            return {
                "useWorkflow": False,
                "strategy": "serial",
                "batchCount": 1,
                "batches": valid_batches,
                "workflowScript": None,
                "reason": "single_batch_use_serial"
            }

        validation = validate_plan_for_parallel(workspace, feature)
        if not validation.get("canParallel"):
            return {
                "useWorkflow": False,
                "strategy": "blocked",
                "batchCount": batch_count,
                "batches": valid_batches,
                "workflowScript": None,
                "reason": f"parallel_plan_invalid:{validation.get('reason')}",
                "requiresPlanRepair": True,
                "validation": validation,
            }

        # 多 Batch：启用 Workflow
        workflow_script = ROOT / "workflows" / "code-batched-execution.workflow.js"

        return {
            "useWorkflow": True,
            "strategy": "parallel",
            "batchCount": batch_count,
            "batches": valid_batches,
            "workflowScript": str(workflow_script),
            "reason": f"multiple_batches_use_workflow:{batch_count}",
            "planDigest": plan_digest(bundle),
            "validation": validation,
        }

    except Exception as e:
        return {
            "useWorkflow": False,
            "strategy": "serial",
            "batchCount": 0,
            "batches": [],
            "workflowScript": None,
            "reason": f"error:{str(e)}"
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="判断是否使用 Workflow 并行执行 Code Batch")
    parser.add_argument("--feature", required=True, help="Feature ID")
    parser.add_argument("--plugin-path", help="Plugin 路径（默认为当前项目根目录）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    args = parser.parse_args()

    plugin_path = Path(args.plugin_path) if args.plugin_path else None
    result = analyze_batches(args.feature, plugin_path)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["useWorkflow"]:
            print(f"✓ 检测到 {result['batchCount']} 个待执行 Batch")
            print(f"  策略: 使用 Workflow 并行执行")
            print(f"  脚本: {result['workflowScript']}")
            for batch in result["batches"]:
                print(f"  - {batch['id']} ({batch['lane']}): {batch['taskCount']} 个任务")
        else:
            mode = "计划修复" if result["strategy"] == "blocked" else "串行执行模式"
            print(f"✗ {mode}")
            print(f"  原因: {result['reason']}")
            if result['batches']:
                print(f"  Batch: {result['batches'][0]['id']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
