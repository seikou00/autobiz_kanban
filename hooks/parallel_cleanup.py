#!/usr/bin/env python3
"""Bounded, recoverable cleanup after deliveries have finished merging."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_kernel import FileLock
from hooks.json_writer_common import resolve_feature, resolve_workspace
from hooks.parallel_runtime import load_manifest, run_dir, run_lock, save_manifest, utc_now


def pending(manifest: dict) -> list[dict]:
    return [{key: job.get(key) for key in ("jobId", "batchId", "kind", "status", "error")}
            for job in manifest.get("cleanupQueue", {}).values() if job.get("status") != "completed"]


def enqueue(manifest: dict, repository_ref: str, wave: int) -> list[str]:
    """Called in the same run-lock transaction that publishes merged state."""
    train = manifest["mergeTrains"][f"{repository_ref}:wave-{wave:03d}"]
    queue = manifest.setdefault("cleanupQueue", {})
    jobs = [("candidate", None, train)] + [
        ("delivery", batch_id, manifest["batches"][batch_id]) for batch_id in train["batchIds"]]
    ids = []
    for kind, batch_id, source in jobs:
        identity = f"{manifest['runId']}:{repository_ref}:{wave}:{kind}:{batch_id or ''}"
        job_id = hashlib.sha256(identity.encode()).hexdigest()[:32]
        queue.setdefault(job_id, {
            "jobId": job_id, "kind": kind, "batchId": batch_id, "repositoryRef": repository_ref,
            "wave": wave, "worktreePath": source.get("worktreePath"), "branchName": source.get("branchName"),
            "expectedSha": source.get("candidateSha") if kind == "candidate" else source.get("commitSha"),
            "mergeCommitSha": train["candidateSha"], "dispatchGeneration": source.get("dispatchGeneration"),
            "status": "pending", "attempts": 0, "createdAt": utc_now(),
        })
        ids.append(job_id)
    train["cleanup"] = {"status": "pending", "jobIds": ids, "at": utc_now(), "errors": []}
    return ids


def _git(repo: Path, deadline: float, *args: str) -> subprocess.CompletedProcess:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("cleanup_budget_exhausted")
    command = ["git", *args]
    process = subprocess.Popen(command, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace", start_new_session=os.name != "nt")
    try:
        out, err = process.communicate(timeout=min(30, remaining))
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=10)
        raise TimeoutError("cleanup_git_timeout")
    return subprocess.CompletedProcess(command, process.returncode, out, err)


def _remove(manifest: dict, job: dict, deadline: float) -> None:
    if manifest.get("status") in {"cancelled", "rolled_back", "cleaned"}:
        raise ValueError("cleanup_run_inactive")
    binding = manifest["repositories"][job["repositoryRef"]]
    repo = Path(binding["gitRoot"])
    train = manifest["mergeTrains"][f"{job['repositoryRef']}:wave-{job['wave']:03d}"]
    source = train if job["kind"] == "candidate" else manifest["batches"][job["batchId"]]
    if train.get("status") != "promoted" or train.get("candidateSha") != job["mergeCommitSha"]:
        raise ValueError("cleanup_promotion_not_complete")
    if job["kind"] == "delivery" and (source.get("status") != "merged" or source.get("lease")
            or source.get("dispatchGeneration") != job["dispatchGeneration"]):
        raise ValueError("cleanup_delivery_in_use_or_changed")
    if (manifest.get("isolation") or {}).get("owner") != "plugin":
        raise ValueError("cleanup_owner_unknown")
    raw_path, branch = job.get("worktreePath"), job.get("branchName")
    if not raw_path or not branch or not job.get("expectedSha"):
        raise ValueError("cleanup_identity_missing")
    if source.get("worktreePath") not in {raw_path, None} or source.get("branchName") not in {branch, None}:
        raise ValueError("cleanup_path_reused")
    if source.get("worktreePath") is None and source.get("removedWorktreePath") != raw_path:
        raise ValueError("cleanup_ownership_changed")
    protected = {binding.get("baseBranch"), (binding.get("integration") or {}).get("branchName")}
    if branch in protected:
        raise ValueError("cleanup_protected_branch")
    path = Path(raw_path)
    for validation in manifest.get("validationBatches", {}).values():
        if validation.get("status") == "running" and any(
                Path(value).resolve() == path.resolve() for value in (validation.get("worktrees") or {}).values()):
            raise ValueError("cleanup_worktree_in_validation_use")
    protected_paths = {repo.resolve(), Path((binding.get("integration") or {}).get("worktreePath") or repo).resolve()}
    if path.resolve() in protected_paths:
        raise ValueError("cleanup_protected_worktree")
    if path.is_symlink():
        raise ValueError("cleanup_symlink_refused")
    if path.exists():
        head = _git(path, deadline, "rev-parse", "HEAD")
        branch_result = _git(path, deadline, "symbolic-ref", "--quiet", "--short", "HEAD")
        common = _git(path, deadline, "rev-parse", "--git-common-dir")
        root_common = _git(repo, deadline, "rev-parse", "--git-common-dir")
        if (branch_result.returncode or branch_result.stdout.strip() != branch or common.returncode or root_common.returncode
                or (path / common.stdout.strip()).resolve() != (repo / root_common.stdout.strip()).resolve()):
            raise ValueError("cleanup_repository_or_branch_changed")
        clean = _git(path, deadline, "status", "--porcelain", "--untracked-files=all")
        if head.returncode or head.stdout.strip() != job["expectedSha"] or clean.returncode or clean.stdout.strip():
            raise ValueError("cleanup_worktree_changed")
        removed = _git(repo, deadline, "worktree", "remove", str(path))
        if removed.returncode:
            raise ValueError("cleanup_worktree_remove_failed:" + removed.stderr.strip())
    found = _git(repo, deadline, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if found.returncode == 0:
        if found.stdout.strip() != job["expectedSha"]:
            raise ValueError("cleanup_branch_changed")
        target = Path((binding.get("integration") or {}).get("worktreePath") or binding["gitRoot"])
        deleted = _git(target, deadline, "branch", "-d", branch)
        if deleted.returncode:
            raise ValueError("cleanup_branch_remove_failed:" + deleted.stderr.strip())
    elif found.returncode != 1:
        raise ValueError("cleanup_branch_lookup_failed:" + found.stderr.strip())


def drain(workspace: Path, feature: str, run_id: str, budget_seconds: int = 120) -> dict:
    started = time.monotonic()
    deadline = started + max(1, min(budget_seconds, 120))
    cleaned, errors = [], []
    with FileLock(run_dir(workspace, feature, run_id) / ".cleanup.lock", timeout=max(0, deadline - time.monotonic())):
        manifest = load_manifest(workspace, feature, run_id)
        if manifest.get("status") in {"cancelled", "rolled_back", "cleaned"}:
            return {"success": True, "status": "inactive", "cleanupPending": pending(manifest)}
        for job_id in list(manifest.get("cleanupQueue", {})):
            if time.monotonic() >= deadline:
                break
            with run_lock(workspace, feature, run_id, timeout=max(0, deadline - time.monotonic())):
                manifest = load_manifest(workspace, feature, run_id)
                job = manifest["cleanupQueue"][job_id]
                if job["status"] == "completed":
                    continue
                job.update(status="running", attempts=job["attempts"] + 1, startedAt=utc_now())
                save_manifest(workspace, feature, run_id, manifest)
            error = None
            try:
                # Exact owned paths only: directory deletion must not hold the
                # publication lock and delay final workspace sync or another run.
                with FileLock(run_dir(workspace, feature, run_id) / "cleanup-locks" / (job_id + ".lock"),
                              timeout=max(0, deadline - time.monotonic())):
                    fresh = load_manifest(workspace, feature, run_id)
                    _remove(fresh, job, deadline)
            except (OSError, ValueError, subprocess.SubprocessError, TimeoutError) as exc:
                error = str(exc)
                errors.append({"jobId": job_id, "error": error})
            with run_lock(workspace, feature, run_id, timeout=max(0, deadline - time.monotonic())):
                manifest = load_manifest(workspace, feature, run_id)
                current = manifest["cleanupQueue"][job_id]
                current.update(status="retryable" if error else "completed", error=error, completedAt=utc_now())
                if not error:
                    cleaned.append(job_id)
                    if job["kind"] == "delivery":
                        batch = manifest["batches"][job["batchId"]]
                        batch.update(removedWorktreePath=job["worktreePath"], removedBranchName=job["branchName"],
                                     worktreePath=None, branchName=None, worktreeRemovedAt=utc_now())
                record = manifest["mergeTrains"][f"{job['repositoryRef']}:wave-{job['wave']:03d}"]
                ids = (record.get("cleanup") or {}).get("jobIds", [])
                record["cleanup"]["status"] = "completed" if all(
                    manifest["cleanupQueue"][key]["status"] == "completed" for key in ids) else "pending"
                record["cleanup"]["errors"] = [manifest["cleanupQueue"][key]["error"] for key in ids
                                                  if manifest["cleanupQueue"][key].get("error")]
                save_manifest(workspace, feature, run_id, manifest)
        manifest = load_manifest(workspace, feature, run_id)
    return {"success": True, "status": "pending" if pending(manifest) else "completed",
            "cleanedJobIds": cleaned, "errors": errors, "cleanupPending": pending(manifest),
            "durationMs": round((time.monotonic() - started) * 1000)}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["drain"])
    for name in ("workspace", "feature", "run-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--budget-seconds", type=int, default=120)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = drain(resolve_workspace(args.workspace), resolve_feature(args.feature), args.run_id, args.budget_seconds)
    except (OSError, ValueError) as exc:
        result = {"success": False, "status": "failed", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
