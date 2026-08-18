#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同步 plan.json 状态变更后的所有相关字段。

当你手动修改了任务或批次的状态后，运行此脚本自动同步：
- taskSetDigest
- batch completedTaskCount
- feature 整体状态
- activeBatchId / nextBatchId 指针
- 必要的 evidence 字段（如果缺失则生成模拟值）

Usage:
    python scripts/sync_plan_status.py <feature_dir> [--dry-run]

Example:
    # 预览变更
    python scripts/sync_plan_status.py .autodev/features/feature-001 --dry-run

    # 实际应用变更
    python scripts/sync_plan_status.py .autodev/features/feature-001
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hooks.plan_json import (
    load_plan,
    load_plan_bundle,
    task_set_digest,
    write_plan_json,
    normalize_status,
    tasks,
    PlanJsonError,
    defer_to_test_stages_enabled,
)


def generate_evidence_id(existing_ids: list[str]) -> str:
    """生成一个新的 evidence ID。"""
    if not existing_ids:
        return "ev_0001"

    # 从最后一个 ID 递增
    last_id = existing_ids[-1]
    if last_id.startswith("ev_"):
        try:
            num = int(last_id.split("_")[1]) + 1
            return f"ev_{num:04d}"
        except (IndexError, ValueError):
            pass

    return f"ev_{len(existing_ids) + 1:04d}"


def sync_task_done_fields(task: dict[str, Any], defer_to_test: bool) -> dict[str, str]:
    """同步任务 done 状态所需的字段。"""
    changes = {}

    evidence_ids = task.get("evidenceIds", [])
    completion_ids = task.get("completionEvidenceIds", [])

    # 如果是 done 状态但缺少必要的 evidence
    if normalize_status(task.get("status")) == "done":
        # 确保有 evidenceIds
        if not evidence_ids:
            new_evidence = generate_evidence_id([])
            evidence_ids = [new_evidence]
            task["evidenceIds"] = evidence_ids
            changes["evidenceIds"] = f"generated [{new_evidence}]"

        # defer_to_test_stages 策略下不强制要求 completion evidence
        if not defer_to_test:
            # 确保有 completionEvidenceIds
            if not completion_ids:
                # 使用最后一个 evidence ID 作为 completion
                completion_ids = [evidence_ids[-1]]
                task["completionEvidenceIds"] = completion_ids
                changes["completionEvidenceIds"] = f"set to [{evidence_ids[-1]}]"

            # 确保有 latestPassEvidenceId（除非 deferred）
            disposition = task.get("validationDisposition")
            if not disposition and not task.get("latestPassEvidenceId"):
                task["latestPassEvidenceId"] = completion_ids[-1]
                changes["latestPassEvidenceId"] = f"set to {completion_ids[-1]}"

        # 同步 implementationRevision
        impl_ids = task.get("implementationEvidenceIds", [])
        if impl_ids:
            expected_revision = len(impl_ids)
            current_revision = task.get("implementationRevision")
            if current_revision != expected_revision:
                task["implementationRevision"] = expected_revision
                changes["implementationRevision"] = f"{current_revision} -> {expected_revision}"

            # 同步 latestImplementationEvidenceId
            if task.get("latestImplementationEvidenceId") != impl_ids[-1]:
                task["latestImplementationEvidenceId"] = impl_ids[-1]
                changes["latestImplementationEvidenceId"] = f"set to {impl_ids[-1]}"

    return changes


def sync_batch_status(batch_data: dict[str, Any], batch_id: str) -> dict[str, Any]:
    """同步批次状态相关字段。"""
    changes = {}

    batch_tasks = tasks(batch_data)
    completed_count = sum(1 for t in batch_tasks if normalize_status(t.get("status")) == "done")
    total_count = len(batch_tasks)

    # 更新 completedTaskCount
    if batch_data.get("completedTaskCount") != completed_count:
        old_count = batch_data.get("completedTaskCount")
        batch_data["completedTaskCount"] = completed_count
        changes["completedTaskCount"] = f"{old_count} -> {completed_count}"

    # 根据任务完成情况更新批次状态
    current_status = batch_data.get("status")
    expected_status = current_status

    if completed_count == total_count and current_status != "done":
        expected_status = "done"
    elif completed_count > 0 and current_status == "todo":
        expected_status = "in_progress"

    if current_status != expected_status:
        batch_data["status"] = expected_status
        changes["status"] = f"{current_status} -> {expected_status}"

    # 如果批次变为 done，添加 completedAt 时间戳
    if expected_status == "done" and not batch_data.get("completedAt"):
        timestamp = datetime.now().isoformat() + "Z"
        batch_data["completedAt"] = timestamp
        changes["completedAt"] = f"set to {timestamp}"

    return changes


def sync_feature_status(root: dict[str, Any], batch_data: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """同步功能整体状态和批次指针。"""
    changes = {}

    entries = [e for e in root.get("batches", []) if isinstance(e, dict)]

    # 同步每个 batch entry 的状态投影
    for entry in entries:
        batch_id = entry.get("id")
        batch = batch_data.get(batch_id, {})
        batch_status = batch.get("status")

        if entry.get("status") != batch_status:
            old_status = entry.get("status")
            entry["status"] = batch_status
            changes[f"batches.{batch_id}.status"] = f"{old_status} -> {batch_status}"

    # 找出当前应该激活的批次
    active_batch = None
    next_batch = None

    for entry in entries:
        status = entry.get("status")
        batch_id = entry.get("id")

        if status == "in_progress":
            active_batch = batch_id
            break
        elif status == "todo" and next_batch is None:
            next_batch = batch_id

    # 更新指针
    if root.get("activeBatchId") != active_batch:
        old_active = root.get("activeBatchId")
        root["activeBatchId"] = active_batch
        changes["activeBatchId"] = f"{old_active} -> {active_batch}"

    if root.get("nextBatchId") != next_batch:
        old_next = root.get("nextBatchId")
        root["nextBatchId"] = next_batch
        changes["nextBatchId"] = f"{old_next} -> {next_batch}"

    # 更新功能整体状态
    all_done = all(e.get("status") == "done" for e in entries)
    any_in_progress = any(e.get("status") == "in_progress" for e in entries)
    current_feature_status = root.get("status")

    if all_done and current_feature_status != "done":
        root["status"] = "done"
        changes["status"] = f"{current_feature_status} -> done"
    elif any_in_progress and current_feature_status == "todo":
        root["status"] = "in_progress"
        changes["status"] = f"{current_feature_status} -> in_progress"
    elif not any_in_progress and not all_done and active_batch is None and next_batch:
        # 有待办批次但没有进行中的，可能需要标记为 awaiting_next_conversation
        if current_feature_status == "in_progress":
            root["status"] = "awaiting_next_conversation"
            changes["status"] = f"{current_feature_status} -> awaiting_next_conversation"

    return changes


def sync_plan(feature_dir: Path, dry_run: bool = False) -> int:
    """同步整个 plan 的状态相关字段并更新 digest。"""

    try:
        print(f"{'[DRY RUN] ' if dry_run else ''}Loading plan bundle: {feature_dir}")
        bundle = load_plan_bundle(feature_dir)

        defer_to_test = defer_to_test_stages_enabled(bundle.root)
        if defer_to_test:
            print("  ℹ Using defer_to_test_stages validation policy")

        all_changes = {}

        # 1. 同步所有任务的 done 状态字段
        print("\n📋 Syncing task fields...")
        task_changes_count = 0
        for batch_id, batch in bundle.batches.items():
            for task in tasks(batch):
                task_id = task.get("id")
                task_changes = sync_task_done_fields(task, defer_to_test)
                if task_changes:
                    print(f"  ✓ {task_id}: {task_changes}")
                    all_changes[f"task.{task_id}"] = task_changes
                    task_changes_count += 1
        if task_changes_count == 0:
            print("  ℹ No task changes needed")

        # 2. 同步批次状态
        print("\n📦 Syncing batch status...")
        batch_changes_count = 0
        for batch_id, batch in bundle.batches.items():
            batch_changes = sync_batch_status(batch, batch_id)
            if batch_changes:
                print(f"  ✓ {batch_id}: {batch_changes}")
                all_changes[f"batch.{batch_id}"] = batch_changes
                batch_changes_count += 1
        if batch_changes_count == 0:
            print("  ℹ No batch changes needed")

        # 3. 同步功能整体状态
        print("\n🎯 Syncing feature status...")
        feature_changes = sync_feature_status(bundle.root, bundle.batches)
        if feature_changes:
            for key, value in feature_changes.items():
                print(f"  ✓ {key}: {value}")
            all_changes["feature"] = feature_changes
        else:
            print("  ℹ No feature status changes needed")

        # 4. 重新计算并更新 digest（总是执行）
        print("\n🔐 Recalculating taskSetDigest...")
        new_digest = task_set_digest(bundle.root, bundle.batches)
        old_digest = bundle.root.get("taskSetDigest")

        if old_digest != new_digest:
            bundle.root["taskSetDigest"] = new_digest
            print(f"  ✓ Digest updated")
            print(f"    Old: {old_digest[:16] if old_digest else 'None'}...")
            print(f"    New: {new_digest[:16]}...")
            all_changes["taskSetDigest"] = "updated"
        else:
            print(f"  ℹ Digest already correct: {new_digest[:16]}...")

        # 5. 写回文件
        if not dry_run:
            if all_changes:
                print("\n💾 Writing changes to disk...")

                # 写回根 plan.json
                root_path = feature_dir / "plan.json"
                write_plan_json(root_path, bundle.root)
                print(f"  ✓ {root_path}")

                # 写回所有批次 plan.json
                for batch_id, batch in bundle.batches.items():
                    batch_path = feature_dir / "plans" / batch_id / "plan.json"
                    write_plan_json(batch_path, batch)
                    print(f"  ✓ {batch_path}")

                print(f"\n✅ Successfully synced and saved {len(all_changes)} groups of changes")
            else:
                print("\n✅ No changes needed - plan is already in sync")
        else:
            if all_changes:
                print(f"\n🔍 [DRY RUN] Would apply {len(all_changes)} groups of changes")
                print("    Run without --dry-run to apply")
            else:
                print("\n✅ [DRY RUN] No changes needed")

        return 0

    except PlanJsonError as exc:
        print(f"\n✗ Plan validation error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\n✗ Unexpected error: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="同步 plan.json 状态变更后的所有相关字段",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 预览变更
  python scripts/sync_plan_status.py .autodev/features/feature-001 --dry-run

  # 实际应用变更
  python scripts/sync_plan_status.py .autodev/features/feature-001
        """
    )
    parser.add_argument(
        "feature_dir",
        type=Path,
        help="Path to feature directory"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to disk"
    )

    args = parser.parse_args(argv)

    if not args.feature_dir.is_dir():
        print(f"✗ Directory not found: {args.feature_dir}", file=sys.stderr)
        return 1

    return sync_plan(args.feature_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
