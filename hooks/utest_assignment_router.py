#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build stable UTest assignments from root and Batch plans."""

from __future__ import print_function

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import resolve_feature, resolve_workspace  # noqa: E402


PREFERRED_LANES = ("backend", "frontend")


class UTestAssignmentError(Exception):
    """Raised when plan facts cannot form safe assignments."""


class RepairArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise UTestAssignmentError(
            "命令参数无效：{}。修复：运行 `{} --help` 并传入 workspace/feature。".format(
                message, self.prog
            )
        )


def _read_object(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UTestAssignmentError(
            "无法读取计划 {}：{}。修复：确认根/Batch plan 路径存在且可读。".format(path, exc)
        )
    except ValueError as exc:
        raise UTestAssignmentError(
            "计划不是合法 JSON {}：{}。修复：回到 /autodev-plan 重新生成。".format(path, exc)
        )
    if not isinstance(data, dict):
        raise UTestAssignmentError(
            "计划顶层不是 object：{}。修复：回到 /autodev-plan 重新生成。".format(path)
        )
    return data


def _batch_path(feature_dir, raw_path):
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise UTestAssignmentError(
            "root plan batch.path 缺失。修复：为每个 Batch 写入相对 plan.json 路径。"
        )
    requested = Path(raw_path)
    if requested.is_absolute():
        raise UTestAssignmentError(
            "Batch plan path 不能是绝对路径：{}。修复：使用 feature 目录内相对路径。".format(raw_path)
        )
    resolved = (feature_dir / requested).resolve()
    try:
        resolved.relative_to(feature_dir.resolve())
    except ValueError:
        raise UTestAssignmentError(
            "Batch plan path 越界：{}。修复：使用 feature 目录内相对路径。".format(raw_path)
        )
    return resolved


def _batch_assignments(feature_dir, entry, root_index):
    batch_path = _batch_path(feature_dir, entry.get("path"))
    batch = _read_object(batch_path)
    batch_id = batch.get("batchId") or entry.get("id") or entry.get("batchId")
    lane = batch.get("executionLane")
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise UTestAssignmentError(
            "第 {} 个 Batch 缺少 batchId。修复：回到 /autodev-plan 补齐。".format(root_index + 1)
        )
    if not isinstance(lane, str) or not lane.strip():
        raise UTestAssignmentError(
            "{} 缺少 executionLane。修复：回到 /autodev-plan 写入稳定 lane。".format(batch_id)
        )
    tasks = batch.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise UTestAssignmentError(
            "{} tasks 为空。修复：回到 /autodev-plan 生成可执行任务。".format(batch_id)
        )

    groups = []
    by_workspace = {}
    for task_index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise UTestAssignmentError(
                "{} tasks[{}] 不是 object。修复：回到 /autodev-plan 重新生成。".format(
                    batch_id, task_index
                )
            )
        task_id = task.get("id")
        workspace_ref = task.get("workspaceRef")
        if not isinstance(task_id, str) or not task_id.strip():
            raise UTestAssignmentError(
                "{} tasks[{}] 缺少 id。修复：回到 /autodev-plan 补齐 TASK ID。".format(
                    batch_id, task_index
                )
            )
        if not isinstance(workspace_ref, str) or not workspace_ref.strip():
            raise UTestAssignmentError(
                "{} {} 缺少 workspaceRef。修复：回到 /autodev-plan 绑定声明的代码仓库。".format(
                    batch_id, task_id
                )
            )
        assignment = by_workspace.get(workspace_ref)
        if assignment is None:
            assignment = {
                "batchId": batch_id,
                "executionLane": lane,
                "workspaceRef": workspace_ref,
                "taskIds": [],
                "planPath": batch_path.relative_to(feature_dir).as_posix(),
                "rootOrder": root_index,
            }
            by_workspace[workspace_ref] = assignment
            groups.append(assignment)
        assignment["taskIds"].append(task_id)
    return groups


def build_assignments(feature_dir):
    target = Path(feature_dir).expanduser().resolve()
    root_plan = _read_object(target / "plan.json")
    batches = root_plan.get("batches")
    if not isinstance(batches, list):
        raise UTestAssignmentError(
            "root plan.batches 不是数组。修复：回到 /autodev-plan 生成 Batch 根索引。"
        )
    assignments = []
    for root_index, entry in enumerate(batches):
        if not isinstance(entry, dict):
            raise UTestAssignmentError(
                "root plan.batches[{}] 不是 object。修复：回到 /autodev-plan 重新生成。".format(
                    root_index
                )
            )
        assignments.extend(_batch_assignments(target, entry, root_index))

    lane_rank = {lane: index for index, lane in enumerate(PREFERRED_LANES)}
    assignments.sort(
        key=lambda item: (
            lane_rank.get(item["executionLane"], len(PREFERRED_LANES)),
            item["rootOrder"],
        )
    )
    for assignment in assignments:
        assignment.pop("rootOrder", None)
    return assignments


def main(argv=None):
    parser = RepairArgumentParser(description="生成稳定 UTest assignment 顺序")
    parser.add_argument("--workspace")
    parser.add_argument("--feature")
    parser.add_argument("--feature-dir")
    parser.add_argument("--json", action="store_true", help="输出稳定 JSON（默认格式）")
    try:
        args = parser.parse_args(argv)
        if args.feature_dir:
            if args.workspace or args.feature:
                raise UTestAssignmentError(
                    "--feature-dir 不能与 --workspace/--feature 混用。修复：只保留一种定位方式。"
                )
            feature_dir = Path(args.feature_dir)
        else:
            if not args.workspace or not args.feature:
                raise UTestAssignmentError(
                    "缺少 --workspace/--feature。修复：传入插件输出工作区和 Feature。"
                )
            workspace = resolve_workspace(args.workspace)
            feature = resolve_feature(args.feature)
            feature_dir = workspace / ".autobizdevops" / "features" / feature
        result = {"assignments": build_assignments(feature_dir)}
    except UTestAssignmentError as exc:
        print("utest_assignment_router_failed: {}".format(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            "utest_assignment_router_failed: {}。修复：检查 workspace、feature 和计划产物。".format(exc),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
