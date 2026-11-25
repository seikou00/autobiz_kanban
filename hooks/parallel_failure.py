"""Classify recovery failures with explicit P1-P4 precedence and whitelist hook diagnostics."""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hooks.evidence_kernel import FileLock
from hooks.worktree_target import WORKTREE_GIT_DIR_TOO_LONG, is_git_dir_too_big_output

MAX_TRANSIENT_EXTRA_RETRIES = 2
MAX_UNCLASSIFIED_RETRIES = 1

NON_RETRYABLE = (WORKTREE_GIT_DIR_TOO_LONG,)

CONTENTION_SIGNALS = (
    "parallel_worktree_provision_lock_busy",
    "delivery_queue_wait",
    "repository_busy",
    "workspace_sync_busy",
    "capacity_full",
)

DETERMINISTIC = (
    "engineering_handoff_",
    "engineering_worktree_",
    "engineering_task_workspace_",
    "engineering_batch_",
    "engineering_branch_",
    "engineering_task_contract_",
    "engineering_snapshot_",
    "engineering_dependency_inputs_changed",
    "parallel_batch_stage_out_of_order",
    "parallel_batch_stage_needs_triage",
    "parallel_dispatch_stale_stage_owner",
    "prestart_unattributed_changes_detected",
    "remediation_compile_completion_unconfirmed",
    "remediation_previous_task_execution_unconfirmed",
    "review_not_passed:needs_triage",
    "review_not_passed:failed",
    "unresolved_review_implementation_finding:",
    "parallel_repair_",
    "utest_runtime_deployment_changed",
    "utest_runtime_policy_missing",
    "workflow_runtime_contract_missing",
    "delivery_gate_failed:",
    "parallel_plan_contract_changed",
)

TRANSIENT_SIGNALS = (
    "empty_agent_output",
    "unparsedstructuredoutput",
    "transport",
    "timed out",
    "timeout",
    "worker_interrupted",
    "execution was interrupted",
    "interrupted",
    "lost_review_response",
    "review_repair_lease_unconfirmed",
    "review_stage_not_finalized",
    "connection refused",
    "econnrefused",
    "network is unreachable",
    "temporary failure",
    "model unavailable",
    "dispatch unavailable",
    "dispatch_claim_expired",
    "parallel_batch_recovery:lease_missing",
    "parallel_batch_recovery:lease_expired",
    "parallel_batch_recovery:lease_invalid",
    "parallel_batch_recovery:batch_timeout",
    "parallel_batch_recovery:dispatch_terminated",
)

HOOK_RECORD_FIELDS = (
    "at",
    "runId",
    "batchId",
    "dispatchGeneration",
    "attributionConfidence",
    "hookName",
    "errorCode",
    "blockedTool",
    "commandCategory",
    "executableName",
    "normalizedRelPath",
)

_ALLOWED_HOOK_NAMES = frozenset({
    "code_execution_guard",
    "frontend_route_write_guard",
    "runtime_artifact_guard",
    "state_checkpoint",
})
_ALLOWED_CONFIDENCE = frozenset({"verified_generation", "unverified_diagnostic_only"})
_ALLOWED_COMMAND_CATEGORIES = frozenset({
    "maven_compile",
    "gradle_compile",
    "node_frontend",
    "git_operation",
    "python_runtime",
    "file_write",
    "shell_other",
    "none",
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_token(value: object, *, fallback: str = "unknown", max_len: int = 80) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = "".join(ch if (ch.isalnum() or ch in "._:-") else "_" for ch in value.strip())
    return cleaned[:max_len] or fallback


def _command_category(executable_name: str, blocked_tool: str) -> str:
    tool = blocked_tool.lower()
    if tool in {"write_file", "edit_file", "apply_patch", "write", "edit"}:
        return "file_write"
    exe = executable_name.lower()
    if exe in {"mvn", "mvnw", "mvn.cmd", "mvnw.cmd"}:
        return "maven_compile"
    if exe in {"gradle", "gradlew", "gradle.bat", "gradlew.bat"}:
        return "gradle_compile"
    if exe in {"npm", "pnpm", "yarn", "npx", "vite", "webpack", "tsc", "vue-tsc", "eslint", "node"}:
        return "node_frontend"
    if exe in {"git", "git.exe"}:
        return "git_operation"
    if exe in {"python", "python3", "python.exe", "python3.exe"}:
        return "python_runtime"
    return "shell_other" if exe else "none"


def _normalize_rel_path(target_path: str | Path | None, workspace: Path | None) -> str | None:
    if target_path is None:
        return None
    raw = str(target_path).strip()
    if not raw:
        return None
    try:
        resolved = Path(raw).expanduser().resolve(strict=False)
    except OSError:
        return "<invalid_path>"
    if workspace is not None:
        for root in (workspace, workspace.parent):
            try:
                rel = resolved.relative_to(root.resolve(strict=False)).as_posix()
                return posixpath.normpath(rel)[:200]
            except ValueError:
                continue
    return "<outside_workspace>"


def _extract_flag(tokens: list[str], flag: str) -> str | None:
    for idx, token in enumerate(tokens):
        if token == flag and idx + 1 < len(tokens):
            return tokens[idx + 1]
        if token.startswith(flag + "="):
            return token.split("=", 1)[1]
    return None


def _verify_caller_generation(
    workspace: Path,
    feature: str,
    command: str,
    host_dispatch_auth: dict[str, Any] | None = None,
) -> tuple[str | None, str | None, int | None, str]:
    """Verify generation strictly from credentials carried by THIS call (or host auth).

    Never infer active generation from shared disk state (.task-runs or worktreePath),
    because a delayed older agent can observe a newer generation's disk artifacts.
    Dispatch tokens and business lease tokens are verified through their separate
    Runtime entrypoints and are never interchangeable.
    """
    from hooks.parallel_runtime import check_lease, load_manifest, validate_cli_dispatch

    feature_dir = workspace / ".autobizdevops" / "features" / feature
    if isinstance(host_dispatch_auth, dict):
        run_id = host_dispatch_auth.get("runId")
        batch_id = host_dispatch_auth.get("batchId")
        gen = host_dispatch_auth.get("generation")
        token = host_dispatch_auth.get("dispatchToken")
        if isinstance(run_id, str) and isinstance(batch_id, str) and isinstance(gen, int) and isinstance(token, str):
            try:
                manifest = load_manifest(workspace, feature, run_id)
                batch = (manifest.get("batches") or {}).get(batch_id)
                if isinstance(batch, dict) and validate_cli_dispatch(
                    manifest, batch, batch_id, {"generation": gen, "token": token}, "hook"
                ):
                    return run_id, batch_id, gen, "verified_generation"
            except (OSError, ValueError, KeyError):
                pass

    if not command.strip():
        return None, None, None, "unverified_diagnostic_only"
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    run_id = _extract_flag(tokens, "--run-id") or _extract_flag(tokens, "--parallel-run-id")
    batch_id = _extract_flag(tokens, "--batch-id")
    dispatch_gen_raw = _extract_flag(tokens, "--dispatch-generation")
    dispatch_token = _extract_flag(tokens, "--dispatch-token") or _extract_flag(tokens, "--owner-token")
    lease_token = _extract_flag(tokens, "--lease-token")

    # Separate dispatch-token verification vs business-lease-token verification.
    if dispatch_gen_raw is not None and dispatch_token and run_id and batch_id:
        try:
            gen = int(dispatch_gen_raw)
            manifest = load_manifest(workspace, feature, run_id)
            batch = (manifest.get("batches") or {}).get(batch_id)
            if isinstance(batch, dict) and validate_cli_dispatch(
                manifest, batch, batch_id, {"generation": gen, "token": dispatch_token}, "hook"
            ):
                return run_id, batch_id, gen, "verified_generation"
        except (OSError, ValueError, KeyError):
            return run_id, batch_id, None, "unverified_diagnostic_only"

    if lease_token and run_id:
        try:
            manifest = load_manifest(workspace, feature, run_id)
            batches = manifest.get("batches") or {}
            candidates = [batch_id] if batch_id and batch_id in batches else list(batches.keys())
            for candidate_id in candidates:
                batch = batches.get(candidate_id)
                if not isinstance(batch, dict):
                    continue
                if check_lease(workspace, feature, run_id, str(candidate_id), lease_token):
                    lease_file = feature_dir / ".parallel-runs" / run_id / "leases" / f"{candidate_id}.json"
                    lease_data = json.loads(lease_file.read_text(encoding="utf-8"))
                    lease_gen = lease_data.get("dispatchGeneration")
                    if isinstance(lease_gen, int) and lease_gen == int(batch.get("dispatchGeneration", 1)):
                        return run_id, str(candidate_id), lease_gen, "verified_generation"
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass

    return run_id, batch_id, None, "unverified_diagnostic_only"


def _resolve_diagnostic_log_path(workspace: Path, feature: str, run_id: str | None) -> Path:
    feature_dir = workspace / ".autobizdevops" / "features" / feature
    if run_id and (feature_dir / ".parallel-runs" / run_id).is_dir():
        return feature_dir / ".parallel-runs" / run_id / "diagnostics" / "hook-blocks.jsonl"
    return feature_dir / ".runtime" / "hook-blocks.jsonl"


def record_hook_block(
    *,
    workspace: Path | None,
    feature: str | None,
    hook_name: str,
    error_code: str,
    blocked_tool: str,
    command: str = "",
    executable_name: str = "",
    target_path: str | Path | None = None,
    run_id_hint: str | None = None,
    batch_id_hint: str | None = None,
    host_dispatch_auth: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Persist one strictly whitelisted, parameter-free Hook block diagnostic record."""
    if workspace is None or not feature or hook_name not in _ALLOWED_HOOK_NAMES:
        return None
    try:
        workspace = Path(workspace).resolve(strict=False)
        verified_run, verified_batch, verified_gen, confidence = _verify_caller_generation(
            workspace, feature, command, host_dispatch_auth=host_dispatch_auth
        )
        run_id = verified_run or (run_id_hint if isinstance(run_id_hint, str) and run_id_hint else None)
        batch_id = verified_batch or (batch_id_hint if isinstance(batch_id_hint, str) and batch_id_hint else None)
        if not executable_name and command.strip():
            try:
                first = shlex.split(command)[0]
            except (ValueError, IndexError):
                first = command.strip().split()[0]
            executable_name = Path(first.replace("\\", "/")).name
        exe_base = _safe_token(Path(executable_name).name, fallback="", max_len=40) if executable_name else ""
        raw_entry = {
            "at": _utc_now(),
            "runId": _safe_token(run_id, fallback="", max_len=64) or None,
            "batchId": _safe_token(batch_id, fallback="", max_len=32) or None,
            "dispatchGeneration": verified_gen if confidence == "verified_generation" else None,
            "attributionConfidence": confidence if confidence in _ALLOWED_CONFIDENCE else "unverified_diagnostic_only",
            "hookName": hook_name,
            "errorCode": _safe_token(error_code, fallback="HOOK_BLOCKED", max_len=80),
            "blockedTool": _safe_token(blocked_tool.lower(), fallback="unknown", max_len=32),
            "commandCategory": _command_category(exe_base, blocked_tool),
            "executableName": exe_base or None,
            "normalizedRelPath": _normalize_rel_path(target_path, workspace),
        }
        entry = {key: raw_entry[key] for key in HOOK_RECORD_FIELDS}
        log_path = _resolve_diagnostic_log_path(workspace, feature, entry["runId"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(log_path.with_suffix(".lock"), timeout=2):
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return entry
    except Exception:
        return None


def find_verified_hook_block(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    generation: int,
) -> dict[str, Any] | None:
    """Return the latest Hook block proven to belong to ``(run_id, batch_id, generation)``."""
    log_path = workspace / ".autobizdevops" / "features" / feature / ".parallel-runs" / run_id / "diagnostics" / "hook-blocks.jsonl"
    if not log_path.is_file():
        return None
    matched: dict[str, Any] | None = None
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if (
                isinstance(record, dict)
                and record.get("attributionConfidence") == "verified_generation"
                and record.get("runId") == run_id
                and record.get("batchId") == batch_id
                and record.get("dispatchGeneration") == generation
            ):
                matched = record
    except (OSError, json.JSONDecodeError):
        return None
    return matched


def classify_error_signal(
    error: str,
    *,
    verified_hook_code: str | None = None,
) -> tuple[str, str, bool]:
    """Apply strict P1 -> P2 -> P3 -> P4 signal classification before fingerprint fallback.

    Returns ``(priority_bucket, reason_code, non_retryable)`` where ``priority_bucket``
    is one of ``"contention"``, ``"non_retryable"``, ``"precondition"``, ``"transient"``,
    or ``"unclassified"``.
    """
    text = str(error or "")
    lowered = text.lower()

    # P1: Contention / queue waits never consume failure budget.
    contention = next((sig for sig in CONTENTION_SIGNALS if sig in text), None)
    if contention is not None:
        return "contention", contention, False

    # P2a: Explicit non-retryable bounds.
    non_retryable_reason = next((value for value in NON_RETRYABLE if value in text), None)
    if non_retryable_reason is None and is_git_dir_too_big_output(text):
        non_retryable_reason = WORKTREE_GIT_DIR_TOO_LONG
    if non_retryable_reason is not None:
        return "non_retryable", non_retryable_reason, True

    # P2b: Verified same-generation Hook block or deterministic precondition signal.
    if verified_hook_code:
        return "precondition", f"hook_blocked:{verified_hook_code}", False
    deterministic = next((value for value in DETERMINISTIC if value in text), None)
    if deterministic is not None:
        return "precondition", deterministic.rstrip(":"), False

    # P3: Explicit transient model/transport/interruption signal.
    transient = next((sig for sig in TRANSIENT_SIGNALS if sig in lowered), None)
    if transient is not None:
        return "transient", "transient_execution_failure", False

    # P4: Unclassified fallback (evaluated against fingerprint repetition by caller).
    return "unclassified", "batch_execution_failed", False


def recovery_failure(
    error: str,
    batch: dict[str, Any],
    context: Path,
    *,
    verified_hook_code: str | None = None,
) -> dict[str, Any]:
    bucket, reason_code, non_retryable = classify_error_signal(
        error,
        verified_hook_code=verified_hook_code,
    )
    # Only inspect the Batch's small current handoffs. Do not include wall clock,
    # lease metadata or retry counters in the precondition fingerprint.
    handoffs: dict[str, str] = {}
    if context.exists():
        for path in sorted((context / "handoffs").glob("*.json")):
            try:
                handoffs[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
    identity = {
        "reason": reason_code if bucket in {"non_retryable", "precondition"} else error,
        "commit": batch.get("commitSha"),
        "worktree": batch.get("worktreePath"),
        "branch": batch.get("branchName"),
        "stageStates": {
            k: v.get("status")
            for k, v in (batch.get("stageStates") or {}).items()
            if isinstance(v, dict)
        },
        "handoffs": handoffs,
    }
    classification = (
        "non_retryable"
        if non_retryable
        else "precondition"
        if bucket == "precondition"
        else "contention"
        if bucket == "contention"
        else "transient"
        if bucket == "transient"
        else "retryable"
    )
    return {
        "bucket": bucket,
        "classification": classification,
        "reasonCode": reason_code,
        "nonRetryable": non_retryable,
        "fingerprint": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
    }
