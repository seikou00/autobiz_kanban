"""Durable FIFO publication jobs. Queue locks precede run locks.

A publication failure never reopens a delivered Batch. The queue is driven by
Workflow events; retry deadlines survive process and Workflow restarts.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from hooks.evidence_kernel import FileLock
from hooks.parallel_repository import coordinates
from hooks.parallel_runtime import load_manifest, run_lock, save_manifest, utc_now

RETRY_DELAYS = (5, 30, 120)
OWNER_TTL = 15 * 60
DONE = {"synced", "covered"}


def reconcile_locked(manifest: dict, ref: str) -> list[dict]:
    jobs = manifest.setdefault("workspaceSyncQueue", {}).setdefault(ref, [])
    known = {job["targetSha"] for job in jobs}
    # Promotion records give historical deliveries a stable order on upgrade.
    waves = {batch: train.get("wave", 0) for train in manifest.get("mergeTrains", {}).values()
             for batch in train.get("batchIds", [])}
    targets = []
    for batch_id, batch in sorted(manifest.get("batches", {}).items(), key=lambda pair: (waves.get(pair[0], 0), pair[0])):
        batch_ref = batch.get("repositoryRef") or batch.get("workspaceRef")
        canonical = manifest.get("repositories", {}).get(batch_ref, {}).get("integrationRepositoryRef", batch_ref)
        if canonical == ref and batch.get("status") == "merged" and batch.get("mergeCommitSha"):
            targets.append((batch["mergeCommitSha"], batch_id))
    head = manifest["repositories"][ref]["headSha"]
    if targets or jobs or manifest.get("workspaceSync", {}).get(ref, {}).get("lastSyncedSha") != head:
        targets.append((head, None))
    for target, batch_id in targets:
        if target in known:
            if batch_id:
                job = next(job for job in jobs if job["targetSha"] == target)
                if batch_id not in job["batchIds"]:
                    job["batchIds"].append(batch_id)
            continue
        known.add(target)
        jobs.append({"jobId": hashlib.sha256(f"{manifest['runId']}:{ref}:{target}".encode()).hexdigest(),
                     "repositoryRef": ref, "targetSha": target, "batchIds": [batch_id] if batch_id else [],
                     "sequence": len(jobs) + 1, "status": "queued", "attempts": 0, "failures": 0,
                     "createdAt": utc_now(), "errors": []})
    return jobs


def next_job(jobs: list[dict]) -> dict | None:
    remaining = [job for job in jobs if job["status"] not in DONE]
    # Exhausted jobs may be covered by a later cumulative publication.
    return next((job for job in remaining if job["status"] != "failed"), remaining[-1] if remaining else None)


def summary_locked(manifest: dict, ref: str) -> dict:
    saved = manifest.setdefault("workspaceSync", {}).get(ref, {})
    jobs = manifest.get("workspaceSyncQueue", {}).get(ref, [])
    remaining = [job for job in jobs if job["status"] not in DONE]
    head = manifest["repositories"][ref]["headSha"]
    result = {**saved, "repositoryRef": ref, "desiredSha": head, "queue": [dict(job) for job in jobs],
              "pendingBatchIds": list(dict.fromkeys(batch for job in remaining for batch in job["batchIds"])),
              "hasPending": bool(remaining) or saved.get("lastSyncedSha") != head}
    if remaining:
        first = next_job(jobs)
        result.update(status=first["status"], jobId=first["jobId"], targetSha=first["targetSha"],
                      attempts=first["attempts"], retryAfterSeconds=max(0, first.get("nextAttemptEpoch", 0) - time.time()))
        if first["status"] == "running" and saved.get("status") == "conflicted" and saved.get("targetSha") == first["targetSha"]:
            result["status"] = "conflicted"
        result.pop("error", None)
        if first.get("error"):
            result["error"] = first["error"]
    else:
        result.update(status="synced" if saved.get("lastSyncedSha") == head else "pending", targetSha=head)
        result.pop("error", None)
        result.pop("jobId", None)
        result["retryAfterSeconds"] = 0
    result["success"] = result["status"] not in {"failed", "retry_wait"}
    manifest["workspaceSync"][ref] = result
    return result


def _failure(manifest: dict, ref: str, job: dict, error: str, failure_id: str) -> dict:
    if not any(item.get("failureId") == failure_id for item in job["errors"]) and job["status"] not in DONE | {"failed"}:
        count = job.get("failures", 0) + 1
        job.update(failures=count, error=error, lastFailureId=failure_id,
                   status="retry_wait" if count <= len(RETRY_DELAYS) else "failed",
                   nextAttemptEpoch=time.time() + RETRY_DELAYS[count - 1] if count <= len(RETRY_DELAYS) else 0)
        job["errors"].append({"error": error, "at": utc_now(), "attempt": job["attempts"], "failureId": failure_id})
        job.pop("owner", None)
    return summary_locked(manifest, ref)


def operate(workspace: Path, feature: str, run_id: str, repository_ref: str, resolver: str,
            *, action: str = "process", job_id: str | None = None, attempt_id: str | None = None,
            error: str | None = None, failure_id: str | None = None) -> dict:
    from hooks import parallel_workspace_sync as sync
    manifest = load_manifest(workspace, feature, run_id)
    if manifest.get("integrationMode") != "isolated":
        return {"success": True, "status": "skipped", "repositoryRef": repository_ref}
    ref = manifest["repositories"][repository_ref].get("integrationRepositoryRef", repository_ref)
    binding = manifest["repositories"][ref]
    guard = FileLock(coordinates(binding)[0] / "workspace-queue.lock", timeout=0)
    try:
        guard.__enter__()
    except TimeoutError:
        return {"success": True, "status": "busy", "repositoryRef": ref, "retryAfterSeconds": 5,
                "reasonCode": "workspace_sync_busy", "hasPending": True}
    try:
        with run_lock(workspace, feature, run_id):
            manifest = load_manifest(workspace, feature, run_id)
            if manifest.get("status") in {"cancelled", "rolled_back", "cleaned"}:
                raise ValueError("workspace_sync_run_inactive")
            jobs = reconcile_locked(manifest, ref)
            job = next_job(jobs)
            if not job:
                result = summary_locked(manifest, ref)
                save_manifest(workspace, feature, run_id, manifest)
                return result
            if job_id and job_id != job["jobId"]:
                return {**summary_locked(manifest, ref), "reasonCode": "workspace_sync_stale_job"}
            if (job.get("owner") not in {None, resolver} and job.get("ownerExpiresEpoch", 0) > time.time()
                    and manifest.get("workspaceSync", {}).get(ref, {}).get("status") == "conflicted"):
                return {**summary_locked(manifest, ref), "status": "busy", "retryAfterSeconds": 5}
            if action == "retry":
                if job["status"] == "failed":
                    job.update(status="queued", failures=0, nextAttemptEpoch=0)
                    job.pop("owner", None)
                result = summary_locked(manifest, ref)
                save_manifest(workspace, feature, run_id, manifest)
                return result
            if action == "fail":
                result = _failure(manifest, ref, job, error or "workspace_sync_agent_failed", failure_id or resolver)
                save_manifest(workspace, feature, run_id, manifest)
                return result
            if job["status"] == "failed" or job.get("nextAttemptEpoch", 0) > time.time():
                result = summary_locked(manifest, ref)
                save_manifest(workspace, feature, run_id, manifest)
                return result
            if action == "complete" and (job.get("owner") != resolver or not attempt_id or not job_id):
                raise ValueError("workspace_sync_stale_resolution")
            job.update(status="running", owner=resolver, ownerExpiresEpoch=time.time() + OWNER_TTL)
            if action != "complete":
                job["attempts"] += 1
            result = summary_locked(manifest, ref)
            save_manifest(workspace, feature, run_id, manifest)
        try:
            # A crash after writing files but before acknowledging the queue is
            # recovered from state.json, including targets already covered.
            _, directory = sync._paths(workspace, feature, run_id, ref)
            previous = sync._read(directory / "state.json")
            published = previous.get("lastSyncedSha")
            covered = bool(published and sync._git(Path(binding["gitRoot"]), "merge-base", "--is-ancestor",
                           job["targetSha"], published, check=False).returncode == 0)
            if covered:
                receipt = {**sync._summary(previous, ref), "status": "synced"}
            else:
                receipt = sync.sync_workspace(workspace, feature, run_id, ref, resolver,
                                              complete_attempt=attempt_id if action == "complete" else None,
                                              target_sha=job["targetSha"])
            covered_ids = []
            if receipt["status"] == "synced":
                for earlier in jobs:
                    if earlier["status"] not in DONE and earlier["jobId"] != job["jobId"] and sync._git(
                            Path(binding["gitRoot"]), "merge-base", "--is-ancestor", earlier["targetSha"],
                            receipt["lastSyncedSha"], check=False).returncode == 0:
                        covered_ids.append(earlier["jobId"])
            with run_lock(workspace, feature, run_id):
                manifest = load_manifest(workspace, feature, run_id)
                current = next(item for item in manifest["workspaceSyncQueue"][ref] if item["jobId"] == job["jobId"])
                if receipt["status"] == "synced":
                    current.update(status="covered" if covered else "synced", completedAt=utc_now())
                    current.pop("owner", None)
                    for earlier in manifest["workspaceSyncQueue"][ref]:
                        if earlier["jobId"] in covered_ids:
                            earlier.update(status="covered", coveredBy=job["jobId"], completedAt=utc_now())
                            earlier.pop("owner", None)
                    manifest["workspaceSync"][ref].update(lastSyncedSha=receipt["lastSyncedSha"])
                    result = summary_locked(manifest, ref)
                elif receipt["status"] in {"conflicted", "refresh_required"}:
                    result = {**summary_locked(manifest, ref), **receipt, "jobId": job["jobId"], "hasPending": True}
                    manifest["workspaceSync"][ref] = result
                else:
                    result = _failure(manifest, ref, current, receipt.get("error") or receipt.get("reasonCode")
                                      or "workspace_sync_incomplete", f"attempt:{current['attempts']}")
                save_manifest(workspace, feature, run_id, manifest)
                return result
        except Exception as exc:
            with run_lock(workspace, feature, run_id):
                manifest = load_manifest(workspace, feature, run_id)
                current = next(item for item in manifest["workspaceSyncQueue"][ref] if item["jobId"] == job["jobId"])
                result = _failure(manifest, ref, current, str(exc), f"attempt:{current['attempts']}")
                save_manifest(workspace, feature, run_id, manifest)
                return result
    finally:
        guard.__exit__(None, None, None)
