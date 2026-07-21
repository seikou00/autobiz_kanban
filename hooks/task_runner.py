#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transactional execution entrypoint for structured code tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
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
    deferred_task_validation_enabled,
    bundle_unfinished_tasks,
    find_task,
    load_plan_bundle,
    normalize_status,
    task_contract_sha256,
    task_workspace_roots,
    validation_command_manifest_names,
)
from hooks.plan_writer import (  # noqa: E402
    PlanWriterInputError,
    activate_batch as activate_plan_batch,
    invalidate_deferred_task_validation_for_repair,
    record_batch_validation_attempt,
    record_deferred_task_validation_attempt,
    record_project_check_attempt,
    record_task_implementation,
    record_task_covered_batch,
    record_task_attempt,
    recover_plan_write_transaction,
    request_batch_revalidation,
    set_task_execution_status,
    start_batch_validation_run,
    start_deferred_task_validation,
)
from hooks.repository_snapshot import (  # noqa: E402
    RepositoryMap,
    RepositorySnapshotError,
    capture_file_snapshot,
    capture_untracked_files,
    resolve_git_root,
    resolve_repositories,
    snapshot_changes,
    unignored_runtime_artifact_paths,
)
from hooks.task_run_integrity import (  # noqa: E402
    task_run_integrity_error,
    task_run_integrity_sha256,
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


def _batch_run_path(feature_dir: Path, batch_id: str, run_id: str) -> Path:
    return feature_dir / ".batch-runs" / batch_id / f"{run_id}.json"


def _task_validation_run_path(feature_dir: Path, batch_id: str, run_id: str) -> Path:
    return feature_dir / ".batch-task-validation-runs" / batch_id / f"{run_id}.json"


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
    task_id = str(task.get("id", ""))
    task_batch = plan.task_batches.get(task_id)
    deferred = deferred_task_validation_enabled(plan.root)
    unfinished: list[str] = []
    for dep in task.get("deps", []):
        if not isinstance(dep, str):
            continue
        status = normalize_status(by_id.get(dep, {}).get("status"))
        same_batch = task_batch is not None and plan.task_batches.get(dep) == task_batch
        satisfied = status == "done" or (deferred and same_batch and status == "implemented")
        if not satisfied:
            unfinished.append(dep)
    return unfinished


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
        {
            "id": repository_id,
            "path": str(repo),
            "snapshot": _git_snapshot(repo),
            "untrackedFiles": _git_untracked_files(repo),
        }
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


def _assert_workspace_roots_match(
    workspace_roots: dict[str, str],
    contexts: list[dict[str, str]],
    *,
    contract_name: str,
) -> None:
    if not workspace_roots:
        return
    if "default" in workspace_roots:
        expected = workspace_roots["default"]
        if len(contexts) != 1 or contexts[0].get("workspacePrefix") != ("" if expected == "." else expected):
            raise TaskRunnerError(
                "code_workspace_contract_mismatch",
                contract=contract_name,
                expectedWorkspaceRoots=workspace_roots,
                requestedCodeWorkspaces=[item.get("requestedPath") for item in contexts],
                workspacePrefixes=[item.get("workspacePrefix") for item in contexts],
            )
        return
    actual = {
        str(item.get("repository")): str(item.get("workspacePrefix") or ".")
        for item in contexts
    }
    if actual != workspace_roots:
        raise TaskRunnerError(
            "code_workspace_contract_mismatch",
            contract=contract_name,
            expectedWorkspaceRoots=workspace_roots,
            actualWorkspaceRoots=actual,
            requestedCodeWorkspaces=[item.get("requestedPath") for item in contexts],
        )


def _assert_workspace_ref_matches(
    workspace_ref: Any,
    contexts: list[dict[str, str]],
    *,
    contract_name: str,
) -> None:
    if len(contexts) != 1:
        raise TaskRunnerError(
            "task_workspace_ref_requires_single_repository",
            contract=contract_name,
            workspaceRef=workspace_ref,
        )
    actual = contexts[0].get("repository")
    if workspace_ref != "default" and workspace_ref != actual:
        raise TaskRunnerError(
            "task_workspace_ref_mismatch",
            contract=contract_name,
            workspaceRef=workspace_ref,
            actualRepository=actual,
        )


def _assert_validation_command_workspaces(
    commands: list[dict[str, Any]],
    workspace_roots: dict[str, str],
    repositories: RepositoryMap,
    *,
    contract_name: str,
) -> None:
    for index, command in enumerate(commands):
        repository_id, repo = _command_repository(command, repositories)
        cwd = command.get("cwd")
        command_dir = (repo / str(cwd)).resolve()
        if not command_dir.is_dir():
            raise TaskRunnerError(
                f"validation_cwd_missing:{command.get('id', index)}",
                contract=contract_name,
                cwd=cwd,
                repository=repository_id,
            )
        manifests = validation_command_manifest_names(command)
        if manifests and not any((command_dir / name).is_file() for name in manifests):
            raise TaskRunnerError(
                f"validation_manifest_missing:{command.get('id', index)}",
                contract=contract_name,
                cwd=cwd,
                repository=repository_id,
                expectedManifests=list(manifests),
            )


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


def _git_untracked_files(repo: Path) -> list[str]:
    try:
        return capture_untracked_files(repo)
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


def _merge_file_changes(
    *change_sets: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    """Keep a stable, de-duplicated history of changes across task runs."""

    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for change_set in change_sets:
        if not isinstance(change_set, list):
            continue
        for change in change_set:
            if not isinstance(change, dict):
                continue
            normalized = {
                str(key): value
                for key, value in change.items()
                if isinstance(value, str)
            }
            if not normalized:
                continue
            key = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(normalized)
    return merged


def _historical_task_file_changes(
    feature_dir: Path,
    task_id: str,
    current_run_id: str,
) -> list[dict[str, str]]:
    """Read implementation changes from prior runs, including forced aborts."""

    run_dir = _runs_dir(feature_dir, task_id)
    if not run_dir.is_dir():
        return []
    historical: list[dict[str, str]] = []
    for run_path in sorted(run_dir.glob("*.json")):
        if run_path.stem == current_run_id:
            continue
        try:
            state = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TaskRunnerError(f"invalid_historical_task_run:{run_path.name}") from exc
        if not isinstance(state, dict) or state.get("taskId") != task_id:
            continue
        abort_changes = state.get("fileChangesAtAbort")
        run_changes = state.get("fileChanges")
        historical.extend(
            _merge_file_changes(
                abort_changes if isinstance(abort_changes, list) else None,
                run_changes if isinstance(run_changes, list) else None,
            )
        )
    return _merge_file_changes(historical)


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
        untracked_files = _git_untracked_files(repo)
        repo_changes = _snapshot_changes(before, after)
        if multiple:
            for change in repo_changes:
                change["path"] = f"{repository_id}:{change['path']}"
                if "fromPath" in change:
                    change["fromPath"] = f"{repository_id}:{change['fromPath']}"
                change["repository"] = repository_id
        changes.extend(repo_changes)
        final.append(
            {
                "id": repository_id,
                "path": str(repo),
                "snapshot": after,
                "untrackedFiles": untracked_files,
            }
        )
    return changes, final


def _requested_workspace_relative_path(
    state: dict[str, Any],
    repository_id: str,
    path: str,
) -> str | None:
    contexts = state.get("scopeWorkspaces")
    if not isinstance(contexts, list):
        return None
    for context in contexts:
        if not isinstance(context, dict) or context.get("repository") != repository_id:
            continue
        prefix = context.get("workspacePrefix")
        if not isinstance(prefix, str):
            return None
        if not prefix:
            return path
        if path.startswith(f"{prefix}/"):
            return path[len(prefix) + 1 :]
        return None
    return None


def _is_transient_validation_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if parts and parts[0] in {"test", "tests"}:
        return True
    return any(parts[index : index + 2] == ("src", "test") for index in range(len(parts) - 1))


def _partition_transient_validation_changes(
    state: dict[str, Any],
    file_changes: list[dict[str, str]],
    final_repositories: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[str]]:
    repositories = {
        str(item.get("id")): item
        for item in final_repositories
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    default_repository_id = next(iter(repositories)) if len(repositories) == 1 else None
    formal_changes: list[dict[str, str]] = []
    transient_files: list[str] = []
    for change in file_changes:
        display_path = change.get("path")
        repository_id = change.get("repository") or default_repository_id
        if not isinstance(display_path, str) or not isinstance(repository_id, str):
            formal_changes.append(change)
            continue
        repository_path = display_path
        if change.get("repository") == repository_id and display_path.startswith(f"{repository_id}:"):
            repository_path = display_path[len(repository_id) + 1 :]
        untracked = repositories.get(repository_id, {}).get("untrackedFiles")
        relative_path = _requested_workspace_relative_path(state, repository_id, repository_path)
        if (
            change.get("operation") == "created"
            and isinstance(untracked, list)
            and repository_path in untracked
            and isinstance(relative_path, str)
            and _is_transient_validation_path(relative_path)
        ):
            transient_files.append(display_path)
            continue
        formal_changes.append(change)
    return formal_changes, sorted(set(transient_files))


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{stamp}-{uuid.uuid4().hex[:8]}"


def _verify_task_run_integrity(state: dict[str, Any]) -> None:
    error = task_run_integrity_error(state)
    if error is not None:
        raise TaskRunnerError(error)


def _save_run(path: Path, state: dict[str, Any]) -> None:
    _verify_task_run_integrity(state)
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
        if item.get("status") not in {"implemented", "done", "failed", "aborted"}:
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
    if deferred_task_validation_enabled(plan.root):
        batch = plan.batches.get(batch_id)
        task_validation = batch.get("taskValidation") if isinstance(batch, dict) else None
        if isinstance(task_validation, dict) and task_validation.get("status") == "running":
            raise TaskRunnerError(
                f"task_validation_workspace_frozen:{batch_id}",
                requiredAction="resume_batch_task_validation",
                activeRunId=task_validation.get("activeRunId"),
                currentTaskId=task_validation.get("currentTaskId"),
            )
        task_status = normalize_status(task.get("status"))
        if task_status == "failed":
            raise TaskRunnerError(
                f"deferred_validation_repair_requires_explicit_start:{task_id}",
                requiredAction="start_validation_repair",
            )
        if task_status == "implemented":
            raise TaskRunnerError(f"task_implementation_already_ready:{task_id}")
    if task.get("blockers"):
        raise TaskRunnerError(f"task_has_blockers:{task_id}")
    if unfinished := _unfinished_dependencies(plan, task):
        raise TaskRunnerError("unfinished_task_dependencies:" + ",".join(unfinished))
    if normalize_status(task.get("status")) == "done":
        raise TaskRunnerError(f"task_already_done:{task_id}")
    requested_workspaces = (
        [code_workspace] if isinstance(code_workspace, Path) else list(code_workspace)
    )
    if len(requested_workspaces) != 1:
        raise TaskRunnerError(
            "task_requires_single_code_workspace",
            taskId=task_id,
            workspaceRef=task.get("workspaceRef"),
            requestedCodeWorkspaces=[str(path.resolve()) for path in requested_workspaces],
        )
    repositories = _resolve_repositories(requested_workspaces)
    _assert_runtime_artifacts_ignored(repositories)
    scope_workspaces = _scope_workspaces(requested_workspaces, repositories)
    workspace_roots = task_workspace_roots(task)
    _assert_workspace_ref_matches(task.get("workspaceRef"), scope_workspaces, contract_name=task_id)
    _assert_workspace_roots_match(workspace_roots, scope_workspaces, contract_name=task_id)
    _assert_validation_command_workspaces(
        [item for item in task.get("validationCommands", []) if isinstance(item, dict)],
        workspace_roots,
        repositories,
        contract_name=task_id,
    )
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
        "version": 2,
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
    pending_revalidation = task.get("pendingRevalidation")
    if isinstance(pending_revalidation, dict):
        state["revalidation"] = dict(pending_revalidation)
    state["integritySha256"] = task_run_integrity_sha256(state)
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
    _verify_task_run_integrity(state)
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
    expected_transient_validation_files = state.get("transientValidationFiles", [])
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
        if record.get("transientValidationFiles", []) != expected_transient_validation_files:
            raise TaskRunnerError(
                f"task_run_evidence_transient_validation_files_mismatch:{evidence_id}"
            )
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
        if record.get("transientValidationFiles", []) != state.get("transientValidationFiles", []):
            raise TaskRunnerError(
                f"streamed_run_evidence_transient_validation_files_mismatch:{evidence_id}"
            )
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


def _validation_environment_error(
    command: dict[str, Any],
    *,
    category: str,
    detail: str,
    retry_same_run: bool,
    run_id: str | None = None,
    batch_id: str | None = None,
    task_id: str | None = None,
) -> TaskRunnerError:
    command_id = str(command.get("id", ""))
    executable = str(command.get("argv", [""])[0])
    details: dict[str, Any] = {
        "requiredAction": (
            "fix_validation_environment_and_retry_same_run"
            if retry_same_run
            else "fix_validation_environment_and_retry_batch_validation"
        ),
        "failureCategory": category,
        "commandId": command_id,
        "executable": executable,
        "cwd": command.get("cwd"),
        "detail": detail,
        "userMessage": (
            f"校验命令 {command_id} 无法运行（{category}）。请修复验证环境后重新运行校验；"
            + ("继续使用原 runId。" if retry_same_run else "无需修改代码或 Plan。")
        ),
    }
    if run_id is not None:
        details["runId"] = run_id
    if batch_id is not None:
        details["batchId"] = batch_id
    if task_id is not None:
        details["taskId"] = task_id
    return TaskRunnerError(
        f"validation_environment_unavailable:{command_id}:{category}",
        **details,
    )


def _assert_validation_command_environment(
    command: dict[str, Any],
    repositories: RepositoryMap,
    *,
    retry_same_run: bool,
    run_id: str | None = None,
    batch_id: str | None = None,
    task_id: str | None = None,
) -> None:
    argv = command.get("argv")
    if not isinstance(argv, list) or not argv or not isinstance(argv[0], str):
        raise TaskRunnerError(f"invalid_validation_argv:{command.get('id', '')}")
    _, repo = _command_repository(command, repositories)
    command_cwd = (repo / str(command.get("cwd"))).resolve()
    executable = argv[0]
    has_path = "/" in executable or "\\" in executable or Path(executable).is_absolute()
    if has_path:
        candidate = Path(executable)
        if not candidate.is_absolute():
            candidate = command_cwd / candidate
        available = candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK))
    else:
        available = shutil.which(executable) is not None
    if not available:
        raise _validation_environment_error(
            command,
            category="executable_missing",
            detail=(
                f"executable={executable};cwd={command_cwd};"
                "check PATH or use the project wrapper (for example ./mvnw)"
            ),
            retry_same_run=retry_same_run,
            run_id=run_id,
            batch_id=batch_id,
            task_id=task_id,
        )


def _run_validation(
    command: dict[str, Any],
    repositories: RepositoryMap,
    *,
    run_id: str | None = None,
    batch_id: str | None = None,
    task_id: str | None = None,
    retry_same_run: bool = True,
) -> tuple[int, str]:
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
        _assert_validation_command_environment(
            command,
            repositories,
            retry_same_run=retry_same_run,
            run_id=run_id,
            batch_id=batch_id,
            task_id=task_id,
        )
        completed = subprocess.run(
            argv,
            cwd=command_cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = completed.stdout + completed.stderr
        if completed.returncode in {126, 127}:
            raise _validation_environment_error(
                command,
                category="executable_failed_to_start",
                detail=f"exitCode={completed.returncode};output={output[-2000:]}",
                retry_same_run=retry_same_run,
                run_id=run_id,
                batch_id=batch_id,
                task_id=task_id,
            )
        return completed.returncode, output
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        raise _validation_environment_error(
            command,
            category="command_timeout",
            detail=(
                f"timeoutSeconds={timeout};output="
                f"{(stdout + stderr)[-2000:]}"
            ),
            retry_same_run=retry_same_run,
            run_id=run_id,
            batch_id=batch_id,
            task_id=task_id,
        )
    except OSError as exc:
        raise _validation_environment_error(
            command,
            category="process_start_failed",
            detail=str(exc),
            retry_same_run=retry_same_run,
            run_id=run_id,
            batch_id=batch_id,
            task_id=task_id,
        ) from exc


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
    transient_validation_files: list[str],
    supporting_files: list[str],
    no_change_why: str | None,
    repository_id: str | None,
    revalidation: dict[str, Any] | None = None,
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
        "transientValidationFiles": transient_validation_files,
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
        **(
            {
                "attemptType": revalidation.get("attemptType"),
                "triggeredByBatchEvidenceIds": list(
                    revalidation.get("triggeredByBatchEvidenceIds", [])
                ),
                "supersedesEvidenceIds": list(revalidation.get("supersedesEvidenceIds", [])),
            }
            if isinstance(revalidation, dict)
            else {}
        ),
    }


def _implementation_record(
    *,
    feature: str,
    task: dict[str, Any],
    run_id: str,
    completion_mode: str,
    file_changes: list[dict[str, str]],
    transient_validation_files: list[str],
    supporting_files: list[str],
    no_change_why: str | None,
) -> dict[str, Any]:
    changed_files = _changed_files(file_changes)
    no_code_change = completion_mode == "verified_existing"
    return {
        "featureId": feature,
        "checkpoint": "code_in_progress",
        "nodeId": "dev.code",
        "skill": "autodev-code",
        "taskId": task.get("id"),
        "action": "implementation",
        "runId": run_id,
        "completionMode": completion_mode,
        "summary": f"{task.get('id')} implementation ready for deferred validation",
        "implementation": {
            "noCodeChange": no_code_change,
            "whatChanged": [] if no_code_change else changed_files,
            "why": no_change_why if no_code_change else str(task.get("goal", "task implementation")),
        },
        "specRefs": task.get("specRefs", []),
        "designRefs": task.get("designRefs", []),
        "changedFiles": changed_files,
        "fileChanges": file_changes,
        "transientValidationFiles": transient_validation_files,
        "supportingFiles": supporting_files,
        "checkedCriteria": [],
    }


def _finish_implementation_unlocked(
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
    plan, batch_id, task = _load_plan_and_task(feature_dir, task_id, require_active_batch=False)
    if not deferred_task_validation_enabled(plan.root):
        raise TaskRunnerError(
            f"finish_implementation_requires_deferred_plan:{task_id}",
            requiredAction="use_complete_for_legacy_plan",
        )
    batch = plan.batches.get(batch_id)
    task_validation = batch.get("taskValidation") if isinstance(batch, dict) else None
    if isinstance(task_validation, dict) and task_validation.get("status") == "running":
        raise TaskRunnerError(f"task_validation_workspace_frozen:{batch_id}")
    path, state = _load_run(feature_dir, task_id, run_id)
    if state.get("taskContractSha256") != task_contract_sha256(task):
        raise TaskRunnerError(f"task_contract_changed_after_start:{task_id}")
    if state.get("batchId") not in {None, batch_id}:
        raise TaskRunnerError(f"task_batch_changed_after_start:{task_id}")
    requested_workspaces = [code_workspace] if isinstance(code_workspace, Path) else list(code_workspace)
    if len(requested_workspaces) != 1:
        raise TaskRunnerError(
            "task_requires_single_code_workspace",
            taskId=task_id,
            workspaceRef=task.get("workspaceRef"),
            requestedCodeWorkspaces=[str(path.resolve()) for path in requested_workspaces],
        )
    repositories = _resolve_repositories(requested_workspaces)
    _assert_repositories_match(state, repositories)
    _assert_requested_workspaces_match(state, requested_workspaces, repositories)
    if state.get("status") == "implemented":
        return True, state
    if state.get("status") in {"done", "failed", "evidence_written", "validation_running"}:
        raise TaskRunnerError(f"task_run_not_implementation_finishable:{state.get('status')}")

    file_changes, final_repositories = _repository_changes(state, repositories)
    file_changes, transient_validation_files = _partition_transient_validation_changes(
        state,
        file_changes,
        final_repositories,
    )
    historical_file_changes = _historical_task_file_changes(
        feature_dir,
        task_id,
        run_id,
    )
    cumulative_file_changes = _merge_file_changes(historical_file_changes, file_changes)
    _, scope_paths = _run_scope_paths(state, task)
    outside_workspace = [
        changed_path
        for change in cumulative_file_changes
        for changed_path in (change.get("path"), change.get("fromPath"))
        if isinstance(changed_path, str)
        and not _paths_within_requested_workspaces([changed_path], state)
    ]
    if outside_workspace:
        raise TaskRunnerError(
            "out_of_scope_changes_detected:" + ",".join(sorted(set(outside_workspace))),
            requiredAction="fix_workspace_and_retry_same_run",
            runId=run_id,
            changedFiles=_changed_files(cumulative_file_changes),
            resolvedScopePaths=scope_paths,
        )
    normalized_supporting = _validate_supporting_files(repositories, supporting_files)
    if cumulative_file_changes:
        if no_code_change_why or normalized_supporting:
            if not file_changes and no_code_change_why:
                conflict = _prior_aborted_run_conflict(
                    feature_dir,
                    task,
                    run_id,
                    repositories,
                    state,
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
            state,
        )
        if conflict:
            prior_run_id, prior_changed_files = conflict
            raise TaskRunnerError(
                f"verified_existing_conflicts_with_prior_run_changes:{prior_run_id}:"
                + ",".join(prior_changed_files),
                requiredAction="resume_original_run_or_rebuild_baseline",
            )
        commands = [item for item in task.get("validationCommands", []) if isinstance(item, dict)]
        if not any(
            item.get("kind") in BEHAVIOR_VALIDATION_KINDS
            for item in commands
            if item.get("required") is True
        ):
            raise TaskRunnerError("verified_existing_requires_behavior_validation")
        completion_mode = "verified_existing"
    _check_required_coverage(task)

    state.update({
        "status": "implementation_recording",
        "completionMode": completion_mode,
        "changedFiles": _changed_files(cumulative_file_changes),
        "fileChanges": cumulative_file_changes,
        "transientValidationFiles": transient_validation_files,
        "finalSnapshot": final_repositories[0]["snapshot"],
        "finalRepositories": final_repositories,
        "supportingFiles": normalized_supporting,
        "noCodeChangeWhy": no_code_change_why,
    })
    _save_run(path, state)
    existing = next(
        (
            record
            for record in read_records(stream_path(feature_dir))
            if record.get("action") == "implementation"
            and record.get("taskId") == task_id
            and record.get("runId") == run_id
        ),
        None,
    )
    if isinstance(existing, dict) and isinstance(existing.get("evidenceId"), str):
        evidence_id = str(existing["evidenceId"])
    else:
        try:
            evidence = append_evidence(
                feature_dir,
                _implementation_record(
                    feature=feature,
                    task=task,
                    run_id=run_id,
                    completion_mode=completion_mode,
                    file_changes=cumulative_file_changes,
                    transient_validation_files=transient_validation_files,
                    supporting_files=normalized_supporting,
                    no_change_why=no_code_change_why,
                ),
            )
        except EvidenceStoreError as exc:
            raise TaskRunnerError(f"evidence_append_failed:{exc}") from exc
        evidence_id = str(evidence["evidenceId"])
    result = record_task_implementation(
        workspace,
        feature,
        task_id,
        evidence_id,
        expected_task_contract_sha256=str(state.get("taskContractSha256", "")),
    )
    if not result.ok:
        raise TaskRunnerError("implementation_plan_binding_failed")
    state.update({
        "status": "implemented",
        "success": True,
        "evidenceIds": [evidence_id],
        "implementationEvidenceId": evidence_id,
    })
    if isinstance(result.data, dict):
        for field in ("batchContinuation", "taskValidation"):
            if isinstance(result.data.get(field), dict):
                state[field] = result.data[field]
    _save_run(path, state)
    return True, state


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
    plan, batch_id, task = _load_plan_and_task(feature_dir, task_id, require_active_batch=False)
    if deferred_task_validation_enabled(plan.root):
        raise TaskRunnerError(
            f"complete_disabled_for_deferred_validation:{task_id}",
            requiredAction="use_finish_implementation",
        )
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
            revalidation=state.get("revalidation") if isinstance(state.get("revalidation"), dict) else None,
        )
        if not result.ok:
            raise TaskRunnerError("plan_binding_failed")
        if isinstance(result.data, dict) and isinstance(result.data.get("batchHandoff"), dict):
            state["batchHandoff"] = result.data["batchHandoff"]
        if isinstance(result.data, dict) and isinstance(result.data.get("batchContinuation"), dict):
            state["batchContinuation"] = result.data["batchContinuation"]
        if isinstance(result.data, dict) and isinstance(result.data.get("batchCheck"), dict):
            state["batchCheck"] = result.data["batchCheck"]
        state["status"] = "done" if success else "failed"
        _save_run(path, state)
        return success, state
    if state.get("status") == "validation_running" and state.get("evidenceIds"):
        _validate_run_evidence(feature_dir, state)
        _save_run(path, state)

    repository_states = _state_repositories(state)
    multiple_repositories = len(repository_states) > 1
    file_changes, final_repositories = _repository_changes(state, repositories)
    file_changes, transient_validation_files = _partition_transient_validation_changes(
        state,
        file_changes,
        final_repositories,
    )
    historical_file_changes = _historical_task_file_changes(
        feature_dir,
        task_id,
        run_id,
    )
    cumulative_file_changes = _merge_file_changes(historical_file_changes, file_changes)
    _, scope_paths = _run_scope_paths(state, task)
    outside_workspace = [
        changed_path
        for change in cumulative_file_changes
        for changed_path in (change.get("path"), change.get("fromPath"))
        if isinstance(changed_path, str)
        and not _paths_within_requested_workspaces([changed_path], state)
    ]
    if outside_workspace:
        raise TaskRunnerError(
            "out_of_scope_changes_detected:" + ",".join(sorted(set(outside_workspace))),
            requiredAction="fix_workspace_and_retry_same_run",
            runId=run_id,
            changedFiles=_changed_files(cumulative_file_changes),
            resolvedScopePaths=scope_paths,
            requestedCodeWorkspaces=state.get("requestedCodeWorkspaces", []),
            resolvedGitRoots=[str(item) for item in repositories.values()],
        )
    normalized_supporting = _validate_supporting_files(repositories, supporting_files)
    if cumulative_file_changes:
        if no_code_change_why or normalized_supporting:
            if not file_changes and no_code_change_why:
                conflict = _prior_aborted_run_conflict(
                    feature_dir,
                    task,
                    run_id,
                    repositories,
                    state,
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
            state,
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
    changed_files = _changed_files(cumulative_file_changes)
    if state.get("status") == "validation_running":
        if (
            state.get("changedFiles") != changed_files
            or state.get("fileChanges") != cumulative_file_changes
        ):
            raise TaskRunnerError("task_run_workspace_changed_after_validation_started")
        if state.get("completionMode") != completion_mode:
            raise TaskRunnerError("task_run_completion_mode_changed")
    state.update(
        {
            "status": "validation_running",
            "completionMode": completion_mode,
            "changedFiles": changed_files,
            "fileChanges": cumulative_file_changes,
            "transientValidationFiles": transient_validation_files,
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
        exit_code, output = _run_validation(
            command,
            repositories,
            run_id=run_id,
            batch_id=batch_id,
            task_id=task_id,
        )
        if not _repository_snapshots_match(final_repositories, _repository_state(repositories)):
            raise TaskRunnerError(f"validation_modified_workspace:{command_id}")
        record = _record_for_command(
            feature=feature,
            task=task,
            run_id=run_id,
            command=command,
            exit_code=exit_code,
            completion_mode=completion_mode,
            file_changes=cumulative_file_changes,
            transient_validation_files=transient_validation_files,
            supporting_files=normalized_supporting,
            no_change_why=no_code_change_why,
            repository_id=command_repository_id if multiple_repositories or command.get("repo") else None,
            revalidation=state.get("revalidation") if isinstance(state.get("revalidation"), dict) else None,
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
        revalidation=state.get("revalidation") if isinstance(state.get("revalidation"), dict) else None,
    )
    if not result.ok:
        raise TaskRunnerError("plan_binding_failed")
    if isinstance(result.data, dict) and isinstance(result.data.get("batchHandoff"), dict):
        state["batchHandoff"] = result.data["batchHandoff"]
    if isinstance(result.data, dict) and isinstance(result.data.get("batchContinuation"), dict):
        state["batchContinuation"] = result.data["batchContinuation"]
    if isinstance(result.data, dict) and isinstance(result.data.get("batchCheck"), dict):
        state["batchCheck"] = result.data["batchCheck"]
    batch_check = state.get("batchCheck")
    if success and isinstance(batch_check, dict) and batch_check.get("mode") == "task_covered":
        closure = _close_task_covered_batch(
            workspace,
            feature,
            str(batch_check.get("activeBatchId")),
            run_id,
        )
        state["batchCheck"] = closure
        state["batchClosureEvidenceId"] = closure["closureEvidenceId"]
        if isinstance(closure.get("batchHandoff"), dict):
            state["batchHandoff"] = closure["batchHandoff"]
    state["status"] = "done" if success else "failed"
    _save_run(path, state)
    return success, state


def _batch_commands_sha256(commands: list[dict[str, Any]]) -> str:
    content = json.dumps(commands, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _repository_state_sha256(repository_state: list[dict[str, Any]]) -> str:
    content = json.dumps(
        repository_state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _task_validation_run_integrity_sha256(state: dict[str, Any]) -> str:
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
    content = json.dumps(
        {field: state.get(field) for field in fields},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _save_task_validation_run(path: Path, state: dict[str, Any]) -> None:
    expected = _task_validation_run_integrity_sha256(state)
    stored = state.get("integritySha256")
    if stored is not None and stored != expected:
        raise TaskRunnerError("task_validation_run_integrity_mismatch")
    state["integritySha256"] = expected
    state["updatedAt"] = _utc_now()
    atomic_write_json(path, state)


def _load_task_validation_run(
    feature_dir: Path,
    batch_id: str,
    run_id: str,
) -> tuple[Path, dict[str, Any]]:
    path = _task_validation_run_path(feature_dir, batch_id, run_id)
    if not path.is_file():
        raise TaskRunnerError(f"task_validation_run_not_found:{run_id}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskRunnerError(f"invalid_task_validation_run:{run_id}") from exc
    if not isinstance(state, dict) or state.get("batchId") != batch_id:
        raise TaskRunnerError(f"invalid_task_validation_run:{run_id}")
    if state.get("integritySha256") != _task_validation_run_integrity_sha256(state):
        raise TaskRunnerError("task_validation_run_integrity_mismatch")
    return path, state


def _task_validation_context(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "featureId": state.get("featureId"),
        "batchId": state.get("batchId"),
        "runId": state.get("runId"),
        "taskOrder": state.get("taskOrder", []),
        "currentTaskId": state.get("currentTaskId"),
        "requestedCodeWorkspaces": state.get("requestedCodeWorkspaces", []),
        "batchSnapshotSha256": state.get("batchSnapshotSha256"),
        "agentScope": "task_and_batch_validation_commands",
        "allowedRunnerCommands": ["validate-batch-task", "batch-check"],
    }


def _start_deferred_task_validation_unlocked(
    workspace: Path,
    feature: str,
    batch_id: str,
    code_workspace: Path | list[Path],
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    try:
        bundle = load_plan_bundle(feature_dir)
    except ValueError as exc:
        raise TaskRunnerError(f"invalid_plan_json:{exc}") from exc
    if not deferred_task_validation_enabled(bundle.root):
        raise TaskRunnerError(f"deferred_task_validation_not_enabled:{batch_id}")
    if bundle.root.get("activeBatchId") != batch_id:
        raise TaskRunnerError(f"batch_not_active:{batch_id}")
    batch = bundle.batches.get(batch_id)
    if not isinstance(batch, dict):
        raise TaskRunnerError(f"batch_not_found:{batch_id}")
    validation = batch.get("taskValidation")
    validation_status = validation.get("status") if isinstance(validation, dict) else None
    if not isinstance(validation, dict) or validation_status not in {"ready", "failed"}:
        raise TaskRunnerError(f"task_validation_not_startable:{batch_id}:{validation_status}")
    if _active_feature_runs(feature_dir):
        raise TaskRunnerError("active_task_run_blocks_batch_task_validation")
    requested_workspaces = [code_workspace] if isinstance(code_workspace, Path) else list(code_workspace)
    if len(requested_workspaces) != 1:
        raise TaskRunnerError(
            "batch_task_validation_requires_single_code_workspace",
            batchId=batch_id,
            requestedCodeWorkspaces=[str(path.resolve()) for path in requested_workspaces],
        )
    repositories = _resolve_repositories(requested_workspaces)
    _assert_runtime_artifacts_ignored(repositories)
    scope_workspaces = _scope_workspaces(requested_workspaces, repositories)
    for task in batch.get("tasks", []):
        if not isinstance(task, dict):
            continue
        workspace_roots = task_workspace_roots(task)
        _assert_workspace_ref_matches(
            task.get("workspaceRef"),
            scope_workspaces,
            contract_name=str(task.get("id")),
        )
        _assert_workspace_roots_match(
            workspace_roots,
            scope_workspaces,
            contract_name=str(task.get("id")),
        )
        task_commands = [
            item for item in task.get("validationCommands", []) if isinstance(item, dict)
        ]
        _assert_validation_command_workspaces(
            task_commands,
            workspace_roots,
            repositories,
            contract_name=str(task.get("id")),
        )
        for command in task_commands:
            _assert_validation_command_environment(
                command,
                repositories,
                retry_same_run=False,
                batch_id=batch_id,
                task_id=str(task.get("id")),
            )
    batch_validation = batch.get("batchValidation")
    batch_commands = (
        [
            command
            for command in batch_validation.get("commands", [])
            if isinstance(command, dict)
        ]
        if isinstance(batch_validation, dict)
        else []
    )
    for command in batch_commands:
        _assert_validation_command_environment(
            command,
            repositories,
            retry_same_run=False,
            batch_id=batch_id,
        )
    baseline = _repository_state(repositories)
    snapshot_sha256 = _repository_state_sha256(baseline)
    run_id = _new_run_id()
    result = start_deferred_task_validation(
        workspace,
        feature,
        batch_id,
        run_id,
        snapshot_sha256,
    )
    if not result.ok:
        detail = ";".join(
            f"{item.get('reason')}:{item.get('detail', '')}"
            for item in result.errors or []
            if isinstance(item, dict)
        )
        raise TaskRunnerError(detail or "task_validation_plan_start_failed")
    refreshed = load_plan_bundle(feature_dir)
    refreshed_batch = refreshed.batches[batch_id]
    refreshed_validation = refreshed_batch["taskValidation"]
    state = {
        "version": 1,
        "runId": run_id,
        "featureId": feature,
        "batchId": batch_id,
        "status": "running",
        "taskOrder": list(refreshed_validation.get("taskOrder", [])),
        "currentTaskId": refreshed_validation.get("currentTaskId"),
        "completedTaskIds": list(refreshed_validation.get("completedTaskIds", [])),
        "taskContractSha256ByTask": {
            str(task.get("id")): task_contract_sha256(task)
            for task in refreshed_batch.get("tasks", [])
            if isinstance(task, dict)
        },
        "requestedCodeWorkspaces": [str(path.resolve()) for path in requested_workspaces],
        "repositories": baseline,
        "batchSnapshotSha256": snapshot_sha256,
        "completedCommandEvidence": {},
        "evidenceIds": [],
        "startedAt": _utc_now(),
    }
    _save_task_validation_run(
        _task_validation_run_path(feature_dir, batch_id, run_id),
        state,
    )
    return state


def _deferred_validation_implementation_context(
    feature_dir: Path,
    task: dict[str, Any],
) -> tuple[str, list[dict[str, str]], list[str], str | None, list[str]]:
    evidence_id = task.get("latestImplementationEvidenceId")
    record = next(
        (
            item
            for item in read_records(stream_path(feature_dir))
            if item.get("evidenceId") == evidence_id
            and item.get("action") == "implementation"
            and item.get("taskId") == task.get("id")
        ),
        None,
    )
    if not isinstance(record, dict):
        raise TaskRunnerError(f"implementation_evidence_missing:{task.get('id')}:{evidence_id}")
    return (
        str(record.get("completionMode", "implemented")),
        list(record.get("fileChanges", [])) if isinstance(record.get("fileChanges"), list) else [],
        list(record.get("transientValidationFiles", []))
        if isinstance(record.get("transientValidationFiles"), list)
        else [],
        record.get("implementation", {}).get("why")
        if isinstance(record.get("implementation"), dict)
        else None,
        list(record.get("supportingFiles", [])) if isinstance(record.get("supportingFiles"), list) else [],
    )


def _adopt_deferred_validation_evidence(
    feature_dir: Path,
    state: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    task_id = str(task.get("id"))
    planned = {
        str(command.get("id")): command
        for command in task.get("validationCommands", [])
        if isinstance(command, dict) and isinstance(command.get("id"), str)
    }
    all_completed = state.get("completedCommandEvidence")
    all_completed = all_completed if isinstance(all_completed, dict) else {}
    completed = all_completed.get(task_id)
    completed = dict(completed) if isinstance(completed, dict) else {}
    for record in read_records(stream_path(feature_dir)):
        if (
            record.get("action") != "validation"
            or record.get("taskId") != task_id
            or record.get("runId") != state.get("runId")
            or record.get("validationTarget") != "batch_final_snapshot"
        ):
            continue
        validation = record.get("validation")
        command_id = validation.get("commandId") if isinstance(validation, dict) else None
        if not isinstance(command_id, str) or command_id not in planned:
            raise TaskRunnerError(f"streamed_task_validation_unplanned_command:{command_id}")
        evidence_id = record.get("evidenceId")
        existing = completed.get(command_id)
        if isinstance(existing, dict) and existing.get("evidenceId") != evidence_id:
            raise TaskRunnerError(f"duplicate_task_validation_command_evidence:{command_id}")
        completed[command_id] = {
            "evidenceId": evidence_id,
            "result": validation.get("result"),
            "required": validation.get("required"),
        }
    all_completed[task_id] = completed
    state["completedCommandEvidence"] = all_completed
    evidence_ids = state.get("evidenceIds")
    evidence_ids = list(evidence_ids) if isinstance(evidence_ids, list) else []
    for item in completed.values():
        evidence_id = item.get("evidenceId") if isinstance(item, dict) else None
        if isinstance(evidence_id, str) and evidence_id not in evidence_ids:
            evidence_ids.append(evidence_id)
    state["evidenceIds"] = evidence_ids
    return completed


def _fail_deferred_validation_for_workspace_change(
    workspace: Path,
    feature: str,
    batch_id: str,
    task_id: str,
    state: dict[str, Any],
    path: Path,
) -> None:
    completed_by_task = state.get("completedCommandEvidence")
    completed_by_task = completed_by_task if isinstance(completed_by_task, dict) else {}
    completed = completed_by_task.get(task_id)
    completed = completed if isinstance(completed, dict) else {}
    evidence_ids = [
        str(item.get("evidenceId"))
        for item in completed.values()
        if isinstance(item, dict) and isinstance(item.get("evidenceId"), str)
    ]
    result = record_deferred_task_validation_attempt(
        workspace,
        feature,
        batch_id,
        task_id,
        str(state.get("runId")),
        evidence_ids,
        completion_evidence_ids=[],
        success=False,
        batch_snapshot_sha256=str(state.get("batchSnapshotSha256")),
    )
    if not result.ok:
        raise TaskRunnerError("task_validation_workspace_failure_binding_failed")
    state.update({"status": "failed", "success": False, "failedTaskId": task_id})
    _save_task_validation_run(path, state)


def _validate_deferred_task_unlocked(
    workspace: Path,
    feature: str,
    batch_id: str,
    task_id: str,
    code_workspace: Path | list[Path],
    run_id: str,
) -> tuple[bool, dict[str, Any]]:
    feature_dir = _feature_dir(workspace, feature)
    path, state = _load_task_validation_run(feature_dir, batch_id, run_id)
    if state.get("status") in {"done", "failed"}:
        return state.get("status") == "done", state
    if state.get("currentTaskId") != task_id:
        raise TaskRunnerError(
            f"task_validation_out_of_order:expected={state.get('currentTaskId')};actual={task_id}"
        )
    bundle, actual_batch_id, task = _load_plan_and_task(
        feature_dir,
        task_id,
        require_active_batch=False,
    )
    if actual_batch_id != batch_id or not deferred_task_validation_enabled(bundle.root):
        raise TaskRunnerError(f"task_validation_contract_mismatch:{task_id}")
    plan_batch = bundle.batches.get(batch_id)
    plan_task_validation = (
        plan_batch.get("taskValidation") if isinstance(plan_batch, dict) else None
    )
    expected_contract = state.get("taskContractSha256ByTask", {}).get(task_id)
    if expected_contract != task_contract_sha256(task):
        raise TaskRunnerError(f"task_contract_changed_after_validation_start:{task_id}")
    requested_workspaces = [code_workspace] if isinstance(code_workspace, Path) else list(code_workspace)
    if len(requested_workspaces) != 1:
        raise TaskRunnerError(
            "batch_task_validation_requires_single_code_workspace",
            batchId=batch_id,
            requestedCodeWorkspaces=[str(path.resolve()) for path in requested_workspaces],
        )
    repositories = _resolve_repositories(requested_workspaces)
    _assert_repositories_match(state, repositories)
    scope_workspaces = _scope_workspaces(requested_workspaces, repositories)
    _assert_workspace_ref_matches(task.get("workspaceRef"), scope_workspaces, contract_name=task_id)
    if [str(workspace_path.resolve()) for workspace_path in requested_workspaces] != state.get(
        "requestedCodeWorkspaces"
    ):
        raise TaskRunnerError("task_validation_workspace_mismatch")
    current_repository_state = _repository_state(repositories)
    current_snapshot_sha256 = _repository_state_sha256(current_repository_state)
    expected_snapshot_sha256 = state.get("batchSnapshotSha256")
    workspace_changed = current_snapshot_sha256 != expected_snapshot_sha256
    recovering_passed_plan = (
        isinstance(plan_task_validation, dict)
        and plan_task_validation.get("status") == "passed"
        and plan_task_validation.get("lastRunId") == run_id
    )
    if workspace_changed and recovering_passed_plan:
        raise TaskRunnerError(
            "task_validation_workspace_changed_after_pass",
            requiredAction="restore_batch_snapshot_and_retry_same_run",
            runId=run_id,
            expectedBatchSnapshotSha256=expected_snapshot_sha256,
            currentBatchSnapshotSha256=current_snapshot_sha256,
        )
    if workspace_changed:
        _fail_deferred_validation_for_workspace_change(
            workspace,
            feature,
            batch_id,
            task_id,
            state,
            path,
        )
        raise TaskRunnerError(
            "task_validation_workspace_changed",
            requiredAction="start_validation_repair",
        )
    if (
        recovering_passed_plan
    ):
        batch_validation = plan_batch.get("batchValidation") if isinstance(plan_batch, dict) else None
        mode = (
            batch_validation.get("mode", "commands" if batch_validation.get("commands") else None)
            if isinstance(batch_validation, dict)
            else None
        )
        if mode == "task_covered" and batch_validation.get("status") != "passed":
            state["batchCheck"] = _close_task_covered_batch(
                workspace,
                feature,
                batch_id,
                run_id,
            )
        elif mode == "commands":
            state["batchCheck"] = {
                "requiredAction": "run_batch_check",
                "activeBatchId": batch_id,
                "mode": mode,
                "activeRunId": batch_validation.get("activeRunId"),
            }
        state.update({"status": "done", "success": True, "currentTaskId": None})
        _save_task_validation_run(path, state)
        return True, state
    completion_mode, file_changes, transient_files, no_change_why, supporting_files = (
        _deferred_validation_implementation_context(feature_dir, task)
    )
    completed = _adopt_deferred_validation_evidence(feature_dir, state, task)
    _save_task_validation_run(path, state)
    evidence_ids = [
        str(item.get("evidenceId"))
        for item in completed.values()
        if isinstance(item, dict) and isinstance(item.get("evidenceId"), str)
    ]
    pass_evidence_ids = [
        str(item.get("evidenceId"))
        for item in completed.values()
        if isinstance(item, dict)
        and isinstance(item.get("evidenceId"), str)
        and item.get("result") == "pass"
        and item.get("required") is True
    ]
    required_failed = any(
        isinstance(item, dict) and item.get("required") is True and item.get("result") != "pass"
        for item in completed.values()
    )
    for command in task.get("validationCommands", []):
        if not isinstance(command, dict):
            continue
        command_id = command.get("id")
        if isinstance(command_id, str) and command_id in completed:
            continue
        repository_id, _ = _command_repository(command, repositories)
        exit_code, output = _run_validation(
            command,
            repositories,
            run_id=run_id,
            batch_id=batch_id,
            task_id=task_id,
        )
        if _repository_state_sha256(_repository_state(repositories)) != state.get("batchSnapshotSha256"):
            _fail_deferred_validation_for_workspace_change(
                workspace,
                feature,
                batch_id,
                task_id,
                state,
                path,
            )
            raise TaskRunnerError(f"validation_modified_workspace:{command_id}")
        record = _record_for_command(
            feature=feature,
            task=task,
            run_id=run_id,
            command=command,
            exit_code=exit_code,
            completion_mode=completion_mode,
            file_changes=file_changes,
            transient_validation_files=transient_files,
            supporting_files=supporting_files,
            no_change_why=no_change_why,
            repository_id=repository_id if len(repositories) > 1 or command.get("repo") else None,
            revalidation=(
                task.get("pendingRevalidation")
                if isinstance(task.get("pendingRevalidation"), dict)
                else None
            ),
        )
        record.update({
            "validationTarget": "batch_final_snapshot",
            "batchId": batch_id,
            "batchSnapshotSha256": state.get("batchSnapshotSha256"),
            "implementationRevision": task.get("implementationRevision", 0),
            "latestImplementationEvidenceId": task.get("latestImplementationEvidenceId"),
        })
        try:
            evidence = append_evidence(
                feature_dir,
                record,
                output_tail=output,
                allow_during_task_validation=True,
            )
        except EvidenceStoreError as exc:
            raise TaskRunnerError(f"evidence_append_failed:{exc}") from exc
        evidence_id = str(evidence["evidenceId"])
        evidence_ids.append(evidence_id)
        attempt = {
            "evidenceId": evidence_id,
            "result": "pass" if exit_code == 0 else "fail",
            "required": command.get("required"),
        }
        if isinstance(command_id, str):
            completed[command_id] = attempt
        if command.get("required") is True and exit_code == 0:
            pass_evidence_ids.append(evidence_id)
        if command.get("required") is True and exit_code != 0:
            required_failed = True
        state["evidenceIds"] = [
            *[item for item in state.get("evidenceIds", []) if isinstance(item, str)],
            evidence_id,
        ]
        _save_task_validation_run(path, state)

    success = not required_failed
    result = record_deferred_task_validation_attempt(
        workspace,
        feature,
        batch_id,
        task_id,
        run_id,
        evidence_ids,
        completion_evidence_ids=pass_evidence_ids if success else [],
        success=success,
        batch_snapshot_sha256=str(state.get("batchSnapshotSha256")),
    )
    if not result.ok:
        raise TaskRunnerError("task_validation_plan_binding_failed")
    if not success:
        state.update({"status": "failed", "success": False, "failedTaskId": task_id})
        _save_task_validation_run(path, state)
        return False, state

    data = result.data if isinstance(result.data, dict) else {}
    next_task_id = data.get("nextTaskId")
    if isinstance(next_task_id, str):
        state["currentTaskId"] = next_task_id
        state["completedTaskIds"] = [
            *[item for item in state.get("completedTaskIds", []) if isinstance(item, str)],
            task_id,
        ]
        _save_task_validation_run(path, state)
        return True, state

    state["completedTaskIds"] = list(state.get("taskOrder", []))
    state["currentTaskId"] = None
    refreshed = load_plan_bundle(feature_dir)
    batch = refreshed.batches[batch_id]
    batch_validation = batch.get("batchValidation")
    mode = (
        batch_validation.get("mode", "commands" if batch_validation.get("commands") else None)
        if isinstance(batch_validation, dict)
        else None
    )
    if mode == "task_covered":
        closure = _close_task_covered_batch(workspace, feature, batch_id, run_id)
        state["batchCheck"] = closure
        state["batchClosureEvidenceId"] = closure.get("closureEvidenceId")
    else:
        state["batchCheck"] = {
            "requiredAction": "run_batch_check",
            "activeBatchId": batch_id,
            "mode": mode,
            "activeRunId": (
                batch_validation.get("activeRunId") if isinstance(batch_validation, dict) else None
            ),
        }
    state.update({"status": "done", "success": True})
    _save_task_validation_run(path, state)
    return True, state


def _task_covered_closure_record(
    *,
    feature: str,
    batch_id: str,
    run_id: str,
    coverage_command_ids: list[str],
    source_evidence_ids: list[str],
) -> dict[str, Any]:
    return {
        "featureId": feature,
        "checkpoint": "code_in_progress",
        "nodeId": "dev.code",
        "skill": "autodev-code",
        "taskId": "__batch__",
        "batchId": batch_id,
        "action": "batch_closure",
        "runId": run_id,
        "summary": f"{batch_id} covered by current task validation evidence",
        "specRefs": [],
        "designRefs": [],
        "changedFiles": [],
        "coverage": {
            "mode": "task_covered",
            "commandIds": coverage_command_ids,
            "sourceEvidenceIds": source_evidence_ids,
            "result": "pass",
        },
    }


def _close_task_covered_batch(
    workspace: Path,
    feature: str,
    batch_id: str,
    run_id: str,
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    bundle = load_plan_bundle(feature_dir)
    batch = bundle.batches.get(batch_id)
    if not isinstance(batch, dict):
        raise TaskRunnerError(f"batch_not_found:{batch_id}")
    if deferred_task_validation_enabled(bundle.root):
        task_validation = batch.get("taskValidation")
        if not isinstance(task_validation, dict) or task_validation.get("status") != "passed":
            raise TaskRunnerError(
                f"batch_check_requires_task_validation_passed:{batch_id}",
                requiredAction="run_batch_task_validation",
            )
    validation = batch.get("batchValidation")
    if not isinstance(validation, dict) or validation.get("mode") != "task_covered":
        raise TaskRunnerError(f"batch_validation_mode_mismatch:{batch_id}")
    coverage_command_ids = [
        item for item in validation.get("coverageCommandIds", []) if isinstance(item, str)
    ]
    records = read_records(stream_path(feature_dir))
    by_id = {
        str(record.get("evidenceId")): record
        for record in records
        if isinstance(record.get("evidenceId"), str)
    }
    source_by_command: dict[str, str] = {}
    for task in batch.get("tasks", []):
        if not isinstance(task, dict):
            continue
        for evidence_id in task.get("completionEvidenceIds", []):
            record = by_id.get(str(evidence_id))
            command = record.get("validation") if isinstance(record, dict) else None
            command_id = command.get("commandId") if isinstance(command, dict) else None
            if command_id in coverage_command_ids and isinstance(evidence_id, str):
                source_by_command[str(command_id)] = evidence_id
    missing = [command_id for command_id in coverage_command_ids if command_id not in source_by_command]
    if missing:
        raise TaskRunnerError("task_covered_evidence_missing:" + ",".join(missing))
    source_evidence_ids = [source_by_command[command_id] for command_id in coverage_command_ids]
    existing = next(
        (
            record
            for record in records
            if record.get("action") == "batch_closure"
            and record.get("taskId") == "__batch__"
            and record.get("batchId") == batch_id
            and record.get("runId") == run_id
        ),
        None,
    )
    if existing is None:
        evidence = append_evidence(
            feature_dir,
            _task_covered_closure_record(
                feature=feature,
                batch_id=batch_id,
                run_id=run_id,
                coverage_command_ids=coverage_command_ids,
                source_evidence_ids=source_evidence_ids,
            ),
        )
    else:
        coverage = existing.get("coverage")
        if not isinstance(coverage, dict) or coverage.get("commandIds") != coverage_command_ids or coverage.get(
            "sourceEvidenceIds"
        ) != source_evidence_ids:
            raise TaskRunnerError(f"batch_closure_evidence_mismatch:{batch_id}")
        evidence = existing
    evidence_id = evidence.get("evidenceId")
    if not isinstance(evidence_id, str):
        raise TaskRunnerError(f"batch_closure_evidence_invalid:{batch_id}")
    result = record_task_covered_batch(
        workspace,
        feature,
        batch_id,
        evidence_id,
        run_id=run_id,
    )
    if not result.ok:
        raise TaskRunnerError("batch_closure_plan_binding_failed")
    handoff = result.data.get("batchHandoff") if isinstance(result.data, dict) else None
    refreshed = load_plan_bundle(feature_dir)
    project_commands = refreshed.root.get("projectValidationCommands")
    required_action = (
        handoff.get("requiredAction")
        if isinstance(handoff, dict)
        else "run_project_check"
        if isinstance(project_commands, list) and project_commands
        else "code_done_ready"
    )
    return {
        "mode": "task_covered",
        "requiredAction": required_action,
        "activeBatchId": None,
        "closureEvidenceId": evidence_id,
        **({"batchHandoff": handoff} if isinstance(handoff, dict) else {}),
    }


def _record_for_batch_command(
    *,
    feature: str,
    batch_id: str,
    run_id: str,
    command: dict[str, Any],
    exit_code: int,
    file_changes: list[dict[str, str]],
    repository_id: str | None,
) -> dict[str, Any]:
    changed_files = _changed_files(file_changes)
    result = "pass" if exit_code == 0 else "fail"
    no_code_change = not file_changes
    return {
        "featureId": feature,
        "checkpoint": "code_in_progress",
        "nodeId": "dev.code",
        "skill": "autodev-code",
        "taskId": "__batch__",
        "batchId": batch_id,
        "action": "batch_validation",
        "detailVersion": 2,
        "runId": run_id,
        "completionMode": "verified_existing" if no_code_change else "implemented",
        "summary": f"{command.get('id')} batch validation {result}",
        "implementation": {
            "noCodeChange": no_code_change,
            "whatChanged": [] if no_code_change else changed_files,
            "why": (
                "Batch-level checks validate the completed workspace without changing files"
                if no_code_change
                else "Changes repair the failed batch validation"
            ),
        },
        "specRefs": [],
        "designRefs": [],
        "changedFiles": changed_files,
        "fileChanges": file_changes,
        "transientValidationFiles": [],
        "supportingFiles": [],
        "checkedCriteria": [str(command.get("id"))],
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


def _affected_tasks_for_batch_changes(
    batch: dict[str, Any],
    file_changes: list[dict[str, str]],
    requested_workspaces: list[Path],
    repositories: RepositoryMap,
) -> list[str]:
    changed_paths = _changed_files(file_changes)
    if not changed_paths:
        return []
    scope_workspaces = _scope_workspaces(requested_workspaces, repositories)
    if not _paths_within_workspace_contexts(changed_paths, scope_workspaces):
        outside = [
            path
            for path in changed_paths
            if not _paths_within_workspace_contexts([path], scope_workspaces)
        ]
        raise TaskRunnerError(
            "batch_fix_outside_workspace:" + ",".join(sorted(set(outside))),
            requiredAction="fix_workspace_and_retry_same_batch_run",
            changedFiles=changed_paths,
        )
    batch_task_ids: list[str] = []
    for task in batch.get("tasks", []):
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            continue
        task_id = str(task["id"])
        batch_task_ids.append(task_id)
    # A batch is validated against one final workspace snapshot. Without a
    # hard path allowlist, any repair change can affect the whole batch and
    # must conservatively trigger full TASK revalidation.
    return batch_task_ids


def _load_batch_run(feature_dir: Path, batch_id: str, run_id: str) -> tuple[Path, dict[str, Any]]:
    path = _batch_run_path(feature_dir, batch_id, run_id)
    if not path.is_file():
        raise TaskRunnerError(f"batch_run_not_found:{run_id}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TaskRunnerError(f"invalid_batch_run:{run_id}") from exc
    if not isinstance(state, dict) or state.get("batchId") != batch_id:
        raise TaskRunnerError(f"invalid_batch_run:{run_id}")
    return path, state


def _active_batch_run_ids(feature_dir: Path, batch_id: str) -> list[str]:
    active: list[str] = []
    for path in (feature_dir / ".batch-runs" / batch_id).glob("*.json"):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(state, dict)
            and state.get("batchId") == batch_id
            and state.get("status") not in {"done", "failed", "aborted"}
        ):
            run_id = state.get("runId")
            active.append(run_id if isinstance(run_id, str) else path.stem)
    return sorted(set(active))


def _batch_record_matches_attempt(
    record: dict[str, Any],
    *,
    state: dict[str, Any],
    command: dict[str, Any],
) -> None:
    evidence_id = str(record.get("evidenceId", ""))
    if (
        record.get("action") != "batch_validation"
        or record.get("taskId") != "__batch__"
        or record.get("batchId") != state.get("batchId")
        or record.get("runId") != state.get("runId")
    ):
        raise TaskRunnerError(f"batch_run_evidence_identity_mismatch:{evidence_id}")
    validation = record.get("validation")
    command_id = command.get("id")
    if not isinstance(validation, dict) or validation.get("commandId") != command_id:
        raise TaskRunnerError(f"batch_run_evidence_command_mismatch:{command_id}:id")
    for field in ("argv", "cwd", "kind", "required", "repo"):
        if validation.get(field) != command.get(field):
            raise TaskRunnerError(f"batch_run_evidence_command_mismatch:{command_id}:{field}")
    expected_file_changes = state.get("attemptFileChanges", [])
    if record.get("fileChanges") != expected_file_changes:
        raise TaskRunnerError(f"batch_run_evidence_file_changes_mismatch:{evidence_id}")
    if record.get("changedFiles") != _changed_files(expected_file_changes):
        raise TaskRunnerError(f"batch_run_evidence_changed_files_mismatch:{evidence_id}")


def _adopt_streamed_batch_evidence(
    feature_dir: Path,
    state: dict[str, Any],
    commands: list[dict[str, Any]],
) -> None:
    evidence_path = stream_path(feature_dir)
    if not evidence_path.is_file():
        return
    planned = {
        str(command.get("id")): command
        for command in commands
        if isinstance(command.get("id"), str)
    }
    completed = (
        dict(state.get("completedCommandEvidence"))
        if isinstance(state.get("completedCommandEvidence"), dict)
        else {}
    )
    historical_ids = set(state.get("evidenceIds", [])) if isinstance(state.get("evidenceIds"), list) else set()
    attempt_ids = (
        list(state.get("attemptEvidenceIds"))
        if isinstance(state.get("attemptEvidenceIds"), list)
        else []
    )
    for record in read_records(evidence_path):
        if (
            record.get("action") != "batch_validation"
            or record.get("batchId") != state.get("batchId")
            or record.get("runId") != state.get("runId")
        ):
            continue
        evidence_id = record.get("evidenceId")
        if not isinstance(evidence_id, str) or evidence_id in historical_ids or evidence_id in attempt_ids:
            continue
        validation = record.get("validation")
        command_id = validation.get("commandId") if isinstance(validation, dict) else None
        if not isinstance(command_id, str) or command_id not in planned:
            raise TaskRunnerError(f"streamed_batch_evidence_unplanned_command:{command_id}")
        if command_id in completed:
            raise TaskRunnerError(f"duplicate_batch_run_command_evidence:{command_id}")
        command = planned[command_id]
        _batch_record_matches_attempt(record, state=state, command=command)
        completed[command_id] = {
            "evidenceId": evidence_id,
            "result": validation.get("result"),
            "required": validation.get("required"),
        }
        attempt_ids.append(evidence_id)
    state["completedCommandEvidence"] = completed
    state["attemptEvidenceIds"] = attempt_ids


def _validate_batch_attempt_evidence(
    feature_dir: Path,
    state: dict[str, Any],
    commands: list[dict[str, Any]],
) -> None:
    records = {
        str(record.get("evidenceId")): record
        for record in read_records(stream_path(feature_dir))
        if isinstance(record.get("evidenceId"), str)
    }
    planned = {
        str(command.get("id")): command
        for command in commands
        if isinstance(command.get("id"), str)
    }
    completed = state.get("completedCommandEvidence")
    if not isinstance(completed, dict):
        raise TaskRunnerError("batch_run_completed_commands_missing")
    for command_id, attempt in completed.items():
        if command_id not in planned or not isinstance(attempt, dict):
            raise TaskRunnerError(f"invalid_batch_completed_command:{command_id}")
        evidence_id = attempt.get("evidenceId")
        record = records.get(str(evidence_id))
        if not isinstance(record, dict):
            raise TaskRunnerError(f"batch_run_evidence_missing:{evidence_id}")
        _batch_record_matches_attempt(record, state=state, command=planned[command_id])


def _bind_batch_attempt(
    workspace: Path,
    feature: str,
    batch_id: str,
    feature_dir: Path,
    path: Path,
    state: dict[str, Any],
    commands: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    binding = state.get("pendingBinding")
    if not isinstance(binding, dict):
        raise TaskRunnerError("batch_run_pending_binding_missing")
    _validate_batch_attempt_evidence(feature_dir, state, commands)
    attempt_evidence_ids = list(binding.get("evidenceIds", []))
    passing_evidence_ids = list(binding.get("passingEvidenceIds", []))
    success = binding.get("result") == "pass"
    affected_task_ids = [
        str(task_id)
        for task_id in binding.get("affectedTaskIds", [])
        if isinstance(task_id, str)
    ]
    if success and affected_task_ids:
        result = request_batch_revalidation(
            workspace,
            feature,
            batch_id,
            passing_evidence_ids,
            affected_task_ids=affected_task_ids,
            run_id=str(state.get("runId")),
            attempt_evidence_ids=attempt_evidence_ids,
        )
        if not result.ok:
            raise TaskRunnerError("batch_revalidation_plan_binding_failed")
        state["status"] = "revalidation_required"
        state["success"] = False
        refreshed = load_plan_bundle(feature_dir)
        deferred = deferred_task_validation_enabled(refreshed.root)
        state["requiredAction"] = (
            "run_batch_task_validation" if deferred else "revalidate_affected_tasks"
        )
        state["affectedTaskIds"] = (
            [
                str(task.get("id"))
                for task in refreshed.batches.get(batch_id, {}).get("tasks", [])
                if isinstance(task, dict)
            ]
            if deferred
            else affected_task_ids
        )
        state["revalidationBaselineRepositories"] = state.get("finalRepositories", [])
        state["triggeredByBatchEvidenceIds"] = passing_evidence_ids
        state.pop("pendingBinding", None)
        _save_run(path, state)
        return True, state

    result = record_batch_validation_attempt(
        workspace,
        feature,
        batch_id,
        attempt_evidence_ids,
        success=success,
        run_id=str(state.get("runId")),
        passing_evidence_ids=passing_evidence_ids,
    )
    if not result.ok:
        raise TaskRunnerError("batch_validation_plan_binding_failed")
    if isinstance(result.data, dict) and isinstance(result.data.get("batchHandoff"), dict):
        state["batchHandoff"] = result.data["batchHandoff"]
    state["status"] = "done" if success else "failed"
    state["success"] = success
    if success:
        state.pop("affectedTaskIds", None)
        state.pop("revalidationBaselineRepositories", None)
        state.pop("triggeredByBatchEvidenceIds", None)
    state["requiredAction"] = (
        state.get("batchHandoff", {}).get("requiredAction")
        if isinstance(state.get("batchHandoff"), dict)
        else "batch_validation_passed" if success else "fix_batch_and_retry_same_run"
    )
    state.pop("pendingBinding", None)
    _save_run(path, state)
    return success, state


def _run_batch_checks_unlocked(
    workspace: Path,
    feature: str,
    batch_id: str,
    code_workspace: Path | list[Path],
    run_id: str | None,
) -> tuple[bool, dict[str, Any]]:
    feature_dir = _feature_dir(workspace, feature)
    recovery = recover_plan_write_transaction(workspace, feature)
    if not recovery.ok:
        reasons = ";".join(
            str(error.get("reason", "unknown"))
            for error in recovery.errors or []
            if isinstance(error, dict)
        )
        raise TaskRunnerError(f"plan_write_transaction_recovery_failed:{reasons}")
    try:
        bundle = load_plan_bundle(feature_dir)
    except ValueError as exc:
        raise TaskRunnerError(f"invalid_plan_json:{exc}") from exc
    batch = bundle.batches.get(batch_id)
    if not isinstance(batch, dict):
        raise TaskRunnerError(f"batch_not_found:{batch_id}")
    validation = batch.get("batchValidation")
    if not isinstance(validation, dict):
        raise TaskRunnerError(f"batch_validation_contract_missing:{batch_id}")
    mode = validation.get("mode", "commands" if validation.get("commands") else None)
    if mode != "commands":
        raise TaskRunnerError(f"batch_check_not_required:{batch_id}:{mode}")
    commands = [item for item in validation.get("commands", []) if isinstance(item, dict)]
    if not commands or not any(command.get("required") is True for command in commands):
        raise TaskRunnerError(f"batch_validation_commands_missing:{batch_id}")
    commands_sha256 = _batch_commands_sha256(commands)

    requested_workspaces = [code_workspace] if isinstance(code_workspace, Path) else list(code_workspace)
    if len(requested_workspaces) != 1:
        raise TaskRunnerError(
            "batch_check_requires_single_code_workspace",
            batchId=batch_id,
            requestedCodeWorkspaces=[str(path.resolve()) for path in requested_workspaces],
        )
    repositories = _resolve_repositories(requested_workspaces)
    _assert_runtime_artifacts_ignored(repositories)
    scope_workspaces = _scope_workspaces(requested_workspaces, repositories)
    batch_workspace_root_sets = {
        tuple(sorted(task_workspace_roots(task).items()))
        for task in batch.get("tasks", [])
        if isinstance(task, dict) and task_workspace_roots(task)
    }
    batch_workspace_roots = (
        dict(next(iter(batch_workspace_root_sets)))
        if len(batch_workspace_root_sets) == 1
        else {}
    )
    _assert_workspace_roots_match(
        batch_workspace_roots,
        scope_workspaces,
        contract_name=batch_id,
    )
    for task in batch.get("tasks", []):
        if isinstance(task, dict):
            _assert_workspace_ref_matches(
                task.get("workspaceRef"),
                scope_workspaces,
                contract_name=str(task.get("id")),
            )
    _assert_validation_command_workspaces(
        commands,
        batch_workspace_roots,
        repositories,
        contract_name=batch_id,
    )
    for command in commands:
        _assert_validation_command_environment(
            command,
            repositories,
            retry_same_run=run_id is not None,
            run_id=run_id,
            batch_id=batch_id,
        )
    path: Path
    if run_id is None:
        if bundle.root.get("activeBatchId") != batch_id:
            raise TaskRunnerError(f"batch_validation_not_active:{batch_id}")
        unfinished = [
            str(task.get("id"))
            for task in batch.get("tasks", [])
            if isinstance(task, dict) and normalize_status(task.get("status")) != "done"
        ]
        if unfinished:
            raise TaskRunnerError("batch_validation_requires_tasks_done:" + ",".join(unfinished))
        active_run_id = validation.get("activeRunId")
        if isinstance(active_run_id, str):
            raise TaskRunnerError(
                f"active_batch_run_exists:{active_run_id}",
                requiredAction="retry_same_batch_run",
                runId=active_run_id,
            )
        orphaned_run_ids = _active_batch_run_ids(feature_dir, batch_id)
        if len(orphaned_run_ids) > 1:
            raise TaskRunnerError(
                "multiple_active_batch_runs:" + ",".join(orphaned_run_ids),
                requiredAction="inspect_batch_runs",
                runIds=orphaned_run_ids,
            )
        if orphaned_run_ids:
            orphaned_run_id = orphaned_run_ids[0]
            raise TaskRunnerError(
                f"active_batch_run_exists:{orphaned_run_id}",
                requiredAction="retry_same_batch_run",
                runId=orphaned_run_id,
            )
        run_id = _new_run_id()
        repository_state = _repository_state(repositories)
        state = {
            "version": 1,
            "runId": run_id,
            "featureId": feature,
            "batchId": batch_id,
            "status": "started",
            "commandsSha256": commands_sha256,
            "commands": commands,
            "codeWorkspace": str(next(iter(repositories.values()))),
            "requestedCodeWorkspaces": [item["requestedPath"] for item in scope_workspaces],
            "resolvedGitRoots": [item["resolvedGitRoot"] for item in scope_workspaces],
            "scopePathBase": "requested_code_workspace",
            "scopeWorkspaces": scope_workspaces,
            "repositories": repository_state,
            "snapshot": repository_state[0]["snapshot"],
            "snapshotMode": "git_visible_file_content_sha256",
            "startedAt": _utc_now(),
            "attempts": [],
            "evidenceIds": [],
        }
        path = _batch_run_path(feature_dir, batch_id, run_id)
        _save_run(path, state)
        started = start_batch_validation_run(workspace, feature, batch_id, run_id)
        if not started.ok:
            raise TaskRunnerError("batch_validation_start_binding_failed")
    else:
        path, state = _load_batch_run(feature_dir, batch_id, run_id)
        if state.get("commandsSha256") != commands_sha256:
            raise TaskRunnerError(f"batch_validation_commands_changed:{batch_id}")
        _assert_repositories_match(state, repositories)
        _assert_requested_workspaces_match(state, requested_workspaces, repositories)
        if state.get("status") not in {"done", "evidence_written"} and validation.get("activeRunId") is None:
            started = start_batch_validation_run(workspace, feature, batch_id, run_id)
            if not started.ok:
                raise TaskRunnerError("batch_validation_start_binding_failed")
        if state.get("status") == "done":
            return True, state
        if state.get("status") == "evidence_written":
            return _bind_batch_attempt(
                workspace,
                feature,
                batch_id,
                feature_dir,
                path,
                state,
                commands,
            )
        if bundle.root.get("activeBatchId") != batch_id:
            raise TaskRunnerError(f"batch_validation_not_active:{batch_id}")
        unfinished = [
            str(task.get("id"))
            for task in batch.get("tasks", [])
            if isinstance(task, dict) and normalize_status(task.get("status")) != "done"
        ]
        if unfinished:
            raise TaskRunnerError("batch_validation_requires_tasks_done:" + ",".join(unfinished))

    if state.get("status") != "validation_running":
        file_changes, _ = _repository_changes(state, repositories)
        revalidation_baseline = state.get("revalidationBaselineRepositories")
        if isinstance(revalidation_baseline, list) and revalidation_baseline:
            relevant_changes, _ = _repository_changes(
                {"repositories": revalidation_baseline},
                repositories,
            )
        else:
            relevant_changes = file_changes
        affected_task_ids = _affected_tasks_for_batch_changes(
            batch,
            relevant_changes,
            requested_workspaces,
            repositories,
        )
        validation_snapshot = _repository_state(repositories)
        state.update(
            {
                "status": "validation_running",
                "attemptNumber": len(state.get("attempts", [])) + 1,
                "attemptFileChanges": file_changes,
                "attemptAffectedTaskIds": affected_task_ids,
                "attemptRepositories": validation_snapshot,
                "attemptEvidenceIds": [],
                "completedCommandEvidence": {},
            }
        )
        _save_run(path, state)
    else:
        validation_snapshot = state.get("attemptRepositories")
        if not isinstance(validation_snapshot, list) or not _repository_snapshots_match(
            validation_snapshot,
            _repository_state(repositories),
        ):
            raise TaskRunnerError("batch_run_workspace_changed_after_validation_started")
        file_changes = list(state.get("attemptFileChanges", []))
        affected_task_ids = list(state.get("attemptAffectedTaskIds", []))

    _adopt_streamed_batch_evidence(feature_dir, state, commands)
    _save_run(path, state)
    completed_commands = state.get("completedCommandEvidence")
    completed_commands = completed_commands if isinstance(completed_commands, dict) else {}
    multiple_repositories = len(repositories) > 1
    for command in commands:
        command_id = str(command.get("id", ""))
        if command_id in completed_commands:
            continue
        repository_id, _ = _command_repository(command, repositories)
        exit_code, output = _run_validation(
            command,
            repositories,
            run_id=run_id,
            batch_id=batch_id,
        )
        if not _repository_snapshots_match(validation_snapshot, _repository_state(repositories)):
            raise TaskRunnerError(f"batch_validation_modified_workspace:{command.get('id', '')}")
        record = _record_for_batch_command(
            feature=feature,
            batch_id=batch_id,
            run_id=run_id,
            command=command,
            exit_code=exit_code,
            file_changes=file_changes,
            repository_id=repository_id if multiple_repositories or command.get("repo") else None,
        )
        evidence = append_evidence(feature_dir, record, output_tail=output)
        evidence_id = str(evidence["evidenceId"])
        completed_commands[command_id] = {
            "evidenceId": evidence_id,
            "result": "pass" if exit_code == 0 else "fail",
            "required": command.get("required"),
        }
        state["completedCommandEvidence"] = completed_commands
        state["attemptEvidenceIds"] = [
            str(completed_commands[str(item.get("id"))]["evidenceId"])
            for item in commands
            if str(item.get("id")) in completed_commands
        ]
        _save_run(path, state)

    attempt_evidence_ids = list(state.get("attemptEvidenceIds", []))
    passing_evidence_ids = [
        str(attempt.get("evidenceId"))
        for attempt in completed_commands.values()
        if isinstance(attempt, dict)
        and attempt.get("result") == "pass"
        and attempt.get("required") is True
    ]
    success = not any(
        isinstance(attempt, dict)
        and attempt.get("required") is True
        and attempt.get("result") != "pass"
        for attempt in completed_commands.values()
    )
    evidence_history = state.get("evidenceIds") if isinstance(state.get("evidenceIds"), list) else []
    state["evidenceIds"] = [
        *evidence_history,
        *(evidence_id for evidence_id in attempt_evidence_ids if evidence_id not in evidence_history),
    ]
    attempts = state.get("attempts") if isinstance(state.get("attempts"), list) else []
    attempts.append(
        {
            "attempt": len(attempts) + 1,
            "evidenceIds": attempt_evidence_ids,
            "passingEvidenceIds": passing_evidence_ids,
            "result": "pass" if success else "fail",
            "completedAt": _utc_now(),
        }
    )
    state["attempts"] = attempts
    state["finalRepositories"] = _repository_state(repositories)
    state["success"] = success
    state["status"] = "evidence_written"
    state["pendingBinding"] = {
        "result": "pass" if success else "fail",
        "evidenceIds": attempt_evidence_ids,
        "passingEvidenceIds": passing_evidence_ids if success else [],
        "affectedTaskIds": affected_task_ids if success else [],
    }
    _save_run(path, state)
    return _bind_batch_attempt(
        workspace,
        feature,
        batch_id,
        feature_dir,
        path,
        state,
        commands,
    )


def run_batch_checks(
    workspace: Path,
    feature: str,
    batch_id: str,
    code_workspace: Path | list[Path],
    run_id: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        return _run_batch_checks_unlocked(workspace, feature, batch_id, code_workspace, run_id)


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


def _paths_within_workspace_contexts(
    paths: list[str], contexts: list[dict[str, str]] | Any,
) -> bool:
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
        context = by_repository.get(repository_id) if repository_id is not None else contexts[0]
        if not isinstance(context, dict):
            return False
        prefix = context.get("workspacePrefix")
        if not isinstance(prefix, str):
            return False
        if not prefix:
            continue
        candidate = PurePosixPath(relative)
        workspace = PurePosixPath(prefix)
        if candidate != workspace and workspace not in candidate.parents:
            return False
    return True


def _paths_within_requested_workspaces(paths: list[str], state: dict[str, Any]) -> bool:
    # Legacy runs were rooted at the complete Git repository and did not
    # persist workspace contexts. Preserve that contract while new runs keep
    # the requested module boundary hard.
    if state.get("scopePathBase") != "requested_code_workspace":
        return True
    return _paths_within_workspace_contexts(paths, state.get("scopeWorkspaces"))


def _prior_aborted_run_conflict(
    feature_dir: Path,
    task: dict[str, Any],
    current_run_id: str,
    repositories: RepositoryMap,
    current_state: dict[str, Any],
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
                prior_changes, prior_final = _repository_changes(prior, repositories)
                prior_changes, _ = _partition_transient_validation_changes(
                    prior,
                    prior_changes,
                    prior_final,
                )
                changed = _changed_files(prior_changes)
            except TaskRunnerError:
                continue
        relevant = [
            item
            for item in changed
            if isinstance(item, str)
            and _paths_within_requested_workspaces([item], current_state)
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
    if state.get("status") in {
        "implementation_recording",
        "implemented",
        "evidence_written",
        "done",
        "failed",
    }:
        raise TaskRunnerError(f"task_run_cannot_abort:{state.get('status')}")
    requested_workspaces = (
        [code_workspace] if isinstance(code_workspace, Path) else list(code_workspace)
    )
    repositories = _resolve_repositories(requested_workspaces)
    _assert_repositories_match(state, repositories)
    _assert_requested_workspaces_match(state, requested_workspaces, repositories)
    file_changes, final_repositories = _repository_changes(state, repositories)
    file_changes, transient_validation_files = _partition_transient_validation_changes(
        state,
        file_changes,
        final_repositories,
    )
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
    if transient_validation_files:
        state["transientValidationFilesAtAbort"] = transient_validation_files
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
    project_commands = [
        command
        for command in bundle.root.get("projectValidationCommands", [])
        if isinstance(command, dict)
    ]
    for command in project_commands:
        _assert_validation_command_environment(
            command,
            repositories,
            retry_same_run=False,
        )
    project_snapshot = _repository_state(repositories)
    evidence_ids: list[str] = []
    required_failed = False
    run_id = _new_run_id()
    for command in project_commands:
        repository_id, _ = _command_repository(command, repositories)
        exit_code, output = _run_validation(
            command,
            repositories,
            run_id=run_id,
            retry_same_run=False,
        )
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


def finish_implementation(
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
        return _finish_implementation_unlocked(
            workspace,
            feature,
            task_id,
            code_workspace,
            run_id,
            no_code_change_why=no_code_change_why,
            supporting_files=supporting_files,
        )


def start_batch_task_validation(
    workspace: Path,
    feature: str,
    batch_id: str,
    code_workspace: Path | list[Path],
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        return _start_deferred_task_validation_unlocked(
            workspace,
            feature,
            batch_id,
            code_workspace,
        )


def validate_batch_task(
    workspace: Path,
    feature: str,
    batch_id: str,
    task_id: str,
    code_workspace: Path | list[Path],
    run_id: str,
) -> tuple[bool, dict[str, Any]]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        return _validate_deferred_task_unlocked(
            workspace,
            feature,
            batch_id,
            task_id,
            code_workspace,
            run_id,
        )


def start_validation_repair(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspace: Path | list[Path],
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        bundle, batch_id, task = _load_plan_and_task(feature_dir, task_id)
        if not deferred_task_validation_enabled(bundle.root):
            raise TaskRunnerError(f"deferred_task_validation_not_enabled:{task_id}")
        if normalize_status(task.get("status")) != "failed":
            raise TaskRunnerError(f"validation_repair_requires_failed_task:{task_id}")
        result = invalidate_deferred_task_validation_for_repair(
            workspace,
            feature,
            batch_id,
            task_id,
        )
        if not result.ok:
            raise TaskRunnerError("task_validation_repair_plan_binding_failed")
        return _start_task_unlocked(workspace, feature, task_id, code_workspace)


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
        batch_plan = bundle.batches.get(active_batch_id)
        batch_tasks = batch_plan.get("tasks", []) if isinstance(batch_plan, dict) else []
        if deferred_task_validation_enabled(bundle.root):
            task_validation = batch_plan.get("taskValidation") if isinstance(batch_plan, dict) else None
            if not isinstance(task_validation, dict):
                raise TaskRunnerError(f"task_validation_contract_missing:{active_batch_id}")
            task_validation_status = task_validation.get("status")
            if task_validation_status == "ready":
                return {
                    "action": "run_batch_task_validation",
                    "activeBatchId": active_batch_id,
                    "executionLane": execution_lane,
                    "taskIds": list(task_validation.get("taskOrder", [])),
                    "activatedFromHandoff": activated_from_handoff,
                    "userMessage": (
                        f"批次 {active_batch_id} 的实现已全部就绪，"
                        "启动一个批次级独立验证子代理，按 TASK 顺序执行全部校验。"
                    ),
                }
            if task_validation_status == "running":
                active_run_id = task_validation.get("activeRunId")
                if not isinstance(active_run_id, str):
                    raise TaskRunnerError(f"task_validation_active_run_missing:{active_batch_id}")
                _, validation_run = _load_task_validation_run(
                    feature_dir,
                    active_batch_id,
                    active_run_id,
                )
                return {
                    "action": "resume_batch_task_validation",
                    "activeBatchId": active_batch_id,
                    "executionLane": execution_lane,
                    "activeRunId": active_run_id,
                    "currentTaskId": task_validation.get("currentTaskId"),
                    "validationContext": _task_validation_context(validation_run),
                    "activatedFromHandoff": activated_from_handoff,
                    "userMessage": (
                        f"恢复批次 {active_batch_id} 的批次级独立验证子代理。"
                    ),
                }
            if task_validation_status == "failed":
                return {
                    "action": "fix_or_retry_task_validation",
                    "activeBatchId": active_batch_id,
                    "executionLane": execution_lane,
                    "failedTaskId": task_validation.get("currentTaskId"),
                    "lastRunId": task_validation.get("lastRunId"),
                    "activatedFromHandoff": activated_from_handoff,
                    "userMessage": (
                        f"批次 {active_batch_id} 的 TASK 验证失败；"
                        "工作区未变化时可重试，修改源码前必须启动 validation repair。"
                    ),
                }
            if task_validation_status in {"pending", "invalidated"}:
                return {
                    "action": "execute_active_batch",
                    "activeBatchId": active_batch_id,
                    "executionLane": execution_lane,
                    "taskIds": list(entry.get("taskIds", [])),
                    "activatedFromHandoff": activated_from_handoff,
                    "userMessage": f"继续实现批次 {active_batch_id}。",
                }
            if task_validation_status != "passed":
                raise TaskRunnerError(
                    f"task_validation_status_invalid:{active_batch_id}:{task_validation_status}"
                )
        all_tasks_done = bool(batch_tasks) and all(
            isinstance(task, dict) and normalize_status(task.get("status")) == "done"
            for task in batch_tasks
        )
        batch_validation = batch_plan.get("batchValidation") if isinstance(batch_plan, dict) else None
        if all_tasks_done and isinstance(batch_validation, dict) and batch_validation.get("status") != "passed":
            mode = batch_validation.get("mode", "commands" if batch_validation.get("commands") else None)
            if mode == "task_covered":
                if deferred_task_validation_enabled(bundle.root):
                    task_validation = batch_plan.get("taskValidation")
                    task_order = (
                        task_validation.get("taskOrder", [])
                        if isinstance(task_validation, dict)
                        else []
                    )
                    last_run_id = (
                        task_validation.get("lastRunId")
                        if isinstance(task_validation, dict)
                        else None
                    )
                    if not isinstance(last_run_id, str):
                        raise TaskRunnerError(f"task_validation_last_run_missing:{active_batch_id}")
                    _, validation_run = _load_task_validation_run(
                        feature_dir,
                        active_batch_id,
                        last_run_id,
                    )
                    return {
                        "action": "recover_batch_task_validation_closure",
                        "activeBatchId": active_batch_id,
                        "executionLane": execution_lane,
                        "activeRunId": last_run_id,
                        "currentTaskId": task_order[-1] if task_order else None,
                        "validationContext": _task_validation_context(validation_run),
                        "activatedFromHandoff": activated_from_handoff,
                        "userMessage": (
                            f"批次 {active_batch_id} 的 TASK 验证已通过但收口未绑定；"
                            "请恢复最后一个 deferred validation run。"
                        ),
                    }
                return {
                    "action": "recover_task_covered_batch",
                    "activeBatchId": active_batch_id,
                    "executionLane": execution_lane,
                    "batchValidationStatus": batch_validation.get("status"),
                    "activatedFromHandoff": activated_from_handoff,
                    "userMessage": (
                        f"批次 {active_batch_id} 的 TASK 已完成但收口未绑定；"
                        "请 inspect 并 recover 最后一个 TASK run。"
                    ),
                }
            if deferred_task_validation_enabled(bundle.root):
                task_validation = batch_plan.get("taskValidation")
                last_run_id = (
                    task_validation.get("lastRunId")
                    if isinstance(task_validation, dict)
                    else None
                )
                if not isinstance(last_run_id, str):
                    raise TaskRunnerError(f"task_validation_last_run_missing:{active_batch_id}")
                _, validation_run = _load_task_validation_run(
                    feature_dir,
                    active_batch_id,
                    last_run_id,
                )
                validation_context = _task_validation_context(validation_run)
            else:
                validation_context = None
            return {
                "action": (
                    "run_batch_check_in_validation_subagent"
                    if validation_context is not None
                    else "run_batch_check"
                ),
                "activeBatchId": active_batch_id,
                "executionLane": execution_lane,
                "batchValidationStatus": batch_validation.get("status"),
                "activeRunId": batch_validation.get("activeRunId"),
                **(
                    {"validationContext": validation_context}
                    if validation_context is not None
                    else {}
                ),
                "activatedFromHandoff": activated_from_handoff,
                "userMessage": f"批次 {active_batch_id} 的 TASK 已完成，开始执行批次级验证。",
            }
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
    project_commands = bundle.root.get("projectValidationCommands")
    if isinstance(project_commands, list) and not project_commands:
        return {
            "action": "code_done_ready",
            "activeBatchId": None,
            "activatedFromHandoff": False,
            "userMessage": "所有批次已完成，且没有额外的跨批次项目校验。",
        }
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
        batch_check = state.get("batchCheck")
        batch_check = batch_check if isinstance(batch_check, dict) else None
        return _emit(
            success,
            error=None if success else "validation_failed",
            runId=state.get("runId"),
            status=state.get("status"),
            completionMode=state.get("completionMode"),
            evidenceIds=state.get("evidenceIds", []),
            completionEvidenceIds=state.get("completionEvidenceIds", []),
            transientValidationFiles=state.get("transientValidationFiles", []),
            batchHandoff=batch_handoff,
            batchContinuation=batch_continuation,
            stopAfterBatch=bool(batch_handoff),
            continueCurrentBatch=(
                bool(batch_continuation.get("continueCurrentBatch")) if batch_continuation else False
            ),
            activeBatchId=(
                batch_continuation.get("activeBatchId")
                if batch_continuation
                else batch_check.get("activeBatchId") if batch_check else None
            ),
            nextTaskId=batch_continuation.get("nextTaskId") if batch_continuation else None,
            requiresNewConversation=(
                bool(batch_handoff.get("requiresNewConversation")) if batch_handoff else False
            ),
            requiredAction=(
                batch_handoff.get("requiredAction")
                if batch_handoff
                else batch_continuation.get("requiredAction")
                if batch_continuation
                else batch_check.get("requiredAction") if batch_check else None
            ),
            userMessage=batch_handoff.get("userMessage") if batch_handoff else None,
        )
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)


def _cmd_finish_implementation(args: argparse.Namespace) -> int:
    try:
        workspace, feature, code_workspace = _resolve(args)
        success, state = finish_implementation(
            workspace,
            feature,
            args.task_id,
            code_workspace,
            args.run_id,
            no_code_change_why=args.no_code_change_why,
            supporting_files=args.supporting_file or [],
        )
        continuation = state.get("batchContinuation")
        continuation = continuation if isinstance(continuation, dict) else None
        task_validation = state.get("taskValidation")
        task_validation = task_validation if isinstance(task_validation, dict) else None
        return _emit(
            success,
            runId=state.get("runId"),
            status=state.get("status"),
            completionMode=state.get("completionMode"),
            implementationEvidenceId=state.get("implementationEvidenceId"),
            changedFiles=state.get("changedFiles", []),
            transientValidationFiles=state.get("transientValidationFiles", []),
            batchContinuation=continuation,
            taskValidation=task_validation,
            continueCurrentBatch=bool(continuation),
            activeBatchId=(
                continuation.get("activeBatchId")
                if continuation
                else task_validation.get("activeBatchId") if task_validation else None
            ),
            nextTaskId=continuation.get("nextTaskId") if continuation else None,
            requiredAction=(
                continuation.get("requiredAction")
                if continuation
                else task_validation.get("requiredAction") if task_validation else None
            ),
        )
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)


def _cmd_start_batch_task_validation(args: argparse.Namespace) -> int:
    try:
        workspace, feature, code_workspace = _resolve(args)
        state = start_batch_task_validation(
            workspace,
            feature,
            args.batch_id,
            code_workspace,
        )
        validation_context = _task_validation_context(state)
        return _emit(
            True,
            runId=state.get("runId"),
            status=state.get("status"),
            activeBatchId=state.get("batchId"),
            currentTaskId=state.get("currentTaskId"),
            taskOrder=state.get("taskOrder", []),
            completedTaskIds=state.get("completedTaskIds", []),
            batchSnapshotSha256=state.get("batchSnapshotSha256"),
            requestedCodeWorkspaces=state.get("requestedCodeWorkspaces", []),
            validationContext=validation_context,
            requiredAction="spawn_batch_validation_subagent",
        )
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)


def _cmd_validate_batch_task(args: argparse.Namespace) -> int:
    try:
        workspace, feature, code_workspace = _resolve(args)
        success, state = validate_batch_task(
            workspace,
            feature,
            args.batch_id,
            args.task_id,
            code_workspace,
            args.run_id,
        )
        batch_check = state.get("batchCheck")
        batch_check = batch_check if isinstance(batch_check, dict) else None
        current_task_id = state.get("currentTaskId")
        validation_context = _task_validation_context(state)
        next_action = batch_check.get("requiredAction") if batch_check else "run_batch_check"
        if next_action == "run_batch_check":
            next_action = "run_batch_check_in_validation_subagent"
        return _emit(
            success,
            error=None if success else "validation_failed",
            runId=state.get("runId"),
            status=state.get("status"),
            activeBatchId=state.get("batchId"),
            validatedTaskId=args.task_id,
            currentTaskId=current_task_id,
            evidenceIds=state.get("evidenceIds", []),
            batchCheck=batch_check,
            validationContext=validation_context,
            requiredAction=(
                "fix_task_validation"
                if not success
                else "continue_batch_validation_subagent"
                if isinstance(current_task_id, str)
                else next_action
            ),
        )
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)


def _cmd_start_validation_repair(args: argparse.Namespace) -> int:
    try:
        workspace, feature, code_workspace = _resolve(args)
        state = start_validation_repair(workspace, feature, args.task_id, code_workspace)
        return _emit(True, **state)
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


def _cmd_batch_check(args: argparse.Namespace) -> int:
    try:
        workspace, feature, code_workspace = _resolve(args)
        success, state = run_batch_checks(
            workspace,
            feature,
            args.batch_id,
            code_workspace,
            args.run_id,
        )
        batch_handoff = state.get("batchHandoff")
        batch_handoff = batch_handoff if isinstance(batch_handoff, dict) else None
        return _emit(
            success,
            error=None if success else "batch_validation_failed",
            runId=state.get("runId"),
            status=state.get("status"),
            evidenceIds=(
                state.get("attempts", [])[-1].get("evidenceIds", [])
                if isinstance(state.get("attempts"), list) and state.get("attempts")
                else []
            ),
            allEvidenceIds=state.get("evidenceIds", []),
            requiredAction=state.get("requiredAction"),
            affectedTaskIds=state.get("affectedTaskIds", []),
            batchHandoff=batch_handoff,
            stopAfterBatch=bool(batch_handoff),
            requiresNewConversation=(
                bool(batch_handoff.get("requiresNewConversation")) if batch_handoff else False
            ),
            userMessage=batch_handoff.get("userMessage") if batch_handoff else None,
        )
    except (TaskRunnerError, EvidenceStoreError, ValueError) as exc:
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

    finish_implementation_parser = subparsers.add_parser("finish-implementation")
    common(finish_implementation_parser, needs_run=True)
    finish_implementation_parser.add_argument("--no-code-change-why")
    finish_implementation_parser.add_argument("--supporting-file", action="append")
    finish_implementation_parser.set_defaults(func=_cmd_finish_implementation)

    validation_repair = subparsers.add_parser("start-validation-repair")
    common(validation_repair)
    validation_repair.set_defaults(func=_cmd_start_validation_repair)

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

    batch_check = subparsers.add_parser("batch-check")
    batch_check.add_argument("--workspace")
    batch_check.add_argument("--feature")
    batch_check.add_argument("--batch-id", required=True)
    batch_check.add_argument("--code-workspace", required=True, action="append")
    batch_check.add_argument("--run-id")
    batch_check.set_defaults(func=_cmd_batch_check)

    start_task_validation = subparsers.add_parser("start-batch-task-validation")
    start_task_validation.add_argument("--workspace")
    start_task_validation.add_argument("--feature")
    start_task_validation.add_argument("--batch-id", required=True)
    start_task_validation.add_argument("--code-workspace", required=True, action="append")
    start_task_validation.set_defaults(func=_cmd_start_batch_task_validation)

    validate_task = subparsers.add_parser("validate-batch-task")
    validate_task.add_argument("--workspace")
    validate_task.add_argument("--feature")
    validate_task.add_argument("--batch-id", required=True)
    validate_task.add_argument("--task-id", required=True)
    validate_task.add_argument("--run-id", required=True)
    validate_task.add_argument("--code-workspace", required=True, action="append")
    validate_task.set_defaults(func=_cmd_validate_batch_task)

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
