#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Durable state for the recoverable Plan-generation Workflow.

This module intentionally owns only Workflow runtime data.  It never reads or
writes formal plan artifacts; :mod:`hooks.plan_writer` remains the sole owner
of Draft and finalized plan files.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from hooks.evidence_kernel import FileLock
from hooks.design_contract_lock import DESIGN_CONTRACT_LOCK_FILE, load_confirmed_design_contract
from hooks.json_writer_common import atomic_write_json, feature_dir, load_json


RUNS_RELATIVE_DIR = Path(".tmp") / "plan_generation"
MANIFEST_FILE = "manifest.json"
LEASE_FILE = "lease.json"
EVENTS_FILE = "events.jsonl"
STATE_VERSION = 1
DEFAULT_LEASE_TTL_SECONDS = 300
MAX_PROPOSAL_ATTEMPTS = 2

TERMINAL_STATUSES = {"finalized", "cancelled", "invalidated"}
ACTIVE_STATUSES = {
    "snapshot_locked",
    "generating_groups",
    "groups_validated",
    "draft_prepared",
    "generating_details",
    "details_committed",
    "preflight_passed",
    "needs_repair",
}


class PlanGenerationError(ValueError):
    """Expected Workflow errors with a stable machine-readable reason."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_digest(value: Any) -> str:
    return _digest_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _safe_name(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _git_value(path: Path, *args: str) -> str:
    return _git_bytes(path, *args).decode("utf-8", "surrogateescape").strip()


def _git_bytes(path: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PlanGenerationError("plan_generation_code_workspace_not_git", str(path)) from exc
    return bytes(result.stdout)


def _repository_input_digest(git_root: Path) -> str:
    """Hash every local code input visible to a Plan worker.

    ``git status`` alone cannot distinguish two edits to an already-modified
    file.  Include the tracked binary diff and untracked file content so a
    resumed run cannot reuse proposals generated from a different checkout.
    """

    digest = hashlib.sha256()
    try:
        digest.update(_git_bytes(git_root, "diff", "--binary", "HEAD"))
        candidates = _git_bytes(git_root, "ls-files", "--others", "--exclude-standard", "-z")
    except PlanGenerationError:
        # An unborn repository has no HEAD.  Hash all files that Git knows or
        # would treat as untracked instead of rejecting a legitimate first plan.
        digest.update(b"unborn-repository\0")
        candidates = _git_bytes(git_root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    for raw_relative in sorted(item for item in candidates.split(b"\0") if item):
        relative = raw_relative.decode("utf-8", "surrogateescape")
        candidate = git_root / relative
        digest.update(raw_relative)
        if candidate.is_symlink():
            digest.update(os.readlink(candidate).encode("utf-8", "surrogateescape"))
        elif candidate.is_file():
            digest.update(candidate.read_bytes())
    return digest.hexdigest()


def _parse_workspace_arg(raw: str) -> tuple[str, Path]:
    value = raw.strip()
    if not value:
        raise PlanGenerationError("plan_generation_code_workspace_empty")
    reference, separator, path_value = value.partition("=")
    if separator:
        if not reference.strip() or not path_value.strip():
            raise PlanGenerationError("plan_generation_code_workspace_invalid", value)
        return reference.strip(), Path(path_value.strip()).expanduser().resolve(strict=False)
    return "default", Path(value).expanduser().resolve(strict=False)


class PlanGenerationStore:
    """Feature-scoped runtime store with short file-locked mutations."""

    def __init__(self, workspace: Path, feature: str) -> None:
        self.workspace = workspace.resolve()
        self.feature = feature
        self.feature_dir = feature_dir(self.workspace, feature)
        self.runs_dir = self.feature_dir / RUNS_RELATIVE_DIR

    def run_dir(self, run_id: str) -> Path:
        if not run_id.startswith("pg-") or any(item in run_id for item in ("/", "\\", "..")):
            raise PlanGenerationError("plan_generation_run_id_invalid", run_id)
        return self.runs_dir / run_id

    def manifest_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / MANIFEST_FILE

    def _run_lock(self, run_id: str) -> FileLock:
        return FileLock(self.run_dir(run_id) / ".lock")

    def _runs_lock(self) -> FileLock:
        return FileLock(self.runs_dir / ".runs.lock")

    def capture_snapshot(self, code_workspace_args: list[str]) -> dict[str, Any]:
        # Design owns semantic validation of design.md.  Plan generation locks
        # only the Design-produced snapshot and never rechecks the raw file.
        design_lock_path = self.feature_dir / DESIGN_CONTRACT_LOCK_FILE
        contract, contract_errors = load_confirmed_design_contract(self.feature_dir, self.feature)
        if contract_errors:
            first = contract_errors[0]
            raise PlanGenerationError(
                "plan_generation_design_contract_lock_invalid",
                str(first.get("reason") or design_lock_path),
            )

        specs_dir = self.feature_dir / "specs"
        spec_files = sorted(path for path in specs_dir.glob("**/*.md") if path.is_file()) if specs_dir.is_dir() else []
        if not spec_files:
            raise PlanGenerationError("plan_generation_specs_missing", str(specs_dir))
        specs = {
            path.relative_to(self.feature_dir).as_posix(): _digest_bytes(path.read_bytes())
            for path in spec_files
        }

        if not code_workspace_args:
            raise PlanGenerationError("plan_generation_code_workspace_required")
        workspaces: list[dict[str, str]] = []
        seen_refs: set[str] = set()
        seen_root_names: set[str] = set()
        seen_paths: set[str] = set()
        for raw in code_workspace_args:
            reference, path = _parse_workspace_arg(raw)
            if not path.is_dir():
                raise PlanGenerationError("plan_generation_code_workspace_missing", f"{reference}:{path}")
            if reference in seen_refs:
                raise PlanGenerationError("plan_generation_code_workspace_duplicate_ref", reference)
            git_root = Path(_git_value(path, "rev-parse", "--show-toplevel")).resolve()
            root_key = str(git_root)
            if root_key in seen_paths:
                raise PlanGenerationError("plan_generation_code_workspace_duplicate_repository", root_key)
            if git_root.name in seen_root_names:
                raise PlanGenerationError("plan_generation_code_workspace_duplicate_ref", git_root.name)
            seen_refs.add(reference)
            seen_root_names.add(git_root.name)
            seen_paths.add(root_key)
            try:
                head = _git_value(git_root, "rev-parse", "HEAD")
            except PlanGenerationError:
                head = "unborn"
            workspaces.append({
                # Plan writer derives workspaceRef from the physical Git root
                # name.  Keep any caller alias out of the durable contract so
                # a worker cannot emit an alias that the writer later rejects.
                "reference": git_root.name,
                "requestedPath": str(path),
                "gitRoot": root_key,
                "head": head,
                "statusSha256": _digest_bytes(_git_value(git_root, "status", "--porcelain=v1").encode("utf-8")),
                "contentSha256": _repository_input_digest(git_root),
            })

        writer_path = Path(__file__).resolve().parent / "plan_writer.py"
        template_dir = Path(__file__).resolve().parents[1] / "skills" / "autodev" / "autodev-plan" / "templates"
        template_digests = {
            path.name: _digest_bytes(path.read_bytes())
            for path in sorted(template_dir.glob("*.json"))
            if path.is_file()
        }
        snapshot = {
            "designContractLock": {
                "path": DESIGN_CONTRACT_LOCK_FILE,
                "sha256": _digest_bytes(design_lock_path.read_bytes()),
                "contractSha256": contract["sha256"],
            },
            "specs": specs,
            "codeWorkspaces": workspaces,
            "writer": {
                "planWriterSha256": _digest_bytes(writer_path.read_bytes()),
                "templateSha256ByName": template_digests,
            },
        }
        snapshot["digest"] = _canonical_digest(snapshot)
        return snapshot

    def discover_partitions(self, requested: list[str] | None = None) -> list[str]:
        if requested:
            values = sorted({item.strip() for item in requested if item.strip()})
            if not values:
                raise PlanGenerationError("plan_generation_partitions_empty")
            return values
        specs_dir = self.feature_dir / "specs"
        partitions = {
            path.parent.relative_to(self.feature_dir).as_posix()
            for path in specs_dir.glob("**/*.md")
            if path.is_file()
        }
        return sorted(partitions) or ["specs"]

    def _load_unlocked(self, run_id: str) -> dict[str, Any]:
        path = self.manifest_path(run_id)
        if not path.is_file():
            raise PlanGenerationError("plan_generation_run_not_found", run_id)
        value = load_json(path)
        if not isinstance(value, dict) or value.get("version") != STATE_VERSION or value.get("runId") != run_id:
            raise PlanGenerationError("plan_generation_manifest_invalid", str(path))
        return value

    def load(self, run_id: str) -> dict[str, Any]:
        with self._run_lock(run_id):
            return self._load_unlocked(run_id)

    def _write_unlocked(self, run_id: str, manifest: dict[str, Any]) -> None:
        manifest["updatedAt"] = utc_now()
        atomic_write_json(self.manifest_path(run_id), manifest)
        lease = manifest.get("lease")
        lease_path = self.run_dir(run_id) / LEASE_FILE
        if isinstance(lease, dict):
            atomic_write_json(lease_path, lease)
        elif lease_path.exists():
            lease_path.unlink()

    def _append_event_unlocked(self, run_id: str, event: str, **data: Any) -> None:
        path = self.run_dir(run_id) / EVENTS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": utc_now(), "event": event, **data}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()

    def mutate(self, run_id: str, callback: Callable[[dict[str, Any]], None], *, event: str | None = None, event_data: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._run_lock(run_id):
            manifest = self._load_unlocked(run_id)
            callback(manifest)
            self._write_unlocked(run_id, manifest)
            if event:
                self._append_event_unlocked(run_id, event, **(event_data or {}))
            return manifest

    def _active_manifests_unlocked(self) -> list[dict[str, Any]]:
        if not self.runs_dir.is_dir():
            return []
        manifests: list[dict[str, Any]] = []
        for path in sorted(self.runs_dir.glob("pg-*/manifest.json")):
            try:
                raw = load_json(path)
            except Exception:
                continue
            if isinstance(raw, dict) and raw.get("version") == STATE_VERSION and raw.get("featureId") == self.feature:
                manifests.append(raw)
        return manifests

    def _acquire_lease_unlocked(self, run_id: str, manifest: dict[str, Any], owner_id: str, ttl_seconds: int) -> str:
        now = time.time()
        lease = manifest.get("lease") if isinstance(manifest.get("lease"), dict) else None
        if lease and float(lease.get("expiresAtEpoch", 0)) > now and lease.get("ownerId") != owner_id:
            raise PlanGenerationError("plan_generation_run_leased", f"runId={run_id};owner={lease.get('ownerId')}")
        token = str(lease.get("token")) if lease and lease.get("ownerId") == owner_id else secrets.token_urlsafe(24)
        manifest["lease"] = {
            "ownerId": owner_id,
            "token": token,
            "heartbeatAt": utc_now(),
            "expiresAtEpoch": now + ttl_seconds,
            "ttlSeconds": ttl_seconds,
        }
        return token

    def ensure_run(
        self,
        code_workspace_args: list[str],
        *,
        requested_partitions: list[str] | None,
        owner_id: str,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ) -> dict[str, Any]:
        if ttl_seconds <= 0:
            raise PlanGenerationError("plan_generation_lease_ttl_invalid", str(ttl_seconds))
        if (self.feature_dir / "plan.json").is_file():
            raise PlanGenerationError("plan_generation_formal_plan_exists", "use diagnose-plan-repair/reopen-finalized-draft")
        snapshot = self.capture_snapshot(code_workspace_args)
        partitions = self.discover_partitions(requested_partitions)
        with self._runs_lock():
            candidates = sorted(
                self._active_manifests_unlocked(),
                key=lambda item: str(item.get("updatedAt", "")),
                reverse=True,
            )
            for candidate in candidates:
                run_id = str(candidate.get("runId", ""))
                if candidate.get("status") not in ACTIVE_STATUSES:
                    continue
                if candidate.get("snapshot", {}).get("digest") != snapshot["digest"]:
                    self.mutate(
                        run_id,
                        lambda item: item.update({
                            "status": "invalidated",
                            "lastError": {"reason": "plan_generation_input_changed"},
                            "lease": None,
                        }),
                        event="invalidated",
                        event_data={"reason": "plan_generation_input_changed"},
                    )
                    continue
                with self._run_lock(run_id):
                    manifest = self._load_unlocked(run_id)
                    token = self._acquire_lease_unlocked(run_id, manifest, owner_id, ttl_seconds)
                    self._write_unlocked(run_id, manifest)
                    self._append_event_unlocked(run_id, "resumed", ownerId=owner_id)
                    return {**manifest, "leaseToken": token, "resumed": True}

            run_id = f"pg-{uuid.uuid4().hex}"
            manifest = {
                "version": STATE_VERSION,
                "runId": run_id,
                "featureId": self.feature,
                "status": "generating_groups",
                "createdAt": utc_now(),
                "updatedAt": utc_now(),
                "snapshot": snapshot,
                "codeWorkspaceArgs": [item["requestedPath"] for item in snapshot["codeWorkspaces"]],
                "partitions": partitions,
                "groupJobs": {
                    key: {"status": "pending", "attempts": 0, "proposalPath": None, "error": None}
                    for key in partitions
                },
                "taskIds": [],
                "detailJobs": {},
                "lastError": None,
                "lease": None,
            }
            token = self._acquire_lease_unlocked(run_id, manifest, owner_id, ttl_seconds)
            self._write_unlocked(run_id, manifest)
            self._append_event_unlocked(run_id, "created", ownerId=owner_id, snapshotDigest=snapshot["digest"])
            return {**manifest, "leaseToken": token, "resumed": False}

    def assert_current(self, run_id: str, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
        current = manifest or self.load(run_id)
        snapshot = self.capture_snapshot([str(item) for item in current.get("codeWorkspaceArgs", [])])
        if snapshot["digest"] == current.get("snapshot", {}).get("digest"):
            return current
        self.mutate(
            run_id,
            lambda item: item.update({
                "status": "invalidated",
                "lastError": {"reason": "plan_generation_input_changed"},
                "lease": None,
            }),
            event="invalidated",
            event_data={"reason": "plan_generation_input_changed"},
        )
        raise PlanGenerationError("plan_generation_input_changed", run_id)

    def require_lease(self, manifest: dict[str, Any], token: str) -> None:
        lease = manifest.get("lease") if isinstance(manifest.get("lease"), dict) else None
        if not lease or not token or not secrets.compare_digest(str(lease.get("token", "")), token):
            raise PlanGenerationError("plan_generation_lease_invalid")
        if float(lease.get("expiresAtEpoch", 0)) <= time.time():
            raise PlanGenerationError("plan_generation_lease_expired")

    def heartbeat(self, run_id: str, token: str) -> dict[str, Any]:
        def update(manifest: dict[str, Any]) -> None:
            self.require_lease(manifest, token)
            lease = manifest["lease"]
            lease["heartbeatAt"] = utc_now()
            lease["expiresAtEpoch"] = time.time() + int(lease.get("ttlSeconds", DEFAULT_LEASE_TTL_SECONDS))

        return self.mutate(run_id, update, event="heartbeat")

    def release(self, run_id: str, token: str) -> dict[str, Any]:
        def update(manifest: dict[str, Any]) -> None:
            self.require_lease(manifest, token)
            manifest["lease"] = None

        return self.mutate(run_id, update, event="lease_released")

    def record_group_proposal(self, run_id: str, partition_key: str, proposal: dict[str, Any]) -> dict[str, Any]:
        self.assert_current(run_id)

        def update(manifest: dict[str, Any]) -> None:
            job = manifest.get("groupJobs", {}).get(partition_key)
            if not isinstance(job, dict):
                raise PlanGenerationError("plan_generation_partition_unknown", partition_key)
            if int(job.get("attempts", 0)) >= MAX_PROPOSAL_ATTEMPTS:
                raise PlanGenerationError("plan_generation_group_retry_exhausted", partition_key)
            if proposal.get("snapshotDigest") != manifest.get("snapshot", {}).get("digest"):
                raise PlanGenerationError("plan_generation_proposal_snapshot_mismatch", partition_key)
            if proposal.get("partitionKey") != partition_key:
                raise PlanGenerationError("plan_generation_proposal_partition_mismatch", partition_key)
            path = self.run_dir(run_id) / "group-proposals" / f"{_safe_name(partition_key)}.json"
            atomic_write_json(path, proposal)
            job.update({"status": "completed", "attempts": int(job.get("attempts", 0)) + 1, "proposalPath": str(path), "error": None})

        return self.mutate(run_id, update, event="group_proposal_recorded", event_data={"partitionKey": partition_key})

    def mark_group_failed(self, run_id: str, partition_key: str, reason: str) -> dict[str, Any]:
        def update(manifest: dict[str, Any]) -> None:
            job = manifest.get("groupJobs", {}).get(partition_key)
            if not isinstance(job, dict):
                raise PlanGenerationError("plan_generation_partition_unknown", partition_key)
            job.update({"status": "failed", "attempts": int(job.get("attempts", 0)) + 1, "error": reason})
            manifest.update({"status": "needs_repair", "lastError": {"reason": reason, "partitionKey": partition_key}})

        return self.mutate(run_id, update, event="group_proposal_failed", event_data={"partitionKey": partition_key, "reason": reason})

    def set_draft_tasks(self, run_id: str, token: str, task_ids: list[str], group_file: Path) -> dict[str, Any]:
        self.assert_current(run_id)

        def update(manifest: dict[str, Any]) -> None:
            self.require_lease(manifest, token)
            if any(job.get("status") != "completed" for job in manifest.get("groupJobs", {}).values() if isinstance(job, dict)):
                raise PlanGenerationError("plan_generation_group_proposals_incomplete")
            if not task_ids or len(set(task_ids)) != len(task_ids):
                raise PlanGenerationError("plan_generation_task_ids_invalid")
            manifest.update({
                "status": "generating_details",
                "groupFile": str(group_file.resolve()),
                "taskIds": list(task_ids),
                "detailJobs": {
                    task_id: {"status": "pending", "attempts": 0, "proposalPath": None, "error": None}
                    for task_id in task_ids
                },
                "lastError": None,
            })

        return self.mutate(run_id, update, event="draft_prepared", event_data={"taskIds": task_ids})

    def record_detail_proposal(self, run_id: str, task_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        self.assert_current(run_id)

        def update(manifest: dict[str, Any]) -> None:
            job = manifest.get("detailJobs", {}).get(task_id)
            if not isinstance(job, dict):
                raise PlanGenerationError("plan_generation_task_unknown", task_id)
            if int(job.get("attempts", 0)) >= MAX_PROPOSAL_ATTEMPTS:
                raise PlanGenerationError("plan_generation_detail_retry_exhausted", task_id)
            if proposal.get("snapshotDigest") != manifest.get("snapshot", {}).get("digest"):
                raise PlanGenerationError("plan_generation_proposal_snapshot_mismatch", task_id)
            if proposal.get("taskId") != task_id or not isinstance(proposal.get("detail"), dict):
                raise PlanGenerationError("plan_generation_detail_proposal_invalid", task_id)
            path = self.run_dir(run_id) / "detail-proposals" / f"{task_id}.json"
            atomic_write_json(path, proposal)
            job.update({"status": "completed", "attempts": int(job.get("attempts", 0)) + 1, "proposalPath": str(path), "error": None})

        return self.mutate(run_id, update, event="detail_proposal_recorded", event_data={"taskId": task_id})

    def retry_detail_jobs(
        self,
        run_id: str,
        token: str,
        task_ids: list[str],
        *,
        error: dict[str, Any],
    ) -> dict[str, Any]:
        """Return rejected detail proposals to workers without touching Draft.

        A writer rejection is not a worker transport failure: the proposal was
        persisted and consumed once, so its next generation counts as the
        second bounded attempt.  Only task-detail errors take this path; any
        group, Design, or Draft-integrity error remains ``needs_repair`` for
        an explicit coordinator decision.
        """
        self.assert_current(run_id)
        requested = list(dict.fromkeys(task_ids))
        if not requested:
            raise PlanGenerationError("plan_generation_retry_task_ids_empty")

        def update(manifest: dict[str, Any]) -> None:
            self.require_lease(manifest, token)
            jobs = manifest.get("detailJobs") if isinstance(manifest.get("detailJobs"), dict) else {}
            unknown = [task_id for task_id in requested if not isinstance(jobs.get(task_id), dict)]
            if unknown:
                raise PlanGenerationError("plan_generation_task_unknown", ",".join(unknown))
            exhausted = [
                task_id
                for task_id in requested
                if int(jobs[task_id].get("attempts", 0)) >= MAX_PROPOSAL_ATTEMPTS
            ]
            if exhausted:
                raise PlanGenerationError("plan_generation_detail_retry_exhausted", ",".join(exhausted))
            for task_id in requested:
                jobs[task_id].update({"status": "pending", "proposalPath": None, "error": "writer_validation_rejected"})
            manifest.update({"status": "generating_details", "lastError": error})

        return self.mutate(
            run_id,
            update,
            event="detail_proposals_requeued",
            event_data={"taskIds": requested},
        )

    def mark_detail_failed(self, run_id: str, task_id: str, reason: str) -> dict[str, Any]:
        def update(manifest: dict[str, Any]) -> None:
            job = manifest.get("detailJobs", {}).get(task_id)
            if not isinstance(job, dict):
                raise PlanGenerationError("plan_generation_task_unknown", task_id)
            job.update({"status": "failed", "attempts": int(job.get("attempts", 0)) + 1, "error": reason})
            manifest.update({"status": "needs_repair", "lastError": {"reason": reason, "taskId": task_id}})

        return self.mutate(run_id, update, event="detail_proposal_failed", event_data={"taskId": task_id, "reason": reason})

    def set_status(self, run_id: str, token: str, status: str, *, error: dict[str, Any] | None = None) -> dict[str, Any]:
        if status not in ACTIVE_STATUSES | TERMINAL_STATUSES:
            raise PlanGenerationError("plan_generation_status_invalid", status)
        self.assert_current(run_id)

        def update(manifest: dict[str, Any]) -> None:
            self.require_lease(manifest, token)
            manifest["status"] = status
            manifest["lastError"] = error

        return self.mutate(run_id, update, event="status_changed", event_data={"status": status, "error": error})

    def cancel(self, run_id: str, token: str) -> dict[str, Any]:
        def update(manifest: dict[str, Any]) -> None:
            self.require_lease(manifest, token)
            manifest.update({"status": "cancelled", "lease": None, "lastError": {"reason": "cancelled_by_user"}})

        return self.mutate(run_id, update, event="cancelled")
