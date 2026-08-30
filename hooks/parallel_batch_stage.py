#!/usr/bin/env python3
"""Durable stage state machine for staged parallel Batch execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import atomic_write_json, resolve_feature, resolve_workspace
from hooks.parallel_runtime import (
    append_event,
    delivery_stage_names,
    load_manifest,
    run_dir,
    run_lock,
    save_manifest,
)


VALIDATION_STAGE_BY_BATCH = {"V-INT": "integration_test", "V-E2E": "e2e_test"}
STAGE_STATUSES = {"pending", "running", "passed", "failed", "stale", "skipped", "needs_triage"}
FAILURE_NEXT_STAGE = {
    "implementation": "implement",
    "test_definition": "test",
    "documentation": "review",
    # Environment failures keep the same stage pending.  They are retryable
    # and must not invalidate previously-passed upstream evidence.
    "environment": "__retry__",
    "needs_triage": None,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stage_names(batch: dict[str, Any]) -> tuple[str, ...]:
    batch_type = str(batch.get("type") or "delivery")
    if batch_type == "validation":
        stage = str(batch.get("validationStage") or VALIDATION_STAGE_BY_BATCH.get(str(batch.get("batchId")), "integration_test"))
        return ("prepare", stage)
    if batch_type == "repair":
        return delivery_stage_names(batch)
    return delivery_stage_names(batch)


def stage_template(batch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        stage: {
            "status": "pending",
            "attempt": 0,
            "evidenceIds": [],
            "latestEvidenceId": None,
            "startedAt": None,
            "completedAt": None,
        }
        for stage in stage_names(batch)
    }


def _ensure_stage_states(batch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    current = batch.get("stageStates")
    if not isinstance(current, dict):
        current = {}
        batch["stageStates"] = current
    for stage, template in stage_template(batch).items():
        value = current.get(stage)
        if not isinstance(value, dict):
            current[stage] = dict(template)
            continue
        value.setdefault("status", "pending")
        value.setdefault("attempt", 0)
        value.setdefault("evidenceIds", [])
        value.setdefault("latestEvidenceId", None)
        value.setdefault("startedAt", None)
        value.setdefault("completedAt", None)
    return current


def _pipeline(manifest: dict[str, Any]) -> dict[str, Any]:
    pipeline = manifest.get("pipeline")
    if not isinstance(pipeline, dict):
        raise ValueError("parallel_batch_pipeline_manifest_missing")
    return pipeline


def _batch(manifest: dict[str, Any], batch_id: str) -> dict[str, Any]:
    batch = manifest.get("batches", {}).get(batch_id)
    if not isinstance(batch, dict):
        batch = manifest.get("validationBatches", {}).get(batch_id)
    if not isinstance(batch, dict):
        raise ValueError(f"parallel_batch_not_found:{batch_id}")
    return batch


def _dependency_snapshot(manifest: dict[str, Any], batch: dict[str, Any]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    candidate_validation = batch.get("type") == "validation" and batch.get("validationStage") == "integration_test"
    for dependency in batch.get("dependencies", []):
        item = manifest.get("batches", {}).get(dependency)
        if isinstance(item, dict):
            sha = (item.get("commitSha") or item.get("mergeCommitSha")) if candidate_validation else (item.get("mergeCommitSha") or item.get("commitSha"))
            if isinstance(sha, str) and sha:
                snapshot[str(dependency)] = sha
    return snapshot


def reset_validation_batch(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    *,
    candidate_sha: str,
    candidate_base_sha: str,
    train_id: str,
    dependency_batch_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Start a new immutable validation attempt for a Merge Train candidate."""
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        if batch.get("type") != "validation":
            raise ValueError(f"parallel_validation_batch_required:{batch_id}")
        batch["status"] = "pending"
        batch["activeStage"] = None
        batch["candidateSha"] = candidate_sha
        batch["candidateBaseSha"] = candidate_base_sha
        batch["trainId"] = train_id
        if dependency_batch_ids is not None:
            batch["dependencies"] = sorted(set(dependency_batch_ids))
        batch["stageStates"] = stage_template(batch)
        save_manifest(workspace, feature, run_id, manifest)
    append_event(
        workspace,
        feature,
        run_id,
        "validation_batch_reset",
        batchId=batch_id,
        candidateSha=candidate_sha,
        candidateBaseSha=candidate_base_sha,
        trainId=train_id,
    )
    return {"batchId": batch_id, "status": "pending", "trainId": train_id}


def _stage_input(manifest: dict[str, Any], batch: dict[str, Any], stage: str, metadata: dict[str, Any]) -> dict[str, Any]:
    pipeline = _pipeline(manifest)
    command = metadata.get("command") if isinstance(metadata.get("command"), dict) else metadata.get("commands")
    command_digest = (
        "sha256:" + hashlib.sha256(json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if command is not None
        else None
    )
    toolchain = metadata.get("toolchain") if isinstance(metadata.get("toolchain"), dict) else {}
    toolchain_digest = "sha256:" + hashlib.sha256(
        json.dumps(toolchain, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "planRevision": pipeline.get("planRevision"),
        "batchCommit": metadata.get("batchCommit") or metadata.get("candidateSha") or batch.get("candidateSha") or batch.get("commitSha"),
        "dependencies": _dependency_snapshot(manifest, batch),
        "commandDigest": command_digest,
        "toolchainDigest": toolchain_digest,
        "stage": stage,
    }


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _evidence_path(workspace: Path, feature: str, run_id: str, batch_id: str, stage: str, attempt: int) -> Path:
    return run_dir(workspace, feature, run_id) / "stages" / batch_id / stage / f"{attempt:03d}.json"


def next_stage(workspace: Path, feature: str, run_id: str, batch_id: str) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        for stage in stage_names(batch):
            state = states[stage]
            if state.get("status") in {"pending", "stale", "failed", "needs_triage"}:
                save_manifest(workspace, feature, run_id, manifest)
                return {
                    "batchId": batch_id,
                    "stage": stage,
                    "status": state.get("status"),
                    "attempt": state.get("attempt", 0),
                    "stageStates": states,
                }
            if state.get("status") == "running":
                save_manifest(workspace, feature, run_id, manifest)
                return {"batchId": batch_id, "stage": stage, "status": "running", "stageStates": states}
        save_manifest(workspace, feature, run_id, manifest)
        return {"batchId": batch_id, "stage": None, "status": "complete", "stageStates": states}


def start_stage(workspace: Path, feature: str, run_id: str, batch_id: str, stage: str) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        if stage not in states:
            raise ValueError(f"parallel_batch_stage_unknown:{batch_id}:{stage}")
        state = states[stage]
        # Workflow retries are normal after a host interruption.  A completed
        # stage must keep its original evidence, and a running stage must not
        # acquire a second attempt merely because the coordinator retried.
        if state.get("status") in {"passed", "skipped"}:
            save_manifest(workspace, feature, run_id, manifest)
            return {
                "batchId": batch_id,
                "stage": stage,
                "attempt": state.get("attempt", 0),
                "status": state.get("status"),
                "reused": True,
            }
        if state.get("status") == "running":
            save_manifest(workspace, feature, run_id, manifest)
            return {
                "batchId": batch_id,
                "stage": stage,
                "attempt": state.get("attempt", 0),
                "status": "running",
                "reused": True,
            }
        expected = next(
            (
                name for name in stage_names(batch)
                if states[name].get("status") not in {"passed", "skipped"}
            ),
            None,
        )
        if expected is None:
            raise ValueError(f"parallel_batch_stage_already_complete:{batch_id}")
        if expected != stage:
            raise ValueError(f"parallel_batch_stage_out_of_order:{batch_id}:expected={expected}:actual={stage}")
        if state.get("status") == "needs_triage":
            raise ValueError(f"parallel_batch_stage_needs_triage:{batch_id}:{stage}")
        state.update({"status": "running", "attempt": int(state.get("attempt", 0)) + 1, "startedAt": _utc_now(), "completedAt": None})
        batch["activeStage"] = stage
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "batch_stage_started", batchId=batch_id, stage=stage, attempt=state["attempt"])
    return {"batchId": batch_id, "stage": stage, "attempt": state["attempt"], "status": "running"}


def complete_stage(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    stage: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(metadata or {})
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        state = states.get(stage)
        if isinstance(state, dict) and state.get("status") in {"passed", "skipped"}:
            return {
                "batchId": batch_id,
                "stage": stage,
                "status": state.get("status"),
                "evidenceId": state.get("latestEvidenceId"),
                "reused": True,
            }
        if not isinstance(state, dict) or state.get("status") != "running":
            raise ValueError(f"parallel_batch_stage_not_running:{batch_id}:{stage}")
        attempt = int(state.get("attempt", 0))
        inputs = _stage_input(manifest, batch, stage, metadata)
        evidence_id = f"STAGE-{batch_id}-{stage.upper()}-{attempt:03d}"
        evidence = {
            "evidenceId": evidence_id,
            "batchId": batch_id,
            "stage": stage,
            "attempt": attempt,
            "createdAt": _utc_now(),
            "inputs": inputs,
            "validity": {
                "type": "content_based",
                "invalidateOn": ["planRevision", "batchCommit", "dependencies", "commandDigest", "toolchainDigest"],
            },
            "metadata": metadata,
        }
        evidence["digest"] = _digest(evidence)
        path = _evidence_path(workspace, feature, run_id, batch_id, stage, attempt)
        atomic_write_json(path, evidence)
        state.update({
            "status": "passed",
            "completedAt": evidence["createdAt"],
            "latestEvidenceId": evidence_id,
            "evidenceIds": [*state.get("evidenceIds", []), evidence_id],
            "inputDigest": _digest(inputs),
            "evidencePath": str(path),
            "failure": None,
        })
        batch["activeStage"] = None
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "batch_stage_completed", batchId=batch_id, stage=stage, evidenceId=evidence_id)
    return {"batchId": batch_id, "stage": stage, "status": "passed", "evidenceId": evidence_id, "evidence": evidence}


def fail_stage(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    stage: str,
    *,
    failure_type: str,
    message: str,
) -> dict[str, Any]:
    if failure_type not in FAILURE_NEXT_STAGE:
        raise ValueError(f"parallel_batch_failure_type_invalid:{failure_type}")
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        state = states.get(stage)
        if not isinstance(state, dict) or state.get("status") != "running":
            raise ValueError(f"parallel_batch_stage_not_running:{batch_id}:{stage}")
        next_name = FAILURE_NEXT_STAGE[failure_type]
        validation_repair = batch.get("type") == "validation" and failure_type == "implementation"
        if validation_repair:
            next_name = "create_repair_batch"
        state.update({
            "status": "needs_triage" if failure_type == "needs_triage" else "failed",
            "completedAt": _utc_now(),
            "failure": {"type": failure_type, "message": message, "nextStage": next_name},
        })
        if validation_repair:
            batch["activeStage"] = None
            batch["status"] = "blocked"
        elif next_name == "__retry__":
            state.update({"status": "pending", "completedAt": None})
            state["failure"]["nextStage"] = stage
            batch["activeStage"] = None
            batch["status"] = "running"
            next_name = stage
        elif next_name is not None:
            reset = False
            for name in stage_names(batch):
                if name == next_name:
                    reset = True
                if reset:
                    states[name].update({"status": "pending", "latestEvidenceId": None, "completedAt": None})
            batch["activeStage"] = None
            batch["status"] = "running"
        else:
            batch["status"] = "blocked" if failure_type == "needs_triage" else "failed"
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "batch_stage_failed", batchId=batch_id, stage=stage, failureType=failure_type)
    return {"batchId": batch_id, "stage": stage, "status": state["status"], "nextStage": next_name}


def gate_batch(workspace: Path, feature: str, run_id: str, batch_id: str) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        missing = [stage for stage in stage_names(batch) if states[stage].get("status") not in {"passed", "skipped"}]
        if missing:
            return {"success": False, "batchId": batch_id, "error": "parallel_batch_stage_gate_incomplete", "missingStages": missing}
        if str(batch.get("type") or "delivery") == "validation":
            batch["status"] = "verified"
        elif not isinstance(batch.get("commitSha"), str) or not batch.get("commitSha"):
            return {"success": False, "batchId": batch_id, "error": "parallel_batch_stage_gate_delivery_not_sealed"}
        else:
            batch["status"] = "ready_to_candidate"
        batch["activeStage"] = None
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "batch_stage_gate_passed", batchId=batch_id, status=batch["status"])
    return {"success": True, "batchId": batch_id, "status": batch["status"]}


def triage_failure(workspace: Path, feature: str, run_id: str, batch_id: str, stage: str, failure_type: str) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        state = states.get(stage)
        if not isinstance(state, dict) or state.get("status") != "needs_triage":
            raise ValueError(f"parallel_batch_stage_not_waiting_for_triage:{batch_id}:{stage}")
        message = str((state.get("failure") or {}).get("message") or "triaged")
        # ``fail_stage`` is intentionally the only function that records the
        # classification.  Restore the transient running state so it can
        # apply the normal failure transition under a fresh lock.
        state["status"] = "running"
        save_manifest(workspace, feature, run_id, manifest)
    return fail_stage(workspace, feature, run_id, batch_id, stage, failure_type=failure_type, message=message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advance a staged parallel Batch")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("next", "start", "complete", "fail", "gate", "triage-failure", "reset-validation"):
        item = sub.add_parser(name)
        item.add_argument("--workspace")
        item.add_argument("--feature", required=True)
        item.add_argument("--run-id", required=True)
        item.add_argument("--batch-id", required=True)
        if name in {"start", "complete", "fail", "triage-failure"}:
            item.add_argument("--stage", required=True)
        if name == "complete":
            item.add_argument("--metadata-json", default="{}")
        if name in {"fail", "triage-failure"}:
            item.add_argument("--failure-type", required=True, choices=tuple(FAILURE_NEXT_STAGE))
        if name == "fail":
            item.add_argument("--message", required=True)
        if name == "reset-validation":
            item.add_argument("--candidate-sha", required=True)
            item.add_argument("--candidate-base-sha", required=True)
            item.add_argument("--train-id", required=True)
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        if args.command == "next":
            result = next_stage(workspace, feature, args.run_id, args.batch_id)
        elif args.command == "start":
            result = start_stage(workspace, feature, args.run_id, args.batch_id, args.stage)
        elif args.command == "complete":
            metadata = json.loads(args.metadata_json)
            if not isinstance(metadata, dict):
                raise ValueError("parallel_batch_stage_metadata_must_be_object")
            result = complete_stage(workspace, feature, args.run_id, args.batch_id, args.stage, metadata=metadata)
        elif args.command == "fail":
            result = fail_stage(workspace, feature, args.run_id, args.batch_id, args.stage, failure_type=args.failure_type, message=args.message)
        elif args.command == "triage-failure":
            result = triage_failure(workspace, feature, args.run_id, args.batch_id, args.stage, args.failure_type)
        elif args.command == "reset-validation":
            result = reset_validation_batch(
                workspace,
                feature,
                args.run_id,
                args.batch_id,
                candidate_sha=args.candidate_sha,
                candidate_base_sha=args.candidate_base_sha,
                train_id=args.train_id,
            )
        else:
            result = gate_batch(workspace, feature, args.run_id, args.batch_id)
        print(json.dumps({"ok": result.get("success", True), **result}, ensure_ascii=False, indent=2))
        return 0 if result.get("success", True) else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
