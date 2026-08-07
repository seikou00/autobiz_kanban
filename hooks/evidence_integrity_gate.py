#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integrity and completion gates for Autodev evidence streams."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HOOKS_DIR = ROOT / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from evidence_store import (  # noqa: E402
    EVIDENCE_ID_RE,
    EvidenceStoreError,
    index_path,
    load_index,
    read_records,
    snapshot,
    stream_path,
    validate_record,
)
from evidence_kernel import check_record_artifacts  # noqa: E402
from plan_json import (  # noqa: E402
    blocked_tasks,
    deferred_task_validation_enabled,
    failed_tasks,
    load_and_validate_plan,
    plan_json_path,
    task_contract_sha256,
    batch_validation_terminal,
    task_validation_terminal,
    task_ids,
    tasks,
    unfinished_tasks,
)
from task_run_integrity import task_run_integrity_error  # noqa: E402
from validation_groups import validation_groups_sha256_payload  # noqa: E402


PASS_RESULTS = {"pass", "passed", "success", "ok", "PASS", "PASS_WITH_WARNINGS"}


def _expected_evidence_id(index: int) -> str:
    return f"ev_{index:04d}"


def check_integrity(target_feature_dir: Path, *, require_index: bool = True) -> list[str]:
    errors: list[str] = []
    path = stream_path(target_feature_dir)
    if not path.is_file() or path.stat().st_size <= 0:
        return ["missing_evidence_stream"]

    try:
        records = read_records(path)
    except EvidenceStoreError as exc:
        return [str(exc)]
    if not records:
        errors.append("empty_evidence_stream")

    seen: set[str] = set()
    for line_no, record in enumerate(records, start=1):
        for reason in validate_record(record):
            errors.append(f"line={line_no}:{reason}")
        evidence_id = record.get("evidenceId")
        if isinstance(evidence_id, str):
            if evidence_id in seen:
                errors.append(f"duplicate_evidence_id:{evidence_id}")
            seen.add(evidence_id)
            if evidence_id != _expected_evidence_id(line_no):
                errors.append(f"non_sequential_evidence_id:line={line_no}:id={evidence_id}")
        task_id = record.get("taskId")
        is_batch_validation = (
            record.get("action") in {"batch_validation", "batch_closure"}
            and task_id == "__batch__"
            and isinstance(record.get("batchId"), str)
        )
        if (
            isinstance(task_id, str)
            and task_id
            and task_id != "__project__"
            and not is_batch_validation
            and not task_id.startswith("T")
        ):
            errors.append(f"line={line_no}:invalid_task_id:{task_id}")
        errors.extend(check_record_artifacts(target_feature_dir, record))

    try:
        index = load_index(target_feature_dir)
    except EvidenceStoreError as exc:
        errors.append(str(exc))
        index = None
    if index is None:
        if require_index:
            errors.append(f"missing_evidence_index:{index_path(target_feature_dir)}")
    else:
        snap = snapshot(target_feature_dir)
        if index.get("lineCount") != snap.line_count:
            errors.append("evidence_index_mismatch:lineCount")
        if index.get("lastEvidenceId") != snap.last_evidence_id:
            errors.append("evidence_index_mismatch:lastEvidenceId")
        if index.get("sha256") != snap.sha256:
            errors.append("evidence_stream_rewritten_or_truncated:sha256")
    return errors


def _validation_passed(record: dict[str, Any]) -> bool:
    if record.get("action") not in {"validation", "batch_validation", "project_check"}:
        return False
    validation = record.get("validation")
    if not isinstance(validation, dict):
        return False
    result = validation.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip() in PASS_RESULTS and validation.get("exitCode") == 0
    exit_code = validation.get("exitCode")
    return exit_code == 0


def _validation_deferred(task: dict[str, Any]) -> bool:
    disposition = task.get("validationDisposition")
    return isinstance(disposition, dict) and disposition.get("status") == "deferred"


def _check_deferral_evidence(
    disposition: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    *,
    expected_action: str,
    expected_task_id: str,
    context: str,
) -> list[str]:
    errors: list[str] = []
    evidence_ids = disposition.get("evidenceIds")
    if not isinstance(evidence_ids, list) or not evidence_ids:
        return [f"{context}.deferred_evidence_missing"]
    for evidence_id in evidence_ids:
        record = by_id.get(str(evidence_id))
        validation = record.get("validation") if isinstance(record, dict) else None
        if (
            not isinstance(record, dict)
            or record.get("action") != expected_action
            or record.get("taskId") != expected_task_id
            or not isinstance(validation, dict)
            or validation.get("result") not in {"fail", "blocked"}
        ):
            errors.append(f"{context}.invalid_deferred_evidence:{evidence_id}")
    return errors


def check_plan_evidence_refs(target_feature_dir: Path) -> list[str]:
    errors: list[str] = []
    plan, plan_errors = load_and_validate_plan(plan_json_path(target_feature_dir))
    if plan_errors:
        return [f"plan_json:{error}" for error in plan_errors]
    if plan is None:
        return ["missing_plan_json"]

    records = read_records(stream_path(target_feature_dir))
    known_evidence_ids = {
        evidence_id
        for evidence_id in (record.get("evidenceId") for record in records)
        if isinstance(evidence_id, str)
    }
    known_tasks = task_ids(plan)
    for record in records:
        task_id = record.get("taskId")
        is_project_check = record.get("action") == "project_check" and task_id == "__project__"
        is_batch_validation = (
            record.get("action") in {"batch_validation", "batch_closure"}
            and task_id == "__batch__"
        )
        if (
            isinstance(task_id, str)
            and task_id
            and task_id not in known_tasks
            and not is_project_check
            and not is_batch_validation
        ):
            errors.append(f"unknown_evidence_task_id:{task_id}")
    for task in tasks(plan):
        task_id = str(task.get("id", ""))
        evidence_ids = task.get("evidenceIds")
        if not isinstance(evidence_ids, list):
            continue
        for evidence_id in evidence_ids:
            if not isinstance(evidence_id, str) or not EVIDENCE_ID_RE.match(evidence_id):
                errors.append(f"{task_id}.invalid_evidence_id:{evidence_id}")
            elif evidence_id not in known_evidence_ids:
                errors.append(f"{task_id}.unknown_evidence_id:{evidence_id}")
    project_evidence_ids = plan.get("projectCheckEvidenceIds")
    if isinstance(project_evidence_ids, list):
        records_by_id = {
            str(record.get("evidenceId")): record
            for record in records
            if isinstance(record.get("evidenceId"), str)
        }
        for evidence_id in project_evidence_ids:
            record = records_by_id.get(str(evidence_id))
            if record is None:
                errors.append(f"unknown_project_check_evidence_id:{evidence_id}")
            elif record.get("action") != "project_check" or record.get("taskId") != "__project__":
                errors.append(f"invalid_project_check_evidence_id:{evidence_id}")
    batch_plans = plan.get("_bundleBatches")
    if isinstance(batch_plans, dict):
        records_by_id = {
            str(record.get("evidenceId")): record
            for record in records
            if isinstance(record.get("evidenceId"), str)
        }
        for batch_id, batch in batch_plans.items():
            validation = batch.get("batchValidation") if isinstance(batch, dict) else None
            evidence_ids = validation.get("evidenceIds") if isinstance(validation, dict) else None
            for evidence_id in evidence_ids or []:
                record = records_by_id.get(str(evidence_id))
                if record is None:
                    errors.append(f"{batch_id}.unknown_batch_validation_evidence_id:{evidence_id}")
                elif (
                    record.get("action") not in {"batch_validation", "batch_closure"}
                    or record.get("taskId") != "__batch__"
                    or record.get("batchId") != batch_id
                ):
                    errors.append(f"{batch_id}.invalid_batch_validation_evidence_id:{evidence_id}")
    return errors


def check_code_done(target_feature_dir: Path) -> list[str]:
    errors = check_integrity(target_feature_dir, require_index=True)
    plan_path = plan_json_path(target_feature_dir)
    if (target_feature_dir / "BATCH_HANDOFF.json").exists():
        errors.append("unresolved_batch_handoff")

    if not errors:
        errors.extend(check_plan_evidence_refs(target_feature_dir))

    plan, plan_errors = load_and_validate_plan(plan_path, require_all_done=True)
    if plan_errors:
        errors.extend(f"plan_json:{error}" for error in plan_errors)
        diagnostic_plan, _ = load_and_validate_plan(plan_path)
        if diagnostic_plan is not None:
            blocked = blocked_tasks(diagnostic_plan)
            if blocked:
                errors.append("unresolved_blocker:" + ",".join(blocked))
        return errors
    if plan is None:
        errors.append("missing_plan_json")
        return errors

    unfinished = unfinished_tasks(plan)
    if unfinished:
        errors.append("plan_json_unfinished_tasks:" + ",".join(unfinished))
    failed = failed_tasks(plan)
    if failed:
        errors.append("plan_json_failed_tasks:" + ",".join(failed))
    blocked = blocked_tasks(plan)
    if blocked:
        errors.append("unresolved_blocker:" + ",".join(blocked))

    try:
        records = read_records(stream_path(target_feature_dir))
    except EvidenceStoreError as exc:
        errors.append(str(exc))
        records = ()
    errors.extend(_check_completion(plan, records, feature_dir=target_feature_dir))
    return errors


def _check_completion(
    plan: dict[str, Any],
    records: tuple[dict[str, Any], ...],
    *,
    feature_dir: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    by_id = {
        str(record.get("evidenceId")): record
        for record in records
        if isinstance(record.get("evidenceId"), str)
    }
    deferred = deferred_task_validation_enabled(plan)
    projected_issue_ids = {
        str(issue.get("issueId"))
        for issue in plan.get("deferredValidationIssues", [])
        if isinstance(issue, dict) and isinstance(issue.get("issueId"), str)
    }
    actual_issue_ids: set[str] = set()
    for task in tasks(plan):
        task_id = str(task.get("id", ""))
        validation_deferred = _validation_deferred(task)
        disposition = (
            task.get("validationDisposition") if validation_deferred else None
        )
        if isinstance(disposition, dict):
            if isinstance(disposition.get("issueId"), str):
                actual_issue_ids.add(str(disposition["issueId"]))
            errors.extend(
                _check_deferral_evidence(
                    disposition,
                    by_id,
                    expected_action="validation",
                    expected_task_id=task_id,
                    context=task_id,
                )
            )
        if isinstance(task.get("pendingRevalidation"), dict):
            errors.append(f"{task_id}.pending_batch_revalidation")
        completion_ids = task.get("completionEvidenceIds")
        if not isinstance(completion_ids, list):
            completion_ids = []
        planned_commands = {
            str(command.get("id")): command
            for command in task.get("validationCommands", [])
            if isinstance(command, dict) and isinstance(command.get("id"), str)
        }
        required_command_ids = {
            command_id
            for command_id, command in planned_commands.items()
            if command.get("required") is True
        }
        passed_command_ids: set[str] = set()
        covered_criteria: set[str] = set()
        completion_records: list[dict[str, Any]] = []
        if deferred:
            implementation_ids = task.get("implementationEvidenceIds")
            implementation_ids = implementation_ids if isinstance(implementation_ids, list) else []
            latest_implementation = task.get("latestImplementationEvidenceId")
            if not implementation_ids:
                errors.append(f"{task_id}.implementation_evidence_missing")
            if latest_implementation not in implementation_ids:
                errors.append(f"{task_id}.latest_implementation_evidence_invalid")
            for evidence_id in implementation_ids:
                implementation_record = by_id.get(str(evidence_id))
                if (
                    not isinstance(implementation_record, dict)
                    or implementation_record.get("action") != "implementation"
                    or implementation_record.get("taskId") != task_id
                ):
                    errors.append(f"{task_id}.implementation_evidence_invalid:{evidence_id}")
        for evidence_id in completion_ids:
            record = by_id.get(str(evidence_id))
            if record is None:
                continue
            evidence_task_id = record.get("taskId")
            if evidence_task_id != task_id:
                errors.append(f"{task_id}.evidence_task_mismatch:{evidence_id}:{evidence_task_id}")
                continue
            if not _validation_passed(record):
                errors.append(f"{task_id}.completion_evidence_not_pass:{evidence_id}")
                continue
            if record.get("detailVersion") != 2:
                errors.append(f"{task_id}.completion_evidence_requires_detail_v2:{evidence_id}")
                continue
            completion_records.append(record)
            if deferred:
                task_batch_map = plan.get("_bundleTaskBatches")
                batch_id = (
                    task_batch_map.get(task_id) if isinstance(task_batch_map, dict) else None
                )
                batch = (
                    plan.get("_bundleBatches", {}).get(batch_id)
                    if isinstance(plan.get("_bundleBatches"), dict)
                    else None
                )
                task_validation = batch.get("taskValidation") if isinstance(batch, dict) else None
                if record.get("validationTarget") != "batch_final_snapshot":
                    errors.append(f"{task_id}.validation_target_invalid:{evidence_id}")
                if record.get("batchId") != batch_id:
                    errors.append(f"{task_id}.validation_batch_mismatch:{evidence_id}")
                if (
                    not isinstance(task_validation, dict)
                    or record.get("batchSnapshotSha256")
                    != task_validation.get("batchSnapshotSha256")
                ):
                    errors.append(f"{task_id}.validation_snapshot_mismatch:{evidence_id}")
                if record.get("implementationRevision") != task.get("implementationRevision"):
                    errors.append(f"{task_id}.validation_revision_mismatch:{evidence_id}")
                if record.get("latestImplementationEvidenceId") != task.get("latestImplementationEvidenceId"):
                    errors.append(f"{task_id}.validation_implementation_pointer_mismatch:{evidence_id}")
            validation = record.get("validation")
            if not isinstance(validation, dict):
                continue
            command_id = validation.get("commandId")
            if not isinstance(command_id, str) or command_id not in planned_commands:
                errors.append(f"{task_id}.unplanned_validation_command:{command_id}")
                continue
            planned = planned_commands[command_id]
            for field in ("argv", "cwd", "kind", "required", "repo"):
                if validation.get(field) != planned.get(field):
                    errors.append(f"{task_id}.validation_command_mismatch:{command_id}:{field}")
            passed_command_ids.add(command_id)
            checked = record.get("checkedCriteria")
            if isinstance(checked, list):
                unknown_criteria = sorted(
                    item for item in checked if isinstance(item, str) and item not in _task_acceptance_ids(task)
                )
                if unknown_criteria:
                    errors.append(
                        f"{task_id}.completion_evidence_unknown_criteria:{evidence_id}:"
                        + ",".join(unknown_criteria)
                    )
                covered_criteria.update(item for item in checked if isinstance(item, str))

        if deferred and completion_records:
            latest_implementation = task.get("latestImplementationEvidenceId")
            if isinstance(latest_implementation, str):
                implementation_number = _evidence_number(latest_implementation)
                validation_numbers = [
                    _evidence_number(str(record.get("evidenceId", "")))
                    for record in completion_records
                ]
                if validation_numbers and implementation_number >= min(validation_numbers):
                    errors.append(f"{task_id}.validation_evidence_not_newer_than_implementation")

        if feature_dir is not None:
            if deferred:
                errors.extend(
                    _check_deferred_task_validation_run_state(
                        feature_dir,
                        plan,
                        task,
                        completion_records,
                    )
                )
            else:
                errors.extend(_check_task_run_state(feature_dir, task, completion_records))

        completed_revalidation = task.get("completedRevalidation")
        revalidation_records = [
            record
            for record in completion_records
            if record.get("attemptType") == "batch_revalidation"
        ]
        if isinstance(completed_revalidation, dict):
            expected_triggered = completed_revalidation.get("triggeredByBatchEvidenceIds")
            expected_superseded = completed_revalidation.get("supersedesEvidenceIds")
            expected_completion = completed_revalidation.get("completionEvidenceIds")
            if completed_revalidation.get("attemptType") != "batch_revalidation":
                errors.append(f"{task_id}.completed_revalidation_attempt_type_invalid")
            if expected_completion != completion_ids:
                errors.append(f"{task_id}.completed_revalidation_completion_pointer_mismatch")
            if not isinstance(expected_triggered, list) or not expected_triggered:
                errors.append(f"{task_id}.completed_revalidation_trigger_missing")
                expected_triggered = []
            if not isinstance(expected_superseded, list) or not expected_superseded:
                errors.append(f"{task_id}.completed_revalidation_supersedes_missing")
                expected_superseded = []
            for record in completion_records:
                evidence_id = str(record.get("evidenceId", ""))
                if (
                    record.get("attemptType") != "batch_revalidation"
                    or record.get("triggeredByBatchEvidenceIds") != expected_triggered
                    or record.get("supersedesEvidenceIds") != expected_superseded
                ):
                    errors.append(f"{task_id}.batch_revalidation_evidence_mismatch:{evidence_id}")
            task_batch_map = plan.get("_bundleTaskBatches")
            expected_batch_id = (
                task_batch_map.get(task_id)
                if isinstance(task_batch_map, dict)
                else None
            )
            current_numbers = [_evidence_number(str(evidence_id)) for evidence_id in completion_ids]
            for evidence_id in expected_triggered:
                record = by_id.get(str(evidence_id))
                if (
                    not isinstance(record, dict)
                    or record.get("action") != "batch_validation"
                    or record.get("taskId") != "__batch__"
                    or record.get("batchId") != expected_batch_id
                    or not _validation_passed(record)
                ):
                    errors.append(f"{task_id}.batch_revalidation_trigger_invalid:{evidence_id}")
                elif current_numbers and _evidence_number(str(evidence_id)) >= min(current_numbers):
                    errors.append(f"{task_id}.batch_revalidation_trigger_not_older:{evidence_id}")
            task_history = set(task.get("evidenceIds", [])) if isinstance(task.get("evidenceIds"), list) else set()
            for evidence_id in expected_superseded:
                record = by_id.get(str(evidence_id))
                if (
                    evidence_id not in task_history
                    or not isinstance(record, dict)
                    or record.get("taskId") != task_id
                    or record.get("action") != "validation"
                ):
                    errors.append(f"{task_id}.batch_revalidation_superseded_invalid:{evidence_id}")
                elif current_numbers and _evidence_number(str(evidence_id)) >= min(current_numbers):
                    errors.append(f"{task_id}.batch_revalidation_superseded_not_older:{evidence_id}")
        elif revalidation_records:
            errors.append(f"{task_id}.completed_revalidation_pointer_missing")

        if not validation_deferred:
            for command_id in sorted(required_command_ids - passed_command_ids):
                errors.append(f"{task_id}.missing_required_validation_pass:{command_id}")
            acceptance_ids = _task_acceptance_ids(task)
            missing_criteria = sorted(acceptance_ids - covered_criteria)
            if missing_criteria:
                errors.append(f"{task_id}.missing_acceptance_coverage:" + ",".join(missing_criteria))
    for batch in (plan.get("_bundleBatches") or {}).values():
        if not isinstance(batch, dict):
            continue
        validation = batch.get("batchValidation")
        for issue in (
            validation.get("deferredIssues", []) if isinstance(validation, dict) else []
        ):
            if isinstance(issue, dict) and isinstance(issue.get("issueId"), str):
                actual_issue_ids.add(str(issue["issueId"]))
    project_disposition = plan.get("projectValidationDisposition")
    if isinstance(project_disposition, dict) and isinstance(
        project_disposition.get("issueId"), str
    ):
        actual_issue_ids.add(str(project_disposition["issueId"]))
    if projected_issue_ids != actual_issue_ids:
        errors.append("deferred_validation_issue_projection_mismatch")
    errors.extend(_check_batch_completion(plan, by_id, feature_dir=feature_dir))
    errors.extend(_check_project_completion(plan, by_id))
    return errors


def _deferred_validation_run_integrity_sha256(state: dict[str, Any]) -> str:
    fields = (
        "version",
        "runId",
        "featureId",
        "batchId",
        "taskOrder",
        "taskContractSha256ByTask",
        "requestedCodeWorkspaces",
        "repositories",
        "batchSnapshotSha256",
        "startedAt",
    )
    integrity_data = {field: state.get(field) for field in fields}
    integrity_data["executionPlan"] = validation_groups_sha256_payload(
        state.get("executionGroups", [])
        if isinstance(state.get("executionGroups"), list)
        else []
    )
    content = json.dumps(
        integrity_data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _check_deferred_task_validation_run_state(
    feature_dir: Path,
    plan: dict[str, Any],
    task: dict[str, Any],
    completion_records: list[dict[str, Any]],
) -> list[str]:
    task_id = str(task.get("id", ""))
    run_ids = {
        str(record.get("runId"))
        for record in completion_records
        if isinstance(record.get("runId"), str)
    }
    disposition = task.get("validationDisposition")
    if not run_ids and isinstance(disposition, dict) and isinstance(disposition.get("runId"), str):
        run_ids.add(str(disposition["runId"]))
    if len(run_ids) != 1:
        return [
            f"{task_id}.deferred_validation_run_count_invalid:"
            + ",".join(sorted(run_ids))
        ]
    run_id = next(iter(run_ids))
    task_batches = plan.get("_bundleTaskBatches")
    batch_id = task_batches.get(task_id) if isinstance(task_batches, dict) else None
    if not isinstance(batch_id, str):
        return [f"{task_id}.deferred_validation_batch_missing"]
    path = feature_dir / ".batch-task-validation-runs" / batch_id / f"{run_id}.json"
    if not path.is_file():
        return [f"{task_id}.deferred_validation_run_missing:{run_id}"]
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [f"{task_id}.deferred_validation_run_invalid:{run_id}"]
    if not isinstance(state, dict):
        return [f"{task_id}.deferred_validation_run_invalid:{run_id}"]
    errors: list[str] = []
    if state.get("integritySha256") != _deferred_validation_run_integrity_sha256(state):
        errors.append(f"{task_id}.deferred_validation_run_integrity_mismatch:{run_id}")
    if state.get("status") not in {"done", "failed"}:
        errors.append(f"{task_id}.deferred_validation_run_not_terminal:{run_id}")
    if task_id not in state.get("completedTaskIds", []):
        errors.append(f"{task_id}.deferred_validation_task_not_completed:{run_id}")
    expected_contracts = state.get("taskContractSha256ByTask")
    if (
        not isinstance(expected_contracts, dict)
        or expected_contracts.get(task_id) != task_contract_sha256(task)
    ):
        errors.append(f"{task_id}.deferred_validation_contract_mismatch:{run_id}")
    for record in completion_records:
        if record.get("batchSnapshotSha256") != state.get("batchSnapshotSha256"):
            errors.append(
                f"{task_id}.deferred_validation_evidence_snapshot_mismatch:"
                f"{record.get('evidenceId')}"
            )
    return errors


def _check_batch_completion(
    plan: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    *,
    feature_dir: Path | None,
) -> list[str]:
    errors: list[str] = []
    batch_plans = plan.get("_bundleBatches")
    if not isinstance(batch_plans, dict):
        return ["missing_batch_plan_projection"]
    for batch_id, batch in batch_plans.items():
        if not isinstance(batch, dict):
            continue
        if deferred_task_validation_enabled(plan):
            task_validation = batch.get("taskValidation")
            if not isinstance(task_validation, dict):
                errors.append(f"{batch_id}.task_validation_contract_missing")
            else:
                if not task_validation_terminal(task_validation.get("status")):
                    errors.append(f"{batch_id}.task_validation_not_passed")
                expected_task_ids = [
                    str(task.get("id"))
                    for task in batch.get("tasks", [])
                    if isinstance(task, dict)
                ]
                if task_validation.get("completedTaskIds") != expected_task_ids:
                    errors.append(f"{batch_id}.task_validation_completion_mismatch")
                latest_by_task = task_validation.get("latestPassEvidenceByTask")
                if not isinstance(latest_by_task, dict) or any(
                    latest_by_task.get(task_id)
                    != next(
                        (
                            task.get("completionEvidenceIds")
                            for task in batch.get("tasks", [])
                            if isinstance(task, dict) and task.get("id") == task_id
                        ),
                        None,
                    )
                    for task_id in expected_task_ids
                ):
                    errors.append(f"{batch_id}.task_validation_evidence_projection_mismatch")
                expected_deferred = [
                    str(task.get("id"))
                    for task in batch.get("tasks", [])
                    if isinstance(task, dict) and _validation_deferred(task)
                ]
                if task_validation.get("deferredTaskIds", []) != expected_deferred:
                    errors.append(f"{batch_id}.task_validation_deferred_projection_mismatch")
        validation = batch.get("batchValidation")
        if not isinstance(validation, dict):
            errors.append(f"{batch_id}.batch_validation_contract_missing")
            continue
        if not batch_validation_terminal(validation.get("status")):
            errors.append(f"{batch_id}.batch_validation_not_passed")
        if validation.get("status") == "deferred":
            deferred_issues = validation.get("deferredIssues")
            if not isinstance(deferred_issues, list) or not deferred_issues:
                errors.append(f"{batch_id}.batch_validation_deferred_issue_missing")
                continue
            for issue in deferred_issues:
                if not isinstance(issue, dict):
                    errors.append(f"{batch_id}.batch_validation_deferred_issue_invalid")
                    continue
                errors.extend(
                    _check_deferral_evidence(
                        issue,
                        by_id,
                        expected_action=(
                            "validation"
                            if issue.get("taskId") not in {None, ""}
                            else "batch_validation"
                        ),
                        expected_task_id=(
                            str(issue.get("taskId"))
                            if issue.get("taskId") not in {None, ""}
                            else "__batch__"
                        ),
                        context=f"{batch_id}.batchValidation",
                    )
                )
            continue
        mode = validation.get("mode", "commands" if validation.get("commands") else None)
        latest_ids = validation.get("latestPassEvidenceIds")
        latest_ids = latest_ids if isinstance(latest_ids, list) else []
        task_completion_ids = [
            str(evidence_id)
            for task in batch.get("tasks", [])
            if isinstance(task, dict)
            for evidence_id in task.get("completionEvidenceIds", [])
            if isinstance(evidence_id, str)
        ]
        task_completion_numbers = [_evidence_number(evidence_id) for evidence_id in task_completion_ids]
        if mode == "task_covered":
            coverage_ids = [
                item for item in validation.get("coverageCommandIds", []) if isinstance(item, str)
            ]
            source_by_command: dict[str, str] = {}
            for evidence_id in task_completion_ids:
                record = by_id.get(evidence_id)
                command = record.get("validation") if isinstance(record, dict) else None
                command_id = command.get("commandId") if isinstance(command, dict) else None
                if command_id in coverage_ids:
                    source_by_command[str(command_id)] = evidence_id
            expected_sources = [
                source_by_command[command_id]
                for command_id in coverage_ids
                if command_id in source_by_command
            ]
            if len(expected_sources) != len(coverage_ids):
                errors.append(f"{batch_id}.task_covered_source_evidence_missing")
            if len(latest_ids) != 1:
                errors.append(f"{batch_id}.task_covered_closure_count_invalid")
                continue
            closure_id = str(latest_ids[0])
            closure = by_id.get(closure_id)
            coverage = closure.get("coverage") if isinstance(closure, dict) else None
            if (
                not isinstance(closure, dict)
                or closure.get("action") != "batch_closure"
                or closure.get("taskId") != "__batch__"
                or closure.get("batchId") != batch_id
                or not isinstance(coverage, dict)
                or coverage.get("mode") != "task_covered"
                or coverage.get("result") != "pass"
            ):
                errors.append(f"{batch_id}.invalid_task_covered_closure:{closure_id}")
                continue
            if coverage.get("commandIds") != coverage_ids:
                errors.append(f"{batch_id}.task_covered_command_ids_mismatch")
            if coverage.get("sourceEvidenceIds") != expected_sources:
                errors.append(f"{batch_id}.task_covered_source_evidence_mismatch")
            if task_completion_numbers and _evidence_number(closure_id) <= max(task_completion_numbers):
                errors.append(f"{batch_id}.batch_closure_older_than_task_completion")
            continue
        planned = {
            str(command.get("id")): command
            for command in validation.get("commands", [])
            if isinstance(command, dict) and isinstance(command.get("id"), str)
        }
        required = {
            command_id
            for command_id, command in planned.items()
            if command.get("required") is True
        }
        passed: set[str] = set()
        run_ids: set[str] = set()
        for evidence_id in latest_ids:
            record = by_id.get(str(evidence_id))
            if (
                not isinstance(record, dict)
                or record.get("action") != "batch_validation"
                or record.get("taskId") != "__batch__"
                or record.get("batchId") != batch_id
                or not _validation_passed(record)
            ):
                errors.append(f"{batch_id}.invalid_batch_validation_pass:{evidence_id}")
                continue
            run_id = record.get("runId")
            if isinstance(run_id, str):
                run_ids.add(run_id)
            command_payload = record.get("validation")
            command_id = command_payload.get("commandId") if isinstance(command_payload, dict) else None
            if not isinstance(command_id, str) or command_id not in planned:
                errors.append(f"{batch_id}.unplanned_batch_validation_command:{command_id}")
                continue
            command = planned[command_id]
            for field in ("argv", "cwd", "kind", "required", "repo"):
                if command_payload.get(field) != command.get(field):
                    errors.append(f"{batch_id}.batch_validation_command_mismatch:{command_id}:{field}")
            passed.add(command_id)
        for command_id in sorted(required - passed):
            errors.append(f"{batch_id}.missing_batch_validation_pass:{command_id}")

        latest_batch_number = max(
            (_evidence_number(str(evidence_id)) for evidence_id in latest_ids),
            default=-1,
        )
        if task_completion_numbers and latest_batch_number <= max(task_completion_numbers):
            errors.append(f"{batch_id}.batch_validation_older_than_task_completion")

        if len(run_ids) > 1:
            errors.append(f"{batch_id}.batch_validation_multiple_runs:" + ",".join(sorted(run_ids)))
        if feature_dir is not None and len(run_ids) == 1:
            run_id = next(iter(run_ids))
            run_path = feature_dir / ".batch-runs" / str(batch_id) / f"{run_id}.json"
            if not run_path.is_file():
                errors.append(f"{batch_id}.missing_batch_run_state:{run_id}")
            else:
                try:
                    state = json.loads(run_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    state = None
                if not isinstance(state, dict):
                    errors.append(f"{batch_id}.invalid_batch_run_state:{run_id}")
                else:
                    if state.get("status") != "done" or state.get("success") is not True:
                        errors.append(f"{batch_id}.batch_run_not_successful:{run_id}")
                    attempts = state.get("attempts")
                    latest_attempt = attempts[-1] if isinstance(attempts, list) and attempts else None
                    bound_attempt_ids = (
                        latest_attempt.get("passingEvidenceIds", latest_attempt.get("evidenceIds"))
                        if isinstance(latest_attempt, dict)
                        else None
                    )
                    if bound_attempt_ids != latest_ids:
                        errors.append(f"{batch_id}.batch_run_evidence_not_bound:{run_id}")
    return errors


def _task_acceptance_ids(task: dict[str, Any]) -> set[str]:
    return {
        str(item.get("id"))
        for item in task.get("acceptanceCriteria", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _check_task_run_state(
    feature_dir: Path,
    task: dict[str, Any],
    completion_records: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    task_id = str(task.get("id", ""))
    by_run: dict[str, list[dict[str, Any]]] = {}
    for record in completion_records:
        run_id = record.get("runId")
        if isinstance(run_id, str) and run_id:
            by_run.setdefault(run_id, []).append(record)
    if not by_run:
        return [f"{task_id}.missing_task_run_state"]
    if len(by_run) != 1:
        errors.append(f"{task_id}.completion_evidence_multiple_runs:" + ",".join(sorted(by_run)))

    for run_id, run_records in by_run.items():
        run_path = feature_dir / ".task-runs" / task_id / f"{run_id}.json"
        if not run_path.is_file():
            errors.append(f"{task_id}.missing_task_run_state:{run_id}")
            continue
        try:
            state = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append(f"{task_id}.invalid_task_run_state:{run_id}")
            continue
        if not isinstance(state, dict):
            errors.append(f"{task_id}.invalid_task_run_state:{run_id}")
            continue
        integrity_error = task_run_integrity_error(state)
        if integrity_error is not None:
            errors.append(f"{task_id}.{integrity_error}:{run_id}")
        if state.get("taskId") != task_id or state.get("runId") != run_id:
            errors.append(f"{task_id}.task_run_identity_mismatch:{run_id}")
        if state.get("status") != "done" or state.get("success") is not True:
            errors.append(f"{task_id}.task_run_not_successful:{run_id}")
        if state.get("taskContractSha256") != task_contract_sha256(task):
            errors.append(f"{task_id}.task_run_contract_mismatch:{run_id}")
        state_evidence = set(state.get("evidenceIds", [])) if isinstance(state.get("evidenceIds"), list) else set()
        state_completion = (
            set(state.get("completionEvidenceIds", []))
            if isinstance(state.get("completionEvidenceIds"), list)
            else set()
        )
        expected_changed = state.get("changedFiles")
        expected_file_changes = state.get("fileChanges")
        expected_transient_validation_files = state.get("transientValidationFiles", [])
        for record in run_records:
            evidence_id = str(record.get("evidenceId", ""))
            if evidence_id not in state_evidence or evidence_id not in state_completion:
                errors.append(f"{task_id}.task_run_evidence_not_bound:{evidence_id}")
            if record.get("completionMode") != state.get("completionMode"):
                errors.append(f"{task_id}.task_run_completion_mode_mismatch:{evidence_id}")
            if record.get("changedFiles") != expected_changed:
                errors.append(f"{task_id}.task_run_changed_files_mismatch:{evidence_id}")
            if record.get("fileChanges") != expected_file_changes:
                errors.append(f"{task_id}.task_run_file_changes_mismatch:{evidence_id}")
            if record.get("transientValidationFiles", []) != expected_transient_validation_files:
                errors.append(
                    f"{task_id}.task_run_transient_validation_files_mismatch:{evidence_id}"
                )
    return errors


def _check_project_completion(
    plan: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    planned = {
        str(command.get("id")): command
        for command in plan.get("projectValidationCommands", [])
        if isinstance(command, dict) and isinstance(command.get("id"), str)
    }
    if not planned:
        return errors
    disposition = plan.get("projectValidationDisposition")
    if isinstance(disposition, dict):
        errors.extend(
            _check_deferral_evidence(
                disposition,
                by_id,
                expected_action="project_check",
                expected_task_id="__project__",
                context="projectValidation",
            )
        )
        return errors
    required = {command_id for command_id, command in planned.items() if command.get("required") is True}
    passed: set[str] = set()
    evidence_ids = plan.get("projectCheckEvidenceIds")
    if not isinstance(evidence_ids, list):
        evidence_ids = []
    latest_id = plan.get("latestProjectCheckEvidenceId")
    latest_record = by_id.get(str(latest_id)) if isinstance(latest_id, str) else None
    if latest_record is None:
        errors.append("missing_latest_project_check_evidence")
        current_run_id = None
    elif latest_record.get("action") != "project_check" or latest_record.get("taskId") != "__project__":
        errors.append(f"invalid_latest_project_check_evidence:{latest_id}")
        current_run_id = None
    else:
        current_run_id = latest_record.get("runId")
        latest_number = _evidence_number(str(latest_id))
        task_completion_numbers = [
            _evidence_number(str(evidence_id))
            for task in tasks(plan)
            for evidence_id in task.get("completionEvidenceIds", [])
            if isinstance(evidence_id, str)
        ]
        batch_completion_numbers = [
            _evidence_number(str(evidence_id))
            for batch in (plan.get("_bundleBatches") or {}).values()
            if isinstance(batch, dict)
            for evidence_id in (
                batch.get("batchValidation", {}).get("latestPassEvidenceIds", [])
                if isinstance(batch.get("batchValidation"), dict)
                else []
            )
            if isinstance(evidence_id, str)
        ]
        completion_numbers = [*task_completion_numbers, *batch_completion_numbers]
        if completion_numbers and latest_number <= max(completion_numbers):
            errors.append(f"project_check_older_than_task_completion:{latest_id}")
    for evidence_id in evidence_ids:
        record = by_id.get(str(evidence_id))
        if record is None or record.get("action") != "project_check" or record.get("taskId") != "__project__":
            continue
        if current_run_id is None or record.get("runId") != current_run_id:
            continue
        if not _validation_passed(record):
            continue
        validation = record.get("validation")
        if not isinstance(validation, dict):
            continue
        command_id = validation.get("commandId")
        if not isinstance(command_id, str) or command_id not in planned:
            errors.append(f"unplanned_project_validation_command:{command_id}")
            continue
        command = planned[command_id]
        for field in ("argv", "cwd", "kind", "required", "repo"):
            if validation.get(field) != command.get(field):
                errors.append(f"project_validation_command_mismatch:{command_id}:{field}")
        passed.add(command_id)
    for command_id in sorted(required - passed):
        errors.append(f"missing_project_validation_pass:{command_id}")
    return errors


def _evidence_number(evidence_id: str) -> int:
    trimmed = evidence_id[len("ev_"):] if evidence_id.startswith("ev_") else evidence_id
    try:
        return int(trimmed)
    except ValueError:
        return -1


def _cmd_check(args: argparse.Namespace) -> int:
    target = Path(args.feature_dir).resolve()
    errors = check_integrity(target, require_index=not args.allow_missing_index)
    if args.refs:
        errors.extend(check_plan_evidence_refs(target))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"EVIDENCE_INTEGRITY_PASS path={stream_path(target)}")
    return 0


def _cmd_code_done(args: argparse.Namespace) -> int:
    target = Path(args.feature_dir).resolve()
    errors = check_code_done(target)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"CODE_DONE_EVIDENCE_PASS path={stream_path(target)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Autodev evidence integrity")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check")
    check.add_argument("--feature-dir", required=True)
    check.add_argument("--allow-missing-index", action="store_true")
    check.add_argument("--refs", action="store_true", help="Also validate plan.json evidence references")
    check.set_defaults(func=_cmd_check)

    code_done = subparsers.add_parser("code-done")
    code_done.add_argument("--feature-dir", required=True)
    code_done.set_defaults(func=_cmd_code_done)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
