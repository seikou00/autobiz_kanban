#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Machine-readable plan helpers for Autodev.

``PLAN.md`` remains the human-readable view. ``plan.json`` is the machine
fact source for task ids, dependencies, status, validation commands, and
evidence links.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TASK_ID_RE = re.compile(r"^T\d{3}$")
BATCH_ID_RE = re.compile(r"^B\d{3}$")
REQ_ID_RE = re.compile(r"\bREQ-\d{3}\b")
SCN_ID_RE = re.compile(r"\bSCN-\d{3}\b")
API_ID_RE = re.compile(r"^API-\d{3}$")
DATA_ID_RE = re.compile(r"^DATA-\d{3}$")
DECISION_ID_RE = re.compile(r"^D-\d{3}$")
EVIDENCE_ID_RE = re.compile(r"^ev_\d{4}$")
ACCEPTANCE_ID_RE = re.compile(r"^AC-T\d{3}-\d{2,3}$")
VALIDATION_ID_RE = re.compile(r"^VAL-T\d{3}-\d{2,3}$")
PROJECT_VALIDATION_ID_RE = re.compile(r"^PROJECT-VAL-\d{3}$")
REPOSITORY_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
PAGE_ID_RE = re.compile(r"^PAGE-\d{3}$")
INTERACTION_ID_RE = re.compile(r"^UIX-\d{3}$")
VISUAL_SOURCE_ID_RE = re.compile(r"^VIS-\d{3}$")
FRONTEND_ROUTES = {"none", "spec-driven-ui", "absolute-html", "standard-html", "missing-html"}
COMPLETION_POLICIES = {"all_required_validations_pass"}
VALIDATION_KINDS = {
    "behavior_test",
    "integration_test",
    "e2e_test",
    "static_check",
    "typecheck",
    "lint",
    "compile",
}
MAX_BATCH_TASKS = 5
BATCH_STRATEGY = "spec_capability_topological"
FEATURE_STATUSES = {"todo", "in_progress", "awaiting_next_conversation", "failed", "done"}
BATCH_STATUSES = {"todo", "in_progress", "failed", "done"}

TODO_STATUSES = {"todo", "pending", "not_started", "not-started", "待做", "未开始"}
IN_PROGRESS_STATUSES = {"in_progress", "in-progress", "doing", "进行中"}
DONE_STATUSES = {"done", "completed", "complete", "pass", "passed", "完成", "已完成"}
FAILED_STATUSES = {"failed", "fail", "blocked", "失败", "阻断"}
TASK_RUNTIME_FIELDS = {
    "status",
    "evidenceIds",
    "completionEvidenceIds",
    "latestPassEvidenceId",
}


class PlanJsonError(ValueError):
    """Raised when a plan.json file cannot be loaded or validated."""


@dataclass(frozen=True)
class PlanBundle:
    root: dict[str, Any]
    batches: dict[str, dict[str, Any]]
    tasks: list[dict[str, Any]]
    task_batches: dict[str, str]


def plan_json_path(target_feature_dir: Path) -> Path:
    return target_feature_dir / "plan.json"


def batch_plan_path(target_feature_dir: Path, batch_id: str) -> Path:
    if not BATCH_ID_RE.fullmatch(batch_id):
        raise PlanJsonError(f"invalid_batch_id:{batch_id}")
    return target_feature_dir / "plans" / batch_id / "plan.json"


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


def task_contract_sha256(task: dict[str, Any]) -> str:
    payload = {key: value for key, value in task.items() if key not in TASK_RUNTIME_FIELDS}
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


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
            if not item_re.fullmatch(value):
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


def _validate_tasks_container(
    data: Any,
    *,
    require_initial_status: bool = False,
    require_all_done: bool = False,
    require_task_details: bool = False,
    known_task_ids: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["plan_json_root_must_be_object"]

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

        _validate_task_details(errors, raw_task, task_id)

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
        if "mergedScenarioRefs" in raw_task:
            _validate_string_list(errors, raw_task, task_id, "mergedScenarioRefs", required=False)
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
        completion_evidence_ids = _validate_string_list(
            errors,
            raw_task,
            task_id,
            "completionEvidenceIds",
            required=False,
            item_re=EVIDENCE_ID_RE,
        )
        for completion_id in completion_evidence_ids:
            if completion_id not in evidence_ids:
                errors.append(f"{task_id}.completionEvidenceId_not_in_evidenceIds:{completion_id}")
        latest_pass = raw_task.get("latestPassEvidenceId")
        if latest_pass is not None and (
            not isinstance(latest_pass, str) or not EVIDENCE_ID_RE.fullmatch(latest_pass)
        ):
            errors.append(f"{task_id}.latestPassEvidenceId_invalid")
        if require_all_done:
            if not completion_evidence_ids:
                errors.append(f"{task_id}.completionEvidenceIds_missing")
            if not isinstance(latest_pass, str) or not latest_pass:
                errors.append(f"{task_id}.latestPassEvidenceId_missing")
            elif latest_pass not in completion_evidence_ids:
                errors.append(f"{task_id}.latestPassEvidenceId_not_completion_evidence:{latest_pass}")
            elif latest_pass != completion_evidence_ids[-1]:
                errors.append(f"{task_id}.latestPassEvidenceId_not_latest:{latest_pass}")
        _validate_string_list(errors, raw_task, task_id, "expectedFiles", required=False)
        blockers = _validate_string_list(errors, raw_task, task_id, "blockers", required=False)
        if require_all_done and blockers:
            errors.append(f"{task_id}.blockers_unresolved")

        ui_required = raw_task.get("uiRequired")
        is_ui_required = ui_required is True
        if ui_required is not None and not isinstance(ui_required, bool):
            errors.append(f"{task_id}.uiRequired_must_be_bool")
        ui_refs = raw_task.get("uiRefs")
        if ui_refs is None:
            if is_ui_required:
                errors.append(f"{task_id}.uiRefs_missing")
        elif not isinstance(ui_refs, dict):
            errors.append(f"{task_id}.uiRefs_must_be_object")
        else:
            for field, item_re in (
                ("pageRefs", PAGE_ID_RE),
                ("interactionRefs", INTERACTION_ID_RE),
                ("visualSourceRefs", VISUAL_SOURCE_ID_RE),
            ):
                if field not in ui_refs and is_ui_required:
                    errors.append(f"{task_id}.uiRefs.{field}_missing")
                elif field in ui_refs:
                    _validate_string_list(errors, ui_refs, task_id, field, required=False, item_re=item_re)
            frontend_route = ui_refs.get("frontendRoute")
            if frontend_route is None and is_ui_required:
                errors.append(f"{task_id}.uiRefs.frontendRoute_missing")
            elif frontend_route is not None and (
                not isinstance(frontend_route, str) or frontend_route not in FRONTEND_ROUTES
            ):
                errors.append(f"{task_id}.uiRefs.frontendRoute_invalid")

        commands = raw_task.get("validationCommands")
        if not isinstance(commands, list):
            errors.append(f"{task_id}.validationCommands_must_be_array")
        elif not commands:
            errors.append(f"{task_id}.validationCommands_missing")
        else:
            required_coverage: set[str] = set()
            for command_index, command in enumerate(commands):
                if not isinstance(command, dict):
                    errors.append(f"{task_id}.validationCommands[{command_index}]_must_be_object")
                    continue
                _validate_validation_command(
                    errors,
                    command,
                    task_id=task_id,
                    command_index=command_index,
                    acceptance_ids=_acceptance_ids(raw_task),
                )
                if command.get("required") is True:
                    required_coverage.update(
                        item for item in (command.get("covers") or []) if isinstance(item, str)
                    )
            for criterion_id in sorted(_acceptance_ids(raw_task) - required_coverage):
                errors.append(f"{task_id}.acceptanceCriteria_uncovered:{criterion_id}")

    known_ids = known_task_ids or {task_id for task_id in task_ids if TASK_ID_RE.match(task_id)}
    for task_id, deps in deps_by_task.items():
        for dep in deps:
            if dep not in known_ids:
                errors.append(f"{task_id}.dependency_unknown:{dep}")
    errors.extend(_dag_errors(deps_by_task))
    return errors


def validate_plan_data(
    data: Any,
    *,
    require_initial_status: bool = False,
    require_all_done: bool = False,
    require_task_details: bool = False,
) -> list[str]:
    """Validate the root batch index. Task contracts live in batch plans."""

    del require_task_details
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["plan_json_root_must_be_object"]
    if "tasks" in data:
        errors.append("monolithic_plan_requires_rebuild")
    if "version" in data or "taskDetailVersion" in data:
        errors.append("legacy_plan_requires_rebuild")
    feature_id = data.get("featureId")
    if not isinstance(feature_id, str) or not feature_id.strip():
        errors.append("plan_json_missing_feature_id")

    status = data.get("status")
    if status not in FEATURE_STATUSES:
        errors.append("plan_json_status_invalid")
    elif require_initial_status and status != "todo":
        errors.append("plan_json_status_not_initial")
    elif require_all_done and status != "done":
        errors.append("plan_json_status_not_done")

    policy = data.get("batchPolicy")
    if not isinstance(policy, dict):
        errors.append("plan_json_batchPolicy_missing")
    else:
        if policy.get("maxTasks") != MAX_BATCH_TASKS:
            errors.append(f"plan_json_batchPolicy_maxTasks_must_be:{MAX_BATCH_TASKS}")
        if policy.get("strategy") != BATCH_STRATEGY:
            errors.append(f"plan_json_batchPolicy_strategy_must_be:{BATCH_STRATEGY}")

    raw_batches = data.get("batches")
    batch_ids: list[str] = []
    if not isinstance(raw_batches, list) or not raw_batches:
        errors.append("plan_json_missing_batches")
    else:
        for index, entry in enumerate(raw_batches):
            context = f"batches[{index}]"
            if not isinstance(entry, dict):
                errors.append(f"{context}_must_be_object")
                continue
            batch_id = entry.get("id")
            if not isinstance(batch_id, str) or not BATCH_ID_RE.fullmatch(batch_id):
                errors.append(f"{context}.id_invalid")
                continue
            if batch_id in batch_ids:
                errors.append(f"duplicate_batch_id:{batch_id}")
            batch_ids.append(batch_id)
            expected_path = f"plans/{batch_id}/plan.json"
            if entry.get("path") != expected_path:
                errors.append(f"{batch_id}.path_invalid")
            if not isinstance(entry.get("title"), str) or not str(entry.get("title")).strip():
                errors.append(f"{batch_id}.title_missing")
            _validate_string_list(errors, entry, batch_id, "specRoots", required=True)
            _validate_string_list(errors, entry, batch_id, "deps", required=False, item_re=BATCH_ID_RE)
            _validate_string_list(errors, entry, batch_id, "taskIds", required=True, item_re=TASK_ID_RE)
            batch_status = entry.get("status")
            if batch_status not in BATCH_STATUSES:
                errors.append(f"{batch_id}.status_invalid")
            elif require_initial_status and batch_status != "todo":
                errors.append(f"{batch_id}.status_not_initial")
            elif require_all_done and batch_status != "done":
                errors.append(f"{batch_id}.status_not_done")

    known_batches = set(batch_ids)
    for field in ("activeBatchId", "nextBatchId"):
        value = data.get(field)
        if value is not None and (not isinstance(value, str) or value not in known_batches):
            errors.append(f"plan_json_{field}_invalid")
    if require_all_done and (data.get("activeBatchId") is not None or data.get("nextBatchId") is not None):
        errors.append("plan_json_done_with_pending_batch_pointer")

    _validate_project_commands(errors, data, require_all_done=require_all_done)
    return errors


def validate_batch_plan_data(
    data: Any,
    *,
    expected_feature_id: str | None = None,
    expected_batch_id: str | None = None,
    known_task_ids: set[str] | None = None,
    require_initial_status: bool = False,
    require_all_done: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["batch_plan_root_must_be_object"]
    if "version" in data or "taskDetailVersion" in data:
        errors.append("legacy_plan_requires_rebuild")
    feature_id = data.get("featureId")
    if not isinstance(feature_id, str) or not feature_id.strip():
        errors.append("batch_plan_missing_feature_id")
    elif expected_feature_id is not None and feature_id != expected_feature_id:
        errors.append(f"batch_plan_feature_mismatch:{feature_id}")
    batch_id = data.get("batchId")
    if not isinstance(batch_id, str) or not BATCH_ID_RE.fullmatch(batch_id):
        errors.append("batch_plan_id_invalid")
        batch_id = "batch"
    elif expected_batch_id is not None and batch_id != expected_batch_id:
        errors.append(f"batch_plan_id_mismatch:{batch_id}")
    if not isinstance(data.get("title"), str) or not str(data.get("title")).strip():
        errors.append(f"{batch_id}.title_missing")
    status = data.get("status")
    if status not in BATCH_STATUSES:
        errors.append(f"{batch_id}.status_invalid")
    elif require_initial_status and status != "todo":
        errors.append(f"{batch_id}.status_not_initial")
    elif require_all_done and status != "done":
        errors.append(f"{batch_id}.status_not_done")

    batch_tasks = tasks(data)
    if len(batch_tasks) > MAX_BATCH_TASKS:
        errors.append(f"{batch_id}.batch_task_limit_exceeded:{len(batch_tasks)}>{MAX_BATCH_TASKS}")
    if data.get("taskCount") != len(batch_tasks):
        errors.append(f"{batch_id}.taskCount_mismatch")
    completed_count = sum(normalize_status(item.get("status")) == "done" for item in batch_tasks)
    if data.get("completedTaskCount") != completed_count:
        errors.append(f"{batch_id}.completedTaskCount_mismatch")
    _validate_string_list(errors, data, str(batch_id), "completionEvidenceIds", required=False, item_re=EVIDENCE_ID_RE)
    for field in ("startedAt", "completedAt"):
        value = data.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(f"{batch_id}.{field}_invalid")
    errors.extend(
        _validate_tasks_container(
            data,
            require_initial_status=require_initial_status,
            require_all_done=require_all_done,
            require_task_details=True,
            known_task_ids=known_task_ids,
        )
    )
    return errors


def validate_task_collection(
    feature_id: str,
    task_items: list[dict[str, Any]],
    *,
    require_initial_status: bool = False,
    require_all_done: bool = False,
) -> list[str]:
    """Validate task contracts before they are projected into batch files."""

    known = {
        str(item.get("id"))
        for item in task_items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    return _validate_tasks_container(
        {"featureId": feature_id, "tasks": task_items},
        require_initial_status=require_initial_status,
        require_all_done=require_all_done,
        require_task_details=True,
        known_task_ids=known,
    )


def _acceptance_ids(task: dict[str, Any]) -> set[str]:
    values = task.get("acceptanceCriteria")
    if not isinstance(values, list):
        return set()
    return {
        str(item.get("id"))
        for item in values
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _validate_acceptance_criteria(errors: list[str], task: dict[str, Any], task_id: str) -> None:
    criteria = task.get("acceptanceCriteria")
    if not isinstance(criteria, list):
        errors.append(f"{task_id}.acceptanceCriteria_must_be_array")
        return
    if not criteria:
        errors.append(f"{task_id}.acceptanceCriteria_missing")
        return
    seen: set[str] = set()
    task_scenario_ids = {
        match.group(0)
        for ref in (_string_list(task.get("specRefs")) or [])
        for match in SCN_ID_RE.finditer(ref)
    }
    for index, criterion in enumerate(criteria):
        context = f"{task_id}.acceptanceCriteria[{index}]"
        if not isinstance(criterion, dict):
            errors.append(f"{context}_must_be_object")
            continue
        criterion_id = criterion.get("id")
        if not isinstance(criterion_id, str) or not ACCEPTANCE_ID_RE.fullmatch(criterion_id):
            errors.append(f"{context}.id_invalid")
        elif not criterion_id.startswith(f"AC-{task_id}-"):
            errors.append(f"{context}.id_task_mismatch:{criterion_id}")
        elif criterion_id in seen:
            errors.append(f"{task_id}.acceptanceCriteria_duplicate:{criterion_id}")
        else:
            seen.add(criterion_id)
        text = criterion.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{context}.text_missing")
        scenario_refs = _string_list(criterion.get("scenarioRefs"))
        if scenario_refs is None:
            errors.append(f"{context}.scenarioRefs_must_be_string_array")
        elif not scenario_refs or not all(SCN_ID_RE.search(ref) for ref in scenario_refs):
            errors.append(f"{context}.scenarioRefs_missing_scenario_id")
        else:
            criterion_scenario_ids = {
                match.group(0)
                for ref in scenario_refs
                for match in SCN_ID_RE.finditer(ref)
            }
            for scenario_id in sorted(criterion_scenario_ids - task_scenario_ids):
                errors.append(f"{context}.scenario_not_in_task_specRefs:{scenario_id}")


def _validate_validation_command(
    errors: list[str],
    command: dict[str, Any],
    *,
    task_id: str,
    command_index: int,
    acceptance_ids: set[str],
) -> None:
    context = f"{task_id}.validationCommands[{command_index}]"
    command_id = command.get("id")
    if not isinstance(command_id, str) or not VALIDATION_ID_RE.fullmatch(command_id):
        errors.append(f"{context}.id_invalid")
    elif not command_id.startswith(f"VAL-{task_id}-"):
        errors.append(f"{context}.id_task_mismatch:{command_id}")
    argv = _string_list(command.get("argv"))
    if argv is None or not argv:
        errors.append(f"{context}.argv_missing")
    cwd = command.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip() or Path(cwd).is_absolute() or ".." in Path(cwd).parts:
        errors.append(f"{context}.cwd_invalid")
    kind = command.get("kind")
    if kind not in VALIDATION_KINDS:
        errors.append(f"{context}.kind_invalid")
    if not isinstance(command.get("required"), bool):
        errors.append(f"{context}.required_must_be_bool")
    repository = command.get("repo")
    if repository is not None and (
        not isinstance(repository, str) or not REPOSITORY_ID_RE.fullmatch(repository)
    ):
        errors.append(f"{context}.repo_invalid")
    covers = _string_list(command.get("covers"))
    if covers is None:
        errors.append(f"{context}.covers_must_be_string_array")
    else:
        if kind == "compile" and covers:
            errors.append(f"{context}.compile_cannot_cover_acceptance")
        for criterion_id in covers:
            if criterion_id not in acceptance_ids:
                errors.append(f"{context}.covers_unknown:{criterion_id}")


def _validate_project_commands(
    errors: list[str],
    data: dict[str, Any],
    *,
    require_all_done: bool,
) -> None:
    commands = data.get("projectValidationCommands")
    if not isinstance(commands, list):
        errors.append("projectValidationCommands_must_be_array")
        return
    if not commands and require_all_done:
        errors.append("projectValidationCommands_missing")
    seen: set[str] = set()
    for index, command in enumerate(commands):
        context = f"projectValidationCommands[{index}]"
        if not isinstance(command, dict):
            errors.append(f"{context}_must_be_object")
            continue
        command_id = command.get("id")
        if not isinstance(command_id, str) or not PROJECT_VALIDATION_ID_RE.fullmatch(command_id):
            errors.append(f"{context}.id_invalid")
        elif command_id in seen:
            errors.append(f"projectValidationCommands_duplicate:{command_id}")
        else:
            seen.add(command_id)
        argv = _string_list(command.get("argv"))
        if argv is None or not argv:
            errors.append(f"{context}.argv_missing")
        cwd = command.get("cwd")
        if not isinstance(cwd, str) or not cwd.strip() or Path(cwd).is_absolute() or ".." in Path(cwd).parts:
            errors.append(f"{context}.cwd_invalid")
        if command.get("kind") not in {"compile", "typecheck", "lint", "static_check"}:
            errors.append(f"{context}.kind_invalid")
        if not isinstance(command.get("required"), bool):
            errors.append(f"{context}.required_must_be_bool")
        repository = command.get("repo")
        if repository is not None and (
            not isinstance(repository, str) or not REPOSITORY_ID_RE.fullmatch(repository)
        ):
            errors.append(f"{context}.repo_invalid")
    project_evidence_ids = _validate_string_list(
        errors,
        data,
        "plan",
        "projectCheckEvidenceIds",
        required=False,
        item_re=EVIDENCE_ID_RE,
    )
    latest = data.get("latestProjectCheckEvidenceId")
    if latest is not None and (not isinstance(latest, str) or not EVIDENCE_ID_RE.fullmatch(latest)):
        errors.append("latestProjectCheckEvidenceId_invalid")
    elif isinstance(latest, str):
        if latest not in project_evidence_ids:
            errors.append(f"latestProjectCheckEvidenceId_not_in_history:{latest}")
        elif latest != project_evidence_ids[-1]:
            errors.append(f"latestProjectCheckEvidenceId_not_latest:{latest}")


def _validate_task_details(
    errors: list[str],
    task: dict[str, Any],
    task_id: str,
) -> None:
    goal = task.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        errors.append(f"{task_id}.goal_missing")

    scope = task.get("scope")
    scope_pages: list[str] = []
    if not isinstance(scope, dict):
        errors.append(f"{task_id}.scope_must_be_object")
    else:
        for field in ("modules", "entrypoints", "pages", "dataObjects", "paths"):
            values = _string_list(scope.get(field))
            if field == "paths" and values is None:
                continue
            if values is None:
                errors.append(f"{task_id}.scope.{field}_must_be_string_array")
                continue
            if field == "paths":
                for value in values:
                    if Path(value).is_absolute() or ".." in Path(value).parts:
                        errors.append(f"{task_id}.scope.paths_invalid:{value}")
            if field == "pages":
                scope_pages = values
                for value in values:
                    if not PAGE_ID_RE.fullmatch(value):
                        errors.append(f"{task_id}.scope.pages_invalid:{value}")

    implementation_points = _string_list(task.get("implementationPoints"))
    if implementation_points is None:
        errors.append(f"{task_id}.implementationPoints_must_be_string_array")
    elif len(implementation_points) < 2:
        errors.append(f"{task_id}.implementationPoints_too_few")
    elif len(implementation_points) > 6:
        errors.append(f"{task_id}.implementationPoints_too_many")

    _validate_acceptance_criteria(errors, task, task_id)
    if task.get("completionPolicy") not in COMPLETION_POLICIES:
        errors.append(f"{task_id}.completionPolicy_invalid")

    non_goals = _string_list(task.get("nonGoals"))
    if non_goals is None:
        errors.append(f"{task_id}.nonGoals_must_be_string_array")
        non_goals = []

    api_ids = _string_list(task.get("apiIds")) or []
    ui_required = task.get("uiRequired") is True
    if (ui_required or api_ids) and not non_goals:
        errors.append(f"{task_id}.nonGoals_missing")

    ui_refs = task.get("uiRefs")
    page_refs: list[str] = []
    if isinstance(ui_refs, dict):
        page_refs = _string_list(ui_refs.get("pageRefs")) or []
    if ui_required and sorted(scope_pages) != sorted(page_refs):
        errors.append(f"{task_id}.scope.pages_mismatch_uiRefs")


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
    bundle_tasks = data.get("_bundleTasks")
    if isinstance(bundle_tasks, list):
        return [task for task in bundle_tasks if isinstance(task, dict)]
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


def _bundle_consistency_errors(
    root: dict[str, Any],
    batch_data: dict[str, dict[str, Any]],
    *,
    require_initial_status: bool = False,
    require_all_done: bool = False,
) -> list[str]:
    entries = [entry for entry in root.get("batches", []) if isinstance(entry, dict)]
    all_tasks: list[dict[str, Any]] = []
    task_batches: dict[str, str] = {}
    errors: list[str] = []
    for entry in entries:
        batch_id = str(entry.get("id", ""))
        data = batch_data.get(batch_id)
        if not isinstance(data, dict):
            errors.append(f"missing_batch_plan:{batch_id}")
            continue
        for item in tasks(data):
            task_id = item.get("id")
            if not isinstance(task_id, str):
                continue
            if task_id in task_batches:
                errors.append(f"duplicate_task_id:{task_id}")
            else:
                task_batches[task_id] = batch_id
                all_tasks.append(item)
    if errors:
        return errors

    known_task_ids = set(task_batches)
    batch_order = {str(entry.get("id")): index for index, entry in enumerate(entries)}
    task_by_id = {str(item.get("id")): item for item in all_tasks}
    deps_by_task: dict[str, list[str]] = {}
    for entry in entries:
        batch_id = str(entry.get("id"))
        data = batch_data[batch_id]
        errors.extend(
            validate_batch_plan_data(
                data,
                expected_feature_id=str(root.get("featureId")),
                expected_batch_id=batch_id,
                known_task_ids=known_task_ids,
                require_initial_status=require_initial_status,
                require_all_done=require_all_done,
            )
        )
        actual_ids = [str(item.get("id")) for item in tasks(data)]
        if entry.get("taskIds") != actual_ids:
            errors.append(f"{batch_id}.taskIds_mismatch")
        if entry.get("status") != data.get("status"):
            errors.append(f"{batch_id}.root_status_projection_mismatch")

        declared_batch_deps = set(entry.get("deps") or [])
        for dep_batch in declared_batch_deps:
            if dep_batch not in batch_order:
                errors.append(f"{batch_id}.dependency_unknown:{dep_batch}")
            elif batch_order[dep_batch] >= batch_order[batch_id]:
                errors.append(f"{batch_id}.dependency_not_earlier:{dep_batch}")
        required_batch_deps: set[str] = set()
        for item in tasks(data):
            task_id = str(item.get("id"))
            deps = [dep for dep in item.get("deps", []) if isinstance(dep, str)]
            deps_by_task[task_id] = deps
            for dep in deps:
                dep_batch = task_batches.get(dep)
                if dep_batch is None:
                    continue
                if batch_order[dep_batch] > batch_order[batch_id]:
                    errors.append(f"{task_id}.dependency_not_in_earlier_batch:{dep}")
                elif dep_batch != batch_id:
                    required_batch_deps.add(dep_batch)
        for dep_batch in sorted(required_batch_deps - declared_batch_deps):
            errors.append(f"{batch_id}.missing_batch_dependency:{dep_batch}")

    for task_id, item in task_by_id.items():
        for dep in item.get("deps", []):
            if isinstance(dep, str) and dep not in known_task_ids:
                errors.append(f"{task_id}.dependency_unknown:{dep}")
    errors.extend(_dag_errors(deps_by_task))

    active = root.get("activeBatchId")
    next_batch = root.get("nextBatchId")
    status = root.get("status")
    if status == "awaiting_next_conversation":
        if active is not None:
            errors.append("awaiting_next_conversation_has_active_batch")
        if next_batch is None:
            errors.append("awaiting_next_conversation_missing_next_batch")
    if status == "done" and (active is not None or next_batch is not None):
        errors.append("done_plan_has_pending_batch_pointer")
    if isinstance(active, str):
        active_entry = next((entry for entry in entries if entry.get("id") == active), None)
        if isinstance(active_entry, dict) and active_entry.get("status") == "done":
            errors.append(f"active_batch_already_done:{active}")
    if isinstance(next_batch, str):
        next_entry = next((entry for entry in entries if entry.get("id") == next_batch), None)
        if isinstance(next_entry, dict) and next_entry.get("status") != "todo":
            errors.append(f"next_batch_not_todo:{next_batch}")
    return errors


def validate_plan_bundle_data(
    root: dict[str, Any],
    batch_data: dict[str, dict[str, Any]],
    *,
    require_initial_status: bool = False,
    require_all_done: bool = False,
) -> list[str]:
    errors = validate_plan_data(
        root,
        require_initial_status=require_initial_status,
        require_all_done=require_all_done,
    )
    if errors:
        return errors
    return _bundle_consistency_errors(
        root,
        batch_data,
        require_initial_status=require_initial_status,
        require_all_done=require_all_done,
    )


def load_plan_bundle(
    target_feature_dir: Path,
    *,
    require_initial_status: bool = False,
    require_all_done: bool = False,
    require_task_details: bool = False,
) -> PlanBundle:
    del require_task_details
    root = load_plan(plan_json_path(target_feature_dir))
    root_errors = validate_plan_data(
        root,
        require_initial_status=require_initial_status,
        require_all_done=require_all_done,
    )
    if root_errors:
        raise PlanJsonError(";".join(root_errors))

    entries = [entry for entry in root.get("batches", []) if isinstance(entry, dict)]
    batch_data: dict[str, dict[str, Any]] = {}
    all_tasks: list[dict[str, Any]] = []
    task_batches: dict[str, str] = {}
    load_errors: list[str] = []
    for entry in entries:
        batch_id = str(entry.get("id", ""))
        path = batch_plan_path(target_feature_dir, batch_id)
        try:
            data = load_plan(path)
        except PlanJsonError as exc:
            load_errors.append(str(exc))
            continue
        batch_data[batch_id] = data
        for item in tasks(data):
            task_id = item.get("id")
            if not isinstance(task_id, str):
                continue
            if task_id in task_batches:
                load_errors.append(f"duplicate_task_id:{task_id}")
            else:
                task_batches[task_id] = batch_id
                all_tasks.append(item)
    if load_errors:
        raise PlanJsonError(";".join(load_errors))

    errors = _bundle_consistency_errors(
        root,
        batch_data,
        require_initial_status=require_initial_status,
        require_all_done=require_all_done,
    )
    if errors:
        raise PlanJsonError(";".join(errors))
    return PlanBundle(root=root, batches=batch_data, tasks=all_tasks, task_batches=task_batches)


def find_task(bundle: PlanBundle, task_id: str) -> tuple[str, dict[str, Any]]:
    batch_id = bundle.task_batches.get(task_id)
    if batch_id is None:
        raise PlanJsonError(f"task_not_found:{task_id}")
    for item in tasks(bundle.batches[batch_id]):
        if item.get("id") == task_id:
            return batch_id, item
    raise PlanJsonError(f"task_not_found:{task_id}")


def bundle_unfinished_tasks(bundle: PlanBundle) -> list[str]:
    return [str(item.get("id")) for item in bundle.tasks if normalize_status(item.get("status")) != "done"]


def bundle_failed_tasks(bundle: PlanBundle) -> list[str]:
    return [str(item.get("id")) for item in bundle.tasks if normalize_status(item.get("status")) == "failed"]


def bundle_blocked_tasks(bundle: PlanBundle) -> list[str]:
    return [
        str(item.get("id"))
        for item in bundle.tasks
        if isinstance(item.get("blockers"), list) and any(str(value).strip() for value in item["blockers"])
    ]


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
    errors = validate_plan_data(data, **kwargs)
    if errors:
        return data, errors
    try:
        bundle = load_plan_bundle(path.parent, **kwargs)
    except PlanJsonError as exc:
        return data, str(exc).split(";")
    view = dict(data)
    view["_bundleTasks"] = bundle.tasks
    view["_bundleTaskBatches"] = bundle.task_batches
    view["tasks"] = bundle.tasks
    return view, []


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
