#!/usr/bin/env python3
"""Plan-aware scheduler commands for parallel Code batch runs.

This process never implements code itself.  It creates and updates the durable
run manifest consumed by the workflow runtime and Task Runner entrypoints.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import feature_dir, resolve_feature, resolve_workspace
from hooks.commit_message import build_commit_message, normalize_task_card_id
from hooks.git_runtime import git_command
from hooks.evidence_kernel import FileLock
from hooks.parallel_runtime import (
    append_event,
    advance_dispatch_generation,
    batch_occupies_scheduler_slot,
    batch_write_sets_conflict,
    create_manifest,
    DELIVERY_STAGES,
    INACTIVE_RUN_STATES,
    get_active_run,
    lease_path,
    list_runs,
    load_manifest,
    parallel_plan_errors,
    plan_digest,
    mergeable_batches,
    stage_recovery_batches,
    ready_batches,
    resource_groups,
    select_runnable_batches,
    run_lock,
    run_dir,
    save_manifest,
    validate_cli_dispatch,
)
from hooks.plan_json import load_plan_bundle
from hooks.parallel_integration import (
    PROMOTION_STATES, assert_integration, initialize_integrations,
    integration_repo, isolated, promotion_view,
)
from hooks.parallel_validation_ownership import validation_ownership_errors
from hooks.repository_snapshot import (
    PLATFORM_RUNTIME_DIRECTORY,
    RepositorySnapshotError,
    current_git_branch,
    git_status_porcelain,
    resolve_git_root,
    working_tree_changed_files,
)
_BOOTSTRAP_IGNORE_RULES = (
    ".cmbdevclaw/large_tool_results/",
    ".autobizdevops/",
    ".autobizdevops/features/*/.parallel-runs/",
    ".autobizdevops/worktrees/",
)
MAX_AUTOMATIC_BATCH_RECOVERY_ATTEMPTS = 20


def _current_invocation_id(manifest: dict[str, Any], explicit_invocation_id: str | None = None) -> str | None:
    if explicit_invocation_id:
        return explicit_invocation_id
    history = manifest.get("workflowInvocationIds")
    if isinstance(history, list) and history:
        last = history[-1]
        if isinstance(last, str) and last:
            return last
    return None


def _parse_timestamp_epoch(value: object) -> float | None:
    """Parse one persisted UTC timestamp without making recovery fragile."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _next_open_delivery_stage(batch: dict[str, Any]) -> str | None:
    """Return the first delivery stage that has not reached a durable end state."""
    states = batch.get("stageStates") if isinstance(batch.get("stageStates"), dict) else {}
    return next(
        (
            stage
            for stage in DELIVERY_STAGES
            if not isinstance(states.get(stage), dict)
            or states[stage].get("status") not in {"passed", "skipped", "deferred"}
        ),
        None,
    )


def _lease_staleness_reason_locked(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    batch: dict[str, Any],
    *,
    now: float,
    timeout_seconds: int,
) -> str | None:
    """Return why an active Batch has lost execution authority, if it has.

    This helper runs while the scheduler owns the run lock and takes the
    per-Batch lease lock before inspecting the bearer lease.  A valid lease is
    intentionally left untouched: resuming a workflow must never steal work
    from a worker that is still renewing its lease.  Conversely, a missing, corrupt,
    expired, or over-deadline lease has no safe worker authority
    left and can be deterministically retried by the scheduler.
    """
    path = lease_path(workspace, feature, run_id, batch_id)
    with FileLock(path.with_suffix(".lock"), timeout=5):
        dispatch = batch.get("dispatch") or {}
        if (dispatch.get("generation") == batch.get("dispatchGeneration", 1)
                and (dispatch.get("status") in {"failed", "retry_pending", "expired", "revoked"}
                     or (dispatch.get("status") == "running" and dispatch.get("expiresEpoch", 0) <= now))):
            if path.exists():
                path.unlink()
            return "dispatch_terminated"
        if not path.is_file():
            if (dispatch.get("status") == "running" and dispatch.get("generation") == batch.get("dispatchGeneration", 1)
                    and dispatch.get("expiresEpoch", 0) > now):
                return None  # Control/stage handoffs may intentionally have no business lease.
            return "lease_missing"
        try:
            lease = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            if path.exists():
                path.unlink()
            return "lease_invalid"
        if not isinstance(lease, dict):
            if path.exists():
                path.unlink()
            return "lease_invalid"
        try:
            expires_epoch = float(lease.get("expiresEpoch", 0))
        except (TypeError, ValueError):
            expires_epoch = 0
        if expires_epoch <= now:
            if path.exists():
                path.unlink()
            return "lease_expired"

        # Each acquire begins a new implementation or repair attempt. The
        # original Batch startedAt can be hours old by the time Fix begins.
        # Honour the guarded lease's effective TTL for older run manifests.
        started_epoch = _parse_timestamp_epoch(lease.get("startedAt"))
        try:
            lease_ttl = int(lease.get("ttlSeconds", 0))
        except (TypeError, ValueError):
            lease_ttl = 0
        attempt_timeout = max(1, timeout_seconds, lease_ttl)
        if started_epoch is not None and now - started_epoch >= attempt_timeout:
            if path.exists():
                path.unlink()
            return "batch_timeout"
    return None


def _is_non_retryable_blocked(batch: dict[str, Any]) -> bool:
    recovery = batch.get("recovery")
    if not isinstance(recovery, dict):
        return False
    return (
        recovery.get("nonRetryable") is True
        or recovery.get("status") == "blocked_non_retryable"
        or recovery.get("failureClassification") == "non_retryable"
    )


def _is_escalated_blocked(batch: dict[str, Any]) -> bool:
    recovery = batch.get("recovery")
    return isinstance(recovery, dict) and recovery.get("status") == "escalated_to_operator"


def _is_terminal_operator_blocked(batch: dict[str, Any]) -> bool:
    return _is_non_retryable_blocked(batch) or _is_escalated_blocked(batch)


def _is_retry_exhausted_blocked(
    batch: dict[str, Any],
    current_invocation_id: str | None,
    *,
    allow_new_invocation_retry: bool = False,
) -> bool:
    _ = current_invocation_id, allow_new_invocation_retry
    recovery = batch.get("recovery")
    if not isinstance(recovery, dict):
        return False
    return recovery.get("status") in {"retry_exhausted", "escalated_to_operator"}


def _escalate_batch_locked(
    workspace: Path,
    feature: str,
    run_id: str,
    manifest: dict[str, Any],
    batch_id: str,
    batch: dict[str, Any],
    *,
    error: str,
    reason_code: str,
    category: str,
    classification: str = "precondition",
    fingerprint: str | None = None,
    generation: int | None = None,
    hook_block_code: str | None = None,
    non_retryable: bool = False,
    recovery_overrides: dict[str, Any] | None = None,
) -> bool:
    """Atomically transition a Batch to an operator-owned terminal block and revoke credentials."""
    previous_recovery = batch.get("recovery") if isinstance(batch.get("recovery"), dict) else {}
    effective_gen = generation if generation is not None else int(batch.get("dispatchGeneration", 1))
    dispatch = batch.get("dispatch")
    if isinstance(dispatch, dict):
        dispatch.update(status="revoked", revokedAtEpoch=time.time(), completedEpoch=time.time())
    else:
        batch["dispatchManaged"] = True
    (run_dir(workspace, feature, run_id) / "dispatch" / f"{batch_id}.json").unlink(missing_ok=True)
    status_name = "blocked_non_retryable" if non_retryable else "escalated_to_operator"
    recovery_state = {
        **previous_recovery,
        **(recovery_overrides or {}),
        "status": status_name,
        "escalatedAt": manifest.get("updatedAt"),
        "escalationCategory": category,
        "reasonCode": reason_code,
        "rootCause": error,
        "lastFailureAt": manifest.get("updatedAt"),
        "lastError": error,
        "failureClassification": "non_retryable" if non_retryable else classification,
        "failureFingerprint": fingerprint or previous_recovery.get("failureFingerprint"),
        "lastChargedGeneration": effective_gen,
        "hookBlockCode": hook_block_code,
        "nextAction": "resolve_escalated_batches_then_reopen",
    }
    recovery_state.pop("pendingAttempt", None)
    if non_retryable:
        recovery_state["nonRetryable"] = True
    else:
        recovery_state.pop("nonRetryable", None)
    batch.update({
        "status": "blocked",
        "error": error,
        "activeStage": None,
        "startedAt": None,
        "completedAt": manifest.get("updatedAt"),
        "recovery": recovery_state,
    })
    manifest["status"] = "blocked"
    return _clear_retry_lease_locked(workspace, feature, run_id, batch_id, batch)


def _mark_retry_pending_locked(
    workspace: Path,
    feature: str,
    run_id: str,
    manifest: dict[str, Any],
    batch_id: str,
    batch: dict[str, Any],
    *,
    error: str,
    previous_status: str | None = None,
    allow_manual_override: bool = False,
    reset_invocation_budget: bool = False,
    invocation_id: str | None = None,
    generation: int | None = None,
    charge_failure: bool = True,
) -> bool:
    """Persist one task-scoped recovery or escalation transition under P0-P4 precedence."""
    if not allow_manual_override and _is_terminal_operator_blocked(batch):
        return _clear_retry_lease_locked(workspace, feature, run_id, batch_id, batch)
    if batch.get("mergeCommitSha"):
        batch["status"] = "merged"
        if isinstance(batch.get("recovery"), dict):
            batch["recovery"].pop("pendingAttempt", None)
        return False

    previous = previous_status if previous_status is not None else str(batch.get("status") or "")
    previous_recovery = batch.get("recovery") if isinstance(batch.get("recovery"), dict) else {}
    if previous == "blocked" and previous_recovery.get("resumeStatus"):
        previous = str(previous_recovery["resumeStatus"])

    effective_gen = generation if generation is not None else int(batch.get("dispatchGeneration", 1))
    from hooks.parallel_failure import (
        MAX_TRANSIENT_EXTRA_RETRIES,
        MAX_UNCLASSIFIED_RETRIES,
        find_verified_hook_block,
        recovery_failure,
    )
    verified_hook = (
        None
        if allow_manual_override
        else find_verified_hook_block(workspace, feature, run_id, batch_id, effective_gen)
    )
    verified_hook_code = str(verified_hook["errorCode"]) if isinstance(verified_hook, dict) and verified_hook.get("errorCode") else None

    reopened_missing_review_receipt = False
    if "parallel_batch_review_not_bound_to_seal" in error:
        # A passed flag without the sealed commit's receipt is incomplete.
        # Reopen only Review; never replay the implementation or invent evidence.
        review = (batch.get("stageStates") or {}).get("review")
        if isinstance(review, dict) and review.get("status") in {"passed", "skipped", "deferred"}:
            review.update(status="pending", latestEvidenceId=None, completedAt=None)
            reopened_missing_review_receipt = True
        if previous == "ready_to_candidate":
            previous = "sealed"

    failure = recovery_failure(
        error,
        batch,
        run_dir(workspace, feature, run_id) / "engineering" / batch_id,
        verified_hook_code=verified_hook_code,
    )
    bucket = failure["bucket"]
    is_contention = bucket == "contention"
    superseded_reclaim = error.startswith("workflow_invocation_superseded:")

    # P0: Check whether an interrupted/transient worker actually persisted its target stage receipt.
    pending_attempt = previous_recovery.get("pendingAttempt") if isinstance(previous_recovery.get("pendingAttempt"), dict) else {}
    can_adopt_stage_receipt = (
        not allow_manual_override
        and not failure.get("nonRetryable")
        and bucket != "precondition"
        and previous != "ready_to_candidate"
        and pending_attempt.get("source") != "merge_candidate"
        and previous_recovery.get("kind") != "integration_resume"
    )
    sealed_progress = (
        can_adopt_stage_receipt
        and previous not in {"sealed", "ready_to_candidate"}
        and pending_attempt.get("source") in {"initial", "implementation_recovery"}
        and not pending_attempt.get("targetCommitSha")
        and bool(batch.get("commitSha"))
        and _sealed_delivery_error(manifest, batch_id) is None
    )
    review_state = ((batch.get("stageStates") or {}).get("review") or {}) if isinstance(batch.get("stageStates"), dict) else {}
    review_progress = (
        can_adopt_stage_receipt
        and not reopened_missing_review_receipt
        and bool(batch.get("commitSha"))
        and review_state.get("status") == "passed"
        and bool(review_state.get("evidencePath"))
        and Path(str(review_state.get("evidencePath"))).is_file()
        and _sealed_delivery_error(manifest, batch_id) is None
    )
    receipt_adopted = sealed_progress or review_progress

    dispatch_inv = (batch.get("dispatch") or {}).get("invocationId") if isinstance(batch.get("dispatch"), dict) else None
    current_inv = _current_invocation_id(
        manifest,
        invocation_id or (dispatch_inv if isinstance(dispatch_inv, str) and dispatch_inv else None),
    )

    already_charged = (
        not allow_manual_override
        and not reset_invocation_budget
        and previous_recovery.get("lastChargedGeneration") == effective_gen
    )
    charge_this_attempt = not (
        not charge_failure or is_contention or receipt_adopted or superseded_reclaim
        or allow_manual_override or reset_invocation_budget or already_charged
    )

    retry_attempts = (
        int(previous_recovery.get("retryAttempts", 0))
        if not charge_this_attempt
        else int(previous_recovery.get("retryAttempts", 0)) + 1
    )
    transient_attempts = (
        0
        if allow_manual_override
        else int(previous_recovery.get("transientRetryAttempts", 0)) + (1 if (charge_this_attempt and bucket == "transient") else 0)
    )
    unclassified_attempts = (
        0
        if allow_manual_override
        else int(previous_recovery.get("unclassifiedRetryAttempts", 0)) + (1 if (charge_this_attempt and bucket == "unclassified") else 0)
    )

    resume_status = (
        "ready_to_candidate"
        if previous == "ready_to_candidate"
        else "sealed"
        if batch.get("commitSha")
        else "pending"
    )
    implementation_resume = (
        _implementation_resume_details(manifest, batch_id, batch)
        if resume_status == "pending"
        else None
    )
    recovery_kind = (
        "remediation"
        if previous == "remediation_pending"
        else "integration_resume"
        if resume_status == "ready_to_candidate"
        else "stage_resume"
        if resume_status == "sealed"
        else "implementation_resume"
        if implementation_resume is not None
        else "retry_dispatch"
    )

    # P2: Non-retryable or deterministic precondition / verified same-generation Hook block.
    if charge_failure and not allow_manual_override and not receipt_adopted and not reopened_missing_review_receipt:
        if failure.get("nonRetryable"):
            return _escalate_batch_locked(
                workspace, feature, run_id, manifest, batch_id, batch,
                error=error, reason_code=failure["reasonCode"], category="non_retryable",
                classification="non_retryable", fingerprint=failure["fingerprint"],
                generation=effective_gen, non_retryable=True,
                recovery_overrides={"retryAttempts": retry_attempts, "resumeStatus": resume_status, "kind": recovery_kind},
            )
        if bucket == "precondition":
            # Phase 2 narrow remediation path for unattributed changes in a verified isolated worktree.
            remediation_enabled = bool((manifest.get("runtimeConfig") or {}).get("enableUnattributedRemediation", True))
            if (
                remediation_enabled
                and "prestart_unattributed_changes_detected" in error
                and implementation_resume is not None
                and int(previous_recovery.get("remediationAttempts", 0)) == 0
            ):
                recovery_state = {
                    **previous_recovery,
                    "retryAttempts": retry_attempts,
                    "lastChargedGeneration": effective_gen,
                    "lastFailureAt": manifest.get("updatedAt"),
                    "lastError": error,
                    "failureClassification": "precondition",
                    "failureFingerprint": failure["fingerprint"],
                    "reasonCode": "prestart_unattributed_changes_detected",
                    "resumeStatus": "pending",
                    "kind": "remediation",
                    "remediationKind": "unattributed_changes",
                    "preserveWorktree": True,
                    "reprovision": False,
                    "implementationResume": implementation_resume,
                    "status": "remediation_required",
                }
                recovery_state.pop("pendingAttempt", None)
                batch.update({
                    "status": "remediation_pending",
                    "error": error,
                    "activeStage": None,
                    "startedAt": None,
                    "completedAt": None,
                    "recovery": recovery_state,
                })
                advance_dispatch_generation(batch)
                (run_dir(workspace, feature, run_id) / "dispatch" / f"{batch_id}.json").unlink(missing_ok=True)
                return _clear_retry_lease_locked(workspace, feature, run_id, batch_id, batch)
            category = "hook_blocked" if verified_hook_code else "precondition"
            return _escalate_batch_locked(
                workspace, feature, run_id, manifest, batch_id, batch,
                error=error, reason_code=failure["reasonCode"], category=category,
                classification="precondition", fingerprint=failure["fingerprint"],
                generation=effective_gen, hook_block_code=verified_hook_code,
                recovery_overrides={"retryAttempts": retry_attempts, "resumeStatus": resume_status, "kind": recovery_kind},
            )
        # P3: Explicit transient budget (1 initial + 2 extra retries = max 3 failed attempts).
        if bucket == "transient" and transient_attempts > MAX_TRANSIENT_EXTRA_RETRIES:
            return _escalate_batch_locked(
                workspace, feature, run_id, manifest, batch_id, batch,
                error=error, reason_code="transient_retry_budget_exhausted",
                category="transient_exhausted", classification="transient",
                fingerprint=failure["fingerprint"], generation=effective_gen,
                recovery_overrides={
                    "retryAttempts": retry_attempts,
                    "transientRetryAttempts": transient_attempts,
                    "resumeStatus": resume_status,
                    "kind": recovery_kind,
                },
            )
        # P4: Unclassified fallback — fingerprint repetition or >1 retry escalates immediately.
        if bucket == "unclassified":
            same_fingerprint = (
                charge_this_attempt
                and previous_recovery.get("failureFingerprint") == failure["fingerprint"]
                and int(previous_recovery.get("unclassifiedRetryAttempts", 0)) >= 1
            )
            if same_fingerprint or unclassified_attempts > MAX_UNCLASSIFIED_RETRIES:
                reason_code = "repeated_failure_fingerprint" if same_fingerprint else "unclassified_retry_budget_exhausted"
                return _escalate_batch_locked(
                    workspace, feature, run_id, manifest, batch_id, batch,
                    error=error, reason_code=reason_code, category="repeated_failure",
                    classification="retryable", fingerprint=failure["fingerprint"],
                    generation=effective_gen,
                    recovery_overrides={
                        "retryAttempts": retry_attempts,
                        "unclassifiedRetryAttempts": unclassified_attempts,
                        "resumeStatus": resume_status,
                        "kind": recovery_kind,
                    },
                )

    recovery_state = {
        **previous_recovery,
        "retryAttempts": retry_attempts,
        "transientRetryAttempts": transient_attempts,
        "unclassifiedRetryAttempts": unclassified_attempts,
        "invocationRetryAttempts": transient_attempts + unclassified_attempts,
        "retryInvocationId": current_inv,
        **({"lastChargedGeneration": effective_gen} if charge_this_attempt else {}),
        "lastFailureAt": manifest.get("updatedAt"),
        "lastError": error,
        "failureClassification": failure["classification"],
        "failureFingerprint": failure["fingerprint"],
        "reasonCode": failure["reasonCode"],
        "resumeStatus": resume_status,
        "kind": recovery_kind,
        "resumeFromStage": (
            _next_open_delivery_stage(batch)
            if recovery_kind == "stage_resume"
            else "implement"
            if recovery_kind == "implementation_resume"
            else None
        ),
        "preserveWorktree": recovery_kind in {"stage_resume", "implementation_resume", "remediation"},
        "reprovision": recovery_kind == "retry_dispatch",
        **({"implementationResume": implementation_resume} if implementation_resume is not None else {}),
        "status": "remediation_required" if recovery_kind == "remediation" else "pending_retry",
    }
    for stale_key in (
        "pendingAttempt", "nonRetryable", "exhaustedInvocationId", "maxAutomaticAttempts",
        "nextAction", "exhaustedReported", "escalatedAt", "escalationCategory", "rootCause", "hookBlockCode",
    ):
        recovery_state.pop(stale_key, None)
    if allow_manual_override:
        recovery_state["lastChargedGeneration"] = None
    batch.update(
        {
            "status": "remediation_pending" if recovery_kind == "remediation" else "retry_pending",
            "error": None if allow_manual_override else error,
            "activeStage": None,
            "startedAt": None,
            "completedAt": None,
            "recovery": recovery_state,
        }
    )
    if recovery_kind == "remediation":
        advance_dispatch_generation(batch)
        (run_dir(workspace, feature, run_id) / "dispatch" / f"{batch_id}.json").unlink(missing_ok=True)
    return _clear_retry_lease_locked(workspace, feature, run_id, batch_id, batch)


def _recover_stale_active_batches_locked(
    workspace: Path,
    feature: str,
    run_id: str,
    manifest: dict[str, Any],
    delivery_owned: set[str],
) -> list[dict[str, str]]:
    """Recover lost workers, including lease-free interrupted Review stages."""
    now = time.time()
    timeout_seconds = max(1, int(manifest.get("timeoutPerBatch", 3600)))
    recovered: list[dict[str, str]] = []
    for raw_batch_id, batch in manifest.get("batches", {}).items():
        if not isinstance(batch, dict) or raw_batch_id in delivery_owned or _is_terminal_operator_blocked(batch):
            continue
        dispatch = batch.get("dispatch") or {}
        consumed_grant = (dispatch.get("generation") == batch.get("dispatchGeneration", 1)
                          and dispatch.get("status") in {"completed", "failed", "retry_pending", "remediation_pending", "expired", "revoked"})
        failed_pending_grant = (batch.get("status") == "pending"
                                and dispatch.get("generation") == batch.get("dispatchGeneration", 1)
                                and dispatch.get("status") in {"failed", "retry_pending", "expired", "revoked"})
        sealed_running = (batch.get("status") == "sealed"
                          and (batch.get("recovery") or {}).get("status") != "rescheduled"
                          and any((batch.get("stageStates") or {}).get(stage, {}).get("status") == "running"
                                  for stage in DELIVERY_STAGES))
        delivery_handoff_lost = batch.get("status") in {"sealed", "ready_to_candidate"} and consumed_grant
        if (batch.get("status") not in {"leased", "running"}
                and not sealed_running and not delivery_handoff_lost and not failed_pending_grant):
            continue
        batch_id = str(raw_batch_id)
        reason = _lease_staleness_reason_locked(
            workspace,
            feature,
            run_id,
            batch_id,
            batch,
            now=now,
            timeout_seconds=timeout_seconds,
        )
        if reason is None:
            continue
        _mark_retry_pending_locked(
            workspace,
            feature,
            run_id,
            manifest,
            batch_id,
            batch,
            error=f"parallel_batch_recovery:{reason}",
            generation=int(batch.get("dispatchGeneration", 1)),
        )
        recovered.append({"batchId": batch_id, "reason": reason})
    return recovered


def _recover_blocked_batches_locked(
    workspace: Path,
    feature: str,
    run_id: str,
    manifest: dict[str, Any],
    *,
    current_invocation_id: str | None = None,
    allow_new_invocation_retry: bool = False,
) -> list[str]:
    """Requeue legacy failures without stealing a live worker or reopening operator escalations."""
    recovered = []
    now = time.time()
    resolved_inv = _current_invocation_id(manifest, current_invocation_id)
    for batch_id, batch in manifest.get("batches", {}).items():
        if not isinstance(batch, dict) or batch.get("status") not in {"blocked", "failed"}:
            continue
        if _is_terminal_operator_blocked(batch):
            continue
        if _is_retry_exhausted_blocked(batch, resolved_inv, allow_new_invocation_retry=allow_new_invocation_retry):
            continue
        if batch.get("mergeCommitSha"):
            continue  # Delivery reconciliation owns completed side effects.
        dispatch = batch.get("dispatch") or {}
        if (dispatch.get("status") == "running" and dispatch.get("generation") == batch.get("dispatchGeneration", 1)
                and dispatch.get("expiresEpoch", 0) > now):
            continue
        if batch.get("lease") is not None or lease_path(workspace, feature, run_id, str(batch_id)).is_file():
            if _lease_staleness_reason_locked(workspace, feature, run_id, str(batch_id), batch,
                    now=now, timeout_seconds=max(1, int(manifest.get("timeoutPerBatch", 3600)))) is None:
                continue
        recovery = batch.get("recovery") or {}
        _mark_retry_pending_locked(
            workspace,
            feature,
            run_id,
            manifest,
            str(batch_id),
            batch,
            error=str(batch.get("error") or recovery.get("lastError") or "batch_execution_failed"),
            previous_status=str(recovery.get("resumeStatus") or batch.get("status")),
            reset_invocation_budget=True,
            invocation_id=resolved_inv,
        )
        batch["recovery"]["retryAttempts"] = int(recovery.get("retryAttempts", 0))
        if batch.get("status") == "retry_pending":
            recovered.append(str(batch_id))
    return recovered


def _unresolved_state(manifest: dict[str, Any]) -> tuple[list[str], list[str], set[str]]:
    """Return retained, task-scoped manual work and the Batches it owns."""
    unresolved_batches = sorted(
        str(batch_id)
        for batch_id, batch in manifest.get("batches", {}).items()
        if isinstance(batch, dict) and batch.get("status") in {"needs_resolution", "conflict"}
    )
    unresolved_trains: list[str] = []
    train_batch_ids: set[str] = set()
    for key, train in (manifest.get("mergeTrains") or {}).items():
        if not isinstance(train, dict) or train.get("status") not in {"candidate_conflicted", "needs_resolution"}:
            continue
        unresolved_trains.append(str(key))
        train_batch_ids.update(
            str(batch_id)
            for batch_id in train.get("batchIds", [])
            if isinstance(batch_id, str) and batch_id.strip()
        )
    return unresolved_batches, sorted(unresolved_trains), set(unresolved_batches) | train_batch_ids


def _is_global_resolution_batch(batch: dict[str, Any]) -> bool:
    """A promoted source whose Plan write failed is not an isolatable conflict."""
    resolution = batch.get("resolution")
    return isinstance(resolution, dict) and resolution.get("kind") == "plan_state_update"


def _is_global_resolution_train(train: dict[str, Any]) -> bool:
    """Do not schedule through a train that may already have promoted source."""
    return bool(train.get("planWriterErrors")) or bool(train.get("promotedSha"))


def _scoped_merge_train_keys(
    manifest: dict[str, Any],
    keys: list[str],
    workspace_refs: list[str] | None,
) -> list[str]:
    if not workspace_refs:
        return list(keys)
    allowed = {str(ref).strip() for ref in workspace_refs if str(ref).strip()}
    return [
        key
        for key in keys
        if str(((manifest.get("mergeTrains") or {}).get(key, {}) or {}).get("repositoryRef") or "") in allowed
    ]


def _clear_retry_lease_locked(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    batch: dict[str, Any],
) -> bool:
    """Clear lease authority while the caller owns the run lock.

    ``retry_pending`` means that the worker which held this Batch's lease has
    already yielded control.  Keeping either the lease file or the manifest
    lease metadata would make a resumed ``ready_to_candidate`` delivery
    permanently non-mergeable, or make a resumed ``pending`` delivery reject
    its next worker with ``parallel_batch_lease_held``.  Do this as part of
    the durable state transition rather than depending on a best-effort
    Workflow cleanup prompt.
    """
    path = lease_path(workspace, feature, run_id, batch_id)
    had_lease = path.is_file() or batch.get("lease") is not None
    with FileLock(path.with_suffix(".lock"), timeout=5):
        if path.exists():
            path.unlink()
    batch["lease"] = None
    return had_lease


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run Git using UTF-8 so CJK worktree paths work on Windows."""
    return subprocess.run(
        git_command(*args),
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _ensure_git_root(requested: Path, *, allow_bootstrap: bool) -> tuple[Path, bool]:
    """Return a Git root, initializing an explicit code directory when needed."""
    try:
        return resolve_git_root(requested), False
    except RepositorySnapshotError:
        if not allow_bootstrap:
            raise ValueError(f"parallel_code_workspace_git_repository_required:{requested}")
        if not requested.is_dir():
            raise
        init = _git(requested, "init", "-b", "main")
        if init.returncode != 0:
            # Older Git versions do not support `init -b`; retain the same
            # bootstrap behavior with the portable form.
            init = _git(requested, "init")
        if init.returncode != 0:
            raise ValueError(f"parallel_code_workspace_git_init_failed:{init.stderr.strip()}")
        return resolve_git_root(requested), True


def _ensure_runtime_ignores(git_root: Path) -> list[str]:
    """Keep workflow-owned files out of the user's tracked baseline."""
    exclude = _git_metadata_path(git_root, "--git-common-dir") / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    additions = [rule for rule in _BOOTSTRAP_IGNORE_RULES if rule not in existing.splitlines()]
    if additions:
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        exclude.write_text(existing + prefix + "\n".join(additions) + "\n", encoding="utf-8")
    return additions


def _git_head(git_root: Path) -> str | None:
    result = _git(git_root, "rev-parse", "--verify", "HEAD")
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _unstage_platform_runtime(git_root: Path, *, has_head: bool) -> None:
    """Keep already-staged platform artifacts out of an automatic baseline."""
    if has_head:
        staged = _git(git_root, "diff", "--cached", "--quiet", "--", PLATFORM_RUNTIME_DIRECTORY)
        if staged.returncode == 0:
            return
        if staged.returncode != 1:
            raise ValueError(f"parallel_code_workspace_runtime_stage_check_failed:{staged.stderr.strip()}")
        # ``git restore`` arrived after the Git version shipped by several
        # supported Windows images.  ``reset HEAD --`` has the same staging
        # effect here and is supported by Git 2.20.
        command = ["reset", "HEAD", "--", PLATFORM_RUNTIME_DIRECTORY]
    else:
        command = ["rm", "-r", "--cached", "--ignore-unmatch", "--", PLATFORM_RUNTIME_DIRECTORY]
    result = _git(git_root, *command)
    if result.returncode != 0:
        raise ValueError(f"parallel_code_workspace_runtime_unstage_failed:{result.stderr.strip()}")


def _bootstrap_repository(
    git_root: Path,
    feature: str,
    *,
    task_card_id: str,
    initialized: bool,
    ignore_additions: list[str],
    allow_bootstrap: bool,
) -> dict[str, Any]:
    """Optionally create an explicit baseline commit for an unusable source tree."""
    before_head = _git_head(git_root)
    status = git_status_porcelain(git_root)
    if status.returncode != 0:
        raise ValueError("parallel_code_workspace_status_unavailable")
    dirty = bool(status.stdout.strip())
    if before_head and not dirty:
        return {
            "headSha": before_head,
            "performed": bool(initialized or ignore_additions),
            "initialized": initialized,
            "reason": "git_initialized" if initialized else None,
            "commitSha": before_head,
        }

    if not allow_bootstrap:
        reason = "unborn_head" if before_head is None else "dirty_worktree"
        raise ValueError(f"parallel_code_workspace_bootstrap_required:{reason}:{git_root}")

    _unstage_platform_runtime(git_root, has_head=before_head is not None)
    add = _git(git_root, "add", "-A", "--", ".", f":(exclude){PLATFORM_RUNTIME_DIRECTORY}**")
    if add.returncode != 0:
        raise ValueError(f"parallel_code_workspace_bootstrap_stage_failed:{add.stderr.strip()}")
    reason = "unborn_head" if before_head is None else "dirty_worktree"
    message = build_commit_message(task_card_id, f"初始化 {feature} 工作流基线")
    # Use the target repository's normal Git identity and hook chain. This is
    # the same commit path used later by native Batch worktrees; overriding it
    # with a plugin address makes corporate author-domain hooks reject the
    # bootstrap before the Workflow can start.
    commit = _git(
        git_root,
        "commit",
        "--allow-empty",
        "-m",
        message,
    )
    if commit.returncode != 0:
        raise ValueError(f"parallel_code_workspace_bootstrap_commit_failed:{commit.stderr.strip()}")
    head = _git_head(git_root)
    if head is None:
        raise ValueError("parallel_code_workspace_bootstrap_head_unavailable")
    return {
        "headSha": head,
        "performed": True,
        "initialized": initialized,
        "reason": reason,
        "commitSha": head,
    }


def resolve_repository_bindings(
    bundle: Any,
    values: list[str] | None,
    *,
    feature: str = "feature",
    allow_bootstrap: bool = False,
    task_card_id: str | None = None,
    integration_mode: str = "direct",
) -> dict[str, dict[str, Any]]:
    """Resolve `workspaceRef=/path` arguments into immutable repository bindings."""
    normalized_task_card_id = normalize_task_card_id(task_card_id)
    refs = sorted({
        str(item.get("workspaceRef"))
        for batch in bundle.batches.values()
        for item in batch.get("tasks", [])
        if isinstance(item, dict) and isinstance(item.get("workspaceRef"), str)
    })
    raw = values or []
    parsed: dict[str, Path] = {}
    bare: list[Path] = []
    for value in raw:
        key, separator, path = value.partition("=")
        if separator:
            if not key or not path or key in parsed:
                raise ValueError(f"parallel_code_workspace_invalid:{value}")
            parsed[key] = Path(path).expanduser().resolve()
        elif value:
            bare.append(Path(value).expanduser().resolve())
    if bare:
        if len(bare) != 1 or len(refs) != 1:
            raise ValueError("parallel_code_workspace_mapping_required")
        parsed[refs[0]] = bare[0]
    missing = sorted(set(refs) - set(parsed))
    unexpected = sorted(set(parsed) - set(refs))
    if missing:
        raise ValueError("parallel_code_workspace_missing:" + ",".join(missing))
    if unexpected:
        raise ValueError("parallel_code_workspace_unknown:" + ",".join(unexpected))
    bindings: dict[str, dict[str, Any]] = {}
    common_roots: dict[str, str] = {}
    for ref in refs:
        requested = parsed[ref]
        try:
            git_root, initialized = _ensure_git_root(requested, allow_bootstrap=allow_bootstrap if integration_mode != "isolated" else False)
        except (RepositorySnapshotError, ValueError) as exc:
            raise ValueError(f"parallel_code_workspace_invalid:{ref}:{exc}") from exc
        if integration_mode == "isolated":
            # Freeze the business checkout's current branch and committed HEAD
            # once, before creating the run. Resume uses this binding even if
            # the user later switches the source checkout to another branch.
            target_branch = current_git_branch(git_root)
            if not target_branch:
                raise ValueError(f"integration_current_branch_required:{ref}:detached_head")
            target = _git(git_root, "rev-parse", "--verify", f"refs/heads/{target_branch}^{{commit}}")
            if target.returncode:
                raise ValueError(f"integration_current_branch_no_commits:{ref}:{target_branch}")
            head = target.stdout.strip()
            if _git_head(git_root) != head or current_git_branch(git_root) != target_branch:
                raise ValueError(f"integration_current_branch_changed:{ref}")
            common = str(_git_metadata_path(git_root, "--git-common-dir"))
            canonical_ref = common_roots.setdefault(common, ref)
            bindings[ref] = {
                "workspaceRef": ref, "requestedPath": str(requested), "gitRoot": str(git_root),
                "baseSha": head, "headSha": head,
                "baseBranch": target_branch, "sourceBranch": target_branch,
                "integrationRepositoryRef": canonical_ref,
                "bootstrap": {"created": False, "reason": "committed_current_branch_only"},
                "runtimeIgnoreAdditions": _ensure_runtime_ignores(git_root),
            }
            if bindings[canonical_ref]["baseBranch"] != target_branch or bindings[canonical_ref]["baseSha"] != head:
                raise ValueError(f"integration_shared_repository_target_mismatch:{ref}")
            if canonical_ref != ref:
                bindings[ref]["baseSha"] = bindings[canonical_ref]["baseSha"]
                bindings[ref]["headSha"] = bindings[canonical_ref]["headSha"]
            continue
        ignore_additions = _ensure_runtime_ignores(git_root)
        bootstrap = _bootstrap_repository(
            git_root,
            feature,
            task_card_id=normalized_task_card_id,
            initialized=initialized,
            ignore_additions=ignore_additions,
            allow_bootstrap=allow_bootstrap,
        )
        head = bootstrap["headSha"]
        branch = current_git_branch(git_root)
        bindings[ref] = {
            "workspaceRef": ref,
            "requestedPath": str(requested),
            "gitRoot": str(git_root),
            "baseSha": head,
            # This moves forward only through merges owned by this run.  It
            # is the expected main HEAD for later dependency waves.
            "headSha": head,
            "baseBranch": branch,
            "bootstrap": bootstrap,
            "runtimeIgnoreAdditions": ignore_additions,
        }
    return bindings


def _git_metadata_path(git_root: Path, argument: str) -> Path:
    """Resolve a Git metadata path returned relative to a worktree root."""
    result = _git(git_root, "rev-parse", argument)
    raw = result.stdout.strip()
    if result.returncode != 0 or not raw:
        raise ValueError(f"parallel_batch_worktree_git_metadata_unavailable:{git_root}")
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (git_root / path).resolve()


def assert_batch_worktree_isolated(
    manifest: dict[str, Any],
    batch_id: str,
    worktree_path: Path | str,
) -> None:
    """Require the plugin-owned linked native worktree assigned to a Batch.

    The plugin provisions a linked worktree before starting the Batch agent and
    this guard verifies Git's own worktree metadata rather than trusting a
    filesystem path supplied by the agent.
    """
    batch = manifest.get("batches", {}).get(batch_id)
    if not isinstance(batch, dict):
        raise ValueError(f"parallel_batch_not_found:{batch_id}")
    repository_ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
    binding = manifest.get("repositories", {}).get(repository_ref)
    if not isinstance(binding, dict) or not isinstance(binding.get("gitRoot"), str):
        raise ValueError(f"parallel_repository_binding_missing:{repository_ref}")

    source_root = Path(str(binding["gitRoot"])).expanduser().resolve()
    candidate = Path(worktree_path).expanduser().resolve()
    try:
        candidate_root = resolve_git_root(candidate)
    except RepositorySnapshotError as exc:
        raise ValueError(f"parallel_batch_worktree_not_git:{candidate}") from exc

    details = {
        "batchId": batch_id,
        "repositoryRef": repository_ref,
        "sourceGitRoot": str(source_root),
        "worktreePath": str(candidate),
    }
    if candidate_root == source_root or (isolated(manifest) and candidate_root == integration_repo(binding)):
        raise ValueError(
            "parallel_batch_worktree_not_isolated:"
            + json.dumps({**details, "reason": "source_checkout"}, ensure_ascii=False, separators=(",", ":"))
        )

    source_common = _git_metadata_path(source_root, "--git-common-dir")
    candidate_common = _git_metadata_path(candidate_root, "--git-common-dir")
    source_git_dir = _git_metadata_path(source_root, "--git-dir")
    candidate_git_dir = _git_metadata_path(candidate_root, "--git-dir")
    if candidate_common != source_common or candidate_git_dir == source_git_dir:
        raise ValueError(
            "parallel_batch_worktree_not_isolated:"
            + json.dumps(
                {
                    **details,
                    "reason": "not_linked_to_source_repository",
                    "sourceGitCommonDir": str(source_common),
                    "worktreeGitCommonDir": str(candidate_common),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    listed = _git(source_root, "worktree", "list", "--porcelain")
    registered = {
        Path(line[len("worktree "):]).resolve()
        for line in listed.stdout.splitlines()
        if line.startswith("worktree ")
    }
    if listed.returncode != 0 or candidate_root not in registered:
        raise ValueError(
            "parallel_batch_worktree_not_isolated:"
            + json.dumps(
                {**details, "reason": "not_registered_with_source_repository"},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )


def _implementation_resume_details(
    manifest: dict[str, Any],
    batch_id: str,
    batch: dict[str, Any],
) -> dict[str, Any] | None:
    """Return safe in-place recovery coordinates for an unsealed Batch.

    A dirty, plugin-owned linked worktree can contain implementation that was
    interrupted between Task Runner start and the first seal.  It is unsafe to
    provision over that checkout, but it is equally unsafe to resume an
    arbitrary dirty directory.  Require the exact Batch binding, branch, and
    frozen repository head before offering the Worktree to Task Runner's
    attribution checks.
    """
    if batch.get("commitSha"):
        return None
    raw_path = batch.get("worktreePath")
    branch_name = batch.get("branchName")
    repository_ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
    repository = (manifest.get("repositories") or {}).get(repository_ref)
    if not isinstance(raw_path, str) or not raw_path.strip() or not isinstance(branch_name, str) or not branch_name.strip():
        return None
    if not isinstance(repository, dict):
        return None
    expected_head = batch.get("startSha") or repository.get("headSha") or repository.get("baseSha")
    if not isinstance(expected_head, str) or not expected_head.strip():
        return None
    worktree = Path(raw_path).expanduser().resolve()
    try:
        assert_batch_worktree_isolated(manifest, batch_id, worktree)
        if current_git_branch(worktree) != branch_name:
            return None
        if _git_head(worktree) != expected_head:
            return None
        changed_files = working_tree_changed_files(worktree)
    except (OSError, RepositorySnapshotError, ValueError):
        return None
    if not changed_files:
        return None
    return {
        "worktreePath": str(worktree),
        "branchName": branch_name,
        "expectedHead": expected_head,
        "changedFileCount": len(changed_files),
        "changedFileSample": changed_files[:20],
    }


def validate_plan_for_parallel(workspace: Path, feature: str) -> dict[str, Any]:
    try:
        bundle = load_plan_bundle(feature_dir(workspace, feature))
    except ValueError as exc:
        return {
            "canParallel": False,
            "requiresPlanRepair": True,
            "reason": f"invalid_plan:{exc}",
            "errors": [str(exc)],
        }
    errors = [*parallel_plan_errors(bundle), *validation_ownership_errors(bundle.root, bundle.batches)]
    if errors:
        return {
            "canParallel": False,
            "requiresPlanRepair": True,
            "reason": errors[0],
            "errors": errors,
        }
    entries = [item for item in bundle.root.get("batches", []) if isinstance(item, dict) and item.get("status") not in {"done", "failed"}]
    if not entries:
        return {"canParallel": False, "reason": "no_pending_batches", "errors": []}
    return {
        "canParallel": True,
        "reason": "parallel_plan_valid" if len(entries) > 1 else "single_batch_workflow_valid",
        "planDigest": plan_digest(bundle),
        "batches": [str(item["id"]) for item in entries],
        "workspaceRefs": sorted({
            str(task.get("workspaceRef"))
            for batch in bundle.batches.values()
            for task in batch.get("tasks", [])
            if isinstance(task, dict) and isinstance(task.get("workspaceRef"), str)
        }),
        "errors": [],
    }


def create_run(
    workspace: Path,
    feature: str,
    *,
    max_parallel: int,
    timeout_seconds: int,
    code_workspaces: list[str] | None = None,
    workflow_workspace: Path | None = None,
    allow_bootstrap: bool = False,
    task_card_id: str | None = None,
    integration_mode: str | None = None,
    workflow_invocation_id: str | None = None,
) -> dict[str, Any]:
    # Kept as an ignored Python API compatibility parameter for older callers.
    # The CLI and fixed Workflow no longer expose it: platform workspace identity
    # must not constrain one or more business repositories.
    _ = workflow_workspace
    normalized_task_card_id = normalize_task_card_id(task_card_id)
    verdict = validate_plan_for_parallel(workspace, feature)
    if not verdict["canParallel"]:
        raise ValueError(f"parallel_not_available:{verdict['reason']}")
    # The active-run check and manifest creation must be one critical section.
    # Child repository Workflows are launched concurrently and otherwise can
    # both observe an empty run directory and create divergent DAG runs.
    # Reuse the Feature's existing plan lock so an invalid source repository
    # does not create a leftover ``.parallel-runs`` directory just to acquire
    # a scheduler lock.  It also prevents Plan edits racing run creation.
    with FileLock(feature_dir(workspace, feature) / ".plan.lock"):
        if _invocation_run_id(workspace, feature, workflow_invocation_id) or get_active_run(workspace, feature) is not None:
            raise ValueError("parallel_run_already_active")
        bundle = load_plan_bundle(feature_dir(workspace, feature))
        from hooks.workflow_launcher import _load_runtime_config
        runtime_config = _load_runtime_config(workspace)
        mode = integration_mode or runtime_config["integrationMode"]
        if mode not in {"isolated", "direct"}:
            raise ValueError("integration_mode_invalid")
        repositories = resolve_repository_bindings(
            bundle,
            code_workspaces,
            feature=feature,
            allow_bootstrap=allow_bootstrap,
            task_card_id=normalized_task_card_id,
            integration_mode=mode,
        )
        manifest = create_manifest(
            workspace,
            feature,
            max_parallel=max_parallel,
            timeout_seconds=timeout_seconds,
            repositories=repositories,
            runtime_config=runtime_config,  # Pass runtime config
            task_card_id=normalized_task_card_id,
            integration_mode=mode,
            workflow_invocation_id=workflow_invocation_id,
        )
        manifest["isolation"] = {
            "mode": "native_git_worktrees",
            "owner": "plugin",
            "cleanupOwner": "plugin",
            "provisioner": "hooks/worktree_manager.py",
            "workspaceRefs": sorted(repositories),
        }
        manifest["integrationMode"] = mode
        manifest["integrationReady"] = mode == "direct"
        manifest["status"] = "running"
        save_manifest(workspace, feature, str(manifest["runId"]), manifest)
        initialize_integrations(workspace, feature, str(manifest["runId"]))
        append_event(workspace, feature, str(manifest["runId"]), "run_created", maxParallel=manifest["maxParallel"])
        return schedule(workspace, feature, str(manifest["runId"]))


def _has_unsealed_implementation_repair(workspace: Path, feature: str, batch: dict[str, Any]) -> bool:
    if _stage_recovery_failure_context(batch, test_log_path=feature_dir(workspace, feature) / "test-output.log") is None:
        return False
    last_seal = batch.get("lastSeal") if isinstance(batch.get("lastSeal"), dict) else {}
    return not (
        last_seal.get("commitStage") == "rework"
        and last_seal.get("commitSha") == batch.get("commitSha")
        and last_seal.get("previousCommitSha") not in {None, batch.get("commitSha")}
    )


def _sealed_delivery_error(manifest: dict[str, Any], batch_id: str, *, allow_dirty: bool = False) -> str | None:
    """Validate a plugin-managed native Worktree before resuming a run."""
    batch = manifest.get("batches", {}).get(batch_id)
    if not isinstance(batch, dict):
        return f"parallel_batch_not_found:{batch_id}"
    commit_sha = batch.get("commitSha")
    if not isinstance(commit_sha, str) or not commit_sha:
        return None
    worktree_path = batch.get("worktreePath")
    branch_name = batch.get("branchName")
    if not isinstance(worktree_path, str) or not worktree_path:
        return f"native_worktree_delivery_missing:{batch_id}:path"
    if not isinstance(branch_name, str) or not branch_name:
        return f"native_worktree_delivery_missing:{batch_id}:branch"
    try:
        assert_batch_worktree_isolated(manifest, batch_id, worktree_path)
    except ValueError:
        return f"native_worktree_delivery_missing:{batch_id}:worktree"
    worktree = Path(worktree_path)
    if current_git_branch(worktree) != branch_name:
        return f"native_worktree_delivery_missing:{batch_id}:branch"
    head = _git(worktree, "rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != commit_sha:
        return f"native_worktree_delivery_commit_mismatch:{batch_id}"
    status = git_status_porcelain(worktree)
    if status.returncode != 0:
        return f"native_worktree_delivery_status_unavailable:{batch_id}"
    if status.stdout.strip() and not allow_dirty:
        return f"native_worktree_delivery_dirty:{batch_id}"
    return None


def _source_repository_errors(manifest: dict[str, Any]) -> list[str]:
    """Return source-worktree violations before an active run is reused.

    Each repository binding freezes the expected HEAD.  It moves forward only
    through ``parallel_merge_train.py`` after exact candidate promotion. Direct commits, resets,
    and shared-checkout changes must stop recovery before another Batch runs
    against a different base.
    """
    errors: list[str] = []
    repositories = manifest.get("repositories", {})
    if not isinstance(repositories, dict) or not repositories:
        return ["parallel_repository_bindings_missing"]
    for ref, repository in sorted(repositories.items()):
        if not isinstance(repository, dict) or not isinstance(repository.get("gitRoot"), str):
            errors.append(f"parallel_repository_binding_invalid:{ref}")
            continue
        if isolated(manifest):
            try:
                root = assert_integration(repository)
            except (ValueError, OSError) as exc:
                errors.append(f"parallel_integration_invalid:{ref}:{exc}")
                continue
        else:
            root = integration_repo(repository)
        status = git_status_porcelain(root)
        if status.returncode != 0:
            errors.append(f"parallel_repository_status_unavailable:{ref}")
            continue
        if status.stdout.strip():
            errors.append(f"parallel_repository_dirty:{ref}")
            continue
        expected_head = repository.get("headSha") or repository.get("baseSha")
        actual_head = _git_head(root)
        if not isinstance(expected_head, str) or not expected_head.strip() or not actual_head:
            errors.append(f"parallel_repository_head_unavailable:{ref}")
        elif actual_head != expected_head:
            errors.append(
                f"parallel_repository_head_changed:{ref}:expected={expected_head}:actual={actual_head}"
            )
    return errors


def _scoped_batch_ids(manifest: dict[str, Any], batch_ids: list[str], workspace_refs: list[str] | None) -> list[str]:
    if not workspace_refs:
        return list(batch_ids)
    allowed = {str(ref) for ref in workspace_refs if str(ref).strip()}
    return [
        batch_id
        for batch_id in batch_ids
        if str(
            (manifest.get("batches", {}).get(batch_id, {}) or {}).get("workspaceRef")
            or (manifest.get("batches", {}).get(batch_id, {}) or {}).get("repositoryRef")
        ) in allowed
    ]


def _stage_recovery_failure_context(
    batch: dict[str, Any],
    *,
    test_log_path: Path,
) -> dict[str, Any] | None:
    """Return the saved Review finding that caused an implement recovery."""
    states = batch.get("stageStates") if isinstance(batch.get("stageStates"), dict) else {}
    for stage in DELIVERY_STAGES:
        state = states.get(stage)
        failure = state.get("failure") if isinstance(state, dict) else None
        if not isinstance(failure, dict) or failure.get("nextStage") != "implement":
            continue
        message = failure.get("message")
        if not isinstance(message, str) or not message.strip():
            continue
        context: dict[str, Any] = {
            "failedStage": stage,
            "failureType": str(failure.get("type") or "implementation"),
            "message": message,
        }
        for field in ("repairContextPath", "repairContextDigest", "affectedTaskIds"):
            if field in state.get("repairContext", {}):
                context[field] = state["repairContext"][field]
        if stage == "test":
            context["testLogPath"] = str(test_log_path)
        return context
    return None


def schedule(
    workspace: Path,
    feature: str,
    run_id: str,
    workspace_refs: list[str] | None = None,
    *,
    workflow_view: bool = False,
    workflow_invocation_id: str | None = None,
) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if _invocation_superseded(manifest, workflow_invocation_id):
            return {"runId": run_id, "status": "superseded"}
        if manifest.get("status") in INACTIVE_RUN_STATES | PROMOTION_STATES | {"integration_sync_conflicted", "integration_sync_required"}:
            return {"runId": run_id, "status": manifest["status"], "scheduledGroups": [],
                    "controlProtocolVersion": manifest.get("controlProtocolVersion", 1),
                    "controlRevision": manifest.get("controlRevision", 0),
                    "readyBatches": [], "allReadyBatches": [], "mergeableBatches": [],
                    "implementationRecoveryBatches": [], "stageRecoveryBatches": [], "retryPendingBatches": [],
                    "blockedBatches": [], "parallelGroups": [], "allParallelGroups": [], "dispatchGrants": {},
                    "batchTaskIds": {}, "batchWorkspaces": {}, "activeWorkers": 0,
                    "maxParallel": manifest["maxParallel"], **promotion_view(manifest)}
        from hooks.parallel_delivery import reconcile_deliveries_locked
        delivery_owned = reconcile_deliveries_locked(workspace, feature, run_id, manifest)
        if manifest.get("status") not in {"succeeded", "succeeded_with_issues"}:
            _recover_blocked_batches_locked(
                workspace,
                feature,
                run_id,
                manifest,
                current_invocation_id=workflow_invocation_id,
                allow_new_invocation_retry=False,
            )
            _recover_stale_active_batches_locked(workspace, feature, run_id, manifest, delivery_owned)
        for batch_id, batch in manifest.get("batches", {}).items():
            if batch_id in delivery_owned:
                continue
            dispatch = batch.get("dispatch") or {}
            if (dispatch.get("status") == "running" and dispatch.get("generation") == batch.get("dispatchGeneration", 1)
                    and dispatch.get("expiresEpoch", 0) <= time.time()
                    and batch.get("status") in {"pending", "leased", "running", "sealed", "ready_to_candidate", "remediation_pending"}):
                if batch.get("status") == "remediation_pending":
                    from hooks.parallel_delivery import execution_running
                    if execution_running(workspace, feature, run_id, batch_id):
                        continue
                _mark_retry_pending_locked(workspace, feature, run_id, manifest, batch_id, batch, error="dispatch_claim_expired")
                dispatch["status"] = "expired"
                save_manifest(workspace, feature, run_id, manifest)
                append_event(workspace, feature, run_id, "dispatch_claim_expired", batchId=batch_id,
                             generation=batch.get("dispatchGeneration", 1))
        bundle = load_plan_bundle(feature_dir(workspace, feature))
        # A retained per-Batch conflict (or one conflicted Merge Train) owns
        # only the deliveries recorded in that retained state.  Keep those
        # deliveries out of every runnable output so a resume cannot silently
        # recreate their candidate, while allowing unrelated DAG branches to
        # continue.  Dependency release remains enforced by `ready_batches`.
        unresolved_batches, unresolved_trains, withheld_batches = _unresolved_state(manifest)
        batches = manifest.get("batches", {})
        # Recheck an in-place implementation recovery on every dispatch.
        # If it is no longer safe to continue in the old checkout, route the
        # idle, unsealed Batch through provision instead.  Provision archives
        # the old checkout before replacing it; a stale recovery marker must
        # not strand the Batch outside both runnable queues.
        all_implementation_recovery: list[str] = []
        implementation_recovery_details: dict[str, dict[str, Any]] = {}
        recovery_reprovisioned: list[str] = []
        for raw_batch_id, batch in batches.items():
            if not isinstance(batch, dict) or str(raw_batch_id) in withheld_batches:
                continue
            recovery = batch.get("recovery") if isinstance(batch.get("recovery"), dict) else {}
            if batch.get("status") != "pending" or recovery.get("kind") != "implementation_resume":
                continue
            batch_id = str(raw_batch_id)
            details = _implementation_resume_details(manifest, str(raw_batch_id), batch)
            if details is None:
                if batch.get("lease") is not None or batch.get("commitSha"):
                    all_implementation_recovery.append(batch_id)
                    continue
                batch["recovery"] = {
                    **recovery,
                    "kind": "retry_dispatch",
                    "resumeFromStage": None,
                    "preserveWorktree": False,
                    "reprovision": True,
                }
                batch["recovery"].pop("implementationResume", None)
                recovery_reprovisioned.append(batch_id)
                continue
            all_implementation_recovery.append(batch_id)
            implementation_recovery_details[batch_id] = details
        implementation_recovery_ids = set(all_implementation_recovery)
        ready = [
            batch_id
            for batch_id in ready_batches(manifest)
            if batch_id not in withheld_batches and batch_id not in implementation_recovery_ids
        ]
        # ``resource_groups`` treats ``None`` as "all batches" for callers
        # that intentionally omit a scope.  The scheduler has already built
        # an explicit readiness set, however, and an empty set must remain
        # empty: otherwise a retained conflict can be accidentally re-added
        # as a runnable group.
        groups = resource_groups(manifest, ready) if ready else []
        if workspace_refs:
            known_refs = {
                str(batch.get("workspaceRef") or batch.get("repositoryRef"))
                for batch in manifest.get("batches", {}).values()
                if isinstance(batch, dict)
            }
            requested_refs = {str(ref).strip() for ref in workspace_refs if str(ref).strip()}
            unknown_refs = sorted(requested_refs - known_refs)
            if unknown_refs:
                raise ValueError("parallel_workspace_refs_unknown:" + ",".join(unknown_refs))
        scoped_ready = _scoped_batch_ids(manifest, ready, workspace_refs)
        scoped_groups = [
            _scoped_batch_ids(manifest, group, workspace_refs)
            for group in groups
        ]
        scoped_groups = [group for group in scoped_groups if group]
        mergeable = [batch_id for batch_id in mergeable_batches(manifest) if batch_id not in withheld_batches]
        def unresolved_dependency(batch: dict[str, Any]) -> str | None:
            return next(
                (
                    str(dependency_id)
                    for dependency_id in batch.get("dependencies", [])
                    if not isinstance(batches.get(dependency_id), dict)
                    or batches[dependency_id].get("status") != "merged"
                    or not batches[dependency_id].get("mergeCommitSha")
                ),
                None,
            )

        implementation_recovery: list[str] = []
        excluded_implementation_recovery: list[dict[str, str]] = []
        for batch_id in all_implementation_recovery:
            batch = batches[batch_id]
            if batch_id not in implementation_recovery_details:
                excluded_implementation_recovery.append({
                    "batchId": batch_id,
                    "reason": "worktree_verification_failed",
                })
                continue
            if batch.get("lease") is not None:
                excluded_implementation_recovery.append({"batchId": batch_id, "reason": "lease_held"})
                continue
            dependency = unresolved_dependency(batch)
            if dependency is not None:
                excluded_implementation_recovery.append({
                    "batchId": batch_id,
                    "reason": f"dependency_unmerged:{dependency}",
                })
                continue
            implementation_recovery.append(batch_id)

        all_stage_recovery = [
            batch_id
            for batch_id in stage_recovery_batches(manifest)
            if batch_id not in withheld_batches
        ]
        scoped_all_stage_recovery = set(_scoped_batch_ids(manifest, all_stage_recovery, workspace_refs))
        scoped_all_implementation_recovery = set(
            _scoped_batch_ids(manifest, all_implementation_recovery, workspace_refs)
        )
        stage_recovery: list[str] = []
        excluded_stage_recovery: list[dict[str, str]] = []
        for batch_id in all_stage_recovery:
            batch = batches.get(batch_id, {})
            if batch.get("lease") is not None:
                excluded_stage_recovery.append({"batchId": batch_id, "reason": "lease_held"})
                continue
            dependency = unresolved_dependency(batch)
            if dependency is not None:
                excluded_stage_recovery.append({
                    "batchId": batch_id,
                    "reason": f"dependency_unmerged:{dependency}",
                })
                continue
            stage_recovery.append(batch_id)
        scoped_mergeable = _scoped_batch_ids(manifest, mergeable, workspace_refs)
        scoped_implementation_recovery = _scoped_batch_ids(manifest, implementation_recovery, workspace_refs)
        scoped_stage_recovery = _scoped_batch_ids(manifest, stage_recovery, workspace_refs)
        all_remediation = [
            str(raw_id)
            for raw_id, batch in batches.items()
            if isinstance(batch, dict)
            and batch.get("status") == "remediation_pending"
            and str(raw_id) not in withheld_batches
            and batch.get("lease") is None
            and unresolved_dependency(batch) is None
        ]
        scoped_remediation = _scoped_batch_ids(manifest, all_remediation, workspace_refs)
        scoped_excluded_implementation_recovery = [
            item
            for item in excluded_implementation_recovery
            if item["batchId"] in scoped_all_implementation_recovery
        ]
        scoped_excluded_stage_recovery = [
            item
            for item in excluded_stage_recovery
            if item["batchId"] in scoped_all_stage_recovery
        ]
        scoped_unresolved_batches = _scoped_batch_ids(manifest, unresolved_batches, workspace_refs)
        scoped_unresolved_trains = _scoped_merge_train_keys(manifest, unresolved_trains, workspace_refs)
        max_parallel = int(manifest.get("maxParallel", 1))
        selected: list[list[str]] = []
        allowed_refs = {str(ref) for ref in workspace_refs or []}
        active = sum(
            1
            for item in manifest.get("batches", {}).values()
            if batch_occupies_scheduler_slot(item)
        )
        active_outside_scope = sum(
            1
            for item in manifest.get("batches", {}).values()
            if batch_occupies_scheduler_slot(item)
            and str(item.get("workspaceRef") or item.get("repositoryRef")) not in allowed_refs
        ) if workspace_refs else 0
        slots = max(0, max_parallel - active)
        active_batch_ids = sorted(
            str(batch_id)
            for batch_id, item in manifest.get("batches", {}).items()
            if batch_occupies_scheduler_slot(item)
        )
        runnable = select_runnable_batches(
            manifest,
            _scoped_batch_ids(manifest, ready, workspace_refs),
            active_batch_ids,
            slots,
        )
        if runnable:
            # Keep the nested response shape for fixed-workflow compatibility;
            # it is now one dynamic dispatch batch, not a completion barrier.
            selected.append(runnable)
        selected_ids = set(runnable)
        dispatch_exclusions: list[dict[str, str]] = []
        optimistic = (
            isinstance(manifest.get("runtimeConfig"), dict)
            and manifest["runtimeConfig"].get("parallelSchedulingMode") == "optimistic"
        )
        for batch_id in scoped_ready:
            if batch_id in selected_ids:
                continue
            batch = batches.get(batch_id, {})
            stage = str(batch.get("executionStage", "parallel")) if isinstance(batch, dict) else "parallel"
            selected_stage_batch = next(
                (batches.get(selected_id, {}) for selected_id in runnable),
                {},
            )
            selected_stage = (
                str(selected_stage_batch.get("executionStage", "parallel"))
                if isinstance(selected_stage_batch, dict)
                else "parallel"
            )
            if slots <= 0:
                reason = "max_parallel_capacity"
            elif stage == "parallel" and selected_stage != "parallel":
                reason = f"execution_stage_order:{runnable[0]}"
            elif stage != "parallel":
                reason = "critical_stage_waiting_for_active_workers" if active else "critical_stage_frontier"
            elif optimistic:
                reason = "max_parallel_capacity"
            else:
                conflict = next(
                    (
                        other
                        for other in [*active_batch_ids, *runnable]
                        if batch_write_sets_conflict(manifest, batch_id, other)
                    ),
                    None,
                )
                reason = f"write_set_conflict:{conflict}" if conflict else "not_selected_by_scheduler"
            dispatch_exclusions.append({"batchId": batch_id, "reason": reason})
        out_of_scope_ready = sorted(set(ready) - set(scoped_ready))
        dispatch_exclusions.extend(
            {"batchId": batch_id, "reason": "workspace_scope_excluded"}
            for batch_id in out_of_scope_ready
        )
        ready_ids = set(ready)
        allowed_scope = {str(ref) for ref in workspace_refs or []}
        for raw_batch_id, batch in batches.items():
            batch_id = str(raw_batch_id)
            if not isinstance(batch, dict) or batch.get("status") != "pending" or batch_id in ready_ids:
                continue
            workspace_ref = str(batch.get("workspaceRef") or batch.get("repositoryRef") or "")
            if allowed_scope and workspace_ref not in allowed_scope:
                continue
            dependency = next(
                (
                    str(dependency_id)
                    for dependency_id in batch.get("dependencies", [])
                    if not isinstance(batches.get(dependency_id), dict)
                    or batches[dependency_id].get("status") != "merged"
                    or not batches[dependency_id].get("mergeCommitSha")
                ),
                None,
            )
            if dependency is not None:
                dispatch_exclusions.append({
                    "batchId": batch_id,
                    "reason": f"dependency_unmerged:{dependency}",
                })
        manifest["scheduledAt"] = manifest.get("updatedAt")
        save_manifest(workspace, feature, run_id, manifest)
        for batch_id in recovery_reprovisioned:
            append_event(
                workspace, feature, run_id,
                "batch_implementation_recovery_reprovision_scheduled",
                batchId=batch_id,
                reason="worktree_verification_failed",
            )
        result = {
            "runId": run_id,
            "controlProtocolVersion": manifest.get("controlProtocolVersion", 1),
            "controlRevision": manifest.get("controlRevision", 0),
            "cleanupPending": [
                {key: job.get(key) for key in ("jobId", "batchId", "kind", "status", "error")}
                for job in manifest.get("cleanupQueue", {}).values() if job.get("status") != "completed"
            ],
            "workspaceSync": list(manifest.get("workspaceSync", {}).values()),
            "observedEpochMs": int(time.time() * 1000),
            "status": manifest.get("status"),
            "readyBatches": scoped_ready,
            "allReadyBatches": ready,
            "mergeableBatches": scoped_mergeable,
            "allMergeableBatches": mergeable,
            "implementationRecoveryBatches": [
                {
                    "batchId": batch_id,
                    "worktreePath": implementation_recovery_details[batch_id]["worktreePath"],
                    "branchName": implementation_recovery_details[batch_id]["branchName"],
                    "recoveryKind": "implementation_resume",
                    "resumeFromStage": "implement",
                    "preserveWorktree": True,
                    "reprovision": False,
                    "changedFileCount": implementation_recovery_details[batch_id]["changedFileCount"],
                    "changedFileSample": implementation_recovery_details[batch_id]["changedFileSample"],
                }
                for batch_id in scoped_implementation_recovery
            ],
            "allImplementationRecoveryBatches": all_implementation_recovery,
            "excludedImplementationRecoveryBatches": scoped_excluded_implementation_recovery,
            "stageRecoveryBatches": [
                {
                    "batchId": batch_id,
                    "worktreePath": batch.get("worktreePath"),
                    "branchName": batch.get("branchName"),
                    "commitSha": batch.get("commitSha"),
                    "recoveryKind": "stage_resume",
                    "preserveWorktree": True,
                    "reprovision": False,
                    "nextStage": "implement" if failure_context is not None else next_stage or "delivery",
                    **(
                        {"failureContext": failure_context}
                        if failure_context is not None
                        else {}
                    ),
                }
                for batch_id in scoped_stage_recovery
                for batch in [manifest["batches"][batch_id]]
                for next_stage in [
                    _next_open_delivery_stage(batch)
                ]
                for failure_context in [
                    _stage_recovery_failure_context(
                        batch,
                        test_log_path=feature_dir(workspace, feature) / "test-output.log",
                    )
                ]
            ],
            "allStageRecoveryBatches": all_stage_recovery,
            "excludedStageRecoveryBatches": scoped_excluded_stage_recovery,
            "remediationBatches": [
                {
                    "batchId": batch_id,
                    "worktreePath": manifest["batches"][batch_id].get("worktreePath"),
                    "branchName": manifest["batches"][batch_id].get("branchName"),
                    "recoveryKind": "remediation",
                    "remediationKind": (manifest["batches"][batch_id].get("recovery") or {}).get("remediationKind") or "unattributed_changes",
                    "remediationAttempts": int((manifest["batches"][batch_id].get("recovery") or {}).get("remediationAttempts", 0)),
                    "attempt": int((manifest["batches"][batch_id].get("recovery") or {}).get("remediationAttempts", 0)) + 1,
                    "preserveWorktree": True,
                    "reprovision": False,
                }
                for batch_id in scoped_remediation
            ],
            "blockedBatches": sorted(
                str(batch_id)
                for batch_id, batch in manifest.get("batches", {}).items()
                if isinstance(batch, dict) and batch.get("status") == "blocked"
            ),
            "retryPendingBatches": sorted(
                str(batch_id)
                for batch_id, batch in manifest.get("batches", {}).items()
                if isinstance(batch, dict) and batch.get("status") == "retry_pending"
            ),
            "unresolvedBatches": scoped_unresolved_batches,
            "allUnresolvedBatches": unresolved_batches,
            "unresolvedMergeTrains": scoped_unresolved_trains,
            "allUnresolvedMergeTrains": unresolved_trains,
            "recoveryRequired": bool(scoped_unresolved_batches or scoped_unresolved_trains),
            "parallelGroups": scoped_groups,
            "allParallelGroups": groups,
            "scheduledGroups": selected,
            "workspaceRefs": sorted(set(workspace_refs or [])),
            "waitingForRepositories": bool(workspace_refs and not selected and not scoped_mergeable and (
                groups or mergeable or active_outside_scope
            )),
            "maxParallel": max_parallel,
            "activeWorkers": active,
            "dispatchGrants": {key: int(item.get("dispatchGeneration", 1)) for key, item in manifest.get("batches", {}).items() if isinstance(item, dict)},
            "dispatchDiagnostics": {
                "maxParallel": max_parallel,
                "occupiedSlots": active,
                "availableSlots": slots,
                "activeBatchIds": active_batch_ids,
                "eligibleBatchIds": scoped_ready,
                "selectedBatchIds": runnable,
                "recoveryBatchIds": sorted(set(scoped_stage_recovery + scoped_implementation_recovery + scoped_mergeable)),
                "notSelected": dispatch_exclusions,
            },
            "batchWorkspaces": {
                batch_id: {
                    "workspaceRef": item.get("workspaceRef"),
                    "componentRoots": item.get("componentRoots", []),
                    "executionStage": item.get("executionStage", "parallel"),
                    "deliveryKind": item.get("deliveryKind"),
                    "atomicGroupId": item.get("atomicGroupId"),
                    "batchRationale": item.get("batchRationale"),
                    "requestedPath": (manifest.get("repositories", {}).get(str(item.get("repositoryRef")), {}) or {}).get("requestedPath"),
                    "worktreePath": item.get("worktreePath"),
                    "branchName": item.get("branchName"),
                }
                for batch_id, item in manifest.get("batches", {}).items()
                if isinstance(item, dict)
            },
            "batchTaskIds": {
                batch_id: list(item.get("taskIds", []))
                for batch_id, item in manifest.get("batches", {}).items()
                if isinstance(item, dict)
            },
            "isolation": manifest.get("isolation"),
            **promotion_view(manifest),
        }
        return _workflow_status_view(result, manifest) if workflow_view else result


def mark_batch(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    status: str,
    *,
    dispatch_auth: dict[str, Any] | None = None,
    enforce_cli_authority: bool = False,
    **details: Any,
) -> dict[str, Any]:
    # ``merged`` is written only by the Merge Train after exact candidate promotion and
    # plan-state update. Worker-facing status changes may never unlock deps.
    if status == "merged":
        raise ValueError(f"parallel_batch_merge_owner_required:{batch_id}")
    allowed = {"pending", "leased", "running", "compile_failed", "sealed", "ready_to_candidate", "needs_resolution", "retry_pending", "failed", "blocked", "cancelled"}
    if status not in allowed:
        raise ValueError(f"parallel_batch_status_invalid:{status}")
    retry_lease_cleared = False
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if manifest.get("status") in INACTIVE_RUN_STATES:
            raise ValueError("parallel_batch_run_inactive")
        batch = manifest.get("batches", {}).get(batch_id)
        if not isinstance(batch, dict):
            raise ValueError(f"parallel_batch_not_found:{batch_id}")
        if enforce_cli_authority and dispatch_auth is None:
            dispatch_auth = {}
        validate_cli_dispatch(manifest, batch, batch_id, dispatch_auth, "batch")
        previous = batch.get("status")
        if _is_terminal_operator_blocked(batch) and status != "blocked":
            raise ValueError(f"parallel_batch_escalated_terminal:{batch_id}")
        terminal = {"merged", "failed", "blocked", "cancelled"}
        # `failed` was emitted by older workers before retry_pending existed.
        # Permit non-escalated legacy failures to re-enter recovery; merged,
        # cancelled and operator-escalated blocks stay immutable here.
        retrying_terminal = previous in {"failed", "blocked"} and status == "retry_pending" and not _is_terminal_operator_blocked(batch)
        if previous in terminal and previous != status and not retrying_terminal:
            raise ValueError(f"parallel_batch_terminal:{batch_id}:{previous}")
        if status in {"running", "sealed", "ready_to_candidate"}:
            candidate = details.get("worktreePath") or batch.get("worktreePath")
            if not isinstance(candidate, str) or not candidate.strip():
                raise ValueError(f"parallel_batch_worktree_path_required:{batch_id}")
            assert_batch_worktree_isolated(manifest, batch_id, candidate)
            expected_branch = details.get("branchName") or batch.get("branchName")
            if not isinstance(expected_branch, str) or not expected_branch.strip():
                raise ValueError(f"parallel_batch_worktree_branch_required:{batch_id}")
            if current_git_branch(Path(candidate)) != expected_branch:
                raise ValueError(f"parallel_batch_worktree_branch_mismatch:{batch_id}")
        for key in ("worktreePath", "branchName", "commitSha", "mergeCommitSha", "error"):
            if key in details:
                batch[key] = details[key]
        if status == "retry_pending":
            retry_lease_cleared = _mark_retry_pending_locked(
                workspace,
                feature,
                run_id,
                manifest,
                batch_id,
                batch,
                error=str(details.get("error") or batch.get("error") or "batch_execution_failed"),
                previous_status=str(previous or ""),
                charge_failure=bool(details.get("error")),
            )
        else:
            batch["status"] = status
        if status == "running" and not batch.get("startedAt"):
            batch["startedAt"] = details.get("startedAt") or manifest.get("updatedAt")
        if status in terminal:
            batch["completedAt"] = details.get("completedAt") or manifest.get("updatedAt")
        statuses = [item.get("status") for item in manifest.get("batches", {}).values() if isinstance(item, dict)]
        if statuses and all(item == "merged" for item in statuses):
            manifest["status"] = "verifying" if isolated(manifest) else "succeeded"
        elif status == "needs_resolution":
            manifest["status"] = "needs_resolution"
        elif status in {"failed", "blocked"}:
            manifest["status"] = "blocked"
        save_manifest(workspace, feature, run_id, manifest)
        if retry_lease_cleared:
            append_event(
                workspace,
                feature,
                run_id,
                "lease_reclaimed",
                batchId=batch_id,
                force=True,
                reason="retry_pending",
            )
        append_event(workspace, feature, run_id, "batch_status_changed", batchId=batch_id, previous=previous, status=batch["status"])
        return manifest


def _invocation_superseded(manifest: dict, invocation_id: str | None) -> bool:
    # The last admitted invocation owns the run. Earlier IDs remain for replay detection.
    history = manifest.get("workflowInvocationIds") or []
    return invocation_id is not None and invocation_id in history[:-1]


def _revoke_superseded_dispatches(
    workspace: Path,
    feature: str,
    run_id: str,
    workflow_invocation_id: str,
) -> list[dict[str, Any]]:
    """Revoke live dispatches from an older Workflow invocation before resume."""
    from hooks.parallel_delivery import execution_running
    from hooks.parallel_dispatch import active_claim, invocation_id_of

    superseded: list[dict[str, Any]] = []
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if manifest.get("status") in INACTIVE_RUN_STATES:
            return superseded
        changed = False
        invocation_ids = manifest.setdefault("workflowInvocationIds", [])
        if workflow_invocation_id not in invocation_ids:
            invocation_ids.append(workflow_invocation_id)
            changed = True
        now = time.time()
        for raw_batch_id, batch in manifest.get("batches", {}).items():
            if not isinstance(batch, dict):
                continue
            batch_id = str(raw_batch_id)
            dispatch = batch.get("dispatch") or {}
            owner_invocation = invocation_id_of(dispatch)
            # Older runs may only record the owner in the dispatch request ID.
            if owner_invocation and owner_invocation not in invocation_ids:
                invocation_ids.insert(0, owner_invocation)
                changed = True
            if not active_claim(batch, now=now) or owner_invocation == workflow_invocation_id:
                continue
            if manifest.get("controlProtocolVersion", 1) >= 2 and execution_running(workspace, feature, run_id, batch_id):
                continue
            prior_gen = int(batch.get("dispatchGeneration", 1))
            _mark_retry_pending_locked(
                workspace,
                feature,
                run_id,
                manifest,
                batch_id,
                batch,
                error=f"workflow_invocation_superseded:{owner_invocation or 'unknown'}->{workflow_invocation_id}",
            )
            dispatch.update(status="revoked", revokedAtEpoch=now, supersededByInvocationId=workflow_invocation_id)
            (run_dir(workspace, feature, run_id) / "dispatch" / f"{batch_id}.json").unlink(missing_ok=True)
            superseded.append({
                "batchId": batch_id,
                "previousInvocationId": owner_invocation,
                "generation": prior_gen,
            })
            changed = True
        if changed:
            save_manifest(workspace, feature, run_id, manifest)
    for item in superseded:
        append_event(
            workspace,
            feature,
            run_id,
            "dispatch_superseded_by_invocation",
            workflowInvocationId=workflow_invocation_id,
            **item,
        )
    return superseded


def resume_run(
    workspace: Path,
    feature: str,
    run_id: str,
    workspace_refs: list[str] | None = None,
    *,
    workflow_invocation_id: str | None = None,
    manual: bool = False,
    reopen_batches: list[str] | None = None,
    reopen_candidates: list[str] | None = None,
    reopen_sync_refs: list[str] | None = None,
    reopen_finalization: bool = False,
) -> dict[str, Any]:
    """Idempotently resume only batches that do not already own a result."""
    if workflow_invocation_id is not None and not re.fullmatch(r"[a-f0-9]{32}", workflow_invocation_id):
        raise ValueError("workflow_invocation_id_invalid")
    # TASK and Git recovery span multiple run-lock sections. Serialize the whole
    # resume so the next invocation cannot take over midway through that work.
    with FileLock(run_dir(workspace, feature, run_id) / ".resume.lock"):
        manifest = load_manifest(workspace, feature, run_id)
        if _invocation_superseded(manifest, workflow_invocation_id):
            result = {"runId": run_id, "status": "superseded"}
            if manual:
                result["manualRetryBatches"] = []
            return result
        reopened_candidates = (
            _reopen_candidates(workspace, feature, run_id, reopen_candidates, workflow_invocation_id=workflow_invocation_id)
            if reopen_candidates
            else []
        )
        queued = (
            _queue_manual_retries(
                workspace,
                feature,
                run_id,
                workflow_invocation_id=workflow_invocation_id,
                reopen_batches=reopen_batches,
                reopen_sync_refs=reopen_sync_refs,
                reopen_finalization=reopen_finalization,
            )
            if (manual or reopen_batches or reopen_sync_refs or reopen_finalization)
            else []
        )
        result = _resume_run(workspace, feature, run_id, workspace_refs, workflow_invocation_id)
        if workflow_invocation_id and result.get("status") != "superseded":
            _close_prepare_ledger(workspace, feature, run_id, workflow_invocation_id)
        if manual or reopened_candidates or queued:
            result["manualRetryBatches"] = sorted(set([*queued, *reopened_candidates]))
        return result


def _resume_run(workspace: Path, feature: str, run_id: str, workspace_refs: list[str] | None,
                workflow_invocation_id: str | None) -> dict[str, Any]:
    superseded_dispatches = (
        _revoke_superseded_dispatches(workspace, feature, run_id, workflow_invocation_id)
        if workflow_invocation_id is not None
        else []
    )
    superseded_batches = [
        {"batchId": str(item["batchId"]), "reason": "workflow_invocation_superseded"}
        for item in superseded_dispatches
    ]
    # A Git merge may have committed successfully immediately before the Plan
    # writer failed. Recover that metadata before evaluating the normal
    # needs-resolution gate, so an interrupted workflow can resume unattended.
    initial = load_manifest(workspace, feature, run_id)
    if initial.get("status") in INACTIVE_RUN_STATES:
        return {"runId": run_id, "status": initial["status"], "skipped": "terminal_run"}
    from hooks.parallel_runtime import workflow_report_pending
    report_states = {"awaiting_user_confirmation", "promotion_prepared", "integration_retained",
                     "succeeded", "succeeded_with_issues", "verifying"}
    def needs_report(manifest):
        return manifest.get("status") in report_states and workflow_report_pending(manifest)
    if needs_report(initial):
        with run_lock(workspace, feature, run_id):
            initial = load_manifest(workspace, feature, run_id)
            if needs_report(initial):
                initial["status"] = "running"
                initial.pop("mainPromotion", None)
                if not (initial.get("finalEvidenceAggregate") or {}).get("evidenceStateDigest"):
                    initial.pop("workflowCompletedAt", None)
                save_manifest(workspace, feature, run_id, initial)
    if isolated(initial):
        initialize_integrations(workspace, feature, run_id)
        if initial.get("status") in PROMOTION_STATES | {"integration_sync_conflicted", "integration_sync_required"}:
            return schedule(workspace, feature, run_id, workspace_refs=workspace_refs)
    promoted_train_batch_ids = {
        str(batch_id)
        for train in (initial.get("mergeTrains") or {}).values()
        if isinstance(train, dict)
        and (
            train.get("status") == "promoting"
            or (
                train.get("status") == "needs_resolution"
                and (
                    bool(train.get("promotedSha"))
                    or bool(train.get("planWriterErrors"))
                    or (isinstance(train.get("resolution"), dict) and train["resolution"].get("kind") == "promoted_plan_state_update")
                )
            )
        )
        for batch_id in (train.get("batchIds") or [])
        if isinstance(batch_id, str)
    }
    interrupted_recovery_errors: list[str] = []
    try:
        from hooks.task_runner import recover_interrupted_parallel_runs

        for batch_id, batch in (initial.get("batches") or {}).items():
            if not isinstance(batch, dict):
                continue
            # A sealed/candidate delivery cannot still have a legitimate
            # implementation worker.  Include train-recovery batches so an
            # old started run cannot make Plan recovery fail again.
            legacy_retry = (
                batch.get("status") == "blocked"
                and can_manually_resume_batch(batch)
                and not _is_non_retryable_blocked(batch)
                and not _is_retry_exhausted_blocked(
                    batch,
                    _current_invocation_id(initial, workflow_invocation_id),
                    allow_new_invocation_retry=True,
                )
            )
            if (str(batch_id) not in promoted_train_batch_ids
                    and batch.get("status") not in {
                        "retry_pending", "sealed", "ready_to_candidate", "needs_resolution"
                    } and not legacy_retry):
                continue
            worktree_path = batch.get("worktreePath")
            if not isinstance(worktree_path, str) or not worktree_path.strip():
                continue
            recovered = recover_interrupted_parallel_runs(
                workspace,
                feature,
                run_id,
                str(batch_id),
                Path(worktree_path),
                workspace_ref=str(batch.get("workspaceRef") or batch.get("repositoryRef") or "") or None,
            )
            for item in recovered["recovered"]:
                append_event(
                    workspace,
                    feature,
                    run_id,
                    "interrupted_task_run_recovered",
                    batchId=str(batch_id),
                    **item,
                )
    except (OSError, ValueError) as exc:
        interrupted_recovery_errors.append(f"parallel_interrupted_task_run_recovery_failed:{exc}")
    if interrupted_recovery_errors:
        return {
            "runId": run_id,
            "status": "needs_resolution",
            "scheduledGroups": [],
            "mergeableBatches": mergeable_batches(load_manifest(workspace, feature, run_id)),
            "recoveryRequired": True,
            "errors": interrupted_recovery_errors,
        }
    recovery_trains = [
        (str(train.get("repositoryRef") or ""), int(train.get("wave")))
        for train in (initial.get("mergeTrains") or {}).values()
        if isinstance(train, dict)
        and isinstance(train.get("wave"), int)
        and isinstance(train.get("repositoryRef"), str)
        and (
            train.get("status") == "promoting"
            or (
                train.get("status") == "needs_resolution"
                and (
                    (isinstance(train.get("resolution"), dict) and train["resolution"].get("kind") == "promoted_plan_state_update")
                    # Compatibility for the historical record written before
                    # train-level Plan recovery had a structured resolution.
                    or bool(train.get("promotedSha"))
                    or bool(train.get("planWriterErrors"))
                )
            )
        )
    ]
    if recovery_trains:
        from hooks.parallel_merge_train import recover_promoted_plan

        recovery_errors: list[str] = []
        for repository_ref, wave in recovery_trains:
            recovered = recover_promoted_plan(
                workspace,
                feature,
                run_id,
                repository_ref=repository_ref,
                wave=wave,
            )
            if not recovered.get("success"):
                recovery_errors.append(str(recovered.get("error") or f"parallel_merge_train_plan_recovery_failed:{repository_ref}:{wave}"))
        if recovery_errors:
            return {
                "runId": run_id,
                "status": "needs_resolution",
                "scheduledGroups": [],
                "mergeableBatches": mergeable_batches(load_manifest(workspace, feature, run_id)),
                "recoveryRequired": True,
                "errors": recovery_errors,
            }
        initial = load_manifest(workspace, feature, run_id)
    recovery_batches = [
        str(batch_id)
        for batch_id, batch in initial.get("batches", {}).items()
        if isinstance(batch, dict)
        and batch.get("status") == "needs_resolution"
        and isinstance(batch.get("resolution"), dict)
        and batch["resolution"].get("kind") == "plan_state_update"
    ]
    if recovery_batches:
        from hooks.batch_merger import recover_plan_state_after_merge

        recovery_errors: list[str] = []
        for batch_id in recovery_batches:
            recovered = recover_plan_state_after_merge(workspace, feature, run_id, batch_id)
            if not recovered.get("success"):
                recovery_errors.append(str(recovered.get("error") or f"parallel_plan_recovery_failed:{batch_id}"))
        if recovery_errors:
            return {
                "runId": run_id,
                "status": "needs_resolution",
                "scheduledGroups": [],
                "mergeableBatches": mergeable_batches(load_manifest(workspace, feature, run_id)),
                "recoveryRequired": True,
                "errors": recovery_errors,
            }
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if manifest.get("status") in INACTIVE_RUN_STATES:
            return {"runId": run_id, "status": manifest.get("status"), "skipped": "terminal_run"}
        from hooks.parallel_delivery import reconcile_deliveries_locked
        delivery_owned = reconcile_deliveries_locked(workspace, feature, run_id, manifest)
        # Older runs (and an interrupted Workflow cleanup) can have reached
        # ``retry_pending`` before their lease handoff completed.  Repair that
        # invariant before deciding whether the preserved delivery is sealed,
        # mergeable, or ready to be scheduled again.
        retry_leases_cleared: list[str] = []
        legacy_retries_repaired = _recover_blocked_batches_locked(
            workspace,
            feature,
            run_id,
            manifest,
            current_invocation_id=workflow_invocation_id,
            allow_new_invocation_retry=True,
        )
        for batch_id, batch in manifest.get("batches", {}).items():
            if not isinstance(batch, dict):
                continue
            if batch.get("status") != "retry_pending":
                continue
            if _clear_retry_lease_locked(workspace, feature, run_id, str(batch_id), batch):
                retry_leases_cleared.append(str(batch_id))
        # Persist lease metadata removal before running any later recovery
        # checks.  The lease file and manifest are separate atomic files, so a
        # process crash can never make their unlink/write one filesystem
        # transaction; this immediate durable boundary minimizes that window,
        # while the same loop reconciles the remaining one-sided state on the
        # next resume.
        if retry_leases_cleared or legacy_retries_repaired:
            save_manifest(workspace, feature, run_id, manifest)
        for batch_id in retry_leases_cleared:
            append_event(
                workspace,
                feature,
                run_id,
                "lease_reclaimed",
                batchId=batch_id,
                force=True,
                reason="retry_resume_repair",
            )
        # This is deliberately scheduler-owned rather than a best-effort
        # Workflow cleanup.  If a model/worker dies before it can write its
        # own retry marker, its expired execution authority cannot keep the
        # whole run's concurrency slots occupied forever.
        stale_recovered = _recover_stale_active_batches_locked(
            workspace,
            feature,
            run_id,
            manifest,
            delivery_owned,
        )
        active_stale_recovered = len(stale_recovered)
        for item in stale_recovered:
            append_event(
                workspace,
                feature,
                run_id,
                "batch_stale_lease_recovered",
                batchId=item["batchId"],
                reason=item["reason"],
            )
        invalid_deliveries: list[str] = []
        for batch_id, batch in manifest.get("batches", {}).items():
            if not isinstance(batch, dict):
                continue
            if batch.get("status") == "merged" and not (
                isinstance(batch.get("mergeCommitSha"), str) and batch["mergeCommitSha"].strip()
            ):
                delivery_error = f"parallel_batch_merge_evidence_required:{batch_id}"
                batch.update({"status": "blocked", "error": delivery_error})
                invalid_deliveries.append(delivery_error)
        invalid_deliveries.extend(_source_repository_errors(manifest))
        for batch_id, batch in manifest.get("batches", {}).items():
            if (
                isinstance(batch, dict)
                and batch.get("status") == "needs_resolution"
                and _is_global_resolution_batch(batch)
            ):
                invalid_deliveries.append(f"parallel_plan_state_recovery_required:{batch_id}")
        for train_key, train in (manifest.get("mergeTrains") or {}).items():
            if (
                isinstance(train, dict)
                and train.get("status") in {"candidate_conflicted", "needs_resolution"}
                and _is_global_resolution_train(train)
            ):
                invalid_deliveries.append(f"parallel_merge_train_plan_recovery_required:{train_key}")
        if invalid_deliveries:
            manifest["status"] = "blocked"
            save_manifest(workspace, feature, run_id, manifest)
            append_event(
                workspace,
                feature,
                run_id,
                "run_resume_blocked_integrity",
                errors=invalid_deliveries,
            )
            return {
                "runId": run_id,
                "status": "blocked",
                "scheduledGroups": [],
                "mergeableBatches": mergeable_batches(manifest),
                "recoveryRequired": True,
                "errors": invalid_deliveries,
            }
        if manifest.get("status") in {"succeeded", "succeeded_with_issues", "verifying"}:
            return {"runId": run_id, "status": manifest.get("status"), "skipped": "terminal_run"}
        # Ordinary delivery and candidate conflicts stay durable in their
        # own records.  They are intentionally not a global resume gate:
        # `schedule` filters those exact Batch IDs while independent work
        # continues.  Plan-state recovery and shared-source integrity cases
        # were handled above as genuine global blockers.
        invalid_deliveries = []
        for batch_id, batch in manifest.get("batches", {}).items():
            if not isinstance(batch, dict):
                continue
            if batch.get("mergeCommitSha"):
                batch["status"] = "merged"
                continue
            # Keep task-scoped terminal/recovery states intact. Retryable
            # legacy blocks were requeued above; other blocked deliveries
            # and valid active leases still belong to their repair/worker.
            if batch.get("status") in {
                "retry_pending",
                "blocked",
                "failed",
                "cancelled",
                "needs_resolution",
                "conflict",
                "leased",
                "running",
            }:
                continue
            # A delivery that has already completed every stage remains a
            # Merge Train candidate across an interrupted Workflow. Validate
            # its immutable delivery before retaining that state.
            if batch.get("status") == "ready_to_candidate":
                # A candidate should have released its worker authority before
                # it becomes mergeable.  Recover a stale residual lease before
                # delivery validation so a crashed handoff cannot strand this
                # Batch outside both normal work and Merge Train recovery.
                # Check the lease file as well as the manifest metadata: an
                # interrupted prior write may have persisted only one side.
                if batch.get("lease") is not None or lease_path(workspace, feature, run_id, str(batch_id)).is_file():
                    reason = _lease_staleness_reason_locked(
                        workspace,
                        feature,
                        run_id,
                        str(batch_id),
                        batch,
                        now=time.time(),
                        timeout_seconds=max(1, int(manifest.get("timeoutPerBatch", 3600))),
                    )
                    if reason is not None:
                        _mark_retry_pending_locked(
                            workspace,
                            feature,
                            run_id,
                            manifest,
                            str(batch_id),
                            batch,
                            error=f"parallel_batch_recovery:{reason}",
                            previous_status="ready_to_candidate",
                        )
                        stale_recovered.append({"batchId": str(batch_id), "reason": reason})
                        continue
                delivery_error = _sealed_delivery_error(manifest, str(batch_id))
                if delivery_error:
                    batch.update({"status": "blocked", "error": delivery_error})
                    invalid_deliveries.append(delivery_error)
                continue
            if batch.get("commitSha"):
                delivery_error = _sealed_delivery_error(
                    manifest,
                    str(batch_id),
                    allow_dirty=_has_unsealed_implementation_repair(workspace, feature, batch),
                )
                if delivery_error:
                    batch.update({"status": "blocked", "error": delivery_error})
                    invalid_deliveries.append(delivery_error)
                else:
                    batch["status"] = "sealed"
            elif batch.get("status") == "sealed":
                # A compile result may enter sealed before `seal`.  It
                # must not be resumed as a merge candidate without a delivery
                # SHA, otherwise the workflow reports an empty successful
                # merge and leaves downstream dependencies blocked forever.
                delivery_error = f"parallel_batch_seal_required:{batch_id}"
                batch.update({"status": "blocked", "error": delivery_error})
                invalid_deliveries.append(delivery_error)
        if invalid_deliveries:
            manifest["status"] = "blocked"
            save_manifest(workspace, feature, run_id, manifest)
            append_event(
                workspace,
                feature,
                run_id,
                "run_resume_blocked_missing_delivery",
                errors=invalid_deliveries,
            )
            return {
                "runId": run_id,
                "status": "blocked",
                "scheduledGroups": [],
                "mergeableBatches": mergeable_batches(manifest),
                "recoveryRequired": True,
                "errors": invalid_deliveries,
            }
        current_invocation = _current_invocation_id(manifest, workflow_invocation_id)
        retry_resumed: list[str] = []
        retry_exhausted: list[str] = []
        for batch_id, batch in manifest.get("batches", {}).items():
            if not isinstance(batch, dict):
                continue
            recovery = batch.get("recovery") if isinstance(batch.get("recovery"), dict) else {}
            if (
                batch.get("status") == "blocked"
                and recovery.get("status") == "retry_exhausted"
                and recovery.get("exhaustedReported") is False
            ):
                recovery["exhaustedReported"] = True
                retry_exhausted.append(str(batch_id))
                continue
            if batch.get("status") == "remediation_pending":
                _clear_retry_lease_locked(workspace, feature, run_id, str(batch_id), batch)
                dispatch = batch.get("dispatch") or {}
                if dispatch.get("status") in {"remediation_pending", "failed", "revoked", "expired", "completed"}:
                    advance_dispatch_generation(batch)
                continue
            if batch.get("status") != "retry_pending":
                continue
            if (
                "retryInvocationId" in recovery
                and current_invocation is not None
                and recovery.get("retryInvocationId") != current_invocation
            ):
                # Preserve cross-restart transientRetryAttempts and unclassifiedRetryAttempts!
                recovery = {
                    **recovery,
                    "retryInvocationId": current_invocation,
                }
                batch["recovery"] = recovery
            from hooks.parallel_failure import MAX_TRANSIENT_EXTRA_RETRIES
            trans_attempts = int(recovery.get("transientRetryAttempts", 0))
            if (trans_attempts > MAX_TRANSIENT_EXTRA_RETRIES
                    and recovery.get("reasonCode") != "parallel_worktree_provision_lock_busy"):
                _escalate_batch_locked(
                    workspace, feature, run_id, manifest, str(batch_id), batch,
                    error=str(batch.get("error") or recovery.get("lastError") or "transient_retry_budget_exhausted"),
                    reason_code="transient_retry_budget_exhausted",
                    category="transient_exhausted",
                    classification="transient",
                )
                retry_exhausted.append(str(batch_id))
                continue
            implementation_resume = (
                _implementation_resume_details(manifest, str(batch_id), batch)
                if not batch.get("commitSha")
                else None
            )
            # A Merge Train retry must retain its ready-to-candidate state;
            # otherwise its completed stage evidence would be stranded.  A
            # sealed draft goes through per-Batch stage recovery, while an
            # implementation failure before any seal restarts as normal
            # pending work in the same native Worktree.
            batch["status"] = (
                "ready_to_candidate"
                if recovery.get("resumeStatus") == "ready_to_candidate"
                else "sealed"
                if batch.get("commitSha")
                else "pending"
            )
            batch["recovery"] = {
                **recovery,
                "kind": (
                    "implementation_resume"
                    if implementation_resume is not None
                    else "integration_resume"
                    if recovery.get("resumeStatus") == "ready_to_candidate"
                    else "stage_resume"
                    if batch.get("commitSha")
                    else "retry_dispatch"
                ),
                "resumeFromStage": (
                    "implement" if implementation_resume is not None
                    else recovery.get("resumeFromStage") if batch.get("commitSha")
                    else None
                ),
                "preserveWorktree": (
                    True
                    if implementation_resume is not None
                    else bool(batch.get("commitSha"))
                ),
                "reprovision": (
                    False
                    if implementation_resume is not None
                    else not bool(batch.get("commitSha"))
                ),
                **({"implementationResume": implementation_resume} if implementation_resume is not None else {}),
                "status": "rescheduled",
                "rescheduledAt": manifest.get("updatedAt"),
            }
            if implementation_resume is None:
                batch["recovery"].pop("implementationResume", None)
            advance_dispatch_generation(batch)
            retry_resumed.append(str(batch_id))
        # Persist the recovery contract for every sealed delivery that still
        # has a stage to finish.  This is intentionally distinct from a
        # `retry_dispatch`: its existing Worktree and commit are the only
        # valid execution context for Review/delivery continuation.
        for batch_id in stage_recovery_batches(manifest):
            batch = manifest["batches"].get(batch_id)
            if not isinstance(batch, dict):
                continue
            recovery = batch.get("recovery") if isinstance(batch.get("recovery"), dict) else {}
            batch["recovery"] = {
                **recovery,
                "kind": "stage_resume",
                "resumeFromStage": _next_open_delivery_stage(batch),
                "preserveWorktree": True,
                "reprovision": False,
            }
        has_blocked = any(
            isinstance(item, dict) and item.get("status") == "blocked"
            for item in manifest.get("batches", {}).values()
        )
        has_remediation = any(
            isinstance(item, dict) and item.get("status") == "remediation_pending"
            for item in manifest.get("batches", {}).values()
        )
        manifest["status"] = "blocked" if has_blocked and not (retry_resumed or has_remediation) else "running"
        save_manifest(workspace, feature, run_id, manifest)
        for item in stale_recovered[active_stale_recovered:]:
            append_event(
                workspace,
                feature,
                run_id,
                "batch_stale_lease_recovered",
                batchId=item["batchId"],
                reason=item["reason"],
            )
        append_event(workspace, feature, run_id, "run_resumed")
    for batch_id in retry_resumed:
        append_event(workspace, feature, run_id, "batch_retry_rescheduled", batchId=batch_id)
    for batch_id in retry_exhausted:
        append_event(
            workspace,
            feature,
            run_id,
            "batch_retry_exhausted",
            batchId=batch_id,
            maxAutomaticAttempts=MAX_AUTOMATIC_BATCH_RECOVERY_ATTEMPTS,
        )
    result = schedule(workspace, feature, run_id, workspace_refs=workspace_refs, workflow_invocation_id=workflow_invocation_id)
    result["reclaimedStaleBatches"] = superseded_batches + stale_recovered
    result["rescheduledRetryBatches"] = retry_resumed
    result["retryExhaustedBatches"] = retry_exhausted
    return result


def can_manually_resume_batch(batch: dict[str, Any]) -> bool:
    if _is_escalated_blocked(batch):
        return False
    return batch.get("status") in {"retry_pending", "failed", "blocked"}


def manual_resume_run(
    workspace: Path,
    feature: str,
    run_id: str,
    workspace_refs: list[str] | None = None,
    *,
    workflow_invocation_id: str | None = None,
    reopen_batches: list[str] | None = None,
    reopen_candidates: list[str] | None = None,
    reopen_sync_refs: list[str] | None = None,
    reopen_finalization: bool = False,
) -> dict[str, Any]:
    """Explicitly re-admit retryable or operator-named Batches/Candidates."""
    return resume_run(
        workspace,
        feature,
        run_id,
        workspace_refs,
        workflow_invocation_id=workflow_invocation_id,
        manual=True,
        reopen_batches=reopen_batches,
        reopen_candidates=reopen_candidates,
        reopen_sync_refs=reopen_sync_refs,
        reopen_finalization=reopen_finalization,
    )


def _reopen_candidates(
    workspace: Path,
    feature: str,
    run_id: str,
    specs: list[str],
    *,
    workflow_invocation_id: str | None = None,
) -> list[str]:
    """Reopen conflicted Merge Train candidates via resume or discard, then release withheld Batches."""
    from hooks import parallel_merge_train as train

    reopened_batches: list[str] = []
    for spec in specs:
        parts = [part.strip() for part in str(spec or "").rsplit(":", 2)]
        if len(parts) != 3 or not parts[0] or parts[2] not in {"resume", "rebuild"}:
            raise ValueError(f"invalid_reopen_candidate_spec:{spec}")
        repo_ref, wave_raw, mode = parts
        normalized_wave = wave_raw.removeprefix("wave-")
        try:
            wave = int(normalized_wave)
        except ValueError as exc:
            raise ValueError(f"invalid_reopen_candidate_wave:{spec}") from exc
        manifest = load_manifest(workspace, feature, run_id)
        record = train._record(manifest, repo_ref, wave) or (manifest.get("mergeTrains") or {}).get(f"{repo_ref}:{wave}")
        if not isinstance(record, dict):
            raise ValueError(f"no_candidate_record:{repo_ref}:{wave}")
        if record.get("promotedSha") or record.get("planWriterErrors"):
            raise ValueError(f"candidate_plan_recovery_required:{repo_ref}:{wave}")
        batch_ids = [str(bid) for bid in (record.get("batchIds") or []) if isinstance(bid, str) and bid.strip()]
        outcome = (
            train.resume_candidate(workspace, feature, run_id, wave, repo_ref)
            if mode == "resume"
            else train.discard_candidate(workspace, feature, run_id, wave, repo_ref)
        )
        if not outcome.get("success"):
            raise ValueError(str(outcome.get("error") or f"candidate_reopen_failed:{repo_ref}:{wave}:{mode}"))
        with run_lock(workspace, feature, run_id):
            manifest = load_manifest(workspace, feature, run_id)
            if workflow_invocation_id is not None:
                inv_ids = manifest.setdefault("workflowInvocationIds", [])
                if workflow_invocation_id not in inv_ids:
                    inv_ids.append(workflow_invocation_id)
            for bid in batch_ids:
                batch = (manifest.get("batches") or {}).get(bid)
                if not isinstance(batch, dict):
                    continue
                if batch.get("mergeCommitSha"):
                    batch["status"] = "merged"
                    continue
                recovery = dict(batch.get("recovery") or {})
                for key in (
                    "nonRetryable", "escalatedAt", "escalationCategory", "rootCause",
                    "hookBlockCode", "nextAction", "pendingAttempt", "lastChargedGeneration",
                ):
                    recovery.pop(key, None)
                recovery.update({
                    "transientRetryAttempts": 0,
                    "unclassifiedRetryAttempts": 0,
                    "candidateConflictAttempts": 0,
                    "manualRetryAttempts": int(recovery.get("manualRetryAttempts", 0)) + 1,
                    "lastManualRetryAt": manifest.get("updatedAt"),
                    "status": "rescheduled",
                    "kind": "integration_resume",
                    "resumeStatus": "ready_to_candidate",
                })
                batch.update({
                    "status": "ready_to_candidate",
                    "error": None,
                    "completedAt": None,
                    "recovery": recovery,
                })
                batch.pop("deliveryQueue", None)
                _clear_retry_lease_locked(workspace, feature, run_id, bid, batch)
                advance_dispatch_generation(batch)
                reopened_batches.append(bid)
            manifest["status"] = "running"
            save_manifest(workspace, feature, run_id, manifest)
        append_event(
            workspace, feature, run_id, "candidate_escalation_reopened",
            repositoryRef=repo_ref, wave=wave, mode=mode, batchIds=batch_ids,
        )
    return reopened_batches


def _queue_manual_retries(
    workspace: Path,
    feature: str,
    run_id: str,
    *,
    workflow_invocation_id: str | None = None,
    reopen_batches: list[str] | None = None,
    reopen_sync_refs: list[str] | None = None,
    reopen_finalization: bool = False,
) -> list[str]:
    manually_queued: list[str] = []
    explicit_ids = [str(b).strip() for b in (reopen_batches or []) if str(b).strip()]
    explicit_set = set(explicit_ids)
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if manifest.get("status") in INACTIVE_RUN_STATES:
            return manually_queued
        if workflow_invocation_id is not None:
            invocation_ids = manifest.setdefault("workflowInvocationIds", [])
            if workflow_invocation_id not in invocation_ids:
                invocation_ids.append(workflow_invocation_id)
        resolved_inv = _current_invocation_id(manifest, workflow_invocation_id)

        if explicit_set:
            for bid in explicit_ids:
                if bid not in (manifest.get("batches") or {}):
                    raise ValueError(f"parallel_batch_not_found:{bid}")
            for train_key, train in (manifest.get("mergeTrains") or {}).items():
                if not isinstance(train, dict) or train.get("status") not in {"candidate_conflicted", "needs_resolution"}:
                    continue
                held = explicit_set.intersection(str(x) for x in (train.get("batchIds") or []))
                if held:
                    raise ValueError(f"candidate_reopen_required:{train_key}")

        changed = False
        if reopen_sync_refs:
            from hooks.parallel_workspace_queue import reopen_sync_jobs_locked
            for ref in reopen_sync_refs:
                if reopen_sync_jobs_locked(manifest, None if ref == "*" else ref):
                    changed = True
        if reopen_finalization and "finalization" in manifest:
            manifest.pop("finalization", None)
            changed = True

        for raw_batch_id, batch in manifest.get("batches", {}).items():
            if not isinstance(batch, dict):
                continue
            batch_id = str(raw_batch_id)
            recovery = batch.get("recovery") if isinstance(batch.get("recovery"), dict) else {}
            if explicit_set:
                if batch_id not in explicit_set:
                    continue
                if batch.get("status") in {"merged", "cancelled"}:
                    raise ValueError(f"parallel_batch_not_reopenable:{batch_id}:{batch.get('status')}")
            else:
                if not can_manually_resume_batch(batch):
                    continue
            cleaned_recovery = {
                **recovery,
                "manualRetryAttempts": int(recovery.get("manualRetryAttempts", 0)) + 1,
                "lastManualRetryAt": manifest.get("updatedAt"),
                "transientRetryAttempts": 0,
                "unclassifiedRetryAttempts": 0,
                "remediationAttempts": 0,
                "reviewRepairRounds": 0,
                "lastChargedGeneration": None,
                "status": "manual_retry_queued",
            }
            for stale in ("nonRetryable", "escalatedAt", "escalationCategory", "rootCause", "hookBlockCode", "nextAction"):
                cleaned_recovery.pop(stale, None)
            batch["recovery"] = cleaned_recovery
            _mark_retry_pending_locked(
                workspace,
                feature,
                run_id,
                manifest,
                batch_id,
                batch,
                error=str(batch.get("error") or recovery.get("lastError") or "operator_requested_retry"),
                previous_status=str(recovery.get("resumeStatus") or batch.get("status") or ""),
                allow_manual_override=True,
                reset_invocation_budget=True,
                invocation_id=resolved_inv,
            )
            manually_queued.append(batch_id)
            changed = True
        if manually_queued:
            manifest["status"] = "running"
        if changed:
            save_manifest(workspace, feature, run_id, manifest)
    for batch_id in manually_queued:
        append_event(
            workspace,
            feature,
            run_id,
            "batch_escalation_reopened" if batch_id in explicit_set else "batch_manual_retry_queued",
            batchId=batch_id,
        )
    return manually_queued


def prepare_state_path(workspace: Path, feature: str) -> Path:
    return feature_dir(workspace, feature) / ".runtime" / "workflow-prepare-state.json"


def read_prepare_ledger(
    workspace: Path, feature: str, *, plan_digest_value: str, contract_digest_value: str,
) -> dict[str, Any]:
    """Read the current binding without reconciling an in-flight attempt."""
    try:
        state = json.loads(prepare_state_path(workspace, feature).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(state, dict) or state.get("bindingKey") != f"{plan_digest_value}:{contract_digest_value}":
        return {}
    return state


def _prepare_run_receipt(workspace: Path, feature: str, plan_digest_value: str, attempt: dict) -> str | None:
    active_id = get_active_run(workspace, feature)
    if active_id is None or attempt.get("resume"):
        return None
    try:
        manifest = load_manifest(workspace, feature, active_id)
    except (OSError, ValueError):
        return None
    if manifest.get("planDigest") != plan_digest_value:
        return None
    if attempt.get("number") is not None and attempt.get("invocationId") not in manifest.get("workflowInvocationIds", []):
        return None
    return active_id


def reconcile_prepare_ledger(
    workspace: Path,
    feature: str,
    *,
    plan_digest_value: str,
    contract_digest_value: str,
    invocation_id: str | None = None,
    begin_attempt: bool = False,
    reopen: bool = False,
    attempt_number: int | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Track pre-run prepare attempts across crashes before any runId exists."""
    from hooks.json_writer_common import atomic_write_json
    from hooks.parallel_failure import MAX_TRANSIENT_EXTRA_RETRIES

    path = prepare_state_path(workspace, feature)
    path.parent.mkdir(parents=True, exist_ok=True)
    binding_key = f"{plan_digest_value}:{contract_digest_value}"
    with FileLock(path.with_suffix(".lock"), timeout=5):
        try:
            state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            state = {}
        if not isinstance(state, dict) or state.get("bindingKey") != binding_key or reopen:
            state = {
                "bindingKey": binding_key,
                "transientFailures": 0,
                "chargedAttemptIds": [],
                "escalated": False,
                "lastError": None,
                "inFlightAttempt": None,
            }
        # P0: Did a prior in-flight prepare actually succeed on disk before its response was lost?
        in_flight = state.get("inFlightAttempt")
        attempt_key = f"{invocation_id}:prepare" + (f":{attempt_number}" if attempt_number is not None else "")
        if begin_attempt and isinstance(in_flight, dict) and in_flight.get("attemptId") == attempt_key:
            return state
        if isinstance(in_flight, dict):
            active_id = _prepare_run_receipt(workspace, feature, plan_digest_value, in_flight)
            if active_id:
                state["inFlightAttempt"] = None
                state["reconciledRunId"] = active_id
                state["reconciledAttemptId"] = in_flight.get("attemptId")
            else:
                attempt_id = str(in_flight.get("attemptId") or "")
                charged = list(state.get("chargedAttemptIds") or [])
                if attempt_id and attempt_id not in charged:
                    charged.append(attempt_id)
                    state["chargedAttemptIds"] = charged[-20:]
                    state["transientFailures"] = int(state.get("transientFailures", 0)) + 1
                    state["lastError"] = state.get("lastError") or "prepare_interrupted_before_run_created"
                    if state["transientFailures"] > MAX_TRANSIENT_EXTRA_RETRIES:
                        state["escalated"] = True
                        state["escalationCategory"] = "prepare_transient_exhausted"
                state["inFlightAttempt"] = None
        if begin_attempt and not state.get("escalated") and invocation_id:
            state["inFlightAttempt"] = {
                "attemptId": attempt_key,
                "invocationId": invocation_id,
                "bindingKey": binding_key,
                "startedEpoch": time.time(),
                "number": attempt_number,
                "resume": resume,
            }
        atomic_write_json(path, state)
        return state


def record_prepare_attempt_failure(
    workspace: Path,
    feature: str,
    *,
    plan_digest_value: str,
    contract_digest_value: str,
    attempt_id: str,
    error: str,
    deterministic: bool = False,
) -> dict[str, Any]:
    """Charge or escalate a failed prepare attempt unless its durable run receipt already exists."""
    from hooks.json_writer_common import atomic_write_json
    from hooks.parallel_failure import MAX_TRANSIENT_EXTRA_RETRIES, classify_error_signal

    path = prepare_state_path(workspace, feature)
    path.parent.mkdir(parents=True, exist_ok=True)
    binding_key = f"{plan_digest_value}:{contract_digest_value}"
    with FileLock(path.with_suffix(".lock"), timeout=5):
        try:
            state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            state = {}
        if not isinstance(state, dict) or state.get("bindingKey") != binding_key:
            raise ValueError("workflow_prepare_binding_changed")
        in_flight = state.get("inFlightAttempt") or {}
        if in_flight.get("attemptId") != attempt_id:
            run_id = state.get("reconciledRunId") if state.get("reconciledAttemptId") == attempt_id else None
            return {**state, "reconciledRunId": run_id}
        active_id = _prepare_run_receipt(workspace, feature, plan_digest_value, in_flight)
        if active_id:
            state["inFlightAttempt"] = None
            state["reconciledRunId"] = active_id
            state["reconciledAttemptId"] = attempt_id
            atomic_write_json(path, state)
            return {**state, "reconciled": True, "runId": active_id}
        bucket, reason_code, _ = classify_error_signal(error)
        charged = list(state.get("chargedAttemptIds") or [])
        if deterministic or bucket in {"non_retryable", "precondition"}:
            state.update({
                "escalated": True,
                "escalationCategory": "precondition" if deterministic else bucket,
                "reasonCode": reason_code,
                "lastError": error,
                "inFlightAttempt": None,
            })
        elif bucket != "contention" and attempt_id not in charged:
            charged.append(attempt_id)
            state["chargedAttemptIds"] = charged[-20:]
            state["transientFailures"] = int(state.get("transientFailures", 0)) + 1
            state["lastError"] = error
            state["inFlightAttempt"] = None
            if state["transientFailures"] > MAX_TRANSIENT_EXTRA_RETRIES:
                state.update({
                    "escalated": True,
                    "escalationCategory": "prepare_transient_exhausted",
                    "reasonCode": "transient_retry_budget_exhausted",
                })
        elif bucket == "contention":
            state["inFlightAttempt"] = None
        atomic_write_json(path, state)
        return {**state, "reconciled": False}


def _close_prepare_ledger(workspace: Path, feature: str, run_id: str, invocation_id: str | None) -> None:
    from hooks.json_writer_common import atomic_write_json

    path = prepare_state_path(workspace, feature)
    if not path.is_file():
        return
    try:
        with FileLock(path.with_suffix(".lock"), timeout=2):
            state = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(state, dict) and (state.get("inFlightAttempt") or {}).get("invocationId") == invocation_id:
                state["reconciledAttemptId"] = (state.get("inFlightAttempt") or {}).get("attemptId")
                state["reconciledRunId"] = run_id
                state["inFlightAttempt"] = None
                state["completedRunId"] = run_id
                atomic_write_json(path, state)
    except Exception:
        pass


def _invocation_run_id(workspace: Path, feature: str, invocation_id: str | None) -> str | None:
    if invocation_id:
        return next((str(run["runId"]) for run in list_runs(workspace, feature)
                     if invocation_id in run.get("workflowInvocationIds", [])), None)
    return None


def ensure_run(
    workspace: Path,
    feature: str,
    *,
    max_parallel: int,
    timeout_seconds: int,
    code_workspaces: list[str] | None = None,
    allow_bootstrap: bool = False,
    workspace_refs: list[str] | None = None,
    task_card_id: str | None = None,
    workflow_invocation_id: str | None = None,
) -> dict[str, Any]:
    """Create one scheduler run or safely resume the existing durable run."""
    if workflow_invocation_id is not None and not re.fullmatch(r"[a-f0-9]{32}", workflow_invocation_id):
        raise ValueError("workflow_invocation_id_invalid")
    active_run_id = _invocation_run_id(workspace, feature, workflow_invocation_id) or get_active_run(workspace, feature)
    if active_run_id is None:
        try:
            result = create_run(
                workspace,
                feature,
                max_parallel=max_parallel,
                timeout_seconds=timeout_seconds,
                code_workspaces=code_workspaces,
                allow_bootstrap=allow_bootstrap,
                task_card_id=task_card_id,
                workflow_invocation_id=workflow_invocation_id,
            )
            if workspace_refs:
                result = schedule(workspace, feature, str(result["runId"]), workspace_refs=workspace_refs)
            result["reused"] = False
            _close_prepare_ledger(workspace, feature, str(result["runId"]), workflow_invocation_id)
            return result
        except ValueError as exc:
            # Another concurrent ensure may have created the durable run after
            # the initial active-run read. Re-read it and take the normal
            # idempotent reuse path instead of creating a second run.
            if str(exc) != "parallel_run_already_active":
                raise
            active_run_id = _invocation_run_id(workspace, feature, workflow_invocation_id) or get_active_run(workspace, feature)
            if active_run_id is None:
                raise
    # Resume is also the scheduler-owned recovery boundary for retained
    # per-Batch conflicts.  It preserves their worktrees and excludes their
    # Batches from output, but must still release independent DAG branches.
    # `resume_run` retains the stricter global plan/source-integrity gates.
    result = resume_run(
        workspace,
        feature,
        active_run_id,
        workspace_refs=workspace_refs,
        workflow_invocation_id=workflow_invocation_id,
    )
    result["reused"] = True
    _close_prepare_ledger(workspace, feature, str(active_run_id), workflow_invocation_id)
    return result


def _emit(ok: bool, **payload: Any) -> int:
    print(json.dumps({"ok": ok, **payload}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


_WORKFLOW_STATUS_FIELDS = (
    "controlProtocolVersion", "controlRevision", "cleanupPending", "workspaceSync",
    "integrationMode", "integrationWorkspaces", "mainPromotion",
    "runId", "status", "scheduledGroups", "readyBatches", "allReadyBatches",
    "mergeableBatches", "implementationRecoveryBatches", "stageRecoveryBatches", "remediationBatches",
    "retryPendingBatches", "blockedBatches", "parallelGroups", "allParallelGroups",
    "maxParallel", "activeWorkers", "observedEpochMs", "dispatchDiagnostics", "dispatchGrants", "batchTaskIds",
)
_WORKFLOW_WORKSPACE_FIELDS = (
    "workspaceRef", "componentRoots", "executionStage", "requestedPath",
    "worktreePath", "branchName",
)
_WORKFLOW_ISSUE_FIELDS = (
    "issueId", "kind", "batchId", "stage", "failureType", "message",
    "disposition", "blocksWorkflow", "status", "evidenceId", "batchCommit",
    "createdAt",
)
_WORKFLOW_RECOVERY_FIELDS = (
    "status", "retryAttempts", "transientRetryAttempts", "unclassifiedRetryAttempts",
    "remediationAttempts", "reviewRepairRounds", "lastChargedGeneration",
    "lastError", "failureClassification", "failureFingerprint", "reasonCode",
    "nextAction", "escalatedAt", "escalationCategory", "rootCause", "hookBlockCode",
)


def _workflow_status_view(scheduled: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Project only fields consumed by the fixed Workflow across the agent boundary."""
    batches = manifest.get("batches")
    batches = batches if isinstance(batches, dict) else {}
    trains = manifest.get("mergeTrains")
    trains = trains if isinstance(trains, dict) else {}
    issues = manifest.get("deferredIssues")
    issues = issues if isinstance(issues, list) else []
    result = {key: scheduled[key] for key in _WORKFLOW_STATUS_FIELDS if key in scheduled}
    result["dispatchDiagnostics"] = scheduled.get("dispatchDiagnostics") or {}
    if isinstance(manifest.get("finalization"), dict):
        result["finalization"] = manifest["finalization"]
    result["batchWorkspaces"] = {
        batch_id: {key: workspace.get(key) for key in _WORKFLOW_WORKSPACE_FIELDS}
        for batch_id, workspace in scheduled.get("batchWorkspaces", {}).items()
        if isinstance(workspace, dict)
    }
    result["manifest"] = {
        "batches": {
            batch_id: {
                "status": batch.get("status"),
                "dispatchGeneration": int(batch.get("dispatchGeneration", 1)),
                "dispatch": {k: (batch.get("dispatch") or {}).get(k) for k in ("generation", "status", "worker", "expiresEpoch", "source")},
                "dependencies": batch.get("dependencies", []),
                "error": batch.get("error"),
                "worktreePath": batch.get("worktreePath"),
                "branchName": batch.get("branchName"),
                "commitSha": batch.get("commitSha"),
                "mergeCommitSha": batch.get("mergeCommitSha"),
                "recovery": {
                    key: (batch.get("recovery") if isinstance(batch.get("recovery"), dict) else {}).get(key)
                    for key in _WORKFLOW_RECOVERY_FIELDS
                },
            }
            for batch_id, batch in batches.items()
            if isinstance(batch, dict)
        },
        "mergeTrains": {
            train_id: {
                key: train.get(key)
                for key in (
                    "status", "batchIds", "repositoryRef", "wave", "error",
                    "worktreePath", "cleanupErrors",
                )
            } | {
                "conflictContext": {
                    "conflictedFiles": (train.get("conflictContext") if isinstance(train.get("conflictContext"), dict) else {}).get("conflictedFiles")
                }
            }
            for train_id, train in trains.items()
            if isinstance(train, dict)
            and train.get("status") in {"candidate_conflicted", "needs_resolution", "failed", "stale", "built"}
        },
        "deferredIssues": [
            {key: issue[key] for key in _WORKFLOW_ISSUE_FIELDS if key in issue}
            for issue in issues
            if isinstance(issue, dict)
        ],
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Schedule parallel Code batch runs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "create", "ensure", "status", "resume", "manual-resume", "list"):
        item = subparsers.add_parser(name)
        item.add_argument("--workspace")
        item.add_argument("--feature", required=True)
        if name in {"status", "resume", "manual-resume"}:
            item.add_argument("--run-id", required=True)
        if name in {"create", "ensure"}:
            item.add_argument("--max-parallel", type=int, default=5)
            item.add_argument("--timeout-seconds", type=int, default=4 * 60 * 60)
            item.add_argument("--code-workspace", action="append", required=True, help="workspaceRef=/path; single-ref runs may pass /path")
            item.add_argument("--allow-bootstrap", action="store_true", help="explicitly allow Git initialization or a baseline commit for a dirty source repository")
            item.add_argument("--task-card-id", required=True, help="task card selected before the workflow starts")
        if name in {"status", "resume", "manual-resume", "ensure"}:
            item.add_argument("--workspace-ref", action="append", dest="workspace_refs", help="only schedule batches for these workspaceRef values")
        if name == "status":
            item.add_argument("--workflow-view", action="store_true", help="emit only fields consumed by the fixed Code Workflow")
        if name in {"ensure", "resume", "manual-resume", "status"}:
            item.add_argument("--workflow-invocation-id")
        if name == "manual-resume":
            item.add_argument("--reopen-batch", action="append", dest="reopen_batches", help="explicitly reopen an escalated Batch")
            item.add_argument("--reopen-candidate", action="append", dest="reopen_candidates", help="reopen candidate via <repoRef>:<wave>:<resume|rebuild>")
            item.add_argument("--reopen-sync", action="append", dest="reopen_sync_refs", help="reopen escalated workspaceSync jobs for <repositoryRef>")
            item.add_argument("--reopen-finalization", action="store_true", help="reopen escalated finalization state")
    mark = subparsers.add_parser("mark-batch")
    mark.add_argument("--workspace")
    mark.add_argument("--feature", required=True)
    mark.add_argument("--run-id", required=True)
    mark.add_argument("--batch-id", required=True)
    mark.add_argument("--status", required=True)
    mark.add_argument("--commit-sha")
    mark.add_argument("--merge-commit-sha")
    mark.add_argument("--worktree-path")
    mark.add_argument("--branch-name")
    mark.add_argument("--error")
    mark.add_argument("--dispatch-generation", type=int)
    mark.add_argument("--dispatch-token")
    for name in ("prepare-attempt", "prepare-failure"):
        item = subparsers.add_parser(name)
        item.add_argument("--workspace")
        item.add_argument("--feature", required=True)
        item.add_argument("--plan-digest", required=True)
        item.add_argument("--contract-digest", required=True)
        item.add_argument("--workflow-invocation-id", required=True)
        item.add_argument("--attempt", type=int, required=True)
        if name == "prepare-attempt":
            item.add_argument("--resume", action="store_true")
        else:
            item.add_argument("--error-b64", required=True)
            item.add_argument("--deterministic", action="store_true")
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        if args.command in {"prepare-attempt", "prepare-failure"}:
            invocation = args.workflow_invocation_id
            if not re.fullmatch(r"[a-f0-9]{32}", invocation) or not 1 <= args.attempt <= 3:
                raise ValueError("workflow_prepare_attempt_invalid")
            binding = {"plan_digest_value": args.plan_digest, "contract_digest_value": args.contract_digest}
            if args.command == "prepare-attempt":
                result = reconcile_prepare_ledger(
                    workspace, feature, **binding, begin_attempt=True, invocation_id=invocation,
                    attempt_number=args.attempt, resume=args.resume,
                )
            else:
                result = record_prepare_attempt_failure(
                    workspace, feature, **binding, attempt_id=f"{invocation}:prepare:{args.attempt}",
                    error=base64.b64decode(args.error_b64, validate=True).decode("utf-8"),
                    deterministic=args.deterministic,
                )
            return _emit(True, **result)
        if args.command == "validate":
            return _emit(True, **validate_plan_for_parallel(workspace, feature))
        if args.command == "create":
            return _emit(
                True,
                **create_run(
                    workspace,
                    feature,
                    max_parallel=args.max_parallel,
                    timeout_seconds=args.timeout_seconds,
                    code_workspaces=args.code_workspace,
                    allow_bootstrap=args.allow_bootstrap,
                    task_card_id=args.task_card_id,
                ),
            )
        if args.command == "ensure":
            return _emit(
                True,
                **ensure_run(
                    workspace,
                    feature,
                    max_parallel=args.max_parallel,
                    timeout_seconds=args.timeout_seconds,
                    code_workspaces=args.code_workspace,
                    allow_bootstrap=args.allow_bootstrap,
                    workspace_refs=args.workspace_refs,
                    task_card_id=args.task_card_id,
                    workflow_invocation_id=args.workflow_invocation_id,
                ),
            )
        if args.command == "status":
            result = schedule(workspace, feature, args.run_id, workspace_refs=args.workspace_refs,
                              workflow_view=args.workflow_view, workflow_invocation_id=args.workflow_invocation_id)
            if not args.workflow_view and result["status"] != "superseded":
                result["manifest"] = load_manifest(workspace, feature, args.run_id)
            return _emit(True, **result)
        if args.command == "resume":
            return _emit(
                True,
                **resume_run(
                    workspace,
                    feature,
                    args.run_id,
                    workspace_refs=args.workspace_refs,
                    workflow_invocation_id=args.workflow_invocation_id,
                ),
            )
        if args.command == "manual-resume":
            return _emit(
                True,
                **manual_resume_run(
                    workspace,
                    feature,
                    args.run_id,
                    workspace_refs=args.workspace_refs,
                    workflow_invocation_id=args.workflow_invocation_id,
                    reopen_batches=args.reopen_batches,
                    reopen_candidates=args.reopen_candidates,
                    reopen_sync_refs=args.reopen_sync_refs,
                    reopen_finalization=args.reopen_finalization,
                ),
            )
        if args.command == "list":
            return _emit(True, runs=list_runs(workspace, feature))
        details = {key: value for key, value in vars(args).items() if key in {"commit_sha", "merge_commit_sha", "worktree_path", "branch_name", "error"} and value is not None}
        detail_names = {
            "commit_sha": "commitSha",
            "merge_commit_sha": "mergeCommitSha",
            "worktree_path": "worktreePath",
            "branch_name": "branchName",
            "error": "error",
        }
        details = {detail_names[key]: value for key, value in details.items()}
        return _emit(
            True,
            manifest=mark_batch(
                workspace,
                feature,
                args.run_id,
                args.batch_id,
                args.status,
                dispatch_auth={"generation": args.dispatch_generation, "token": args.dispatch_token},
                **details,
            ),
        )
    except (ValueError, OSError) as exc:
        return _emit(False, error=str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
