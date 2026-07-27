#!/usr/bin/env python3
"""Read and atomically update Code-stage exploration caches."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.code_exploration import (  # noqa: E402
    CACHE_STATUSES,
    CRITICAL_BASENAMES,
    CRITICAL_GLOB_PATTERNS,
    FINDING_FIELDS,
    POLICIES,
    POLICY_PRIORITY,
    SCHEMA_VERSION,
    CodeExplorationError,
    cache_sha256,
    exploration_cache_path,
    inspect_exploration_cache,
    utc_now,
    validate_findings,
)
from hooks.evidence_kernel import FileLock  # noqa: E402
from hooks.json_writer_common import (  # noqa: E402
    WriterError,
    atomic_write_json,
    resolve_feature,
    resolve_workspace,
)
from hooks.plan_json import EXECUTION_LANES, load_plan_bundle, normalize_status  # noqa: E402
from hooks.repository_snapshot import (  # noqa: E402
    RepositorySnapshotError,
    resolve_repositories,
)


def _emit(ok: bool, **payload: Any) -> int:
    print(json.dumps({"ok": ok, **payload}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _feature_dir(workspace: Path, feature: str) -> Path:
    return workspace / ".autobizdevops" / "features" / feature


def _exploration_lock(feature_dir: Path) -> FileLock:
    return FileLock(feature_dir / "cache" / "code-exploration" / ".lock")


def _bundle_for_task(feature_dir: Path, task_id: str):
    try:
        bundle = load_plan_bundle(feature_dir)
    except ValueError as exc:
        raise CodeExplorationError(f"invalid_plan_json:{exc}") from exc
    batch_id = bundle.task_batches.get(task_id)
    if not isinstance(batch_id, str):
        raise CodeExplorationError(f"task_not_found:{task_id}")
    if bundle.root.get("activeBatchId") != batch_id:
        raise CodeExplorationError(f"task_not_in_active_batch:{task_id}")
    batch = bundle.batches.get(batch_id)
    lane = batch.get("executionLane") if isinstance(batch, dict) else None
    if lane not in EXECUTION_LANES:
        raise CodeExplorationError(f"active_batch_execution_lane_invalid:{batch_id}")
    return bundle, batch_id, lane


def _load_body(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CodeExplorationError(f"code_exploration_body_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise CodeExplorationError("code_exploration_body_root_invalid")
    return value


def _validate_findings(findings: Any) -> dict[str, list[Any]]:
    errors = validate_findings(findings)
    if errors:
        raise CodeExplorationError("code_exploration_findings_invalid:" + ",".join(errors))
    return {field: list(findings[field]) for field in FINDING_FIELDS}


def _string_paths(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise CodeExplorationError(f"code_exploration_paths_invalid:{field}")
    paths = [item.replace("\\", "/").strip() for item in value]
    if any(Path(item).is_absolute() or ".." in Path(item).parts for item in paths):
        raise CodeExplorationError(f"code_exploration_paths_unsafe:{field}")
    return sorted(set(paths))


def _repository_for_command(code_workspace: Path) -> tuple[str, Path]:
    try:
        repositories = resolve_repositories([code_workspace])
    except RepositorySnapshotError as exc:
        raise CodeExplorationError(str(exc)) from exc
    return next(iter(repositories.items()))


def _all_completed_coverage(bundle) -> tuple[list[str], list[str], str | None]:
    task_ids: list[str] = []
    evidence_ids: list[str] = []
    last_batch: str | None = None
    for task in bundle.tasks:
        if normalize_status(task.get("status")) != "done":
            continue
        task_id = task.get("id")
        batch_id = bundle.task_batches.get(task_id)
        if isinstance(task_id, str):
            task_ids.append(task_id)
        if isinstance(batch_id, str):
            last_batch = batch_id
        evidence_ids.extend(item for item in task.get("completionEvidenceIds", []) if isinstance(item, str))
    return sorted(set(task_ids)), sorted(set(evidence_ids)), last_batch


def _new_cache(
    *,
    feature: str,
    bundle: Any,
    batch_id: str,
    task_id: str,
    lane: str,
    repository_id: str,
    repository_root: Path,
    snapshot: dict[str, Any],
    findings: dict[str, list[Any]],
    explored_paths: list[str],
    shared_paths: list[str],
    coverage: tuple[list[str], list[str], str | None],
) -> dict[str, Any]:
    task_ids, evidence_ids, last_batch = coverage
    now = utc_now()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "featureId": feature,
        "repository": {"id": repository_id, "root": str(repository_root)},
        "executionLane": lane,
        "capturedAt": now,
        "capturedBatchId": batch_id,
        "capturedTaskId": task_id,
        "gitSnapshot": snapshot,
        "findings": findings,
        "exploredPaths": explored_paths,
        "sharedPaths": shared_paths,
        "evidenceCoverage": {
            "explainedTaskIds": task_ids,
            "completionEvidenceIds": evidence_ids,
            "lastExplainedBatchId": last_batch,
            "lastExplainedAt": now,
        },
    }


def _inspect_one(feature_dir: Path, bundle: Any, task_id: str, repository_root: Path) -> dict[str, Any]:
    try:
        return inspect_exploration_cache(feature_dir, bundle, task_id, repository_root)
    except CodeExplorationError:
        raise
    except RepositorySnapshotError as exc:
        raise CodeExplorationError(str(exc)) from exc


def inspect_caches(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspaces: list[Path],
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    bundle, _batch_id, lane = _bundle_for_task(feature_dir, task_id)
    try:
        repositories = resolve_repositories(code_workspaces)
    except RepositorySnapshotError as exc:
        raise CodeExplorationError(str(exc)) from exc
    caches = [_inspect_one(feature_dir, bundle, task_id, root) for root in repositories.values()]
    status = max((item["status"] for item in caches), key=lambda value: POLICY_PRIORITY[value], default="missing")
    return {
        "feature": feature,
        "taskId": task_id,
        "executionLane": lane,
        "explorationCaches": [{key: value for key, value in item.items() if not key.startswith("_")} for item in caches],
        "explorationPolicy": {"status": status, **POLICIES[status]},
    }


def _record(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspace: Path,
    expected_sha: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    with _exploration_lock(feature_dir):
        bundle, batch_id, lane = _bundle_for_task(feature_dir, task_id)
        repository_id, repository_root = _repository_for_command(code_workspace)
        current = _inspect_one(feature_dir, bundle, task_id, repository_root)
        if current["cacheSha256"] != expected_sha:
            raise CodeExplorationError("code_exploration_cache_sha_mismatch")
        if current["status"] not in {"missing", "stale"}:
            raise CodeExplorationError(f"code_exploration_cache_state_disallows_record:{current['status']}")
        findings = _validate_findings(body.get("findings"))
        explored = _string_paths(body.get("exploredPaths", []), "exploredPaths")
        shared = _string_paths(body.get("sharedPaths", []), "sharedPaths")
        snapshot = current["_currentSnapshot"]
        cache = _new_cache(
            feature=feature,
            bundle=bundle,
            batch_id=batch_id,
            task_id=task_id,
            lane=lane,
            repository_id=repository_id,
            repository_root=repository_root,
            snapshot=snapshot,
            findings=findings,
            explored_paths=explored,
            shared_paths=shared,
            coverage=_all_completed_coverage(bundle),
        )
        path = exploration_cache_path(feature_dir, repository_id, lane)
        atomic_write_json(path, cache)
        return {"status": "recorded", "cachePath": str(path), "cacheSha256": cache_sha256(cache)}


def _patch(
    workspace: Path,
    feature: str,
    task_id: str,
    code_workspace: Path,
    expected_sha: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    feature_dir = _feature_dir(workspace, feature)
    with _exploration_lock(feature_dir):
        bundle, batch_id, lane = _bundle_for_task(feature_dir, task_id)
        repository_id, repository_root = _repository_for_command(code_workspace)
        current = _inspect_one(feature_dir, bundle, task_id, repository_root)
        if current["cacheSha256"] != expected_sha:
            raise CodeExplorationError("code_exploration_cache_sha_mismatch")
        if current["status"] != "reusable_with_changes":
            raise CodeExplorationError(f"code_exploration_cache_state_disallows_patch:{current['status']}")
        reviewed = set(_string_paths(body.get("reviewedPaths", []), "reviewedPaths"))
        changed = set(current["changedPaths"])
        if not changed.issubset(reviewed):
            raise CodeExplorationError(
                "code_exploration_reviewed_paths_incomplete:" + ",".join(sorted(changed - reviewed))
            )
        old_cache = current["_cache"]
        findings = dict(old_cache["findings"])
        updates = body.get("findingUpdates", {})
        if not isinstance(updates, dict):
            raise CodeExplorationError("code_exploration_finding_updates_invalid")
        for field, value in updates.items():
            if field not in FINDING_FIELDS or not isinstance(value, list):
                raise CodeExplorationError(f"code_exploration_finding_updates_invalid:{field}")
            findings[field] = value
        findings = _validate_findings(findings)
        explored = sorted(set(old_cache["exploredPaths"]) | reviewed | set(_string_paths(body.get("exploredPathsAdd", []), "exploredPathsAdd")))
        shared = sorted(set(old_cache["sharedPaths"]) | set(_string_paths(body.get("sharedPathsAdd", []), "sharedPathsAdd")))
        coverage = old_cache["evidenceCoverage"]
        task_ids = sorted(set(coverage["explainedTaskIds"]) | set(current["matchedTaskIds"]))
        evidence_ids = sorted(set(coverage["completionEvidenceIds"]) | set(current["matchedEvidenceIds"]))
        cache = _new_cache(
            feature=feature,
            bundle=bundle,
            batch_id=batch_id,
            task_id=task_id,
            lane=lane,
            repository_id=repository_id,
            repository_root=repository_root,
            snapshot=current["_currentSnapshot"],
            findings=findings,
            explored_paths=explored,
            shared_paths=shared,
            coverage=(task_ids, evidence_ids, batch_id),
        )
        path = exploration_cache_path(feature_dir, repository_id, lane)
        atomic_write_json(path, cache)
        return {"status": "patched", "cachePath": str(path), "cacheSha256": cache_sha256(cache)}


def _contract() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "executionLanes": sorted(EXECUTION_LANES),
        "laneDerivation": {"uiRequired_false": "backend", "uiRequired_true": "frontend"},
        "statuses": sorted(CACHE_STATUSES),
        "policies": POLICIES,
        "criticalPathRules": {
            "basenames": sorted(CRITICAL_BASENAMES),
            "patterns": list(CRITICAL_GLOB_PATTERNS),
        },
        "invalidationRules": {
            "headCommitChanged": "reuse_when_file_snapshot_is_unchanged_or_trusted_evidence_matches",
            "headCommitChangedWithoutTrustedSnapshot": "stale",
            "criticalPathChanged": "stale_even_when_completion_evidence_explains_the_change",
        },
        "batchUpdateRules": {
            "sameBatch": "fresh_with_trusted_changes_without_patch",
            "sameBatchImplementationEvidence": "trusted_until_deferred_validation_finishes",
            "sameBatchImplementationStatuses": ["implemented", "validating", "failed", "repair_in_progress"],
            "newBatch": "reusable_with_changes_requires_targeted_patch",
            "newBatchWithoutFileChanges": "patch_metadata_to_advance_batch_baseline",
            "sensitivePaths": "shared_paths_and_integration_points_require_targeted_patch",
            "transientValidationFiles": "excluded_unless_formal_changed",
        },
        "runtimeIgnoreRequirements": {
            "paths": [".autobizdevops/", ".cmbdevclaw/"],
            "rule": "when_present_inside_a_business_repository_the_path_must_be_git_ignored_before_record",
        },
        "patchSemantics": {
            "findingUpdates": "full_category_replacement",
            "mergedFindingsValidated": True,
        },
        "recordFields": ["findings", "exploredPaths", "sharedPaths"],
        "patchFields": ["reviewedPaths", "findingUpdates", "exploredPathsAdd", "sharedPathsAdd"],
        "recordExample": {
            "findings": {
                "moduleMap": [
                    {
                        "path": "src",
                        "role": "application modules",
                        "dependsOn": [],
                        "ownerLane": "backend",
                        "sharedWithLanes": [],
                    }
                ],
                "conventions": [
                    {
                        "category": "error_handling",
                        "fact": "Domain errors use the existing application exception type.",
                        "evidencePaths": ["src/errors.py"],
                    }
                ],
                "integrationPoints": [
                    {
                        "kind": "http",
                        "path": "src/routes.py",
                        "symbol": "register_routes",
                        "purpose": "register public HTTP routes",
                        "sharedWithLanes": [],
                    }
                ],
                "testEntrypoints": [
                    {"cwd": ".", "argv": ["python", "-m", "unittest"], "scope": "backend unit tests"}
                ],
                "validationPatterns": [
                    {
                        "kind": "behavior_test",
                        "cwd": ".",
                        "argv": ["python", "-m", "unittest", "tests.test_service"],
                        "scope": "targeted service behavior",
                    }
                ],
            },
            "exploredPaths": ["src", "tests"],
            "sharedPaths": [],
        },
        "patchExample": {
            "reviewedPaths": ["src/service.py"],
            "findingUpdates": {},
            "exploredPathsAdd": [],
            "sharedPathsAdd": [],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage Code-stage exploration caches")
    subparsers = parser.add_subparsers(dest="command", required=True)

    contract = subparsers.add_parser("contract")
    contract.set_defaults(handler=lambda _args: _emit(True, **_contract()))

    def common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--workspace")
        subparser.add_argument("--feature")
        subparser.add_argument("--task-id", required=True)
        subparser.add_argument("--code-workspace", required=True, action="append")

    inspect = subparsers.add_parser("inspect")
    common(inspect)

    record = subparsers.add_parser("record")
    common(record)
    record.add_argument("--expected-cache-sha256", required=True)
    record.add_argument("--body-file", required=True)

    patch = subparsers.add_parser("patch")
    common(patch)
    patch.add_argument("--expected-cache-sha256", required=True)
    patch.add_argument("--body-file", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "contract":
            return args.handler(args)
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        code_workspaces = [Path(item).expanduser().resolve() for item in args.code_workspace]
        if args.command == "inspect":
            return _emit(True, **inspect_caches(workspace, feature, args.task_id, code_workspaces))
        if len(code_workspaces) != 1:
            raise CodeExplorationError("code_exploration_record_requires_one_repository")
        body = _load_body(Path(args.body_file).expanduser().resolve())
        if args.command == "record":
            return _emit(True, **_record(workspace, feature, args.task_id, code_workspaces[0], args.expected_cache_sha256, body))
        return _emit(True, **_patch(workspace, feature, args.task_id, code_workspaces[0], args.expected_cache_sha256, body))
    except (CodeExplorationError, WriterError, ValueError) as exc:
        return _emit(False, error=str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
