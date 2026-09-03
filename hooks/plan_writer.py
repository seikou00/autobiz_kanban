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
from typing import Any, NoReturn


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
    PROJECT_VALIDATION_KINDS,
    REPOSITORY_ID_RE,
    SOURCE_REQUIREMENT_ID_RE,
    TASK_EXECUTION_MODES,
    TASK_VALIDATION_KINDS,
    VISUAL_SOURCE_ID_RE,
    batch_plan_path,
    defer_to_test_stages_enabled,
    load_plan_bundle,
    normalize_status,
    task_execution_lane,
    task_execution_mode,
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
    scenario_refs_from_spec_refs,
    validate_plan_task_granularity_item,
    validate_plan_task_grouping_item,
)
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
from hooks.run_context import load as load_run_context  # noqa: E402
from hooks.validation_capabilities import (  # noqa: E402
    command_errors as validation_capability_command_errors,
    load as load_validation_capabilities,
)
from hooks.artifact_ref_validator import (  # noqa: E402
    design_contract_id_universe,
    design_contract_snapshot,
    load_design_contract,
    plan_source_requirement_universe,
    validate_plan_design_coverage,
    validate_plan_source_coverage,
    validate_task_artifact_refs,
    validate_task_group_design_contract,
)
from hooks.plan_scope import (  # noqa: E402
    SCOPE_KINDS,
    ScopeSelection,
    load_plan_scope,
    scope_report,
)
from board_core.state_store import load_state_json_records_result  # noqa: E402


PLAN_FILE = "plan.json"
PLAN_MD_FILE = "PLAN.md"
PLAN_WRITE_TRANSACTION_FILE = ".plan-write-transaction.json"
SPEC_SCENARIO_DEF_RE = re.compile(
    r"^####\s+Scenario\s+(?=\[?(SCN-\d{3})\]?:\s+.+$)(?:\[SCN-\d{3}\]|SCN-\d{3}):\s+.+$",
    re.MULTILINE,
)
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
DESIGN_CONTRACT_LOCK_FILE = ".design-contract.lock.json"
DRAFT_GROUP_OWNED_FIELDS = {
    "id",
    "title",
    "deps",
    "uiRequired",
    "specRefs",
    "sourceRefs",
    "mergedScenarioRefs",
    "apiIds",
    "uiRefs",
    "splitRationale",
    "validationBoundary",
    "workspaceRef",
    "executionMode",
    "externalDependency",
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
TASK_REPAIR_BODY_FIELDS = {"repairs"}
TASK_ID_IN_REASON_RE = re.compile(r"^(T\d{3})\.([A-Za-z][A-Za-z0-9]*(?:\[[0-9]+\])?)")
TASK_ID_IN_DETAIL_RE = re.compile(r"(?:^|;)task=(T\d{3})(?:;|$)")
TASK_IDS_IN_DETAIL_RE = re.compile(r"(?:^|;)taskIds=([^;]+)(?:;|$)")
TASK_CONTEXT_IN_DETAIL_RE = re.compile(
    r"(?:^|;)context=(T\d{3})\.([A-Za-z][A-Za-z0-9]*(?:\[[0-9]+\])?)(?:;|$)"
)
TASK_DETAIL_PATCH_FIELDS = {"goal", "implementationPoints", "acceptanceCriteria", "nonGoals", "blockers"}
TASK_DETAIL_FORBIDDEN_FIELDS = {
    "id",
    "status",
    "deps",
    "evidenceIds",
    "specRefs",
    "sourceRefs",
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
    "codeGate": "batch_compile_only",
    "maxTestStageRepairAttempts": BATCH_COMPILE_MAX_REPAIR_ATTEMPTS,
}
DRAFT_BUNDLE_COMMANDS = {
    "prepare-task-draft",
    "import-task-directory",
    "set-draft-task-detail",
    "repair-draft-task",
    "repair-draft-tasks",
    "preflight-task-draft",
    "show-task-draft",
    "rebuild-task-draft",
    "reopen-finalized-draft",
    "diagnose-plan-repair",
    "finalize-task-draft",
}
DRAFT_RUNTIME_GUARDED_COMMANDS = DRAFT_BUNDLE_COMMANDS - {
    "diagnose-plan-repair",
    "reopen-finalized-draft",
    "show-task-draft",
}
PLAN_REOPEN_ALLOWED_CHECKPOINTS = {
    "specs_done",
    "plan_in_progress",
    "plan_done",
    "detail_design_in_progress",
}


class PlanWriterInputError(ValueError):
    def __init__(
        self,
        reason: str,
        detail: str = "",
        *,
        repair_suggestion: str = "",
        repair_target: str = "draft_integrity",
    ) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail
        self.repair_suggestion = repair_suggestion
        self.repair_target = repair_target

    def as_error(self) -> dict[str, Any]:
        """Render as one entry of the shared preflight envelope."""

        error: dict[str, Any] = {
            "reason": self.reason,
            "severity": "blocker",
            "layer": "structure",
            "repairTarget": self.repair_target,
        }
        if self.detail:
            error["detail"] = self.detail
        if self.repair_suggestion:
            error["repairSuggestion"] = self.repair_suggestion
        return error


class JsonArgumentParser(argparse.ArgumentParser):
    """Report argument errors in the writer's envelope instead of a usage dump."""

    def error(self, message: str) -> NoReturn:  # type: ignore[override]
        raise SystemExit(render_result(WriterResult(
            ok=False,
            errors=[{
                "reason": "plan_writer_argument_invalid",
                "detail": f"command={self.prog};{message}",
                "severity": "blocker",
                "layer": "structure",
                "repairTarget": "draft_integrity",
                "repairSuggestion": (
                    "按本命令的参数契约重传。可用的输入模式和字段见 "
                    "plan_writer.py add-task-contract 输出的 supportedInputModes。"
                ),
            }],
        )))


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


def _current_design_contract(feature_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return load_design_contract(feature_dir)


def _ensure_persistent_design_contract_lock(
    feature_dir: Path,
    feature: str,
    contract: dict[str, Any],
    *,
    revision_confirmed: bool = False,
    reason: str = "",
) -> list[dict[str, Any]]:
    """Keep Design confirmation independent from the disposable Draft tree."""
    path = feature_dir / DESIGN_CONTRACT_LOCK_FILE
    snapshot = design_contract_snapshot(contract)
    if not path.is_file():
        atomic_write_json(path, {
            "version": 1,
            "featureId": feature,
            "designContract": snapshot,
            "confirmedAt": _utc_now(),
            "confirmationReason": "initial_plan_prepare",
        })
        return []
    try:
        stored = load_json(path)
    except (OSError, ValueError, TypeError):
        return [{
            "reason": "persistent_design_contract_lock_invalid",
            "repairTarget": "design_revision",
            "repairable": False,
        }]
    stored_contract = stored.get("designContract") if isinstance(stored, dict) else None
    stored_sha = stored_contract.get("sha256") if isinstance(stored_contract, dict) else None
    if not isinstance(stored_sha, str):
        return [{
            "reason": "persistent_design_contract_lock_invalid",
            "repairTarget": "design_revision",
            "repairable": False,
        }]
    if stored_sha == snapshot.get("sha256"):
        return []
    if not revision_confirmed:
        return [{
            "reason": "confirmed_design_changed_without_reconfirmation",
            "detail": (
                f"expected={stored_sha};actual={snapshot.get('sha256')};"
                "pass --design-revision-confirmed --reason after Design confirmation"
            ),
            "repairTarget": "design_revision",
            "repairable": False,
            "designMutationAllowed": False,
        }]
    if not reason.strip():
        return [{
            "reason": "design_revision_confirmation_reason_required",
            "repairTarget": "design_revision",
            "repairable": False,
        }]
    atomic_write_json(path, {
        "version": 1,
        "featureId": feature,
        "designContract": snapshot,
        "confirmedAt": _utc_now(),
        "confirmationReason": reason.strip(),
    })
    return []


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
                "plan_cannot_redefine_design;explicit_design_revision_required"
            ),
            "repairTarget": "design_revision",
            "repairable": False,
            "designMutationAllowed": False,
        }]
    return []


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


def _task_group_external_dependency_example() -> dict[str, Any]:
    value = _task_group_example().get("externalDependencyExample")
    if not isinstance(value, dict):
        raise RuntimeError("task_group_external_dependency_example_must_be_object")
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
        "taskValidationPolicy": copy.deepcopy(DEFAULT_TASK_VALIDATION_POLICY),
        "batches": [],
        "batchValidationProfiles": {},
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


def _load(
    workspace: Path,
    feature: str,
    *,
    allow_task_set_digest_mismatch: bool = False,
) -> dict[str, Any]:
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
    if (
        not allow_task_set_digest_mismatch
        and data.get("taskSetDigest") is not None
        and data.get("taskSetDigest") != task_set_digest(data, batch_plans)
    ):
        raise PlanWriterInputError(
            "task_set_digest_mismatch",
            "formal plan artifacts were modified outside plan_writer",
        )
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
        for validation_name in ("batchValidation",):
            validation = batch.get(validation_name)
            if not isinstance(validation, dict):
                continue
            status = validation.get("status")
            if status not in {None, "pending"}:
                blockers.append(f"validation_started:{batch_id}:{validation_name}:{status}")
            for field in (
                "activeRunId",
                "lastRunId",
                "currentTaskId",
                "batchSnapshotSha256",
            ):
                if validation.get(field) is not None:
                    blockers.append(f"validation_runtime_data_present:{batch_id}:{validation_name}.{field}")
            for field in (
                "completedTaskIds",
                "evidenceIds",
                "latestPassEvidenceIds",
                "deferredTaskIds",
            ):
                value = validation.get(field)
                if isinstance(value, list) and value:
                    blockers.append(f"validation_runtime_data_present:{batch_id}:{validation_name}.{field}")
            latest = validation.get("latestPassEvidenceByTask")
            if isinstance(latest, dict) and latest:
                blockers.append(
                    f"validation_runtime_data_present:{batch_id}:{validation_name}.latestPassEvidenceByTask"
                )
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
        if "sourceRefs" in raw_group:
            _group_string_list(
                errors,
                raw_group,
                task_id,
                "sourceRefs",
                required=False,
                item_re=SOURCE_REQUIREMENT_ID_RE,
            )
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
            # Lane ordering is a batching preference; batch execution order is
            # derived from the batches themselves, not from this array position.
            errors.append({
                "reason": "backend_task_after_frontend",
                "detail": f"task={task_id}",
                "severity": "warning",
                "repairTarget": "task_group",
                "repairSuggestion": (
                    f"任务 {task_id} 是后端任务但排在前端任务之后。"
                    "把后端任务集中排在前端任务之前可以少切一次 batch。"
                ),
            })
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
        workspace_ref = raw_group.get("workspaceRef")
        if not isinstance(workspace_ref, str) or not REPOSITORY_ID_RE.fullmatch(workspace_ref):
            errors.append({"reason": f"{task_id}.workspaceRef_invalid"})
        prior_ids.add(task_id)
    return errors


PREFLIGHT_LAYERS = ("structure", "task_local", "cross_artifact", "runtime")

# Only an unusable group list stops the later layers; every other structural
# problem is reported alongside them.
FATAL_STRUCTURE_REASONS = frozenset({"task_groups_missing"})


def _partition_preflight(
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Keep blockers in the returned list; warnings ride along for the report."""

    blockers: list[dict[str, Any]] = []
    for error in errors:
        if error.get("severity") == "warning":
            if warnings is not None:
                warnings.append(error)
        else:
            blockers.append(error)
    return blockers


def _preflight_layer(
    errors: list[dict[str, Any]],
    layer: str,
    *,
    pending: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Stamp one layer's errors, naming the layers its failure left unevaluated."""

    for error in errors:
        error.setdefault("severity", "blocker")
        error.setdefault("layer", layer)
        if pending:
            error.setdefault("blockedBy", list(pending))
    return errors


def _task_group_preflight_errors(
    feature_dir: Path,
    data: dict[str, Any],
    *,
    warnings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Report every independently detectable problem, layer by layer.

    A layer only stops the run when the next one cannot be evaluated safely: an
    unusable group list, or a Design contract that will not load. Advisory findings
    are collected into ``warnings`` instead of blocking the stage.
    """

    structure_errors = _task_group_structure_errors(data)
    if any(error.get("reason") in FATAL_STRUCTURE_REASONS for error in structure_errors):
        return _partition_preflight(_preflight_layer(
            structure_errors,
            "structure",
            pending=("task_local", "cross_artifact", "runtime"),
        ), warnings)
    errors = _preflight_layer(structure_errors, "structure")

    local_errors: list[dict[str, Any]] = []
    implementation_scope, scope_errors = load_scope(feature_dir)
    local_errors.extend({"reason": error} for error in scope_errors)
    for group in _task_groups(data):
        task_id = str(group.get("id", "task"))
        ui_required = group.get("uiRequired") is True
        if implementation_scope == "backend_only" and ui_required:
            local_errors.append({
                "reason": "implementation_scope_frontend_task_forbidden",
                "detail": f"scope=backend_only;task={task_id}",
                "repairSuggestion": f"当前实现范围为 backend_only，但任务 {task_id} 标记为需要前端（uiRequired=true）。请将该任务的 uiRequired 改为 false，或修改 scope.md 中的实现范围"
            })
        elif implementation_scope == "frontend_only" and not ui_required:
            local_errors.append({
                "reason": "implementation_scope_backend_task_forbidden",
                "detail": f"scope=frontend_only;task={task_id}",
                "repairSuggestion": f"当前实现范围为 frontend_only，但任务 {task_id} 标记为后端任务（uiRequired=false）。请将该任务的 uiRequired 改为 true，或修改 scope.md 中的实现范围"
            })
        local_errors.extend(validate_plan_task_grouping_item(group, task_id=task_id))
    errors.extend(_preflight_layer(local_errors, "task_local"))

    design_contract, design_errors = load_design_contract(feature_dir)
    if design_errors:
        errors.extend(_preflight_layer(
            design_errors,
            "cross_artifact",
            pending=("runtime",),
        ))
        return _partition_preflight(errors, warnings)

    cross_errors = list(validate_task_group_design_contract(design_contract, _task_groups(data)))
    scope, partition_errors = load_plan_scope(feature_dir)
    cross_errors.extend(partition_errors)
    source_selection, source_errors = scope.select(
        "source", plan_source_requirement_universe(feature_dir)
    )
    cross_errors.extend(source_errors)
    cross_errors.extend(validate_plan_source_coverage(
        feature_dir,
        _task_groups(data),
        included_ids=source_selection.included,
    ))
    missing, coverage_errors = _scoped_scenario_coverage(feature_dir, _task_groups(data))
    cross_errors.extend(coverage_errors)
    if missing:
        missing_count = len(missing)
        missing_preview = ', '.join(missing[:10])
        if missing_count > 10:
            missing_preview += f" ...还有 {missing_count - 10} 个"
        cross_errors.append({
            "reason": "missing_plan_scenario_coverage",
            "detail": f"return_to_scenario_matrix;ids={','.join(missing)}",
            "repairSuggestion": f"有 {missing_count} 个场景未被任务覆盖：{missing_preview}。请在 task-groups.json 中添加或调整任务的 mergedScenarioRefs，确保所有场景都被覆盖"
        })
    errors.extend(_preflight_layer(cross_errors, "cross_artifact"))

    errors.extend(_preflight_layer(
        _runtime_task_group_lane_errors(feature_dir, data),
        "runtime",
    ))
    return _partition_preflight(errors, warnings)


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
        "sourceRefs": item.get("sourceRefs") if isinstance(item.get("sourceRefs"), list) else [],
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
        "externalDependency": (
            copy.deepcopy(item.get("externalDependency"))
            if isinstance(item.get("externalDependency"), dict)
            else None
        ),
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
) -> list[dict[str, Any]]:
    structure_errors = [{"reason": reason} for reason in _structure_errors(data, allow_empty=allow_empty)]
    if allow_empty and not _tasks(data):
        return structure_errors

    # Shape and granularity are independent verdicts on the same task; report both
    # rather than making the caller fix one to discover the other.
    granularity_errors: list[dict[str, Any]] = []
    for task in _tasks(data):
        task_id = str(task.get("id", "task"))
        granularity_errors.extend(validate_plan_task_granularity_item(task, task_id=task_id))
    return structure_errors + granularity_errors


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
    batch_compile: dict[str, Any] | None = None,
) -> str:
    statuses = [normalize_status(task.get("status")) for task in batch_tasks]
    if (
        any(status == "failed" for status in statuses)
        or batch_validation.get("status") == "failed"
        or (isinstance(batch_compile, dict) and batch_compile.get("status") == "failed")
    ):
        return "failed"
    if statuses and all(status == "done" for status in statuses):
        compile_passed = isinstance(batch_compile, dict) and batch_compile.get("status") == "passed"
        return "done" if compile_passed else "in_progress"
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
    workspace_root = workspace_contract[0][1]
    command_repository = command.get("repo")
    if repository == "default":
        repository_matches = command_repository in {None, "default"}
    else:
        repository_matches = command_repository == repository
    return repository_matches and _relative_paths_overlap(
        str(command.get("cwd", ".")), workspace_root
    )


def _project_batches(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    tasks_view = _tasks(data)
    assignments = dict(data.get("_batchAssignments") or {})
    prior_plans = data.get("_batchPlans") if isinstance(data.get("_batchPlans"), dict) else {}
    groups: dict[str, list[dict[str, Any]]] = {}
    spec_roots: dict[str, str] = {}
    execution_lanes: dict[str, str] = {}
    workspace_contracts: dict[str, tuple[tuple[str, str], ...]] = {}
    frontend_routes: dict[str, str] = {}
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
            workspace_contract = _batch_workspace_contract(task)
            frontend_route = _batch_frontend_route(task)
            last_batch = sorted(groups)[-1] if groups else None
            can_append_to_last = bool(
                last_batch
                and spec_roots.get(str(last_batch)) == primary
                and execution_lanes.get(str(last_batch)) == execution_lane
                and workspace_contracts.get(str(last_batch)) == workspace_contract
                and frontend_routes.get(str(last_batch)) == frontend_route
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
        workspace_contracts.setdefault(
            batch_id,
            _batch_workspace_contract(task),
        )
        frontend_routes.setdefault(batch_id, _batch_frontend_route(task))

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
        all_profile_commands = profile.get("commands") if isinstance(profile, dict) else []
        if not isinstance(all_profile_commands, list):
            all_profile_commands = []
        profile_mode = "commands"
        workspace_contract = workspace_contracts[batch_id]
        profile_commands = [
            command
            for command in all_profile_commands
            if isinstance(command, dict)
            and _batch_profile_command_matches_workspace(command, workspace_contract)
        ]
        effective_commands = [
            {**command, "id": f"BATCH-{batch_id}-VAL-{command_index:03d}"}
            for command_index, command in enumerate(profile_commands, start=1)
        ]
        previous_validation = previous.get("batchValidation")
        previous_validation = previous_validation if isinstance(previous_validation, dict) else {}
        coverage_command_ids: list[str] = []
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
            "deferredIssues": (
                copy.deepcopy(previous_validation.get("deferredIssues", []))
                if contract_unchanged
                else []
            ),
        }
        batch_compile = previous.get("batchCompile") if isinstance(previous.get("batchCompile"), dict) else None
        status = _batch_status(batch_tasks, batch_validation, batch_compile)
        task_ids_list = [str(task.get("id")) for task in batch_tasks]
        projected[batch_id] = {
            "featureId": root.get("featureId"),
            "batchId": batch_id,
            "title": title,
            "executionLane": execution_lane,
            "status": status,
            "taskCount": len(batch_tasks),
            "completedTaskCount": sum(normalize_status(task.get("status")) == "done" for task in batch_tasks),
            "completionEvidenceIds": completion_ids,
            "taskIds": task_ids_list,
            "batchValidation": batch_validation,
            **({"batchCompile": previous.get("batchCompile")} if "batchCompile" in previous else {}),
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
    deferred_issues: list[dict[str, Any]] = []
    seen_issue_ids: set[str] = set()
    for batch_plan in projected.values():
        for validation_name in ("batchValidation",):
            validation = batch_plan.get(validation_name)
            issues = validation.get("deferredIssues") if isinstance(validation, dict) else None
            for issue in issues if isinstance(issues, list) else []:
                issue_id = issue.get("issueId") if isinstance(issue, dict) else None
                if isinstance(issue_id, str) and issue_id not in seen_issue_ids:
                    deferred_issues.append(copy.deepcopy(issue))
                    seen_issue_ids.add(issue_id)
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
    elif root.get("status") == "awaiting_next_conversation":
        root["activeBatchId"] = None
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
        unlink_if_exists(transaction_path)
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
        raise PlanWriterInputError(
            "task_draft_digest_mismatch",
            "draft artifacts were modified outside plan_writer",
            repair_suggestion=(
                "Draft 产物被 plan_writer 之外的写入改动过。用 rebuild-task-draft 重建，"
                "所有改动都通过 plan_writer 子命令提交。"
            ),
        )
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
            repair_suggestion=(
                "task-groups.json 在 Draft 创建后被改过。跑 rebuild-task-draft 重建 Draft，"
                "再继续填 task detail。"
            ),
            repair_target="task_group",
        )
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
    task: dict[str, Any] = {
        "id": task_id,
        "title": group.get("title"),
        "executionMode": execution_mode,
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
        "workspaceRef": group.get("workspaceRef"),
        "nonGoals": [],
        "specRefs": copy.deepcopy(group.get("specRefs", [])),
        "sourceRefs": copy.deepcopy(group.get("sourceRefs", [])),
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
    rationale = group.get("splitRationale")
    if isinstance(rationale, str) and rationale.strip():
        task["splitRationale"] = rationale
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
        return read_object_file(args.body_file)
    if args.body_stdin:
        return _plan_writer_stdin_body()
    if args.body_json:
        value = parse_json_value(args.body_json)
        if not isinstance(value, dict):
            raise PlanWriterInputError("draft_task_detail_must_be_object")
        return value
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
        raise PlanWriterInputError(
            "draft_task_repair_patch_empty",
            f"task={task_id}",
            repair_suggestion=(
                "patch 至少要带一个待改字段。只改分组归属的字段要走 task_group 修复，"
                "不要提交空 patch。"
            ),
            repair_target="task_detail",
        )
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
            repair_suggestion=(
                f"这些字段不属于 task detail：{', '.join(unknown)}。"
                f"可改字段见 add-task-contract 的 taskDetailInputExample。"
            ),
            repair_target="task_detail",
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


def _scoped_scenario_coverage(
    feature_dir: Path,
    task_items: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return (missing in-scope scenarios, scope declaration errors)."""

    expected, covered = _scenario_coverage(feature_dir, task_items)
    scope, errors = load_plan_scope(feature_dir)
    selection, select_errors = scope.select("scenario", expected)
    errors.extend(select_errors)
    return sorted(selection.included - covered), errors


def _feature_scope_report(
    feature_dir: Path,
    task_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deferred work stays visible in the result payload instead of becoming tasks."""

    scope, _ = load_plan_scope(feature_dir)
    if not scope.declared_kinds:
        return {}
    selections: dict[str, ScopeSelection] = {}
    expected, _covered = _scenario_coverage(feature_dir, task_items)
    selections["scenario"], _ = scope.select("scenario", expected)
    design_contract, design_errors = load_design_contract(feature_dir)
    if not design_errors:
        selections["design"], _ = scope.select(
            "design", design_contract_id_universe(design_contract)
        )
    selections["source"], _ = scope.select(
        "source", plan_source_requirement_universe(feature_dir)
    )
    return scope_report(selections)


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
        "sourceRefs": [],
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
                unlink_if_exists(old_plan)
                try:
                    old_plan.parent.rmdir()
                except OSError:
                    pass
            try:
                plans_dir.rmdir()
            except OSError:
                pass
        unlink_if_exists(_handoff_path(workspace, feature))
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
    task.setdefault("sourceRefs", [])
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


# Optional list fields the writer fills in when absent. A present-but-wrong value
# is still an error: only omission has an unambiguous default.
OPTIONAL_GROUP_LIST_FIELDS = ("deps", "apiIds", "mergedScenarioRefs")


def _normalize_task_groups(data: dict[str, Any]) -> dict[str, Any]:
    """Fill in the omitted list fields whose empty default is unambiguous."""

    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        return data
    groups: list[dict[str, Any]] = []
    changed = False
    for group in raw_groups:
        if not isinstance(group, dict):
            groups.append(group)
            continue
        missing = [field for field in OPTIONAL_GROUP_LIST_FIELDS if field not in group]
        if not missing:
            groups.append(group)
            continue
        updated = copy.deepcopy(group)
        for field in missing:
            updated[field] = []
        groups.append(updated)
        changed = True
    if not changed:
        return data
    data = dict(data)
    data["groups"] = groups
    return data


def _renumber_task_groups(data: dict[str, Any]) -> dict[str, Any]:
    """Assign the positional task ids the contract requires, rewiring deps.

    Position is the contract, so the writer derives the ids instead of reporting a
    cascade every time a group is inserted, dropped or reordered. Ambiguous input
    (a duplicate or malformed id) is left untouched for the validators to report.
    """

    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        return data
    if any(not isinstance(group, dict) for group in raw_groups):
        return data
    current_ids = [group.get("id") for group in raw_groups]
    if any(not isinstance(value, str) for value in current_ids):
        return data
    if len(set(current_ids)) != len(current_ids):
        return data
    renamed = {
        str(value): f"T{index:03d}"
        for index, value in enumerate(current_ids, start=1)
    }
    if all(old == new for old, new in renamed.items()):
        return data
    groups: list[dict[str, Any]] = []
    for group in raw_groups:
        updated = copy.deepcopy(group)
        updated["id"] = renamed[str(group.get("id"))]
        deps = group.get("deps")
        if isinstance(deps, list):
            updated["deps"] = [
                renamed.get(dep, dep) if isinstance(dep, str) else dep
                for dep in deps
            ]
        groups.append(updated)
    data = dict(data)
    data["groups"] = groups
    return data


def _load_task_group_file(group_file: Path, feature: str) -> dict[str, Any]:
    data = read_object_file(group_file)
    manifest_feature = data.get("featureId")
    if manifest_feature != feature:
        raise PlanWriterInputError(
            "task_groups_feature_mismatch",
            f"expected={feature};actual={manifest_feature}",
        )
    return _renumber_task_groups(_normalize_task_groups(data))


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
    }


def _code_workspace_contexts(values: list[str] | None) -> list[dict[str, Any]]:
    """Bind each requested path to a workspace ref.

    Workspace identity is the requested directory, repository identity is its git
    root. A monorepo therefore registers several workspaces — one per sub-path —
    and only an exact repeat or two paths claiming the same ref are rejected.
    """

    entries: list[dict[str, Any]] = []
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
        key = (str(git_root), workspace_root)
        if key in seen:
            continue
        seen.add(key)
        entries.append({
            "gitRoot": git_root,
            "workspaceRoot": workspace_root,
            "requestedPath": requested,
        })

    workspaces_per_root: dict[str, int] = {}
    for entry in entries:
        root_key = str(entry["gitRoot"])
        workspaces_per_root[root_key] = workspaces_per_root.get(root_key, 0) + 1

    contexts: list[dict[str, Any]] = []
    for entry in entries:
        git_root = entry["gitRoot"]
        requested = entry["requestedPath"]
        # One workspace per repository keeps addressing it by the repository name;
        # siblings inside one repository are addressed by their own directory.
        shares_repository = workspaces_per_root[str(git_root)] > 1
        workspace_ref = requested.name if shares_repository else git_root.name
        if not REPOSITORY_ID_RE.fullmatch(workspace_ref):
            raise PlanWriterInputError(
                "code_workspace_ref_invalid",
                (
                    f"path={requested};ref={workspace_ref};"
                    "workspace directory name must match [A-Za-z0-9._-]+"
                ),
            )
        existing = next(
            (item for item in contexts if item.get("repo") == workspace_ref),
            None,
        )
        if existing is not None:
            raise PlanWriterInputError(
                "code_workspace_ref_conflict",
                (
                    f"ref={workspace_ref};first={existing.get('requestedPath')};"
                    f"second={requested};use_distinct_workspace_directory_names"
                ),
            )
        contexts.append({
            "repo": workspace_ref,
            "repositoryId": git_root.name,
            "gitRoot": git_root,
            "workspaceRoot": entry["workspaceRoot"],
            "requestedPath": requested,
        })
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
        issue["issueId"] = f"ISSUE-{index:03d}"
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
    warnings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    runtime_profile_errors = _apply_runtime_validation_profiles(feature_dir, data)
    if runtime_profile_errors:
        # Validation profiles decide which commands are even legal; without them
        # every later verdict would be provisional.
        return _partition_preflight(_preflight_layer(
            runtime_profile_errors,
            "structure",
            pending=("task_local", "cross_artifact", "runtime"),
        ), warnings)
    errors = _task_group_preflight_errors(feature_dir, group_data, warnings=warnings)
    errors.extend(_preflight_layer(
        _task_group_contract_errors(group_data, _tasks(data)),
        "task_local",
    ))
    design_contract, design_errors = load_design_contract(feature_dir)
    if design_errors:
        errors.extend(_preflight_layer(
            design_errors,
            "cross_artifact",
            pending=("runtime",),
        ))
        return _partition_preflight(errors, warnings)
    for task in _tasks(data):
        errors.extend(validate_task_artifact_refs(
            feature_dir,
            task,
            design_contract=design_contract,
        ))
    scope, scope_errors = load_plan_scope(feature_dir)
    errors.extend(scope_errors)
    design_selection, design_errors = scope.select(
        "design", design_contract_id_universe(design_contract)
    )
    errors.extend(design_errors)
    source_selection, source_errors = scope.select(
        "source", plan_source_requirement_universe(feature_dir)
    )
    errors.extend(source_errors)
    errors.extend(validate_plan_design_coverage(
        design_contract,
        _tasks(data),
        included_ids=design_selection.included,
    ))
    errors.extend(validate_plan_source_coverage(
        feature_dir,
        _tasks(data),
        included_ids=source_selection.included,
    ))
    errors.extend(_preflight_layer(_task_set_validation_errors(data), "task_local"))
    errors.extend(_preflight_layer(
        _code_workspace_preflight_errors(data, code_workspaces),
        "runtime",
    ))
    errors.extend(_preflight_layer(
        _runtime_plan_contract_errors(feature_dir, data),
        "runtime",
    ))
    if not errors:
        root, batches = _project_batches(data)
        bundle_errors = validate_plan_bundle_data(root, batches)
        errors.extend(_preflight_layer(
            [{"reason": error} for error in bundle_errors],
            "runtime",
        ))
    missing, coverage_errors = _scoped_scenario_coverage(feature_dir, _tasks(data))
    errors.extend(coverage_errors)
    if missing:
        errors.append({
            "reason": "missing_plan_scenario_coverage",
            "detail": f"return_to_scenario_matrix;ids={','.join(missing)}",
            "field": "specRefs",
            "repairTarget": "task_group",
        })
    # Anything a validator produced without a layer belongs to the cross-artifact
    # pass; setdefault keeps the layers already stamped above.
    return _partition_preflight(_preflight_layer(errors, "cross_artifact"), warnings)


def _runtime_scope_error(reason: str, detail: str) -> dict[str, Any]:
    return {
        "reason": reason,
        "detail": detail,
        "repairTarget": "draft_integrity",
        "repairable": False,
        "retryable": False,
        "requiredAction": "restart_feature_after_runtime_fix",
        "repairSuggestion": (
            "停止当前 Plan；修正部署单元目录、manifest 或构建脚本后新开 Feature 会话。"
            "不得重跑 preflight/finalize、使用 --force、编辑 .runtime 或删除/重建 Draft。"
        ),
    }


def _runtime_toolchain_error(
    feature_dir: Path,
    lane: str,
    unavailable: list[dict[str, Any]],
) -> dict[str, Any]:
    executables = sorted({
        str(item.get("requiredExecutable"))
        for item in unavailable
        if item.get("requiredExecutable")
    })
    manifests = sorted({
        "{}/{}".format(item.get("cwd", "."), item.get("source", "manifest")).replace("./", "")
        for item in unavailable
    })
    refresh_command = "python {} refresh --feature-dir {} --lane {}".format(
        ROOT / "hooks" / "validation_capabilities.py",
        feature_dir,
        lane,
    )
    return {
        "reason": "validation_toolchain_unavailable",
        "detail": "lane={};missingExecutables={};manifests={}".format(
            lane,
            ",".join(executables) or "unknown",
            ",".join(manifests) or "unknown",
        ),
        "repairTarget": "runtime_environment",
        "repairable": False,
        "retryable": True,
        "requiredAction": "install_missing_tool_and_refresh_validation_capabilities",
        "repairSuggestion": "安装 {}，再执行 `{}`；refresh 成功前不得重跑 Plan preflight/finalize。".format(
            ", ".join(executables) or "缺失构建工具",
            refresh_command,
        ),
    }


def _runtime_lane_items(catalog: dict[str, Any], lane: str, field: str) -> list[dict[str, Any]]:
    return [
        item for item in catalog.get(field, [])
        if isinstance(item, dict)
        and (
            (lane == "frontend" and str(item.get("source", "")).startswith("package.json"))
            or (lane == "backend" and not str(item.get("source", "")).startswith("package.json"))
        )
    ]


def _runtime_task_group_lane_errors(
    feature_dir: Path,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    context_path = feature_dir / ".runtime" / "RUN_CONTEXT.json"
    if not context_path.is_file():
        return []
    try:
        run_context = load_run_context(feature_dir.parents[2], feature_dir.name)
        catalog = load_validation_capabilities(
            feature_dir, run_context.get("contextDigest")
        )
    except ValueError as exc:
        return [_runtime_scope_error("SCOPE_UNRESOLVED", str(exc))]
    used_lanes = {
        "frontend" if group.get("uiRequired") is True else "backend"
        for group in _task_groups(data)
    }
    for lane in sorted(used_lanes):
        if _runtime_lane_items(catalog, lane, "capabilities"):
            continue
        unavailable = _runtime_lane_items(catalog, lane, "unavailable")
        if unavailable:
            return [_runtime_toolchain_error(feature_dir, lane, unavailable)]
        return [_runtime_scope_error(
            "validation_capability_unresolved",
            "lane={};missingManifestOrBuildScript=true;catalog={}".format(
                lane, catalog.get("catalogDigest", "missing")
            ),
        )]
    return []


def _relative_paths_overlap(first: str, second: str) -> bool:
    left = str(first or ".").replace("\\", "/").strip("/") or "."
    right = str(second or ".").replace("\\", "/").strip("/") or "."
    return (
        left == "."
        or right == "."
        or left == right
        or left.startswith(right + "/")
        or right.startswith(left + "/")
    )


def _preferred_runtime_capability(
    capabilities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not capabilities:
        return None
    priority = {"build": 0, "compile": 1, "typecheck": 2}
    return sorted(
        capabilities,
        key=lambda item: (
            priority.get(str(item.get("kind")), 99),
            str(item.get("source", "")),
            str(item.get("capabilityId", "")),
        ),
    )[0]


def _task_roots_for_repository(
    tasks: list[dict[str, Any]],
    repository_name: str,
    repository_count: int,
) -> list[str]:
    roots: list[str] = []
    for task in tasks:
        for key, value in task_workspace_roots(task).items():
            if key == repository_name or (key == "default" and repository_count == 1):
                normalized = str(value or ".").replace("\\", "/").strip("/") or "."
                if normalized not in roots:
                    roots.append(normalized)
    return roots


def _select_module_runtime_capabilities(
    module: dict[str, Any],
    capabilities: list[dict[str, Any]],
    task_roots: list[str],
) -> list[dict[str, Any]]:
    if not capabilities or not task_roots:
        return []
    module_root = str(module.get("relativeRoot", ".") or ".").strip("/") or "."
    if not any(_relative_paths_overlap(module_root, root) for root in task_roots):
        return []
    matching = [
        item for item in capabilities
        if any(_relative_paths_overlap(str(item.get("cwd", ".")), root) for root in task_roots)
    ]
    at_module_root = [
        item for item in matching
        if (str(item.get("cwd", ".")).strip("/") or ".") == module_root
    ]
    if at_module_root:
        preferred = _preferred_runtime_capability(at_module_root)
        return [preferred] if preferred is not None else []
    selected: list[dict[str, Any]] = []
    by_cwd: dict[str, list[dict[str, Any]]] = {}
    for item in matching:
        by_cwd.setdefault(str(item.get("cwd", ".")), []).append(item)
    for cwd in sorted(by_cwd):
        preferred = _preferred_runtime_capability(by_cwd[cwd])
        if preferred is not None:
            selected.append(preferred)
    return selected


def _runtime_items_for_lane_tasks(
    items: list[dict[str, Any]],
    modules: dict[str, dict[str, Any]],
    repositories: dict[str, str],
    lane_tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in items:
        module = modules.get(str(item.get("moduleId", "")), {})
        repository_name = repositories.get(str(module.get("repositoryId")), "")
        task_roots = _task_roots_for_repository(
            lane_tasks, repository_name, len(repositories)
        )
        module_root = str(module.get("relativeRoot", ".") or ".").strip("/") or "."
        if not any(_relative_paths_overlap(module_root, root) for root in task_roots):
            continue
        if any(_relative_paths_overlap(str(item.get("cwd", ".")), root) for root in task_roots):
            selected.append(item)
    return selected


def _apply_runtime_validation_profiles(
    feature_dir: Path,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Project manifest-derived capabilities into Runtime-owned batch profiles."""

    context_path = feature_dir / ".runtime" / "RUN_CONTEXT.json"
    if not context_path.is_file():
        return []
    try:
        run_context = load_run_context(feature_dir.parents[2], feature_dir.name)
        catalog = load_validation_capabilities(
            feature_dir, run_context.get("contextDigest")
        )
    except ValueError as exc:
        return [_runtime_scope_error("SCOPE_UNRESOLVED", str(exc))]
    data["runContextDigest"] = run_context.get("contextDigest")

    repositories = {
        str(item.get("repositoryId")): Path(str(item.get("root"))).name
        for item in run_context.get("repositories", [])
        if isinstance(item, dict)
    }
    capabilities = [
        item for item in catalog.get("capabilities", []) if isinstance(item, dict)
    ]
    modules = {
        str(item.get("moduleId")): item
        for item in run_context.get("modules", [])
        if isinstance(item, dict)
    }
    used_lanes = {
        task_execution_lane(task) for task in _tasks(data) if isinstance(task, dict)
    }
    profiles: dict[str, dict[str, Any]] = {}
    for lane in sorted(used_lanes):
        eligible = _runtime_lane_items(catalog, lane, "capabilities")
        selected: list[dict[str, Any]] = []
        by_module: dict[str, list[dict[str, Any]]] = {}
        for capability in eligible:
            by_module.setdefault(str(capability.get("moduleId", "")), []).append(capability)
        lane_tasks = [task for task in _tasks(data) if task_execution_lane(task) == lane]
        for module_id, module_capabilities in sorted(by_module.items()):
            module = modules.get(module_id, {})
            repository_name = repositories.get(str(module.get("repositoryId")), "")
            task_roots = _task_roots_for_repository(
                lane_tasks, repository_name, len(repositories)
            )
            selected.extend(_select_module_runtime_capabilities(
                module, module_capabilities, task_roots
            ))
        if not selected:
            unavailable = _runtime_items_for_lane_tasks(
                _runtime_lane_items(catalog, lane, "unavailable"),
                modules,
                repositories,
                lane_tasks,
            )
            if unavailable:
                return [_runtime_toolchain_error(feature_dir, lane, unavailable)]
            workspace_rows = sorted({
                "{}={}".format(key, value)
                for task in lane_tasks
                for key, value in task_workspace_roots(task).items()
            })
            candidate_rows = sorted({
                "{}:{}".format(item.get("moduleId"), item.get("cwd", "."))
                for item in eligible
            })
            if eligible:
                return [{
                    "reason": "validation_capability_workspace_unresolved",
                    "detail": "lane={};workspaceRoots={};candidates={}".format(
                        lane,
                        ",".join(workspace_rows) or "none",
                        ",".join(candidate_rows) or "none",
                    ),
                    "repairTarget": "task_group",
                    "repairable": True,
                    "retryable": True,
                    "requiredAction": "repair_task_workspace_roots",
                    "repairSuggestion": "使用真实代码工作区重新 prepare/rebuild Draft，使 lane 任务的 workspaceRoots 与候选 manifest 目录相交。",
                }]
            module_rows = ",".join(
                "{}={}".format(item.get("moduleId"), item.get("root"))
                for item in run_context.get("modules", [])
                if isinstance(item, dict)
            )
            return [_runtime_scope_error(
                "validation_capability_unresolved",
                "lane={};modules={};catalog={}".format(
                    lane, module_rows or "none", catalog.get("catalogDigest", "missing")
                ),
            )]
        named_repositories = {
            key
            for task in lane_tasks
            for key in task_workspace_roots(task)
            if key != "default"
        }
        commands = []
        unique_selected = {
            str(item.get("capabilityId")): item for item in selected
            if item.get("capabilityId")
        }
        for capability in sorted(
            unique_selected.values(),
            key=lambda item: (
                repositories.get(str(item.get("repositoryId")), ""),
                str(item.get("cwd", ".")),
                str(item.get("capabilityId", "")),
            ),
        ):
            repository_name = repositories.get(str(capability.get("repositoryId")))
            command = {
                "argv": copy.deepcopy(capability.get("argv", [])),
                "cwd": capability.get("cwd", "."),
                "kind": "compile",
                "required": True,
                "capabilityId": capability.get("capabilityId"),
            }
            if repository_name in named_repositories:
                command["repo"] = repository_name
            commands.append(command)
        profiles[lane] = {"mode": "commands", "commands": commands}
    data["batchValidationProfiles"] = profiles
    return []


def _runtime_plan_contract_errors(
    feature_dir: Path,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply the RunContext/capability contract when this Feature has one."""

    context_path = feature_dir / ".runtime" / "RUN_CONTEXT.json"
    if not context_path.is_file():
        return []
    workspace = feature_dir.parents[2]
    try:
        run_context = load_run_context(workspace, feature_dir.name)
        catalog = load_validation_capabilities(
            feature_dir, run_context.get("contextDigest")
        )
    except ValueError as exc:
        return [_runtime_scope_error("SCOPE_UNRESOLVED", str(exc))]

    module_prefixes = {
        str(item.get("relativeRoot", ".") or ".").strip("/") or "."
        for item in run_context.get("modules", [])
        if isinstance(item, dict)
    }
    errors: list[dict[str, Any]] = []
    for task in _tasks(data):
        task_id = str(task.get("id", "task"))
        task_prefixes = {
            str(value or ".").replace("\\", "/").strip("/") or "."
            for value in task_workspace_roots(task).values()
        }
        for expected_file in task.get("expectedFiles", []):
            if not isinstance(expected_file, str):
                continue
            normalized = expected_file.replace("\\", "/").lstrip("./")
            if not any(
                prefix == "." or normalized == prefix or normalized.startswith(prefix + "/")
                for prefix in module_prefixes
            ):
                errors.append({
                    "reason": f"{task_id}.expected_file_outside_module_root",
                    "detail": f"path={expected_file};moduleRoots={','.join(sorted(module_prefixes))}",
                    "field": "expectedFiles",
                    "repairTarget": "task_detail",
                })
            if task_prefixes and not any(
                prefix == "." or normalized == prefix or normalized.startswith(prefix + "/")
                for prefix in task_prefixes
            ):
                errors.append({
                    "reason": f"{task_id}.expected_file_outside_task_workspace_root",
                    "detail": f"path={expected_file};workspaceRoots={','.join(sorted(task_prefixes))}",
                    "field": "expectedFiles",
                    "repairTarget": "task_detail",
                })
        for index, command in enumerate(task.get("validationCommands", []), start=1):
            if not isinstance(command, dict) or command.get("kind") not in FRONTEND_COMPILE_VALIDATION_KINDS:
                continue
            for reason in validation_capability_command_errors(
                catalog, command, f"{task_id}.validationCommands[{index - 1}]"
            ):
                errors.append({"reason": reason, "field": "validationCommands", "repairTarget": "task_detail"})

    root, _ = _project_batches(data)
    profiles = root.get("batchValidationProfiles")
    if isinstance(profiles, dict):
        for lane, profile in profiles.items():
            commands = profile.get("commands", []) if isinstance(profile, dict) else []
            for index, command in enumerate(commands):
                if not isinstance(command, dict) or command.get("required") is not True:
                    continue
                for reason in validation_capability_command_errors(
                    catalog, command, f"batchValidationProfiles.{lane}.commands[{index}]"
                ):
                    errors.append({"reason": reason, "field": "batchValidationProfiles", "repairTarget": "task_group"})
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
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=[{"reason": error} for error in scope_errors],
        ))
    persistent_lock_errors = _ensure_persistent_design_contract_lock(
        feature_dir,
        feature,
        design_contract,
        revision_confirmed=args.design_revision_confirmed is True,
        reason=args.reason or "",
    )
    if persistent_lock_errors:
        return render_result(WriterResult(ok=False, path=group_file, errors=persistent_lock_errors))
    data["implementationScope"] = implementation_scope
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
    persistent_lock_errors = _ensure_persistent_design_contract_lock(
        feature_dir,
        feature,
        design_contract,
        revision_confirmed=False,
    )
    if persistent_lock_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=persistent_lock_errors,
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
        "designContract": design_contract_snapshot(design_contract),
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
) -> WriterResult:
    lock, data = _load_draft_bundle(workspace, feature)
    if lock.get("status") == "finalized":
        return fail("task_draft_finalized", path=_draft_plan_path(workspace, feature))
    group_data = _draft_group_data(lock, feature)
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
    write_result = _write_draft_bundle(workspace, feature, data, lock)
    remaining_errors: list[dict[str, Any]] = []
    if len(ready) == len(ordered_ids):
        remaining_errors = _task_set_preflight_errors(
            _path(workspace, feature).parent,
            data,
            group_data,
            code_workspaces,
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


def _draft_preflight(
    workspace: Path,
    feature: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    lock, data = _load_draft_bundle(workspace, feature)
    group_data = _draft_group_data(lock, feature)
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
    return lock, data, group_data, _task_set_preflight_errors(
        _path(workspace, feature).parent,
        data,
        group_data,
        code_workspaces,
    )


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
    _draft_group_data(lock, feature)
    return render_result(WriterResult(
        ok=True,
        path=_draft_plan_path(workspace, feature),
        data={"draft": _draft_summary(lock, data)},
    ))


def _cmd_diagnose_plan_repair(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    formal_root, formal_batches, formal_load_errors = _load_raw_formal_bundle(workspace, feature)
    formal_validation_errors: list[str] = []
    if formal_root is not None and not formal_load_errors:
        formal_validation_errors = validate_plan_bundle_data(formal_root, formal_batches)
        stored_digest = formal_root.get("taskSetDigest")
        if (
            stored_digest is not None
            and stored_digest != task_set_digest(formal_root, formal_batches)
            and "task_set_digest_mismatch" not in formal_validation_errors
        ):
            formal_validation_errors.append("task_set_digest_mismatch")
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
    try:
        draft_lock, draft_data = _load_draft_bundle(workspace, feature)
        draft_available = True
        draft_status = str(draft_lock.get("status"))
        draft_summary = _draft_summary(draft_lock, draft_data)
        design_lock_errors = _draft_design_contract_errors(
            _path(workspace, feature).parent,
            draft_lock,
        )
        draft_valid = not design_lock_errors
        if design_lock_errors:
            first = design_lock_errors[0]
            draft_error = str(first.get("reason"))
            if first.get("detail"):
                draft_error += f":{first['detail']}"
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

    if draft_error == "confirmed_design_changed_after_draft_created" or (
        isinstance(draft_error, str)
        and draft_error.startswith("confirmed_design_changed_after_draft_created:")
    ):
        recommended = "design_revision_required"
    elif not draft_available or not draft_valid:
        recommended = "full_rebuild_required"
    elif draft_status != "finalized":
        recommended = "continue_draft_repair"
    elif execution_blockers:
        recommended = "plan_revision_required"
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
                "executionStarted": bool(execution_blockers),
                "executionBlockers": execution_blockers,
                "recommendedCommand": recommended,
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
    _draft_group_data(lock, feature)
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
        persistent_lock_errors = _ensure_persistent_design_contract_lock(
            _path(workspace, feature).parent,
            feature,
            design_contract,
            revision_confirmed=True,
            reason=reason,
        )
        if persistent_lock_errors:
            return render_result(WriterResult(
                ok=False,
                path=_draft_plan_path(workspace, feature),
                errors=persistent_lock_errors,
            ))
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


def _cmd_rebuild_task_draft(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    if _path(workspace, feature).is_file():
        return render_result(fail("formal_plan_already_exists", path=_path(workspace, feature)))
    old_lock, old_data = _load_draft_bundle(workspace, feature)
    design_lock_errors = _draft_design_contract_errors(_path(workspace, feature).parent, old_lock)
    if design_lock_errors:
        return render_result(WriterResult(
            ok=False,
            path=_draft_plan_path(workspace, feature),
            errors=design_lock_errors,
        ))
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
            and _task_group_projection(old_task) == _task_group_projection(group)
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
    result = _write_draft_bundle(workspace, feature, data, lock)
    return render_result(with_result_data(
        result,
        preservedTaskIds=preserved,
        resetTaskIds=reset,
        draft=_draft_summary(lock, data),
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


def _scope_result_data(
    feature_dir: Path,
    task_items: list[dict[str, Any]],
) -> dict[str, Any]:
    report = _feature_scope_report(feature_dir, task_items)
    return {"scope": report} if report else {}


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
    warnings: list[dict[str, Any]] = []
    errors = _task_set_preflight_errors(
        feature_dir,
        data,
        group_data,
        args.code_workspace,
        warnings=warnings,
    )
    return render_result(WriterResult(
        ok=not errors,
        path=_path(workspace, feature),
        errors=errors,
        data={
            "grouping": _task_group_summary(group_data),
            "preflight": _task_set_summary(data),
            **({"warnings": warnings} if warnings else {}),
            **_scope_result_data(feature_dir, _tasks(data)),
        } if not errors else ({"warnings": warnings} if warnings else {}),
    ))


def _cmd_preflight_task_groups(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    group_data = _load_task_group_file(Path(args.group_file).resolve(), feature)
    warnings: list[dict[str, Any]] = []
    errors = _task_group_preflight_errors(
        _path(workspace, feature).parent,
        group_data,
        warnings=warnings,
    )
    return render_result(WriterResult(
        ok=not errors,
        path=Path(args.group_file).resolve(),
        errors=errors,
        data={
            "grouping": _task_group_summary(group_data),
            "validation": _task_group_validation_report(group_data, errors),
            **({"warnings": warnings} if warnings else {}),
            **_scope_result_data(_path(workspace, feature).parent, _task_groups(group_data)),
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
        lane_warnings = [{
            "reason": "backend_task_after_frontend",
            "detail": f"task={task_id}",
            "severity": "warning",
            "repairTarget": "task_group",
        }]
    else:
        lane_warnings = []
    _tasks(data).append(task)
    warnings = list(lane_warnings)
    granularity_errors = _partition_preflight(
        validate_plan_task_granularity_item(task, task_id=task_id),
        warnings,
    )
    if granularity_errors:
        return render_result(WriterResult(ok=False, path=_path(workspace, feature), errors=granularity_errors))
    structure_errors = _structure_errors(data)
    if structure_errors:
        return render_result(WriterResult(ok=False, path=_path(workspace, feature), errors=[{"reason": error} for error in structure_errors]))
    result = _write(workspace, feature, data)
    return render_result(with_result_data(result, warnings=warnings) if warnings else result)


def _cmd_finalize_task_set(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    errors = _task_set_validation_errors(data)
    if errors:
        return render_result(WriterResult(ok=False, path=_path(workspace, feature), errors=errors))
    missing, coverage_errors = _scoped_scenario_coverage(
        _path(workspace, feature).parent, _tasks(data)
    )
    if coverage_errors:
        return render_result(
            WriterResult(ok=False, path=_path(workspace, feature), errors=coverage_errors)
        )
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
                        "groupOwnedFieldsRequire": "edit task-groups.json then rebuild-task-draft",
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
                    "requiredTaskFields": [
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
                    "requiredTaskGroupFields": [
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
                    "exampleOnlyTaskFields": ["matrixExceptionExample"],
                    "exampleOnlyTaskGroupFields": [
                        "externalDependencyExample",
                        "matrixExceptionExample",
                        "uiRequiredExample",
                    ],
                    "groupOwnedTaskFields": sorted(DRAFT_GROUP_OWNED_FIELDS),
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
                            "sourceOfTruth": "confirmed design.md",
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
                    "batchValidationKinds": ["compile"],
                    "batchValidationOwnership": {
                        "withRunContext": "runtime_owned_and_projected_during_preflight_and_finalize",
                        "withoutRunContext": "plan_owned_legacy_flow",
                        "manualAddWhenRuntimeOwned": "forbidden",
                        "legacyManualCommand": (
                            "add-batch-validation-command --lane <backend|frontend> "
                            "[--repo <workspaceRef>] --command <command> --code-workspace <path>"
                        ),
                    },
                    "terminalRuntimeErrors": {
                        "reasons": [
                            "SCOPE_UNRESOLVED",
                            "validation_capability_unresolved",
                        ],
                        "retryable": False,
                        "requiredAction": "restart_feature_after_runtime_fix",
                        "activation": "any_writer_issue_with_retryable_false",
                    },
                    "validationCoverage": {
                        "rule": "required_commands_cover_all_acceptance_criteria",
                        "compileMayCoverAcceptanceCriteriaByLane": {
                            "backend": False,
                            "frontend": True,
                        },
                        "frontendCompileKinds": sorted(FRONTEND_COMPILE_VALIDATION_KINDS),
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
                        "missingExecutableResult": "block_batch_compile",
                        "runtimeEnvironmentResult": "block_batch_compile",
                        "requiredActions": [
                            "fix_compile_environment_and_retry_batch_compile"
                        ],
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
                        "primaryCapabilitySource": "first_spec_ref_file",
                        "executionLaneSource": "uiRequired",
                        "executionLaneMapping": {
                            "uiRequired_false": "backend",
                            "uiRequired_true": "frontend",
                        },
                        "executionLaneOrder": ["backend", "frontend"],
                        "appendRule": (
                            "same_primary_capability_execution_lane_and_workspace_as_"
                            "immediately_preceding_batch_frontend_route_and_not_full"
                        ),
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
                            "add-project-validation-command",
                            "render-md",
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
                        "designLock": "designContract.sha256",
                        "persistentDesignLock": DESIGN_CONTRACT_LOCK_FILE,
                        "designRevisionConfirmation": "prepare-task-draft --design-revision-confirmed --reason <reason>",
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
                        "requiredValidationByLane": {
                            "backend": "one_complete_required_behavior_command",
                            "frontend": "one_complete_required_behavior_or_matching_compile_command",
                        },
                    },
                    "granularity": {
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
                        "softLimitSeverity": "warning",
                        "hardLimitSeverity": "blocker",
                    },
                    "implementationScope": {
                        "path": "IMPLEMENTATION_SCOPE.json",
                        "writer": "hooks/plan_scope.py set-partition --body-stdin",
                        "partitionFields": sorted(
                            field
                            for included, deferred, _ in SCOPE_KINDS.values()
                            for field in (included, deferred)
                        ),
                        "undeclaredMeans": "every_id_is_included",
                        "deferredMeans": "reported_not_planned_no_task_required",
                    },
                    "resultSeverities": {
                        "errors": "blocker",
                        "warnings": "advisory_only_stage_continues",
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
                            "[--repo <workspaceRef>] --command <command> "
                            "--code-workspace <path>"
                        ),
                        "requiredFields": ["argv", "cwd", "kind", "required"],
                        "requiredPerUsedWorkspaceInLane": "commands_mode_only",
                        "repoRequiredWhenLaneUsesMultipleWorkspaces": True,
                        "defaultCwd": "declared_workspace_root",
                    },
                    "batchValidationMode": {
                        "mode": "commands",
                        "requiredGate": "one required compile command per used lane and workspace",
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


def _cmd_add_batch_validation_command(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    if (_path(workspace, feature).parent / ".runtime" / "RUN_CONTEXT.json").is_file():
        return render_result(WriterResult(
            ok=False,
            path=_path(workspace, feature),
            errors=[_runtime_scope_error(
                "batch_validation_profile_runtime_owned",
                "Plan 不直接写批次验证命令。若 preflight/finalize 报 Runtime 错误，"
                "停止当前 Plan，修正运行上下文后新开 Feature 会话；不得重试、--force、"
                "编辑 .runtime 或删除/重建 Draft。",
            )],
        ))
    data = _load(workspace, feature)
    lane_workspace_contracts = {
        _batch_workspace_contract(task)
        for task in _tasks(data)
        if task_execution_lane(task) == args.lane
    }
    if any(len(contract) != 1 for contract in lane_workspace_contracts):
        return render_result(fail(
            "batch_validation_task_workspace_invalid",
            args.lane,
            path=_path(workspace, feature),
        ))
    if not lane_workspace_contracts:
        return render_result(fail(
            "batch_validation_lane_unused",
            args.lane,
            path=_path(workspace, feature),
        ))
    contracts_by_repository = {
        contract[0][0]: contract for contract in lane_workspace_contracts
    }
    if len(contracts_by_repository) != len(lane_workspace_contracts):
        return render_result(fail(
            "batch_validation_repository_workspace_ambiguous",
            args.lane,
            path=_path(workspace, feature),
        ))
    if len(lane_workspace_contracts) > 1 and not args.repo:
        return render_result(fail(
            "batch_validation_repository_required",
            args.lane,
            path=_path(workspace, feature),
        ))
    selected_repository = args.repo
    if selected_repository is None:
        selected_repository = next(iter(contracts_by_repository))
    selected_contract = contracts_by_repository.get(selected_repository)
    if selected_contract is None:
        return render_result(fail(
            "batch_validation_repository_unknown",
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
        "kind": args.kind,
        "required": not args.optional,
        **({"repo": command_repository} if command_repository else {}),
    }
    if workspace_preflight_required:
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
            compile_only=True,
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
    allow_task_set_digest_mismatch: bool = False,
) -> WriterResult:
    """Internal task-runner API; public CLI cannot set a task to done."""

    with _plan_lock(workspace, feature):
        data = _load(
            workspace,
            feature,
            allow_task_set_digest_mismatch=allow_task_set_digest_mismatch,
        )
        task = _find_task(data, task_id)
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
    allow_task_set_digest_mismatch: bool = False,
) -> WriterResult:
    """Bind implementation evidence without running or completing task validation."""

    with _plan_lock(workspace, feature):
        data = _load(
            workspace,
            feature,
            allow_task_set_digest_mismatch=allow_task_set_digest_mismatch,
        )
        if not defer_to_test_stages_enabled(data):
            return fail("defer_to_test_stages_not_enabled", task_id, path=_path(workspace, feature))
        task = _find_task(data, task_id)
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

        batch_compile = batch_plan.get("batchCompile")
        batch_compile = batch_compile if isinstance(batch_compile, dict) else None
        if batch_compile is not None and batch_compile.get("status") == "repairing":
            if batch_compile.get("repairTaskId") != task_id:
                return fail(
                    "batch_compile_repair_task_mismatch",
                    task_id,
                    path=_path(workspace, feature),
                )
            last_failure = {
                field: copy.deepcopy(batch_compile.get(field))
                for field in (
                    "commandId",
                    "output",
                    "failureCategory",
                    "diagnosticPaths",
                    "repairOwnerTaskIds",
                    "requestedCodeWorkspaces",
                    "workspaceSnapshotSha256",
                    "implementationEvidenceByTask",
                    "implementationRevisionByTask",
                )
            }
            batch_plan["batchCompile"] = {
                "status": "pending",
                "commandId": None,
                "output": None,
                "failureCategory": None,
                "diagnosticPaths": [],
                "repairOwnerTaskIds": [],
                "repairTaskId": None,
                "repairAttempts": int(batch_compile.get("repairAttempts", 0)),
                "maxRepairAttempts": BATCH_COMPILE_MAX_REPAIR_ATTEMPTS,
                "requestedCodeWorkspaces": [],
                "workspaceSnapshotSha256": None,
                "implementationEvidenceByTask": {},
                "implementationRevisionByTask": {},
                "lastFailure": last_failure,
            }
        elif all_implemented and batch_compile is None:
            batch_plan["batchCompile"] = {
                "status": "pending",
                "commandId": None,
                "output": None,
                "failureCategory": None,
                "diagnosticPaths": [],
                "repairOwnerTaskIds": [],
                "repairTaskId": None,
                "repairAttempts": 0,
                "maxRepairAttempts": BATCH_COMPILE_MAX_REPAIR_ATTEMPTS,
                "requestedCodeWorkspaces": [],
                "workspaceSnapshotSha256": None,
                "implementationEvidenceByTask": {},
                "implementationRevisionByTask": {},
            }

        data["status"] = "in_progress"
        data["activeBatchId"] = batch_id

        result = _write(workspace, feature, data)
        if not result.ok:
            return result
        if all_implemented:
            return with_result_data(result, batchCompile={
                "requiredAction": "run_batch_compile",
                "activeBatchId": batch_id,
                "taskIds": [str(item.get("id")) for item in batch_tasks],
                "status": "ready",
            })

        return result


def update_batch_compile_status(
    workspace: Path,
    feature: str,
    batch_id: str,
    compile_result: dict[str, Any],
    *,
    allow_task_set_digest_mismatch: bool = False,
) -> WriterResult:
    """
    更新批次编译状态。

    compile_result: {
        "compileStatus": "passed" | "failed",
        "commandId": str,
        "output": str (失败时),
        "failureCategory": str (失败时)
    }
    """
    with _plan_lock(workspace, feature):
        data = _load(
            workspace,
            feature,
            allow_task_set_digest_mismatch=allow_task_set_digest_mismatch,
        )
        if not defer_to_test_stages_enabled(data):
            return fail("defer_to_test_stages_not_enabled", batch_id, path=_path(workspace, feature))

        batch_plans = data.get("_batchPlans")
        batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
        if not isinstance(batch_plan, dict):
            return fail("batch_not_found", batch_id, path=_path(workspace, feature))

        batch_compile = batch_plan.get("batchCompile")
        if not isinstance(batch_compile, dict):
            return fail("batch_compile_not_initialized", batch_id, path=_path(workspace, feature))

        compile_status = compile_result.get("compileStatus")
        if compile_status not in {"passed", "failed"}:
            return fail("invalid_compile_status", compile_status, path=_path(workspace, feature))

        current_status = batch_compile.get("status")
        if current_status == "passed" and compile_status == "passed":
            return _write(workspace, feature, data)
        if current_status != "pending":
            return fail(
                "batch_compile_result_requires_pending",
                f"batch={batch_id};status={current_status}",
                path=_path(workspace, feature),
            )

        command_id = compile_result.get("commandId")
        batch_validation = batch_plan.get("batchValidation")
        commands = batch_validation.get("commands", []) if isinstance(batch_validation, dict) else []
        if not any(
            isinstance(command, dict)
            and command.get("kind") == "compile"
            and command.get("required") is True
            and command.get("id") == command_id
            for command in commands
        ):
            return fail("batch_compile_command_invalid", command_id, path=_path(workspace, feature))

        batch_compile["status"] = compile_status
        batch_compile["commandId"] = command_id
        batch_compile["repairAttempts"] = int(batch_compile.get("repairAttempts", 0))
        batch_compile["maxRepairAttempts"] = BATCH_COMPILE_MAX_REPAIR_ATTEMPTS
        batch_compile["repairTaskId"] = None

        if compile_status == "failed":
            batch_compile["output"] = compile_result.get("output", "")
            batch_compile["failureCategory"] = compile_result.get("failureCategory", "")
            batch_compile["diagnosticPaths"] = list(compile_result.get("diagnosticPaths", []))
            batch_compile["repairOwnerTaskIds"] = list(
                compile_result.get("repairOwnerTaskIds", [])
            )
            batch_compile["requestedCodeWorkspaces"] = list(
                compile_result.get("requestedCodeWorkspaces", [])
            )
            batch_compile["workspaceSnapshotSha256"] = compile_result.get(
                "workspaceSnapshotSha256"
            )
            batch_compile["implementationEvidenceByTask"] = dict(
                compile_result.get("implementationEvidenceByTask", {})
            )
            batch_compile["implementationRevisionByTask"] = dict(
                compile_result.get("implementationRevisionByTask", {})
            )
        else:
            batch_compile["output"] = None
            batch_compile["failureCategory"] = None
            batch_compile["diagnosticPaths"] = []
            batch_compile["repairOwnerTaskIds"] = []
            batch_compile["requestedCodeWorkspaces"] = list(
                compile_result.get("requestedCodeWorkspaces", [])
            )
            batch_compile["workspaceSnapshotSha256"] = compile_result.get(
                "workspaceSnapshotSha256"
            )
            batch_compile["implementationEvidenceByTask"] = dict(
                compile_result.get("implementationEvidenceByTask", {})
            )
            batch_compile["implementationRevisionByTask"] = dict(
                compile_result.get("implementationRevisionByTask", {})
            )

        return _write(workspace, feature, data)


def reset_batch_compile_for_revalidation(
    workspace: Path,
    feature: str,
    batch_id: str,
    *,
    allow_task_set_digest_mismatch: bool = False,
) -> WriterResult:
    """Reset a passed batch compile gate before running a fresh compile."""

    with _plan_lock(workspace, feature):
        data = _load(
            workspace,
            feature,
            allow_task_set_digest_mismatch=allow_task_set_digest_mismatch,
        )
        if not defer_to_test_stages_enabled(data):
            return fail("defer_to_test_stages_not_enabled", batch_id, path=_path(workspace, feature))

        batch_plans = data.get("_batchPlans")
        batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
        if not isinstance(batch_plan, dict):
            return fail("batch_not_found", batch_id, path=_path(workspace, feature))

        batch_compile = batch_plan.get("batchCompile")
        if not isinstance(batch_compile, dict):
            return fail("batch_compile_not_initialized", batch_id, path=_path(workspace, feature))
        if batch_compile.get("status") != "passed":
            return fail(
                "batch_compile_revalidation_requires_passed",
                f"batch={batch_id};status={batch_compile.get('status')}",
                path=_path(workspace, feature),
            )

        batch_compile.update(
            {
                "status": "pending",
                "commandId": None,
                "output": None,
                "failureCategory": None,
                "diagnosticPaths": [],
                "repairOwnerTaskIds": [],
                "repairTaskId": None,
                "repairAttempts": 0,
                "maxRepairAttempts": BATCH_COMPILE_MAX_REPAIR_ATTEMPTS,
                "requestedCodeWorkspaces": [],
                "workspaceSnapshotSha256": None,
                "implementationEvidenceByTask": {},
                "implementationRevisionByTask": {},
            }
        )
        return _write(workspace, feature, data)


def begin_batch_compile_repair(
    workspace: Path,
    feature: str,
    batch_id: str,
    task_id: str,
    *,
    allow_task_set_digest_mismatch: bool = False,
) -> WriterResult:
    """Reserve one model repair attempt and move the compile gate to repairing."""

    with _plan_lock(workspace, feature):
        data = _load(
            workspace,
            feature,
            allow_task_set_digest_mismatch=allow_task_set_digest_mismatch,
        )
        if not defer_to_test_stages_enabled(data):
            return fail("defer_to_test_stages_not_enabled", batch_id, path=_path(workspace, feature))
        batch_plans = data.get("_batchPlans")
        batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
        if not isinstance(batch_plan, dict):
            return fail("batch_not_found", batch_id, path=_path(workspace, feature))
        batch_compile = batch_plan.get("batchCompile")
        if not isinstance(batch_compile, dict) or batch_compile.get("status") != "failed":
            return fail("batch_compile_repair_requires_failed", batch_id, path=_path(workspace, feature))
        owner_ids = batch_compile.get("repairOwnerTaskIds")
        owner_ids = owner_ids if isinstance(owner_ids, list) else []
        if task_id not in owner_ids:
            return fail(
                "batch_compile_repair_owner_mismatch",
                f"task={task_id};allowed={','.join(str(item) for item in owner_ids)}",
                path=_path(workspace, feature),
            )
        attempts = int(batch_compile.get("repairAttempts", 0))
        if attempts >= BATCH_COMPILE_MAX_REPAIR_ATTEMPTS:
            return fail(
                "batch_compile_repair_attempts_exhausted",
                f"attempts={attempts};max={BATCH_COMPILE_MAX_REPAIR_ATTEMPTS}",
                path=_path(workspace, feature),
            )
        task = _find_task(data, task_id)
        if normalize_status(task.get("status")) not in {"implemented", "in_progress"}:
            return fail("batch_compile_repair_task_not_startable", task_id, path=_path(workspace, feature))

        batch_compile["status"] = "repairing"
        batch_compile["repairAttempts"] = attempts + 1
        batch_compile["maxRepairAttempts"] = BATCH_COMPILE_MAX_REPAIR_ATTEMPTS
        batch_compile["repairTaskId"] = task_id
        batch_compile["repairStartedAt"] = _utc_now()
        data["status"] = "in_progress"
        data["activeBatchId"] = batch_id
        return _write(workspace, feature, data)


def _build_compile_batch_handoff(
    workspace: Path,
    feature: str,
    batch_id: str,
    batch_tasks: list[dict[str, Any]],
    batch_compile: dict[str, Any],
    next_entry: dict[str, Any],
) -> dict[str, Any]:
    next_batch_id = str(next_entry.get("id"))
    user_message = (
        f"当前批次 {batch_id} 已通过生产代码编译门禁。"
        f"请打开新的对话继续执行 {next_batch_id}。"
    )
    return {
        "version": 1,
        "featureId": feature,
        "completedBatchId": batch_id,
        "nextBatchId": next_batch_id,
        "completedTaskIds": [str(task.get("id")) for task in batch_tasks],
        "implementationEvidenceIds": [
            evidence_id
            for task in batch_tasks
            for evidence_id in task.get("implementationEvidenceIds", [])
            if isinstance(evidence_id, str)
        ],
        "batchCompile": {
            field: copy.deepcopy(batch_compile.get(field))
            for field in (
                "status",
                "commandId",
                "workspaceSnapshotSha256",
                "implementationEvidenceByTask",
                "implementationRevisionByTask",
            )
        },
        "nextBatch": {
            "title": str(next_entry.get("title", "")),
            "taskIds": list(next_entry.get("taskIds", [])),
            "specRoots": list(next_entry.get("specRoots", [])),
            "deps": list(next_entry.get("deps", [])),
            "executionLane": next_entry.get("executionLane"),
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


def mark_batch_tasks_done_after_compile(
    workspace: Path,
    feature: str,
    batch_id: str,
    *,
    allow_task_set_digest_mismatch: bool = False,
) -> WriterResult:
    """
    编译通过后，将批次中所有 implemented 状态的任务标记为 done。
    仅在 defer_to_test_stages 策略下使用。
    """
    with _plan_lock(workspace, feature):
        data = _load(
            workspace,
            feature,
            allow_task_set_digest_mismatch=allow_task_set_digest_mismatch,
        )
        if not defer_to_test_stages_enabled(data):
            return fail("defer_to_test_stages_not_enabled", batch_id, path=_path(workspace, feature))

        batch_plans = data.get("_batchPlans")
        batch_plan = batch_plans.get(batch_id) if isinstance(batch_plans, dict) else None
        if not isinstance(batch_plan, dict):
            return fail("batch_not_found", batch_id, path=_path(workspace, feature))

        batch_compile = batch_plan.get("batchCompile")
        if not isinstance(batch_compile, dict) or batch_compile.get("status") != "passed":
            return fail("batch_compile_not_passed", batch_id, path=_path(workspace, feature))
        command_id = batch_compile.get("commandId")
        batch_validation = batch_plan.get("batchValidation")
        commands = batch_validation.get("commands", []) if isinstance(batch_validation, dict) else []
        if not any(
            isinstance(command, dict)
            and command.get("kind") == "compile"
            and command.get("required") is True
            and command.get("id") == command_id
            for command in commands
        ):
            return fail("batch_compile_command_invalid", command_id, path=_path(workspace, feature))

        task_ids = batch_plan.get("taskIds", [])
        if not isinstance(task_ids, list):
            return fail("batch_task_ids_invalid", batch_id, path=_path(workspace, feature))

        tasks = data.get("tasks", [])
        if not isinstance(tasks, list):
            return fail("tasks_not_found", path=_path(workspace, feature))

        updated_count = 0
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = task.get("id")
            if task_id not in task_ids:
                continue
            if normalize_status(task.get("status")) == "implemented":
                task["status"] = "done"
                # 新策略：不使用虚拟 evidence，保留真实 implementation evidence
                # done gate 将直接检查 batchCompile.status == passed
                updated_count += 1

        entries = [entry for entry in data.get("batches", []) if isinstance(entry, dict)]
        ordered_ids = [str(entry.get("id")) for entry in entries]
        try:
            batch_index = ordered_ids.index(batch_id)
        except ValueError:
            return fail("batch_not_found", batch_id, path=_path(workspace, feature))
        next_entry = entries[batch_index + 1] if batch_index + 1 < len(entries) else None
        handoff: dict[str, Any] | None = None
        if isinstance(next_entry, dict):
            next_batch_id = str(next_entry.get("id"))
            data["status"] = "awaiting_next_conversation"
            data["activeBatchId"] = None
            data["nextBatchId"] = next_batch_id
            handoff = _build_compile_batch_handoff(
                workspace,
                feature,
                batch_id,
                [task for task in tasks if task.get("id") in task_ids],
                batch_compile,
                next_entry,
            )

        result = _write(workspace, feature, data)
        if not result.ok:
            return result
        if handoff is None:
            unlink_if_exists(_handoff_path(workspace, feature))
            return result
        handoff_changed = atomic_write_json(_handoff_path(workspace, feature), handoff)
        if handoff_changed and not result.changed:
            result = WriterResult(ok=True, path=result.path, changed=True)
        return with_result_data(result, batchHandoff=handoff)




























def update_task_evidence_only(
    workspace: Path,
    feature: str,
    task_id: str,
    evidence_id: str,
    *,
    allow_task_set_digest_mismatch: bool = False,
) -> WriterResult:
    """仅更新任务的 evidence 记录，不改变 status 和其他状态。用于 repair 模式追加新证据。"""

    with _plan_lock(workspace, feature):
        data = _load(
            workspace,
            feature,
            allow_task_set_digest_mismatch=allow_task_set_digest_mismatch,
        )
        task = _find_task(data, task_id)
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


def activate_batch(
    workspace: Path,
    feature: str,
    batch_id: str,
    *,
    allow_task_set_digest_mismatch: bool = False,
) -> WriterResult:
    with _plan_lock(workspace, feature):
        data = _load(
            workspace,
            feature,
            allow_task_set_digest_mismatch=allow_task_set_digest_mismatch,
        )
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
            unlink_if_exists(_handoff_path(workspace, feature))
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
        "## 任务总览",
        "",
        "| Task ID | 任务 | 执行模式 | 依赖 | 状态 |",
        "| ------- | ---- | -------- | ---- | ---- |",
    ]
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
                f"- 外部资料要求: {_fmt(task.get('sourceRefs'))}",
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
                "sourceRefs": len(task.get("sourceRefs", [])) if isinstance(task.get("sourceRefs"), list) else 0,
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
    parser = JsonArgumentParser(description="Incrementally write plan.json")
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
    prepare_task_draft.add_argument(
        "--design-revision-confirmed",
        action="store_true",
        help="仅在 Design 已重新确认后刷新 Feature 级 Design 锁",
    )
    prepare_task_draft.add_argument("--reason", default="")
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

    diagnose_plan_repair = sub.add_parser("diagnose-plan-repair")
    _common(diagnose_plan_repair)
    diagnose_plan_repair.set_defaults(func=_cmd_diagnose_plan_repair)

    reopen_finalized_draft = sub.add_parser("reopen-finalized-draft")
    _common(reopen_finalized_draft)
    reopen_finalized_draft.add_argument("--reason", required=True)
    reopen_finalized_draft.add_argument("--design-revision-confirmed", action="store_true")
    reopen_finalized_draft.set_defaults(func=_cmd_reopen_finalized_draft)

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
        choices=["compile"],
        default="compile",
    )
    batch_validation.add_argument("--optional", action="store_true")
    batch_validation.set_defaults(func=_cmd_add_batch_validation_command)

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
        return render_result(WriterResult(ok=False, errors=[exc.as_error()]))
    except WriterEncodingError as exc:
        return render_result(fail("plan_writer_encoding_error", str(exc)))
    except Exception as exc:
        return render_result(fail("plan_writer_failed", str(exc)))


if __name__ == "__main__":
    raise SystemExit(main())
