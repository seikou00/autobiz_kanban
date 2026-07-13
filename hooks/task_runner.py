#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transactional execution entrypoint for structured code tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_store import EvidenceStoreError, append_evidence, read_records, stream_path  # noqa: E402
from hooks.evidence_kernel import FileLock  # noqa: E402
from hooks.json_writer_common import atomic_write_json, resolve_feature, resolve_workspace  # noqa: E402
from hooks.plan_json import (  # noqa: E402
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


DEFAULT_TIMEOUT_SECONDS = 300
BEHAVIOR_VALIDATION_KINDS = {"behavior_test", "integration_test", "e2e_test", "static_check"}


class TaskRunnerError(ValueError):
    pass


RepositoryMap = dict[str, Path]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _emit(ok: bool, **data: Any) -> int:
    print(json.dumps({"ok": ok, **data}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


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
    completed = subprocess.run(
        ["git", "-C", str(code_workspace), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise TaskRunnerError(f"code_workspace_not_git_repository:{code_workspace}")
    return Path(completed.stdout.strip()).resolve()


def _resolve_repositories(code_workspaces: Path | list[Path]) -> RepositoryMap:
    values = code_workspaces if isinstance(code_workspaces, list) else [code_workspaces]
    repositories: RepositoryMap = {}
    seen_roots: set[Path] = set()
    for workspace in values:
        root = _git_root(workspace)
        if root in seen_roots:
            continue
        repository_id = root.name
        if repository_id in repositories:
            raise TaskRunnerError(f"duplicate_repository_id:{repository_id}")
        repositories[repository_id] = root
        seen_roots.add(root)
    if not repositories:
        raise TaskRunnerError("code_workspace_missing")
    return repositories


def _repository_state(repositories: RepositoryMap) -> list[dict[str, Any]]:
    return [
        {"id": repository_id, "path": str(repo), "snapshot": _git_snapshot(repo)}
        for repository_id, repo in repositories.items()
    ]


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


def _hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_snapshot(repo: Path) -> dict[str, str | None]:
    completed = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-co", "--exclude-standard", "-z"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise TaskRunnerError("git_snapshot_failed")
    paths = sorted({raw.decode("utf-8", errors="surrogateescape") for raw in completed.stdout.split(b"\0") if raw})
    return {path: _hash_file(repo / path) for path in paths}


def _snapshot_changes(
    before: dict[str, str | None],
    after: dict[str, str | None],
) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    deleted = {
        path: digest
        for path, digest in before.items()
        if (path not in after or after.get(path) is None) and isinstance(digest, str)
    }
    created = {
        path: digest
        for path, digest in after.items()
        if path not in before and isinstance(digest, str)
    }
    renamed_from: set[str] = set()
    renamed_to: set[str] = set()
    for old_path, old_digest in sorted(deleted.items()):
        new_path = next(
            (
                candidate
                for candidate, digest in sorted(created.items())
                if candidate not in renamed_to and digest == old_digest
            ),
            None,
        )
        if new_path is None:
            continue
        renamed_from.add(old_path)
        renamed_to.add(new_path)
        changes.append(
            {
                "path": new_path,
                "fromPath": old_path,
                "operation": "renamed",
                "kind": _file_kind(new_path),
                "summary": f"Task execution renamed {old_path} to {new_path}",
                "reason": "Detected from matching task run file hashes",
            }
        )
    for path in sorted(set(before) | set(after)):
        if path in renamed_from or path in renamed_to:
            continue
        old = before.get(path)
        new = after.get(path)
        if old == new:
            continue
        if path not in before:
            operation = "created"
        elif path not in after or new is None:
            operation = "deleted"
        else:
            operation = "modified"
        changes.append(
            {
                "path": path,
                "operation": operation,
                "kind": _file_kind(path),
                "summary": f"Task execution {operation} {path}",
                "reason": "Detected from the task run Git snapshot",
            }
        )
    return changes


def _file_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".md", ".rst", ".txt"}:
        return "docs"
    if suffix in {".json", ".yaml", ".yml", ".toml", ".ini", ".properties"}:
        return "config"
    if "test" in Path(path).parts or Path(path).name.lower().startswith("test"):
        return "test"
    return "source"


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{stamp}-{uuid.uuid4().hex[:8]}"


def _save_run(path: Path, state: dict[str, Any]) -> None:
    state["updatedAt"] = _utc_now()
    atomic_write_json(path, state)


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
    repositories = _resolve_repositories(code_workspace)
    repository_state = _repository_state(repositories)
    active: list[str] = []
    runs_root = feature_dir / ".task-runs"
    for path in runs_root.glob("T*/*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if item.get("status") not in {"done", "failed", "aborted"}:
            active.append(f"{item.get('taskId', path.parent.name)}:{item.get('runId', path.stem)}")
    if active:
        active_tasks = sorted({item.partition(":")[0] for item in active})
        if task_id in active_tasks:
            raise TaskRunnerError("active_task_run_exists:" + ",".join(active))
        raise TaskRunnerError("active_feature_task_run_exists:" + ",".join(active_tasks))

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
        "repositories": repository_state,
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
    repositories = _resolve_repositories(code_workspace)
    _assert_repositories_match(state, repositories)
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
        state["status"] = "done" if success else "failed"
        _save_run(path, state)
        return success, state
    if state.get("status") == "validation_running" and state.get("evidenceIds"):
        _validate_run_evidence(feature_dir, state)
        _save_run(path, state)

    repository_states = _state_repositories(state)
    if not repository_states:
        raise TaskRunnerError("task_run_snapshot_missing")
    multiple_repositories = len(repository_states) > 1
    file_changes: list[dict[str, str]] = []
    final_repositories: list[dict[str, Any]] = []
    for repository_state in repository_states:
        repository_id = str(repository_state.get("id", ""))
        before = repository_state.get("snapshot")
        repo = repositories.get(repository_id)
        if not isinstance(before, dict) or repo is None:
            raise TaskRunnerError(f"task_run_repository_snapshot_missing:{repository_id}")
        final_snapshot = _git_snapshot(repo)
        repo_changes = _snapshot_changes(before, final_snapshot)
        if multiple_repositories:
            for change in repo_changes:
                change["path"] = f"{repository_id}:{change['path']}"
                if "fromPath" in change:
                    change["fromPath"] = f"{repository_id}:{change['fromPath']}"
                change["repository"] = repository_id
        file_changes.extend(repo_changes)
        final_repositories.append({"id": repository_id, "path": str(repo), "snapshot": final_snapshot})
    scope = task.get("scope")
    scope_paths = scope.get("paths") if isinstance(scope, dict) and isinstance(scope.get("paths"), list) else []
    if scope_paths:
        outside = [
            path
            for change in file_changes
            for path in (change.get("path"), change.get("fromPath"))
            if isinstance(path, str) and not _path_in_scope(path, scope_paths)
        ]
        if outside:
            raise TaskRunnerError("out_of_scope_changes_detected:" + ",".join(sorted(set(outside))))
    normalized_supporting = _validate_supporting_files(repositories, supporting_files)
    if file_changes:
        if no_code_change_why or normalized_supporting:
            raise TaskRunnerError("no_code_change_claim_conflicts_with_snapshot")
        completion_mode = "implemented"
    else:
        if not no_code_change_why or not normalized_supporting:
            raise TaskRunnerError("no_code_change_requires_reason_and_supporting_files")
        commands = [item for item in task.get("validationCommands", []) if isinstance(item, dict)]
        if not any(item.get("kind") in BEHAVIOR_VALIDATION_KINDS for item in commands if item.get("required") is True):
            raise TaskRunnerError("verified_existing_requires_behavior_validation")
        completion_mode = "verified_existing"

    _check_required_coverage(task)
    changed_files = sorted(
        {
            value
            for change in file_changes
            for value in (change.get("path"), change.get("fromPath"))
            if isinstance(value, str)
        }
    )
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
    state["status"] = "done" if success else "failed"
    _save_run(path, state)
    return success, state


def _path_in_scope(path: str, scope_paths: list[Any]) -> bool:
    candidate = Path(path)
    for raw in scope_paths:
        if not isinstance(raw, str) or not raw:
            continue
        scope = Path(raw)
        if candidate == scope or scope in candidate.parents:
            return True
    return False


def _abort_task_unlocked(workspace: Path, feature: str, task_id: str, run_id: str) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    path, state = _load_run(feature_dir, task_id, run_id)
    if state.get("status") in {"evidence_written", "done", "failed"}:
        raise TaskRunnerError(f"task_run_cannot_abort:{state.get('status')}")
    state["status"] = "aborted"
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


def abort_task(workspace: Path, feature: str, task_id: str, run_id: str) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        return _abort_task_unlocked(workspace, feature, task_id, run_id)


def run_project_checks(
    workspace: Path,
    feature: str,
    code_workspace: Path | list[Path],
) -> tuple[bool, list[str]]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        return _run_project_checks_unlocked(workspace, feature, code_workspace)


def activate_batch(workspace: Path, feature: str, batch_id: str) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    with _task_run_lock(feature_dir):
        result = activate_plan_batch(workspace, feature, batch_id)
        if not result.ok:
            errors = result.errors or []
            detail = ";".join(
                f"{item.get('reason')}:{item.get('detail', '')}" for item in errors
            )
            raise TaskRunnerError(detail or "batch_activation_failed")
        return dict(result.data or {})


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
        return _emit(False, error=str(exc))


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
        return _emit(
            success,
            error=None if success else "validation_failed",
            runId=state.get("runId"),
            status=state.get("status"),
            completionMode=state.get("completionMode"),
            evidenceIds=state.get("evidenceIds", []),
            completionEvidenceIds=state.get("completionEvidenceIds", []),
            batchHandoff=state.get("batchHandoff"),
            stopAfterBatch=bool(state.get("batchHandoff")),
        )
    except (TaskRunnerError, ValueError) as exc:
        return _emit(False, error=str(exc))


def _cmd_recover(args: argparse.Namespace) -> int:
    return _cmd_complete(args)


def _cmd_abort(args: argparse.Namespace) -> int:
    try:
        workspace, feature, _ = _resolve(args)
        state = abort_task(workspace, feature, args.task_id, args.run_id)
        return _emit(True, **state)
    except (TaskRunnerError, ValueError) as exc:
        return _emit(False, error=str(exc))


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
        return _emit(False, error=str(exc))


def _cmd_project_check(args: argparse.Namespace) -> int:
    try:
        workspace, feature, code_workspace = _resolve(args)
        success, evidence_ids = run_project_checks(workspace, feature, code_workspace)
        return _emit(success, error=None if success else "project_validation_failed", evidenceIds=evidence_ids)
    except (TaskRunnerError, EvidenceStoreError, ValueError) as exc:
        return _emit(False, error=str(exc))


def _cmd_activate_batch(args: argparse.Namespace) -> int:
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        return _emit(True, **activate_batch(workspace, feature, args.batch_id))
    except (TaskRunnerError, ValueError) as exc:
        return _emit(False, error=str(exc))


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
    abort.set_defaults(func=_cmd_abort)

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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
