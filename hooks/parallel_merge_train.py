#!/usr/bin/env python3
"""Build and promote immutable parallel Batch merge candidates.

Delivery review and UTest are completed in each Batch worktree before a
candidate is built.  The candidate is therefore an integration boundary, not
an additional test stage; the only post-merge executable validation is V-E2E.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.conflict_types import CandidateStatus, ConflictContext
from hooks.conflict_resolution_agent import ModelBasedResolver
from hooks.commit_message import build_commit_message, normalize_task_card_id
from hooks.evidence_kernel import FileLock
from hooks.json_writer_common import atomic_write_json, resolve_feature, resolve_workspace
from hooks.parallel_batch_stage import complete_stage, gate_batch, reset_validation_batch, start_stage, record_e2e_failure
from hooks.parallel_runtime import append_event, load_manifest, mergeable_batches, run_dir, run_lock, save_manifest, utc_now
from hooks.parallel_integration import integration_repo, assert_integration, isolated, record_integration_head
from hooks.plan_writer import mark_parallel_batch_tasks_merged
from hooks.repository_snapshot import git_status_porcelain
from hooks.merge_resolution_context import prepare_context
from hooks.merge_resolution import (
    PLATFORM_EXCLUDE, business_status, candidate_binding, git as resolution_git,
    git_output, has_conflict_markers, merge_parents, outside_diff_digest, resolution_files,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value).strip("-.") or "default"


def _extract_conflicted_files(git_output: str) -> list[str]:
    """Extract list of conflicted files from Git merge output."""
    import re
    pattern = r'CONFLICT \(.*?\): Merge conflict in (.+)'
    matches = re.findall(pattern, git_output)
    return [m.strip() for m in matches]


def _extract_conflict_markers(worktree_path: Path, conflicted_files: list[str]) -> dict[str, str]:
    """Read conflict markers from conflicted files."""
    markers: dict[str, str] = {}
    for file_path in conflicted_files:
        full_path = worktree_path / file_path
        try:
            if full_path.exists() and full_path.is_file():
                content = full_path.read_text(encoding="utf-8", errors="replace")
                if "<<<<<<<" in content:  # Has conflict markers
                    markers[file_path] = content
        except Exception:
            # Skip files that can't be read
            pass
    return markers


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


def _conflict_context_from_record(
    record: dict[str, Any], repository_ref: str, wave: int
) -> tuple[ConflictContext | None, str | None]:
    """Safely restore the JSON conflict context kept on a merge-train record.

    The context is persisted so a later CLI invocation can resolve a conflict,
    but it must remain bound to the selected merge-train record. Reject a
    malformed or mismatched context before handing its worktree path to a
    resolver.
    """
    raw = record.get("conflictContext")
    if not isinstance(raw, dict):
        return None, "no_conflict_context_in_record"

    required_strings = ("baseSha", "candidateWorktree", "repositoryRef")
    for field in required_strings:
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            return None, f"invalid_conflict_context:{field}"

    batch_ids = raw.get("batchIds")
    if not isinstance(batch_ids, list) or not batch_ids or not all(isinstance(item, str) and item for item in batch_ids):
        return None, "invalid_conflict_context:batchIds"
    conflicted_files = raw.get("conflictedFiles")
    if not isinstance(conflicted_files, list) or not conflicted_files or not all(
        isinstance(item, str) and item for item in conflicted_files
    ):
        return None, "invalid_conflict_context:conflictedFiles"
    conflict_markers = raw.get("conflictMarkers", {})
    if not isinstance(conflict_markers, dict) or not all(
        isinstance(path, str) and isinstance(content, str) for path, content in conflict_markers.items()
    ):
        return None, "invalid_conflict_context:conflictMarkers"

    context_wave = raw.get("wave")
    attempts = raw.get("attempts", 0)
    if not isinstance(context_wave, int) or isinstance(context_wave, bool) or context_wave != wave:
        return None, "invalid_conflict_context:wave"
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
        return None, "invalid_conflict_context:attempts"
    if raw["repositoryRef"] != repository_ref:
        return None, "invalid_conflict_context:repositoryRef"

    record_worktree = record.get("worktreePath")
    if not isinstance(record_worktree, str) or raw["candidateWorktree"] != record_worktree:
        return None, "invalid_conflict_context:candidateWorktree"
    error_message = raw.get("errorMessage", "")
    if not isinstance(error_message, str):
        return None, "invalid_conflict_context:errorMessage"

    return ConflictContext(
        base_sha=raw["baseSha"],
        batch_ids=batch_ids,
        conflicted_files=conflicted_files,
        candidate_worktree=raw["candidateWorktree"],
        conflict_markers=conflict_markers,
        repository_ref=raw["repositoryRef"],
        wave=context_wave,
        attempts=attempts,
        error_message=error_message,
        task_card_id=str(raw.get("taskCardId") or ""),
    ), None


def _candidate_paths(workspace: Path, feature: str, run_id: str, repository_ref: str, wave: int) -> tuple[Path, str]:
    path = run_dir(workspace, feature, run_id) / "merge-trains" / _safe(repository_ref) / f"wave-{wave:03d}"
    branch = f"autodev-candidate/{_safe(feature)}/{_safe(run_id)}/{_safe(repository_ref)}/wave-{wave:03d}"
    return path, branch


def _is_interrupted_worktree_initialization(result: subprocess.CompletedProcess[str]) -> bool:
    output = (result.stderr or result.stdout or "").lower()
    return (
        "cannot remove a locked working tree" in output
        and "lock reason: initializing" in output
    )


def _remove_candidate(
    repo: Path,
    path: Path,
    branch: str,
    *,
    recover_interrupted_initialization: bool = False,
) -> list[str]:
    errors: list[str] = []
    if path.exists():
        removed = _git(repo, "worktree", "remove", "--force", str(path))
        if (
            removed.returncode != 0
            and recover_interrupted_initialization
            and _is_interrupted_worktree_initialization(removed)
        ):
            # A candidate build is serialized by FileLock. Once this caller
            # holds that lock, this Git-specific `initializing` marker can only
            # be residue from an interrupted earlier build, not another plugin
            # candidate construction in progress. Git explicitly requires a
            # second force flag to remove this incomplete worktree.
            removed = _git(repo, "worktree", "remove", "--force", "--force", str(path))
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


def _integration_train_lock(workspace: Path, feature: str, run_id: str, repository_ref: str):
    manifest = load_manifest(workspace, feature, run_id)
    if not isolated(manifest):
        return nullcontext()
    binding = manifest.get("repositories", {}).get(repository_ref, {})
    canonical = str(binding.get("integrationRepositoryRef") or repository_ref)
    return FileLock(run_dir(workspace, feature, run_id) / "integration-locks" / f"{_safe(canonical)}.lock")


def build_candidate(workspace: Path, feature: str, run_id: str, *, wave: int, batch_ids: list[str]) -> dict[str, Any]:
    manifest = load_manifest(workspace, feature, run_id)
    first = manifest.get("batches", {}).get(next(iter(batch_ids), ""), {})
    ref = str(first.get("repositoryRef") or first.get("workspaceRef") or "")
    with _integration_train_lock(workspace, feature, run_id, ref):
        return _build_candidate(workspace, feature, run_id, wave=wave, batch_ids=batch_ids)


def _build_candidate(
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
        task_card_id = normalize_task_card_id(manifest.get("taskCardId"))
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
        if existing and (existing.get("resolution") or {}).get("status") in {"running", "committing", "commit_failed"}:
            raise ValueError("candidate_resolution_in_progress")
        commits = {batch_id: str(batches[batch_id].get("commitSha") or "") for batch_id in ids}
        if existing and existing.get("batchIds") == ids and existing.get("deliveryCommits") == commits and existing.get("status") in {"built", "verified"}:
            return {"success": True, "reused": True, **existing}
        # Allow rebuilding if previously conflicted or failed (not just stale/failed/promoted)
        if existing and existing.get("status") not in {"stale", "failed", "promoted", "discarded", "candidate_conflicted", "needs_resolution"}:
            raise ValueError(f"parallel_merge_train_wave_occupied:{repository_ref}:{wave}")
        repo = assert_integration(binding) if isolated(manifest) else integration_repo(binding)
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
            cleanup_errors = _remove_candidate(
                repo,
                path,
                branch,
                recover_interrupted_initialization=True,
            )
            if cleanup_errors:
                raise ValueError("parallel_merge_train_stale_candidate_cleanup_failed:" + ";".join(cleanup_errors))
        created = _git(repo, "worktree", "add", "-b", branch, str(path), current_head)
        if created.returncode != 0:
            raise ValueError("parallel_merge_train_create_failed:" + (created.stderr.strip() or created.stdout.strip()))
        merged: list[dict[str, str]] = []
        failure: str | None = None
        conflicted_files: list[str] = []
        conflict_markers: dict[str, str] = {}
        try:
            for batch_id in ids:
                source = str(manifest["batches"][batch_id].get("branchName") or "")
                if not source:
                    raise ValueError(f"parallel_merge_train_source_branch_missing:{batch_id}")
                commit_message = build_commit_message(
                    task_card_id,
                    f"合并候选 {repository_ref} wave-{wave:03d} {batch_id}",
                )
                result = _git(path, "merge", "--no-ff", "-m", commit_message, source)
                if result.returncode != 0:
                    # Detect if it's a conflict or other error
                    unmerged = _git(path, "diff", "--name-only", "--diff-filter=U", "-z")
                    conflict_detected = bool(unmerged.stdout) or "CONFLICT" in result.stdout or "CONFLICT" in result.stderr
                    if conflict_detected:
                        # Extract conflicted files
                        conflicted_files = [name for name in unmerged.stdout.split("\0") if name] if unmerged.returncode == 0 else []
                        if not conflicted_files:
                            conflicted_files = _extract_conflicted_files(result.stdout + result.stderr)
                        # Read conflict markers from files
                        conflict_markers = _extract_conflict_markers(path, conflicted_files)
                        # Don't abort merge - preserve worktree for resolution
                        failure = f"parallel_merge_train_conflict:{batch_id}"
                    else:
                        # Non-conflict error, abort merge
                        _git(path, "merge", "--abort")
                        raise ValueError(f"parallel_merge_train_merge_error:{batch_id}:{result.stderr.strip() or result.stdout.strip()}")
                    break  # Stop merging on conflict
                merged.append({"batchId": batch_id, "branchName": source, "deliveryCommitSha": commits[batch_id], "candidateCommitSha": _head(path)})
        except ValueError as exc:
            if not failure:  # Only set if not already set by conflict detection
                failure = str(exc)

        candidate_sha = _head(path) if failure is None else None
        changed_files = _git(path, "diff", "--name-only", f"{current_head}..{candidate_sha}").stdout.splitlines() if candidate_sha else []

        if failure:
            # Check if it's a conflict (preserve worktree) or other failure (cleanup)
            is_conflict = conflicted_files and "conflict" in failure.lower()

            if is_conflict:
                # Preserve worktree for resolution
                conflict_context = {
                    "baseSha": current_head,
                    "batchIds": ids,
                    "conflictedFiles": conflicted_files,
                    "candidateWorktree": str(path),
                    "conflictMarkers": conflict_markers,
                    "repositoryRef": repository_ref,
                    "wave": wave,
                    "attempts": 0,
                    "errorMessage": failure,
                    "taskCardId": manifest.get("taskCardId"),
                }
                with run_lock(workspace, feature, run_id):
                    updated = load_manifest(workspace, feature, run_id)
                    updated.setdefault("mergeTrains", {})[_record_key(repository_ref, wave)] = {
                        "repositoryRef": repository_ref, "wave": wave, "batchIds": ids, "deliveryCommits": commits,
                        "baseSha": current_head, "branchName": branch, "worktreePath": str(path),
                        "status": CandidateStatus.CANDIDATE_CONFLICTED.value,
                        "error": failure, "conflictContext": conflict_context, "createdAt": utc_now(),
                    }
                    updated["status"] = "blocked"
                    save_manifest(workspace, feature, run_id, updated)
                append_event(workspace, feature, run_id, "merge_train_conflicted", repositoryRef=repository_ref, wave=wave, batchIds=ids, conflictedFiles=conflicted_files)
                return {
                    "success": False,
                    "repositoryRef": repository_ref,
                    "wave": wave,
                    "batchIds": ids,
                    "status": CandidateStatus.CANDIDATE_CONFLICTED.value,
                    "conflictContext": conflict_context,
                }
            else:
                # Other failure - cleanup worktree
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
        append_event(workspace, feature, run_id, "merge_train_built", repositoryRef=repository_ref, wave=wave, batchIds=ids, candidateSha=candidate_sha)
        return {"success": True, "reused": False, **record}


def verify_candidate(workspace: Path, feature: str, run_id: str, *, wave: int, repository_ref: str) -> dict[str, Any]:
    """Reject the retired candidate-test phase instead of silently running it."""
    del workspace, feature, run_id, wave, repository_ref
    raise ValueError("parallel_merge_train_candidate_verification_removed_use_batch_utest_and_e2e")


def promote_candidate(workspace: Path, feature: str, run_id: str, *, wave: int, repository_ref: str,
                      allow_unverified: bool = False) -> dict[str, Any]:
    with _integration_train_lock(workspace, feature, run_id, repository_ref):
        return _promote_candidate(workspace, feature, run_id, wave=wave, repository_ref=repository_ref,
                                  allow_unverified=allow_unverified)


def _promote_candidate(
    workspace: Path,
    feature: str,
    run_id: str,
    *,
    wave: int,
    repository_ref: str,
    allow_unverified: bool = False,
) -> dict[str, Any]:
    """Promote a candidate through a recoverable Git/Plan saga.

    Git and the Plan live in independent durable stores, so this operation
    cannot be a single filesystem transaction.  Persist an intent before the
    fast-forward and a Plan-recovery checkpoint immediately afterwards.  A
    later resume can then safely distinguish "not promoted" from "promoted
    but Plan not written" without guessing from a stale repository binding.
    """
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        record = _record(manifest, repository_ref, wave)
        allowed_statuses = {"verified"}
        if allow_unverified:
            allowed_statuses.add("built")
        if not record or record.get("status") not in allowed_statuses:
            raise ValueError(f"parallel_merge_train_candidate_not_verified:{repository_ref}:{wave}")
        utest_gated_promotion = record.get("status") == "built"
        binding = manifest.get("repositories", {}).get(repository_ref)
        if not isinstance(binding, dict) or not isinstance(binding.get("gitRoot"), str):
            raise ValueError(f"parallel_merge_train_repository_missing:{repository_ref}")
        repo = assert_integration(binding) if isolated(manifest) else integration_repo(binding)
        record.update({
            "status": "promoting",
            "promotionReadyStatus": "built" if utest_gated_promotion else "verified",
            "promotionIntentAt": utc_now(),
        })
        save_manifest(workspace, feature, run_id, manifest)

    _clean(repo)
    current_head = _head(repo)
    if current_head != record.get("baseSha"):
        with run_lock(workspace, feature, run_id):
            manifest = load_manifest(workspace, feature, run_id)
            current = _record(manifest, repository_ref, wave)
            if current:
                current.update({"status": "stale", "staleAt": utc_now(), "actualMainSha": current_head})
            save_manifest(workspace, feature, run_id, manifest)
        append_event(workspace, feature, run_id, "merge_train_stale", repositoryRef=repository_ref, wave=wave, candidateBaseSha=record.get("baseSha"), actualMainSha=current_head)
        return {"success": False, "stale": True, "action": "rebuild_candidate", "candidateSha": record.get("candidateSha"), "baseSha": record.get("baseSha"), "actualMainSha": current_head}
    promoted = _git(repo, "merge", "--ff-only", str(record.get("branchName") or ""))
    if promoted.returncode != 0:
        with run_lock(workspace, feature, run_id):
            manifest = load_manifest(workspace, feature, run_id)
            current = _record(manifest, repository_ref, wave)
            if current:
                current.update({"status": record.get("promotionReadyStatus", "verified"), "promotionError": promoted.stderr.strip() or promoted.stdout.strip()})
            save_manifest(workspace, feature, run_id, manifest)
        raise ValueError("parallel_merge_train_promote_failed:" + (promoted.stderr.strip() or promoted.stdout.strip()))
    promoted_sha = _head(repo)
    if promoted_sha != record.get("candidateSha"):
        raise ValueError("parallel_merge_train_promote_sha_mismatch")

    _checkpoint_promoted_plan_recovery(
        workspace,
        feature,
        run_id,
        repository_ref=repository_ref,
        wave=wave,
        promoted_sha=promoted_sha,
    )
    recovered = _recover_promoted_plan(workspace, feature, run_id, repository_ref=repository_ref, wave=wave)
    if utest_gated_promotion and recovered.get("success"):
        with run_lock(workspace, feature, run_id):
            manifest = load_manifest(workspace, feature, run_id)
            current = _record(manifest, repository_ref, wave)
            if current:
                current["validation"] = {
                    "skipped": True,
                    "reason": "batch_utest_gated_e2e_only",
                    "commands": [],
                }
                save_manifest(workspace, feature, run_id, manifest)
    return recovered


_PROMOTED_PLAN_RESOLUTION = "promoted_plan_state_update"


def _checkpoint_promoted_plan_recovery(
    workspace: Path,
    feature: str,
    run_id: str,
    *,
    repository_ref: str,
    wave: int,
    promoted_sha: str,
) -> None:
    """Persist the durable checkpoint immediately after Git promotion."""
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        current = _record(manifest, repository_ref, wave)
        binding = manifest.get("repositories", {}).get(repository_ref)
        if not current or not isinstance(binding, dict):
            raise ValueError(f"parallel_merge_train_recovery_record_missing:{repository_ref}:{wave}")
        if current.get("candidateSha") != promoted_sha:
            raise ValueError("parallel_merge_train_recovery_candidate_mismatch")
        resolution = {
            "kind": _PROMOTED_PLAN_RESOLUTION,
            "repositoryRef": repository_ref,
            "baseSha": current.get("baseSha"),
            "candidateSha": current.get("candidateSha"),
            "promotedSha": promoted_sha,
            "batchIds": list(current.get("batchIds") or []),
            "deliveryRunId": run_id,
            "planWriterErrors": [],
            "checkpointedAt": utc_now(),
        }
        # The repository's actual HEAD is now a durable fact even though Plan
        # metadata may still need replay.  Never leave it pointing at base.
        record_integration_head(manifest, repository_ref, promoted_sha)
        current.update({
            "status": "needs_resolution",
            "promotedSha": promoted_sha,
            "resolution": resolution,
        })
        manifest["status"] = "needs_resolution"
        save_manifest(workspace, feature, run_id, manifest)


def recover_promoted_plan(workspace: Path, feature: str, run_id: str, *, repository_ref: str, wave: int) -> dict[str, Any]:
    with _integration_train_lock(workspace, feature, run_id, repository_ref):
        # Another workflow may finish this promotion while resume waits for
        # the physical repository lock. Its completed result is reusable.
        manifest = load_manifest(workspace, feature, run_id)
        record = _record(manifest, repository_ref, wave)
        if isolated(manifest) and record and record.get("status") == "promoted":
            return {"success": True, "reused": True, "promoted": True,
                    "batchIds": record.get("batchIds", []), "candidateSha": record.get("candidateSha")}
        return _recover_promoted_plan(workspace, feature, run_id, repository_ref=repository_ref, wave=wave)


def _recover_promoted_plan(
    workspace: Path,
    feature: str,
    run_id: str,
    *,
    repository_ref: str,
    wave: int,
) -> dict[str, Any]:
    """Idempotently finish Plan state after an already-promoted candidate."""
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        record = _record(manifest, repository_ref, wave)
        binding = manifest.get("repositories", {}).get(repository_ref)
        if not isinstance(record, dict) or not isinstance(binding, dict) or not isinstance(binding.get("gitRoot"), str):
            return {"success": False, "error": f"parallel_merge_train_recovery_record_missing:{repository_ref}:{wave}"}
        candidate_sha = str(record.get("candidateSha") or "")
        base_sha = str(record.get("baseSha") or "")
        promoted_sha = str(record.get("promotedSha") or candidate_sha)
        resolution = record.get("resolution")
        is_legacy = record.get("status") == "needs_resolution" and (record.get("promotedSha") or record.get("planWriterErrors"))
        if record.get("status") not in {"promoting", "needs_resolution"} or (
            record.get("status") == "needs_resolution"
            and not (isinstance(resolution, dict) and resolution.get("kind") == _PROMOTED_PLAN_RESOLUTION)
            and not is_legacy
        ):
            return {"success": False, "error": "parallel_merge_train_not_waiting_for_plan_recovery"}
        repo = integration_repo(binding)

    try:
        _clean(repo)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    current_head = _head(repo)
    if not candidate_sha or current_head not in {base_sha, candidate_sha, promoted_sha}:
        return {
            "success": False,
            "error": "parallel_train_recovery_head_changed",
            "expected": promoted_sha or candidate_sha,
            "actual": current_head,
        }
    if current_head == base_sha and record.get("status") == "promoting":
        with run_lock(workspace, feature, run_id):
            manifest = load_manifest(workspace, feature, run_id)
            current = _record(manifest, repository_ref, wave)
            if current:
                current.update({"status": current.get("promotionReadyStatus", "verified"), "promotionRecoveredAt": utc_now()})
                current.pop("promotionIntentAt", None)
            manifest["status"] = "running"
            save_manifest(workspace, feature, run_id, manifest)
        return {"success": True, "retryPromotion": True, "candidateSha": candidate_sha}
    if current_head != candidate_sha or promoted_sha != candidate_sha:
        return {
            "success": False,
            "error": "parallel_train_recovery_candidate_mismatch",
            "candidateSha": candidate_sha,
            "promotedSha": promoted_sha,
            "actual": current_head,
        }

    # Repair pre-checkpoint crashes and historical records which only carried
    # promotedSha/planWriterErrors.  The exact HEAD equality above is the
    # authority for this migration.
    _checkpoint_promoted_plan_recovery(
        workspace,
        feature,
        run_id,
        repository_ref=repository_ref,
        wave=wave,
        promoted_sha=promoted_sha,
    )
    with run_lock(workspace, feature, run_id):
        record = _record(load_manifest(workspace, feature, run_id), repository_ref, wave)
        assert isinstance(record, dict)
        batch_ids = [str(batch_id) for batch_id in record.get("batchIds") or []]

    plan_errors: list[str] = []
    for batch_id in batch_ids:
        result = mark_parallel_batch_tasks_merged(
            workspace,
            feature,
            batch_id,
            merge_commit_sha=promoted_sha,
            delivery_run_id=run_id,
        )
        if not result.ok:
            plan_errors.extend(str(item.get("reason") or item) for item in (result.errors or []))
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        current = _record(manifest, repository_ref, wave)
        binding = manifest.get("repositories", {}).get(repository_ref)
        if not isinstance(current, dict) or not isinstance(binding, dict):
            return {"success": False, "error": f"parallel_merge_train_recovery_record_missing:{repository_ref}:{wave}"}
        record_integration_head(manifest, repository_ref, promoted_sha)
        resolution = current.get("resolution") if isinstance(current.get("resolution"), dict) else {}
        if plan_errors:
            resolution.update({"kind": _PROMOTED_PLAN_RESOLUTION, "planWriterErrors": plan_errors, "lastAttemptAt": utc_now()})
            current.update({"status": "needs_resolution", "promotedSha": promoted_sha, "planWriterErrors": plan_errors, "resolution": resolution})
            manifest["status"] = "needs_resolution"
            save_manifest(workspace, feature, run_id, manifest)
            return {"success": False, "needsPlanRecovery": True, "errors": plan_errors, "candidateSha": promoted_sha}
        for batch_id in batch_ids:
            batch = manifest["batches"].get(batch_id)
            if isinstance(batch, dict):
                batch.update({"status": "merged", "mergeCommitSha": promoted_sha, "mergedAt": utc_now()})
        current.update({"status": "promoted", "promotedSha": promoted_sha, "promotedAt": utc_now()})
        current.pop("resolution", None)
        current.pop("planWriterErrors", None)
        manifest["status"] = "running"
        save_manifest(workspace, feature, run_id, manifest)

    worktree_path = record.get("worktreePath")
    cleanup_errors = (
        _remove_candidate(repo, Path(worktree_path), str(record.get("branchName") or ""))
        if isinstance(worktree_path, str) and worktree_path.strip()
        else ["candidate_worktree_missing"]
    )
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        current = _record(manifest, repository_ref, wave)
        if current:
            current["cleanup"] = {"at": utc_now(), "errors": cleanup_errors}
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "merge_train_promoted", repositoryRef=repository_ref, wave=wave, candidateSha=promoted_sha, batchIds=batch_ids, cleanupErrors=cleanup_errors)
    return {"success": True, "promoted": True, "candidateSha": promoted_sha, "batchIds": batch_ids, "cleanupErrors": cleanup_errors}


def begin_e2e(workspace: Path, feature: str, run_id: str) -> dict[str, Any]:
    """Open the single B-E2E validation Batch after all deliveries are merged."""
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        incomplete = [batch_id for batch_id, batch in manifest.get("batches", {}).items() if not isinstance(batch, dict) or batch.get("status") != "merged"]
        if incomplete:
            raise ValueError("parallel_e2e_delivery_incomplete:" + ",".join(sorted(incomplete)))
        heads = {ref: binding.get("headSha") for ref, binding in manifest.get("repositories", {}).items() if isinstance(binding, dict)}
        previous = manifest.get("validationBatches", {}).get("V-E2E", {})
        if (isinstance(previous, dict) and previous.get("status") == "running"
                and previous.get("candidateSha") == json.dumps(heads, sort_keys=True)
                and isinstance(previous.get("worktrees"), dict)):
            return {"success": True, "batchId": "V-E2E", "status": "running", "mainHeads": heads, "worktrees": previous["worktrees"], "reused": True}
        for ref, binding in manifest.get("repositories", {}).items():
            if not isinstance(binding, dict) or not isinstance(binding.get("gitRoot"), str) or not isinstance(heads.get(ref), str):
                raise ValueError(f"parallel_e2e_repository_invalid:{ref}")
    worktrees: dict[str, str] = {}
    try:
        for ref, binding in manifest["repositories"].items():
            repo = integration_repo(binding)
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
        return record_e2e_failure(workspace, feature, run_id, metadata=metadata)
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
            for issue in updated.get("deferredIssues", []):
                if isinstance(issue, dict) and issue.get("kind") == "e2e_failure" and issue.get("status") == "open":
                    issue.update({"status": "resolved", "resolvedAt": utc_now(),
                                  "resolvedBy": updated["validationBatches"]["V-E2E"]["stageStates"]["e2e_test"]["latestEvidenceId"]})
            save_manifest(workspace, feature, run_id, updated)
    return {"success": bool(result.get("success")), "e2eStatus": "passed", "result": result, "environment": environment, "cleanupErrors": cleanup_errors}


def prepare_resolution(workspace: Path, feature: str, run_id: str, wave: int, repository_ref: str, attempt_id: str = "") -> dict[str, Any]:
    """Give the model a durable, candidate-specific permission to edit conflicts."""
    lock = _candidate_paths(workspace, feature, run_id, repository_ref, wave)[0].parent / ".candidate.lock"
    with FileLock(lock), run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        record = _record(manifest, repository_ref, wave)
        if not record:
            return {"success": False, "error": "candidate_record_missing"}
        try:
            path = candidate_binding(manifest, record)
            if record.get("status") == "built" and record.get("resolutionMethod") == "model":
                if (git_output(path, "rev-parse", "HEAD") != record.get("candidateSha")
                        or business_status(path) or len(merge_parents(path)) != 1):
                    raise ValueError("candidate_resolution_git_state_changed")
                return {"success": True, **record, "reused": True}
            if record.get("status") not in {"candidate_conflicted", "needs_resolution"}:
                raise ValueError("candidate_not_in_conflicted_state")
            previous = record.get("resolution") or {}
            if previous.get("status") in {"committing", "commit_failed"}:
                # Commit may already have succeeded before manifest registration.
                # Let the plugin reconcile it; never ask the model to edit again.
                return {"success": True, "status": "ready_to_complete", "worktreePath": str(path)}
            context, error = _conflict_context_from_record(record, repository_ref, wave)
            if error:
                raise ValueError(error)
            assert context is not None
            config = (manifest.get("runtimeConfig") or {}).get("conflictResolution") or {}
            maximum = config.get("maxAttempts", 2)
            if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
                raise ValueError("candidate_resolution_max_attempts_invalid")
            parents = merge_parents(path)
            if len(parents) != 2:
                raise ValueError("candidate_merge_not_in_progress")
            unmerged = resolution_git(path, "diff", "--name-only", "--diff-filter=U", "-z")
            if unmerged.returncode != 0:
                raise ValueError("candidate_unmerged_check_failed")
            unmerged_files = [name for name in unmerged.stdout.split("\0") if name]
            files = resolution_files(path, previous.get("files") or sorted(set(context.conflicted_files + unmerged_files)))
            outside = outside_diff_digest(path, files)
            if previous and (previous.get("parents") != parents or previous.get("outsideDiffDigest") != outside):
                raise ValueError("candidate_resolution_git_state_changed")
            same_attempt = bool(attempt_id and previous.get("status") == "running" and previous.get("attemptId") == attempt_id)
            if not same_attempt and context.attempts >= maximum:
                if previous:
                    previous["status"] = "exhausted"
                    record["status"] = "needs_resolution"
                    save_manifest(workspace, feature, run_id, manifest)
                raise ValueError("max_resolution_attempts_exceeded")
            locations = prepare_context(workspace, feature, run_id, manifest, record, path, files, parents)
            record["resolutionContext"] = locations
            if attempt_id and previous.get("status") == "running" and previous.get("attemptId") == attempt_id:
                # Empty-response retries repeat the same preparation command.
                # They must not consume another model-edit attempt.
                save_manifest(workspace, feature, run_id, manifest)
                return {
                    "success": True, "status": "resolution_required", "worktreePath": str(path),
                    "conflictedFiles": files, "repositoryRef": repository_ref, "wave": wave,
                    "attempts": context.attempts, "reused": True, **locations,
                }
            message = build_commit_message(manifest.get("taskCardId"), f"解决冲突 {', '.join(context.batch_ids)}")
            context.attempts += 1
            record["conflictContext"]["attempts"] = context.attempts
            record["resolution"] = {
                "status": "running", "files": files, "parents": parents,
                "outsideDiffDigest": outside, "commitMessage": message,
                "attempt": context.attempts, "attemptId": attempt_id, "startedAt": utc_now(),
            }
            save_manifest(workspace, feature, run_id, manifest)
            return {
                "success": True, "status": "resolution_required", "worktreePath": str(path),
                "conflictedFiles": files, "repositoryRef": repository_ref, "wave": wave,
                "attempts": context.attempts, **locations,
            }
        except (OSError, ValueError) as exc:
            return {"success": False, "status": "needs_resolution", "error": str(exc), "worktreePath": record.get("worktreePath")}


def complete_resolution(workspace: Path, feature: str, run_id: str, wave: int, repository_ref: str) -> dict[str, Any]:
    """Stage model-resolved files and create the merge commit inside the plugin."""
    lock = _candidate_paths(workspace, feature, run_id, repository_ref, wave)[0].parent / ".candidate.lock"
    with FileLock(lock), run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        record = _record(manifest, repository_ref, wave)
        if not record:
            return {"success": False, "error": "candidate_record_missing"}
        try:
            path = candidate_binding(manifest, record)
            if record.get("status") == "built" and record.get("resolutionMethod") == "model":
                if (git_output(path, "rev-parse", "HEAD") != record.get("candidateSha")
                        or business_status(path) or len(merge_parents(path)) != 1):
                    raise ValueError("candidate_resolution_git_state_changed")
                return {"success": True, **record, "reused": True}
            resolution = record.get("resolution") or {}
            if record.get("status") not in {"candidate_conflicted", "needs_resolution"} or resolution.get("status") not in {"running", "committing", "commit_failed"}:
                raise ValueError("candidate_resolution_not_prepared")
            files = resolution_files(path, resolution.get("files"))
            parents = merge_parents(path)
            if len(parents) == 1:
                # Recover only the exact commit whose intent was saved before Git.
                actual_parents = git_output(path, "show", "-s", "--format=%P", "HEAD").split()
                tree = git_output(path, "rev-parse", "HEAD^{tree}")
                message = git_output(path, "show", "-s", "--format=%B", "HEAD")
                if (resolution.get("status") not in {"committing", "commit_failed"}
                        or actual_parents != resolution.get("parents")
                        or tree != resolution.get("treeSha") or message != resolution.get("commitMessage")):
                    raise ValueError("candidate_resolution_commit_mismatch")
            else:
                if parents != resolution.get("parents"):
                    raise ValueError("candidate_resolution_git_state_changed")
                if outside_diff_digest(path, files) != resolution.get("outsideDiffDigest"):
                    raise ValueError("candidate_resolution_out_of_scope_changes")
                untracked = resolution_git(path, "ls-files", "--others", "--exclude-standard", "-z", "--", ".", PLATFORM_EXCLUDE)
                if untracked.returncode != 0 or untracked.stdout:
                    raise ValueError("candidate_resolution_untracked_files")
                markers = [name for name in files if has_conflict_markers(path / name)]
                if markers:
                    raise ValueError("candidate_conflict_markers_remain:" + ",".join(markers))
                staged = resolution_git(path, "--literal-pathspecs", "add", "-A", "--", *files)
                if staged.returncode != 0:
                    raise ValueError("candidate_resolution_stage_failed:" + staged.stderr.strip())
                unmerged = git_output(path, "diff", "--name-only", "--diff-filter=U")
                if unmerged:
                    raise ValueError("candidate_unmerged_files:" + unmerged)
                if git_output(path, "diff", "--cached", "--name-only", "HEAD", "--", ".cmbdevclaw/"):
                    raise ValueError("candidate_resolution_platform_files_staged")
                resolution.update({"status": "committing", "treeSha": git_output(path, "write-tree"), "commitStartedAt": utc_now()})
                save_manifest(workspace, feature, run_id, manifest)
                committed = resolution_git(path, "commit", "--no-edit", "-m", resolution["commitMessage"])
                if committed.returncode != 0:
                    resolution.update({"status": "commit_failed", "error": committed.stderr.strip() or committed.stdout.strip()})
                    save_manifest(workspace, feature, run_id, manifest)
                    raise ValueError("candidate_resolution_commit_failed:" + resolution["error"])
            if business_status(path) or len(merge_parents(path)) != 1:
                raise ValueError("candidate_resolution_worktree_dirty")
            candidate_sha = git_output(path, "rev-parse", "HEAD")
            if (git_output(path, "show", "-s", "--format=%P", "HEAD").split() != resolution.get("parents")
                    or git_output(path, "rev-parse", "HEAD^{tree}") != resolution.get("treeSha")):
                raise ValueError("candidate_resolution_commit_mismatch")
            for delivery_sha in (record.get("deliveryCommits") or {}).values():
                if resolution_git(path, "merge-base", "--is-ancestor", delivery_sha, candidate_sha).returncode != 0:
                    raise ValueError("candidate_resolution_delivery_missing:" + delivery_sha)
            record.update({
                "status": "built", "candidateSha": candidate_sha, "resolvedAt": utc_now(),
                "resolutionMethod": "model", "resolutionAttempts": resolution["attempt"],
                "changedFiles": git_output(path, "diff", "--name-only", f"{record['baseSha']}..{candidate_sha}").splitlines(),
            })
            resolution.update({"status": "completed", "commitSha": candidate_sha})
            record.pop("conflictContext", None)
            manifest["status"] = "running"
            save_manifest(workspace, feature, run_id, manifest)
            append_event(workspace, feature, run_id, "merge_train_conflict_resolved", repositoryRef=repository_ref, wave=wave, method="model", candidateSha=candidate_sha)
            return {"success": True, **record}
        except (OSError, ValueError) as exc:
            return {"success": False, "status": "needs_resolution", "error": str(exc), "worktreePath": record.get("worktreePath")}


def resolve_candidate(workspace: Path, feature: str, run_id: str, wave: int, repository_ref: str) -> dict[str, Any]:
    """Attempt to resolve conflicts in a candidate_conflicted merge train candidate.

    Returns updated candidate record with resolution status.
    """
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        record = _record(manifest, repository_ref, wave)

        if not record:
            return {"success": False, "error": f"no_candidate_record:{repository_ref}:{wave}"}

        if record.get("status") != CandidateStatus.CANDIDATE_CONFLICTED.value:
            return {"success": False, "error": f"candidate_not_in_conflicted_state:{record.get('status')}"}

        conflict_ctx, context_error = _conflict_context_from_record(record, repository_ref, wave)
        if context_error:
            return {"success": False, "error": context_error}
        assert conflict_ctx is not None
        # The helper above guarantees the persisted shape; retain the raw map
        # only to update its attempts count when manual intervention is needed.
        conflict_context_data = record["conflictContext"]

        # Load runtime config for resolution settings
        runtime_config = manifest.get("runtimeConfig", {})
        conflict_resolution_config = runtime_config.get("conflictResolution", {})
        max_attempts = conflict_resolution_config.get("maxAttempts", 2)
        enable_auto_resolve = conflict_resolution_config.get("enableAutoResolve", False)

        if conflict_ctx.attempts >= max_attempts:
            return {
                "success": False,
                "status": CandidateStatus.NEEDS_RESOLUTION.value,
                "error": "max_resolution_attempts_exceeded",
                "attempts": conflict_ctx.attempts,
                "maxAttempts": max_attempts,
            }

        # Increment attempt counter
        conflict_ctx.attempts += 1

        # Attempt resolution based on configuration
        if enable_auto_resolve:
            resolver = ModelBasedResolver(
                max_attempts=max_attempts,
                enable_auto_commit=enable_auto_resolve,
            )
            resolution_result = resolver.resolve(conflict_ctx)

            if resolution_result.status == "resolved":
                # Resolution successful - update record to 'built' so it can be verified
                worktree_path = Path(conflict_ctx.candidate_worktree)
                candidate_sha = _head(worktree_path)

                updated_record = {
                    **record,
                    "status": CandidateStatus.BUILT.value,
                    "candidateSha": candidate_sha,
                    "resolvedAt": utc_now(),
                    "resolutionAttempts": conflict_ctx.attempts,
                    "resolutionMethod": resolution_result.strategy_used,
                }
                updated_record.pop("conflictContext", None)
                manifest["status"] = "running"
                manifest["mergeTrains"][_record_key(repository_ref, wave)] = updated_record
                save_manifest(workspace, feature, run_id, manifest)
                append_event(
                    workspace,
                    feature,
                    run_id,
                    "merge_train_conflict_resolved",
                    repositoryRef=repository_ref,
                    wave=wave,
                    method=resolution_result.strategy_used,
                )

                return {
                    "success": True,
                    "status": CandidateStatus.BUILT.value,
                    "candidateSha": candidate_sha,
                    "repositoryRef": repository_ref,
                    "wave": wave,
                    "resolutionMethod": resolution_result.strategy_used,
                    "attempts": conflict_ctx.attempts,
                }
            elif resolution_result.status == "manual_required":
                # Persist the terminal manual-resolution state.  A future
                # scheduler resume must not silently recreate this candidate
                # and discard the user's worktree edits.
                conflict_context_data["attempts"] = conflict_ctx.attempts
                record["conflictContext"] = conflict_context_data
                record["status"] = CandidateStatus.NEEDS_RESOLUTION.value
                manifest["mergeTrains"][_record_key(repository_ref, wave)] = record
                manifest["status"] = CandidateStatus.NEEDS_RESOLUTION.value
                save_manifest(workspace, feature, run_id, manifest)

                return {
                    "success": False,
                    "status": CandidateStatus.NEEDS_RESOLUTION.value,
                    "error": "auto_resolution_failed",
                    "reason": resolution_result.reason,
                    "attempts": conflict_ctx.attempts,
                    "conflictedFiles": conflict_ctx.conflicted_files,
                    "worktreePath": conflict_ctx.candidate_worktree,
                }
        else:
            # Auto-resolve disabled, persist a manual-resolution state.
            conflict_context_data["attempts"] = conflict_ctx.attempts
            record["conflictContext"] = conflict_context_data
            record["status"] = CandidateStatus.NEEDS_RESOLUTION.value
            manifest["mergeTrains"][_record_key(repository_ref, wave)] = record
            manifest["status"] = CandidateStatus.NEEDS_RESOLUTION.value
            save_manifest(workspace, feature, run_id, manifest)
            return {
                "success": False,
                "status": CandidateStatus.NEEDS_RESOLUTION.value,
                "error": "auto_resolution_disabled",
                "attempts": conflict_ctx.attempts,
                "conflictedFiles": conflict_ctx.conflicted_files,
                "worktreePath": conflict_ctx.candidate_worktree,
            }


def resume_candidate(workspace: Path, feature: str, run_id: str, wave: int, repository_ref: str) -> dict[str, Any]:
    """Mark a manually committed candidate as ready for normal verification.

    This command deliberately accepts only a clean worktree with no unmerged
    entries and no in-progress merge. Besides conflict-resolution states it
    can explicitly recover a failed candidate whose worktree still exists
    (for example, after a human fixes a failed validation). It never creates a
    candidate or rewrites the user's resolution commit.
    """
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        record = _record(manifest, repository_ref, wave)
        if not record:
            return {"success": False, "error": f"no_candidate_record:{repository_ref}:{wave}"}
        if record.get("status") not in {
            CandidateStatus.CANDIDATE_CONFLICTED.value,
            CandidateStatus.NEEDS_RESOLUTION.value,
            CandidateStatus.FAILED.value,
        }:
            return {"success": False, "error": f"candidate_not_resumable:{record.get('status')}"}

        path = Path(str(record.get("worktreePath") or ""))
        if not path.is_dir():
            return {"success": False, "error": "candidate_worktree_missing"}
        unresolved = _git(path, "diff", "--name-only", "--diff-filter=U")
        if unresolved.returncode != 0:
            return {"success": False, "error": "candidate_unmerged_check_failed"}
        if unresolved.stdout.strip():
            return {"success": False, "error": "candidate_unmerged_files:" + unresolved.stdout.strip()}
        merge_head = _git(path, "rev-parse", "-q", "--verify", "MERGE_HEAD")
        if merge_head.returncode == 0:
            return {"success": False, "error": "candidate_merge_commit_required"}
        status = git_status_porcelain(path)
        if status.returncode != 0:
            return {"success": False, "error": "candidate_status_unavailable"}
        if status.stdout.strip():
            return {"success": False, "error": "candidate_worktree_dirty"}

        candidate_sha = _head(path)
        record.update({
            "status": CandidateStatus.BUILT.value,
            "candidateSha": candidate_sha,
            "resolvedAt": utc_now(),
            "resolutionMethod": "manual",
        })
        manifest.setdefault("mergeTrains", {})[_record_key(repository_ref, wave)] = record
        manifest["status"] = "running"
        save_manifest(workspace, feature, run_id, manifest)
        append_event(
            workspace,
            feature,
            run_id,
            "merge_train_candidate_resumed",
            repositoryRef=repository_ref,
            wave=wave,
            candidateSha=candidate_sha,
        )
        return {
            "success": True,
            "status": CandidateStatus.BUILT.value,
            "repositoryRef": repository_ref,
            "wave": wave,
            "candidateSha": candidate_sha,
            "resolutionMethod": "manual",
        }


def discard_candidate(workspace: Path, feature: str, run_id: str, wave: int, repository_ref: str) -> dict[str, Any]:
    """Discard a conflicted candidate and clean up its worktree.

    Allows rebuilding from scratch.
    """
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        record = _record(manifest, repository_ref, wave)

        if not record:
            return {"success": False, "error": f"no_candidate_record:{repository_ref}:{wave}"}

        status = record.get("status")
        if status not in {CandidateStatus.CANDIDATE_CONFLICTED.value, CandidateStatus.NEEDS_RESOLUTION.value, "failed"}:
            return {"success": False, "error": f"cannot_discard_status:{status}"}

        binding = manifest.get("repositories", {}).get(repository_ref)
        if not isinstance(binding, dict) or not isinstance(binding.get("gitRoot"), str):
            return {"success": False, "error": f"repository_not_found:{repository_ref}"}

        repo = integration_repo(binding)
        worktree_path = Path(record.get("worktreePath", ""))
        branch_name = record.get("branchName", "")

        cleanup_errors = []
        if worktree_path.exists() or branch_name:
            cleanup_errors = _remove_candidate(repo, worktree_path, branch_name)

        # Mark as discarded
        record["status"] = CandidateStatus.DISCARDED.value
        record["discardedAt"] = utc_now()
        record["cleanupErrors"] = cleanup_errors
        manifest["mergeTrains"][_record_key(repository_ref, wave)] = record

        # Unblock manifest if this was the only blocker
        if manifest.get("status") in {"blocked", CandidateStatus.NEEDS_RESOLUTION.value}:
            # Check if any other merge trains are still blocked
            other_blockers = any(
                mt.get("status") in {CandidateStatus.CANDIDATE_CONFLICTED.value, CandidateStatus.NEEDS_RESOLUTION.value, "failed"}
                for key, mt in manifest.get("mergeTrains", {}).items()
                if key != _record_key(repository_ref, wave)
            )
            if not other_blockers:
                manifest["status"] = "running"

        save_manifest(workspace, feature, run_id, manifest)
        append_event(workspace, feature, run_id, "merge_train_candidate_discarded",
                   repositoryRef=repository_ref, wave=wave)

        return {
            "success": True,
            "status": "discarded",
            "repositoryRef": repository_ref,
            "wave": wave,
            "cleanupErrors": cleanup_errors,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operate staged parallel Batch Merge Trains")
    sub = parser.add_subparsers(dest="command", required=True)
    promote_parser: argparse.ArgumentParser | None = None
    for name in ("build-candidate", "verify-candidate", "promote-candidate", "recover-promoted-plan", "prepare-resolution", "complete-resolution", "resolve-candidate", "resume-candidate", "discard-candidate"):
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
        elif name == "prepare-resolution":
            item.add_argument("--attempt-id", default="", help="stable identity for one Workflow resolution step")
    e2e_begin = sub.add_parser("begin-e2e")
    e2e_finish = sub.add_parser("finish-e2e")
    for item in (e2e_begin, e2e_finish):
        item.add_argument("--workspace")
        item.add_argument("--feature", required=True)
        item.add_argument("--run-id", required=True)
    e2e_finish.add_argument("--passed", choices=("true", "false"), required=True)
    e2e_metadata = e2e_finish.add_mutually_exclusive_group()
    e2e_metadata.add_argument("--metadata-json", default="{}")
    e2e_metadata.add_argument("--metadata-base64")
    assert promote_parser is not None
    promote_parser.add_argument("--allow-stale", action="store_true")
    promote_parser.add_argument("--allow-unverified", action="store_true")
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
            result = promote_candidate(
                workspace,
                feature,
                args.run_id,
                wave=args.wave,
                repository_ref=args.repository_ref,
                allow_unverified=args.allow_unverified,
            )
        elif args.command == "recover-promoted-plan":
            result = recover_promoted_plan(
                workspace,
                feature,
                args.run_id,
                wave=args.wave,
                repository_ref=args.repository_ref,
            )
        elif args.command == "resolve-candidate":
            result = resolve_candidate(workspace, feature, args.run_id, wave=args.wave, repository_ref=args.repository_ref)
        elif args.command == "prepare-resolution":
            result = prepare_resolution(workspace, feature, args.run_id, wave=args.wave, repository_ref=args.repository_ref, attempt_id=args.attempt_id)
        elif args.command == "complete-resolution":
            result = complete_resolution(workspace, feature, args.run_id, wave=args.wave, repository_ref=args.repository_ref)
        elif args.command == "resume-candidate":
            result = resume_candidate(workspace, feature, args.run_id, wave=args.wave, repository_ref=args.repository_ref)
        elif args.command == "discard-candidate":
            result = discard_candidate(workspace, feature, args.run_id, wave=args.wave, repository_ref=args.repository_ref)
        elif args.command == "begin-e2e":
            result = begin_e2e(workspace, feature, args.run_id)
        else:
            metadata = json.loads(base64.b64decode(args.metadata_base64, validate=True).decode("utf-8")
                                  if args.metadata_base64 else args.metadata_json)
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
