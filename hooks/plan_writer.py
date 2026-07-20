#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Incrementally write plan.json and render PLAN.md."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
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
    require_finalized_plan,
    resolve_feature,
    resolve_workspace,
    string_list,
    with_result_data,
    write_text,
    WriterError,
)
from hooks.evidence_kernel import FileLock  # noqa: E402
from hooks.plan_json import (  # noqa: E402
    BATCH_VALIDATION_KINDS,
    BATCH_STRATEGY,
    EXECUTION_LANES,
    FRONTEND_ROUTES,
    MAX_BATCH_TASKS,
    PROJECT_VALIDATION_KINDS,
    TASK_VALIDATION_KINDS,
    VISUAL_SOURCE_ID_RE,
    batch_plan_path,
    deferred_task_validation_enabled,
    load_plan_bundle,
    normalize_status,
    task_execution_lane,
    task_contract_sha256,
    task_covered_command_ids,
    task_set_digest,
    task_workspace_roots,
    validation_command_manifest_names,
    validate_plan_bundle_data,
    validate_task_collection,
)
from hooks.plan_granularity import (  # noqa: E402
    PLAN_TASK_MATRIX_MAX_SCENARIOS,
    PLAN_TASK_MAX_SCENARIOS,
    scenario_refs_from_spec_refs,
    validate_plan_task_granularity_item,
    validate_plan_task_grouping_item,
)
from hooks.repository_snapshot import (  # noqa: E402
    RepositorySnapshotError,
    resolve_git_root,
)


PLAN_FILE = "plan.json"
PLAN_MD_FILE = "PLAN.md"
PLAN_WRITE_TRANSACTION_FILE = ".plan-write-transaction.json"
SPEC_SCENARIO_DEF_RE = re.compile(r"^####\s+Scenario\s+\[(SCN-\d{3})\]:\s+.+$", re.MULTILINE)
SCENARIO_ID_RE = re.compile(r"\bSCN-\d{3}\b")
TASK_GROUP_TASK_ID_RE = re.compile(r"^T\d{3}$")
TASK_GROUP_REQUIREMENT_ID_RE = re.compile(r"\bREQ-\d{3}\b")
TASK_GROUP_API_ID_RE = re.compile(r"^API-\d{3}$")
TASK_GROUP_PAGE_ID_RE = re.compile(r"^PAGE-\d{3}$")
TASK_GROUP_INTERACTION_ID_RE = re.compile(r"^UIX-\d{3}$")
TASK_TEMPLATE_RELATIVE_PATH = "skills/autodev/autodev-plan/templates/task-input.json"
TASK_TEMPLATE_PATH = ROOT / TASK_TEMPLATE_RELATIVE_PATH
TASK_GROUP_TEMPLATE_RELATIVE_PATH = "skills/autodev/autodev-plan/templates/task-groups.json"
TASK_GROUP_TEMPLATE_PATH = ROOT / TASK_GROUP_TEMPLATE_RELATIVE_PATH
TASK_DETAIL_TEMPLATE_RELATIVE_PATH = "skills/autodev/autodev-plan/templates/task-detail-input.json"
TASK_DETAIL_TEMPLATE_PATH = ROOT / TASK_DETAIL_TEMPLATE_RELATIVE_PATH
DRAFT_RELATIVE_DIR = ".tmp/plan_writer/draft"
DRAFT_LOCK_FILE = "lock.json"
DRAFT_PLAN_FILE = "plan.json"
DRAFT_TRANSACTION_FILE = ".draft-write-transaction.json"
DRAFT_GROUP_OWNED_FIELDS = {
    "id",
    "title",
    "deps",
    "uiRequired",
    "specRefs",
    "mergedScenarioRefs",
    "apiIds",
    "uiRefs",
    "splitRationale",
    "validationBoundary",
}
DRAFT_DETAIL_FIELDS = {
    "goal",
    "scope",
    "implementationPoints",
    "acceptanceCriteria",
    "nonGoals",
    "designRefs",
    "dataIds",
    "decisionIds",
    "validationCommands",
    "expectedFiles",
    "blockers",
}
DRAFT_REQUIRED_DETAIL_FIELDS = {
    "goal",
    "scope",
    "implementationPoints",
    "acceptanceCriteria",
    "nonGoals",
    "designRefs",
    "dataIds",
    "decisionIds",
    "validationCommands",
}
DRAFT_SCOPE_FIELDS = {"modules", "entrypoints", "dataObjects", "paths"}
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
PLANNING_MUTATION_COMMANDS = {
    "add-task",
    "replace-task",
    "remove-task",
    "update-task",
    "set-task-detail",
    "set-scope",
    "set-ui-required",
    "set-ui-refs",
    "add-spec-ref",
    "remove-spec-ref",
    "add-api-id",
    "remove-api-id",
    "add-data-id",
    "remove-data-id",
    "add-design-ref",
    "remove-design-ref",
    "add-decision-id",
    "remove-decision-id",
    "add-implementation-point",
    "remove-implementation-point",
    "add-acceptance-criterion",
    "remove-acceptance-criterion",
    "add-non-goal",
    "remove-non-goal",
    "set-deps",
    "add-validation-command",
    "set-split-rationale",
}
TASK_VALIDATION_SAFE_COMMANDS = {"validate", "show", "add-task-contract"}


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


def _plan_write_transaction_path(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, PLAN_WRITE_TRANSACTION_FILE)


def _draft_dir(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, DRAFT_RELATIVE_DIR)


def _draft_lock_path(workspace: Path, feature: str) -> Path:
    return _draft_dir(workspace, feature) / DRAFT_LOCK_FILE


def _draft_plan_path(workspace: Path, feature: str) -> Path:
    return _draft_dir(workspace, feature) / DRAFT_PLAN_FILE


def _draft_batch_plan_path(workspace: Path, feature: str, batch_id: str) -> Path:
    return _draft_dir(workspace, feature) / "plans" / batch_id / "plan.json"


def _draft_transaction_path(workspace: Path, feature: str) -> Path:
    return _draft_dir(workspace, feature) / DRAFT_TRANSACTION_FILE


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _plan_lock(workspace: Path, feature: str) -> FileLock:
    return FileLock(_path(workspace, feature).parent / ".plan.lock")


def _recover_plan_write_transaction_unlocked(workspace: Path, feature: str) -> WriterResult:
    transaction_path = _plan_write_transaction_path(workspace, feature)
    if not transaction_path.is_file():
        return WriterResult(ok=True, path=_path(workspace, feature), changed=False)
    transaction = load_json(transaction_path)
    if not isinstance(transaction, dict):
        return fail("plan_write_transaction_invalid", "transaction must be an object", path=transaction_path)
    root = transaction.get("root")
    batch_plans = transaction.get("batchPlans")
    plan_markdown = transaction.get("planMarkdown")
    if (
        transaction.get("version") != 1
        or transaction.get("featureId") != feature
        or not isinstance(root, dict)
        or not isinstance(batch_plans, dict)
        or (plan_markdown is not None and not isinstance(plan_markdown, str))
        or not batch_plans
        or any(
            not isinstance(batch_id, str) or not isinstance(batch, dict)
            for batch_id, batch in batch_plans.items()
        )
    ):
        return fail("plan_write_transaction_invalid", "transaction shape mismatch", path=transaction_path)
    errors = validate_plan_bundle_data(root, batch_plans)
    if errors:
        return fail("plan_write_transaction_invalid", ";".join(errors), path=transaction_path)

    changed = False
    feature_dir = _path(workspace, feature).parent
    for batch_id, batch in batch_plans.items():
        changed = atomic_write_json(batch_plan_path(feature_dir, batch_id), batch) or changed
    changed = atomic_write_json(_path(workspace, feature), root) or changed
    if isinstance(plan_markdown, str):
        changed = write_text(_md_path(workspace, feature), plan_markdown) or changed
    transaction_path.unlink(missing_ok=True)
    return WriterResult(ok=True, path=_path(workspace, feature), changed=changed)


def recover_plan_write_transaction(workspace: Path, feature: str) -> WriterResult:
    """Replay a validated plan bundle write that was interrupted mid-commit."""

    with _plan_lock(workspace, feature):
        return _recover_plan_write_transaction_unlocked(workspace, feature)


def _task_input_example() -> dict[str, Any]:
    try:
        value = json.loads(TASK_TEMPLATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"task_input_template_unavailable:{exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("task_input_template_must_be_object")
    return value


def _task_group_example() -> dict[str, Any]:
    try:
        value = json.loads(TASK_GROUP_TEMPLATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"task_group_template_unavailable:{exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("task_group_template_must_be_object")
    return value


def _task_detail_input_example() -> dict[str, Any]:
    try:
        value = json.loads(TASK_DETAIL_TEMPLATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"task_detail_template_unavailable:{exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("task_detail_template_must_be_object")
    return value


def _task_group_matrix_exception_example() -> dict[str, Any]:
    value = _task_group_example().get("matrixExceptionExample")
    if not isinstance(value, dict):
        raise RuntimeError("task_group_matrix_exception_example_must_be_object")
    return value


def _task_group_ui_required_example() -> dict[str, Any]:
    value = _task_group_example().get("uiRequiredExample")
    if not isinstance(value, dict):
        raise RuntimeError("task_group_ui_required_example_must_be_object")
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
        "splitRationale": (
            "SCN-001, SCN-003, and SCN-006 share one request/response or state matrix "
            "validation loop and cannot be validated independently."
        ),
    }


def _initial(feature: str) -> dict[str, Any]:
    return {
        "featureId": feature,
        "status": "todo",
        "taskSetStatus": "collecting",
        "activeBatchId": None,
        "nextBatchId": None,
        "batchPolicy": {"maxTasks": MAX_BATCH_TASKS, "strategy": BATCH_STRATEGY},
        "taskValidationPolicy": {
            "mode": "deferred_batch",
            "orchestration": "single_batch_subagent",
            "failStrategy": "fail_fast",
            "maxConcurrency": 1,
            "agentScope": "task_and_batch_validation_commands",
        },
        "batches": [],
        "batchValidationProfiles": {},
        "projectValidationCommands": [],
        "projectCheckEvidenceIds": [],
        "latestProjectCheckEvidenceId": None,
        "tasks": [],  # in-memory working view; never written to root plan.json
        "_batchAssignments": {},
        "_batchPlans": {},
    }


def _load(workspace: Path, feature: str) -> dict[str, Any]:
    path = _path(workspace, feature)
    if not path.is_file() or path.stat().st_size <= 0:
        return _initial(feature)
    root = load_json(path)
    if not isinstance(root, dict):
        raise ValueError("plan.json root 必须是 object")
    if "tasks" in root:
        raise PlanWriterInputError("monolithic_plan_requires_rebuild")
    if "version" in root or "taskDetailVersion" in root:
        raise PlanWriterInputError("legacy_plan_requires_rebuild")
    finalized = root.get("taskSetStatus") == "finalized"
    if finalized and "batchValidationProfiles" not in root:
        raise PlanWriterInputError("batch_validation_contract_requires_rebuild", "batchValidationProfiles")
    data = dict(root)
    data.setdefault("featureId", feature)
    data.setdefault("status", "todo")
    data.setdefault("activeBatchId", None)
    data.setdefault("nextBatchId", None)
    data.setdefault("batchPolicy", {"maxTasks": MAX_BATCH_TASKS, "strategy": BATCH_STRATEGY})
    data.setdefault("batches", [])
    data.setdefault("batchValidationProfiles", {})
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
        if finalized and "batchValidation" not in plan:
            raise PlanWriterInputError(
                "batch_validation_contract_requires_rebuild",
                f"{batch_id}.batchValidation",
            )
        batch_plans[batch_id] = plan
        for task in plan.get("tasks", []):
            if isinstance(task, dict):
                task_items.append(task)
                if isinstance(task.get("id"), str):
                    assignments[str(task["id"])] = batch_id
    if data.get("taskSetDigest") is not None and data.get("taskSetDigest") != task_set_digest(data, batch_plans):
        raise PlanWriterInputError(
            "task_set_digest_mismatch",
            "formal plan artifacts were modified outside plan_writer",
        )
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


def _task_groups(data: dict[str, Any]) -> list[dict[str, Any]]:
    groups = data.get("groups")
    if not isinstance(groups, list):
        return []
    return [item for item in groups if isinstance(item, dict)]


def _group_string_list(
    errors: list[dict[str, str]],
    group: dict[str, Any],
    task_id: str,
    field: str,
    *,
    required: bool = True,
    item_re: re.Pattern[str] | None = None,
) -> list[str]:
    value = group.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append({"reason": f"{task_id}.{field}_must_be_string_array"})
        return []
    normalized = [item.strip() for item in value]
    if required and not normalized:
        errors.append({"reason": f"{task_id}.{field}_missing"})
    if item_re is not None:
        for item in normalized:
            if not item_re.fullmatch(item):
                errors.append({"reason": f"{task_id}.{field}_invalid:{item}"})
    return normalized


def _task_group_structure_errors(data: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    feature_id = data.get("featureId")
    if not isinstance(feature_id, str) or not feature_id.strip():
        errors.append({"reason": "task_groups_feature_id_missing"})
    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        return [*errors, {"reason": "task_groups_missing"}]

    prior_ids: set[str] = set()
    frontend_seen = False
    for index, raw_group in enumerate(raw_groups, start=1):
        if not isinstance(raw_group, dict):
            errors.append({"reason": f"task_groups[{index - 1}]_must_be_object"})
            continue
        expected_id = f"T{index:03d}"
        task_id = raw_group.get("id")
        if task_id != expected_id:
            errors.append({"reason": "task_group_sequence_invalid", "detail": f"expected={expected_id};actual={task_id}"})
            task_id = expected_id
        title = raw_group.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append({"reason": f"{task_id}.title_missing"})

        deps = _group_string_list(
            errors,
            raw_group,
            task_id,
            "deps",
            required=False,
            item_re=TASK_GROUP_TASK_ID_RE,
        )
        for dep in deps:
            if dep not in prior_ids:
                errors.append({
                    "reason": "task_group_dependency_must_reference_earlier_task",
                    "detail": f"task={task_id};dep={dep}",
                })

        spec_refs = _group_string_list(errors, raw_group, task_id, "specRefs")
        if spec_refs and not any(TASK_GROUP_REQUIREMENT_ID_RE.search(ref) for ref in spec_refs):
            errors.append({"reason": f"{task_id}.specRefs_missing_requirement_id"})
        if spec_refs and not any(SCENARIO_ID_RE.search(ref) for ref in spec_refs):
            errors.append({"reason": f"{task_id}.specRefs_missing_scenario_id"})
        _group_string_list(
            errors,
            raw_group,
            task_id,
            "apiIds",
            required=False,
            item_re=TASK_GROUP_API_ID_RE,
        )
        if "mergedScenarioRefs" in raw_group:
            _group_string_list(errors, raw_group, task_id, "mergedScenarioRefs", required=False)

        ui_required = raw_group.get("uiRequired")
        if not isinstance(ui_required, bool):
            errors.append({"reason": f"{task_id}.uiRequired_must_be_bool"})
            ui_required = False
        if frontend_seen and not ui_required:
            errors.append({"reason": "backend_task_after_frontend", "detail": f"task={task_id}"})
        frontend_seen = frontend_seen or ui_required

        ui_refs = raw_group.get("uiRefs")
        if ui_required and not isinstance(ui_refs, dict):
            errors.append({"reason": f"{task_id}.uiRefs_missing"})
        elif isinstance(ui_refs, dict):
            _group_string_list(
                errors,
                ui_refs,
                task_id,
                "pageRefs",
                required=ui_required,
                item_re=TASK_GROUP_PAGE_ID_RE,
            )
            _group_string_list(
                errors,
                ui_refs,
                task_id,
                "interactionRefs",
                required=False,
                item_re=TASK_GROUP_INTERACTION_ID_RE,
            )
            _group_string_list(
                errors,
                ui_refs,
                task_id,
                "visualSourceRefs",
                required=False,
                item_re=VISUAL_SOURCE_ID_RE,
            )
            frontend_route = ui_refs.get("frontendRoute")
            if frontend_route is None and ui_required:
                errors.append({"reason": f"{task_id}.frontendRoute_missing"})
            elif frontend_route is not None and (
                not isinstance(frontend_route, str) or frontend_route not in FRONTEND_ROUTES
            ):
                errors.append({"reason": f"{task_id}.frontendRoute_invalid"})

        validation_boundary = raw_group.get("validationBoundary")
        if not isinstance(validation_boundary, str) or len(validation_boundary.strip()) < 10:
            errors.append({"reason": f"{task_id}.validationBoundary_missing_or_too_short"})
        prior_ids.add(task_id)
    return errors


def _task_group_preflight_errors(feature_dir: Path, data: dict[str, Any]) -> list[dict[str, str]]:
    errors = _task_group_structure_errors(data)
    if errors:
        return errors
    for group in _task_groups(data):
        task_id = str(group.get("id", "task"))
        errors.extend(validate_plan_task_grouping_item(group, task_id=task_id))
    if errors:
        return errors
    expected, covered = _scenario_coverage(feature_dir, _task_groups(data))
    missing = sorted(expected - covered)
    if missing:
        return [{
            "reason": "missing_plan_scenario_coverage",
            "detail": f"return_to_scenario_matrix;ids={','.join(missing)}",
        }]
    return []


def _task_group_digest(data: dict[str, Any]) -> str:
    payload = {
        "featureId": data.get("featureId"),
        "groups": data.get("groups"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _task_group_projection(item: dict[str, Any]) -> dict[str, Any]:
    ui_refs = item.get("uiRefs") if isinstance(item.get("uiRefs"), dict) else {}
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "deps": item.get("deps") if isinstance(item.get("deps"), list) else [],
        "uiRequired": item.get("uiRequired"),
        "specRefs": item.get("specRefs") if isinstance(item.get("specRefs"), list) else [],
        "mergedScenarioRefs": (
            item.get("mergedScenarioRefs") if isinstance(item.get("mergedScenarioRefs"), list) else []
        ),
        "apiIds": item.get("apiIds") if isinstance(item.get("apiIds"), list) else [],
        "pageRefs": ui_refs.get("pageRefs") if isinstance(ui_refs.get("pageRefs"), list) else [],
        "interactionRefs": (
            ui_refs.get("interactionRefs") if isinstance(ui_refs.get("interactionRefs"), list) else []
        ),
        "visualSourceRefs": (
            ui_refs.get("visualSourceRefs") if isinstance(ui_refs.get("visualSourceRefs"), list) else []
        ),
        "frontendRoute": ui_refs.get("frontendRoute"),
        "validationBoundary": item.get("validationBoundary"),
        "splitRationale": item.get("splitRationale") or None,
    }


def _task_group_contract_errors(
    group_data: dict[str, Any],
    task_items: list[dict[str, Any]],
) -> list[dict[str, str]]:
    groups = _task_groups(group_data)
    if len(groups) != len(task_items):
        return [{
            "reason": "task_group_contract_count_mismatch",
            "detail": f"groups={len(groups)};tasks={len(task_items)}",
        }]
    errors: list[dict[str, str]] = []
    for group, task in zip(groups, task_items):
        expected = _task_group_projection(group)
        actual = _task_group_projection(task)
        changed_fields = [field for field in expected if expected[field] != actual[field]]
        if changed_fields:
            errors.append({
                "reason": "task_group_contract_mismatch",
                "detail": f"task={expected.get('id')};fields={','.join(changed_fields)}",
            })
    return errors


def _task_set_validation_errors(
    data: dict[str, Any],
    *,
    allow_empty: bool = False,
) -> list[dict[str, str]]:
    grouping_errors: list[dict[str, str]] = []
    for task in _tasks(data):
        task_id = str(task.get("id", "task"))
        grouping_errors.extend(validate_plan_task_grouping_item(task, task_id=task_id))
    if grouping_errors:
        return grouping_errors

    structure_errors = [{"reason": reason} for reason in _structure_errors(data, allow_empty=allow_empty)]
    if structure_errors or (allow_empty and not _tasks(data)):
        return structure_errors

    granularity_errors: list[dict[str, str]] = []
    for task in _tasks(data):
        task_id = str(task.get("id", "task"))
        granularity_errors.extend(validate_plan_task_granularity_item(task, task_id=task_id))
    return granularity_errors


def _primary_spec_root(task: dict[str, Any]) -> str:
    for ref in task.get("specRefs", []):
        if isinstance(ref, str) and ref.strip():
            return ref.split("#", 1)[0] or "specs/unspecified/spec.md"
    return "specs/unspecified/spec.md"


def _next_batch_id(batch_ids: set[str]) -> str:
    numbers = [int(value[1:]) for value in batch_ids if len(value) == 4 and value[0] == "B" and value[1:].isdigit()]
    return f"B{max(numbers, default=0) + 1:03d}"


def _batch_status(
    batch_tasks: list[dict[str, Any]],
    batch_validation: dict[str, Any],
    task_validation: dict[str, Any] | None = None,
) -> str:
    statuses = [normalize_status(task.get("status")) for task in batch_tasks]
    if (
        any(status == "failed" for status in statuses)
        or batch_validation.get("status") == "failed"
        or (isinstance(task_validation, dict) and task_validation.get("status") == "failed")
    ):
        return "failed"
    if statuses and all(status == "done" for status in statuses):
        task_gate_passed = not isinstance(task_validation, dict) or task_validation.get("status") == "passed"
        return "done" if task_gate_passed and batch_validation.get("status") == "passed" else "in_progress"
    if any(status in {"in_progress", "implemented", "validating", "done"} for status in statuses):
        return "in_progress"
    return "todo"


def _project_batches(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    tasks_view = _tasks(data)
    assignments = dict(data.get("_batchAssignments") or {})
    prior_plans = data.get("_batchPlans") if isinstance(data.get("_batchPlans"), dict) else {}
    groups: dict[str, list[dict[str, Any]]] = {}
    spec_roots: dict[str, str] = {}
    execution_lanes: dict[str, str] = {}
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
            execution_lane = task_execution_lane(task)
            last_batch = sorted(groups)[-1] if groups else None
            can_append_to_last = bool(
                last_batch
                and spec_roots.get(str(last_batch)) == primary
                and execution_lanes.get(str(last_batch)) == execution_lane
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
        execution_lanes.setdefault(batch_id, task_execution_lane(task))

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
        completion_ids = [
            evidence_id
            for task in batch_tasks
            for evidence_id in task.get("completionEvidenceIds", [])
            if isinstance(evidence_id, str)
        ]
        spec_root = spec_roots[batch_id]
        execution_lane = execution_lanes[batch_id]
        title = str(previous.get("title") or Path(spec_root).parent.name or batch_id)
        profiles = root.get("batchValidationProfiles")
        profile = profiles.get(execution_lane) if isinstance(profiles, dict) else None
        profile_commands = profile.get("commands") if isinstance(profile, dict) else []
        profile_mode = (
            profile.get("mode", "commands" if profile_commands else None)
            if isinstance(profile, dict)
            else "commands"
        )
        effective_commands = [
            {**command, "id": f"BATCH-{batch_id}-VAL-{command_index:03d}"}
            for command_index, command in enumerate(profile_commands, start=1)
            if isinstance(command, dict)
        ]
        previous_validation = previous.get("batchValidation")
        previous_validation = previous_validation if isinstance(previous_validation, dict) else {}
        coverage_command_ids = (
            task_covered_command_ids(batch_tasks) if profile_mode == "task_covered" else []
        )
        contract_unchanged = (
            previous_validation.get("mode", "commands" if previous_validation.get("commands") else None)
            == profile_mode
            and previous_validation.get("coverageCommandIds", []) == coverage_command_ids
            and previous_validation.get("commands") == effective_commands
        )
        batch_validation = {
            "mode": profile_mode,
            "profile": execution_lane,
            "status": previous_validation.get("status", "pending") if contract_unchanged else "pending",
            "coverageCommandIds": coverage_command_ids,
            "commands": effective_commands,
            "evidenceIds": list(previous_validation.get("evidenceIds", [])) if contract_unchanged else [],
            "latestPassEvidenceIds": (
                list(previous_validation.get("latestPassEvidenceIds", [])) if contract_unchanged else []
            ),
            "activeRunId": previous_validation.get("activeRunId") if contract_unchanged else None,
        }
        task_validation: dict[str, Any] | None = None
        if deferred_task_validation_enabled(root):
            previous_task_validation = previous.get("taskValidation")
            previous_task_validation = (
                previous_task_validation if isinstance(previous_task_validation, dict) else {}
            )
            task_order = [str(task.get("id")) for task in batch_tasks]
            task_contracts = {
                str(task.get("id")): task_contract_sha256(task) for task in batch_tasks
            }
            task_contract_unchanged = (
                previous_task_validation.get("taskOrder") == task_order
                and previous_task_validation.get("taskContractSha256ByTask") == task_contracts
            )
            task_validation = {
                "mode": "deferred_sequential",
                "status": (
                    previous_task_validation.get("status", "pending")
                    if task_contract_unchanged
                    else "pending"
                ),
                "taskOrder": task_order,
                "completedTaskIds": (
                    list(previous_task_validation.get("completedTaskIds", []))
                    if task_contract_unchanged
                    else []
                ),
                "activeRunId": (
                    previous_task_validation.get("activeRunId") if task_contract_unchanged else None
                ),
                "lastRunId": (
                    previous_task_validation.get("lastRunId") if task_contract_unchanged else None
                ),
                "currentTaskId": (
                    previous_task_validation.get("currentTaskId") if task_contract_unchanged else None
                ),
                "batchSnapshotSha256": (
                    previous_task_validation.get("batchSnapshotSha256")
                    if task_contract_unchanged
                    else None
                ),
                "evidenceIds": (
                    list(previous_task_validation.get("evidenceIds", []))
                    if task_contract_unchanged
                    else []
                ),
                "latestPassEvidenceByTask": (
                    copy.deepcopy(previous_task_validation.get("latestPassEvidenceByTask", {}))
                    if task_contract_unchanged
                    else {}
                ),
                "taskContractSha256ByTask": task_contracts,
            }
        status = _batch_status(batch_tasks, batch_validation, task_validation)
        projected[batch_id] = {
            "featureId": root.get("featureId"),
            "batchId": batch_id,
            "title": title,
            "executionLane": execution_lane,
            "status": status,
            "taskCount": len(batch_tasks),
            "completedTaskCount": sum(normalize_status(task.get("status")) == "done" for task in batch_tasks),
            "completionEvidenceIds": completion_ids,
            "batchValidation": batch_validation,
            **({"taskValidation": task_validation} if task_validation is not None else {}),
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
                "executionLane": execution_lane,
                "deps": sorted(cross_deps),
                "taskIds": [str(task.get("id")) for task in batch_tasks],
                "status": status,
            }
        )
    root["batches"] = root_entries
    root["taskSetDigest"] = task_set_digest(root, projected)
    unfinished = [entry["id"] for entry in root_entries if entry["status"] != "done"]
    if not root_entries:
        root.update({"status": "todo", "activeBatchId": None, "nextBatchId": None})
    elif root.get("status") == "awaiting_next_conversation":
        root["activeBatchId"] = None
    elif not unfinished:
        if data.get("status") == "failed":
            root["status"] = "failed"
        else:
            project_commands = root.get("projectValidationCommands")
            project_ready = (
                isinstance(project_commands, list)
                and (not project_commands or isinstance(root.get("latestProjectCheckEvidenceId"), str))
            )
            root["status"] = "done" if project_ready else "in_progress"
        root["activeBatchId"] = None
        root["nextBatchId"] = None
    else:
        active = root.get("activeBatchId")
        if active not in unfinished:
            active = unfinished[0]
        root["activeBatchId"] = active
        active_index = unfinished.index(active)
        root["nextBatchId"] = unfinished[active_index + 1] if active_index + 1 < len(unfinished) else None
        if data.get("status") == "failed" or any(entry["status"] == "failed" for entry in root_entries):
            root["status"] = "failed"
        elif data.get("status") == "in_progress" or any(entry["status"] == "in_progress" for entry in root_entries):
            root["status"] = "in_progress"
        else:
            root["status"] = "todo"
    data["_batchAssignments"] = assignments
    data["_batchPlans"] = projected
    return root, projected


def _write(
    workspace: Path,
    feature: str,
    data: dict[str, Any],
    *,
    allow_empty: bool = False,
    plan_markdown: str | None = None,
) -> WriterResult:
    path = _path(workspace, feature)
    errors = _task_set_validation_errors(data, allow_empty=allow_empty)
    if errors:
        return WriterResult(ok=False, path=path, errors=errors)
    root, batch_plans = _project_batches(data)
    if batch_plans:
        errors = validate_plan_bundle_data(root, batch_plans)
        if errors:
            return WriterResult(ok=False, path=path, errors=[{"reason": error} for error in errors])
    changed = False
    feature_dir = path.parent
    transaction_path: Path | None = None
    if batch_plans:
        transaction_path = _plan_write_transaction_path(workspace, feature)
        transaction = {
            "version": 1,
            "featureId": feature,
            "root": root,
            "batchPlans": batch_plans,
        }
        if plan_markdown is not None:
            transaction["planMarkdown"] = plan_markdown
        atomic_write_json(transaction_path, transaction)
    for batch_id, batch in batch_plans.items():
        changed = atomic_write_json(batch_plan_path(feature_dir, batch_id), batch) or changed
    changed = atomic_write_json(path, root) or changed
    if plan_markdown is not None:
        changed = write_text(_md_path(workspace, feature), plan_markdown) or changed
    if transaction_path is not None:
        transaction_path.unlink(missing_ok=True)
    return WriterResult(ok=True, path=path, changed=changed)


def _replay_draft_transaction(workspace: Path, feature: str) -> bool:
    transaction_path = _draft_transaction_path(workspace, feature)
    if not transaction_path.is_file():
        return False
    transaction = load_json(transaction_path)
    if not isinstance(transaction, dict):
        raise PlanWriterInputError("draft_write_transaction_invalid", "transaction must be an object")
    root = transaction.get("root")
    batch_plans = transaction.get("batchPlans")
    lock = transaction.get("lock")
    if (
        transaction.get("version") != 1
        or transaction.get("featureId") != feature
        or not isinstance(root, dict)
        or not isinstance(batch_plans, dict)
        or not isinstance(lock, dict)
        or any(not isinstance(key, str) or not isinstance(value, dict) for key, value in batch_plans.items())
    ):
        raise PlanWriterInputError("draft_write_transaction_invalid", "transaction shape mismatch")
    if root.get("taskSetDigest") != task_set_digest(root, batch_plans):
        raise PlanWriterInputError("draft_write_transaction_invalid", "taskSetDigest mismatch")

    referenced = set(batch_plans)
    plans_dir = _draft_dir(workspace, feature) / "plans"
    for batch_id, batch in batch_plans.items():
        atomic_write_json(_draft_batch_plan_path(workspace, feature, batch_id), batch)
    atomic_write_json(_draft_plan_path(workspace, feature), root)
    atomic_write_json(_draft_lock_path(workspace, feature), lock)
    if plans_dir.is_dir():
        for old_plan in plans_dir.glob("B*/plan.json"):
            if old_plan.parent.name not in referenced:
                old_plan.unlink(missing_ok=True)
                try:
                    old_plan.parent.rmdir()
                except OSError:
                    pass
    transaction_path.unlink(missing_ok=True)
    return True


def _write_draft_bundle(
    workspace: Path,
    feature: str,
    data: dict[str, Any],
    lock: dict[str, Any],
) -> WriterResult:
    root, batch_plans = _project_batches(data)
    root["taskSetStatus"] = "collecting"
    root["taskSetDigest"] = task_set_digest(root, batch_plans)
    lock = {**lock, "updatedAt": _utc_now()}
    atomic_write_json(
        _draft_transaction_path(workspace, feature),
        {
            "version": 1,
            "featureId": feature,
            "root": root,
            "batchPlans": batch_plans,
            "lock": lock,
        },
    )
    changed = _replay_draft_transaction(workspace, feature)
    return WriterResult(ok=True, path=_draft_plan_path(workspace, feature), changed=changed)


def _load_draft_bundle(workspace: Path, feature: str) -> tuple[dict[str, Any], dict[str, Any]]:
    _replay_draft_transaction(workspace, feature)
    if not _draft_lock_path(workspace, feature).is_file() or not _draft_plan_path(workspace, feature).is_file():
        raise PlanWriterInputError("task_draft_missing")
    lock = load_json(_draft_lock_path(workspace, feature))
    root = load_json(_draft_plan_path(workspace, feature))
    if not isinstance(lock, dict) or not isinstance(root, dict):
        raise PlanWriterInputError("task_draft_missing")
    if lock.get("version") != 1 or lock.get("featureId") != feature:
        raise PlanWriterInputError("task_draft_lock_invalid")
    batch_plans: dict[str, dict[str, Any]] = {}
    tasks: list[dict[str, Any]] = []
    assignments: dict[str, str] = {}
    for entry in root.get("batches", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        batch_id = str(entry["id"])
        batch_path = _draft_batch_plan_path(workspace, feature, batch_id)
        if not batch_path.is_file():
            raise PlanWriterInputError("task_draft_batch_missing", batch_id)
        batch = load_json(batch_path)
        if not isinstance(batch, dict):
            raise PlanWriterInputError("task_draft_batch_missing", batch_id)
        batch_plans[batch_id] = batch
        for task in batch.get("tasks", []):
            if isinstance(task, dict):
                tasks.append(task)
                if isinstance(task.get("id"), str):
                    assignments[str(task["id"])] = batch_id
    if root.get("taskSetDigest") != task_set_digest(root, batch_plans):
        raise PlanWriterInputError("task_draft_digest_mismatch", "draft artifacts were modified outside plan_writer")
    data = dict(root)
    data["tasks"] = tasks
    data["_batchAssignments"] = assignments
    data["_batchPlans"] = batch_plans
    return lock, data


def _draft_group_data(lock: dict[str, Any], feature: str) -> dict[str, Any]:
    group_file = lock.get("groupFile")
    if not isinstance(group_file, str) or not group_file:
        raise PlanWriterInputError("task_draft_group_file_missing")
    data = _load_task_group_file(Path(group_file), feature)
    actual = _task_group_digest(data)
    expected = lock.get("groupingDigest")
    if actual != expected:
        raise PlanWriterInputError(
            "task_group_changed_after_draft_created",
            f"expected={expected};actual={actual};run=rebuild-task-draft",
        )
    return data


def _draft_workspace_roots(code_workspaces: list[str] | None) -> dict[str, str]:
    contexts = _code_workspace_contexts(code_workspaces)
    if not contexts:
        return {}
    if len(contexts) == 1:
        return {"default": str(contexts[0]["workspaceRoot"])}
    return {str(item["repo"]): str(item["workspaceRoot"]) for item in contexts}


def _draft_task_skeleton(group: dict[str, Any], workspace_roots: dict[str, str]) -> dict[str, Any]:
    task_id = str(group.get("id"))
    ui_required = group.get("uiRequired") is True
    ui_refs = copy.deepcopy(group.get("uiRefs")) if isinstance(group.get("uiRefs"), dict) else None
    task: dict[str, Any] = {
        "id": task_id,
        "title": group.get("title"),
        "goal": "",
        "status": "todo",
        "deps": copy.deepcopy(group.get("deps", [])),
        "uiRequired": ui_required,
        "scope": {
            "modules": [],
            "entrypoints": [],
            "pages": copy.deepcopy(ui_refs.get("pageRefs", [])) if ui_refs else [],
            "dataObjects": [],
            "workspaceRoots": copy.deepcopy(workspace_roots),
            "paths": [],
        },
        "implementationPoints": [],
        "acceptanceCriteria": [],
        "validationBoundary": group.get("validationBoundary"),
        "nonGoals": [],
        "specRefs": copy.deepcopy(group.get("specRefs", [])),
        "mergedScenarioRefs": copy.deepcopy(group.get("mergedScenarioRefs", [])),
        "designRefs": [],
        "apiIds": copy.deepcopy(group.get("apiIds", [])),
        "dataIds": [],
        "decisionIds": [],
        "validationCommands": [],
        "expectedFiles": [],
        "evidenceIds": [],
        "implementationEvidenceIds": [],
        "latestImplementationEvidenceId": None,
        "validationEvidenceIds": [],
        "implementationRevision": 0,
        "completionPolicy": "all_required_validations_pass",
        "completionEvidenceIds": [],
        "latestPassEvidenceId": None,
        "blockers": [],
    }
    if ui_refs is not None:
        task["uiRefs"] = ui_refs
    rationale = group.get("splitRationale")
    if isinstance(rationale, str) and rationale.strip():
        task["splitRationale"] = rationale
    return task


def _draft_detail_body(args: argparse.Namespace) -> dict[str, Any]:
    if args.body_file:
        return read_object_file(args.body_file)
    if args.body_stdin:
        return read_object_stdin()
    if args.body_json:
        value = parse_json_value(args.body_json)
        if not isinstance(value, dict):
            raise PlanWriterInputError("draft_task_detail_must_be_object")
        return value
    raise PlanWriterInputError("draft_task_detail_input_missing")


def _draft_default_command_cwd(scope: dict[str, Any], command: dict[str, Any]) -> str:
    workspace_roots = scope.get("workspaceRoots")
    if not isinstance(workspace_roots, dict) or not workspace_roots:
        return "."
    default = workspace_roots.get("default")
    if isinstance(default, str) and default:
        return default
    repo = command.get("repo")
    value = workspace_roots.get(repo) if isinstance(repo, str) else None
    if isinstance(value, str) and value:
        return value
    raise PlanWriterInputError("draft_validation_repo_required_for_multi_workspace")


def _normalize_draft_task_detail(task: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task.get("id"))
    group_owned = sorted(set(detail) & DRAFT_GROUP_OWNED_FIELDS)
    if group_owned:
        raise PlanWriterInputError(
            "draft_task_group_owned_field_forbidden",
            f"task={task_id};fields={','.join(group_owned)}",
        )
    unknown = sorted(set(detail) - DRAFT_DETAIL_FIELDS)
    if unknown:
        raise PlanWriterInputError(
            "draft_task_detail_field_unknown",
            f"task={task_id};fields={','.join(unknown)}",
        )
    missing = sorted(field for field in DRAFT_REQUIRED_DETAIL_FIELDS if field not in detail)
    if missing:
        raise PlanWriterInputError(
            "draft_task_detail_fields_missing",
            f"task={task_id};fields={','.join(missing)}",
        )

    candidate = copy.deepcopy(task)
    scope = detail.get("scope")
    if not isinstance(scope, dict):
        raise PlanWriterInputError("draft_task_scope_must_be_object", f"task={task_id}")
    scope_unknown = sorted(set(scope) - DRAFT_SCOPE_FIELDS)
    if scope_unknown:
        if scope_unknown == ["pages"]:
            reason = "draft_scope_pages_group_owned"
        elif scope_unknown == ["workspaceRoots"]:
            reason = "draft_scope_workspace_roots_writer_owned"
        else:
            reason = "draft_task_scope_field_unknown"
        raise PlanWriterInputError(reason, f"task={task_id};fields={','.join(scope_unknown)}")
    previous_scope = candidate.get("scope") if isinstance(candidate.get("scope"), dict) else {}
    candidate["scope"] = {
        "modules": copy.deepcopy(scope.get("modules", [])),
        "entrypoints": copy.deepcopy(scope.get("entrypoints", [])),
        "pages": copy.deepcopy(candidate.get("uiRefs", {}).get("pageRefs", []))
        if isinstance(candidate.get("uiRefs"), dict)
        else [],
        "dataObjects": copy.deepcopy(scope.get("dataObjects", [])),
        "workspaceRoots": copy.deepcopy(previous_scope.get("workspaceRoots", {})),
        "paths": copy.deepcopy(scope.get("paths", [])),
    }
    if not candidate["scope"]["workspaceRoots"]:
        candidate["scope"].pop("workspaceRoots")

    raw_criteria = detail.get("acceptanceCriteria")
    if not isinstance(raw_criteria, list):
        raise PlanWriterInputError("draft_acceptance_criteria_must_be_array", f"task={task_id}")
    criteria: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_criteria, start=1):
        if not isinstance(raw, dict):
            raise PlanWriterInputError("draft_acceptance_criterion_must_be_object", f"task={task_id};index={index}")
        if "id" in raw:
            raise PlanWriterInputError("draft_acceptance_id_writer_owned", f"task={task_id};index={index}")
        unknown_fields = sorted(set(raw) - {"text", "scenarioRefs"})
        if unknown_fields:
            raise PlanWriterInputError(
                "draft_acceptance_field_unknown",
                f"task={task_id};index={index};fields={','.join(unknown_fields)}",
            )
        criteria.append({
            "id": f"AC-{task_id}-{index:02d}",
            "text": raw.get("text"),
            "scenarioRefs": copy.deepcopy(raw.get("scenarioRefs", [])),
        })
    candidate["acceptanceCriteria"] = criteria
    acceptance_ids = [item["id"] for item in criteria]

    raw_commands = detail.get("validationCommands")
    if not isinstance(raw_commands, list):
        raise PlanWriterInputError("draft_validation_commands_must_be_array", f"task={task_id}")
    commands: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_commands, start=1):
        if not isinstance(raw, dict):
            raise PlanWriterInputError("draft_validation_command_must_be_object", f"task={task_id};index={index}")
        if "id" in raw:
            raise PlanWriterInputError("draft_validation_id_writer_owned", f"task={task_id};index={index}")
        unknown_fields = sorted(set(raw) - {"argv", "cwd", "kind", "required", "covers", "repo"})
        if unknown_fields:
            raise PlanWriterInputError(
                "draft_validation_field_unknown",
                f"task={task_id};index={index};fields={','.join(unknown_fields)}",
            )
        command = copy.deepcopy(raw)
        command["id"] = f"VAL-{task_id}-{index:02d}"
        command.setdefault("kind", "behavior_test")
        command.setdefault("required", True)
        raw_covers = command.get("covers")
        if raw_covers is None:
            command["covers"] = list(acceptance_ids)
        elif isinstance(raw_covers, list):
            covers: list[str] = []
            for value in raw_covers:
                if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= len(acceptance_ids):
                    covers.append(acceptance_ids[value - 1])
                elif isinstance(value, str) and value in acceptance_ids:
                    covers.append(value)
                else:
                    raise PlanWriterInputError(
                        "draft_validation_cover_invalid",
                        f"task={task_id};command={index};cover={value}",
                    )
            command["covers"] = covers
        else:
            raise PlanWriterInputError("draft_validation_covers_must_be_array", f"task={task_id};index={index}")
        command.setdefault("cwd", _draft_default_command_cwd(candidate["scope"], command))
        commands.append(command)
    candidate["validationCommands"] = commands

    for field in (
        "goal",
        "implementationPoints",
        "nonGoals",
        "designRefs",
        "dataIds",
        "decisionIds",
        "expectedFiles",
        "blockers",
    ):
        candidate[field] = copy.deepcopy(detail.get(field, [] if field != "goal" else ""))
    candidate["status"] = "todo"
    candidate["evidenceIds"] = []
    candidate["implementationEvidenceIds"] = []
    candidate["latestImplementationEvidenceId"] = None
    candidate["validationEvidenceIds"] = []
    candidate["implementationRevision"] = 0
    candidate["completionPolicy"] = "all_required_validations_pass"
    candidate["completionEvidenceIds"] = []
    candidate["latestPassEvidenceId"] = None
    return candidate


def _draft_acceptance_scope_errors(task: dict[str, Any]) -> list[dict[str, str]]:
    task_id = str(task.get("id", "task"))
    allowed = scenario_refs_from_spec_refs(
        [item for item in task.get("specRefs", []) if isinstance(item, str)]
    )
    errors: list[dict[str, str]] = []
    for index, criterion in enumerate(task.get("acceptanceCriteria", [])):
        if not isinstance(criterion, dict):
            continue
        raw_refs = criterion.get("scenarioRefs")
        if not isinstance(raw_refs, list):
            continue
        actual = scenario_refs_from_spec_refs([item for item in raw_refs if isinstance(item, str)])
        if len(actual) != len(raw_refs):
            errors.append({
                "reason": "draft_acceptance_scenario_ref_invalid",
                "detail": f"task={task_id};criterion={index + 1}",
            })
            continue
        outside = sorted(actual - allowed)
        if outside:
            errors.append({
                "reason": "acceptance_scenario_not_in_group",
                "detail": f"task={task_id};criterion={index + 1};refs={','.join(outside)}",
            })
    return errors


def _draft_task_validation_errors(
    feature: str,
    task: dict[str, Any],
    code_workspaces: list[str] | None,
) -> list[dict[str, str]]:
    task_for_structure = copy.deepcopy(task)
    task_for_structure["deps"] = []
    raw_errors = validate_task_collection(feature, [task_for_structure], require_initial_status=True)
    translated = {
        f"{task.get('id')}.implementationPoints_too_many": (
            f"{task.get('id')}.implementation_points_exceeds_limit"
        ),
    }
    errors = [{"reason": translated.get(reason, reason)} for reason in raw_errors]
    errors.extend(_draft_acceptance_scope_errors(task))
    errors.extend(validate_plan_task_granularity_item(task, task_id=str(task.get("id", "task"))))
    errors.extend(_code_workspace_preflight_errors({"tasks": [task]}, code_workspaces))
    return errors


def _tasks(data: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = data.setdefault("tasks", [])
    if not isinstance(tasks, list):
        raise ValueError("plan.json.tasks 必须是数组")
    return tasks


def _require_collecting(data: dict[str, Any]) -> None:
    if data.get("taskSetStatus") == "finalized":
        raise PlanWriterInputError("plan_task_set_finalized")


def _require_no_running_task_validation(data: dict[str, Any], command: str) -> None:
    if command in TASK_VALIDATION_SAFE_COMMANDS:
        return
    batch_plans = data.get("_batchPlans")
    running = [
        batch_id
        for batch_id, batch in (batch_plans.items() if isinstance(batch_plans, dict) else [])
        if isinstance(batch, dict)
        and isinstance(batch.get("taskValidation"), dict)
        and batch["taskValidation"].get("status") == "running"
    ]
    if running:
        raise PlanWriterInputError(
            "task_validation_workspace_frozen",
            f"command={command};batches={','.join(sorted(running))}",
        )


def _scenario_coverage(feature_dir: Path, task_items: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    expected: set[str] = set()
    for spec_path in sorted((feature_dir / "specs").glob("**/*.md")):
        relative = spec_path.relative_to(feature_dir).as_posix()
        text = spec_path.read_text(encoding="utf-8")
        expected.update(f"{relative}#{scenario_id}" for scenario_id in SPEC_SCENARIO_DEF_RE.findall(text))

    covered: set[str] = set()
    for task in task_items:
        for raw_ref in task.get("specRefs", []):
            if not isinstance(raw_ref, str):
                continue
            path_part, separator, anchor = raw_ref.partition("#")
            scenario_ids = SCENARIO_ID_RE.findall(anchor) if separator else []
            normalized_path = path_part.strip().replace("\\", "/")
            if normalized_path:
                covered.update(f"{normalized_path}#{scenario_id}" for scenario_id in scenario_ids)
    return expected, covered


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


def _workspace_roots_from_values(values: list[str] | None) -> dict[str, str]:
    roots: dict[str, str] = {}
    for raw in values or []:
        key, separator, path = raw.partition("=")
        if separator:
            roots[key.strip()] = path.strip()
        else:
            roots["default"] = raw.strip()
    return roots or {"default": "."}


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
            "workspaceRoots": _workspace_roots_from_values(args.workspace_root),
            "paths": _split_values(args.scope_path),
        },
        "implementationPoints": implementation,
        "acceptanceCriteria": acceptance,
        "validationBoundary": (
            args.validation_boundary.strip()
            if isinstance(args.validation_boundary, str) and args.validation_boundary.strip()
            else f"{args.title} 的公开行为边界由验证命令覆盖"
        ),
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
    for field in (
        "specRefs",
        "implementationPoints",
        "acceptanceCriteria",
        "validationBoundary",
        "nonGoals",
        "validationCommands",
    ):
        if field == "validationBoundary":
            value = task.get(field)
            if not isinstance(value, str) or len(value.strip()) < 10:
                missing.append(field)
            continue
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

    return _normalize_task_body(task, requested_id=args.task_id, data=data)


def _normalize_task_body(
    task: dict[str, Any],
    *,
    requested_id: str | None,
    data: dict[str, Any],
) -> dict[str, Any]:
    task = dict(task)
    task.pop("matrixExceptionExample", None)
    body_id = task.get("id")
    if body_id is not None and not isinstance(body_id, str):
        raise ValueError("task body 的 id 必须是字符串")
    if requested_id and body_id and requested_id != body_id:
        raise PlanWriterInputError("task_body_id_mismatch", f"{requested_id}!={body_id}")
    if not body_id:
        task["id"] = requested_id or next_numbered_id(_ids(data), "T")
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
    task["implementationEvidenceIds"] = []
    task["latestImplementationEvidenceId"] = None
    task["validationEvidenceIds"] = []
    task["implementationRevision"] = 0
    _normalize_task(task, str(task["id"]))
    task["completionEvidenceIds"] = []
    task["latestPassEvidenceId"] = None
    task.setdefault("blockers", [])
    return task


def _reset_batch_projection(data: dict[str, Any]) -> None:
    data["batches"] = []
    data["_batchAssignments"] = {}
    data["_batchPlans"] = {}


def _cmd_replace_task(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    _require_collecting(data)
    replacement = _normalize_task_body(read_object_file(args.body_file), requested_id=args.task_id, data=data)
    task_items = _tasks(data)
    index = next((index for index, task in enumerate(task_items) if task.get("id") == args.task_id), None)
    if index is None:
        return render_result(fail("task_not_found", args.task_id, path=_path(workspace, feature)))
    task_items[index] = replacement
    _reset_batch_projection(data)
    return render_result(_write(workspace, feature, data))


def _cmd_remove_task(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    _require_collecting(data)
    dependents = sorted(
        str(task.get("id"))
        for task in _tasks(data)
        if args.task_id in task.get("deps", [])
    )
    if dependents:
        return render_result(
            fail("task_has_dependents", f"task={args.task_id};dependents={','.join(dependents)}", path=_path(workspace, feature))
        )
    before = len(_tasks(data))
    data["tasks"] = [task for task in _tasks(data) if task.get("id") != args.task_id]
    if len(data["tasks"]) == before:
        return render_result(fail("task_not_found", args.task_id, path=_path(workspace, feature)))
    _reset_batch_projection(data)
    return render_result(_write(workspace, feature, data, allow_empty=True))


def _load_task_directory(task_dir: Path, feature: str) -> dict[str, Any]:
    if not task_dir.is_dir():
        raise PlanWriterInputError("task_directory_missing", str(task_dir))
    paths = sorted(task_dir.glob("T*.json"))
    if not paths:
        raise PlanWriterInputError("task_directory_empty", str(task_dir))
    data = _initial(feature)
    tasks: list[dict[str, Any]] = []
    for index, path in enumerate(paths, start=1):
        expected_id = f"T{index:03d}"
        if path.stem != expected_id:
            raise PlanWriterInputError("task_file_sequence_invalid", f"expected={expected_id};actual={path.stem}")
        task = _normalize_task_body(read_object_file(path), requested_id=expected_id, data=data)
        tasks.append(task)
        data["tasks"] = tasks
    return data


def _load_task_group_file(group_file: Path, feature: str) -> dict[str, Any]:
    data = read_object_file(group_file)
    manifest_feature = data.get("featureId")
    if manifest_feature != feature:
        raise PlanWriterInputError(
            "task_groups_feature_mismatch",
            f"expected={feature};actual={manifest_feature}",
        )
    return data


def _task_group_summary(data: dict[str, Any]) -> dict[str, Any]:
    groups = _task_groups(data)
    return {
        "groupCount": len(groups),
        "groupingDigest": _task_group_digest(data),
        "groups": [
            {
                "id": group.get("id"),
                "executionLane": "frontend" if group.get("uiRequired") is True else "backend",
                "scenarioCount": len(
                    scenario_refs_from_spec_refs(
                        [item for item in group.get("specRefs", []) if isinstance(item, str)]
                    )
                ),
            }
            for group in groups
        ],
    }


def _code_workspace_contexts(values: list[str] | None) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in values or []:
        requested = Path(raw).expanduser().resolve()
        try:
            git_root = resolve_git_root(requested)
        except RepositorySnapshotError as exc:
            raise PlanWriterInputError("code_workspace_invalid", str(exc)) from exc
        try:
            relative = requested.relative_to(git_root)
        except ValueError as exc:
            raise PlanWriterInputError("code_workspace_outside_git_root", str(requested)) from exc
        workspace_root = "." if relative == Path(".") else relative.as_posix()
        key = (git_root.name, workspace_root)
        if key in seen:
            continue
        contexts.append({
            "repo": git_root.name,
            "gitRoot": git_root,
            "workspaceRoot": workspace_root,
            "requestedPath": requested,
        })
        seen.add(key)
    return contexts


def _context_for_workspace_root(
    contexts: list[dict[str, Any]],
    key: str,
    workspace_root: str,
) -> dict[str, Any] | None:
    matches = [
        item
        for item in contexts
        if item["workspaceRoot"] == workspace_root
        and (key == "default" or item["repo"] == key)
    ]
    return matches[0] if len(matches) == 1 else None


def _command_workspace_preflight_errors(
    command: dict[str, Any],
    *,
    context_name: str,
    workspace_roots: dict[str, str],
    contexts: list[dict[str, Any]],
) -> list[dict[str, str]]:
    key = "default" if "default" in workspace_roots else command.get("repo")
    workspace_root = workspace_roots.get(str(key)) if isinstance(key, str) else None
    if workspace_root is None:
        return [{"reason": f"{context_name}.workspace_root_missing"}]
    workspace_context = _context_for_workspace_root(contexts, str(key), workspace_root)
    if workspace_context is None:
        return [{
            "reason": "code_workspace_contract_mismatch",
            "detail": f"context={context_name};repo={key};workspaceRoot={workspace_root}",
        }]
    cwd = command.get("cwd")
    command_dir = (
        workspace_context["gitRoot"] / str(cwd)
        if isinstance(cwd, str)
        else workspace_context["gitRoot"]
    ).resolve()
    if not command_dir.is_dir():
        return [{
            "reason": "validation_cwd_missing",
            "detail": f"context={context_name};cwd={cwd}",
        }]
    manifests = validation_command_manifest_names(command)
    if manifests and not any((command_dir / name).is_file() for name in manifests):
        return [{
            "reason": "validation_manifest_missing",
            "detail": f"context={context_name};cwd={cwd};expected={'|'.join(manifests)}",
        }]
    return []


def _code_workspace_preflight_errors(
    data: dict[str, Any],
    code_workspaces: list[str] | None,
) -> list[dict[str, str]]:
    tasks_with_roots = [task for task in _tasks(data) if task_workspace_roots(task)]
    if not tasks_with_roots:
        return []
    if not code_workspaces:
        return [{"reason": "code_workspace_preflight_required"}]
    contexts = _code_workspace_contexts(code_workspaces)
    errors: list[dict[str, str]] = []
    for task in tasks_with_roots:
        task_id = str(task.get("id", "task"))
        workspace_roots = task_workspace_roots(task)
        for key, workspace_root in workspace_roots.items():
            if _context_for_workspace_root(contexts, key, workspace_root) is None:
                errors.append({
                    "reason": "code_workspace_contract_mismatch",
                    "detail": f"task={task_id};repo={key};workspaceRoot={workspace_root}",
                })
        for index, command in enumerate(task.get("validationCommands", [])):
            if isinstance(command, dict):
                errors.extend(_command_workspace_preflight_errors(
                    command,
                    context_name=f"{task_id}.validationCommands[{index}]",
                    workspace_roots=workspace_roots,
                    contexts=contexts,
                ))
    return errors


def _task_set_preflight_errors(
    feature_dir: Path,
    data: dict[str, Any],
    group_data: dict[str, Any],
    code_workspaces: list[str] | None = None,
) -> list[dict[str, str]]:
    errors = _task_group_preflight_errors(feature_dir, group_data)
    if errors:
        return errors
    errors = _task_group_contract_errors(group_data, _tasks(data))
    if errors:
        return errors
    errors = _task_set_validation_errors(data)
    if errors:
        return errors
    errors = _code_workspace_preflight_errors(data, code_workspaces)
    if errors:
        return errors
    root, batches = _project_batches(data)
    bundle_errors = validate_plan_bundle_data(root, batches)
    if bundle_errors:
        return [{"reason": error} for error in bundle_errors]
    expected, covered = _scenario_coverage(feature_dir, _tasks(data))
    missing = sorted(expected - covered)
    if missing:
        return [{
            "reason": "missing_plan_scenario_coverage",
            "detail": f"return_to_scenario_matrix;ids={','.join(missing)}",
        }]
    return []


def _task_set_summary(data: dict[str, Any]) -> dict[str, Any]:
    root, _ = _project_batches(data)
    return {
        "taskCount": len(_tasks(data)),
        "batchCount": len(root.get("batches", [])),
        "batches": [
            {"id": entry["id"], "executionLane": entry["executionLane"], "taskIds": entry["taskIds"]}
            for entry in root.get("batches", [])
        ],
    }


def _draft_summary(lock: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    task_ids = [str(task.get("id")) for task in _tasks(data)]
    ready = {
        item for item in lock.get("readyTaskIds", []) if isinstance(item, str)
    }
    summary = _task_set_summary(data)
    return {
        "status": lock.get("status"),
        "groupingDigest": lock.get("groupingDigest"),
        "taskCount": len(task_ids),
        "readyTaskIds": [task_id for task_id in task_ids if task_id in ready],
        "pendingTaskIds": [task_id for task_id in task_ids if task_id not in ready],
        "batches": summary["batches"],
    }


def _cmd_prepare_task_draft(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    if _path(workspace, feature).is_file():
        return render_result(fail("formal_plan_already_exists", path=_path(workspace, feature)))
    if _draft_lock_path(workspace, feature).is_file() and not args.force:
        return render_result(fail(
            "task_draft_already_exists",
            "use rebuild-task-draft or pass --force to replace the draft",
            path=_draft_plan_path(workspace, feature),
        ))
    group_file = Path(args.group_file).expanduser().resolve()
    group_data = _load_task_group_file(group_file, feature)
    errors = _task_group_preflight_errors(_path(workspace, feature).parent, group_data)
    if errors:
        return render_result(WriterResult(ok=False, path=group_file, errors=errors))
    workspace_roots = _draft_workspace_roots(args.code_workspace)
    data = _initial(feature)
    data["tasks"] = [
        _draft_task_skeleton(group, workspace_roots)
        for group in _task_groups(group_data)
    ]
    data["taskSetStatus"] = "collecting"
    data["_batchAssignments"] = {}
    data["_batchPlans"] = {}
    code_workspaces = [
        str(Path(value).expanduser().resolve()) for value in args.code_workspace or []
    ]
    lock = {
        "version": 1,
        "featureId": feature,
        "groupFile": str(group_file),
        "groupingDigest": _task_group_digest(group_data),
        "status": "collecting",
        "readyTaskIds": [],
        "codeWorkspaces": code_workspaces,
        "createdAt": _utc_now(),
    }
    result = _write_draft_bundle(workspace, feature, data, lock)
    return render_result(with_result_data(result, draft=_draft_summary(lock, data)))


def _cmd_import_task_directory(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    if _path(workspace, feature).is_file():
        return render_result(fail("formal_plan_already_exists", path=_path(workspace, feature)))
    if _draft_lock_path(workspace, feature).is_file() and not args.force:
        return render_result(fail("task_draft_already_exists", path=_draft_plan_path(workspace, feature)))
    group_file = Path(args.group_file).expanduser().resolve()
    group_data = _load_task_group_file(group_file, feature)
    data = _load_task_directory(Path(args.task_dir).expanduser().resolve(), feature)
    errors = _task_set_preflight_errors(
        _path(workspace, feature).parent,
        data,
        group_data,
        args.code_workspace,
    )
    if errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=errors,
        ))
    code_workspaces = [
        str(Path(value).expanduser().resolve()) for value in args.code_workspace or []
    ]
    task_ids = [str(task.get("id")) for task in _tasks(data)]
    lock = {
        "version": 1,
        "featureId": feature,
        "groupFile": str(group_file),
        "groupingDigest": _task_group_digest(group_data),
        "status": "ready",
        "readyTaskIds": task_ids,
        "codeWorkspaces": code_workspaces,
        "createdAt": _utc_now(),
        "importedFromTaskDirectory": str(Path(args.task_dir).expanduser().resolve()),
    }
    result = _write_draft_bundle(workspace, feature, data, lock)
    return render_result(with_result_data(result, importedTaskIds=task_ids, draft=_draft_summary(lock, data)))


def _cmd_set_draft_task_detail(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    lock, data = _load_draft_bundle(workspace, feature)
    if lock.get("status") == "finalized":
        return render_result(fail("task_draft_finalized", path=_draft_plan_path(workspace, feature)))
    _draft_group_data(lock, feature)
    task = _find_task(data, args.task_id)
    candidate = _normalize_draft_task_detail(task, _draft_detail_body(args))
    code_workspaces = [
        item for item in lock.get("codeWorkspaces", []) if isinstance(item, str)
    ]
    errors = _draft_task_validation_errors(feature, candidate, code_workspaces)
    if errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=errors,
        ))
    task_items = _tasks(data)
    task_items[task_items.index(task)] = candidate
    ready = {
        item for item in lock.get("readyTaskIds", []) if isinstance(item, str)
    }
    ready.add(args.task_id)
    ordered_ids = [str(item.get("id")) for item in task_items]
    lock["readyTaskIds"] = [task_id for task_id in ordered_ids if task_id in ready]
    lock["status"] = "ready" if len(ready) == len(task_items) else "collecting"
    result = _write_draft_bundle(workspace, feature, data, lock)
    return render_result(with_result_data(
        result,
        taskId=args.task_id,
        taskStatus="ready",
        draft=_draft_summary(lock, data),
    ))


def _draft_preflight(
    workspace: Path,
    feature: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    lock, data = _load_draft_bundle(workspace, feature)
    group_data = _draft_group_data(lock, feature)
    task_ids = [str(task.get("id")) for task in _tasks(data)]
    ready = {item for item in lock.get("readyTaskIds", []) if isinstance(item, str)}
    pending = [task_id for task_id in task_ids if task_id not in ready]
    if pending:
        return lock, data, group_data, [{
            "reason": "draft_task_not_ready",
            "detail": f"taskIds={','.join(pending)}",
        }]
    code_workspaces = [
        item for item in lock.get("codeWorkspaces", []) if isinstance(item, str)
    ]
    return lock, data, group_data, _task_set_preflight_errors(
        _path(workspace, feature).parent,
        data,
        group_data,
        code_workspaces,
    )


def _cmd_preflight_task_draft(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    lock, data, _, errors = _draft_preflight(workspace, feature)
    return render_result(WriterResult(
        ok=not errors,
        path=_draft_plan_path(workspace, feature),
        errors=errors,
        data={"draft": _draft_summary(lock, data)} if not errors else None,
    ))


def _cmd_show_task_draft(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    lock, data = _load_draft_bundle(workspace, feature)
    _draft_group_data(lock, feature)
    return render_result(WriterResult(
        ok=True,
        path=_draft_plan_path(workspace, feature),
        data={"draft": _draft_summary(lock, data)},
    ))


def _cmd_rebuild_task_draft(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    if _path(workspace, feature).is_file():
        return render_result(fail("formal_plan_already_exists", path=_path(workspace, feature)))
    old_lock, old_data = _load_draft_bundle(workspace, feature)
    group_file = Path(args.group_file).expanduser().resolve()
    group_data = _load_task_group_file(group_file, feature)
    errors = _task_group_preflight_errors(_path(workspace, feature).parent, group_data)
    if errors:
        return render_result(WriterResult(ok=False, path=group_file, errors=errors))
    code_workspaces = (
        [str(Path(value).expanduser().resolve()) for value in args.code_workspace]
        if args.code_workspace
        else [item for item in old_lock.get("codeWorkspaces", []) if isinstance(item, str)]
    )
    if not code_workspaces:
        return render_result(fail(
            "code_workspace_required_for_rebuild",
            "pass --code-workspace to repair this draft",
            path=_draft_plan_path(workspace, feature),
        ))
    old_code_workspaces = [
        item for item in old_lock.get("codeWorkspaces", []) if isinstance(item, str)
    ]
    workspace_contract_changed = code_workspaces != old_code_workspaces
    workspace_roots = _draft_workspace_roots(code_workspaces)
    old_tasks = {
        str(task.get("id")): task for task in _tasks(old_data) if isinstance(task.get("id"), str)
    }
    old_ready = {
        item for item in old_lock.get("readyTaskIds", []) if isinstance(item, str)
    }
    tasks: list[dict[str, Any]] = []
    preserved: list[str] = []
    reset: list[str] = []
    for group in _task_groups(group_data):
        task_id = str(group.get("id"))
        old_task = old_tasks.get(task_id)
        if (
            not workspace_contract_changed
            and old_task is not None
            and _task_group_projection(old_task) == _task_group_projection(group)
        ):
            tasks.append(copy.deepcopy(old_task))
            if task_id in old_ready:
                preserved.append(task_id)
        else:
            tasks.append(_draft_task_skeleton(group, workspace_roots))
            reset.append(task_id)
    data = _initial(feature)
    data["tasks"] = tasks
    data["taskSetStatus"] = "collecting"
    lock = {
        "version": 1,
        "featureId": feature,
        "groupFile": str(group_file),
        "groupingDigest": _task_group_digest(group_data),
        "status": "ready" if len(preserved) == len(tasks) else "collecting",
        "readyTaskIds": preserved,
        "codeWorkspaces": code_workspaces,
        "createdAt": old_lock.get("createdAt") or _utc_now(),
        "rebuiltAt": _utc_now(),
    }
    result = _write_draft_bundle(workspace, feature, data, lock)
    return render_result(with_result_data(
        result,
        preservedTaskIds=preserved,
        resetTaskIds=reset,
        draft=_draft_summary(lock, data),
    ))


def _cmd_finalize_task_draft(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    existing = fail_if_artifact_exists(_path(workspace, feature), force=args.force)
    if existing:
        return render_result(existing)
    lock, data, _, errors = _draft_preflight(workspace, feature)
    if lock.get("status") == "finalized":
        return render_result(fail("task_draft_finalized", path=_draft_plan_path(workspace, feature)))
    if errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=errors,
        ))
    data["taskSetStatus"] = "finalized"
    result = _write(
        workspace,
        feature,
        data,
        plan_markdown=_render_plan_md(data),
    )
    if result.ok:
        if args.force:
            referenced = {
                str(entry.get("id"))
                for entry in data.get("batches", [])
                if isinstance(entry, dict) and isinstance(entry.get("id"), str)
            }
            plans_dir = _path(workspace, feature).parent / "plans"
            for old_plan in plans_dir.glob("B*/plan.json") if plans_dir.is_dir() else []:
                if old_plan.parent.name not in referenced:
                    old_plan.unlink(missing_ok=True)
                    try:
                        old_plan.parent.rmdir()
                    except OSError:
                        pass
        lock["status"] = "finalized"
        lock["finalizedAt"] = _utc_now()
        atomic_write_json(_draft_lock_path(workspace, feature), lock)
    return render_result(with_result_data(
        result,
        materialized=_task_set_summary(data),
        draft=_draft_summary(lock, data),
    ))


def _cmd_preflight_task_set(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    group_data = _load_task_group_file(Path(args.group_file).resolve(), feature)
    feature_dir = _path(workspace, feature).parent
    errors = _task_group_preflight_errors(feature_dir, group_data)
    if errors:
        return render_result(WriterResult(
            ok=False,
            path=_path(workspace, feature),
            errors=errors,
        ))
    data = _load_task_directory(Path(args.task_dir).resolve(), feature)
    errors = _task_set_preflight_errors(feature_dir, data, group_data, args.code_workspace)
    return render_result(WriterResult(
        ok=not errors,
        path=_path(workspace, feature),
        errors=errors,
        data={
            "grouping": _task_group_summary(group_data),
            "preflight": _task_set_summary(data),
        } if not errors else {},
    ))


def _cmd_preflight_task_groups(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    group_data = _load_task_group_file(Path(args.group_file).resolve(), feature)
    errors = _task_group_preflight_errors(_path(workspace, feature).parent, group_data)
    return render_result(WriterResult(
        ok=not errors,
        path=Path(args.group_file).resolve(),
        errors=errors,
        data={"grouping": _task_group_summary(group_data)} if not errors else {},
    ))


def _cmd_materialize_task_set(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    existing = fail_if_artifact_exists(_path(workspace, feature), force=args.force)
    if existing:
        return render_result(existing)
    plans_dir = _path(workspace, feature).parent / "plans"
    group_data = _load_task_group_file(Path(args.group_file).resolve(), feature)
    feature_dir = _path(workspace, feature).parent
    errors = _task_group_preflight_errors(feature_dir, group_data)
    if errors:
        return render_result(WriterResult(ok=False, path=_path(workspace, feature), errors=errors))
    data = _load_task_directory(Path(args.task_dir).resolve(), feature)
    errors = _task_set_preflight_errors(feature_dir, data, group_data, args.code_workspace)
    if errors:
        return render_result(WriterResult(ok=False, path=_path(workspace, feature), errors=errors))
    data["taskSetStatus"] = "finalized"
    summary = _task_set_summary(data)
    result = _write(workspace, feature, data)
    if result.ok and args.force and plans_dir.is_dir():
        referenced = {item["id"] for item in summary["batches"]}
        for old_plan in plans_dir.glob("B*/plan.json"):
            if old_plan.parent.name not in referenced:
                old_plan.unlink(missing_ok=True)
                try:
                    old_plan.parent.rmdir()
                except OSError:
                    pass
    return render_result(with_result_data(result, materialized=summary))


def _cmd_add_task(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    _require_collecting(data)
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
    if task_execution_lane(task) == "backend" and any(
        task_execution_lane(existing) == "frontend" for existing in _tasks(data)
    ):
        return render_result(fail("backend_task_after_frontend", task_id, path=_path(workspace, feature)))
    _tasks(data).append(task)
    granularity_errors = validate_plan_task_granularity_item(task, task_id=task_id)
    if granularity_errors:
        return render_result(WriterResult(ok=False, path=_path(workspace, feature), errors=granularity_errors))
    structure_errors = _structure_errors(data)
    if structure_errors:
        return render_result(WriterResult(ok=False, path=_path(workspace, feature), errors=[{"reason": error} for error in structure_errors]))
    return render_result(_write(workspace, feature, data))


def _cmd_finalize_task_set(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    errors = _task_set_validation_errors(data)
    if errors:
        return render_result(WriterResult(ok=False, path=_path(workspace, feature), errors=errors))
    expected, covered = _scenario_coverage(_path(workspace, feature).parent, _tasks(data))
    missing = sorted(expected - covered)
    if missing:
        return render_result(
            fail(
                "missing_plan_scenario_coverage",
                f"return_to_scenario_matrix;ids={','.join(missing)}",
                path=_path(workspace, feature),
            )
        )
    data["taskSetStatus"] = "finalized"
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
                    "taskTemplateStatus": "deprecated_legacy_import_only",
                    "taskDetailTemplate": TASK_DETAIL_TEMPLATE_RELATIVE_PATH,
                    "taskDetailInputExample": _task_detail_input_example(),
                    "taskGroupTemplate": TASK_GROUP_TEMPLATE_RELATIVE_PATH,
                    "taskGroupInputExample": _task_group_example(),
                    "taskGroupMatrixExceptionExample": _task_group_matrix_exception_example(),
                    "taskGroupUiRequiredExample": _task_group_ui_required_example(),
                    "recommendedInputMode": "draft-batch",
                    "supportedInputModes": ["draft-batch", "body-file", "body-stdin", "body-json"],
                    "deprecatedInputModes": ["task-directory", "task-json", "cli-fields"],
                    "legacyTaskDirectoryMigration": (
                        "import-task-directory --group-file <file> --task-dir <directory> "
                        "--code-workspace <path>"
                    ),
                    "requiredTaskFields": [
                        "title",
                        "goal",
                        "specRefs",
                        "implementationPoints",
                        "acceptanceCriteria",
                        "validationBoundary",
                        "nonGoals",
                        "validationCommands",
                    ],
                    "requiredTaskGroupFields": [
                        "id",
                        "title",
                        "deps",
                        "uiRequired",
                        "specRefs",
                        "mergedScenarioRefs",
                        "apiIds",
                        "validationBoundary",
                    ],
                    "exampleOnlyTaskFields": ["matrixExceptionExample"],
                    "exampleOnlyTaskGroupFields": ["matrixExceptionExample", "uiRequiredExample"],
                    "groupOwnedTaskFields": sorted(DRAFT_GROUP_OWNED_FIELDS),
                    "requiredTaskDetailFields": sorted(DRAFT_REQUIRED_DETAIL_FIELDS),
                    "writerOwnedDetailFields": {
                        "acceptanceCriteria": ["id"],
                        "validationCommands": ["id"],
                        "scope": ["pages", "workspaceRoots"],
                    },
                    "fieldRules": {
                        "validationBoundary": {
                            "required": True,
                            "type": "non_empty_string",
                            "minLength": 10,
                            "source": "task_group",
                        },
                        "nonGoals": {
                            "required": True,
                            "minItems": 1,
                            "items": "non_empty_string",
                        },
                    },
                    "validationKinds": sorted(TASK_VALIDATION_KINDS),
                    "batchValidationKinds": sorted(BATCH_VALIDATION_KINDS),
                    "validationCoverage": {
                        "rule": "required_commands_cover_all_acceptance_criteria",
                        "compileMayCoverAcceptanceCriteria": False,
                    },
                    "taskValidationPolicy": {
                        "mode": "deferred_batch",
                        "orchestration": "single_batch_subagent",
                        "failStrategy": "fail_fast",
                        "maxConcurrency": 1,
                        "agentScope": "task_and_batch_validation_commands",
                        "taskCommandTiming": "after_all_batch_tasks_implemented",
                        "batchCommandTiming": "after_all_task_commands_pass",
                        "validationTarget": "batch_final_snapshot",
                    },
                    "workspaceContract": {
                        "field": "scope.workspaceRoots",
                        "source": "prepare-task-draft --code-workspace",
                        "singleRepositoryExample": {"default": "path/from/git-root/to/code-workspace"},
                        "multiRepositoryExample": {"repo-id": "path/from/git-root/to/code-workspace"},
                        "scopePathsBase": "declared_code_workspace",
                        "validationCwdBase": "git_root",
                        "codeWorkspacePreflightRequired": True,
                        "forbidRepeatedWorkspacePrefixInScopePaths": True,
                    },
                    "batchAssignment": {
                        "strategy": BATCH_STRATEGY,
                        "maxTasks": MAX_BATCH_TASKS,
                        "manualBatchIdSupported": False,
                        "primaryCapabilitySource": "first_spec_ref_file",
                        "executionLaneSource": "uiRequired",
                        "executionLaneMapping": {
                            "uiRequired_false": "backend",
                            "uiRequired_true": "frontend",
                        },
                        "executionLaneOrder": ["backend", "frontend"],
                        "appendRule": "same_primary_capability_and_execution_lane_as_immediately_preceding_batch_and_not_full",
                    },
                    "taskSetFinalization": {
                        "groupingPreflightCommand": "preflight-task-groups --group-file <file>",
                        "prepareCommand": (
                            "prepare-task-draft --group-file <file> --code-workspace <path>"
                        ),
                        "detailCommand": "set-draft-task-detail --task-id <id> --body-stdin",
                        "preflightCommand": "preflight-task-draft",
                        "command": "finalize-task-draft",
                        "coverage": "all_path_qualified_spec_scenarios",
                        "requiredBefore": [
                            "set-batch-validation-mode",
                            "add-batch-validation-command",
                            "add-project-validation-command",
                            "render-md",
                            "smoke_plan_writer.init",
                        ],
                    },
                    "collectingRepairs": {
                        "replace": "set-draft-task-detail --task-id <id> --body-stdin",
                        "rebuild": "rebuild-task-draft --group-file <file>",
                        "atomic": True,
                        "preserveUnchangedTaskDetails": True,
                    },
                    "formalArtifacts": {
                        "root": "plan.json",
                        "batches": "plans/Bxxx/plan.json",
                        "draftRoot": f"{DRAFT_RELATIVE_DIR}/plan.json",
                        "draftBatches": f"{DRAFT_RELATIVE_DIR}/plans/Bxxx/plan.json",
                        "draftLock": f"{DRAFT_RELATIVE_DIR}/lock.json",
                        "ownership": "writer-owned",
                        "integrityField": "taskSetDigest",
                        "directEditingSupported": False,
                    },
                    "forbiddenArguments": ["--batch-id", "--spec-refs", "--design-refs", "--decision-ids"],
                    "draftWorkflow": {
                        "groupLock": "groupingDigest",
                        "groupChangeError": "task_group_changed_after_draft_created",
                        "detailWriteMode": "validate_then_atomic_replace",
                        "standaloneTaskFiles": False,
                        "acceptanceAndValidationIds": "writer_generated",
                        "scopePagesSource": "uiRefs.pageRefs",
                        "scopeWorkspaceRootsSource": "prepare-task-draft --code-workspace",
                        "defaultValidationCwdSource": "scope.workspaceRoots",
                    },
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
                        "requiredFields": ["id", "argv", "cwd", "kind", "required"],
                        "allowedKinds": sorted(PROJECT_VALIDATION_KINDS),
                        "mustNotDuplicateBatchProfile": True,
                    },
                    "batchValidationCommand": {
                        "command": (
                            "add-batch-validation-command --lane <backend|frontend> "
                            "--command <command> --code-workspace <path>"
                        ),
                        "requiredFields": ["argv", "cwd", "kind", "required"],
                        "requiredPerUsedLane": "commands_mode_only",
                        "defaultCwd": "declared_workspace_root",
                    },
                    "batchValidationMode": {
                        "command": (
                            "set-batch-validation-mode --lane <backend|frontend> "
                            "--mode <task_covered|commands>"
                        ),
                        "taskCoveredRequirements": (
                            "one required targeted Maven lifecycle command per task in one workspace"
                        ),
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


def _cmd_add_batch_validation_command(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    lane_workspace_roots = {
        tuple(sorted(task_workspace_roots(task).items()))
        for task in _tasks(data)
        if task_execution_lane(task) == args.lane and task_workspace_roots(task)
    }
    if len(lane_workspace_roots) > 1:
        return render_result(fail(
            "batch_validation_profile_crosses_workspaces",
            args.lane,
            path=_path(workspace, feature),
        ))
    workspace_roots = dict(next(iter(lane_workspace_roots))) if lane_workspace_roots else {}
    root_key = "default" if "default" in workspace_roots else args.repo
    if workspace_roots and not isinstance(root_key, str):
        return render_result(fail(
            "batch_validation_repository_required",
            args.lane,
            path=_path(workspace, feature),
        ))
    command = {
        "argv": shlex.split(args.command),
        "cwd": args.cwd or workspace_roots.get(str(root_key), "."),
        "kind": args.kind,
        "required": not args.optional,
        **({"repo": args.repo} if args.repo else {}),
    }
    if workspace_roots:
        if not args.code_workspace:
            return render_result(fail(
                "code_workspace_preflight_required",
                "add-batch-validation-command",
                path=_path(workspace, feature),
            ))
        contexts = _code_workspace_contexts(args.code_workspace)
        command_errors = _command_workspace_preflight_errors(
            command,
            context_name=f"batchValidationProfiles.{args.lane}",
            workspace_roots=workspace_roots,
            contexts=contexts,
        )
        if command_errors:
            return render_result(WriterResult(
                ok=False,
                path=_path(workspace, feature),
                errors=command_errors,
            ))
    profiles = data.setdefault("batchValidationProfiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        data["batchValidationProfiles"] = profiles
    profile = profiles.setdefault(args.lane, {"mode": "commands", "commands": []})
    profile["mode"] = "commands"
    commands = profile.setdefault("commands", [])
    if not isinstance(commands, list):
        commands = []
        profile["commands"] = commands
    commands.append(command)
    return render_result(_write(workspace, feature, data))


def _cmd_set_batch_validation_mode(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    lane_tasks = [task for task in _tasks(data) if task_execution_lane(task) == args.lane]
    if not lane_tasks:
        return render_result(fail("batch_validation_lane_unused", args.lane, path=_path(workspace, feature)))
    profiles = data.setdefault("batchValidationProfiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        data["batchValidationProfiles"] = profiles
    profile = profiles.setdefault(args.lane, {"mode": args.mode, "commands": []})
    profile["mode"] = args.mode
    if args.mode == "task_covered":
        profile["commands"] = []
    else:
        profile.setdefault("commands", [])
    return render_result(_write(workspace, feature, data))


def _cmd_add_project_validation_command(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    guard = require_finalized_plan(workspace, feature)
    if guard:
        return render_result(guard)
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


def record_task_implementation(
    workspace: Path,
    feature: str,
    task_id: str,
    evidence_id: str,
    *,
    expected_task_contract_sha256: str,
) -> WriterResult:
    """Bind implementation evidence without running or completing task validation."""

    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        if not deferred_task_validation_enabled(data):
            return fail("deferred_task_validation_not_enabled", task_id, path=_path(workspace, feature))
        task = _find_task(data, task_id)
        if task_contract_sha256(task) != expected_task_contract_sha256:
            return fail("task_contract_changed_after_start", task_id, path=_path(workspace, feature))
        if normalize_status(task.get("status")) != "in_progress":
            return fail("task_not_in_progress", task_id, path=_path(workspace, feature))
        task["evidenceIds"] = _append_unique(
            task.get("evidenceIds") if isinstance(task.get("evidenceIds"), list) else [],
            [evidence_id],
        )
        task["implementationEvidenceIds"] = _append_unique(
            task.get("implementationEvidenceIds")
            if isinstance(task.get("implementationEvidenceIds"), list)
            else [],
            [evidence_id],
        )
        task["latestImplementationEvidenceId"] = evidence_id
        task["implementationRevision"] = int(task.get("implementationRevision", 0)) + 1
        task["completionEvidenceIds"] = []
        task["latestPassEvidenceId"] = None
        task["status"] = "implemented"

        batch_id = _batch_for_task(data, task_id)
        batch_plans = data.get("_batchPlans")
        batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
        if not isinstance(batch_plan, dict) or not isinstance(batch_plan.get("taskValidation"), dict):
            return fail("task_validation_contract_missing", batch_id, path=_path(workspace, feature))
        batch_tasks = [item for item in batch_plan.get("tasks", []) if isinstance(item, dict)]
        task_validation = batch_plan["taskValidation"]
        all_implemented = bool(batch_tasks) and all(
            normalize_status(item.get("status")) in {"implemented", "done"}
            for item in batch_tasks
        )
        task_validation["status"] = "ready" if all_implemented else "pending"
        task_validation["activeRunId"] = None
        task_validation["currentTaskId"] = None
        task_validation["batchSnapshotSha256"] = None
        data["status"] = "in_progress"
        data["activeBatchId"] = batch_id

        result = _write(workspace, feature, data)
        if not result.ok:
            return result
        if all_implemented:
            return with_result_data(result, taskValidation={
                "requiredAction": "run_batch_task_validation",
                "activeBatchId": batch_id,
                "taskIds": [str(item.get("id")) for item in batch_tasks],
                "status": "ready",
            })

        tasks_by_id = {
            str(item.get("id")): item
            for item in _tasks(data)
            if isinstance(item.get("id"), str)
        }
        next_task = next(
            (
                item
                for item in batch_tasks
                if normalize_status(item.get("status")) == "todo"
                and all(
                    (
                        normalize_status(tasks_by_id.get(dep, {}).get("status"))
                        in {"implemented", "done"}
                        if _batch_for_task(data, dep) == batch_id
                        else normalize_status(tasks_by_id.get(dep, {}).get("status")) == "done"
                    )
                    for dep in item.get("deps", [])
                    if isinstance(dep, str) and dep in tasks_by_id
                )
            ),
            None,
        )
        if next_task is not None:
            return with_result_data(result, batchContinuation={
                "requiredAction": "continue_active_batch",
                "continueCurrentBatch": True,
                "activeBatchId": batch_id,
                "nextTaskId": str(next_task.get("id")),
            })
        return result


def start_deferred_task_validation(
    workspace: Path,
    feature: str,
    batch_id: str,
    run_id: str,
    batch_snapshot_sha256: str,
) -> WriterResult:
    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        if not deferred_task_validation_enabled(data):
            return fail("deferred_task_validation_not_enabled", batch_id, path=_path(workspace, feature))
        batch_plans = data.get("_batchPlans")
        batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
        if not isinstance(batch_plan, dict):
            return fail("batch_not_found", batch_id, path=_path(workspace, feature))
        validation = batch_plan.get("taskValidation")
        if not isinstance(validation, dict):
            return fail("task_validation_contract_missing", batch_id, path=_path(workspace, feature))
        if validation.get("status") not in {"ready", "failed"}:
            return fail(
                "task_validation_not_startable",
                f"batch={batch_id};status={validation.get('status')}",
                path=_path(workspace, feature),
            )
        prior_snapshot = validation.get("batchSnapshotSha256")
        if validation.get("status") == "failed" and prior_snapshot != batch_snapshot_sha256:
            return fail(
                "task_validation_workspace_changed_after_failure",
                batch_id,
                path=_path(workspace, feature),
            )
        batch_tasks = [item for item in batch_plan.get("tasks", []) if isinstance(item, dict)]
        remaining = [
            item
            for item in batch_tasks
            if normalize_status(item.get("status")) != "done"
        ]
        if not remaining or any(
            normalize_status(item.get("status")) not in {"implemented", "failed"}
            for item in remaining
        ):
            return fail("task_validation_requires_implemented_tasks", batch_id, path=_path(workspace, feature))
        current = remaining[0]
        current["status"] = "validating"
        validation["status"] = "running"
        validation["activeRunId"] = run_id
        validation["currentTaskId"] = str(current.get("id"))
        validation["batchSnapshotSha256"] = batch_snapshot_sha256
        validation.setdefault("completedTaskIds", [
            str(item.get("id")) for item in batch_tasks if normalize_status(item.get("status")) == "done"
        ])
        data["status"] = "in_progress"
        data["activeBatchId"] = batch_id
        return _write(workspace, feature, data)


def record_deferred_task_validation_attempt(
    workspace: Path,
    feature: str,
    batch_id: str,
    task_id: str,
    run_id: str,
    evidence_ids: list[str],
    *,
    completion_evidence_ids: list[str],
    success: bool,
    batch_snapshot_sha256: str,
) -> WriterResult:
    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        batch_plans = data.get("_batchPlans")
        batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
        if not isinstance(batch_plan, dict):
            return fail("batch_not_found", batch_id, path=_path(workspace, feature))
        validation = batch_plan.get("taskValidation")
        if not isinstance(validation, dict):
            return fail("task_validation_contract_missing", batch_id, path=_path(workspace, feature))
        if (
            validation.get("status") != "running"
            or validation.get("activeRunId") != run_id
            or validation.get("currentTaskId") != task_id
            or validation.get("batchSnapshotSha256") != batch_snapshot_sha256
        ):
            return fail("task_validation_binding_mismatch", task_id, path=_path(workspace, feature))
        task = _find_task(data, task_id)
        if normalize_status(task.get("status")) != "validating":
            return fail("task_not_validating", task_id, path=_path(workspace, feature))
        task["evidenceIds"] = _append_unique(
            task.get("evidenceIds") if isinstance(task.get("evidenceIds"), list) else [],
            evidence_ids,
        )
        task["validationEvidenceIds"] = _append_unique(
            task.get("validationEvidenceIds")
            if isinstance(task.get("validationEvidenceIds"), list)
            else [],
            evidence_ids,
        )
        task["completionEvidenceIds"] = list(completion_evidence_ids) if success else []
        task["latestPassEvidenceId"] = (
            completion_evidence_ids[-1] if success and completion_evidence_ids else None
        )
        task["status"] = "done" if success else "failed"
        pending_revalidation = task.get("pendingRevalidation")
        if success and isinstance(pending_revalidation, dict):
            task["completedRevalidation"] = {
                **pending_revalidation,
                "completionEvidenceIds": list(completion_evidence_ids),
            }
            task.pop("pendingRevalidation", None)
        validation["evidenceIds"] = _append_unique(
            validation.get("evidenceIds") if isinstance(validation.get("evidenceIds"), list) else [],
            evidence_ids,
        )
        latest_by_task = validation.get("latestPassEvidenceByTask")
        latest_by_task = latest_by_task if isinstance(latest_by_task, dict) else {}
        latest_by_task[task_id] = list(completion_evidence_ids) if success else []
        validation["latestPassEvidenceByTask"] = latest_by_task

        if not success:
            validation["status"] = "failed"
            validation["activeRunId"] = None
            validation["lastRunId"] = run_id
            validation["currentTaskId"] = task_id
            data["status"] = "failed"
            data["activeBatchId"] = batch_id
            return _write(workspace, feature, data)

        completed = validation.get("completedTaskIds")
        completed = completed if isinstance(completed, list) else []
        validation["completedTaskIds"] = _append_unique(completed, [task_id])
        batch_tasks = [item for item in batch_plan.get("tasks", []) if isinstance(item, dict)]
        next_task = next(
            (item for item in batch_tasks if normalize_status(item.get("status")) != "done"),
            None,
        )
        result_data: dict[str, Any]
        if next_task is None:
            validation["status"] = "passed"
            validation["activeRunId"] = None
            validation["lastRunId"] = run_id
            validation["currentTaskId"] = None
            result_data = {"taskValidationPassed": True, "activeBatchId": batch_id}
        else:
            next_task["status"] = "validating"
            validation["currentTaskId"] = str(next_task.get("id"))
            result_data = {
                "taskValidationPassed": False,
                "activeBatchId": batch_id,
                "nextTaskId": str(next_task.get("id")),
            }
        data["status"] = "in_progress"
        data["activeBatchId"] = batch_id
        result = _write(workspace, feature, data)
        return with_result_data(result, **result_data) if result.ok else result


def invalidate_deferred_task_validation_for_repair(
    workspace: Path,
    feature: str,
    batch_id: str,
    repair_task_id: str,
) -> WriterResult:
    """Invalidate current validation pointers before a source-changing repair."""

    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        batch_plans = data.get("_batchPlans")
        batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
        if not isinstance(batch_plan, dict):
            return fail("batch_not_found", batch_id, path=_path(workspace, feature))
        validation = batch_plan.get("taskValidation")
        if not isinstance(validation, dict) or validation.get("status") not in {"failed", "passed"}:
            return fail("task_validation_repair_not_allowed", batch_id, path=_path(workspace, feature))
        for task in batch_plan.get("tasks", []):
            if not isinstance(task, dict):
                continue
            task["completionEvidenceIds"] = []
            task["latestPassEvidenceId"] = None
            task["status"] = "in_progress" if task.get("id") == repair_task_id else "implemented"
        validation.update({
            "status": "invalidated",
            "activeRunId": None,
            "currentTaskId": None,
            "batchSnapshotSha256": None,
            "completedTaskIds": [],
            "latestPassEvidenceByTask": {},
        })
        batch_validation = batch_plan.get("batchValidation")
        if isinstance(batch_validation, dict):
            batch_validation["status"] = (
                "revalidation_required" if isinstance(batch_validation.get("activeRunId"), str) else "pending"
            )
            batch_validation["latestPassEvidenceIds"] = []
        data["status"] = "in_progress"
        data["activeBatchId"] = batch_id
        return _write(workspace, feature, data)


def record_task_attempt(
    workspace: Path,
    feature: str,
    task_id: str,
    evidence_ids: list[str],
    *,
    completion_evidence_ids: list[str],
    success: bool,
    expected_task_contract_sha256: str | None = None,
    revalidation: dict[str, Any] | None = None,
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
        pending_revalidation = task.get("pendingRevalidation")
        if isinstance(pending_revalidation, dict) and revalidation != pending_revalidation:
            return fail(
                "batch_revalidation_metadata_mismatch",
                task_id,
                path=_path(workspace, feature),
            )
        existing = task.get("evidenceIds") if isinstance(task.get("evidenceIds"), list) else []
        task["evidenceIds"] = _append_unique(existing, evidence_ids)
        task["completionEvidenceIds"] = list(completion_evidence_ids) if success else []
        task["latestPassEvidenceId"] = completion_evidence_ids[-1] if success and completion_evidence_ids else None
        task["status"] = "done" if success else "failed"
        if success and isinstance(pending_revalidation, dict):
            task["completedRevalidation"] = {
                **pending_revalidation,
                "completionEvidenceIds": list(completion_evidence_ids),
            }
        if success:
            task.pop("pendingRevalidation", None)
        batch_id = _batch_for_task(data, task_id)
        batch_tasks = [
            item
            for item in _tasks(data)
            if _batch_for_task(data, str(item.get("id"))) == batch_id
        ]
        batch_completed = success and all(normalize_status(item.get("status")) == "done" for item in batch_tasks)
        root_entries = [entry for entry in data.get("batches", []) if isinstance(entry, dict)]
        ordered_ids = [str(entry.get("id")) for entry in root_entries]
        batch_check: dict[str, Any] | None = None
        continuation: dict[str, Any] | None = None
        if batch_completed:
            batch_plans = data.get("_batchPlans")
            batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
            validation: dict[str, Any] | None = None
            if isinstance(batch_plan, dict):
                raw_validation = batch_plan.get("batchValidation")
                if isinstance(raw_validation, dict):
                    validation = raw_validation
                    validation["status"] = "pending"
            data["status"] = "in_progress"
            data["activeBatchId"] = batch_id
            batch_check = {
                "requiredAction": "run_batch_check",
                "activeBatchId": batch_id,
                "batchValidationStatus": "pending",
                "mode": (
                    validation.get("mode", "commands" if validation.get("commands") else None)
                    if isinstance(validation, dict)
                    else None
                ),
            }
        elif success:
            tasks_by_id = {
                str(item.get("id")): item
                for item in _tasks(data)
                if isinstance(item.get("id"), str)
            }
            next_task = next(
                (
                    item
                    for item in batch_tasks
                    if normalize_status(item.get("status")) in {"todo", "in_progress"}
                    and all(
                        normalize_status(tasks_by_id.get(dep, {}).get("status")) == "done"
                        for dep in item.get("deps", [])
                        if isinstance(dep, str)
                    )
                ),
                None,
            )
            if next_task is not None:
                continuation = {
                    "requiredAction": "continue_active_batch",
                    "continueCurrentBatch": True,
                    "activeBatchId": batch_id,
                    "nextTaskId": str(next_task.get("id")),
                }
        result = _write(workspace, feature, data)
        if result.ok:
            write_text(_md_path(workspace, feature), _render_plan_md(data))
            if batch_check is not None:
                result = with_result_data(result, batchCheck=batch_check)
            elif continuation is not None:
                result = with_result_data(result, batchContinuation=continuation)
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


def start_batch_validation_run(
    workspace: Path,
    feature: str,
    batch_id: str,
    run_id: str,
) -> WriterResult:
    """Publish a batch run identity before executing its first command."""

    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        batch_plans = data.get("_batchPlans")
        batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
        if not isinstance(batch_plan, dict):
            return fail("batch_not_found", batch_id, path=_path(workspace, feature))
        validation = batch_plan.get("batchValidation")
        if not isinstance(validation, dict):
            return fail("batch_validation_contract_missing", batch_id, path=_path(workspace, feature))
        active_run_id = validation.get("activeRunId")
        if isinstance(active_run_id, str) and active_run_id != run_id:
            return fail("active_batch_run_exists", active_run_id, path=_path(workspace, feature))
        if validation.get("status") == "passed":
            return fail("batch_validation_already_passed", batch_id, path=_path(workspace, feature))
        validation["status"] = "running"
        validation["activeRunId"] = run_id
        data["status"] = "in_progress"
        data["activeBatchId"] = batch_id
        result = _write(workspace, feature, data)
        if result.ok:
            write_text(_md_path(workspace, feature), _render_plan_md(data))
        return result


def _build_batch_handoff(
    workspace: Path,
    feature: str,
    data: dict[str, Any],
    batch_id: str,
    batch_tasks: list[dict[str, Any]],
    passing_evidence_ids: list[str],
) -> dict[str, Any] | None:
    entries = [entry for entry in data.get("batches", []) if isinstance(entry, dict)]
    ordered_ids = [str(entry.get("id")) for entry in entries]
    try:
        batch_index = ordered_ids.index(batch_id)
    except ValueError:
        return None
    if batch_index + 1 >= len(entries):
        return None
    next_entry = entries[batch_index + 1]
    next_batch = str(next_entry.get("id"))
    if data.get("status") != "awaiting_next_conversation" or data.get("nextBatchId") != next_batch:
        return None
    user_message = f"当前批次 {batch_id} 已完成，请打开新的对话继续执行 {next_batch}。"
    return {
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
        "batchValidationEvidenceIds": list(passing_evidence_ids),
        "nextBatch": {
            "title": str(next_entry.get("title", "")),
            "taskIds": list(next_entry.get("taskIds", [])),
            "specRoots": list(next_entry.get("specRoots", [])),
            "deps": list(next_entry.get("deps", [])),
        },
        "status": "awaiting_next_conversation",
        "requiredAction": "stop_and_open_new_conversation",
        "requiresNewConversation": True,
        "userMessage": user_message,
        "createdAt": _utc_now(),
        "activationCommand": (
            f"python hooks/task_runner.py code-session --workspace {workspace} "
            f"--feature {feature}"
        ),
        "instruction": user_message,
    }


def record_batch_validation_attempt(
    workspace: Path,
    feature: str,
    batch_id: str,
    evidence_ids: list[str],
    *,
    success: bool,
    run_id: str,
    passing_evidence_ids: list[str] | None = None,
    expected_mode: str = "commands",
) -> WriterResult:
    """Bind one batch validation attempt and advance only after a passing gate."""

    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        batch_plans = data.get("_batchPlans")
        batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
        if not isinstance(batch_plan, dict):
            return fail("batch_not_found", batch_id, path=_path(workspace, feature))
        batch_tasks = [item for item in batch_plan.get("tasks", []) if isinstance(item, dict)]
        unfinished = [
            str(item.get("id"))
            for item in batch_tasks
            if normalize_status(item.get("status")) != "done"
        ]
        if unfinished:
            return fail(
                "batch_validation_requires_tasks_done",
                ",".join(unfinished),
                path=_path(workspace, feature),
            )
        if deferred_task_validation_enabled(data):
            task_validation = batch_plan.get("taskValidation")
            if not isinstance(task_validation, dict) or task_validation.get("status") != "passed":
                return fail(
                    "batch_validation_requires_task_validation_passed",
                    batch_id,
                    path=_path(workspace, feature),
                )
        validation = batch_plan.get("batchValidation")
        if not isinstance(validation, dict):
            return fail("batch_validation_contract_missing", batch_id, path=_path(workspace, feature))
        actual_mode = validation.get("mode", "commands" if validation.get("commands") else None)
        if actual_mode != expected_mode:
            return fail(
                "batch_validation_mode_mismatch",
                f"expected={expected_mode};actual={actual_mode}",
                path=_path(workspace, feature),
            )
        history = validation.get("evidenceIds")
        history = history if isinstance(history, list) else []
        passing_ids = list(passing_evidence_ids if passing_evidence_ids is not None else evidence_ids)
        if not success:
            passing_ids = []
        already_bound = (
            all(evidence_id in history for evidence_id in evidence_ids)
            and validation.get("status") == ("passed" if success else "failed")
            and validation.get("latestPassEvidenceIds") == passing_ids
            and (success or validation.get("activeRunId") == run_id)
        )
        if already_bound:
            result = WriterResult(ok=True, path=_path(workspace, feature), changed=False)
            handoff_path = _handoff_path(workspace, feature)
            handoff = load_json(handoff_path) if handoff_path.is_file() else None
            if handoff is None and success:
                handoff = _build_batch_handoff(
                    workspace,
                    feature,
                    data,
                    batch_id,
                    batch_tasks,
                    passing_ids,
                )
                if handoff is not None:
                    result = WriterResult(
                        ok=True,
                        path=_path(workspace, feature),
                        changed=atomic_write_json(handoff_path, handoff),
                    )
            plan_md_changed = write_text(_md_path(workspace, feature), _render_plan_md(data))
            if plan_md_changed and not result.changed:
                result = WriterResult(ok=True, path=result.path, changed=True)
            if isinstance(handoff, dict) and handoff.get("completedBatchId") == batch_id:
                result = with_result_data(result, batchHandoff=handoff)
            return result
        validation["evidenceIds"] = _append_unique(history, evidence_ids)
        validation["latestPassEvidenceIds"] = passing_ids
        validation["activeRunId"] = None if success else run_id
        validation["status"] = "passed" if success else "failed"

        handoff: dict[str, Any] | None = None
        if success:
            batch_plan["completedAt"] = _utc_now()
            entries = [entry for entry in data.get("batches", []) if isinstance(entry, dict)]
            ordered_ids = [str(entry.get("id")) for entry in entries]
            batch_index = ordered_ids.index(batch_id)
            if batch_index + 1 < len(ordered_ids):
                next_batch = ordered_ids[batch_index + 1]
                data["status"] = "awaiting_next_conversation"
                data["activeBatchId"] = None
                data["nextBatchId"] = next_batch
                handoff = _build_batch_handoff(
                    workspace,
                    feature,
                    data,
                    batch_id,
                    batch_tasks,
                    passing_ids,
                )
            else:
                data["status"] = "in_progress"
                data["activeBatchId"] = None
                data["nextBatchId"] = None
        else:
            data["status"] = "failed"
            data["activeBatchId"] = batch_id

        result = _write(workspace, feature, data)
        if result.ok:
            write_text(_md_path(workspace, feature), _render_plan_md(data))
            if handoff is not None:
                atomic_write_json(_handoff_path(workspace, feature), handoff)
                result = with_result_data(result, batchHandoff=handoff)
        return result


def record_task_covered_batch(
    workspace: Path,
    feature: str,
    batch_id: str,
    evidence_id: str,
    *,
    run_id: str,
) -> WriterResult:
    """Close a task-covered batch using one aggregate closure evidence record."""

    return record_batch_validation_attempt(
        workspace,
        feature,
        batch_id,
        [evidence_id],
        success=True,
        run_id=run_id,
        passing_evidence_ids=[evidence_id],
        expected_mode="task_covered",
    )


def request_batch_revalidation(
    workspace: Path,
    feature: str,
    batch_id: str,
    evidence_ids: list[str],
    *,
    affected_task_ids: list[str],
    run_id: str,
    attempt_evidence_ids: list[str] | None = None,
) -> WriterResult:
    """Bind a passing repair check and reopen affected TASK validation pointers."""

    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        batch_plans = data.get("_batchPlans")
        batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
        if not isinstance(batch_plan, dict):
            return fail("batch_not_found", batch_id, path=_path(workspace, feature))
        validation = batch_plan.get("batchValidation")
        if not isinstance(validation, dict):
            return fail("batch_validation_contract_missing", batch_id, path=_path(workspace, feature))
        history = validation.get("evidenceIds")
        history = history if isinstance(history, list) else []
        all_attempt_ids = list(attempt_evidence_ids if attempt_evidence_ids is not None else evidence_ids)
        validation["evidenceIds"] = _append_unique(history, all_attempt_ids)
        validation["latestPassEvidenceIds"] = list(evidence_ids)
        validation["activeRunId"] = run_id
        validation["status"] = "revalidation_required"
        batch_plan["completedAt"] = None

        affected = set(affected_task_ids)
        known = {
            str(task.get("id"))
            for task in batch_plan.get("tasks", [])
            if isinstance(task, dict)
        }
        if not affected or not affected.issubset(known):
            return fail(
                "batch_revalidation_tasks_invalid",
                ",".join(sorted(affected - known)),
                path=_path(workspace, feature),
            )
        deferred = deferred_task_validation_enabled(data)
        if deferred:
            affected = set(known)
        for task in batch_plan.get("tasks", []):
            if not isinstance(task, dict) or task.get("id") not in affected:
                continue
            existing_pending = task.get("pendingRevalidation")
            expected_pending = {
                "attemptType": "batch_revalidation",
                "triggeredByBatchEvidenceIds": list(evidence_ids),
                "supersedesEvidenceIds": (
                    list(task.get("completionEvidenceIds", []))
                    if isinstance(task.get("completionEvidenceIds"), list)
                    else []
                ),
            }
            if isinstance(existing_pending, dict):
                if (
                    existing_pending.get("attemptType") != "batch_revalidation"
                    or existing_pending.get("triggeredByBatchEvidenceIds") != list(evidence_ids)
                ):
                    return fail(
                        "batch_revalidation_binding_conflict",
                        str(task.get("id")),
                        path=_path(workspace, feature),
                    )
                continue
            superseded = (
                list(task.get("completionEvidenceIds", []))
                if isinstance(task.get("completionEvidenceIds"), list)
                else []
            )
            task["pendingRevalidation"] = {**expected_pending, "supersedesEvidenceIds": superseded}
            task["completionEvidenceIds"] = []
            task["latestPassEvidenceId"] = None
            task["status"] = "implemented" if deferred else "todo"

        if deferred:
            task_validation = batch_plan.get("taskValidation")
            if not isinstance(task_validation, dict):
                return fail("task_validation_contract_missing", batch_id, path=_path(workspace, feature))
            task_validation.update({
                "status": "ready",
                "activeRunId": None,
                "currentTaskId": None,
                "batchSnapshotSha256": None,
                "completedTaskIds": [],
                "latestPassEvidenceByTask": {},
            })

        data["status"] = "in_progress"
        data["activeBatchId"] = batch_id
        result = _write(workspace, feature, data)
        if result.ok:
            write_text(_md_path(workspace, feature), _render_plan_md(data))
            result = with_result_data(result, affectedTaskIds=sorted(affected))
        return result


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
        bundle = load_plan_bundle(
            path.parent,
            require_initial_status=args.initial,
            require_all_done=args.done,
        )
        for task in bundle.tasks:
            for error in validate_plan_task_granularity_item(task, task_id=str(task.get("id", "task"))):
                detail = error.get("detail")
                errors.append(f"{error['reason']}:{detail}" if detail else error["reason"])
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
                f"- 验证边界: {task.get('validationBoundary', '')}",
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
    guard = require_finalized_plan(workspace, feature)
    if guard:
        return render_result(guard)
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
    parser.add_argument(
        "--workspace-root",
        action="append",
        help="default workspace root or repo=workspace root, relative to the Git root",
    )
    parser.add_argument("--scope-path", action="append")
    parser.add_argument("--implementation-point", action="append")
    parser.add_argument("--acceptance-criterion", action="append")
    parser.add_argument("--validation-boundary")
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

    replace_task = sub.add_parser("replace-task")
    _task_selector(replace_task)
    replace_task.add_argument("--body-file", required=True)
    replace_task.set_defaults(func=_cmd_replace_task)

    remove_task = sub.add_parser("remove-task")
    _task_selector(remove_task)
    remove_task.set_defaults(func=_cmd_remove_task)

    prepare_task_draft = sub.add_parser("prepare-task-draft")
    _common(prepare_task_draft)
    prepare_task_draft.add_argument("--group-file", required=True)
    prepare_task_draft.add_argument("--code-workspace", required=True, action="append")
    prepare_task_draft.add_argument("--force", action="store_true")
    prepare_task_draft.set_defaults(func=_cmd_prepare_task_draft)

    import_task_directory = sub.add_parser("import-task-directory")
    _common(import_task_directory)
    import_task_directory.add_argument("--group-file", required=True)
    import_task_directory.add_argument("--task-dir", required=True)
    import_task_directory.add_argument("--code-workspace", required=True, action="append")
    import_task_directory.add_argument("--force", action="store_true")
    import_task_directory.set_defaults(func=_cmd_import_task_directory)

    draft_detail = sub.add_parser("set-draft-task-detail")
    _task_selector(draft_detail)
    draft_detail_input = draft_detail.add_mutually_exclusive_group(required=True)
    draft_detail_input.add_argument("--body-file")
    draft_detail_input.add_argument("--body-stdin", action="store_true")
    draft_detail_input.add_argument("--body-json")
    draft_detail.set_defaults(func=_cmd_set_draft_task_detail)

    preflight_task_draft = sub.add_parser("preflight-task-draft")
    _common(preflight_task_draft)
    preflight_task_draft.set_defaults(func=_cmd_preflight_task_draft)

    show_task_draft = sub.add_parser("show-task-draft")
    _common(show_task_draft)
    show_task_draft.set_defaults(func=_cmd_show_task_draft)

    rebuild_task_draft = sub.add_parser("rebuild-task-draft")
    _common(rebuild_task_draft)
    rebuild_task_draft.add_argument("--group-file", required=True)
    rebuild_task_draft.add_argument("--code-workspace", action="append")
    rebuild_task_draft.set_defaults(func=_cmd_rebuild_task_draft)

    finalize_task_draft = sub.add_parser("finalize-task-draft")
    _common(finalize_task_draft)
    finalize_task_draft.add_argument("--force", action="store_true")
    finalize_task_draft.set_defaults(func=_cmd_finalize_task_draft)

    preflight_task_groups = sub.add_parser("preflight-task-groups")
    _common(preflight_task_groups)
    preflight_task_groups.add_argument("--group-file", required=True)
    preflight_task_groups.set_defaults(func=_cmd_preflight_task_groups)

    preflight_task_set = sub.add_parser("preflight-task-set")
    _common(preflight_task_set)
    preflight_task_set.add_argument("--group-file", required=True)
    preflight_task_set.add_argument("--task-dir", required=True)
    preflight_task_set.add_argument("--code-workspace", action="append")
    preflight_task_set.set_defaults(func=_cmd_preflight_task_set)

    materialize_task_set = sub.add_parser("materialize-task-set")
    _common(materialize_task_set)
    materialize_task_set.add_argument("--group-file", required=True)
    materialize_task_set.add_argument("--task-dir", required=True)
    materialize_task_set.add_argument("--code-workspace", action="append")
    materialize_task_set.add_argument("--force", action="store_true")
    materialize_task_set.set_defaults(func=_cmd_materialize_task_set)

    add_task_contract = sub.add_parser("add-task-contract")
    add_task_contract.set_defaults(func=_cmd_add_task_contract)

    finalize_task_set = sub.add_parser("finalize-task-set")
    _common(finalize_task_set)
    finalize_task_set.set_defaults(func=_cmd_finalize_task_set)

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
        choices=sorted(TASK_VALIDATION_KINDS),
        default="behavior_test",
    )
    validation_command.add_argument("--repo")
    validation_command.add_argument("--optional", action="store_true")
    validation_command.add_argument("--covers", action="append")
    validation_command.set_defaults(func=_cmd_add_validation_command)

    batch_validation = sub.add_parser("add-batch-validation-command")
    _common(batch_validation)
    batch_validation.add_argument("--lane", choices=sorted(EXECUTION_LANES), required=True)
    batch_validation.add_argument("--command", required=True)
    batch_validation.add_argument("--cwd")
    batch_validation.add_argument("--repo")
    batch_validation.add_argument("--code-workspace", action="append")
    batch_validation.add_argument(
        "--kind",
        choices=sorted(BATCH_VALIDATION_KINDS),
        default="compile",
    )
    batch_validation.add_argument("--optional", action="store_true")
    batch_validation.set_defaults(func=_cmd_add_batch_validation_command)

    batch_validation_mode = sub.add_parser("set-batch-validation-mode")
    _common(batch_validation_mode)
    batch_validation_mode.add_argument("--lane", choices=sorted(EXECUTION_LANES), required=True)
    batch_validation_mode.add_argument("--mode", choices=["commands", "task_covered"], required=True)
    batch_validation_mode.set_defaults(func=_cmd_set_batch_validation_mode)

    project_validation = sub.add_parser("add-project-validation-command")
    _common(project_validation)
    project_validation.add_argument("--command-id")
    project_validation.add_argument("--command", required=True)
    project_validation.add_argument("--cwd", default=".")
    project_validation.add_argument("--repo")
    project_validation.add_argument(
        "--kind",
        choices=sorted(PROJECT_VALIDATION_KINDS),
        default="integration_test",
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
            current = _load(workspace, feature)
            _require_no_running_task_validation(current, args.command)
            if args.command in PLANNING_MUTATION_COMMANDS:
                _require_collecting(current)
            return args.func(args)
    except PlanWriterInputError as exc:
        return render_result(fail(exc.reason, exc.detail))
    except Exception as exc:
        return render_result(fail("plan_writer_failed", str(exc)))


if __name__ == "__main__":
    raise SystemExit(main())
