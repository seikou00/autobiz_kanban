#!/usr/bin/env python3
"""Plan-aware scheduler commands for parallel Code batch runs.

This process never implements code itself.  It creates and updates the durable
run manifest consumed by the workflow runtime and Task Runner entrypoints.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import feature_dir, resolve_feature, resolve_workspace
from hooks.commit_message import build_commit_message, normalize_task_card_id
from hooks.evidence_kernel import FileLock
from hooks.parallel_runtime import (
    append_event,
    create_manifest,
    delivery_stage_names,
    get_active_run,
    lease_path,
    list_runs,
    load_manifest,
    parallel_plan_errors,
    plan_drift_details,
    plan_digest,
    mergeable_batches,
    stage_recovery_batches,
    ready_batches,
    resource_groups,
    select_runnable_batches,
    run_lock,
    save_manifest,
)
from hooks.plan_json import load_plan_bundle
from hooks.parallel_validation_ownership import validation_ownership_errors
from hooks.repository_snapshot import (
    PLATFORM_RUNTIME_DIRECTORY,
    RepositorySnapshotError,
    current_git_branch,
    git_status_porcelain,
    resolve_git_root,
)
_BOOTSTRAP_IGNORE_RULES = (
    ".cmbdevclaw/large_tool_results/",
    ".autobizdevops/features/*/.parallel-runs/",
)
MAX_AUTOMATIC_BATCH_RECOVERY_ATTEMPTS = 2


def _parse_timestamp_epoch(value: object) -> float | None:
    """Parse one persisted UTC timestamp without making recovery fragile."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _lease_staleness_reason_locked(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    batch: dict[str, Any],
    *,
    now: float,
    timeout_seconds: int,
) -> str | None:
    """Return why an active Batch has lost execution authority, if it has.

    This helper runs while the scheduler owns the run lock and takes the
    per-Batch lease lock before inspecting the bearer lease.  A valid lease is
    intentionally left untouched: resuming a workflow must never steal work
    from a worker that is still renewing its lease.  Conversely, a missing, corrupt,
    expired, or over-deadline lease has no safe worker authority
    left and can be deterministically retried by the scheduler.
    """
    path = lease_path(workspace, feature, run_id, batch_id)
    with FileLock(path.with_suffix(".lock")):
        if not path.is_file():
            return "lease_missing"
        try:
            lease = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            return "lease_invalid"
        if not isinstance(lease, dict):
            path.unlink(missing_ok=True)
            return "lease_invalid"
        try:
            expires_epoch = float(lease.get("expiresEpoch", 0))
        except (TypeError, ValueError):
            expires_epoch = 0
        if expires_epoch <= now:
            path.unlink(missing_ok=True)
            return "lease_expired"

        # A lease that survives beyond the configured Batch deadline is
        # still not allowed to reserve a scheduler slot forever.  `startedAt`
        # is persisted in the manifest once implementation begins; the lease
        # timestamp covers a worker that dies during initial provisioning.
        started_epoch = _parse_timestamp_epoch(batch.get("startedAt"))
        if started_epoch is None:
            started_epoch = _parse_timestamp_epoch(lease.get("startedAt"))
        if started_epoch is not None and now - started_epoch >= max(1, timeout_seconds):
            path.unlink(missing_ok=True)
            return "batch_timeout"
    return None


def _mark_retry_pending_locked(
    workspace: Path,
    feature: str,
    run_id: str,
    manifest: dict[str, Any],
    batch_id: str,
    batch: dict[str, Any],
    *,
    error: str,
    previous_status: str | None = None,
) -> bool:
    """Persist one task-scoped recovery marker without delegating to a worker."""
    previous = previous_status if previous_status is not None else str(batch.get("status") or "")
    previous_recovery = batch.get("recovery") if isinstance(batch.get("recovery"), dict) else {}
    retry_attempts = (
        1
        if previous == "blocked" and previous_recovery.get("status") == "retry_exhausted"
        else int(previous_recovery.get("retryAttempts", 0)) + 1
    )
    resume_status = (
        "ready_to_candidate"
        if previous == "ready_to_candidate"
        else "sealed"
        if batch.get("commitSha")
        else "pending"
    )
    batch.update(
        {
            "status": "retry_pending",
            "error": error,
            "activeStage": None,
            "startedAt": None,
            "completedAt": None,
            "recovery": {
                **previous_recovery,
                "retryAttempts": retry_attempts,
                "lastFailureAt": manifest.get("updatedAt"),
                "lastError": error,
                "resumeStatus": resume_status,
                "status": "pending_retry",
            },
        }
    )
    return _clear_retry_lease_locked(workspace, feature, run_id, batch_id, batch)


def _recover_stale_active_batches_locked(
    workspace: Path,
    feature: str,
    run_id: str,
    manifest: dict[str, Any],
) -> list[dict[str, str]]:
    """Move orphaned leased/running Batches into durable retry recovery."""
    now = time.time()
    timeout_seconds = max(1, int(manifest.get("timeoutPerBatch", 3600)))
    recovered: list[dict[str, str]] = []
    for raw_batch_id, batch in manifest.get("batches", {}).items():
        if not isinstance(batch, dict) or batch.get("status") not in {"leased", "running"}:
            continue
        batch_id = str(raw_batch_id)
        reason = _lease_staleness_reason_locked(
            workspace,
            feature,
            run_id,
            batch_id,
            batch,
            now=now,
            timeout_seconds=timeout_seconds,
        )
        if reason is None:
            continue
        _mark_retry_pending_locked(
            workspace,
            feature,
            run_id,
            manifest,
            batch_id,
            batch,
            error=f"parallel_batch_recovery:{reason}",
        )
        recovered.append({"batchId": batch_id, "reason": reason})
    return recovered


def _unresolved_state(manifest: dict[str, Any]) -> tuple[list[str], list[str], set[str]]:
    """Return retained, task-scoped manual work and the Batches it owns."""
    unresolved_batches = sorted(
        str(batch_id)
        for batch_id, batch in manifest.get("batches", {}).items()
        if isinstance(batch, dict) and batch.get("status") in {"needs_resolution", "conflict"}
    )
    unresolved_trains: list[str] = []
    train_batch_ids: set[str] = set()
    for key, train in (manifest.get("mergeTrains") or {}).items():
        if not isinstance(train, dict) or train.get("status") not in {"candidate_conflicted", "needs_resolution"}:
            continue
        unresolved_trains.append(str(key))
        train_batch_ids.update(
            str(batch_id)
            for batch_id in train.get("batchIds", [])
            if isinstance(batch_id, str) and batch_id.strip()
        )
    return unresolved_batches, sorted(unresolved_trains), set(unresolved_batches) | train_batch_ids


def _is_global_resolution_batch(batch: dict[str, Any]) -> bool:
    """A promoted source whose Plan write failed is not an isolatable conflict."""
    resolution = batch.get("resolution")
    return isinstance(resolution, dict) and resolution.get("kind") == "plan_state_update"


def _is_global_resolution_train(train: dict[str, Any]) -> bool:
    """Do not schedule through a train that may already have promoted source."""
    return bool(train.get("planWriterErrors")) or bool(train.get("promotedSha"))


def _scoped_merge_train_keys(
    manifest: dict[str, Any],
    keys: list[str],
    workspace_refs: list[str] | None,
) -> list[str]:
    if not workspace_refs:
        return list(keys)
    allowed = {str(ref).strip() for ref in workspace_refs if str(ref).strip()}
    return [
        key
        for key in keys
        if str(((manifest.get("mergeTrains") or {}).get(key, {}) or {}).get("repositoryRef") or "") in allowed
    ]


def _clear_retry_lease_locked(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    batch: dict[str, Any],
) -> bool:
    """Clear lease authority while the caller owns the run lock.

    ``retry_pending`` means that the worker which held this Batch's lease has
    already yielded control.  Keeping either the lease file or the manifest
    lease metadata would make a resumed ``ready_to_candidate`` delivery
    permanently non-mergeable, or make a resumed ``pending`` delivery reject
    its next worker with ``parallel_batch_lease_held``.  Do this as part of
    the durable state transition rather than depending on a best-effort
    Workflow cleanup prompt.
    """
    path = lease_path(workspace, feature, run_id, batch_id)
    had_lease = path.is_file() or batch.get("lease") is not None
    with FileLock(path.with_suffix(".lock")):
        path.unlink(missing_ok=True)
    batch["lease"] = None
    return had_lease


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run Git using UTF-8 so CJK worktree paths work on Windows."""
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _ensure_git_root(requested: Path, *, allow_bootstrap: bool) -> tuple[Path, bool]:
    """Return a Git root, initializing an explicit code directory when needed."""
    try:
        return resolve_git_root(requested), False
    except RepositorySnapshotError:
        if not allow_bootstrap:
            raise ValueError(f"parallel_code_workspace_git_repository_required:{requested}")
        if not requested.is_dir():
            raise
        init = _git(requested, "init", "-b", "main")
        if init.returncode != 0:
            # Older Git versions do not support `init -b`; retain the same
            # bootstrap behavior with the portable form.
            init = _git(requested, "init")
        if init.returncode != 0:
            raise ValueError(f"parallel_code_workspace_git_init_failed:{init.stderr.strip()}")
        return resolve_git_root(requested), True


def _ensure_runtime_ignores(git_root: Path) -> list[str]:
    """Keep workflow-owned files out of the user's tracked baseline."""
    exclude = git_root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    additions = [rule for rule in _BOOTSTRAP_IGNORE_RULES if rule not in existing.splitlines()]
    if additions:
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        exclude.write_text(existing + prefix + "\n".join(additions) + "\n", encoding="utf-8")
    return additions


def _git_head(git_root: Path) -> str | None:
    result = _git(git_root, "rev-parse", "--verify", "HEAD")
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _unstage_platform_runtime(git_root: Path, *, has_head: bool) -> None:
    """Keep already-staged platform artifacts out of an automatic baseline."""
    if has_head:
        staged = _git(git_root, "diff", "--cached", "--quiet", "--", PLATFORM_RUNTIME_DIRECTORY)
        if staged.returncode == 0:
            return
        if staged.returncode != 1:
            raise ValueError(f"parallel_code_workspace_runtime_stage_check_failed:{staged.stderr.strip()}")
        # ``git restore`` arrived after the Git version shipped by several
        # supported Windows images.  ``reset HEAD --`` has the same staging
        # effect here and is supported by Git 2.20.
        command = ["reset", "HEAD", "--", PLATFORM_RUNTIME_DIRECTORY]
    else:
        command = ["rm", "-r", "--cached", "--ignore-unmatch", "--", PLATFORM_RUNTIME_DIRECTORY]
    result = _git(git_root, *command)
    if result.returncode != 0:
        raise ValueError(f"parallel_code_workspace_runtime_unstage_failed:{result.stderr.strip()}")


def _bootstrap_repository(
    git_root: Path,
    feature: str,
    *,
    task_card_id: str,
    initialized: bool,
    ignore_additions: list[str],
    allow_bootstrap: bool,
) -> dict[str, Any]:
    """Optionally create an explicit baseline commit for an unusable source tree."""
    before_head = _git_head(git_root)
    status = git_status_porcelain(git_root)
    if status.returncode != 0:
        raise ValueError("parallel_code_workspace_status_unavailable")
    dirty = bool(status.stdout.strip())
    if before_head and not dirty:
        return {
            "headSha": before_head,
            "performed": bool(initialized or ignore_additions),
            "initialized": initialized,
            "reason": "git_initialized" if initialized else None,
            "commitSha": before_head,
        }

    if not allow_bootstrap:
        reason = "unborn_head" if before_head is None else "dirty_worktree"
        raise ValueError(f"parallel_code_workspace_bootstrap_required:{reason}:{git_root}")

    _unstage_platform_runtime(git_root, has_head=before_head is not None)
    add = _git(git_root, "add", "-A", "--", ".", f":(exclude){PLATFORM_RUNTIME_DIRECTORY}**")
    if add.returncode != 0:
        raise ValueError(f"parallel_code_workspace_bootstrap_stage_failed:{add.stderr.strip()}")
    reason = "unborn_head" if before_head is None else "dirty_worktree"
    message = build_commit_message(task_card_id, f"初始化 {feature} 工作流基线")
    commit = _git(
        git_root,
        "-c",
        "user.name=AutoDevOps",
        "-c",
        "user.email=autodev@localhost",
        "commit",
        "--allow-empty",
        "-m",
        message,
    )
    if commit.returncode != 0:
        raise ValueError(f"parallel_code_workspace_bootstrap_commit_failed:{commit.stderr.strip()}")
    head = _git_head(git_root)
    if head is None:
        raise ValueError("parallel_code_workspace_bootstrap_head_unavailable")
    return {
        "headSha": head,
        "performed": True,
        "initialized": initialized,
        "reason": reason,
        "commitSha": head,
    }


def resolve_repository_bindings(
    bundle: Any,
    values: list[str] | None,
    *,
    feature: str = "feature",
    allow_bootstrap: bool = False,
    task_card_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve `workspaceRef=/path` arguments into immutable repository bindings."""
    normalized_task_card_id = normalize_task_card_id(task_card_id)
    refs = sorted({
        str(item.get("workspaceRef"))
        for batch in bundle.batches.values()
        for item in batch.get("tasks", [])
        if isinstance(item, dict) and isinstance(item.get("workspaceRef"), str)
    })
    raw = values or []
    parsed: dict[str, Path] = {}
    bare: list[Path] = []
    for value in raw:
        key, separator, path = value.partition("=")
        if separator:
            if not key or not path or key in parsed:
                raise ValueError(f"parallel_code_workspace_invalid:{value}")
            parsed[key] = Path(path).expanduser().resolve()
        elif value:
            bare.append(Path(value).expanduser().resolve())
    if bare:
        if len(bare) != 1 or len(refs) != 1:
            raise ValueError("parallel_code_workspace_mapping_required")
        parsed[refs[0]] = bare[0]
    missing = sorted(set(refs) - set(parsed))
    unexpected = sorted(set(parsed) - set(refs))
    if missing:
        raise ValueError("parallel_code_workspace_missing:" + ",".join(missing))
    if unexpected:
        raise ValueError("parallel_code_workspace_unknown:" + ",".join(unexpected))
    bindings: dict[str, dict[str, Any]] = {}
    for ref in refs:
        requested = parsed[ref]
        try:
            git_root, initialized = _ensure_git_root(requested, allow_bootstrap=allow_bootstrap)
        except (RepositorySnapshotError, ValueError) as exc:
            raise ValueError(f"parallel_code_workspace_invalid:{ref}:{exc}") from exc
        ignore_additions = _ensure_runtime_ignores(git_root)
        bootstrap = _bootstrap_repository(
            git_root,
            feature,
            task_card_id=normalized_task_card_id,
            initialized=initialized,
            ignore_additions=ignore_additions,
            allow_bootstrap=allow_bootstrap,
        )
        head = bootstrap["headSha"]
        branch = current_git_branch(git_root)
        bindings[ref] = {
            "workspaceRef": ref,
            "requestedPath": str(requested),
            "gitRoot": str(git_root),
            "baseSha": head,
            # This moves forward only through merges owned by this run.  It
            # is the expected main HEAD for later dependency waves.
            "headSha": head,
            "baseBranch": branch,
            "bootstrap": bootstrap,
            "runtimeIgnoreAdditions": ignore_additions,
        }
    return bindings


def _git_metadata_path(git_root: Path, argument: str) -> Path:
    """Resolve a Git metadata path returned relative to a worktree root."""
    result = _git(git_root, "rev-parse", argument)
    raw = result.stdout.strip()
    if result.returncode != 0 or not raw:
        raise ValueError(f"parallel_batch_worktree_git_metadata_unavailable:{git_root}")
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (git_root / path).resolve()


def assert_batch_worktree_isolated(
    manifest: dict[str, Any],
    batch_id: str,
    worktree_path: Path | str,
) -> None:
    """Require the plugin-owned linked native worktree assigned to a Batch.

    The plugin provisions a linked worktree before starting the Batch agent and
    this guard verifies Git's own worktree metadata rather than trusting a
    filesystem path supplied by the agent.
    """
    batch = manifest.get("batches", {}).get(batch_id)
    if not isinstance(batch, dict):
        raise ValueError(f"parallel_batch_not_found:{batch_id}")
    repository_ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
    binding = manifest.get("repositories", {}).get(repository_ref)
    if not isinstance(binding, dict) or not isinstance(binding.get("gitRoot"), str):
        raise ValueError(f"parallel_repository_binding_missing:{repository_ref}")

    source_root = Path(str(binding["gitRoot"])).expanduser().resolve()
    candidate = Path(worktree_path).expanduser().resolve()
    try:
        candidate_root = resolve_git_root(candidate)
    except RepositorySnapshotError as exc:
        raise ValueError(f"parallel_batch_worktree_not_git:{candidate}") from exc

    details = {
        "batchId": batch_id,
        "repositoryRef": repository_ref,
        "sourceGitRoot": str(source_root),
        "worktreePath": str(candidate),
    }
    if candidate_root == source_root:
        raise ValueError(
            "parallel_batch_worktree_not_isolated:"
            + json.dumps({**details, "reason": "source_checkout"}, ensure_ascii=False, separators=(",", ":"))
        )

    source_common = _git_metadata_path(source_root, "--git-common-dir")
    candidate_common = _git_metadata_path(candidate_root, "--git-common-dir")
    source_git_dir = _git_metadata_path(source_root, "--git-dir")
    candidate_git_dir = _git_metadata_path(candidate_root, "--git-dir")
    if candidate_common != source_common or candidate_git_dir == source_git_dir:
        raise ValueError(
            "parallel_batch_worktree_not_isolated:"
            + json.dumps(
                {
                    **details,
                    "reason": "not_linked_to_source_repository",
                    "sourceGitCommonDir": str(source_common),
                    "worktreeGitCommonDir": str(candidate_common),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    listed = _git(source_root, "worktree", "list", "--porcelain")
    registered = {
        Path(line.removeprefix("worktree ")).resolve()
        for line in listed.stdout.splitlines()
        if line.startswith("worktree ")
    }
    if listed.returncode != 0 or candidate_root not in registered:
        raise ValueError(
            "parallel_batch_worktree_not_isolated:"
            + json.dumps(
                {**details, "reason": "not_registered_with_source_repository"},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )


def validate_plan_for_parallel(workspace: Path, feature: str) -> dict[str, Any]:
    try:
        bundle = load_plan_bundle(feature_dir(workspace, feature))
    except ValueError as exc:
        return {
            "canParallel": False,
            "requiresPlanRepair": True,
            "reason": f"invalid_plan:{exc}",
            "errors": [str(exc)],
        }
    errors = [*parallel_plan_errors(bundle), *validation_ownership_errors(bundle.root, bundle.batches)]
    if errors:
        return {
            "canParallel": False,
            "requiresPlanRepair": True,
            "reason": errors[0],
            "errors": errors,
        }
    entries = [item for item in bundle.root.get("batches", []) if isinstance(item, dict) and item.get("status") not in {"done", "failed"}]
    if not entries:
        return {"canParallel": False, "reason": "no_pending_batches", "errors": []}
    return {
        "canParallel": True,
        "reason": "parallel_plan_valid" if len(entries) > 1 else "single_batch_workflow_valid",
        "planDigest": plan_digest(bundle),
        "batches": [str(item["id"]) for item in entries],
        "workspaceRefs": sorted({
            str(task.get("workspaceRef"))
            for batch in bundle.batches.values()
            for task in batch.get("tasks", [])
            if isinstance(task, dict) and isinstance(task.get("workspaceRef"), str)
        }),
        "errors": [],
    }


def create_run(
    workspace: Path,
    feature: str,
    *,
    max_parallel: int,
    timeout_seconds: int,
    code_workspaces: list[str] | None = None,
    workflow_workspace: Path | None = None,
    allow_bootstrap: bool = False,
    task_card_id: str | None = None,
) -> dict[str, Any]:
    # Kept as an ignored Python API compatibility parameter for older callers.
    # The CLI and fixed Workflow no longer expose it: platform workspace identity
    # must not constrain one or more business repositories.
    _ = workflow_workspace
    normalized_task_card_id = normalize_task_card_id(task_card_id)
    verdict = validate_plan_for_parallel(workspace, feature)
    if not verdict["canParallel"]:
        raise ValueError(f"parallel_not_available:{verdict['reason']}")
    # The active-run check and manifest creation must be one critical section.
    # Child repository Workflows are launched concurrently and otherwise can
    # both observe an empty run directory and create divergent DAG runs.
    # Reuse the Feature's existing plan lock so an invalid source repository
    # does not create a leftover ``.parallel-runs`` directory just to acquire
    # a scheduler lock.  It also prevents Plan edits racing run creation.
    with FileLock(feature_dir(workspace, feature) / ".plan.lock"):
        if get_active_run(workspace, feature) is not None:
            raise ValueError("parallel_run_already_active")
        bundle = load_plan_bundle(feature_dir(workspace, feature))
        repositories = resolve_repository_bindings(
            bundle,
            code_workspaces,
            feature=feature,
            allow_bootstrap=allow_bootstrap,
            task_card_id=normalized_task_card_id,
        )
        # Load runtime config from workspace
        from hooks.workflow_launcher import _load_runtime_config
        runtime_config = _load_runtime_config(workspace)

        manifest = create_manifest(
            workspace,
            feature,
            max_parallel=max_parallel,
            timeout_seconds=timeout_seconds,
            repositories=repositories,
            runtime_config=runtime_config,  # Pass runtime config
            task_card_id=normalized_task_card_id,
        )
        manifest["isolation"] = {
            "mode": "native_git_worktrees",
            "owner": "plugin",
            "cleanupOwner": "plugin",
            "provisioner": "hooks/worktree_manager.py",
            "workspaceRefs": sorted(repositories),
        }
        manifest["status"] = "running"
        save_manifest(workspace, feature, str(manifest["runId"]), manifest)
        append_event(workspace, feature, str(manifest["runId"]), "run_created", maxParallel=manifest["maxParallel"])
        return schedule(workspace, feature, str(manifest["runId"]))


def _sealed_delivery_error(manifest: dict[str, Any], batch_id: str) -> str | None:
    """Validate a plugin-managed native Worktree before resuming a run."""
    batch = manifest.get("batches", {}).get(batch_id)
    if not isinstance(batch, dict):
        return f"parallel_batch_not_found:{batch_id}"
    commit_sha = batch.get("commitSha")
    if not isinstance(commit_sha, str) or not commit_sha:
        return None
    worktree_path = batch.get("worktreePath")
    branch_name = batch.get("branchName")
    if not isinstance(worktree_path, str) or not worktree_path:
        return f"native_worktree_delivery_missing:{batch_id}:path"
    if not isinstance(branch_name, str) or not branch_name:
        return f"native_worktree_delivery_missing:{batch_id}:branch"
    try:
        assert_batch_worktree_isolated(manifest, batch_id, worktree_path)
    except ValueError:
        return f"native_worktree_delivery_missing:{batch_id}:worktree"
    worktree = Path(worktree_path)
    if current_git_branch(worktree) != branch_name:
        return f"native_worktree_delivery_missing:{batch_id}:branch"
    head = _git(worktree, "rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != commit_sha:
        return f"native_worktree_delivery_commit_mismatch:{batch_id}"
    status = git_status_porcelain(worktree)
    if status.returncode != 0:
        return f"native_worktree_delivery_status_unavailable:{batch_id}"
    if status.stdout.strip():
        return f"native_worktree_delivery_dirty:{batch_id}"
    return None


def _source_repository_errors(manifest: dict[str, Any]) -> list[str]:
    """Return source-worktree violations before an active run is reused.

    Each repository binding freezes the expected HEAD.  It moves forward only
    through ``parallel_merge_train.py`` after exact candidate promotion. Direct commits, resets,
    and shared-checkout changes must stop recovery before another Batch runs
    against a different base.
    """
    errors: list[str] = []
    repositories = manifest.get("repositories", {})
    if not isinstance(repositories, dict) or not repositories:
        return ["parallel_repository_bindings_missing"]
    for ref, repository in sorted(repositories.items()):
        if not isinstance(repository, dict) or not isinstance(repository.get("gitRoot"), str):
            errors.append(f"parallel_repository_binding_invalid:{ref}")
            continue
        root = Path(repository["gitRoot"])
        status = git_status_porcelain(root)
        if status.returncode != 0:
            errors.append(f"parallel_repository_status_unavailable:{ref}")
            continue
        if status.stdout.strip():
            errors.append(f"parallel_repository_dirty:{ref}")
            continue
        expected_head = repository.get("headSha") or repository.get("baseSha")
        actual_head = _git_head(root)
        if not isinstance(expected_head, str) or not expected_head.strip() or not actual_head:
            errors.append(f"parallel_repository_head_unavailable:{ref}")
        elif actual_head != expected_head:
            errors.append(
                f"parallel_repository_head_changed:{ref}:expected={expected_head}:actual={actual_head}"
            )
    return errors


def _scoped_batch_ids(manifest: dict[str, Any], batch_ids: list[str], workspace_refs: list[str] | None) -> list[str]:
    if not workspace_refs:
        return list(batch_ids)
    allowed = {str(ref) for ref in workspace_refs if str(ref).strip()}
    return [
        batch_id
        for batch_id in batch_ids
        if str(
            (manifest.get("batches", {}).get(batch_id, {}) or {}).get("workspaceRef")
            or (manifest.get("batches", {}).get(batch_id, {}) or {}).get("repositoryRef")
        ) in allowed
    ]


def _stage_recovery_failure_context(
    batch: dict[str, Any],
    *,
    test_log_path: Path,
) -> dict[str, Any] | None:
    """Return the saved review/UTest finding that caused an implement recovery."""
    states = batch.get("stageStates") if isinstance(batch.get("stageStates"), dict) else {}
    for stage in delivery_stage_names(batch):
        state = states.get(stage)
        failure = state.get("failure") if isinstance(state, dict) else None
        if not isinstance(failure, dict) or failure.get("nextStage") != "implement":
            continue
        message = failure.get("message")
        if not isinstance(message, str) or not message.strip():
            continue
        context: dict[str, Any] = {
            "failedStage": stage,
            "failureType": str(failure.get("type") or "implementation"),
            "message": message,
        }
        if stage == "test":
            context["testLogPath"] = str(test_log_path)
        return context
    return None


def schedule(
    workspace: Path,
    feature: str,
    run_id: str,
    workspace_refs: list[str] | None = None,
) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        bundle = load_plan_bundle(feature_dir(workspace, feature))
        current_digest = plan_digest(bundle)
        if current_digest != manifest.get("planDigest"):
            drift = plan_drift_details(manifest.get("planContract"), bundle)
            manifest["status"] = "blocked"
            manifest["planDrift"] = {
                "reason": "parallel_plan_digest_changed",
                "expectedDigest": manifest.get("planDigest"),
                "currentDigest": current_digest,
                **drift,
            }
            save_manifest(workspace, feature, run_id, manifest)
            append_event(
                workspace,
                feature,
                run_id,
                "plan_changed",
                expectedDigest=manifest.get("planDigest"),
                currentDigest=current_digest,
                drift=drift,
            )
            raise ValueError("parallel_plan_digest_changed:" + json.dumps(drift, ensure_ascii=False, sort_keys=True))
        # A retained per-Batch conflict (or one conflicted Merge Train) owns
        # only the deliveries recorded in that retained state.  Keep those
        # deliveries out of every runnable output so a resume cannot silently
        # recreate their candidate, while allowing unrelated DAG branches to
        # continue.  Dependency release remains enforced by `ready_batches`.
        unresolved_batches, unresolved_trains, withheld_batches = _unresolved_state(manifest)
        ready = [batch_id for batch_id in ready_batches(manifest) if batch_id not in withheld_batches]
        # ``resource_groups`` treats ``None`` as "all batches" for callers
        # that intentionally omit a scope.  The scheduler has already built
        # an explicit readiness set, however, and an empty set must remain
        # empty: otherwise a retained conflict can be accidentally re-added
        # as a runnable group.
        groups = resource_groups(manifest, ready) if ready else []
        if workspace_refs:
            known_refs = {
                str(batch.get("workspaceRef") or batch.get("repositoryRef"))
                for batch in manifest.get("batches", {}).values()
                if isinstance(batch, dict)
            }
            requested_refs = {str(ref).strip() for ref in workspace_refs if str(ref).strip()}
            unknown_refs = sorted(requested_refs - known_refs)
            if unknown_refs:
                raise ValueError("parallel_workspace_refs_unknown:" + ",".join(unknown_refs))
        scoped_ready = _scoped_batch_ids(manifest, ready, workspace_refs)
        scoped_groups = [
            _scoped_batch_ids(manifest, group, workspace_refs)
            for group in groups
        ]
        scoped_groups = [group for group in scoped_groups if group]
        mergeable = [batch_id for batch_id in mergeable_batches(manifest) if batch_id not in withheld_batches]
        stage_recovery = [
            batch_id
            for batch_id in stage_recovery_batches(manifest)
            if batch_id not in withheld_batches
            and (manifest.get("batches", {}).get(batch_id, {}) or {}).get("lease") is None
        ]
        scoped_mergeable = _scoped_batch_ids(manifest, mergeable, workspace_refs)
        scoped_stage_recovery = _scoped_batch_ids(manifest, stage_recovery, workspace_refs)
        scoped_unresolved_batches = _scoped_batch_ids(manifest, unresolved_batches, workspace_refs)
        scoped_unresolved_trains = _scoped_merge_train_keys(manifest, unresolved_trains, workspace_refs)
        max_parallel = int(manifest.get("maxParallel", 1))
        selected: list[list[str]] = []
        allowed_refs = {str(ref) for ref in workspace_refs or []}
        active = sum(
            1
            for item in manifest.get("batches", {}).values()
            if isinstance(item, dict) and item.get("status") in {"leased", "running"}
        )
        active_outside_scope = sum(
            1
            for item in manifest.get("batches", {}).values()
            if isinstance(item, dict)
            and item.get("status") in {"leased", "running"}
            and str(item.get("workspaceRef") or item.get("repositoryRef")) not in allowed_refs
        ) if workspace_refs else 0
        slots = max(0, max_parallel - active)
        active_batch_ids = sorted(
            str(batch_id)
            for batch_id, item in manifest.get("batches", {}).items()
            if isinstance(item, dict) and item.get("status") in {"leased", "running"}
        )
        runnable = select_runnable_batches(
            manifest,
            _scoped_batch_ids(manifest, ready, workspace_refs),
            active_batch_ids,
            slots,
        )
        if runnable:
            # Keep the nested response shape for fixed-workflow compatibility;
            # it is now one dynamic dispatch batch, not a completion barrier.
            selected.append(runnable)
        manifest["scheduledAt"] = manifest.get("updatedAt")
        save_manifest(workspace, feature, run_id, manifest)
        return {
            "runId": run_id,
            "status": manifest.get("status"),
            "readyBatches": scoped_ready,
            "allReadyBatches": ready,
            "mergeableBatches": scoped_mergeable,
            "allMergeableBatches": mergeable,
            "stageRecoveryBatches": [
                {
                    "batchId": batch_id,
                    "worktreePath": batch.get("worktreePath"),
                    "branchName": batch.get("branchName"),
                    "commitSha": batch.get("commitSha"),
                    "nextStage": "implement" if failure_context is not None else next_stage,
                    **(
                        {"failureContext": failure_context}
                        if failure_context is not None
                        else {}
                    ),
                }
                for batch_id in scoped_stage_recovery
                for batch in [manifest["batches"][batch_id]]
                for next_stage in [
                    next(
                        (
                            stage
                            for stage in delivery_stage_names(batch)
                            if not isinstance((batch.get("stageStates") or {}).get(stage), dict)
                            or (batch.get("stageStates") or {}).get(stage, {}).get("status") not in {"passed", "skipped"}
                        ),
                        None,
                    )
                ]
                for failure_context in [
                    _stage_recovery_failure_context(
                        batch,
                        test_log_path=feature_dir(workspace, feature) / "test-output.log",
                    )
                ]
            ],
            "allStageRecoveryBatches": stage_recovery,
            "blockedBatches": sorted(
                str(batch_id)
                for batch_id, batch in manifest.get("batches", {}).items()
                if isinstance(batch, dict) and batch.get("status") == "blocked"
            ),
            "retryPendingBatches": sorted(
                str(batch_id)
                for batch_id, batch in manifest.get("batches", {}).items()
                if isinstance(batch, dict) and batch.get("status") == "retry_pending"
            ),
            "unresolvedBatches": scoped_unresolved_batches,
            "allUnresolvedBatches": unresolved_batches,
            "unresolvedMergeTrains": scoped_unresolved_trains,
            "allUnresolvedMergeTrains": unresolved_trains,
            "recoveryRequired": bool(scoped_unresolved_batches or scoped_unresolved_trains),
            "parallelGroups": scoped_groups,
            "allParallelGroups": groups,
            "scheduledGroups": selected,
            "workspaceRefs": sorted(set(workspace_refs or [])),
            "waitingForRepositories": bool(workspace_refs and not selected and not scoped_mergeable and (
                groups or mergeable or active_outside_scope
            )),
            "maxParallel": max_parallel,
            "activeWorkers": active,
            "batchWorkspaces": {
                batch_id: {
                    "workspaceRef": item.get("workspaceRef"),
                    "componentRoots": item.get("componentRoots", []),
                    "executionStage": item.get("executionStage", "parallel"),
                    "qualityGateRequired": item.get("qualityGateRequired") is True,
                    "requestedPath": (manifest.get("repositories", {}).get(str(item.get("repositoryRef")), {}) or {}).get("requestedPath"),
                    "worktreePath": item.get("worktreePath"),
                    "branchName": item.get("branchName"),
                }
                for batch_id, item in manifest.get("batches", {}).items()
                if isinstance(item, dict)
            },
            "batchTaskIds": {
                batch_id: list(item.get("taskIds", []))
                for batch_id, item in manifest.get("batches", {}).items()
                if isinstance(item, dict)
            },
            "isolation": manifest.get("isolation"),
        }


def mark_batch(workspace: Path, feature: str, run_id: str, batch_id: str, status: str, **details: Any) -> dict[str, Any]:
    # ``merged`` is written only by the Merge Train after exact candidate promotion and
    # plan-state update. Worker-facing status changes may never unlock deps.
    if status == "merged":
        raise ValueError(f"parallel_batch_merge_owner_required:{batch_id}")
    allowed = {"pending", "leased", "running", "compile_failed", "sealed", "ready_to_candidate", "needs_resolution", "retry_pending", "failed", "blocked", "cancelled"}
    if status not in allowed:
        raise ValueError(f"parallel_batch_status_invalid:{status}")
    retry_lease_cleared = False
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = manifest.get("batches", {}).get(batch_id)
        if not isinstance(batch, dict):
            raise ValueError(f"parallel_batch_not_found:{batch_id}")
        previous = batch.get("status")
        terminal = {"merged", "failed", "blocked", "cancelled"}
        # `failed` was emitted by older workers before retry_pending existed.
        # Permit it (and a retry-exhausted block) to re-enter the controlled
        # recovery path; only a real merged/cancelled delivery stays immutable.
        retrying_terminal = previous in {"failed", "blocked"} and status == "retry_pending"
        if previous in terminal and previous != status and not retrying_terminal:
            raise ValueError(f"parallel_batch_terminal:{batch_id}:{previous}")
        if status in {"running", "sealed", "ready_to_candidate"}:
            candidate = details.get("worktreePath") or batch.get("worktreePath")
            if not isinstance(candidate, str) or not candidate.strip():
                raise ValueError(f"parallel_batch_worktree_path_required:{batch_id}")
            assert_batch_worktree_isolated(manifest, batch_id, candidate)
            expected_branch = details.get("branchName") or batch.get("branchName")
            if not isinstance(expected_branch, str) or not expected_branch.strip():
                raise ValueError(f"parallel_batch_worktree_branch_required:{batch_id}")
            if current_git_branch(Path(candidate)) != expected_branch:
                raise ValueError(f"parallel_batch_worktree_branch_mismatch:{batch_id}")
        for key in ("worktreePath", "branchName", "commitSha", "compileStatus", "mergeCommitSha", "error"):
            if key in details:
                batch[key] = details[key]
        if status == "retry_pending":
            retry_lease_cleared = _mark_retry_pending_locked(
                workspace,
                feature,
                run_id,
                manifest,
                batch_id,
                batch,
                error=str(details.get("error") or batch.get("error") or "batch_execution_failed"),
                previous_status=str(previous or ""),
            )
        else:
            batch["status"] = status
        if status == "running" and not batch.get("startedAt"):
            batch["startedAt"] = details.get("startedAt") or manifest.get("updatedAt")
        if status in terminal:
            batch["completedAt"] = details.get("completedAt") or manifest.get("updatedAt")
        statuses = [item.get("status") for item in manifest.get("batches", {}).values() if isinstance(item, dict)]
        if statuses and all(item == "merged" for item in statuses):
            manifest["status"] = "succeeded"
        elif status == "needs_resolution":
            manifest["status"] = "needs_resolution"
        elif status in {"failed", "blocked"}:
            manifest["status"] = "blocked"
        save_manifest(workspace, feature, run_id, manifest)
        if retry_lease_cleared:
            append_event(
                workspace,
                feature,
                run_id,
                "lease_reclaimed",
                batchId=batch_id,
                force=True,
                reason="retry_pending",
            )
        append_event(workspace, feature, run_id, "batch_status_changed", batchId=batch_id, previous=previous, status=status)
        return manifest


def resume_run(
    workspace: Path,
    feature: str,
    run_id: str,
    workspace_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Idempotently resume only batches that do not already own a result."""
    stale_recovered: list[dict[str, str]] = []
    # A Git merge may have committed successfully immediately before the Plan
    # writer failed. Recover that metadata before evaluating the normal
    # needs-resolution gate, so an interrupted workflow can resume unattended.
    initial = load_manifest(workspace, feature, run_id)
    recovery_batches = [
        str(batch_id)
        for batch_id, batch in initial.get("batches", {}).items()
        if isinstance(batch, dict)
        and batch.get("status") == "needs_resolution"
        and isinstance(batch.get("resolution"), dict)
        and batch["resolution"].get("kind") == "plan_state_update"
    ]
    if recovery_batches:
        from hooks.batch_merger import recover_plan_state_after_merge

        recovery_errors: list[str] = []
        for batch_id in recovery_batches:
            recovered = recover_plan_state_after_merge(workspace, feature, run_id, batch_id)
            if not recovered.get("success"):
                recovery_errors.append(str(recovered.get("error") or f"parallel_plan_recovery_failed:{batch_id}"))
        if recovery_errors:
            return {
                "runId": run_id,
                "status": "needs_resolution",
                "scheduledGroups": [],
                "mergeableBatches": mergeable_batches(load_manifest(workspace, feature, run_id)),
                "recoveryRequired": True,
                "errors": recovery_errors,
            }
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if manifest.get("status") in {"cleaned", "rolled_back"}:
            return {"runId": run_id, "status": manifest.get("status"), "skipped": "terminal_run"}
        # Older runs (and an interrupted Workflow cleanup) can have reached
        # ``retry_pending`` before their lease handoff completed.  Repair that
        # invariant before deciding whether the preserved delivery is sealed,
        # mergeable, or ready to be scheduled again.
        retry_leases_cleared: list[str] = []
        for batch_id, batch in manifest.get("batches", {}).items():
            if not isinstance(batch, dict) or batch.get("status") != "retry_pending":
                continue
            if _clear_retry_lease_locked(workspace, feature, run_id, str(batch_id), batch):
                retry_leases_cleared.append(str(batch_id))
        # Persist lease metadata removal before running any later recovery
        # checks.  The lease file and manifest are separate atomic files, so a
        # process crash can never make their unlink/write one filesystem
        # transaction; this immediate durable boundary minimizes that window,
        # while the same loop reconciles the remaining one-sided state on the
        # next resume.
        if retry_leases_cleared:
            save_manifest(workspace, feature, run_id, manifest)
        for batch_id in retry_leases_cleared:
            append_event(
                workspace,
                feature,
                run_id,
                "lease_reclaimed",
                batchId=batch_id,
                force=True,
                reason="retry_resume_repair",
            )
        # This is deliberately scheduler-owned rather than a best-effort
        # Workflow cleanup.  If a model/worker dies before it can write its
        # own retry marker, its expired execution authority cannot keep the
        # whole run's concurrency slots occupied forever.
        stale_recovered = _recover_stale_active_batches_locked(
            workspace,
            feature,
            run_id,
            manifest,
        )
        active_stale_recovered = len(stale_recovered)
        for item in stale_recovered:
            append_event(
                workspace,
                feature,
                run_id,
                "batch_stale_lease_recovered",
                batchId=item["batchId"],
                reason=item["reason"],
            )
        invalid_deliveries: list[str] = []
        for batch_id, batch in manifest.get("batches", {}).items():
            if not isinstance(batch, dict):
                continue
            if batch.get("status") == "merged" and not (
                isinstance(batch.get("mergeCommitSha"), str) and batch["mergeCommitSha"].strip()
            ):
                delivery_error = f"parallel_batch_merge_evidence_required:{batch_id}"
                batch.update({"status": "blocked", "error": delivery_error})
                invalid_deliveries.append(delivery_error)
        invalid_deliveries.extend(_source_repository_errors(manifest))
        for batch_id, batch in manifest.get("batches", {}).items():
            if (
                isinstance(batch, dict)
                and batch.get("status") == "needs_resolution"
                and _is_global_resolution_batch(batch)
            ):
                invalid_deliveries.append(f"parallel_plan_state_recovery_required:{batch_id}")
        for train_key, train in (manifest.get("mergeTrains") or {}).items():
            if (
                isinstance(train, dict)
                and train.get("status") in {"candidate_conflicted", "needs_resolution"}
                and _is_global_resolution_train(train)
            ):
                invalid_deliveries.append(f"parallel_merge_train_plan_recovery_required:{train_key}")
        if invalid_deliveries:
            manifest["status"] = "blocked"
            save_manifest(workspace, feature, run_id, manifest)
            append_event(
                workspace,
                feature,
                run_id,
                "run_resume_blocked_integrity",
                errors=invalid_deliveries,
            )
            return {
                "runId": run_id,
                "status": "blocked",
                "scheduledGroups": [],
                "mergeableBatches": mergeable_batches(manifest),
                "recoveryRequired": True,
                "errors": invalid_deliveries,
            }
        if manifest.get("status") in {"succeeded", "succeeded_with_issues", "verifying"}:
            return {"runId": run_id, "status": manifest.get("status"), "skipped": "terminal_run"}
        # Ordinary delivery and candidate conflicts stay durable in their
        # own records.  They are intentionally not a global resume gate:
        # `schedule` filters those exact Batch IDs while independent work
        # continues.  Plan-state recovery and shared-source integrity cases
        # were handled above as genuine global blockers.
        invalid_deliveries = []
        for batch_id, batch in manifest.get("batches", {}).items():
            if not isinstance(batch, dict):
                continue
            if batch.get("mergeCommitSha"):
                batch["status"] = "merged"
                continue
            # Keep task-scoped terminal/recovery states intact.  In
            # particular, a retry-exhausted Batch with a retained commit must
            # not be silently resurrected as `sealed` on the next resume.
            # Valid active leases are also left to their actual worker.
            if batch.get("status") in {
                "retry_pending",
                "blocked",
                "failed",
                "cancelled",
                "needs_resolution",
                "conflict",
                "leased",
                "running",
            }:
                continue
            # A delivery that has already completed every stage remains a
            # Merge Train candidate across an interrupted Workflow. Validate
            # its immutable delivery before retaining that state.
            if batch.get("status") == "ready_to_candidate":
                # A candidate should have released its worker authority before
                # it becomes mergeable.  Recover a stale residual lease before
                # delivery validation so a crashed handoff cannot strand this
                # Batch outside both normal work and Merge Train recovery.
                # Check the lease file as well as the manifest metadata: an
                # interrupted prior write may have persisted only one side.
                if batch.get("lease") is not None or lease_path(workspace, feature, run_id, str(batch_id)).is_file():
                    reason = _lease_staleness_reason_locked(
                        workspace,
                        feature,
                        run_id,
                        str(batch_id),
                        batch,
                        now=time.time(),
                        timeout_seconds=max(1, int(manifest.get("timeoutPerBatch", 3600))),
                    )
                    if reason is not None:
                        _mark_retry_pending_locked(
                            workspace,
                            feature,
                            run_id,
                            manifest,
                            str(batch_id),
                            batch,
                            error=f"parallel_batch_recovery:{reason}",
                            previous_status="ready_to_candidate",
                        )
                        stale_recovered.append({"batchId": str(batch_id), "reason": reason})
                        continue
                delivery_error = _sealed_delivery_error(manifest, str(batch_id))
                if delivery_error:
                    batch.update({"status": "blocked", "error": delivery_error})
                    invalid_deliveries.append(delivery_error)
                continue
            if batch.get("commitSha"):
                delivery_error = _sealed_delivery_error(manifest, str(batch_id))
                if delivery_error:
                    batch.update({"status": "blocked", "error": delivery_error})
                    invalid_deliveries.append(delivery_error)
                else:
                    batch["status"] = "sealed"
            elif batch.get("status") == "sealed":
                # A compile result may enter sealed before `seal`.  It
                # must not be resumed as a merge candidate without a delivery
                # SHA, otherwise the workflow reports an empty successful
                # merge and leaves downstream dependencies blocked forever.
                delivery_error = f"parallel_batch_seal_required:{batch_id}"
                batch.update({"status": "blocked", "error": delivery_error})
                invalid_deliveries.append(delivery_error)
        if invalid_deliveries:
            manifest["status"] = "blocked"
            save_manifest(workspace, feature, run_id, manifest)
            append_event(
                workspace,
                feature,
                run_id,
                "run_resume_blocked_missing_delivery",
                errors=invalid_deliveries,
            )
            return {
                "runId": run_id,
                "status": "blocked",
                "scheduledGroups": [],
                "mergeableBatches": mergeable_batches(manifest),
                "recoveryRequired": True,
                "errors": invalid_deliveries,
            }
        retry_resumed: list[str] = []
        retry_exhausted: list[str] = []
        for batch_id, batch in manifest.get("batches", {}).items():
            if not isinstance(batch, dict) or batch.get("status") != "retry_pending":
                continue
            recovery = batch.get("recovery") if isinstance(batch.get("recovery"), dict) else {}
            retry_attempts = int(recovery.get("retryAttempts", 0))
            if retry_attempts >= MAX_AUTOMATIC_BATCH_RECOVERY_ATTEMPTS:
                batch["status"] = "blocked"
                batch["activeStage"] = None
                batch["recovery"] = {
                    **recovery,
                    "status": "retry_exhausted",
                    "maxAutomaticAttempts": MAX_AUTOMATIC_BATCH_RECOVERY_ATTEMPTS,
                }
                retry_exhausted.append(str(batch_id))
                continue
            # A Merge Train retry must retain its ready-to-candidate state;
            # otherwise its completed stage evidence would be stranded.  A
            # sealed draft goes through per-Batch stage recovery, while an
            # implementation failure before any seal restarts as normal
            # pending work in the same native Worktree.
            batch["status"] = (
                "ready_to_candidate"
                if recovery.get("resumeStatus") == "ready_to_candidate"
                else "sealed"
                if batch.get("commitSha")
                else "pending"
            )
            batch["recovery"] = {
                **recovery,
                "status": "rescheduled",
                "rescheduledAt": manifest.get("updatedAt"),
            }
            retry_resumed.append(str(batch_id))
        manifest["status"] = "running"
        save_manifest(workspace, feature, run_id, manifest)
        for item in stale_recovered[active_stale_recovered:]:
            append_event(
                workspace,
                feature,
                run_id,
                "batch_stale_lease_recovered",
                batchId=item["batchId"],
                reason=item["reason"],
            )
        append_event(workspace, feature, run_id, "run_resumed")
    for batch_id in retry_resumed:
        append_event(workspace, feature, run_id, "batch_retry_rescheduled", batchId=batch_id)
    for batch_id in retry_exhausted:
        append_event(workspace, feature, run_id, "batch_retry_exhausted", batchId=batch_id)
    result = schedule(workspace, feature, run_id, workspace_refs=workspace_refs)
    result["reclaimedStaleBatches"] = stale_recovered
    result["rescheduledRetryBatches"] = retry_resumed
    result["retryExhaustedBatches"] = retry_exhausted
    return result


def manual_resume_run(
    workspace: Path,
    feature: str,
    run_id: str,
    workspace_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Explicitly re-admit retryable Batches after an operator intervention.

    Automatic retries deliberately stop after a small bounded budget.  A user
    asking the Code workflow to continue is a separate admission decision, not
    another automatic retry: retain the failed Worktree and evidence, reset the
    automatic counter, then let ``resume_run`` perform its normal integrity
    checks and scheduling.  This never revives merge conflicts, cancellation,
    or generic blocked states because those need a more specific repair path.
    """
    manually_queued: list[str] = []
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if manifest.get("status") in {"cleaned", "rolled_back"}:
            return {
                "runId": run_id,
                "status": manifest.get("status"),
                "manualRetryBatches": [],
                "skipped": "terminal_run",
            }
        for raw_batch_id, batch in manifest.get("batches", {}).items():
            if not isinstance(batch, dict):
                continue
            batch_id = str(raw_batch_id)
            recovery = batch.get("recovery") if isinstance(batch.get("recovery"), dict) else {}
            retryable = (
                batch.get("status") in {"retry_pending", "failed"}
                or (
                    batch.get("status") == "blocked"
                    and recovery.get("status") == "retry_exhausted"
                )
            )
            if not retryable:
                continue
            batch["recovery"] = {
                **recovery,
                "retryAttempts": 0,
                "manualRetryAttempts": int(recovery.get("manualRetryAttempts", 0)) + 1,
                "lastManualRetryAt": manifest.get("updatedAt"),
                "status": "manual_retry_queued",
            }
            _mark_retry_pending_locked(
                workspace,
                feature,
                run_id,
                manifest,
                batch_id,
                batch,
                error=str(batch.get("error") or recovery.get("lastError") or "operator_requested_retry"),
                previous_status=str(batch.get("status") or ""),
            )
            manually_queued.append(batch_id)
        if manually_queued:
            manifest["status"] = "running"
            save_manifest(workspace, feature, run_id, manifest)
    for batch_id in manually_queued:
        append_event(workspace, feature, run_id, "batch_manual_retry_queued", batchId=batch_id)
    result = resume_run(workspace, feature, run_id, workspace_refs=workspace_refs)
    result["manualRetryBatches"] = manually_queued
    return result


def ensure_run(
    workspace: Path,
    feature: str,
    *,
    max_parallel: int,
    timeout_seconds: int,
    code_workspaces: list[str] | None = None,
    allow_bootstrap: bool = False,
    workspace_refs: list[str] | None = None,
    task_card_id: str | None = None,
) -> dict[str, Any]:
    """Create one scheduler run or safely resume the existing durable run."""
    active_run_id = get_active_run(workspace, feature)
    if active_run_id is None:
        try:
            result = create_run(
                workspace,
                feature,
                max_parallel=max_parallel,
                timeout_seconds=timeout_seconds,
                code_workspaces=code_workspaces,
                allow_bootstrap=allow_bootstrap,
                task_card_id=task_card_id,
            )
            if workspace_refs:
                result = schedule(workspace, feature, str(result["runId"]), workspace_refs=workspace_refs)
            result["reused"] = False
            return result
        except ValueError as exc:
            # Another concurrent ensure may have created the durable run after
            # the initial active-run read. Re-read it and take the normal
            # idempotent reuse path instead of creating a second run.
            if str(exc) != "parallel_run_already_active":
                raise
            active_run_id = get_active_run(workspace, feature)
            if active_run_id is None:
                raise
    # Resume is also the scheduler-owned recovery boundary for retained
    # per-Batch conflicts.  It preserves their worktrees and excludes their
    # Batches from output, but must still release independent DAG branches.
    # `resume_run` retains the stricter global plan/source-integrity gates.
    result = resume_run(workspace, feature, active_run_id, workspace_refs=workspace_refs)
    result["reused"] = True
    return result


def _emit(ok: bool, **payload: Any) -> int:
    print(json.dumps({"ok": ok, **payload}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Schedule parallel Code batch runs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "create", "ensure", "status", "resume", "manual-resume", "list"):
        item = subparsers.add_parser(name)
        item.add_argument("--workspace")
        item.add_argument("--feature", required=True)
        if name in {"status", "resume", "manual-resume"}:
            item.add_argument("--run-id", required=True)
        if name in {"create", "ensure"}:
            item.add_argument("--max-parallel", type=int, default=4)
            item.add_argument("--timeout-seconds", type=int, default=3600)
            item.add_argument("--code-workspace", action="append", required=True, help="workspaceRef=/path; single-ref runs may pass /path")
            item.add_argument("--allow-bootstrap", action="store_true", help="explicitly allow Git initialization or a baseline commit for a dirty source repository")
            item.add_argument("--task-card-id", required=True, help="task card selected before the workflow starts")
        if name in {"status", "resume", "manual-resume", "ensure"}:
            item.add_argument("--workspace-ref", action="append", dest="workspace_refs", help="only schedule batches for these workspaceRef values")
    mark = subparsers.add_parser("mark-batch")
    mark.add_argument("--workspace")
    mark.add_argument("--feature", required=True)
    mark.add_argument("--run-id", required=True)
    mark.add_argument("--batch-id", required=True)
    mark.add_argument("--status", required=True)
    mark.add_argument("--commit-sha")
    mark.add_argument("--merge-commit-sha")
    mark.add_argument("--worktree-path")
    mark.add_argument("--branch-name")
    mark.add_argument("--compile-status")
    mark.add_argument("--error")
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        if args.command == "validate":
            return _emit(True, **validate_plan_for_parallel(workspace, feature))
        if args.command == "create":
            return _emit(
                True,
                **create_run(
                    workspace,
                    feature,
                    max_parallel=args.max_parallel,
                    timeout_seconds=args.timeout_seconds,
                    code_workspaces=args.code_workspace,
                    allow_bootstrap=args.allow_bootstrap,
                    task_card_id=args.task_card_id,
                ),
            )
        if args.command == "ensure":
            return _emit(
                True,
                **ensure_run(
                    workspace,
                    feature,
                    max_parallel=args.max_parallel,
                    timeout_seconds=args.timeout_seconds,
                    code_workspaces=args.code_workspace,
                    allow_bootstrap=args.allow_bootstrap,
                    workspace_refs=args.workspace_refs,
                    task_card_id=args.task_card_id,
                ),
            )
        if args.command == "status":
            return _emit(True, manifest=load_manifest(workspace, feature, args.run_id), **schedule(workspace, feature, args.run_id, workspace_refs=args.workspace_refs))
        if args.command == "resume":
            return _emit(True, **resume_run(workspace, feature, args.run_id, workspace_refs=args.workspace_refs))
        if args.command == "manual-resume":
            return _emit(True, **manual_resume_run(workspace, feature, args.run_id, workspace_refs=args.workspace_refs))
        if args.command == "list":
            return _emit(True, runs=list_runs(workspace, feature))
        details = {key: value for key, value in vars(args).items() if key in {"commit_sha", "merge_commit_sha", "worktree_path", "branch_name", "compile_status", "error"} and value is not None}
        detail_names = {
            "commit_sha": "commitSha",
            "merge_commit_sha": "mergeCommitSha",
            "worktree_path": "worktreePath",
            "branch_name": "branchName",
            "compile_status": "compileStatus",
            "error": "error",
        }
        details = {detail_names[key]: value for key, value in details.items()}
        return _emit(True, manifest=mark_batch(workspace, feature, args.run_id, args.batch_id, args.status, **details))
    except (ValueError, OSError) as exc:
        return _emit(False, error=str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
