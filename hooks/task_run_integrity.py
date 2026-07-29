#!/usr/bin/env python3
"""Stable integrity seal for immutable task-run baseline fields."""

from __future__ import annotations

import hashlib
import json
from typing import Any


TASK_RUN_INTEGRITY_FIELDS = (
    "version",
    "runId",
    "featureId",
    "batchId",
    "taskId",
    "taskContractSha256",
    "codeWorkspace",
    "requestedCodeWorkspaces",
    "resolvedGitRoots",
    "workspacePrefixes",
    "scopeWorkspaces",
    "scopePathBase",
    "declaredScopePaths",
    "resolvedScopePaths",
    "repositories",
    "snapshotMode",
    "stagingAffectsSnapshot",
    "startedAt",
    "snapshot",
    "revalidation",
)

TASK_RUN_OPTIONAL_INTEGRITY_FIELDS = (
    "repairContext",
)


def task_run_integrity_sha256(state: dict[str, Any]) -> str:
    payload = {field: state.get(field) for field in TASK_RUN_INTEGRITY_FIELDS}
    # Optional fields are sealed only when present so existing v2 runs keep
    # their original digest while newer repair runs can carry audit context.
    payload.update({
        field: state[field]
        for field in TASK_RUN_OPTIONAL_INTEGRITY_FIELDS
        if field in state
    })
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def task_run_integrity_error(state: dict[str, Any]) -> str | None:
    if state.get("version") != 2 or not isinstance(state.get("taskId"), str):
        return None
    stored = state.get("integritySha256")
    if not isinstance(stored, str):
        return "task_run_integrity_missing"
    if stored != task_run_integrity_sha256(state):
        return "task_run_integrity_mismatch"
    return None
