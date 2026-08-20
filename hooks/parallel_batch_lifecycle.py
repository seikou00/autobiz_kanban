#!/usr/bin/env python3
"""Lifecycle operations for durable parallel Code runs."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hooks.json_writer_common import resolve_feature, resolve_workspace
from hooks.parallel_runtime import (
    append_event,
    get_active_run,
    lease_path,
    list_runs,
    load_manifest,
    reclaim_lease,
    run_dir,
    run_lock,
    save_manifest,
)
from hooks.worktree_manager import remove_worktree


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def detect_external_changes(repo_path: Path, base_sha: str, *, allow_paths: tuple[str, ...] = ()) -> dict[str, Any]:
    """Detect modifications made to the main checkout during a run."""
    head = _git(repo_path, "rev-parse", "HEAD").stdout.strip()
    status = [line for line in _git(repo_path, "status", "--porcelain").stdout.splitlines() if line]
    changed = [line[3:] if len(line) > 3 else line for line in status]
    unexpected = [
        path for path in changed
        if not any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in allow_paths)
    ]
    commits = _git(repo_path, "diff", "--name-only", f"{base_sha}..HEAD").stdout.splitlines() if base_sha and head else []
    return {
        "clean": not unexpected and not commits,
        "baseSha": base_sha,
        "headSha": head,
        "workingTreeChanges": changed,
        "unexpectedWorkingTreeChanges": unexpected,
        "commitsSinceBase": commits,
    }


def reclaim_stale_leases(workspace: Path, feature: str, run_id: str, *, force: bool = False) -> list[str]:
    manifest = load_manifest(workspace, feature, run_id)
    reclaimed: list[str] = []
    for batch_id, item in manifest.get("batches", {}).items():
        if isinstance(item, dict) and item.get("status") == "leased":
            if reclaim_lease(workspace, feature, run_id, batch_id, force=force):
                reclaimed.append(batch_id)
    return reclaimed


def cleanup_run(workspace: Path, feature: str, run_id: str, *, repo_path: Path | None = None, force: bool = False) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if manifest.get("status") not in {"succeeded", "failed", "blocked", "cancelled"} and not force:
            raise ValueError("parallel_run_not_terminal")
        removed: list[str] = []
        errors: list[str] = []
        for item in manifest.get("batches", {}).values():
            if not isinstance(item, dict) or not item.get("worktreePath"):
                continue
            ref = str(item.get("repositoryRef") or item.get("workspaceRef") or "")
            repository = manifest.get("repositories", {}).get(ref)
            root = Path(repository["gitRoot"]) if isinstance(repository, dict) and isinstance(repository.get("gitRoot"), str) else repo_path
            if root is None:
                errors.append(f"parallel_repository_binding_missing:{ref}")
                continue
            result = remove_worktree(root, Path(str(item["worktreePath"])).name, force=force)
            if result.get("success"):
                removed.append(str(item["worktreePath"]))
            elif result.get("error"):
                errors.append(str(result["error"]))
        manifest["cleanup"] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "removedWorktrees": removed, "errors": errors}
        manifest["status"] = "cleaned" if not errors else "cleanup_failed"
        save_manifest(workspace, feature, run_id, manifest)
        append_event(workspace, feature, run_id, "run_cleaned", removed=removed, errors=errors)
        return {"runId": run_id, "status": manifest["status"], "removedWorktrees": removed, "errors": errors}


def rollback_run(workspace: Path, feature: str, run_id: str, repo_path: Path | None = None, *, mode: str = "partial", confirm: bool = False) -> dict[str, Any]:
    if mode not in {"full", "partial"}:
        raise ValueError("rollback_mode_invalid")
    if mode == "full" and not confirm:
        raise ValueError("full_rollback_requires_confirm")
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        reverted: list[str] = []
        if mode == "full":
            commits_by_repository: dict[str, dict[str, Any]] = {}
            for item in manifest.get("batches", {}).values():
                if isinstance(item, dict) and isinstance(item.get("mergeCommitSha"), str):
                    ref = str(item.get("repositoryRef") or item.get("workspaceRef") or "")
                    repository = manifest.get("repositories", {}).get(ref)
                    root = Path(repository["gitRoot"]) if isinstance(repository, dict) and isinstance(repository.get("gitRoot"), str) else repo_path
                    if root is None:
                        raise ValueError(f"parallel_repository_binding_missing:{ref}")
                    key = str(root.resolve())
                    record = commits_by_repository.setdefault(key, {"root": root, "refs": [], "commits": []})
                    record["refs"].append(ref)
                    record["commits"].append(str(item["mergeCommitSha"]))
            for record in commits_by_repository.values():
                root = record["root"]
                commits = record["commits"]
                refs = sorted(set(record["refs"]))
                for commit in reversed(commits):
                    result = _git(root, "revert", "--no-edit", commit)
                    if result.returncode != 0:
                        _git(root, "revert", "--abort")
                        raise ValueError(f"rollback_revert_failed:{','.join(refs)}:{commit}:{result.stderr.strip()}")
                    reverted.append(f"{','.join(refs)}:{commit}")
        else:
            # Partial rollback preserves successful work and only abandons the
            # scheduler state; no commits are rewritten.
            for item in manifest.get("batches", {}).values():
                if isinstance(item, dict) and item.get("status") not in {"merged", "succeeded"}:
                    item["status"] = "cancelled"
        manifest["status"] = "rolled_back"
        manifest["rollback"] = {"mode": mode, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reverted": reverted}
        save_manifest(workspace, feature, run_id, manifest)
        append_event(workspace, feature, run_id, "run_rolled_back", mode=mode)
        return {"runId": run_id, "status": manifest["status"], "mode": mode, "reverted": reverted}


def monitor_run(workspace: Path, feature: str, run_id: str) -> dict[str, Any]:
    manifest = load_manifest(workspace, feature, run_id)
    batches = manifest.get("batches", {})
    now = time.time()
    counts: dict[str, int] = {}
    timeline: list[dict[str, Any]] = []
    def epoch(value: Any) -> float | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    for batch_id, item in batches.items():
        if not isinstance(item, dict):
            continue
        status = str(item.get("status"))
        counts[status] = counts.get(status, 0) + 1
        started = item.get("startedAt")
        completed = item.get("completedAt")
        started_epoch = epoch(started)
        completed_epoch = epoch(completed)
        duration = None
        if started_epoch is not None:
            duration = round(max(0.0, (completed_epoch if completed_epoch is not None else now) - started_epoch), 3)
        timeline.append({"batchId": batch_id, "status": status, "startedAt": started, "completedAt": completed, "durationSeconds": duration})
    return {"runId": run_id, "status": manifest.get("status"), "counts": counts, "activeWorkers": counts.get("running", 0) + counts.get("leased", 0), "timeline": timeline, "updatedAt": manifest.get("updatedAt"), "nowEpoch": now}


def auto_cleanup_old_runs(workspace: Path, feature: str, *, repo_path: Path | None = None, keep_days: int = 7) -> list[dict[str, Any]]:
    """Remove worktrees for old successful runs; failed runs remain diagnostic data."""
    cutoff = time.time() - max(0, keep_days) * 24 * 60 * 60
    cleaned: list[dict[str, Any]] = []
    for manifest in list_runs(workspace, feature):
        if manifest.get("status") != "succeeded":
            continue
        try:
            created = datetime.fromisoformat(str(manifest.get("createdAt", "")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if created > cutoff:
            continue
        cleaned.append(cleanup_run(workspace, feature, str(manifest["runId"]), repo_path=repo_path))
    return cleaned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage parallel Code run lifecycle")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("monitor", "reclaim-leases", "cleanup", "rollback", "external-changes", "auto-cleanup"):
        p = sub.add_parser(name)
        p.add_argument("--workspace")
        p.add_argument("--feature", required=True)
        if name not in {"external-changes", "auto-cleanup"}:
            p.add_argument("--run-id", required=True)
        if name in {"cleanup", "rollback", "external-changes", "auto-cleanup"}:
            p.add_argument("--repo-path")
        if name in {"cleanup", "reclaim-leases"}:
            p.add_argument("--force", action="store_true")
        if name == "rollback":
            p.add_argument("--mode", choices=("full", "partial"), default="partial")
            p.add_argument("--confirm", action="store_true")
        if name == "external-changes":
            p.add_argument("--base-sha", required=True)
        if name == "auto-cleanup":
            p.add_argument("--keep-days", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        if args.command == "monitor":
            result = monitor_run(workspace, feature, args.run_id)
        elif args.command == "reclaim-leases":
            result = {"reclaimed": reclaim_stale_leases(workspace, feature, args.run_id, force=args.force)}
        elif args.command == "cleanup":
            result = cleanup_run(workspace, feature, args.run_id, repo_path=Path(args.repo_path) if args.repo_path else None, force=args.force)
        elif args.command == "rollback":
            result = rollback_run(workspace, feature, args.run_id, Path(args.repo_path) if args.repo_path else None, mode=args.mode, confirm=args.confirm)
        elif args.command == "auto-cleanup":
            result = {"cleaned": auto_cleanup_old_runs(workspace, feature, repo_path=Path(args.repo_path) if args.repo_path else None, keep_days=args.keep_days)}
        else:
            if not args.repo_path:
                raise ValueError("repo_path_required_for_external_changes")
            result = detect_external_changes(Path(args.repo_path), args.base_sha)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
