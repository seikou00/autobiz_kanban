#!/usr/bin/env python3
"""Coordinate one fixed native-worktree Workflow per physical Git repository.

The platform does not provide a repository-selectable Worktree API. This hook
keeps one durable cross-repository scheduler run, then emits child Workflow
requests grouped by physical Git root. Each child asks the plugin's
``worktree_manager.py`` to create a linked native Git worktree from that
binding; the caller launches those requests in parallel and calls ``next``
after the whole wave has merged.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import resolve_feature, resolve_workspace  # noqa: E402
from hooks.parallel_batch_scheduler import ensure_run, resume_run  # noqa: E402
from hooks.parallel_final_verify import verify_final  # noqa: E402
from hooks.parallel_runtime import load_manifest  # noqa: E402


def _parse_workspaces(values: list[str] | None) -> list[str]:
    return [value for value in (values or []) if isinstance(value, str) and value.strip()]


def _repository_requests(
    manifest: dict[str, Any],
    scheduler: dict[str, Any],
    *,
    feature: str,
    plugin_path: str,
    artifact_workspace: str,
) -> list[dict[str, Any]]:
    batch_ids = [
        str(batch_id)
        for group in scheduler.get("scheduledGroups", [])
        if isinstance(group, list)
        for batch_id in group
        if isinstance(batch_id, str)
    ]
    if not batch_ids:
        return []
    repositories = manifest.get("repositories", {})
    batches = manifest.get("batches", {})
    grouped: dict[str, dict[str, Any]] = {}
    for batch_id in batch_ids:
        batch = batches.get(batch_id)
        if not isinstance(batch, dict):
            continue
        ref = str(batch.get("repositoryRef") or batch.get("workspaceRef") or "")
        binding = repositories.get(ref)
        if not ref or not isinstance(binding, dict) or not isinstance(binding.get("gitRoot"), str):
            raise ValueError(f"repository_coordinator_binding_missing:{batch_id}")
        root = str(Path(binding["gitRoot"]).resolve())
        item = grouped.setdefault(root, {"gitRoot": root, "workspaceRefs": [], "batchIds": []})
        if ref not in item["workspaceRefs"]:
            item["workspaceRefs"].append(ref)
        item["batchIds"].append(batch_id)

    requests: list[dict[str, Any]] = []
    for root, item in sorted(grouped.items()):
        workspace_map = {
            ref: str(Path(repositories[ref]["gitRoot"]).resolve())
            for ref in sorted(item["workspaceRefs"])
        }
        ordered_refs = sorted(item["workspaceRefs"])
        requests.append({
            "repositoryRef": ordered_refs[0],
            "repositoryRefs": ordered_refs,
            "batchIds": item["batchIds"],
            "workflowHostGitRoot": root,
            "workflowArgs": {
                "feature": feature,
                "pluginPath": plugin_path,
                "artifactWorkspace": artifact_workspace,
                "codeWorkspaces": workspace_map,
                "workflowHostGitRoot": root,
                "repositoryRefs": ordered_refs,
                "batchIds": item["batchIds"],
                "coordinatorManaged": True,
                "maxParallel": int(manifest.get("maxParallel", 4)),
                "timeoutPerBatch": int(manifest.get("timeoutPerBatch", 3600)),
            },
        })
    return requests


def _result(
    workspace: Path,
    feature: str,
    scheduler: dict[str, Any],
    *,
    plugin_path: str = "",
) -> dict[str, Any]:
    run_id = str(scheduler.get("runId", ""))
    manifest = load_manifest(workspace, feature, run_id)
    artifact_workspace = str(workspace.resolve())
    requests = _repository_requests(
        manifest,
        scheduler,
        feature=feature,
        plugin_path=plugin_path or str(ROOT),
        artifact_workspace=artifact_workspace,
    )
    all_merged = all(
        isinstance(item, dict) and item.get("status") == "merged" and item.get("mergeCommitSha")
        for item in manifest.get("batches", {}).values()
    )
    return {
        "ok": True,
        "runId": run_id,
        "status": scheduler.get("status"),
        "scheduledGroups": scheduler.get("scheduledGroups", []),
        "repositoryWorkflows": requests,
        "waitingForRepositories": bool(scheduler.get("waitingForRepositories")),
        "allMerged": all_merged,
        "nextAction": "final_verify" if all_merged else ("launch_repository_workflows" if requests else "wait_for_other_repository_workflows"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coordinate fixed Workflows across physical Git repositories")
    parser.add_argument("command", choices=("prepare", "next", "final-verify"))
    parser.add_argument("--workspace")
    parser.add_argument("--feature", required=True)
    parser.add_argument("--plugin-path", default=str(ROOT))
    parser.add_argument("--run-id")
    parser.add_argument("--max-parallel", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--code-workspace", action="append")
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        if args.command == "prepare":
            if not _parse_workspaces(args.code_workspace):
                raise ValueError("repository_coordinator_code_workspace_required")
            scheduler = ensure_run(
                workspace,
                feature,
                max_parallel=args.max_parallel,
                timeout_seconds=args.timeout_seconds,
                code_workspaces=_parse_workspaces(args.code_workspace),
                # Keep the repository-coordinated path consistent with the
                # fixed single-repository Workflow.  The coordinator is part
                # of that controlled workflow, so dirty source checkouts may
                # be committed as an explicit bootstrap baseline before
                # child worktrees are provisioned.
                allow_bootstrap=True,
            )
        elif args.command == "next":
            if not args.run_id:
                raise ValueError("repository_coordinator_run_id_required")
            scheduler = resume_run(workspace, feature, args.run_id)
        else:
            if not args.run_id:
                raise ValueError("repository_coordinator_run_id_required")
            result = verify_final(workspace, feature, args.run_id)
            print(json.dumps({"ok": result["passed"], **result}, ensure_ascii=False, indent=2))
            return 0 if result["passed"] else 1
        result = _result(workspace, feature, scheduler, plugin_path=args.plugin_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
