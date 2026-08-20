#!/usr/bin/env python3
"""Prepare and complete a normal Git conflict resolution in an isolated tree."""

from __future__ import annotations

import argparse
import json
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from hooks.batch_merger import _git, _resolve_branch, merge_worktree_to_main
from hooks.json_writer_common import resolve_feature, resolve_workspace
from hooks.parallel_runtime import append_event, load_manifest, run_lock, save_manifest, utc_now
from hooks.plan_json import load_plan_bundle
from hooks.repository_snapshot import resolve_git_root
from hooks.task_runner import TaskRunnerError, _run_validation
from hooks.worktree_manager import create_worktree, remove_worktree


def _resolution_name(run_id: str, batch_id: str) -> str:
    return f"{run_id}-{batch_id}-resolution"


def prepare_resolution(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    repo_path: Path,
    source_branch: str,
    *,
    locked: bool = False,
) -> dict[str, Any]:
    repo = resolve_git_root(repo_path)
    lock = nullcontext() if locked else run_lock(workspace, feature, run_id)
    with lock:
        manifest = load_manifest(workspace, feature, run_id)
        batch = manifest.get("batches", {}).get(batch_id)
        if not isinstance(batch, dict):
            raise ValueError(f"parallel_batch_not_found:{batch_id}")
        ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
        binding = manifest.get("repositories", {}).get(ref, {})
        expected = binding.get("headSha") if isinstance(binding, dict) else None
        actual = _git(repo, "rev-parse", "HEAD").stdout.strip()
        if not expected or actual != expected:
            raise ValueError(f"parallel_repository_head_sha_mismatch:{ref}")
        if _git(repo, "status", "--porcelain").stdout.strip():
            raise ValueError(f"parallel_main_worktree_dirty:{ref}")
        if _git(repo, "rev-parse", "--verify", source_branch).returncode != 0:
            raise ValueError(f"parallel_source_branch_not_found:{source_branch}")
        tree_name = _resolution_name(run_id, batch_id)
        branch_name = f"autodev/{feature}/{run_id}/{ref}/{batch_id}-resolution"
    created = create_worktree(repo, tree_name, actual, branch_name=branch_name)
    if not created.get("success"):
        return created
    tree = Path(str(created["worktreePath"]))
    merge = _git(tree, "merge", "--no-ff", "--no-edit", source_branch)
    conflicts = _git(tree, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
    if merge.returncode == 0:
        # A probe race can produce a clean merge; leave the tree for the normal
        # completion command so the audit trail remains uniform.
        conflicts = []
    elif not conflicts:
        remove_worktree(repo, tree_name, force=True)
        return {"success": False, "error": merge.stderr.strip() or "resolution_merge_failed"}
    record = {
        "status": "needs_resolution",
        "worktreePath": str(tree),
        "branchName": branch_name,
        "sourceBranch": source_branch,
        "baseSha": actual,
        "conflicts": sorted(conflicts),
        "resolutionCommitSha": None,
    }
    lock = nullcontext() if locked else run_lock(workspace, feature, run_id)
    with lock:
        manifest = load_manifest(workspace, feature, run_id)
        batch = manifest["batches"][batch_id]
        batch["resolution"] = record
        batch["status"] = "needs_resolution"
        manifest["status"] = "needs_resolution"
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "conflict_resolution_prepared", batchId=batch_id, conflicts=conflicts)
    return {"success": True, "batchId": batch_id, **record}


def complete_resolution(workspace: Path, feature: str, run_id: str, batch_id: str, *, message: str | None = None) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = manifest.get("batches", {}).get(batch_id)
        if not isinstance(batch, dict) or not isinstance(batch.get("resolution"), dict):
            raise ValueError(f"parallel_resolution_not_found:{batch_id}")
        resolution = batch["resolution"]
        tree = Path(str(resolution.get("worktreePath", "")))
        if not tree.is_dir():
            raise ValueError(f"parallel_resolution_worktree_missing:{batch_id}")
        unmerged = _git(tree, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
        if unmerged:
            return {"success": False, "error": "parallel_resolution_unmerged_files", "files": unmerged}
        ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
        bundle = load_plan_bundle(workspace / ".autobizdevops" / "features" / feature)
        plan_batch = bundle.batches.get(batch_id, {})
        compile_commands = [
            command for command in (plan_batch.get("batchValidation", {}) or {}).get("commands", [])
            if isinstance(command, dict) and command.get("kind") == "compile" and command.get("required") is True
        ]
        compile_results: list[dict[str, Any]] = []
        for command in compile_commands:
            try:
                exit_code, output = _run_validation(
                    command,
                    {ref: tree, "default": tree},
                    run_id=run_id,
                    batch_id=batch_id,
                )
            except (TaskRunnerError, OSError) as exc:
                exit_code, output = 1, str(exc)
            compile_results.append({
                "commandId": command.get("id"),
                "passed": exit_code == 0,
                "outputTail": output[-4000:],
            })
            if exit_code != 0:
                resolution.update({
                    "status": "compile_failed",
                    "compile": {"passed": False, "commands": compile_results},
                    "compileFailedAt": utc_now(),
                })
                batch["status"] = "needs_resolution"
                manifest["status"] = "needs_resolution"
                save_manifest(workspace, feature, run_id, manifest)
                return {"success": False, "error": "parallel_resolution_compile_failed", "compile": compile_results}
        status = _git(tree, "status", "--porcelain").stdout.splitlines()
        changed_paths = [line[3:] for line in status if len(line) > 3]
        forbidden = [
            path for path in changed_paths
            if path.startswith((".autobizdevops/", ".parallel-runs/")) or path == "BATCH_HANDOFF.json"
        ]
        if forbidden:
            return {
                "success": False,
                "error": "parallel_resolution_artifact_changes_forbidden",
                "files": forbidden,
            }
        if status:
            add = _git(tree, "add", "-A")
            if add.returncode != 0:
                return {"success": False, "error": add.stderr.strip() or "parallel_resolution_stage_failed"}
            commit = _git(tree, "commit", "-m", message or f"autodev: resolve conflict {feature} {batch_id}")
            if commit.returncode != 0:
                return {"success": False, "error": commit.stderr.strip() or "parallel_resolution_commit_failed"}
        head = _git(tree, "rev-parse", "HEAD").stdout.strip()
        if not head:
            raise ValueError("parallel_resolution_commit_unavailable")
        resolution["status"] = "resolved"
        resolution["resolutionCommitSha"] = head
        resolution["resolvedFiles"] = sorted(resolution.get("conflicts", []))
        resolution["compile"] = {"passed": True, "commands": compile_results}
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "conflict_resolution_completed", batchId=batch_id, commitSha=head)
    return {"success": True, "batchId": batch_id, "resolutionCommitSha": head, "worktreePath": str(tree)}


def merge_resolution(workspace: Path, feature: str, run_id: str, batch_id: str) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = manifest.get("batches", {}).get(batch_id)
        if not isinstance(batch, dict) or not isinstance(batch.get("resolution"), dict):
            raise ValueError(f"parallel_resolution_not_found:{batch_id}")
        resolution = batch["resolution"]
        if resolution.get("status") != "resolved":
            raise ValueError(f"parallel_resolution_not_completed:{batch_id}")
        ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
        repository = manifest.get("repositories", {}).get(ref, {})
        root = Path(str(repository.get("gitRoot")))
        branch = str(resolution.get("branchName"))
        expected = repository.get("headSha")
        result = merge_worktree_to_main(root, str(resolution.get("worktreePath")), base_sha=expected if isinstance(expected, str) else None)
        if not result.get("success"):
            return result
        repository["headSha"] = result.get("commitSha")
        batch.update({"status": "merged", "mergeCommitSha": result.get("commitSha")})
        resolution["status"] = "merged"
        resolution["mergeCommitSha"] = result.get("commitSha")
        manifest["status"] = "running"
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "conflict_resolution_merged", batchId=batch_id, commitSha=result.get("commitSha"))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve a parallel Code merge conflict")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "complete", "merge"):
        item = sub.add_parser(command)
        item.add_argument("--workspace")
        item.add_argument("--feature", required=True)
        item.add_argument("--run-id", required=True)
        item.add_argument("--batch-id", required=True)
        if command == "prepare":
            item.add_argument("--repo", required=True)
            item.add_argument("--source-branch", required=True)
        if command == "complete":
            item.add_argument("--message")
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        if args.command == "prepare":
            result = prepare_resolution(workspace, feature, args.run_id, args.batch_id, Path(args.repo), args.source_branch)
        elif args.command == "complete":
            result = complete_resolution(workspace, feature, args.run_id, args.batch_id, message=args.message)
        else:
            result = merge_resolution(workspace, feature, args.run_id, args.batch_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("success") else 1
    except (ValueError, OSError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
