#!/usr/bin/env python3
"""Durable runtime state for parallel Code batch execution.

The plan remains the business source of truth. This module only stores the
state of one scheduler run, its leases, and references to platform-owned Git
worktree deliveries.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from hooks.evidence_kernel import FileLock
from hooks.json_writer_common import atomic_write_json, feature_dir
from hooks.plan_json import (
    BATCH_ID_RE,
    PARALLEL_EXECUTION_STAGES,
    PlanBundle,
    load_plan_bundle,
    normalize_status,
)


RUN_SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 15 * 60
HEARTBEAT_SECONDS = 30

_PLAN_MUTABLE_KEYS = {
    "status", "activeBatchId", "nextBatchId", "startedAt", "completedAt",
    "updatedAt", "createdAt", "evidenceIds", "completionEvidenceIds",
    "implementationEvidenceIds", "validationEvidenceIds", "latestImplementationEvidenceId",
    "latestPassEvidenceId", "latestPassEvidenceIds", "implementationRevision",
    "taskSetDigest", "completedTaskCount", "batchCompile", "mergeCommitSha",
    "deliveryRunId", "mergedAt",
    "projectCheckEvidenceIds", "latestProjectCheckEvidenceId",
    "projectValidationDisposition", "projectValidationFailedRunIds",
    "activeRunId", "repairAttempts", "repairTaskId", "repairStartedAt",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_id(value: str) -> str:
    if not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in value):
        raise ValueError(f"invalid_parallel_identifier:{value}")
    return value


def runs_root(workspace: Path, feature: str) -> Path:
    return feature_dir(workspace, feature) / ".parallel-runs"


def run_dir(workspace: Path, feature: str, run_id: str) -> Path:
    _safe_id(run_id)
    return runs_root(workspace, feature) / run_id


def manifest_path(workspace: Path, feature: str, run_id: str) -> Path:
    return run_dir(workspace, feature, run_id) / "manifest.json"


def generate_run_id(workspace: Path | None = None, feature: str | None = None) -> str:
    """Generate the operator-facing `cw-YYYYMMDD-NNN` run identifier."""
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    if workspace is None or feature is None:
        return f"cw-{date}-001"
    pattern = re.compile(rf"^cw-{date}-(\d{{3}})$")
    sequence = 0
    for path in runs_root(workspace, feature).glob("*"):
        match = pattern.fullmatch(path.name)
        if match:
            sequence = max(sequence, int(match.group(1)))
    return f"cw-{date}-{sequence + 1:03d}"


def plan_digest(bundle: PlanBundle) -> str:
    # Runtime-only fields are deliberately excluded. Business contract fields
    # remain included, so changing goals, dependencies, scopes or validation
    # commands invalidates an active run instead of silently drifting it.
    payload = {"root": _stable_plan_value(bundle.root), "batches": _stable_plan_value(bundle.batches)}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _stable_plan_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable_plan_value(item)
            for key, item in sorted(value.items())
            if key not in _PLAN_MUTABLE_KEYS
        }
    if isinstance(value, list):
        return [_stable_plan_value(item) for item in value]
    return value


def plan_contract_snapshot(bundle: PlanBundle) -> dict[str, Any]:
    """Return a compact, user-facing contract snapshot for drift diagnostics."""
    root_entries = [item for item in bundle.root.get("batches", []) if isinstance(item, dict)]
    batches: dict[str, dict[str, Any]] = {}
    for entry in root_entries:
        batch_id = str(entry.get("id", ""))
        if not batch_id:
            continue
        batch = bundle.batches.get(batch_id, {})
        contract = _stable_plan_value(batch)
        contract_hash = hashlib.sha256(
            json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        batches[batch_id] = {
            "batchId": batch_id,
            "dependencies": sorted(str(dep) for dep in entry.get("deps", []) if isinstance(dep, str)),
            "workspaceRef": batch_workspace_ref(batch),
            "componentRoots": list(batch_component_roots(batch)),
            "writeSet": list(batch_write_set(batch)),
            "taskIds": sorted(
                str(task.get("id"))
                for task in batch.get("tasks", [])
                if isinstance(task, dict) and task.get("id")
            ),
            "contractHash": contract_hash,
        }
    return {"schemaVersion": 1, "batchIds": sorted(batches), "batches": batches}


def plan_drift_details(expected: Any, bundle: PlanBundle) -> dict[str, Any]:
    """Describe contract changes without exposing full task/prompt contents."""
    current = plan_contract_snapshot(bundle)
    expected_batches = expected.get("batches", {}) if isinstance(expected, dict) else {}
    if not isinstance(expected_batches, dict):
        expected_batches = {}
    current_batches = current["batches"]
    added = sorted(set(current_batches) - set(expected_batches))
    removed = sorted(set(expected_batches) - set(current_batches))
    modified = sorted(
        batch_id
        for batch_id in set(current_batches) & set(expected_batches)
        if current_batches[batch_id].get("contractHash") != expected_batches[batch_id].get("contractHash")
    )
    dependency_changes = []
    workspace_changes = []
    write_set_changes = []
    for batch_id in sorted(set(current_batches) & set(expected_batches)):
        before = expected_batches[batch_id]
        after = current_batches[batch_id]
        if before.get("dependencies", []) != after.get("dependencies", []):
            dependency_changes.append({
                "batchId": batch_id,
                "expected": before.get("dependencies", []),
                "current": after.get("dependencies", []),
            })
        if before.get("workspaceRef") != after.get("workspaceRef"):
            workspace_changes.append({
                "batchId": batch_id,
                "expected": before.get("workspaceRef"),
                "current": after.get("workspaceRef"),
            })
        if before.get("writeSet", []) != after.get("writeSet", []):
            write_set_changes.append({
                "batchId": batch_id,
                "expected": before.get("writeSet", []),
                "current": after.get("writeSet", []),
            })
    return {
        "addedBatches": added,
        "removedBatches": removed,
        "modifiedBatches": modified,
        "dependencyChanges": dependency_changes,
        "workspaceChanges": workspace_changes,
        "writeSetChanges": write_set_changes,
        "currentContract": current,
        "action": "restore_original_plan_or_create_new_run",
    }


def batch_workspace_ref(batch: dict[str, Any]) -> str | None:
    values = {
        str(task.get("workspaceRef"))
        for task in batch.get("tasks", [])
        if isinstance(task, dict) and isinstance(task.get("workspaceRef"), str) and task.get("workspaceRef")
    }
    if len(values) == 1:
        return next(iter(values))
    return None


def batch_component_roots(batch: dict[str, Any]) -> tuple[str, ...]:
    """Return the declared component roots inside a batch's repository."""
    roots: set[str] = set()
    for task in batch.get("tasks", []):
        if not isinstance(task, dict):
            continue
        scope = task.get("scope")
        workspace_roots = scope.get("workspaceRoots") if isinstance(scope, dict) else None
        ref = task.get("workspaceRef")
        if isinstance(workspace_roots, dict) and isinstance(ref, str):
            key = "default" if ref == "default" else ref
            value = workspace_roots.get(key)
            if isinstance(value, str) and value:
                roots.add(value.replace("\\", "/").strip("/") or ".")
    return tuple(sorted(roots))


def batch_write_set(batch: dict[str, Any]) -> tuple[str, ...]:
    paths: set[str] = set()
    for task in batch.get("tasks", []):
        if not isinstance(task, dict):
            continue
        scope = task.get("scope")
        if isinstance(scope, dict):
            raw = scope.get("paths", [])
            if isinstance(raw, list):
                paths.update(str(item).replace("\\", "/") for item in raw if isinstance(item, str) and item.strip())
        raw_expected = task.get("expectedFiles", [])
        if isinstance(raw_expected, list):
            paths.update(str(item).replace("\\", "/") for item in raw_expected if isinstance(item, str) and item.strip())
    return tuple(sorted(paths))


def parallel_plan_errors(bundle: PlanBundle) -> list[str]:
    """Validate the fields required by the parallel scheduler.

    Existing plans may omit an explicit root workspaceRef; it is safely
    derived from the batch task contracts.  Ambiguous batches are rejected.
    """
    errors: list[str] = []
    entries = [item for item in bundle.root.get("batches", []) if isinstance(item, dict)]
    by_id = {str(item.get("id")): item for item in entries}
    graph: dict[str, list[str]] = {}
    order = {str(item.get("id")): index for index, item in enumerate(entries)}
    for entry in entries:
        batch_id = str(entry.get("id", ""))
        if not BATCH_ID_RE.fullmatch(batch_id):
            errors.append(f"{batch_id}.id_invalid")
            continue
        batch = bundle.batches.get(batch_id, {})
        workspace = batch_workspace_ref(batch)
        declared_workspace = entry.get("workspaceRef")
        if declared_workspace is not None and declared_workspace != workspace:
            errors.append(f"{batch_id}.workspaceRef_projection_mismatch")
        if workspace is None:
            errors.append(f"{batch_id}.workspaceRef_ambiguous_or_missing")
        execution_stage = entry.get("executionStage", "parallel")
        if execution_stage not in PARALLEL_EXECUTION_STAGES:
            errors.append(f"{batch_id}.executionStage_invalid")
        deps = entry.get("deps", [])
        graph[batch_id] = [str(dep) for dep in deps if isinstance(dep, str)] if isinstance(deps, list) else []
        for dep in graph[batch_id]:
            if dep not in by_id:
                errors.append(f"{batch_id}.dependency_unknown:{dep}")
            elif dep == batch_id:
                errors.append(f"{batch_id}.dependency_self")
            elif order.get(dep, 10**9) >= order.get(batch_id, -1):
                errors.append(f"{batch_id}.dependency_not_earlier:{dep}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, stack: list[str]) -> None:
        if node in visiting:
            errors.append("batch_dependency_cycle:" + "->".join([*stack, node]))
            return
        if node in visited:
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            visit(dep, [*stack, node])
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node, [])
    return sorted(set(errors))


def create_manifest(
    workspace: Path,
    feature: str,
    run_id: str | None = None,
    *,
    max_parallel: int = 4,
    timeout_seconds: int = 3600,
    repositories: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    bundle = load_plan_bundle(feature_dir(workspace, feature))
    errors = parallel_plan_errors(bundle)
    if errors:
        raise ValueError("parallel_plan_invalid:" + ";".join(errors))
    refs = {batch_workspace_ref(batch) for batch in bundle.batches.values()}
    if not repositories:
        raise ValueError("parallel_repository_bindings_required")
    repositories = {
        ref: {**binding, "headSha": binding.get("headSha") or binding.get("baseSha")}
        for ref, binding in repositories.items()
        if isinstance(binding, dict)
    }
    missing = sorted(ref for ref in refs if isinstance(ref, str) and ref not in repositories)
    if missing:
        raise ValueError("parallel_repository_binding_missing:" + ",".join(missing))
    root = runs_root(workspace, feature)
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(root / ".run-id.lock"):
        run_id = _safe_id(run_id or generate_run_id(workspace, feature))
        target = run_dir(workspace, feature, run_id)
        if target.exists():
            raise ValueError(f"parallel_run_exists:{run_id}")
        target.joinpath("batches").mkdir(parents=True, exist_ok=False)
        target.joinpath("leases").mkdir()
    entries: dict[str, Any] = {}
    for entry in bundle.root.get("batches", []):
        batch_id = str(entry["id"])
        batch = bundle.batches[batch_id]
        batch_status = normalize_status(entry.get("status"))
        merged_commit_sha = batch.get("mergeCommitSha")
        if batch_status == "done" and (
            not isinstance(merged_commit_sha, str) or not merged_commit_sha.strip()
        ):
            raise ValueError(f"parallel_plan_done_without_merge_evidence:{batch_id}")
        task_ids = [
            str(task_id)
            for task_id in batch.get("taskIds", [])
            if isinstance(task_id, str) and task_id.strip()
        ]
        if not task_ids:
            task_ids = [
                str(task.get("id"))
                for task in batch.get("tasks", [])
                if isinstance(task, dict) and isinstance(task.get("id"), str) and task.get("id").strip()
            ]
        entries[batch_id] = {
            "batchId": batch_id,
            # Persist the Plan-owned task contract in the durable run. The
            # workflow must consume these IDs from the scheduler response; it
            # must not rediscover or invent them from the artifact workspace.
            "taskIds": task_ids,
            "executionLane": entry.get("executionLane"),
            "workspaceRef": batch_workspace_ref(batch),
            "componentRoots": list(batch_component_roots(batch)),
            "repositoryRef": batch_workspace_ref(batch),
            "gitRoot": repositories[str(batch_workspace_ref(batch))]["gitRoot"],
            "writeSet": list(batch_write_set(batch)),
            "executionStage": entry.get("executionStage", "parallel"),
            "dependencies": sorted(set(entry.get("deps", []))),
            "status": "merged" if batch_status == "done" else "failed" if batch_status == "failed" else "pending",
            "lease": None,
            "worktreePath": None,
            "branchName": None,
            "commitSha": None,
            "compileStatus": (batch.get("batchCompile") or {}).get("status", "pending"),
            "mergeCommitSha": merged_commit_sha if batch_status == "done" else None,
            "startedAt": None,
            "completedAt": None,
            "error": None,
        }
    manifest = {
        "schemaVersion": RUN_SCHEMA_VERSION,
        "runId": run_id,
        "featureId": feature,
        "status": "created",
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "baseSha": None,
        "repositories": repositories,
        "planDigest": plan_digest(bundle),
        "planContract": plan_contract_snapshot(bundle),
        "maxParallel": max(1, int(max_parallel)),
        "timeoutPerBatch": max(1, int(timeout_seconds)),
        "batches": entries,
        "costs": {"totalTimeSeconds": 0, "estimatedTokens": 0, "batchCosts": {}},
    }
    atomic_write_json(manifest_path(workspace, feature, run_id), manifest)
    return manifest


def load_manifest(workspace: Path, feature: str, run_id: str) -> dict[str, Any]:
    path = manifest_path(workspace, feature, run_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"parallel_run_not_found:{run_id}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"parallel_manifest_invalid:{run_id}") from exc
    if not isinstance(data, dict) or data.get("runId") != run_id:
        raise ValueError(f"parallel_manifest_invalid:{run_id}")
    return _strip_legacy_parallel_flags(data)


def _strip_legacy_parallel_flags(manifest: dict[str, Any]) -> dict[str, Any]:
    """Ignore obsolete hint flags from manifests created before worktree DAGs.

    Native worktree isolation has made same-lane and same-repository overlap
    schedulable. These values never governed the scheduler, but old `false`
    values made diagnostics imply serialization.
    """
    batches = manifest.get("batches")
    if not isinstance(batches, dict):
        return manifest
    for batch in batches.values():
        if isinstance(batch, dict):
            batch.pop("canParallelInSameLane", None)
            batch.pop("canParallelInSameRepository", None)
    return manifest


def save_manifest(workspace: Path, feature: str, run_id: str, manifest: dict[str, Any]) -> None:
    manifest = dict(manifest)
    manifest["updatedAt"] = utc_now()
    atomic_write_json(manifest_path(workspace, feature, run_id), manifest)


def append_event(workspace: Path, feature: str, run_id: str, event: str, **details: Any) -> None:
    path = run_dir(workspace, feature, run_id) / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"at": utc_now(), "event": event, **details}
    with FileLock(path.with_suffix(".lock")):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


@contextmanager
def run_lock(workspace: Path, feature: str, run_id: str) -> Iterator[None]:
    with FileLock(run_dir(workspace, feature, run_id) / ".lock"):
        yield


def lease_path(workspace: Path, feature: str, run_id: str, batch_id: str) -> Path:
    _safe_id(batch_id)
    return run_dir(workspace, feature, run_id) / "leases" / f"{batch_id}.json"


def acquire_lease(workspace: Path, feature: str, run_id: str, batch_id: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS, owner_token: str | None = None) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = manifest.get("batches", {}).get(batch_id)
        if not isinstance(batch, dict):
            raise ValueError(f"parallel_batch_not_found:{batch_id}")
        if batch.get("status") not in {"pending", "leased"}:
            raise ValueError(f"parallel_batch_not_leaseable:{batch_id}:{batch.get('status')}")
        dependencies = batch.get("dependencies", [])
        for dependency in dependencies:
            dep_batch = manifest.get("batches", {}).get(dependency)
            if not isinstance(dep_batch, dict) or dep_batch.get("status") != "merged":
                raise ValueError(f"parallel_batch_dependencies_unmerged:{batch_id}:{dependency}")
            if not dep_batch.get("mergeCommitSha"):
                raise ValueError(f"parallel_batch_dependency_incomplete:{batch_id}:{dependency}")
        path = lease_path(workspace, feature, run_id, batch_id)
        now = time.time()
        token = owner_token or uuid.uuid4().hex
        with FileLock(path.with_suffix(".lock")):
            existing = None
            if path.is_file():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    existing = None
            if isinstance(existing, dict) and existing.get("expiresEpoch", 0) > now and existing.get("ownerToken") != token:
                raise ValueError(f"parallel_batch_lease_held:{batch_id}")
            lease = {
                "runId": run_id,
                "batchId": batch_id,
                "ownerToken": token,
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "startedAt": utc_now(),
                "heartbeatAt": utc_now(),
                "expiresAt": datetime.fromtimestamp(now + ttl_seconds, timezone.utc).isoformat().replace("+00:00", "Z"),
                "expiresEpoch": now + ttl_seconds,
            }
            atomic_write_json(path, lease)
        # Keep the bearer token only in the lease file returned to the worker;
        # the durable manifest and audit log contain metadata, never the token.
        batch["lease"] = {
            key: value
            for key, value in lease.items()
            if key not in {"expiresEpoch", "ownerToken"}
        }
        batch["status"] = "leased"
        save_manifest(workspace, feature, run_id, manifest)
    append_event(
        workspace,
        feature,
        run_id,
        "lease_acquired",
        batchId=batch_id,
        ownerTokenSha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
    )
    return lease


def check_lease(workspace: Path, feature: str, run_id: str, batch_id: str, owner_token: str) -> bool:
    path = lease_path(workspace, feature, run_id, batch_id)
    try:
        lease = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(lease, dict) or lease.get("ownerToken") != owner_token:
        return False
    if float(lease.get("expiresEpoch", 0)) <= time.time():
        return False
    # The token and heartbeat are the authority used by worker processes.  A
    # scheduler may acquire a lease in one process and launch the worker in a
    # second process, so PID liveness is used by reclaim_lease rather than as
    # a hard validity check here.
    return True


def reclaim_lease(workspace: Path, feature: str, run_id: str, batch_id: str, *, force: bool = False) -> bool:
    """Remove an expired or stale lease and return whether it was removed."""
    path = lease_path(workspace, feature, run_id, batch_id)
    if not path.is_file():
        return False
    try:
        lease = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        lease = {}
    stale = force or not isinstance(lease, dict) or float(lease.get("expiresEpoch", 0)) <= time.time()
    if not stale and lease.get("host") == socket.gethostname() and isinstance(lease.get("pid"), int):
        try:
            os.kill(int(lease["pid"]), 0)
        except OSError:
            stale = True
    if not stale:
        return False
    with FileLock(path.with_suffix(".lock")):
        path.unlink(missing_ok=True)
    manifest = load_manifest(workspace, feature, run_id)
    item = manifest.get("batches", {}).get(batch_id)
    if isinstance(item, dict):
        item["lease"] = None
        if item.get("status") == "leased":
            item["status"] = "pending"
    save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "lease_reclaimed", batchId=batch_id, force=force)
    return True


def renew_lease(workspace: Path, feature: str, run_id: str, batch_id: str, owner_token: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> dict[str, Any]:
    if not check_lease(workspace, feature, run_id, batch_id, owner_token):
        raise ValueError(f"parallel_batch_lease_invalid:{batch_id}")
    path = lease_path(workspace, feature, run_id, batch_id)
    now = time.time()
    with FileLock(path.with_suffix(".lock")):
        lease = json.loads(path.read_text(encoding="utf-8"))
        lease.update({"heartbeatAt": utc_now(), "expiresAt": datetime.fromtimestamp(now + ttl_seconds, timezone.utc).isoformat().replace("+00:00", "Z"), "expiresEpoch": now + ttl_seconds})
        atomic_write_json(path, lease)
    return lease


def release_lease(workspace: Path, feature: str, run_id: str, batch_id: str, owner_token: str, *, final_status: str = "pending") -> None:
    if final_status not in {"pending", "failed", "compile_failed", "blocked", "ready_to_merge"}:
        raise ValueError(f"parallel_batch_release_status_invalid:{final_status}")

    # A worker may only release a delivery as mergeable after `seal` has
    # persisted a delivery commit.  Without this guard a failed worker could
    # create a false merge frontier with no commit SHA.
    path = lease_path(workspace, feature, run_id, batch_id)
    with run_lock(workspace, feature, run_id):
        with FileLock(path.with_suffix(".lock")):
            if not check_lease(workspace, feature, run_id, batch_id, owner_token):
                raise ValueError(f"parallel_batch_lease_invalid:{batch_id}")
            manifest = load_manifest(workspace, feature, run_id)
            batch = manifest.get("batches", {}).get(batch_id)
            if not isinstance(batch, dict):
                raise ValueError(f"parallel_batch_not_found:{batch_id}")
            if final_status == "ready_to_merge":
                if batch.get("status") != "ready_to_merge" or batch.get("compileStatus") != "passed":
                    raise ValueError(f"parallel_batch_not_ready_to_release:{batch_id}")
                commit_sha = batch.get("commitSha")
                if not isinstance(commit_sha, str) or not commit_sha.strip():
                    raise ValueError(f"parallel_batch_not_sealed:{batch_id}")
            path.unlink(missing_ok=True)
            batch["lease"] = None
            batch["status"] = final_status
            save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "lease_released", batchId=batch_id, status=final_status)


def dependency_graph(manifest: dict[str, Any]) -> dict[str, list[str]]:
    return {batch_id: list(item.get("dependencies", [])) for batch_id, item in manifest.get("batches", {}).items() if isinstance(item, dict)}


def ready_batches(manifest: dict[str, Any]) -> list[str]:
    batches = manifest.get("batches", {})
    ready: list[str] = []
    for batch_id, item in batches.items():
        if not isinstance(item, dict) or item.get("status") != "pending":
            continue
        deps = item.get("dependencies", [])
        if all(
            isinstance(batches.get(dep), dict)
            and batches[dep].get("status") == "merged"
            and batches[dep].get("mergeCommitSha")
            for dep in deps
        ):
            ready.append(batch_id)
    return sorted(ready)


def mergeable_batches(manifest: dict[str, Any]) -> list[str]:
    """Return sealed Batch results that may be merged in this barrier.

    A Batch becomes eligible only after its own compile/seal step and after
    every dependency has already reached ``merged`` with a merge commit.
    This keeps dependency release tied to an actual merge, not an agent result.
    """
    batches = manifest.get("batches", {})
    result: list[str] = []
    for batch_id, item in batches.items():
        if not isinstance(item, dict) or item.get("status") != "ready_to_merge":
            continue
        if not item.get("commitSha"):
            continue
        # A mergeable delivery must have completed the worker lease handoff.
        # This prevents a still-running or stale worker from being merged.
        if item.get("lease") is not None:
            continue
        dependencies = item.get("dependencies", [])
        if all(
            isinstance(batches.get(dep), dict)
            and batches[dep].get("status") == "merged"
            and batches[dep].get("mergeCommitSha")
            for dep in dependencies
        ):
            result.append(str(batch_id))
    return sorted(result)


def resource_groups(manifest: dict[str, Any], batch_ids: list[str] | None = None) -> list[list[str]]:
    """Build conservative execution waves from stage and physical write sets.

    Worktrees isolate checkouts, not shared delivery risk.  A batch with an
    unknown write set is therefore serialized with another batch in the same
    repository.  Known paths conflict when they are equal or one is an
    ancestor of the other.  Special stages (proto/global/integration) are
    always single-batch waves and are ordered before ordinary implementation.
    """
    ids = sorted(set(batch_ids or ready_batches(manifest)))
    if not ids:
        return []
    stages = {"proto": 0, "global": 1, "parallel": 2, "integration": 3}
    by_id = manifest.get("batches", {})
    stage_rank = lambda bid: stages.get(str(by_id.get(bid, {}).get("executionStage", "parallel")), 2)
    frontier_rank = min(stage_rank(batch_id) for batch_id in ids)
    frontier = [batch_id for batch_id in ids if stage_rank(batch_id) == frontier_rank]
    if frontier_rank != stages["parallel"]:
        return [[batch_id] for batch_id in frontier]

    def normalized_paths(batch_id: str) -> tuple[str, ...]:
        raw = by_id.get(batch_id, {}).get("writeSet")
        if not isinstance(raw, list):
            return ()
        return tuple(sorted({str(path).replace("\\", "/").strip("/") for path in raw if str(path).strip()}))

    def overlaps(left: str, right: str) -> bool:
        if left in {".", ""} or right in {".", ""}:
            return True
        if left == right:
            return True
        return left.startswith(right + "/") or right.startswith(left + "/")

    def conflicts(left: str, right: str) -> bool:
        a = by_id.get(left, {})
        b = by_id.get(right, {})
        left_repo = a.get("gitRoot") or a.get("repositoryRef") or a.get("workspaceRef")
        right_repo = b.get("gitRoot") or b.get("repositoryRef") or b.get("workspaceRef")
        if left_repo != right_repo:
            return False
        left_paths = normalized_paths(left)
        right_paths = normalized_paths(right)
        if not left_paths or not right_paths:
            return True
        return any(overlaps(path_a, path_b) for path_a in left_paths for path_b in right_paths)

    waves: list[list[str]] = []
    for batch_id in frontier:
        for wave in waves:
            if not any(conflicts(batch_id, existing) for existing in wave):
                wave.append(batch_id)
                break
        else:
            waves.append([batch_id])
    return waves


def list_runs(workspace: Path, feature: str) -> list[dict[str, Any]]:
    result = []
    for path in sorted(runs_root(workspace, feature).glob("*/manifest.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            result.append(_strip_legacy_parallel_flags(data))
    return result


def get_active_run(workspace: Path, feature: str) -> str | None:
    active = [
        item
        for item in list_runs(workspace, feature)
        if item.get("status") in {"created", "running", "merging", "verifying", "blocked", "needs_resolution"}
    ]
    if len(active) > 1:
        raise ValueError("multiple_parallel_runs_active")
    return str(active[0]["runId"]) if active else None
