#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""File-level write ownership checks for generated Plans.

``scope.paths`` and ``expectedFiles`` are the physical write set consumed by
the conservative Batch scheduler.  They must not make several *Batches* claim
the same file: that looks parallel in the Task DAG but is necessarily
serialized at runtime.  A shared schema, route registry, or global
configuration file is instead owned by one earlier Batch; its consumers
depend on that Batch without also listing the file in their write sets.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping


def normalize_owned_path(value: Any, workspace_ref: Any) -> str | None:
    """Normalize the writer's ``Repo:path`` and ``Repo/path`` spellings.

    Plan artifacts historically used both forms.  The scheduler only needs a
    repository-relative path, so normalize both before deciding ownership.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.replace("\\", "/").strip().strip("/")
    if not raw:
        return None
    repository = workspace_ref.strip() if isinstance(workspace_ref, str) else ""
    if repository:
        for prefix in (f"{repository}:", f"{repository}/"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].strip("/")
                break
    return raw or None


def task_write_paths(task: dict[str, Any]) -> set[str]:
    """Return all declared physical write paths for one Task."""

    workspace_ref = task.get("workspaceRef")
    raw_paths: list[Any] = []
    scope = task.get("scope")
    if isinstance(scope, dict) and isinstance(scope.get("paths"), list):
        raw_paths.extend(scope["paths"])
    if isinstance(task.get("expectedFiles"), list):
        raw_paths.extend(task["expectedFiles"])
    return {
        path
        for value in raw_paths
        if (path := normalize_owned_path(value, workspace_ref)) is not None
    }


def write_ownership_violations(
    tasks: Iterable[dict[str, Any]],
    *,
    ownership_scope_by_task: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return structured errors for files claimed by more than one Batch.

    Tasks in one Batch execute as one ordered delivery, so they may share an
    implementation file without reducing inter-Batch parallelism.  The
    ownership boundary is therefore a projected Batch, not an individual
    Task.  When a scope map is omitted each Task is treated as its own Batch;
    this is useful only for callers that do not have a Batch projection yet.
    """

    owners: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for task in tasks:
        if not isinstance(task, dict) or task.get("executionMode") == "external_dependency":
            continue
        task_id = task.get("id")
        workspace_ref = task.get("workspaceRef")
        if not isinstance(task_id, str) or not task_id.strip():
            continue
        if not isinstance(workspace_ref, str) or not workspace_ref.strip():
            continue
        scope = (
            ownership_scope_by_task.get(task_id, task_id)
            if ownership_scope_by_task is not None
            else task_id
        )
        for path in task_write_paths(task):
            owners[(workspace_ref.strip(), path)][scope].add(task_id)

    violations: list[dict[str, Any]] = []
    for (workspace_ref, path), scoped_task_ids in sorted(owners.items()):
        if len(scoped_task_ids) < 2:
            continue
        ordered_task_ids = sorted({task_id for values in scoped_task_ids.values() for task_id in values})
        violations.append({
            "reason": "shared_write_path_requires_single_owner",
            "workspaceRef": workspace_ref,
            "path": path,
            "detail": (
                f"workspace={workspace_ref};path={path};"
                f"taskIds={','.join(ordered_task_ids)}"
            ),
            "taskIds": ordered_task_ids,
            "field": "touches",
            "repairTarget": "task_group",
            "repairSuggestion": (
                f"{workspace_ref}:{path} 被多个 Task 同时声明为写入目标。请创建或保留一个"
                "前置 owner Task（共享 SQL/路由/全局配置建议 executionStage=global），"
                "把该文件的全部改动与验证收敛到 owner；其他 Task 通过 deps 消费其产出，"
                "并从 touches、scope.paths、expectedFiles 和 implementationPoints 中移除该文件。"
            ),
        })
    return violations


def write_ownership_error_codes(
    tasks: Iterable[dict[str, Any]],
    *,
    ownership_scope_by_task: Mapping[str, str] | None = None,
) -> list[str]:
    """Stable compact form for the low-level plan.json validator."""

    return [
        "{reason}:workspace={workspace}:path={path}:taskIds={task_ids}".format(
            reason=violation["reason"],
            workspace=violation["workspaceRef"],
            path=violation["path"],
            task_ids=",".join(violation["taskIds"]),
        )
        for violation in write_ownership_violations(
            tasks,
            ownership_scope_by_task=ownership_scope_by_task,
        )
    ]
