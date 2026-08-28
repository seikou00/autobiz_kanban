#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lifecycle operations for plugin-owned native Git worktrees.

The workflow host may be an artifact directory or another repository.  The
plugin therefore creates a real linked Git worktree from each repository
binding and records its path in the scheduler manifest.  Agents only receive
that explicit path; all delivery and cleanup remains deterministic here.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.parallel_runtime import append_event, check_lease, load_manifest, run_dir, run_lock, save_manifest
from hooks.repository_snapshot import (
    PLATFORM_RUNTIME_DIRECTORY,
    RepositorySnapshotError,
    current_git_branch,
    git_status_porcelain,
    resolve_git_root,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _branch_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return component or "workspace"


def _native_worktree_path(
    artifact_workspace: Path,
    feature: str,
    run_id: str,
    repository_ref: str,
    batch_id: str,
) -> Path:
    return (
        run_dir(artifact_workspace, feature, run_id)
        / "worktrees"
        / _branch_component(repository_ref)
        / _branch_component(batch_id)
    ).resolve()


def provision_parallel_worktree(
    artifact_workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
) -> dict[str, Any]:
    """Create or reuse the native Git worktree assigned to one Batch.

    Provisioning is idempotent for a live Batch, but stale paths and branches
    are rejected rather than overwritten.  This prevents an interrupted run
    from silently attaching a Batch to another checkout.
    """
    with run_lock(artifact_workspace, feature, run_id):
        try:
            manifest, batch, repository_ref, git_root = _parallel_binding(
                artifact_workspace, feature, run_id, batch_id
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        status = str(batch.get("status") or "pending")
        if status in {"merged", "succeeded", "cancelled"}:
            return {"success": False, "error": f"parallel_batch_not_provisionable:{batch_id}:{status}"}

        raw_path = batch.get("worktreePath")
        if isinstance(raw_path, str) and raw_path.strip():
            existing = Path(raw_path).expanduser().resolve()
            try:
                from hooks.parallel_batch_scheduler import assert_batch_worktree_isolated

                assert_batch_worktree_isolated(manifest, batch_id, existing)
                expected = str(batch.get("branchName") or "")
                if current_git_branch(existing) == expected:
                    return {
                        "success": True,
                        "batchId": batch_id,
                        "repositoryRef": repository_ref,
                        "worktreePath": str(existing),
                        "branchName": expected,
                        "reused": True,
                    }
            except (ValueError, OSError):
                pass
            return {"success": False, "error": f"parallel_worktree_stale:{batch_id}"}

        head = str(
            (manifest.get("repositories", {}).get(repository_ref, {}) or {}).get("headSha")
            or (manifest.get("repositories", {}).get(repository_ref, {}) or {}).get("baseSha")
            or ""
        )
        if not head or _git(git_root, "rev-parse", "--verify", head).returncode != 0:
            return {"success": False, "error": f"parallel_repository_head_unavailable:{repository_ref}"}
        branch_name = "autodev/{}/{}/{}".format(
            _branch_component(feature), _branch_component(run_id), _branch_component(batch_id)
        )
        target = _native_worktree_path(artifact_workspace, feature, run_id, repository_ref, batch_id)
        if target.exists():
            return {"success": False, "error": f"parallel_worktree_path_occupied:{target}"}
        if _git(git_root, "show-ref", "--verify", f"refs/heads/{branch_name}").returncode == 0:
            return {"success": False, "error": f"parallel_worktree_branch_occupied:{branch_name}"}
        target.parent.mkdir(parents=True, exist_ok=True)
        created = _git(git_root, "worktree", "add", "-b", branch_name, str(target), head)
        if created.returncode != 0:
            return {"success": False, "error": f"parallel_worktree_create_failed:{created.stderr.strip()}"}
        batch.update({
            "worktreePath": str(target),
            "branchName": branch_name,
            "worktreeOwner": "plugin",
        })
        save_manifest(artifact_workspace, feature, run_id, manifest)
    append_event(
        artifact_workspace,
        feature,
        run_id,
        "worktree_provisioned",
        batchId=batch_id,
        repositoryRef=repository_ref,
        path=str(target),
        branch=branch_name,
        owner="plugin",
    )
    return {
        "success": True,
        "batchId": batch_id,
        "repositoryRef": repository_ref,
        "worktreePath": str(target),
        "branchName": branch_name,
        "reused": False,
    }


def _parallel_binding(
    artifact_workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str, Path]:
    manifest = load_manifest(artifact_workspace, feature, run_id)
    batch = manifest.get("batches", {}).get(batch_id)
    if not isinstance(batch, dict):
        raise ValueError(f"parallel_batch_not_found:{batch_id}")
    repository_ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
    repository = manifest.get("repositories", {}).get(repository_ref)
    if not isinstance(repository, dict) or not isinstance(repository.get("gitRoot"), str):
        raise ValueError(f"parallel_repository_binding_missing:{repository_ref}")
    return manifest, batch, repository_ref, Path(repository["gitRoot"]).resolve()


def _unstage_platform_runtime(worktree: Path) -> dict[str, Any] | None:
    """Keep platform workflow journals out of the delivery commit.

    Dynamic Workflow writes its own state under ``.cmbdevclaw`` while the
    Batch agent runs.  A previous command may already have staged that state,
    so an exclude pathspec on ``git add`` alone is insufficient.
    """
    staged = _git(worktree, "diff", "--cached", "--name-only", "--", PLATFORM_RUNTIME_DIRECTORY)
    if staged.returncode != 0:
        return {"success": False, "error": f"parallel_batch_staged_runtime_check_failed:{staged.stderr.strip()}"}
    paths = [line.strip() for line in staged.stdout.splitlines() if line.strip()]
    if not paths:
        return None
    reset = _git(worktree, "reset", "--", *paths)
    if reset.returncode != 0:
        return {"success": False, "error": f"parallel_batch_unstage_runtime_failed:{reset.stderr.strip()}"}
    return None


def seal_parallel_batch(
    artifact_workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    repo_path: Path | None,
    owner_token: str,
) -> dict[str, Any]:
    """Commit a compiled Batch worktree and persist its delivery SHA."""
    if not check_lease(artifact_workspace, feature, run_id, batch_id, owner_token):
        return {"success": False, "error": f"parallel_batch_lease_invalid:{batch_id}"}
    with run_lock(artifact_workspace, feature, run_id):
        try:
            manifest, batch, repository_ref, git_root = _parallel_binding(artifact_workspace, feature, run_id, batch_id)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        if batch.get("status") != "ready_to_merge" or batch.get("compileStatus") != "passed":
            return {"success": False, "error": f"parallel_batch_not_ready_to_seal:{batch_id}"}
        raw_worktree = batch.get("worktreePath")
        if not isinstance(raw_worktree, str) or not raw_worktree.strip():
            return {"success": False, "error": f"parallel_worktree_missing:{batch_id}"}
        worktree = Path(raw_worktree).resolve()
        try:
            # ``git rev-parse --show-toplevel`` returns the linked worktree,
            # not the primary checkout.  Compare the request to the stored
            # worktree first; assert_batch_worktree_isolated below verifies it
            # belongs to the primary repository's Git worktree registry.
            if repo_path is not None and resolve_git_root(repo_path) != worktree:
                return {"success": False, "error": f"parallel_repository_binding_mismatch:{repository_ref}"}
        except RepositorySnapshotError as exc:
            return {"success": False, "error": f"parallel_repository_binding_mismatch:{repository_ref}:{exc}"}
        try:
            # Keep the isolation rule in one place.  It validates Git's live
            # worktree registry rather than a plugin-owned filesystem layout.
            from hooks.parallel_batch_scheduler import assert_batch_worktree_isolated

            assert_batch_worktree_isolated(manifest, batch_id, worktree)
        except ValueError:
            return {"success": False, "error": f"parallel_worktree_invalid:{batch_id}"}
        if current_git_branch(worktree) != batch.get("branchName"):
            return {"success": False, "error": f"parallel_worktree_branch_mismatch:{batch_id}"}
        runtime_error = _unstage_platform_runtime(worktree)
        if runtime_error:
            return runtime_error
        status = git_status_porcelain(worktree)
        if status.returncode != 0:
            return {"success": False, "error": f"parallel_worktree_status_failed:{status.stderr.strip()}"}
        changed = [line[3:] for line in status.stdout.splitlines() if len(line) > 3]
        forbidden = [path for path in changed if path.startswith((".autobizdevops/", ".parallel-runs/"))]
        if forbidden:
            return {"success": False, "error": "parallel_batch_artifact_changes_forbidden", "files": forbidden}
        if changed:
            staged = _git(
                worktree,
                "add",
                "-A",
                "--",
                ".",
                f":(exclude){PLATFORM_RUNTIME_DIRECTORY}**",
            )
            if staged.returncode != 0:
                return {"success": False, "error": f"parallel_batch_stage_failed:{staged.stderr.strip()}"}
            committed = _git(
                worktree,
                "commit",
                "-m",
                f"autodev: implement {feature} {batch_id}",
                "--",
                ".",
                f":(exclude){PLATFORM_RUNTIME_DIRECTORY}**",
            )
            if committed.returncode != 0:
                return {"success": False, "error": f"parallel_batch_commit_failed:{committed.stderr.strip()}"}
        sha = _git(worktree, "rev-parse", "HEAD")
        if sha.returncode != 0 or not sha.stdout.strip():
            return {"success": False, "error": "parallel_batch_commit_sha_unavailable"}
        batch["commitSha"] = sha.stdout.strip()
        save_manifest(artifact_workspace, feature, run_id, manifest)
    append_event(artifact_workspace, feature, run_id, "batch_sealed", batchId=batch_id, commitSha=sha.stdout.strip(), changedFiles=changed)
    return {"success": True, "batchId": batch_id, "commitSha": sha.stdout.strip(), "changedFiles": changed}


def remove_parallel_worktree(
    artifact_workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Remove a plugin-owned native Worktree after delivery or failure."""
    with run_lock(artifact_workspace, feature, run_id):
        try:
            manifest, batch, repository_ref, git_root = _parallel_binding(artifact_workspace, feature, run_id, batch_id)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        raw_path = batch.get("worktreePath")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return {"success": True, "worktreePath": None, "error": None}
        worktree = Path(raw_path).expanduser().resolve()
        isolation = manifest.get("isolation") if isinstance(manifest.get("isolation"), dict) else {}
        if isolation.get("mode") != "native_git_worktrees":
            return {"success": False, "error": f"parallel_worktree_cleanup_owner_unknown:{batch_id}"}
        branch = str(batch.get("branchName") or "")

    removed = False
    if worktree.exists():
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(worktree))
        result = _git(git_root, *args)
        if result.returncode != 0:
            return {"success": False, "error": f"parallel_worktree_remove_failed:{result.stderr.strip()}"}
        removed = True
    pruned = _git(git_root, "worktree", "prune")
    if pruned.returncode != 0:
        return {"success": False, "error": f"parallel_worktree_prune_failed:{pruned.stderr.strip()}"}
    branch_removed = False
    if branch and branch != str((manifest.get("repositories", {}).get(repository_ref, {}) or {}).get("baseBranch") or ""):
        delete = _git(git_root, "branch", "-D" if force else "-d", branch)
        branch_removed = delete.returncode == 0
    with run_lock(artifact_workspace, feature, run_id):
        manifest, batch, _repository_ref, _git_root = _parallel_binding(artifact_workspace, feature, run_id, batch_id)
        batch["worktreeRemovedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        save_manifest(artifact_workspace, feature, run_id, manifest)
    append_event(
        artifact_workspace,
        feature,
        run_id,
        "worktree_removed",
        batchId=batch_id,
        path=str(worktree),
        owner="plugin",
        requestedForce=force,
        removed=removed,
        branchRemoved=branch_removed,
    )
    return {
        "success": True,
        "worktreePath": str(worktree),
        "removed": removed,
        "branchRemoved": branch_removed,
        "error": None,
    }


def list_worktrees(repo_path: Path) -> dict[str, Any]:
    try:
        root = resolve_git_root(repo_path)
    except RepositorySnapshotError as exc:
        return {"worktrees": [], "error": str(exc)}
    result = _git(root, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        return {"worktrees": [], "error": result.stderr.strip()}
    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line:
            if current:
                worktrees.append(current)
                current = {}
        elif line.startswith("worktree "):
            current["path"] = line[9:]
        elif line.startswith("branch "):
            current["branch"] = line[7:]
        elif line.startswith("HEAD "):
            current["commit"] = line[5:]
    if current:
        worktrees.append(current)
    return {"worktrees": worktrees, "error": None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="管理插件托管原生 Git Worktree 的 Batch 交付")
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision")
    provision.add_argument("--artifact-workspace", required=True)
    provision.add_argument("--feature", required=True)
    provision.add_argument("--run-id", required=True)
    provision.add_argument("--batch-id", required=True)
    seal = commands.add_parser("seal")
    seal.add_argument("--repo")
    seal.add_argument("--artifact-workspace", required=True)
    seal.add_argument("--feature", required=True)
    seal.add_argument("--run-id", required=True)
    seal.add_argument("--batch-id", required=True)
    seal.add_argument("--owner-token", required=True)
    listed = commands.add_parser("list")
    listed.add_argument("--repo", required=True)
    args = parser.parse_args(argv)

    if args.command == "provision":
        result = provision_parallel_worktree(Path(args.artifact_workspace), args.feature, args.run_id, args.batch_id)
    elif args.command == "seal":
        result = seal_parallel_batch(Path(args.artifact_workspace), args.feature, args.run_id, args.batch_id, Path(args.repo) if args.repo else None, args.owner_token)
    else:
        result = list_worktrees(Path(args.repo))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("success"):
        print("success")
    else:
        print(f"failed: {result.get('error', 'unknown')}")
    return 0 if result.get("success") or args.command == "list" else 1


if __name__ == "__main__":
    raise SystemExit(main())
