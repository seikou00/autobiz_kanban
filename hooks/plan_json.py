#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Machine-readable plan helpers for Autodev.

``PLAN.md`` remains the human-readable view. ``plan.json`` is the machine
fact source for task ids, dependencies, status, validation commands, and
evidence links.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PLAN_VERSION = 1
TASK_ID_RE = re.compile(r"^T\d{3}$")
REQ_ID_RE = re.compile(r"\bREQ-\d{3}\b")
SCN_ID_RE = re.compile(r"\bSCN-\d{3}\b")
API_ID_RE = re.compile(r"^API-\d{3}$")
DATA_ID_RE = re.compile(r"^DATA-\d{3}$")
DECISION_ID_RE = re.compile(r"^D-\d{3}$")
EVIDENCE_ID_RE = re.compile(r"^ev_\d{4}$")

TODO_STATUSES = {"todo", "pending", "not_started", "not-started", "待做", "未开始"}
IN_PROGRESS_STATUSES = {"in_progress", "in-progress", "doing", "进行中"}
DONE_STATUSES = {"done", "completed", "complete", "pass", "passed", "完成", "已完成"}
FAILED_STATUSES = {"failed", "fail", "blocked", "失败", "阻断"}


class PlanJsonError(ValueError):
    """Raised when a plan.json file cannot be loaded or validated."""


def plan_json_path(target_feature_dir: Path) -> Path:
    return target_feature_dir / "plan.json"


def normalize_status(status: Any) -> str:
    if not isinstance(status, str):
        return ""
    raw = status.strip()
    lowered = raw.lower().replace(" ", "_")
    if raw in TODO_STATUSES or lowered in TODO_STATUSES:
        return "todo"
    if raw in IN_PROGRESS_STATUSES or lowered in IN_PROGRESS_STATUSES:
        return "in_progress"
    if raw in DONE_STATUSES or lowered in DONE_STATUSES:
        return "done"
    if raw in FAILED_STATUSES or lowered in FAILED_STATUSES:
        return "failed"
    return ""


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        stripped = item.strip()
        if stripped:
            result.append(stripped)
    return result


def _validate_string_list(
    errors: list[str],
    task: dict[str, Any],
    task_id: str,
    field: str,
    *,
    required: bool = True,
    item_re: re.Pattern[str] | None = None,
) -> list[str]:
    values = _string_list(task.get(field))
    if values is None:
        errors.append(f"{task_id}.{field}_must_be_string_array")
        return []
    if required and not values:
        errors.append(f"{task_id}.{field}_missing")
    if item_re is not None:
        for value in values:
            if not item_re.match(value):
                errors.append(f"{task_id}.{field}_invalid:{value}")
    return values


def load_plan(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise PlanJsonError(f"missing_plan_json:{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PlanJsonError(f"invalid_plan_json:{path}:{exc}") from exc
    if not isinstance(data, dict):
        raise PlanJsonError(f"invalid_plan_json_root:{path}")
    return data


def validate_plan_data(
    data: Any,
    *,
    require_initial_status: bool = False,
    require_all_done: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["plan_json_root_must_be_object"]

    if data.get("version") != PLAN_VERSION:
        errors.append("plan_json_invalid_version")
    feature_id = data.get("featureId")
    if not isinstance(feature_id, str) or not feature_id.strip():
        errors.append("plan_json_missing_feature_id")

    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        errors.append("plan_json_missing_tasks")
        return errors

    task_ids: list[str] = []
    deps_by_task: dict[str, list[str]] = {}
    for index, raw_task in enumerate(tasks):
        if not isinstance(raw_task, dict):
            errors.append(f"tasks[{index}]_must_be_object")
            continue
        task_id = raw_task.get("id")
        if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
            errors.append(f"tasks[{index}].id_invalid")
            task_id = f"tasks[{index}]"
        elif task_id in task_ids:
            errors.append(f"duplicate_task_id:{task_id}")
        task_ids.append(task_id)

        title = raw_task.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append(f"{task_id}.title_missing")

        status = normalize_status(raw_task.get("status"))
        if not status:
            errors.append(f"{task_id}.status_invalid")
        elif require_initial_status and status != "todo":
            errors.append(f"{task_id}.status_not_initial")
        elif require_all_done and status != "done":
            errors.append(f"{task_id}.status_not_done")

        deps = _validate_string_list(errors, raw_task, task_id, "deps", required=False, item_re=TASK_ID_RE)
        deps_by_task[task_id] = deps
        if task_id in deps:
            errors.append(f"{task_id}.dependency_self_cycle")

        spec_refs = _validate_string_list(errors, raw_task, task_id, "specRefs")
        if spec_refs and not any(REQ_ID_RE.search(ref) for ref in spec_refs):
            errors.append(f"{task_id}.specRefs_missing_requirement_id")
        if spec_refs and not any(SCN_ID_RE.search(ref) for ref in spec_refs):
            errors.append(f"{task_id}.specRefs_missing_scenario_id")

        _validate_string_list(errors, raw_task, task_id, "designRefs", required=False)
        _validate_string_list(errors, raw_task, task_id, "apiIds", required=False, item_re=API_ID_RE)
        _validate_string_list(errors, raw_task, task_id, "dataIds", required=False, item_re=DATA_ID_RE)
        _validate_string_list(errors, raw_task, task_id, "decisionIds", item_re=DECISION_ID_RE)
        evidence_ids = _validate_string_list(
            errors,
            raw_task,
            task_id,
            "evidenceIds",
            required=False,
            item_re=EVIDENCE_ID_RE,
        )
        if require_all_done and not evidence_ids:
            errors.append(f"{task_id}.evidenceIds_missing")
        _validate_string_list(errors, raw_task, task_id, "expectedFiles", required=False)
        blockers = _validate_string_list(errors, raw_task, task_id, "blockers", required=False)
        if require_all_done and blockers:
            errors.append(f"{task_id}.blockers_unresolved")

        commands = raw_task.get("validationCommands")
        if not isinstance(commands, list):
            errors.append(f"{task_id}.validationCommands_must_be_array")
        elif not commands:
            errors.append(f"{task_id}.validationCommands_missing")
        else:
            for command_index, command in enumerate(commands):
                if not isinstance(command, dict):
                    errors.append(f"{task_id}.validationCommands[{command_index}]_must_be_object")
                    continue
                raw_command = command.get("command")
                if not isinstance(raw_command, str) or not raw_command.strip():
                    errors.append(f"{task_id}.validationCommands[{command_index}].command_missing")

    known_ids = {task_id for task_id in task_ids if TASK_ID_RE.match(task_id)}
    for task_id, deps in deps_by_task.items():
        for dep in deps:
            if dep not in known_ids:
                errors.append(f"{task_id}.dependency_unknown:{dep}")
    errors.extend(_dag_errors(deps_by_task))
    return errors


def _dag_errors(deps_by_task: dict[str, list[str]]) -> list[str]:
    errors: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str, stack: list[str]) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            errors.append("task_dependency_cycle:" + "->".join([*stack, task_id]))
            return
        visiting.add(task_id)
        for dep in deps_by_task.get(task_id, []):
            if dep in deps_by_task:
                visit(dep, [*stack, task_id])
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in deps_by_task:
        visit(task_id, [])
    return errors


def tasks(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_tasks = data.get("tasks")
    return [task for task in raw_tasks if isinstance(task, dict)] if isinstance(raw_tasks, list) else []


def task_ids(data: dict[str, Any]) -> set[str]:
    return {task["id"] for task in tasks(data) if isinstance(task.get("id"), str)}


def unfinished_tasks(data: dict[str, Any]) -> list[str]:
    return [
        str(task.get("id", ""))
        for task in tasks(data)
        if normalize_status(task.get("status")) != "done"
    ]


def failed_tasks(data: dict[str, Any]) -> list[str]:
    return [
        str(task.get("id", ""))
        for task in tasks(data)
        if normalize_status(task.get("status")) == "failed"
    ]


def blocked_tasks(data: dict[str, Any]) -> list[str]:
    blocked: list[str] = []
    for task in tasks(data):
        blockers = task.get("blockers")
        if isinstance(blockers, list) and any(str(item).strip() for item in blockers):
            blocked.append(str(task.get("id", "")))
    return blocked


def write_plan_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def load_and_validate_plan(path: Path, **kwargs: Any) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        data = load_plan(path)
    except PlanJsonError as exc:
        return None, [str(exc)]
    return data, validate_plan_data(data, **kwargs)


def _cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path).resolve()
    _, errors = load_and_validate_plan(
        path,
        require_initial_status=args.initial,
        require_all_done=args.done,
    )
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"PLAN_JSON_PASS path={path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Autodev plan.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("path")
    validate.add_argument("--initial", action="store_true")
    validate.add_argument("--done", action="store_true")
    validate.set_defaults(func=_cmd_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
