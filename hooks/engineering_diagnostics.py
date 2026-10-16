"""Read-only engineering/dispatch diagnostics. Never prepare, schedule or migrate."""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
from hooks.json_writer_common import feature_dir
from hooks.parallel_runtime import manifest_path, run_dir


def read(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def artifact(path):
    return {"path": str(path), "exists": path.is_file(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}


def inspect(workspace: Path, feature: str, run_id: str, batch_id: str | None = None) -> dict:
    target = manifest_path(workspace, feature, run_id)
    manifest = read(target)
    if not manifest.get("batches"):
        raise ValueError("engineering_diagnostic_manifest_missing_or_invalid")
    if batch_id is not None and batch_id not in manifest["batches"]:
        raise ValueError("engineering_diagnostic_batch_missing")
    shared = feature_dir(workspace, feature) / ".engineering"
    counts, recent = {}, []
    for path in sorted((shared / "observations").glob("*.json")):
        data = read(path)
        for name, count in data.get("counts", {}).items():
            counts[name] = counts.get(name, 0) + count
        recent.extend(data.get("events", []))
    batches = {}
    for key, batch in manifest["batches"].items():
        if batch_id and key != batch_id:
            continue
        base = run_dir(workspace, feature, run_id) / "engineering" / key
        handoffs, reasons = [], []
        for path in sorted((base / "handoffs").glob("*.json")):
            handoff = read(path)
            from hooks.engineering_paths import same_path
            reason = None
            if not handoff:
                reason = "engineering_handoff_invalid"
            elif (handoff.get("runId") != run_id or handoff.get("batchId") != key
                  or not same_path(handoff.get("worktreePath", ""), batch.get("worktreePath") or ".")):
                reason = "engineering_handoff_identity_mismatch"
            if reason:
                reasons.append(reason)
            handoffs.append({**artifact(path), **{field: handoff.get(field) for field in
                ("schemaVersion", "taskId", "worktreePath", "taskWorkspace", "pathBase", "implementationRevision")}, "reasonCode": reason})
        dispatch = batch.get("dispatch") or {}
        recovery = batch.get("recovery") or {}
        states = batch.get("stageStates") or {}
        if dispatch.get("status") == "running":
            reasons.append("dispatch_running" if dispatch.get("expiresEpoch", 0) > time.time() else "dispatch_claim_expired")
        if batch.get("status") == "blocked":
            reasons.append(recovery.get("reasonCode") or "batch_blocked")
        if states.get("review", {}).get("status") not in {"passed", "skipped"}:
            reasons.append("utest_requires_review_passed")
        batches[key] = {"status": batch.get("status"), "worktreePath": batch.get("worktreePath"),
            "branchName": batch.get("branchName"), "dispatchGeneration": batch.get("dispatchGeneration", 1),
            "dispatch": {k: dispatch.get(k) for k in ("status", "generation", "source", "worker", "expiresEpoch")},
            "stageStates": {k: v.get("status") for k, v in states.items() if isinstance(v, dict)},
            "recovery": {k: recovery.get(k) for k in ("status", "failureClassification", "failureFingerprint", "reasonCode", "nextAction")},
            "utestClosurePending": bool(batch.get("utestClosurePending")),
            "utestPolicy": states.get("test", {}).get("utestPolicy"),
            "stopRequested": states.get("test", {}).get("stopRequested"),
            "cacheWriteSummary": {read(path).get("taskId", path.stem): read(path).get("cacheWriteSummary", {})
                                  for path in sorted((base / "handoffs").glob("*.json"))},
            "contextSummaries": [{"contextDigest": event.get("contextDigest"), "stage": event.get("stage"),
                                  "factCount": event.get("count"), "at": event.get("at")}
                                 for event in recent if event.get("event") == "context_prepared" and event.get("runId") == run_id and event.get("batchId") == key][-10:],
            "handoffs": handoffs, "repairContextPath": str(base / "REPAIR_CONTEXT.json"), "reasonCodes": sorted(set(reasons))}
    source = Path(__file__).resolve().parents[1]
    scripts = ("engineering_context.py", "engineering_facts.py", "engineering_cache.py", "engineering_dependencies.py",
               "engineering_environment.py", "utest_policy.py", "utest_deadline_guard.py", "parallel_dispatch.py")
    deployed = workspace / ".cmbdevclaw" / "workflows" / feature / "code-batched-execution.workflow.js"
    from hooks.engineering_contract import runtime_contract
    return {"ok": True, "readOnly": True, "runId": run_id, "manifest": artifact(target),
        "runtimeContract": runtime_contract(source),
        "paths": {"sharedRoot": str(shared), "factsRoot": str(shared / "facts"),
                  "dependencyRoot": str(shared / "dependencies"), "environmentsRoot": str(shared / "environments"),
                  "diagnosticsRoot": str(shared / "diagnostics"), "observationsRoot": str(shared / "observations"),
                  "runRoot": str(run_dir(workspace, feature, run_id))},
        "runtimeVersions": {name: artifact(source / "hooks" / name) for name in scripts},
        "workflowSource": artifact(source / "workflows" / "code-batched-execution.workflow.js"),
        "workflowArtifact": artifact(deployed),
        "deployments": [{**read(path), "receiptPath": str(path),
            "currentExecutionArtifact": artifact(Path(read(path)["executionPath"]))}
            for path in sorted((shared / "deployments").glob("*.json")) if read(path).get("executionPath")],
        "batches": batches,
        "metrics": {"counts": counts, "recentEvents": sorted(recent, key=lambda item: item.get("at", 0))[-50:]}}
