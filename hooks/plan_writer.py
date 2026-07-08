#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Incrementally write plan.json and render PLAN.md."""

from __future__ import annotations

import argparse
import sys
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
    render_result,
    resolve_feature,
    resolve_workspace,
    string_list,
    with_result_data,
    write_text,
)
from hooks.plan_json import (  # noqa: E402
    TASK_DETAIL_VERSION,
    validate_plan_data,
)


PLAN_FILE = "plan.json"
PLAN_MD_FILE = "PLAN.md"
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


def _path(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, PLAN_FILE)


def _md_path(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, PLAN_MD_FILE)


def _initial(feature: str) -> dict[str, Any]:
    return {"version": 1, "taskDetailVersion": TASK_DETAIL_VERSION, "featureId": feature, "tasks": []}


def _load(workspace: Path, feature: str) -> dict[str, Any]:
    data = load_json(_path(workspace, feature), default=_initial(feature))
    if not isinstance(data, dict):
        raise ValueError("plan.json root 必须是 object")
    data.setdefault("version", 1)
    data.setdefault("taskDetailVersion", TASK_DETAIL_VERSION)
    data.setdefault("featureId", feature)
    data.setdefault("tasks", [])
    return data


def _structure_errors(data: dict[str, Any], *, allow_empty: bool = False) -> list[str]:
    if allow_empty and data.get("tasks") == []:
        errors: list[str] = []
        if data.get("version") != 1:
            errors.append("plan_json_invalid_version")
        if data.get("taskDetailVersion") != TASK_DETAIL_VERSION:
            errors.append("plan_json_invalid_task_detail_version")
        if not isinstance(data.get("featureId"), str) or not data.get("featureId"):
            errors.append("plan_json_missing_feature_id")
        return errors
    return validate_plan_data(data)


def _write(workspace: Path, feature: str, data: dict[str, Any], *, allow_empty: bool = False) -> WriterResult:
    path = _path(workspace, feature)
    errors = _structure_errors(data, allow_empty=allow_empty)
    if errors:
        return WriterResult(ok=False, path=path, errors=[{"reason": error} for error in errors])
    changed = atomic_write_json(path, data)
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
    if ui_required:
        task["uiRefs"] = {
            "pageRefs": _split_values(args.page_ref) or _split_values(args.page),
            "interactionRefs": _split_values(args.interaction_ref),
            "visualSourceRefs": _split_values(args.visual_source_ref),
            "frontendRoute": args.frontend_route,
        }
        task["scope"]["pages"] = list(task["uiRefs"]["pageRefs"])
    return task


def _cmd_init(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    existing = fail_if_artifact_exists(_path(workspace, feature), force=args.force)
    if existing:
        return render_result(existing)
    return render_result(with_result_data(_write(workspace, feature, _initial(feature), allow_empty=True), reset=bool(args.force)))


def _list_field_is_populated(task: dict[str, Any], field: str) -> bool:
    value = task.get(field)
    if not isinstance(value, list):
        return False
    if field == "validationCommands":
        return any(
            (isinstance(item, dict) and isinstance(item.get("command"), str) and item.get("command", "").strip())
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
    body_sources = [source for source in (args.body_file, args.task_json) if source]
    if len(body_sources) > 1:
        raise ValueError("--body-file 与 --task-json 只能二选一")
    if args.body_file:
        task = read_object_file(args.body_file)
    elif args.task_json:
        task = parse_json_value(args.task_json)
        if not isinstance(task, dict):
            raise ValueError("--task-json 顶层必须是 object")
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
        raise ValueError(f"invalid_plan_task_body: missing={','.join(missing)}")
    task["status"] = "todo"
    task.setdefault("deps", [])
    task.setdefault("uiRequired", False)
    task.setdefault("scope", {"modules": [], "entrypoints": [], "pages": [], "dataObjects": []})
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
    task.setdefault("blockers", [])
    return task


def _cmd_add_task(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    body_task = _task_from_body(args, data)
    if body_task is None:
        if not args.title or not args.goal:
            return render_result(fail("missing_plan_task_args", "--title/--goal 或 --body-file/--task-json 必填", path=_path(workspace, feature)))
        task_id = args.task_id or next_numbered_id(_ids(data), "T")
        task = _default_task(task_id, args)
    else:
        task = body_task
        task_id = str(task["id"])
    if task_id in _ids(data):
        return render_result(fail("duplicate_task_id", task_id, path=_path(workspace, feature)))
    _tasks(data).append(task)
    return render_result(_write(workspace, feature, data))


def _cmd_update_task(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    task = _find_task(data, args.task_id)
    for field in ("title", "goal", "status"):
        value = getattr(args, field)
        if value is not None:
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
    data = _load(workspace, feature)
    task = _find_task(data, args.task_id)
    current = task.get(args.field)
    if not isinstance(current, list):
        current = []
    items = _split_values(args.value)
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
    commands.append({"command": args.command})
    return render_result(_write(workspace, feature, data))


def _cmd_set_split_rationale(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    _find_task(data, args.task_id)["splitRationale"] = args.rationale
    return render_result(_write(workspace, feature, data))


def _cmd_set_status(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    _find_task(data, args.task_id)["status"] = args.status
    return render_result(_write(workspace, feature, data))


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
    data = load_json(path)
    errors = validate_plan_data(
        data,
        require_initial_status=args.initial,
        require_all_done=args.done,
        require_task_details=args.gate,
    )
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
            lines.append(f"  {index}. {criterion}")
        lines.append(f"- 非目标: {_fmt(task.get('nonGoals'))}")
        if task.get("splitRationale"):
            lines.append(f"- 合并理由: {task.get('splitRationale')}")
        commands = task.get("validationCommands", [])
        lines.append("- 验证命令:")
        if isinstance(commands, list) and commands:
            for command in commands:
                if isinstance(command, dict):
                    lines.append(f"  - {command.get('command', '')}")
        else:
            lines.append("  - -")
        lines.append(f"- 状态: {task.get('status', '')}")
        lines.append("")
    return "\n".join(lines)


def _cmd_render_md(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    errors = validate_plan_data(data)
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
    _add_task_fields(add_task, require_title=False)
    add_task.set_defaults(func=_cmd_add_task)

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
    validation_command.add_argument("--command", required=True)
    validation_command.set_defaults(func=_cmd_add_validation_command)

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
    try:
        return args.func(args)
    except Exception as exc:
        return render_result(fail("plan_writer_failed", str(exc)))


if __name__ == "__main__":
    raise SystemExit(main())
