#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plugin-owned Git worktree lifecycle for parallel Code batches.

The workflow host may run from a neutral artifact directory.  This module is
therefore the only component that creates writable Batch worktrees, always in
``<business-git-root>/.worktrees/``.  It never treats the artifact workspace
as a business repository.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.parallel_runtime import append_event, check_lease, load_manifest, run_lock, save_manifest
from hooks.repository_snapshot import RepositorySnapshotError, resolve_git_root


WORKTREE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_worktree_name(name: str) -> str:
    if not WORKTREE_NAME_RE.fullmatch(name):
        raise ValueError(f"invalid_worktree_name:{name}")
    return name


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def _worktree_directory_is_ignored(git_root: Path) -> bool:
    return _git(git_root, "check-ignore", "-q", "--", ".worktrees/.autodev-worktree-ignore-probe").returncode == 0


def _branch_for(feature: str, run_id: str, repository_ref: str, batch_id: str) -> str:
    return f"autodev/{feature}/{run_id}/{repository_ref}/{batch_id}"


def create_worktree(
    repo_path: Path,
    worktree_name: str,
    base_revision: str | None = None,
    *,
    branch_name: str | None = None,
) -> dict[str, Any]:
    """Create one linked worktree under the business repository's `.worktrees`."""
    try:
        git_root = resolve_git_root(repo_path)
        name = _safe_worktree_name(worktree_name)
    except (RepositorySnapshotError, ValueError) as exc:
        return {"success": False, "worktreePath": None, "branchName": None, "error": str(exc)}

    parent = git_root / ".worktrees"
    target = parent / name
    if not _worktree_directory_is_ignored(git_root):
        return {"success": False, "worktreePath": None, "branchName": None, "error": "worktree_dir_not_ignored"}
    if target.exists():
        return {"success": False, "worktreePath": str(target), "branchName": None, "error": "worktree_already_exists"}

    branch = branch_name or f"worktree/{name}"
    if branch.startswith("-") or ".." in branch or branch.endswith("/"):
        return {"success": False, "worktreePath": None, "branchName": None, "error": "invalid_branch_name"}
    if _git(git_root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
        return {"success": False, "worktreePath": None, "branchName": branch, "error": "worktree_branch_already_exists"}

    parent.mkdir(parents=True, exist_ok=True)
    base = base_revision or "HEAD"
    created = _git(git_root, "worktree", "add", str(target), "-b", branch, base)
    if created.returncode != 0:
        return {
            "success": False,
            "worktreePath": None,
            "branchName": None,
            "error": f"git_worktree_add_failed:{created.stderr.strip()}",
        }
    return {"success": True, "worktreePath": str(target), "branchName": branch, "error": None}


def remove_worktree(repo_path: Path, worktree_name: str, *, force: bool = False) -> dict[str, Any]:
    """Remove one plugin-managed worktree and its temporary branch."""
    try:
        git_root = resolve_git_root(repo_path)
        target = (git_root / ".worktrees" / _safe_worktree_name(worktree_name)).resolve()
    except (RepositorySnapshotError, ValueError) as exc:
        return {"success": False, "error": str(exc)}

    branch_name: str | None = None
    listed = _git(git_root, "worktree", "list", "--porcelain")
    current: Path | None = None
    for line in listed.stdout.splitlines():
        if line.startswith("worktree "):
            current = Path(line[9:]).resolve()
        elif line.startswith("branch ") and current == target:
            branch_name = line[7:].removeprefix("refs/heads/")

    if target.exists():
        command = ["worktree", "remove"]
        if force:
            command.append("--force")
        removed = _git(git_root, *command, str(target))
        if removed.returncode != 0:
            return {"success": False, "error": f"git_worktree_remove_failed:{removed.stderr.strip()}"}
    if branch_name:
        deleted = _git(git_root, "branch", "-D" if force else "-d", branch_name)
        if deleted.returncode != 0 and not force:
            return {"success": False, "error": f"git_worktree_branch_delete_failed:{deleted.stderr.strip()}"}
    return {"success": True, "error": None}


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


def create_parallel_worktree(
    artifact_workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    repo_path: Path | None,
    owner_token: str | None,
) -> dict[str, Any]:
    """Create the isolated worktree recorded for a leased Batch."""
    with run_lock(artifact_workspace, feature, run_id):
        try:
            manifest, batch, repository_ref, git_root = _parallel_binding(artifact_workspace, feature, run_id, batch_id)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        if not owner_token or not check_lease(artifact_workspace, feature, run_id, batch_id, owner_token):
            return {"success": False, "error": f"parallel_batch_lease_invalid:{batch_id}"}
        try:
            if repo_path is not None and resolve_git_root(repo_path) != git_root:
                return {"success": False, "error": f"parallel_repository_binding_mismatch:{repository_ref}"}
        except RepositorySnapshotError as exc:
            return {"success": False, "error": f"parallel_repository_binding_mismatch:{repository_ref}:{exc}"}
        existing_path = batch.get("worktreePath")
        existing_branch = batch.get("branchName")
        if isinstance(existing_path, str) and existing_path and isinstance(existing_branch, str) and existing_branch:
            existing = Path(existing_path).resolve()
            if existing.is_dir() and existing.parent == (git_root / ".worktrees").resolve():
                return {"success": True, "worktreePath": str(existing), "branchName": existing_branch, "error": None}
            return {"success": False, "error": f"parallel_worktree_invalid:{batch_id}"}
        if _git(git_root, "status", "--porcelain").stdout.strip():
            return {"success": False, "error": f"parallel_main_worktree_dirty:{repository_ref}"}
        binding = manifest["repositories"][repository_ref]
        base_sha = str(binding.get("headSha") or binding.get("baseSha") or "")
        if not base_sha:
            return {"success": False, "error": f"parallel_base_sha_unavailable:{repository_ref}"}
        if _git(git_root, "rev-parse", "HEAD").stdout.strip() != base_sha:
            return {"success": False, "error": f"parallel_main_head_changed:{repository_ref}"}

        result = create_worktree(
            git_root,
            f"{run_id}-{batch_id}",
            base_sha,
            branch_name=_branch_for(feature, run_id, repository_ref, batch_id),
        )
        if result.get("success"):
            batch.update({
                "worktreePath": result["worktreePath"],
                "branchName": result["branchName"],
                "repositoryRef": repository_ref,
                "gitRoot": str(git_root),
                "status": "leased",
            })
            save_manifest(artifact_workspace, feature, run_id, manifest)
    if result.get("success"):
        append_event(
            artifact_workspace, feature, run_id, "worktree_created",
            batchId=batch_id, path=result["worktreePath"], branchName=result["branchName"], owner="plugin",
        )
    return result


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
        try:
            if repo_path is not None and resolve_git_root(repo_path) != git_root:
                return {"success": False, "error": f"parallel_repository_binding_mismatch:{repository_ref}"}
        except RepositorySnapshotError as exc:
            return {"success": False, "error": f"parallel_repository_binding_mismatch:{repository_ref}:{exc}"}

        worktree = Path(str(batch.get("worktreePath") or "")).resolve()
        if not worktree.is_dir() or worktree.parent != (git_root / ".worktrees").resolve():
            return {"success": False, "error": f"parallel_worktree_invalid:{batch_id}"}
        if _git(worktree, "branch", "--show-current").stdout.strip() != batch.get("branchName"):
            return {"success": False, "error": f"parallel_worktree_branch_mismatch:{batch_id}"}
        status = _git(worktree, "status", "--porcelain")
        if status.returncode != 0:
            return {"success": False, "error": f"parallel_worktree_status_failed:{status.stderr.strip()}"}
        changed = [line[3:] for line in status.stdout.splitlines() if len(line) > 3]
        forbidden = [path for path in changed if path.startswith((".autobizdevops/", ".parallel-runs/"))]
        if forbidden:
            return {"success": False, "error": "parallel_batch_artifact_changes_forbidden", "files": forbidden}
        if changed:
            staged = _git(worktree, "add", "-A")
            if staged.returncode != 0:
                return {"success": False, "error": f"parallel_batch_stage_failed:{staged.stderr.strip()}"}
            committed = _git(worktree, "commit", "-m", f"autodev: implement {feature} {batch_id}")
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
    """Remove the plugin-owned worktree recorded for a terminal Batch."""
    with run_lock(artifact_workspace, feature, run_id):
        try:
            _manifest, batch, _repository_ref, git_root = _parallel_binding(artifact_workspace, feature, run_id, batch_id)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        raw_path = batch.get("worktreePath")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return {"success": True, "worktreePath": None, "error": None}
        worktree = Path(raw_path).resolve()
        expected_parent = (git_root / ".worktrees").resolve()
        if worktree.parent != expected_parent:
            return {"success": False, "error": f"parallel_worktree_cleanup_invalid:{batch_id}"}
        name = worktree.name
    result = remove_worktree(git_root, name, force=force)
    if result.get("success"):
        append_event(artifact_workspace, feature, run_id, "worktree_removed", batchId=batch_id, path=str(worktree), owner="plugin")
    return {**result, "worktreePath": str(worktree)}


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
    parser = argparse.ArgumentParser(description="管理插件创建的 Git worktree")
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--repo")
    create.add_argument("--name")
    create.add_argument("--base-branch")
    create.add_argument("--branch-name")
    create.add_argument("--artifact-workspace")
    create.add_argument("--feature")
    create.add_argument("--run-id")
    create.add_argument("--batch-id")
    create.add_argument("--owner-token")
    seal = commands.add_parser("seal")
    seal.add_argument("--repo")
    seal.add_argument("--artifact-workspace", required=True)
    seal.add_argument("--feature", required=True)
    seal.add_argument("--run-id", required=True)
    seal.add_argument("--batch-id", required=True)
    seal.add_argument("--owner-token", required=True)
    remove = commands.add_parser("remove")
    remove.add_argument("--repo", required=True)
    remove.add_argument("--name", required=True)
    remove.add_argument("--force", action="store_true")
    listed = commands.add_parser("list")
    listed.add_argument("--repo", required=True)
    args = parser.parse_args(argv)

    if args.command == "create":
        parallel_values = (args.artifact_workspace, args.feature, args.run_id, args.batch_id)
        if any(parallel_values):
            result = (
                create_parallel_worktree(Path(args.artifact_workspace), args.feature, args.run_id, args.batch_id, Path(args.repo) if args.repo else None, args.owner_token)
                if all(parallel_values)
                else {"success": False, "error": "parallel_worktree_arguments_incomplete"}
            )
        else:
            result = create_worktree(Path(args.repo), args.name, args.base_branch, branch_name=args.branch_name) if args.repo and args.name else {"success": False, "error": "name_and_repo_required"}
    elif args.command == "seal":
        result = seal_parallel_batch(Path(args.artifact_workspace), args.feature, args.run_id, args.batch_id, Path(args.repo) if args.repo else None, args.owner_token)
    elif args.command == "remove":
        result = remove_worktree(Path(args.repo), args.name, force=args.force)
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
