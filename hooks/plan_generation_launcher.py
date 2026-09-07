#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Control plane for the fixed, recoverable Plan-generation Workflow.

Workers only register immutable proposals under ``.tmp/plan_generation``.
This launcher is the only Workflow component allowed to invoke ``plan_writer``
for Draft creation, detail commits, preflight, and finalization.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import (  # noqa: E402
    WriterError,
    atomic_write_json,
    load_json,
    read_object_file,
    resolve_feature,
    resolve_workspace,
)
from hooks.plan_writer import _task_group_digest  # noqa: E402
from hooks.plan_generation_state import (  # noqa: E402
    DEFAULT_LEASE_TTL_SECONDS,
    PlanGenerationError,
    PlanGenerationStore,
)


def _render(ok: bool, *, data: dict[str, Any] | None = None, error: PlanGenerationError | None = None) -> int:
    payload: dict[str, Any] = {"ok": ok}
    if data:
        payload.update(data)
    if error:
        payload["errors"] = [{"reason": error.reason, **({"detail": error.detail} if error.detail else {})}]
    else:
        payload.setdefault("errors", [])
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
    return 0 if ok else 1


def _summary(manifest: dict[str, Any], *, include_lease_token: str | None = None) -> dict[str, Any]:
    group_jobs = manifest.get("groupJobs") if isinstance(manifest.get("groupJobs"), dict) else {}
    detail_jobs = manifest.get("detailJobs") if isinstance(manifest.get("detailJobs"), dict) else {}
    data = {
        "runId": manifest.get("runId"),
        "featureId": manifest.get("featureId"),
        "status": manifest.get("status"),
        "snapshotDigest": manifest.get("snapshot", {}).get("digest"),
        "partitions": list(manifest.get("partitions") or []),
        "taskIds": list(manifest.get("taskIds") or []),
        "pendingPartitions": sorted(key for key, job in group_jobs.items() if isinstance(job, dict) and job.get("status") != "completed"),
        "pendingTaskIds": sorted(key for key, job in detail_jobs.items() if isinstance(job, dict) and job.get("status") != "completed"),
        "lastError": manifest.get("lastError"),
        "groupFile": manifest.get("groupFile"),
    }
    if include_lease_token:
        data["leaseToken"] = include_lease_token
    return data


def _store(args: argparse.Namespace) -> PlanGenerationStore:
    return PlanGenerationStore(resolve_workspace(args.workspace), resolve_feature(args.feature))


def _read_body(path: str) -> dict[str, Any]:
    try:
        return read_object_file(path)
    except WriterError as exc:
        raise PlanGenerationError("plan_generation_proposal_unreadable", str(exc)) from exc


def _writer_result(store: PlanGenerationStore, command: str, extra: list[str]) -> tuple[bool, dict[str, Any]]:
    argv = [
        sys.executable,
        str(ROOT / "hooks" / "plan_writer.py"),
        command,
        "--workspace",
        str(store.workspace),
        "--feature",
        store.feature,
        *extra,
    ]
    result = subprocess.run(argv, capture_output=True, text=True)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "errors": [{"reason": "plan_generation_writer_output_invalid", "detail": result.stderr.strip() or result.stdout.strip()}],
        }
    return result.returncode == 0 and payload.get("ok") is True, payload


def _group_file_for(store: PlanGenerationStore) -> Path:
    return store.feature_dir / ".tmp" / "plan_writer" / "task-groups.json"


def _require_owned_group_file(store: PlanGenerationStore, value: str) -> Path:
    requested = Path(value).expanduser().resolve(strict=False)
    expected = _group_file_for(store).resolve(strict=False)
    if requested != expected:
        raise PlanGenerationError("plan_generation_group_file_path_invalid", f"expected={expected};actual={requested}")
    if not requested.is_file():
        raise PlanGenerationError("plan_generation_group_file_missing", str(requested))
    return requested


def _task_ids_from_group_file(group_file: Path) -> list[str]:
    value = load_json(group_file)
    groups = value.get("groups") if isinstance(value, dict) else None
    if not isinstance(groups, list):
        raise PlanGenerationError("plan_generation_group_file_invalid", str(group_file))
    task_ids = [item.get("id") for item in groups if isinstance(item, dict) and isinstance(item.get("id"), str)]
    if not task_ids or len(task_ids) != len(groups) or len(set(task_ids)) != len(task_ids):
        raise PlanGenerationError("plan_generation_group_task_ids_invalid", str(group_file))
    return task_ids


def _existing_collecting_draft_matches(
    store: PlanGenerationStore,
    group_file: Path,
    code_workspaces: list[str],
) -> bool:
    """Return whether a Draft was created by this run before state was saved.

    ``prepare-task-draft`` writes the Draft and its lock atomically, but the
    Workflow process can still stop before ``set_draft_tasks`` records that
    success in its manifest.  Only adopt an untouched collecting Draft with
    the exact group digest and code-workspace set; every other existing Draft
    remains a conflict that requires an explicit operator decision.
    """
    lock_path = store.feature_dir / ".tmp" / "plan_writer" / "draft" / "lock.json"
    if not lock_path.is_file():
        return False
    try:
        lock = load_json(lock_path)
        groups = load_json(group_file)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(lock, dict) or not isinstance(groups, dict):
        return False
    try:
        locked_group_file = Path(str(lock.get("groupFile", ""))).expanduser().resolve(strict=False)
        expected_workspaces = sorted(str(Path(value).expanduser().resolve()) for value in code_workspaces)
        locked_workspaces = sorted(
            str(Path(value).expanduser().resolve())
            for value in lock.get("codeWorkspaces", [])
            if isinstance(value, str)
        )
    except (OSError, ValueError):
        return False
    return (
        lock.get("featureId") == store.feature
        and lock.get("status") == "collecting"
        and lock.get("readyTaskIds") == []
        and locked_group_file == group_file.resolve(strict=False)
        and lock.get("groupingDigest") == _task_group_digest(groups)
        and locked_workspaces == expected_workspaces
    )


def _all_jobs_complete(manifest: dict[str, Any], field: str) -> bool:
    jobs = manifest.get(field)
    return isinstance(jobs, dict) and bool(jobs) and all(
        isinstance(job, dict) and job.get("status") == "completed"
        for job in jobs.values()
    )


def _writer_error_payload(payload: dict[str, Any]) -> dict[str, Any]:
    errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
    return {
        "reason": "plan_generation_writer_rejected",
        "writerErrors": errors,
        "validation": payload.get("validation"),
    }


def _mark_writer_repair(store: PlanGenerationStore, run_id: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    return store.set_status(run_id, token, "needs_repair", error=_writer_error_payload(payload))


def _repairable_detail_task_ids(payload: dict[str, Any], task_ids: list[str]) -> list[str]:
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    if (
        validation.get("repairable") is not True
        or validation.get("requiresTaskGroupRepair") is True
        or validation.get("requiresIntegrityRepair") is True
    ):
        return []
    rejected = validation.get("repairableTaskIds") if isinstance(validation.get("repairableTaskIds"), list) else []
    rejected_set = {item for item in rejected if isinstance(item, str)}
    return [task_id for task_id in task_ids if task_id in rejected_set]


def _cmd_ensure(args: argparse.Namespace) -> int:
    store = _store(args)
    owner_id = args.owner_id or f"plan-workflow:{args.feature}"
    manifest = store.ensure_run(
        args.code_workspace,
        requested_partitions=args.partition,
        owner_id=owner_id,
        ttl_seconds=args.lease_ttl_seconds,
    )
    token = str(manifest.pop("leaseToken"))
    summary = _summary(manifest, include_lease_token=token)
    summary["resumed"] = bool(manifest.pop("resumed", False))
    return _render(True, data=summary)


def _cmd_status(args: argparse.Namespace) -> int:
    store = _store(args)
    return _render(True, data=_summary(store.load(args.run_id)))


def _cmd_heartbeat(args: argparse.Namespace) -> int:
    store = _store(args)
    return _render(True, data=_summary(store.heartbeat(args.run_id, args.lease_token)))


def _cmd_release(args: argparse.Namespace) -> int:
    store = _store(args)
    return _render(True, data=_summary(store.release(args.run_id, args.lease_token)))


def _cmd_cancel(args: argparse.Namespace) -> int:
    store = _store(args)
    return _render(True, data=_summary(store.cancel(args.run_id, args.lease_token)))


def _cmd_record_group(args: argparse.Namespace) -> int:
    store = _store(args)
    manifest = store.record_group_proposal(args.run_id, args.partition_key, _read_body(args.body_file))
    return _render(True, data=_summary(manifest))


def _cmd_fail_group(args: argparse.Namespace) -> int:
    store = _store(args)
    manifest = store.mark_group_failed(args.run_id, args.partition_key, args.reason)
    return _render(True, data=_summary(manifest))


def _cmd_accept_groups(args: argparse.Namespace) -> int:
    store = _store(args)
    manifest = store.load(args.run_id)
    store.require_lease(manifest, args.lease_token)
    store.assert_current(args.run_id, manifest)
    if not _all_jobs_complete(manifest, "groupJobs"):
        raise PlanGenerationError("plan_generation_group_proposals_incomplete")
    group_file = _require_owned_group_file(store, args.group_file)

    preflight_ok, preflight = _writer_result(store, "preflight-task-groups", ["--group-file", str(group_file)])
    if not preflight_ok:
        updated = _mark_writer_repair(store, args.run_id, args.lease_token, preflight)
        return _render(False, data={**_summary(updated), "writer": preflight})
    store.set_status(args.run_id, args.lease_token, "groups_validated")

    workspace_args = [value for raw in manifest.get("codeWorkspaceArgs", []) for value in ("--code-workspace", str(raw))]
    prepared_ok, prepared = _writer_result(
        store,
        "prepare-task-draft",
        ["--group-file", str(group_file), *workspace_args],
    )
    code_workspaces = [str(value) for value in manifest.get("codeWorkspaceArgs", [])]
    if not prepared_ok and _existing_collecting_draft_matches(store, group_file, code_workspaces):
        prepared_ok = True
        prepared = {
            "ok": True,
            "recovered": True,
            "draft": {
                "status": "collecting",
                "groupingDigest": _task_group_digest(load_json(group_file)),
            },
        }
    if not prepared_ok:
        updated = _mark_writer_repair(store, args.run_id, args.lease_token, prepared)
        return _render(False, data={**_summary(updated), "writer": prepared})
    task_ids = _task_ids_from_group_file(group_file)
    updated = store.set_draft_tasks(args.run_id, args.lease_token, task_ids, group_file)
    return _render(True, data={**_summary(updated), "writer": prepared})


def _cmd_record_detail(args: argparse.Namespace) -> int:
    store = _store(args)
    manifest = store.record_detail_proposal(args.run_id, args.task_id, _read_body(args.body_file))
    return _render(True, data=_summary(manifest))


def _cmd_fail_detail(args: argparse.Namespace) -> int:
    store = _store(args)
    manifest = store.mark_detail_failed(args.run_id, args.task_id, args.reason)
    return _render(True, data=_summary(manifest))


def _collect_detail_commit_body(store: PlanGenerationStore, manifest: dict[str, Any]) -> Path:
    if not _all_jobs_complete(manifest, "detailJobs"):
        raise PlanGenerationError("plan_generation_detail_proposals_incomplete")
    entries: list[dict[str, Any]] = []
    run_dir = store.run_dir(str(manifest["runId"])).resolve()
    for task_id in manifest.get("taskIds", []):
        job = manifest["detailJobs"].get(task_id)
        proposal_path = Path(str(job.get("proposalPath", ""))).resolve(strict=False)
        try:
            proposal_path.relative_to(run_dir)
        except ValueError as exc:
            raise PlanGenerationError("plan_generation_detail_proposal_path_invalid", task_id) from exc
        proposal = load_json(proposal_path)
        if not isinstance(proposal, dict) or proposal.get("taskId") != task_id or not isinstance(proposal.get("detail"), dict):
            raise PlanGenerationError("plan_generation_detail_proposal_invalid", task_id)
        entries.append({"taskId": task_id, "detail": proposal["detail"]})
    path = run_dir / "detail-commit.json"
    atomic_write_json(path, {"details": entries})
    return path


def _cmd_commit_details(args: argparse.Namespace) -> int:
    store = _store(args)
    manifest = store.load(args.run_id)
    store.require_lease(manifest, args.lease_token)
    store.assert_current(args.run_id, manifest)
    body_file = _collect_detail_commit_body(store, manifest)
    committed_ok, committed = _writer_result(store, "set-draft-task-details", ["--body-file", str(body_file)])
    if not committed_ok:
        retry_task_ids = _repairable_detail_task_ids(committed, list(manifest.get("taskIds") or []))
        if retry_task_ids:
            try:
                updated = store.retry_detail_jobs(
                    args.run_id,
                    args.lease_token,
                    retry_task_ids,
                    error=_writer_error_payload(committed),
                )
            except PlanGenerationError as exc:
                if exc.reason != "plan_generation_detail_retry_exhausted":
                    raise
            else:
                return _render(
                    False,
                    data={
                        **_summary(updated),
                        "writer": committed,
                        "recovery": {"action": "regenerate_task_details", "taskIds": retry_task_ids},
                    },
                )
        updated = _mark_writer_repair(store, args.run_id, args.lease_token, committed)
        return _render(False, data={**_summary(updated), "writer": committed})
    updated = store.set_status(args.run_id, args.lease_token, "details_committed")
    return _render(True, data={**_summary(updated), "writer": committed})


def _cmd_preflight(args: argparse.Namespace) -> int:
    store = _store(args)
    manifest = store.load(args.run_id)
    store.require_lease(manifest, args.lease_token)
    store.assert_current(args.run_id, manifest)
    if manifest.get("status") not in {"details_committed", "preflight_passed", "needs_repair"}:
        raise PlanGenerationError("plan_generation_preflight_state_invalid", str(manifest.get("status")))
    passed, payload = _writer_result(store, "preflight-task-draft", [])
    if not passed:
        updated = _mark_writer_repair(store, args.run_id, args.lease_token, payload)
        return _render(False, data={**_summary(updated), "writer": payload})
    updated = store.set_status(args.run_id, args.lease_token, "preflight_passed")
    return _render(True, data={**_summary(updated), "writer": payload})


def _cmd_finalize(args: argparse.Namespace) -> int:
    store = _store(args)
    manifest = store.load(args.run_id)
    store.require_lease(manifest, args.lease_token)
    store.assert_current(args.run_id, manifest)
    if manifest.get("status") != "preflight_passed":
        raise PlanGenerationError("plan_generation_finalize_state_invalid", str(manifest.get("status")))
    finalized, payload = _writer_result(store, "finalize-task-draft", [])
    if not finalized:
        updated = _mark_writer_repair(store, args.run_id, args.lease_token, payload)
        return _render(False, data={**_summary(updated), "writer": payload})
    updated = store.set_status(args.run_id, args.lease_token, "finalized")
    return _render(True, data={**_summary(updated), "writer": payload})


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--feature", required=True)


def _run_selector(parser: argparse.ArgumentParser, *, lease: bool = False) -> None:
    _common(parser)
    parser.add_argument("--run-id", required=True)
    if lease:
        parser.add_argument("--lease-token", required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run or recover fixed Plan generation")
    sub = parser.add_subparsers(dest="command", required=True)

    ensure = sub.add_parser("ensure")
    _common(ensure)
    ensure.add_argument("--code-workspace", action="append", required=True)
    ensure.add_argument("--partition", action="append")
    ensure.add_argument("--owner-id")
    ensure.add_argument("--lease-ttl-seconds", type=int, default=DEFAULT_LEASE_TTL_SECONDS)
    ensure.set_defaults(func=_cmd_ensure)

    status = sub.add_parser("status")
    _run_selector(status)
    status.set_defaults(func=_cmd_status)

    heartbeat = sub.add_parser("heartbeat")
    _run_selector(heartbeat, lease=True)
    heartbeat.set_defaults(func=_cmd_heartbeat)

    release = sub.add_parser("release")
    _run_selector(release, lease=True)
    release.set_defaults(func=_cmd_release)

    cancel = sub.add_parser("cancel")
    _run_selector(cancel, lease=True)
    cancel.set_defaults(func=_cmd_cancel)

    record_group = sub.add_parser("record-group-proposal")
    _run_selector(record_group)
    record_group.add_argument("--partition-key", required=True)
    record_group.add_argument("--body-file", required=True)
    record_group.set_defaults(func=_cmd_record_group)

    fail_group = sub.add_parser("fail-group-proposal")
    _run_selector(fail_group)
    fail_group.add_argument("--partition-key", required=True)
    fail_group.add_argument("--reason", required=True)
    fail_group.set_defaults(func=_cmd_fail_group)

    accept_groups = sub.add_parser("accept-groups")
    _run_selector(accept_groups, lease=True)
    accept_groups.add_argument("--group-file", required=True)
    accept_groups.set_defaults(func=_cmd_accept_groups)

    record_detail = sub.add_parser("record-detail-proposal")
    _run_selector(record_detail)
    record_detail.add_argument("--task-id", required=True)
    record_detail.add_argument("--body-file", required=True)
    record_detail.set_defaults(func=_cmd_record_detail)

    fail_detail = sub.add_parser("fail-detail-proposal")
    _run_selector(fail_detail)
    fail_detail.add_argument("--task-id", required=True)
    fail_detail.add_argument("--reason", required=True)
    fail_detail.set_defaults(func=_cmd_fail_detail)

    commit_details = sub.add_parser("commit-details")
    _run_selector(commit_details, lease=True)
    commit_details.set_defaults(func=_cmd_commit_details)

    preflight = sub.add_parser("preflight")
    _run_selector(preflight, lease=True)
    preflight.set_defaults(func=_cmd_preflight)

    finalize = sub.add_parser("finalize")
    _run_selector(finalize, lease=True)
    finalize.set_defaults(func=_cmd_finalize)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PlanGenerationError as exc:
        return _render(False, error=exc)
    except (WriterError, OSError, ValueError) as exc:
        return _render(False, error=PlanGenerationError("plan_generation_launcher_error", str(exc)))


if __name__ == "__main__":
    raise SystemExit(main())
