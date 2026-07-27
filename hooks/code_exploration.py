#!/usr/bin/env python3
"""Machine facts and inspection rules for Code-stage repository exploration."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hooks.evidence_store import read_records, stream_path, validate_record
from hooks.plan_json import EXECUTION_LANES, PlanBundle, normalize_status
from hooks.repository_snapshot import capture_repository_snapshot, snapshot_changes


SCHEMA_VERSION = "autodev.code-exploration.v1"
CACHE_STATUSES = {"missing", "fresh", "fresh_with_trusted_changes", "reusable_with_changes", "stale"}
POLICIES: dict[str, dict[str, Any]] = {
    "missing": {
        "explorationPolicy": "full_bounded_explore",
        "requiresRecord": True,
        "requiresPatch": False,
    },
    "stale": {
        "explorationPolicy": "full_bounded_explore",
        "requiresRecord": True,
        "requiresPatch": False,
    },
    "fresh": {
        "explorationPolicy": "task_scope_only",
        "requiresRecord": False,
        "requiresPatch": False,
    },
    "fresh_with_trusted_changes": {
        "explorationPolicy": "task_scope_only",
        "requiresRecord": False,
        "requiresPatch": False,
        "deferredCacheUpdate": True,
    },
    "reusable_with_changes": {
        "explorationPolicy": "targeted_reread",
        "requiresRecord": False,
        "requiresPatch": True,
    },
}
POLICY_PRIORITY = {
    "fresh": 0,
    "fresh_with_trusted_changes": 1,
    "reusable_with_changes": 2,
    "missing": 3,
    "stale": 4,
}
REPOSITORY_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
FINDING_FIELDS = (
    "moduleMap",
    "conventions",
    "integrationPoints",
    "testEntrypoints",
    "validationPatterns",
)
CRITICAL_BASENAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "poetry.lock",
    "requirements.txt",
    "pipfile",
    "pipfile.lock",
    "go.mod",
    "go.sum",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "gradle.properties",
    "cargo.toml",
    "cargo.lock",
    "gemfile",
    "gemfile.lock",
    "composer.json",
    "composer.lock",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "jenkinsfile",
    ".gitlab-ci.yml",
    "vite.config.js",
    "vite.config.ts",
    "webpack.config.js",
    "webpack.config.ts",
    "rollup.config.js",
    "rollup.config.ts",
    "next.config.js",
    "next.config.ts",
    "nuxt.config.ts",
    "board_config.json",
}
CRITICAL_GLOB_PATTERNS = (
    ".github/workflows/*",
    "**/migrations/*",
    "**/migration/*",
    "**/db/migration/*",
    "**/*.proto",
)


class CodeExplorationError(ValueError):
    pass


@dataclass(frozen=True)
class TrustedEvolution:
    changed_paths: frozenset[str]
    latest_files: dict[str, str | None] | None
    task_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    untrusted_reasons: tuple[str, ...]
    transient_paths: frozenset[str] = frozenset()
    implementation_task_ids: tuple[str, ...] = ()
    implementation_evidence_ids: tuple[str, ...] = ()

    @classmethod
    def empty(cls) -> "TrustedEvolution":
        return cls(frozenset(), None, (), (), ())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def exploration_cache_path(feature_dir: Path, repository_id: str, lane: str) -> Path:
    if not REPOSITORY_ID_RE.fullmatch(repository_id):
        raise CodeExplorationError(f"invalid_repository_id:{repository_id}")
    if lane not in EXECUTION_LANES:
        raise CodeExplorationError(f"invalid_execution_lane:{lane}")
    return feature_dir / "cache" / "code-exploration" / repository_id / f"{lane}.json"


def cache_sha256(data: dict[str, Any]) -> str:
    content = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def is_critical_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    basename = Path(normalized).name.lower()
    if basename in CRITICAL_BASENAMES:
        return True
    if basename.startswith("tsconfig") and basename.endswith(".json"):
        return True
    if basename.startswith("openapi") or basename.startswith("schema.graphql"):
        return True
    if basename.startswith(".env"):
        return True
    if any(part.lower() in {"migrations", "migration", "schema"} for part in Path(normalized).parts):
        return True
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in CRITICAL_GLOB_PATTERNS)


def _safe_relative_path(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not Path(value).is_absolute()
        and ".." not in Path(value).parts
    )


def validate_findings(findings: Any) -> list[str]:
    if not isinstance(findings, dict):
        return ["findings_missing"]
    errors: list[str] = []
    required_strings = {
        "moduleMap": ("path", "role"),
        "conventions": ("category", "fact"),
        "integrationPoints": ("kind", "path", "purpose"),
        "testEntrypoints": ("cwd", "scope"),
        "validationPatterns": ("kind", "cwd", "scope"),
    }
    for field in FINDING_FIELDS:
        values = findings.get(field)
        if not isinstance(values, list):
            errors.append(f"findings_{field}_invalid")
            continue
        for index, item in enumerate(values):
            context = f"findings_{field}[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{context}_must_be_object")
                continue
            for name in required_strings[field]:
                if not isinstance(item.get(name), str) or not item[name].strip():
                    errors.append(f"{context}_{name}_missing")
            for name in ("path", "cwd"):
                if name in item and not _safe_relative_path(item.get(name)):
                    errors.append(f"{context}_{name}_unsafe")
            for name in ("dependsOn", "evidencePaths", "sharedWithLanes"):
                if name in item and (
                    not isinstance(item[name], list)
                    or not all(isinstance(value, str) and value.strip() for value in item[name])
                ):
                    errors.append(f"{context}_{name}_invalid")
            for name in ("evidencePaths", "dependsOn"):
                if isinstance(item.get(name), list) and any(not _safe_relative_path(value) for value in item[name]):
                    errors.append(f"{context}_{name}_unsafe")
            if isinstance(item.get("sharedWithLanes"), list) and any(
                value not in EXECUTION_LANES for value in item["sharedWithLanes"]
            ):
                errors.append(f"{context}_sharedWithLanes_invalid")
            if "ownerLane" in item and item.get("ownerLane") not in EXECUTION_LANES:
                errors.append(f"{context}_ownerLane_invalid")
            if field in {"testEntrypoints", "validationPatterns"}:
                argv = item.get("argv")
                if not isinstance(argv, list) or not argv or not all(isinstance(value, str) for value in argv):
                    errors.append(f"{context}_argv_invalid")
                if "command" in item:
                    errors.append(f"{context}_command_string_forbidden")
    return errors


def validate_cache(
    data: dict[str, Any],
    *,
    feature: str,
    repository_id: str,
    repository_root: Path,
    lane: str,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["root_must_be_object"]
    if data.get("schemaVersion") != SCHEMA_VERSION:
        errors.append("schema_version_invalid")
    if data.get("featureId") != feature:
        errors.append("feature_id_mismatch")
    if data.get("executionLane") != lane:
        errors.append("execution_lane_mismatch")
    repository = data.get("repository")
    if not isinstance(repository, dict):
        errors.append("repository_missing")
    else:
        if repository.get("id") != repository_id:
            errors.append("repository_id_mismatch")
        if repository.get("root") != str(repository_root):
            errors.append("repository_root_mismatch")
    snapshot = data.get("gitSnapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("files"), dict):
        errors.append("git_snapshot_invalid")
    errors.extend(validate_findings(data.get("findings")))
    for field in ("exploredPaths", "sharedPaths"):
        values = data.get(field)
        if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
            errors.append(f"{field}_invalid")
        elif any(Path(item).is_absolute() or ".." in Path(item).parts for item in values):
            errors.append(f"{field}_unsafe")
    coverage = data.get("evidenceCoverage")
    if not isinstance(coverage, dict):
        errors.append("evidence_coverage_missing")
    else:
        for field in ("explainedTaskIds", "completionEvidenceIds"):
            if not isinstance(coverage.get(field), list) or not all(isinstance(item, str) for item in coverage[field]):
                errors.append(f"evidence_coverage_{field}_invalid")
    return errors


def _result(
    status: str,
    *,
    changed_paths: list[str] | None = None,
    critical_hits: list[str] | None = None,
    unexplained_paths: list[str] | None = None,
    trusted: TrustedEvolution | None = None,
    stale_reasons: list[str] | None = None,
) -> dict[str, Any]:
    evolution = trusted or TrustedEvolution.empty()
    return {
        "status": status,
        "policy": dict(POLICIES[status]),
        "changedPaths": sorted(changed_paths or []),
        "criticalHits": sorted(critical_hits or []),
        "unexplainedPaths": sorted(unexplained_paths or []),
        "matchedTaskIds": list(evolution.task_ids),
        "matchedEvidenceIds": list(evolution.evidence_ids),
        "matchedImplementationTaskIds": list(evolution.implementation_task_ids),
        "matchedImplementationEvidenceIds": list(evolution.implementation_evidence_ids),
        "untrustedReasons": list(evolution.untrusted_reasons),
        "staleReasons": list(stale_reasons or []),
    }


def classify_cache(
    cache: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
    trusted: TrustedEvolution,
    *,
    current_batch_id: str | None = None,
) -> dict[str, Any]:
    if cache is None:
        return _result("missing")
    cached_snapshot = cache.get("gitSnapshot") if isinstance(cache, dict) else None
    if not isinstance(cached_snapshot, dict) or not isinstance(cached_snapshot.get("files"), dict):
        return _result("stale", stale_reasons=["git_snapshot_invalid"])
    head_commit_changed = cached_snapshot.get("headCommit") != current_snapshot.get("headCommit")
    before = cached_snapshot["files"]
    after = current_snapshot.get("files")
    if not isinstance(after, dict):
        return _result("stale", stale_reasons=["current_git_snapshot_invalid"])
    changes = snapshot_changes(before, after)
    changed_paths = {
        item.get("path")
        for item in changes
        if isinstance(item.get("path"), str)
    } | {
        item.get("fromPath")
        for item in changes
        if isinstance(item.get("fromPath"), str)
    }
    changed_paths.difference_update(trusted.transient_paths - trusted.changed_paths)
    changed_paths = sorted(changed_paths)
    if not changed_paths:
        stale_reasons = list(trusted.untrusted_reasons)
        if trusted.latest_files is not None and trusted.latest_files != after:
            stale_reasons.append("current_snapshot_not_latest_task_snapshot")
            if head_commit_changed:
                stale_reasons.append("head_commit_changed_without_trusted_snapshot")
        if stale_reasons:
            return _result(
                "stale",
                trusted=trusted,
                stale_reasons=stale_reasons,
            )
        if current_batch_id is not None and cache.get("capturedBatchId") != current_batch_id:
            return _result("reusable_with_changes", trusted=trusted)
        return _result("fresh", trusted=trusted)
    critical_hits = [path for path in changed_paths if is_critical_path(path)]
    if critical_hits:
        return _result(
            "stale",
            changed_paths=changed_paths,
            critical_hits=critical_hits,
            trusted=trusted,
            stale_reasons=["critical_path_changed"],
        )
    unexplained = sorted(set(changed_paths) - set(trusted.changed_paths))
    stale_reasons = list(trusted.untrusted_reasons)
    if unexplained:
        stale_reasons.append("unexplained_paths")
    if trusted.latest_files != after:
        stale_reasons.append("current_snapshot_not_latest_task_snapshot")
        if head_commit_changed:
            stale_reasons.append("head_commit_changed_without_trusted_snapshot")
    if stale_reasons:
        return _result(
            "stale",
            changed_paths=changed_paths,
            critical_hits=critical_hits,
            unexplained_paths=unexplained,
            trusted=trusted,
            stale_reasons=stale_reasons,
        )
    sensitive_paths = _cache_sensitive_paths(cache)
    sensitive_change = any(
        _path_matches_scope(path, sensitive)
        for path in trusted.changed_paths
        for sensitive in sensitive_paths
    )
    if (
        current_batch_id is not None
        and cache.get("capturedBatchId") == current_batch_id
        and not sensitive_change
    ):
        return _result(
            "fresh_with_trusted_changes",
            changed_paths=changed_paths,
            critical_hits=critical_hits,
            unexplained_paths=unexplained,
            trusted=trusted,
        )
    return _result(
        "reusable_with_changes",
        changed_paths=changed_paths,
        critical_hits=critical_hits,
        unexplained_paths=unexplained,
        trusted=trusted,
    )


def _path_matches_scope(path: str, scope: str) -> bool:
    candidate = Path(path.replace("\\", "/"))
    normalized_scope = Path(scope.replace("\\", "/").rstrip("/"))
    return candidate == normalized_scope or normalized_scope in candidate.parents


def _cache_sensitive_paths(cache: dict[str, Any]) -> set[str]:
    paths = {
        item
        for item in cache.get("sharedPaths", [])
        if isinstance(item, str) and item.strip()
    }
    findings = cache.get("findings")
    integration_points = findings.get("integrationPoints", []) if isinstance(findings, dict) else []
    paths.update(
        item.get("path")
        for item in integration_points
        if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"].strip()
    )
    return paths


def _read_run(feature_dir: Path, task_id: str, run_id: str) -> dict[str, Any] | None:
    path = feature_dir / ".task-runs" / task_id / f"{run_id}.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _evidence_by_id(
    feature_dir: Path,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, int],
    dict[str, tuple[str, ...]],
    list[str],
]:
    try:
        records = read_records(stream_path(feature_dir))
    except (OSError, ValueError):
        return {}, {}, {}, ["evidence_stream_unreadable"]
    result: dict[str, dict[str, Any]] = {}
    order: dict[str, int] = {}
    errors: dict[str, tuple[str, ...]] = {}
    for sequence, record in enumerate(records):
        evidence_id = record.get("evidenceId")
        if not isinstance(evidence_id, str):
            continue
        result[evidence_id] = record
        order[evidence_id] = sequence
        record_errors = tuple(validate_record(record))
        if record_errors:
            errors[evidence_id] = record_errors
    return result, order, errors, []


def _record_paths_for_repository(
    record: dict[str, Any],
    *,
    repository_id: str,
    repository_count: int,
    context: str,
) -> tuple[set[str], set[str], list[str]]:
    errors: list[str] = []
    changed: set[str] = set()
    transient: set[str] = set()

    def project(raw_paths: Any, field: str, target: set[str]) -> None:
        if raw_paths is None and field == "transientValidationFiles":
            raw_paths = []
        if not isinstance(raw_paths, list) or not all(isinstance(item, str) for item in raw_paths):
            errors.append(f"{context}_{field}_invalid")
            return
        prefix = f"{repository_id}:"
        for raw_path in raw_paths:
            if raw_path.startswith(prefix):
                target.add(raw_path[len(prefix):])
            elif repository_count == 1:
                target.add(raw_path)
            else:
                errors.append(f"{context}_repository_prefix_missing:{raw_path}")

    project(record.get("changedFiles"), "changedFiles", changed)
    project(record.get("transientValidationFiles", []), "transientValidationFiles", transient)
    return changed, transient, errors


def collect_trusted_evolution(
    feature_dir: Path,
    bundle: PlanBundle,
    cache: dict[str, Any] | None,
    repository_id: str,
    *,
    current_batch_id: str | None = None,
) -> TrustedEvolution:
    coverage = cache.get("evidenceCoverage", {}) if isinstance(cache, dict) else {}
    covered_evidence = set(coverage.get("completionEvidenceIds", [])) if isinstance(coverage, dict) else set()
    evidence_by_id, evidence_order, evidence_errors, stream_errors = _evidence_by_id(feature_dir)
    changed_paths: set[str] = set()
    transient_paths: set[str] = set()
    task_ids: list[str] = []
    evidence_ids: list[str] = []
    implementation_task_ids: list[str] = []
    implementation_evidence_ids: list[str] = []
    untrusted = list(stream_errors)
    # The append-only evidence stream records completion order independently of plan order.
    latest_files: dict[str, str | None] | None = None
    latest_completion_order = -1
    for task in bundle.tasks:
        task_status = normalize_status(task.get("status"))
        task_id = str(task.get("id"))
        task_batch_id = bundle.task_batches.get(task.get("id"))
        evidence_id = task.get("latestImplementationEvidenceId")
        implementation_state_expected = task_status in {"implemented", "validating", "failed"}
        repair_in_progress = task_status == "in_progress" and isinstance(evidence_id, str)
        if (
            (implementation_state_expected or repair_in_progress)
            and current_batch_id is not None
            and task_batch_id == current_batch_id
            and bundle.root.get("activeBatchId") == current_batch_id
        ):
            record = evidence_by_id.get(evidence_id) if isinstance(evidence_id, str) else None
            if (
                not isinstance(record, dict)
                or record.get("action") != "implementation"
                or record.get("taskId") != task.get("id")
                or not isinstance(record.get("runId"), str)
                or record.get("evidenceId") != evidence_id
            ):
                untrusted.append(f"implementation_evidence_invalid:{task_id}:{evidence_id}")
                continue
            if evidence_errors.get(evidence_id):
                untrusted.extend(
                    f"{evidence_id}:{error}" for error in evidence_errors[evidence_id]
                )
                continue
            run_id = str(record["runId"])
            run = _read_run(feature_dir, task_id, run_id)
            repositories = run.get("finalRepositories") if isinstance(run, dict) else None
            matching = next(
                (
                    item
                    for item in repositories or []
                    if isinstance(item, dict) and item.get("id") == repository_id
                ),
                None,
            )
            if isinstance(run, dict) and isinstance(repositories, list) and matching is None:
                # The task belongs to another business repository and cannot
                # explain changes in this cache.
                continue
            if (
                not isinstance(run, dict)
                or run.get("status") != "implemented"
                or run.get("success") is not True
                or not isinstance(matching, dict)
                or not isinstance(matching.get("snapshot"), dict)
            ):
                untrusted.append(f"implementation_run_state_invalid:{task_id}:{run_id}")
                continue
            repository_count = len(
                [item for item in repositories if isinstance(item, dict)]
            )
            task_changed_paths, task_transient_paths, path_errors = _record_paths_for_repository(
                record,
                repository_id=repository_id,
                repository_count=repository_count,
                context=f"implementation:{evidence_id}",
            )
            if path_errors:
                untrusted.extend(path_errors)
                continue
            changed_paths.update(task_changed_paths)
            transient_paths.update(task_transient_paths)
            implementation_task_ids.append(task_id)
            implementation_evidence_ids.append(evidence_id)
            completion_order = evidence_order.get(evidence_id, -1)
            if completion_order > latest_completion_order:
                latest_files = matching["snapshot"]
                latest_completion_order = completion_order
            continue
        if task_status != "done":
            continue
        task_evidence = [item for item in task.get("completionEvidenceIds", []) if isinstance(item, str)]
        new_ids = [item for item in task_evidence if item not in covered_evidence]
        if not new_ids:
            continue
        run_ids: set[str] = set()
        run_completion_order: dict[str, int] = {}
        task_records: list[tuple[str, dict[str, Any]]] = []
        task_valid = True
        for evidence_id in new_ids:
            record = evidence_by_id.get(evidence_id)
            if record is None:
                untrusted.append(f"evidence_missing:{evidence_id}")
                task_valid = False
                continue
            validation = record.get("validation")
            if (
                record.get("action") != "validation"
                or record.get("detailVersion") != 2
                or not isinstance(validation, dict)
                or validation.get("required") is not True
                or validation.get("result") != "pass"
                or record.get("taskId") != task.get("id")
                or not isinstance(record.get("runId"), str)
            ):
                untrusted.append(f"evidence_invalid:{evidence_id}")
                task_valid = False
                continue
            if evidence_errors.get(evidence_id):
                untrusted.extend(
                    f"{evidence_id}:{error}" for error in evidence_errors[evidence_id]
                )
                task_valid = False
                continue
            run_id = record["runId"]
            run_ids.add(run_id)
            run_completion_order[run_id] = max(
                run_completion_order.get(run_id, -1),
                evidence_order.get(evidence_id, -1),
            )
            task_records.append((evidence_id, record))
        task_latest_files: dict[str, str | None] | None = None
        task_completion_order = -1
        repository_count_by_run: dict[str, int] = {}
        task_targets_repository = False
        for run_id in sorted(run_ids, key=lambda item: (run_completion_order.get(item, -1), item)):
            run = _read_run(feature_dir, str(task.get("id")), run_id)
            repositories = run.get("finalRepositories") if isinstance(run, dict) else None
            matching = next(
                (item for item in repositories or [] if isinstance(item, dict) and item.get("id") == repository_id),
                None,
            )
            if isinstance(run, dict) and isinstance(repositories, list) and matching is None:
                continue
            if (
                not isinstance(run, dict)
                or run.get("status") != "done"
                or run.get("success") is not True
                or not isinstance(repositories, list)
                or not isinstance(matching, dict)
            ):
                untrusted.append(f"run_state_invalid:{task.get('id')}:{run_id}")
                task_valid = False
                continue
            task_targets_repository = True
            repository_count_by_run[run_id] = len(
                [item for item in repositories if isinstance(item, dict)]
            )
            snapshot = matching.get("snapshot")
            if not isinstance(snapshot, dict):
                untrusted.append(f"run_snapshot_invalid:{task.get('id')}:{run_id}")
                task_valid = False
                continue
            run_order = run_completion_order.get(run_id, -1)
            if run_order >= task_completion_order:
                task_latest_files = snapshot
                task_completion_order = run_order
        if task_valid and not task_targets_repository:
            continue
        task_changed_paths: set[str] = set()
        task_transient_paths: set[str] = set()
        for evidence_id, record in task_records:
            record_changed, record_transient, path_errors = _record_paths_for_repository(
                record,
                repository_id=repository_id,
                repository_count=repository_count_by_run.get(str(record.get("runId")), 0),
                context=f"validation:{evidence_id}",
            )
            if path_errors:
                untrusted.extend(path_errors)
                task_valid = False
                continue
            task_changed_paths.update(record_changed)
            task_transient_paths.update(record_transient)
        if task_valid:
            changed_paths.update(task_changed_paths)
            transient_paths.update(task_transient_paths)
            task_ids.append(str(task.get("id")))
            evidence_ids.extend(evidence_id for evidence_id, _ in task_records)
            if task_latest_files is not None and task_completion_order > latest_completion_order:
                latest_files = task_latest_files
                latest_completion_order = task_completion_order

    for batch_id, batch in bundle.batches.items():
        validation = batch.get("batchValidation") if isinstance(batch, dict) else None
        latest_pass_ids = (
            [item for item in validation.get("latestPassEvidenceIds", []) if isinstance(item, str)]
            if isinstance(validation, dict)
            else []
        )
        new_ids = [item for item in latest_pass_ids if item not in covered_evidence]
        if not new_ids:
            continue
        mode = validation.get("mode", "commands" if validation.get("commands") else None)
        if mode == "task_covered":
            for evidence_id in new_ids:
                record = evidence_by_id.get(evidence_id)
                coverage = record.get("coverage") if isinstance(record, dict) else None
                if (
                    not isinstance(record, dict)
                    or record.get("action") != "batch_closure"
                    or record.get("batchId") != batch_id
                    or record.get("taskId") != "__batch__"
                    or not isinstance(coverage, dict)
                    or coverage.get("mode") != "task_covered"
                    or coverage.get("result") != "pass"
                ):
                    untrusted.append(f"batch_closure_evidence_invalid:{evidence_id}")
                    continue
                if evidence_errors.get(evidence_id):
                    untrusted.extend(
                        f"{evidence_id}:{error}" for error in evidence_errors[evidence_id]
                    )
                    continue
                evidence_ids.append(evidence_id)
            continue
        batch_valid = True
        run_ids: set[str] = set()
        batch_records: list[tuple[str, dict[str, Any]]] = []
        for evidence_id in new_ids:
            record = evidence_by_id.get(evidence_id)
            validation_payload = record.get("validation") if isinstance(record, dict) else None
            if (
                not isinstance(record, dict)
                or record.get("action") != "batch_validation"
                or record.get("batchId") != batch_id
                or record.get("taskId") != "__batch__"
                or record.get("detailVersion") != 2
                or not isinstance(validation_payload, dict)
                or validation_payload.get("required") is not True
                or validation_payload.get("result") != "pass"
                or not isinstance(record.get("runId"), str)
            ):
                untrusted.append(f"batch_evidence_invalid:{evidence_id}")
                batch_valid = False
                continue
            if evidence_errors.get(evidence_id):
                untrusted.extend(
                    f"{evidence_id}:{error}" for error in evidence_errors[evidence_id]
                )
                batch_valid = False
                continue
            run_ids.add(str(record["runId"]))
            batch_records.append((evidence_id, record))
        if len(run_ids) != 1:
            if run_ids:
                untrusted.append(f"batch_evidence_multiple_runs:{batch_id}")
            continue
        run_id = next(iter(run_ids))
        run_path = feature_dir / ".batch-runs" / batch_id / f"{run_id}.json"
        try:
            run = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            run = None
        if not isinstance(run, dict) or run.get("batchId") != batch_id:
            untrusted.append(f"batch_run_state_invalid:{batch_id}:{run_id}")
            continue
        run_evidence = set(run.get("evidenceIds", [])) if isinstance(run.get("evidenceIds"), list) else set()
        if not all(evidence_id in run_evidence for evidence_id, _ in batch_records):
            untrusted.append(f"batch_run_evidence_not_bound:{batch_id}:{run_id}")
            continue
        repositories = run.get("finalRepositories")
        if not isinstance(repositories, list):
            repositories = run.get("revalidationBaselineRepositories")
        matching = next(
            (
                item
                for item in repositories or []
                if isinstance(item, dict) and item.get("id") == repository_id
            ),
            None,
        )
        if not isinstance(matching, dict) or not isinstance(matching.get("snapshot"), dict):
            untrusted.append(f"batch_run_snapshot_invalid:{batch_id}:{run_id}")
            continue
        repository_count = len([item for item in repositories or [] if isinstance(item, dict)])
        batch_changed_paths: set[str] = set()
        batch_transient_paths: set[str] = set()
        for evidence_id, record in batch_records:
            record_changed, record_transient, path_errors = _record_paths_for_repository(
                record,
                repository_id=repository_id,
                repository_count=repository_count,
                context=f"batch:{evidence_id}",
            )
            if path_errors:
                untrusted.extend(path_errors)
                batch_valid = False
                continue
            batch_changed_paths.update(record_changed)
            batch_transient_paths.update(record_transient)
        batch_order = max((evidence_order.get(evidence_id, -1) for evidence_id, _ in batch_records), default=-1)
        if batch_valid:
            changed_paths.update(batch_changed_paths)
            transient_paths.update(batch_transient_paths)
            evidence_ids.extend(evidence_id for evidence_id, _ in batch_records)
            if batch_order > latest_completion_order:
                latest_files = matching["snapshot"]
                latest_completion_order = batch_order
    return TrustedEvolution(
        changed_paths=frozenset(changed_paths),
        latest_files=latest_files,
        task_ids=tuple(task_ids),
        evidence_ids=tuple(evidence_ids),
        untrusted_reasons=tuple(sorted(set(untrusted))),
        transient_paths=frozenset(transient_paths),
        implementation_task_ids=tuple(implementation_task_ids),
        implementation_evidence_ids=tuple(implementation_evidence_ids),
    )


def inspect_exploration_cache(
    feature_dir: Path,
    bundle: PlanBundle,
    task_id: str,
    repository_root: Path,
) -> dict[str, Any]:
    repository_id = repository_root.name
    batch_id = bundle.task_batches.get(task_id)
    batch = bundle.batches.get(batch_id) if batch_id else None
    lane = batch.get("executionLane") if isinstance(batch, dict) else None
    if lane not in EXECUTION_LANES:
        raise CodeExplorationError(f"active_batch_execution_lane_invalid:{batch_id}")
    path = exploration_cache_path(feature_dir, repository_id, lane)
    cache: dict[str, Any] | None = None
    if path.is_file():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CodeExplorationError(f"code_exploration_cache_invalid:{path}") from exc
        if not isinstance(value, dict):
            raise CodeExplorationError(f"code_exploration_cache_invalid:{path}")
        errors = validate_cache(
            value,
            feature=str(bundle.root.get("featureId", "")),
            repository_id=repository_id,
            repository_root=repository_root,
            lane=lane,
        )
        if errors:
            raise CodeExplorationError("code_exploration_cache_invalid:" + ",".join(errors))
        cache = value
    current = capture_repository_snapshot(repository_root)
    trusted = collect_trusted_evolution(
        feature_dir,
        bundle,
        cache,
        repository_id,
        current_batch_id=batch_id,
    )
    result = classify_cache(cache, current, trusted, current_batch_id=batch_id)
    result.update(
        {
            "repositoryId": repository_id,
            "repositoryRoot": str(repository_root),
            "executionLane": lane,
            "cachePath": str(path),
            "cacheSha256": cache_sha256(cache) if cache is not None else "missing",
            "findings": cache.get("findings", {}) if cache is not None else {},
            "exploredPaths": cache.get("exploredPaths", []) if cache is not None else [],
            "sharedPaths": cache.get("sharedPaths", []) if cache is not None else [],
            "_cache": cache,
            "_currentSnapshot": current,
        }
    )
    return result
