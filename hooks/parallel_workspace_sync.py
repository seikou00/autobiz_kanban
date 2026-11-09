#!/usr/bin/env python3
"""Publish integrated batches as uncommitted files, preserving the user's index.

Three-way merges run in a disposable detached worktree. Backup refs and an
apply journal make a stopped publication resumable without resetting the source
branch, stashing its files, or requiring a clean user checkout.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_kernel import FileLock
from hooks.json_writer_common import atomic_write_json, resolve_feature, resolve_workspace
from hooks.parallel_runtime import global_worktrees_root, load_manifest, run_dir, run_lock, save_manifest, utc_now
from hooks.parallel_repository import coordinates


def _git(repo: Path, *args: str, env: dict | None = None, data: bytes | None = None, check: bool = True,
         hooks_path: Path | None = None, log_path: Path | None = None):
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", **(env or {})}
    options = ["-c", "core.hooksPath=" + str(hooks_path)] if hooks_path is not None else []
    result = subprocess.run(["git", *options, *args], cwd=repo, env=environment, input=data, capture_output=True)
    if log_path is not None:
        log_path.with_suffix(".stdout.log").write_bytes(result.stdout)
        log_path.with_suffix(".stderr.log").write_bytes(result.stderr)
    if check and result.returncode:
        raise ValueError("workspace_sync_git_failed:" + args[0] + ":" + result.stderr.decode("utf-8", "replace").strip())
    return result


def _value(repo: Path, *args: str, **kwargs) -> str:
    return _git(repo, *args, **kwargs).stdout.decode("utf-8").strip()


def _names(repo: Path, *args: str) -> list[str]:
    return [os.fsdecode(value) for value in _git(repo, *args).stdout.split(b"\0") if value]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _relative(value: str) -> str:
    path = PurePosixPath(value)
    if (path.is_absolute() or any(part.lower() in {"..", ".git", ".cmbdevclaw"} for part in path.parts)
            or "\\" in value or not path.parts):
        raise ValueError("workspace_sync_path_invalid:" + value)
    return value


def _file(path: Path) -> tuple[dict, bytes]:
    if path.is_symlink():
        data, kind, executable = os.fsencode(os.readlink(path)), "symlink", False
    elif path.is_file():
        data, kind = path.read_bytes(), "file"
        executable = bool(path.stat().st_mode & stat.S_IXUSR)
    elif path.exists():
        return {"kind": "directory"}, b""
    else:
        return {"kind": "missing"}, b""
    return {"kind": kind, "sha256": hashlib.sha256(data).hexdigest(), "executable": executable}, data


def _store(directory: Path, entry: dict, data: bytes) -> None:
    if "sha256" in entry:
        blobs = directory / "blobs"
        blobs.mkdir(exist_ok=True)
        path = blobs / entry["sha256"]
        if not path.exists():
            path.write_bytes(data)


def _source_file(source: Path, name: str) -> tuple[dict, bytes]:
    path = source / _relative(name)
    for parent in path.parents:
        if parent == source:
            break
        if parent.is_symlink():
            raise ValueError("workspace_sync_parent_symlink:" + name)
    return _file(path)


def _paths(workspace: Path, feature: str, run_id: str, ref: str):
    token = hashlib.sha256(ref.encode()).hexdigest()[:16]
    directory = run_dir(workspace, feature, run_id) / "workspace-sync" / token
    directory.mkdir(parents=True, exist_ok=True)
    return token, directory


def _summary(state: dict, ref: str) -> dict:
    keys = ("status", "attemptId", "sourcePath", "sourceBranch", "sourceHead", "lastSyncedSha", "targetSha",
            "worktreePath", "contextPath", "backupPath", "conflictedFiles", "error", "reasonCode")
    result = {"success": True, "repositoryRef": ref, **{key: state[key] for key in keys if key in state}}
    if state.get("status") == "synced":
        for key in ("worktreePath", "contextPath", "conflictedFiles"):
            result.pop(key, None)
    return result


def workspace_sync_pending(manifest: dict) -> bool:
    return any(item.get("status") != "skipped" and (item.get("status") != "synced"
               or item.get("lastSyncedSha") != manifest.get("repositories", {}).get(ref, {}).get("headSha"))
               for ref, item in manifest.get("workspaceSync", {}).items())


def request_sync_locked(manifest: dict, repository_ref: str) -> dict:
    """Register publication without Git/file work; caller owns the run lock."""
    if manifest.get("integrationMode") != "isolated":
        return {"success": True, "status": "skipped", "repositoryRef": repository_ref}
    ref = manifest["repositories"][repository_ref].get("integrationRepositoryRef", repository_ref)
    from hooks.parallel_workspace_queue import reconcile_locked, summary_locked
    reconcile_locked(manifest, ref)
    return summary_locked(manifest, ref).copy()


def resolution_write_error(workspace: Path, manifest: dict, record: dict, target: Path) -> str | None:
    """Authorize only current, Runtime-owned temporary conflict files."""
    try:
        if manifest.get("status") in {"cancelled", "rolled_back", "cleaned"}:
            return "workspace_sync_run_inactive"
        ref, attempt = record["repositoryRef"], record["attemptId"]
        token = hashlib.sha256(ref.encode()).hexdigest()[:16]
        directory = run_dir(workspace, manifest["featureId"], manifest["runId"]) / "workspace-sync" / token
        state = _read(directory / "state.json")
        if (record.get("status") != "conflicted" or state.get("status") != "conflicted"
                or state.get("attemptId") != attempt or not state.get("resolverId")):
            return "workspace_sync_resolution_not_running"
        candidate = Path(state["worktreePath"])
        expected = global_worktrees_root(workspace) / manifest["runId"] / "workspace-sync" / attempt
        if candidate.is_symlink() or candidate.resolve() != expected.resolve():
            return "workspace_sync_resolution_binding_changed"
        relative = target.resolve().relative_to(candidate.resolve()).as_posix()
        if target.is_symlink() or relative not in state["conflictedFiles"]:
            return "workspace_sync_resolution_file_not_authorized"
        source = Path(manifest["repositories"][ref]["gitRoot"])
        common = lambda root: (root / _value(root, "rev-parse", "--git-common-dir")).resolve()
        if (common(candidate) != common(source) or candidate.resolve() == source.resolve()
                or _value(candidate, "rev-parse", "HEAD") != state["snapshotSha"]
                or _value(candidate, "rev-parse", "MERGE_HEAD") != state["targetSha"]):
            return "workspace_sync_resolution_git_state_changed"
    except (OSError, ValueError, KeyError) as exc:
        return "workspace_sync_resolution_invalid:" + str(exc)
    return None


def _persist(workspace: Path, feature: str, run_id: str, ref: str, path: Path, state: dict) -> dict:
    result = _summary(state, ref)
    covered_ids = set()
    covering_job = None
    if state.get("status") == "synced":
        # Legacy sync/complete callers also acknowledge durable publication.
        # Do Git ancestry checks before taking the run lock. A concurrent newer
        # merge cannot be covered by this older snapshot.
        jobs = load_manifest(workspace, feature, run_id).get("workspaceSyncQueue", {}).get(ref, [])
        covering_job = next((job["jobId"] for job in jobs if job["targetSha"] == state["lastSyncedSha"]), None)
        for job in jobs:
            if job["status"] not in {"synced", "covered"} and _git(Path(state["sourcePath"]), "merge-base", "--is-ancestor",
                    job["targetSha"], state["lastSyncedSha"], check=False).returncode == 0:
                covered_ids.add(job["jobId"])
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if manifest.get("status") in {"cancelled", "rolled_back", "cleaned"}:
            raise ValueError("workspace_sync_run_inactive")
        atomic_write_json(path, state)
        if state.get("backupPath"):
            atomic_write_json(Path(state["backupPath"]) / "journal.json", state)
        previous = manifest.setdefault("workspaceSync", {}).get(ref, {})
        manifest["workspaceSync"][ref] = {**previous, **result}
        # Per-attempt success is distinct from repository-wide freshness.
        head = manifest["repositories"][ref]["headSha"]
        manifest["workspaceSync"][ref]["desiredSha"] = head
        if state.get("status") == "synced":
            for job in manifest.get("workspaceSyncQueue", {}).get(ref, []):
                if job["jobId"] in covered_ids:
                    job.update(status="synced" if job["targetSha"] == state["lastSyncedSha"] else "covered",
                               completedAt=utc_now())
                    if job["status"] == "covered":
                        job["coveredBy"] = covering_job
                    job.pop("owner", None)
            from hooks.parallel_workspace_queue import summary_locked
            summary_locked(manifest, ref)
        save_manifest(workspace, feature, run_id, manifest)
    return result


def _identity(source: Path, binding: dict) -> tuple[str, str]:
    branch = _value(source, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch != binding["baseBranch"]:
        raise ValueError("workspace_sync_source_branch_changed")
    if _git(source, "ls-files", "--unmerged", "-z").stdout:
        raise ValueError("workspace_sync_source_merge_in_progress")
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "rebase-merge", "rebase-apply"):
        path = Path(_value(source, "rev-parse", "--git-path", name))
        if not path.is_absolute():
            path = source / path
        if path.exists():
            raise ValueError("workspace_sync_source_merge_in_progress")
    return branch, _value(source, "rev-parse", "HEAD")


def _outside(candidate: Path, conflicts: list[str]) -> str:
    specs = [".", *(f":(exclude,literal){name}" for name in conflicts)]
    tracked = _git(candidate, "diff", "--binary", "HEAD", "--", *specs).stdout
    staged = _git(candidate, "diff", "--cached", "--binary", "HEAD", "--", *specs).stdout
    others = _git(candidate, "ls-files", "--others", "--exclude-standard", "-z", "--", *specs).stdout
    return hashlib.sha256(tracked + b"\0" + staged + b"\0" + others).hexdigest()


def _prepare(workspace: Path, feature: str, run_id: str, ref: str, binding: dict,
             directory: Path, previous: dict, resolver_id: str | None) -> dict:
    source = Path(binding["gitRoot"]).resolve()
    branch, head = _identity(source, binding)
    base, target = previous.get("lastSyncedSha") or binding["baseSha"], binding["headSha"]
    if _git(source, "merge-base", "--is-ancestor", base, target, check=False).returncode:
        raise ValueError("workspace_sync_integration_history_changed")
    attempt = uuid.uuid4().hex
    backup = directory / attempt
    backup.mkdir()
    candidate = global_worktrees_root(workspace) / run_id / "workspace-sync" / attempt
    state = {"status": "preparing", "attemptId": attempt, "sourcePath": str(source), "sourceBranch": branch,
             "sourceHead": head, "lastSyncedSha": previous.get("lastSyncedSha"), "baseSha": base,
             "targetSha": target, "backupPath": str(backup), "worktreePath": str(candidate),
             "resolverId": resolver_id, "createdAt": utc_now()}
    state_path = directory / "state.json"
    _persist(workspace, feature, run_id, ref, state_path, state)
    index_path = Path(_value(source, "rev-parse", "--git-path", "index"))
    if not index_path.is_absolute():
        index_path = source / index_path
    if index_path.exists():
        shutil.copy2(index_path, backup / "user.index")
    dirty = set(_names(source, "diff", "--name-only", "--no-renames", "-z", "HEAD", "--", ".", ":(exclude).cmbdevclaw/**"))
    dirty.update(_names(source, "diff", "--cached", "--name-only", "--no-renames", "-z", "--", ".", ":(exclude).cmbdevclaw/**"))
    dirty.update(_names(source, "ls-files", "--others", "--exclude-standard", "-z", "--", ".", ":(exclude).cmbdevclaw/**"))
    incoming = set(_names(source, "diff", "--name-only", "--no-renames", "-z", base, target))
    scope = dirty | incoming
    originals = {}
    for name in sorted(scope):
        _relative(name)
        originals[name], data = _source_file(source, name)
        _store(backup, originals[name], data)
    state["originals"] = originals
    atomic_write_json(backup / "local-files.json", {name: originals[name] for name in sorted(dirty)})
    _persist(workspace, feature, run_id, ref, state_path, state)
    environment = {"GIT_INDEX_FILE": str(backup / "snapshot.index")}
    _git(source, "read-tree", head, env=environment)
    _git(source, "add", "-A", "--", ".", ":(exclude).cmbdevclaw/**", env=environment)
    # An incoming tracked path can collide with a locally ignored file. Include
    # just those collisions in the user side instead of silently replacing them
    # or recursively snapshotting ignored dependency/build directories.
    for name in sorted(incoming):
        if originals[name]["kind"] in {"file", "symlink"}:
            _git(source, "add", "-f", "--", f":(literal){name}", env=environment)
    tree = _value(source, "write-tree", env=environment)
    if _identity(source, binding) != (branch, head) or any(_source_file(source, name)[0] != value for name, value in originals.items()):
        raise ValueError("workspace_sync_source_changed_during_snapshot")
    # These commits are backup objects on private refs, never source-branch
    # commits. They also pin staged-only blobs which may differ from the files.
    author = {"GIT_AUTHOR_NAME": "Autobiz Backup", "GIT_AUTHOR_EMAIL": "autobiz@localhost",
              "GIT_COMMITTER_NAME": "Autobiz Backup", "GIT_COMMITTER_EMAIL": "autobiz@localhost"}
    snapshot = _value(source, "commit-tree", tree, "-p", base, data="工作区同步文件备份\n".encode(), env=author)
    backup_ref = f"refs/autobiz-workspace-backups/{run_id}/{attempt}"
    _git(source, "update-ref", backup_ref + "/files", snapshot)
    if (backup / "user.index").exists():
        index_tree = _value(source, "write-tree", env={"GIT_INDEX_FILE": str(backup / "user.index")})
        index_commit = _value(source, "commit-tree", index_tree, "-p", snapshot, data="工作区暂存区备份\n".encode(), env=author)
        _git(source, "update-ref", backup_ref + "/index", index_commit)
    state.update(snapshotSha=snapshot, snapshotTree=tree, backupRef=backup_ref)
    _persist(workspace, feature, run_id, ref, state_path, state)
    return _finish_preparation(workspace, feature, run_id, ref, state_path, state)


def _finish_preparation(workspace: Path, feature: str, run_id: str, ref: str, state_path: Path, state: dict) -> dict:
    source, candidate, backup = (Path(state[key]) for key in ("sourcePath", "worktreePath", "backupPath"))
    expected = global_worktrees_root(workspace) / run_id / "workspace-sync" / state["attemptId"]
    if (candidate.is_symlink() or candidate.resolve() != expected.resolve()
            or backup.is_symlink() or backup.resolve() != (state_path.parent / state["attemptId"]).resolve()):
        raise ValueError("workspace_sync_preparation_binding_changed")
    hooks = backup / "empty-hooks"
    hooks.mkdir(exist_ok=True)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    merge_head = None
    if candidate.exists():
        common = lambda root: (root / _value(root, "rev-parse", "--git-common-dir")).resolve()
        if (_value(candidate, "rev-parse", "HEAD") != state["snapshotSha"]
                or common(candidate) != common(source)
                or Path(_value(candidate, "rev-parse", "--show-toplevel")).resolve() != candidate.resolve()
                or not _git(candidate, "symbolic-ref", "--quiet", "HEAD", check=False).returncode):
            raise ValueError("workspace_sync_preparation_worktree_changed")
        merge_head = _git(candidate, "rev-parse", "--verify", "MERGE_HEAD", check=False)
        if not merge_head.returncode and merge_head.stdout.decode("ascii").strip() != state["targetSha"]:
            raise ValueError("workspace_sync_preparation_merge_changed")
        if merge_head.returncode and (_value(candidate, "write-tree") != state["snapshotTree"]
                or _git(candidate, "diff", "--quiet", "HEAD", check=False).returncode
                or _git(candidate, "ls-files", "--others", "--exclude-standard", "-z").stdout):
            # A stopped checkout is disposable. No resolver can write while
            # preparing; source files and their backups remain untouched.
            _git(source, "worktree", "remove", "--force", str(candidate), hooks_path=hooks)
    if not candidate.exists():
        _git(source, "worktree", "add", "--detach", str(candidate), state["snapshotSha"],
             hooks_path=hooks, log_path=backup / "worktree-add")
    merged = (subprocess.CompletedProcess([], 0, b"", b"") if merge_head is not None and not merge_head.returncode
              else _git(candidate, "merge", "--no-commit", "--no-ff", state["targetSha"], check=False,
                        hooks_path=hooks, log_path=backup / "merge"))
    conflicts = sorted(set(_names(candidate, "diff", "--name-only", "--diff-filter=U", "-z")))
    if conflicts:
        for name in conflicts:
            _relative(name)
        state.update(status="conflicted", conflictedFiles=conflicts, outsideDigest=_outside(candidate, conflicts))
        files = []
        for index, name in enumerate(conflicts):
            snapshots = {}
            for stage, role in ((1, "base"), (2, "user"), (3, "batch")):
                result = _git(candidate, "show", f":{stage}:{name}", check=False)
                if not result.returncode:
                    path = backup / f"conflict-{index}-{role}"
                    path.write_bytes(result.stdout)
                    snapshots[role] = str(path)
            files.append({"path": name, **snapshots})
        context = backup / "conflicts.json"
        atomic_write_json(context, {"runId": run_id, "repositoryRef": ref, "attemptId": state["attemptId"],
                          "worktreePath": str(candidate), "sourcePath": str(source), "baseSha": state["baseSha"],
                          "targetSha": state["targetSha"], "sideMeaning": {"user": "用户当前保存的内容，含暂存、未暂存和未跟踪文件",
                          "batch": "最新已集成的 Batch 成果"}, "files": files})
        state["contextPath"] = str(context)
    elif merged.returncode:
        raise ValueError("workspace_sync_merge_failed:" + merged.stderr.decode("utf-8", "replace").strip())
    else:
        state.update(status="merged", mergedTree=_value(candidate, "write-tree"))
    _persist(workspace, feature, run_id, ref, state_path, state)
    return state


def _plan(state: dict) -> None:
    candidate, backup = Path(state["worktreePath"]), Path(state["backupPath"])
    changes = _names(candidate, "diff", "--name-only", "--no-renames", "-z", state["snapshotTree"], state["mergedTree"])
    plan = []
    for name in changes:
        _relative(name)
        if name not in state["originals"]:
            raise ValueError("workspace_sync_unexpected_merge_path:" + name)
        after, data = _file(candidate / name)
        if after["kind"] == "directory" or state["originals"][name]["kind"] == "directory":
            raise ValueError("workspace_sync_directory_transition_requires_resolution:" + name)
        _store(backup, after, data)
        plan.append({"path": name, "before": state["originals"][name], "after": after})
    state.update(status="applying", changes=plan)


def _write_file(source: Path, backup: Path, change: dict) -> None:
    path, after = source / change["path"], change["after"]
    for parent in path.parents:
        if parent == source:
            break
        if parent.is_symlink():
            raise ValueError("workspace_sync_parent_symlink:" + change["path"])
    if after["kind"] == "missing":
        if path.exists() or path.is_symlink():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (backup / "blobs" / after["sha256"]).read_bytes()
    temporary = path.parent / (".autobiz-sync-" + uuid.uuid4().hex)
    try:
        if after["kind"] == "symlink":
            temporary.symlink_to(os.fsdecode(data))
        else:
            temporary.write_bytes(data)
            mode = stat.S_IMODE(path.stat().st_mode) if path.is_file() and not path.is_symlink() else 0o644
            os.chmod(temporary, mode | 0o111 if after["executable"] else mode & ~0o111)
        os.replace(temporary, path)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def _cleanup(workspace: Path, run_id: str, state: dict) -> None:
    expected = global_worktrees_root(workspace) / run_id / "workspace-sync" / state["attemptId"]
    candidate = Path(state["worktreePath"])
    if candidate.is_symlink() or candidate.resolve() != expected.resolve() or not candidate.exists():
        return
    head = _git(candidate, "rev-parse", "HEAD", check=False)
    if not head.returncode and head.stdout.decode().strip() == state["snapshotSha"]:
        _git(Path(state["sourcePath"]), "worktree", "remove", "--force", str(candidate), check=False)


def _apply(workspace: Path, feature: str, run_id: str, ref: str, binding: dict, path: Path, state: dict) -> dict:
    source = Path(state["sourcePath"])
    if _identity(source, binding) != (state["sourceBranch"], state["sourceHead"]):
        state.update(status="refresh_required", reasonCode="source_head_changed")
        return _persist(workspace, feature, run_id, ref, path, state)
    for change in state["changes"]:
        if _source_file(source, change["path"])[0] not in (change["before"], change["after"]):
            state.update(status="refresh_required", reasonCode="user_edited_during_sync")
            return _persist(workspace, feature, run_id, ref, path, state)
    for change in sorted(state["changes"], key=lambda item: (item["after"]["kind"] != "missing", item["path"])):
        if load_manifest(workspace, feature, run_id).get("status") in {"cancelled", "rolled_back", "cleaned"}:
            raise ValueError("workspace_sync_run_inactive")
        if _identity(source, binding) != (state["sourceBranch"], state["sourceHead"]):
            state.update(status="refresh_required", reasonCode="source_head_changed")
            return _persist(workspace, feature, run_id, ref, path, state)
        current = _source_file(source, change["path"])[0]
        if current == change["after"]:
            continue  # Replay a write completed before the journal was saved.
        if current != change["before"]:
            state.update(status="refresh_required", reasonCode="user_edited_during_sync")
            return _persist(workspace, feature, run_id, ref, path, state)
        _write_file(source, Path(state["backupPath"]), change)
    state.update(status="synced", lastSyncedSha=state["targetSha"], syncedAt=utc_now())
    result = _persist(workspace, feature, run_id, ref, path, state)
    _cleanup(workspace, run_id, state)
    return result


def sync_workspace(workspace: Path, feature: str, run_id: str, repository_ref: str,
                   resolver_id: str | None = None, *, complete_attempt: str | None = None,
                   target_sha: str | None = None) -> dict:
    manifest = load_manifest(workspace, feature, run_id)
    if manifest.get("status") in {"cancelled", "rolled_back", "cleaned"}:
        raise ValueError("workspace_sync_run_inactive")
    if manifest.get("integrationMode") != "isolated":
        return {"success": True, "status": "skipped", "repositoryRef": repository_ref}
    binding = manifest["repositories"][repository_ref]
    ref = binding.get("integrationRepositoryRef", repository_ref)
    binding = manifest["repositories"][ref]
    _, directory = _paths(workspace, feature, run_id, ref)
    path = directory / "state.json"
    lock = FileLock(coordinates(binding)[0] / "workspace-sync.lock", timeout=0)
    try:
        lock.__enter__()
    except TimeoutError:
        return {"success": True, "status": "pending", "repositoryRef": ref, "reasonCode": "workspace_sync_busy"}
    try:
        # Re-read after taking the publication lock; another Batch may have
        # advanced integration or completed the previous publication meanwhile.
        binding = dict(load_manifest(workspace, feature, run_id)["repositories"][ref])
        if target_sha:
            if _git(Path(binding["gitRoot"]), "merge-base", "--is-ancestor", target_sha,
                    binding["headSha"], check=False).returncode:
                raise ValueError("workspace_sync_target_not_integrated")
            binding["headSha"] = target_sha
        state = _read(path)
        if target_sha and state.get("status") in {"conflicted", "merged", "applying"} and state.get("targetSha") != target_sha:
            # The exhausted predecessor may have a conflict or partial apply.
            # Snapshot the actual user files again against lastSyncedSha; its
            # backup journal remains available and no source content is reset.
            state = {**state, "status": "refresh_required"}
        if complete_attempt:
            if state.get("attemptId") != complete_attempt or not resolver_id or state.get("resolverId") != resolver_id:
                raise ValueError("workspace_sync_stale_resolution")
            if state.get("status") == "synced":
                _cleanup(workspace, run_id, state)
                return {**_summary(state, ref), "reused": True}
            if state.get("status") not in {"conflicted", "merged", "applying", "refresh_required"}:
                raise ValueError("workspace_sync_resolution_not_ready")
            if state.get("status") == "conflicted":
                candidate = Path(state["worktreePath"])
                error = resolution_write_error(workspace, manifest, _summary(state, ref),
                                               candidate / state["conflictedFiles"][0])
                if error:
                    raise ValueError(error)
                if (_value(candidate, "rev-parse", "HEAD") != state["snapshotSha"]
                        or _value(candidate, "rev-parse", "MERGE_HEAD") != state["targetSha"]
                        or _outside(candidate, state["conflictedFiles"]) != state["outsideDigest"]):
                    raise ValueError("workspace_sync_resolution_changed_outside_scope")
                for name in state["conflictedFiles"]:
                    data = _file(candidate / name)[1]
                    if b"<<<<<<< " in data or b">>>>>>> " in data:
                        raise ValueError("workspace_sync_conflict_markers_remaining")
                _git(candidate, "add", "-A", "--", *(f":(literal){name}" for name in state["conflictedFiles"]))
                state.update(status="merged", mergedTree=_value(candidate, "write-tree"))
        elif state.get("status") == "conflicted" and (not resolver_id or state.get("resolverId") in {None, resolver_id}):
            state["resolverId"] = resolver_id
            return _persist(workspace, feature, run_id, ref, path, state)
        elif state.get("status") not in {"merged", "applying"}:
            if state.get("status") == "synced" and state.get("lastSyncedSha") == binding["headSha"]:
                _cleanup(workspace, run_id, state)
                return {**_summary(state, ref), "reused": True}
            if (state.get("status") == "preparing" and state.get("snapshotSha")
                    and state.get("sourcePath") == str(Path(binding["gitRoot"]).resolve())
                    and state.get("targetSha") == binding["headSha"]
                    and _identity(Path(state["sourcePath"]), binding) == (state["sourceBranch"], state["sourceHead"])
                    and all(_source_file(Path(state["sourcePath"]), name)[0] == value
                            for name, value in state["originals"].items())):
                state["resolverId"] = resolver_id
                state = _finish_preparation(workspace, feature, run_id, ref, path, state)
            else:
                if state.get("status") == "preparing" and state.get("snapshotSha"):
                    _cleanup(workspace, run_id, state)
                state = _prepare(workspace, feature, run_id, ref, binding, directory, state, resolver_id)
            if state["status"] == "conflicted":
                return _summary(state, ref)
        if state.get("status") == "merged":
            _plan(state)
            _persist(workspace, feature, run_id, ref, path, state)
        return _apply(workspace, feature, run_id, ref, binding, path, state)
    finally:
        lock.__exit__(None, None, None)


def safely_sync(workspace: Path, feature: str, run_id: str, repository_ref: str,
                resolver_id: str | None = None) -> dict:
    try:
        binding = load_manifest(workspace, feature, run_id)["repositories"][repository_ref]
        repository_ref = binding.get("integrationRepositoryRef", repository_ref)
        return sync_workspace(workspace, feature, run_id, repository_ref, resolver_id)
    except Exception as exc:
        # This is a post-merge publication receipt, not a Batch verdict.
        result = {"success": True, "status": "pending", "repositoryRef": repository_ref, "error": str(exc)}
        with run_lock(workspace, feature, run_id):
            manifest = load_manifest(workspace, feature, run_id)
            result = {**manifest.get("workspaceSync", {}).get(repository_ref, {}), **result}
            manifest.setdefault("workspaceSync", {})[repository_ref] = result
            save_manifest(workspace, feature, run_id, manifest)
        return result


def sync_all(workspace: Path, feature: str, run_id: str) -> list[dict]:
    manifest = load_manifest(workspace, feature, run_id)
    if manifest.get("integrationMode") != "isolated":
        return []
    return [safely_sync(workspace, feature, run_id, ref) for ref, binding in manifest["repositories"].items()
            if binding.get("integrationRepositoryRef", ref) == ref]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("sync", "complete", "process", "complete-job", "fail", "retry"))
    for name in ("workspace", "feature", "run-id", "repository-ref"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--resolver-id")
    parser.add_argument("--attempt-id")
    parser.add_argument("--job-id")
    parser.add_argument("--failure-id")
    parser.add_argument("--error-base64")
    args = parser.parse_args(argv)
    try:
        if args.action == "complete" and not args.attempt_id:
            raise ValueError("workspace_sync_attempt_required")
        workspace, feature = resolve_workspace(args.workspace), resolve_feature(args.feature)
        if args.action in {"process", "complete-job", "fail", "retry"}:
            from hooks.parallel_workspace_queue import operate
            if not args.resolver_id:
                raise ValueError("workspace_sync_resolver_required")
            result = operate(workspace, feature, args.run_id, args.repository_ref, args.resolver_id,
                             action="complete" if args.action == "complete-job" else args.action,
                             job_id=args.job_id, attempt_id=args.attempt_id, failure_id=args.failure_id,
                             error=base64.b64decode(args.error_base64, validate=True).decode("utf-8") if args.error_base64 else None)
        elif args.action == "complete":
            result = sync_workspace(workspace, feature, args.run_id, args.repository_ref,
                                    args.resolver_id, complete_attempt=args.attempt_id)
        else:
            result = safely_sync(workspace, feature, args.run_id, args.repository_ref, args.resolver_id)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result = {"success": False, "status": "pending", "repositoryRef": args.repository_ref, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
