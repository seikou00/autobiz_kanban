#!/usr/bin/env python3
"""Deterministic, guarded merging of parallel Code batch worktrees."""

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

from hooks.json_writer_common import resolve_feature, resolve_workspace  # noqa: E402
from hooks.parallel_runtime import (  # noqa: E402
    append_event,
    load_manifest,
    run_lock,
    save_manifest,
)
from hooks.repository_snapshot import resolve_git_root  # noqa: E402


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def detect_conflicts(batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    file_to_batches: dict[str, list[str]] = {}
    for batch in batches:
        batch_id = str(batch.get("id", ""))
        for file_path in batch.get("changedFiles", []):
            if isinstance(file_path, str) and file_path:
                file_to_batches.setdefault(file_path, []).append(batch_id)
    return [
        {"file": path, "batches": ids}
        for path, ids in sorted(file_to_batches.items())
        if len(ids) > 1
    ]


def _worktree_records(repo: Path) -> list[dict[str, str]]:
    result = _git(repo, "worktree", "list", "--porcelain")
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines() + [""]:
        if line.startswith("worktree "):
            if current:
                records.append(current)
            current = {"path": line[9:]}
        elif line.startswith("branch "):
            current["branch"] = line[7:].removeprefix("refs/heads/")
        elif line == "" and current:
            records.append(current)
            current = {}
    return records


def _resolve_branch(repo: Path, name: str) -> tuple[Path | None, str | None]:
    records = _worktree_records(repo)
    for record in records:
        path = Path(record.get("path", ""))
        branch = record.get("branch")
        if path.name == name or str(path) == name:
            return path, branch
    branch_candidates = [f"worktree/{name}", name]
    for branch in branch_candidates:
        if _git(repo, "rev-parse", "--verify", branch).returncode == 0:
            return None, branch
    return None, None


def _dirty(repo: Path) -> list[str]:
    return [line for line in _git(repo, "status", "--porcelain").stdout.splitlines() if line]


def preflight_merge(repo_path: Path, *, base_sha: str | None = None) -> dict[str, Any]:
    repo = resolve_git_root(repo_path)
    dirty = _dirty(repo)
    if dirty:
        return {"ok": False, "error": "main_worktree_dirty", "changes": dirty}
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if base_sha and head != base_sha:
        return {"ok": False, "error": "main_head_changed", "expected": base_sha, "actual": head}
    return {"ok": True, "head": head}


def merge_worktree_to_main(repo_path: Path, worktree_name: str, target_branch: str | None = None, *, base_sha: str | None = None) -> dict[str, Any]:
    try:
        repo = resolve_git_root(repo_path)
        preflight = preflight_merge(repo, base_sha=base_sha)
        if not preflight.get("ok"):
            return {"success": False, "mergedFiles": [], "conflicts": [], "error": preflight.get("error"), "details": preflight}
        worktree_path, branch = _resolve_branch(repo, worktree_name)
        if worktree_path is not None and not worktree_path.exists():
            return {"success": False, "mergedFiles": [], "conflicts": [], "error": f"worktree_not_found:{worktree_name}"}
        if not branch:
            return {"success": False, "mergedFiles": [], "conflicts": [], "error": f"worktree_branch_not_found:{worktree_name}"}
        if _git(repo, "rev-parse", "--verify", branch).returncode != 0:
            return {"success": False, "mergedFiles": [], "conflicts": [], "error": f"worktree_branch_not_found:{branch}"}
        changed = _git(repo, "diff", "--name-only", "HEAD", branch).stdout.splitlines()
        target = target_branch or _git(repo, "branch", "--show-current").stdout.strip()
        merge_args = ["merge", "--no-ff", "--no-edit", branch]
        if target and target != "HEAD":
            checkout = _git(repo, "branch", "--show-current")
            if checkout.stdout.strip() != target:
                return {"success": False, "mergedFiles": [], "conflicts": [], "error": f"target_branch_mismatch:{target}"}
        result = _git(repo, *merge_args)
        if result.returncode != 0:
            conflicts = _git(repo, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
            _git(repo, "merge", "--abort")
            return {"success": False, "mergedFiles": [], "conflicts": conflicts, "error": "merge_conflict" if conflicts else result.stderr.strip() or "merge_failed"}
        sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        return {"success": True, "mergedFiles": changed, "conflicts": [], "commitSha": sha, "error": None, "branch": branch}
    except (OSError, ValueError) as exc:
        return {"success": False, "mergedFiles": [], "conflicts": [], "error": str(exc)}


def commit_merge(repo_path: Path, batch_id: str, message: str | None = None) -> dict[str, Any]:
    repo = resolve_git_root(repo_path)
    # merge_worktree_to_main creates the merge commit itself.  This function is
    # retained for callers of the old API and only commits an outstanding merge.
    if _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode != 0:
        sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        return {"success": True, "commitSha": sha, "error": None}
    result = _git(repo, "commit", "-m", message or f"Merge batch {batch_id} from workflow")
    if result.returncode != 0:
        return {"success": False, "commitSha": None, "error": result.stderr.strip() or "commit_failed"}
    return {"success": True, "commitSha": _git(repo, "rev-parse", "HEAD").stdout.strip(), "error": None}


def sequential_merge_batches(repo_path: Path, worktree_names: list[str], batch_ids: list[str], *, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    if len(worktree_names) != len(batch_ids):
        return {"success": False, "merged": [], "failed": [{"error": "worktree_batch_length_mismatch"}], "totalConflicts": 0}
    merged: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    base_sha = manifest.get("baseSha") if isinstance(manifest, dict) else None
    for worktree_name, batch_id in zip(worktree_names, batch_ids):
        result = merge_worktree_to_main(repo_path, worktree_name, base_sha=base_sha if not merged else None)
        if not result["success"]:
            failed.append({"batchId": batch_id, "worktree": worktree_name, "error": result.get("error"), "conflicts": result.get("conflicts", [])})
            break
        merged.append({"batchId": batch_id, "commitSha": result.get("commitSha"), "mergedFiles": result.get("mergedFiles", [])})
    return {"success": not failed, "merged": merged, "failed": failed, "totalConflicts": sum(len(item.get("conflicts", [])) for item in failed)}


def _batch_repository(manifest: dict[str, Any], batch_id: str, fallback: Path | None = None) -> tuple[str, Path, str | None]:
    batch = manifest.get("batches", {}).get(batch_id)
    if not isinstance(batch, dict):
        raise ValueError(f"parallel_batch_not_found:{batch_id}")
    ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
    repository = manifest.get("repositories", {}).get(ref)
    if isinstance(repository, dict) and isinstance(repository.get("gitRoot"), str):
        return ref, Path(repository["gitRoot"]), repository.get("baseSha") if isinstance(repository.get("baseSha"), str) else None
    if fallback is not None:
        return ref or "default", resolve_git_root(fallback), manifest.get("baseSha") if isinstance(manifest.get("baseSha"), str) else None
    raise ValueError(f"parallel_repository_binding_missing:{ref}")


def merge_run(workspace: Path, feature: str, run_id: str, *, repo_path: Path | None = None, batch_ids: list[str] | None = None) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        plan_path = workspace / ".autobizdevops" / "features" / feature
        from hooks.plan_json import load_plan_bundle
        from hooks.parallel_runtime import plan_digest
        if plan_digest(load_plan_bundle(plan_path)) != manifest.get("planDigest"):
            manifest["status"] = "blocked"
            save_manifest(workspace, feature, run_id, manifest)
            return {"success": False, "merged": [], "failed": [{"error": "parallel_plan_digest_changed"}], "totalConflicts": 0}
        ready = {
            bid for bid, item in manifest.get("batches", {}).items()
            if isinstance(item, dict) and item.get("status") == "ready_to_merge"
        }
        if batch_ids:
            ids = batch_ids
        else:
            # Dependency order is primary; independent batches are stable by ID.
            remaining = set(ready)
            ids = []
            while remaining:
                layer = sorted(
                    bid for bid in remaining
                    if all(dep not in remaining for dep in manifest["batches"][bid].get("dependencies", []))
                )
                if not layer:
                    manifest["status"] = "blocked"
                    save_manifest(workspace, feature, run_id, manifest)
                    return {"success": False, "merged": [], "failed": [{"error": "merge_dependency_cycle_or_unready_dependency"}], "totalConflicts": 0}
                ids.extend(layer)
                remaining.difference_update(layer)
        unsealed = [bid for bid in ids if not manifest["batches"].get(bid, {}).get("commitSha")]
        if unsealed:
            manifest["status"] = "blocked"
            save_manifest(workspace, feature, run_id, manifest)
            return {"success": False, "merged": [], "failed": [{"batchId": bid, "error": "parallel_batch_not_sealed"} for bid in unsealed], "totalConflicts": 0}
        # Several workspaceRefs may deliberately point at component directories
        # in one monorepo.  Merge safety and base-SHA tracking are physical
        # repository concerns, so group them by resolved Git root, not ref.
        repositories: dict[str, dict[str, Any]] = {}
        for batch_id in ids:
            ref, root, base_sha = _batch_repository(manifest, batch_id, repo_path)
            key = str(root.resolve())
            binding = manifest.get("repositories", {}).get(ref)
            expected_sha = binding.get("headSha") if isinstance(binding, dict) else None
            if not isinstance(expected_sha, str) or not expected_sha:
                expected_sha = base_sha
            record = repositories.setdefault(key, {"root": root, "headSha": expected_sha, "refs": []})
            if record["headSha"] != expected_sha:
                manifest["status"] = "blocked"
                save_manifest(workspace, feature, run_id, manifest)
                return {
                    "success": False,
                    "merged": [],
                    "failed": [{"repositoryRef": ref, "error": "parallel_repository_head_sha_mismatch"}],
                    "totalConflicts": 0,
                }
            record["refs"].append(ref)
        for record in repositories.values():
            root = record["root"]
            preflight = preflight_merge(root, base_sha=record["headSha"])
            if not preflight.get("ok"):
                manifest["status"] = "blocked"
                save_manifest(workspace, feature, run_id, manifest)
                return {
                    "success": False,
                    "merged": [],
                    "failed": [{"repositoryRefs": sorted(set(record["refs"])), "error": preflight.get("error"), "details": preflight}],
                    "totalConflicts": 0,
                }
        manifest["status"] = "merging"
        save_manifest(workspace, feature, run_id, manifest)
        merged: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for batch_id in ids:
            batch = manifest["batches"][batch_id]
            ref, root, base_sha = _batch_repository(manifest, batch_id, repo_path)
            root_key = str(root.resolve())
            record = repositories[root_key]
            worktree_path, source_branch = _resolve_branch(root, str(batch.get("worktreePath") or batch_id))
            if not source_branch:
                failed.append({
                    "batchId": batch_id,
                    "repositoryRef": ref,
                    "error": "worktree_branch_not_found",
                })
                break
            probe = _git(root, "merge-tree", "--write-tree", record["headSha"], source_branch)
            if probe.returncode != 0:
                from hooks.parallel_conflict_resolver import prepare_resolution
                # Persist the repository head advanced by earlier merges before
                # the resolver reloads the manifest from disk.
                for merged_item in merged:
                    merged_batch = manifest["batches"][merged_item["batchId"]]
                    merged_batch.update({"status": "merged", "mergeCommitSha": merged_item["commitSha"]})
                save_manifest(workspace, feature, run_id, manifest)
                resolution = prepare_resolution(workspace, feature, run_id, batch_id, root, source_branch, locked=True)
                failed.append({
                    "batchId": batch_id,
                    "repositoryRef": ref,
                    "worktree": batch.get("worktreePath"),
                    "error": "merge_conflict_needs_resolution",
                    "conflicts": resolution.get("conflicts", []),
                    "resolution": resolution,
                    "needsResolution": bool(resolution.get("success")),
                })
                if resolution.get("success"):
                    batch["status"] = "needs_resolution"
                    batch["resolution"] = resolution
                else:
                    batch["status"] = "conflict"
                for merged_item in merged:
                    merged_batch = manifest["batches"][merged_item["batchId"]]
                    merged_batch.update({"status": "merged", "mergeCommitSha": merged_item["commitSha"]})
                manifest["status"] = "needs_resolution" if resolution.get("success") else "blocked"
                save_manifest(workspace, feature, run_id, manifest)
                append_event(workspace, feature, run_id, "merge_conflict_detected", batchId=batch_id, conflicts=resolution.get("conflicts", []))
                return {
                    "success": False,
                    "needsResolution": bool(resolution.get("success")),
                    "merged": merged,
                    "failed": failed,
                    "totalConflicts": len(resolution.get("conflicts", [])),
                }
            result = merge_worktree_to_main(
                root,
                str(batch.get("worktreePath") or batch_id),
                base_sha=record["headSha"],
            )
            if not result.get("success"):
                failed.append({
                    "batchId": batch_id,
                    "repositoryRef": ref,
                    "worktree": batch.get("worktreePath"),
                    "error": result.get("error"),
                    "conflicts": result.get("conflicts", []),
                })
                break
            record["headSha"] = result.get("commitSha")
            for binding in manifest.get("repositories", {}).values():
                if isinstance(binding, dict) and isinstance(binding.get("gitRoot"), str) and str(Path(binding["gitRoot"]).resolve()) == root_key:
                    binding["headSha"] = result.get("commitSha")
            merged.append({
                "batchId": batch_id,
                "repositoryRef": ref,
                "gitRoot": root_key,
                "commitSha": result.get("commitSha"),
                "mergedFiles": result.get("mergedFiles", []),
            })
        result = {"success": not failed, "merged": merged, "failed": failed, "totalConflicts": sum(len(item.get("conflicts", [])) for item in failed)}
        for item in merged:
            batch = manifest["batches"][item["batchId"]]
            batch.update({"status": "merged", "mergeCommitSha": item["commitSha"]})
        if result["success"]:
            all_merged = all(
                isinstance(item, dict) and item.get("status") == "merged"
                for item in manifest.get("batches", {}).values()
            )
            manifest["status"] = "verifying" if all_merged else "running"
        else:
            for item in failed:
                if item.get("batchId") in manifest.get("batches", {}):
                    manifest["batches"][item["batchId"]].update({"status": "conflict", "error": item.get("error")})
            manifest["status"] = "blocked"
        save_manifest(workspace, feature, run_id, manifest)
        append_event(workspace, feature, run_id, "merge_completed", result=result)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge parallel Code batches")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("command", nargs="?", choices=("detect-conflicts", "merge"), default="merge")
    parser.add_argument("--batches")
    parser.add_argument("--workspace")
    parser.add_argument("--feature")
    parser.add_argument("--run-id")
    parser.add_argument("--repo-path", help="旧单仓库兼容参数；并行 run 从 manifest 读取仓库绑定")
    parser.add_argument("--worktree", action="append", dest="worktrees")
    parser.add_argument("--batch-id", action="append", dest="batch_ids")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "detect-conflicts":
            batches = json.loads(args.batches or "[]")
            conflicts = detect_conflicts(batches if isinstance(batches, list) else [])
            print(json.dumps({"success": not conflicts, "conflicts": conflicts}, ensure_ascii=False, indent=2))
            return 0 if not conflicts else 1
        if not args.feature:
            raise ValueError("feature_required")
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        repo = Path(args.repo_path) if args.repo_path else None
        if args.preflight:
            if repo is None:
                raise ValueError("repo_path_required_for_preflight")
            print(json.dumps(preflight_merge(repo), ensure_ascii=False, indent=2))
            return 0
        if args.run_id and not args.worktrees and not args.batch_ids:
            result = merge_run(workspace, feature, args.run_id, repo_path=repo)
        else:
            if not args.run_id or not args.worktrees or not args.batch_ids:
                raise ValueError("run_id_worktrees_and_batch_ids_required")
            if repo is None:
                raise ValueError("repo_path_required_for_explicit_merge")
            result = sequential_merge_batches(repo, args.worktrees, args.batch_ids)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("success") else 1
    except (ValueError, OSError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
