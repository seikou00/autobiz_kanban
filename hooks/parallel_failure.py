"""Classify recovery preconditions without rewriting legacy artifacts."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path


DETERMINISTIC = (
    "engineering_handoff_", "engineering_worktree_", "engineering_task_workspace_",
    "engineering_batch_", "engineering_branch_", "engineering_task_contract_", "engineering_snapshot_", "engineering_dependency_inputs_changed",
    "parallel_batch_stage_out_of_order", "parallel_batch_stage_needs_triage", "parallel_dispatch_stale_stage_owner", "prestart_unattributed_changes_detected",
    "parallel_scheduler_manifest_view_incomplete", "review_not_passed:needs_triage",
    "review_not_passed:incomplete", "review_not_passed:failed",
    "utest_runtime_deployment_changed", "utest_runtime_policy_missing", "workflow_runtime_contract_missing",
)


def recovery_failure(error: str, batch: dict, context: Path) -> dict:
    reason = next((value for value in DETERMINISTIC if value in error), None)
    # Only inspect the Batch's small current handoffs. Do not include wall clock,
    # lease metadata or retry counters in the precondition fingerprint.
    handoffs = {}
    for path in sorted((context / "handoffs").glob("*.json")):
        handoffs[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    identity = {"reason": reason or error, "commit": batch.get("commitSha"),
                "worktree": batch.get("worktreePath"), "branch": batch.get("branchName"),
                "stageStates": {k: v.get("status") for k, v in (batch.get("stageStates") or {}).items() if isinstance(v, dict)},
                "handoffs": handoffs}
    return {"classification": "precondition" if reason else "retryable",
            "reasonCode": reason or "batch_execution_failed",
            "fingerprint": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()}
