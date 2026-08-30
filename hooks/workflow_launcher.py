#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Select the fixed, plan-aware Code batch workflow.

The workflow control plane is deliberately repository-owned and static. The
model implements individual tasks inside isolated worktrees, but it does not
generate the DAG scheduler or merge sequence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import feature_dir, resolve_workspace  # noqa: E402
from hooks.parallel_batch_scheduler import validate_plan_for_parallel  # noqa: E402
from hooks.parallel_runtime import batch_workspace_ref, plan_digest, resource_groups  # noqa: E402
from hooks.plan_json import BATCH_ID_RE, load_plan_bundle, plan_json_path  # noqa: E402
from hooks.repository_snapshot import RepositorySnapshotError, resolve_git_root  # noqa: E402


WORKFLOW_SCRIPT_NAME = "code-batched-execution.workflow.js"
WORKFLOW_RUNTIME_RELATIVE_PATH = ".cmbdevclaw/workflows/" + WORKFLOW_SCRIPT_NAME
DEFAULT_WORKFLOW_MAX_PARALLEL = 4
DEFAULT_WORKFLOW_TIMEOUT_SECONDS = 3600


def _plan_workspace_refs(bundle: Any) -> set[str]:
    refs: set[str] = set()
    for entry in bundle.root.get("batches", []) if isinstance(bundle.root, dict) else []:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("workspaceRef")
        batch = bundle.batches.get(str(entry.get("id", "")), {})
        ref = ref or batch_workspace_ref(batch)
        if isinstance(ref, str) and ref.strip():
            refs.add(ref.strip())
    return refs


def _workspace_contract_values(
    code_workspaces: Mapping[str, str] | None,
) -> tuple[dict[str, str], str]:
    """Resolve the Plan's required workspace refs to real code repositories."""
    if code_workspaces is None:
        raise ValueError("code_workspace_mapping_missing:plan.json:codeWorkspaces")

    if not isinstance(code_workspaces, Mapping):
        raise ValueError("code_workspace_mapping_invalid:expected_object")
    mapping: dict[str, str] = {}
    for key, value in code_workspaces.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip():
            raise ValueError("code_workspace_mapping_invalid:key_or_path_empty")
        if key in mapping:
            raise ValueError(f"code_workspace_mapping_duplicate:{key}")
        mapping[key.strip()] = value.strip()

    resolved: dict[str, str] = {}
    for key, raw_path in mapping.items():
        requested = Path(raw_path).expanduser().resolve(strict=False)
        if not requested.is_dir():
            raise ValueError(f"code_workspace_path_missing:{key}:{requested}")
        try:
            git_root = resolve_git_root(requested)
        except RepositorySnapshotError as exc:
            raise ValueError(f"code_workspace_not_git_repository:{key}:{requested}") from exc
        if key in resolved:
            raise ValueError(f"code_workspace_duplicate_repository:{key}")
        # Platform worktree isolation starts from the Workflow host's Git
        # checkout.  Persist the Git root, not a potentially nested module
        # directory, so the host contract is unambiguous.
        resolved[key] = str(git_root)

    return resolved, "plan_json"


def resolve_code_workspace_contract(
    bundle: Any,
    artifact_workspace: Path,
    feature: str,
) -> dict[str, Any]:
    refs = _plan_workspace_refs(bundle)
    plan_values = bundle.root.get("codeWorkspaces") if isinstance(bundle.root, dict) else None
    mapping, parsed_source = _workspace_contract_values(plan_values)
    if not refs:
        raise ValueError("code_workspace_refs_missing_from_plan")
    missing = sorted(refs - set(mapping))
    unexpected = sorted(set(mapping) - refs)
    if missing:
        raise ValueError("code_workspace_mapping_missing_refs:" + ",".join(missing))
    if unexpected:
        raise ValueError("code_workspace_mapping_unknown_refs:" + ",".join(unexpected))
    if parsed_source == "plan_json":
        contract_path = artifact_workspace / ".autobizdevops" / "features" / feature / "plan.json"
    else:
        contract_path = None
    git_roots = sorted(set(mapping.values()))
    return {
        "codeWorkspaces": mapping,
        "executionIsolation": "native_git_worktrees",
        # The plugin creates linked native Git worktrees from each repository
        # binding.  The workflow host may therefore be an artifact directory;
        # it no longer needs to be the business repository root.
        # The repository coordinator still turns multi-root contracts into one
        # child Workflow per root so each child has a single repository scope.
        "workflowHostGitRoot": git_roots[0] if len(git_roots) == 1 else None,
        "workflowHostGitRoots": git_roots,
        "repositoryCount": len(git_roots),
        "codeWorkspaceSource": parsed_source,
        "workspaceContractPath": str(contract_path) if contract_path else None,
    }


def materialize_workflow_script(source: Path, artifact_workspace: str) -> dict[str, str]:
    """Materialize an audit copy and return the inline source for the host."""
    target_root = Path(artifact_workspace).expanduser().resolve()
    if not target_root.is_dir():
        raise ValueError(f"workflow_artifact_workspace_missing:{target_root}")
    try:
        source_bytes = source.read_bytes()
    except OSError as exc:
        raise ValueError(f"fixed_workflow_script_unreadable:{source}:{exc}") from exc
    digest = hashlib.sha256(source_bytes).hexdigest()
    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"fixed_workflow_script_not_utf8:{source}:{exc}") from exc
    target = target_root / WORKFLOW_RUNTIME_RELATIVE_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            target.write_bytes(source_bytes)
    except OSError as exc:
        raise ValueError(f"fixed_workflow_runtime_copy_failed:{target}:{exc}") from exc
    return {
        "workflowScript": str(target),
        "workflowScriptSource": str(source),
        "workflowScriptSha256": digest,
        "workflowScriptContent": source_text,
    }


def _batch_execution_plan(
    batches: list[dict[str, Any]],
    code_workspaces: Mapping[str, str],
) -> dict[str, Any]:
    """Render the scheduler's deterministic preflight view for the caller.

    It deliberately stays a preview: a later scheduler resume can change the
    remaining waves after a merge failure or plan repair.  The initial order,
    dependencies, write-set serialization, and parallelism limit all use the
    same resource grouping logic as the runtime scheduler.
    """
    by_id = {str(batch["id"]): batch for batch in batches}
    remaining = set(by_id)
    completed = {
        dependency
        for batch in batches
        for dependency in batch.get("deps", [])
        if dependency not in by_id
    }
    preview_manifest = {
        "batches": {
            batch_id: {
                "repositoryRef": batch.get("workspaceRef"),
                "workspaceRef": batch.get("workspaceRef"),
                "gitRoot": code_workspaces.get(str(batch.get("workspaceRef") or "")),
                "executionStage": batch.get("executionStage", "parallel"),
                "writeSet": batch.get("writeSet", []),
            }
            for batch_id, batch in by_id.items()
        }
    }
    waves: list[dict[str, Any]] = []
    while remaining:
        ready = sorted(
            batch_id
            for batch_id in remaining
            if all(dependency in completed for dependency in by_id[batch_id].get("deps", []))
        )
        if not ready:
            # The plan validator reports the precise graph error separately.
            # Keep an inspectable fallback rather than looping forever when a
            # caller requests a preview for an invalid/incomplete draft.
            waves.append({"index": len(waves) + 1, "batchIds": sorted(remaining), "parallel": False, "blocked": True})
            break
        grouped = resource_groups(preview_manifest, ready)
        selected = grouped[0][:DEFAULT_WORKFLOW_MAX_PARALLEL] if grouped else ready[:DEFAULT_WORKFLOW_MAX_PARALLEL]
        waves.append(
            {
                "index": len(waves) + 1,
                "batchIds": selected,
                "parallel": len(selected) > 1,
                "blocked": False,
            }
        )
        completed.update(selected)
        remaining.difference_update(selected)
    return {
        "schemaVersion": 2,
        "maxParallel": DEFAULT_WORKFLOW_MAX_PARALLEL,
        "deliveryStages": ["prepare", "implement", "review", "test", "quality_gate"],
        "mergeBarrier": {
            "type": "merge_train",
            "validationBatch": "V-INT",
            "rule": "candidate_sha_must_pass_before_fast_forward_promotion",
        },
        "postMergeValidation": {
            "validationBatch": "V-E2E",
            "rule": "temporary_main_worktree_then_evidence_aggregate_only",
        },
        "batches": [
            {
                "id": batch["id"],
                "title": batch.get("title"),
                "taskIds": batch.get("taskIds", []),
                "taskCount": batch.get("taskCount", 0),
                "executionLane": batch.get("executionLane"),
                "workspaceRef": batch.get("workspaceRef"),
                "executionStage": batch.get("executionStage"),
                "dependencies": batch.get("deps", []),
                "writeSet": batch.get("writeSet", []),
            }
            for batch in batches
        ],
        "waves": waves,
        "notes": [
            "每个 Batch 依次完成 prepare、implement、review、test、quality gate；通过后才进入候选合并。",
            "每个 Wave 先在 Merge Train 候选 Worktree 运行 B-INT；只有同一 candidate SHA 通过才 fast-forward 推广并释放下游 Batch。",
            "所有 delivery Batch 推广后，B-E2E 在临时 main Worktree 运行；最终仅聚合证据，绝不重复执行验证命令。",
            "同一仓库的重叠写集会拆分为串行 Wave；原生 Git Worktree 仅隔离 checkout，不绕过该规则。",
        ],
    }


def analyze_batches(
    feature: str,
    plugin_path: Path | None = None,
    workspace: Path | None = None,
) -> dict:
    """Return the fixed workflow entrypoint for every valid pending Batch."""
    try:
        script_root = (plugin_path or ROOT).expanduser().resolve()
        artifact_workspace = resolve_workspace(workspace)
        feat_dir = feature_dir(artifact_workspace, feature)
        plan_path = plan_json_path(feat_dir)

        if not plan_path.exists():
            return {
                "useWorkflow": False,
                "strategy": "blocked",
                "batchCount": 0,
                "batches": [],
                "workflowScript": None,
                "reason": "plan_not_found",
                "requiresPlanRepair": True,
                "canStartWorkflow": False,
                "requiredAction": "repair_plan",
            }

        bundle = load_plan_bundle(feat_dir)
        batch_entries = [entry for entry in bundle.root.get("batches", []) if isinstance(entry, dict)]
        valid_batches: list[dict] = []
        for entry in batch_entries:
            batch_id = str(entry.get("id", ""))
            if not BATCH_ID_RE.fullmatch(batch_id):
                continue
            status = str(entry.get("status", "")).lower()
            if status in {"done", "failed"}:
                continue
            batch_plan = bundle.batches.get(batch_id, {})
            valid_batches.append({
                "id": batch_id,
                "title": entry.get("title") or batch_plan.get("title"),
                "lane": entry.get("executionLane", entry.get("lane", "unknown")),
                "executionLane": entry.get("executionLane", entry.get("lane", "unknown")),
                "workspaceRef": entry.get("workspaceRef") or batch_workspace_ref(batch_plan),
                "executionStage": entry.get("executionStage", "parallel"),
                "deps": list(entry.get("deps", [])),
                "status": status,
                "taskCount": len([task for task in batch_plan.get("tasks", []) if isinstance(task, dict)]),
                "taskIds": [
                    str(task.get("id"))
                    for task in batch_plan.get("tasks", [])
                    if isinstance(task, dict) and isinstance(task.get("id"), str)
                ],
                "writeSet": list(batch_plan.get("writeSet", entry.get("writeSet", [])) or []),
            })

        if not valid_batches:
            return {
                "useWorkflow": False,
                "strategy": "complete",
                "batchCount": 0,
                "batches": [],
                "workflowScript": None,
                "workflowScriptPath": None,
                "reason": "no_pending_batches",
                "canStartWorkflow": False,
                "requiredAction": "code_done_ready",
            }

        validation = validate_plan_for_parallel(artifact_workspace, feature)
        if not validation.get("canParallel"):
            return {
                "useWorkflow": False,
                "strategy": "blocked",
                "batchCount": len(valid_batches),
                "batches": valid_batches,
                "workflowScript": None,
                "reason": f"parallel_plan_invalid:{validation.get('reason')}",
                "requiresPlanRepair": True,
                "canStartWorkflow": False,
                "requiredAction": "repair_plan",
                "validation": validation,
            }

        workflow_script = script_root / "workflows" / "code-batched-execution.workflow.js"
        if not workflow_script.is_file():
            return {
                "useWorkflow": False,
                "strategy": "blocked",
                "batchCount": len(valid_batches),
                "batches": valid_batches,
                "workflowScript": None,
                "reason": "fixed_workflow_script_not_found",
                "canStartWorkflow": False,
                "requiredAction": "restore_fixed_workflow_script",
                "validation": validation,
            }

        try:
            workspace_contract = resolve_code_workspace_contract(
                bundle,
                artifact_workspace,
                feature,
            )
        except ValueError as exc:
            return {
                "useWorkflow": False,
                "strategy": "blocked",
                "batchCount": len(valid_batches),
                "batches": valid_batches,
                "artifactWorkspace": str(artifact_workspace),
                "workflowScript": str(workflow_script),
                "reason": str(exc),
                "requiresPlanRepair": False,
                "canStartWorkflow": False,
                "requiredAction": "provide_code_workspace_mapping",
                "validation": validation,
            }

        runtime_script = materialize_workflow_script(
            workflow_script,
            str(artifact_workspace),
        )
        common_result = {
            "useWorkflow": True,
            "strategy": "fixed",
            "batchCount": len(valid_batches),
            "batches": valid_batches,
            "artifactWorkspace": str(artifact_workspace),
            **runtime_script,
            "workflowScriptPath": runtime_script["workflowScript"],
            "codeWorkspaces": workspace_contract["codeWorkspaces"],
            "executionIsolation": workspace_contract["executionIsolation"],
            "workflowHostGitRoot": workspace_contract["workflowHostGitRoot"],
            "workflowHostGitRoots": workspace_contract["workflowHostGitRoots"],
            "codeWorkspaceSource": workspace_contract["codeWorkspaceSource"],
            "workspaceContractPath": workspace_contract["workspaceContractPath"],
            "planDigest": plan_digest(bundle),
            "batchExecutionPlan": _batch_execution_plan(
                valid_batches,
                workspace_contract["codeWorkspaces"],
            ),
            "canStartWorkflow": True,
            "validation": validation,
        }
        if workspace_contract["repositoryCount"] == 1:
            # This is the complete payload for the platform workflow call.
            # Returning it avoids models reconstructing a workflow or guessing
            # a code workspace from the artifact directory.
            return {
                **common_result,
                "executionMode": "fixed",
                "workflowArgs": {
                    "feature": feature,
                    "pluginPath": str(script_root),
                    "artifactWorkspace": str(artifact_workspace),
                    "codeWorkspaces": workspace_contract["codeWorkspaces"],
                    "workflowHostGitRoot": workspace_contract["workflowHostGitRoot"],
                    "maxParallel": DEFAULT_WORKFLOW_MAX_PARALLEL,
                    "timeoutPerBatch": DEFAULT_WORKFLOW_TIMEOUT_SECONDS,
                },
                "reason": f"fixed_workflow_for_pending_batches:{len(valid_batches)}",
                "requiredAction": "start_fixed_workflow",
            }

        coordinator_path = script_root / "hooks" / "repository_workflow_coordinator.py"
        if not coordinator_path.is_file():
            return {
                **common_result,
                "useWorkflow": False,
                "strategy": "blocked",
                "executionMode": "repository_coordinated",
                "reason": "repository_workflow_coordinator_not_found",
                "canStartWorkflow": False,
                "requiredAction": "restore_repository_workflow_coordinator",
            }
        # The parent Code session invokes this coordinator before each DAG
        # wave. It returns child workflow args with exactly one physical Git
        # root. The plugin provisions the native worktree for that binding;
        # the workflow host itself is not a repository-routing contract.
        return {
            **common_result,
            "strategy": "repository_coordinated",
            "executionMode": "repository_coordinated",
            "workflowArgs": {
                "feature": feature,
                "pluginPath": str(script_root),
                "artifactWorkspace": str(artifact_workspace),
                "codeWorkspaces": workspace_contract["codeWorkspaces"],
                "maxParallel": DEFAULT_WORKFLOW_MAX_PARALLEL,
                "timeoutPerBatch": DEFAULT_WORKFLOW_TIMEOUT_SECONDS,
            },
            "repositoryCoordinator": {
                "path": str(coordinator_path),
                "prepareCommand": "prepare",
                "nextCommand": "next",
                "workflowInvocation": "launch_each_repository_workflow_in_parallel",
            },
            "reason": f"repository_coordinated_workflow_for_pending_batches:{len(valid_batches)}",
            "requiredAction": "start_repository_coordinator",
        }
    except Exception as exc:
        return {
            "useWorkflow": False,
            "strategy": "blocked",
            "batchCount": 0,
            "batches": [],
            "workflowScript": None,
            "reason": f"launcher_error:{type(exc).__name__}:{exc}",
            "requiresPlanRepair": False,
            "canStartWorkflow": False,
            "requiredAction": "stop_on_launcher_error",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Choose the fixed Code batch workflow")
    parser.add_argument("--feature", required=True, help="Feature ID")
    parser.add_argument("--plugin-path", help="Plugin source path; defaults to this repository")
    parser.add_argument("--workspace", help="Artifact workspace containing .autobizdevops/state.json")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    result = analyze_batches(
        args.feature,
        Path(args.plugin_path) if args.plugin_path else None,
        Path(args.workspace) if args.workspace else None,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["useWorkflow"]:
        print(f"fixed workflow: {result['workflowScript']}")
        print(f"pending batches: {result['batchCount']}")
    else:
        print(f"workflow unavailable: {result['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
