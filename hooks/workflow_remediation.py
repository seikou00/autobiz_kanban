#!/usr/bin/env python3
"""Controlled Phase-2 worktree remediation with exclusive worktree lock and 3-state WAL."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_kernel import FileLock  # noqa: E402
from hooks.json_writer_common import atomic_write_json, feature_dir, resolve_feature, resolve_workspace  # noqa: E402
from hooks.parallel_runtime import (  # noqa: E402
    advance_dispatch_generation,
    append_event,
    load_manifest,
    run_dir,
    run_lock,
    save_manifest,
    utc_now,
    validate_cli_dispatch,
    worktree_lock,
)
from hooks.plan_json import load_plan_bundle  # noqa: E402
from hooks.repository_snapshot import RUNTIME_DIRECTORIES, current_git_branch  # noqa: E402

ABSENT = "ABSENT"
RUNTIME_EXCLUDES = tuple(f":(exclude){directory}" for directory in RUNTIME_DIRECTORIES)
GIT_FORBIDDEN_LOCK_NAMES = (
    "index.lock",
    "HEAD.lock",
    "locked",
    "MERGE_HEAD",
    "REBASE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
)
UNCONFIRMED_TASK_EXECUTION = {
    "remediation_compile_completion_unconfirmed",
    "remediation_previous_task_execution_unconfirmed",
}

# Deterministic fault-injection hook used by crash-recovery tests between Step 1 (index) and Step 2 (worktree).
_AFTER_INDEX_RESTORE_HOOK: Callable[[str], None] | None = None


def _git_bytes(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        check=False,
    )


def _git_text(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _fsync_file(path: Path) -> None:
    with open(path, "rb") as handle:
        os.fsync(handle.fileno())


def _worktree_git_dir(worktree: Path) -> Path:
    res = _git_text(worktree, "rev-parse", "--git-dir")
    raw = res.stdout.strip()
    if res.returncode != 0 or not raw:
        raise ValueError("remediation_git_dir_unavailable")
    git_dir = Path(raw)
    return git_dir.resolve() if git_dir.is_absolute() else (worktree / git_dir).resolve()


@contextmanager
def _remediation_write_lock(workspace: Path, feature: str, run_id: str, batch_id: str):
    from hooks.parallel_delivery import _execution_lock

    with worktree_lock(workspace, feature, run_id, batch_id, timeout=5):
        with _execution_lock(workspace, feature, run_id, batch_id):
            yield


def _assert_no_live_writers_or_git_locks(workspace: Path, feature: str, run_id: str, batch_id: str, worktree: Path) -> None:
    feat_root = feature_dir(workspace, feature)
    task_lock_path = feat_root / ".task-runs" / ".lock"
    if task_lock_path.parent.exists():
        try:
            with FileLock(task_lock_path, timeout=0):
                pass
        except TimeoutError as exc:
            raise ValueError("remediation_task_runner_lock_busy") from exc

    batch_runs = []
    for run_file in (feat_root / ".task-runs").glob("*/*.json"):
        try:
            state = json.loads(run_file.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(state, dict) and state.get("parallelRunId") == run_id and state.get("batchId") == batch_id:
            batch_runs.append(state)
    active_runs: list[str] = []
    completed_by_task: dict[str, str] = {}
    for state in batch_runs:
        if any(attempt.get("status") == "prepared" for attempt in state.get("compileAttempts", [])):
            raise ValueError("remediation_compile_completion_unconfirmed")
        status = state.get("status")
        if status in {"started", "in_progress", "implementation_recording"}:
            active_runs.append(f"{state.get('taskId')}:{state.get('runId')}")
        if status in {"implemented", "done"} and state.get("implementationEvidenceId"):
            task = state.get("taskId")
            completed_by_task[task] = max(completed_by_task.get(task, ""), str(state.get("updatedAt") or ""))
    if active_runs:
        raise ValueError("remediation_active_task_run_exists:" + ",".join(sorted(active_runs)))
    for state in batch_runs:
        status = state.get("status")
        if status not in {"aborted", "failed"}:
            continue
        if status == "aborted" and state.get("planStatusReset") is True:
            continue
        ended = state.get("updatedAt") or state.get("startedAt")
        if ended and completed_by_task.get(state.get("taskId"), "") > str(ended):
            continue
        raise ValueError("remediation_previous_task_execution_unconfirmed")

    git_dir = _worktree_git_dir(worktree)
    for name in GIT_FORBIDDEN_LOCK_NAMES:
        if (git_dir / name).exists():
            raise ValueError(f"remediation_git_lock_or_operation_present:{name}")


def _safe_rel_path(worktree: Path, rel_path: str) -> str:
    norm = rel_path.replace("\\", "/") if os.name == "nt" else rel_path
    if not norm or Path(norm).anchor or "\0" in norm or set(norm.split("/")) & {"", ".", ".."}:
        raise ValueError(f"remediation_invalid_relative_path:{rel_path}")
    candidate = worktree / norm
    resolved_parent = candidate.parent.resolve(strict=False)
    wt_resolved = worktree.resolve()
    if not resolved_parent.is_relative_to(wt_resolved) or resolved_parent != candidate.parent:
        raise ValueError(f"remediation_path_escapes_worktree:{rel_path}")
    return norm


def _index_blob(worktree: Path, rel_path: str) -> str:
    res = _git_text(worktree, "--literal-pathspecs", "ls-files", "--stage", "-z", "--", rel_path)
    if res.returncode != 0:
        raise ValueError(f"remediation_ls_files_failed:{rel_path}")
    records = [item for item in res.stdout.split("\0") if item]
    if not records:
        return ABSENT
    if len(records) > 1:
        return "UNMERGED"
    meta, _, _ = records[0].partition("\t")
    parts = meta.split()
    if len(parts) < 3 or parts[2] != "0":
        return "UNMERGED"
    return f"{parts[0]}:{parts[1]}"


def _base_blob_and_sha256(worktree: Path, start_sha: str, rel_path: str) -> tuple[str, str]:
    res = _git_text(worktree, "--literal-pathspecs", "ls-tree", "-z", start_sha, "--", rel_path)
    if res.returncode != 0:
        raise ValueError(f"remediation_ls_tree_failed:{rel_path}")
    records = [item for item in res.stdout.split("\0") if item]
    if not records:
        return ABSENT, ABSENT
    meta, _, _ = records[0].partition("\t")
    parts = meta.split()
    if len(parts) < 3:
        raise ValueError(f"remediation_ls_tree_unexpected:{rel_path}")
    mode, _, blob_sha = parts[0], parts[1], parts[2]
    cat = _git_bytes(worktree, "cat-file", "blob", blob_sha)
    if cat.returncode != 0:
        raise ValueError(f"remediation_cat_blob_failed:{rel_path}:{blob_sha}")
    digest = hashlib.sha256(cat.stdout).hexdigest()
    wt_sha = f"SYMLINK:{digest}" if mode == "120000" else digest
    return f"{mode}:{blob_sha}", wt_sha


def _worktree_sha256(worktree: Path, rel_path: str) -> str:
    full = worktree / rel_path
    if full.is_symlink():
        raw_target = os.fsencode(os.readlink(full))
        return "SYMLINK:" + hashlib.sha256(raw_target).hexdigest()
    if not full.exists():
        return ABSENT
    if not full.is_file():
        raise ValueError(f"remediation_unsupported_entry_type:{rel_path}")
    return hashlib.sha256(full.read_bytes()).hexdigest()


def _dirty_worktree_paths(worktree: Path, start_sha: str) -> list[str]:
    found: set[str] = set()
    diff_res = _git_text(worktree, "diff", "--name-only", "-z", start_sha, "--", ".", *RUNTIME_EXCLUDES)
    if diff_res.returncode != 0:
        raise ValueError(f"remediation_git_diff_failed:{diff_res.stderr.strip()}")
    for entry in diff_res.stdout.split("\0"):
        if entry:
            found.add(entry)

    status_res = _git_text(
        worktree, "status", "--porcelain", "-z", "--untracked-files=all", "--", ".", *RUNTIME_EXCLUDES
    )
    if status_res.returncode != 0:
        raise ValueError(f"remediation_git_status_failed:{status_res.stderr.strip()}")
    tokens = status_res.stdout.split("\0")
    idx = 0
    while idx < len(tokens):
        record = tokens[idx]
        idx += 1
        if len(record) <= 3:
            continue
        status_xy = record[:2]
        path_str = record[3:]
        if path_str:
            found.add(path_str)
        if ("R" in status_xy or "C" in status_xy) and idx < len(tokens):
            orig = tokens[idx]
            idx += 1
            if orig:
                found.add(orig)
    return sorted(
        p for p in found
        if not any(p == d or p.startswith(d + "/") for d in RUNTIME_DIRECTORIES)
    )


def _claimed_paths_for_batch(workspace: Path, feature: str, batch_id: str, task_ids: list[str]) -> set[str]:
    from hooks.task_runner import _changed_files, _claimed_batch_change_paths, _historical_task_file_changes

    feat_root = feature_dir(workspace, feature)
    plan = load_plan_bundle(feat_root)
    claimed = set(_claimed_batch_change_paths(feat_root, plan, batch_id, excluding_task_id=""))
    for tid in task_ids:
        claimed.update(_changed_files(_historical_task_file_changes(feat_root, str(tid), "")))
    return {p for p in claimed if p}


def _compute_snapshot(
    workspace: Path,
    feature: str,
    batch_id: str,
    batch: dict[str, Any],
    worktree: Path,
    start_sha: str,
) -> dict[str, Any]:
    task_ids = [str(t) for t in (batch.get("taskIds") or [])]
    claimed_set = _claimed_paths_for_batch(workspace, feature, batch_id, task_ids)
    dirty_list = _dirty_worktree_paths(worktree, start_sha)
    unattributed = sorted(set(dirty_list) - claimed_set)
    claimed_sorted = sorted(claimed_set)

    claimed_states: dict[str, dict[str, str]] = {}
    for rel in claimed_sorted:
        norm = _safe_rel_path(worktree, rel)
        claimed_states[norm] = {
            "indexBlob": _index_blob(worktree, norm),
            "worktreeSha256": _worktree_sha256(worktree, norm),
        }

    unattributed_states: dict[str, dict[str, str]] = {}
    for rel in unattributed:
        norm = _safe_rel_path(worktree, rel)
        base_blob, base_wt_sha = _base_blob_and_sha256(worktree, start_sha, norm)
        unattributed_states[norm] = {
            "indexBlob": _index_blob(worktree, norm),
            "worktreeSha256": _worktree_sha256(worktree, norm),
            "baseBlob": base_blob,
            "baseWorktreeSha256": base_wt_sha,
        }

    canonical = json.dumps(
        {
            "startSha": start_sha,
            "claimed": claimed_states,
            "unattributed": unattributed_states,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return {
        "startSha": start_sha,
        "claimedFiles": claimed_sorted,
        "claimedStates": claimed_states,
        "unattributedFiles": unattributed,
        "unattributedStates": unattributed_states,
        "snapshotDigest": digest,
    }


def _restore_index_step(worktree: Path, start_sha: str, rel_path: str, base_blob: str) -> None:
    if base_blob == ABSENT:
        res = _git_text(worktree, "--literal-pathspecs", "rm", "-f", "--cached", "--ignore-unmatch", "--", rel_path)
    else:
        res = _git_text(worktree, "--literal-pathspecs", "reset", "-q", start_sha, "--", rel_path)
    if res.returncode != 0:
        raise ValueError(f"remediation_index_restore_failed:{rel_path}:{res.stderr.strip()}")


def _restore_worktree_step(worktree: Path, start_sha: str, rel_path: str, base_blob: str) -> None:
    target = worktree / rel_path
    if base_blob == ABSENT:
        if target.is_symlink() or target.exists():
            target.unlink()
        parent = target.parent
        wt_resolved = worktree.resolve()
        while parent != wt_resolved and parent.is_relative_to(wt_resolved):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    else:
        res = _git_text(worktree, "--literal-pathspecs", "checkout", start_sha, "--", rel_path)
        if res.returncode != 0:
            raise ValueError(f"remediation_worktree_restore_failed:{rel_path}:{res.stderr.strip()}")


def _execute_or_replay_journal(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    worktree: Path,
    journal_path: Path,
) -> dict[str, Any]:
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal.get("status") == "completed":
        return journal
    start_sha = str(journal["startSha"])
    transitions: list[dict[str, str]] = list(journal.get("transitions") or [])

    if (journal.get("runId"), journal.get("batchId")) != (run_id, batch_id):
        raise ValueError("remediation_journal_identity_mismatch")
    archive = Path(journal["archiveDir"])
    patch_digest = hashlib.sha256((archive / "staged.patch").read_bytes()).hexdigest()
    if journal.get("stagedPatchSha256", patch_digest) != patch_digest:
        raise ValueError("remediation_archive_staged_patch_mismatch")
    for entry in journal.get("entries") or []:
        expected = entry["preWorktreeSha256"]
        if expected == ABSENT:
            continue
        is_link = expected.startswith("SYMLINK:")
        legacy_path = str(entry["path"]) + (".symlink" if is_link else "")
        archived_file = archive / "worktree" / entry.get("archiveFile", legacy_path)
        archived_hash = hashlib.sha256(archived_file.read_bytes()).hexdigest()
        if expected != ("SYMLINK:" + archived_hash if is_link else archived_hash):
            raise ValueError(f"remediation_archive_content_mismatch:{entry['path']}")

    for rel, expected in (journal.get("claimedBaseline") or {}).items():
        norm = _safe_rel_path(worktree, rel)
        if expected != {"indexBlob": _index_blob(worktree, norm), "worktreeSha256": _worktree_sha256(worktree, norm)}:
            raise ValueError(f"remediation_claimed_files_modified:{norm}")

    for entry in journal.get("entries") or []:
        rel = _safe_rel_path(worktree, str(entry["path"]))
        pre = (entry["preIndexBlob"], entry["preWorktreeSha256"])
        s1 = (entry["baseBlob"], entry["preWorktreeSha256"])
        s2 = (entry["baseBlob"], entry["baseWorktreeSha256"])
        cur = (_index_blob(worktree, rel), _worktree_sha256(worktree, rel))

        if cur == s2:
            transitions.append({"path": rel, "fromState": "S2_restored"})
            continue
        if cur not in {pre, s1}:
            with run_lock(workspace, feature, run_id):
                manifest = load_manifest(workspace, feature, run_id)
                batch = manifest["batches"][batch_id]
                from hooks.parallel_batch_scheduler import _escalate_batch_locked
                _escalate_batch_locked(
                    workspace, feature, run_id, manifest, batch_id, batch,
                    error=f"remediation_journal_corrupted:{rel}",
                    reason_code="remediation_journal_corrupted",
                    category="remediation_corrupted",
                    classification="precondition",
                )
                save_manifest(workspace, feature, run_id, manifest)
            journal["status"] = "corrupted"
            journal["corruptedPath"] = rel
            atomic_write_json(journal_path, journal)
            raise ValueError(f"remediation_journal_corrupted:{rel}")

        if cur[0] != entry["baseBlob"]:
            state_label = "S0_pending"
            _restore_index_step(worktree, start_sha, rel, entry["baseBlob"])
            if _AFTER_INDEX_RESTORE_HOOK is not None:
                _AFTER_INDEX_RESTORE_HOOK(rel)
        else:
            state_label = "S1_index_restored_worktree_pending"

        _restore_worktree_step(worktree, start_sha, rel, entry["baseBlob"])
        after = (_index_blob(worktree, rel), _worktree_sha256(worktree, rel))
        if after != s2:
            raise ValueError(f"remediation_post_restore_mismatch:{rel}")
        transitions.append({"path": rel, "fromState": state_label})

    for rel, expected in (journal.get("claimedBaseline") or {}).items():
        norm = _safe_rel_path(worktree, rel)
        actual = {
            "indexBlob": _index_blob(worktree, norm),
            "worktreeSha256": _worktree_sha256(worktree, norm),
        }
        if actual != expected:
            with run_lock(workspace, feature, run_id):
                manifest = load_manifest(workspace, feature, run_id)
                batch = manifest["batches"][batch_id]
                from hooks.parallel_batch_scheduler import _escalate_batch_locked
                _escalate_batch_locked(
                    workspace, feature, run_id, manifest, batch_id, batch,
                    error=f"remediation_claimed_files_modified:{norm}",
                    reason_code="remediation_claimed_files_modified",
                    category="remediation_corrupted",
                    classification="precondition",
                )
                save_manifest(workspace, feature, run_id, manifest)
            raise ValueError(f"remediation_claimed_files_modified:{norm}")

    journal["transitions"] = transitions
    journal["status"] = "completed"
    journal["completedAt"] = utc_now()
    atomic_write_json(journal_path, journal)
    return journal


def _replay_unfinished_journals(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    worktree: Path,
    start_sha: str,
) -> list[str]:
    replayed: list[str] = []
    archives_root = run_dir(workspace, feature, run_id) / "recovery-archives" / batch_id
    if not archives_root.is_dir():
        return replayed
    for journal_path in sorted(archives_root.glob("remediation-gen*.journal.json")):
        try:
            data = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") == "archived_pending_restore":
            if data.get("startSha") != start_sha:
                raise ValueError("remediation_journal_base_changed")
            _execute_or_replay_journal(workspace, feature, run_id, batch_id, worktree, journal_path)
            replayed.append(journal_path.name)
    return replayed


def _validate_remediation_dispatch(manifest: dict, batch: dict, batch_id: str, generation: int, token: str) -> None:
    if batch.get("status") != "remediation_pending" or (batch.get("dispatch") or {}).get("source") != "remediation":
        raise ValueError(f"remediation_dispatch_not_active:{batch_id}")
    if not validate_cli_dispatch(manifest, batch, batch_id, {"generation": generation, "token": token}, "remediation"):
        raise ValueError("stale_stage_owner")


def _load_authorized_remediation_context(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    generation: int,
    token: str,
) -> tuple[dict[str, Any], dict[str, Any], Path, str]:
    from hooks.parallel_batch_scheduler import assert_batch_worktree_isolated

    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        batch = (manifest.get("batches") or {}).get(batch_id)
        if not isinstance(batch, dict):
            raise ValueError(f"parallel_batch_not_found:{batch_id}")
        _validate_remediation_dispatch(manifest, batch, batch_id, generation, token)
        if batch.get("commitSha"):
            raise ValueError(f"remediation_batch_already_sealed:{batch_id}")
        raw_wt = batch.get("worktreePath")
        if not isinstance(raw_wt, str) or not raw_wt.strip():
            raise ValueError(f"remediation_worktree_missing:{batch_id}")
        worktree = Path(raw_wt).expanduser().resolve()
        assert_batch_worktree_isolated(manifest, batch_id, worktree)
        start_sha = str(batch.get("startSha") or "")
        if not start_sha:
            ref = batch.get("repositoryRef") or batch.get("workspaceRef")
            binding = (manifest.get("repositories") or {}).get(ref) or {}
            start_sha = str(binding.get("headSha") or binding.get("baseSha") or "")
        if not start_sha:
            raise ValueError(f"remediation_start_sha_missing:{batch_id}")
    if current_git_branch(worktree) != batch.get("branchName"):
        raise ValueError(f"remediation_branch_mismatch:{batch_id}")
    head = _git_text(worktree, "rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != start_sha:
        raise ValueError(f"remediation_head_mismatch:{batch_id}")
    return manifest, batch, worktree, start_sha


def _inspect_unattributed(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    *,
    generation: int,
    token: str,
) -> dict[str, Any]:
    with _remediation_write_lock(workspace, feature, run_id, batch_id):
        _, batch, worktree, start_sha = _load_authorized_remediation_context(
            workspace, feature, run_id, batch_id, generation, token
        )
        _assert_no_live_writers_or_git_locks(workspace, feature, run_id, batch_id, worktree)

        replayed = _replay_unfinished_journals(workspace, feature, run_id, batch_id, worktree, start_sha)
        snapshot = _compute_snapshot(workspace, feature, batch_id, batch, worktree, start_sha)

        with run_lock(workspace, feature, run_id):
            manifest = load_manifest(workspace, feature, run_id)
            cur_batch = manifest["batches"][batch_id]
            _validate_remediation_dispatch(manifest, cur_batch, batch_id, generation, token)
            recovery = cur_batch.setdefault("recovery", {})
            if "remediationBaselineCount" not in recovery or int(recovery.get("remediationAttempts", 0)) == 0 and not replayed:
                recovery["remediationBaselineCount"] = len(snapshot["unattributedFiles"])
            recovery["remediationClaimedBaseline"] = snapshot["claimedStates"]
            recovery["lastInspectedSnapshotDigest"] = snapshot["snapshotDigest"]
            save_manifest(workspace, feature, run_id, manifest)

        return {
            "success": True,
            "runId": run_id,
            "batchId": batch_id,
            "worktreePath": str(worktree),
            "startSha": start_sha,
            "claimedFiles": snapshot["claimedFiles"],
            "unattributedFiles": snapshot["unattributedFiles"],
            "snapshotDigest": snapshot["snapshotDigest"],
            "replayedJournals": replayed,
        }


def inspect_unattributed(
    workspace: Path, feature: str, run_id: str, batch_id: str, *, generation: int, token: str,
) -> dict[str, Any]:
    try:
        return _inspect_unattributed(workspace, feature, run_id, batch_id, generation=generation, token=token)
    except ValueError as exc:
        reason = str(exc)
        if reason not in UNCONFIRMED_TASK_EXECUTION:
            raise
        return escalate_remediation(
            workspace, feature, run_id, batch_id,
            generation=generation, token=token, reason=reason, reason_code=reason,
        )


def archive_and_restore_unattributed(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    *,
    generation: int,
    token: str,
    expected_snapshot_digest: str,
    paths: list[str],
) -> dict[str, Any]:
    if not paths:
        raise ValueError("remediation_paths_empty")

    with _remediation_write_lock(workspace, feature, run_id, batch_id):
        _, batch, worktree, start_sha = _load_authorized_remediation_context(
            workspace, feature, run_id, batch_id, generation, token
        )
        clean_paths = sorted({_safe_rel_path(worktree, p) for p in paths})
        _assert_no_live_writers_or_git_locks(workspace, feature, run_id, batch_id, worktree)

        _replay_unfinished_journals(workspace, feature, run_id, batch_id, worktree, start_sha)
        snapshot = _compute_snapshot(workspace, feature, batch_id, batch, worktree, start_sha)
        if snapshot["snapshotDigest"] != expected_snapshot_digest:
            raise ValueError("remediation_snapshot_changed")

        claimed_set = set(snapshot["claimedFiles"])
        unattributed_set = set(snapshot["unattributedFiles"])
        overlap = claimed_set.intersection(clean_paths)
        if overlap:
            raise ValueError("remediation_cannot_touch_claimed_files:" + ",".join(sorted(overlap)))
        outside = set(clean_paths) - unattributed_set
        if outside:
            raise ValueError("remediation_paths_not_unattributed:" + ",".join(sorted(outside)))

        archives_root = run_dir(workspace, feature, run_id) / "recovery-archives" / batch_id
        archive_dir = archives_root / f"gen{generation}-{expected_snapshot_digest[:12]}"
        wt_archive_dir = archive_dir / "worktree"
        wt_archive_dir.mkdir(parents=True, exist_ok=True)

        # Archive worktree contents and staged diff before writing WAL journal.
        for index, rel in enumerate(clean_paths):
            src = worktree / rel
            dst = wt_archive_dir / str(index)
            if src.is_symlink():
                dst.write_bytes(os.fsencode(os.readlink(src)))
                _fsync_file(dst)
            elif src.is_file():
                shutil.copy2(src, dst)
                _fsync_file(dst)

        staged_diff = _git_bytes(worktree, "--literal-pathspecs", "diff", "--cached", "--binary", start_sha, "--", *clean_paths)
        if staged_diff.returncode != 0:
            raise ValueError("remediation_staged_diff_archive_failed")
        staged_patch_path = archive_dir / "staged.patch"
        staged_patch_path.write_bytes(staged_diff.stdout)
        _fsync_file(staged_patch_path)

        entries = [
            {
                "path": rel,
                "archiveFile": str(index),
                "preIndexBlob": snapshot["unattributedStates"][rel]["indexBlob"],
                "preWorktreeSha256": snapshot["unattributedStates"][rel]["worktreeSha256"],
                "baseBlob": snapshot["unattributedStates"][rel]["baseBlob"],
                "baseWorktreeSha256": snapshot["unattributedStates"][rel]["baseWorktreeSha256"],
            }
            for index, rel in enumerate(clean_paths)
        ]
        journal_path = archives_root / f"remediation-gen{generation}.journal.json"
        journal = {
            "runId": run_id,
            "batchId": batch_id,
            "generation": generation,
            "startSha": start_sha,
            "snapshotDigest": expected_snapshot_digest,
            "archiveDir": str(archive_dir),
            "stagedPatchPath": str(staged_patch_path),
            "stagedPatchSha256": hashlib.sha256(staged_diff.stdout).hexdigest(),
            "status": "archived_pending_restore",
            "createdAt": utc_now(),
            "entries": entries,
            "claimedBaseline": snapshot["claimedStates"],
            "transitions": [],
        }
        atomic_write_json(journal_path, journal)
        _fsync_file(journal_path)

        completed_journal = _execute_or_replay_journal(workspace, feature, run_id, batch_id, worktree, journal_path)
        post = _compute_snapshot(workspace, feature, batch_id, batch, worktree, start_sha)

        append_event(
            workspace,
            feature,
            run_id,
            "batch_unattributed_changes_remediated",
            batchId=batch_id,
            generation=generation,
            restoredPaths=clean_paths,
            archiveDir=str(archive_dir),
        )
        return {
            "success": True,
            "runId": run_id,
            "batchId": batch_id,
            "restoredPaths": clean_paths,
            "archiveDir": str(archive_dir),
            "journalPath": str(journal_path),
            "transitions": completed_journal.get("transitions", []),
            "remainingUnattributedFiles": post["unattributedFiles"],
            "claimedFiles": post["claimedFiles"],
            "postSnapshotDigest": post["snapshotDigest"],
        }


def verify_remediation(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    *,
    generation: int,
    token: str,
) -> dict[str, Any]:
    """Runtime StageProgressValidator for Phase 2 unattributed-change remediation."""
    from hooks.parallel_batch_scheduler import _escalate_batch_locked, schedule

    with _remediation_write_lock(workspace, feature, run_id, batch_id):
        _, batch, worktree, start_sha = _load_authorized_remediation_context(
            workspace, feature, run_id, batch_id, generation, token
        )
        _assert_no_live_writers_or_git_locks(workspace, feature, run_id, batch_id, worktree)
        replayed = _replay_unfinished_journals(workspace, feature, run_id, batch_id, worktree, start_sha)
        snapshot = _compute_snapshot(workspace, feature, batch_id, batch, worktree, start_sha)

        with run_lock(workspace, feature, run_id):
            manifest = load_manifest(workspace, feature, run_id)
            cur_batch = manifest["batches"][batch_id]
            _validate_remediation_dispatch(manifest, cur_batch, batch_id, generation, token)
            recovery = dict(cur_batch.get("recovery") or {})
            claimed_baseline = recovery.get("remediationClaimedBaseline")
            remaining = snapshot["unattributedFiles"]
            before_count = int(recovery.get("remediationBaselineCount", len(remaining) or 1))
            attempts_done = int(recovery.get("remediationAttempts", 0))
            if isinstance(claimed_baseline, dict) and claimed_baseline != snapshot["claimedStates"]:
                reason_code = "remediation_claimed_files_modified"
                _escalate_batch_locked(
                    workspace, feature, run_id, manifest, batch_id, cur_batch,
                    error=reason_code,
                    reason_code=reason_code,
                    category="remediation_corrupted",
                    classification="precondition",
                )
                save_manifest(workspace, feature, run_id, manifest)
                outcome = "escalated"
            elif not remaining:
                recovery.update({
                    "remediationAttempts": attempts_done + 1,
                    "status": "remediation_resolved",
                    "kind": "implementation_resume" if snapshot["claimedFiles"] else "retry_dispatch",
                    "preserveWorktree": True,
                    "reprovision": False,
                })
                recovery.pop("pendingAttempt", None)
                recovery.pop("remediationBaselineCount", None)
                recovery.pop("remediationClaimedBaseline", None)
                cur_batch.update({
                    "status": "pending",
                    "error": None,
                    "activeStage": None,
                    "recovery": recovery,
                })
                if isinstance(cur_batch.get("dispatch"), dict):
                    cur_batch["dispatch"]["status"] = "completed"
                (run_dir(workspace, feature, run_id) / "dispatch" / f"{batch_id}.json").unlink(missing_ok=True)
                advance_dispatch_generation(cur_batch)
                save_manifest(workspace, feature, run_id, manifest)
                outcome = "resolved"
                reason_code = "unattributed_changes_archived_and_restored"
            elif len(remaining) < before_count and attempts_done == 0:
                recovery.update({
                    "remediationAttempts": 1,
                    "remediationBaselineCount": len(remaining),
                    "remediationClaimedBaseline": snapshot["claimedStates"],
                    "status": "remediation_required",
                })
                recovery.pop("pendingAttempt", None)
                cur_batch.update({
                    "status": "remediation_pending",
                    "recovery": recovery,
                })
                if isinstance(cur_batch.get("dispatch"), dict):
                    cur_batch["dispatch"]["status"] = "remediation_pending"
                (run_dir(workspace, feature, run_id) / "dispatch" / f"{batch_id}.json").unlink(missing_ok=True)
                advance_dispatch_generation(cur_batch)
                save_manifest(workspace, feature, run_id, manifest)
                outcome = "partial_progress"
                reason_code = "unattributed_changes_reduced"
            else:
                reason_code = "remediation_no_progress" if len(remaining) >= before_count else "remediation_budget_exhausted"
                _escalate_batch_locked(
                    workspace, feature, run_id, manifest, batch_id, cur_batch,
                    error=f"{reason_code}:{','.join(remaining)}",
                    reason_code=reason_code,
                    category="remediation_unresolvable",
                    classification="precondition",
                    recovery_overrides={"remediationAttempts": attempts_done + 1},
                )
                save_manifest(workspace, feature, run_id, manifest)
                outcome = "escalated"

    sched = schedule(workspace, feature, run_id, workflow_view=True)
    return {
        "success": True,
        "runId": run_id,
        "batchId": batch_id,
        "outcome": outcome,
        "reasonCode": reason_code,
        "remainingUnattributedFiles": remaining,
        "replayedJournals": replayed,
        "schedulerSnapshot": sched,
    }


def escalate_remediation(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    *,
    generation: int,
    token: str,
    reason: str,
    reason_code: str = "remediation_agent_escalated",
) -> dict[str, Any]:
    from hooks.parallel_batch_scheduler import _escalate_batch_locked, schedule

    with worktree_lock(workspace, feature, run_id, batch_id, timeout=5):
        with run_lock(workspace, feature, run_id):
            manifest = load_manifest(workspace, feature, run_id)
            batch = (manifest.get("batches") or {}).get(batch_id)
            if not isinstance(batch, dict):
                raise ValueError(f"parallel_batch_not_found:{batch_id}")
            _validate_remediation_dispatch(manifest, batch, batch_id, generation, token)
            _escalate_batch_locked(
                workspace, feature, run_id, manifest, batch_id, batch,
                error=reason or "remediation_agent_escalated",
                reason_code=reason_code,
                category="remediation_unresolvable",
                classification="precondition",
            )
            save_manifest(workspace, feature, run_id, manifest)
    sched = schedule(workspace, feature, run_id, workflow_view=True)
    return {
        "success": True,
        "runId": run_id,
        "batchId": batch_id,
        "outcome": "escalated",
        "reasonCode": reason_code,
        "schedulerSnapshot": sched,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect-unattributed", "archive-and-restore-unattributed", "verify-remediation", "escalate"):
        item = sub.add_parser(name)
        item.add_argument("--workspace", required=True)
        item.add_argument("--feature", required=True)
        item.add_argument("--run-id", required=True)
        item.add_argument("--batch-id", required=True)
        item.add_argument("--dispatch-generation", type=int, required=True)
        item.add_argument("--dispatch-token", required=True)
        if name == "archive-and-restore-unattributed":
            item.add_argument("--expected-snapshot-digest", required=True)
            paths = item.add_mutually_exclusive_group(required=True)
            paths.add_argument("--path", action="append", dest="paths")
            paths.add_argument("--paths-b64")
        if name == "escalate":
            item.add_argument("--reason", default="remediation_agent_escalated")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        if args.command == "inspect-unattributed":
            result = inspect_unattributed(
                workspace, feature, args.run_id, args.batch_id,
                generation=args.dispatch_generation, token=args.dispatch_token,
            )
        elif args.command == "archive-and-restore-unattributed":
            paths = args.paths
            if args.paths_b64:
                paths = json.loads(base64.b64decode(args.paths_b64, validate=True))
                if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
                    raise ValueError("remediation_paths_invalid")
            result = archive_and_restore_unattributed(
                workspace, feature, args.run_id, args.batch_id,
                generation=args.dispatch_generation, token=args.dispatch_token,
                expected_snapshot_digest=args.expected_snapshot_digest, paths=paths,
            )
        elif args.command == "verify-remediation":
            result = verify_remediation(
                workspace, feature, args.run_id, args.batch_id,
                generation=args.dispatch_generation, token=args.dispatch_token,
            )
        else:
            result = escalate_remediation(
                workspace, feature, args.run_id, args.batch_id,
                generation=args.dispatch_generation, token=args.dispatch_token,
                reason=args.reason,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
