#!/usr/bin/env python3
"""Durable stage state machine for staged parallel Batch execution."""

from __future__ import annotations

import argparse
import hashlib
import time
from contextvars import ContextVar
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.runtime_guidance import RuntimeArgumentParser
from hooks.json_writer_common import atomic_write_json, resolve_feature, resolve_workspace
from hooks.parallel_runtime import (
    append_event,
    DELIVERY_STAGES,
    load_manifest,
    run_dir,
    run_lock,
    save_manifest,
)


VALIDATION_STAGE_BY_BATCH = {"V-E2E": "e2e_test"}
STAGE_STATUSES = {"pending", "running", "passed", "failed", "stale", "skipped", "deferred", "needs_triage"}
FAILURE_NEXT_STAGE = {
    "implementation": "implement",
    "test_definition": "test",
    "documentation": "review",
    # Environment failures keep the same stage pending.  They are retryable
    # and must not invalidate previously-passed upstream evidence.
    "environment": "__retry__",
    "needs_triage": None,
}
# UTest is a bounded quality loop.  This value is mirrored by the Workflow
# runner and the scheduler so a model cannot keep a Batch in ``test`` merely
# by trying another command after the command-level timeout has fired.
from hooks.utest_policy import POLICY, policy_snapshot, stage_budget
UTEST_STAGE_TIMEOUT_SECONDS = POLICY["stageSeconds"]
FRONTEND_UTEST_SKIP_REASON = "frontend_utest_temporarily_disabled"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stage_names(batch: dict[str, Any]) -> tuple[str, ...]:
    batch_type = str(batch.get("type") or "delivery")
    if batch_type == "validation":
        stage = str(batch.get("validationStage") or VALIDATION_STAGE_BY_BATCH.get(str(batch.get("batchId")), "integration_test"))
        return ("prepare", stage)
    return DELIVERY_STAGES


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


_CLI_DISPATCH_AUTH = ContextVar("stage_dispatch_auth", default=None)


def _batch(manifest: dict[str, Any], batch_id: str) -> dict[str, Any]:
    batch = manifest.get("batches", {}).get(batch_id)
    if not isinstance(batch, dict):
        batch = manifest.get("validationBatches", {}).get(batch_id)
    if not isinstance(batch, dict):
        raise ValueError(f"parallel_batch_not_found:{batch_id}")
    auth = _CLI_DISPATCH_AUTH.get()
    dispatch = batch.get("dispatch") or {}
    if auth is not None and dispatch:
        if (auth.get("generation") != batch.get("dispatchGeneration", 1)
                or dispatch.get("generation") != auth.get("generation")
                or dispatch.get("status") != "running" or dispatch.get("expiresEpoch", 0) <= time.time()
                or dispatch.get("tokenSha256") != hashlib.sha256((auth.get("token") or "").encode()).hexdigest()):
            raise ValueError(f"parallel_dispatch_stale_stage_owner:{batch_id}")
        # Stage transitions renew lifecycle ownership; UTest's independent
        # first-command and total execution deadlines remain unchanged.
        dispatch["expiresEpoch"] = time.time() + max(60, int(dispatch.get("ttlSeconds", 14400)))
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
    inputs = {
        "planRevision": pipeline.get("planRevision"),
        "batchCommit": metadata.get("batchCommit") or metadata.get("candidateSha") or batch.get("candidateSha") or batch.get("commitSha"),
        "dependencies": _dependency_snapshot(manifest, batch),
        "commandDigest": command_digest,
        "toolchainDigest": toolchain_digest,
        "stage": stage,
    }
    if stage == "prepare" and metadata.get("preparationSource") == "task_runner_workspace_binding":
        # Preparation happens before production changes exist. Its evidence
        # binds the frozen repository base, not the future sealed delivery.
        repository_ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
        repository = manifest.get("repositories", {}).get(repository_ref, {})
        inputs.update({
            "commitScope": "repository_base",
            "repositoryRef": repository_ref,
            "batchCommit": repository.get("headSha") or repository.get("baseSha"),
        })
    return inputs


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
        if stage == "test" and batch.get("executionLane") == "frontend":
            raise ValueError(f"frontend_utest_disabled_use_prepare_test:{batch_id}")
        if stage not in states:
            raise ValueError(f"parallel_batch_stage_unknown:{batch_id}:{stage}")
        state = states[stage]
        # Workflow retries are normal after a host interruption.  A completed
        # stage must keep its original evidence, and a running stage must not
        # acquire a second attempt merely because the coordinator retried.
        if state.get("status") in {"passed", "skipped", "deferred"}:
            save_manifest(workspace, feature, run_id, manifest)
            return {
                "batchId": batch_id,
                "stage": stage,
                "attempt": state.get("attempt", 0),
                "status": state.get("status"),
                "reused": True,
            }
        if state.get("status") == "running":
            conflicting = [
                name for name, item in states.items()
                if name != stage and item.get("status") == "running"
            ]
            if conflicting or batch.get("activeStage") not in {None, "", stage}:
                raise ValueError(f"parallel_batch_stage_running_conflict:{batch_id}:{stage}")
            # Older interrupted runs may have lost only the active pointer.
            # Reuse the durable attempt while restoring the actual stage.
            batch["activeStage"] = stage
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
                if states[name].get("status") not in {"passed", "skipped", "deferred"}
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
        if stage == "test":
            state.pop("stopRequested", None)
            state["utestPolicy"] = policy_snapshot(manifest.get("timeoutPerBatch", POLICY["stageSeconds"]))
            for field in ("stageDeadlineEpoch", "firstCommandDeadlineEpoch"):
                state.pop(field, None)
            state.update(stage_budget(manifest, batch))
        batch["activeStage"] = stage
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "batch_stage_started", batchId=batch_id, stage=stage, attempt=state["attempt"])
    return {"batchId": batch_id, "stage": stage, "attempt": state["attempt"], "status": "running"}


def begin_implementation(workspace: Path, feature: str, run_id: str, batch_id: str) -> dict[str, Any]:
    """Register Code before a validated Task Runner start/resume can return.

    Callers must first verify the lease and native worktree binding. This
    function never reopens completed implementation or a delivery-stage run.
    Repeating it for subsequent TASKs/retries preserves the stage attempt.
    """
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        if "implement" not in states or not batch.get("worktreePath"):
            raise ValueError(f"parallel_batch_implementation_workspace_missing:{batch_id}")
        if batch.get("activeStage") not in {None, "", "prepare", "implement"} or any(
            states[name].get("status") == "running" for name in ("review", "test")
        ):
            raise ValueError(f"parallel_batch_implementation_stage_conflict:{batch_id}:{batch.get('activeStage')}")
        if states["implement"].get("status") in {"passed", "skipped", "deferred", "needs_triage"}:
            raise ValueError(f"parallel_batch_implementation_not_open:{batch_id}:{states['implement'].get('status')}")
        if states["prepare"].get("status") in {"deferred", "needs_triage"}:
            raise ValueError(f"parallel_batch_preparation_not_ready:{batch_id}")
        prepared = states["prepare"].get("status") in {"passed", "skipped"}
        metadata = {
            "preparationSource": "task_runner_workspace_binding",
            "worktreePath": batch["worktreePath"],
            "branchName": batch.get("branchName"),
        }
    if not prepared:
        start_stage(workspace, feature, run_id, batch_id, "prepare")
        complete_stage(workspace, feature, run_id, batch_id, "prepare", metadata=metadata)
    return start_stage(workspace, feature, run_id, batch_id, "implement")


def finalize_implementation(workspace: Path, feature: str, run_id: str, batch_id: str) -> dict[str, Any]:
    """Complete Code from the actual sealed commit, including legacy retries."""
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        commit = batch.get("commitSha")
        if not commit or batch.get("status") not in {"sealed", "ready_to_candidate", "merged"}:
            raise ValueError(f"parallel_batch_implementation_delivery_not_sealed:{batch_id}")
        if states["implement"].get("status") == "passed":
            return {"batchId": batch_id, "stage": "implement", "status": "passed", "reused": True}
        # A sealed delivery from before live stage registration can be adopted
        # only when every TASK has a completed run from this exact Batch/run.
        # Active TASKs must never be hidden by sealing or stage completion.
        finished: set[str] = set()
        active: list[str] = []
        feature_dir = run_dir(workspace, feature, run_id).parent.parent
        for path in (feature_dir / ".task-runs").glob("*/*.json"):
            state = json.loads(path.read_text(encoding="utf-8-sig"))
            if state.get("parallelRunId") != run_id or state.get("batchId") != batch_id:
                continue
            if state.get("status") in {"started", "in_progress", "implementation_recording"}:
                active.append(str(state.get("taskId") or path.parent.name))
            elif state.get("status") in {"implemented", "done"} and state.get("implementationEvidenceId"):
                finished.add(str(state.get("taskId") or path.parent.name))
        if active:
            raise ValueError(f"parallel_batch_implementation_tasks_active:{batch_id}:{','.join(sorted(set(active)))}")
        task_ids = set(batch.get("taskIds") or [])
        if not task_ids or not task_ids <= finished:
            raise ValueError(f"parallel_batch_implementation_tasks_incomplete:{batch_id}:{','.join(sorted(task_ids - finished))}")
        running = states["implement"].get("status") == "running"
        metadata = {"batchCommit": commit, "worktreePath": batch.get("worktreePath"), "branchName": batch.get("branchName")}
    if not running:
        begin_implementation(workspace, feature, run_id, batch_id)
    return complete_stage(workspace, feature, run_id, batch_id, "implement", metadata=metadata)


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
        if isinstance(state, dict) and state.get("status") in {"passed", "skipped", "deferred"}:
            return {
                "batchId": batch_id,
                "stage": stage,
                "status": state.get("status"),
                "evidenceId": state.get("latestEvidenceId"),
                "reused": True,
            }
        if not isinstance(state, dict) or state.get("status") != "running":
            raise ValueError(f"parallel_batch_stage_not_running:{batch_id}:{stage}")
        if stage == "test":
            from hooks.utest_control import request_stop_locked
            request_stop_locked(workspace, feature, run_id, batch_id, manifest, batch)
            if state.get("stopRequested"):
                save_manifest(workspace, feature, run_id, manifest)
        if stage == "test" and state.get("stopRequested") and not metadata.get("testFailure"):
            raise ValueError("utest_stop_requested_cannot_pass")
        if stage == "test" and state.get("stopRequested") and (batch.get("lastSeal") or {}).get("stopRequest") != state["stopRequested"]:
            raise ValueError("utest_stop_requires_asset_seal_before_closure")
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
        repaired_failure = None
        if metadata.get("repairDisposition") == "single_repair_accepted":
            prior_failure = state.get("failure")
            if isinstance(prior_failure, dict):
                repaired_failure = dict(prior_failure)
            elif isinstance(metadata.get("repairedFailure"), dict):
                repaired_failure = dict(metadata["repairedFailure"])
        state.update({
            "status": "passed",
            "completedAt": evidence["createdAt"],
            "latestEvidenceId": evidence_id,
            "evidenceIds": [*state.get("evidenceIds", []), evidence_id],
            "inputDigest": _digest(inputs),
            "evidencePath": str(path),
            "failure": None,
        })
        if repaired_failure is not None:
            state["repairResolution"] = {
                "disposition": "single_repair_accepted",
                "failure": repaired_failure,
                "resolvedAt": evidence["createdAt"],
            }
        batch["activeStage"] = None
        if stage == "test":
            batch.pop("utestClosurePending", None)
            if batch.get("worktreePath"):
                from hooks.engineering_facts import FactStore
                store = FactStore(workspace, feature, Path(batch["worktreePath"]))
                owners = set(batch.get("taskIds", [])) | {",".join(sorted(batch.get("taskIds", [])))}
                state["unpublishedClaimIds"] = [fid for tasks in owners for fid in store.close_claims(":".join([run_id, batch_id, tasks]))]
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "batch_stage_completed", batchId=batch_id, stage=stage, evidenceId=evidence_id)
    return {"batchId": batch_id, "stage": stage, "status": "passed", "evidenceId": evidence_id, "evidence": evidence}


def validate_review_result(workspace: Path, feature: str, run_id: str, batch_id: str) -> dict[str, Any]:
    """Return the durable Review decision in one canonical JSON shape.

    Review is made by a model, but the model's prose or tool-return text must
    never be the source of truth for the next Workflow transition.  In
    particular, an implementation finding resets Review to ``pending`` so the
    repaired commit can regenerate all evidence.  The logical decision is
    nevertheless a failed Review and must retain its original finding.
    """
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        state = states.get("review")
        if not isinstance(state, dict):
            raise ValueError(f"parallel_batch_stage_unknown:{batch_id}:review")
        state_status = str(state.get("status") or "pending")
        failure = state.get("failure") if isinstance(state.get("failure"), dict) else None
        if state_status in {"passed", "skipped", "deferred"}:
            return {
                "success": True,
                "batchId": batch_id,
                "stage": "review",
                "status": "passed",
                "evidenceId": state.get("latestEvidenceId"),
                "reused": state_status != "passed",
            }
        if failure and isinstance(failure.get("type"), str) and isinstance(failure.get("message"), str) and failure["message"].strip():
            return {
                "success": True,
                "batchId": batch_id,
                "stage": "review",
                # ``fail_stage`` resets an implementation finding to pending
                # so the rework can rebuild prepare/implement/review evidence.
                # Expose the decision, not that transient reset state.
                "status": "needs_triage" if failure.get("type") == "needs_triage" else "failed",
                "nextStage": failure.get("nextStage"),
                "failure": dict(failure),
                **state.get("repairContext", {}),
                "durableState": state_status,
            }
        return {
            "success": False,
            "batchId": batch_id,
            "stage": "review",
            "status": "incomplete",
            "durableState": state_status,
            "error": f"parallel_review_result_not_final:{batch_id}:{state_status}",
        }


def record_single_repair_resolution(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    *,
    failed_stage: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Replay the evidence sequence after one accepted repair through one CLI.

    This replaces a model-directed sequence of ``start``/``complete`` calls.
    A host interruption remains safely retryable because every transition is
    idempotent and each individual stage has durable evidence.
    """
    stages_by_failure = {
        "review": ("prepare", "implement", "review"),
        # A production defect first proven by UTest invalidates the code and
        # review evidence, but deliberately leaves ``test`` pending.  The
        # Workflow must rerun the exact failing command after the repair.
        "test": ("prepare", "implement", "review"),
    }
    stages = stages_by_failure.get(failed_stage)
    if stages is None:
        raise ValueError(f"parallel_single_repair_stage_invalid:{batch_id}:{failed_stage}")
    if not isinstance(metadata, dict):
        raise ValueError("parallel_single_repair_metadata_must_be_object")
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        failed_state = states.get(failed_stage)
        repaired_failure = (
            dict(failed_state.get("failure"))
            if isinstance(failed_state, dict) and isinstance(failed_state.get("failure"), dict)
            else None
        )
    # A test-proven source defect rebuilds prepare/implement/review but leaves
    # the test itself pending for its exact-command retry. Persist the accepted
    # repair marker on that rebuilt Review evidence, which is also where later
    # UTest failures can reliably determine that the one repair was used.
    repair_marker_stage = failed_stage if failed_stage in stages else stages[-1]
    completed: list[dict[str, Any]] = []
    for stage in stages:
        start_stage(workspace, feature, run_id, batch_id, stage)
        stage_metadata = dict(metadata)
        if stage == repair_marker_stage:
            stage_metadata["repairDisposition"] = "single_repair_accepted"
            if repaired_failure is not None:
                stage_metadata["repairedFailure"] = repaired_failure
        completed.append(complete_stage(
            workspace,
            feature,
            run_id,
            batch_id,
            stage,
            metadata=stage_metadata,
        ))
    final = completed[-1]
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        review = states.get("review") if isinstance(states.get("review"), dict) else {}
        resolution = review.get("repairResolution") if isinstance(review.get("repairResolution"), dict) else {}
        if resolution.get("disposition") != "single_repair_accepted":
            raise ValueError(f"parallel_single_repair_resolution_missing:{batch_id}:{failed_stage}")
    return {
        "success": True,
        "batchId": batch_id,
        "failedStage": failed_stage,
        "status": "success",
        "stage": final.get("stage"),
        "stageEvidenceId": final.get("evidenceId"),
        "stages": completed,
    }


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
    # Test/environment failures are final only after UTest has exhausted its
    # bounded local retry.  A production defect found by a real failing test
    # follows the ordinary implementation-repair path below: it resets stale
    # delivery evidence and returns the Batch to the same Worktree for repair.
    if stage == "test" and failure_type != "implementation":
        return record_test_failure(
            workspace,
            feature,
            run_id,
            batch_id,
            failure_type=failure_type,
            message=message,
        )
    if stage == "test" and failure_type == "implementation":
        # A source failure proven by UTest gets exactly one controlled
        # production repair. If the original command still proves the same
        # class of source failure after that repair, close it as a visible
        # UTest deferral rather than restarting another repair/compile loop.
        with run_lock(workspace, feature, run_id):
            manifest = load_manifest(workspace, feature, run_id)
            batch = _batch(manifest, batch_id)
            states = _ensure_stage_states(batch)
            review = states.get("review") if isinstance(states.get("review"), dict) else {}
            resolution = review.get("repairResolution") if isinstance(review.get("repairResolution"), dict) else {}
            repair_already_used = resolution.get("disposition") == "single_repair_accepted"
        if repair_already_used:
            return record_test_failure(
                workspace,
                feature,
                run_id,
                batch_id,
                failure_type=failure_type,
                message=message,
            )
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
        if batch.get("worktreePath") and stage in {"review", "test"}:
            from hooks.engineering_context import record_repair
            state["repairContext"] = record_repair(workspace, feature, run_id, batch_id, batch,
                                                    stage, dict(state["failure"]))
        if validation_repair:
            batch["activeStage"] = None
            batch["status"] = "blocked"
        elif next_name == "__retry__":
            state.update({"status": "pending", "completedAt": None})
            state["failure"]["nextStage"] = stage
            batch["activeStage"] = None
            # A UTest agent can fail after it has re-sealed test assets. Keep
            # that delivery releasable and recoverable rather than downgrading
            # it to ``running`` and then rejecting `release --final-status
            # sealed` for the same lease.
            batch["status"] = "sealed" if batch.get("commitSha") else "running"
            next_name = stage
        elif next_name is not None:
            # A production repair changes the delivery commit.  All delivery
            # evidence must be regenerated against that new commit, not only
            # the downstream stage that first reported the issue.
            reset_from = (
                "prepare"
                if failure_type == "implementation" and stage in {"review", "test"}
                else next_name
            )
            reset = False
            for name in stage_names(batch):
                if name == reset_from:
                    reset = True
                if reset:
                    states[name].update({"status": "pending", "latestEvidenceId": None, "completedAt": None})
            batch["activeStage"] = None
            batch["status"] = "sealed" if batch.get("commitSha") else "running"
        else:
            batch["status"] = "blocked" if failure_type == "needs_triage" else "failed"
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "batch_stage_failed", batchId=batch_id, stage=stage, failureType=failure_type)
    return {
        "batchId": batch_id,
        "stage": stage,
        "status": state["status"],
        "nextStage": next_name,
        # The Workflow needs the original finding to distinguish a genuine
        # follow-up issue from an unchanged review result after a repair.
        # Returning it also keeps the recovery decision evidence-bound.
        "failure": dict(state.get("failure") or {}),
        **state.get("repairContext", {}),
    }


def prepare_test(workspace: Path, feature: str, run_id: str, batch_id: str) -> dict[str, Any]:
    """Route delivery testing; frontend UTest is temporarily disabled by policy."""
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        if batch.get("type") == "validation":
            raise ValueError("parallel_delivery_batch_required")
        if batch.get("executionLane") != "frontend":
            from hooks.engineering_contract import runtime_contract
            from types import SimpleNamespace
            from hooks.engineering_context import execute, context_reference
            context = execute(SimpleNamespace(action="prepare", workspace=str(workspace), feature=feature,
                run_id=run_id, batch_id=batch_id, batch_worktree=batch.get("worktreePath"),
                task_id=batch.get("taskIds", []), stage="utest", owner_token=None)) if batch.get("worktreePath") else None
            current_test = batch.get("stageStates", {}).get("test", {})
            if current_test.get("status") == "running":
                from hooks.engineering_context import read_json
                execution = read_json(run_dir(workspace, feature, run_id) / "utest" / batch_id / "UTEST_EXECUTION.json")
                selected_policy = stage_budget(manifest, batch, execution)["utestPolicy"]
            else:
                selected_policy = policy_snapshot(manifest.get("timeoutPerBatch", POLICY["stageSeconds"]))
            return {"success": True, "batchId": batch_id, "status": "test_required",
                    "runtimeContractDigest": runtime_contract()["digest"],
                    "utestPolicy": selected_policy,
                    "engineeringContext": context_reference(context) if context else {"status": "worktree_not_provisioned"}}
        states = _ensure_stage_states(batch)
        if states["review"].get("status") != "passed":
            raise ValueError(f"frontend_utest_skip_requires_review_passed:{batch_id}")
        if any(states[stage].get("status") not in {"passed", "skipped"} for stage in ("prepare", "implement")):
            raise ValueError(f"frontend_utest_skip_requires_implementation_complete:{batch_id}")
        if any(state.get("status") == "running" for stage, state in states.items() if stage != "test"):
            raise ValueError(f"parallel_batch_stage_running_conflict:{batch_id}:test")
        if not batch.get("commitSha") or not batch.get("worktreePath") or not batch.get("branchName"):
            raise ValueError(f"frontend_utest_skip_requires_sealed_delivery:{batch_id}")
        state = states["test"]
        inputs = _stage_input(manifest, batch, "test", {})
        reused = (state.get("status") == "skipped" and state.get("skipReason") == FRONTEND_UTEST_SKIP_REASON
                  and state.get("inputDigest") == _digest(inputs))
        if not reused:
            attempt = int(state.get("attempt", 0)) + 1
            evidence_id = f"STAGE-{batch_id}-TEST-{attempt:03d}"
            evidence = {
                "evidenceId": evidence_id, "batchId": batch_id, "stage": "test", "attempt": attempt,
                "createdAt": _utc_now(), "inputs": inputs, "outcome": "SKIP",
                "metadata": {"executionLane": "frontend", "executionOutcome": "not_run", "skipReason": FRONTEND_UTEST_SKIP_REASON},
            }
            evidence["digest"] = _digest(evidence)
            path = _evidence_path(workspace, feature, run_id, batch_id, "test", attempt)
            atomic_write_json(path, evidence)
            state.update({
                "status": "skipped", "attempt": attempt, "completedAt": evidence["createdAt"],
                "skipReason": FRONTEND_UTEST_SKIP_REASON, "executionOutcome": "not_run",
                "latestEvidenceId": evidence_id, "evidencePath": str(path), "inputDigest": _digest(inputs),
                "evidenceIds": [*state.get("evidenceIds", []), evidence_id],
            })
        batch["activeStage"] = None
        save_manifest(workspace, feature, run_id, manifest)
        result = {
            "success": True, "batchId": batch_id, "stage": "test", "status": "skipped", "testStatus": "skipped",
            "skipReason": FRONTEND_UTEST_SKIP_REASON, "worktreePath": batch["worktreePath"],
            "branchName": batch["branchName"], "commitSha": batch["commitSha"],
            "stageEvidenceId": state["latestEvidenceId"], "reused": reused,
        }
    if not reused:
        append_event(workspace, feature, run_id, "batch_stage_skipped", batchId=batch_id, stage="test", skipReason=FRONTEND_UTEST_SKIP_REASON)
    return result


def record_test_failure(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    *,
    failure_type: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Durably defer a non-repairable UTest failure without claiming a pass.

    This is the terminal escape hatch for an exhausted test/environment retry
    budget. It keeps the evidence and lets this delivery continue as an
    explicitly unverified candidate. The final B-E2E remains mandatory and
    the deferred UTest finding remains visible in the run result.
    """
    if failure_type not in FAILURE_NEXT_STAGE:
        raise ValueError(f"parallel_batch_failure_type_invalid:{failure_type}")
    metadata = dict(metadata or {})
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        state = states.get("test")
        if not isinstance(state, dict) or state.get("status") != "running":
            raise ValueError(f"parallel_batch_stage_not_running:{batch_id}:test")
        failure = {
            "type": failure_type,
            "message": message,
            "nextStage": None,
        }
        repair_context = {}
        if batch.get("worktreePath"):
            from hooks.engineering_context import record_repair
            repair_context = record_repair(workspace, feature, run_id, batch_id, batch, "test", dict(failure))
        issue_index = 1 + sum(
            1
            for item in manifest.get("deferredIssues", [])
            if isinstance(item, dict)
            and item.get("batchId") == batch_id
            and item.get("stage") == "test"
            and item.get("kind") == "test_failure"
        )
        issue_id = f"TEST-FAILED-{batch_id}-{issue_index:03d}"

    # Record the failure in normal immutable stage evidence before changing
    # state to ``deferred``.  This lets aggregate/reporting distinguish it
    # from a passed UTest without treating it as a successful test run.
    completed = complete_stage(
        workspace,
        feature,
        run_id,
        batch_id,
        "test",
        metadata={
            **metadata,
            "testFailure": failure,
            "testFailureDisposition": "recorded_deferred",
            "testFailureIssueId": issue_id,
        },
    )
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        state = states["test"]
        if repair_context:
            state["repairContext"] = repair_context
        if state.get("status") != "passed" or state.get("latestEvidenceId") != completed.get("evidenceId"):
            raise ValueError(f"parallel_batch_test_failure_completion_changed:{batch_id}")
        issue = {
            "issueId": issue_id,
            "kind": "test_failure",
            "batchId": batch_id,
            "stage": "test",
            "failureType": failure_type,
            "message": message,
            "disposition": "recorded_deferred",
            "blocksWorkflow": False,
            "status": "open",
            "evidenceId": completed["evidenceId"],
            "batchCommit": (completed.get("evidence") or {}).get("inputs", {}).get("batchCommit"),
            "createdAt": _utc_now(),
        }
        issues = manifest.setdefault("deferredIssues", [])
        if not isinstance(issues, list):
            raise ValueError("parallel_deferred_issues_invalid")
        issues.append(issue)
        state.update({
            "status": "deferred",
            "failure": failure,
            "deferredIssueId": issue_id,
            "deferredDisposition": "test_failure_recorded_deferred",
        })
        batch["activeStage"] = None
        save_manifest(workspace, feature, run_id, manifest)
    append_event(
        workspace,
        feature,
        run_id,
        "batch_test_failure_recorded",
        batchId=batch_id,
        stage="test",
        issueId=issue_id,
        failureType=failure_type,
    )
    return {"success": True, "batchId": batch_id, "stage": "test", "status": "deferred", "issue": issue}


def defer_stage(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    stage: str,
    *,
    disposition: str,
) -> dict[str, Any]:
    """Persist an unresolved implementation finding and advance the pipeline."""
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        state = states.get(stage)
        if not isinstance(state, dict) or state.get("status") not in {"pending", "failed"}:
            raise ValueError(f"parallel_batch_stage_not_deferable:{batch_id}:{stage}")
        failure = dict(state.get("failure") or {})
        if failure.get("type") != "implementation" or failure.get("nextStage") != "implement":
            raise ValueError(f"parallel_batch_stage_deferred_failure_invalid:{batch_id}:{stage}")
        issue_index = 1 + sum(
            1
            for item in manifest.get("deferredIssues", [])
            if isinstance(item, dict) and item.get("batchId") == batch_id and item.get("stage") == stage
        )
        issue_id = f"DEFERRED-{batch_id}-{stage.upper()}-{issue_index:03d}"

    # Write ordinary, content-bound evidence first.  The state is then made
    # explicitly ``deferred`` so it cannot be mistaken for a successful review.
    start_stage(workspace, feature, run_id, batch_id, stage)
    completed = complete_stage(
        workspace,
        feature,
        run_id,
        batch_id,
        stage,
        metadata={"deferredIssueId": issue_id, "deferredDisposition": disposition, "failure": failure},
    )
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        states = _ensure_stage_states(batch)
        state = states[stage]
        if state.get("status") != "passed" or state.get("latestEvidenceId") != completed.get("evidenceId"):
            raise ValueError(f"parallel_batch_stage_defer_completion_changed:{batch_id}:{stage}")
        issue = {
            "issueId": issue_id,
            "kind": "implementation_finding",
            "batchId": batch_id,
            "stage": stage,
            "failureType": failure["type"],
            "message": str(failure.get("message") or "implementation_issue"),
            "disposition": disposition,
            "blocksWorkflow": True,
            "status": "open",
            "evidenceId": completed["evidenceId"],
            "batchCommit": (completed.get("evidence") or {}).get("inputs", {}).get("batchCommit"),
            "createdAt": _utc_now(),
        }
        issues = manifest.setdefault("deferredIssues", [])
        if not isinstance(issues, list):
            raise ValueError("parallel_deferred_issues_invalid")
        issues.append(issue)
        state.update({"status": "deferred", "failure": failure, "deferredIssueId": issue_id})
        batch["activeStage"] = None
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "batch_stage_deferred", batchId=batch_id, stage=stage, issueId=issue_id, disposition=disposition)
    return {"success": True, "batchId": batch_id, "stage": stage, "status": "deferred", "issue": issue}


def gate_batch(workspace: Path, feature: str, run_id: str, batch_id: str) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = _batch(manifest, batch_id)
        if batch.get("utestClosurePending"):
            return {"success": False, "batchId": batch_id, "error": "utest_asset_closure_pending"}
        states = _ensure_stage_states(batch)
        deferred = [stage for stage in stage_names(batch) if states[stage].get("status") == "deferred"]
        # UTest is the only delivery stage that may defer without stopping its
        # Batch. It is explicitly carried to an `allow-unverified` candidate
        # and final B-E2E. Deferred prepare/implement/review findings still
        # represent unresolved production-quality issues and block the gate.
        blocking_deferred = [stage for stage in deferred if stage != "test"]
        if blocking_deferred:
            batch["status"] = "blocked"
            batch["activeStage"] = None
            save_manifest(workspace, feature, run_id, manifest)
            return {
                "success": False,
                "batchId": batch_id,
                "error": "parallel_batch_stage_gate_deferred_findings",
                "deferredStages": blocking_deferred,
            }
        missing = [
            stage
            for stage in stage_names(batch)
            if states[stage].get("status") not in {"passed", "skipped"}
            and stage not in deferred
        ]
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
    return {
        "success": True,
        "batchId": batch_id,
        "status": batch["status"],
        "continuedTestFailureStages": [stage for stage in deferred if stage == "test"],
    }


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


def build_parser() -> argparse.ArgumentParser:
    parser = RuntimeArgumentParser(description="Advance a staged parallel Batch")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("next", "start", "complete", "finalize-implementation", "prepare-test", "check-test-budget", "fail", "record-test-failure", "defer", "gate", "triage-failure", "reset-validation", "validate-review-result", "record-single-repair"):
        item = sub.add_parser(name)
        item.add_argument("--workspace")
        item.add_argument("--feature", required=True)
        item.add_argument("--run-id", required=True)
        item.add_argument("--batch-id", required=True)
        item.add_argument("--dispatch-generation", type=int)
        item.add_argument("--dispatch-token")
        if name in {"start", "complete", "fail", "defer", "triage-failure"}:
            item.add_argument("--stage", required=True)
        if name == "complete":
            item.add_argument("--metadata-json", default="{}")
        if name in {"fail", "record-test-failure", "triage-failure"}:
            item.add_argument("--failure-type", required=True, choices=tuple(FAILURE_NEXT_STAGE))
        if name in {"fail", "record-test-failure"}:
            item.add_argument("--message", required=True)
        if name == "record-test-failure":
            item.add_argument("--metadata-json", default="{}")
        if name == "defer":
            item.add_argument("--disposition", required=True, choices=("repeated_feedback", "repair_limit_reached", "no_new_commit"))
        if name == "reset-validation":
            item.add_argument("--candidate-sha", required=True)
            item.add_argument("--candidate-base-sha", required=True)
            item.add_argument("--train-id", required=True)
        if name == "record-single-repair":
            item.add_argument("--failed-stage", required=True)
            item.add_argument("--metadata-json", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    authority = _CLI_DISPATCH_AUTH.set({"generation": args.dispatch_generation, "token": args.dispatch_token})
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        if args.command == "next":
            result = next_stage(workspace, feature, args.run_id, args.batch_id)
        elif args.command == "start":
            result = start_stage(workspace, feature, args.run_id, args.batch_id, args.stage)
        elif args.command == "finalize-implementation":
            result = finalize_implementation(workspace, feature, args.run_id, args.batch_id)
        elif args.command == "prepare-test":
            result = prepare_test(workspace, feature, args.run_id, args.batch_id)
        elif args.command == "check-test-budget":
            from hooks.utest_control import request_stop_locked
            with run_lock(workspace, feature, args.run_id):
                manifest = load_manifest(workspace, feature, args.run_id)
                batch = _batch(manifest, args.batch_id)
                result = {"success": True, **request_stop_locked(workspace, feature, args.run_id, args.batch_id, manifest, batch)}
                save_manifest(workspace, feature, args.run_id, manifest)
        elif args.command == "complete":
            metadata = json.loads(args.metadata_json)
            if not isinstance(metadata, dict):
                raise ValueError("parallel_batch_stage_metadata_must_be_object")
            result = complete_stage(workspace, feature, args.run_id, args.batch_id, args.stage, metadata=metadata)
        elif args.command == "fail":
            result = fail_stage(workspace, feature, args.run_id, args.batch_id, args.stage, failure_type=args.failure_type, message=args.message)
        elif args.command == "record-test-failure":
            metadata = json.loads(args.metadata_json)
            if not isinstance(metadata, dict):
                raise ValueError("parallel_batch_stage_metadata_must_be_object")
            result = record_test_failure(
                workspace,
                feature,
                args.run_id,
                args.batch_id,
                failure_type=args.failure_type,
                message=args.message,
                metadata=metadata,
            )
        elif args.command == "defer":
            result = defer_stage(workspace, feature, args.run_id, args.batch_id, args.stage, disposition=args.disposition)
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
        elif args.command == "validate-review-result":
            result = validate_review_result(workspace, feature, args.run_id, args.batch_id)
        elif args.command == "record-single-repair":
            metadata = json.loads(args.metadata_json)
            result = record_single_repair_resolution(
                workspace,
                feature,
                args.run_id,
                args.batch_id,
                failed_stage=args.failed_stage,
                metadata=metadata,
            )
        else:
            result = gate_batch(workspace, feature, args.run_id, args.batch_id)
        print(json.dumps({"ok": result.get("success", True), **result}, ensure_ascii=False, indent=2))
        return 0 if result.get("success", True) else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        _CLI_DISPATCH_AUTH.reset(authority)


if __name__ == "__main__":
    raise SystemExit(main())
