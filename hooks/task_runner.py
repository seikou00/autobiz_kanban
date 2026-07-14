#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transactional execution entrypoint for structured code tasks."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_store import EvidenceStoreError, append_evidence, read_records, stream_path  # noqa: E402
from hooks.evidence_kernel import FileLock  # noqa: E402
from hooks.json_writer_common import atomic_write_json, resolve_feature, resolve_workspace  # noqa: E402
from hooks.plan_json import (  # noqa: E402
    EXECUTION_LANES,
    PlanBundle,
    bundle_unfinished_tasks,
    find_task,
    load_plan_bundle,
    normalize_status,
    task_contract_sha256,
)
from hooks.plan_writer import (  # noqa: E402
    PlanWriterInputError,
    activate_batch as activate_plan_batch,
    record_project_check_attempt,
    record_task_attempt,
    set_task_execution_status,
)
from hooks.repository_snapshot import (  # noqa: E402
    RepositoryMap,
    RepositorySnapshotError,
    capture_file_snapshot,
    resolve_git_root,
    resolve_repositories,
    snapshot_changes,
    unignored_runtime_artifact_paths,
)


DEFAULT_TIMEOUT_SECONDS = 300
BEHAVIOR_VALIDATION_KINDS = {"behavior_test", "integration_test", "e2e_test", "static_check"}


class TaskRunnerError(ValueError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _emit(ok: bool, **data: Any) -> int:
    print(json.dumps({"ok": ok, **data}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _emit_error(exc: ValueError) -> int:
    details = exc.details if isinstance(exc, TaskRunnerError) else {}
    return _emit(False, error=str(exc), **details)


def _feature_dir(workspace: Path, feature: str) -> Path:
    return workspace / ".autobizdevops" / "features" / feature


def _runs_dir(feature_dir: Path, task_id: str) -> Path:
    return feature_dir / ".task-runs" / task_id


def _run_path(feature_dir: Path, task_id: str, run_id: str) -> Path:
    return _runs_dir(feature_dir, task_id) / f"{run_id}.json"


def _task_run_lock(feature_dir: Path) -> FileLock:
    return FileLock(feature_dir / ".task-runs" / ".lock")


def _load_plan_and_task(
    feature_dir: Path,
    task_id: str,
    *,
    require_active_batch: bool = True,
) -> tuple[PlanBundle, str, dict[str, Any]]:
    try:
        bundle = load_plan_bundle(feature_dir)
        batch_id, task = find_task(bundle, task_id)
    except ValueError as exc:
        raise TaskRunnerError(f"invalid_plan_json:{exc}") from exc
    if require_active_batch and bundle.root.get("status") == "awaiting_next_conversation":
        raise TaskRunnerError(f"batch_handoff_requires_new_conversation:{bundle.root.get('nextBatchId')}")
    active_batch = bundle.root.get("activeBatchId")
    if require_active_batch and active_batch != batch_id:
        raise TaskRunnerError(f"task_not_in_active_batch:{task_id}:active={active_batch}:taskBatch={batch_id}")
    return bundle, batch_id, task


def _unfinished_dependencies(plan: PlanBundle, task: dict[str, Any]) -> list[str]:
    by_id = {
        item.get("id"): item
        for item in plan.tasks
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    return [
        dep
        for dep in task.get("deps", [])
        if isinstance(dep, str) and normalize_status(by_id.get(dep, {}).get("status")) != "done"
    ]


def _git_root(code_workspace: Path) -> Path:
    try:
        return resolve_git_root(code_workspace)
    except RepositorySnapshotError as exc:
        raise TaskRunnerError(str(exc)) from exc


def _resolve_repositories(code_workspaces: Path | list[Path]) -> RepositoryMap:
    try:
        return resolve_repositories(code_workspaces)
    except RepositorySnapshotError as exc:
        raise TaskRunnerError(str(exc)) from exc


def _repository_state(repositories: RepositoryMap) -> list[dict[str, Any]]:
    return [
        {"id": repository_id, "path": str(repo), "snapshot": _git_snapshot(repo)}
        for repository_id, repo in repositories.items()
    ]


def _assert_runtime_artifacts_ignored(repositories: RepositoryMap) -> None:
    for repository_id, repo in repositories.items():
        unignored = unignored_runtime_artifact_paths(repo)
        if unignored:
            raise TaskRunnerError(
                f"runtime_artifact_path_not_ignored:{repository_id}:{unignored[0]}",
                requiredAction="configure_git_ignore_and_retry",
                resolvedGitRoots=[str(item) for item in repositories.values()],
                runtimeArtifactPaths=unignored,
            )


def _normalize_git_relative_path(raw: str, *, error: str) -> str:
    normalized = raw.replace("\\", "/")
    path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(raw)
    value = normalized.strip("/")
    if (
        not value
        or value == "."
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in path.parts
    ):
        raise TaskRunnerError(f"{error}:{raw}")
    return PurePosixPath(value).as_posix()


def _scope_workspaces(
    requested_workspaces: list[Path],
    repositories: RepositoryMap,
) -> list[dict[str, str]]:
    contexts: list[dict[str, str]] = []
    seen_roots: dict[Path, Path] = {}
    for requested in requested_workspaces:
        requested = requested.resolve()
        root = _git_root(requested)
        previous = seen_roots.get(root)
        if previous is not None:
            if previous != requested:
                raise TaskRunnerError(
                    f"ambiguous_code_workspace_base:{root}",
                    requestedCodeWorkspaces=[str(previous), str(requested)],
                )
            continue
        repository_id = root.name
        if repositories.get(repository_id) != root:
            raise TaskRunnerError(f"task_run_repository_snapshot_missing:{repository_id}")
        try:
            relative = requested.relative_to(root)
        except ValueError as exc:
            raise TaskRunnerError(f"code_workspace_outside_git_root:{requested}") from exc
        prefix = "" if relative == Path(".") else relative.as_posix()
        contexts.append(
            {
                "repository": repository_id,
                "requestedPath": str(requested),
                "resolvedGitRoot": str(root),
                "workspacePrefix": prefix,
            }
        )
        seen_roots[root] = requested
    return contexts


def _resolved_scope_paths(
    task: dict[str, Any],
    contexts: list[dict[str, str]],
) -> tuple[list[str], list[str]]:
    scope = task.get("scope")
    raw_paths = scope.get("paths") if isinstance(scope, dict) else []
    raw_paths = raw_paths if isinstance(raw_paths, list) else []
    declared = [item for item in raw_paths if isinstance(item, str)]
    if not declared:
        return [], []
    multiple = len(contexts) > 1
    by_repository = {item["repository"]: item for item in contexts}
    resolved: list[str] = []
    for raw in declared:
        repository_id: str | None = None
        relative = raw
        if multiple:
            repository_id, separator, relative = raw.partition(":")
            if not separator:
                raise TaskRunnerError(f"scope_path_repository_prefix_required:{raw}")
            if repository_id not in by_repository:
                raise TaskRunnerError(f"scope_path_repository_not_found:{raw}")
        context = (
            by_repository[repository_id]
            if repository_id is not None
            else contexts[0]
        )
        normalized = _normalize_git_relative_path(relative, error="invalid_scope_path")
        prefix = context["workspacePrefix"]
        projected = f"{prefix}/{normalized}" if prefix else normalized
        resolved.append(f"{repository_id}:{projected}" if repository_id else projected)
    return declared, sorted(set(resolved))


def _assert_requested_workspaces_match(
    state: dict[str, Any],
    requested_workspaces: list[Path],
    repositories: RepositoryMap,
) -> None:
    if state.get("scopePathBase") != "requested_code_workspace":
        return
    actual_contexts = _scope_workspaces(requested_workspaces, repositories)
    actual = [item["requestedPath"] for item in actual_contexts]
    expected = state.get("requestedCodeWorkspaces")
    if expected != actual:
        raise TaskRunnerError(
            "task_run_requested_workspace_mismatch",
            expectedRequestedCodeWorkspaces=expected,
            requestedCodeWorkspaces=actual,
            resolvedGitRoots=[str(item) for item in repositories.values()],
        )


def _repository_snapshots_match(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> bool:
    expected_snapshots = {
        str(item.get("id")): item.get("snapshot") for item in expected if isinstance(item, dict)
    }
    actual_snapshots = {
        str(item.get("id")): item.get("snapshot") for item in actual if isinstance(item, dict)
    }
    return expected_snapshots == actual_snapshots


def _state_repositories(state: dict[str, Any]) -> list[dict[str, Any]]:
    repositories = state.get("repositories")
    if isinstance(repositories, list) and repositories:
        return [item for item in repositories if isinstance(item, dict)]
    workspace = state.get("codeWorkspace")
    snapshot = state.get("snapshot")
    if isinstance(workspace, str) and isinstance(snapshot, dict):
        return [{"id": Path(workspace).name, "path": workspace, "snapshot": snapshot}]
    return []


def _assert_repositories_match(state: dict[str, Any], repositories: RepositoryMap) -> None:
    expected = [(str(item.get("id")), str(item.get("path"))) for item in _state_repositories(state)]
    actual = [(repository_id, str(repo)) for repository_id, repo in repositories.items()]
    if expected != actual:
        raise TaskRunnerError("task_run_code_workspace_mismatch")


def _git_snapshot(repo: Path) -> dict[str, str | None]:
    try:
        return capture_file_snapshot(repo)
    except RepositorySnapshotError as exc:
        raise TaskRunnerError(str(exc)) from exc


def _snapshot_changes(
    before: dict[str, str | None],
    after: dict[str, str | None],
) -> list[dict[str, str]]:
    return snapshot_changes(before, after)


def _changed_files(file_changes: list[dict[str, str]]) -> list[str]:
    return sorted(
        {
            value
            for change in file_changes
            for value in (change.get("path"), change.get("fromPath"))
            if isinstance(value, str)
        }
    )


def _repository_changes(
    state: dict[str, Any],
    repositories: RepositoryMap,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    repository_states = _state_repositories(state)
    if not repository_states:
        raise TaskRunnerError("task_run_snapshot_missing")
    multiple = len(repository_states) > 1
    changes: list[dict[str, str]] = []
    final: list[dict[str, Any]] = []
    for repository_state in repository_states:
        repository_id = str(repository_state.get("id", ""))
        before = repository_state.get("snapshot")
        repo = repositories.get(repository_id)
        if not isinstance(before, dict) or repo is None:
            raise TaskRunnerError(f"task_run_repository_snapshot_missing:{repository_id}")
        after = _git_snapshot(repo)
        repo_changes = _snapshot_changes(before, after)
        if multiple:
            for change in repo_changes:
                change["path"] = f"{repository_id}:{change['path']}"
                if "fromPath" in change:
                    change["fromPath"] = f"{repository_id}:{change['fromPath']}"
                change["repository"] = repository_id
        changes.extend(repo_changes)
        final.append({"id": repository_id, "path": str(repo), "snapshot": after})
    return changes, final


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{stamp}-{uuid.uuid4().hex[:8]}"


def _save_run(path: Path, state: dict[str, Any]) -> None:
    state["updatedAt"] = _utc_now()
    atomic_write_json(path, state)


def _active_feature_runs(feature_dir: Path, *, exclude: Path | None = None) -> list[str]:
    active: list[str] = []
    for path in (feature_dir / ".task-runs").glob("T*/*.json"):
        if exclude is not None and path == exclude:
            continue
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if item.get("status") not in {"done", "failed", "aborted"}:
            active.append(f"{item.get('taskId', path.parent.name)}:{item.get('runId', path.stem)}")
    return sorted(active)


def _start_task_unlocked(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspace: Path | list[Path],
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    plan, batch_id, task = _load_plan_and_task(feature_dir, task_id)
    if task.get("blockers"):
        raise TaskRunnerError(f"task_has_blockers:{task_id}")
    if unfinished := _unfinished_dependencies(plan, task):
        raise TaskRunnerError("unfinished_task_dependencies:" + ",".join(unfinished))
    if normalize_status(task.get("status")) == "done":
        raise TaskRunnerError(f"task_already_done:{task_id}")
    requested_workspaces = (
        [code_workspace] if isinstance(code_workspace, Path) else list(code_workspace)
    )
    repositories = _resolve_repositories(requested_workspaces)
    _assert_runtime_artifacts_ignored(repositories)
    scope_workspaces = _scope_workspaces(requested_workspaces, repositories)
    declared_scope_paths, resolved_scope_paths = _resolved_scope_paths(task, scope_workspaces)
    repository_state = _repository_state(repositories)
    active = _active_feature_runs(feature_dir)
    if active:
        active_tasks = sorted({item.partition(":")[0] for item in active})
        if task_id in active_tasks:
            raise TaskRunnerError(
                "active_task_run_exists:" + ",".join(active),
                requiredAction="inspect_and_retry_existing_run",
                activeRuns=active,
            )
        raise TaskRunnerError(
            "active_feature_task_run_exists:" + ",".join(active_tasks),
            requiredAction="inspect_and_retry_existing_run",
            activeRuns=active,
        )

    run_id = _new_run_id()
    state = {
        "version": 1,
        "runId": run_id,
        "featureId": feature,
        "batchId": batch_id,
        "taskId": task_id,
        "taskContractSha256": task_contract_sha256(task),
        "status": "started",
        "codeWorkspace": str(next(iter(repositories.values()))),
        "requestedCodeWorkspaces": [item["requestedPath"] for item in scope_workspaces],
        "resolvedGitRoots": [item["resolvedGitRoot"] for item in scope_workspaces],
        "workspacePrefixes": [item["workspacePrefix"] for item in scope_workspaces],
        "scopeWorkspaces": scope_workspaces,
        "scopePathBase": "requested_code_workspace",
        "declaredScopePaths": declared_scope_paths,
        "resolvedScopePaths": resolved_scope_paths,
        "repositories": repository_state,
        "snapshotMode": "git_visible_file_content_sha256",
        "stagingAffectsSnapshot": False,
        "startedAt": _utc_now(),
        "snapshot": repository_state[0]["snapshot"],
        "evidenceIds": [],
        "completionEvidenceIds": [],
        "completedCommandEvidence": {},
    }
    path = _run_path(feature_dir, task_id, run_id)
    _save_run(path, state)
    result = set_task_execution_status(
        workspace,
        feature,
        task_id,
        "in_progress",
        expected_task_contract_sha256=str(state["taskContractSha256"]),
    )
    if not result.ok:
        state["status"] = "aborted"
        state["abortReason"] = "plan_status_update_failed"
        _save_run(path, state)
        raise TaskRunnerError("plan_status_update_failed")
    return state


def _load_run(feature_dir: Path, task_id: str, run_id: str) -> tuple[Path, dict[str, Any]]:
    path = _run_path(feature_dir, task_id, run_id)
    if not path.is_file():
        raise TaskRunnerError(f"task_run_not_found:{run_id}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TaskRunnerError(f"invalid_task_run:{run_id}") from exc
    if not isinstance(state, dict):
        raise TaskRunnerError(f"invalid_task_run:{run_id}")
    return path, state


def _validate_run_evidence(feature_dir: Path, state: dict[str, Any]) -> None:
    records = {
        str(record.get("evidenceId")): record
        for record in read_records(stream_path(feature_dir))
        if isinstance(record.get("evidenceId"), str)
    }
    run_id = state.get("runId")
    task_id = state.get("taskId")
    completion_ids = set(state.get("completionEvidenceIds", []))
    completed_commands = (
        state.get("completedCommandEvidence")
        if isinstance(state.get("completedCommandEvidence"), dict)
        else {}
    )
    expected_changed_files = state.get("changedFiles")
    expected_file_changes = state.get("fileChanges")
    expected_mode = state.get("completionMode")
    for evidence_id in state.get("evidenceIds", []):
        record = records.get(str(evidence_id))
        if record is None:
            raise TaskRunnerError(f"task_run_evidence_missing:{evidence_id}")
        if record.get("taskId") != task_id:
            raise TaskRunnerError(f"task_run_evidence_task_mismatch:{evidence_id}")
        if record.get("runId") != run_id:
            raise TaskRunnerError(f"task_run_evidence_run_mismatch:{evidence_id}")
        if expected_mode is None:
            expected_mode = record.get("completionMode")
            state["completionMode"] = expected_mode
        elif record.get("completionMode") != expected_mode:
            raise TaskRunnerError(f"task_run_evidence_completion_mode_mismatch:{evidence_id}")
        if expected_changed_files is None:
            expected_changed_files = record.get("changedFiles")
            state["changedFiles"] = expected_changed_files
        elif record.get("changedFiles") != expected_changed_files:
            raise TaskRunnerError(f"task_run_evidence_changed_files_mismatch:{evidence_id}")
        if expected_file_changes is None:
            expected_file_changes = record.get("fileChanges")
            state["fileChanges"] = expected_file_changes
        elif record.get("fileChanges") != expected_file_changes:
            raise TaskRunnerError(f"task_run_evidence_file_changes_mismatch:{evidence_id}")
        if evidence_id in completion_ids:
            validation = record.get("validation")
            if not isinstance(validation, dict) or validation.get("result") != "pass":
                raise TaskRunnerError(f"task_run_completion_evidence_not_pass:{evidence_id}")
    for command_id, attempt in completed_commands.items():
        if not isinstance(attempt, dict):
            raise TaskRunnerError(f"invalid_completed_command_evidence:{command_id}")
        evidence_id = attempt.get("evidenceId")
        record = records.get(str(evidence_id))
        validation = record.get("validation") if isinstance(record, dict) else None
        if (
            not isinstance(validation, dict)
            or validation.get("commandId") != command_id
            or validation.get("result") != attempt.get("result")
        ):
            raise TaskRunnerError(f"completed_command_evidence_mismatch:{command_id}")


def _adopt_streamed_run_evidence(
    feature_dir: Path,
    state: dict[str, Any],
    task: dict[str, Any],
) -> None:
    path = stream_path(feature_dir)
    if not path.is_file():
        return
    planned = {
        str(command.get("id")): command
        for command in task.get("validationCommands", [])
        if isinstance(command, dict) and isinstance(command.get("id"), str)
    }
    completed = (
        dict(state.get("completedCommandEvidence"))
        if isinstance(state.get("completedCommandEvidence"), dict)
        else {}
    )
    evidence_ids = list(state.get("evidenceIds", [])) if isinstance(state.get("evidenceIds"), list) else []
    completion_ids = (
        list(state.get("completionEvidenceIds", []))
        if isinstance(state.get("completionEvidenceIds"), list)
        else []
    )
    for record in read_records(path):
        if record.get("taskId") != state.get("taskId") or record.get("runId") != state.get("runId"):
            continue
        validation = record.get("validation")
        command_id = validation.get("commandId") if isinstance(validation, dict) else None
        if not isinstance(command_id, str) or command_id not in planned:
            raise TaskRunnerError(f"streamed_run_evidence_unplanned_command:{command_id}")
        evidence_id = record.get("evidenceId")
        if not isinstance(evidence_id, str):
            raise TaskRunnerError(f"streamed_run_evidence_missing_id:{command_id}")
        existing = completed.get(command_id)
        if isinstance(existing, dict) and existing.get("evidenceId") != evidence_id:
            raise TaskRunnerError(f"duplicate_run_command_evidence:{command_id}")
        command = planned[command_id]
        for field in ("argv", "cwd", "kind", "required", "repo"):
            if validation.get(field) != command.get(field):
                raise TaskRunnerError(f"streamed_run_evidence_command_mismatch:{command_id}:{field}")
        if record.get("completionMode") != state.get("completionMode"):
            raise TaskRunnerError(f"streamed_run_evidence_completion_mode_mismatch:{evidence_id}")
        if record.get("changedFiles") != state.get("changedFiles"):
            raise TaskRunnerError(f"streamed_run_evidence_changed_files_mismatch:{evidence_id}")
        if record.get("fileChanges") != state.get("fileChanges"):
            raise TaskRunnerError(f"streamed_run_evidence_file_changes_mismatch:{evidence_id}")
        result = validation.get("result")
        completed[command_id] = {
            "evidenceId": evidence_id,
            "result": result,
            "required": validation.get("required"),
        }
        if evidence_id not in evidence_ids:
            evidence_ids.append(evidence_id)
        if result == "pass" and validation.get("required") is True and evidence_id not in completion_ids:
            completion_ids.append(evidence_id)
    state["completedCommandEvidence"] = completed
    state["evidenceIds"] = evidence_ids
    state["completionEvidenceIds"] = completion_ids


def _validate_supporting_files(repositories: RepositoryMap, supporting_files: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in supporting_files:
        repository_id: str | None = None
        relative = raw
        if len(repositories) > 1:
            repository_id, separator, relative = raw.partition(":")
            if not separator or repository_id not in repositories:
                raise TaskRunnerError(f"supporting_file_requires_repository_prefix:{raw}")
        repo = repositories[repository_id] if repository_id else next(iter(repositories.values()))
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise TaskRunnerError(f"invalid_supporting_file:{raw}")
        if not (repo / path).is_file():
            raise TaskRunnerError(f"missing_supporting_file:{raw}")
        value = path.as_posix()
        normalized.append(f"{repository_id}:{value}" if repository_id else value)
    return normalized


def _command_repository(command: dict[str, Any], repositories: RepositoryMap) -> tuple[str, Path]:
    requested = command.get("repo")
    if requested is None:
        if len(repositories) > 1:
            raise TaskRunnerError(f"validation_repository_required:{command.get('id', '')}")
        return next(iter(repositories.items()))
    if not isinstance(requested, str) or requested not in repositories:
        raise TaskRunnerError(f"validation_repository_not_found:{requested}")
    return requested, repositories[requested]


def _run_validation(command: dict[str, Any], repositories: RepositoryMap) -> tuple[int, str]:
    argv = command.get("argv")
    cwd = command.get("cwd")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise TaskRunnerError(f"invalid_validation_argv:{command.get('id', '')}")
    _, repo = _command_repository(command, repositories)
    command_cwd = (repo / str(cwd)).resolve()
    try:
        command_cwd.relative_to(repo)
    except ValueError as exc:
        raise TaskRunnerError(f"validation_cwd_outside_repository:{cwd}") from exc
    timeout = command.get("timeoutSeconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, int) or timeout <= 0:
        timeout = DEFAULT_TIMEOUT_SECONDS
    try:
        completed = subprocess.run(
            argv,
            cwd=command_cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = completed.stdout + completed.stderr
        return completed.returncode, output
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return 124, stdout + stderr + f"\ncommand timed out after {timeout} seconds\n"
    except OSError as exc:
        return 127, str(exc) + "\n"


def _criteria_ids(task: dict[str, Any]) -> set[str]:
    return {
        str(item.get("id"))
        for item in task.get("acceptanceCriteria", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _check_required_coverage(task: dict[str, Any]) -> None:
    required = [
        command
        for command in task.get("validationCommands", [])
        if isinstance(command, dict) and command.get("required") is True
    ]
    covered = {
        criterion
        for command in required
        for criterion in command.get("covers", [])
        if isinstance(criterion, str)
    }
    missing = sorted(_criteria_ids(task) - covered)
    if missing:
        raise TaskRunnerError("acceptance_criteria_not_covered:" + ",".join(missing))


def _record_for_command(
    *,
    feature: str,
    task: dict[str, Any],
    run_id: str,
    command: dict[str, Any],
    exit_code: int,
    completion_mode: str,
    file_changes: list[dict[str, str]],
    supporting_files: list[str],
    no_change_why: str | None,
    repository_id: str | None,
) -> dict[str, Any]:
    changed_files = sorted(
        {
            value
            for change in file_changes
            for value in (change.get("path"), change.get("fromPath"))
            if isinstance(value, str)
        }
    )
    checked_criteria = [item for item in command.get("covers", []) if isinstance(item, str)]
    result = "pass" if exit_code == 0 else "fail"
    no_code_change = completion_mode == "verified_existing"
    return {
        "featureId": feature,
        "checkpoint": "code_in_progress",
        "nodeId": "dev.code",
        "skill": "autodev-code",
        "taskId": task.get("id"),
        "action": "validation",
        "detailVersion": 2,
        "runId": run_id,
        "completionMode": completion_mode,
        "summary": f"{command.get('id')} validation {result}",
        "implementation": {
            "noCodeChange": no_code_change,
            "whatChanged": [] if no_code_change else changed_files,
            "why": no_change_why if no_code_change else str(task.get("goal", "task implementation")),
        },
        "specRefs": task.get("specRefs", []),
        "designRefs": task.get("designRefs", []),
        "changedFiles": changed_files,
        "fileChanges": file_changes,
        "supportingFiles": supporting_files,
        "checkedCriteria": checked_criteria,
        "validation": {
            "commandId": command.get("id"),
            "argv": command.get("argv"),
            "command": " ".join(str(item) for item in command.get("argv", [])),
            "cwd": command.get("cwd"),
            "kind": command.get("kind"),
            "required": command.get("required"),
            **({"repo": repository_id} if repository_id else {}),
            "exitCode": exit_code,
            "result": result,
        },
    }


def _complete_task_unlocked(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspace: Path | list[Path],
    run_id: str,
    *,
    no_code_change_why: str | None,
    supporting_files: list[str],
) -> tuple[bool, dict[str, Any]]:
    feature_dir = _feature_dir(workspace, feature)
    _, batch_id, task = _load_plan_and_task(feature_dir, task_id, require_active_batch=False)
    path, state = _load_run(feature_dir, task_id, run_id)
    if state.get("taskContractSha256") != task_contract_sha256(task):
        raise TaskRunnerError(f"task_contract_changed_after_start:{task_id}")
    stored_batch = state.get("batchId")
    if stored_batch is not None and stored_batch != batch_id:
        raise TaskRunnerError(f"task_batch_changed_after_start:{task_id}")
    requested_workspaces = (
        [code_workspace] if isinstance(code_workspace, Path) else list(code_workspace)
    )
    repositories = _resolve_repositories(requested_workspaces)
    _assert_repositories_match(state, repositories)
    _assert_requested_workspaces_match(state, requested_workspaces, repositories)
    if state.get("status") in {"done", "failed"}:
        return state.get("status") == "done", state
    if state.get("status") == "evidence_written":
        _validate_run_evidence(feature_dir, state)
        _save_run(path, state)
        success = bool(state.get("success"))
        result = record_task_attempt(
            workspace,
            feature,
            task_id,
            list(state.get("evidenceIds", [])),
            completion_evidence_ids=list(state.get("completionEvidenceIds", [])),
            success=success,
            expected_task_contract_sha256=str(state.get("taskContractSha256", "")),
        )
        if not result.ok:
            raise TaskRunnerError("plan_binding_failed")
        if isinstance(result.data, dict) and isinstance(result.data.get("batchHandoff"), dict):
            state["batchHandoff"] = result.data["batchHandoff"]
        if isinstance(result.data, dict) and isinstance(result.data.get("batchContinuation"), dict):
            state["batchContinuation"] = result.data["batchContinuation"]
        state["status"] = "done" if success else "failed"
        _save_run(path, state)
        return success, state
    if state.get("status") == "validation_running" and state.get("evidenceIds"):
        _validate_run_evidence(feature_dir, state)
        _save_run(path, state)

    repository_states = _state_repositories(state)
    multiple_repositories = len(repository_states) > 1
    file_changes, final_repositories = _repository_changes(state, repositories)
    declared_scope_paths, scope_paths = _run_scope_paths(state, task)
    if scope_paths:
        outside = [
            path
            for change in file_changes
            for path in (change.get("path"), change.get("fromPath"))
            if isinstance(path, str) and not _path_in_scope(path, scope_paths)
        ]
        if outside:
            required_action = (
                "correct_plan_scope_and_rebuild_task_baseline"
                if _paths_within_requested_workspaces(outside, state)
                else "fix_workspace_and_retry_same_run"
            )
            raise TaskRunnerError(
                "out_of_scope_changes_detected:" + ",".join(sorted(set(outside))),
                requiredAction=required_action,
                runId=run_id,
                changedFiles=_changed_files(file_changes),
                declaredScopePaths=declared_scope_paths,
                resolvedScopePaths=scope_paths,
                requestedCodeWorkspaces=state.get("requestedCodeWorkspaces", []),
                resolvedGitRoots=[str(item) for item in repositories.values()],
            )
    normalized_supporting = _validate_supporting_files(repositories, supporting_files)
    if file_changes:
        if no_code_change_why or normalized_supporting:
            raise TaskRunnerError("no_code_change_claim_conflicts_with_snapshot")
        completion_mode = "implemented"
    else:
        if not no_code_change_why or not normalized_supporting:
            raise TaskRunnerError("no_code_change_requires_reason_and_supporting_files")
        conflict = _prior_aborted_run_conflict(
            feature_dir,
            task,
            run_id,
            repositories,
            scope_paths,
        )
        if conflict:
            prior_run_id, prior_changed_files = conflict
            raise TaskRunnerError(
                f"verified_existing_conflicts_with_prior_run_changes:{prior_run_id}:"
                + ",".join(prior_changed_files),
                requiredAction="resume_original_run_or_rebuild_baseline",
                priorRunId=prior_run_id,
                changedFiles=prior_changed_files,
            )
        commands = [item for item in task.get("validationCommands", []) if isinstance(item, dict)]
        if not any(item.get("kind") in BEHAVIOR_VALIDATION_KINDS for item in commands if item.get("required") is True):
            raise TaskRunnerError("verified_existing_requires_behavior_validation")
        completion_mode = "verified_existing"

    _check_required_coverage(task)
    changed_files = _changed_files(file_changes)
    if state.get("status") == "validation_running":
        if state.get("changedFiles") != changed_files or state.get("fileChanges") != file_changes:
            raise TaskRunnerError("task_run_workspace_changed_after_validation_started")
        if state.get("completionMode") != completion_mode:
            raise TaskRunnerError("task_run_completion_mode_changed")
    state.update(
        {
            "status": "validation_running",
            "completionMode": completion_mode,
            "changedFiles": changed_files,
            "fileChanges": file_changes,
            "finalSnapshot": final_repositories[0]["snapshot"],
            "finalRepositories": final_repositories,
            "supportingFiles": normalized_supporting,
            "noCodeChangeWhy": no_code_change_why,
        }
    )
    _save_run(path, state)

    completed_commands = state.get("completedCommandEvidence")
    if not isinstance(completed_commands, dict):
        completed_commands = {}
    _adopt_streamed_run_evidence(feature_dir, state, task)
    completed_commands = state.get("completedCommandEvidence", {})
    _save_run(path, state)
    if state.get("evidenceIds"):
        _validate_run_evidence(feature_dir, state)
    evidence_ids = [
        str(item.get("evidenceId"))
        for item in completed_commands.values()
        if isinstance(item, dict) and isinstance(item.get("evidenceId"), str)
    ]
    pass_evidence_ids = [
        str(item.get("evidenceId"))
        for item in completed_commands.values()
        if isinstance(item, dict)
        and isinstance(item.get("evidenceId"), str)
        and item.get("result") == "pass"
        and item.get("required") is True
    ]
    required_failed = any(
        isinstance(item, dict) and item.get("result") != "pass" and item.get("required") is True
        for item in completed_commands.values()
    )
    for command in task.get("validationCommands", []):
        if not isinstance(command, dict):
            continue
        command_id = command.get("id")
        if isinstance(command_id, str) and command_id in completed_commands:
            continue
        command_repository_id, _ = _command_repository(command, repositories)
        exit_code, output = _run_validation(command, repositories)
        if not _repository_snapshots_match(final_repositories, _repository_state(repositories)):
            raise TaskRunnerError(f"validation_modified_workspace:{command_id}")
        record = _record_for_command(
            feature=feature,
            task=task,
            run_id=run_id,
            command=command,
            exit_code=exit_code,
            completion_mode=completion_mode,
            file_changes=file_changes,
            supporting_files=normalized_supporting,
            no_change_why=no_code_change_why,
            repository_id=command_repository_id if multiple_repositories or command.get("repo") else None,
        )
        try:
            evidence = append_evidence(feature_dir, record, output_tail=output)
        except EvidenceStoreError as exc:
            raise TaskRunnerError(f"evidence_append_failed:{exc}") from exc
        evidence_id = str(evidence["evidenceId"])
        evidence_ids.append(evidence_id)
        if exit_code == 0 and command.get("required") is True:
            pass_evidence_ids.append(evidence_id)
        if exit_code != 0 and command.get("required") is True:
            required_failed = True
        if isinstance(command_id, str):
            completed_commands[command_id] = {
                "evidenceId": evidence_id,
                "result": "pass" if exit_code == 0 else "fail",
                "required": command.get("required"),
            }
            state.update(
                {
                    "status": "validation_running",
                    "completedCommandEvidence": completed_commands,
                    "evidenceIds": evidence_ids,
                    "completionEvidenceIds": pass_evidence_ids,
                }
            )
            _save_run(path, state)

    success = not required_failed
    state.update(
        {
            "status": "evidence_written",
            "success": success,
            "evidenceIds": evidence_ids,
            "completionEvidenceIds": pass_evidence_ids if success else [],
        }
    )
    _save_run(path, state)
    result = record_task_attempt(
        workspace,
        feature,
        task_id,
        evidence_ids,
        completion_evidence_ids=pass_evidence_ids if success else [],
        success=success,
        expected_task_contract_sha256=str(state.get("taskContractSha256", "")),
    )
    if not result.ok:
        raise TaskRunnerError("plan_binding_failed")
    if isinstance(result.data, dict) and isinstance(result.data.get("batchHandoff"), dict):
        state["batchHandoff"] = result.data["batchHandoff"]
    if isinstance(result.data, dict) and isinstance(result.data.get("batchContinuation"), dict):
        state["batchContinuation"] = result.data["batchContinuation"]
    state["status"] = "done" if success else "failed"
    _save_run(path, state)
    return success, state


def _path_in_scope(path: str, scope_paths: list[Any]) -> bool:
    candidate = PurePosixPath(path)
    for raw in scope_paths:
        if not isinstance(raw, str) or not raw:
            continue
        scope = PurePosixPath(raw)
        if candidate == scope or scope in candidate.parents:
            return True
    return False


def _run_scope_paths(
    state: dict[str, Any],
    task: dict[str, Any],
) -> tuple[list[str], list[str]]:
    scope = task.get("scope")
    raw_paths = scope.get("paths") if isinstance(scope, dict) else []
    raw_paths = raw_paths if isinstance(raw_paths, list) else []
    declared = [item for item in raw_paths if isinstance(item, str)]
    if state.get("scopePathBase") != "requested_code_workspace":
        return declared, declared
    stored_declared = state.get("declaredScopePaths")
    stored_resolved = state.get("resolvedScopePaths")
    if not isinstance(stored_declared, list) or not all(
        isinstance(item, str) for item in stored_declared
    ):
        raise TaskRunnerError("task_run_declared_scope_paths_missing")
    if not isinstance(stored_resolved, list) or not all(
        isinstance(item, str) for item in stored_resolved
    ):
        raise TaskRunnerError("task_run_resolved_scope_paths_missing")
    return stored_declared, stored_resolved


def _paths_within_requested_workspaces(paths: list[str], state: dict[str, Any]) -> bool:
    contexts = state.get("scopeWorkspaces")
    if not isinstance(contexts, list) or not contexts:
        return False
    by_repository = {
        item.get("repository"): item
        for item in contexts
        if isinstance(item, dict) and isinstance(item.get("repository"), str)
    }
    multiple = len(contexts) > 1
    for raw in paths:
        repository_id: str | None = None
        relative = raw
        if multiple:
            repository_id, separator, relative = raw.partition(":")
            if not separator or repository_id not in by_repository:
                return False
        context = (
            by_repository.get(repository_id)
            if repository_id is not None
            else contexts[0]
        )
        if not isinstance(context, dict):
            return False
        prefix = context.get("workspacePrefix")
        if not isinstance(prefix, str) or not prefix:
            return False
        candidate = PurePosixPath(relative)
        workspace = PurePosixPath(prefix)
        if candidate != workspace and workspace not in candidate.parents:
            return False
    return True


def _prior_aborted_run_conflict(
    feature_dir: Path,
    task: dict[str, Any],
    current_run_id: str,
    repositories: RepositoryMap,
    scope_paths: list[str],
) -> tuple[str, list[str]] | None:
    for path in sorted(_runs_dir(feature_dir, str(task.get("id"))).glob("*.json")):
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if prior.get("runId") == current_run_id or prior.get("status") != "aborted":
            continue
        changed = prior.get("changedFilesAtAbort")
        if not isinstance(changed, list):
            try:
                changed = _changed_files(_repository_changes(prior, repositories)[0])
            except TaskRunnerError:
                continue
        relevant = [
            item
            for item in changed
            if isinstance(item, str) and (not scope_paths or _path_in_scope(item, scope_paths))
        ]
        if relevant:
            return str(prior.get("runId")), sorted(set(relevant))
    return None


def _abort_task_unlocked(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspace: Path | list[Path],
    run_id: str,
    *,
    force_with_changes: bool,
    abort_why: str | None,
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    path, state = _load_run(feature_dir, task_id, run_id)
    if state.get("status") in {"evidence_written", "done", "failed"}:
        raise TaskRunnerError(f"task_run_cannot_abort:{state.get('status')}")
    requested_workspaces = (
        [code_workspace] if isinstance(code_workspace, Path) else list(code_workspace)
    )
    repositories = _resolve_repositories(requested_workspaces)
    _assert_repositories_match(state, repositories)
    _assert_requested_workspaces_match(state, requested_workspaces, repositories)
    file_changes, final_repositories = _repository_changes(state, repositories)
    changed_files = _changed_files(file_changes)
    if file_changes and not force_with_changes:
        raise TaskRunnerError(
            "task_run_has_unrecorded_changes:" + ",".join(changed_files),
            requiredAction="fix_workspace_and_retry_complete_or_force_abort",
            changedFiles=changed_files,
            resolvedGitRoots=[str(item) for item in repositories.values()],
        )
    if file_changes and force_with_changes and not abort_why:
        raise TaskRunnerError(
            "abort_with_changes_requires_reason",
            requiredAction="provide_abort_reason_or_retry_complete",
            changedFiles=changed_files,
        )
    state["status"] = "aborted"
    if file_changes:
        state.update(
            {
                "abortSnapshot": final_repositories[0]["snapshot"],
                "abortRepositories": final_repositories,
                "fileChangesAtAbort": file_changes,
                "changedFilesAtAbort": changed_files,
                "abortWhy": abort_why,
            }
        )
    _save_run(path, state)
    try:
        result = set_task_execution_status(
            workspace,
            feature,
            task_id,
            "todo",
        )
    except PlanWriterInputError:
        state["planStatusReset"] = False
        state["planStatusResetError"] = "plan_integrity_error"
        _save_run(path, state)
        return state
    if not result.ok:
        state["planStatusReset"] = False
        state["planStatusResetError"] = "plan_status_update_failed"
        _save_run(path, state)
        return state
    state["planStatusReset"] = True
    _save_run(path, state)
    return state


def _resume_task_unlocked(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspace: Path | list[Path],
    run_id: str,
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    _, batch_id, task = _load_plan_and_task(feature_dir, task_id)
    path, state = _load_run(feature_dir, task_id, run_id)
    if state.get("status") != "aborted":
        raise TaskRunnerError(f"task_run_cannot_resume:{state.get('status')}")
    if (
        state.get("evidenceIds")
        or state.get("completionEvidenceIds")
        or state.get("completedCommandEvidence")
    ):
        raise TaskRunnerError("task_run_cannot_resume_with_evidence")
    if state.get("taskContractSha256") != task_contract_sha256(task):
        raise TaskRunnerError(f"task_contract_changed_after_start:{task_id}")
    if state.get("batchId") is not None and state.get("batchId") != batch_id:
        raise TaskRunnerError(f"task_batch_changed_after_start:{task_id}")
    requested_workspaces = (
        [code_workspace] if isinstance(code_workspace, Path) else list(code_workspace)
    )
    repositories = _resolve_repositories(requested_workspaces)
    _assert_repositories_match(state, repositories)
    _assert_requested_workspaces_match(state, requested_workspaces, repositories)
    _assert_runtime_artifacts_ignored(repositories)
    active = _active_feature_runs(feature_dir, exclude=path)
    if active:
        active_tasks = sorted({item.partition(":")[0] for item in active})
        if task_id in active_tasks:
            raise TaskRunnerError(
                "active_task_run_exists:" + ",".join(active),
                requiredAction="finish_or_abort_active_run_before_resume",
                activeRuns=active,
            )
        raise TaskRunnerError(
            "active_feature_task_run_exists:" + ",".join(active_tasks),
            requiredAction="finish_or_abort_active_run_before_resume",
            activeRuns=active,
        )
    result = set_task_execution_status(
        workspace,
        feature,
        task_id,
        "in_progress",
        expected_task_contract_sha256=str(state["taskContractSha256"]),
    )
    if not result.ok:
        raise TaskRunnerError("plan_status_update_failed")
    state.update(
        {
            "status": "started",
            "resumedAt": _utc_now(),
            "resumeCount": int(state.get("resumeCount", 0)) + 1,
        }
    )
    _save_run(path, state)
    return state


def _run_project_checks_unlocked(
    workspace: Path,
    feature: str,
    code_workspace: Path | list[Path],
) -> tuple[bool, list[str]]:
    feature_dir = _feature_dir(workspace, feature)
    try:
        bundle = load_plan_bundle(feature_dir)
    except ValueError as exc:
        raise TaskRunnerError(f"invalid_plan_json:{exc}") from exc
    if unfinished := bundle_unfinished_tasks(bundle):
        raise TaskRunnerError("project_check_requires_all_tasks_done:" + ",".join(unfinished))
    if bundle.root.get("activeBatchId") is not None or bundle.root.get("nextBatchId") is not None:
        raise TaskRunnerError("project_check_requires_all_batches_done")
    if (feature_dir / "BATCH_HANDOFF.json").exists():
        raise TaskRunnerError("project_check_blocked_by_batch_handoff")
    repositories = _resolve_repositories(code_workspace)
    project_snapshot = _repository_state(repositories)
    evidence_ids: list[str] = []
    required_failed = False
    run_id = _new_run_id()
    for command in bundle.root.get("projectValidationCommands", []):
        if not isinstance(command, dict):
            continue
        repository_id, _ = _command_repository(command, repositories)
        exit_code, output = _run_validation(command, repositories)
        if not _repository_snapshots_match(project_snapshot, _repository_state(repositories)):
            raise TaskRunnerError(f"project_validation_modified_workspace:{command.get('id', '')}")
        record = {
            "featureId": feature,
            "checkpoint": "code_in_progress",
            "nodeId": "dev.code",
            "skill": "autodev-code",
            "taskId": "__project__",
            "action": "project_check",
            "detailVersion": 2,
            "runId": run_id,
            "completionMode": "verified_existing",
            "summary": f"{command.get('id')} project check",
            "implementation": {
                "noCodeChange": True,
                "whatChanged": [],
                "why": "Project-level checks validate the completed workspace without changing files",
            },
            "specRefs": [],
            "designRefs": [],
            "changedFiles": [],
            "fileChanges": [],
            "supportingFiles": [],
            "checkedCriteria": [str(command.get("id"))],
            "validation": {
                "commandId": command.get("id"),
                "argv": command.get("argv"),
                "command": " ".join(str(item) for item in command.get("argv", [])),
                "cwd": command.get("cwd"),
                "kind": command.get("kind"),
                "required": command.get("required"),
                **({"repo": repository_id} if len(repositories) > 1 or command.get("repo") else {}),
                "exitCode": exit_code,
                "result": "pass" if exit_code == 0 else "fail",
            },
        }
        evidence = append_evidence(feature_dir, record, output_tail=output)
        evidence_ids.append(str(evidence["evidenceId"]))
        if command.get("required") is True and exit_code != 0:
            required_failed = True
    success = not required_failed
    result = record_project_check_attempt(workspace, feature, evidence_ids, success=success)
    if not result.ok:
        raise TaskRunnerError("project_check_plan_binding_failed")
    return success, evidence_ids


def start_task(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspace: Path | list[Path],
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        return _start_task_unlocked(workspace, feature, task_id, code_workspace)


def complete_task(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspace: Path | list[Path],
    run_id: str,
    *,
    no_code_change_why: str | None,
    supporting_files: list[str],
) -> tuple[bool, dict[str, Any]]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        return _complete_task_unlocked(
            workspace,
            feature,
            task_id,
            code_workspace,
            run_id,
            no_code_change_why=no_code_change_why,
            supporting_files=supporting_files,
        )


def abort_task(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspace: Path | list[Path],
    run_id: str,
    *,
    force_with_changes: bool,
    abort_why: str | None,
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        return _abort_task_unlocked(
            workspace,
            feature,
            task_id,
            code_workspace,
            run_id,
            force_with_changes=force_with_changes,
            abort_why=abort_why,
        )


def resume_task(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspace: Path | list[Path],
    run_id: str,
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        return _resume_task_unlocked(workspace, feature, task_id, code_workspace, run_id)


def run_project_checks(
    workspace: Path,
    feature: str,
    code_workspace: Path | list[Path],
) -> tuple[bool, list[str]]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        return _run_project_checks_unlocked(workspace, feature, code_workspace)


def _activate_batch_unlocked(workspace: Path, feature: str, batch_id: str) -> dict[str, Any]:
    result = activate_plan_batch(workspace, feature, batch_id)
    if not result.ok:
        errors = result.errors or []
        detail = ";".join(
            f"{item.get('reason')}:{item.get('detail', '')}" for item in errors
        )
        raise TaskRunnerError(detail or "batch_activation_failed")
    return dict(result.data or {})


def activate_batch(workspace: Path, feature: str, batch_id: str) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        return _activate_batch_unlocked(workspace, feature, batch_id)


def _code_session_unlocked(workspace: Path, feature: str) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    try:
        bundle = load_plan_bundle(feature_dir)
    except ValueError as exc:
        raise TaskRunnerError(f"invalid_plan_json:{exc}") from exc

    activated_from_handoff = False
    if bundle.root.get("status") == "awaiting_next_conversation":
        next_batch_id = bundle.root.get("nextBatchId")
        if not isinstance(next_batch_id, str):
            raise TaskRunnerError("batch_handoff_missing_next_batch")
        handoff_path = feature_dir / "BATCH_HANDOFF.json"
        if not handoff_path.is_file():
            raise TaskRunnerError(f"batch_handoff_missing:{next_batch_id}")
        try:
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TaskRunnerError(f"batch_handoff_invalid:{next_batch_id}") from exc
        if not isinstance(handoff, dict) or handoff.get("nextBatchId") != next_batch_id:
            raise TaskRunnerError(f"batch_handoff_mismatch:{next_batch_id}")
        _activate_batch_unlocked(workspace, feature, next_batch_id)
        activated_from_handoff = True
        try:
            bundle = load_plan_bundle(feature_dir)
        except ValueError as exc:
            raise TaskRunnerError(f"invalid_plan_json:{exc}") from exc

    active_batch_id = bundle.root.get("activeBatchId")
    if isinstance(active_batch_id, str):
        entry = next(
            (item for item in bundle.root.get("batches", []) if item.get("id") == active_batch_id),
            None,
        )
        if not isinstance(entry, dict):
            raise TaskRunnerError(f"active_batch_missing:{active_batch_id}")
        execution_lane = entry.get("executionLane")
        if execution_lane not in EXECUTION_LANES:
            raise TaskRunnerError(f"active_batch_execution_lane_invalid:{active_batch_id}")
        return {
            "action": "execute_active_batch",
            "activeBatchId": active_batch_id,
            "executionLane": execution_lane,
            "taskIds": list(entry.get("taskIds", [])),
            "activatedFromHandoff": activated_from_handoff,
            "userMessage": f"开始执行批次 {active_batch_id}。",
        }

    unfinished = bundle_unfinished_tasks(bundle)
    if unfinished:
        raise TaskRunnerError("no_active_batch_for_unfinished_tasks:" + ",".join(unfinished))
    if isinstance(bundle.root.get("latestProjectCheckEvidenceId"), str):
        return {
            "action": "code_done_ready",
            "activeBatchId": None,
            "activatedFromHandoff": False,
            "userMessage": "所有批次及项目级最终校验已完成。",
        }
    return {
        "action": "run_project_check",
        "activeBatchId": None,
        "activatedFromHandoff": False,
        "userMessage": "所有批次已完成，开始执行项目级最终校验。",
    }


def code_session(workspace: Path, feature: str) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        return _code_session_unlocked(workspace, feature)


def _resolve(args: argparse.Namespace) -> tuple[Path, str, list[Path]]:
    workspace = resolve_workspace(args.workspace)
    feature = resolve_feature(args.feature)
    code_workspaces = [Path(item).expanduser().resolve() for item in args.code_workspace]
    return workspace, feature, code_workspaces


def _cmd_start(args: argparse.Namespace) -> int:
    try:
        workspace, feature, code_workspace = _resolve(args)
        state = start_task(workspace, feature, args.task_id, code_workspace)
        return _emit(True, **state)
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)


def _cmd_complete(args: argparse.Namespace) -> int:
    try:
        workspace, feature, code_workspace = _resolve(args)
        success, state = complete_task(
            workspace,
            feature,
            args.task_id,
            code_workspace,
            args.run_id,
            no_code_change_why=args.no_code_change_why,
            supporting_files=args.supporting_file or [],
        )
        batch_handoff = state.get("batchHandoff")
        batch_handoff = batch_handoff if isinstance(batch_handoff, dict) else None
        batch_continuation = state.get("batchContinuation")
        batch_continuation = batch_continuation if isinstance(batch_continuation, dict) else None
        return _emit(
            success,
            error=None if success else "validation_failed",
            runId=state.get("runId"),
            status=state.get("status"),
            completionMode=state.get("completionMode"),
            evidenceIds=state.get("evidenceIds", []),
            completionEvidenceIds=state.get("completionEvidenceIds", []),
            batchHandoff=batch_handoff,
            batchContinuation=batch_continuation,
            stopAfterBatch=bool(batch_handoff),
            continueCurrentBatch=(
                bool(batch_continuation.get("continueCurrentBatch")) if batch_continuation else False
            ),
            activeBatchId=batch_continuation.get("activeBatchId") if batch_continuation else None,
            nextTaskId=batch_continuation.get("nextTaskId") if batch_continuation else None,
            requiresNewConversation=(
                bool(batch_handoff.get("requiresNewConversation")) if batch_handoff else False
            ),
            requiredAction=(
                batch_handoff.get("requiredAction")
                if batch_handoff
                else batch_continuation.get("requiredAction") if batch_continuation else None
            ),
            userMessage=batch_handoff.get("userMessage") if batch_handoff else None,
        )
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)


def _cmd_recover(args: argparse.Namespace) -> int:
    return _cmd_complete(args)


def _cmd_abort(args: argparse.Namespace) -> int:
    try:
        workspace, feature, code_workspace = _resolve(args)
        state = abort_task(
            workspace,
            feature,
            args.task_id,
            code_workspace,
            args.run_id,
            force_with_changes=args.force_with_changes,
            abort_why=args.abort_why,
        )
        return _emit(True, **state)
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)


def _cmd_resume(args: argparse.Namespace) -> int:
    try:
        workspace, feature, code_workspace = _resolve(args)
        state = resume_task(workspace, feature, args.task_id, code_workspace, args.run_id)
        return _emit(True, **state)
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)


def _cmd_inspect(args: argparse.Namespace) -> int:
    try:
        workspace, feature, _ = _resolve(args)
        feature_dir = _feature_dir(workspace, feature)
        if args.run_id:
            _, state = _load_run(feature_dir, args.task_id, args.run_id)
            return _emit(True, run=state)
        runs = []
        for path in sorted(_runs_dir(feature_dir, args.task_id).glob("*.json")):
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        return _emit(True, runs=runs)
    except (TaskRunnerError, ValueError, json.JSONDecodeError) as exc:
        return _emit_error(exc)


def _cmd_project_check(args: argparse.Namespace) -> int:
    try:
        workspace, feature, code_workspace = _resolve(args)
        success, evidence_ids = run_project_checks(workspace, feature, code_workspace)
        return _emit(success, error=None if success else "project_validation_failed", evidenceIds=evidence_ids)
    except (TaskRunnerError, EvidenceStoreError, ValueError) as exc:
        return _emit_error(exc)


def _cmd_activate_batch(args: argparse.Namespace) -> int:
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        return _emit(True, **activate_batch(workspace, feature, args.batch_id))
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)


def _cmd_code_session(args: argparse.Namespace) -> int:
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        return _emit(True, **code_session(workspace, feature))
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run and complete structured code tasks")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(subparser: argparse.ArgumentParser, *, needs_run: bool = False) -> None:
        subparser.add_argument("--workspace")
        subparser.add_argument("--feature")
        subparser.add_argument("--task-id", required=True)
        subparser.add_argument("--code-workspace", required=True, action="append")
        if needs_run:
            subparser.add_argument("--run-id", required=True)

    start = subparsers.add_parser("start")
    common(start)
    start.set_defaults(func=_cmd_start)

    complete = subparsers.add_parser("complete")
    common(complete, needs_run=True)
    complete.add_argument("--no-code-change-why")
    complete.add_argument("--supporting-file", action="append")
    complete.set_defaults(func=_cmd_complete)

    recover = subparsers.add_parser("recover")
    common(recover, needs_run=True)
    recover.add_argument("--no-code-change-why")
    recover.add_argument("--supporting-file", action="append")
    recover.set_defaults(func=_cmd_recover)

    abort = subparsers.add_parser("abort")
    common(abort, needs_run=True)
    abort.add_argument("--force-with-changes", action="store_true")
    abort.add_argument("--abort-why")
    abort.set_defaults(func=_cmd_abort)

    resume = subparsers.add_parser("resume")
    common(resume, needs_run=True)
    resume.set_defaults(func=_cmd_resume)

    inspect = subparsers.add_parser("inspect")
    common(inspect)
    inspect.add_argument("--run-id")
    inspect.set_defaults(func=_cmd_inspect)

    project_check = subparsers.add_parser("project-check")
    project_check.add_argument("--workspace")
    project_check.add_argument("--feature")
    project_check.add_argument("--code-workspace", required=True, action="append")
    project_check.set_defaults(func=_cmd_project_check)

    activate = subparsers.add_parser("activate-batch")
    activate.add_argument("--workspace")
    activate.add_argument("--feature")
    activate.add_argument("--batch-id", required=True)
    activate.set_defaults(func=_cmd_activate_batch)

    session = subparsers.add_parser("code-session")
    session.add_argument("--workspace")
    session.add_argument("--feature")
    session.set_defaults(func=_cmd_code_session)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
