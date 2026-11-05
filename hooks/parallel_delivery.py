#!/usr/bin/env python3
"""One fenced Runtime operation for gate, candidate promotion and dispatch closure."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import resolve_feature, resolve_workspace
from hooks.parallel_runtime import load_manifest, run_lock, save_manifest, utc_now, plan_digest
from hooks.parallel_repository import repository_lock, assert_reservation, reserve, release, release_if_owned, coordinates


def _owner(manifest: dict, batch_id: str, generation: int, token: str, expected_commit: str) -> dict:
    if manifest.get("controlProtocolVersion", 1) != 2:
        raise ValueError("delivery_control_protocol_required")
    if manifest.get("status") in {"cancelled", "rolled_back", "cleaned"}:
        raise ValueError("delivery_run_inactive")
    batch = manifest["batches"][batch_id]
    grant = batch.get("dispatch") or {}
    if (batch.get("dispatchGeneration", 1) != generation or grant.get("generation") != generation
            or grant.get("tokenSha256") != hashlib.sha256(token.encode()).hexdigest()
            or grant.get("status") not in {"running", "completed"}
            or (grant.get("status") == "completed" and batch.get("status") != "merged")
            or (grant.get("status") == "running" and grant.get("expiresEpoch", 0) <= time.time())):
        raise ValueError("stale_stage_owner")
    if batch.get("commitSha") != expected_commit:
        raise ValueError("delivery_commit_changed")
    return batch


def _snapshot(workspace: Path, feature: str, run_id: str) -> dict:
    from hooks.parallel_batch_scheduler import schedule
    return {"success": True, **schedule(workspace, feature, run_id, workflow_view=True)}


def settle_delivery(workspace: Path, feature: str, run_id: str, batch_id: str,
                    generation: int, token: str, expected_commit: str, *, skip_tests: bool = False) -> dict:
    from hooks.parallel_batch_stage import gate_batch
    from hooks.parallel_dispatch import finish
    from hooks import parallel_merge_train as train
    from hooks.parallel_cleanup import pending

    started = time.monotonic()
    key = hashlib.sha256(f"{run_id}:{batch_id}:{generation}:{expected_commit}".encode()).hexdigest()
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _owner(manifest, batch_id, generation, token, expected_commit)
        ref = batch.get("repositoryRef") or batch["workspaceRef"]
        binding = manifest["repositories"][ref]
        operation = manifest.setdefault("deliveryOperations", {}).get(key)
        if not operation:
            sequence = int(manifest.get("deliverySequence", 0)) + 1
            manifest["deliverySequence"] = sequence
            operation = {"operationId": key, "batchId": batch_id, "dispatchGeneration": generation,
                         "deliverySha": expected_commit, "repositoryRef": ref,
                         "repositoryKey": str(coordinates(binding)[0]), "ticket": sequence,
                         "status": "queued", "createdAt": utc_now()}
            # Resume a candidate retained by an older generation, including a
            # promotion whose Git mutation succeeded before its response.
            existing = [item for item in manifest.get("mergeTrains", {}).values()
                        if item.get("batchIds") == [batch_id]
                        and item.get("deliveryCommits", {}).get(batch_id) == expected_commit
                        and item.get("status") not in {"discarded", "failed", "stale"}]
            if existing:
                operation["wave"] = max(existing, key=lambda item: item["wave"])["wave"]
            manifest["deliveryOperations"][key] = operation
            save_manifest(workspace, feature, run_id, manifest)
    if batch.get("status") != "merged":
        gate = gate_batch(workspace, feature, run_id, batch_id, skip_tests=skip_tests)
        if gate.get("status") != "ready_to_candidate":
            with run_lock(workspace, feature, run_id):
                manifest = load_manifest(workspace, feature, run_id)
                manifest["deliveryOperations"][key]["status"] = "blocked"
                save_manifest(workspace, feature, run_id, manifest)
            raise ValueError("delivery_gate_failed:" + json.dumps(gate, ensure_ascii=False))
    queue_started = time.monotonic()
    build_ms = promote_ms = 0
    while True:
        with repository_lock(binding):
            manifest = load_manifest(workspace, feature, run_id)
            batch = _owner(manifest, batch_id, generation, token, expected_commit)
            binding = manifest["repositories"][ref]
            operation = manifest["deliveryOperations"][key]
            earlier = [item for item in manifest["deliveryOperations"].values()
                       if item["repositoryKey"] == operation["repositoryKey"] and item["ticket"] < operation["ticket"]
                       and item.get("status") in {"queued", "built", "promoting"}
                       and manifest["batches"][item["batchId"]].get("status") != "merged"
                       and (manifest["batches"][item["batchId"]].get("dispatch") or {}).get("status") == "running"
                       and (manifest["batches"][item["batchId"]].get("dispatch") or {}).get("expiresEpoch", 0) > time.time()
                       and manifest["batches"][item["batchId"]].get("dispatchGeneration", 1) == item["dispatchGeneration"]]
            if not earlier:
                queue_wait_ms = round((time.monotonic() - queue_started) * 1000)
                with run_lock(workspace, feature, run_id):
                    manifest = load_manifest(workspace, feature, run_id)
                    _owner(manifest, batch_id, generation, token, expected_commit)
                    operation = manifest["deliveryOperations"][key]
                    if "wave" not in operation:
                        operation["wave"] = max([int(item.get("wave", 0)) for item in
                                                 manifest.get("mergeTrains", {}).values()] + [
                                                 int(item.get("wave", 0)) for item in
                                                 manifest["deliveryOperations"].values()] + [0]) + 1
                        save_manifest(workspace, feature, run_id, manifest)
                    wave = operation["wave"]
                record = train._record(manifest, ref, wave)
                if batch.get("status") == "merged":
                    if not batch.get("mergeCommitSha"):
                        raise ValueError("delivery_merged_receipt_missing")
                    release_if_owned(binding, workspace, feature, run_id, wave)
                    result = {"success": True, "promoted": True, "candidateSha": batch["mergeCommitSha"],
                              "batchIds": [batch_id], "reused": True}
                else:
                    assert_reservation(binding, workspace, feature, run_id, wave)
                    reserve(binding, workspace, feature, run_id, wave,
                            (record or {}).get("baseSha") or binding.get("headSha") or binding["baseSha"])
                    if record and record.get("status") in {"promoting", "needs_resolution"} and (
                            record.get("promotionIntentAt") or record.get("promotedSha")):
                        result = train._recover_promoted_plan(workspace, feature, run_id, wave=wave, repository_ref=ref)
                        if not result.get("retryPromotion"):
                            if not result.get("success"):
                                return {**result, "status": "needs_plan_recovery", "batchId": batch_id, "wave": wave, "repositoryRef": ref}
                        else:
                            record = train._record(load_manifest(workspace, feature, run_id), ref, wave)
                    else:
                        result = None
                    if result is None or result.get("retryPromotion"):
                        if record and record.get("status") in {"candidate_conflicted", "needs_resolution"}:
                            built = {"success": False, **record}
                        else:
                            build_started = time.monotonic()
                            built = train._build_candidate(workspace, feature, run_id, wave=wave, batch_ids=[batch_id])
                            build_ms = round((time.monotonic() - build_started) * 1000)
                        if built.get("status") in {"candidate_conflicted", "needs_resolution"}:
                            reserve(binding, workspace, feature, run_id, wave, built["baseSha"] if "baseSha" in built
                                    else built["conflictContext"]["baseSha"])
                            return {**built, "status": "candidate_conflicted", "batchId": batch_id, "operationId": key}
                        if not built.get("success"):
                            return {**built, "status": "blocked", "batchId": batch_id, "operationId": key}
                        _owner(load_manifest(workspace, feature, run_id), batch_id, generation, token, expected_commit)
                        promote_started = time.monotonic()
                        result = train._promote_candidate(workspace, feature, run_id, wave=wave, repository_ref=ref,
                                                          allow_unverified=True)
                        promote_ms = round((time.monotonic() - promote_started) * 1000)
                        if not result.get("success"):
                            return {**result, "status": "needs_plan_recovery" if result.get("needsPlanRecovery") else "blocked",
                                    "batchId": batch_id, "wave": wave, "repositoryRef": ref}
                with run_lock(workspace, feature, run_id):
                    manifest = load_manifest(workspace, feature, run_id)
                    _owner(manifest, batch_id, generation, token, expected_commit)
                    operation = manifest["deliveryOperations"][key]
                    operation.update(status="merged", mergeCommitSha=result["candidateSha"], completedAt=utc_now(),
                                     queueWaitMs=queue_wait_ms,
                                     buildMs=build_ms, promoteMs=promote_ms,
                                     durationMs=round((time.monotonic() - started) * 1000))
                    save_manifest(workspace, feature, run_id, manifest)
                break
        time.sleep(0.1)
    finalized = finish(workspace, feature, run_id, batch_id, generation, token)
    if finalized.get("status") not in {"finished", "already_finished"}:
        raise ValueError("delivery_dispatch_not_finalized")
    snapshot = _snapshot(workspace, feature, run_id)
    current = load_manifest(workspace, feature, run_id)
    return {"success": True, "status": "merged", "runId": run_id, "batchId": batch_id,
            "dispatchGeneration": generation, "operationId": key, "wave": wave, "repositoryRef": ref,
            "deliverySha": expected_commit, "mergeCommitSha": result["candidateSha"],
            "dispatchFinalized": True, "schedulerSnapshot": snapshot, "cleanupPending": pending(current),
            "durationMs": round((time.monotonic() - started) * 1000), "reused": result.get("reused", False)}


def finalization_inputs(manifest: dict) -> dict:
    return {"deliveryCommits": {key: batch["mergeCommitSha"] for key, batch in manifest["batches"].items()},
            "productionCommits": {key: batch.get("commitSha") for key, batch in manifest["batches"].items()},
            "stageEvidence": {key: {stage: (batch.get("stageStates", {}).get(stage) or {}).get("latestEvidenceId")
                                    for stage in ("review", "test")} for key, batch in manifest["batches"].items()},
            "heads": {ref: binding.get("headSha") for ref, binding in manifest["repositories"].items()},
            "planDigest": manifest["planDigest"]}


def prepare_finalization(workspace: Path, feature: str, run_id: str) -> dict:
    from hooks.plan_json import load_plan_bundle
    from hooks.json_writer_common import feature_dir
    from hooks.parallel_integration import assert_integration, git
    from hooks.parallel_cleanup import pending
    # Tick recovery here; the Workflow reads the view through scheduler status.
    _snapshot(workspace, feature, run_id)
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        unfinished = [key for key, batch in manifest["batches"].items()
                      if batch.get("status") != "merged" or not batch.get("mergeCommitSha")
                      or (batch.get("dispatch") or {}).get("status") == "running"]
        if unfinished:
            return {"success": True, "status": "repair_required", "repairRequired": True,
                    "batchIds": unfinished}
        if plan_digest(load_plan_bundle(feature_dir(workspace, feature))) != manifest["planDigest"]:
            raise ValueError("parallel_plan_contract_changed")
        heads = {}
        for ref, binding in manifest["repositories"].items():
            repo = assert_integration(binding)
            heads[ref] = git(repo, "rev-parse", "HEAD")
            if heads[ref] != binding.get("headSha"):
                raise ValueError("delivery_finalization_head_changed")
        receipt = finalization_inputs(manifest)
        receipt["digest"] = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
        manifest["finalizationReceipt"] = receipt
        manifest.pop("finalizationReceiptInvalidated", None)
        save_manifest(workspace, feature, run_id, manifest)
    return {"success": True, "status": "ready", "repairRequired": False,
            "batchIds": [], "cleanupPending": pending(manifest)}


def upgrade_protocol(workspace: Path, feature: str, run_id: str) -> dict:
    """Explicit upgrade only after old execution and recovery have drained."""
    from hooks.parallel_cleanup import enqueue, pending
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if manifest.get("controlProtocolVersion", 1) == 2:
            return {"success": True, "status": "upgraded", "reused": True}
        if manifest.get("integrationMode") != "isolated":
            raise ValueError("delivery_upgrade_requires_isolated_run")
        for batch in manifest["batches"].values():
            grant = batch.get("dispatch") or {}
            if batch.get("lease") or (grant.get("status") == "running" and grant.get("expiresEpoch", 0) > time.time()):
                raise ValueError("delivery_upgrade_requires_quiescent_run")
        if any(item.get("status") in {"promoting", "needs_resolution", "candidate_conflicted"}
               for item in manifest.get("mergeTrains", {}).values()):
            raise ValueError("delivery_upgrade_requires_recovery_first")
        for path in (workspace / ".autobizdevops" / "features" / feature / ".task-runs").glob("*/*.json"):
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("parallelRunId") == run_id and state.get("status") in {"started", "in_progress", "implementation_recording"}:
                raise ValueError("delivery_upgrade_requires_quiescent_tasks")
        manifest["controlProtocolVersion"] = 2
        manifest["controlProtocolUpgradedAt"] = utc_now()
        for item in manifest.get("mergeTrains", {}).values():
            if item.get("status") == "promoted":
                enqueue(manifest, item["repositoryRef"], item["wave"])
        save_manifest(workspace, feature, run_id, manifest)
    return {"success": True, "status": "upgraded", "controlProtocolVersion": 2, "cleanupPending": pending(manifest)}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("settle-delivery", "prepare-finalization", "upgrade-control-protocol"):
        command = commands.add_parser(action)
        for name in ("workspace", "feature", "run-id"):
            command.add_argument("--" + name, required=True)
        if action == "settle-delivery":
            command.add_argument("--skip-tests", action="store_true")
            for name in ("batch-id", "dispatch-token", "expected-commit"):
                command.add_argument("--" + name, required=True)
            command.add_argument("--dispatch-generation", type=int, required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        workspace, feature = resolve_workspace(args.workspace), resolve_feature(args.feature)
        if args.action == "settle-delivery":
            result = settle_delivery(workspace, feature, args.run_id, args.batch_id, args.dispatch_generation,
                                     args.dispatch_token, args.expected_commit, skip_tests=args.skip_tests)
        elif args.action == "prepare-finalization":
            result = prepare_finalization(workspace, feature, args.run_id)
        else:
            result = upgrade_protocol(workspace, feature, args.run_id)
    except (OSError, ValueError, KeyError) as exc:
        result = {"success": False, "status": "failed", "error": str(exc)}
        if getattr(args, "batch_id", None):
            result["batchId"] = args.batch_id
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
