#!/usr/bin/env python3
"""Conflict-prevention policy for parallel Code batches.

The planner declares each task's intended file touches.  This module turns
those declarations into execution stages and dependency edges without using
them as broad repository locks: only shared entry files, protobuf changes and
database/configuration changes are serialized by policy.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from hooks.plan_json import PlanBundle, normalize_repository_relative_path


TOUCH_KINDS = {"code", "shared", "proto", "database", "configuration"}
EXECUTION_STAGES = {"parallel", "integration", "proto", "global"}
SPECIAL_TOUCH_KINDS = {"shared", "proto", "database", "configuration"}


def _task_id(task: dict[str, Any]) -> str:
    return str(task.get("id", "TASK"))


def normalized_touches(task: dict[str, Any]) -> tuple[list[dict[str, str]], list[str]]:
    """Validate canonical task touches without changing legacy Plan content."""
    task_id = _task_id(task)
    raw = task.get("touches")
    if not isinstance(raw, list) or not raw:
        return [], [f"{task_id}.touches_missing"]
    touches: list[dict[str, str]] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"{task_id}.touches[{index}]_must_be_object")
            continue
        path = item.get("path")
        kind = item.get("kind", "code")
        normalized_path = normalize_repository_relative_path(path) if isinstance(path, str) else None
        if normalized_path is None or normalized_path == ".":
            errors.append(f"{task_id}.touches[{index}].path_invalid")
            continue
        if not isinstance(kind, str) or kind not in TOUCH_KINDS:
            errors.append(f"{task_id}.touches[{index}].kind_invalid")
            continue
        key = (normalized_path, kind)
        if key in seen:
            errors.append(f"{task_id}.touches_duplicate:{normalized_path}:{kind}")
            continue
        seen.add(key)
        touches.append({"path": normalized_path, "kind": kind})
    return touches, errors


def task_execution_stage(task: dict[str, Any]) -> tuple[str | None, list[str], list[dict[str, str]]]:
    """Return the required stage from declared touches.

    A task with two special ownership classes is intentionally rejected.  A
    planner must split it so one accountable owner performs each global change.
    """
    touches, errors = normalized_touches(task)
    kinds = {item["kind"] for item in touches}
    special = kinds & SPECIAL_TOUCH_KINDS
    if "shared" in special:
        if special != {"shared"}:
            errors.append(f"{_task_id(task)}.touches_shared_must_be_integration_only")
        return "integration", errors, touches
    if "proto" in special:
        if special != {"proto"}:
            errors.append(f"{_task_id(task)}.touches_proto_must_be_in_proto_batch")
        return "proto", errors, touches
    if special & {"database", "configuration"}:
        if special - {"database", "configuration"}:
            errors.append(f"{_task_id(task)}.touches_global_change_must_be_in_global_batch")
        return "global", errors, touches
    return "parallel", errors, touches


def _repository_key(batch_id: str, batch: dict[str, Any], repository_roots: dict[str, str] | None) -> str:
    refs = {
        str(task.get("workspaceRef"))
        for task in batch.get("tasks", [])
        if isinstance(task, dict) and isinstance(task.get("workspaceRef"), str)
    }
    ref = next(iter(refs)) if len(refs) == 1 else f"batch:{batch_id}"
    if repository_roots and ref in repository_roots:
        return str(PurePosixPath(repository_roots[ref]))
    return ref


def _confirmation(policy: dict[str, Any], kind: str, batch_id: str) -> bool:
    records = policy.get("global_change_confirmations")
    record = records.get(kind) if isinstance(records, dict) else None
    return isinstance(record, dict) and record.get("confirmed") is True and record.get("batchId") == batch_id


def analyze_parallel_conflict_policy(
    bundle: PlanBundle,
    *,
    repository_roots: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return policy edges, stages, warnings and validation errors.

    New plans enable this contract by default; the scheduler uses the declared
    dependency DAG and this contract to validate every parallel run.
    """
    raw_policy = bundle.root.get("parallelPolicy")
    if not isinstance(raw_policy, dict) or raw_policy.get("enabled") is not True:
        return {
            "enabled": False,
            "errors": [],
            "warnings": [],
            "stages": {},
            "dependencies": {},
            "touches": {},
        }

    errors: list[str] = []
    warnings: list[dict[str, Any]] = []
    entries = [item for item in bundle.root.get("batches", []) if isinstance(item, dict)]
    order = {str(entry.get("id")): index for index, entry in enumerate(entries)}
    stages: dict[str, str] = {}
    dependencies: dict[str, set[str]] = defaultdict(set)
    touches_by_batch: dict[str, list[dict[str, str]]] = {}
    repository_batches: dict[str, list[str]] = defaultdict(list)
    kind_batches: dict[tuple[str, str], set[str]] = defaultdict(set)
    path_batches: dict[tuple[str, str], set[str]] = defaultdict(set)

    has_pb_change = raw_policy.get("has_pb_change")
    if not isinstance(has_pb_change, bool):
        errors.append("parallelPolicy.has_pb_change_must_be_bool")

    for entry in entries:
        batch_id = str(entry.get("id", ""))
        batch = bundle.batches.get(batch_id)
        if not isinstance(batch, dict):
            continue
        refs = {
            str(task.get("workspaceRef"))
            for task in batch.get("tasks", [])
            if isinstance(task, dict) and isinstance(task.get("workspaceRef"), str) and task.get("workspaceRef")
        }
        if len(refs) != 1:
            errors.append(f"{batch_id}.workspaceRef_ambiguous")
        batch_stages: set[str] = set()
        batch_touches: list[dict[str, str]] = []
        for task in batch.get("tasks", []):
            if not isinstance(task, dict):
                continue
            stage, task_errors, touches = task_execution_stage(task)
            errors.extend(task_errors)
            if stage:
                batch_stages.add(stage)
            batch_touches.extend(touches)
        if len(batch_stages) != 1:
            errors.append(f"{batch_id}.executionStage_ambiguous")
            stage = "parallel"
        else:
            stage = next(iter(batch_stages))
        declared_stage = entry.get("executionStage")
        if declared_stage is not None and declared_stage != stage:
            errors.append(f"{batch_id}.executionStage_projection_mismatch")
        stages[batch_id] = stage
        touches_by_batch[batch_id] = sorted(batch_touches, key=lambda item: (item["path"], item["kind"]))
        repository = _repository_key(batch_id, batch, repository_roots)
        repository_batches[repository].append(batch_id)
        for touch in batch_touches:
            kind_batches[(repository, touch["kind"])].add(batch_id)
            if stage == "parallel" and touch["kind"] == "code":
                path_batches[(repository, touch["path"])].add(batch_id)

    proto_present = any(stages.get(batch_id) == "proto" for batch_id in stages)
    if isinstance(has_pb_change, bool) and has_pb_change != proto_present:
        errors.append("parallelPolicy.has_pb_change_mismatch")

    for repository, batch_ids in repository_batches.items():
        by_stage: dict[str, list[str]] = defaultdict(list)
        for batch_id in batch_ids:
            by_stage[stages.get(batch_id, "parallel")].append(batch_id)
        for stage, owner_name in (("proto", "proto-engineer"), ("global", "global-change-engineer"), ("integration", "integration-agent")):
            owners = by_stage.get(stage, [])
            if len(owners) > 1:
                errors.append(f"parallelPolicy.{stage}_multiple_batches:{repository}:{','.join(sorted(owners))}")
            if owners:
                owner = owners[0]
                for target in batch_ids:
                    if target == owner:
                        continue
                    if stage == "integration":
                        dependencies[owner].add(target)
                    elif stages.get(target) in {"parallel", "integration"}:
                        dependencies[target].add(owner)
                # Ownership is implicit in the required stage and stored for
                # the runtime manifest; this keeps planner output concise.
                _ = owner_name

        global_owners = by_stage.get("global", [])
        if global_owners:
            owner = global_owners[0]
            global_kinds = {
                touch["kind"]
                for touch in touches_by_batch.get(owner, [])
                if touch["kind"] in {"database", "configuration"}
            }
            for kind in global_kinds:
                if not _confirmation(raw_policy, kind, owner):
                    errors.append(f"parallelPolicy.global_change_confirmation_missing:{kind}:{owner}")

    for batch_id, required in dependencies.items():
        for dependency in required:
            if order.get(dependency, 10**9) >= order.get(batch_id, -1):
                errors.append(f"{batch_id}.policy_dependency_not_earlier:{dependency}")

    for (repository, path), batch_ids in sorted(path_batches.items()):
        if len(batch_ids) > 1:
            warnings.append({
                "type": "normal_touch_overlap",
                "repository": repository,
                "path": path,
                "batches": sorted(batch_ids),
                "message": "planner should isolate ordinary file touches before parallel execution",
            })

    return {
        "enabled": True,
        "errors": sorted(set(errors)),
        "warnings": warnings,
        "stages": stages,
        "dependencies": {batch_id: sorted(values) for batch_id, values in dependencies.items()},
        "touches": touches_by_batch,
    }
