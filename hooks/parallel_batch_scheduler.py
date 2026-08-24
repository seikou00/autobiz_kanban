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
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import feature_dir, resolve_feature, resolve_workspace
from hooks.parallel_runtime import (
    append_event,
    create_manifest,
    get_active_run,
    list_runs,
    load_manifest,
    parallel_plan_errors,
    plan_drift_details,
    plan_digest,
    mergeable_batches,
    ready_batches,
    resource_groups,
    run_lock,
    save_manifest,
)
from hooks.plan_json import load_plan_bundle
from hooks.repository_snapshot import RepositorySnapshotError, resolve_git_root
from hooks.worktree_manager import create_worktree


_BOOTSTRAP_IGNORE_RULES = (
    ".worktrees/",
    ".cmbdevclaw/large_tool_results/",
    ".autobizdevops/features/*/.parallel-runs/",
)


def _ensure_git_root(requested: Path) -> tuple[Path, bool]:
    """Return a Git root, initializing an explicit code directory when needed."""
    try:
        return resolve_git_root(requested), False
    except RepositorySnapshotError:
        if not requested.is_dir():
            raise
        init = subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=requested,
            capture_output=True,
            text=True,
        )
        if init.returncode != 0:
            # Older Git versions do not support `init -b`; retain the same
            # bootstrap behavior with the portable form.
            init = subprocess.run(
                ["git", "init"],
                cwd=requested,
                capture_output=True,
                text=True,
            )
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
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=git_root,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _bootstrap_repository(git_root: Path, feature: str, *, initialized: bool, ignore_additions: list[str]) -> dict[str, Any]:
    """Create an internal baseline commit for unborn or dirty repositories.

    The commit is deliberately created by the workflow so users do not need to
    prepare a repository manually.  It contains the current visible working
    tree after runtime paths have been excluded, and leaves the repository
    clean for worktree creation and deterministic merging.
    """
    before_head = _git_head(git_root)
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=git_root,
        capture_output=True,
        text=True,
    )
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

    add = subprocess.run(
        ["git", "add", "-A"],
        cwd=git_root,
        capture_output=True,
        text=True,
    )
    if add.returncode != 0:
        raise ValueError(f"parallel_code_workspace_bootstrap_stage_failed:{add.stderr.strip()}")
    reason = "unborn_head" if before_head is None else "dirty_worktree"
    message = f"autodev: bootstrap {feature} baseline"
    commit = subprocess.run(
        [
            "git",
            "-c",
            "user.name=AutoDevOps",
            "-c",
            "user.email=autodev@localhost",
            "commit",
            "--allow-empty",
            "-m",
            message,
        ],
        cwd=git_root,
        capture_output=True,
        text=True,
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
) -> dict[str, dict[str, Any]]:
    """Resolve `workspaceRef=/path` arguments into immutable repository bindings."""
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
            git_root, initialized = _ensure_git_root(requested)
        except (RepositorySnapshotError, ValueError) as exc:
            raise ValueError(f"parallel_code_workspace_invalid:{ref}:{exc}") from exc
        ignore_additions = _ensure_runtime_ignores(git_root)
        bootstrap = _bootstrap_repository(
            git_root,
            feature,
            initialized=initialized,
            ignore_additions=ignore_additions,
        )
        head = bootstrap["headSha"]
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=git_root, capture_output=True, text=True)
        bindings[ref] = {
            "workspaceRef": ref,
            "requestedPath": str(requested),
            "gitRoot": str(git_root),
            "baseSha": head,
            # This moves forward only through merges owned by this run.  It
            # is the expected main HEAD for later dependency waves.
            "headSha": head,
            "baseBranch": branch.stdout.strip() or None,
            "bootstrap": bootstrap,
            "runtimeIgnoreAdditions": ignore_additions,
        }
    return bindings


def _git_metadata_path(git_root: Path, argument: str) -> Path:
    """Resolve a Git metadata path returned relative to a worktree root."""
    result = subprocess.run(
        ["git", "rev-parse", argument],
        cwd=git_root,
        capture_output=True,
        text=True,
    )
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
    """Require the plugin-owned linked worktree assigned to a Batch.

    The conversation workspace is deliberately independent of business code.
    Every writing Batch must therefore run from a linked worktree created by
    ``worktree_manager.py`` below its own repository's ``.worktrees/`` root.
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

    expected_parent = (source_root / ".worktrees").resolve()
    if candidate_root.parent != expected_parent:
        raise ValueError(
            "parallel_batch_worktree_not_isolated:"
            + json.dumps(
                {**details, "reason": "not_plugin_managed", "expectedParent": str(expected_parent)},
                ensure_ascii=False,
                separators=(",", ":"),
            )
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
    errors = parallel_plan_errors(bundle)
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
) -> dict[str, Any]:
    # Kept as an ignored Python API compatibility parameter for older callers.
    # The CLI and fixed Workflow no longer expose it: platform workspace identity
    # must not constrain one or more business repositories.
    _ = workflow_workspace
    verdict = validate_plan_for_parallel(workspace, feature)
    if not verdict["canParallel"]:
        raise ValueError(f"parallel_not_available:{verdict['reason']}")
    if get_active_run(workspace, feature) is not None:
        raise ValueError("parallel_run_already_active")
    bundle = load_plan_bundle(feature_dir(workspace, feature))
    repositories = resolve_repository_bindings(bundle, code_workspaces, feature=feature)
    manifest = create_manifest(
        workspace,
        feature,
        max_parallel=max_parallel,
        timeout_seconds=timeout_seconds,
        repositories=repositories,
    )
    manifest["isolation"] = {
        "mode": "plugin_managed_git_worktrees",
        "worktreeDirectory": ".worktrees",
        "workspaceRefs": sorted(repositories),
    }
    manifest["status"] = "running"
    save_manifest(workspace, feature, str(manifest["runId"]), manifest)
    append_event(workspace, feature, str(manifest["runId"]), "run_created", maxParallel=manifest["maxParallel"])
    return schedule(workspace, feature, str(manifest["runId"]))


def _provision_scheduled_worktrees(
    workspace: Path,
    feature: str,
    run_id: str,
    manifest: dict[str, Any],
    selected_groups: list[list[str]],
) -> None:
    """Create deterministic Batch worktrees before implementation agents start.

    A workflow agent receives a ready-to-use path, not an instruction to create
    one itself. This prevents an aborted agent from falling back to the source
    checkout and makes every selected Batch observable in the manifest before
    code implementation begins.
    """
    for batch_id in (batch_id for group in selected_groups for batch_id in group):
        batch = manifest.get("batches", {}).get(batch_id)
        if not isinstance(batch, dict) or batch.get("status") != "pending":
            continue
        existing_path = batch.get("worktreePath")
        existing_branch = batch.get("branchName")
        if isinstance(existing_path, str) and existing_path and isinstance(existing_branch, str) and existing_branch:
            try:
                assert_batch_worktree_isolated(manifest, batch_id, existing_path)
            except ValueError as exc:
                raise ValueError(f"parallel_worktree_provision_invalid:{batch_id}:{exc}") from exc
            continue

        repository_ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
        repository = manifest.get("repositories", {}).get(repository_ref)
        if not isinstance(repository, dict) or not isinstance(repository.get("gitRoot"), str):
            raise ValueError(f"parallel_repository_binding_missing:{repository_ref}")
        git_root = Path(repository["gitRoot"]).resolve()
        expected_head = str(repository.get("headSha") or repository.get("baseSha") or "")
        if not expected_head:
            raise ValueError(f"parallel_base_sha_unavailable:{repository_ref}")
        if _git_head(git_root) != expected_head:
            raise ValueError(f"parallel_main_head_changed:{repository_ref}")
        status = subprocess.run(["git", "status", "--porcelain"], cwd=git_root, capture_output=True, text=True)
        if status.returncode != 0:
            raise ValueError(f"parallel_repository_unavailable:{repository_ref}")
        if status.stdout.strip():
            raise ValueError(f"parallel_main_worktree_dirty:{repository_ref}")

        result = create_worktree(
            git_root,
            f"{run_id}-{batch_id}",
            expected_head,
            branch_name=f"autodev/{feature}/{run_id}/{repository_ref}/{batch_id}",
        )
        if not result.get("success"):
            raise ValueError(f"parallel_worktree_provision_failed:{batch_id}:{result.get('error', 'unknown')}")
        batch.update({
            "worktreePath": result["worktreePath"],
            "branchName": result["branchName"],
            "repositoryRef": repository_ref,
            "gitRoot": str(git_root),
        })
        append_event(
            workspace,
            feature,
            run_id,
            "worktree_provisioned",
            batchId=batch_id,
            path=result["worktreePath"],
            branchName=result["branchName"],
            owner="scheduler",
        )


def schedule(workspace: Path, feature: str, run_id: str) -> dict[str, Any]:
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
        ready = ready_batches(manifest)
        groups = resource_groups(manifest, ready)
        max_parallel = int(manifest.get("maxParallel", 1))
        selected: list[list[str]] = []
        active = sum(
            1
            for item in manifest.get("batches", {}).values()
            if isinstance(item, dict) and item.get("status") in {"leased", "running"}
        )
        slots = max(0, max_parallel - active)
        for group in groups:
            if slots <= 0:
                break
            selected.append(group)
            slots -= 1
        try:
            _provision_scheduled_worktrees(workspace, feature, run_id, manifest, selected)
        except ValueError as exc:
            manifest["status"] = "blocked"
            manifest["provisionError"] = str(exc)
            save_manifest(workspace, feature, run_id, manifest)
            append_event(workspace, feature, run_id, "worktree_provision_failed", error=str(exc))
            raise
        manifest["scheduledAt"] = manifest.get("updatedAt")
        save_manifest(workspace, feature, run_id, manifest)
        return {
            "runId": run_id,
            "status": manifest.get("status"),
            "readyBatches": ready,
            "mergeableBatches": mergeable_batches(manifest),
            "parallelGroups": groups,
            "scheduledGroups": selected,
            "maxParallel": max_parallel,
            "activeWorkers": active,
            "batchWorkspaces": {
                batch_id: {
                    "workspaceRef": item.get("workspaceRef"),
                    "componentRoots": item.get("componentRoots", []),
                    "executionStage": item.get("executionStage", "parallel"),
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
    allowed = {"pending", "leased", "running", "compile_failed", "ready_to_merge", "needs_resolution", "merged", "failed", "blocked", "cancelled"}
    if status not in allowed:
        raise ValueError(f"parallel_batch_status_invalid:{status}")
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = manifest.get("batches", {}).get(batch_id)
        if not isinstance(batch, dict):
            raise ValueError(f"parallel_batch_not_found:{batch_id}")
        previous = batch.get("status")
        terminal = {"merged", "failed", "blocked", "cancelled"}
        if previous in terminal and previous != status:
            raise ValueError(f"parallel_batch_terminal:{batch_id}:{previous}")
        if status in {"running", "ready_to_merge"}:
            candidate = details.get("worktreePath") or batch.get("worktreePath")
            if not isinstance(candidate, str) or not candidate.strip():
                raise ValueError(f"parallel_batch_worktree_path_required:{batch_id}")
            assert_batch_worktree_isolated(manifest, batch_id, candidate)
        batch["status"] = status
        for key in ("worktreePath", "branchName", "commitSha", "compileStatus", "mergeCommitSha", "error"):
            if key in details:
                batch[key] = details[key]
        if status == "running" and not batch.get("startedAt"):
            batch["startedAt"] = details.get("startedAt") or manifest.get("updatedAt")
        if status in terminal:
            batch["completedAt"] = details.get("completedAt") or manifest.get("updatedAt")
        statuses = [item.get("status") for item in manifest.get("batches", {}).values() if isinstance(item, dict)]
        if statuses and all(item == "merged" for item in statuses):
            manifest["status"] = "succeeded"
        elif status in {"failed", "blocked"}:
            manifest["status"] = "blocked"
        save_manifest(workspace, feature, run_id, manifest)
        append_event(workspace, feature, run_id, "batch_status_changed", batchId=batch_id, previous=previous, status=status)
        return manifest


def resume_run(workspace: Path, feature: str, run_id: str) -> dict[str, Any]:
    """Idempotently resume only batches that do not already own a result."""
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if manifest.get("status") in {"succeeded", "cleaned", "rolled_back", "verifying"}:
            return {"runId": run_id, "status": manifest.get("status"), "skipped": "terminal_run"}
        for batch in manifest.get("batches", {}).values():
            if not isinstance(batch, dict):
                continue
            if batch.get("commitSha") or batch.get("mergeCommitSha"):
                batch["status"] = "merged" if batch.get("mergeCommitSha") else "ready_to_merge"
        manifest["status"] = "running"
        save_manifest(workspace, feature, run_id, manifest)
        append_event(workspace, feature, run_id, "run_resumed")
    return schedule(workspace, feature, run_id)


def _emit(ok: bool, **payload: Any) -> int:
    print(json.dumps({"ok": ok, **payload}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Schedule parallel Code batch runs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "create", "status", "resume", "list"):
        item = subparsers.add_parser(name)
        item.add_argument("--workspace")
        item.add_argument("--feature", required=True)
        if name in {"status", "resume"}:
            item.add_argument("--run-id", required=True)
        if name == "create":
            item.add_argument("--max-parallel", type=int, default=4)
            item.add_argument("--timeout-seconds", type=int, default=3600)
            item.add_argument("--code-workspace", action="append", required=True, help="workspaceRef=/path; single-ref runs may pass /path")
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
                ),
            )
        if args.command == "status":
            return _emit(True, manifest=load_manifest(workspace, feature, args.run_id), **schedule(workspace, feature, args.run_id))
        if args.command == "resume":
            return _emit(True, **resume_run(workspace, feature, args.run_id))
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
