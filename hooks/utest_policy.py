"""One durable UTest policy shared by stages, scheduler, tools and Workflow."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

POLICY = {"firstExecutionSeconds": 1800, "stageSeconds": 2700,
          "commandSeconds": 300, "strategyChanges": 1,
          "testRepairs": 1, "sourceRepairs": 1, "indexSeconds": 30}
POLICY_VERSION = "autodev.utest.policy.v2"


def policy_snapshot(timeout_seconds: int | None = None) -> dict:
    timeout_seconds = POLICY["stageSeconds"] if timeout_seconds is None else timeout_seconds
    value = {"version": POLICY_VERSION, **POLICY,
             "stageSeconds": min(POLICY["stageSeconds"], max(1, int(timeout_seconds)))}
    value["digest"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def epoch(value: str | None) -> float | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def stage_budget(manifest: dict, batch: dict, execution: dict | None = None) -> dict:
    """Old running attempts keep the earlier known limit; never gain time."""
    state = batch.get("stageStates", {}).get("test", {})
    saved = state.get("utestPolicy")
    policy = saved
    if not policy:
        policy = {**policy_snapshot(min(900, manifest.get("timeoutPerBatch", 900))),
                  "version": "legacy_preserved", "firstExecutionSeconds": 600}
        policy["digest"] = hashlib.sha256(json.dumps({k: v for k, v in policy.items() if k != "digest"}, sort_keys=True).encode()).hexdigest()
    started = epoch(state.get("startedAt"))
    result = {"utestPolicy": policy}
    if started is not None:
        execution = execution or {}
        result.update(startedEpoch=started,
                      stageDeadlineEpoch=min(state.get("stageDeadlineEpoch", float("inf")),
                                             execution.get("stageDeadlineEpoch", float("inf")),
                                             started + policy["stageSeconds"]),
                      firstCommandDeadlineEpoch=min(state.get("firstCommandDeadlineEpoch", float("inf")),
                                                    execution.get("firstCommandDeadlineEpoch", float("inf")),
                                                    started + policy["firstExecutionSeconds"]))
    return result


def deadline_reason(budget: dict, execution: dict, now: float) -> str | None:
    if budget.get("stageDeadlineEpoch", float("inf")) <= now:
        return "stage_deadline_exceeded"
    if not execution.get("firstCommandAt") and budget.get("firstCommandDeadlineEpoch", float("inf")) <= now:
        return "first_execution_deadline_exceeded"
    return None
