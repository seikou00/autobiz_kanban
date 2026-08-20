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

from hooks.json_writer_common import feature_dir, resolve_feature, resolve_workspace
from hooks.parallel_runtime import (
    append_event,
    create_manifest,
    get_active_run,
    list_runs,
    load_manifest,
    parallel_plan_errors,
    plan_digest,
    ready_batches,
    resource_groups,
    run_lock,
    save_manifest,
)
from hooks.plan_json import load_plan_bundle
from hooks.repository_snapshot import resolve_git_root


def resolve_repository_bindings(bundle: Any, values: list[str] | None) -> dict[str, dict[str, Any]]:
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
        git_root = resolve_git_root(requested)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=git_root, capture_output=True, text=True)
        if status.returncode != 0:
            raise ValueError(f"parallel_code_workspace_invalid:{ref}")
        if status.stdout.strip():
            raise ValueError(f"parallel_code_workspace_dirty:{ref}")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_root, capture_output=True, text=True)
        if head.returncode != 0 or not head.stdout.strip():
            raise ValueError(f"parallel_code_workspace_head_unavailable:{ref}")
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=git_root, capture_output=True, text=True)
        bindings[ref] = {
            "workspaceRef": ref,
            "requestedPath": str(requested),
            "gitRoot": str(git_root),
            "baseSha": head.stdout.strip(),
            # This moves forward only through merges owned by this run.  It
            # is the expected main HEAD for later dependency waves.
            "headSha": head.stdout.strip(),
            "baseBranch": branch.stdout.strip() or None,
        }
    return bindings


def validate_plan_for_parallel(workspace: Path, feature: str) -> dict[str, Any]:
    try:
        bundle = load_plan_bundle(feature_dir(workspace, feature))
    except ValueError as exc:
        return {
            "canParallel": False,
            "fallbackToSerial": False,
            "requiresPlanRepair": True,
            "reason": f"invalid_plan:{exc}",
            "errors": [str(exc)],
        }
    errors = parallel_plan_errors(bundle)
    if errors:
        return {
            "canParallel": False,
            "fallbackToSerial": False,
            "requiresPlanRepair": True,
            "reason": errors[0],
            "errors": errors,
        }
    entries = [item for item in bundle.root.get("batches", []) if isinstance(item, dict) and item.get("status") not in {"done", "failed"}]
    if len(entries) < 2:
        return {"canParallel": False, "fallbackToSerial": True, "reason": "fewer_than_two_pending_batches", "errors": []}
    return {
        "canParallel": True,
        "fallbackToSerial": False,
        "reason": "parallel_plan_valid",
        "planDigest": plan_digest(bundle),
        "batches": [str(item["id"]) for item in entries],
        "workspaceRefs": sorted({
            str(task.get("workspaceRef"))
            for batch in bundle.batches.values()
            for task in batch.get("tasks", [])
            if isinstance(task, dict) and isinstance(task.get("workspaceRef"), str)
        }),
        "conflictPolicy": {
            "enabled": isinstance(bundle.root.get("parallelPolicy"), dict) and bundle.root["parallelPolicy"].get("enabled") is True,
        },
        "errors": [],
    }


def create_run(workspace: Path, feature: str, *, max_parallel: int, timeout_seconds: int, code_workspaces: list[str] | None = None) -> dict[str, Any]:
    verdict = validate_plan_for_parallel(workspace, feature)
    if not verdict["canParallel"]:
        raise ValueError(f"parallel_not_available:{verdict['reason']}")
    if get_active_run(workspace, feature) is not None:
        raise ValueError("parallel_run_already_active")
    bundle = load_plan_bundle(feature_dir(workspace, feature))
    repositories = resolve_repository_bindings(bundle, code_workspaces)
    manifest = create_manifest(
        workspace,
        feature,
        max_parallel=max_parallel,
        timeout_seconds=timeout_seconds,
        repositories=repositories,
    )
    manifest["status"] = "running"
    save_manifest(workspace, feature, str(manifest["runId"]), manifest)
    append_event(workspace, feature, str(manifest["runId"]), "run_created", maxParallel=manifest["maxParallel"])
    return schedule(workspace, feature, str(manifest["runId"]))


def schedule(workspace: Path, feature: str, run_id: str) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        bundle = load_plan_bundle(feature_dir(workspace, feature))
        if plan_digest(bundle) != manifest.get("planDigest"):
            manifest["status"] = "blocked"
            save_manifest(workspace, feature, run_id, manifest)
            append_event(workspace, feature, run_id, "plan_changed")
            raise ValueError("parallel_plan_digest_changed")
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
        manifest["scheduledAt"] = manifest.get("updatedAt")
        save_manifest(workspace, feature, run_id, manifest)
        return {
            "runId": run_id,
            "status": manifest.get("status"),
            "readyBatches": ready,
            "parallelGroups": groups,
            "scheduledGroups": selected,
            "conflictWarnings": (manifest.get("conflictPolicy", {}) or {}).get("warnings", []),
            "maxParallel": max_parallel,
            "activeWorkers": active,
            "batchWorkspaces": {
                batch_id: {
                    "workspaceRef": item.get("workspaceRef"),
                    "componentRoots": item.get("componentRoots", []),
                    "executionStage": item.get("executionStage", "parallel"),
                    "executionOwner": item.get("executionOwner", "batch-engineer"),
                    "requestedPath": (manifest.get("repositories", {}).get(str(item.get("repositoryRef")), {}) or {}).get("requestedPath"),
                }
                for batch_id, item in manifest.get("batches", {}).items()
                if isinstance(item, dict)
            },
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
    mark.add_argument("--compile-status")
    mark.add_argument("--error")
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        if args.command == "validate":
            return _emit(True, **validate_plan_for_parallel(workspace, feature))
        if args.command == "create":
            return _emit(True, **create_run(workspace, feature, max_parallel=args.max_parallel, timeout_seconds=args.timeout_seconds, code_workspaces=args.code_workspace))
        if args.command == "status":
            return _emit(True, manifest=load_manifest(workspace, feature, args.run_id), **schedule(workspace, feature, args.run_id))
        if args.command == "resume":
            return _emit(True, **resume_run(workspace, feature, args.run_id))
        if args.command == "list":
            return _emit(True, runs=list_runs(workspace, feature))
        details = {key: value for key, value in vars(args).items() if key in {"commit_sha", "merge_commit_sha", "compile_status", "error"} and value is not None}
        details = {key.replace("_sha", "Sha").replace("_status", "Status").replace("_commit", "Commit"): value for key, value in details.items()}
        return _emit(True, manifest=mark_batch(workspace, feature, args.run_id, args.batch_id, args.status, **details))
    except (ValueError, OSError) as exc:
        return _emit(False, error=str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
