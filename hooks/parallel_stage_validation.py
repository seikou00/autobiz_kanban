#!/usr/bin/env python3
"""Execute exactly the Plan-owned validation commands for one pipeline stage.

This is the command boundary behind the staged Workflow.  It resolves each
command through ``parallelBatchPipeline.validationOwnership`` instead of
asking an agent to reconstruct a test command from prose.  Consequently a
command can be run only by its declared delivery or validation Batch.
"""

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

from hooks.json_writer_common import resolve_feature, resolve_workspace
from hooks.parallel_batch_stage import complete_stage, fail_stage, start_stage
from hooks.parallel_failure_classifier import classify_failure
from hooks.parallel_runtime import load_manifest
from hooks.plan_json import PlanBundle, load_plan_bundle
from hooks.task_runner import TaskRunnerError, _run_validation


def _command_index(bundle: PlanBundle) -> dict[str, tuple[dict[str, Any], str | None]]:
    """Return the canonical command and its source repository reference."""
    result: dict[str, tuple[dict[str, Any], str | None]] = {}
    for command in bundle.root.get("projectValidationCommands", []):
        if isinstance(command, dict) and isinstance(command.get("id"), str):
            result[command["id"]] = (dict(command), str(command.get("repo")) if command.get("repo") else None)
    for entry in bundle.root.get("batches", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        batch = bundle.batches.get(entry["id"], {})
        for command in (batch.get("batchValidation") or {}).get("commands", []):
            if isinstance(command, dict) and isinstance(command.get("id"), str):
                result[command["id"]] = (dict(command), str(command.get("repo")) if command.get("repo") else str(entry.get("workspaceRef") or "") or None)
        for task in batch.get("tasks", []):
            if not isinstance(task, dict):
                continue
            task_ref = str(task.get("workspaceRef") or "") or None
            for command in task.get("validationCommands", []):
                if isinstance(command, dict) and isinstance(command.get("id"), str):
                    result[command["id"]] = (dict(command), str(command.get("repo")) if command.get("repo") else task_ref)
    return result


def owned_commands(
    bundle: PlanBundle,
    *,
    batch_id: str,
    stage: str,
    source_batch_ids: set[str] | None = None,
    repository_ref: str | None = None,
) -> list[dict[str, Any]]:
    """Resolve commands owned by exactly ``batch_id``/``stage`` in Plan order."""
    pipeline = bundle.root.get("parallelBatchPipeline")
    ownership = pipeline.get("validationOwnership", {}) if isinstance(pipeline, dict) else {}
    if not isinstance(ownership, dict):
        raise ValueError("parallel_validation_ownership_missing")
    index = _command_index(bundle)
    selected: list[dict[str, Any]] = []
    for command_id, owner in ownership.items():
        if not isinstance(owner, dict) or owner.get("ownerBatchId") != batch_id or owner.get("stage") != stage:
            continue
        source_batch_id = owner.get("sourceBatchId")
        if source_batch_ids is not None and source_batch_id and source_batch_id not in source_batch_ids:
            continue
        item = index.get(command_id)
        if item is None:
            # REVIEW-* is intentionally a human/read-only stage criterion,
            # not a shell command.  Every executable owner must resolve.
            if owner.get("kind") == "review":
                continue
            raise ValueError(f"parallel_owned_command_missing:{command_id}")
        command, command_ref = item
        ref = command_ref or repository_ref
        if repository_ref is not None and ref is not None and ref != repository_ref:
            continue
        if ref:
            command["repo"] = ref
        selected.append({"commandId": command_id, "command": command, "owner": owner, "repositoryRef": ref})
    return selected


def _repository_paths(
    manifest: dict[str, Any],
    batch_id: str,
    *,
    worktree: Path | None,
    repository_ref: str | None,
) -> dict[str, Path]:
    if worktree is not None:
        if not repository_ref:
            raise ValueError("parallel_stage_validation_repository_ref_required")
        return {repository_ref: worktree.resolve()}
    if batch_id in manifest.get("batches", {}):
        batch = manifest["batches"][batch_id]
        ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
        raw_path = batch.get("worktreePath")
        if not ref or not isinstance(raw_path, str) or not raw_path:
            raise ValueError(f"parallel_stage_validation_worktree_missing:{batch_id}")
        return {ref: Path(raw_path).resolve()}
    validation = (manifest.get("validationBatches") or {}).get(batch_id)
    worktrees = validation.get("worktrees") if isinstance(validation, dict) else None
    if not isinstance(worktrees, dict) or not worktrees:
        raise ValueError(f"parallel_stage_validation_worktree_missing:{batch_id}")
    return {str(ref): Path(str(path)).resolve() for ref, path in worktrees.items()}


def run_owned_stage(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    stage: str,
    *,
    worktree: Path | None = None,
    repository_ref: str | None = None,
    source_batch_ids: set[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run all commands uniquely owned by the stage and write one evidence item."""
    manifest = load_manifest(workspace, feature, run_id)
    bundle = load_plan_bundle(workspace / ".autobizdevops" / "features" / feature)
    repositories = _repository_paths(manifest, batch_id, worktree=worktree, repository_ref=repository_ref)
    selected = owned_commands(
        bundle,
        batch_id=batch_id,
        stage=stage,
        source_batch_ids=source_batch_ids,
        repository_ref=repository_ref,
    )
    states = (
        (manifest.get("batches") or {}).get(batch_id, {}).get("stageStates")
        or (manifest.get("validationBatches") or {}).get(batch_id, {}).get("stageStates")
        or {}
    )
    state = states.get(stage) if isinstance(states, dict) else None
    if isinstance(state, dict) and state.get("status") == "passed":
        return {"success": True, "reused": True, "batchId": batch_id, "stage": stage, "commands": []}
    if not isinstance(state, dict) or state.get("status") != "running":
        start_stage(workspace, feature, run_id, batch_id, stage)

    results: list[dict[str, Any]] = []
    for item in selected:
        command = item["command"]
        command_id = item["commandId"]
        try:
            exit_code, output = _run_validation(command, repositories, run_id=run_id, batch_id=batch_id)
        except (TaskRunnerError, OSError) as exc:
            exit_code, output = 1, str(exc)
        results.append({
            "commandId": command_id,
            "passed": exit_code == 0,
            "repositoryRef": command.get("repo"),
            "cwd": command.get("cwd"),
            "command": {key: command.get(key) for key in ("id", "argv", "cwd", "kind", "repo")},
            "outputSha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "outputTail": output[-4000:],
        })
    if all(item["passed"] for item in results):
        completed = complete_stage(
            workspace,
            feature,
            run_id,
            batch_id,
            stage,
            metadata={**dict(metadata or {}), "commands": results, "commandCount": len(results)},
        )
        return {"success": True, "batchId": batch_id, "stage": stage, "commands": results, "completion": completed}
    logs = "\n".join(str(item["outputTail"]) for item in results if not item["passed"])
    classified = classify_failure(stage, logs)
    failed = fail_stage(
        workspace,
        feature,
        run_id,
        batch_id,
        stage,
        failure_type=str(classified["failureType"]),
        message="owned_validation_failed",
    )
    return {"success": False, "batchId": batch_id, "stage": stage, "commands": results, "classification": classified, "failure": failed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the uniquely-owned Plan validation commands for a staged Batch")
    parser.add_argument("run", nargs="?")
    parser.add_argument("--workspace")
    parser.add_argument("--feature", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--worktree")
    parser.add_argument("--repository-ref")
    parser.add_argument("--source-batch-id", action="append", default=[])
    parser.add_argument("--metadata-json", default="{}")
    args = parser.parse_args(argv)
    try:
        metadata = json.loads(args.metadata_json)
        if not isinstance(metadata, dict):
            raise ValueError("parallel_stage_validation_metadata_must_be_object")
        result = run_owned_stage(
            resolve_workspace(args.workspace),
            resolve_feature(args.feature),
            args.run_id,
            args.batch_id,
            args.stage,
            worktree=Path(args.worktree).resolve() if args.worktree else None,
            repository_ref=args.repository_ref,
            source_batch_ids=set(args.source_batch_id) or None,
            metadata=metadata,
        )
        print(json.dumps({"ok": bool(result.get("success")), **result}, ensure_ascii=False, indent=2))
        return 0 if result.get("success") else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
