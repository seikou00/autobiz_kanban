"""Cooperative UTest stop requests; keep ownership until assets are sealed."""
from __future__ import annotations
import time
from pathlib import Path
from hooks.utest_policy import stage_budget, deadline_reason
from hooks.parallel_runtime import run_dir


def request_stop_locked(workspace: Path, feature: str, run_id: str, batch_id: str,
                        manifest: dict, batch: dict, now: float | None = None) -> dict:
    from hooks.engineering_context import read_json
    state = batch.get("stageStates", {}).get("test", {})
    if state.get("status") != "running":
        return {"batchId": batch_id, "status": "terminal" if state.get("status") in {"passed", "skipped", "deferred"} else "not_started",
                "nextCheckSeconds": 30}
    now = time.time() if now is None else now
    execution = read_json(run_dir(workspace, feature, run_id) / "utest" / batch_id / "UTEST_EXECUTION.json")
    budget = stage_budget(manifest, batch, execution)
    reason = deadline_reason(budget, execution, now)
    if reason and not state.get("stopRequested"):
        state["stopRequested"] = {"reasonCode": reason, "requestedEpoch": now,
                                  "attempt": state.get("attempt"), "dispatchGeneration": batch.get("dispatchGeneration", 1),
                                  "modelCancellation": "cooperative_tool_boundary"}
        batch["utestClosurePending"] = True
    pending = state.get("stopRequested")
    check = budget.get("stageDeadlineEpoch", now + 30)
    if not execution.get("firstCommandAt"):
        check = min(check, budget.get("firstCommandDeadlineEpoch", check))
    return {"batchId": batch_id, "status": "stop_requested" if pending else "running",
            "stopRequested": pending, "utestPolicy": budget["utestPolicy"],
            "deadlines": {k: budget.get(k) for k in ("stageDeadlineEpoch", "firstCommandDeadlineEpoch")},
            "nextCheckSeconds": max(1, int(check - now)),
            "requiredAction": "seal_existing_assets_record_failure_release" if pending else "continue_current_assignment"}
