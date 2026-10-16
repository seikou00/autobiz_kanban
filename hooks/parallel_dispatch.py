#!/usr/bin/env python3
"""Durable lifecycle grants. A scheduler poll never creates a new generation."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import atomic_write_json, resolve_feature, resolve_workspace
from hooks.parallel_runtime import (append_event, batch_occupies_scheduler_slot, lease_path, load_manifest,
                                    ready_batches, mergeable_batches, stage_recovery_batches,
                                    run_dir, run_lock, save_manifest, select_runnable_batches)
from hooks.runtime_guidance import RuntimeArgumentParser


def generation(batch: dict) -> int:
    return int(batch.get("dispatchGeneration", 1))


def active_claim(batch: dict, now: float | None = None) -> bool:
    claim = batch.get("dispatch", {})
    return (claim.get("status") == "running" and claim.get("generation") == generation(batch)
            and claim.get("expiresEpoch", 0) > (time.time() if now is None else now))


def read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def claim(workspace: Path, feature: str, run_id: str, batch_id: str, expected: int,
          source: str, worker: str, ttl: int = 14400, request_id: str | None = None) -> dict:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batches = manifest.get("batches", {})
        batch = batches.get(batch_id)
        if not isinstance(batch, dict):
            raise ValueError("parallel_dispatch_batch_missing")
        def denied(reason):
            return {"success": True, "status": "not_claimed", "batchId": batch_id,
                    "generation": generation(batch), "reasonCode": reason}
        if expected != generation(batch):
            return denied("stale_generation")
        previous = batch.get("dispatch", {})
        if active_claim(batch):
            if request_id and previous.get("requestId") == request_id and previous.get("worker") == worker:
                private = read(run_dir(workspace, feature, run_id) / "dispatch" / (batch_id + ".json"))
                token = private.get("ownerToken", "")
                if token and hashlib.sha256(token.encode()).hexdigest() == previous.get("tokenSha256"):
                    return {"success": True, "status": "claimed", "batchId": batch_id,
                            "generation": expected, "ownerToken": token, "reused": True}
            return denied("running")
        if previous.get("generation") == expected and previous.get("status") in {"completed", "failed", "retry_pending"}:
            return denied("grant_consumed")
        business_lease = read(lease_path(workspace, feature, run_id, batch_id))
        if business_lease.get("expiresEpoch", 0) > time.time():
            return denied("business_lease_held")
        active_ids = {key for key, item in batches.items() if batch_occupies_scheduler_slot(item) or active_claim(item)}
        slots = int(manifest.get("maxParallel", 1)) - len(active_ids - {batch_id})
        if slots <= 0:
            return denied("capacity_full")
        if batch_id not in select_runnable_batches(manifest, [batch_id], sorted(active_ids - {batch_id}), slots):
            return denied("resource_or_stage_conflict")
        if source in {"initial", "scheduler_snapshot_fallback"}:
            allowed = select_runnable_batches(manifest, ready_batches(manifest), sorted(active_ids), slots)
            eligible = batch_id in allowed
        elif source == "merge_candidate":
            eligible = batch_id in mergeable_batches(manifest)
        elif source == "stage_recovery":
            eligible = batch_id in stage_recovery_batches(manifest)
        elif source == "implementation_recovery":
            from hooks.parallel_batch_scheduler import _implementation_resume_details
            eligible = batch.get("status") not in {"blocked", "failed", "retry_pending", "merged", "cancelled"} and _implementation_resume_details(manifest, batch_id, batch) is not None
        else:
            raise ValueError("parallel_dispatch_source_invalid")
        if not eligible:
            return denied("not_runnable")
        for dep in batch.get("dependencies", []):
            if batches.get(dep, {}).get("status") != "merged" or not batches.get(dep, {}).get("mergeCommitSha"):
                return denied("dependencies_unmerged")
        token = uuid.uuid4().hex
        metadata = {"generation": expected, "status": "running", "worker": worker,
                    "requestId": request_id, "tokenSha256": hashlib.sha256(token.encode()).hexdigest(),
                    "expiresEpoch": time.time() + max(60, ttl), "ttlSeconds": max(60, ttl), "source": source}
        path = run_dir(workspace, feature, run_id) / "dispatch" / (batch_id + ".json")
        atomic_write_json(path, {**metadata, "ownerToken": token})
        batch["dispatch"] = metadata
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "dispatch_claimed", batchId=batch_id, generation=expected, worker=worker)
    return {"success": True, "status": "claimed", "batchId": batch_id, "generation": expected, "ownerToken": token}


def finish(workspace: Path, feature: str, run_id: str, batch_id: str, expected: int,
           token: str, outcome: str = "completed") -> dict:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = manifest["batches"][batch_id]
        current = batch.get("dispatch", {})
        if (generation(batch) != expected or current.get("generation") != expected
                or current.get("expiresEpoch", 0) <= time.time()
                or current.get("tokenSha256") != hashlib.sha256(token.encode()).hexdigest()):
            return {"success": True, "status": "stale_owner_ignored", "batchId": batch_id}
        if current.get("status") != "running":
            return {"success": True, "status": "already_finished", "batchId": batch_id}
        current.update(status=outcome, completedEpoch=time.time())
        save_manifest(workspace, feature, run_id, manifest)
        path = run_dir(workspace, feature, run_id) / "dispatch" / (batch_id + ".json")
        path.unlink(missing_ok=True)
    append_event(workspace, feature, run_id, "dispatch_finished", batchId=batch_id, generation=expected, outcome=outcome)
    return {"success": True, "status": "finished", "batchId": batch_id}


def defer(workspace: Path, feature: str, run_id: str, batch_id: str, expected: int,
          token: str, error: str) -> dict:
    """Fence failure cleanup as well as success: an old worker cannot reclaim a new lease."""
    from hooks.parallel_batch_scheduler import _mark_retry_pending_locked
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = manifest["batches"][batch_id]
        current = batch.get("dispatch", {})
        if (generation(batch) != expected or current.get("generation") != expected
                or current.get("tokenSha256") != hashlib.sha256(token.encode()).hexdigest()):
            return {"success": True, "status": "stale_owner_ignored", "batchId": batch_id}
        if current.get("status") in {"retry_pending", "failed"} and batch.get("status") in {"retry_pending", "blocked"}:
            return {"success": True, "status": batch["status"], "batchId": batch_id, "reused": True}
        if current.get("status") != "running" or current.get("expiresEpoch", 0) <= time.time():
            return {"success": True, "status": "stale_owner_ignored", "batchId": batch_id}
        if batch.get("status") == "merged":
            return {"success": True, "status": "merged", "batchId": batch_id}
        _mark_retry_pending_locked(workspace, feature, run_id, manifest, batch_id, batch, error=error)
        current.update(status="retry_pending" if batch["status"] == "retry_pending" else "failed")
        save_manifest(workspace, feature, run_id, manifest)
        (run_dir(workspace, feature, run_id) / "dispatch" / (batch_id + ".json")).unlink(missing_ok=True)
    append_event(workspace, feature, run_id, "dispatch_deferred", batchId=batch_id,
                 generation=expected, status=batch["status"], reasonCode=(batch.get("recovery") or {}).get("reasonCode"))
    return {"success": True, "status": batch["status"], "batchId": batch_id,
            "reasonCode": (batch.get("recovery") or {}).get("reasonCode")}


def build_parser():
    parser = RuntimeArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("claim", "finish", "defer"):
        cmd = commands.add_parser(action)
        for name in ("workspace", "feature", "run-id", "batch-id"):
            cmd.add_argument("--" + name, required=True)
        cmd.add_argument("--generation", type=int, required=True)
        if action == "claim":
            cmd.add_argument("--source", required=True)
            cmd.add_argument("--worker", required=True)
            cmd.add_argument("--request-id")
            cmd.add_argument("--ttl-seconds", type=int, default=14400)
        else:
            cmd.add_argument("--owner-token", required=True)
            if action == "finish":
                cmd.add_argument("--outcome", choices=("completed", "failed", "retry_pending"), default="completed")
            else:
                cmd.add_argument("--error-b64", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        workspace, feature = resolve_workspace(args.workspace), resolve_feature(args.feature)
        if args.action == "claim":
            result = claim(workspace, feature, args.run_id, args.batch_id, args.generation, args.source, args.worker, args.ttl_seconds, args.request_id)
        elif args.action == "finish":
            result = finish(workspace, feature, args.run_id, args.batch_id, args.generation, args.owner_token, args.outcome)
        else:
            error = base64.b64decode(args.error_b64, validate=True).decode("utf-8")
            result = defer(workspace, feature, args.run_id, args.batch_id, args.generation, args.owner_token, error)
    except (ValueError, OSError, KeyError) as exc:
        result = {"success": False, "reasonCode": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
