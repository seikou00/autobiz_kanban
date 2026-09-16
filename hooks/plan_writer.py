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
    shell_join,
    with_result_data,
    write_text,
    WriterError,
    WriterEncodingError,
)
from hooks.evidence_kernel import FileLock, unlink_if_exists  # noqa: E402
from hooks.implementation_scope import load_scope  # noqa: E402
from hooks.plan_json import (  # noqa: E402
    BATCH_COMPILE_MAX_REPAIR_ATTEMPTS,
    BATCH_STRATEGY,
    EXECUTION_LANES,
    FRONTEND_ROUTES,
    MAX_BATCH_TASKS,
    PARALLEL_EXECUTION_STAGES,
    PROJECT_VALIDATION_KINDS,
    REPOSITORY_ID_RE,
    TASK_EXECUTION_MODES,
    TASK_VALIDATION_KINDS,
    VISUAL_SOURCE_ID_RE,
    atomic_group_errors,
    batch_plan_path,
    defer_to_test_stages_enabled,
    load_plan_bundle,
    normalize_status,
    task_execution_lane,
    task_execution_mode,
    task_contract_sha256,
    task_set_digest,
    task_workspace_roots,
    validation_command_manifest_names,
    validate_plan_bundle_data,
    validate_task_collection,
)
from hooks.plan_granularity import (  # noqa: E402
    PLAN_TASK_HARD_MAX_APIS,
    PLAN_TASK_HARD_MAX_UI_INTERACTIONS,
    PLAN_TASK_HARD_MAX_UI_PAGES,
    PLAN_TASK_MATRIX_MAX_SCENARIOS,
    PLAN_TASK_MAX_APIS,
    PLAN_TASK_MAX_SCENARIOS,
    PLAN_TASK_MAX_UI_INTERACTIONS,
    PLAN_TASK_MAX_UI_PAGES,
    PLAN_TASK_SPLIT_RATIONALE_MIN_IDS_BY_PREFIX,
    PLAN_TASK_SPLIT_RATIONALE_MIN_LENGTH,
    scenario_refs_from_spec_refs,
    validate_plan_task_granularity_item,
    validate_plan_task_grouping_item,
)
from hooks.plan_write_ownership import normalize_owned_path, write_ownership_violations  # noqa: E402
from hooks.repository_snapshot import (  # noqa: E402
    RepositorySnapshotError,
    resolve_git_root,
)
from hooks.validation_policy import (  # noqa: E402
    compile_only_package_scripts_errors,
    BEHAVIOR_TASK_VALIDATION_KINDS,
    FRONTEND_COMPILE_VALIDATION_KINDS,
    check_maven_test_target_ambiguity,
    maven_project_selector_workspace_errors,
    package_script_name,
    package_script_policy_errors,
)
from hooks.artifact_ref_validator import (  # noqa: E402
    design_contract_snapshot,
    validate_plan_design_coverage,
    validate_task_artifact_refs,
    validate_task_group_design_contract,
)
from hooks.design_contract_lock import (  # noqa: E402
    DESIGN_CONTRACT_LOCK_FILE,
    load_confirmed_design_contract,
)
from hooks.parallel_validation_ownership import build_pipeline_contract  # noqa: E402
from board_core.state_store import load_state_json_records_result  # noqa: E402


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
TASK_GROUP_TEMPLATE_RELATIVE_PATH = "skills/autodev/autodev-plan/templates/task-groups.json"
TASK_GROUP_TEMPLATE_PATH = ROOT / TASK_GROUP_TEMPLATE_RELATIVE_PATH
TASK_DETAIL_TEMPLATE_RELATIVE_PATH = "skills/autodev/autodev-plan/templates/task-detail-input.json"
TASK_DETAIL_TEMPLATE_PATH = ROOT / TASK_DETAIL_TEMPLATE_RELATIVE_PATH
DRAFT_CORE_RELATIVE_DIR = ".tmp/plan_writer"
DEFAULT_TASK_GROUPS_FILENAME = "task-groups.json"
DRAFT_RELATIVE_DIR = ".tmp/plan_writer/draft"
DRAFT_LOCK_FILE = "lock.json"
DRAFT_PLAN_FILE = "plan.json"
DRAFT_TRANSACTION_FILE = ".draft-write-transaction.json"
DRAFT_REPAIR_WORK_RELATIVE_DIR = ".tmp/plan_writer/repair-work"
PLAN_CORE_SCHEMA = "autodev.plan-core.v1"
PLAN_SCHEMA = "autodev.plan.v2"
PLAN_DETAIL_SCHEMA = "autodev.plan-detail.v1"
PLAN_REPAIR_WORK_SCHEMA = "autodev.plan-repair-work.v1"
PLAN_REPAIR_PATCH_SCHEMA = "autodev.plan-repair-patch.v1"
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
    "workspaceRef",
    "executionMode",
    "externalDependency",
    "executionStage",
    "atomicGroup",
    "touches",
    "writeTargets",
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
COMPACT_PLAN_CORE_FORBIDDEN_FIELDS = (
    DRAFT_GROUP_OWNED_FIELDS - {"id"}
) | DRAFT_DETAIL_FIELDS
DRAFT_SCOPE_FIELDS = {"modules", "entrypoints", "dataObjects", "paths"}
TASK_REPAIR_BODY_FIELDS = {"repairs"}
TASK_ID_IN_REASON_RE = re.compile(r"^(T\d{3})\.([A-Za-z][A-Za-z0-9]*(?:\[[0-9]+\])?)")
TASK_ID_IN_DETAIL_RE = re.compile(r"(?:^|;)task=(T\d{3})(?:;|$)")
TASK_IDS_IN_DETAIL_RE = re.compile(r"(?:^|;)taskIds=([^;]+)(?:;|$)")
TASK_CONTEXT_IN_DETAIL_RE = re.compile(
    r"(?:^|;)context=(T\d{3})\.([A-Za-z][A-Za-z0-9]*(?:\[[0-9]+\])?)(?:;|$)"
)
TASK_DETAIL_PATCH_FIELDS = set(DRAFT_DETAIL_FIELDS)
REPAIR_PATCH_PATH_RE = re.compile(r"^/tasks/(T\d{3})/([A-Za-z][A-Za-z0-9]*)$")
REPAIR_GROUP_PATCH_PATH_RE = re.compile(
    r"^/groups/(T\d{3})/(dependsOn|writeSet|validation\.mergeJustification)$"
)
REPAIR_WORK_ID_RE = re.compile(r"^RW-\d+-[0-9a-f]{8}$")
GROUP_REPAIR_FIELD_TO_RUNTIME_FIELD = {
    "dependsOn": "deps",
    "writeSet": "touches",
    "validation.mergeJustification": "splitRationale",
}
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
DEFAULT_TASK_VALIDATION_POLICY = {
    "mode": "defer_to_test_stages",
    "orchestration": "inline",
    "codeGate": "review_only",
    "maxTestStageRepairAttempts": BATCH_COMPILE_MAX_REPAIR_ATTEMPTS,
}
DRAFT_BUNDLE_COMMANDS = {
    "prepare-task-draft",
    "import-task-directory",
    "set-draft-task-detail",
    "set-draft-task-details",
    "lint-draft-task-detail",
    "lint-draft-task-details",
    "write-task-groups",
    "repair-draft-task",
    "repair-draft-tasks",
    "preflight-task-draft",
    "show-task-draft",
    "show-draft-task-work",
    "create-repair-work",
    "apply-draft-patch",
    "rebuild-task-draft",
    "rebuild-finalized-draft",
    "reopen-finalized-draft",
    "diagnose-plan-repair",
    "finalize-task-draft",
    "add-quality-gate-command",
    "add-project-validation-command",
}
DRAFT_RUNTIME_GUARDED_COMMANDS = DRAFT_BUNDLE_COMMANDS - {
    "diagnose-plan-repair",
    "reopen-finalized-draft",
    "rebuild-finalized-draft",
    "show-task-draft",
    "show-draft-task-work",
    "lint-draft-task-detail",
    "lint-draft-task-details",
    "create-repair-work",
}
PLAN_REOPEN_ALLOWED_CHECKPOINTS = {
    "specs_done",
    "design_done",
    "plan_in_progress",
    "plan_done",
    "detail_design_in_progress",
}


class PlanWriterInputError(ValueError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}:{detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _path(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, PLAN_FILE)


def _default_task_groups_path(workspace: Path, feature: str) -> Path:
    """Return the single writer-owned Plan Core source location for a Feature."""

    return artifact_path(
        workspace,
        feature,
        f"{DRAFT_CORE_RELATIVE_DIR}/{DEFAULT_TASK_GROUPS_FILENAME}",
    )


def _md_path(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, PLAN_MD_FILE)


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


def _draft_repair_work_dir(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, DRAFT_REPAIR_WORK_RELATIVE_DIR)


def _json_digest(value: Any) -> str:
    """Return a stable digest suitable for optimistic-concurrency checks."""

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _current_design_contract(feature_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    # Plan deliberately consumes the snapshot produced by dev.design instead
    # of re-opening design.md.  A Design edit must re-run its own completion
    # gate and refresh this snapshot before Plan can start.
    return load_confirmed_design_contract(feature_dir, feature_dir.name)


def _draft_design_contract_errors(
    feature_dir: Path,
    lock: dict[str, Any],
) -> list[dict[str, Any]]:
    contract, errors = _current_design_contract(feature_dir)
    if errors:
        return errors
    snapshot = lock.get("designContract")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("sha256"), str):
        return [{
            "reason": "task_draft_design_contract_lock_missing",
            "repairTarget": "draft_integrity",
            "repairable": False,
        }]
    expected = snapshot["sha256"]
    actual = contract.get("sha256")
    if expected != actual:
        return [{
            "reason": "confirmed_design_changed_after_draft_created",
            "detail": (
                f"expected={expected};actual={actual};"
                "design_contract_lock_changed;explicit_design_revision_required"
            ),
            "repairTarget": "design_revision",
            "repairable": False,
            "designMutationAllowed": False,
        }]
    return []


def _plan_lock(workspace: Path, feature: str) -> FileLock:
    return FileLock(_path(workspace, feature).parent / ".plan.lock")






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
    scenario_refs = [f"specs/[capability]/spec.md#SCN-{index:03d}" for index in range(1, 7)]
    cited_scenarios = ", ".join([scenario_refs[0], scenario_refs[2], scenario_refs[5]])
    return {
        "id": "T001",
        "outcome": "[shared observable matrix behavior]",
        "refs": {
            "requirements": ["specs/[capability]/spec.md#REQ-001"],
            "scenarios": scenario_refs,
            "api": [],
        },
        "validation": {
            "seam": "one request returns the complete matrix and one executable assertion validates it",
            "mergeJustification": (
                f"{cited_scenarios} share one request/response and one validation loop "
                "and cannot be validated independently."
            ),
        },
    }


def _task_group_ui_required_example() -> dict[str, Any]:
    return {
        "id": "T001",
        "outcome": "[single observable UI behavior]",
        "ui": {
            "pages": ["PAGE-001"],
            "interactions": ["UIX-001"],
            "visualSources": ["VIS-001"],
            "route": "absolute-html",
        },
    }


def _task_group_external_dependency_example() -> dict[str, Any]:
    return {
        "id": "T001",
        "outcome": "[record externally owned outcome]",
        "mode": "external_dependency",
        "writeSet": [],
        "external": {
            "system": "[external-system-or-repository]",
            "owner": "[owning-team-or-person]",
            "trackingRefs": ["[ticket-or-design-reference]"],
        },
    }


def _initial(feature: str) -> dict[str, Any]:
    return {
        "featureId": feature,
        "status": "todo",
        "taskSetStatus": "collecting",
        "activeBatchId": None,
        "nextBatchId": None,
        "batchPolicy": {"maxTasks": MAX_BATCH_TASKS, "strategy": BATCH_STRATEGY},
        "taskValidationPolicy": copy.deepcopy(DEFAULT_TASK_VALIDATION_POLICY),
        "batches": [],
        "qualityGateProfiles": {},
        "projectValidationCommands": [],
        "projectCheckEvidenceIds": [],
        "latestProjectCheckEvidenceId": None,
        "projectValidationDisposition": None,
        "projectValidationFailedRunIds": [],
        "deferredValidationIssues": [],
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
    if "compileProfiles" in root:
        raise PlanWriterInputError("compile_profiles_retired_requires_rebuild")
    policy = root.get("batchPolicy")
    if isinstance(policy, dict) and policy.get("strategy") != BATCH_STRATEGY:
        raise PlanWriterInputError("batch_policy_requires_rebuild", str(policy.get("strategy")))
    finalized = root.get("taskSetStatus") == "finalized"
    if finalized and "qualityGateProfiles" not in root:
        raise PlanWriterInputError("quality_gate_contract_requires_rebuild", "qualityGateProfiles")
    data = dict(root)
    data.setdefault("featureId", feature)
    data.setdefault("status", "todo")
    data.setdefault("activeBatchId", None)
    data.setdefault("nextBatchId", None)
    data.setdefault("batchPolicy", {"maxTasks": MAX_BATCH_TASKS, "strategy": BATCH_STRATEGY})
    data.setdefault("batches", [])
    data.setdefault("qualityGateProfiles", {})
    data.setdefault("projectValidationCommands", [])
    data.setdefault("projectCheckEvidenceIds", [])
    data.setdefault("latestProjectCheckEvidenceId", None)
    data.setdefault("projectValidationDisposition", None)
    data.setdefault("projectValidationFailedRunIds", [])
    data.setdefault("deferredValidationIssues", [])
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
        if finalized and "qualityGateCommands" not in plan:
            raise PlanWriterInputError(
                "quality_gate_contract_requires_rebuild",
                f"{batch_id}.qualityGateCommands",
            )
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


def _load_raw_formal_bundle(
    workspace: Path,
    feature: str,
) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]], list[str]]:
    """Load a formal Bundle without enforcing its integrity digest.

    Recovery commands need to diagnose a Bundle whose contract files may have
    been edited outside the writer. They must not use this data as the repair
    source; the retained Draft remains the source of truth.
    """

    path = _path(workspace, feature)
    if not path.is_file() or path.stat().st_size <= 0:
        return None, {}, []
    try:
        root = load_json(path)
    except Exception as exc:
        return None, {}, [f"formal_plan_unreadable:{exc}"]
    if not isinstance(root, dict):
        return None, {}, ["formal_plan_root_must_be_object"]
    errors: list[str] = []
    batches: dict[str, dict[str, Any]] = {}
    entries = root.get("batches")
    if not isinstance(entries, list):
        return root, {}, ["formal_plan_batches_must_be_array"]
    feature_dir = path.parent
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            errors.append("formal_plan_batch_entry_invalid")
            continue
        batch_id = str(entry["id"])
        batch_path = batch_plan_path(feature_dir, batch_id)
        if not batch_path.is_file():
            errors.append(f"missing_batch_plan:{batch_id}")
            continue
        try:
            batch = load_json(batch_path)
        except Exception as exc:
            errors.append(f"batch_plan_unreadable:{batch_id}:{exc}")
            continue
        if not isinstance(batch, dict):
            errors.append(f"batch_plan_root_must_be_object:{batch_id}")
            continue
        batches[batch_id] = batch
    return root, batches, errors


def _feature_checkpoint(workspace: Path, feature: str) -> tuple[str | None, list[str]]:
    state = load_state_json_records_result(workspace)
    if not state.exists:
        return None, ["state_json_missing"]
    if state.errors:
        return None, [f"state_json_invalid:{error}" for error in state.errors]
    record = state.records.get(feature)
    if not isinstance(record, dict):
        return None, [f"feature_state_missing:{feature}"]
    checkpoint = record.get("checkpoint")
    if not isinstance(checkpoint, str) or not checkpoint:
        return None, [f"feature_checkpoint_missing:{feature}"]
    return checkpoint, []


def _formal_execution_blockers(
    workspace: Path,
    feature: str,
    root: dict[str, Any] | None,
    batches: dict[str, dict[str, Any]],
    load_errors: list[str],
) -> tuple[str | None, list[str]]:
    blockers = list(load_errors)
    checkpoint, state_errors = _feature_checkpoint(workspace, feature)
    blockers.extend(state_errors)
    if checkpoint is not None and checkpoint not in PLAN_REOPEN_ALLOWED_CHECKPOINTS:
        blockers.append(f"checkpoint_not_reopenable:{checkpoint}")
    if root is None:
        return checkpoint, blockers

    if root.get("taskSetStatus") != "finalized":
        blockers.append(f"formal_task_set_not_finalized:{root.get('taskSetStatus')}")
    root_status = root.get("status")
    if root_status not in {None, "todo"}:
        blockers.append(f"formal_plan_status_started:{root_status}")
    for field in (
        "projectCheckEvidenceIds",
        "projectValidationFailedRunIds",
    ):
        value = root.get(field)
        if isinstance(value, list) and value:
            blockers.append(f"formal_plan_runtime_data_present:{field}")
    if root.get("latestProjectCheckEvidenceId") is not None:
        blockers.append("formal_plan_runtime_data_present:latestProjectCheckEvidenceId")
    if root.get("projectValidationDisposition") is not None:
        blockers.append("formal_plan_runtime_data_present:projectValidationDisposition")

    for batch_id, batch in sorted(batches.items()):
        batch_status = batch.get("status")
        if batch_status not in {None, "todo"}:
            blockers.append(f"batch_started:{batch_id}:{batch_status}")
        if batch.get("startedAt") is not None or batch.get("completedAt") is not None:
            blockers.append(f"batch_runtime_timestamp_present:{batch_id}")
        if isinstance(batch.get("completionEvidenceIds"), list) and batch["completionEvidenceIds"]:
            blockers.append(f"batch_evidence_present:{batch_id}")
        for task in batch.get("tasks", []):
            if not isinstance(task, dict):
                blockers.append(f"task_invalid:{batch_id}")
                continue
            task_id = str(task.get("id", "task"))
            task_status = task.get("status")
            if task_status not in {None, "todo"}:
                blockers.append(f"task_started:{task_id}:{task_status}")
            for field in (
                "evidenceIds",
                "implementationEvidenceIds",
                "validationEvidenceIds",
                "completionEvidenceIds",
            ):
                value = task.get(field)
                if isinstance(value, list) and value:
                    blockers.append(f"task_evidence_present:{task_id}:{field}")
            for field in (
                "latestImplementationEvidenceId",
                "latestPassEvidenceId",
            ):
                if task.get(field) is not None:
                    blockers.append(f"task_evidence_present:{task_id}:{field}")
    return checkpoint, list(dict.fromkeys(blockers))


def _structure_errors(data: dict[str, Any], *, allow_empty: bool = False) -> list[str]:
    if allow_empty and _tasks(data) == []:
        errors: list[str] = []
        if "version" in data or "taskDetailVersion" in data:
            errors.append("legacy_plan_requires_rebuild")
        if not isinstance(data.get("featureId"), str) or not data.get("featureId"):
            errors.append("plan_json_missing_feature_id")
        return errors
    return validate_task_collection(
        str(data.get("featureId", "")),
        _tasks(data),
        defer_to_test_stages=defer_to_test_stages_enabled(data),
    )


def _task_groups(data: dict[str, Any]) -> list[dict[str, Any]]:
    groups = data.get("groups")
    if not isinstance(groups, list):
        return []
    return [item for item in groups if isinstance(item, dict)]


def _compact_string_list(value: Any, *, task_id: str, field: str) -> list[str]:
    """Read a compact-contract string list without silently inventing data."""

    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise PlanWriterInputError("compact_plan_core_string_array_required", f"task={task_id};field={field}")
    return [item.strip() for item in value]


def _compact_write_set(value: Any, *, task_id: str) -> list[str]:
    """Accept terse paths or ``{path, intent}`` entries and project ``touches``."""

    if not isinstance(value, list):
        raise PlanWriterInputError("compact_plan_core_write_set_required", f"task={task_id}")
    paths: list[str] = []
    for index, entry in enumerate(value, start=1):
        raw_path = entry if isinstance(entry, str) else entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise PlanWriterInputError(
                "compact_plan_core_write_set_path_invalid",
                f"task={task_id};index={index}",
            )
        paths.append(raw_path.strip())
    return paths


def _compact_write_targets(value: Any, *, task_id: str) -> list[dict[str, Any]]:
    """Preserve optional method/member anchors alongside physical write paths."""

    paths = _compact_write_set(value, task_id=task_id)
    targets: list[dict[str, Any]] = []
    for index, (entry, path) in enumerate(zip(value, paths), start=1):
        symbols = entry.get("symbols") if isinstance(entry, dict) else None
        if symbols is not None and (
            not isinstance(symbols, list)
            or not symbols
            or any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols)
        ):
            raise PlanWriterInputError(
                "compact_plan_core_write_set_symbols_invalid",
                f"task={task_id};index={index}",
            )
        target: dict[str, Any] = {"path": path}
        if symbols is not None:
            target["symbols"] = list(dict.fromkeys(symbol.strip() for symbol in symbols))
        targets.append(target)
    return targets


def _compact_plan_core_to_groups(data: dict[str, Any]) -> dict[str, Any]:
    """Project the model-facing compact Plan Core to the legacy group contract.

    Runtime Plan artifacts intentionally retain their established schema.  This
    adapter means that planner models only need to emit one reference map and
    one write set, while the writer remains backward compatible with existing
    ``task-groups.json`` files and all downstream schedulers.
    """

    if data.get("schemaVersion") == PLAN_SCHEMA:
        return _plan_v2_to_groups(data)
    if data.get("schemaVersion") != PLAN_CORE_SCHEMA:
        return data
    feature_id = data.get("featureId")
    raw_tasks = data.get("tasks")
    if not isinstance(feature_id, str) or not feature_id.strip():
        raise PlanWriterInputError("compact_plan_core_feature_id_missing")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlanWriterInputError("compact_plan_core_tasks_missing")

    groups: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_tasks, start=1):
        if not isinstance(raw, dict):
            raise PlanWriterInputError("compact_plan_core_task_must_be_object", f"index={index}")
        task_id = raw.get("id")
        if not isinstance(task_id, str) or not TASK_GROUP_TASK_ID_RE.fullmatch(task_id):
            raise PlanWriterInputError("compact_plan_core_task_id_invalid", f"index={index};task={task_id}")
        forbidden_fields = sorted(set(raw) & COMPACT_PLAN_CORE_FORBIDDEN_FIELDS)
        if forbidden_fields:
            raise PlanWriterInputError(
                "compact_plan_core_non_core_field_forbidden",
                f"task={task_id};fields={','.join(forbidden_fields)};"
                "use=outcome,dependsOn,writeSet,refs,validation",
            )
        outcome = raw.get("outcome")
        if not isinstance(outcome, str) or not outcome.strip():
            raise PlanWriterInputError("compact_plan_core_outcome_missing", f"task={task_id}")
        refs = raw.get("refs")
        if not isinstance(refs, dict):
            raise PlanWriterInputError("compact_plan_core_refs_missing", f"task={task_id}")
        requirements = _compact_string_list(refs.get("requirements"), task_id=task_id, field="refs.requirements")
        scenarios = _compact_string_list(refs.get("scenarios"), task_id=task_id, field="refs.scenarios")
        api_ids = _compact_string_list(refs.get("api", []), task_id=task_id, field="refs.api")
        validation = raw.get("validation")
        if not isinstance(validation, dict):
            raise PlanWriterInputError("compact_plan_core_validation_missing", f"task={task_id}")
        seam = validation.get("seam")
        if not isinstance(seam, str) or not seam.strip():
            raise PlanWriterInputError("compact_plan_core_validation_seam_missing", f"task={task_id}")

        write_targets = _compact_write_targets(raw.get("writeSet", []), task_id=task_id)
        group: dict[str, Any] = {
            "id": task_id,
            "title": outcome.strip(),
            "executionMode": raw.get("mode", "code"),
            "executionStage": raw.get("stage", "parallel"),
            "touches": [target["path"] for target in write_targets],
            "writeTargets": write_targets,
            "deps": _compact_string_list(raw.get("dependsOn", []), task_id=task_id, field="dependsOn"),
            "workspaceRef": raw.get("workspace", "default"),
            "specRefs": [*requirements, *scenarios],
            "apiIds": api_ids,
            "validationBoundary": seam.strip(),
        }
        merge_justification = validation.get("mergeJustification")
        if merge_justification is not None:
            if not isinstance(merge_justification, str) or not merge_justification.strip():
                raise PlanWriterInputError("compact_plan_core_merge_justification_invalid", f"task={task_id}")
            group["mergedScenarioRefs"] = scenarios
            group["splitRationale"] = merge_justification.strip()

        ui = raw.get("ui")
        group["uiRequired"] = ui is not None
        if ui is not None:
            if not isinstance(ui, dict):
                raise PlanWriterInputError("compact_plan_core_ui_must_be_object", f"task={task_id}")
            group["uiRefs"] = {
                "pageRefs": _compact_string_list(ui.get("pages"), task_id=task_id, field="ui.pages"),
                "interactionRefs": _compact_string_list(ui.get("interactions", []), task_id=task_id, field="ui.interactions"),
                "visualSourceRefs": _compact_string_list(ui.get("visualSources", []), task_id=task_id, field="ui.visualSources"),
                "frontendRoute": ui.get("route"),
            }
        external = raw.get("external")
        if external is not None:
            group["externalDependency"] = copy.deepcopy(external)
        atomic = raw.get("atomic")
        if atomic is not None:
            group["atomicGroup"] = copy.deepcopy(atomic)
        groups.append(group)
    return {"featureId": feature_id, "groups": groups}


def _plan_v2_to_groups(data: dict[str, Any]) -> dict[str, Any]:
    """Project the intentionally small Plan v2 input into runtime groups.

    Plan v2 is the only model-facing contract. It contains outcomes, repository
    ownership, dependencies, references, outcome-level implementation points
    and test points. File lists, symbols, concrete commands and batch mechanics
    are discovered or derived by later stages.
    """
    feature_id = data.get("featureId")
    raw_tasks = data.get("tasks")
    if not isinstance(feature_id, str) or not feature_id.strip():
        raise PlanWriterInputError("plan_v2_feature_id_missing")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlanWriterInputError("plan_v2_tasks_missing")

    groups: list[dict[str, Any]] = []
    allowed_task_fields = {
        "id", "outcome", "workspace", "dependsOn", "refs", "implementationPoints",
        "testPoints", "verification",
        "ui", "mode", "stage", "external", "atomic", "notes",
    }
    for index, raw in enumerate(raw_tasks, start=1):
        if not isinstance(raw, dict):
            raise PlanWriterInputError("plan_v2_task_must_be_object", f"index={index}")
        task_id = raw.get("id")
        if not isinstance(task_id, str) or not TASK_GROUP_TASK_ID_RE.fullmatch(task_id):
            raise PlanWriterInputError("plan_v2_task_id_invalid", f"index={index};task={task_id}")
        unknown = sorted(set(raw) - allowed_task_fields)
        if unknown:
            raise PlanWriterInputError("plan_v2_task_field_unknown", f"task={task_id};fields={','.join(unknown)}")
        outcome = raw.get("outcome")
        if not isinstance(outcome, str) or not outcome.strip():
            raise PlanWriterInputError("plan_v2_task_outcome_missing", f"task={task_id}")
        implementation_points = _compact_string_list(
            raw.get("implementationPoints"), task_id=task_id, field="implementationPoints",
        )
        if not implementation_points:
            raise PlanWriterInputError("plan_v2_task_implementation_points_missing", f"task={task_id}")
        test_points = _compact_string_list(
            raw.get("testPoints"), task_id=task_id, field="testPoints",
        )
        if not test_points:
            raise PlanWriterInputError("plan_v2_task_test_points_missing", f"task={task_id}")
        refs = raw.get("refs")
        if not isinstance(refs, dict):
            raise PlanWriterInputError("plan_v2_task_refs_missing", f"task={task_id}")
        requirements = _compact_string_list(refs.get("requirements"), task_id=task_id, field="refs.requirements")
        scenarios = _compact_string_list(refs.get("scenarios"), task_id=task_id, field="refs.scenarios")
        api_ids = _compact_string_list(refs.get("api", []), task_id=task_id, field="refs.api")
        design_refs = _compact_string_list(refs.get("design", []), task_id=task_id, field="refs.design")
        design_refs = [ref if "#" in ref else f"design.md#{ref}" for ref in design_refs]
        data_ids = _compact_string_list(refs.get("data", []), task_id=task_id, field="refs.data")
        decision_ids = _compact_string_list(refs.get("decisions", []), task_id=task_id, field="refs.decisions")
        verification = raw.get("verification")
        if verification is None:
            verification = {}
        if not isinstance(verification, dict):
            raise PlanWriterInputError("plan_v2_task_verification_invalid", f"task={task_id}")
        intent = verification.get("intent")
        if intent is not None and (not isinstance(intent, str) or not intent.strip()):
            raise PlanWriterInputError("plan_v2_task_verification_intent_invalid", f"task={task_id}")
        group: dict[str, Any] = {
            "id": task_id,
            "title": outcome.strip(),
            "executionMode": raw.get("mode", "code"),
            "executionStage": raw.get("stage", "parallel"),
            "touches": [],
            "writeTargets": [],
            "deps": _compact_string_list(raw.get("dependsOn", []), task_id=task_id, field="dependsOn"),
            "workspaceRef": raw.get("workspace", "default"),
            "specRefs": [*requirements, *scenarios],
            "apiIds": api_ids,
            "designRefs": design_refs,
            "dataIds": data_ids,
            "decisionIds": decision_ids,
            "validationBoundary": str(intent or outcome).strip(),
            "verificationIntent": str(intent or outcome).strip(),
            "implementationPoints": implementation_points,
            "testPoints": test_points,
        }
        ui = raw.get("ui")
        group["uiRequired"] = ui is not None
        if ui is not None:
            if not isinstance(ui, dict):
                raise PlanWriterInputError("plan_v2_task_ui_invalid", f"task={task_id}")
            group["uiRefs"] = {
                "pageRefs": _compact_string_list(ui.get("pages"), task_id=task_id, field="ui.pages"),
                "interactionRefs": _compact_string_list(ui.get("interactions", []), task_id=task_id, field="ui.interactions"),
                "visualSourceRefs": _compact_string_list(ui.get("visualSources", []), task_id=task_id, field="ui.visualSources"),
                "frontendRoute": ui.get("route"),
            }
        if raw.get("external") is not None:
            group["externalDependency"] = copy.deepcopy(raw["external"])
        if raw.get("atomic") is not None:
            group["atomicGroup"] = copy.deepcopy(raw["atomic"])
        groups.append(group)
    return {"featureId": feature_id, "groups": groups}


def _compact_detail_to_legacy(detail: dict[str, Any]) -> dict[str, Any]:
    """Accept the terse Detail contract while preserving legacy command input."""

    if detail.get("schemaVersion") != PLAN_DETAIL_SCHEMA:
        return detail
    unknown = sorted(set(detail) - {
        "schemaVersion", "outcome", "context", "implementation", "acceptance",
        "checks", "nonGoals", "refs", "expectedFiles", "blockers",
    })
    if unknown:
        raise PlanWriterInputError("compact_plan_detail_field_unknown", f"fields={','.join(unknown)}")
    refs = detail.get("refs")
    if not isinstance(refs, dict):
        raise PlanWriterInputError("compact_plan_detail_refs_missing")
    context = detail.get("context")
    if not isinstance(context, dict):
        raise PlanWriterInputError("compact_plan_detail_context_missing")
    return {
        # A Detail normally reuses the Plan Core outcome.  Keeping this marker
        # internal avoids asking the model to repeat goal/title for every task.
        "goal": detail.get("outcome"),
        "__compactGoalFromCore": "outcome" not in detail,
        "scope": {
            "modules": copy.deepcopy(context.get("modules", [])),
            "entrypoints": copy.deepcopy(context.get("entrypoints", [])),
            "dataObjects": copy.deepcopy(context.get("dataObjects", [])),
        },
        "implementationPoints": copy.deepcopy(detail.get("implementation")),
        "acceptanceCriteria": copy.deepcopy(detail.get("acceptance")),
        "nonGoals": copy.deepcopy(detail.get("nonGoals")),
        "designRefs": copy.deepcopy(refs.get("design", [])),
        "dataIds": copy.deepcopy(refs.get("data", [])),
        "decisionIds": copy.deepcopy(refs.get("decisions", [])),
        "validationCommands": copy.deepcopy(detail.get("checks")),
        "expectedFiles": copy.deepcopy(detail.get("expectedFiles", [])),
        "blockers": copy.deepcopy(detail.get("blockers", [])),
    }


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

        raw_execution_mode = raw_group.get("executionMode")
        if raw_execution_mode is not None and raw_execution_mode not in TASK_EXECUTION_MODES:
            errors.append({"reason": f"{task_id}.executionMode_invalid"})
        execution_stage = raw_group.get("executionStage", "parallel")
        if execution_stage not in PARALLEL_EXECUTION_STAGES:
            errors.append({"reason": f"{task_id}.executionStage_invalid"})
        atomic_group = raw_group.get("atomicGroup")
        if atomic_group is not None:
            if not isinstance(atomic_group, dict):
                errors.append({"reason": f"{task_id}.atomicGroup_must_be_object"})
            else:
                unknown = sorted(set(atomic_group) - {"id", "rationale"})
                if unknown:
                    errors.append({"reason": f"{task_id}.atomicGroup_unknown_fields:{','.join(unknown)}"})
                group_id = atomic_group.get("id")
                rationale = atomic_group.get("rationale")
                if not isinstance(group_id, str) or not re.fullmatch(r"AG\d{3}", group_id):
                    errors.append({"reason": f"{task_id}.atomicGroup.id_invalid"})
                if not isinstance(rationale, str) or len(rationale.strip()) < 10:
                    errors.append({"reason": f"{task_id}.atomicGroup.rationale_missing_or_too_short"})
        if "touches" in raw_group:
            _group_string_list(errors, raw_group, task_id, "touches", required=False)
        write_targets = raw_group.get("writeTargets")
        if write_targets is not None:
            if not isinstance(write_targets, list):
                errors.append({"reason": f"{task_id}.writeTargets_must_be_array"})
            else:
                for target_index, target in enumerate(write_targets, start=1):
                    if not isinstance(target, dict):
                        errors.append({"reason": f"{task_id}.writeTargets[{target_index}].must_be_object"})
                        continue
                    target_path = target.get("path")
                    symbols = target.get("symbols")
                    if not isinstance(target_path, str) or not target_path.strip():
                        errors.append({"reason": f"{task_id}.writeTargets[{target_index}].path_missing"})
                    if symbols is not None and (
                        not isinstance(symbols, list)
                        or not symbols
                        or any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols)
                    ):
                        errors.append({"reason": f"{task_id}.writeTargets[{target_index}].symbols_invalid"})
        execution_mode = task_execution_mode(raw_group)
        external_dependency = raw_group.get("externalDependency")
        if execution_mode == "external_dependency":
            if not isinstance(external_dependency, dict):
                errors.append({"reason": f"{task_id}.externalDependency_missing"})
            else:
                for field in ("system", "owner"):
                    value = external_dependency.get(field)
                    if not isinstance(value, str) or not value.strip():
                        errors.append({
                            "reason": f"{task_id}.externalDependency.{field}_missing"
                        })
                _group_string_list(
                    errors,
                    external_dependency,
                    task_id,
                    "trackingRefs",
                )
        elif external_dependency is not None:
            errors.append({"reason": f"{task_id}.externalDependency_forbidden"})

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
        if not isinstance(validation_boundary, str) or not validation_boundary.strip():
            errors.append({"reason": f"{task_id}.validationBoundary_missing"})
        workspace_ref = raw_group.get("workspaceRef")
        if not isinstance(workspace_ref, str) or not REPOSITORY_ID_RE.fullmatch(workspace_ref):
            errors.append({"reason": f"{task_id}.workspaceRef_invalid"})
        prior_ids.add(task_id)
    errors.extend({"reason": reason} for reason in atomic_group_errors(
        [item for item in raw_groups if isinstance(item, dict)],
    ))
    return errors


def _with_validation_stage(
    stage: str,
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Annotate independently-computed preflight failures with their stage."""

    annotated: list[dict[str, Any]] = []
    for error in errors:
        item = copy.deepcopy(error)
        item.setdefault("validationStage", stage)
        annotated.append(item)
    return annotated


def _task_group_spec_ref_errors(
    feature_dir: Path,
    groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Verify that every path-qualified Core scenario reference exists.

    Coverage alone catches a missing expected scenario, but it used to leave a
    misplaced ``SCN-110`` looking like an unrelated coverage gap.  This check
    reports the owning task, input field, and exact missing path first.
    """

    defined: dict[str, set[str]] = {}
    for spec_path in sorted((feature_dir / "specs").glob("**/*.md")):
        relative = spec_path.relative_to(feature_dir).as_posix()
        defined[relative] = set(SPEC_SCENARIO_DEF_RE.findall(
            spec_path.read_text(encoding="utf-8")
        ))

    errors: list[dict[str, Any]] = []
    for group in groups:
        task_id = str(group.get("id", "task"))
        refs = group.get("specRefs") if isinstance(group.get("specRefs"), list) else []
        for index, raw_ref in enumerate(refs):
            if not isinstance(raw_ref, str):
                continue
            path_part, separator, anchor = raw_ref.partition("#")
            scenario_ids = SCENARIO_ID_RE.findall(anchor) if separator else []
            if not scenario_ids:
                continue
            normalized_path = path_part.strip().replace("\\", "/")
            field = f"specRefs[{index}]"
            if not normalized_path:
                errors.append({
                    "reason": "plan_task_scenario_ref_not_path_qualified",
                    "detail": f"task={task_id};ref={raw_ref}",
                    "taskIds": [task_id],
                    "field": field,
                    "repairTarget": "task_group",
                    "repairSuggestion": "每个 SCN 必须写成 specs/<capability>/spec.md#SCN-NNN，不能只写 SCN ID。",
                })
                continue
            known_scenarios = defined.get(normalized_path)
            if known_scenarios is None:
                errors.append({
                    "reason": "plan_task_spec_file_unknown",
                    "detail": f"task={task_id};path={normalized_path}",
                    "taskIds": [task_id],
                    "field": field,
                    "repairTarget": "task_group",
                    "repairSuggestion": "将 specRefs 指向 Feature specs/ 下实际存在的 spec 文件。",
                })
                continue
            for scenario_id in scenario_ids:
                if scenario_id not in known_scenarios:
                    errors.append({
                        "reason": "unknown_plan_task_scenario_ref",
                        "detail": f"task={task_id};path={normalized_path};scenario={scenario_id}",
                        "taskIds": [task_id],
                        "field": field,
                        "repairTarget": "task_group",
                        "repairSuggestion": (
                            f"{normalized_path} 未定义 {scenario_id}；请改用该文件中真实的 SCN，"
                            "或将引用移到实际定义该 SCN 的 spec 文件。"
                        ),
                    })
    return errors


def _task_group_preflight_errors(feature_dir: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all independent Core failures in one pass.

    Later stages deliberately continue after structural, granularity, or
    ownership failures whenever their input is still readable.  A missing
    design lock is the only dependency that suppresses Design-ID validation;
    it does not suppress spec-reference validation or scenario coverage.
    """

    errors = _with_validation_stage("structure", _task_group_structure_errors(data))
    implementation_scope, scope_errors = load_scope(feature_dir)
    errors.extend(_with_validation_stage(
        "scope", [{"reason": error} for error in scope_errors],
    ))
    groups = _task_groups(data)
    grouping_errors: list[dict[str, Any]] = []
    for group in groups:
        task_id = str(group.get("id", "task"))
        ui_required = group.get("uiRequired") is True
        if implementation_scope == "backend_only" and ui_required:
            grouping_errors.append({
                "reason": "implementation_scope_frontend_task_forbidden",
                "detail": f"scope=backend_only;task={task_id}",
                "repairSuggestion": f"当前实现范围为 backend_only，但任务 {task_id} 标记为需要前端（uiRequired=true）。请将该任务的 uiRequired 改为 false，或修改 scope.md 中的实现范围"
            })
        elif implementation_scope == "frontend_only" and not ui_required:
            grouping_errors.append({
                "reason": "implementation_scope_backend_task_forbidden",
                "detail": f"scope=frontend_only;task={task_id}",
                "repairSuggestion": f"当前实现范围为 frontend_only，但任务 {task_id} 标记为后端任务（uiRequired=false）。请将该任务的 uiRequired 改为 true，或修改 scope.md 中的实现范围"
            })
        grouping_errors.extend(validate_plan_task_grouping_item(group, task_id=task_id))
    errors.extend(_with_validation_stage("granularity", grouping_errors))
    # ``touches`` is the candidate group's ownership declaration.  Validate it
    # before a Draft exists, otherwise a common SQL/config file only surfaces
    # later as a surprising sequence of single-Batch waves.
    group_tasks = [
        {
            "id": group.get("id"),
            "executionMode": group.get("executionMode", "code"),
            "executionStage": group.get("executionStage", "parallel"),
            "workspaceRef": group.get("workspaceRef"),
            "scope": {"paths": group.get("touches", [])},
            "writeTargets": copy.deepcopy(group.get("writeTargets", [])),
            "expectedFiles": [],
        }
        for group in groups
    ]
    group_scope_data = {
        "featureId": data.get("featureId"),
        "tasks": copy.deepcopy(group_tasks),
    }
    try:
        _, projected_group_batches = _project_batches(group_scope_data)
        group_scopes = {
            str(task.get("id")): batch_id
            for batch_id, batch in projected_group_batches.items()
            for task in batch.get("tasks", [])
            if isinstance(task, dict) and isinstance(task.get("id"), str)
        }
        ownership_errors = write_ownership_violations(
            group_tasks,
            ownership_scope_by_task=group_scopes,
        )
    except (TypeError, ValueError) as exc:
        ownership_errors = [{
            "reason": "task_group_ownership_projection_invalid",
            "detail": str(exc),
            "repairTarget": "task_group",
        }]
    errors.extend(_with_validation_stage("write_ownership", ownership_errors))

    design_contract, design_errors = _current_design_contract(feature_dir)
    errors.extend(_with_validation_stage("design_lock", design_errors))
    if not design_errors:
        errors.extend(_with_validation_stage(
            "design_refs", validate_task_group_design_contract(design_contract, groups),
        ))

    errors.extend(_with_validation_stage(
        "spec_refs", _task_group_spec_ref_errors(feature_dir, groups),
    ))
    expected, covered = _scenario_coverage(feature_dir, groups)
    missing = sorted(expected - covered)
    if missing:
        missing_count = len(missing)
        missing_preview = ', '.join(missing[:10])
        if missing_count > 10:
            missing_preview += f" ...还有 {missing_count - 10} 个"
        errors.extend(_with_validation_stage("scenario_coverage", [{
            "reason": "missing_plan_scenario_coverage",
            "detail": f"return_to_scenario_matrix;ids={','.join(missing)}",
            "repairSuggestion": (
                f"有 {missing_count} 个场景未被任务覆盖：{missing_preview}。"
                "请在 Plan Core 的 refs.scenarios 中添加或调整任务引用，确保所有场景都被覆盖。"
            ),
        }]))
    return errors


def _task_group_digest(data: dict[str, Any]) -> str:
    # A candidate group may spell an owned path as ``Repo:path``, ``Repo/path``
    # or a repository-relative path.  They identify the same write set, so a
    # cosmetic prefix change must not invalidate an otherwise unchanged Draft.
    groups: list[Any] = []
    raw_groups = data.get("groups")
    if isinstance(raw_groups, list):
        for raw_group in raw_groups:
            if not isinstance(raw_group, dict):
                groups.append(raw_group)
                continue
            group = copy.deepcopy(raw_group)
            touches = group.get("touches")
            if isinstance(touches, list):
                workspace_ref = group.get("workspaceRef")
                normalized = [
                    path for value in touches
                    if (path := normalize_owned_path(value, workspace_ref)) is not None
                ]
                group["touches"] = sorted(set(normalized))
            write_targets = group.get("writeTargets")
            if isinstance(write_targets, list):
                workspace_ref = group.get("workspaceRef")
                normalized_targets: list[dict[str, Any]] = []
                for target in write_targets:
                    if not isinstance(target, dict):
                        normalized_targets.append(target)
                        continue
                    path = normalize_owned_path(target.get("path"), workspace_ref)
                    normalized_target = copy.deepcopy(target)
                    if path is not None:
                        normalized_target["path"] = path
                    normalized_targets.append(normalized_target)
                group["writeTargets"] = normalized_targets
            groups.append(group)
    payload = {
        "featureId": data.get("featureId"),
        "groups": groups if isinstance(raw_groups, list) else raw_groups,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _task_group_projection(item: dict[str, Any]) -> dict[str, Any]:
    ui_refs = item.get("uiRefs") if isinstance(item.get("uiRefs"), dict) else {}
    result = {
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
        "workspaceRef": item.get("workspaceRef"),
        "executionMode": item.get("executionMode", "code"),
        "executionStage": item.get("executionStage", "parallel"),
        "atomicGroup": copy.deepcopy(item.get("atomicGroup")) if isinstance(item.get("atomicGroup"), dict) else None,
        "externalDependency": (
            copy.deepcopy(item.get("externalDependency"))
            if isinstance(item.get("externalDependency"), dict)
            else None
        ),
        "splitRationale": item.get("splitRationale") or None,
    }
    if (isinstance(item.get("touches"), list) and item.get("touches")) or (
        isinstance(item.get("scope"), dict)
        and isinstance(item["scope"].get("paths"), list)
        and bool(item["scope"]["paths"])
    ):
        raw = item.get("touches") if isinstance(item.get("touches"), list) else item["scope"].get("paths", [])
        result["touches"] = sorted({
            normalized
            for path in raw
            if (normalized := normalize_owned_path(path, item.get("workspaceRef"))) is not None
        })
    if isinstance(item.get("writeTargets"), list):
        result["writeTargets"] = copy.deepcopy(item["writeTargets"])
    return result


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


def _task_matches_group_projection(group: dict[str, Any], task: dict[str, Any]) -> bool:
    """Compare only candidate-group owned fields.

    A task detail may add concrete ``scope.paths`` after grouping.  When the
    group did not declare planning ``touches``, those detail paths must not
    invalidate an otherwise reusable draft task.
    """
    expected = _task_group_projection(group)
    actual = _task_group_projection(task)
    return all(actual.get(field) == value for field, value in expected.items())


def _task_set_validation_errors(
    data: dict[str, Any],
    *,
    allow_empty: bool = False,
) -> list[dict[str, Any]]:
    granularity_errors: list[dict[str, Any]] = []
    for task in _tasks(data):
        task_id = str(task.get("id", "task"))
        granularity_errors.extend(validate_plan_task_granularity_item(task, task_id=task_id))
    if granularity_errors:
        return granularity_errors

    structure_errors = [{"reason": reason} for reason in _structure_errors(data, allow_empty=allow_empty)]
    if structure_errors or (allow_empty and not _tasks(data)):
        return structure_errors
    return []


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
    batch_compile: dict[str, Any] | None = None,
) -> str:
    statuses = [normalize_status(task.get("status")) for task in batch_tasks]
    if any(status == "failed" for status in statuses):
        return "failed"
    if statuses and all(status == "done" for status in statuses):
        legacy_recorded = isinstance(batch_compile, dict) and batch_compile.get("status") in {"passed", "failed", "skipped"}
        return "done" if legacy_recorded else "in_progress"
    if any(status in {"in_progress", "implemented", "validating", "done"} for status in statuses):
        return "in_progress"
    return "todo"


def _batch_workspace_contract(task: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    roots = task_workspace_roots(task)
    workspace_ref = task.get("workspaceRef")
    if not roots and isinstance(workspace_ref, str) and workspace_ref:
        roots = {workspace_ref: "."}
    return tuple(sorted(roots.items()))


def _batch_frontend_route(task: dict[str, Any]) -> str:
    if task_execution_lane(task) != "frontend":
        return "none"
    ui_refs = task.get("uiRefs")
    route = ui_refs.get("frontendRoute") if isinstance(ui_refs, dict) else None
    return str(route) if route in FRONTEND_ROUTES else "spec-driven-ui"


def _batch_profile_command_matches_workspace(
    command: dict[str, Any],
    workspace_contract: tuple[tuple[str, str], ...],
) -> bool:
    if len(workspace_contract) != 1:
        return False
    repository = workspace_contract[0][0]
    command_repository = command.get("repo")
    if repository == "default":
        return command_repository in {None, "default"}
    return command_repository == repository


def _project_batches(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    tasks_view = _tasks(data)
    assignments = dict(data.get("_batchAssignments") or {})
    prior_plans = data.get("_batchPlans") if isinstance(data.get("_batchPlans"), dict) else {}
    groups: dict[str, list[dict[str, Any]]] = {}
    spec_roots: dict[str, str] = {}
    execution_lanes: dict[str, str] = {}
    execution_stages: dict[str, str] = {}
    workspace_contracts: dict[str, tuple[tuple[str, str], ...]] = {}
    frontend_routes: dict[str, str] = {}
    existing_ids = {
        str(entry.get("id"))
        for entry in data.get("batches", [])
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    used_ids = set(existing_ids)
    next_batch_number = max(
        (int(value[1:]) for value in used_ids if re.fullmatch(r"B\d{3}", value)),
        default=0,
    ) + 1

    def allocate_batch_id() -> str:
        """Allocate deterministically without rescanning all existing IDs."""

        nonlocal next_batch_number
        while True:
            batch_id = f"B{next_batch_number:03d}"
            next_batch_number += 1
            if batch_id not in used_ids:
                used_ids.add(batch_id)
                return batch_id

    # Default to one independently deliverable Task per Batch.  Similarity of
    # capability, route, workspace, or write set is not a reason to co-deliver.
    # The only grouping signal is an explicit atomicGroup id on every member.
    delivery_units: dict[str, list[dict[str, Any]]] = {}
    for task in tasks_view:
        raw_atomic = task.get("atomicGroup")
        atomic_id = raw_atomic.get("id") if isinstance(raw_atomic, dict) else None
        unit_key = f"atomic:{atomic_id}" if isinstance(atomic_id, str) else f"task:{task.get('id')}"
        delivery_units.setdefault(unit_key, []).append(task)

    for unit_tasks in delivery_units.values():
        assigned = sorted({
            str(assignments[str(task.get("id"))])
            for task in unit_tasks
            if str(task.get("id")) in assignments
        })
        batch_id = assigned[0] if assigned else allocate_batch_id()
        used_ids.add(batch_id)
        for task in unit_tasks:
            assignments[str(task.get("id", ""))] = batch_id
            task_stage = str(task.get("executionStage") or "parallel")
            if task_stage not in PARALLEL_EXECUTION_STAGES:
                raise PlanWriterInputError("invalid_batch_execution_stage", f"task={task.get('id')};stage={task_stage}")
            task.pop("touches", None)
            groups.setdefault(batch_id, []).append(task)
            spec_roots.setdefault(batch_id, _primary_spec_root(task))
            execution_lanes.setdefault(batch_id, task_execution_lane(task))
            execution_stages.setdefault(batch_id, task_stage)
            workspace_contracts.setdefault(batch_id, _batch_workspace_contract(task))
            frontend_routes.setdefault(batch_id, _batch_frontend_route(task))

    ordered_ids = sorted(groups)
    root = {
        key: value
        for key, value in data.items()
        if key not in {"tasks", "_batchAssignments", "_batchPlans", "parallelPolicy"}
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
        quality_profiles = root.get("qualityGateProfiles")
        quality_profile = (
            quality_profiles.get(execution_lane) if isinstance(quality_profiles, dict) else None
        )
        quality_profile_commands = (
            quality_profile.get("commands") if isinstance(quality_profile, dict) else []
        )
        if not isinstance(quality_profile_commands, list):
            quality_profile_commands = []
        workspace_contract = workspace_contracts[batch_id]
        quality_matches = [
            command
            for command in quality_profile_commands
            if isinstance(command, dict)
            and _batch_profile_command_matches_workspace(command, workspace_contract)
        ]
        quality_commands = [
            {**command, "id": f"BATCH-{batch_id}-QUALITY-{command_index:03d}"}
            for command_index, command in enumerate(quality_matches, start=1)
        ]
        status = _batch_status(batch_tasks)
        execution_stage = execution_stages.get(batch_id, "parallel")
        task_ids_list = [str(task.get("id")) for task in batch_tasks]
        atomic_group = batch_tasks[0].get("atomicGroup") if batch_tasks else None
        is_atomic_group = isinstance(atomic_group, dict)
        delivery_kind = "atomic_group" if is_atomic_group else "single_task"
        atomic_group_id = atomic_group.get("id") if is_atomic_group else None
        batch_rationale = atomic_group.get("rationale") if is_atomic_group else None
        projected[batch_id] = {
            "featureId": root.get("featureId"),
            "batchId": batch_id,
            "title": title,
            "executionLane": execution_lane,
            "executionStage": execution_stage,
            "status": status,
            "taskCount": len(batch_tasks),
            "completedTaskCount": sum(normalize_status(task.get("status")) == "done" for task in batch_tasks),
            "completionEvidenceIds": completion_ids,
            "taskIds": task_ids_list,
            "deliveryKind": delivery_kind,
            **({"atomicGroupId": atomic_group_id, "batchRationale": batch_rationale} if is_atomic_group else {}),
            "qualityGateCommands": quality_commands,
            **({"mergeCommitSha": previous.get("mergeCommitSha")} if "mergeCommitSha" in previous else {}),
            **({"deliveryRunId": previous.get("deliveryRunId")} if "deliveryRunId" in previous else {}),
            **({"mergedAt": previous.get("mergedAt")} if "mergedAt" in previous else {}),
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
        workspace_ref = workspace_contract[0][0] if len(workspace_contract) == 1 else None
        root_entries.append(
            {
                "id": batch_id,
                "path": f"plans/{batch_id}/plan.json",
                "title": title,
                "specRoots": [spec_root],
                "executionLane": execution_lane,
                "executionStage": execution_stage,
                **({"workspaceRef": workspace_ref} if workspace_ref else {}),
                "deps": sorted(cross_deps),
                "taskIds": [str(task.get("id")) for task in batch_tasks],
                "deliveryKind": delivery_kind,
                **({"atomicGroupId": atomic_group_id, "batchRationale": batch_rationale} if is_atomic_group else {}),
                "status": status,
                **({"mergeCommitSha": previous.get("mergeCommitSha")} if "mergeCommitSha" in previous else {}),
                **({"deliveryRunId": previous.get("deliveryRunId")} if "deliveryRunId" in previous else {}),
            }
        )
    root["batches"] = root_entries
    # The parallel workflow consumes this contract directly.  It deliberately
    # owns Review/Test execution inside each Batch and reserves integration
    # and E2E validation for runtime validation Batches, so Board-level stages
    # cannot execute the same command a second time after merge.
    root["parallelBatchPipeline"] = build_pipeline_contract(root, projected)
    deferred_issues: list[dict[str, Any]] = []
    seen_issue_ids: set[str] = set()
    project_disposition = root.get("projectValidationDisposition")
    project_issue_id = (
        project_disposition.get("issueId") if isinstance(project_disposition, dict) else None
    )
    if isinstance(project_issue_id, str) and project_issue_id not in seen_issue_ids:
        deferred_issues.append(copy.deepcopy(project_disposition))
    root["deferredValidationIssues"] = deferred_issues
    root["taskSetDigest"] = task_set_digest(root, projected)
    unfinished = [entry["id"] for entry in root_entries if entry["status"] != "done"]
    if not root_entries:
        root.update({"status": "todo", "activeBatchId": None, "nextBatchId": None})
    elif not unfinished:
        if data.get("status") == "failed":
            root["status"] = "failed"
        else:
            project_commands = root.get("projectValidationCommands")
            project_ready = (
                isinstance(project_commands, list)
                and (
                    not project_commands
                    or isinstance(root.get("latestProjectCheckEvidenceId"), str)
                    or isinstance(root.get("projectValidationDisposition"), dict)
                )
            )
            root["status"] = "done" if project_ready else "in_progress"
        root["activeBatchId"] = None
        root["nextBatchId"] = None
    elif len(unfinished) > 1:
        # Multi-Batch execution is selected from the dependency DAG, not the
        # presentation order of Batch IDs.
        root["activeBatchId"] = None
        root["nextBatchId"] = None
        if data.get("status") == "failed" or any(entry["status"] == "failed" for entry in root_entries):
            root["status"] = "failed"
        elif data.get("status") == "in_progress" or any(entry["status"] == "in_progress" for entry in root_entries):
            root["status"] = "in_progress"
        else:
            root["status"] = "todo"
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
        errors = validate_plan_bundle_data(
            root,
            batch_plans,
        )
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
        unlink_if_exists(transaction_path)
    try:
        # UI_CONTEXT.md exists before Plan. Refresh its derived task labels
        # whenever the formal Plan changes so it does not keep page summaries.
        from hooks.ui_context_writer import refresh_ui_context_md

        refresh_ui_context_md(workspace, feature, tasks=_tasks(data))
    except Exception as exc:
        # A human-readable sidecar must not invalidate a committed Plan.
        print(f"Warning: Failed to refresh UI_CONTEXT.md from Plan: {exc}", file=sys.stderr)
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
    referenced = set(batch_plans)
    plans_dir = _draft_dir(workspace, feature) / "plans"
    for batch_id, batch in batch_plans.items():
        atomic_write_json(_draft_batch_plan_path(workspace, feature, batch_id), batch)
    atomic_write_json(_draft_plan_path(workspace, feature), root)
    atomic_write_json(_draft_lock_path(workspace, feature), lock)
    if plans_dir.is_dir():
        for old_plan in plans_dir.glob("B*/plan.json"):
            if old_plan.parent.name not in referenced:
                unlink_if_exists(old_plan)
                try:
                    old_plan.parent.rmdir()
                except OSError:
                    pass
    unlink_if_exists(transaction_path)
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
    previous_revision = 0
    lock_path = _draft_lock_path(workspace, feature)
    if lock_path.is_file():
        previous = load_json(lock_path)
        if isinstance(previous, dict) and isinstance(previous.get("revision"), int):
            previous_revision = previous["revision"]
    requested_revision = lock.get("revision")
    if not isinstance(requested_revision, int) or isinstance(requested_revision, bool):
        requested_revision = 0
    lock["revision"] = max(previous_revision, requested_revision) + 1
    last_change = lock.get("lastChange")
    if isinstance(last_change, dict):
        last_change["revision"] = lock["revision"]
    lock["updatedAt"] = _utc_now()
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
    data = dict(root)
    data["tasks"] = tasks
    data["_batchAssignments"] = assignments
    data["_batchPlans"] = batch_plans
    return lock, data


def _draft_group_change_summary(
    group_data: dict[str, Any],
    task_items: list[dict[str, Any]],
) -> str:
    """Describe the Draft tasks that cannot be safely reused after a group edit."""

    tasks_by_id = {
        str(task.get("id")): task
        for task in task_items
        if isinstance(task, dict) and isinstance(task.get("id"), str)
    }
    changes: list[str] = []
    for group in _task_groups(group_data):
        task_id = str(group.get("id"))
        task = tasks_by_id.get(task_id)
        if task is None:
            changes.append(f"{task_id}:missing_draft_task")
            continue
        expected = _task_group_projection(group)
        actual = _task_group_projection(task)
        fields = [field for field, value in expected.items() if actual.get(field) != value]
        if fields:
            changes.append(f"{task_id}:{','.join(fields)}")
    return ",".join(changes) if changes else "unknown_group_change"


def _draft_group_drift(
    lock: dict[str, Any],
    feature: str,
    task_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Inspect a Draft's Core source without forcing callers into an exception.

    Finalized-plan recovery needs to reason about design and Core drift at the
    same time.  The former implementation could only learn about group drift
    by throwing before the Design check, which made the two recovery commands
    mutually blocking.
    """

    group_file = lock.get("groupFile")
    if not isinstance(group_file, str) or not group_file:
        return {
            "changed": True,
            "reason": "task_draft_group_file_missing",
            "detail": "",
            "groupFile": None,
        }
    try:
        data = _load_task_group_file(Path(group_file), feature)
    except (PlanWriterInputError, OSError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, PlanWriterInputError) else "task_draft_group_source_invalid"
        detail = exc.detail if isinstance(exc, PlanWriterInputError) else str(exc)
        return {
            "changed": True,
            "reason": reason,
            "detail": detail or "",
            "groupFile": group_file,
        }
    actual = _task_group_digest(data)
    expected = lock.get("groupingDigest")
    changed = actual != expected
    return {
        "changed": changed,
        "reason": "task_group_changed_after_draft_created" if changed else None,
        "detail": (
            f"expected={expected};actual={actual};affectedGroupFields="
            f"{_draft_group_change_summary(data, task_items) if changed and task_items is not None else 'unknown_group_change'}"
        ) if changed else "",
        "groupFile": group_file,
        "expectedDigest": expected,
        "actualDigest": actual,
        "data": data,
    }


def _draft_group_data(
    lock: dict[str, Any],
    feature: str,
    task_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    drift = _draft_group_drift(lock, feature, task_items)
    if drift.get("reason") == "task_draft_group_file_missing":
        raise PlanWriterInputError("task_draft_group_file_missing")
    if drift.get("reason") not in {None, "task_group_changed_after_draft_created"}:
        raise PlanWriterInputError(str(drift["reason"]), str(drift.get("detail") or ""))
    if drift.get("changed") is True:
        raise PlanWriterInputError(
            "task_group_changed_after_draft_created",
            f"{drift.get('detail')};"
            "run=rebuild-task-draft;then_refill_resetTaskIds_only",
        )
    data = drift.get("data")
    if not isinstance(data, dict):
        raise PlanWriterInputError("task_draft_group_source_invalid")
    return data


def _workspace_context_for_group(
    group: dict[str, Any],
    contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    task_id = str(group.get("id", "task"))
    workspace_ref = group.get("workspaceRef")
    if workspace_ref == "default" and len(contexts) == 1:
        return contexts[0]
    matches = [item for item in contexts if item.get("repo") == workspace_ref]
    if len(matches) != 1:
        raise PlanWriterInputError(
            "task_group_workspace_ref_not_found",
            f"task={task_id};workspaceRef={workspace_ref};available={','.join(str(item.get('repo')) for item in contexts)}",
        )
    return matches[0]


def _draft_task_workspace_roots(
    group: dict[str, Any],
    contexts: list[dict[str, Any]],
) -> dict[str, str]:
    context = _workspace_context_for_group(group, contexts)
    workspace_ref = str(group.get("workspaceRef"))
    key = "default" if workspace_ref == "default" else str(context["repo"])
    return {key: str(context["workspaceRoot"])}


def _draft_task_workspace_contract(
    group: dict[str, Any],
    contexts: list[dict[str, Any]],
) -> tuple[str, str, str, str]:
    context = _workspace_context_for_group(group, contexts)
    return (
        str(context["repo"]),
        str(context["gitRoot"]),
        str(context["workspaceRoot"]),
        str(context["requestedPath"]),
    )


def _draft_task_skeleton(group: dict[str, Any], workspace_roots: dict[str, str]) -> dict[str, Any]:
    task_id = str(group.get("id"))
    ui_required = group.get("uiRequired") is True
    execution_mode = group.get("executionMode", "code")
    ui_refs = copy.deepcopy(group.get("uiRefs")) if isinstance(group.get("uiRefs"), dict) else None
    touches = (
        sorted({str(path).replace("\\", "/").strip("/") for path in group.get("touches", []) if isinstance(path, str) and path.strip()})
        if isinstance(group.get("touches"), list)
        else []
    )
    task: dict[str, Any] = {
        "id": task_id,
        "title": group.get("title"),
        "executionMode": execution_mode,
        "executionStage": group.get("executionStage", "parallel"),
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
            "paths": touches,
        },
        "writeTargets": copy.deepcopy(group.get("writeTargets", [])),
        "implementationPoints": [],
        "acceptanceCriteria": [],
        "validationBoundary": group.get("validationBoundary"),
        "workspaceRef": group.get("workspaceRef"),
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
        "completionPolicy": (
            "external_dependency_recorded"
            if execution_mode == "external_dependency"
            else "all_required_validations_pass"
        ),
        "completionEvidenceIds": [],
        "latestPassEvidenceId": None,
        "blockers": [],
    }
    if ui_refs is not None:
        task["uiRefs"] = ui_refs
    if execution_mode == "external_dependency":
        task["externalDependency"] = copy.deepcopy(group.get("externalDependency"))
    if isinstance(group.get("atomicGroup"), dict):
        task["atomicGroup"] = copy.deepcopy(group["atomicGroup"])
    rationale = group.get("splitRationale")
    if isinstance(rationale, str) and rationale.strip():
        task["splitRationale"] = rationale
    return task


def _plan_v2_runtime_task(group: dict[str, Any], workspace_roots: dict[str, str]) -> dict[str, Any]:
    """Create a complete runtime task without asking the planner for code detail.

    The generated fields preserve the planner's high-level implementation and
    test guidance. Code and UTest select actual files, symbols and executable
    commands against the codebase in their respective stages.
    """
    task = _draft_task_skeleton(group, workspace_roots)
    task_id = str(task["id"])
    outcome = str(group.get("title") or "").strip()
    scenario_refs = [
        ref for ref in task.get("specRefs", [])
        if isinstance(ref, str) and "#SCN-" in ref
    ]
    task["goal"] = outcome
    task["implementationPoints"] = copy.deepcopy(group.get("implementationPoints", []))
    task["testPoints"] = copy.deepcopy(group.get("testPoints", []))
    task["acceptanceCriteria"] = [{
        "id": f"AC-{task_id}-01",
        "text": outcome,
        "scenarioRefs": scenario_refs,
    }]
    task["nonGoals"] = []
    task["designRefs"] = copy.deepcopy(group.get("designRefs", []))
    task["dataIds"] = copy.deepcopy(group.get("dataIds", []))
    task["decisionIds"] = copy.deepcopy(group.get("decisionIds", []))
    task["validationCommands"] = []
    task["verificationIntent"] = str(group.get("verificationIntent") or outcome).strip()
    if task_execution_mode(task) == "external_dependency":
        task["validationTestPlan"] = []
    else:
        task["validationTestPlan"] = [{
            "id": f"TEST-{task_id}-01",
            "assetType": "e2e_test" if task.get("uiRequired") is True else "unit_test",
            "executionStage": "post_batch" if task.get("uiRequired") is True else "with_code",
            "covers": [f"AC-{task_id}-01"],
            "testIntent": {
                "behavior": task["verificationIntent"],
                "acceptanceCriteria": copy.deepcopy(task["acceptanceCriteria"]),
                "testPoints": copy.deepcopy(task["testPoints"]),
            },
        }]
    return task


def _plan_writer_stdin_body() -> dict[str, Any]:
    try:
        return read_object_stdin()
    except WriterEncodingError as exc:
        raise PlanWriterInputError("invalid_body_stdin_encoding", str(exc)) from exc
    except WriterError as exc:
        message = str(exc)
        if "stdin 为空" in message:
            raise PlanWriterInputError("empty_body_stdin", message) from exc
        if "stdin 不是合法 JSON" in message:
            raise PlanWriterInputError("invalid_body_stdin_json", message) from exc
        if "stdin JSON 顶层必须是 object" in message:
            raise PlanWriterInputError("invalid_body_stdin_object", message) from exc
        raise


def _draft_detail_body(args: argparse.Namespace) -> dict[str, Any]:
    if args.body_file:
        return _compact_detail_to_legacy(read_object_file(args.body_file))
    if args.body_stdin:
        return _compact_detail_to_legacy(_plan_writer_stdin_body())
    if args.body_json:
        value = parse_json_value(args.body_json)
        if not isinstance(value, dict):
            raise PlanWriterInputError("draft_task_detail_must_be_object")
        return _compact_detail_to_legacy(value)
    raise PlanWriterInputError("draft_task_detail_input_missing")


def _draft_task_detail_projection(task: dict[str, Any]) -> dict[str, Any]:
    """Return the user-owned detail shape accepted by the draft normalizer."""

    raw_scope = task.get("scope") if isinstance(task.get("scope"), dict) else {}
    scope = {
        field: copy.deepcopy(raw_scope.get(field, []))
        for field in sorted(DRAFT_SCOPE_FIELDS)
    }
    criteria = []
    for raw in task.get("acceptanceCriteria", []):
        if isinstance(raw, dict):
            criteria.append({
                "text": copy.deepcopy(raw.get("text")),
                "scenarioRefs": copy.deepcopy(raw.get("scenarioRefs", [])),
            })
    commands = []
    for raw in task.get("validationCommands", []):
        if not isinstance(raw, dict):
            continue
        commands.append({
            key: copy.deepcopy(value)
            for key, value in raw.items()
            if key != "id"
        })
    detail = {
        field: copy.deepcopy(task.get(field, [] if field != "goal" else ""))
        for field in DRAFT_DETAIL_FIELDS
        if field not in {"scope", "acceptanceCriteria", "validationCommands"}
    }
    detail["scope"] = scope
    detail["acceptanceCriteria"] = criteria
    detail["validationCommands"] = commands
    return detail


def _merge_draft_task_patch(task: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task.get("id", "task"))
    if not patch:
        raise PlanWriterInputError("draft_task_repair_patch_empty", f"task={task_id}")
    group_owned = sorted(set(patch) & DRAFT_GROUP_OWNED_FIELDS)
    if group_owned:
        raise PlanWriterInputError(
            "draft_task_repair_group_owned_field_forbidden",
            f"task={task_id};fields={','.join(group_owned)};repairTarget=task_group",
        )
    unknown = sorted(set(patch) - DRAFT_DETAIL_FIELDS)
    if unknown:
        raise PlanWriterInputError(
            "draft_task_repair_field_unknown",
            f"task={task_id};fields={','.join(unknown)}",
        )
    detail = _draft_task_detail_projection(task)
    if "scope" in patch:
        raw_scope_patch = patch.get("scope")
        if not isinstance(raw_scope_patch, dict):
            raise PlanWriterInputError("draft_task_scope_must_be_object", f"task={task_id}")
        scope_unknown = sorted(set(raw_scope_patch) - DRAFT_SCOPE_FIELDS)
        if scope_unknown:
            raise PlanWriterInputError(
                "draft_task_scope_field_unknown",
                f"task={task_id};fields={','.join(scope_unknown)}",
            )
        merged_scope = copy.deepcopy(detail["scope"])
        merged_scope.update(copy.deepcopy(raw_scope_patch))
        detail["scope"] = merged_scope
    for field, value in patch.items():
        if field != "scope":
            detail[field] = copy.deepcopy(value)
    return detail


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
    detail = copy.deepcopy(detail)
    compact_goal_from_core = detail.pop("__compactGoalFromCore", False) is True
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
        # Candidate grouping owns planned file isolation. A detail may add
        # concrete paths, but omitting the field must not erase the group
        # projection before Code consumes it.
        "paths": copy.deepcopy(scope["paths"] if "paths" in scope else previous_scope.get("paths", [])),
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
        workspace_roots = candidate["scope"].get("workspaceRoots", {})
        if (
            isinstance(workspace_roots, dict)
            and "default" not in workspace_roots
            and len(workspace_roots) == 1
            and "repo" not in command
        ):
            command["repo"] = next(iter(workspace_roots))
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
            if len(set(covers)) != len(covers):
                raise PlanWriterInputError(
                    "draft_validation_cover_duplicate",
                    f"task={task_id};command={index}",
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
        if field == "goal" and compact_goal_from_core:
            candidate[field] = copy.deepcopy(task.get("title", ""))
        else:
            candidate[field] = copy.deepcopy(detail.get(field, [] if field != "goal" else ""))
    candidate["status"] = "todo"
    candidate["evidenceIds"] = []
    candidate["implementationEvidenceIds"] = []
    candidate["latestImplementationEvidenceId"] = None
    candidate["validationEvidenceIds"] = []
    candidate["implementationRevision"] = 0
    candidate["completionPolicy"] = (
        "external_dependency_recorded"
        if task_execution_mode(candidate) == "external_dependency"
        else "all_required_validations_pass"
    )
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
    *,
    defer_to_test_stages: bool = False,
) -> list[dict[str, str]]:
    task_for_structure = copy.deepcopy(task)
    task_for_structure["deps"] = []
    raw_errors = validate_task_collection(
        feature,
        [task_for_structure],
        require_initial_status=True,
        defer_to_test_stages=defer_to_test_stages,
    )
    translated = {
        f"{task.get('id')}.implementationPoints_too_many": (
            f"{task.get('id')}.implementation_points_exceeds_limit"
        ),
    }
    errors = [{"reason": translated.get(reason, reason)} for reason in raw_errors]
    errors.extend(_draft_acceptance_scope_errors(task))
    errors.extend(validate_plan_task_granularity_item(task, task_id=str(task.get("id", "task"))))
    errors.extend(_code_workspace_preflight_errors({"tasks": [task]}, code_workspaces))

    # Validate Maven test target ambiguity
    contexts = _code_workspace_contexts(code_workspaces or [])
    workspace_roots = task_workspace_roots(task)
    for index, command in enumerate(task.get("validationCommands", [])):
        if not isinstance(command, dict):
            continue
        key = "default" if "default" in workspace_roots else command.get("repo")
        workspace_root = workspace_roots.get(str(key)) if isinstance(key, str) else None
        context = (
            _context_for_workspace_root(contexts, str(key), workspace_root)
            if isinstance(workspace_root, str)
            else None
        )
        cwd = command.get("cwd")
        if context is None or not isinstance(cwd, str):
            continue
        command_dir = (context["gitRoot"] / cwd).resolve()
        ambiguity_errors = check_maven_test_target_ambiguity(command, command_dir)
        if ambiguity_errors:
            task_id = str(task.get("id", "task"))
            errors.append({
                "reason": ambiguity_errors[0],
                "detail": f"task={task_id};command={index + 1};use_fully_qualified_class_name",
            })

    return errors


def _annotate_validation_test_plan(
    task: dict[str, Any],
    code_workspaces: list[str],
    plan_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist test intent for UTest/E2E without creating Code-stage test targets."""

    del code_workspaces, plan_data

    if task_execution_mode(task) == "external_dependency":
        candidate = copy.deepcopy(task)
        candidate["validationTestPlan"] = []
        return candidate

    candidate = copy.deepcopy(task)
    validation_commands = task.get("validationCommands", [])
    acceptance_criteria = task.get("acceptanceCriteria", [])
    description = task.get("description", "")
    behavior = (
        task.get("goal", "")
        or task.get("summary", "")
        or (description.split("\n")[0] if description else "")
        or f"Task {task.get('id', 'unknown')}: Implementation"
    )
    acceptance_ids = [
        str(criterion.get("id"))
        for criterion in acceptance_criteria
        if isinstance(criterion, dict) and isinstance(criterion.get("id"), str)
    ]
    test_plans: list[dict[str, Any]] = []
    for command in validation_commands if isinstance(validation_commands, list) else []:
        if not isinstance(command, dict):
            continue
        kind = command.get("kind", "unit_test")
        if kind == "e2e_test":
            asset_type, execution_stage = "e2e_test", "post_batch"
        elif kind == "integration_test":
            asset_type, execution_stage = "integration_test", "post_batch"
        else:
            asset_type, execution_stage = "unit_test", "with_code"
        test_plans.append({
            "commandId": command.get("id"),
            "assetType": asset_type,
            "executionStage": execution_stage,
            "covers": list(command.get("covers") or acceptance_ids),
            "testIntent": {
                "behavior": str(behavior).strip(),
                "acceptanceCriteria": copy.deepcopy(acceptance_criteria),
            },
        })
    candidate["validationTestPlan"] = test_plans
    return candidate


def _tasks(data: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = data.setdefault("tasks", [])
    if not isinstance(tasks, list):
        raise ValueError("plan.json.tasks 必须是数组")
    return tasks


def _require_collecting(data: dict[str, Any]) -> None:
    if data.get("taskSetStatus") == "finalized":
        raise PlanWriterInputError("plan_task_set_finalized")


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
    task.pop("touches", None)
    task.setdefault("executionMode", "code")
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
    task.setdefault(
        "completionPolicy",
        (
            "external_dependency_recorded"
            if task_execution_mode(task) == "external_dependency"
            else "all_required_validations_pass"
        ),
    )
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
        "executionMode": "code",
        "goal": args.goal,
        "status": args.status,
        "deps": _split_values(args.dep),
        "uiRequired": ui_required,
        "workspaceRef": "default",
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
    if _draft_lock_path(workspace, feature).is_file():
        return render_result(fail(
            "task_draft_already_exists",
            "Draft 已存在；请继续、预检或重建 Draft，init 不会创建新的根 plan.json 占位。",
            path=_draft_plan_path(workspace, feature),
        ))
    existing = fail_if_artifact_exists(_path(workspace, feature), force=args.force)
    if existing:
        return render_result(existing)
    if args.force:
        plans_dir = _path(workspace, feature).parent / "plans"
        if plans_dir.is_dir():
            for old_plan in plans_dir.glob("B*/plan.json"):
                unlink_if_exists(old_plan)
                try:
                    old_plan.parent.rmdir()
                except OSError:
                    pass
            try:
                plans_dir.rmdir()
            except OSError:
                pass
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
        task = _plan_writer_stdin_body()
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
    task.pop("touches", None)
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
    task.setdefault("workspaceRef", "default")
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
    data = _compact_plan_core_to_groups(read_object_file(group_file))
    manifest_feature = data.get("featureId")
    if manifest_feature != feature:
        raise PlanWriterInputError(
            "task_groups_feature_mismatch",
            f"expected={feature};actual={manifest_feature}",
        )
    return data


def _task_groups_body(args: argparse.Namespace) -> dict[str, Any]:
    """Read one Plan Core payload without relying on a reusable temp file."""

    if args.body_file:
        body = read_object_file(Path(args.body_file))
    elif args.body_stdin:
        body = _plan_writer_stdin_body()
    elif args.body_json:
        body = parse_json_value(args.body_json)
        if not isinstance(body, dict):
            raise PlanWriterInputError("task_groups_body_must_be_object")
    else:
        raise PlanWriterInputError("task_groups_input_missing")
    if not isinstance(body, dict):
        raise PlanWriterInputError("task_groups_body_must_be_object")
    unknown = sorted(set(body) - {"schemaVersion", "featureId", "tasks"})
    if unknown:
        raise PlanWriterInputError("compact_plan_core_field_unknown", f"fields={','.join(unknown)}")
    if body.get("schemaVersion") != PLAN_CORE_SCHEMA:
        raise PlanWriterInputError(
            "compact_plan_core_schema_required",
            f"expected={PLAN_CORE_SCHEMA};actual={body.get('schemaVersion')}",
        )
    return body


def _cmd_write_task_groups(args: argparse.Namespace) -> int:
    """Preflight a compact Core and atomically persist it as the Draft source."""

    workspace, feature = _resolve(args)
    path = (
        Path(args.group_file).expanduser().resolve()
        if args.group_file
        else _default_task_groups_path(workspace, feature)
    )
    if _path(workspace, feature).is_file():
        return render_result(fail("formal_plan_already_exists", path=_path(workspace, feature)))
    if _draft_lock_path(workspace, feature).is_file():
        return render_result(fail(
            "task_draft_already_exists",
            "use create-repair-work/apply-draft-patch or rebuild-task-draft; Core source is locked after Draft creation",
            path=_draft_plan_path(workspace, feature),
        ))

    core = _task_groups_body(args)
    group_data = _compact_plan_core_to_groups(core)
    manifest_feature = group_data.get("featureId")
    if manifest_feature != feature:
        return render_result(fail(
            "task_groups_feature_mismatch",
            f"expected={feature};actual={manifest_feature}",
            path=path,
        ))
    errors = _task_group_preflight_errors(_path(workspace, feature).parent, group_data)
    report = _task_group_validation_report(group_data, errors)
    if errors:
        return render_result(WriterResult(
            ok=False,
            path=path,
            errors=errors,
            data={"grouping": _task_group_summary(group_data), "validation": report},
        ))

    canonical_core = {
        "schemaVersion": PLAN_CORE_SCHEMA,
        "featureId": feature,
        "tasks": copy.deepcopy(core["tasks"]),
    }
    changed = atomic_write_json(path, canonical_core)
    return render_result(WriterResult(
        ok=True,
        path=path,
        changed=changed,
        data={
            "groupFile": str(path),
            "grouping": _task_group_summary(group_data),
            "validation": report,
        },
    ))


def _task_group_summary(data: dict[str, Any]) -> dict[str, Any]:
    groups = _task_groups(data)
    return {
        "groupCount": len(groups),
        "groupingDigest": _task_group_digest(data),
        "groups": [
            {
                "id": group.get("id"),
                "executionLane": "frontend" if group.get("uiRequired") is True else "backend",
                "workspaceRef": group.get("workspaceRef"),
                "scenarioCount": len(
                    scenario_refs_from_spec_refs(
                        [item for item in group.get("specRefs", []) if isinstance(item, str)]
                    )
                ),
            }
            for group in groups
        ],
        "detailObligations": _matrix_detail_obligations(groups),
    }


def _matrix_detail_obligations(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose Detail requirements implied by an SCN matrix before Detail exists.

    The full Plan validator can only check a matrix command after acceptance
    criteria and checks have been supplied.  This projection makes the same
    rule visible immediately after Core preflight, where it can still affect
    task splitting rather than trigger an avoidable Detail rewrite.
    """

    obligations: list[dict[str, Any]] = []
    for group in groups:
        scenario_count = len(scenario_refs_from_spec_refs([
            item for item in group.get("specRefs", []) if isinstance(item, str)
        ]))
        if scenario_count <= PLAN_TASK_MAX_SCENARIOS:
            continue
        allowed_kinds = set(BEHAVIOR_TASK_VALIDATION_KINDS) - {"static_check"}
        if group.get("uiRequired") is True:
            allowed_kinds.update(FRONTEND_COMPILE_VALIDATION_KINDS)
        obligations.append({
            "taskId": group.get("id"),
            "trigger": f"scenarios={scenario_count}>{PLAN_TASK_MAX_SCENARIOS}",
            "required": {
                "commandCount": 1,
                "required": True,
                "allowedKinds": sorted(allowed_kinds),
                "covers": (
                    "omit covers to have the writer cover every generated acceptance criterion; "
                    "if supplied, it must list every generated acceptance criterion exactly once"
                ),
            },
        })
    return obligations


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
        existing = next(
            (item for item in contexts if item.get("repo") == git_root.name),
            None,
        )
        if existing is not None:
            raise PlanWriterInputError(
                "code_workspace_repository_id_duplicate",
                (
                    f"repo={git_root.name};first={existing.get('requestedPath')};"
                    f"second={requested};use_distinct_git_root_directory_names"
                ),
            )
        contexts.append({
            "repo": git_root.name,
            "gitRoot": git_root,
            "workspaceRoot": workspace_root,
            "requestedPath": requested,
        })
        seen.add(key)
    return contexts


def _code_workspace_bindings(
    contexts: list[dict[str, Any]],
    workspace_refs: set[str] | None = None,
) -> dict[str, str]:
    """Project Plan-time repository paths into the root Plan contract."""
    refs = workspace_refs or set()
    if len(contexts) == 1 and refs == {"default"}:
        return {"default": str(contexts[0]["requestedPath"])}
    return {
        str(context["repo"]): str(context["requestedPath"])
        for context in contexts
        if isinstance(context.get("repo"), str) and isinstance(context.get("requestedPath"), Path)
    }


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
    compile_only: bool = False,
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
    selector_errors = maven_project_selector_workspace_errors(command, command_dir)
    if selector_errors:
        return [{
            "reason": selector_errors[0],
            "detail": f"context={context_name};cwd={cwd}",
        }]
    script_name = package_script_name(command)
    if script_name is not None:
        package_path = command_dir / "package.json"
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return [{
                "reason": "validation_package_manifest_invalid",
                "detail": f"context={context_name};path={package_path};error={exc}",
            }]
        scripts = package.get("scripts") if isinstance(package, dict) else None
        script = scripts.get(script_name) if isinstance(scripts, dict) else None
        script_errors = (
            compile_only_package_scripts_errors(scripts, script_name)
            if compile_only
            else package_script_policy_errors(script)
        )
        if script_errors:
            return [{
                "reason": script_errors[0],
                "detail": f"context={context_name};packageScript={script_name}",
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


def _issue_task_ids(error: dict[str, Any]) -> list[str]:
    explicit = error.get("taskIds")
    if isinstance(explicit, list):
        return list(dict.fromkeys(
            item for item in explicit if isinstance(item, str) and TASK_GROUP_TASK_ID_RE.fullmatch(item)
        ))
    explicit_task_id = error.get("taskId")
    if isinstance(explicit_task_id, str) and TASK_GROUP_TASK_ID_RE.fullmatch(explicit_task_id):
        return [explicit_task_id]
    detail = error.get("detail")
    detail = detail if isinstance(detail, str) else ""
    many = TASK_IDS_IN_DETAIL_RE.search(detail)
    if many:
        return list(dict.fromkeys(
            item.strip()
            for item in many.group(1).split(",")
            if TASK_GROUP_TASK_ID_RE.fullmatch(item.strip())
        ))
    single = TASK_ID_IN_DETAIL_RE.search(detail)
    if single:
        return [single.group(1)]
    context = TASK_CONTEXT_IN_DETAIL_RE.search(detail)
    if context:
        return [context.group(1)]
    reason = error.get("reason")
    reason_match = TASK_ID_IN_REASON_RE.match(reason) if isinstance(reason, str) else None
    return [reason_match.group(1)] if reason_match else []


def _issue_field(error: dict[str, Any]) -> str | None:
    explicit = error.get("field")
    if isinstance(explicit, str) and explicit:
        return explicit
    reason = error.get("reason")
    reason = reason if isinstance(reason, str) else ""
    reason_match = TASK_ID_IN_REASON_RE.match(reason)
    if reason_match:
        return reason_match.group(2)
    detail = error.get("detail")
    detail = detail if isinstance(detail, str) else ""
    context = TASK_CONTEXT_IN_DETAIL_RE.search(detail)
    if context:
        return context.group(2)
    fields_match = re.search(r"(?:^|;)fields=([^;]+)(?:;|$)", detail)
    if fields_match:
        fields = [item.strip() for item in fields_match.group(1).split(",") if item.strip()]
        if len(fields) == 1:
            return fields[0]
    if reason.startswith("maven_test_"):
        return "validationCommands"
    if reason in {"draft_acceptance_scenario_ref_invalid", "acceptance_scenario_not_in_group"}:
        return "acceptanceCriteria"
    return None


def _issue_repair_target(error: dict[str, Any], task_ids: list[str], field: str | None) -> str:
    explicit = error.get("repairTarget")
    if explicit in {"task_detail", "task_group", "draft_integrity", "design_revision"}:
        return str(explicit)
    reason = str(error.get("reason", ""))
    if "digest" in reason or "bundle" in reason or "batch_missing" in reason:
        return "draft_integrity"
    if (
        reason.startswith("task_group_")
        or reason.startswith("missing_plan_scenario_coverage")
        or (field is not None and field.split("[", 1)[0] in DRAFT_GROUP_OWNED_FIELDS)
    ):
        return "task_group"
    if task_ids:
        return "task_detail"
    return "draft_integrity"


def _structured_draft_issues(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index, raw in enumerate(errors, start=1):
        issue = copy.deepcopy(raw)
        issue.setdefault(
            "diagnostics",
            {
                key: copy.deepcopy(value)
                for key, value in raw.items()
                if key not in {"reason", "detail"}
            },
        )
        task_ids = _issue_task_ids(issue)
        field = _issue_field(issue)
        repair_target = _issue_repair_target(issue, task_ids, field)
        existing_issue_id = issue.get("issueId")
        issue["issueId"] = (
            existing_issue_id
            if isinstance(existing_issue_id, str) and existing_issue_id.strip()
            else f"ISSUE-{index:03d}"
        )
        issue["scope"] = "cross_task" if len(task_ids) > 1 else "task" if task_ids else "draft"
        issue["taskIds"] = task_ids
        if field is not None:
            issue["field"] = field
        issue["repairTarget"] = repair_target
        issue["repairable"] = repair_target in {"task_detail", "task_group"}
        issues.append(issue)
    return issues


def _validation_report(
    task_items: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    issues = _structured_draft_issues(errors)
    all_task_ids = [
        str(task.get("id"))
        for task in task_items
        if isinstance(task.get("id"), str)
    ]
    invalid = list(dict.fromkeys(
        task_id
        for issue in issues
        for task_id in issue.get("taskIds", [])
        if isinstance(task_id, str)
    ))
    has_global_issue = any(not issue.get("taskIds") for issue in issues)
    valid = [] if has_global_issue else [task_id for task_id in all_task_ids if task_id not in invalid]
    repairable_task_ids = list(dict.fromkeys(
        task_id
        for issue in issues
        if issue.get("repairTarget") == "task_detail"
        for task_id in issue.get("taskIds", [])
        if isinstance(task_id, str)
    ))
    return {
        "ok": not issues,
        "repairable": bool(issues) and all(issue.get("repairable") is True for issue in issues),
        "issues": issues,
        "validTaskIds": valid,
        "invalidTaskIds": invalid,
        "repairableTaskIds": repairable_task_ids,
        "requiresTaskGroupRepair": any(issue.get("repairTarget") == "task_group" for issue in issues),
        "requiresIntegrityRepair": any(issue.get("repairTarget") == "draft_integrity" for issue in issues),
    }


def _draft_validation_report(data: dict[str, Any], errors: list[dict[str, Any]]) -> dict[str, Any]:
    return _validation_report(_tasks(data), errors)


def _task_group_validation_report(
    data: dict[str, Any],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    return _validation_report(_task_groups(data), errors)


def _task_set_preflight_errors(
    feature_dir: Path,
    data: dict[str, Any],
    group_data: dict[str, Any],
    code_workspaces: list[str] | None = None,
    *,
    require_engineering_commands: bool = False,
) -> list[dict[str, Any]]:
    errors = _task_group_preflight_errors(feature_dir, group_data)
    if errors:
        return errors
    contract_errors = _task_group_contract_errors(group_data, _tasks(data))
    if contract_errors:
        return contract_errors
    errors = []
    design_contract, design_errors = _current_design_contract(feature_dir)
    errors.extend(design_errors)
    if design_errors:
        return errors
    for task in _tasks(data):
        errors.extend(validate_task_artifact_refs(
            feature_dir,
            task,
            design_contract=design_contract,
            check_design_artifact=False,
        ))
    errors.extend(validate_plan_design_coverage(design_contract, _tasks(data)))
    # A task detail may add scope.paths/expectedFiles beyond its group-owned
    # touches.  Recheck the final write sets before projecting Batches.
    _, projected_batches = _project_batches(copy.deepcopy(data))
    projected_scopes = {
        str(task.get("id")): batch_id
        for batch_id, batch in projected_batches.items()
        for task in batch.get("tasks", [])
        if isinstance(task, dict) and isinstance(task.get("id"), str)
    }
    errors.extend(write_ownership_violations(
        _tasks(data),
        ownership_scope_by_task=projected_scopes,
    ))
    errors.extend(_task_set_validation_errors(data))
    errors.extend(_code_workspace_preflight_errors(data, code_workspaces))
    if require_engineering_commands:
        # Report the Draft-facing repair actions first.  Skipping bundle
        # projection while they are missing avoids duplicating the same issue
        # as low-level Bxxx profile-projection errors.
        errors.extend(_validate_draft_engineering_commands(data))
    if not errors:
        root, batches = _project_batches(data)
        bundle_errors = validate_plan_bundle_data(root, batches)
        errors.extend({"reason": error} for error in bundle_errors)
    expected, covered = _scenario_coverage(feature_dir, _tasks(data))
    missing = sorted(expected - covered)
    if missing:
        errors.append({
            "reason": "missing_plan_scenario_coverage",
            "detail": f"return_to_scenario_matrix;ids={','.join(missing)}",
            "field": "specRefs",
            "repairTarget": "task_group",
        })
    return errors


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
    task_contract_hashes = {
        str(task.get("id")): _json_digest({
            field: value
            for field, value in task.items()
            if field not in {
                "status", "evidenceIds", "implementationEvidenceIds",
                "latestImplementationEvidenceId", "validationEvidenceIds",
                "implementationRevision", "completionEvidenceIds", "latestPassEvidenceId",
            }
        })
        for task in _tasks(data)
        if isinstance(task.get("id"), str)
    }
    return {
        "status": lock.get("status"),
        "revision": lock.get("revision", 0),
        "groupingDigest": lock.get("groupingDigest"),
        "taskCount": len(task_ids),
        "readyTaskIds": [task_id for task_id in task_ids if task_id in ready],
        "pendingTaskIds": [task_id for task_id in task_ids if task_id not in ready],
        "taskContractHashes": task_contract_hashes,
        "lastChange": copy.deepcopy(lock.get("lastChange")) if isinstance(lock.get("lastChange"), dict) else None,
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
    feature_dir = _path(workspace, feature).parent
    errors = _task_group_preflight_errors(feature_dir, group_data)
    if errors:
        return render_result(WriterResult(ok=False, path=group_file, errors=errors))
    design_contract, design_errors = _current_design_contract(feature_dir)
    if design_errors:
        return render_result(WriterResult(ok=False, path=group_file, errors=design_errors))
    workspace_contexts = _code_workspace_contexts(args.code_workspace)
    data = _initial(feature)
    implementation_scope, scope_errors = load_scope(_path(workspace, feature).parent)
    if scope_errors:
        return WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=[{"reason": error} for error in scope_errors],
        )
    data["implementationScope"] = implementation_scope
    data["codeWorkspaces"] = _code_workspace_bindings(
        workspace_contexts,
        {str(group.get("workspaceRef")) for group in _task_groups(group_data)},
    )
    data["tasks"] = [
        _draft_task_skeleton(
            group,
            _draft_task_workspace_roots(group, workspace_contexts),
        )
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
        "designContract": design_contract_snapshot(design_contract),
        "createdAt": _utc_now(),
    }
    result = _write_draft_bundle(workspace, feature, data, lock)
    return render_result(with_result_data(result, draft=_draft_summary(lock, data)))


def _cmd_publish_plan(args: argparse.Namespace) -> int:
    """Publish a complete Plan v2 in one atomic operation.

    This is the normal planner entry point.  It intentionally bypasses the old
    Core/Draft/Detail choreography: planner input is converted directly into a
    finalized, runtime-valid Bundle only after every structural and coverage
    check succeeds.
    """
    workspace, feature = _resolve(args)
    if _path(workspace, feature).is_file():
        return render_result(fail("formal_plan_already_exists", path=_path(workspace, feature)))
    body = _plan_writer_stdin_body() if args.body_stdin else read_object_file(Path(args.body_file))
    if not isinstance(body, dict):
        return render_result(fail("plan_v2_body_must_be_object"))
    if body.get("schemaVersion") != PLAN_SCHEMA:
        return render_result(fail("plan_v2_schema_required", f"expected={PLAN_SCHEMA};actual={body.get('schemaVersion')}"))
    try:
        group_data = _compact_plan_core_to_groups(body)
    except PlanWriterInputError as exc:
        return render_result(fail(exc.reason, exc.detail))
    if group_data.get("featureId") != feature:
        return render_result(fail("plan_v2_feature_mismatch", f"expected={feature};actual={group_data.get('featureId')}"))
    feature_dir = _path(workspace, feature).parent
    group_errors = _task_group_preflight_errors(feature_dir, group_data)
    blocking_group_errors = [item for item in group_errors if item.get("severity") != "warning"]
    if blocking_group_errors:
        return render_result(WriterResult(ok=False, path=_path(workspace, feature), errors=blocking_group_errors))
    try:
        workspace_contexts = _code_workspace_contexts(args.code_workspace)
        implementation_scope, scope_errors = load_scope(feature_dir)
        if scope_errors:
            return render_result(WriterResult(
                ok=False,
                path=_path(workspace, feature),
                errors=[{"reason": error} for error in scope_errors],
            ))
        data = _initial(feature)
        data["implementationScope"] = implementation_scope
        data["codeWorkspaces"] = _code_workspace_bindings(
            workspace_contexts,
            {str(group.get("workspaceRef")) for group in _task_groups(group_data)},
        )
        data["tasks"] = [
            _plan_v2_runtime_task(group, _draft_task_workspace_roots(group, workspace_contexts))
            for group in _task_groups(group_data)
        ]
        data["taskSetStatus"] = "finalized"
        plan_errors = _task_set_preflight_errors(
            feature_dir,
            data,
            group_data,
            [str(Path(value).expanduser().resolve()) for value in args.code_workspace],
            require_engineering_commands=False,
        )
        blocking_plan_errors = [item for item in plan_errors if item.get("severity") != "warning"]
        if blocking_plan_errors:
            return render_result(WriterResult(ok=False, path=_path(workspace, feature), errors=blocking_plan_errors))
        result = _write(workspace, feature, data, plan_markdown=_render_plan_md(data))
        return render_result(with_result_data(
            result,
            planSchema=PLAN_SCHEMA,
            warnings=[item for item in [*group_errors, *plan_errors] if item.get("severity") == "warning"],
            materialized=_task_set_summary(data),
        ))
    except (PlanWriterInputError, ValueError) as exc:
        if isinstance(exc, PlanWriterInputError):
            return render_result(fail(exc.reason, exc.detail))
        return render_result(fail("plan_v2_publish_failed", str(exc)))


def _cmd_import_task_directory(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    if _path(workspace, feature).is_file():
        return render_result(fail("formal_plan_already_exists", path=_path(workspace, feature)))
    if _draft_lock_path(workspace, feature).is_file() and not args.force:
        return render_result(fail("task_draft_already_exists", path=_draft_plan_path(workspace, feature)))
    group_file = Path(args.group_file).expanduser().resolve()
    group_data = _load_task_group_file(group_file, feature)
    data = _load_task_directory(Path(args.task_dir).expanduser().resolve(), feature)
    feature_dir = _path(workspace, feature).parent
    errors = _task_set_preflight_errors(
        feature_dir,
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
    design_contract, design_errors = _current_design_contract(feature_dir)
    if design_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_errors,
        ))
    workspace_contexts = _code_workspace_contexts(args.code_workspace)
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
        "designContract": design_contract_snapshot(design_contract),
        "createdAt": _utc_now(),
        "importedFromTaskDirectory": str(Path(args.task_dir).expanduser().resolve()),
    }
    data["codeWorkspaces"] = _code_workspace_bindings(
        workspace_contexts,
        {str(task.get("workspaceRef")) for task in _tasks(data) if isinstance(task.get("workspaceRef"), str)},
    )
    result = _write_draft_bundle(workspace, feature, data, lock)
    return render_result(with_result_data(result, importedTaskIds=task_ids, draft=_draft_summary(lock, data)))


def _cmd_set_draft_task_detail(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    lock, data = _load_draft_bundle(workspace, feature)
    if lock.get("status") == "finalized":
        return render_result(fail("task_draft_finalized", path=_draft_plan_path(workspace, feature)))
    _draft_group_data(lock, feature, _tasks(data))
    feature_dir = _path(workspace, feature).parent
    design_lock_errors = _draft_design_contract_errors(feature_dir, lock)
    if design_lock_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_lock_errors,
        ))
    design_contract, design_errors = _current_design_contract(feature_dir)
    if design_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_errors,
        ))
    task = _find_task(data, args.task_id)
    candidate = _normalize_draft_task_detail(task, _draft_detail_body(args))
    code_workspaces = [
        item for item in lock.get("codeWorkspaces", []) if isinstance(item, str)
    ]
    candidate = _annotate_validation_test_plan(candidate, code_workspaces, data)

    # Step 1: Run structural validations first (fields, granularity, acceptance criteria)
    # This matches the original validation order and prevents artifact ref errors
    # from masking more fundamental issues like missing fields or oversized tasks
    errors = _draft_task_validation_errors(
        feature,
        candidate,
        code_workspaces,
        defer_to_test_stages=defer_to_test_stages_enabled(data),
    )
    if errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=errors,
        ))

    # Step 2: Validate artifact references (designRefs/specRefs)
    # Only run this after structural validation passes, so that tests expecting
    # specific structural errors aren't intercepted by missing design.md
    feature_dir = _path(workspace, feature).parent
    ref_errors = validate_task_artifact_refs(
        feature_dir,
        candidate,
        cache=None,
        design_contract=design_contract,
        check_design_artifact=False,
    )
    if ref_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=ref_errors,
        ))

    # All validations passed - update the task
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


def _draft_detail_lint_errors(
    feature: str,
    feature_dir: Path,
    candidate: dict[str, Any],
    code_workspaces: list[str],
    design_contract: dict[str, Any],
    *,
    defer_to_test_stages: bool,
) -> list[dict[str, Any]]:
    """Collect structural and artifact-reference errors without writing a Draft."""

    errors = _draft_task_validation_errors(
        feature,
        candidate,
        code_workspaces,
        defer_to_test_stages=defer_to_test_stages,
    )
    errors.extend(validate_task_artifact_refs(
        feature_dir,
        candidate,
        cache=None,
        design_contract=design_contract,
        check_design_artifact=False,
    ))
    return errors


def _cmd_lint_draft_task_detail(args: argparse.Namespace) -> int:
    """Validate one proposed Detail fully, without mutating Draft state."""

    workspace, feature = _resolve(args)
    lock, data = _load_draft_bundle(workspace, feature)
    if lock.get("status") == "finalized":
        return render_result(fail("task_draft_finalized", path=_draft_plan_path(workspace, feature)))
    _draft_group_data(lock, feature, _tasks(data))
    feature_dir = _path(workspace, feature).parent
    design_lock_errors = _draft_design_contract_errors(feature_dir, lock)
    if design_lock_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_lock_errors,
        ))
    design_contract, design_errors = _current_design_contract(feature_dir)
    if design_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_errors,
        ))
    task = _find_task(data, args.task_id)
    candidate = _normalize_draft_task_detail(task, _draft_detail_body(args))
    code_workspaces = [
        item for item in lock.get("codeWorkspaces", []) if isinstance(item, str)
    ]
    candidate = _annotate_validation_test_plan(candidate, code_workspaces, data)
    errors = _draft_detail_lint_errors(
        feature,
        feature_dir,
        candidate,
        code_workspaces,
        design_contract,
        defer_to_test_stages=defer_to_test_stages_enabled(data),
    )
    report = _validation_report([candidate], errors)
    return render_result(WriterResult(
        ok=not errors,
        path=_draft_plan_path(workspace, feature),
        errors=errors,
        data={
            "taskId": args.task_id,
            "validation": report,
            "draft": _draft_summary(lock, data),
        },
    ))


def _draft_detail_batch_entries(args: argparse.Namespace) -> list[tuple[str, dict[str, Any]]]:
    """Read the atomic multi-detail body accepted by set-draft-task-details."""

    if args.body_file:
        body = read_object_file(args.body_file)
    elif args.body_stdin:
        body = _plan_writer_stdin_body()
    elif args.body_json:
        body = parse_json_value(args.body_json)
        if not isinstance(body, dict):
            raise PlanWriterInputError("draft_task_details_must_be_object")
    else:
        raise PlanWriterInputError("draft_task_details_input_missing")
    unknown = sorted(set(body) - {"details"})
    if unknown:
        raise PlanWriterInputError("draft_task_details_field_unknown", f"fields={','.join(unknown)}")
    raw_details = body.get("details")
    if not isinstance(raw_details, list) or not raw_details:
        raise PlanWriterInputError("draft_task_details_missing")
    entries: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_details, start=1):
        if not isinstance(raw, dict):
            raise PlanWriterInputError("draft_task_details_entry_must_be_object", f"index={index}")
        unknown_entry = sorted(set(raw) - {"taskId", "body"})
        if unknown_entry:
            raise PlanWriterInputError(
                "draft_task_details_entry_field_unknown",
                f"index={index};fields={','.join(unknown_entry)}",
            )
        task_id = raw.get("taskId")
        body_value = raw.get("body")
        if not isinstance(task_id, str) or not TASK_GROUP_TASK_ID_RE.fullmatch(task_id):
            raise PlanWriterInputError("draft_task_details_task_id_invalid", f"index={index};task={task_id}")
        if task_id in seen:
            raise PlanWriterInputError("draft_task_details_task_id_duplicate", f"task={task_id}")
        if not isinstance(body_value, dict):
            raise PlanWriterInputError("draft_task_details_body_must_be_object", f"task={task_id}")
        seen.add(task_id)
        entries.append((task_id, _compact_detail_to_legacy(body_value)))
    return entries


def _draft_detail_batch_candidate(
    feature: str,
    feature_dir: Path,
    data: dict[str, Any],
    group_data: dict[str, Any],
    entries: list[tuple[str, dict[str, Any]]],
    code_workspaces: list[str],
    design_contract: dict[str, Any],
    *,
    full: bool,
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """Apply Details to an in-memory Draft and validate the resulting state.

    ``lint-draft-task-details --full`` and ``set-draft-task-details --full``
    intentionally share this routine.  A successful full lint therefore uses
    the exact candidate Draft and the same aggregate preflight as the write;
    only the final atomic bundle write differs.
    """

    candidate_data = copy.deepcopy(data)
    errors: list[dict[str, Any]] = []
    written_task_ids: list[str] = []
    ordered_ids = [str(item.get("id")) for item in _tasks(candidate_data)]
    submitted_ids = {task_id for task_id, _ in entries}
    if full:
        missing = [task_id for task_id in ordered_ids if task_id not in submitted_ids]
        if missing:
            errors.append({
                "reason": "draft_task_details_full_payload_incomplete",
                "detail": f"missingTaskIds={','.join(missing)}",
                "taskIds": missing,
                "repairTarget": "task_detail",
            })

    for task_id, detail in entries:
        try:
            task = _find_task(candidate_data, task_id)
            candidate = _normalize_draft_task_detail(task, detail)
            candidate = _annotate_validation_test_plan(candidate, code_workspaces, candidate_data)
            task_errors = _draft_detail_lint_errors(
                feature,
                feature_dir,
                candidate,
                code_workspaces,
                design_contract,
                defer_to_test_stages=defer_to_test_stages_enabled(candidate_data),
            )
            if task_errors:
                errors.extend(task_errors)
                continue
            task_items = _tasks(candidate_data)
            task_items[task_items.index(task)] = candidate
            written_task_ids.append(task_id)
        except (PlanWriterInputError, ValueError) as exc:
            reason = exc.reason if isinstance(exc, PlanWriterInputError) else "draft_task_detail_invalid"
            detail_text = exc.detail if isinstance(exc, PlanWriterInputError) else str(exc)
            errors.append({"reason": reason, "detail": f"task={task_id};{detail_text}"})

    if full and not errors:
        errors.extend(_task_set_preflight_errors(
            feature_dir,
            candidate_data,
            group_data,
            code_workspaces,
            require_engineering_commands=True,
        ))
    return candidate_data, written_task_ids, errors


def _draft_detail_batch_context(
    args: argparse.Namespace,
) -> tuple[
    Path,
    str,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    Path,
    list[str],
]:
    """Load the immutable Draft inputs shared by batch Detail operations."""

    workspace, feature = _resolve(args)
    lock, data = _load_draft_bundle(workspace, feature)
    group_data = _draft_group_data(lock, feature, _tasks(data))
    feature_dir = _path(workspace, feature).parent
    code_workspaces = [
        item for item in lock.get("codeWorkspaces", []) if isinstance(item, str)
    ]
    return (
        workspace,
        feature,
        lock,
        data,
        group_data,
        feature_dir,
        code_workspaces,
    )


def _cmd_lint_draft_task_details(args: argparse.Namespace) -> int:
    """Validate multiple Detail candidates, optionally as one complete Draft."""

    (
        workspace,
        feature,
        lock,
        data,
        group_data,
        feature_dir,
        code_workspaces,
    ) = _draft_detail_batch_context(args)
    if lock.get("status") == "finalized":
        return render_result(fail("task_draft_finalized", path=_draft_plan_path(workspace, feature)))
    design_lock_errors = _draft_design_contract_errors(feature_dir, lock)
    if design_lock_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_lock_errors,
        ))
    design_contract, design_errors = _current_design_contract(feature_dir)
    if design_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_errors,
        ))
    entries = _draft_detail_batch_entries(args)
    candidate_data, candidate_task_ids, errors = _draft_detail_batch_candidate(
        feature,
        feature_dir,
        data,
        group_data,
        entries,
        code_workspaces,
        design_contract,
        full=args.full,
    )
    report = _draft_validation_report(candidate_data, errors)
    return render_result(WriterResult(
        ok=not errors,
        path=_draft_plan_path(workspace, feature),
        errors=report["issues"],
        data={
            "candidateTaskIds": candidate_task_ids,
            "fullValidation": args.full,
            "validation": report,
            "draft": _draft_summary(lock, data),
        },
    ))


def _cmd_set_draft_task_details(args: argparse.Namespace) -> int:
    """Atomically validate and write multiple independent task details."""

    (
        workspace,
        feature,
        lock,
        data,
        group_data,
        feature_dir,
        code_workspaces,
    ) = _draft_detail_batch_context(args)
    if lock.get("status") == "finalized":
        return render_result(fail("task_draft_finalized", path=_draft_plan_path(workspace, feature)))
    design_lock_errors = _draft_design_contract_errors(feature_dir, lock)
    if design_lock_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_lock_errors,
        ))
    design_contract, design_errors = _current_design_contract(feature_dir)
    if design_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_errors,
        ))
    entries = _draft_detail_batch_entries(args)
    candidate_data, written_task_ids, errors = _draft_detail_batch_candidate(
        feature,
        feature_dir,
        data,
        group_data,
        entries,
        code_workspaces,
        design_contract,
        full=args.full,
    )
    if errors:
        report = _draft_validation_report(candidate_data, errors)
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=errors,
            data={"validation": report, "draft": _draft_summary(lock, data)},
        ))

    data = candidate_data
    ordered_ids = [str(item.get("id")) for item in _tasks(data)]
    ready = {
        item for item in lock.get("readyTaskIds", []) if isinstance(item, str)
    }
    ready.update(written_task_ids)
    lock["readyTaskIds"] = [task_id for task_id in ordered_ids if task_id in ready]
    lock["status"] = "ready" if len(ready) == len(ordered_ids) else "collecting"
    result = _write_draft_bundle(workspace, feature, data, lock)
    return render_result(with_result_data(
        result,
        writtenTaskIds=written_task_ids,
        fullValidation=args.full,
        draft=_draft_summary(lock, data),
    ))


def _draft_repair_entries(args: argparse.Namespace, *, single_task: bool) -> list[tuple[str, dict[str, Any]]]:
    body = _draft_detail_body(args)
    if single_task:
        return [(str(args.task_id), body)]
    unknown = sorted(set(body) - TASK_REPAIR_BODY_FIELDS)
    if unknown:
        raise PlanWriterInputError("draft_task_repairs_field_unknown", f"fields={','.join(unknown)}")
    raw_repairs = body.get("repairs")
    if not isinstance(raw_repairs, list) or not raw_repairs:
        raise PlanWriterInputError("draft_task_repairs_missing")
    repairs: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_repairs, start=1):
        if not isinstance(raw, dict):
            raise PlanWriterInputError("draft_task_repair_must_be_object", f"index={index}")
        raw_unknown = sorted(set(raw) - {"taskId", "patch"})
        if raw_unknown:
            raise PlanWriterInputError(
                "draft_task_repair_field_unknown",
                f"index={index};fields={','.join(raw_unknown)}",
            )
        task_id = raw.get("taskId")
        patch = raw.get("patch")
        if not isinstance(task_id, str) or not TASK_GROUP_TASK_ID_RE.fullmatch(task_id):
            raise PlanWriterInputError("draft_task_repair_task_id_invalid", f"index={index};task={task_id}")
        if task_id in seen:
            raise PlanWriterInputError("draft_task_repair_task_id_duplicate", f"task={task_id}")
        if not isinstance(patch, dict):
            raise PlanWriterInputError("draft_task_repair_patch_must_be_object", f"task={task_id}")
        seen.add(task_id)
        repairs.append((task_id, patch))
    return repairs


def _apply_draft_task_repairs(
    workspace: Path,
    feature: str,
    repairs: list[tuple[str, dict[str, Any]]],
    *,
    change_summary: dict[str, Any] | None = None,
) -> WriterResult:
    lock, data = _load_draft_bundle(workspace, feature)
    if lock.get("status") == "finalized":
        return fail("task_draft_finalized", path=_draft_plan_path(workspace, feature))
    group_data = _draft_group_data(lock, feature, _tasks(data))
    feature_dir = _path(workspace, feature).parent
    design_lock_errors = _draft_design_contract_errors(feature_dir, lock)
    if design_lock_errors:
        return WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_lock_errors,
        )
    design_contract, design_errors = _current_design_contract(feature_dir)
    if design_errors:
        return WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_errors,
        )
    code_workspaces = [
        item for item in lock.get("codeWorkspaces", []) if isinstance(item, str)
    ]
    candidate_data = copy.deepcopy(data)
    errors: list[dict[str, Any]] = []
    repaired_task_ids: list[str] = []
    for task_id, patch in repairs:
        try:
            task = _find_task(candidate_data, task_id)
            detail = _merge_draft_task_patch(task, patch)
            candidate = _normalize_draft_task_detail(task, detail)
            candidate = _annotate_validation_test_plan(candidate, code_workspaces, candidate_data)
            task_errors = validate_task_artifact_refs(
                feature_dir,
                candidate,
                design_contract=design_contract,
                check_design_artifact=False,
            )
            task_errors.extend(_draft_task_validation_errors(
                feature,
                candidate,
                code_workspaces,
                defer_to_test_stages=defer_to_test_stages_enabled(candidate_data),
            ))
            if task_errors:
                errors.extend(task_errors)
                continue
            task_items = _tasks(candidate_data)
            task_items[task_items.index(task)] = candidate
            repaired_task_ids.append(task_id)
        except PlanWriterInputError as exc:
            detail = exc.detail or f"task={task_id}"
            if "task=" not in detail:
                detail = f"task={task_id};{detail}"
            errors.append({"reason": exc.reason, "detail": detail})
    if errors:
        report = _draft_validation_report(candidate_data, errors)
        return WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=report["issues"],
            data={"validation": report, "draft": _draft_summary(lock, data)},
        )

    data = candidate_data
    ordered_ids = [str(item.get("id")) for item in _tasks(data)]
    ready = {
        item
        for item in lock.get("readyTaskIds", [])
        if isinstance(item, str) and item in ordered_ids
    }
    ready.update(repaired_task_ids)
    lock["readyTaskIds"] = [task_id for task_id in ordered_ids if task_id in ready]
    lock["status"] = "ready" if len(ready) == len(ordered_ids) else "collecting"
    if change_summary is not None:
        lock["lastChange"] = copy.deepcopy(change_summary)
    write_result = _write_draft_bundle(workspace, feature, data, lock)
    remaining_errors: list[dict[str, Any]] = []
    if len(ready) == len(ordered_ids):
        remaining_errors = _task_set_preflight_errors(
            _path(workspace, feature).parent,
            data,
            group_data,
            code_workspaces,
            require_engineering_commands=True,
        )
    report = _draft_validation_report(data, remaining_errors)
    return WriterResult(
        ok=True,
        path=write_result.path,
        changed=write_result.changed,
        data={
            "repairedTaskIds": repaired_task_ids,
            "repairComplete": report["ok"] and len(ready) == len(ordered_ids),
            "validation": report,
            "draft": _draft_summary(lock, data),
        },
    )


def _cmd_repair_draft_task(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    repairs = _draft_repair_entries(args, single_task=True)
    return render_result(_apply_draft_task_repairs(workspace, feature, repairs))


def _cmd_repair_draft_tasks(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    repairs = _draft_repair_entries(args, single_task=False)
    return render_result(_apply_draft_task_repairs(workspace, feature, repairs))


def _validate_draft_engineering_commands(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Batch compilation is no longer a Draft completion prerequisite.

    Task tests and optional static quality gates retain their own contracts;
    no lane is required to declare a cross-task compile command.
    """
    del data
    return []


def _draft_preflight(
    workspace: Path,
    feature: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    lock, data = _load_draft_bundle(workspace, feature)
    group_data = _draft_group_data(lock, feature, _tasks(data))
    design_lock_errors = _draft_design_contract_errors(_path(workspace, feature).parent, lock)
    if design_lock_errors:
        return lock, data, group_data, design_lock_errors
    task_ids = [str(task.get("id")) for task in _tasks(data)]
    ready = {item for item in lock.get("readyTaskIds", []) if isinstance(item, str)}
    pending = [task_id for task_id in task_ids if task_id not in ready]
    if pending:
        return lock, data, group_data, [{
            "reason": "draft_task_not_ready",
            "detail": f"taskIds={','.join(pending)}",
            "taskIds": pending,
            "repairTarget": "task_detail",
        }]
    code_workspaces = [
        item for item in lock.get("codeWorkspaces", []) if isinstance(item, str)
    ]
    errors = _task_set_preflight_errors(
        _path(workspace, feature).parent,
        data,
        group_data,
        code_workspaces,
        require_engineering_commands=True,
    )
    return lock, data, group_data, errors


def _cmd_preflight_task_draft(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    lock, data, _, errors = _draft_preflight(workspace, feature)
    report = _draft_validation_report(data, errors)
    return render_result(WriterResult(
        ok=not errors,
        path=_draft_plan_path(workspace, feature),
        errors=report["issues"],
        data={"draft": _draft_summary(lock, data), "validation": report},
    ))


def _cmd_show_task_draft(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    lock, data = _load_draft_bundle(workspace, feature)
    _draft_group_data(lock, feature, _tasks(data))
    return render_result(WriterResult(
        ok=True,
        path=_draft_plan_path(workspace, feature),
        data={"draft": _draft_summary(lock, data)},
    ))


def _compact_group_projection(group: dict[str, Any]) -> dict[str, Any]:
    """Render only stable, task-local planner context for a model call."""

    spec_refs = [item for item in group.get("specRefs", []) if isinstance(item, str)]
    refs = {
        "requirements": [item for item in spec_refs if TASK_GROUP_REQUIREMENT_ID_RE.search(item)],
        "scenarios": [item for item in spec_refs if SCENARIO_ID_RE.search(item)],
        "api": copy.deepcopy(group.get("apiIds", [])),
    }
    write_targets = group.get("writeTargets")
    compact_write_set = (
        copy.deepcopy(write_targets)
        if isinstance(write_targets, list)
        else [{"path": path} for path in group.get("touches", []) if isinstance(path, str)]
    )
    projected: dict[str, Any] = {
        "id": group.get("id"),
        "outcome": group.get("title"),
        "dependsOn": copy.deepcopy(group.get("deps", [])),
        "mode": group.get("executionMode", "code"),
        "stage": group.get("executionStage", "parallel"),
        "workspace": group.get("workspaceRef"),
        "writeSet": compact_write_set,
        "refs": refs,
        "validation": {"seam": group.get("validationBoundary")},
    }
    if isinstance(group.get("splitRationale"), str):
        projected["validation"]["mergeJustification"] = group["splitRationale"]
    ui_refs = group.get("uiRefs")
    if isinstance(ui_refs, dict):
        projected["ui"] = {
            "pages": copy.deepcopy(ui_refs.get("pageRefs", [])),
            "interactions": copy.deepcopy(ui_refs.get("interactionRefs", [])),
            "visualSources": copy.deepcopy(ui_refs.get("visualSourceRefs", [])),
            "route": ui_refs.get("frontendRoute"),
        }
    if isinstance(group.get("externalDependency"), dict):
        projected["external"] = copy.deepcopy(group["externalDependency"])
    if isinstance(group.get("atomicGroup"), dict):
        projected["atomic"] = copy.deepcopy(group["atomicGroup"])
    return projected


def _compact_detail_projection(task: dict[str, Any]) -> dict[str, Any]:
    detail = _draft_task_detail_projection(task)
    scope = detail.get("scope") if isinstance(detail.get("scope"), dict) else {}
    return {
        "schemaVersion": PLAN_DETAIL_SCHEMA,
        "outcome": detail.get("goal"),
        "context": {
            "modules": copy.deepcopy(scope.get("modules", [])),
            "entrypoints": copy.deepcopy(scope.get("entrypoints", [])),
            "dataObjects": copy.deepcopy(scope.get("dataObjects", [])),
        },
        "implementation": copy.deepcopy(detail.get("implementationPoints", [])),
        "acceptance": copy.deepcopy(detail.get("acceptanceCriteria", [])),
        "checks": copy.deepcopy(detail.get("validationCommands", [])),
        "nonGoals": copy.deepcopy(detail.get("nonGoals", [])),
        "refs": {
            "design": copy.deepcopy(detail.get("designRefs", [])),
            "data": copy.deepcopy(detail.get("dataIds", [])),
            "decisions": copy.deepcopy(detail.get("decisionIds", [])),
        },
        "expectedFiles": copy.deepcopy(detail.get("expectedFiles", [])),
        "blockers": copy.deepcopy(detail.get("blockers", [])),
    }


def _cmd_show_draft_task_work(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    lock, data = _load_draft_bundle(workspace, feature)
    group_data = _draft_group_data(lock, feature, _tasks(data))
    task = _find_task(data, args.task_id)
    group = next((item for item in _task_groups(group_data) if item.get("id") == args.task_id), None)
    if group is None:
        return render_result(fail("task_group_not_found", args.task_id, path=_draft_plan_path(workspace, feature)))
    matrix_obligation = next(
        (item for item in _matrix_detail_obligations(_task_groups(group_data))
         if item.get("taskId") == args.task_id),
        None,
    )
    return render_result(WriterResult(
        ok=True,
        path=_draft_plan_path(workspace, feature),
        data={
            "taskWork": {
                "schemaVersion": "autodev.plan-task-work.v1",
                "baseRevision": lock.get("revision", 0),
                "taskId": args.task_id,
                "core": _compact_group_projection(group),
                "currentDetail": _compact_detail_projection(task),
                "detailObligation": matrix_obligation,
                "requiredOutput": PLAN_DETAIL_SCHEMA,
                "writerOwned": ["acceptance.id", "checks.id", "checks.cwd", "scope.paths"],
                "note": "仅返回 schemaVersion=autodev.plan-detail.v1 的单个任务详情；不要返回 PLAN.md、Batch 或其他 Task。",
            },
        },
    ))


def _repair_issue_field(issue: dict[str, Any]) -> str | None:
    field = issue.get("field")
    if not isinstance(field, str) or not field.strip():
        return None
    compact_aliases = {
        "outcome": "goal",
        "context": "scope",
        "implementation": "implementationPoints",
        "acceptance": "acceptanceCriteria",
        "checks": "validationCommands",
    }
    if field.startswith("/tasks/"):
        match = REPAIR_PATCH_PATH_RE.fullmatch(field)
        return match.group(2) if match else None
    top = re.split(r"[.\[]", field.strip(), maxsplit=1)[0]
    return compact_aliases.get(top, top)


def _group_patch_current_value(group: dict[str, Any], field: str) -> Any:
    """Return the compact Core value used for a guarded group patch."""

    runtime_field = GROUP_REPAIR_FIELD_TO_RUNTIME_FIELD[field]
    value = group.get(runtime_field)
    if field == "writeSet":
        targets = group.get("writeTargets")
        if isinstance(targets, list):
            return copy.deepcopy(targets)
        return [{"path": path} for path in value if isinstance(value, list) and isinstance(path, str)]
    return copy.deepcopy(value)


def _repair_group_compact_fields(issue: dict[str, Any]) -> list[str]:
    """Map a group-level failure to its smallest editable Core fields."""

    reason = str(issue.get("reason", ""))
    field = _repair_issue_field(issue)
    if reason == "shared_write_path_requires_single_owner" or field in {
        "touches", "writeTargets", "writeSet", "scope.paths", "expectedFiles",
    }:
        # The owner choice needs one removal/addition in writeSet and, when a
        # consumer needs the produced contract, an explicit dependency.
        return ["writeSet", "dependsOn"]
    if field in {"deps", "dependsOn"}:
        return ["dependsOn"]
    if field in {"splitRationale", "validation.mergeJustification"}:
        return ["validation.mergeJustification"]
    return []


def _repair_issue_allowed_ops(
    issue: dict[str, Any],
    data: dict[str, Any],
    group_data: dict[str, Any],
) -> list[dict[str, Any]]:
    if issue.get("repairTarget") == "task_group":
        fields = _repair_group_compact_fields(issue)
        if not fields:
            return []
        groups_by_id = {
            str(group.get("id")): group
            for group in _task_groups(group_data)
            if isinstance(group.get("id"), str)
        }
        allowed: list[dict[str, Any]] = []
        for task_id in issue.get("taskIds", []):
            group = groups_by_id.get(task_id) if isinstance(task_id, str) else None
            if group is None:
                continue
            for editable_field in fields:
                current = _group_patch_current_value(group, editable_field)
                allowed.append({
                    "op": "replace",
                    "path": f"/groups/{task_id}/{editable_field}",
                    "currentHash": _json_digest(current),
                    "currentValue": current,
                })
        return allowed
    if issue.get("repairTarget") != "task_detail":
        return []
    field = _repair_issue_field(issue)
    fields = [field] if field in TASK_DETAIL_PATCH_FIELDS else []
    if not fields:
        return []
    allowed: list[dict[str, Any]] = []
    for task_id in issue.get("taskIds", []):
        if not isinstance(task_id, str):
            continue
        try:
            task = _find_task(data, task_id)
        except PlanWriterInputError:
            continue
        for editable_field in fields:
            current = copy.deepcopy(task.get(editable_field))
            allowed.append({
                "op": "replace",
                "path": f"/tasks/{task_id}/{editable_field}",
                "currentHash": _json_digest(current),
                "currentValue": current,
            })
    return allowed


def _repair_work_issues(
    raw: list[dict[str, Any]],
    data: dict[str, Any],
    group_data: dict[str, Any],
) -> list[dict[str, Any]]:
    structured = _structured_draft_issues(raw)
    work_issues: list[dict[str, Any]] = []
    for issue in structured:
        allowed_ops = _repair_issue_allowed_ops(issue, data, group_data)
        issue["reasonCode"] = issue.get("reason")
        issue["reason"] = issue.get("repairSuggestion") or issue.get("detail") or issue.get("reason")
        issue["allowedOps"] = allowed_ops
        if issue.get("repairTarget") == "task_group" and allowed_ops:
            issue["recommendedAction"] = (
                "仅使用 allowedOps 中的 Core replace 操作；不要删除或重建 task-groups.json。"
                "脚本会校验整个候选分组，并只重置 Core 投影发生变化的 resetTaskIds。"
            )
        elif issue.get("repairTarget") == "task_group":
            issue["recommendedAction"] = "该分组错误没有可安全自动修改的字段；保留现有 task-groups.json，先回到覆盖矩阵或设计决策定位原因。"
        elif issue.get("repairTarget") == "design_revision":
            issue["recommendedAction"] = "回到 Design 修订并重新锁定；Plan Draft 不允许绕过设计锁。"
        elif not allowed_ops:
            issue["recommendedAction"] = "该错误没有可安全自动修改的单字段 patch；先补充 field 和 taskId，或按 repairTarget 处理。"
        else:
            issue["recommendedAction"] = "仅使用 allowedOps 中的 replace 操作；提交后脚本会原子校验并仅重校验受影响任务。"
        issue["successCondition"] = f"重新预检后不再出现 {issue.get('reason')}。"
        work_issues.append(issue)
    return work_issues


def _repair_feedback_issues(
    args: argparse.Namespace,
    workspace: Path,
    feature: str,
    data: dict[str, Any],
    group_data: dict[str, Any],
) -> list[dict[str, Any]]:
    if args.feedback_file:
        feedback = read_object_file(args.feedback_file)
        validation = feedback.get("validation")
        raw = validation.get("issues") if isinstance(validation, dict) else feedback.get("issues")
        if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
            raise PlanWriterInputError("repair_feedback_issues_missing")
        return [copy.deepcopy(item) for item in raw]
    errors = _task_set_preflight_errors(
        _path(workspace, feature).parent,
        data,
        group_data,
        [item for item in data.get("codeWorkspaces", []) if isinstance(item, str)],
    )
    return errors


def _cmd_create_repair_work(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    group_file_arg = getattr(args, "group_file", None)
    if group_file_arg:
        group_file = Path(group_file_arg).expanduser().resolve()
        if not group_file.is_file():
            return render_result(fail(
                "task_group_source_missing_incremental_repair_forbidden",
                "保留原 task-groups.json；不能删除后全量重新生成",
                path=group_file,
            ))
        group_data = _load_task_group_file(group_file, feature)
        # A finalized Draft can be stale for both its Design snapshot and its
        # Core digest.  A restricted patch cannot safely repair that state, so
        # return the same explicit recovery disposition as diagnose instead of
        # emitting an empty repair-work item and sending callers in circles.
        if _draft_lock_path(workspace, feature).is_file():
            try:
                draft_lock, draft_data = _load_draft_bundle(workspace, feature)
                design_drift = _draft_design_contract_errors(
                    _path(workspace, feature).parent,
                    draft_lock,
                )
                core_drift = _draft_group_drift(draft_lock, feature, _tasks(draft_data))
                if design_drift and core_drift.get("changed") is True:
                    return render_result(WriterResult(
                        ok=False,
                        path=group_file,
                        errors=[{
                            "reason": "full_rebuild_required",
                            "detail": "design_and_group_changed_after_draft_created;"
                            f"{core_drift.get('detail')}",
                            "repairTarget": "full_rebuild",
                            "repairable": False,
                            "nextCommand": (
                                "rebuild-finalized-draft --group-file <file> "
                                "--design-revision-confirmed --reason <reason>"
                            ),
                        }],
                    ))
            except PlanWriterInputError:
                # Pre-Draft repair work remains available when an interrupted
                # Draft cannot be loaded; diagnose owns its recovery path.
                pass
        data = {"featureId": feature, "tasks": []}
        if args.feedback_file:
            raw_issues = _repair_feedback_issues(args, workspace, feature, data, group_data)
        else:
            raw_issues = _task_group_preflight_errors(
                _path(workspace, feature).parent,
                group_data,
            )
        revision = None
        work_source = {
            "kind": "plan_core",
            "groupFile": str(group_file),
            "groupingDigest": _task_group_digest(group_data),
        }
    else:
        lock, data = _load_draft_bundle(workspace, feature)
        group_data = _draft_group_data(lock, feature, _tasks(data))
        raw_issues = _repair_feedback_issues(args, workspace, feature, data, group_data)
        revision = lock.get("revision", 0)
        work_source = {"kind": "draft", "groupingDigest": _task_group_digest(group_data)}
    issues = _repair_work_issues(raw_issues, data, group_data)
    work_id = f"RW-{int(revision) if isinstance(revision, int) else 0:04d}-{_json_digest({'issues': issues, 'source': work_source})[7:15]}"
    work = {
        "schemaVersion": PLAN_REPAIR_WORK_SCHEMA,
        "workId": work_id,
        "featureId": feature,
        "createdAt": _utc_now(),
        "status": "open",
        "source": work_source,
        "issues": issues,
    }
    if revision is not None:
        work["baseRevision"] = revision
    path = _draft_repair_work_dir(workspace, feature) / f"{work_id}.json"
    changed = atomic_write_json(path, work)
    return render_result(WriterResult(
        ok=True,
        path=path,
        changed=changed,
        data={"repairWork": work},
    ))


def _patch_body(args: argparse.Namespace) -> dict[str, Any]:
    if args.patch_file:
        return read_object_file(args.patch_file)
    if args.patch_stdin:
        return _plan_writer_stdin_body()
    raise PlanWriterInputError("draft_patch_input_missing")


def _normalize_group_patch_value(field: str, value: Any, *, task_id: str) -> Any:
    """Validate a group patch in compact Core spelling before persistence."""

    if field == "dependsOn":
        return _compact_string_list(value, task_id=task_id, field="dependsOn")
    if field == "writeSet":
        return _compact_write_targets(value, task_id=task_id)
    if field == "validation.mergeJustification":
        if not isinstance(value, str) or not value.strip():
            raise PlanWriterInputError("compact_plan_core_merge_justification_invalid", f"task={task_id}")
        return value.strip()
    raise PlanWriterInputError("draft_patch_group_field_invalid", f"task={task_id};field={field}")


def _patch_group_source(
    source: dict[str, Any],
    task_id: str,
    field: str,
    value: Any,
) -> dict[str, Any]:
    """Patch exactly one model-facing Core field without changing source shape."""

    candidate = copy.deepcopy(source)
    compact_source = candidate.get("schemaVersion") == PLAN_CORE_SCHEMA
    collection_name = "tasks" if compact_source else "groups"
    entries = candidate.get(collection_name)
    if not isinstance(entries, list):
        raise PlanWriterInputError("task_group_source_invalid", f"collection={collection_name}")
    entry = next(
        (item for item in entries if isinstance(item, dict) and item.get("id") == task_id),
        None,
    )
    if entry is None:
        raise PlanWriterInputError("task_group_not_found", task_id)
    if compact_source:
        if field == "validation.mergeJustification":
            validation = entry.get("validation")
            if not isinstance(validation, dict):
                raise PlanWriterInputError("compact_plan_core_validation_missing", f"task={task_id}")
            validation["mergeJustification"] = value
        else:
            entry[field] = value
        return candidate

    runtime_field = GROUP_REPAIR_FIELD_TO_RUNTIME_FIELD[field]
    if field == "writeSet":
        entry[runtime_field] = [item["path"] for item in value]
        entry["writeTargets"] = copy.deepcopy(value)
    else:
        entry[runtime_field] = value
    return candidate


def _group_patch_candidate(
    group_file: Path,
    feature_dir: Path,
    expected_grouping_digest: str,
    group_repairs: list[tuple[str, str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | WriterResult:
    """Build and validate a minimal Core source replacement without writing it."""

    if not group_file.is_file():
        return fail(
            "task_group_source_missing_incremental_repair_forbidden",
            "保留原 task-groups.json；不能删除后用全量生成替代定点修复",
            path=group_file,
        )
    source = read_object_file(group_file)
    try:
        current_group_data = _compact_plan_core_to_groups(source)
    except PlanWriterInputError as exc:
        return fail(exc.reason, exc.detail, path=group_file)
    if _task_group_digest(current_group_data) != expected_grouping_digest:
        return fail(
            "draft_patch_group_source_conflict",
            "task-groups.json 在 repair work 创建后发生变化；请重新生成 repair work",
            path=group_file,
        )
    candidate_source = source
    try:
        for task_id, field, raw_value in group_repairs:
            value = _normalize_group_patch_value(field, raw_value, task_id=task_id)
            candidate_source = _patch_group_source(candidate_source, task_id, field, value)
        candidate_group_data = _compact_plan_core_to_groups(candidate_source)
    except PlanWriterInputError as exc:
        return fail(exc.reason, exc.detail, path=group_file)
    errors = _task_group_preflight_errors(feature_dir, candidate_group_data)
    if errors:
        return WriterResult(ok=False, path=group_file, errors=errors)
    return source, candidate_source, candidate_group_data


def _apply_draft_group_patch(
    workspace: Path,
    feature: str,
    *,
    work_id: str,
    resolves: list[str],
    group_repairs: list[tuple[str, str, Any]],
    changed_fields_by_task: dict[str, list[str]],
    reason: str | None,
) -> WriterResult:
    """Apply a locked Plan Core patch and reproject only affected Draft tasks."""

    if _path(workspace, feature).is_file():
        return fail("formal_plan_already_exists", path=_path(workspace, feature))
    lock, data = _load_draft_bundle(workspace, feature)
    if lock.get("status") == "finalized":
        return fail("task_draft_finalized", path=_draft_plan_path(workspace, feature))
    group_data = _draft_group_data(lock, feature, _tasks(data))
    design_lock_errors = _draft_design_contract_errors(_path(workspace, feature).parent, lock)
    if design_lock_errors:
        return WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_lock_errors,
        )
    group_file_value = lock.get("groupFile")
    group_file = Path(group_file_value).expanduser().resolve() if isinstance(group_file_value, str) else None
    if group_file is None:
        return fail("task_draft_group_file_missing", path=_draft_plan_path(workspace, feature))
    candidate = _group_patch_candidate(
        group_file,
        _path(workspace, feature).parent,
        _task_group_digest(group_data),
        group_repairs,
    )
    if isinstance(candidate, WriterResult):
        return candidate
    _, candidate_source, candidate_group_data = candidate

    code_workspaces = [
        item for item in lock.get("codeWorkspaces", []) if isinstance(item, str) and item
    ]
    change_summary = {
        "kind": "restricted_group_patch",
        "repairWorkId": work_id,
        "issueIds": list(resolves),
        "changedFieldsByTask": copy.deepcopy(changed_fields_by_task),
        "reason": reason,
        "sourceFile": str(group_file),
    }
    # Validate all group invariants before touching the source.  The source is
    # then atomically replaced and the Draft is reprojected from that exact
    # candidate; no temporary deletion or regenerated task-group file exists.
    source_changed = atomic_write_json(group_file, candidate_source)
    result = _rebuild_task_draft_from_group_data(
        workspace,
        feature,
        lock,
        data,
        group_file,
        candidate_group_data,
        code_workspaces,
        last_change=change_summary,
    )
    if not result.ok:
        return result
    result_data = dict(result.data or {})
    if result_data:
        reset = result_data.get("resetTaskIds", [])
        preserved = result_data.get("preservedTaskIds", [])
        change_summary["resetTaskIds"] = copy.deepcopy(reset)
        change_summary["preservedTaskIds"] = copy.deepcopy(preserved)
        change_summary["revalidatedTaskIds"] = sorted(changed_fields_by_task)
        change_summary["reusedTaskIds"] = copy.deepcopy(preserved)
        result_data["incrementalChange"] = change_summary
    return WriterResult(
        ok=True,
        path=result.path,
        changed=result.changed or source_changed,
        errors=result.errors,
        data=result_data,
    )


def _apply_plan_core_patch(
    workspace: Path,
    feature: str,
    *,
    group_file: Path,
    base_grouping_digest: str,
    work_id: str,
    resolves: list[str],
    group_repairs: list[tuple[str, str, Any]],
    changed_fields_by_task: dict[str, list[str]],
    reason: str | None,
) -> WriterResult:
    """Repair a pre-Draft Plan Core in place, without fabricating a new file."""

    candidate = _group_patch_candidate(
        group_file,
        _path(workspace, feature).parent,
        base_grouping_digest,
        group_repairs,
    )
    if isinstance(candidate, WriterResult):
        return candidate
    _, candidate_source, candidate_group_data = candidate
    changed = atomic_write_json(group_file, candidate_source)
    incremental_change = {
        "kind": "restricted_plan_core_patch",
        "repairWorkId": work_id,
        "issueIds": list(resolves),
        "changedFieldsByTask": copy.deepcopy(changed_fields_by_task),
        "revalidatedTaskIds": sorted(changed_fields_by_task),
        "reason": reason,
        "sourceFile": str(group_file),
        "groupingDigest": _task_group_digest(candidate_group_data),
        "draftReprojection": "not_required_before_prepare_task_draft",
    }
    return WriterResult(
        ok=True,
        path=group_file,
        changed=changed,
        data={"incrementalChange": incremental_change},
    )


def _cmd_apply_draft_patch(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    patch = _patch_body(args)
    if patch.get("schemaVersion") != PLAN_REPAIR_PATCH_SCHEMA:
        return render_result(fail("draft_patch_schema_invalid"))
    work_id = patch.get("workId")
    if not isinstance(work_id, str) or not REPAIR_WORK_ID_RE.fullmatch(work_id):
        return render_result(fail("draft_patch_work_id_missing"))
    work_path = _draft_repair_work_dir(workspace, feature) / f"{work_id}.json"
    work = read_object_file(work_path)
    if work.get("schemaVersion") != PLAN_REPAIR_WORK_SCHEMA or work.get("featureId") != feature:
        return render_result(fail("draft_patch_repair_work_invalid", path=work_path))
    if work.get("status") != "open":
        return render_result(fail("draft_patch_repair_work_not_open", path=work_path))
    source = work.get("source") if isinstance(work.get("source"), dict) else {}
    is_plan_core_work = source.get("kind") == "plan_core"
    if is_plan_core_work:
        group_file_value = source.get("groupFile")
        base_grouping_digest = source.get("groupingDigest")
        if not isinstance(group_file_value, str) or not isinstance(base_grouping_digest, str):
            return render_result(fail("draft_patch_repair_work_invalid", path=work_path))
        group_file = Path(group_file_value).expanduser().resolve()
        if patch.get("baseGroupingDigest") != base_grouping_digest:
            return render_result(fail(
                "plan_core_patch_revision_conflict",
                f"expected={base_grouping_digest};patch={patch.get('baseGroupingDigest')}",
                path=group_file,
            ))
        group_data = _load_task_group_file(group_file, feature)
        if _task_group_digest(group_data) != base_grouping_digest:
            return render_result(fail(
                "plan_core_patch_revision_conflict",
                "task-groups.json 已变化；请重新生成 repair work",
                path=group_file,
            ))
        data = {"featureId": feature, "tasks": []}
    else:
        lock, data = _load_draft_bundle(workspace, feature)
        revision = lock.get("revision", 0)
        if patch.get("baseRevision") != revision or work.get("baseRevision") != revision:
            return render_result(fail(
                "draft_patch_revision_conflict",
                f"expected={revision};patch={patch.get('baseRevision')};work={work.get('baseRevision')}",
                path=_draft_plan_path(workspace, feature),
            ))
        group_data = _draft_group_data(lock, feature, _tasks(data))
    resolves = patch.get("resolves")
    operations = patch.get("ops")
    if not isinstance(resolves, list) or not resolves or any(not isinstance(item, str) for item in resolves):
        return render_result(fail("draft_patch_resolves_invalid", path=work_path))
    if not isinstance(operations, list) or not operations or any(not isinstance(item, dict) for item in operations):
        return render_result(fail("draft_patch_ops_invalid", path=work_path))
    issue_by_id = {
        item.get("issueId"): item
        for item in work.get("issues", [])
        if isinstance(item, dict) and isinstance(item.get("issueId"), str)
    }
    if any(issue_id not in issue_by_id for issue_id in resolves):
        return render_result(fail("draft_patch_issue_not_in_work", path=work_path))
    allowed = {
        (issue_id, item.get("path")): item
        for issue_id in resolves
        for item in issue_by_id[issue_id].get("allowedOps", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    repairs_by_task: dict[str, dict[str, Any]] = {}
    group_repairs: list[tuple[str, str, Any]] = []
    changed_fields_by_task: dict[str, list[str]] = {}
    groups_by_id = {
        str(group.get("id")): group
        for group in _task_groups(group_data)
        if isinstance(group.get("id"), str)
    }
    seen_paths: set[str] = set()
    for operation in operations:
        path = operation.get("path")
        task_match = REPAIR_PATCH_PATH_RE.fullmatch(path) if isinstance(path, str) else None
        group_match = REPAIR_GROUP_PATCH_PATH_RE.fullmatch(path) if isinstance(path, str) else None
        if operation.get("op") != "replace" or (task_match is None and group_match is None):
            return render_result(fail("draft_patch_operation_invalid", str(path), path=work_path))
        if path in seen_paths or not any((issue_id, path) in allowed for issue_id in resolves):
            return render_result(fail("draft_patch_path_not_allowed", str(path), path=work_path))
        expected_hash = operation.get("expectedHash")
        allowed_entry = next(item for issue_id in resolves if (item := allowed.get((issue_id, path))) is not None)
        if expected_hash != allowed_entry.get("currentHash"):
            return render_result(fail("draft_patch_precondition_invalid", str(path), path=work_path))
        if task_match is not None:
            task_id, field = task_match.groups()
            task = _find_task(data, task_id)
            if _json_digest(task.get(field)) != expected_hash:
                return render_result(fail("draft_patch_precondition_conflict", str(path), path=_draft_plan_path(workspace, feature)))
            repairs_by_task.setdefault(task_id, {})[field] = copy.deepcopy(operation.get("value"))
        else:
            assert group_match is not None
            task_id, field = group_match.groups()
            group = groups_by_id.get(task_id)
            if group is None or _json_digest(_group_patch_current_value(group, field)) != expected_hash:
                return render_result(fail("draft_patch_precondition_conflict", str(path), path=_draft_plan_path(workspace, feature)))
            group_repairs.append((task_id, field, copy.deepcopy(operation.get("value"))))
        changed_fields_by_task.setdefault(task_id, []).append(field)
        seen_paths.add(path)
    if repairs_by_task and group_repairs:
        return render_result(fail(
            "draft_patch_mixed_targets_not_allowed",
            "先应用 task_group patch 并只重填 resetTaskIds，再为 task_detail 创建新的 repair work",
            path=work_path,
        ))
    if group_repairs:
        if is_plan_core_work:
            result = _apply_plan_core_patch(
                workspace,
                feature,
                group_file=group_file,
                base_grouping_digest=base_grouping_digest,
                work_id=work_id,
                resolves=list(resolves),
                group_repairs=group_repairs,
                changed_fields_by_task=changed_fields_by_task,
                reason=patch.get("reason") if isinstance(patch.get("reason"), str) else None,
            )
        else:
            result = _apply_draft_group_patch(
                workspace,
                feature,
                work_id=work_id,
                resolves=list(resolves),
                group_repairs=group_repairs,
                changed_fields_by_task=changed_fields_by_task,
                reason=patch.get("reason") if isinstance(patch.get("reason"), str) else None,
            )
        if result.ok:
            work["status"] = "applied"
            work["appliedAt"] = _utc_now()
            work["appliedRevision"] = result.data.get("draft", {}).get("revision") if isinstance(result.data, dict) else None
            work["appliedPatch"] = {
                "issueIds": list(resolves),
                "paths": sorted(seen_paths),
            }
            atomic_write_json(work_path, work)
        return render_result(result)
    changed_task_ids = sorted(repairs_by_task)
    change_summary = {
        "kind": "restricted_patch",
        "repairWorkId": work_id,
        "issueIds": list(resolves),
        "changedFieldsByTask": changed_fields_by_task,
        "revalidatedTaskIds": changed_task_ids,
        "reusedTaskIds": [
            str(task.get("id")) for task in _tasks(data)
            if isinstance(task.get("id"), str) and str(task.get("id")) not in changed_task_ids
        ],
        "reason": patch.get("reason") if isinstance(patch.get("reason"), str) else None,
    }
    result = _apply_draft_task_repairs(
        workspace,
        feature,
        [(task_id, repair) for task_id, repair in repairs_by_task.items()],
        change_summary=change_summary,
    )
    if result.ok:
        work["status"] = "applied"
        work["appliedAt"] = _utc_now()
        work["appliedRevision"] = result.data.get("draft", {}).get("revision") if isinstance(result.data, dict) else None
        work["appliedPatch"] = {
            "issueIds": list(resolves),
            "paths": sorted(seen_paths),
        }
        atomic_write_json(work_path, work)
        if isinstance(result.data, dict):
            result.data["incrementalChange"] = change_summary
    return render_result(result)


def _cmd_diagnose_plan_repair(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    formal_root, formal_batches, formal_load_errors = _load_raw_formal_bundle(workspace, feature)
    formal_validation_errors: list[str] = []
    if formal_root is not None and not formal_load_errors:
        formal_validation_errors.extend(validate_plan_bundle_data(formal_root, formal_batches))
    checkpoint, execution_blockers = _formal_execution_blockers(
        workspace,
        feature,
        formal_root,
        formal_batches,
        formal_load_errors,
    )

    draft_available = False
    draft_valid = False
    draft_status: str | None = None
    draft_error: str | None = None
    draft_summary: dict[str, Any] | None = None
    design_changed = False
    group_changed = False
    group_drift: dict[str, Any] | None = None
    try:
        draft_lock, draft_data = _load_draft_bundle(workspace, feature)
        draft_available = True
        draft_status = str(draft_lock.get("status"))
        draft_summary = _draft_summary(draft_lock, draft_data)
        design_lock_errors = _draft_design_contract_errors(
            _path(workspace, feature).parent,
            draft_lock,
        )
        group_drift = _draft_group_drift(draft_lock, feature, _tasks(draft_data))
        design_changed = any(
            error.get("reason") == "confirmed_design_changed_after_draft_created"
            for error in design_lock_errors
        )
        group_changed = group_drift.get("changed") is True
        draft_valid = not design_lock_errors and not group_changed
        if design_lock_errors:
            first = design_lock_errors[0]
            draft_error = str(first.get("reason"))
            if first.get("detail"):
                draft_error += f":{first['detail']}"
        elif group_changed:
            draft_error = str(group_drift.get("reason"))
            detail = group_drift.get("detail")
            if detail:
                draft_error += f":{detail}"
    except PlanWriterInputError as exc:
        draft_available = _draft_lock_path(workspace, feature).is_file()
        draft_error = f"{exc.reason}:{exc.detail}" if exc.detail else exc.reason

    formal_available = formal_root is not None
    formal_valid = formal_available and not formal_load_errors and not formal_validation_errors
    if not formal_available:
        artifact_state = "draft" if draft_available else "missing"
    elif formal_valid:
        artifact_state = "finalized"
    else:
        artifact_state = "finalized_corrupt"

    if execution_blockers:
        recommended = "plan_revision_required"
    elif design_changed and group_changed:
        recommended = "full_rebuild_required"
    elif design_changed:
        recommended = "design_revision_required"
    elif group_changed:
        recommended = "task_group_rebuild_required"
    elif not draft_available or not draft_valid:
        recommended = "full_rebuild_required"
    elif draft_status != "finalized":
        recommended = "continue_draft_repair"
    else:
        recommended = "reopen-finalized-draft"

    return render_result(WriterResult(
        ok=True,
        path=_path(workspace, feature),
        data={
            "diagnosis": {
                "artifactState": artifact_state,
                "checkpoint": checkpoint,
                "formalPlanAvailable": formal_available,
                "formalPlanValid": formal_valid,
                "formalValidationErrors": formal_load_errors + formal_validation_errors,
                "draftAvailable": draft_available,
                "draftValid": draft_valid,
                "draftStatus": draft_status,
                "draftError": draft_error,
                "drift": {
                    "designChanged": design_changed,
                    "groupChanged": group_changed,
                    "groupFile": group_drift.get("groupFile") if group_drift else None,
                    "grouping": {
                        key: group_drift.get(key)
                        for key in ("expectedDigest", "actualDigest", "reason", "detail")
                    } if group_drift else None,
                },
                "executionStarted": bool(execution_blockers),
                "executionBlockers": execution_blockers,
                "recommendedCommand": recommended,
                "nextCommand": (
                    "rebuild-finalized-draft --group-file <file> "
                    "--design-revision-confirmed --reason <reason>"
                    if recommended == "full_rebuild_required" and design_changed and group_changed
                    else None
                ),
                "draft": draft_summary,
            },
        },
    ))


def _cmd_reopen_finalized_draft(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    lock, data = _load_draft_bundle(workspace, feature)
    if lock.get("status") != "finalized":
        return render_result(fail(
            "task_draft_not_finalized",
            f"status={lock.get('status')}",
            path=_draft_plan_path(workspace, feature),
        ))
    feature_dir = _path(workspace, feature).parent
    design_contract, design_errors = _current_design_contract(feature_dir)
    if design_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_errors,
        ))
    design_snapshot = lock.get("designContract")
    design_changed = (
        isinstance(design_snapshot, dict)
        and isinstance(design_snapshot.get("sha256"), str)
        and design_snapshot.get("sha256") != design_contract.get("sha256")
    )
    group_drift = _draft_group_drift(lock, feature, _tasks(data))
    group_changed = group_drift.get("changed") is True
    if design_changed and group_changed:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=[{
                "reason": "full_rebuild_required",
                "detail": "design_and_group_changed_after_draft_created;"
                f"{group_drift.get('detail')}",
                "repairTarget": "full_rebuild",
                "repairable": False,
                "designRevisionConfirmed": args.design_revision_confirmed is True,
                "nextCommand": (
                    "rebuild-finalized-draft --group-file <file> "
                    "--design-revision-confirmed --reason <reason>"
                ),
            }],
        ))
    if design_changed and args.design_revision_confirmed is not True:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=[{
                "reason": "confirmed_design_changed_after_draft_created",
                "detail": "pass --design-revision-confirmed only after the Design revision was separately confirmed",
                "repairTarget": "design_revision",
                "repairable": False,
                "designMutationAllowed": False,
            }],
        ))
    if group_changed:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=[{
                "reason": "task_group_changed_after_draft_created",
                "detail": str(group_drift.get("detail") or ""),
                "repairTarget": "task_group",
                "repairable": False,
                "nextCommand": "rebuild-finalized-draft --group-file <file> --reason <reason>",
            }],
        ))
    task_ids = [str(task.get("id")) for task in _tasks(data)]
    ready = [item for item in lock.get("readyTaskIds", []) if isinstance(item, str)]
    if ready != task_ids:
        return render_result(fail(
            "finalized_draft_ready_projection_invalid",
            f"expected={','.join(task_ids)};actual={','.join(ready)}",
            path=_draft_plan_path(workspace, feature),
        ))

    formal_root, formal_batches, formal_load_errors = _load_raw_formal_bundle(workspace, feature)
    checkpoint, blockers = _formal_execution_blockers(
        workspace,
        feature,
        formal_root,
        formal_batches,
        formal_load_errors,
    )
    if blockers:
        return render_result(WriterResult(
            ok=False,
            path=_path(workspace, feature),
            errors=[{
                "reason": "finalized_plan_reopen_forbidden",
                "detail": ";".join(blockers),
                "repairTarget": "plan_revision",
            }],
            data={
                "checkpoint": checkpoint,
                "executionBlockers": blockers,
            },
        ))

    reason = str(args.reason).strip()
    if not reason:
        return render_result(fail("finalized_plan_reopen_reason_required"))
    previous_finalized_at = lock.pop("finalizedAt", None)
    lock.update({
        "status": "ready",
        "reopenedForRepair": True,
        "reopenedAt": _utc_now(),
        "reopenedReason": reason,
        "reopenedFromFormalDigest": formal_root.get("taskSetDigest")
        if isinstance(formal_root, dict)
        else None,
        "previousFinalizedAt": previous_finalized_at,
    })
    if design_changed:
        lock["designContract"] = design_contract_snapshot(design_contract)
        lock["designRevisionConfirmedAt"] = _utc_now()
        lock["designRevisionConfirmationReason"] = reason
    changed = atomic_write_json(_draft_lock_path(workspace, feature), lock)
    return render_result(WriterResult(
        ok=True,
        path=_draft_plan_path(workspace, feature),
        changed=changed,
        data={
            "checkpoint": checkpoint,
            "formalPlanWasPresent": formal_root is not None,
            "draft": _draft_summary(lock, data),
            "nextCommands": [
                "repair-draft-task|repair-draft-tasks",
                "preflight-task-draft",
                "finalize-task-draft --force",
            ],
        },
    ))


def _rebuild_task_draft_from_group_data(
    workspace: Path,
    feature: str,
    old_lock: dict[str, Any],
    old_data: dict[str, Any],
    group_file: Path,
    group_data: dict[str, Any],
    code_workspaces: list[str],
    *,
    last_change: dict[str, Any] | None = None,
) -> WriterResult:
    """Reproject a Draft after a validated, existing group source changed.

    The caller owns source-file validation and persistence.  Keeping this
    projection separate is what lets a repair patch preserve every task whose
    Core projection and workspace binding are unchanged.
    """
    if not code_workspaces:
        return fail(
            "code_workspace_required_for_rebuild",
            "pass --code-workspace to repair this draft",
            path=_draft_plan_path(workspace, feature),
        )
    old_code_workspaces = [
        item for item in old_lock.get("codeWorkspaces", []) if isinstance(item, str) and item
    ]
    legacy_workspace_contract_missing = not old_code_workspaces
    old_workspace_contexts = (
        _code_workspace_contexts(old_code_workspaces)
        if old_code_workspaces
        else []
    )
    workspace_contexts = _code_workspace_contexts(code_workspaces)
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
        workspace_roots = _draft_task_workspace_roots(group, workspace_contexts)
        workspace_contract_unchanged = False
        if not legacy_workspace_contract_missing:
            try:
                workspace_contract_unchanged = (
                    _draft_task_workspace_contract(group, old_workspace_contexts)
                    == _draft_task_workspace_contract(group, workspace_contexts)
                )
            except PlanWriterInputError:
                workspace_contract_unchanged = False
        if (
            workspace_contract_unchanged
            and old_task is not None
            and _task_matches_group_projection(group, old_task)
            and task_workspace_roots(old_task) == workspace_roots
        ):
            tasks.append(copy.deepcopy(old_task))
            if task_id in old_ready:
                preserved.append(task_id)
        else:
            tasks.append(_draft_task_skeleton(group, workspace_roots))
            reset.append(task_id)
    data = _initial(feature)
    implementation_scope, scope_errors = load_scope(_path(workspace, feature).parent)
    if scope_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=[{"reason": error} for error in scope_errors],
        ))
    data["implementationScope"] = implementation_scope
    data["codeWorkspaces"] = _code_workspace_bindings(
        workspace_contexts,
        {str(group.get("workspaceRef")) for group in _task_groups(group_data)},
    )
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
        "designContract": copy.deepcopy(old_lock.get("designContract")),
        "createdAt": old_lock.get("createdAt") or _utc_now(),
        "rebuiltAt": _utc_now(),
    }
    if last_change is not None:
        lock["lastChange"] = copy.deepcopy(last_change)
    result = _write_draft_bundle(workspace, feature, data, lock)
    return with_result_data(
        result,
        preservedTaskIds=preserved,
        resetTaskIds=reset,
        rebuildGuidance={
            "action": "refill_reset_task_details_only",
            "preservedTaskCount": len(preserved),
            "resetTaskCount": len(reset),
            "nextStep": (
                "只对 resetTaskIds 调用 set-draft-task-detail；"
                "preservedTaskIds 的既有详情已保留，不要全量重填"
            ),
        },
        draft=_draft_summary(lock, data),
    )


def _cmd_rebuild_task_draft(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    if _path(workspace, feature).is_file():
        return render_result(fail("formal_plan_already_exists", path=_path(workspace, feature)))
    old_lock, old_data = _load_draft_bundle(workspace, feature)
    design_lock_errors = _draft_design_contract_errors(_path(workspace, feature).parent, old_lock)
    if design_lock_errors:
        confirmed_only = all(
            error.get("reason") == "confirmed_design_changed_after_draft_created"
            for error in design_lock_errors
        )
        if getattr(args, "design_revision_confirmed", False) and confirmed_only:
            design_contract, design_errors = _current_design_contract(_path(workspace, feature).parent)
            if design_errors:
                return render_result(WriterResult(
                    ok=False,
                    path=_draft_plan_path(workspace, feature),
                    errors=design_errors,
                ))
            old_lock = copy.deepcopy(old_lock)
            old_lock["designContract"] = design_contract_snapshot(design_contract)
            old_lock["designRevisionConfirmedAt"] = _utc_now()
        else:
            return render_result(WriterResult(
                ok=False,
                path=_draft_plan_path(workspace, feature),
                errors=design_lock_errors,
            ))
    group_file = Path(args.group_file).expanduser().resolve()
    if not group_file.is_file():
        return render_result(fail(
            "task_group_source_missing_incremental_repair_forbidden",
            "保留原 task-groups.json；用 create-repair-work 和 apply-draft-patch 提交定点分组修复，"
            "不得删除后全量重新生成",
            path=group_file,
        ))
    group_data = _load_task_group_file(group_file, feature)
    errors = _task_group_preflight_errors(_path(workspace, feature).parent, group_data)
    if errors:
        return render_result(WriterResult(ok=False, path=group_file, errors=errors))
    code_workspaces = (
        [str(Path(value).expanduser().resolve()) for value in args.code_workspace]
        if args.code_workspace
        else [item for item in old_lock.get("codeWorkspaces", []) if isinstance(item, str)]
    )
    return render_result(_rebuild_task_draft_from_group_data(
        workspace,
        feature,
        old_lock,
        old_data,
        group_file,
        group_data,
        code_workspaces,
    ))


def _cmd_rebuild_finalized_draft(args: argparse.Namespace) -> int:
    """Safely reproject an unexecuted finalized Draft after Core/Design drift.

    This is intentionally separate from ``rebuild-task-draft``: a finalized
    plan still has formal artifacts, and only this command verifies that none
    of them has begun execution before permitting a replacement Draft.
    """

    workspace, feature = _resolve(args)
    old_lock, old_data = _load_draft_bundle(workspace, feature)
    if old_lock.get("status") != "finalized":
        return render_result(fail(
            "task_draft_not_finalized",
            f"status={old_lock.get('status')}",
            path=_draft_plan_path(workspace, feature),
        ))
    reason = str(args.reason).strip()
    if not reason:
        return render_result(fail("finalized_plan_reopen_reason_required"))

    feature_dir = _path(workspace, feature).parent
    design_contract, design_errors = _current_design_contract(feature_dir)
    if design_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_errors,
        ))
    design_snapshot = old_lock.get("designContract")
    design_changed = (
        isinstance(design_snapshot, dict)
        and isinstance(design_snapshot.get("sha256"), str)
        and design_snapshot.get("sha256") != design_contract.get("sha256")
    )
    if design_changed and args.design_revision_confirmed is not True:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=[{
                "reason": "confirmed_design_changed_after_draft_created",
                "detail": "pass --design-revision-confirmed only after the Design revision was separately confirmed",
                "repairTarget": "design_revision",
                "repairable": False,
                "designMutationAllowed": False,
            }],
        ))

    group_file = Path(args.group_file).expanduser().resolve()
    if not group_file.is_file():
        return render_result(fail(
            "task_group_source_missing_incremental_repair_forbidden",
            "必须保留现有 task-groups.json，不能删除后再全量生成。",
            path=group_file,
        ))
    group_data = _load_task_group_file(group_file, feature)
    errors = _task_group_preflight_errors(feature_dir, group_data)
    if errors:
        return render_result(WriterResult(ok=False, path=group_file, errors=errors))

    formal_root, formal_batches, formal_load_errors = _load_raw_formal_bundle(workspace, feature)
    checkpoint, blockers = _formal_execution_blockers(
        workspace,
        feature,
        formal_root,
        formal_batches,
        formal_load_errors,
    )
    if blockers:
        return render_result(WriterResult(
            ok=False,
            path=_path(workspace, feature),
            errors=[{
                "reason": "finalized_plan_rebuild_forbidden",
                "detail": ";".join(blockers),
                "repairTarget": "plan_revision",
            }],
            data={"checkpoint": checkpoint, "executionBlockers": blockers},
        ))

    code_workspaces = (
        [str(Path(value).expanduser().resolve()) for value in args.code_workspace]
        if args.code_workspace
        else [item for item in old_lock.get("codeWorkspaces", []) if isinstance(item, str)]
    )
    rebuild_lock = copy.deepcopy(old_lock)
    rebuild_lock["designContract"] = design_contract_snapshot(design_contract)
    result = _rebuild_task_draft_from_group_data(
        workspace,
        feature,
        rebuild_lock,
        old_data,
        group_file,
        group_data,
        code_workspaces,
        last_change={
            "kind": "full_rebuild",
            "reason": reason,
            "designChanged": design_changed,
            "groupChanged": _task_group_digest(group_data) != old_lock.get("groupingDigest"),
            "sourceFile": str(group_file),
        },
    )
    if not result.ok:
        return render_result(result)

    new_lock, new_data = _load_draft_bundle(workspace, feature)
    new_lock.update({
        "reopenedForRepair": True,
        "reopenedAt": _utc_now(),
        "reopenedReason": reason,
        "reopenedFromFormalDigest": formal_root.get("taskSetDigest")
        if isinstance(formal_root, dict)
        else None,
        "previousFinalizedAt": old_lock.get("finalizedAt"),
        "fullRebuiltAt": _utc_now(),
        "designContract": design_contract_snapshot(design_contract),
    })
    if design_changed:
        new_lock["designRevisionConfirmedAt"] = _utc_now()
        new_lock["designRevisionConfirmationReason"] = reason
    lock_changed = atomic_write_json(_draft_lock_path(workspace, feature), new_lock)
    return render_result(with_result_data(
        result,
        changed=result.changed or lock_changed,
        checkpoint=checkpoint,
        formalPlanWasPresent=formal_root is not None,
        draft=_draft_summary(new_lock, new_data),
        nextCommands=[
            "set-draft-task-details --body-stdin (only resetTaskIds)",
            "preflight-task-draft",
            "finalize-task-draft --force",
        ],
    ))


def _cmd_finalize_task_draft(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    formal_plan_exists = _path(workspace, feature).is_file()
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
    if args.force and formal_plan_exists:
        if lock.get("reopenedForRepair") is not True:
            return render_result(fail(
                "formal_plan_force_requires_reopen",
                "run diagnose-plan-repair then reopen-finalized-draft",
                path=_path(workspace, feature),
            ))
        formal_root, formal_batches, formal_load_errors = _load_raw_formal_bundle(workspace, feature)
        checkpoint, blockers = _formal_execution_blockers(
            workspace,
            feature,
            formal_root,
            formal_batches,
            formal_load_errors,
        )
        if blockers:
            return render_result(WriterResult(
                ok=False,
                path=_path(workspace, feature),
                errors=[{
                    "reason": "finalized_plan_replace_forbidden",
                    "detail": ";".join(blockers),
                    "repairTarget": "plan_revision",
                }],
                data={"checkpoint": checkpoint, "executionBlockers": blockers},
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
                    unlink_if_exists(old_plan)
                    try:
                        old_plan.parent.rmdir()
                    except OSError:
                        pass
        lock["status"] = "finalized"
        lock["finalizedAt"] = _utc_now()
        if lock.pop("reopenedForRepair", None) is not None:
            lock["amendedAt"] = lock["finalizedAt"]
        lock.pop("reopenedAt", None)
        lock.pop("reopenedReason", None)
        lock.pop("reopenedFromFormalDigest", None)
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
        data={
            "grouping": _task_group_summary(group_data),
            "validation": _task_group_validation_report(group_data, errors),
        },
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
                unlink_if_exists(old_plan)
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
                    "taskDetailTemplate": TASK_DETAIL_TEMPLATE_RELATIVE_PATH,
                    "taskDetailInputExample": _task_detail_input_example(),
                    "taskGroupTemplate": TASK_GROUP_TEMPLATE_RELATIVE_PATH,
                    "taskGroupInputExample": _task_group_example(),
                    "taskGroupMatrixExceptionExample": _task_group_matrix_exception_example(),
                    "taskGroupUiRequiredExample": _task_group_ui_required_example(),
                    "taskGroupExternalDependencyExample": (
                        _task_group_external_dependency_example()
                    ),
                    "recommendedInputMode": "draft-batch",
                    "supportedInputModes": ["draft-batch", "body-file", "body-stdin", "body-json"],
                    "deprecatedInputModes": ["task-directory", "task-json", "cli-fields"],
                    "legacyTaskDirectoryMigration": (
                        "import-task-directory --group-file <file> --task-dir <directory> "
                        "--code-workspace <path>"
                    ),
                    "draftRepair": {
                        "preflight": "preflight-task-draft",
                        "singleTask": "repair-draft-task --task-id <id> --body-stdin",
                        "multipleTasks": "repair-draft-tasks --body-stdin",
                        "batchBody": {
                            "repairs": [
                                {"taskId": "T001", "patch": {"designRefs": ["design.md#D-001"]}},
                            ],
                        },
                        "groupOwnedFieldsRequire": "create-repair-work --group-file <file> then apply-draft-patch; never delete/recreate task-groups.json",
                    },
                    "modelFacingContracts": {
                        "planCore": {
                            "schemaVersion": PLAN_CORE_SCHEMA,
                            "template": TASK_GROUP_TEMPLATE_RELATIVE_PATH,
                            "input": "write compact tasks[] only; writer projects the runtime group contract",
                            "requiredFields": [
                                "id", "outcome", "dependsOn", "workspace", "writeSet", "refs", "validation",
                            ],
                            "taskIdPolicy": {
                                "format": "TNNN",
                                "sequence": "ordered tasks start at T001 and increment by one without gaps",
                            },
                            "forbiddenNonCoreFields": sorted(COMPACT_PLAN_CORE_FORBIDDEN_FIELDS),
                            "mergedFields": {
                                "outcome": ["title"],
                                "dependsOn": ["deps"],
                                "writeSet": ["touches", "scope.paths", "expectedFiles", "writeTargets.symbols"],
                                "refs": ["specRefs", "apiIds"],
                                "validation.seam": ["validationBoundary"],
                                "validation.mergeJustification": [
                                    "mergedScenarioRefs", "splitRationale",
                                ],
                            },
                            "conditionalFields": {
                                "ui": {
                                    "when": "task_has_a_user_facing_ui_surface",
                                    "requiredFields": ["pages", "interactions", "visualSources", "route"],
                                },
                                "validation.mergeJustification": {
                                    "when": "any_granularity_soft_limit_is_exceeded",
                                    "mustMention": "path_qualified scenario refs and relevant API/PAGE/UIX IDs",
                                    "writerDerives": ["mergedScenarioRefs", "splitRationale"],
                                    "omitWhen": "all_granularity_dimensions_are_at_or_below_soft_limits",
                                },
                            },
                        },
                        "taskDetail": {
                            "schemaVersion": PLAN_DETAIL_SCHEMA,
                            "workCommand": "show-draft-task-work --task-id <id>",
                            "input": "set-draft-task-detail --task-id <id> --body-stdin",
                            "outputIsSingleTaskOnly": True,
                        },
                        "repair": {
                            "workCommand": "create-repair-work [--feedback-file <json>]",
                            "planCoreWorkCommand": "create-repair-work --group-file <task-groups.json>",
                            "patchSchemaVersion": PLAN_REPAIR_PATCH_SCHEMA,
                            "applyCommand": "apply-draft-patch --patch-file <json>",
                            "draftRequires": ["workId", "baseRevision", "resolves", "ops[].expectedHash"],
                            "planCoreRequires": ["workId", "baseGroupingDigest", "resolves", "ops[].expectedHash"],
                            "groupPatchPaths": ["writeSet", "dependsOn", "validation.mergeJustification"],
                            "forbidden": ["full_plan_replacement", "PLAN.md_patch", "batch_patch", "unlisted_path_patch", "delete_task_groups_source"],
                        },
                    },
                    "finalizedRepair": {
                        "diagnose": "diagnose-plan-repair",
                        "reopen": "reopen-finalized-draft --reason <reason>",
                        "designRevisionReopen": "reopen-finalized-draft --design-revision-confirmed --reason <reason>",
                        "repair": "repair-draft-task|repair-draft-tasks",
                        "preflight": "preflight-task-draft",
                        "rematerialize": "finalize-task-draft --force",
                        "guard": "only before code/validation execution and evidence creation",
                    },
                    "runtimeRequiredTaskFields": [
                        "title",
                        "goal",
                        "specRefs",
                        "implementationPoints",
                        "acceptanceCriteria",
                        "workspaceRef",
                        "validationBoundary",
                        "nonGoals",
                        "validationCommands",
                    ],
                    "runtimeProjectedTaskGroupFields": [
                        "id",
                        "title",
                        "deps",
                        "uiRequired",
                        "specRefs",
                        "mergedScenarioRefs",
                        "apiIds",
                        "validationBoundary",
                        "workspaceRef",
                    ],
                    "planCoreExamples": [
                        "externalDependencyTask",
                        "matrixExceptionTask",
                        "uiTask",
                    ],
                    "runtimeGroupOwnedTaskFields": sorted(DRAFT_GROUP_OWNED_FIELDS),
                    "requiredTaskDetailFields": sorted(DRAFT_REQUIRED_DETAIL_FIELDS),
                    "emptyAllowedTaskDetailFields": [
                        "designRefs",
                        "dataIds",
                        "decisionIds",
                    ],
                    "writerOwnedDetailFields": {
                        "acceptanceCriteria": ["id"],
                        "validationCommands": ["id"],
                        "validationTestPlan": [
                            "commandId",
                            "assetType",
                            "executionStage",
                            "covers",
                            "testIntent",
                        ],
                        "scope": ["pages", "workspaceRoots"],
                    },
                    "fieldRules": {
                        "designTraceability": {
                            "sourceOfTruth": "dev.design .design-contract.lock.json snapshot",
                            "direction": "design_to_plan_only",
                            "unknownIdRepairTarget": "plan_task_or_task_group",
                            "designMutationFromPlanErrorAllowed": False,
                            "apiIdsMayBeEmpty": True,
                            "dataIdsMayBeEmpty": True,
                            "decisionIdsMayBeEmpty": True,
                            "globalCoverageRequiredForDefinedDesignIds": True,
                        },
                        "executionMode": {
                            "source": "task_group",
                            "allowed": sorted(TASK_EXECUTION_MODES),
                            "default": "code",
                            "rules": {
                                "code": "implementation changes are allowed; Code receives test intent and must not create tests",
                                "verified_existing": "no implementation changes; test intent is deferred to UTest/E2E stages",
                                "external_dependency": "no local implementation or validation command; record structured dependency and defer with blocked Evidence",
                            },
                        },
                        "externalDependency": {
                            "requiredWhen": "executionMode=external_dependency",
                            "forbiddenOtherwise": True,
                            "requiredFields": ["system", "owner", "trackingRefs"],
                        },
                        "workspaceRef": {
                            "required": True,
                            "type": "repository_id",
                            "source": "task_group",
                        },
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
                    "validationKindsByLane": {
                        "backend": sorted(BEHAVIOR_TASK_VALIDATION_KINDS),
                        "frontend": sorted(TASK_VALIDATION_KINDS),
                    },
                    "qualityGateCommandKinds": ["static_check"],
                    "validationCoverage": {
                        "rule": "required_commands_cover_all_acceptance_criteria",
                    },
                    "taskGranularity": {
                        "softLimits": {
                            "scenarios": PLAN_TASK_MAX_SCENARIOS,
                            "apis": PLAN_TASK_MAX_APIS,
                            "pages": PLAN_TASK_MAX_UI_PAGES,
                            "interactions": PLAN_TASK_MAX_UI_INTERACTIONS,
                        },
                        "hardLimits": {
                            "scenarios": PLAN_TASK_MATRIX_MAX_SCENARIOS,
                            "apis": PLAN_TASK_HARD_MAX_APIS,
                            "pages": PLAN_TASK_HARD_MAX_UI_PAGES,
                            "interactions": PLAN_TASK_HARD_MAX_UI_INTERACTIONS,
                        },
                        "softLimitAction": "validation.mergeJustification is required; otherwise split the task by observable seam",
                        "hardLimitAction": "must_split_before_draft",
                        "mergeJustification": {
                            "minimumLength": PLAN_TASK_SPLIT_RATIONALE_MIN_LENGTH,
                            "mustExplain": "shared public seam and validation loop; implementation convenience is invalid",
                            "minimumMentionedIds": dict(PLAN_TASK_SPLIT_RATIONALE_MIN_IDS_BY_PREFIX),
                            "scenarioRefs": "use one path-qualified specs/<capability>/spec.md#SCN-NNN reference per scenario",
                        },
                        "matrixValidation": {
                            "when": f"scenarios>{PLAN_TASK_MAX_SCENARIOS}",
                            "requiredCommandCount": 1,
                            "backendAllowedKinds": sorted(
                                set(BEHAVIOR_TASK_VALIDATION_KINDS) - {"static_check"}
                            ),
                            "frontendAllowedKinds": sorted(
                                (set(BEHAVIOR_TASK_VALIDATION_KINDS) - {"static_check"})
                                | set(FRONTEND_COMPILE_VALIDATION_KINDS)
                            ),
                            "covers": (
                                "omit to auto-cover all generated acceptance criteria; "
                                "when specified, it must equal all generated acceptance criteria"
                            ),
                        },
                    },
                    "taskDetailConstraints": {
                        "implementation": {"minItems": 2, "maxItems": 6},
                        "acceptance": {
                            "minItems": 1,
                            "scenarioRefsMustBeSubsetOf": "Plan Core refs.scenarios",
                        },
                        "nonGoals": {"minItems": 1},
                        "checks": {
                            "minItemsWhenLocalMode": 1,
                            "requiredChecksCoverAllAcceptance": True,
                            "maven": {
                                "leafModule": "set cwd to the leaf module and omit -pl",
                                "aggregator": "use -pl only when cwd is a reactor aggregator containing modules",
                                "duplicateSelector": "-pl may not repeat a non-root cwd",
                            },
                        },
                    },
                    "validationCommandPolicy": {
                        "forbiddenExecutables": ["echo", "false", "printf", "true"],
                        "inlineShell": "forbidden",
                        "placeholderText": "forbidden",
                        "packageScriptMustExist": True,
                        "packageScriptMayNotBeNoop": True,
                        "mavenTargetMustBeConcreteClass": True,
                        "mavenSkipOrZeroMatchOptions": "forbidden",
                    },
                    "validationTestPlanPolicy": {
                        "source": "task_contract",
                        "representation": "test_intent_only",
                        "generatedFields": [
                            "commandId",
                            "assetType",
                            "executionStage",
                            "covers",
                            "testIntent",
                        ],
                        "targetModes": [],
                        "createInCodeAllowed": False,
                        "productionCodeStageCreatesTests": False,
                        "testAssetCreationStages": ["utest", "e2e"],
                    },
                    "taskValidationPolicy": {
                        **copy.deepcopy(DEFAULT_TASK_VALIDATION_POLICY),
                        "taskCommandTiming": "test_stages_only",
                        "codeCommandTiming": "after_all_batch_tasks_implemented",
                        "validationTarget": "batch_final_snapshot",
                        "codeStageTestExecution": "forbidden",
                    },
                    "validationEnvironmentPolicy": {
                        "preflightBeforeRun": True,
                        "missingExecutableResult": "record_stage_environment_failure",
                        "runtimeEnvironmentResult": "record_stage_environment_failure",
                        "requiredActions": ["repair_stage_environment_and_retry"],
                        "planOrDigestRebuildRequired": False,
                    },
                    "workspaceContract": {
                        "field": "scope.workspaceRoots",
                        "source": "prepare-task-draft --code-workspace",
                        "taskBindingField": "workspaceRef",
                        "multiRepositoryRequiresTaskBinding": True,
                        "maxWorkspaceRefsPerTask": 1,
                        "crossRepositoryTaskSupported": False,
                        "codeWorkspaceArgumentRepeatable": True,
                        "repositoryIdSource": "git_root_directory_name",
                        "singleRepositoryExample": {"default": "path/from/git-root/to/code-workspace"},
                        "multiRepositoryExample": {"repo-id": "path/from/git-root/to/code-workspace"},
                        "scopePathsBase": "declared_code_workspace",
                        "scopePathsMode": "advisory_change_hint",
                        "validationCwdBase": "git_root",
                        "codeWorkspacePreflightRequired": True,
                        "forbidRepeatedWorkspacePrefixInScopePaths": True,
                    },
                    "batchAssignment": {
                        "strategy": BATCH_STRATEGY,
                        "maxTasks": MAX_BATCH_TASKS,
                        "manualBatchIdSupported": False,
                        "executionOrder": "root_batch_order_then_task_order",
                        "batchConcurrency": 1,
                        "taskConcurrency": 1,
                        "requiresNewConversationBetweenBatches": True,
                        "defaultDeliveryKind": "single_task",
                        "atomicGroup": {
                            "required": "explicit_on_every_member",
                            "maxTasks": 3,
                            "requiresSame": ["workspaceRef", "executionLane", "executionStage"],
                            "forbiddenImplicitSignals": ["deps", "writeSet", "spec", "route"],
                        },
                    },
                    "taskSetFinalization": {
                        "coreWriteCommand": "write-task-groups --body-stdin",
                        "groupingPreflightCommand": "preflight-task-groups --group-file <file>",
                        "prepareCommand": (
                            "prepare-task-draft --group-file <file> --code-workspace <path>"
                        ),
                        "detailCommand": "set-draft-task-detail --task-id <id> --body-stdin",
                        "detailLintCommand": "lint-draft-task-detail --task-id <id> --body-stdin",
                        "detailBatchCommand": "set-draft-task-details --body-stdin",
                        "fullDetailLintCommand": "lint-draft-task-details --full --body-stdin",
                        "fullDetailBatchCommand": "set-draft-task-details --full --body-stdin",
                        "preflightCommand": "preflight-task-draft",
                        "command": "finalize-task-draft",
                        "coverage": "all_path_qualified_spec_scenarios",
                        "requiredBefore": [],
                    },
                    "collectingRepairs": {
                        "replace": "set-draft-task-detail --task-id <id> --body-stdin",
                        "batchReplace": "set-draft-task-details --body-stdin",
                        "lint": "lint-draft-task-detail --task-id <id> --body-stdin",
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
                        "designLock": "designContract.sha256",
                        "persistentDesignLock": DESIGN_CONTRACT_LOCK_FILE,
                        "persistentDesignLockOwner": "dev.design",
                        "designRevisionConfirmation": (
                            "autodev-design refreshes .design-contract.lock.json; "
                            "reopen-finalized-draft --design-revision-confirmed --reason <reason> only rebinds Draft"
                        ),
                        "dualDriftRecovery": (
                            "diagnose-plan-repair returns full_rebuild_required for Design plus Core drift; "
                            "run rebuild-finalized-draft --group-file <file> --design-revision-confirmed --reason <reason> "
                            "only before execution starts"
                        ),
                        "designChangeError": "confirmed_design_changed_after_draft_created",
                        "groupChangeError": "task_group_changed_after_draft_created",
                        "detailWriteMode": "validate_then_atomic_replace",
                        "standaloneTaskFiles": False,
                        "acceptanceAndValidationIds": "writer_generated",
                        "scopePagesSource": "uiRefs.pageRefs",
                        "scopeWorkspaceRootsSource": "prepare-task-draft --code-workspace",
                        "defaultValidationCwdSource": "scope.workspaceRoots",
                    },
                    "uiRule": "scope.pages_must_equal_uiRefs.pageRefs_when_uiRequired",
                    "matrixException": {
                        "normalScenarioMaximum": PLAN_TASK_MAX_SCENARIOS,
                        "scenarioMaximum": PLAN_TASK_MATRIX_MAX_SCENARIOS,
                        "requiredValidationByLane": {
                            "backend": "one_complete_required_behavior_command",
                            "frontend": "one_complete_required_behavior_or_matching_compile_command",
                        },
                    },
                    "planCoreMatrixExceptionExample": _task_group_matrix_exception_example(),
                    "projectValidationCommand": {
                        "command": (
                            "add-project-validation-command [--repo <workspaceRef>] "
                            "--command <final-e2e-command>"
                        ),
                        "requiredFields": ["id", "argv", "cwd", "kind", "required"],
                        "allowedKinds": sorted(PROJECT_VALIDATION_KINDS),
                        "mustNotDuplicateBatchProfile": True,
                        "requiredForParallelPipeline": False,
                        "requiredPerWorkspaceRef": "optional_final_e2e_command",
                        "executionTarget": "merged_main_e2e",
                        "repoRequiredWhenMultipleWorkspaces": True,
                    },
                    "qualityGateCommand": {
                        "command": (
                            "add-quality-gate-command --lane <backend|frontend> "
                            "[--repo <workspaceRef>] [--replace] "
                            "--command <static-check-command>"
                        ),
                        "requiredFields": ["argv", "cwd", "kind", "required"],
                        "allowedKinds": ["static_check"],
                        "optional": True,
                        "executionStage": "quality_gate_only_when_commands_present",
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
    workspace_roots = task_workspace_roots(task)
    repository = args.repo
    cwd = args.cwd
    if len(workspace_roots) == 1 and "default" not in workspace_roots:
        task_repository, task_workspace_root = next(iter(workspace_roots.items()))
        repository = repository or task_repository
        if cwd == ".":
            cwd = task_workspace_root
    commands.append(
        {
            "id": args.command_id or f"VAL-{args.task_id}-{len(commands) + 1:02d}",
            "argv": shlex.split(args.command),
            "cwd": cwd,
            "kind": args.kind,
            "required": not args.optional,
            "covers": args.covers if args.covers is not None else covers,
            **({"repo": repository} if repository else {}),
        }
    )
    return render_result(_write(workspace, feature, data))


def _cmd_add_compile_command(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    return render_result(fail(
        "batch_compile_retired",
        "使用 quality gate 或后续 Review/UTest 阶段；当前 Plan 不再接受 compile command",
        path=_draft_plan_path(workspace, feature),
    ))
    # Check Draft status: allow if Draft is ready or reopened, reject if finalized or no Draft
    try:
        lock, data = _load_draft_bundle(workspace, feature)
    except PlanWriterInputError as e:
        return render_result(fail(str(e), path=_draft_plan_path(workspace, feature)))

    # A reopened Draft is marked ready. A finalized lock must never be edited.
    if lock.get("status") == "finalized":
        return render_result(fail(
            "task_draft_finalized",
            "工程命令必须在 Draft 阶段配置。如需修改已 finalized 的计划，请先运行 reopen-finalized-draft",
            path=_draft_plan_path(workspace, feature),
        ))
    lane_workspace_contracts = {
        _batch_workspace_contract(task)
        for task in _tasks(data)
        if task_execution_lane(task) == args.lane
    }
    if any(len(contract) != 1 for contract in lane_workspace_contracts):
        return render_result(fail(
            "compile_command_task_workspace_invalid",
            args.lane,
            path=_path(workspace, feature),
        ))
    if not lane_workspace_contracts:
        return render_result(fail(
            "compile_command_lane_unused",
            args.lane,
            path=_path(workspace, feature),
        ))
    contracts_by_repository = {
        contract[0][0]: contract for contract in lane_workspace_contracts
    }
    if len(contracts_by_repository) != len(lane_workspace_contracts):
        return render_result(fail(
            "compile_command_repository_workspace_ambiguous",
            args.lane,
            path=_path(workspace, feature),
        ))
    if len(lane_workspace_contracts) > 1 and not args.repo:
        return render_result(fail(
            "compile_command_repository_required",
            args.lane,
            path=_path(workspace, feature),
        ))
    selected_repository = args.repo
    if selected_repository is None:
        selected_repository = next(iter(contracts_by_repository))
    selected_contract = contracts_by_repository.get(selected_repository)
    if selected_contract is None:
        return render_result(fail(
            "compile_command_repository_unknown",
            f"lane={args.lane};repo={selected_repository};available={','.join(sorted(contracts_by_repository))}",
            path=_path(workspace, feature),
        ))
    workspace_roots = dict(selected_contract)
    root_key = selected_contract[0][0]
    command_repository = None if root_key == "default" else root_key
    workspace_preflight_required = any(
        task_execution_lane(task) == args.lane
        and _batch_workspace_contract(task) == selected_contract
        and bool(task_workspace_roots(task))
        for task in _tasks(data)
    )
    command = {
        "argv": shlex.split(args.command),
        "cwd": args.cwd or workspace_roots[root_key],
        "kind": "compile",
        "required": True,
        **({"repo": command_repository} if command_repository else {}),
    }
    if workspace_preflight_required:
        # Reuse the workspaces locked by prepare-task-draft so callers do not
        # need to repeat --code-workspace for every engineering command.
        configured_workspaces = (
            args.code_workspace
            or [item for item in lock.get("codeWorkspaces", []) if isinstance(item, str)]
        )
        if not configured_workspaces:
            return render_result(fail(
                "code_workspace_preflight_required",
                "add-compile-command",
                path=_draft_plan_path(workspace, feature),
            ))
        contexts = _code_workspace_contexts(configured_workspaces)
        command_errors = _command_workspace_preflight_errors(
            command,
            context_name=f"compileProfiles.{args.lane}",
            workspace_roots=workspace_roots,
            contexts=contexts,
            compile_only=True,
        )
        if command_errors:
            return render_result(WriterResult(
                ok=False,
                path=_draft_plan_path(workspace, feature),
                errors=command_errors,
            ))
    profiles = data.setdefault("compileProfiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        data["compileProfiles"] = profiles
    profile = profiles.setdefault(args.lane, {"commands": []})
    commands = profile.setdefault("commands", [])
    if not isinstance(commands, list):
        commands = []
        profile["commands"] = commands

    # Exactly one compile command is allowed for each lane/workspace pair.
    # Re-applying the command therefore replaces every prior command for that pair.
    command_repo = command.get("repo")
    commands[:] = [
        existing_cmd
        for existing_cmd in commands
        if not isinstance(existing_cmd, dict) or existing_cmd.get("repo") != command_repo
    ]
    commands.append(command)

    return render_result(_write_draft_bundle(workspace, feature, data, lock))


def _cmd_add_quality_gate_command(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    # Check Draft status: allow if Draft is ready or reopened, reject if finalized or no Draft
    try:
        lock, data = _load_draft_bundle(workspace, feature)
    except PlanWriterInputError as e:
        return render_result(fail(str(e), path=_draft_plan_path(workspace, feature)))

    # A reopened Draft is marked ready. A finalized lock must never be edited.
    if lock.get("status") == "finalized":
        return render_result(fail(
            "task_draft_finalized",
            "工程命令必须在 Draft 阶段配置。如需修改已 finalized 的计划，请先运行 reopen-finalized-draft",
            path=_draft_plan_path(workspace, feature),
        ))
    lane_workspace_contracts = {
        _batch_workspace_contract(task)
        for task in _tasks(data)
        if task_execution_lane(task) == args.lane
    }
    if any(len(contract) != 1 for contract in lane_workspace_contracts):
        return render_result(fail(
            "quality_gate_command_task_workspace_invalid",
            args.lane,
            path=_path(workspace, feature),
        ))
    if not lane_workspace_contracts:
        return render_result(fail(
            "quality_gate_command_lane_unused",
            args.lane,
            path=_path(workspace, feature),
        ))
    contracts_by_repository = {
        contract[0][0]: contract for contract in lane_workspace_contracts
    }
    if len(contracts_by_repository) != len(lane_workspace_contracts):
        return render_result(fail(
            "quality_gate_command_repository_workspace_ambiguous",
            args.lane,
            path=_path(workspace, feature),
        ))
    if len(lane_workspace_contracts) > 1 and not args.repo:
        return render_result(fail(
            "quality_gate_command_repository_required",
            args.lane,
            path=_path(workspace, feature),
        ))
    selected_repository = args.repo or next(iter(contracts_by_repository))
    selected_contract = contracts_by_repository.get(selected_repository)
    if selected_contract is None:
        return render_result(fail(
            "quality_gate_command_repository_unknown",
            f"lane={args.lane};repo={selected_repository};available={','.join(sorted(contracts_by_repository))}",
            path=_path(workspace, feature),
        ))
    workspace_roots = dict(selected_contract)
    root_key = selected_contract[0][0]
    command_repository = None if root_key == "default" else root_key
    command = {
        "argv": shlex.split(args.command),
        "cwd": args.cwd or workspace_roots[root_key],
        "kind": "static_check",
        "required": True,
        **({"repo": command_repository} if command_repository else {}),
    }
    workspace_preflight_required = any(
        task_execution_lane(task) == args.lane
        and _batch_workspace_contract(task) == selected_contract
        and bool(task_workspace_roots(task))
        for task in _tasks(data)
    )
    if workspace_preflight_required:
        configured_workspaces = (
            args.code_workspace
            or [item for item in lock.get("codeWorkspaces", []) if isinstance(item, str)]
        )
        if not configured_workspaces:
            return render_result(fail(
                "code_workspace_preflight_required",
                "add-quality-gate-command",
                path=_draft_plan_path(workspace, feature),
            ))
        command_errors = _command_workspace_preflight_errors(
            command,
            context_name=f"qualityGateProfiles.{args.lane}",
            workspace_roots=workspace_roots,
            contexts=_code_workspace_contexts(configured_workspaces),
            compile_only=False,
        )
        if command_errors:
            return render_result(WriterResult(
                ok=False,
                path=_draft_plan_path(workspace, feature),
                errors=command_errors,
            ))
    profiles = data.setdefault("qualityGateProfiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        data["qualityGateProfiles"] = profiles
    profile = profiles.setdefault(args.lane, {"commands": []})
    commands = profile.setdefault("commands", [])
    if not isinstance(commands, list):
        commands = []
        profile["commands"] = commands

    # Quality profiles may contain multiple checks for one workspace. Appending
    # keeps that capability; --replace intentionally replaces that workspace's
    # current quality profile when an operator needs to revise it.
    command_repo = command.get("repo")
    if args.replace:
        commands[:] = [
            existing_cmd
            for existing_cmd in commands
            if not isinstance(existing_cmd, dict) or existing_cmd.get("repo") != command_repo
        ]
    commands.append(command)

    return render_result(_write_draft_bundle(workspace, feature, data, lock))


def _cmd_add_project_validation_command(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    # Check Draft status: allow if Draft is ready or reopened, reject if finalized or no Draft
    try:
        lock, data = _load_draft_bundle(workspace, feature)
    except PlanWriterInputError as e:
        return render_result(fail(str(e), path=_draft_plan_path(workspace, feature)))

    # A reopened Draft is marked ready. A finalized lock must never be edited.
    if lock.get("status") == "finalized":
        return render_result(fail(
            "task_draft_finalized",
            "工程命令必须在 Draft 阶段配置。如需修改已 finalized 的计划，请先运行 reopen-finalized-draft",
            path=_draft_plan_path(workspace, feature),
        ))
    workspace_roots_by_ref: dict[str, str] = {}
    for task in _tasks(data):
        if task_execution_mode(task) != "code":
            continue
        workspace_ref = task.get("workspaceRef")
        if not isinstance(workspace_ref, str) or not workspace_ref:
            continue
        roots = task_workspace_roots(task)
        root_key = "default" if "default" in roots else workspace_ref
        workspace_root = roots.get(root_key)
        if isinstance(workspace_root, str) and workspace_root:
            workspace_roots_by_ref.setdefault(workspace_ref, workspace_root)
    if not workspace_roots_by_ref:
        return render_result(fail(
            "project_validation_workspace_unused",
            path=_draft_plan_path(workspace, feature),
        ))
    if len(workspace_roots_by_ref) > 1 and not args.repo:
        return render_result(fail(
            "project_validation_repository_required",
            path=_draft_plan_path(workspace, feature),
        ))
    selected_workspace_ref = args.repo or next(iter(workspace_roots_by_ref))
    workspace_root = workspace_roots_by_ref.get(selected_workspace_ref)
    if workspace_root is None:
        return render_result(fail(
            "project_validation_repository_unknown",
            f"repo={selected_workspace_ref};available={','.join(sorted(workspace_roots_by_ref))}",
            path=_draft_plan_path(workspace, feature),
        ))
    command_repo = None if selected_workspace_ref == "default" else selected_workspace_ref
    commands = data.setdefault("projectValidationCommands", [])
    if not isinstance(commands, list):
        commands = []
        data["projectValidationCommands"] = commands

    # Build new command
    command_kind = args.kind
    command_required = not args.optional
    new_command = {
        "id": args.command_id or f"PROJECT-VAL-{len(commands) + 1:03d}",
        "argv": shlex.split(args.command),
        "cwd": args.cwd or workspace_root,
        "kind": command_kind,
        "required": command_required,
        **({"repo": command_repo} if command_repo else {}),
    }

    command_errors = _command_workspace_preflight_errors(
        new_command,
        context_name="projectValidationCommands",
        workspace_roots={selected_workspace_ref: workspace_root},
        contexts=_code_workspace_contexts([
            item for item in lock.get("codeWorkspaces", []) if isinstance(item, str)
        ]),
        compile_only=False,
    )
    if command_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=command_errors,
        ))

    # A required system command belongs to final B-E2E for its workspace, so
    # re-applying it is a deterministic replacement. Optional commands remain
    # additive.
    if command_kind == "integration_test" and command_required:
        existing = next(
            (
                item for item in commands
                if isinstance(item, dict)
                and item.get("kind") == "integration_test"
                and item.get("required") is True
                and item.get("repo") == command_repo
            ),
            None,
        )
        if isinstance(existing, dict) and isinstance(existing.get("id"), str):
            new_command["id"] = existing["id"]
        commands[:] = [
            item
            for item in commands
            if not (
                isinstance(item, dict)
                and item.get("kind") == "integration_test"
                and item.get("required") is True
                and item.get("repo") == command_repo
            )
        ]
        commands.append(new_command)
    else:
        # For non-required or non-integration commands, always append
        commands.append(new_command)

    return render_result(_write_draft_bundle(workspace, feature, data, lock))


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
    parallel: bool = False,
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
            if not parallel:
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
    parallel: bool = False,
) -> WriterResult:
    """Bind implementation evidence without running or completing task validation."""

    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        if not defer_to_test_stages_enabled(data):
            return fail("defer_to_test_stages_not_enabled", task_id, path=_path(workspace, feature))
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

        if not isinstance(batch_plan, dict):
            return fail("batch_plan_missing", batch_id, path=_path(workspace, feature))
        batch_tasks = [item for item in batch_plan.get("tasks", []) if isinstance(item, dict)]
        all_implemented = bool(batch_tasks) and all(
            normalize_status(item.get("status")) in {"implemented", "done"}
            for item in batch_tasks
        )

        data["status"] = "in_progress"
        if not parallel:
            data["activeBatchId"] = batch_id

        result = _write(workspace, feature, data)
        if not result.ok:
            return result
        if all_implemented:
            return with_result_data(result, batchContinuation={
                "requiredAction": "await_review",
                "activeBatchId": batch_id,
                "taskIds": [str(item.get("id")) for item in batch_tasks],
                "status": "awaiting_review",
            })

        return result


def update_batch_compile_status(
    workspace: Path,
    feature: str,
    batch_id: str,
    compile_result: dict[str, Any],
) -> WriterResult:
    """Reject the retired batch-compile API."""
    del compile_result
    return fail("batch_compile_retired", batch_id, path=_path(workspace, feature))


def reset_batch_compile_for_revalidation(
    workspace: Path,
    feature: str,
    batch_id: str,
) -> WriterResult:
    """Reject the retired batch-compile API."""
    return fail("batch_compile_retired", batch_id, path=_path(workspace, feature))


def begin_batch_compile_repair(
    workspace: Path,
    feature: str,
    batch_id: str,
    task_id: str,
    *,
    parallel: bool = False,
) -> WriterResult:
    """Reject the retired batch-compile repair API."""
    del task_id, parallel
    return fail("batch_compile_retired", batch_id, path=_path(workspace, feature))


def mark_batch_tasks_done_after_compile(
    workspace: Path,
    feature: str,
    batch_id: str,
    *,
    parallel: bool = False,
) -> WriterResult:
    """Reject the retired batch-compile completion API."""
    del parallel
    return fail("batch_compile_retired", batch_id, path=_path(workspace, feature))


def mark_parallel_batch_tasks_merged(
    workspace: Path,
    feature: str,
    batch_id: str,
    *,
    merge_commit_sha: str,
    delivery_run_id: str,
) -> WriterResult:
    """Complete a parallel Batch only after its sealed delivery is merged.

    A current Plan has no batch-compile state; Review, UTest and Merge Train
    form the only delivery path that moves parallel Tasks from ``implemented``
    to ``done``.
    """
    if not merge_commit_sha:
        return fail("parallel_merge_commit_sha_required", batch_id, path=_path(workspace, feature))
    if not delivery_run_id:
        return fail("parallel_delivery_run_id_required", batch_id, path=_path(workspace, feature))
    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        if not defer_to_test_stages_enabled(data):
            return fail("defer_to_test_stages_not_enabled", batch_id, path=_path(workspace, feature))
        batch_plans = data.get("_batchPlans")
        batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
        if not isinstance(batch_plan, dict):
            return fail("batch_not_found", batch_id, path=_path(workspace, feature))
        batch_compile = batch_plan.get("batchCompile")
        if batch_compile is not None or batch_plan.get("compileCommand") is not None:
            return fail("review_only_batch_compile_unexpected", batch_id, path=_path(workspace, feature))
        existing_commit = batch_plan.get("mergeCommitSha")
        if isinstance(existing_commit, str) and existing_commit and existing_commit != merge_commit_sha:
            return fail("parallel_batch_merge_commit_mismatch", batch_id, path=_path(workspace, feature))
        task_ids = batch_plan.get("taskIds", [])
        if not isinstance(task_ids, list):
            return fail("batch_task_ids_invalid", batch_id, path=_path(workspace, feature))
        for task in data.get("tasks", []):
            if not isinstance(task, dict) or task.get("id") not in task_ids:
                continue
            status = normalize_status(task.get("status"))
            if status == "implemented":
                task["status"] = "done"
            elif status != "done":
                return fail(
                    "parallel_batch_task_not_implemented",
                    f"batch={batch_id};task={task.get('id')};status={status}",
                    path=_path(workspace, feature),
                )
        batch_plan["mergeCommitSha"] = merge_commit_sha
        batch_plan["deliveryRunId"] = delivery_run_id
        batch_plan["mergedAt"] = _utc_now()
        data["status"] = "in_progress"
        data["activeBatchId"] = None
        data["nextBatchId"] = None
        return _write(workspace, feature, data)




























def update_task_evidence_only(
    workspace: Path,
    feature: str,
    task_id: str,
    evidence_id: str,
    *,
    expected_task_contract_sha256: str,
) -> WriterResult:
    """仅更新任务的 evidence 记录，不改变 status 和其他状态。用于 repair 模式追加新证据。"""

    with _plan_lock(workspace, feature):
        data = _load(workspace, feature)
        task = _find_task(data, task_id)
        if task_contract_sha256(task) != expected_task_contract_sha256:
            return fail("task_contract_changed_after_start", task_id, path=_path(workspace, feature))

        # 只更新 evidenceIds 和 implementationEvidenceIds，不改变其他状态
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
        # 不更新 latestImplementationEvidenceId，保持原有的
        # 不更新 implementationRevision
        # 不改变 status

        return _write(workspace, feature, data)


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
    errors: list[dict[str, Any]] = []
    validated_tasks: list[dict[str, Any]] = []
    try:
        bundle = load_plan_bundle(
            path.parent,
            require_initial_status=args.initial,
            require_all_done=args.done,
        )
        validated_tasks = bundle.tasks
        for task in bundle.tasks:
            errors.extend(
                validate_plan_task_granularity_item(
                    task,
                    task_id=str(task.get("id", "task")),
                )
            )
    except ValueError as exc:
        errors = [{"reason": error} for error in str(exc).split(";")]
    return render_result(
        WriterResult(
            ok=not errors,
            path=path,
            errors=errors,
            data={
                "validation": "gate" if args.gate or args.initial or args.done else "structure",
                "validationReport": _validation_report(validated_tasks, errors),
            },
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
        "## 代码仓库映射",
        "",
        "| workspaceRef | 代码仓库路径 |",
        "| ------------ | ------------ |",
    ]
    code_workspaces = data.get("codeWorkspaces")
    if isinstance(code_workspaces, dict) and code_workspaces:
        for workspace_ref, code_workspace in sorted(code_workspaces.items()):
            lines.append(f"| {workspace_ref} | {code_workspace} |")
    else:
        lines.append("| - | 未登记，Code 阶段禁止猜测路径 |")
    lines.extend([
        "",
        "## 任务总览",
        "",
        "| Task ID | 任务 | 执行模式 | 依赖 | 状态 |",
        "| ------- | ---- | -------- | ---- | ---- |",
    ])
    for task in _tasks(data):
        lines.append(
            f"| {task.get('id', '')} | {task.get('title', '')} | {task_execution_mode(task)} | {_fmt(task.get('deps'))} | {task.get('status', '')} |"
        )
    lines.extend(["", "## 任务详情", ""])
    for task in _tasks(data):
        lines.extend(
            [
                f"### Task [{task.get('id', '')}]: {task.get('title', '')}",
                "",
                f"- 做什么: {task.get('goal', '')}",
                f"- 执行模式: {task_execution_mode(task)}",
                f"- 规格依据: {_fmt(task.get('specRefs'))}",
                f"- api_id: {_fmt(task.get('apiIds'))}",
                f"- data_id: {_fmt(task.get('dataIds'))}",
                f"- decision_id: {_fmt(task.get('decisionIds'))}",
                f"- 涉及范围: modules={_fmt(task.get('scope', {}).get('modules') if isinstance(task.get('scope'), dict) else [])}; entrypoints={_fmt(task.get('scope', {}).get('entrypoints') if isinstance(task.get('scope'), dict) else [])}; pages={_fmt(task.get('scope', {}).get('pages') if isinstance(task.get('scope'), dict) else [])}",
                f"- 验证边界: {task.get('validationBoundary', '')}",
                f"- 代码工作区: {task.get('workspaceRef', '')}",
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
        external_dependency = task.get("externalDependency")
        if isinstance(external_dependency, dict):
            lines.append(
                "- 外部依赖: "
                f"system={external_dependency.get('system', '')}; "
                f"owner={external_dependency.get('owner', '')}; "
                f"trackingRefs={_fmt(external_dependency.get('trackingRefs'))}"
            )
        if task.get("splitRationale"):
            lines.append(f"- 合并理由: {task.get('splitRationale')}")
        commands = task.get("validationCommands", [])
        lines.append("- 验证命令:")
        if isinstance(commands, list) and commands:
            for command in commands:
                if isinstance(command, dict):
                    argv = command.get("argv")
                    rendered = shell_join(argv) if isinstance(argv, list) and all(isinstance(item, str) for item in argv) else command.get("command", "")
                    command_id = command.get("id")
                    lines.append(f"  - {command_id}: {rendered}" if command_id else f"  - {rendered}")
        else:
            lines.append("  - -")
        lines.append(f"- 状态: {task.get('status', '')}")
        disposition = task.get("validationDisposition")
        if isinstance(disposition, dict):
            lines.append(
                "- Code 验证延期: "
                f"{disposition.get('issueId', '')}; "
                f"reason={disposition.get('reason', '')}; "
                f"command={disposition.get('commandId', '')}; "
                f"repairAttempts={disposition.get('repairAttempts', 0)}"
            )
        lines.append("")
    deferred_issues = data.get("deferredValidationIssues")
    if isinstance(deferred_issues, list) and deferred_issues:
        lines.extend(["## Code 验证延期交接", ""])
        for issue in deferred_issues:
            if not isinstance(issue, dict):
                continue
            lines.append(
                f"- {issue.get('issueId', '')}: scope={issue.get('scope', '')}; "
                f"reason={issue.get('reason', '')}; command={issue.get('commandId', '')}; "
                f"handoff={_fmt(issue.get('handoffStages'))}"
            )
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

    publish = sub.add_parser("publish-plan", help="atomically publish one Plan v2 input")
    _common(publish)
    publish.add_argument("--code-workspace", required=True, action="append")
    publish_input = publish.add_mutually_exclusive_group(required=True)
    publish_input.add_argument("--body-stdin", action="store_true")
    publish_input.add_argument("--body-file")
    publish.set_defaults(func=_cmd_publish_plan)

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

    lint_draft_detail = sub.add_parser("lint-draft-task-detail")
    _task_selector(lint_draft_detail)
    lint_draft_detail_input = lint_draft_detail.add_mutually_exclusive_group(required=True)
    lint_draft_detail_input.add_argument("--body-file")
    lint_draft_detail_input.add_argument("--body-stdin", action="store_true")
    lint_draft_detail_input.add_argument("--body-json")
    lint_draft_detail.set_defaults(func=_cmd_lint_draft_task_detail)

    draft_details = sub.add_parser("set-draft-task-details")
    _common(draft_details)
    draft_details_input = draft_details.add_mutually_exclusive_group(required=True)
    draft_details_input.add_argument("--body-file")
    draft_details_input.add_argument("--body-stdin", action="store_true")
    draft_details_input.add_argument("--body-json")
    draft_details.add_argument(
        "--full",
        action="store_true",
        help="要求 payload 包含 Draft 的每个 Task，并运行与 finalize 相同的聚合预检",
    )
    draft_details.set_defaults(func=_cmd_set_draft_task_details)

    lint_draft_details = sub.add_parser("lint-draft-task-details")
    _common(lint_draft_details)
    lint_draft_details_input = lint_draft_details.add_mutually_exclusive_group(required=True)
    lint_draft_details_input.add_argument("--body-file")
    lint_draft_details_input.add_argument("--body-stdin", action="store_true")
    lint_draft_details_input.add_argument("--body-json")
    lint_draft_details.add_argument(
        "--full",
        action="store_true",
        help="要求 payload 包含 Draft 的每个 Task，并运行与 finalize 相同的聚合预检",
    )
    lint_draft_details.set_defaults(func=_cmd_lint_draft_task_details)

    repair_draft_task = sub.add_parser("repair-draft-task")
    _task_selector(repair_draft_task)
    repair_draft_task_input = repair_draft_task.add_mutually_exclusive_group(required=True)
    repair_draft_task_input.add_argument("--body-file")
    repair_draft_task_input.add_argument("--body-stdin", action="store_true")
    repair_draft_task_input.add_argument("--body-json")
    repair_draft_task.set_defaults(func=_cmd_repair_draft_task)

    repair_draft_tasks = sub.add_parser("repair-draft-tasks")
    _common(repair_draft_tasks)
    repair_draft_tasks_input = repair_draft_tasks.add_mutually_exclusive_group(required=True)
    repair_draft_tasks_input.add_argument("--body-file")
    repair_draft_tasks_input.add_argument("--body-stdin", action="store_true")
    repair_draft_tasks_input.add_argument("--body-json")
    repair_draft_tasks.set_defaults(func=_cmd_repair_draft_tasks)

    preflight_task_draft = sub.add_parser("preflight-task-draft")
    _common(preflight_task_draft)
    preflight_task_draft.set_defaults(func=_cmd_preflight_task_draft)

    show_task_draft = sub.add_parser("show-task-draft")
    _common(show_task_draft)
    show_task_draft.set_defaults(func=_cmd_show_task_draft)

    show_draft_task_work = sub.add_parser("show-draft-task-work")
    _task_selector(show_draft_task_work)
    show_draft_task_work.set_defaults(func=_cmd_show_draft_task_work)

    create_repair_work = sub.add_parser("create-repair-work")
    _common(create_repair_work)
    create_repair_work.add_argument(
        "--feedback-file",
        help="可选的评审/校验 JSON；支持 {issues:[...]} 或 {validation:{issues:[...]}}",
    )
    create_repair_work.add_argument(
        "--group-file",
        help="Core 预检尚未创建 Draft 时，绑定现有 task-groups.json 做定点分组修复",
    )
    create_repair_work.set_defaults(func=_cmd_create_repair_work)

    apply_draft_patch = sub.add_parser("apply-draft-patch")
    _common(apply_draft_patch)
    patch_input = apply_draft_patch.add_mutually_exclusive_group(required=True)
    patch_input.add_argument("--patch-file")
    patch_input.add_argument("--patch-stdin", action="store_true")
    apply_draft_patch.set_defaults(func=_cmd_apply_draft_patch)

    diagnose_plan_repair = sub.add_parser("diagnose-plan-repair")
    _common(diagnose_plan_repair)
    diagnose_plan_repair.set_defaults(func=_cmd_diagnose_plan_repair)

    reopen_finalized_draft = sub.add_parser("reopen-finalized-draft")
    _common(reopen_finalized_draft)
    reopen_finalized_draft.add_argument("--reason", required=True)
    reopen_finalized_draft.add_argument(
        "--design-revision-confirmed",
        action="store_true",
        help="仅在 /autodev-design 已重新锁定契约后，将 Draft 绑定到新快照",
    )
    reopen_finalized_draft.set_defaults(func=_cmd_reopen_finalized_draft)

    rebuild_task_draft = sub.add_parser("rebuild-task-draft")
    _common(rebuild_task_draft)
    rebuild_task_draft.add_argument("--group-file", required=True)
    rebuild_task_draft.add_argument("--code-workspace", action="append")
    rebuild_task_draft.add_argument(
        "--design-revision-confirmed",
        action="store_true",
        help="Design 锁已更新时，显式允许 collecting Draft 绑定到最新已确认设计",
    )
    rebuild_task_draft.set_defaults(func=_cmd_rebuild_task_draft)

    rebuild_finalized_draft = sub.add_parser("rebuild-finalized-draft")
    _common(rebuild_finalized_draft)
    rebuild_finalized_draft.add_argument("--group-file", required=True)
    rebuild_finalized_draft.add_argument("--code-workspace", action="append")
    rebuild_finalized_draft.add_argument("--reason", required=True)
    rebuild_finalized_draft.add_argument(
        "--design-revision-confirmed",
        action="store_true",
        help="仅在 /autodev-design 已重新锁定契约后允许绑定最新设计",
    )
    rebuild_finalized_draft.set_defaults(func=_cmd_rebuild_finalized_draft)

    finalize_task_draft = sub.add_parser("finalize-task-draft")
    _common(finalize_task_draft)
    finalize_task_draft.add_argument("--force", action="store_true")
    finalize_task_draft.set_defaults(func=_cmd_finalize_task_draft)

    preflight_task_groups = sub.add_parser("preflight-task-groups")
    _common(preflight_task_groups)
    preflight_task_groups.add_argument("--group-file", required=True)
    preflight_task_groups.set_defaults(func=_cmd_preflight_task_groups)

    write_task_groups = sub.add_parser("write-task-groups")
    _common(write_task_groups)
    write_task_groups.add_argument(
        "--group-file",
        help=(
            "可选 Core 输出路径；默认写入 Feature 的 .tmp/plan_writer/task-groups.json"
        ),
    )
    write_task_groups_input = write_task_groups.add_mutually_exclusive_group(required=True)
    write_task_groups_input.add_argument("--body-file")
    write_task_groups_input.add_argument("--body-stdin", action="store_true")
    write_task_groups_input.add_argument("--body-json")
    write_task_groups.set_defaults(func=_cmd_write_task_groups)

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

    quality_gate = sub.add_parser("add-quality-gate-command")
    _common(quality_gate)
    quality_gate.add_argument("--lane", choices=sorted(EXECUTION_LANES), required=True)
    quality_gate.add_argument("--command", required=True)
    quality_gate.add_argument("--cwd")
    quality_gate.add_argument("--repo")
    quality_gate.add_argument("--code-workspace", action="append")
    quality_gate.add_argument("--replace", action="store_true")
    quality_gate.set_defaults(func=_cmd_add_quality_gate_command)

    project_validation = sub.add_parser("add-project-validation-command")
    _common(project_validation)
    project_validation.add_argument("--command-id")
    project_validation.add_argument("--command", required=True)
    project_validation.add_argument("--cwd")
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
            if args.command in DRAFT_BUNDLE_COMMANDS:
                if args.command in DRAFT_RUNTIME_GUARDED_COMMANDS:
                    formal_root, formal_batches, formal_load_errors = _load_raw_formal_bundle(
                        workspace,
                        feature,
                    )
                    _, execution_blockers = _formal_execution_blockers(
                        workspace,
                        feature,
                        formal_root,
                        formal_batches,
                        formal_load_errors,
                    )
                    if execution_blockers:
                        return render_result(WriterResult(
                            ok=False,
                            path=_path(workspace, feature),
                            errors=[{
                                "reason": "plan_execution_workspace_frozen",
                                "detail": ";".join(execution_blockers),
                                "repairTarget": "plan_revision",
                            }],
                        ))
                return args.func(args)
            current = _load(workspace, feature)
            if args.command in PLANNING_MUTATION_COMMANDS:
                _require_collecting(current)
            return args.func(args)
    except PlanWriterInputError as exc:
        return render_result(fail(exc.reason, exc.detail))
    except WriterEncodingError as exc:
        return render_result(fail("plan_writer_encoding_error", str(exc)))
    except Exception as exc:
        return render_result(fail("plan_writer_failed", str(exc)))


if __name__ == "__main__":
    raise SystemExit(main())
