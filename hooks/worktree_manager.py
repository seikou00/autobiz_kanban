#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Worktree 生命周期管理：创建、验证、清理。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
import re


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.repository_snapshot import resolve_git_root  # noqa: E402
from hooks.parallel_runtime import append_event, check_lease, load_manifest, run_lock, save_manifest  # noqa: E402


WORKTREE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_worktree_name(name: str) -> str:
    if not WORKTREE_NAME_RE.fullmatch(name):
        raise ValueError(f"invalid_worktree_name:{name}")
    return name


def _worktree_directory_is_ignored(git_root: Path) -> bool:
    probe = ".worktrees/.autodev-worktree-ignore-probe"
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", probe],
        cwd=git_root,
        capture_output=True,
    )
    return result.returncode == 0


def create_worktree(
    repo_path: Path,
    worktree_name: str,
    base_branch: str | None = None,
    *,
    branch_name: str | None = None,
) -> dict[str, Any]:
    """创建 Git Worktree。

    Args:
        repo_path: Git 仓库路径
        worktree_name: Worktree 名称（用于分支和目录名）
        base_branch: 基准分支（默认当前分支）

    Returns:
        {
            "success": bool,
            "worktreePath": str,
            "branchName": str,
            "error": str | None
        }
    """
    try:
        git_root = resolve_git_root(repo_path)
        worktree_name = _safe_worktree_name(worktree_name)
        worktrees_dir = git_root / ".worktrees"
        worktree_path = worktrees_dir / worktree_name

        # 确保 .worktrees 目录被 git 忽略
        if not _worktree_directory_is_ignored(git_root):
            return {
                "success": False,
                "worktreePath": None,
                "branchName": None,
                "error": "worktree_dir_not_ignored",
            }

        # 创建 worktrees 目录
        worktrees_dir.mkdir(parents=True, exist_ok=True)

        # Never discard a prior run implicitly.  A caller must diagnose or
        # clean it explicitly before reusing the name.
        if worktree_path.exists():
            return {
                "success": False,
                "worktreePath": str(worktree_path),
                "branchName": None,
                "error": "worktree_already_exists",
            }

        # 确定基准分支
        if not base_branch:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=git_root,
                capture_output=True,
                text=True
            )
            base_branch = result.stdout.strip() or "HEAD"

        # 生成分支名
        branch_name = branch_name or f"worktree/{worktree_name}"
        if branch_name.startswith("-") or ".." in branch_name or branch_name.endswith("/"):
            return {
                "success": False,
                "worktreePath": None,
                "branchName": None,
                "error": "invalid_branch_name",
            }

        branch_exists = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
            cwd=git_root,
            capture_output=True,
        )
        if branch_exists.returncode == 0:
            return {
                "success": False,
                "worktreePath": None,
                "branchName": branch_name,
                "error": "worktree_branch_already_exists",
            }

        # 创建 worktree
        result = subprocess.run(
            ["git", "worktree", "add", str(worktree_path), "-b", branch_name, base_branch],
            cwd=git_root,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            return {
                "success": False,
                "worktreePath": None,
                "branchName": None,
                "error": f"git_worktree_add_failed:{result.stderr}"
            }

        return {
            "success": True,
            "worktreePath": str(worktree_path),
            "branchName": branch_name,
            "error": None
        }

    except Exception as e:
        return {
            "success": False,
            "worktreePath": None,
            "branchName": None,
            "error": f"exception:{str(e)}"
        }


def remove_worktree(repo_path: Path, worktree_name: str, force: bool = False) -> dict[str, Any]:
    """删除 Worktree。

    Args:
        repo_path: Git 仓库路径
        worktree_name: Worktree 名称
        force: 是否强制删除（忽略未提交的变更）

    Returns:
        {
            "success": bool,
            "error": str | None
        }
    """
    try:
        git_root = resolve_git_root(repo_path)
        worktree_name = _safe_worktree_name(worktree_name)
        worktree_path = git_root / ".worktrees" / worktree_name
        branch_name = None
        listing = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=git_root, capture_output=True, text=True,
        )
        current_path = None
        for line in listing.stdout.splitlines():
            if line.startswith("worktree "):
                current_path = Path(line[9:])
            elif line.startswith("branch ") and current_path == worktree_path:
                branch_name = line[7:].removeprefix("refs/heads/")

        if not worktree_path.exists():
            return {"success": True, "error": None}

        # 删除 worktree
        cmd = ["git", "worktree", "remove", str(worktree_path)]
        if force:
            cmd.append("--force")

        result = subprocess.run(
            cmd,
            cwd=git_root,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            return {
                "success": False,
                "error": f"git_worktree_remove_failed:{result.stderr}"
            }

        # 删除关联的分支
        if branch_name:
            subprocess.run(["git", "branch", "-D", branch_name], cwd=git_root, capture_output=True)

        return {"success": True, "error": None}

    except Exception as e:
        return {
            "success": False,
            "error": f"exception:{str(e)}"
        }


def create_parallel_worktree(
    artifact_workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    repo_path: Path | None,
    owner_token: str | None = None,
) -> dict[str, Any]:
    """Create and persist a batch worktree based on the run's fixed SHA."""
    with run_lock(artifact_workspace, feature, run_id):
        manifest = load_manifest(artifact_workspace, feature, run_id)
        batch = manifest.get("batches", {}).get(batch_id)
        if not isinstance(batch, dict):
            return {"success": False, "error": f"parallel_batch_not_found:{batch_id}"}
        if not owner_token or not check_lease(artifact_workspace, feature, run_id, batch_id, owner_token):
            return {"success": False, "error": f"parallel_batch_lease_invalid:{batch_id}"}
        repository_ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
        repository = manifest.get("repositories", {}).get(repository_ref)
        if not isinstance(repository, dict) or not isinstance(repository.get("gitRoot"), str):
            return {"success": False, "error": f"parallel_repository_binding_missing:{repository_ref}"}
        git_root = Path(repository["gitRoot"])
        if repo_path is not None and resolve_git_root(repo_path) != git_root:
            return {"success": False, "error": f"parallel_repository_binding_mismatch:{repository_ref}"}
        base_sha = repository.get("headSha") or repository.get("baseSha")
        if not isinstance(base_sha, str) or not base_sha:
            return {"success": False, "error": f"parallel_base_sha_unavailable:{repository_ref}"}
        status = subprocess.run(["git", "status", "--porcelain"], cwd=git_root, capture_output=True, text=True)
        if status.returncode != 0:
            return {"success": False, "error": f"parallel_repository_unavailable:{repository_ref}"}
        if status.stdout.strip():
            return {"success": False, "error": f"parallel_main_worktree_dirty:{repository_ref}"}
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_root, capture_output=True, text=True)
        if head.returncode != 0 or head.stdout.strip() != base_sha:
            return {"success": False, "error": f"parallel_main_head_changed:{repository_ref}"}
        name = f"{run_id}-{batch_id}"
        branch_name = f"autodev/{feature}/{run_id}/{repository_ref}/{batch_id}"
        # Keep the Git check-and-create and manifest update under one run lock.
        result = create_worktree(git_root, name, base_sha, branch_name=branch_name)
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
        append_event(artifact_workspace, feature, run_id, "worktree_created", batchId=batch_id, path=result["worktreePath"], branchName=result["branchName"])
    return result


def seal_parallel_batch(
    artifact_workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    repo_path: Path | None,
    owner_token: str,
) -> dict[str, Any]:
    """Commit a compiled batch worktree and bind the commit to its manifest."""
    if not check_lease(artifact_workspace, feature, run_id, batch_id, owner_token):
        return {"success": False, "error": f"parallel_batch_lease_invalid:{batch_id}"}
    with run_lock(artifact_workspace, feature, run_id):
        manifest = load_manifest(artifact_workspace, feature, run_id)
        batch = manifest.get("batches", {}).get(batch_id)
        if not isinstance(batch, dict):
            return {"success": False, "error": f"parallel_batch_not_found:{batch_id}"}
        if batch.get("status") != "ready_to_merge" or batch.get("compileStatus") != "passed":
            return {"success": False, "error": f"parallel_batch_not_ready_to_seal:{batch_id}"}
        worktree_path = Path(str(batch.get("worktreePath") or ""))
        repository_ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
        repository = manifest.get("repositories", {}).get(repository_ref)
        if not isinstance(repository, dict) or not isinstance(repository.get("gitRoot"), str):
            return {"success": False, "error": f"parallel_repository_binding_missing:{repository_ref}"}
        repo_root = Path(repository["gitRoot"])
        if repo_path is not None and resolve_git_root(repo_path) != repo_root:
            return {"success": False, "error": f"parallel_repository_binding_mismatch:{repository_ref}"}
        if not worktree_path.is_dir() or worktree_path.resolve().parent != repo_root / ".worktrees":
            return {"success": False, "error": f"parallel_worktree_invalid:{batch_id}"}
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=worktree_path, capture_output=True, text=True)
        if branch.returncode != 0 or branch.stdout.strip() != batch.get("branchName"):
            return {"success": False, "error": f"parallel_worktree_branch_mismatch:{batch_id}"}
        status = subprocess.run(["git", "status", "--porcelain"], cwd=worktree_path, capture_output=True, text=True)
        changes = [line[3:] for line in status.stdout.splitlines() if len(line) > 3]
        forbidden = [path for path in changes if path.startswith((".autobizdevops/", ".parallel-runs/", "BATCH_HANDOFF.json"))]
        if forbidden:
            return {"success": False, "error": "parallel_batch_artifact_changes_forbidden", "files": forbidden}
        if changes:
            add = subprocess.run(["git", "add", "-A"], cwd=worktree_path, capture_output=True, text=True)
            if add.returncode != 0:
                return {"success": False, "error": f"parallel_batch_stage_failed:{add.stderr.strip()}"}
            commit = subprocess.run(["git", "commit", "-m", f"autodev: implement {feature} {batch_id}"], cwd=worktree_path, capture_output=True, text=True)
            if commit.returncode != 0:
                return {"success": False, "error": f"parallel_batch_commit_failed:{commit.stderr.strip()}"}
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree_path, capture_output=True, text=True)
        if sha.returncode != 0:
            return {"success": False, "error": "parallel_batch_commit_sha_unavailable"}
        batch["commitSha"] = sha.stdout.strip()
        save_manifest(artifact_workspace, feature, run_id, manifest)
    append_event(artifact_workspace, feature, run_id, "batch_sealed", batchId=batch_id, commitSha=sha.stdout.strip(), changedFiles=changes)
    return {"success": True, "batchId": batch_id, "commitSha": sha.stdout.strip(), "changedFiles": changes}


def list_worktrees(repo_path: Path) -> dict[str, Any]:
    """列出所有 worktrees。

    Returns:
        {
            "worktrees": [
                {
                    "path": str,
                    "branch": str,
                    "commit": str
                }
            ]
        }
    """
    try:
        git_root = resolve_git_root(repo_path)

        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=git_root,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            return {"worktrees": [], "error": result.stderr}

        worktrees = []
        current = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                if current:
                    worktrees.append(current)
                    current = {}
            elif line.startswith("worktree "):
                current["path"] = line.split(" ", 1)[1]
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1]
            elif line.startswith("HEAD "):
                current["commit"] = line.split(" ", 1)[1]

        if current:
            worktrees.append(current)

        return {"worktrees": worktrees, "error": None}

    except Exception as e:
        return {"worktrees": [], "error": str(e)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Worktree 生命周期管理")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create 命令
    create_parser = subparsers.add_parser("create", help="创建 worktree")
    create_parser.add_argument("--repo", help="仓库路径；并行模式下可从 manifest 推导")
    create_parser.add_argument("--name", help="Worktree 名称")
    create_parser.add_argument("--base-branch", help="基准分支")
    create_parser.add_argument("--branch-name", help="显式分支名")
    create_parser.add_argument("--artifact-workspace", help="并行运行的产物工作区")
    create_parser.add_argument("--feature", help="并行运行的 Feature")
    create_parser.add_argument("--run-id", help="并行运行 ID")
    create_parser.add_argument("--batch-id", help="并行 Batch ID")
    create_parser.add_argument("--owner-token", help="并行 Batch lease token")

    seal_parser = subparsers.add_parser("seal", help="提交已通过编译的并行 Batch")
    seal_parser.add_argument("--repo", help="业务仓库路径；默认从 manifest 推导")
    seal_parser.add_argument("--artifact-workspace", required=True)
    seal_parser.add_argument("--feature", required=True)
    seal_parser.add_argument("--run-id", required=True)
    seal_parser.add_argument("--batch-id", required=True)
    seal_parser.add_argument("--owner-token", required=True)

    # remove 命令
    remove_parser = subparsers.add_parser("remove", help="删除 worktree")
    remove_parser.add_argument("--repo", required=True, help="仓库路径")
    remove_parser.add_argument("--name", required=True, help="Worktree 名称")
    remove_parser.add_argument("--force", action="store_true", help="强制删除")

    # list 命令
    list_parser = subparsers.add_parser("list", help="列出 worktrees")
    list_parser.add_argument("--repo", required=True, help="仓库路径")

    args = parser.parse_args()

    if args.command == "create":
        if any(value is not None for value in (args.artifact_workspace, args.feature, args.run_id, args.batch_id)):
            if not all((args.artifact_workspace, args.feature, args.run_id, args.batch_id)):
                result = {"success": False, "error": "parallel_worktree_arguments_incomplete"}
            else:
                result = create_parallel_worktree(
                    Path(args.artifact_workspace),
                    args.feature,
                    args.run_id,
                    args.batch_id,
                    Path(args.repo) if args.repo else None,
                    args.owner_token,
                )
        else:
            result = create_worktree(Path(args.repo), args.name, args.base_branch, branch_name=args.branch_name) if args.name and args.repo else {"success": False, "error": "name_and_repo_required"}
    elif args.command == "remove":
        result = remove_worktree(Path(args.repo), args.name, args.force)
    elif args.command == "seal":
        result = seal_parallel_batch(
            Path(args.artifact_workspace), args.feature, args.run_id, args.batch_id,
            Path(args.repo) if args.repo else None, args.owner_token,
        )
    elif args.command == "list":
        result = list_worktrees(Path(args.repo))
    else:
        result = {"error": "unknown_command"}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("success"):
            print("✓ 操作成功")
            if "worktreePath" in result:
                print(f"  路径: {result['worktreePath']}")
                print(f"  分支: {result['branchName']}")
        else:
            print(f"✗ 操作失败: {result.get('error', 'unknown')}")

    return 0 if result.get("success") or args.command == "list" else 1


if __name__ == "__main__":
    sys.exit(main())
