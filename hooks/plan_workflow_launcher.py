#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Expose the repository-owned Plan generation Workflow to the host."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import resolve_feature, resolve_workspace  # noqa: E402
from hooks.plan_generation_state import PlanGenerationError, PlanGenerationStore  # noqa: E402


WORKFLOW_SCRIPT_NAME = "plan-generation.workflow.js"
WORKFLOW_RUNTIME_DIRECTORY = Path(".cmbdevclaw") / "workflows"
DEFAULT_MAX_PARALLEL = 3
MAX_MAX_PARALLEL = 4


def _materialize(source: Path, workspace: Path, feature: str) -> dict[str, str]:
    if not source.is_file():
        raise PlanGenerationError("plan_generation_workflow_source_missing", str(source))
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    target = workspace / WORKFLOW_RUNTIME_DIRECTORY / feature / WORKFLOW_SCRIPT_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        temporary = target.with_name(f".{target.name}.{digest}.tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
    return {
        "workflowScriptPath": str(target.resolve()),
        "workflowScriptSource": str(source.resolve()),
        "workflowScriptSha256": digest,
    }


def build_workflow_request(
    workspace: Path,
    feature: str,
    code_workspace_args: list[str],
    *,
    partitions: list[str] | None,
    max_parallel: int,
    lease_ttl_seconds: int,
) -> dict[str, Any]:
    if not 1 <= max_parallel <= MAX_MAX_PARALLEL:
        raise PlanGenerationError("plan_generation_max_parallel_invalid", str(max_parallel))
    store = PlanGenerationStore(workspace, feature)
    # Capture validates the exact input that the Workflow will lock; it does
    # not create a run or mutate the Plan/Draft state.
    snapshot = store.capture_snapshot(code_workspace_args)
    code_workspaces = {
        item["reference"]: item["requestedPath"]
        for item in snapshot["codeWorkspaces"]
    }
    runtime = _materialize(ROOT / "workflows" / WORKFLOW_SCRIPT_NAME, workspace, feature)
    return {
        "ok": True,
        # Keep the host contract identical to the Code Workflow launcher: the
        # caller may start the fixed JS only when these explicit guards are
        # present, and must forward workflowArgs without reconstruction.
        "useWorkflow": True,
        "strategy": "fixed",
        "canStartWorkflow": True,
        "feature": feature,
        "artifactWorkspace": str(workspace.resolve()),
        "snapshotDigest": snapshot["digest"],
        "executionMode": "fixed",
        "requiredAction": "start_fixed_plan_generation_workflow",
        "reason": "fixed_workflow_for_plan_generation",
        "maxParallel": max_parallel,
        "workflowArgs": {
            "feature": feature,
            "pluginPath": str(ROOT),
            "artifactWorkspace": str(workspace.resolve()),
            "codeWorkspaces": code_workspaces,
            "partitions": list(partitions or []),
            "maxParallel": max_parallel,
            "leaseTtlSeconds": lease_ttl_seconds,
        },
        **runtime,
        # workflow_launcher.py exposes this alias too.  Retaining it makes
        # Plan and Code launcher payloads interchangeable to the host while
        # workflowScriptPath remains the sole executable path.
        "workflowScript": runtime["workflowScriptPath"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare the fixed Plan generation Workflow")
    parser.add_argument("--workspace")
    parser.add_argument("--feature", required=True)
    parser.add_argument("--code-workspace", required=True, action="append")
    parser.add_argument("--partition", action="append")
    parser.add_argument("--max-parallel", type=int, default=DEFAULT_MAX_PARALLEL)
    parser.add_argument("--lease-ttl-seconds", type=int, default=900)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        result = build_workflow_request(
            workspace,
            feature,
            args.code_workspace,
            partitions=args.partition,
            max_parallel=args.max_parallel,
            lease_ttl_seconds=args.lease_ttl_seconds,
        )
    except (PlanGenerationError, ValueError, OSError) as exc:
        reason = exc.reason if isinstance(exc, PlanGenerationError) else "plan_generation_workflow_launcher_error"
        detail = exc.detail if isinstance(exc, PlanGenerationError) else str(exc)
        print(json.dumps({"ok": False, "errors": [{"reason": reason, "detail": detail}]}, ensure_ascii=False, indent=2))
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"fixed plan workflow: {result['workflowScriptPath']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
