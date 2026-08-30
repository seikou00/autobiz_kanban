#!/usr/bin/env python3
"""Build, verify and promote immutable parallel Batch merge candidates.

The candidate worktree is the only checkout used for integration validation.
Promotion fast-forwards the exact SHA that passed, so a passing test can never
be accidentally attributed to a later re-merge on the primary checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_kernel import FileLock
from hooks.json_writer_common import atomic_write_json, resolve_feature, resolve_workspace
from hooks.parallel_batch_stage import complete_stage, fail_stage, gate_batch, reset_validation_batch, start_stage
from hooks.parallel_runtime import append_event, load_manifest, mergeable_batches, run_dir, run_lock, save_manifest, utc_now
from hooks.parallel_repair import create_repair_batch
from hooks.parallel_stage_validation import owned_commands
from hooks.plan_json import load_plan_bundle
from hooks.plan_writer import mark_parallel_batch_tasks_merged
from hooks.repository_snapshot import git_status_porcelain
from hooks.task_runner import TaskRunnerError, _run_validation


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value).strip("-.") or "default"


def _head(repo: Path) -> str:
    result = _git(repo, "rev-parse", "HEAD")
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError(f"parallel_merge_train_head_unavailable:{repo}")
    return result.stdout.strip()


def _clean(repo: Path) -> None:
    status = git_status_porcelain(repo)
    if status.returncode != 0:
        raise ValueError(f"parallel_merge_train_status_unavailable:{repo}")
    if status.stdout.strip():
        raise ValueError(f"parallel_merge_train_main_worktree_dirty:{repo}")


def _record_key(repository_ref: str, wave: int) -> str:
    return f"{repository_ref}:wave-{wave:03d}"


def _record(manifest: dict[str, Any], repository_ref: str, wave: int) -> dict[str, Any] | None:
    value = manifest.get("mergeTrains", {}).get(_record_key(repository_ref, wave))
    return value if isinstance(value, dict) else None


def _candidate_paths(workspace: Path, feature: str, run_id: str, repository_ref: str, wave: int) -> tuple[Path, str]:
    path = run_dir(workspace, feature, run_id) / "merge-trains" / _safe(repository_ref) / f"wave-{wave:03d}"
    branch = f"autodev-candidate/{_safe(feature)}/{_safe(run_id)}/{_safe(repository_ref)}/wave-{wave:03d}"
    return path, branch


def _remove_candidate(repo: Path, path: Path, branch: str) -> list[str]:
    errors: list[str] = []
    if path.exists():
        removed = _git(repo, "worktree", "remove", "--force", str(path))
        if removed.returncode != 0:
            errors.append("worktree_remove_failed:" + (removed.stderr.strip() or removed.stdout.strip()))
    pruned = _git(repo, "worktree", "prune")
    if pruned.returncode != 0:
        errors.append("worktree_prune_failed:" + (pruned.stderr.strip() or pruned.stdout.strip()))
    exists = _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    if exists.returncode == 0:
        deleted = _git(repo, "branch", "-D", branch)
        if deleted.returncode != 0:
            errors.append("branch_remove_failed:" + (deleted.stderr.strip() or deleted.stdout.strip()))
    return errors


def build_candidate(
    workspace: Path,
    feature: str,
    run_id: str,
    *,
    wave: int,
    batch_ids: list[str],
) -> dict[str, Any]:
    """Create one lock-protected candidate from already-gated delivery branches."""
    ids = sorted(set(batch_ids))
    if not ids:
        raise ValueError("parallel_merge_train_batch_ids_required")
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        eligible = set(mergeable_batches(manifest))
        invalid = [batch_id for batch_id in ids if batch_id not in eligible]
        if invalid:
            raise ValueError("parallel_merge_train_batch_not_ready_to_candidate:" + ",".join(invalid))
        batches = manifest["batches"]
        refs = {str(batches[batch_id].get("repositoryRef") or batches[batch_id].get("workspaceRef") or "") for batch_id in ids}
        if len(refs) != 1 or not next(iter(refs), ""):
            raise ValueError("parallel_merge_train_single_repository_required")
        repository_ref = next(iter(refs))
        binding = manifest.get("repositories", {}).get(repository_ref)
        if not isinstance(binding, dict) or not isinstance(binding.get("gitRoot"), str):
            raise ValueError(f"parallel_merge_train_repository_missing:{repository_ref}")
        existing = _record(manifest, repository_ref, wave)
        commits = {batch_id: str(batches[batch_id].get("commitSha") or "") for batch_id in ids}
        if existing and existing.get("batchIds") == ids and existing.get("deliveryCommits") == commits and existing.get("status") in {"built", "verified"}:
            return {"success": True, "reused": True, **existing}
        if existing and existing.get("status") not in {"stale", "failed", "promoted"}:
            raise ValueError(f"parallel_merge_train_wave_occupied:{repository_ref}:{wave}")
        repo = Path(binding["gitRoot"]).resolve()
        expected_head = str(binding.get("headSha") or binding.get("baseSha") or "")
        _clean(repo)
        current_head = _head(repo)
        if not expected_head or current_head != expected_head:
            raise ValueError(f"parallel_merge_train_main_head_changed:{repository_ref}:{expected_head}:{current_head}")
        path, branch = _candidate_paths(workspace, feature, run_id, repository_ref, wave)
        lock_path = path.parent / ".candidate.lock"

    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(lock_path):
        # Repeat no shared-state mutations under the candidate-specific lock;
        # it serializes concurrent Workflow instances for this repository.
        if path.exists() or _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
            cleanup_errors = _remove_candidate(repo, path, branch)
            if cleanup_errors:
                raise ValueError("parallel_merge_train_stale_candidate_cleanup_failed:" + ";".join(cleanup_errors))
        created = _git(repo, "worktree", "add", "-b", branch, str(path), current_head)
        if created.returncode != 0:
            raise ValueError("parallel_merge_train_create_failed:" + (created.stderr.strip() or created.stdout.strip()))
        merged: list[dict[str, str]] = []
        failure: str | None = None
        try:
            for batch_id in ids:
                source = str(manifest["batches"][batch_id].get("branchName") or "")
                if not source:
                    raise ValueError(f"parallel_merge_train_source_branch_missing:{batch_id}")
                result = _git(path, "merge", "--no-ff", "--no-edit", source)
                if result.returncode != 0:
                    _git(path, "merge", "--abort")
                    raise ValueError(f"parallel_merge_train_conflict:{batch_id}:{result.stderr.strip() or result.stdout.strip()}")
                merged.append({"batchId": batch_id, "branchName": source, "deliveryCommitSha": commits[batch_id], "candidateCommitSha": _head(path)})
        except ValueError as exc:
            failure = str(exc)
        candidate_sha = _head(path) if failure is None else None
        changed_files = _git(path, "diff", "--name-only", f"{current_head}..{candidate_sha}").stdout.splitlines() if candidate_sha else []
        if failure:
            cleanup_errors = _remove_candidate(repo, path, branch)
            with run_lock(workspace, feature, run_id):
                updated = load_manifest(workspace, feature, run_id)
                updated.setdefault("mergeTrains", {})[_record_key(repository_ref, wave)] = {
                    "repositoryRef": repository_ref, "wave": wave, "batchIds": ids, "deliveryCommits": commits,
                    "baseSha": current_head, "branchName": branch, "worktreePath": str(path), "status": "failed",
                    "error": failure, "cleanupErrors": cleanup_errors, "createdAt": utc_now(),
                }
                updated["status"] = "blocked"
                save_manifest(workspace, feature, run_id, updated)
            append_event(workspace, feature, run_id, "merge_train_failed", repositoryRef=repository_ref, wave=wave, batchIds=ids, error=failure)
            return {"success": False, "repositoryRef": repository_ref, "wave": wave, "batchIds": ids, "error": failure, "cleanupErrors": cleanup_errors}

        assert candidate_sha is not None
        record = {
            "repositoryRef": repository_ref, "wave": wave, "batchIds": ids, "deliveryCommits": commits,
            "baseSha": current_head, "candidateSha": candidate_sha, "branchName": branch, "worktreePath": str(path),
            "mergedDeliveries": merged, "changedFiles": changed_files, "status": "built", "createdAt": utc_now(),
        }
        with run_lock(workspace, feature, run_id):
            updated = load_manifest(workspace, feature, run_id)
            updated.setdefault("mergeTrains", {})[_record_key(repository_ref, wave)] = record
            save_manifest(workspace, feature, run_id, updated)
        reset_validation_batch(workspace, feature, run_id, "V-INT", candidate_sha=candidate_sha, candidate_base_sha=current_head, train_id=_record_key(repository_ref, wave), dependency_batch_ids=ids)
        append_event(workspace, feature, run_id, "merge_train_built", repositoryRef=repository_ref, wave=wave, batchIds=ids, candidateSha=candidate_sha)
        return {"success": True, "reused": False, **record}


def verify_candidate(workspace: Path, feature: str, run_id: str, *, wave: int, repository_ref: str) -> dict[str, Any]:
    """Run the Plan-owned B-INT commands only on the immutable candidate."""
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        record = _record(manifest, repository_ref, wave)
        if not record or record.get("status") not in {"built", "verified"}:
            raise ValueError(f"parallel_merge_train_candidate_not_built:{repository_ref}:{wave}")
        if record.get("status") == "verified":
            return {"success": True, "reused": True, **record}
        path = Path(str(record.get("worktreePath") or ""))
        if not path.is_dir() or _head(path) != record.get("candidateSha"):
            raise ValueError("parallel_merge_train_candidate_missing_or_changed")
        bundle = load_plan_bundle(workspace / ".autobizdevops" / "features" / feature)
        commands = owned_commands(
            bundle,
            batch_id="V-INT",
            stage="integration_test",
            source_batch_ids=set(str(item) for item in record.get("batchIds", [])),
            repository_ref=repository_ref,
        )
        repository = manifest.get("repositories", {}).get(repository_ref, {})
        if not commands:
            raise ValueError("parallel_merge_train_integration_commands_missing")
        if not isinstance(repository, dict):
            raise ValueError(f"parallel_merge_train_repository_missing:{repository_ref}")

    # The V-INT state intentionally survives a failed command, allowing
    # controlled repair or an explicit retry with a fresh candidate.
    start_stage(workspace, feature, run_id, "V-INT", "prepare")
    complete_stage(workspace, feature, run_id, "V-INT", "prepare", metadata={"candidateSha": record["candidateSha"], "trainId": _record_key(repository_ref, wave)})
    start_stage(workspace, feature, run_id, "V-INT", "integration_test")
    results: list[dict[str, Any]] = []
    passed = True
    for index, owned in enumerate(commands, start=1):
        command = dict(owned["command"])
        command_id = str(owned.get("commandId") or command.get("id") or f"PROJECT-VAL-{index:03d}")
        command.setdefault("repo", repository_ref)
        try:
            exit_code, output = _run_validation(command, {repository_ref: path.resolve()}, run_id=run_id, batch_id="V-INT")
        except (TaskRunnerError, OSError) as exc:
            exit_code, output = 1, str(exc)
        item = {"commandId": command_id, "passed": exit_code == 0, "repositoryPath": str(path.resolve()), "commandCwd": command.get("cwd"), "command": {key: command.get(key) for key in ("id", "argv", "cwd", "kind", "repo")}, "outputSha256": hashlib.sha256(output.encode()).hexdigest(), "outputTail": output[-4000:]}
        results.append(item)
        passed = passed and item["passed"]
    metadata = {"candidateSha": record["candidateSha"], "candidateBaseSha": record["baseSha"], "trainId": _record_key(repository_ref, wave), "commands": results}
    if not passed:
        failed = fail_stage(workspace, feature, run_id, "V-INT", "integration_test", failure_type="implementation", message="integration_test_failed")
        with run_lock(workspace, feature, run_id):
            updated = load_manifest(workspace, feature, run_id)
            target = _record(updated, repository_ref, wave)
            if target:
                target.update({"status": "failed", "validation": metadata, "failedAt": utc_now()})
            updated["status"] = "blocked"
            save_manifest(workspace, feature, run_id, updated)
        repair = create_repair_batch(
            workspace,
            feature,
            run_id,
            "V-INT",
            approved_plan_revision=str(manifest.get("pipeline", {}).get("planRevision") or ""),
            failure_context={
                "stage": "integration_test",
                "repositoryRef": repository_ref,
                "wave": wave,
                "candidateSha": record["candidateSha"],
                "deliveryBatchIds": record.get("batchIds", []),
                "commands": results,
            },
        )
        return {"success": False, "status": "failed", "failure": failed, "repair": repair.get("repair"), "commands": results, "candidateSha": record["candidateSha"]}
    complete_stage(workspace, feature, run_id, "V-INT", "integration_test", metadata=metadata)
    gate = gate_batch(workspace, feature, run_id, "V-INT")
    with run_lock(workspace, feature, run_id):
        updated = load_manifest(workspace, feature, run_id)
        target = _record(updated, repository_ref, wave)
        if target:
            target.update({"status": "verified", "validation": metadata, "verifiedAt": utc_now()})
        save_manifest(workspace, feature, run_id, updated)
    append_event(workspace, feature, run_id, "merge_train_verified", repositoryRef=repository_ref, wave=wave, candidateSha=record["candidateSha"])
    return {"success": bool(gate.get("success")), "status": "verified", "candidateSha": record["candidateSha"], "commands": results}


def promote_candidate(workspace: Path, feature: str, run_id: str, *, wave: int, repository_ref: str) -> dict[str, Any]:
    """Fast-forward main to exactly the previously verified candidate SHA."""
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        record = _record(manifest, repository_ref, wave)
        if not record or record.get("status") != "verified":
            raise ValueError(f"parallel_merge_train_candidate_not_verified:{repository_ref}:{wave}")
        binding = manifest.get("repositories", {}).get(repository_ref)
        if not isinstance(binding, dict) or not isinstance(binding.get("gitRoot"), str):
            raise ValueError(f"parallel_merge_train_repository_missing:{repository_ref}")
        repo = Path(binding["gitRoot"]).resolve()
        _clean(repo)
        current_head = _head(repo)
        if current_head != record.get("baseSha"):
            record.update({"status": "stale", "staleAt": utc_now(), "actualMainSha": current_head})
            save_manifest(workspace, feature, run_id, manifest)
            append_event(workspace, feature, run_id, "merge_train_stale", repositoryRef=repository_ref, wave=wave, candidateBaseSha=record.get("baseSha"), actualMainSha=current_head)
            return {"success": False, "stale": True, "action": "rebuild_candidate", "candidateSha": record.get("candidateSha"), "baseSha": record.get("baseSha"), "actualMainSha": current_head}
        promoted = _git(repo, "merge", "--ff-only", str(record.get("branchName") or ""))
        if promoted.returncode != 0:
            raise ValueError("parallel_merge_train_promote_failed:" + (promoted.stderr.strip() or promoted.stdout.strip()))
        promoted_sha = _head(repo)
        if promoted_sha != record.get("candidateSha"):
            raise ValueError("parallel_merge_train_promote_sha_mismatch")

    plan_errors: list[str] = []
    for batch_id in record["batchIds"]:
        result = mark_parallel_batch_tasks_merged(workspace, feature, batch_id, merge_commit_sha=promoted_sha, delivery_run_id=run_id)
        if not result.ok:
            plan_errors.extend(str(item.get("reason") or item) for item in (result.errors or []))
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        current = _record(manifest, repository_ref, wave)
        if plan_errors:
            if current:
                current.update({"status": "needs_resolution", "planWriterErrors": plan_errors, "promotedSha": promoted_sha})
            manifest["status"] = "needs_resolution"
            save_manifest(workspace, feature, run_id, manifest)
            return {"success": False, "needsPlanRecovery": True, "errors": plan_errors, "candidateSha": promoted_sha}
        for batch_id in record["batchIds"]:
            batch = manifest["batches"][batch_id]
            batch.update({"status": "merged", "mergeCommitSha": promoted_sha, "mergedAt": utc_now()})
        binding = manifest["repositories"][repository_ref]
        binding["headSha"] = promoted_sha
        if current:
            current.update({"status": "promoted", "promotedSha": promoted_sha, "promotedAt": utc_now()})
        manifest["status"] = "running"
        save_manifest(workspace, feature, run_id, manifest)

    path = Path(str(record["worktreePath"]))
    cleanup_errors = _remove_candidate(repo, path, str(record["branchName"]))
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        current = _record(manifest, repository_ref, wave)
        if current:
            current["cleanup"] = {"at": utc_now(), "errors": cleanup_errors}
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "merge_train_promoted", repositoryRef=repository_ref, wave=wave, candidateSha=promoted_sha, batchIds=record["batchIds"], cleanupErrors=cleanup_errors)
    # Promotion is durable even if temporary-resource cleanup needs a later
    # retry; report it as a successful promotion and expose cleanup separately.
    return {"success": True, "promoted": True, "candidateSha": promoted_sha, "batchIds": record["batchIds"], "cleanupErrors": cleanup_errors}


def begin_e2e(workspace: Path, feature: str, run_id: str) -> dict[str, Any]:
    """Open the single B-E2E validation Batch after all deliveries are merged."""
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        incomplete = [batch_id for batch_id, batch in manifest.get("batches", {}).items() if not isinstance(batch, dict) or batch.get("status") != "merged"]
        if incomplete:
            raise ValueError("parallel_e2e_delivery_incomplete:" + ",".join(sorted(incomplete)))
        heads = {ref: binding.get("headSha") for ref, binding in manifest.get("repositories", {}).items() if isinstance(binding, dict)}
        previous = manifest.get("validationBatches", {}).get("V-E2E", {})
        if isinstance(previous, dict) and previous.get("status") == "running" and isinstance(previous.get("worktrees"), dict):
            return {"success": True, "batchId": "V-E2E", "status": "running", "mainHeads": heads, "worktrees": previous["worktrees"], "reused": True}
        for ref, binding in manifest.get("repositories", {}).items():
            if not isinstance(binding, dict) or not isinstance(binding.get("gitRoot"), str) or not isinstance(heads.get(ref), str):
                raise ValueError(f"parallel_e2e_repository_invalid:{ref}")
    worktrees: dict[str, str] = {}
    try:
        for ref, binding in manifest["repositories"].items():
            repo = Path(str(binding["gitRoot"])).resolve()
            _clean(repo)
            target = run_dir(workspace, feature, run_id) / "validation-worktrees" / "e2e" / _safe(ref)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                removed = _git(repo, "worktree", "remove", "--force", str(target))
                if removed.returncode != 0:
                    raise ValueError(f"parallel_e2e_stale_worktree_remove_failed:{ref}")
            added = _git(repo, "worktree", "add", "--detach", str(target), str(heads[ref]))
            if added.returncode != 0:
                raise ValueError(f"parallel_e2e_worktree_create_failed:{ref}:{added.stderr.strip() or added.stdout.strip()}")
            worktrees[ref] = str(target)
    except ValueError:
        for ref, path in worktrees.items():
            _remove_candidate(Path(str(manifest["repositories"][ref]["gitRoot"])), Path(path), "")
        raise
    result = reset_validation_batch(workspace, feature, run_id, "V-E2E", candidate_sha=json.dumps(heads, sort_keys=True), candidate_base_sha=json.dumps(heads, sort_keys=True), train_id="merged-main", dependency_batch_ids=sorted(manifest.get("batches", {})))
    with run_lock(workspace, feature, run_id):
        updated = load_manifest(workspace, feature, run_id)
        updated["validationBatches"]["V-E2E"]["worktrees"] = worktrees
        save_manifest(workspace, feature, run_id, updated)
    start_stage(workspace, feature, run_id, "V-E2E", "prepare")
    complete_stage(workspace, feature, run_id, "V-E2E", "prepare", metadata={"mainHeads": heads, "worktrees": worktrees})
    start_stage(workspace, feature, run_id, "V-E2E", "e2e_test")
    return {"success": True, **result, "mainHeads": heads, "worktrees": worktrees, "reused": False}


def finish_e2e(workspace: Path, feature: str, run_id: str, *, passed: bool, metadata: dict[str, Any]) -> dict[str, Any]:
    manifest = load_manifest(workspace, feature, run_id)
    e2e = (manifest.get("validationBatches") or {}).get("V-E2E", {})
    worktrees = e2e.get("worktrees", {}) if isinstance(e2e, dict) else {}
    if not passed:
        result = fail_stage(workspace, feature, run_id, "V-E2E", "e2e_test", failure_type="implementation", message=str(metadata.get("message") or "e2e_test_failed"))
        repair = create_repair_batch(
            workspace,
            feature,
            run_id,
            "V-E2E",
            approved_plan_revision=str(manifest.get("pipeline", {}).get("planRevision") or ""),
            failure_context={
                "stage": "e2e_test",
                "mainHeads": manifest.get("repositories", {}),
                "metadata": metadata,
            },
        )
        return {"success": False, "result": result, "repair": repair.get("repair")}
    environment = metadata.get("environment")
    if not isinstance(environment, dict):
        raise ValueError("parallel_e2e_environment_metadata_missing")
    for key in ("version", "seedDataDigest", "dependencies"):
        value = environment.get(key)
        if (key == "dependencies" and not isinstance(value, dict)) or (key != "dependencies" and not isinstance(value, str)) or not value:
            raise ValueError(f"parallel_e2e_environment_metadata_invalid:{key}")
    states = e2e.get("stageStates") if isinstance(e2e, dict) and isinstance(e2e.get("stageStates"), dict) else {}
    if not isinstance(states.get("e2e_test"), dict) or states["e2e_test"].get("status") != "passed":
        complete_stage(workspace, feature, run_id, "V-E2E", "e2e_test", metadata=metadata)
    result = gate_batch(workspace, feature, run_id, "V-E2E")
    cleanup_errors: list[str] = []
    if result.get("success") and isinstance(worktrees, dict):
        for ref, raw_path in worktrees.items():
            binding = manifest.get("repositories", {}).get(ref)
            if not isinstance(binding, dict) or not isinstance(binding.get("gitRoot"), str):
                cleanup_errors.append(f"{ref}:repository_missing")
                continue
            cleanup_errors.extend(f"{ref}:{error}" for error in _remove_candidate(Path(binding["gitRoot"]), Path(str(raw_path)), ""))
        with run_lock(workspace, feature, run_id):
            updated = load_manifest(workspace, feature, run_id)
            updated["validationBatches"]["V-E2E"]["cleanup"] = {"at": utc_now(), "errors": cleanup_errors}
            save_manifest(workspace, feature, run_id, updated)
    return {"success": bool(result.get("success")), "result": result, "environment": environment, "cleanupErrors": cleanup_errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operate staged parallel Batch Merge Trains")
    sub = parser.add_subparsers(dest="command", required=True)
    promote_parser: argparse.ArgumentParser | None = None
    for name in ("build-candidate", "verify-candidate", "promote-candidate"):
        item = sub.add_parser(name)
        item.add_argument("--workspace")
        item.add_argument("--feature", required=True)
        item.add_argument("--run-id", required=True)
        item.add_argument("--repository-ref", required=True)
        item.add_argument("--wave", type=int, required=True)
        if name == "build-candidate":
            item.add_argument("--batch-id", action="append", dest="batch_ids", required=True)
        elif name == "promote-candidate":
            promote_parser = item
    e2e_begin = sub.add_parser("begin-e2e")
    e2e_finish = sub.add_parser("finish-e2e")
    for item in (e2e_begin, e2e_finish):
        item.add_argument("--workspace")
        item.add_argument("--feature", required=True)
        item.add_argument("--run-id", required=True)
    e2e_finish.add_argument("--passed", choices=("true", "false"), required=True)
    e2e_finish.add_argument("--metadata-json", default="{}")
    assert promote_parser is not None
    promote_parser.add_argument("--allow-stale", action="store_true")
    args = parser.parse_args(argv)
    try:
        workspace, feature = resolve_workspace(args.workspace), resolve_feature(args.feature)
        if args.command == "build-candidate":
            result = build_candidate(workspace, feature, args.run_id, wave=args.wave, batch_ids=args.batch_ids)
            if result.get("repositoryRef") != args.repository_ref:
                raise ValueError(f"parallel_merge_train_repository_mismatch:{args.repository_ref}:{result.get('repositoryRef')}")
        elif args.command == "verify-candidate":
            result = verify_candidate(workspace, feature, args.run_id, wave=args.wave, repository_ref=args.repository_ref)
        elif args.command == "promote-candidate":
            result = promote_candidate(workspace, feature, args.run_id, wave=args.wave, repository_ref=args.repository_ref)
        elif args.command == "begin-e2e":
            result = begin_e2e(workspace, feature, args.run_id)
        else:
            metadata = json.loads(args.metadata_json)
            if not isinstance(metadata, dict):
                raise ValueError("parallel_e2e_metadata_must_be_object")
            result = finish_e2e(workspace, feature, args.run_id, passed=args.passed == "true", metadata=metadata)
        print(json.dumps({"ok": bool(result.get("success")), **result}, ensure_ascii=False, indent=2))
        return 0 if result.get("success") or (args.command == "promote-candidate" and args.allow_stale and result.get("stale")) else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
