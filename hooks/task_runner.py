#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transactional execution entrypoint for structured code tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_store import EvidenceStoreError, append_evidence, read_records, stream_path  # noqa: E402
from hooks.evidence_kernel import FileLock, unlink_if_exists  # noqa: E402
from hooks.code_exploration import CodeExplorationError, inspect_exploration_cache  # noqa: E402
from hooks.json_writer_common import atomic_write_json, resolve_feature, resolve_workspace  # noqa: E402
from hooks.plan_json import (  # noqa: E402
    BATCH_COMPILE_MAX_REPAIR_ATTEMPTS,
    EXECUTION_LANES,
    PlanBundle,
    defer_to_test_stages_enabled,
    bundle_unfinished_tasks,
    find_task,
    load_plan_bundle,
    normalize_status,
    task_contract_sha256,
    task_execution_lane,
    task_execution_mode,
    task_workspace_roots,
)
from hooks.plan_writer import (  # noqa: E402
    PlanWriterInputError,
    activate_batch as activate_plan_batch,
    begin_batch_compile_repair,
    mark_batch_tasks_done_after_compile,
    record_task_implementation,
    set_task_execution_status,
    update_batch_compile_status,
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
    strict_task_run_integrity_error,
    task_run_integrity_error,
    task_run_integrity_sha256,
)
from hooks.validation_policy import (  # noqa: E402
    command_policy_errors,
    compile_only_command_errors,
    compile_only_package_scripts_errors,
    maven_project_selector_workspace_errors,
    package_script_name,
    task_validation_kinds_for_lane,
)


DEFAULT_TIMEOUT_SECONDS = 300
VALIDATION_OUTPUT_POLL_SECONDS = 0.2
VALIDATION_PROGRESS_INTERVAL_SECONDS = 30.0
COMPILE_DIAGNOSTIC_DRAIN_SECONDS = 2.0
PROCESS_TERMINATION_GRACE_SECONDS = 3.0
VALIDATION_DIAGNOSTIC_BUFFER_BYTES = 64 * 1024
WINDOWS_BATCH_EXECUTABLE_SUFFIXES = frozenset({".bat", ".cmd"})
VALIDATION_DIAGNOSTIC_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/]|/)?[^\r\n]*?\.(?:java|kt|kts|groovy|scala|js|jsx|ts|tsx|vue|py))"
    r"(?=:\[?\d|:\s|$)",
    re.IGNORECASE,
)


class TaskRunnerError(ValueError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


@dataclass
class ValidationProcessResult:
    exit_code: int | None
    output: str
    termination_reason: str | None
    compile_category: str | None
    duration_seconds: float
    process_tree_terminated: bool


@dataclass(frozen=True)
class ValidationLaunchSpec:
    requested_argv: tuple[str, ...]
    resolved_executable: str
    launch_mode: str
    command_shell: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")




def _emit(ok: bool, **data: Any) -> int:
    print(json.dumps({"ok": ok, **data}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _emit_error(exc: ValueError) -> int:
    details = dict(exc.details) if isinstance(exc, TaskRunnerError) else {}
    message = str(exc)
    if "errorCategory" not in details:
        if "integrity" in message:
            details["errorCategory"] = "runner_integrity_failure"
        elif "workspace" in message and ("changed" in message or "mismatch" in message):
            details["errorCategory"] = "workspace_changed"
        elif message.startswith(("validation_command_policy_violation", "invalid_validation_")):
            details["errorCategory"] = "validation_contract_failure"
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
    task_id = str(task.get("id", ""))
    task_batch = plan.task_batches.get(task_id)
    deferred = defer_to_test_stages_enabled(plan.root)
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
        relative_path = _requested_workspace_relative_path(state, repository_id, repository_path)
        if (
            change.get("operation") == "created"
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


def _latest_sealed_prior_task_run(
    feature_dir: Path,
    feature: str,
    task: dict[str, Any],
) -> dict[str, Any] | None:
    task_id = str(task.get("id", ""))
    expected_contract = task_contract_sha256(task)
    candidates: list[dict[str, Any]] = []
    for path in (feature_dir / ".task-runs" / task_id).glob("*.json"):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(state, dict)
            and state.get("featureId") == feature
            and state.get("taskId") == task_id
            and state.get("taskContractSha256") == expected_contract
            and state.get("executionMode") == "code"
            and state.get("status") in {"implemented", "done", "failed", "aborted"}
            and isinstance(state.get("explorationGate"), dict)
            and strict_task_run_integrity_error(state) is None
        ):
            candidates.append(state)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda state: (str(state.get("startedAt", "")), str(state.get("runId", ""))),
    )


def _evidence_only_exploration_staleness(exploration: dict[str, Any]) -> bool:
    stale_reasons = exploration.get("staleReasons")
    return (
        exploration.get("status") == "stale"
        and not exploration.get("changedPaths")
        and not exploration.get("criticalHits")
        and isinstance(stale_reasons, list)
        and bool(stale_reasons)
        and all(
            isinstance(reason, str) and reason.startswith("implementation_evidence_invalid:")
            for reason in stale_reasons
        )
    )


def _start_task_unlocked(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspace: Path | list[Path],
    *,
    repair_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    plan, batch_id, task = _load_plan_and_task(feature_dir, task_id)
    if defer_to_test_stages_enabled(plan.root):
        batch = plan.batches.get(batch_id)
        batch_compile = batch.get("batchCompile") if isinstance(batch, dict) else None
        compile_status = batch_compile.get("status") if isinstance(batch_compile, dict) else None
        is_compile_repair = (
            isinstance(repair_context, dict)
            and repair_context.get("batchCompileRepair") is True
        )
        if compile_status == "failed" and not is_compile_repair:
            raise TaskRunnerError(
                f"batch_compile_repair_requires_explicit_start:{task_id}",
                requiredAction="start_batch_compile_repair",
                repairOwnerTaskIds=batch_compile.get("repairOwnerTaskIds", []),
            )
        if compile_status == "repairing" and not is_compile_repair:
            raise TaskRunnerError(
                f"batch_compile_repair_already_running:{batch_id}",
                requiredAction="continue_batch_compile_repair",
                repairTaskId=batch_compile.get("repairTaskId"),
            )
        if normalize_status(task.get("status")) == "implemented" and not is_compile_repair:
            raise TaskRunnerError(
                f"task_implementation_already_ready:{task_id}",
                requiredAction="run_batch_compile",
            )
    if task.get("blockers"):
        raise TaskRunnerError(f"task_has_blockers:{task_id}")
    unfinished = _unfinished_dependencies(plan, task)
    if unfinished:
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
    execution_mode = task_execution_mode(task)
    exploration_gate = None
    if execution_mode == "code":
        repository_gates: dict[str, Any] = {}
        observed_exploration: dict[str, dict[str, Any]] = {}
        unready_exploration: dict[str, dict[str, Any]] = {}
        for repository_id, repository_root in repositories.items():
            try:
                exploration = inspect_exploration_cache(
                    feature_dir,
                    plan,
                    task_id,
                    repository_root,
                )
            except (CodeExplorationError, RepositorySnapshotError) as exc:
                detail = str(exc)
                raise TaskRunnerError(
                    f"code_exploration_inspect_failed:{repository_id}:{detail}",
                    requiredAction=(
                        "repair_git_snapshot_and_retry_context"
                        if "git_snapshot_failed" in detail
                        else "repair_exploration_cache_and_retry_context"
                    ),
                    explorationBlocked=True,
                    implementationAllowed=False,
                ) from exc
            status = exploration.get("status")
            observed_exploration[repository_id] = {
                "status": status,
                "cachePath": exploration.get("cachePath"),
                "cacheSha256": exploration.get("cacheSha256"),
                "changedPaths": exploration.get("changedPaths", []),
                "criticalHits": exploration.get("criticalHits", []),
                "staleReasons": exploration.get("staleReasons", []),
            }
            if status not in {"fresh", "fresh_with_trusted_changes"}:
                unready_exploration[repository_id] = exploration
                continue
            repository_gates[repository_id] = {
                "status": status,
                "cachePath": exploration.get("cachePath"),
                "cacheSha256": exploration.get("cacheSha256"),
            }
        if unready_exploration:
            prior_run = _latest_sealed_prior_task_run(feature_dir, feature, task)
            prior_gate = prior_run.get("explorationGate") if isinstance(prior_run, dict) else None
            prior_repositories = (
                prior_gate.get("repositories") if isinstance(prior_gate, dict) else None
            )
            controlled_retry = isinstance(repair_context, dict) or all(
                _evidence_only_exploration_staleness(item)
                for item in unready_exploration.values()
            )
            if (
                controlled_retry
                and isinstance(prior_repositories, dict)
                and set(prior_repositories) == set(repositories)
            ):
                exploration_gate = {
                    "checkedAt": _utc_now(),
                    "source": "inherited_after_recheck",
                    "inheritedFromRunId": prior_run.get("runId"),
                    "inheritedGateCheckedAt": prior_gate.get("checkedAt"),
                    "observedRepositories": observed_exploration,
                    "repositories": dict(prior_repositories),
                }
            else:
                repository_id, exploration = next(iter(unready_exploration.items()))
                status = exploration.get("status")
                required_action = (
                    "record_code_exploration_and_retry_start"
                    if status in {"missing", "stale"}
                    else "patch_code_exploration_and_retry_start"
                    if status == "reusable_with_changes"
                    else "rerun_code_task_context_before_start"
                )
                raise TaskRunnerError(
                    f"code_exploration_not_ready:{repository_id}:{status}",
                    requiredAction=required_action,
                    explorationStatus=status,
                    explorationPolicy=exploration.get("policy"),
                    changedPaths=exploration.get("changedPaths", []),
                    criticalHits=exploration.get("criticalHits", []),
                    staleReasons=exploration.get("staleReasons", []),
                    explorationBlocked=True,
                    implementationAllowed=False,
                )
        else:
            exploration_gate = {
                "checkedAt": _utc_now(),
                "source": "current_cache",
                "repositories": repository_gates,
            }
    declared_scope_paths, resolved_scope_paths = _resolved_scope_paths(task, scope_workspaces)
    repository_state = _repository_state(repositories)

    run_id = _new_run_id()
    state = {
        "version": 2,
        "runId": run_id,
        "featureId": feature,
        "batchId": batch_id,
        "taskId": task_id,
        "taskContractSha256": task_contract_sha256(task),
        "executionMode": execution_mode,
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
    }
    pending_revalidation = task.get("pendingRevalidation")
    if isinstance(pending_revalidation, dict):
        state["revalidation"] = dict(pending_revalidation)
    if isinstance(repair_context, dict):
        state["repairContext"] = dict(repair_context)
    if isinstance(exploration_gate, dict):
        state["explorationGate"] = exploration_gate
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






def _runtime_environment_failure_category(output: str) -> str | None:
    lowered = output.lower()
    if any(
        marker in lowered
        for marker in (
            "java_home environment variable is not defined correctly",
            "no compiler is provided in this environment",
        )
    ):
        return "java_toolchain_unavailable"
    artifact_context = any(
        marker in lowered
        for marker in (
            "could not transfer artifact",
            "failed to read artifact descriptor",
            "non-resolvable parent pom",
            "plugin or one of its dependencies could not be resolved",
        )
    )
    network_context = any(
        marker in lowered
        for marker in (
            "unknown host",
            "connection timed out",
            "connect timed out",
            "connection refused",
            "network is unreachable",
            "no route to host",
            "proxy authentication required",
        )
    )
    if artifact_context and network_context:
        return "dependency_network_unavailable"
    if artifact_context and any(
        marker in lowered
        for marker in (
            "pkix path building failed",
            "unable to find valid certification path",
            "return code is: 401",
            "return code is: 403",
            "status code: 401",
            "status code: 403",
        )
    ):
        return "dependency_credentials_or_certificate_failure"
    if any(
        marker in lowered
        for marker in (
            "npm error code eai_again",
            "npm err! code eai_again",
            "npm error code enetunreach",
            "npm err! code enetunreach",
        )
    ):
        return "dependency_network_unavailable"
    return None


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
        "runType": "batch_compile",
        "requiredAction": "fix_compile_environment_and_retry_batch_compile",
        "errorCategory": "environment_failure",
        "failureCategory": category,
        "failedValidationTaskId": task_id,
        "failedTaskId": task_id,
        "failedCommandId": command_id,
        "commandId": command_id,
        "executable": executable,
        "cwd": command.get("cwd"),
        "detail": detail,
        "evidenceIds": [],
        "allowedCommands": ["batch-compile"],
        "userMessage": (
            f"编译命令 {command_id} 无法运行（{category}）。"
            "请修复编译环境后重新执行 batch-compile。"
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
    platform_name: str | None = None,
) -> ValidationLaunchSpec:
    argv = command.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) for item in argv)
    ):
        raise TaskRunnerError(f"invalid_validation_argv:{command.get('id', '')}")
    policy_errors = command_policy_errors(command)
    if policy_errors:
        raise TaskRunnerError(
            f"validation_command_policy_violation:{command.get('id', '')}:{policy_errors[0]}"
        )
    _, repo = _command_repository(command, repositories)
    command_cwd = (repo / str(command.get("cwd"))).resolve()
    if command_cwd.is_dir():
        selector_errors = maven_project_selector_workspace_errors(command, command_cwd)
        if selector_errors:
            raise TaskRunnerError(
                f"validation_command_policy_violation:{command.get('id', '')}:{selector_errors[0]}"
            )
    script_name = package_script_name(command)
    if script_name is not None:
        package_path = command_cwd / "package.json"
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise _validation_environment_error(
                command,
                category="package_manifest_invalid",
                detail=f"path={package_path};error={exc}",
                retry_same_run=retry_same_run,
                run_id=run_id,
                batch_id=batch_id,
                task_id=task_id,
            ) from exc
        scripts = package.get("scripts") if isinstance(package, dict) else None
        script_errors = compile_only_package_scripts_errors(scripts, script_name)
        if script_errors:
            raise _validation_environment_error(
                command,
                category=script_errors[0],
                detail=f"packageScript={script_name}",
                retry_same_run=retry_same_run,
                run_id=run_id,
                batch_id=batch_id,
                task_id=task_id,
            )
    executable = argv[0]
    effective_platform = platform_name or os.name
    has_path = "/" in executable or "\\" in executable or Path(executable).is_absolute()
    if has_path:
        candidate = Path(executable)
        if not candidate.is_absolute():
            candidate = command_cwd / candidate
        candidate = candidate.resolve()
        available = candidate.is_file() and (
            effective_platform == "nt" or os.access(candidate, os.X_OK)
        )
        resolved_executable = str(candidate) if available else None
    else:
        resolved_executable = shutil.which(executable)
        available = resolved_executable is not None
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
    resolved_executable = str(Path(str(resolved_executable)).resolve())
    if (
        effective_platform == "nt"
        and Path(resolved_executable).suffix.casefold()
        in WINDOWS_BATCH_EXECUTABLE_SUFFIXES
    ):
        command_shell = os.environ.get("COMSPEC") or shutil.which("cmd.exe")
        if not isinstance(command_shell, str) or not Path(command_shell).is_file():
            raise _validation_environment_error(
                command,
                category="command_shell_missing",
                detail="COMSPEC/cmd.exe is unavailable for Windows batch validation",
                retry_same_run=retry_same_run,
                run_id=run_id,
                batch_id=batch_id,
                task_id=task_id,
            )
        return ValidationLaunchSpec(
            requested_argv=tuple(argv),
            resolved_executable=resolved_executable,
            launch_mode="windows_batch",
            command_shell=str(Path(command_shell).resolve()),
        )
    return ValidationLaunchSpec(
        requested_argv=tuple(argv),
        resolved_executable=resolved_executable,
        launch_mode="direct",
    )


def _decode_validation_output(content: bytes) -> str:
    return content.decode(
        locale.getpreferredencoding(False) or "utf-8",
        errors="replace",
    )


def _emit_validation_progress(event: str, **details: Any) -> None:
    """Keep async hosts informed without mixing progress into stdout JSON."""
    try:
        print(
            json.dumps(
                {"event": event, **details},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
            flush=True,
        )
    except OSError:
        pass


def _validation_process_group_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _definitive_compile_failure_category(
    output: str,
    command: dict[str, Any],
    repositories: RepositoryMap,
) -> str | None:
    """Detect a definitive compiler diagnostic for the compile-only batch gate.

    The Code stage has no test-validation branch. Even if a misconfigured build
    mentions test sources or testCompile, the result remains a source compile
    failure and must never activate test-specific execution or repair behavior.
    """
    lowered = output.lower().replace("\\", "/")
    explicit_compile_markers = (
        "compilation error",
        "compilation failure",
        "fatal error compiling",
    )
    path_compile_markers = (
        "cannot find symbol",
        "must be caught or declared to be thrown",
        "未报告的异常错误",
        "必须对其进行捕获或声明以便抛出",
        "incompatible types",
        "does not exist",
        "cannot be applied to given types",
        "illegal start of",
        "not a statement",
        "; expected",
        "has private access",
        "does not override",
    )
    has_explicit_compile_marker = any(
        marker in line
        and ("[error]" in line or "failed to execute goal" in line)
        for line in lowered.splitlines()
        for marker in explicit_compile_markers
    )
    has_path_compile_marker = any(
        _validation_diagnostic_paths(line, command, repositories)
        and any(marker in line.lower() for marker in path_compile_markers)
        for line in output.splitlines()
    )
    if not has_explicit_compile_marker and not has_path_compile_marker:
        return None
    return "source_compile_failure"


def _terminate_validation_process_tree(
    process: subprocess.Popen[bytes],
) -> bool:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=PROCESS_TERMINATION_GRACE_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            try:
                process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    return False
        return process.poll() is not None

    def process_group_exists() -> bool:
        try:
            os.killpg(process.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError:
        if process.poll() is None:
            process.terminate()

    grace_deadline = time.monotonic() + PROCESS_TERMINATION_GRACE_SECONDS
    while time.monotonic() < grace_deadline:
        process.poll()
        if not process_group_exists():
            break
        time.sleep(VALIDATION_OUTPUT_POLL_SECONDS)
    if process_group_exists():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            if process.poll() is None:
                process.kill()
    if process.poll() is None:
        try:
            process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                return False
    return process.poll() is not None and not process_group_exists()


def _validation_command_argv(launch_spec: ValidationLaunchSpec) -> list[str]:
    return [
        launch_spec.resolved_executable,
        *launch_spec.requested_argv[1:],
    ]


def _windows_batch_quote(value: str) -> str:
    if any(marker in value for marker in ("\x00", "\r", "\n")):
        raise OSError("validation_windows_batch_argument_invalid")
    return f'"{value.replace("%", "%%").replace(chr(34), chr(34) * 2)}"'


def _validation_windows_wrapper_content(
    launch_spec: ValidationLaunchSpec,
    log_path: Path,
) -> str:
    command_line = " ".join(
        _windows_batch_quote(item)
        for item in _validation_command_argv(launch_spec)
    )
    log_target = _windows_batch_quote(str(log_path))
    return (
        "@echo off\r\n"
        f">{log_target} echo validation_windows_wrapper_started\r\n"
        f"call {command_line} 1>>{log_target} 2>&1\r\n"
        "set \"_autobiz_exit=%ERRORLEVEL%\"\r\n"
        "exit /b %_autobiz_exit%\r\n"
    )


def _validation_windows_shell_command(
    launch_spec: ValidationLaunchSpec,
    wrapper_path: Path,
) -> str:
    if not isinstance(launch_spec.command_shell, str):
        raise OSError("validation_windows_command_shell_missing")
    command_shell = _windows_batch_quote(launch_spec.command_shell)
    wrapper = _windows_batch_quote(str(wrapper_path))
    return f'{command_shell} /D /S /V:OFF /C "{wrapper}"'


def _run_validation_process(
    launch_spec: ValidationLaunchSpec,
    command_cwd: Path,
    timeout: int,
    command: dict[str, Any],
    repositories: RepositoryMap,
) -> ValidationProcessResult:
    started_at = time.monotonic()
    log_fd, log_name = tempfile.mkstemp(
        prefix="autobiz-validation-",
        suffix=".log",
    )
    os.close(log_fd)
    log_path = Path(log_name)
    wrapper_path: Path | None = None
    process: subprocess.Popen[bytes] | None = None
    hard_deadline = started_at + timeout
    compile_category: str | None = None
    compile_deadline: float | None = None
    termination_reason: str | None = None
    process_tree_terminated = True
    rolling_output = bytearray()
    captured_bytes = 0
    next_progress_at = started_at + VALIDATION_PROGRESS_INTERVAL_SECONDS

    try:
        if launch_spec.launch_mode == "windows_batch":
            wrapper_fd, wrapper_name = tempfile.mkstemp(
                prefix="autobiz-validation-",
                suffix=".cmd",
            )
            os.close(wrapper_fd)
            wrapper_path = Path(wrapper_name)
            wrapper_path.write_text(
                _validation_windows_wrapper_content(launch_spec, log_path),
                encoding="mbcs" if os.name == "nt" else "utf-8",
            )
            process = subprocess.Popen(
                _validation_windows_shell_command(launch_spec, wrapper_path),
                executable=launch_spec.command_shell,
                cwd=command_cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                **_validation_process_group_options(),
            )
        else:
            with log_path.open("ab", buffering=0) as child_output:
                process = subprocess.Popen(
                    _validation_command_argv(launch_spec),
                    cwd=command_cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=child_output,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                    **_validation_process_group_options(),
                )
        _emit_validation_progress(
            "validation_process_started",
            commandId=str(command.get("id", "")),
            pid=process.pid,
            timeoutSeconds=timeout,
            resolvedExecutable=launch_spec.resolved_executable,
            launchMode=launch_spec.launch_mode,
            cwd=str(command_cwd),
        )

        with log_path.open("rb", buffering=0) as command_log:
            def read_available_output() -> None:
                nonlocal captured_bytes, compile_category, compile_deadline
                while True:
                    chunk = command_log.read(8192)
                    if not chunk:
                        break
                    captured_bytes += len(chunk)
                    rolling_output.extend(chunk)
                    if len(rolling_output) > VALIDATION_DIAGNOSTIC_BUFFER_BYTES:
                        del rolling_output[:-VALIDATION_DIAGNOSTIC_BUFFER_BYTES]
                    detected = _definitive_compile_failure_category(
                        _decode_validation_output(bytes(rolling_output)),
                        command,
                        repositories,
                    )
                    if detected is not None:
                        if compile_category is None:
                            compile_deadline = (
                                time.monotonic() + COMPILE_DIAGNOSTIC_DRAIN_SECONDS
                            )
                        if compile_category is None:
                            compile_category = detected

            # 新增：环境错误检测
            environment_failure_category: str | None = None

            while True:
                read_available_output()
                now = time.monotonic()
                if process.poll() is not None:
                    read_available_output()
                    if compile_category is not None:
                        termination_reason = "compile_diagnostic"
                    break

                # 检测环境错误（每次轮询都检查）
                decoded_output = _decode_validation_output(bytes(rolling_output))
                env_failure = _runtime_environment_failure_category(decoded_output)
                if env_failure is not None:
                    environment_failure_category = env_failure
                    termination_reason = "environment_failure"
                    _emit_validation_progress(
                        "validation_environment_failure",
                        commandId=str(command.get("id", "")),
                        elapsedSeconds=round(now - started_at, 3),
                        category=env_failure,
                    )
                    process_tree_terminated = _terminate_validation_process_tree(process)
                    read_available_output()
                    break

                if compile_category is not None and (
                    (compile_deadline is not None and now >= compile_deadline)
                    or now >= hard_deadline
                ):
                    termination_reason = "compile_diagnostic"
                    process_tree_terminated = _terminate_validation_process_tree(process)
                    read_available_output()
                    break
                if now >= hard_deadline:
                    termination_reason = "command_timeout"
                    process_tree_terminated = _terminate_validation_process_tree(process)
                    read_available_output()
                    break
                if now >= next_progress_at:
                    _emit_validation_progress(
                        "validation_process_running",
                        commandId=str(command.get("id", "")),
                        pid=process.pid,
                        elapsedSeconds=round(now - started_at, 3),
                        capturedBytes=captured_bytes,
                        resolvedExecutable=launch_spec.resolved_executable,
                        launchMode=launch_spec.launch_mode,
                    )
                    next_progress_at = now + VALIDATION_PROGRESS_INTERVAL_SECONDS
                time.sleep(VALIDATION_OUTPUT_POLL_SECONDS)

        output = _decode_validation_output(log_path.read_bytes())
    finally:
        try:
            unlink_if_exists(log_path)
        except OSError:
            pass
        if wrapper_path is not None:
            try:
                unlink_if_exists(wrapper_path)
            except OSError:
                pass

    if process is None:
        raise OSError("validation_process_not_started")

    _emit_validation_progress(
        "validation_process_finished",
        commandId=str(command.get("id", "")),
        pid=process.pid,
        exitCode=process.poll(),
        terminationReason=termination_reason,
        compileCategory=compile_category,
        durationSeconds=round(time.monotonic() - started_at, 3),
        capturedBytes=captured_bytes,
        resolvedExecutable=launch_spec.resolved_executable,
        launchMode=launch_spec.launch_mode,
    )

    return ValidationProcessResult(
        exit_code=process.poll(),
        output=output,
        termination_reason=termination_reason,
        compile_category=compile_category,
        duration_seconds=time.monotonic() - started_at,
        process_tree_terminated=process_tree_terminated,
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
        launch_spec = _assert_validation_command_environment(
            command,
            repositories,
            retry_same_run=retry_same_run,
            run_id=run_id,
            batch_id=batch_id,
            task_id=task_id,
        )
        process_result = _run_validation_process(
            launch_spec,
            command_cwd,
            timeout,
            command,
            repositories,
        )
        output = process_result.output
        if process_result.termination_reason == "compile_diagnostic":
            return 1, (
                f"{output}\nvalidation_process_stopped_after_compile_failure:"
                f"{process_result.compile_category}:"
                f"durationSeconds={process_result.duration_seconds:.3f}:"
                f"processTreeTerminated={str(process_result.process_tree_terminated).lower()}"
            )
        if process_result.termination_reason == "command_timeout":
            error_category = _definitive_compile_failure_category(
                output, command, repositories
            )
            if error_category == "source_compile_failure":
                return 1, (
                    f"{output}\nvalidation_process_timeout_after_compile_failure:"
                    f"{error_category}:timeoutSeconds={timeout}:"
                    f"processTreeTerminated={str(process_result.process_tree_terminated).lower()}"
                )
            raise _validation_environment_error(
                command,
                category="command_timeout",
                detail=f"timeoutSeconds={timeout};output={output[-2000:]}",
                retry_same_run=retry_same_run,
                run_id=run_id,
                batch_id=batch_id,
                task_id=task_id,
            )
        exit_code = process_result.exit_code
        if exit_code in {126, 127}:
            raise _validation_environment_error(
                command,
                category="executable_failed_to_start",
                detail=f"exitCode={exit_code};output={output[-2000:]}",
                retry_same_run=retry_same_run,
                run_id=run_id,
                batch_id=batch_id,
                task_id=task_id,
            )
        if exit_code not in {None, 0}:
            environment_category = _runtime_environment_failure_category(output)
            if environment_category is not None:
                raise _validation_environment_error(
                    command,
                    category=environment_category,
                    detail=f"exitCode={exit_code};output={output[-2000:]}",
                    retry_same_run=retry_same_run,
                    run_id=run_id,
                    batch_id=batch_id,
                    task_id=task_id,
                )
        return int(exit_code or 0), output
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


def _has_required_task_validation(task: dict[str, Any]) -> bool:
    allowed_kinds = task_validation_kinds_for_lane(task_execution_lane(task))
    return any(
        isinstance(command, dict)
        and command.get("required") is True
        and command.get("kind") in allowed_kinds
        for command in task.get("validationCommands", [])
    )










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
    repair_context: dict[str, Any] | None,
) -> dict[str, Any]:
    changed_files = _changed_files(file_changes)
    no_code_change = completion_mode == "verified_existing"
    record = {
        "featureId": feature,
        "checkpoint": "code_in_progress",
        "nodeId": "dev.code",
        "skill": "autodev-code",
        "taskId": task.get("id"),
        "action": "implementation",
        "runId": run_id,
        "completionMode": completion_mode,
        "summary": f"{task.get('id')} implementation ready for batch compile",
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
    if isinstance(repair_context, dict):
        record["repairContext"] = dict(repair_context)
    return record


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
    execution_mode = task_execution_mode(task)
    if not defer_to_test_stages_enabled(plan.root):
        raise TaskRunnerError(
            f"finish_implementation_requires_compile_only_plan:{task_id}",
            requiredAction="rebuild_plan_with_defer_to_test_stages",
        )
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
        implementation_evidence_id = state.get("implementationEvidenceId")
        latest_implementation_evidence_id = task.get("latestImplementationEvidenceId")
        task_status = normalize_status(task.get("status"))
        if (
            task_status == "implemented"
            and isinstance(implementation_evidence_id, str)
            and latest_implementation_evidence_id == implementation_evidence_id
        ):
            return True, state
        latest_implementation_run_id = next(
            (
                record.get("runId")
                for record in read_records(stream_path(feature_dir))
                if record.get("action") == "implementation"
                and record.get("taskId") == task_id
                and record.get("evidenceId") == latest_implementation_evidence_id
                and isinstance(record.get("runId"), str)
            ),
            None,
        )
        raise TaskRunnerError(
            "stale_implementation_run",
            requiredAction=(
                "start_batch_compile_repair"
                if task_status == "failed"
                else "use_latest_implementation_run"
                if isinstance(latest_implementation_run_id, str)
                else "call_code_session"
            ),
            taskId=task_id,
            staleRunId=run_id,
            staleImplementationEvidenceId=implementation_evidence_id,
            currentTaskStatus=task_status,
            latestImplementationRunId=latest_implementation_run_id,
            latestImplementationEvidenceId=latest_implementation_evidence_id,
        )
    if state.get("status") in {"done", "failed", "evidence_written", "validation_running"}:
        raise TaskRunnerError(f"task_run_not_implementation_finishable:{state.get('status')}")

    file_changes, final_repositories = _repository_changes(state, repositories)
    repair_context = state.get("repairContext")
    repair_context = repair_context if isinstance(repair_context, dict) else None
    adopted_file_changes = (
        repair_context.get("adoptedFileChanges", [])
        if isinstance(repair_context, dict)
        else []
    )
    adopted_file_changes = (
        adopted_file_changes if isinstance(adopted_file_changes, list) else []
    )
    repair_file_changes = _merge_file_changes(adopted_file_changes, file_changes)
    test_asset_changes = sorted({
        path
        for change in repair_file_changes
        for path in (change.get("path"), change.get("fromPath"))
        if isinstance(path, str)
        and _is_transient_validation_path(path.split(":", 1)[-1])
    })
    if test_asset_changes:
        raise TaskRunnerError(
            "code_stage_test_changes_forbidden",
            requiredAction="restore_test_changes_and_continue_production_implementation",
            testFiles=test_asset_changes,
        )
    transient_validation_files: list[str] = []
    historical_file_changes = _historical_task_file_changes(
        feature_dir,
        task_id,
        run_id,
    )
    if (
        isinstance(repair_context, dict)
        and repair_context.get("batchCompileRepair") is True
        and not repair_file_changes
    ):
        raise TaskRunnerError(
            "batch_compile_repair_requires_code_changes",
            requiredAction="continue_model_repair",
            batchId=batch_id,
            taskId=task_id,
            repairAttempt=repair_context.get("batchCompileRepairAttempt"),
        )
    cumulative_file_changes = _merge_file_changes(
        historical_file_changes,
        adopted_file_changes,
        file_changes,
    )
    adopted_transient_files = (
        repair_context.get("adoptedTransientValidationFiles", [])
        if isinstance(repair_context, dict)
        else []
    )
    transient_validation_files = sorted({
        *transient_validation_files,
        *(
            item
            for item in adopted_transient_files
            if isinstance(item, str)
        ),
    })
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
    if cumulative_file_changes or transient_validation_files:
        if execution_mode == "external_dependency":
            raise TaskRunnerError(
                "external_dependency_code_changes_forbidden",
                requiredAction="restore_task_snapshot_and_finish_as_external_dependency",
                changedFiles=_changed_files(cumulative_file_changes),
                transientValidationFiles=transient_validation_files,
            )
        if execution_mode == "verified_existing":
            raise TaskRunnerError(
                "verified_existing_code_changes_forbidden",
                requiredAction="restore_task_snapshot_or_return_to_plan",
                changedFiles=_changed_files(cumulative_file_changes),
                transientValidationFiles=transient_validation_files,
            )
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
        if execution_mode != "external_dependency" and not _has_required_task_validation(task):
            raise TaskRunnerError("verified_existing_requires_task_validation")
        completion_mode = "verified_existing"
    if execution_mode != "external_dependency":
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
                    repair_context=repair_context,
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
        for field in ("batchContinuation", "batchCompile"):
            if isinstance(result.data.get(field), dict):
                state[field] = result.data[field]
    _save_run(path, state)
    return True, state






def _repository_state_sha256(repository_state: list[dict[str, Any]]) -> str:
    content = json.dumps(
        repository_state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _batch_compile_snapshot_path(feature_dir: Path, batch_id: str) -> Path:
    return feature_dir / ".task-runs" / ".batch-compile" / f"{batch_id}.json"


def _persist_batch_compile_workspace_state(
    feature_dir: Path,
    feature: str,
    batch_id: str,
    compile_result: dict[str, Any],
) -> None:
    """Keep the failed compile baseline needed to adopt a model's early repair."""

    repository_state = compile_result.get("workspaceState")
    snapshot_sha256 = compile_result.get("workspaceSnapshotSha256")
    if (
        compile_result.get("compileStatus") != "failed"
        or not isinstance(repository_state, list)
        or not repository_state
        or not isinstance(snapshot_sha256, str)
        or _repository_state_sha256(repository_state) != snapshot_sha256
    ):
        return
    atomic_write_json(
        _batch_compile_snapshot_path(feature_dir, batch_id),
        {
            "version": 1,
            "featureId": feature,
            "batchId": batch_id,
            "commandId": compile_result.get("commandId"),
            "compileStatus": "failed",
            "workspaceSnapshotSha256": snapshot_sha256,
            "repositories": repository_state,
            "capturedAt": _utc_now(),
        },
    )


def _repository_state_matches(
    repository_state: Any,
    expected_snapshot_sha256: str,
) -> bool:
    return (
        isinstance(repository_state, list)
        and bool(repository_state)
        and _repository_state_sha256(repository_state) == expected_snapshot_sha256
    )


def _failed_compile_repository_state(
    feature_dir: Path,
    feature: str,
    batch_id: str,
    command_id: Any,
    expected_snapshot_sha256: str,
) -> list[dict[str, Any]] | None:
    """Load an exact failed-compile baseline, including pre-upgrade run fallback."""

    snapshot_path = _batch_compile_snapshot_path(feature_dir, batch_id)
    try:
        record = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        record = None
    if (
        isinstance(record, dict)
        and record.get("version") == 1
        and record.get("featureId") == feature
        and record.get("batchId") == batch_id
        and record.get("commandId") == command_id
        and record.get("compileStatus") == "failed"
        and record.get("workspaceSnapshotSha256") == expected_snapshot_sha256
        and _repository_state_matches(record.get("repositories"), expected_snapshot_sha256)
    ):
        return [item for item in record["repositories"] if isinstance(item, dict)]

    # Older runner versions did not persist a dedicated compile snapshot. A
    # completed task run normally has the same final repository state because
    # compile outputs are ignored. Reuse it only when its digest is exact.
    for run_path in sorted((feature_dir / ".task-runs").glob("T*/*.json"), reverse=True):
        try:
            run = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            not isinstance(run, dict)
            or run.get("featureId") != feature
            or run.get("batchId") != batch_id
            or strict_task_run_integrity_error(run) is not None
        ):
            continue
        for field in ("finalRepositories", "abortRepositories", "repositories"):
            repository_state = run.get(field)
            if _repository_state_matches(repository_state, expected_snapshot_sha256):
                return [item for item in repository_state if isinstance(item, dict)]
    return None


def _repository_state_file_changes(
    before_state: list[dict[str, Any]],
    after_state: list[dict[str, Any]],
) -> list[dict[str, str]]:
    before_by_id = {
        str(item.get("id")): item
        for item in before_state
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    after_by_id = {
        str(item.get("id")): item
        for item in after_state
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if not before_by_id or set(before_by_id) != set(after_by_id):
        raise TaskRunnerError("batch_compile_repair_repository_state_mismatch")
    multiple = len(before_by_id) > 1
    changes: list[dict[str, str]] = []
    for repository_id in sorted(before_by_id):
        before = before_by_id[repository_id]
        after = after_by_id[repository_id]
        if before.get("path") != after.get("path"):
            raise TaskRunnerError("batch_compile_repair_repository_state_mismatch")
        before_snapshot = before.get("snapshot")
        after_snapshot = after.get("snapshot")
        if not isinstance(before_snapshot, dict) or not isinstance(after_snapshot, dict):
            raise TaskRunnerError("batch_compile_repair_repository_snapshot_missing")
        repository_changes = _snapshot_changes(before_snapshot, after_snapshot)
        if multiple:
            for change in repository_changes:
                change["path"] = f"{repository_id}:{change['path']}"
                if "fromPath" in change:
                    change["fromPath"] = f"{repository_id}:{change['fromPath']}"
                change["repository"] = repository_id
        changes.extend(repository_changes)
    return changes










def _normalize_diagnostic_path(
    raw: str,
    command: dict[str, Any],
    repositories: RepositoryMap,
) -> str | None:
    candidate = raw.strip().strip("'\"").replace("\\", "/")
    candidate = re.sub(r"^(?:\[[A-Z]+\]\s*)+", "", candidate)
    for repository_id, repository in repositories.items():
        repository_path = str(repository).replace("\\", "/").rstrip("/")
        index = candidate.lower().find(f"{repository_path.lower()}/")
        if index >= 0:
            relative = candidate[index + len(repository_path) + 1 :]
            return f"{repository_id}:{relative}" if len(repositories) > 1 else relative
    for anchor in ("src/", "test/", "tests/"):
        index = candidate.find(anchor)
        if index >= 0:
            candidate = candidate[index:]
            break
    if PurePosixPath(candidate).is_absolute() or PureWindowsPath(candidate).is_absolute():
        return None
    try:
        relative = _normalize_git_relative_path(candidate, error="invalid_validation_diagnostic_path")
    except TaskRunnerError:
        return None
    cwd = str(command.get("cwd") or ".").replace("\\", "/").strip("/")
    if cwd not in {"", "."} and relative != cwd and not relative.startswith(f"{cwd}/"):
        relative = f"{cwd}/{relative}"
    repository_id = command.get("repo")
    if len(repositories) > 1 and isinstance(repository_id, str):
        return f"{repository_id}:{relative}"
    return relative


def _validation_diagnostic_paths(
    output: str,
    command: dict[str, Any],
    repositories: RepositoryMap,
) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        for match in VALIDATION_DIAGNOSTIC_PATH_RE.finditer(line):
            path = _normalize_diagnostic_path(match.group("path"), command, repositories)
            if isinstance(path, str) and path not in paths:
                paths.append(path)
    return paths


def _evidence_paths(record: dict[str, Any]) -> set[str]:
    paths = {
        item
        for field in ("changedFiles", "transientValidationFiles", "supportingFiles")
        for item in record.get(field, [])
        if isinstance(item, str)
    }
    for change in record.get("fileChanges", []):
        if not isinstance(change, dict):
            continue
        paths.update(
            item
            for item in (change.get("path"), change.get("fromPath"))
            if isinstance(item, str)
        )
    return {item.replace("\\", "/") for item in paths}


def _validation_repair_owner_task_ids(
    feature_dir: Path,
    batch: dict[str, Any],
    failed_validation_task_id: str,
    diagnostic_paths: list[str],
) -> list[str]:
    if not diagnostic_paths:
        return [failed_validation_task_id]
    records = {
        str(record.get("evidenceId")): record
        for record in read_records(stream_path(feature_dir))
        if record.get("action") == "implementation"
        and isinstance(record.get("evidenceId"), str)
    }
    owners: list[str] = []
    diagnostic_set = {item.replace("\\", "/").casefold() for item in diagnostic_paths}
    for task in batch.get("tasks", []):
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            continue
        record = records.get(str(task.get("latestImplementationEvidenceId")))
        if not isinstance(record, dict):
            continue
        evidence_paths = {item.casefold() for item in _evidence_paths(record)}
        if diagnostic_set & evidence_paths:
            owners.append(str(task["id"]))
    return owners or [failed_validation_task_id]




























































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
    if state.get("scopePathBase") != "requested_code_workspace":
        return False
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
            requiredAction="fix_workspace_and_retry_finish_implementation_or_force_abort",
            changedFiles=changed_files,
            resolvedGitRoots=[str(item) for item in repositories.values()],
        )
    if file_changes and force_with_changes and not abort_why:
        raise TaskRunnerError(
            "abort_with_changes_requires_reason",
            requiredAction="provide_abort_reason_or_retry_finish_implementation",
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
    if state.get("evidenceIds"):
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




def start_task(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspace: Path | list[Path],
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        return _start_task_unlocked(workspace, feature, task_id, code_workspace)




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








def start_batch_compile_repair(
    workspace: Path,
    feature: str,
    batch_id: str,
    task_id: str,
    code_workspace: Path | list[Path],
) -> dict[str, Any]:
    """Start a model-owned task run that repairs a failed batch compile."""

    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        bundle, actual_batch_id, task = _load_plan_and_task(feature_dir, task_id)
        if actual_batch_id != batch_id:
            raise TaskRunnerError(
                f"batch_compile_repair_task_batch_mismatch:{task_id}",
                expectedBatchId=batch_id,
                actualBatchId=actual_batch_id,
            )
        if not defer_to_test_stages_enabled(bundle.root):
            raise TaskRunnerError(f"defer_to_test_stages_not_enabled:{batch_id}")
        batch = bundle.batches.get(batch_id)
        batch_compile = batch.get("batchCompile") if isinstance(batch, dict) else None
        if not isinstance(batch_compile, dict) or batch_compile.get("status") != "failed":
            raise TaskRunnerError(f"batch_compile_repair_requires_failed:{batch_id}")
        owner_ids = batch_compile.get("repairOwnerTaskIds")
        owner_ids = (
            [str(item) for item in owner_ids if isinstance(item, str)]
            if isinstance(owner_ids, list)
            else []
        )
        if task_id not in owner_ids:
            raise TaskRunnerError(
                f"batch_compile_repair_owner_mismatch:{task_id}",
                repairOwnerTaskIds=owner_ids,
            )
        attempts = int(batch_compile.get("repairAttempts", 0))
        if attempts >= BATCH_COMPILE_MAX_REPAIR_ATTEMPTS:
            raise TaskRunnerError(
                f"batch_compile_repair_attempts_exhausted:{batch_id}",
                requiredAction="escalate_batch_compile_repair_exhausted",
                nextActor="main_agent",
                repairAttempts=attempts,
                maxRepairAttempts=BATCH_COMPILE_MAX_REPAIR_ATTEMPTS,
            )
        if normalize_status(task.get("status")) != "implemented":
            raise TaskRunnerError(f"batch_compile_repair_task_not_implemented:{task_id}")

        requested_workspaces = (
            [code_workspace] if isinstance(code_workspace, Path) else list(code_workspace)
        )
        if len(requested_workspaces) != 1:
            raise TaskRunnerError(
                "batch_compile_repair_requires_single_workspace",
                requestedCodeWorkspaces=[str(path.resolve()) for path in requested_workspaces],
            )
        expected_workspaces = batch_compile.get("requestedCodeWorkspaces")
        actual_workspaces = [str(path.resolve()) for path in requested_workspaces]
        if expected_workspaces != actual_workspaces:
            raise TaskRunnerError(
                "batch_compile_repair_workspace_mismatch",
                expectedRequestedCodeWorkspaces=expected_workspaces,
                requestedCodeWorkspaces=actual_workspaces,
            )
        repositories = _resolve_repositories(requested_workspaces)
        current_repository_state = _repository_state(repositories)
        current_snapshot = _repository_state_sha256(current_repository_state)
        expected_snapshot = batch_compile.get("workspaceSnapshotSha256")
        if not isinstance(expected_snapshot, str):
            raise TaskRunnerError("batch_compile_repair_workspace_snapshot_missing")

        adopted_file_changes: list[dict[str, str]] = []
        if current_snapshot != expected_snapshot:
            failed_repository_state = _failed_compile_repository_state(
                feature_dir,
                feature,
                batch_id,
                batch_compile.get("commandId"),
                expected_snapshot,
            )
            if failed_repository_state is None:
                raise TaskRunnerError(
                    "workspace_changed_before_batch_compile_repair",
                    requiredAction="restore_failed_compile_snapshot",
                    expectedWorkspaceSnapshotSha256=expected_snapshot,
                    currentWorkspaceSnapshotSha256=current_snapshot,
                    snapshotRecoveryAvailable=False,
                )
            adopted_file_changes = _repository_state_file_changes(
                failed_repository_state,
                current_repository_state,
            )
            if not adopted_file_changes:
                raise TaskRunnerError(
                    "workspace_changed_before_batch_compile_repair",
                    requiredAction="restore_failed_compile_snapshot",
                    expectedWorkspaceSnapshotSha256=expected_snapshot,
                    currentWorkspaceSnapshotSha256=current_snapshot,
                    snapshotRecoveryAvailable=True,
                )
            changed_files = _changed_files(adopted_file_changes)
            test_asset_changes = sorted({
                path
                for change in adopted_file_changes
                for path in (change.get("path"), change.get("fromPath"))
                if isinstance(path, str)
                and _is_transient_validation_path(path.split(":", 1)[-1])
            })
            if test_asset_changes:
                raise TaskRunnerError(
                    "code_stage_test_changes_forbidden",
                    requiredAction="restore_test_changes_and_retry_start_batch_compile_repair",
                    testFiles=test_asset_changes,
                )
            scope_workspaces = _scope_workspaces(requested_workspaces, repositories)
            if not _paths_within_workspace_contexts(changed_files, scope_workspaces):
                raise TaskRunnerError(
                    "out_of_scope_changes_detected:" + ",".join(changed_files),
                    requiredAction="restore_out_of_scope_changes_and_retry_start_batch_compile_repair",
                    changedFiles=changed_files,
                    requestedCodeWorkspaces=actual_workspaces,
                )

        repair_attempt = attempts + 1
        repair_context = {
            "batchCompileRepair": True,
            "batchCompileRepairAttempt": repair_attempt,
            "parentBatchCompileCommandId": batch_compile.get("commandId"),
            "parentBatchCompileWorkspaceSnapshotSha256": expected_snapshot,
            "failureCategory": batch_compile.get("failureCategory"),
            "diagnosticPaths": list(batch_compile.get("diagnosticPaths", [])),
        }
        if adopted_file_changes:
            repair_context.update(
                {
                    "adoptedFileChanges": adopted_file_changes,
                    "adoptedWorkspaceSnapshotSha256": current_snapshot,
                    "adoptedPreStartChanges": True,
                }
            )
        state = _start_task_unlocked(
            workspace,
            feature,
            task_id,
            code_workspace,
            repair_context=repair_context,
        )
        result = begin_batch_compile_repair(workspace, feature, batch_id, task_id)
        if not result.ok:
            set_task_execution_status(
                workspace,
                feature,
                task_id,
                "implemented",
                expected_task_contract_sha256=task_contract_sha256(task),
            )
            state["status"] = "aborted"
            state["abortReason"] = "batch_compile_repair_plan_binding_failed"
            _save_run(_run_path(feature_dir, task_id, str(state.get("runId"))), state)
            raise TaskRunnerError(
                "batch_compile_repair_plan_binding_failed",
                planWriterErrors=result.errors or [],
            )
        state["batchCompileRepair"] = {
            "batchId": batch_id,
            "taskId": task_id,
            "attempt": repair_attempt,
            "maxAttempts": BATCH_COMPILE_MAX_REPAIR_ATTEMPTS,
            "adoptedPreStartChanges": bool(adopted_file_changes),
            "adoptedChangedFiles": _changed_files(adopted_file_changes),
            "requiredAction": (
                "finish_implementation_then_retry_batch_compile"
                if adopted_file_changes
                else "model_fix_then_finish_implementation"
            ),
        }
        return state


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




def _run_batch_compile(
    workspace: Path,
    feature: str,
    batch_id: str,
    code_workspace: Path | list[Path],
) -> dict[str, Any]:
    """
    在批次完成后执行编译验证（仅编译，不运行测试）。

    返回: {
        "compileStatus": "passed" | "failed",
        "commandId": str,
        "output": str (失败时),
        "failureCategory": str (失败时)
    }
    """
    feature_dir = _feature_dir(workspace, feature)
    plan_bundle = load_plan_bundle(feature_dir)
    if not defer_to_test_stages_enabled(plan_bundle.root):
        raise TaskRunnerError(f"defer_to_test_stages_not_enabled:{batch_id}")
    batch = plan_bundle.batches.get(batch_id)
    if not isinstance(batch, dict):
        raise TaskRunnerError(f"batch_not_found:{batch_id}")

    batch_compile = batch.get("batchCompile")
    if not isinstance(batch_compile, dict):
        raise TaskRunnerError(f"batch_compile_not_initialized:{batch_id}")
    compile_status = batch_compile.get("status")
    if compile_status == "passed":
        return {
            "compileStatus": "passed",
            "commandId": batch_compile.get("commandId", ""),
        }
    if compile_status == "failed":
        attempts = int(batch_compile.get("repairAttempts", 0))
        exhausted = attempts >= BATCH_COMPILE_MAX_REPAIR_ATTEMPTS
        raise TaskRunnerError(
            f"batch_compile_repair_required:{batch_id}",
            requiredAction=(
                "escalate_batch_compile_repair_exhausted"
                if exhausted
                else "start_batch_compile_repair"
            ),
            nextActor="main_agent" if exhausted else "model",
            repairAttempts=attempts,
            maxRepairAttempts=BATCH_COMPILE_MAX_REPAIR_ATTEMPTS,
            repairOwnerTaskIds=batch_compile.get("repairOwnerTaskIds", []),
            diagnosticPaths=batch_compile.get("diagnosticPaths", []),
        )
    if compile_status == "repairing":
        raise TaskRunnerError(
            f"batch_compile_repair_in_progress:{batch_id}",
            requiredAction="finish_implementation",
            nextActor="model",
            repairTaskId=batch_compile.get("repairTaskId"),
            repairAttempts=batch_compile.get("repairAttempts", 0),
        )
    if compile_status != "pending":
        raise TaskRunnerError(f"batch_compile_status_invalid:{batch_id}:{compile_status}")

    # P0: 前置条件 - 检查所有任务是否已经实现
    batch_tasks = batch.get("tasks", [])
    all_tasks_implemented = all(
        normalize_status(task.get("status")) in {"implemented", "done"}
        for task in batch_tasks
        if isinstance(task, dict)
    )
    if not all_tasks_implemented:
        incomplete_tasks = [
            str(task.get("id"))
            for task in batch_tasks
            if isinstance(task, dict) and normalize_status(task.get("status")) not in {"implemented", "done"}
        ]
        raise TaskRunnerError(
            f"batch_compile_precondition_failed:{batch_id}",
            incompleteTasks=incomplete_tasks,
        )
    invalid_implementation_bindings = [
        str(task.get("id"))
        for task in batch_tasks
        if isinstance(task, dict)
        and (
            not isinstance(task.get("latestImplementationEvidenceId"), str)
            or task.get("latestImplementationEvidenceId")
            not in (
                task.get("implementationEvidenceIds")
                if isinstance(task.get("implementationEvidenceIds"), list)
                else []
            )
            or not isinstance(task.get("implementationRevision"), int)
            or isinstance(task.get("implementationRevision"), bool)
            or int(task.get("implementationRevision", 0)) < 1
        )
    ]
    if invalid_implementation_bindings:
        raise TaskRunnerError(
            f"batch_compile_implementation_evidence_missing:{batch_id}",
            taskIds=invalid_implementation_bindings,
        )

    requested_workspaces = [code_workspace] if isinstance(code_workspace, Path) else list(code_workspace)
    if len(requested_workspaces) != 1:
        raise TaskRunnerError(
            "batch_compile_requires_single_workspace",
            batchId=batch_id,
            requestedCodeWorkspaces=[str(p.resolve()) for p in requested_workspaces],
        )

    repositories = _resolve_repositories(requested_workspaces)
    if not repositories:
        raise TaskRunnerError("no_repositories_resolved")
    # 查找编译命令
    batch_validation = batch.get("batchValidation")
    if not isinstance(batch_validation, dict):
        raise TaskRunnerError(f"batch_validation_config_missing:{batch_id}")

    commands = batch_validation.get("commands", [])
    compile_command = next(
        (
            cmd
            for cmd in commands
            if isinstance(cmd, dict)
            and cmd.get("kind") == "compile"
            and cmd.get("required") is True
        ),
        None,
    )

    if not compile_command:
        raise TaskRunnerError(
            f"batch_compile_command_not_found:{batch_id}",
            availableCommands=[cmd.get("kind") for cmd in commands if isinstance(cmd, dict)],
        )
    compile_policy_errors = compile_only_command_errors(compile_command)
    if compile_policy_errors:
        raise TaskRunnerError(
            f"batch_compile_command_not_compile_only:{batch_id}",
            commandId=compile_command.get("id"),
            policyErrors=compile_policy_errors,
        )

    command_id = str(compile_command.get("id", ""))
    try:
        exit_code, output = _run_validation(
            compile_command,
            repositories,
            batch_id=batch_id,
            task_id="__batch_compile__",
        )
        requested_paths = [str(path.resolve()) for path in requested_workspaces]
        workspace_state = _repository_state(repositories)
        workspace_snapshot_sha256 = _repository_state_sha256(workspace_state)
        implementation_evidence_by_task = {
            str(task.get("id")): str(task.get("latestImplementationEvidenceId"))
            for task in batch_tasks
            if isinstance(task, dict) and isinstance(task.get("id"), str)
        }
        implementation_revision_by_task = {
            str(task.get("id")): int(task.get("implementationRevision", 0))
            for task in batch_tasks
            if isinstance(task, dict) and isinstance(task.get("id"), str)
        }
        if exit_code == 0:
            return {
                "compileStatus": "passed",
                "commandId": command_id,
                "requestedCodeWorkspaces": requested_paths,
                "workspaceSnapshotSha256": workspace_snapshot_sha256,
                "workspaceState": workspace_state,
                "implementationEvidenceByTask": implementation_evidence_by_task,
                "implementationRevisionByTask": implementation_revision_by_task,
            }
        diagnostic_paths = _validation_diagnostic_paths(output, compile_command, repositories)
        fallback_task_id = next(
            (
                str(task.get("id"))
                for task in reversed(batch_tasks)
                if isinstance(task, dict) and isinstance(task.get("id"), str)
            ),
            "",
        )
        repair_owner_ids = _validation_repair_owner_task_ids(
            feature_dir,
            batch,
            fallback_task_id,
            diagnostic_paths,
        )
        return {
            "compileStatus": "failed",
            "commandId": command_id,
            "output": output,
            "failureCategory": (
                _definitive_compile_failure_category(output, compile_command, repositories)
                or "compile_error"
            ),
            "diagnosticPaths": diagnostic_paths,
            "repairOwnerTaskIds": repair_owner_ids,
            "requestedCodeWorkspaces": requested_paths,
            "workspaceSnapshotSha256": workspace_snapshot_sha256,
            "workspaceState": workspace_state,
            "implementationEvidenceByTask": implementation_evidence_by_task,
            "implementationRevisionByTask": implementation_revision_by_task,
        }
    except TaskRunnerError:
        raise
    except Exception as exc:
        raise TaskRunnerError(
            f"batch_compile_execution_failed:{exc}",
            batchId=batch_id,
            commandId=command_id,
        ) from exc


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


def run_batch_compile(
    workspace: Path,
    feature: str,
    batch_id: str,
    code_workspace: Path | list[Path],
) -> dict[str, Any]:
    """
    公共 API：在批次完成后执行编译验证。

    返回: {
        "compileStatus": "passed" | "failed",
        "commandId": str,
        "output": str (失败时),
        "failureCategory": str (失败时)
    }
    """
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        compile_result = _run_batch_compile(workspace, feature, batch_id, code_workspace)
        # 将编译结果持久化到 Plan
        return _integrate_batch_compile_result(workspace, feature, batch_id, compile_result)


def _integrate_batch_compile_result(
    workspace: Path,
    feature: str,
    batch_id: str,
    compile_result: dict[str, Any],
) -> dict[str, Any]:
    """
    集成批次编译结果到 PLAN.json。

    返回更新后的状态和下一步动作。
    """
    _persist_batch_compile_workspace_state(
        _feature_dir(workspace, feature),
        feature,
        batch_id,
        compile_result,
    )
    try:
        result = update_batch_compile_status(workspace, feature, batch_id, compile_result)
    except PlanWriterInputError as exc:
        raise TaskRunnerError(f"plan_writer_error:{exc}") from exc

    if not result.ok:
        raise TaskRunnerError(
            result.error_code,
            detail=result.detail,
            path=str(result.path) if result.path else None,
        )

    compile_status = compile_result.get("compileStatus")
    if compile_status == "passed":
        # 编译通过后，将批次中的所有 implemented 任务标记为 done
        try:
            mark_result = mark_batch_tasks_done_after_compile(workspace, feature, batch_id)
            if not mark_result.ok:
                raise TaskRunnerError(
                    "mark_batch_tasks_done_after_compile_failed",
                    planWriterErrors=mark_result.errors or [],
                )
        except PlanWriterInputError as exc:
            raise TaskRunnerError(f"plan_writer_error:{exc}") from exc

        batch_handoff = (
            mark_result.data.get("batchHandoff")
            if isinstance(mark_result.data, dict)
            else None
        )
        if isinstance(batch_handoff, dict):
            continuation = {
                "action": batch_handoff.get(
                    "requiredAction",
                    "stop_and_open_new_conversation",
                ),
                "completedBatchId": batch_handoff.get("completedBatchId", batch_id),
                "nextBatchId": batch_handoff.get("nextBatchId"),
                "requiresNewConversation": True,
                "userMessage": batch_handoff.get("userMessage"),
            }
            return {
                "compileStatus": "passed",
                "requiredAction": continuation["action"],
                "batchId": batch_id,
                "batchHandoff": batch_handoff,
                "stopAfterBatch": True,
                "requiresNewConversation": True,
                "userMessage": batch_handoff.get("userMessage"),
                "continuation": continuation,
            }

        continuation = _code_session_unlocked(workspace, feature)
        return {
            "compileStatus": "passed",
            "requiredAction": continuation.get("action", "batch_compile_passed"),
            "batchId": batch_id,
            "continuation": continuation,
        }
    else:
        refreshed = load_plan_bundle(_feature_dir(workspace, feature))
        refreshed_batch = refreshed.batches.get(batch_id)
        batch_compile = (
            refreshed_batch.get("batchCompile") if isinstance(refreshed_batch, dict) else None
        )
        batch_compile = batch_compile if isinstance(batch_compile, dict) else {}
        attempts = int(batch_compile.get("repairAttempts", 0))
        exhausted = attempts >= BATCH_COMPILE_MAX_REPAIR_ATTEMPTS
        return {
            "compileStatus": "failed",
            "requiredAction": (
                "escalate_batch_compile_repair_exhausted"
                if exhausted
                else "start_batch_compile_repair"
            ),
            "nextActor": "main_agent" if exhausted else "model",
            "modelRepairRequired": not exhausted,
            "batchId": batch_id,
            "commandId": compile_result.get("commandId"),
            "output": compile_result.get("output", ""),
            "failureCategory": compile_result.get("failureCategory", ""),
            "diagnosticPaths": compile_result.get("diagnosticPaths", []),
            "repairOwnerTaskIds": compile_result.get("repairOwnerTaskIds", []),
            "repairAttempts": attempts,
            "maxRepairAttempts": BATCH_COMPILE_MAX_REPAIR_ATTEMPTS,
            "remainingRepairAttempts": max(
                BATCH_COMPILE_MAX_REPAIR_ATTEMPTS - attempts,
                0,
            ),
        }


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
        # Code has one gate: compile the final batch snapshot after all tasks are implemented.
        if not defer_to_test_stages_enabled(bundle.root):
            raise TaskRunnerError("unsupported_task_validation_policy")
        if defer_to_test_stages_enabled(bundle.root):
            batch_compile = batch_plan.get("batchCompile") if isinstance(batch_plan, dict) else None
            batch_compile_status = batch_compile.get("status") if isinstance(batch_compile, dict) else None

            # 所有任务 implemented 或 done，且编译状态是 pending
            all_tasks_ready = bool(batch_tasks) and all(
                isinstance(task, dict) and normalize_status(task.get("status")) in {"implemented", "done"}
                for task in batch_tasks
            )

            if all_tasks_ready and batch_compile_status == "pending":
                return {
                    "action": "run_batch_compile",
                    "activeBatchId": active_batch_id,
                    "executionLane": execution_lane,
                    "activatedFromHandoff": activated_from_handoff,
                    "userMessage": f"批次 {active_batch_id} 的所有任务已实现，开始执行批次编译验证。",
                }
            elif batch_compile_status == "failed":
                attempts = int(batch_compile.get("repairAttempts", 0))
                exhausted = attempts >= BATCH_COMPILE_MAX_REPAIR_ATTEMPTS
                return {
                    "action": (
                        "batch_compile_repair_exhausted"
                        if exhausted
                        else "start_batch_compile_repair"
                    ),
                    "requiredAction": (
                        "escalate_batch_compile_repair_exhausted"
                        if exhausted
                        else "start_batch_compile_repair"
                    ),
                    "nextActor": "main_agent" if exhausted else "model",
                    "modelRepairRequired": not exhausted,
                    "activeBatchId": active_batch_id,
                    "executionLane": execution_lane,
                    "compileOutput": batch_compile.get("output", ""),
                    "failureCategory": batch_compile.get("failureCategory", ""),
                    "commandId": batch_compile.get("commandId", ""),
                    "diagnosticPaths": batch_compile.get("diagnosticPaths", []),
                    "repairOwnerTaskIds": batch_compile.get("repairOwnerTaskIds", []),
                    "repairAttempts": attempts,
                    "maxRepairAttempts": BATCH_COMPILE_MAX_REPAIR_ATTEMPTS,
                    "remainingRepairAttempts": max(
                        BATCH_COMPILE_MAX_REPAIR_ATTEMPTS - attempts,
                        0,
                    ),
                    "allowedRunnerCommands": (
                        [] if exhausted else ["start-batch-compile-repair"]
                    ),
                    "activatedFromHandoff": activated_from_handoff,
                    "userMessage": (
                        f"批次 {active_batch_id} 的编译修复已达到 3 次上限，流程已阻断。"
                        if exhausted
                        else (
                            f"批次 {active_batch_id} 编译失败；必须由模型启动受控修复任务，"
                            "修复完成并记录新的 implementation evidence 后才能重新编译。"
                        )
                    ),
                }
            elif batch_compile_status == "repairing":
                return {
                    "action": "continue_batch_compile_repair",
                    "requiredAction": "model_fix_then_finish_implementation",
                    "nextActor": "model",
                    "activeBatchId": active_batch_id,
                    "executionLane": execution_lane,
                    "repairTaskId": batch_compile.get("repairTaskId"),
                    "repairAttempts": batch_compile.get("repairAttempts", 0),
                    "maxRepairAttempts": BATCH_COMPILE_MAX_REPAIR_ATTEMPTS,
                    "diagnosticPaths": batch_compile.get("diagnosticPaths", []),
                    "allowedRunnerCommands": ["finish-implementation"],
                    "activatedFromHandoff": activated_from_handoff,
                    "userMessage": (
                        f"模型正在修复批次 {active_batch_id} 的编译问题；"
                        "完成代码修改后记录 implementation evidence。"
                    ),
                }
            elif batch_compile_status == "passed":
                # 编译通过，批次完成，检查是否有下一批次
                next_batch_id = bundle.root.get("nextBatchId")
                if isinstance(next_batch_id, str):
                    # 有下一批次，自动激活
                    _activate_batch_unlocked(workspace, feature, next_batch_id)
                    try:
                        bundle = load_plan_bundle(feature_dir)
                    except ValueError as exc:
                        raise TaskRunnerError(f"invalid_plan_json:{exc}") from exc
                    return {
                        "action": "batch_completed_next_activated",
                        "completedBatchId": active_batch_id,
                        "activeBatchId": next_batch_id,
                        "executionLane": execution_lane,
                        "activatedFromHandoff": activated_from_handoff,
                        "userMessage": f"批次 {active_batch_id} 已完成（编译通过）。已自动激活下一批次 {next_batch_id}。",
                    }
                else:
                    return {
                        "action": "code_done_ready",
                        "completedBatchId": active_batch_id,
                        "activatedFromHandoff": activated_from_handoff,
                        "userMessage": f"批次 {active_batch_id} 已完成（编译通过）。所有批次已完成，功能开发结束。",
                    }
            elif batch_compile_status is not None:
                raise TaskRunnerError(
                    f"batch_compile_status_invalid:{active_batch_id}:{batch_compile_status}"
                )
            elif all_tasks_ready:
                raise TaskRunnerError(f"batch_compile_contract_missing:{active_batch_id}")
            else:
                return {
                    "action": "execute_active_batch",
                    "activeBatchId": active_batch_id,
                    "executionLane": execution_lane,
                    "taskIds": list(entry.get("taskIds", [])),
                    "activatedFromHandoff": activated_from_handoff,
                    "userMessage": f"继续实现批次 {active_batch_id}。",
                }


    unfinished = bundle_unfinished_tasks(bundle)
    if unfinished:
        raise TaskRunnerError("no_active_batch_for_unfinished_tasks:" + ",".join(unfinished))
    return {
        "action": "code_done_ready",
        "activeBatchId": None,
        "activatedFromHandoff": False,
        "validationOutcome": "passed",
        "userMessage": "所有批次均已通过生产代码编译门禁。",
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
        batch_compile = state.get("batchCompile")
        batch_compile = batch_compile if isinstance(batch_compile, dict) else None
        return _emit(
            success,
            runId=state.get("runId"),
            status=state.get("status"),
            completionMode=state.get("completionMode"),
            implementationEvidenceId=state.get("implementationEvidenceId"),
            changedFiles=state.get("changedFiles", []),
            transientValidationFiles=state.get("transientValidationFiles", []),
            batchContinuation=continuation,
            batchCompile=batch_compile,
            continueCurrentBatch=bool(continuation),
            activeBatchId=(
                continuation.get("activeBatchId")
                if continuation
                else batch_compile.get("activeBatchId") if batch_compile else None
            ),
            nextTaskId=continuation.get("nextTaskId") if continuation else None,
            requiredAction=(
                continuation.get("requiredAction")
                if continuation
                else batch_compile.get("requiredAction") if batch_compile else None
            ),
        )
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)








def _cmd_start_batch_compile_repair(args: argparse.Namespace) -> int:
    try:
        workspace, feature, code_workspace = _resolve(args)
        state = start_batch_compile_repair(
            workspace,
            feature,
            args.batch_id,
            args.task_id,
            code_workspace,
        )
        return _emit(True, **state)
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)


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






def _cmd_activate_batch(args: argparse.Namespace) -> int:
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        return _emit(True, **activate_batch(workspace, feature, args.batch_id))
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)


def _cmd_batch_compile(args: argparse.Namespace) -> int:
    """处理 batch-compile 子命令"""
    try:
        workspace, feature, code_workspace = _resolve(args)
        result = run_batch_compile(
            workspace,
            feature,
            args.batch_id,
            code_workspace,
        )
        # P1-6: 根据编译状态返回正确的退出码
        compile_status = result.get("compileStatus")
        success = compile_status == "passed"
        return _emit(
            success,
            **result,
        )
    except (TaskRunnerError, EvidenceStoreError, ValueError) as exc:
        return _emit_error(exc)


def _cmd_code_session(args: argparse.Namespace) -> int:
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        return _emit(True, **code_session(workspace, feature))
    except (TaskRunnerError, ValueError) as exc:
        return _emit_error(exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Implement structured code tasks and compile batches")
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

    finish_implementation_parser = subparsers.add_parser("finish-implementation")
    common(finish_implementation_parser, needs_run=True)
    finish_implementation_parser.add_argument("--no-code-change-why")
    finish_implementation_parser.add_argument("--supporting-file", action="append")
    finish_implementation_parser.set_defaults(func=_cmd_finish_implementation)

    batch_compile_repair = subparsers.add_parser("start-batch-compile-repair")
    common(batch_compile_repair)
    batch_compile_repair.add_argument("--batch-id", required=True)
    batch_compile_repair.set_defaults(func=_cmd_start_batch_compile_repair)

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

    activate = subparsers.add_parser("activate-batch")
    activate.add_argument("--workspace")
    activate.add_argument("--feature")
    activate.add_argument("--batch-id", required=True)
    activate.set_defaults(func=_cmd_activate_batch)

    session = subparsers.add_parser("code-session")
    session.add_argument("--workspace")
    session.add_argument("--feature")
    session.set_defaults(func=_cmd_code_session)

    batch_compile = subparsers.add_parser("batch-compile")
    batch_compile.add_argument("--workspace")
    batch_compile.add_argument("--feature")
    batch_compile.add_argument("--batch-id", required=True)
    batch_compile.add_argument("--code-workspace", required=True, action="append")
    batch_compile.set_defaults(func=_cmd_batch_compile)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
