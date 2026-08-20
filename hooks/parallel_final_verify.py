#!/usr/bin/env python3
"""Run the final compile gate against the merged main worktree."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from hooks.json_writer_common import atomic_write_json, resolve_feature, resolve_workspace
from hooks.parallel_runtime import append_event, load_manifest, plan_digest, run_dir, run_lock, save_manifest
from hooks.plan_json import load_plan_bundle
from hooks.task_runner import TaskRunnerError, _run_validation
from hooks.validation_policy import command_policy_errors, compile_only_command_errors


def _commands(bundle: Any) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for batch in bundle.batches.values():
        workspace_refs = {
            str(task.get("workspaceRef"))
            for task in batch.get("tasks", [])
            if isinstance(task, dict) and isinstance(task.get("workspaceRef"), str)
        }
        if len(workspace_refs) != 1:
            continue
        workspace_ref = next(iter(workspace_refs))
        validation = batch.get("batchValidation") if isinstance(batch, dict) else None
        for command in validation.get("commands", []) if isinstance(validation, dict) else []:
            if not isinstance(command, dict) or command.get("kind") != "compile" or command.get("required") is not True:
                continue
            component_roots = sorted(
                {
                    str(root)
                    for task in batch.get("tasks", [])
                    if isinstance(task, dict)
                    for root in (
                        (task.get("scope", {}).get("workspaceRoots", {}) or {}).values()
                        if isinstance(task.get("scope"), dict)
                        and isinstance(task.get("scope", {}).get("workspaceRoots"), dict)
                        else []
                    )
                    if isinstance(root, str) and root
                }
            )
            key = json.dumps({
                "workspaceRef": workspace_ref,
                "componentRoots": component_roots,
                **{key: command.get(key) for key in ("argv", "cwd", "repo")},
            }, sort_keys=True)
            if key not in seen:
                seen.add(key)
                result.append((workspace_ref, dict(command)))
    return result


def verify_final(workspace: Path, feature: str, run_id: str, repo_path: Path | None = None) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        repositories = manifest.get("repositories", {})
        if not isinstance(repositories, dict) or not repositories:
            raise ValueError("parallel_final_verify_repository_bindings_missing")
        for ref, repository in repositories.items():
            if not isinstance(repository, dict) or not isinstance(repository.get("gitRoot"), str):
                raise ValueError(f"parallel_final_verify_repository_invalid:{ref}")
            root = Path(repository["gitRoot"])
            if repo_path is not None and len(repositories) == 1 and root != repo_path.resolve():
                raise ValueError(f"parallel_final_verify_repository_binding_mismatch:{ref}")
            dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True)
            if dirty.returncode != 0:
                raise ValueError(f"parallel_final_verify_repository_invalid:{ref}")
            if dirty.stdout.strip():
                raise ValueError(f"parallel_final_verify_main_worktree_dirty:{ref}")
            expected_head = repository.get("headSha") or repository.get("baseSha")
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True)
            if head.returncode != 0 or not isinstance(expected_head, str) or head.stdout.strip() != expected_head:
                raise ValueError(f"parallel_final_verify_main_head_changed:{ref}")
        bundle = load_plan_bundle(workspace / ".autobizdevops" / "features" / feature)
        if plan_digest(bundle) != manifest.get("planDigest"):
            raise ValueError("parallel_plan_digest_changed")
        incomplete = [
            batch_id for batch_id, item in manifest.get("batches", {}).items()
            if not isinstance(item, dict) or item.get("status") != "merged"
        ]
        if incomplete:
            raise ValueError("parallel_final_verify_incomplete_batches:" + ",".join(incomplete))
        commands = _commands(bundle)
        if not commands:
            raise ValueError("parallel_final_compile_commands_missing")
        policy_errors = {
            f"{workspace_ref}:{command.get('id', index)}": command_policy_errors(command) + compile_only_command_errors(command)
            for index, (workspace_ref, command) in enumerate(commands, start=1)
        }
        policy_errors = {key: value for key, value in policy_errors.items() if value}
        if policy_errors:
            raise ValueError("parallel_final_compile_policy_invalid:" + json.dumps(policy_errors, sort_keys=True))
        manifest["status"] = "verifying"
        save_manifest(workspace, feature, run_id, manifest)

    results: list[dict[str, Any]] = []
    failed_command: str | None = None
    for index, (workspace_ref, command) in enumerate(commands, start=1):
        command_id = str(command.get("id", f"FINAL-{index:03d}"))
        if failed_command is not None:
            results.append({
                "commandId": command_id,
                "workspaceRef": workspace_ref,
                "passed": False,
                "skipped": True,
                "skipReason": f"previous_command_failed:{failed_command}",
            })
            continue
        try:
            repository = manifest["repositories"].get(workspace_ref, {})
            root = Path(str(repository["gitRoot"]))
            # Command repo names are the Plan's workspaceRef identifiers, not
            # inferred directory basenames.
            command.setdefault("repo", workspace_ref)
            exit_code, output = _run_validation(command, {workspace_ref: root}, run_id=run_id, batch_id="FINAL")
            passed = exit_code == 0
        except (TaskRunnerError, OSError) as exc:
            output, passed = str(exc), False
        results.append({
            "commandId": command_id,
            "workspaceRef": workspace_ref,
            "passed": passed,
            "outputSha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "outputTail": output[-4000:],
        })
        if not passed:
            failed_command = command_id

    passed = bool(results) and all(item["passed"] for item in results)
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        manifest["finalVerification"] = {"passed": passed, "commands": results}
        manifest["status"] = "succeeded" if passed else "blocked"
        save_manifest(workspace, feature, run_id, manifest)
        atomic_write_json(run_dir(workspace, feature, run_id) / "final-verification.json", manifest["finalVerification"])
        append_event(workspace, feature, run_id, "final_verification_completed", passed=passed)
    return {"runId": run_id, "passed": passed, "commands": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify merged parallel Code batches")
    parser.add_argument("--workspace")
    parser.add_argument("--feature", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repo-path", help="旧单仓库校验参数；默认从 manifest 读取全部仓库")
    args = parser.parse_args(argv)
    try:
        result = verify_final(resolve_workspace(args.workspace), resolve_feature(args.feature), args.run_id, Path(args.repo_path) if args.repo_path else None)
        print(json.dumps({"ok": result["passed"], **result}, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
