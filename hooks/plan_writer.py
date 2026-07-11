#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Incrementally write plan.json and render PLAN.md."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import (  # noqa: E402
    WriterResult,
    artifact_path,
    atomic_write_json,
    fail,
    fail_if_artifact_exists,
    load_json,
    next_numbered_id,
    parse_json_value,
    read_object_file,
    read_object_stdin,
    render_result,
    resolve_feature,
    resolve_workspace,
    string_list,
    with_result_data,
    write_text,
    WriterError,
)
from hooks.evidence_kernel import FileLock  # noqa: E402
from hooks.plan_json import (  # noqa: E402
    BATCH_STRATEGY,
    MAX_BATCH_TASKS,
    VALIDATION_KINDS,
    batch_plan_path,
    load_plan_bundle,
    normalize_status,
    task_contract_sha256,
    validate_plan_bundle_data,
    validate_task_collection,
)
from hooks.plan_granularity import (  # noqa: E402
    PLAN_TASK_MATRIX_MAX_SCENARIOS,
    PLAN_TASK_MAX_SCENARIOS,
    validate_plan_task_granularity_item,
)


PLAN_FILE = "plan.json"
PLAN_MD_FILE = "PLAN.md"
TASK_TEMPLATE_RELATIVE_PATH = "skills/autodev/autodev-plan/templates/task-input.json"
TASK_TEMPLATE_PATH = ROOT / TASK_TEMPLATE_RELATIVE_PATH
TASK_DETAIL_PATCH_FIELDS = {"goal", "implementationPoints", "acceptanceCriteria", "nonGoals", "blockers"}
TASK_DETAIL_FORBIDDEN_FIELDS = {
    "id",
    "status",
    "deps",
    "evidenceIds",
    "specRefs",
    "apiIds",
    "dataIds",
    "designRefs",
    "decisionIds",
    "uiRefs",
    "uiRequired",
}


class PlanWriterInputError(ValueError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def _path(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, PLAN_FILE)


def _md_path(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, PLAN_MD_FILE)


def _handoff_path(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, "BATCH_HANDOFF.json")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _plan_lock(workspace: Path, feature: str) -> FileLock:
    return FileLock(_path(workspace, feature).parent / ".plan.lock")


def _task_input_example() -> dict[str, Any]:
    try:
        value = json.loads(TASK_TEMPLATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"task_input_template_unavailable:{exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("task_input_template_must_be_object")
    return value


def _matrix_exception_example() -> dict[str, Any]:
    scenario_refs = [f"specs/[capability]/spec.md#SCN-{index:03d}" for index in range(1, 7)]
    return {
        "specRefs": ["specs/[capability]/spec.md#REQ-001", *scenario_refs],
        "mergedScenarioRefs": scenario_refs,
        "acceptanceCriteria": [
            {
                "id": "AC-T001-01",
                "text": "[shared observable matrix result]",
                "scenarioRefs": scenario_refs,
            }
        ],
        "validationCommands": [
            {
                "id": "VAL-T001-01",
                "argv": ["[executable]", "[matrix validation arguments]"],
                "cwd": ".",
                "kind": "integration_test",
                "required": True,
                "covers": ["AC-T001-01"],
            }
        ],
        "splitRationale": "[one request/response or state matrix shares one validation loop]",
    }


def _initial(feature: str) -> dict[str, Any]:
    return {
        "featureId": feature,
        "status": "todo",
        "activeBatchId": None,
        "nextBatchId": None,
        "batchPolicy": {"maxTasks": MAX_BATCH_TASKS, "strategy": BATCH_STRATEGY},
        "batches": [],
        "projectValidationCommands": [],
        "projectCheckEvidenceIds": [],
        "latestProjectCheckEvidenceId": None,
        "tasks": [],  # in-memory working view; never written to root plan.json
        "_batchAssignments": {},
        "_batchPlans": {},
    }


def _load(workspace: Path, feature: str) -> dict[str, Any]:
    root = load_json(_path(workspace, feature), default=None)
    if root is None:
        return _initial(feature)
    if not isinstance(root, dict):
        raise ValueError("plan.json root 必须是 object")
    if "tasks" in root:
        raise PlanWriterInputError("monolithic_plan_requires_rebuild")
    if "version" in root or "taskDetailVersion" in root:
        raise PlanWriterInputError("legacy_plan_requires_rebuild")
    data = dict(root)
    data.setdefault("featureId", feature)
    data.setdefault("status", "todo")
    data.setdefault("activeBatchId", None)
    data.setdefault("nextBatchId", None)
    data.setdefault("batchPolicy", {"maxTasks": MAX_BATCH_TASKS, "strategy": BATCH_STRATEGY})
    data.setdefault("batches", [])
    data.setdefault("projectValidationCommands", [])
    data.setdefault("projectCheckEvidenceIds", [])
    data.setdefault("latestProjectCheckEvidenceId", None)
    task_items: list[dict[str, Any]] = []
    assignments: dict[str, str] = {}
    batch_plans: dict[str, dict[str, Any]] = {}
    feature_dir = _path(workspace, feature).parent
    for entry in data.get("batches", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        batch_id = str(entry["id"])
        plan = load_json(batch_plan_path(feature_dir, batch_id))
        if not isinstance(plan, dict):
            raise PlanWriterInputError("missing_batch_plan", batch_id)
        batch_plans[batch_id] = plan
        for task in plan.get("tasks", []):
            if isinstance(task, dict):
                task_items.append(task)
                if isinstance(task.get("id"), str):
                    assignments[str(task["id"])] = batch_id
    data["tasks"] = task_items
    data["_batchAssignments"] = assignments
    data["_batchPlans"] = batch_plans
    return data


def _structure_errors(data: dict[str, Any], *, allow_empty: bool = False) -> list[str]:
    if allow_empty and _tasks(data) == []:
        errors: list[str] = []
        if "version" in data or "taskDetailVersion" in data:
            errors.append("legacy_plan_requires_rebuild")
        if not isinstance(data.get("featureId"), str) or not data.get("featureId"):
            errors.append("plan_json_missing_feature_id")
        return errors
    return validate_task_collection(str(data.get("featureId", "")), _tasks(data))


def _primary_spec_root(task: dict[str, Any]) -> str:
    for ref in task.get("specRefs", []):
        if isinstance(ref, str) and ref.strip():
            return ref.split("#", 1)[0] or "specs/unspecified/spec.md"
    return "specs/unspecified/spec.md"


def _next_batch_id(batch_ids: set[str]) -> str:
    numbers = [int(value[1:]) for value in batch_ids if len(value) == 4 and value[0] == "B" and value[1:].isdigit()]
    return f"B{max(numbers, default=0) + 1:03d}"


def _batch_status(batch_tasks: list[dict[str, Any]]) -> str:
    statuses = [normalize_status(task.get("status")) for task in batch_tasks]
    if statuses and all(status == "done" for status in statuses):
        return "done"
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "in_progress" for status in statuses) or any(status == "done" for status in statuses):
        return "in_progress"
    return "todo"


def _project_batches(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    tasks_view = _tasks(data)
    assignments = dict(data.get("_batchAssignments") or {})
    prior_plans = data.get("_batchPlans") if isinstance(data.get("_batchPlans"), dict) else {}
    groups: dict[str, list[dict[str, Any]]] = {}
    spec_roots: dict[str, str] = {}
    existing_ids = {
        str(entry.get("id"))
        for entry in data.get("batches", [])
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    used_ids = set(existing_ids)
    for task in tasks_view:
        task_id = str(task.get("id", ""))
        batch_id = assignments.get(task_id)
        if batch_id is None:
            primary = _primary_spec_root(task)
            last_batch = sorted(groups)[-1] if groups else None
            can_append_to_last = bool(
                last_batch
                and spec_roots.get(str(last_batch)) == primary
                and len(groups[str(last_batch)]) < MAX_BATCH_TASKS
            )
            batch_id = str(last_batch) if can_append_to_last else _next_batch_id(used_ids)
            used_ids.add(batch_id)
            assignments[task_id] = batch_id
        group = groups.setdefault(batch_id, [])
        if len(group) >= MAX_BATCH_TASKS:
            new_batch = _next_batch_id(used_ids)
            used_ids.add(new_batch)
            assignments[task_id] = new_batch
            batch_id = new_batch
            group = groups.setdefault(batch_id, [])
        group.append(task)
        spec_roots.setdefault(batch_id, _primary_spec_root(task))

    ordered_ids = sorted(groups)
    root = {
        key: value
        for key, value in data.items()
        if key not in {"tasks", "_batchAssignments", "_batchPlans"}
    }
    root["batchPolicy"] = {"maxTasks": MAX_BATCH_TASKS, "strategy": BATCH_STRATEGY}
    root_entries: list[dict[str, Any]] = []
    projected: dict[str, dict[str, Any]] = {}
    task_to_batch = {
        str(task.get("id")): batch_id
        for batch_id, batch_tasks in groups.items()
        for task in batch_tasks
    }
    for index, batch_id in enumerate(ordered_ids):
        batch_tasks = groups[batch_id]
        previous = prior_plans.get(batch_id) if isinstance(prior_plans, dict) else None
        previous = previous if isinstance(previous, dict) else {}
        status = _batch_status(batch_tasks)
        completion_ids = [
            evidence_id
            for task in batch_tasks
            for evidence_id in task.get("completionEvidenceIds", [])
            if isinstance(evidence_id, str)
        ]
        spec_root = spec_roots[batch_id]
        title = str(previous.get("title") or Path(spec_root).parent.name or batch_id)
        projected[batch_id] = {
            "featureId": root.get("featureId"),
            "batchId": batch_id,
            "title": title,
            "status": status,
            "taskCount": len(batch_tasks),
            "completedTaskCount": sum(normalize_status(task.get("status")) == "done" for task in batch_tasks),
            "completionEvidenceIds": completion_ids,
            "startedAt": previous.get("startedAt"),
            "completedAt": previous.get("completedAt") if status == "done" else None,
            "tasks": batch_tasks,
        }
        cross_deps = {
            task_to_batch[dep]
            for task in batch_tasks
            for dep in task.get("deps", [])
            if isinstance(dep, str) and dep in task_to_batch and task_to_batch[dep] != batch_id
        }
        if index > 0:
            cross_deps.add(ordered_ids[index - 1])
        root_entries.append(
            {
                "id": batch_id,
                "path": f"plans/{batch_id}/plan.json",
                "title": title,
                "specRoots": [spec_root],
                "deps": sorted(cross_deps),
                "taskIds": [str(task.get("id")) for task in batch_tasks],
                "status": status,
            }
        )
    root["batches"] = root_entries
    unfinished = [entry["id"] for entry in root_entries if entry["status"] != "done"]
    if not root_entries:
        root.update({"status": "todo", "activeBatchId": None, "nextBatchId": None})
    elif root.get("status") == "awaiting_next_conversation":
        root["activeBatchId"] = None
    elif not unfinished:
        if data.get("status") == "failed":
            root["status"] = "failed"
        else:
            root["status"] = "done" if root.get("latestProjectCheckEvidenceId") else "in_progress"
        root["activeBatchId"] = None
        root["nextBatchId"] = None
    else:
        active = root.get("activeBatchId")
        if active not in unfinished:
            active = unfinished[0]
        root["activeBatchId"] = active
        active_index = unfinished.index(active)
        root["nextBatchId"] = unfinished[active_index + 1] if active_index + 1 < len(unfinished) else None
        if any(entry["status"] == "failed" for entry in root_entries):
            root["status"] = "failed"
        elif data.get("status") == "in_progress" or any(entry["status"] == "in_progress" for entry in root_entries):
            root["status"] = "in_progress"
        else:
            root["status"] = "todo"
    data["_batchAssignments"] = assignments
    data["_batchPlans"] = projected
    return root, projected


def _write(workspace: Path, feature: str, data: dict[str, Any], *, allow_empty: bool = False) -> WriterResult:
    path = _path(workspace, feature)
    errors = _structure_errors(data, allow_empty=allow_empty)
    if errors:
        return WriterResult(ok=False, path=path, errors=[{"reason": error} for error in errors])
    root, batch_plans = _project_batches(data)
    if batch_plans:
        errors = validate_plan_bundle_data(root, batch_plans)
        if errors:
            return WriterResult(ok=False, path=path, errors=[{"reason": error} for error in errors])
    changed = False
    feature_dir = path.parent
    for batch_id, batch in batch_plans.items():
        changed = atomic_write_json(batch_plan_path(feature_dir, batch_id), batch) or changed
    changed = atomic_write_json(path, root) or changed
    return WriterResult(ok=True, path=path, changed=changed)


def _tasks(data: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = data.setdefault("tasks", [])
    if not isinstance(tasks, list):
        raise ValueError("plan.json.tasks 必须是数组")
    return tasks


def _find_task(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    for task in _tasks(data):
        if isinstance(task, dict) and task.get("id") == task_id:
            return task
    raise ValueError(f"任务不存在: {task_id}")


def _ids(data: dict[str, Any]) -> set[str]:
    return {task.get("id") for task in _tasks(data) if isinstance(task, dict) and isinstance(task.get("id"), str)}


def _batch_for_task(data: dict[str, Any], task_id: str) -> str:
    assignments = data.get("_batchAssignments")
    batch_id = assignments.get(task_id) if isinstance(assignments, dict) else None
    if not isinstance(batch_id, str):
        raise ValueError(f"任务批次不存在: {task_id}")
    return batch_id


def _append_unique(values: list[str], items: list[str]) -> list[str]:
    result = list(values)
    for item in items:
        if item not in result:
            result.append(item)
    return result


def _remove_values(values: list[str], items: list[str]) -> list[str]:
    remove = set(items)
    return [item for item in values if item not in remove]


def _split_values(values: list[str] | None) -> list[str]:
    return [value.strip() for value in values or [] if value.strip()]


def _normalize_task(task: dict[str, Any], task_id: str) -> None:
    scenario_refs = [
        ref for ref in task.get("specRefs", []) if isinstance(ref, str) and "SCN-" in ref
    ]
    raw_criteria = task.get("acceptanceCriteria")
    if isinstance(raw_criteria, list):
        criteria: list[dict[str, Any]] = []
        for index, item in enumerate(raw_criteria, start=1):
            if isinstance(item, dict):
                criterion = dict(item)
                criterion.setdefault("id", f"AC-{task_id}-{index:02d}")
                criterion.setdefault("scenarioRefs", scenario_refs)
            else:
                criterion = {
                    "id": f"AC-{task_id}-{index:02d}",
                    "text": str(item),
                    "scenarioRefs": scenario_refs,
                }
            criteria.append(criterion)
        task["acceptanceCriteria"] = criteria

    acceptance_ids = [
        item["id"]
        for item in task.get("acceptanceCriteria", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    raw_commands = task.get("validationCommands")
    if isinstance(raw_commands, list):
        commands: list[dict[str, Any]] = []
        for index, item in enumerate(raw_commands, start=1):
            if isinstance(item, dict) and isinstance(item.get("argv"), list):
                command = dict(item)
            else:
                text = item.get("command", "") if isinstance(item, dict) else str(item)
                command = {"argv": shlex.split(text)}
            command.setdefault("id", f"VAL-{task_id}-{index:02d}")
            command.setdefault("cwd", ".")
            command.setdefault("kind", "behavior_test")
            command.setdefault("required", True)
            command.setdefault("covers", acceptance_ids)
            commands.append(command)
        task["validationCommands"] = commands
    task.setdefault("completionPolicy", "all_required_validations_pass")
    task.setdefault("completionEvidenceIds", [])
    task.setdefault("latestPassEvidenceId", None)


def _default_task(task_id: str, args: argparse.Namespace) -> dict[str, Any]:
    implementation = _split_values(args.implementation_point)
    if not implementation:
        implementation = [f"实现 {args.title} 的最小行为闭环", "补充对应验证路径"]
    acceptance = _split_values(args.acceptance_criterion)
    if not acceptance:
        acceptance = [f"{args.title} 的主要行为可被验证命令覆盖"]
    non_goals = _split_values(args.non_goal)
    api_ids = _split_values(args.api_id)
    ui_required = bool(args.ui_required)
    if (ui_required or api_ids) and not non_goals:
        non_goals = ["不修改本任务范围之外的能力"]
    task: dict[str, Any] = {
        "id": task_id,
        "title": args.title,
        "goal": args.goal,
        "status": args.status,
        "deps": _split_values(args.dep),
        "uiRequired": ui_required,
        "scope": {
            "modules": _split_values(args.module),
            "entrypoints": _split_values(args.entrypoint),
            "pages": _split_values(args.page),
            "dataObjects": _split_values(args.data_object),
            "paths": _split_values(args.scope_path),
        },
        "implementationPoints": implementation,
        "acceptanceCriteria": acceptance,
        "nonGoals": non_goals,
        "specRefs": _split_values(args.spec_ref),
        "designRefs": _split_values(args.design_ref),
        "apiIds": api_ids,
        "dataIds": _split_values(args.data_id),
        "decisionIds": _split_values(args.decision_id),
        "validationCommands": [{"command": command} for command in _split_values(args.validation_command)],
        "expectedFiles": _split_values(args.expected_file),
        "evidenceIds": [],
        "blockers": [],
    }
    if args.split_rationale and args.split_rationale.strip():
        task["splitRationale"] = args.split_rationale.strip()
    if ui_required:
        task["uiRefs"] = {
            "pageRefs": _split_values(args.page_ref) or _split_values(args.page),
            "interactionRefs": _split_values(args.interaction_ref),
            "visualSourceRefs": _split_values(args.visual_source_ref),
            "frontendRoute": args.frontend_route,
        }
        task["scope"]["pages"] = list(task["uiRefs"]["pageRefs"])
    _normalize_task(task, task_id)
    return task


def _cmd_init(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    existing = fail_if_artifact_exists(_path(workspace, feature), force=args.force)
    if existing:
        return render_result(existing)
    if args.force:
        plans_dir = _path(workspace, feature).parent / "plans"
        if plans_dir.is_dir():
            for old_plan in plans_dir.glob("B*/plan.json"):
                old_plan.unlink(missing_ok=True)
                try:
                    old_plan.parent.rmdir()
                except OSError:
                    pass
            try:
                plans_dir.rmdir()
            except OSError:
                pass
        _handoff_path(workspace, feature).unlink(missing_ok=True)
    return render_result(with_result_data(_write(workspace, feature, _initial(feature), allow_empty=True), reset=bool(args.force)))


def _list_field_is_populated(task: dict[str, Any], field: str) -> bool:
    value = task.get(field)
    if not isinstance(value, list):
        return False
    if field == "validationCommands":
        return any(
            (isinstance(item, dict) and isinstance(item.get("command"), str) and item.get("command", "").strip())
            or (isinstance(item, dict) and isinstance(item.get("argv"), list) and bool(item.get("argv")))
            or (isinstance(item, str) and item.strip())
            for item in value
        )
    if field == "acceptanceCriteria":
        return any(
            (isinstance(item, dict) and isinstance(item.get("text"), str) and item.get("text", "").strip())
            or (isinstance(item, str) and item.strip())
            for item in value
        )
    return any(isinstance(item, str) and item.strip() for item in value)


def _validate_task_body_minimum(task: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in ("title", "goal"):
        value = task.get(field)
        if not isinstance(value, str) or not value.strip():
            missing.append(field)
    for field in ("specRefs", "implementationPoints", "acceptanceCriteria", "validationCommands"):
        if not _list_field_is_populated(task, field):
            missing.append(field)
    return missing


def _task_from_body(args: argparse.Namespace, data: dict[str, Any]) -> dict[str, Any] | None:
    body_sources = [
        source
        for source in (
            args.body_file,
            args.task_json,
            "__stdin__" if args.body_stdin else None,
        )
        if source
    ]
    if len(body_sources) > 1:
        raise PlanWriterInputError("conflicting_task_body_sources", "--body-file / --task-json / --body-stdin 只能三选一")
    if args.body_file:
        task = read_object_file(args.body_file)
    elif args.task_json:
        task = parse_json_value(args.task_json)
        if not isinstance(task, dict):
            raise ValueError("--task-json 顶层必须是 object")
    elif args.body_stdin:
        try:
            task = read_object_stdin()
        except WriterError as exc:
            message = str(exc)
            if "stdin 为空" in message:
                raise PlanWriterInputError("empty_body_stdin", message) from exc
            if "stdin 不是合法 JSON" in message:
                raise PlanWriterInputError("invalid_body_stdin_json", message) from exc
            if "stdin JSON 顶层必须是 object" in message:
                raise PlanWriterInputError("invalid_body_stdin_object", message) from exc
            raise
    else:
        return None

    body_id = task.get("id")
    if body_id is not None and not isinstance(body_id, str):
        raise ValueError("task body 的 id 必须是字符串")
    if args.task_id and body_id and args.task_id != body_id:
        raise ValueError(f"--task-id 与 task body id 不一致: {args.task_id} != {body_id}")
    if not body_id:
        task["id"] = args.task_id or next_numbered_id(_ids(data), "T")
    missing = _validate_task_body_minimum(task)
    if missing:
        raise PlanWriterInputError("invalid_plan_task_body", f"missing={','.join(missing)}")
    task["status"] = "todo"
    task.setdefault("deps", [])
    task.setdefault("uiRequired", False)
    task.setdefault("scope", {"modules": [], "entrypoints": [], "pages": [], "dataObjects": [], "paths": []})
    if isinstance(task.get("scope"), dict):
        task["scope"].setdefault("paths", [])
    task.setdefault("implementationPoints", [])
    task.setdefault("acceptanceCriteria", [])
    task.setdefault("nonGoals", [])
    task.setdefault("specRefs", [])
    task.setdefault("designRefs", [])
    task.setdefault("apiIds", [])
    task.setdefault("dataIds", [])
    task.setdefault("decisionIds", [])
    task.setdefault("validationCommands", [])
    task.setdefault("expectedFiles", [])
    task["evidenceIds"] = []
    _normalize_task(task, str(task["id"]))
    task["completionEvidenceIds"] = []
    task["latestPassEvidenceId"] = None
    task.setdefault("blockers", [])
    return task


def _cmd_add_task(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    body_task = _task_from_body(args, data)
    if body_task is None:
        if not args.title or not args.goal:
            return render_result(fail("missing_plan_task_args", "--title/--goal 或 --body-file/--task-json/--body-stdin 必填", path=_path(workspace, feature)))
        task_id = args.task_id or next_numbered_id(_ids(data), "T")
        task = _default_task(task_id, args)
    else:
        task = body_task
        task_id = str(task["id"])
    if task_id in _ids(data):
        return render_result(fail("duplicate_task_id", task_id, path=_path(workspace, feature)))
    _tasks(data).append(task)
    granularity_errors = validate_plan_task_granularity_item(task, task_id=task_id)
    if granularity_errors:
        return render_result(WriterResult(ok=False, path=_path(workspace, feature), errors=granularity_errors))
    structure_errors = _structure_errors(data)
    if structure_errors:
        return render_result(WriterResult(ok=False, path=_path(workspace, feature), errors=[{"reason": error} for error in structure_errors]))
    return render_result(_write(workspace, feature, data))


def _cmd_add_task_contract(args: argparse.Namespace) -> int:
    del args
    return render_result(
        WriterResult(
            ok=True,
            data={
                "contract": {
                    "taskTemplate": TASK_TEMPLATE_RELATIVE_PATH,
                    "taskInputExample": _task_input_example(),
                    "recommendedInputMode": "body-file",
                    "supportedInputModes": ["body-file", "body-stdin", "task-json", "cli-fields"],
                    "requiredTaskFields": [
                        "title",
                        "goal",
                        "specRefs",
                        "implementationPoints",
                        "acceptanceCriteria",
                        "validationCommands",
                    ],
                    "validationKinds": sorted(VALIDATION_KINDS),
                    "validationCoverage": {
                        "rule": "required_commands_cover_all_acceptance_criteria",
                        "compileMayCoverAcceptanceCriteria": False,
                    },
                    "batchAssignment": {
                        "strategy": BATCH_STRATEGY,
                        "maxTasks": MAX_BATCH_TASKS,
                        "manualBatchIdSupported": False,
                        "primaryCapabilitySource": "first_spec_ref_file",
                        "appendRule": "same_as_immediately_preceding_batch_and_not_full",
                    },
                    "forbiddenArguments": ["--batch-id", "--spec-refs", "--design-refs", "--decision-ids"],
                    "uiRule": "scope.pages_must_equal_uiRefs.pageRefs_when_uiRequired",
                    "conditionalFields": {
                        "uiRefs": {
                            "when": "uiRequired_is_true",
                            "requiredFields": [
                                "pageRefs",
                                "interactionRefs",
                                "visualSourceRefs",
                                "frontendRoute",
                            ],
                        },
                        "mergedScenarioRefs": {
                            "when": "scenario_refs_count_is_6_to_12",
                            "requiredFields": [],
                            "mustEqual": "fully_qualified_scenario_refs_from_specRefs",
                        },
                    },
                    "matrixException": {
                        "normalScenarioMaximum": PLAN_TASK_MAX_SCENARIOS,
                        "scenarioMaximum": PLAN_TASK_MATRIX_MAX_SCENARIOS,
                        "requiredValidation": "one_complete_required_non_compile_behavior_command",
                    },
                    "matrixExceptionExample": _matrix_exception_example(),
                    "projectValidationCommand": {
                        "requiredFields": ["id", "argv", "cwd", "kind", "required"]
                    },
                    "writerOwnedGeneratedArtifacts": {
                        "rootPlan": "plan.json",
                        "batchPlans": "plans/Bxxx/plan.json",
                    },
                }
            },
        )
    )


def _cmd_update_task(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    task = _find_task(data, args.task_id)
    for field in ("title", "goal", "status"):
        value = getattr(args, field)
        if value is not None:
            if field == "status" and normalize_status(value) == "done":
                return render_result(fail("task_completion_requires_task_runner", args.task_id))
            task[field] = value
    return render_result(_write(workspace, feature, data))


def _cmd_set_task_detail(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    patch = read_object_file(args.from_json_file)
    forbidden = sorted(set(patch) & TASK_DETAIL_FORBIDDEN_FIELDS)
    unknown = sorted(set(patch) - TASK_DETAIL_PATCH_FIELDS)
    if forbidden:
        return render_result(fail("forbidden_task_detail_fields", ",".join(forbidden)))
    if unknown:
        return render_result(fail("unknown_task_detail_fields", ",".join(unknown)))
    data = _load(workspace, feature)
    task = _find_task(data, args.task_id)
    task.update(patch)
    _normalize_task(task, args.task_id)
    return render_result(_write(workspace, feature, data))


def _cmd_set_scope(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    task = _find_task(data, args.task_id)
    scope = task.setdefault("scope", {"modules": [], "entrypoints": [], "pages": [], "dataObjects": []})
    for field, source in (
        ("modules", args.module),
        ("entrypoints", args.entrypoint),
        ("pages", args.page),
        ("dataObjects", args.data_object),
        ("paths", args.scope_path),
    ):
        if source is not None:
            scope[field] = _split_values(source)
    if task.get("uiRequired") is True and isinstance(task.get("uiRefs"), dict):
        task["uiRefs"]["pageRefs"] = list(scope.get("pages", []))
    return render_result(_write(workspace, feature, data))


def _cmd_set_ui_required(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    task = _find_task(data, args.task_id)
    required = args.required.lower() == "true"
    task["uiRequired"] = required
    if required:
        task.setdefault(
            "uiRefs",
            {"pageRefs": [], "interactionRefs": [], "visualSourceRefs": [], "frontendRoute": args.frontend_route},
        )
        task["uiRefs"]["frontendRoute"] = args.frontend_route
        task.setdefault("scope", {}).setdefault("pages", task["uiRefs"].get("pageRefs", []))
        if not task.get("nonGoals"):
            task["nonGoals"] = ["不修改本任务范围之外的页面或交互"]
    else:
        task.pop("uiRefs", None)
        task.setdefault("scope", {})["pages"] = []
    return render_result(_write(workspace, feature, data))


def _cmd_set_ui_refs(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    task = _find_task(data, args.task_id)
    task["uiRequired"] = True
    refs = {
        "pageRefs": _split_values(args.page_ref),
        "interactionRefs": _split_values(args.interaction_ref),
        "visualSourceRefs": _split_values(args.visual_source_ref),
        "frontendRoute": args.frontend_route,
    }
    task["uiRefs"] = refs
    task.setdefault("scope", {})["pages"] = list(refs["pageRefs"])
    if not task.get("nonGoals"):
        task["nonGoals"] = ["不修改本任务范围之外的页面或交互"]
    return render_result(_write(workspace, feature, data))


def _cmd_list_field(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    if args.field == "evidenceIds":
        return render_result(fail("task_evidence_binding_requires_task_runner", args.task_id))
    data = _load(workspace, feature)
    task = _find_task(data, args.task_id)
    items = _split_values(args.value)
    if args.field == "acceptanceCriteria":
        current = task.get(args.field) if isinstance(task.get(args.field), list) else []
        if args.remove:
            remove = set(items)
            task[args.field] = [
                item
                for item in current
                if not isinstance(item, dict)
                or (item.get("id") not in remove and item.get("text") not in remove)
            ]
        else:
            scenario_refs = [
                ref for ref in task.get("specRefs", []) if isinstance(ref, str) and "SCN-" in ref
            ]
            next_index = len(current) + 1
            current.extend(
                {
                    "id": f"AC-{args.task_id}-{next_index + offset:02d}",
                    "text": item,
                    "scenarioRefs": scenario_refs,
                }
                for offset, item in enumerate(items)
            )
            task[args.field] = current
        return render_result(_write(workspace, feature, data))
    current = task.get(args.field)
    if not isinstance(current, list):
        current = []
    task[args.field] = _remove_values(current, items) if args.remove else _append_unique(current, items)
    return render_result(_write(workspace, feature, data))


def _cmd_set_deps(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    _find_task(data, args.task_id)["deps"] = _split_values(args.dep)
    return render_result(_write(workspace, feature, data))


def _cmd_add_validation_command(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    task = _find_task(data, args.task_id)
    commands = task.setdefault("validationCommands", [])
    if not isinstance(commands, list):
        commands = []
        task["validationCommands"] = commands
    criteria = task.get("acceptanceCriteria") if isinstance(task.get("acceptanceCriteria"), list) else []
    covers = [item.get("id") for item in criteria if isinstance(item, dict) and isinstance(item.get("id"), str)]
    commands.append(
        {
            "id": args.command_id or f"VAL-{args.task_id}-{len(commands) + 1:02d}",
            "argv": shlex.split(args.command),
            "cwd": args.cwd,
            "kind": args.kind,
            "required": not args.optional,
            "covers": args.covers if args.covers is not None else covers,
            **({"repo": args.repo} if args.repo else {}),
        }
    )
    return render_result(_write(workspace, feature, data))


def _cmd_add_project_validation_command(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    commands = data.setdefault("projectValidationCommands", [])
    if not isinstance(commands, list):
        commands = []
        data["projectValidationCommands"] = commands
    commands.append(
        {
            "id": args.command_id or f"PROJECT-VAL-{len(commands) + 1:03d}",
            "argv": shlex.split(args.command),
            "cwd": args.cwd,
            "kind": args.kind,
            "required": not args.optional,
            **({"repo": args.repo} if args.repo else {}),
        }
    )
    return render_result(_write(workspace, feature, data))


def _cmd_set_split_rationale(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    _find_task(data, args.task_id)["splitRationale"] = args.rationale
    return render_result(_write(workspace, feature, data))


def _cmd_set_status(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    if normalize_status(args.status) == "done":
        return render_result(fail("task_completion_requires_task_runner", args.task_id))
    data = _load(workspace, feature)
    _find_task(data, args.task_id)["status"] = args.status
    return render_result(_write(workspace, feature, data))


def set_task_execution_status(
    workspace: Path,
    feature: str,
    task_id: str,
    status: str,
    *,
    expected_task_contract_sha256: str | None = None,
) -> WriterResult:
    """Internal task-runner API; public CLI cannot set a task to done."""

    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        task = _find_task(data, task_id)
        if (
            expected_task_contract_sha256 is not None
            and task_contract_sha256(task) != expected_task_contract_sha256
        ):
            return fail("task_contract_changed_after_start", task_id, path=_path(workspace, feature))
        task["status"] = status
        batch_id = _batch_for_task(data, task_id)
        batch_plans = data.get("_batchPlans")
        batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
        if isinstance(batch_plan, dict) and normalize_status(status) == "in_progress":
            batch_plan["startedAt"] = batch_plan.get("startedAt") or _utc_now()
            data["status"] = "in_progress"
            data["activeBatchId"] = batch_id
        result = _write(workspace, feature, data)
        if result.ok:
            write_text(_md_path(workspace, feature), _render_plan_md(data))
        return result


def record_task_attempt(
    workspace: Path,
    feature: str,
    task_id: str,
    evidence_ids: list[str],
    *,
    completion_evidence_ids: list[str],
    success: bool,
    expected_task_contract_sha256: str | None = None,
) -> WriterResult:
    """Atomically bind one runner attempt and update its terminal task state."""

    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        task = _find_task(data, task_id)
        if (
            expected_task_contract_sha256 is not None
            and task_contract_sha256(task) != expected_task_contract_sha256
        ):
            return fail("task_contract_changed_after_start", task_id, path=_path(workspace, feature))
        existing = task.get("evidenceIds") if isinstance(task.get("evidenceIds"), list) else []
        task["evidenceIds"] = _append_unique(existing, evidence_ids)
        task["completionEvidenceIds"] = list(completion_evidence_ids) if success else []
        task["latestPassEvidenceId"] = completion_evidence_ids[-1] if success and completion_evidence_ids else None
        task["status"] = "done" if success else "failed"
        batch_id = _batch_for_task(data, task_id)
        batch_tasks = [
            item
            for item in _tasks(data)
            if _batch_for_task(data, str(item.get("id"))) == batch_id
        ]
        batch_completed = success and all(normalize_status(item.get("status")) == "done" for item in batch_tasks)
        root_entries = [entry for entry in data.get("batches", []) if isinstance(entry, dict)]
        ordered_ids = [str(entry.get("id")) for entry in root_entries]
        handoff: dict[str, Any] | None = None
        if batch_completed:
            batch_plans = data.get("_batchPlans")
            batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
            if isinstance(batch_plan, dict):
                batch_plan["completedAt"] = _utc_now()
            batch_index = ordered_ids.index(batch_id)
            if batch_index + 1 < len(ordered_ids):
                next_batch = ordered_ids[batch_index + 1]
                data["status"] = "awaiting_next_conversation"
                data["activeBatchId"] = None
                data["nextBatchId"] = next_batch
                handoff = {
                    "featureId": feature,
                    "completedBatchId": batch_id,
                    "nextBatchId": next_batch,
                    "completedTaskIds": [str(item.get("id")) for item in batch_tasks],
                    "completionEvidenceIds": [
                        evidence_id
                        for item in batch_tasks
                        for evidence_id in item.get("completionEvidenceIds", [])
                        if isinstance(evidence_id, str)
                    ],
                    "nextBatch": {
                        "title": str(root_entries[batch_index + 1].get("title", "")),
                        "taskIds": list(root_entries[batch_index + 1].get("taskIds", [])),
                        "specRoots": list(root_entries[batch_index + 1].get("specRoots", [])),
                        "deps": list(root_entries[batch_index + 1].get("deps", [])),
                    },
                    "status": "awaiting_next_conversation",
                    "createdAt": _utc_now(),
                    "activationCommand": (
                        f"python hooks/task_runner.py activate-batch --workspace {workspace} "
                        f"--feature {feature} --batch-id {next_batch}"
                    ),
                    "instruction": "Open a new conversation and activate the next batch before continuing code work.",
                }
            else:
                data["status"] = "in_progress"
                data["activeBatchId"] = None
                data["nextBatchId"] = None
        result = _write(workspace, feature, data)
        if result.ok:
            write_text(_md_path(workspace, feature), _render_plan_md(data))
            if handoff is not None:
                atomic_write_json(_handoff_path(workspace, feature), handoff)
                result = with_result_data(result, batchHandoff=handoff)
        return result


def record_project_check_attempt(
    workspace: Path,
    feature: str,
    evidence_ids: list[str],
    *,
    success: bool,
) -> WriterResult:
    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        existing = data.get("projectCheckEvidenceIds") if isinstance(data.get("projectCheckEvidenceIds"), list) else []
        data["projectCheckEvidenceIds"] = _append_unique(existing, evidence_ids)
        data["latestProjectCheckEvidenceId"] = evidence_ids[-1] if success and evidence_ids else None
        data["status"] = "done" if success else "failed"
        return _write(workspace, feature, data)


def activate_batch(workspace: Path, feature: str, batch_id: str) -> WriterResult:
    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        if data.get("status") != "awaiting_next_conversation":
            return fail("feature_not_awaiting_next_conversation", str(data.get("status")), path=_path(workspace, feature))
        if data.get("nextBatchId") != batch_id:
            return fail("batch_activation_mismatch", f"expected={data.get('nextBatchId')} actual={batch_id}", path=_path(workspace, feature))
        entries = [entry for entry in data.get("batches", []) if isinstance(entry, dict)]
        entry = next((item for item in entries if item.get("id") == batch_id), None)
        if entry is None:
            return fail("batch_not_found", batch_id, path=_path(workspace, feature))
        by_id = {str(item.get("id")): item for item in entries}
        unfinished = [dep for dep in entry.get("deps", []) if by_id.get(dep, {}).get("status") != "done"]
        if unfinished:
            return fail("batch_dependencies_not_done", ",".join(unfinished), path=_path(workspace, feature))
        ordered = [str(item.get("id")) for item in entries]
        index = ordered.index(batch_id)
        data["status"] = "in_progress"
        data["activeBatchId"] = batch_id
        data["nextBatchId"] = ordered[index + 1] if index + 1 < len(ordered) else None
        result = _write(workspace, feature, data)
        if result.ok:
            _handoff_path(workspace, feature).unlink(missing_ok=True)
        return with_result_data(result, activeBatchId=batch_id, nextBatchId=data.get("nextBatchId"))


def _cmd_add_blocker(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    task = _find_task(data, args.task_id)
    blockers = task.setdefault("blockers", [])
    if not isinstance(blockers, list):
        blockers = []
        task["blockers"] = blockers
    blockers.append(args.blocker)
    return render_result(_write(workspace, feature, data))


def _cmd_clear_blockers(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    _find_task(data, args.task_id)["blockers"] = []
    return render_result(_write(workspace, feature, data))


def _cmd_validate(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    path = _path(workspace, feature)
    errors: list[str] = []
    try:
        load_plan_bundle(
            path.parent,
            require_initial_status=args.initial,
            require_all_done=args.done,
        )
    except ValueError as exc:
        errors = str(exc).split(";")
    return render_result(
        WriterResult(
            ok=not errors,
            path=path,
            errors=[{"reason": error} for error in errors],
            data={"validation": "gate" if args.gate or args.initial or args.done else "structure"},
        )
    )


def _fmt(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "-"
    return " / ".join(str(value) for value in values)


def _render_plan_md(data: dict[str, Any]) -> str:
    lines = [
        f"# 执行计划: {data.get('featureId', '')}",
        "",
        "来源: plan.json",
        "状态: 待执行",
        "",
        "## 任务总览",
        "",
        "| Task ID | 任务 | 依赖 | 状态 |",
        "| ------- | ---- | ---- | ---- |",
    ]
    for task in _tasks(data):
        lines.append(
            f"| {task.get('id', '')} | {task.get('title', '')} | {_fmt(task.get('deps'))} | {task.get('status', '')} |"
        )
    lines.extend(["", "## 任务详情", ""])
    for task in _tasks(data):
        lines.extend(
            [
                f"### Task [{task.get('id', '')}]: {task.get('title', '')}",
                "",
                f"- 做什么: {task.get('goal', '')}",
                f"- 规格依据: {_fmt(task.get('specRefs'))}",
                f"- api_id: {_fmt(task.get('apiIds'))}",
                f"- data_id: {_fmt(task.get('dataIds'))}",
                f"- decision_id: {_fmt(task.get('decisionIds'))}",
                f"- 涉及范围: modules={_fmt(task.get('scope', {}).get('modules') if isinstance(task.get('scope'), dict) else [])}; entrypoints={_fmt(task.get('scope', {}).get('entrypoints') if isinstance(task.get('scope'), dict) else [])}; pages={_fmt(task.get('scope', {}).get('pages') if isinstance(task.get('scope'), dict) else [])}",
                "- 执行要点:",
            ]
        )
        for index, point in enumerate(task.get("implementationPoints", []) if isinstance(task.get("implementationPoints"), list) else [], start=1):
            lines.append(f"  {index}. {point}")
        lines.append("- 验收标准:")
        for index, criterion in enumerate(task.get("acceptanceCriteria", []) if isinstance(task.get("acceptanceCriteria"), list) else [], start=1):
            text = criterion.get("text", "") if isinstance(criterion, dict) else criterion
            criterion_id = criterion.get("id") if isinstance(criterion, dict) else None
            label = f"{criterion_id}: {text}" if criterion_id else text
            lines.append(f"  {index}. {label}")
        lines.append(f"- 非目标: {_fmt(task.get('nonGoals'))}")
        if task.get("splitRationale"):
            lines.append(f"- 合并理由: {task.get('splitRationale')}")
        commands = task.get("validationCommands", [])
        lines.append("- 验证命令:")
        if isinstance(commands, list) and commands:
            for command in commands:
                if isinstance(command, dict):
                    argv = command.get("argv")
                    rendered = shlex.join(argv) if isinstance(argv, list) and all(isinstance(item, str) for item in argv) else command.get("command", "")
                    command_id = command.get("id")
                    lines.append(f"  - {command_id}: {rendered}" if command_id else f"  - {rendered}")
        else:
            lines.append("  - -")
        lines.append(f"- 状态: {task.get('status', '')}")
        lines.append("")
    return "\n".join(lines)


def _cmd_render_md(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    errors = _structure_errors(data)
    if errors:
        return render_result(WriterResult(ok=False, path=_path(workspace, feature), errors=[{"reason": error} for error in errors]))
    changed = write_text(_md_path(workspace, feature), _render_plan_md(data))
    return render_result(WriterResult(ok=True, path=_md_path(workspace, feature), changed=changed))


def _cmd_show(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    tasks = _tasks(data)
    summary = {
        "featureId": data.get("featureId"),
        "taskCount": len(tasks),
        "tasks": [
            {
                "id": task.get("id"),
                "title": task.get("title"),
                "status": task.get("status"),
                "specRefs": len(task.get("specRefs", [])) if isinstance(task.get("specRefs"), list) else 0,
                "apiIds": len(task.get("apiIds", [])) if isinstance(task.get("apiIds"), list) else 0,
            }
            for task in tasks
            if isinstance(task, dict)
        ],
    }
    return render_result(WriterResult(ok=True, path=_path(workspace, feature), data={"summary": summary}))


def _resolve(args: argparse.Namespace) -> tuple[Path, str]:
    return resolve_workspace(args.workspace), resolve_feature(args.feature)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace")
    parser.add_argument("--feature")


def _task_selector(parser: argparse.ArgumentParser) -> None:
    _common(parser)
    parser.add_argument("--task-id", required=True)


def _add_task_fields(parser: argparse.ArgumentParser, *, require_title: bool = True) -> None:
    parser.add_argument("--title", required=require_title)
    parser.add_argument("--goal", required=require_title)
    parser.add_argument("--status", default="todo")
    parser.add_argument("--dep", "--deps", dest="dep", action="append")
    parser.add_argument("--ui-required", action="store_true")
    parser.add_argument("--page-ref", action="append")
    parser.add_argument("--interaction-ref", action="append")
    parser.add_argument("--visual-source-ref", action="append")
    parser.add_argument("--frontend-route", default="spec-driven-ui")
    parser.add_argument("--module", action="append")
    parser.add_argument("--entrypoint", action="append")
    parser.add_argument("--page", action="append")
    parser.add_argument("--data-object", action="append")
    parser.add_argument("--scope-path", action="append")
    parser.add_argument("--implementation-point", action="append")
    parser.add_argument("--acceptance-criterion", action="append")
    parser.add_argument("--non-goal", action="append")
    parser.add_argument("--spec-ref", action="append")
    parser.add_argument("--design-ref", action="append")
    parser.add_argument("--api-id", action="append")
    parser.add_argument("--data-id", action="append")
    parser.add_argument("--decision-id", action="append")
    parser.add_argument("--validation-command", action="append")
    parser.add_argument("--expected-file", action="append")
    parser.add_argument("--split-rationale")


def _list_command(sub: argparse._SubParsersAction, name: str, field: str, *, remove: bool = False) -> None:
    parser = sub.add_parser(name)
    _task_selector(parser)
    parser.add_argument("value", nargs="+")
    parser.set_defaults(func=_cmd_list_field, field=field, remove=remove)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Incrementally write plan.json")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    _common(init)
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=_cmd_init)

    add_task = sub.add_parser("add-task")
    _common(add_task)
    add_task.add_argument("--task-id")
    add_task.add_argument("--body-file")
    add_task.add_argument("--task-json")
    add_task.add_argument("--body-stdin", action="store_true")
    _add_task_fields(add_task, require_title=False)
    add_task.set_defaults(func=_cmd_add_task)

    add_task_contract = sub.add_parser("add-task-contract")
    add_task_contract.set_defaults(func=_cmd_add_task_contract)

    update_task = sub.add_parser("update-task")
    _task_selector(update_task)
    update_task.add_argument("--title")
    update_task.add_argument("--goal")
    update_task.add_argument("--status")
    update_task.set_defaults(func=_cmd_update_task)

    detail = sub.add_parser("set-task-detail")
    _task_selector(detail)
    detail.add_argument("--from-json-file", required=True)
    detail.set_defaults(func=_cmd_set_task_detail)

    scope = sub.add_parser("set-scope")
    _task_selector(scope)
    scope.add_argument("--module", action="append")
    scope.add_argument("--entrypoint", action="append")
    scope.add_argument("--page", action="append")
    scope.add_argument("--data-object", action="append")
    scope.add_argument("--scope-path", action="append")
    scope.set_defaults(func=_cmd_set_scope)

    ui_required = sub.add_parser("set-ui-required")
    _task_selector(ui_required)
    ui_required.add_argument("required", choices=["true", "false"])
    ui_required.add_argument("--frontend-route", default="spec-driven-ui")
    ui_required.set_defaults(func=_cmd_set_ui_required)

    ui_refs = sub.add_parser("set-ui-refs")
    _task_selector(ui_refs)
    ui_refs.add_argument("--page-ref", action="append")
    ui_refs.add_argument("--interaction-ref", action="append")
    ui_refs.add_argument("--visual-source-ref", action="append")
    ui_refs.add_argument("--frontend-route", default="spec-driven-ui")
    ui_refs.set_defaults(func=_cmd_set_ui_refs)

    for name, field in (
        ("add-spec-ref", "specRefs"),
        ("remove-spec-ref", "specRefs"),
        ("add-api-id", "apiIds"),
        ("remove-api-id", "apiIds"),
        ("add-data-id", "dataIds"),
        ("remove-data-id", "dataIds"),
        ("add-design-ref", "designRefs"),
        ("remove-design-ref", "designRefs"),
        ("add-decision-id", "decisionIds"),
        ("remove-decision-id", "decisionIds"),
        ("add-implementation-point", "implementationPoints"),
        ("remove-implementation-point", "implementationPoints"),
        ("add-acceptance-criterion", "acceptanceCriteria"),
        ("remove-acceptance-criterion", "acceptanceCriteria"),
        ("add-non-goal", "nonGoals"),
        ("remove-non-goal", "nonGoals"),
        ("add-evidence-id", "evidenceIds"),
        ("remove-evidence-id", "evidenceIds"),
    ):
        _list_command(sub, name, field, remove=name.startswith("remove-"))

    deps = sub.add_parser("set-deps")
    _task_selector(deps)
    deps.add_argument("--dep", "--deps", dest="dep", action="append")
    deps.set_defaults(func=_cmd_set_deps)

    validation_command = sub.add_parser("add-validation-command")
    _task_selector(validation_command)
    validation_command.add_argument("--command-id")
    validation_command.add_argument("--command", required=True)
    validation_command.add_argument("--cwd", default=".")
    validation_command.add_argument(
        "--kind",
        choices=sorted(VALIDATION_KINDS),
        default="behavior_test",
    )
    validation_command.add_argument("--repo")
    validation_command.add_argument("--optional", action="store_true")
    validation_command.add_argument("--covers", action="append")
    validation_command.set_defaults(func=_cmd_add_validation_command)

    project_validation = sub.add_parser("add-project-validation-command")
    _common(project_validation)
    project_validation.add_argument("--command-id")
    project_validation.add_argument("--command", required=True)
    project_validation.add_argument("--cwd", default=".")
    project_validation.add_argument("--repo")
    project_validation.add_argument(
        "--kind",
        choices=["compile", "typecheck", "lint", "static_check"],
        default="compile",
    )
    project_validation.add_argument("--optional", action="store_true")
    project_validation.set_defaults(func=_cmd_add_project_validation_command)

    rationale = sub.add_parser("set-split-rationale")
    _task_selector(rationale)
    rationale.add_argument("--rationale", required=True)
    rationale.set_defaults(func=_cmd_set_split_rationale)

    status = sub.add_parser("set-status")
    _task_selector(status)
    status.add_argument("status")
    status.set_defaults(func=_cmd_set_status)

    blocker = sub.add_parser("add-blocker")
    _task_selector(blocker)
    blocker.add_argument("--blocker", required=True)
    blocker.set_defaults(func=_cmd_add_blocker)

    clear = sub.add_parser("clear-blockers")
    _task_selector(clear)
    clear.set_defaults(func=_cmd_clear_blockers)

    validate = sub.add_parser("validate")
    _common(validate)
    validate.add_argument("--structure", action="store_true")
    validate.add_argument("--gate", action="store_true")
    validate.add_argument("--initial", action="store_true")
    validate.add_argument("--done", action="store_true")
    validate.set_defaults(func=_cmd_validate)

    render_md = sub.add_parser("render-md")
    _common(render_md)
    render_md.set_defaults(func=_cmd_render_md)

    show = sub.add_parser("show")
    _common(show)
    show.add_argument("--summary", action="store_true")
    show.set_defaults(func=_cmd_show)

    args = parser.parse_args(argv)
    if args.command == "add-task-contract":
        return args.func(args)
    try:
        workspace, feature = _resolve(args)
        with _plan_lock(workspace, feature):
            return args.func(args)
    except PlanWriterInputError as exc:
        return render_result(fail(exc.reason, exc.detail))
    except Exception as exc:
        return render_result(fail("plan_writer_failed", str(exc)))


if __name__ == "__main__":
    raise SystemExit(main())
