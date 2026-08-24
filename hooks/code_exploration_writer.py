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
    finding_validation_issues,
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
    REQUIRED_IGNORED_RUNTIME_PATHS,
    RepositorySnapshotError,
    resolve_repositories,
)


CONTRACT_COMMAND = f'{sys.executable} "{Path(__file__).resolve()}" contract'


class CodeExplorationInputError(CodeExplorationError):
    def __init__(
        self,
        code: str,
        *,
        required_action: str,
        issues: list[dict[str, Any]] | None = None,
        **details: Any,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.required_action = required_action
        self.issues = issues or []
        self.details = details

    def payload(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "requiredAction": self.required_action,
            "issues": self.issues,
            "contractCommand": CONTRACT_COMMAND,
            **self.details,
        }


class ContractArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "code_exploration_cli_arguments_invalid",
                    "requiredAction": "repair_cli_arguments",
                    "issues": [{"path": "argv", "code": "invalid_arguments", "detail": message}],
                    "contractCommand": CONTRACT_COMMAND,
                    "usage": self.format_usage().strip(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        self.exit(2)


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
    active_batch = bundle.root.get("activeBatchId")
    if active_batch is not None and active_batch != batch_id:
        raise CodeExplorationError(f"task_not_in_active_batch:{task_id}")
    if active_batch is None:
        parallel_candidates = [
            str(entry.get("id"))
            for entry in bundle.root.get("batches", [])
            if isinstance(entry, dict)
            and entry.get("status") not in {"done", "failed"}
            and task_id in (entry.get("taskIds") or [])
        ]
        if len(parallel_candidates) != 1:
            raise CodeExplorationError(f"task_not_in_active_batch:{task_id}")
    batch = bundle.batches.get(batch_id)
    lane = batch.get("executionLane") if isinstance(batch, dict) else None
    if lane not in EXECUTION_LANES:
        raise CodeExplorationError(f"active_batch_execution_lane_invalid:{batch_id}")
    return bundle, batch_id, lane


def _load_body_text(text: str, source: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CodeExplorationInputError(
            "code_exploration_body_invalid",
            required_action="repair_record_body_json",
            issues=[
                {
                    "path": "$",
                    "code": "invalid_json",
                    "expected": "json_object",
                    "source": source,
                    "line": exc.lineno,
                    "column": exc.colno,
                }
            ],
        ) from exc
    if not isinstance(value, dict):
        raise CodeExplorationInputError(
            "code_exploration_body_root_invalid",
            required_action="repair_record_body",
            issues=[{"path": "$", "code": "object_required", "expected": "object", "source": source}],
        )
    return value


def _load_body_file(path: Path) -> dict[str, Any]:
    try:
        return _load_body_text(path.read_text(encoding="utf-8"), str(path))
    except OSError as exc:
        raise CodeExplorationInputError(
            "code_exploration_body_unreadable",
            required_action="repair_body_file_path",
            issues=[{"path": str(path), "code": "file_unreadable", "expected": "readable_json_file"}],
        ) from exc


def _load_body_from_args(args: argparse.Namespace) -> tuple[dict[str, Any], Path | None]:
    if bool(getattr(args, "body_stdin", False)):
        return _load_body_text(sys.stdin.read(), "stdin"), None
    path = Path(args.body_file).expanduser().resolve()
    return _load_body_file(path), path


def _path_validation_issues(value: Any, field: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return [{"path": field, "code": "required_array", "expected": "string_array"}]
    issues: list[dict[str, str]] = []
    for index, item in enumerate(value):
        path = f"{field}[{index}]"
        if not isinstance(item, str) or not item.strip():
            issues.append({"path": path, "code": "required_non_empty_string", "expected": "non_empty_string"})
            continue
        normalized = item.replace("\\", "/").strip()
        if Path(normalized).is_absolute() or ".." in Path(normalized).parts:
            issues.append(
                {
                    "path": path,
                    "code": "unsafe_relative_path",
                    "expected": "safe_repository_relative_path",
                }
            )
    return issues


def _validate_findings(findings: Any) -> dict[str, list[Any]]:
    errors = validate_findings(findings)
    if errors:
        raise CodeExplorationInputError(
            "code_exploration_findings_invalid",
            required_action="repair_record_body",
            issues=finding_validation_issues(findings),
            legacyErrors=errors,
            recordExample=_contract()["recordExample"],
        )
    return {field: list(findings[field]) for field in FINDING_FIELDS}


def _string_paths(value: Any, field: str) -> list[str]:
    issues = _path_validation_issues(value, field)
    if issues:
        raise CodeExplorationInputError(
            "code_exploration_paths_invalid",
            required_action="repair_record_body",
            issues=issues,
            field=field,
        )
    paths = [item.replace("\\", "/").strip() for item in value]
    return sorted(set(paths))


def _validate_record_body(body: dict[str, Any]) -> tuple[dict[str, list[Any]], list[str], list[str]]:
    issues = finding_validation_issues(body.get("findings"))
    issues.extend(_path_validation_issues(body.get("exploredPaths", []), "exploredPaths"))
    issues.extend(_path_validation_issues(body.get("sharedPaths", []), "sharedPaths"))
    if issues:
        raise CodeExplorationInputError(
            "code_exploration_record_body_invalid",
            required_action="repair_record_body",
            issues=issues,
            recordExample=_contract()["recordExample"],
        )
    findings = {field: list(body["findings"][field]) for field in FINDING_FIELDS}
    explored = sorted({item.replace("\\", "/").strip() for item in body.get("exploredPaths", [])})
    shared = sorted({item.replace("\\", "/").strip() for item in body.get("sharedPaths", [])})
    return findings, explored, shared


def _assert_body_source_outside_repository(body_path: Path | None, code_workspace: Path) -> None:
    if body_path is None:
        return
    _repository_id, repository_root = _repository_for_command(code_workspace)
    try:
        relative = body_path.relative_to(repository_root)
    except ValueError:
        return
    raise CodeExplorationInputError(
        "code_exploration_body_file_inside_repository",
        required_action="use_body_stdin_or_external_temp_file",
        issues=[
            {
                "path": str(body_path),
                "code": "snapshot_polluting_body_file",
                "expected": "stdin_or_file_outside_business_repository",
                "repositoryRelativePath": relative.as_posix(),
            }
        ],
    )


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
        findings, explored, shared = _validate_record_body(body)
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


def _relative_path_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "x-autodev-relativePath": True,
        "description": "Non-empty repository-relative path without '..' traversal.",
    }


def _string_array_schema(*, min_items: int = 0) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "minItems": min_items,
    }


def _finding_item_schema(field: str) -> dict[str, Any]:
    schemas: dict[str, dict[str, Any]] = {
        "moduleMap": {
            "required": ["path", "role"],
            "properties": {
                "path": _relative_path_schema(),
                "role": {"type": "string", "minLength": 1},
                "dependsOn": {"type": "array", "items": _relative_path_schema()},
                "ownerLane": {"enum": sorted(EXECUTION_LANES)},
                "sharedWithLanes": {"type": "array", "items": {"enum": sorted(EXECUTION_LANES)}},
            },
        },
        "conventions": {
            "required": ["category", "fact"],
            "properties": {
                "category": {"type": "string", "minLength": 1},
                "fact": {"type": "string", "minLength": 1},
                "evidencePaths": {"type": "array", "items": _relative_path_schema()},
            },
        },
        "integrationPoints": {
            "required": ["kind", "path", "purpose"],
            "properties": {
                "kind": {"type": "string", "minLength": 1},
                "path": _relative_path_schema(),
                "symbol": {"type": "string"},
                "purpose": {"type": "string", "minLength": 1},
                "sharedWithLanes": {"type": "array", "items": {"enum": sorted(EXECUTION_LANES)}},
            },
        },
        "testEntrypoints": {
            "required": ["cwd", "argv", "scope"],
            "properties": {
                "cwd": _relative_path_schema(),
                "argv": _string_array_schema(min_items=1),
                "scope": {"type": "string", "minLength": 1},
            },
        },
        "validationPatterns": {
            "required": ["kind", "cwd", "argv", "scope"],
            "properties": {
                "kind": {"type": "string", "minLength": 1},
                "cwd": _relative_path_schema(),
                "argv": _string_array_schema(min_items=1),
                "scope": {"type": "string", "minLength": 1},
            },
        },
    }
    schema = schemas[field]
    return {"type": "object", "additionalProperties": True, **schema}


def _findings_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": list(FINDING_FIELDS),
        "additionalProperties": True,
        "properties": {
            field: {"type": "array", "items": _finding_item_schema(field)}
            for field in FINDING_FIELDS
        },
    }


def _record_body_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["findings"],
        "additionalProperties": True,
        "properties": {
            "findings": _findings_schema(),
            "exploredPaths": {"type": "array", "items": _relative_path_schema(), "default": []},
            "sharedPaths": {"type": "array", "items": _relative_path_schema(), "default": []},
        },
    }


def _patch_body_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["reviewedPaths", "findingUpdates"],
        "additionalProperties": True,
        "properties": {
            "reviewedPaths": {"type": "array", "items": _relative_path_schema()},
            "findingUpdates": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    field: {"type": "array", "items": _finding_item_schema(field)}
                    for field in FINDING_FIELDS
                },
            },
            "exploredPathsAdd": {"type": "array", "items": _relative_path_schema(), "default": []},
            "sharedPathsAdd": {"type": "array", "items": _relative_path_schema(), "default": []},
        },
    }


def _contract(section: str = "full") -> dict[str, Any]:
    contract = {
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
            "requiredPaths": list(REQUIRED_IGNORED_RUNTIME_PATHS),
            "rule": "when_present_inside_a_business_repository_the_path_must_be_git_ignored_before_record",
        },
        "patchSemantics": {
            "findingUpdates": "full_category_replacement",
            "mergedFindingsValidated": True,
        },
        "recordFields": ["findings", "exploredPaths", "sharedPaths"],
        "patchFields": ["reviewedPaths", "findingUpdates", "exploredPathsAdd", "sharedPathsAdd"],
        "recordBodySchema": _record_body_schema(),
        "patchBodySchema": _patch_body_schema(),
        "bodyInput": {
            "preferred": "--body-stdin",
            "alternatives": ["--body-file <path-outside-business-repository>"],
            "repositoryBodyFilesRejected": True,
        },
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
    if section == "record":
        return {
            "schemaVersion": SCHEMA_VERSION,
            "section": "record",
            "runtimeIgnoreRequirements": contract["runtimeIgnoreRequirements"],
            "recordFields": contract["recordFields"],
            "recordBodySchema": contract["recordBodySchema"],
            "bodyInput": contract["bodyInput"],
            "recordExample": contract["recordExample"],
        }
    if section == "patch":
        return {
            "schemaVersion": SCHEMA_VERSION,
            "section": "patch",
            "runtimeIgnoreRequirements": contract["runtimeIgnoreRequirements"],
            "patchSemantics": contract["patchSemantics"],
            "patchFields": contract["patchFields"],
            "patchBodySchema": contract["patchBodySchema"],
            "bodyInput": contract["bodyInput"],
            "patchExample": contract["patchExample"],
        }
    return contract


def main(argv: list[str] | None = None) -> int:
    parser = ContractArgumentParser(
        description="Manage Code-stage exploration caches",
        epilog=(
            "Run the 'contract' command before authoring a record or patch body. "
            "Prefer --body-stdin so the input document cannot enter the business repository snapshot."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    contract = subparsers.add_parser("contract", help="Print schemas, examples, and cache policies")
    contract.add_argument(
        "--section",
        choices=("full", "record", "patch"),
        default="full",
        help="Limit output to the body contract needed for record or patch",
    )
    contract.set_defaults(handler=lambda contract_args: _emit(True, **_contract(contract_args.section)))

    def common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--workspace", help="Artifact workspace containing .autobizdevops/state.json")
        subparser.add_argument("--feature", help="Feature ID")
        subparser.add_argument("--task-id", required=True, help="Task ID from the active batch")
        subparser.add_argument(
            "--code-workspace",
            required=True,
            action="append",
            help="Business repository or module path; repeat only for inspect",
        )

    def body_source(subparser: argparse.ArgumentParser) -> None:
        sources = subparser.add_mutually_exclusive_group(required=True)
        sources.add_argument(
            "--body-file",
            help="JSON body outside the business repository; use --body-stdin for generated input",
        )
        sources.add_argument(
            "--body-stdin",
            action="store_true",
            help="Read the JSON body from stdin (preferred)",
        )

    inspect = subparsers.add_parser("inspect")
    common(inspect)

    record = subparsers.add_parser("record")
    common(record)
    record.add_argument("--expected-cache-sha256", required=True)
    body_source(record)

    patch = subparsers.add_parser("patch")
    common(patch)
    patch.add_argument("--expected-cache-sha256", required=True)
    body_source(patch)

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
        body, body_path = _load_body_from_args(args)
        _assert_body_source_outside_repository(body_path, code_workspaces[0])
        if args.command == "record":
            return _emit(True, **_record(workspace, feature, args.task_id, code_workspaces[0], args.expected_cache_sha256, body))
        return _emit(True, **_patch(workspace, feature, args.task_id, code_workspaces[0], args.expected_cache_sha256, body))
    except CodeExplorationInputError as exc:
        return _emit(False, **exc.payload())
    except (CodeExplorationError, WriterError, ValueError) as exc:
        return _emit(False, error=str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
