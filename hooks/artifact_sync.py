#!/usr/bin/env python3
"""Plan Feature artifact synchronization and maintain durable local state."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.contracts import (  # noqa: E402
    ArtifactSpec,
    SkillContract,
    load_record_workflow_contracts,
)
from board_core.state import find_feature_dir  # noqa: E402
from board_core.state_store import load_state_json_records_result  # noqa: E402


STATUS_FILE_NAME = "sync-status.json"
CATALOG_FILE_NAME = "ARTIFACT_CATALOG.json"
CATALOG_SCHEMA_VERSION = "autobizdevops.artifact-catalog.v1"
REFERENCES_DIR_NAME = "references"
OPTIONAL_VERIFY_ARTIFACTS = (
    "FEATURE_API_DETAIL.md",
)
LEGACY_SYNC_DIR_NAME = "artifact-sync"
LEGACY_OUTBOX_FILE_NAME = "outbox.ndjson"
LEGACY_MANIFESTS_DIR_NAME = "manifests"
MAX_FILE_SIZE = 5 * 1024 * 1024
SYNC_PROCESS_TIMEOUT_SECONDS = 60
UPLOAD_GROUPS = frozenset({"Biz", "Dev"})
WORKFLOW_RECORD_FIELDS = (
    "workflowProfile",
    "workflowDecisions",
    "workflowTemplate",
    "workflowNodes",
    "workflowSkippedNodes",
)
IGNORED_REFERENCE_NAMES = frozenset({".DS_Store", "Thumbs.db"})
IGNORED_REFERENCE_SUFFIXES = frozenset({".tmp", ".swp", ".swo", ".part"})


def utc_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_status() -> dict[str, Any]:
    return {
        "version": 1,
        "published_artifacts": {},
        "events": {},
    }


def status_path(feature_dir: Path) -> Path:
    return feature_dir / STATUS_FILE_NAME


def legacy_sync_dir(feature_dir: Path) -> Path:
    return feature_dir / LEGACY_SYNC_DIR_NAME


def legacy_status_path(feature_dir: Path) -> Path:
    return legacy_sync_dir(feature_dir) / STATUS_FILE_NAME


def cleanup_legacy_sync_files(feature_dir: Path) -> None:
    legacy_dir = legacy_sync_dir(feature_dir)
    for path in (
        legacy_status_path(feature_dir),
        legacy_dir / LEGACY_OUTBOX_FILE_NAME,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return

    manifests = legacy_dir / LEGACY_MANIFESTS_DIR_NAME
    if manifests.is_dir():
        try:
            for path in manifests.iterdir():
                if path.is_file() and path.suffix == ".json":
                    path.unlink()
            manifests.rmdir()
        except OSError:
            return
    try:
        legacy_dir.rmdir()
    except OSError:
        pass


def read_status(feature_dir: Path) -> dict[str, Any]:
    path = status_path(feature_dir)
    legacy_path = legacy_status_path(feature_dir)
    migrated = False
    if not path.is_file():
        if not legacy_path.is_file():
            return default_status()
        path = legacy_path
        migrated = True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"无法读取同步状态文件 {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"同步状态文件 JSON 非法 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"同步状态文件顶层必须是 JSON object: {path}")
    data.setdefault("version", 1)
    if not isinstance(data.get("published_artifacts"), dict):
        data["published_artifacts"] = {}
    if not isinstance(data.get("events"), dict):
        data["events"] = {}
    if migrated:
        atomic_write_json(status_path(feature_dir), data)
    cleanup_legacy_sync_files(feature_dir)
    return data


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def write_status(feature_dir: Path, payload: dict[str, Any]) -> None:
    atomic_write_json(status_path(feature_dir), payload)


def append_sync_hook_log(
    feature_dir: Path,
    *,
    feature: str,
    status: str,
    message: str,
    event_id: str = "",
) -> None:
    record = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "hook",
        "sessionId": os.environ.get("SESSION_ID", ""),
        "pluginId": "AUTOBIZDEVOPS-PLUGIN",
        "featureId": feature,
        "eventId": "artifact-sync",
        "syncEventId": event_id,
        "eventStatus": status,
        "message": message,
    }
    try:
        path = feature_dir / "hooks.ndjson"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def has_glob(path: str) -> bool:
    return any(char in path for char in "*?[")


def relative_artifact_path(feature_dir: Path, path: Path) -> str:
    return path.resolve(strict=False).relative_to(feature_dir.resolve(strict=False)).as_posix()


def object_directory(project_code: str, feature: str, relative_path: str) -> str:
    parent = Path(relative_path).parent.as_posix()
    base = f"{project_code}/DEV/Features/{feature}"
    return base if parent in {"", "."} else f"{base}/{parent}"


def snapshot_file_artifact(
    feature_dir: Path,
    path: Path,
    *,
    project_code: str,
    feature: str,
    required: bool = False,
) -> dict[str, Any]:
    relative_path = relative_artifact_path(feature_dir, path)
    return {
        "path": relative_path,
        "local_path": str(path),
        "required": required,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "upload_path": object_directory(project_code, feature, relative_path),
        "file_name": path.name,
    }


def expand_artifact_spec(feature_dir: Path, spec: ArtifactSpec) -> tuple[list[Path], list[str]]:
    if has_glob(spec.path):
        files = sorted(path for path in feature_dir.glob(spec.path) if path.is_file())
        if spec.required and not files:
            return [], [spec.path]
        return files, []

    path = feature_dir / spec.path
    if path.is_file():
        return [path], []
    return [], [spec.path] if spec.required else []


def snapshot_contract_outputs(
    feature_dir: Path,
    *,
    project_code: str,
    feature: str,
    contract: SkillContract,
) -> tuple[list[dict[str, Any]], list[str]]:
    artifacts: list[dict[str, Any]] = []
    missing: list[str] = []

    for spec in contract.outputs:
        files, missing_paths = expand_artifact_spec(feature_dir, spec)
        missing.extend(missing_paths)
        for path in files:
            artifacts.append(
                snapshot_file_artifact(
                    feature_dir,
                    path,
                    project_code=project_code,
                    feature=feature,
                    required=spec.required,
                )
            )

    artifacts.sort(key=lambda item: item["path"])
    return artifacts, sorted(set(missing))


CATALOG_EXACT_METADATA: dict[str, dict[str, Any]] = {
    "PRD_DISCUSS.md": {
        "category": "requirement_discussion",
        "lifecycle": "process",
        "description": "需求讨论稿。",
    },
    "PRD.md": {
        "category": "requirement",
        "lifecycle": "final",
        "description": "正式产品需求文档。",
    },
    "proposal.md": {
        "category": "behavior_proposal",
        "lifecycle": "final",
        "description": "行为规格总览。",
    },
    "design.md": {
        "category": "technical_design",
        "lifecycle": "process",
        "description": "技术设计文档。",
    },
    "PLAN.md": {
        "category": "implementation_plan",
        "lifecycle": "process",
        "description": "开发执行计划。",
    },
    "DETAIL_DESIGN.md": {
        "category": "technical_detail",
        "lifecycle": "process",
        "description": "详细设计文档。",
    },
    "REQUIREMENTS_EVAL.md": {
        "category": "review_report",
        "lifecycle": "evidence",
        "description": "需求实现评审报告。",
    },
    "UNIT_TEST_REPORT.md": {
        "category": "unit_test_report",
        "lifecycle": "evidence",
        "description": "单元测试报告。",
    },
    "test-output.log": {
        "category": "log",
        "lifecycle": "log",
        "description": "单元测试原始运行日志。",
    },
    "E2E_TEST_CASES.yaml": {
        "category": "e2e_cases",
        "lifecycle": "evidence",
        "description": "E2E 测试用例。",
    },
    "E2E_REPORT.md": {
        "category": "e2e_report",
        "lifecycle": "evidence",
        "description": "E2E 测试报告。",
    },
    "e2e-run.log": {
        "category": "log",
        "lifecycle": "log",
        "description": "E2E 原始运行日志。",
    },
    "VERIFY_REPORT.md": {
        "category": "verify_report",
        "lifecycle": "final",
        "description": "验收汇总报告。",
    },
    "FEATURE_API_DETAIL.md": {
        "category": "api_detail",
        "lifecycle": "final",
        "description": "当前 Feature 涉及接口的详细说明。",
    },
    CATALOG_FILE_NAME: {
        "category": "artifact_catalog",
        "lifecycle": "system",
        "description": "Feature 产物清单。",
    },
}


def catalog_source_for_path(relative_path: str) -> str:
    if relative_path == CATALOG_FILE_NAME:
        return "hook_generated"
    if relative_path.startswith(f"{REFERENCES_DIR_NAME}/") or relative_path in OPTIONAL_VERIFY_ARTIFACTS:
        return "extra"
    return "workflow"


def catalog_metadata_for_path(relative_path: str) -> dict[str, Any]:
    if relative_path.startswith("specs/") and relative_path.endswith(".md"):
        return {
            "category": "behavior_spec",
            "lifecycle": "final",
            "description": "行为规格明细。",
        }
    if relative_path.startswith(f"{REFERENCES_DIR_NAME}/"):
        return {
            "category": "source_reference",
            "lifecycle": "reference",
            "description": "需求沟通阶段使用的原始需求或参考资料。",
        }
    return dict(
        CATALOG_EXACT_METADATA.get(
            relative_path,
            {
                "category": "artifact",
                "lifecycle": "process",
                "description": "Feature 产物。",
            },
        )
    )


def catalog_entry(
    *,
    path: str,
    stage: str,
    upload_status: str,
    size: int | None = None,
    sha256: str | None = None,
    status_reason: str = "",
) -> dict[str, Any]:
    metadata = catalog_metadata_for_path(path)
    entry: dict[str, Any] = {
        "path": path,
        "stage": stage,
        "source": catalog_source_for_path(path),
        "category": metadata["category"],
        "lifecycle": metadata["lifecycle"],
        "upload_status": upload_status,
        "description": metadata["description"],
    }
    if size is not None:
        entry["size"] = size
    if sha256:
        entry["sha256"] = sha256
    if status_reason:
        entry["status_reason"] = status_reason
    return entry


def ignored_reference_file(path: Path) -> bool:
    return (
        path.name in IGNORED_REFERENCE_NAMES
        or path.name.startswith("~$")
        or path.name.startswith(".")
        or path.suffix in IGNORED_REFERENCE_SUFFIXES
    )


def collect_reference_artifacts(
    feature_dir: Path,
    *,
    project_code: str,
    feature: str,
    status: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    artifacts: list[dict[str, Any]] = []
    side_entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    references_dir = feature_dir / REFERENCES_DIR_NAME

    if references_dir.is_dir():
        for path in sorted(references_dir.rglob("*")):
            if not path.is_file() or ignored_reference_file(path):
                continue
            relative_path = relative_artifact_path(feature_dir, path)
            seen.add(relative_path)
            size = path.stat().st_size
            if size > MAX_FILE_SIZE:
                side_entries.append(
                    catalog_entry(
                        path=relative_path,
                        stage="biz.discuss",
                        upload_status="skipped",
                        size=size,
                        status_reason="file_size_exceeds_5mb",
                    )
                )
                continue
            artifacts.append(
                snapshot_file_artifact(
                    feature_dir,
                    path,
                    project_code=project_code,
                    feature=feature,
                    required=False,
                )
            )

    for relative_path in sorted(status.get("published_artifacts", {})):
        if not isinstance(relative_path, str) or not relative_path.startswith(f"{REFERENCES_DIR_NAME}/"):
            continue
        if relative_path in seen:
            continue
        if not (feature_dir / relative_path).is_file():
            side_entries.append(
                catalog_entry(
                    path=relative_path,
                    stage="biz.discuss",
                    upload_status="missing",
                    status_reason="file_not_found",
                )
            )
    return sorted(artifacts, key=lambda item: item["path"]), side_entries


def collect_optional_verify_artifacts(
    feature_dir: Path,
    *,
    project_code: str,
    feature: str,
    status: dict[str, Any],
    include_missing: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    artifacts: list[dict[str, Any]] = []
    side_entries: list[dict[str, Any]] = []
    published = status.get("published_artifacts", {})
    for relative_path in OPTIONAL_VERIFY_ARTIFACTS:
        path = feature_dir / relative_path
        if path.is_file():
            size = path.stat().st_size
            if size > MAX_FILE_SIZE:
                side_entries.append(
                    catalog_entry(
                        path=relative_path,
                        stage="dev.verify",
                        upload_status="skipped",
                        size=size,
                        status_reason="file_size_exceeds_5mb",
                    )
                )
                continue
            artifacts.append(
                snapshot_file_artifact(
                    feature_dir,
                    path,
                    project_code=project_code,
                    feature=feature,
                    required=False,
                )
            )
            continue
        if include_missing or relative_path in published:
            side_entries.append(
                catalog_entry(
                    path=relative_path,
                    stage="dev.verify",
                    upload_status="missing",
                    status_reason="file_not_found",
                )
            )
    return sorted(artifacts, key=lambda item: item["path"]), side_entries


def collect_sidecar_artifacts(
    feature_dir: Path,
    *,
    project_code: str,
    feature: str,
    stage_id: str,
    status: dict[str, Any],
    include_unpublished_missing: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if stage_id == "biz.discuss":
        return collect_reference_artifacts(
            feature_dir,
            project_code=project_code,
            feature=feature,
            status=status,
        )
    if stage_id == "dev.verify":
        return collect_optional_verify_artifacts(
            feature_dir,
            project_code=project_code,
            feature=feature,
            status=status,
            include_missing=include_unpublished_missing,
        )
    return [], []


def split_oversized_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    stage: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    uploadable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for artifact in artifacts:
        size = int(artifact.get("size", 0) or 0)
        relative_path = str(artifact.get("path") or "")
        if relative_path and size > MAX_FILE_SIZE:
            skipped.append(
                catalog_entry(
                    path=relative_path,
                    stage=stage,
                    upload_status="skipped",
                    size=size,
                    sha256=str(artifact.get("sha256") or ""),
                    status_reason="file_size_exceeds_5mb",
                )
            )
            continue
        uploadable.append(artifact)
    return uploadable, skipped


def merge_catalog_entries(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for entry in entries:
        path = str(entry.get("path", ""))
        if not path or path == CATALOG_FILE_NAME:
            continue
        merged[path] = dict(entry)
    return [merged[path] for path in sorted(merged)]


def catalog_entries_from_published(feature_dir: Path, status: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for relative_path, published in status.get("published_artifacts", {}).items():
        if not isinstance(relative_path, str) or relative_path == CATALOG_FILE_NAME:
            continue
        if not isinstance(published, dict):
            continue
        path = feature_dir / relative_path
        stage = str(published.get("stage") or "")
        if path.is_file():
            size = path.stat().st_size
            try:
                current_hash = sha256_file(path)
            except OSError:
                current_hash = ""
            if (
                current_hash
                and current_hash == published.get("sha256")
                and size == published.get("size")
            ):
                entries.append(
                    catalog_entry(
                        path=relative_path,
                        stage=stage,
                        upload_status="unchanged",
                        size=size,
                        sha256=current_hash,
                    )
                )
            else:
                entries.append(
                    catalog_entry(
                        path=relative_path,
                        stage=stage,
                        upload_status="uploaded",
                        size=size,
                        sha256=current_hash or None,
                    )
                )
            continue
        entries.append(
            catalog_entry(
                path=relative_path,
                stage=stage,
                upload_status="missing",
                status_reason="file_not_found",
            )
        )
    return entries


def write_artifact_catalog(
    feature_dir: Path,
    *,
    feature: str,
    status: dict[str, Any],
    current_stage: str,
    current_artifacts: list[dict[str, Any]],
    current_missing: list[str],
    side_entries: list[dict[str, Any]],
    project_code: str,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = catalog_entries_from_published(feature_dir, status)
    for artifact in current_artifacts:
        relative_path = str(artifact.get("path") or "")
        if not relative_path or relative_path == CATALOG_FILE_NAME:
            continue
        entries.append(
            catalog_entry(
                path=relative_path,
                stage=current_stage,
                upload_status="uploaded",
                size=int(artifact.get("size", 0)),
                sha256=str(artifact.get("sha256") or ""),
            )
        )
    for missing_path in current_missing:
        entries.append(
            catalog_entry(
                path=missing_path,
                stage=current_stage,
                upload_status="missing",
                status_reason="file_not_found",
            )
        )
    entries.extend(side_entries)

    payload = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "feature_id": feature,
        "generated_at": utc_now(),
        "artifacts": merge_catalog_entries(entries),
    }
    atomic_write_json(feature_dir / CATALOG_FILE_NAME, payload)
    return snapshot_file_artifact(
        feature_dir,
        feature_dir / CATALOG_FILE_NAME,
        project_code=project_code,
        feature=feature,
        required=False,
    )


def order_upload_artifacts(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(artifacts, key=lambda item: (item.get("path") == CATALOG_FILE_NAME, str(item.get("path", ""))))


def snapshot_sync_candidates(
    feature_dir: Path,
    *,
    project_code: str,
    feature: str,
    contract: SkillContract,
    status: dict[str, Any],
    include_unpublished_missing: bool = False,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    artifacts, missing = snapshot_contract_outputs(
        feature_dir,
        project_code=project_code,
        feature=feature,
        contract=contract,
    )
    sidecar_artifacts, side_entries = collect_sidecar_artifacts(
        feature_dir,
        project_code=project_code,
        feature=feature,
        stage_id=contract.node_id,
        status=status,
        include_unpublished_missing=include_unpublished_missing,
    )
    artifacts.extend(sidecar_artifacts)
    artifacts, oversized_entries = split_oversized_artifacts(artifacts, stage=contract.node_id)
    side_entries.extend(oversized_entries)
    return sorted(artifacts, key=lambda item: item["path"]), missing, side_entries


def snapshot_event_artifacts(
    feature_dir: Path,
    *,
    project_code: str,
    feature: str,
    contract: SkillContract,
    status: dict[str, Any],
    include_unpublished_missing: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    artifacts, missing, side_entries = snapshot_sync_candidates(
        feature_dir,
        project_code=project_code,
        feature=feature,
        contract=contract,
        status=status,
        include_unpublished_missing=include_unpublished_missing,
    )
    catalog = write_artifact_catalog(
        feature_dir,
        feature=feature,
        status=status,
        current_stage=contract.node_id,
        current_artifacts=artifacts,
        current_missing=missing,
        side_entries=side_entries,
        project_code=project_code,
    )
    artifacts.append(catalog)
    return order_upload_artifacts(artifacts), missing


def batch_fingerprint(artifacts: Iterable[dict[str, Any]]) -> str:
    rows = [
        {
            "path": item.get("path", ""),
            "sha256": item.get("sha256", ""),
            "size": item.get("size", 0),
        }
        for item in artifacts
    ]
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def contract_by_node_id(contracts: Any, node_id: str) -> SkillContract | None:
    for contract in contracts.skill_contracts.values():
        if contract.node_id == node_id:
            return contract
    return None


def contract_for_checkpoint(contracts: Any, checkpoint: str | None) -> SkillContract | None:
    if not checkpoint:
        return None
    for contract in contracts.skill_contracts.values():
        if checkpoint in contract.checkpoints:
            return contract
    return None


def workflow_record_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    snapshot = {
        field: record[field]
        for field in WORKFLOW_RECORD_FIELDS
        if field in record and record[field] not in (None, [], {})
    }
    snapshot.setdefault("workflowProfile", str(record.get("workflowProfile") or "standard"))
    snapshot.setdefault("workflowDecisions", {})
    snapshot.setdefault("workflowTemplate", str(record.get("workflowTemplate") or "standard"))
    return json.loads(json.dumps(snapshot, ensure_ascii=False))


def current_feature_record(workspace: Path, feature: str) -> dict[str, Any]:
    result = load_state_json_records_result(workspace)
    if not result.exists:
        raise ValueError(f"state.json 不存在: {workspace / '.autobizdevops' / 'state.json'}")
    if result.errors:
        raise ValueError("\n".join(result.errors))
    record = result.records.get(feature)
    if not isinstance(record, dict):
        raise ValueError(f"Feature 状态记录不存在: {feature}")
    return dict(record)


def resolve_feature_dir(workspace: Path, feature: str) -> Path | None:
    """Resolve the active Feature, or its state-selected archived iteration."""
    active = workspace / ".autobizdevops" / "features" / feature
    if active.is_dir():
        return active
    try:
        record = current_feature_record(workspace, feature)
    except ValueError:
        return find_feature_dir(workspace, feature)
    iteration = str(record.get("iteration") or "").strip()
    if record.get("checkpoint") == "archived" and iteration and iteration != "-":
        archived = workspace / ".autobizdevops" / "archive" / f"{feature}-iter{iteration}"
        if archived.is_dir():
            return archived
    return find_feature_dir(workspace, feature)


def legacy_event_workflow_record(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflowProfile": str(event.get("workflow_profile") or "standard"),
        "workflowDecisions": event.get("workflow_decisions") or {},
        "workflowTemplate": "standard",
    }


def event_workflow_record(event: dict[str, Any]) -> dict[str, Any]:
    record = event.get("workflow_record")
    if isinstance(record, dict):
        return workflow_record_snapshot(record)
    return legacy_event_workflow_record(event)


def load_contracts(workspace: Path, record: dict[str, Any]) -> Any:
    return load_record_workflow_contracts(ROOT, record, workspace=workspace)


def tracked_stage_ids(status: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for event in status.get("events", {}).values():
        if not isinstance(event, dict) or event.get("status") not in {"pending", "failed", "success"}:
            continue
        stage = event.get("source_stage")
        if isinstance(stage, str) and stage:
            result.add(stage)
    for artifact in status.get("published_artifacts", {}).values():
        if not isinstance(artifact, dict):
            continue
        stage = artifact.get("stage")
        if isinstance(stage, str) and stage:
            result.add(stage)
    return result


def stage_needs_upload(status: dict[str, Any], artifacts: list[dict[str, Any]]) -> bool:
    published = status.get("published_artifacts", {})
    for artifact in artifacts:
        previous = published.get(artifact["path"])
        if not isinstance(previous, dict):
            return True
        if previous.get("sha256") != artifact["sha256"] or previous.get("size") != artifact["size"]:
            return True
    return False


def duplicate_retryable_event(
    status: dict[str, Any],
    *,
    source_stage: str,
    fingerprint: str,
) -> str | None:
    for event_id, event in status.get("events", {}).items():
        if not isinstance(event, dict):
            continue
        if event.get("source_stage") != source_stage or event.get("fingerprint") != fingerprint:
            continue
        if event.get("status") in {"pending", "failed"}:
            return str(event_id)
    return None


def new_event_id(source_stage: str) -> str:
    safe_stage = source_stage.replace(".", "-").replace("/", "-")
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    return f"{timestamp}-{safe_stage}-{uuid.uuid4().hex[:8]}"


def create_pending_event(
    feature_dir: Path,
    *,
    feature: str,
    trigger_checkpoint: str,
    contract: SkillContract,
    workflow_record: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> str:
    status = read_status(feature_dir)
    fingerprint = batch_fingerprint(artifacts)
    duplicate = duplicate_retryable_event(
        status,
        source_stage=contract.node_id,
        fingerprint=fingerprint,
    )
    if duplicate:
        return duplicate

    event_id = new_event_id(contract.node_id)
    now = utc_now()
    record_snapshot = workflow_record_snapshot(workflow_record)
    event = {
        "event_id": event_id,
        "feature": feature,
        "trigger_checkpoint": trigger_checkpoint,
        "source_stage": contract.node_id,
        "source_skill": contract.skill,
        "workflow_record": record_snapshot,
        # Legacy fields remain for older tooling and existing status readers.
        "workflow_profile": record_snapshot.get("workflowProfile", "standard"),
        "workflow_decisions": record_snapshot.get("workflowDecisions", {}),
        "fingerprint": fingerprint,
        "status": "pending",
        "attempts": 0,
        "created_at": now,
        "updated_at": now,
        "artifacts": artifacts,
    }
    status["events"][event_id] = dict(event)
    write_status(feature_dir, status)
    return event_id


def mark_event_failed(
    feature_dir: Path,
    event_id: str,
    error: str,
    *,
    increment_attempts: bool = False,
) -> None:
    status = read_status(feature_dir)
    event = status.get("events", {}).get(event_id)
    if not isinstance(event, dict):
        return
    event["status"] = "failed"
    event["last_error"] = error
    event["updated_at"] = utc_now()
    if increment_attempts:
        event["attempts"] = int(event.get("attempts", 0) or 0) + 1
    write_status(feature_dir, status)
    append_sync_hook_log(
        feature_dir,
        feature=str(event.get("feature", "")),
        status="failed",
        event_id=event_id,
        message=error,
    )


def event_by_id(feature_dir: Path, event_id: str) -> dict[str, Any] | None:
    event = read_status(feature_dir).get("events", {}).get(event_id)
    return dict(event) if isinstance(event, dict) else None


def pending_event_ids(feature_dir: Path) -> list[str]:
    status = read_status(feature_dir)
    rows: list[tuple[str, str]] = []
    for event_id, event in status.get("events", {}).items():
        if not isinstance(event, dict) or event.get("status") not in {"pending", "failed"}:
            continue
        rows.append((str(event.get("created_at", "")), str(event_id)))
    return [event_id for _, event_id in sorted(rows)]


def refresh_event_snapshot(
    *,
    workspace: Path,
    feature_dir: Path,
    project_code: str,
    event: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    record = event_workflow_record(event)
    contracts = load_contracts(workspace, record)
    contract = contract_by_node_id(contracts, str(event.get("source_stage", "")))
    if contract is None or contract.group not in UPLOAD_GROUPS:
        raise ValueError(f"同步阶段不存在或不可上传: {event.get('source_stage', '')}")
    return snapshot_event_artifacts(
        feature_dir,
        project_code=project_code,
        feature=str(event.get("feature", "")),
        contract=contract,
        status=read_status(feature_dir),
        include_unpublished_missing=True,
    )


def latest_stage_context(
    workspace: Path,
    status: dict[str, Any],
    stage_id: str,
) -> tuple[SkillContract, dict[str, Any]] | None:
    rows = [
        event
        for event in status.get("events", {}).values()
        if isinstance(event, dict)
        and event.get("source_stage") == stage_id
        and event.get("status") in {"pending", "failed", "success"}
    ]
    for event in sorted(rows, key=lambda item: str(item.get("updated_at", "")), reverse=True):
        record = event_workflow_record(event)
        try:
            contract = contract_by_node_id(load_contracts(workspace, record), stage_id)
        except Exception:
            continue
        if contract is not None:
            return contract, record
    return None


def is_direct_publish_transition(old_checkpoint: str | None, new_checkpoint: str | None) -> bool:
    if not old_checkpoint or not new_checkpoint:
        return False
    if new_checkpoint == "archived":
        return old_checkpoint.endswith("_done")
    return old_checkpoint.endswith("_done") and new_checkpoint.endswith("_in_progress")


def prepare_checkpoint_sync_events(
    *,
    workspace: Path,
    feature: str,
    old_checkpoint: str | None,
    new_checkpoint: str | None,
    workflow_profile: str = "standard",
    workflow_decisions: dict[str, str] | None = None,
    project_code: str,
) -> tuple[Path | None, list[str]]:
    del workflow_profile, workflow_decisions  # Compatibility with the previous internal API.
    if not new_checkpoint or new_checkpoint == old_checkpoint:
        return None, []

    feature_dir = resolve_feature_dir(workspace, feature)
    if feature_dir is None:
        return None, []

    record = current_feature_record(workspace, feature)
    contracts = load_contracts(workspace, record)
    status = read_status(feature_dir)
    contexts: dict[str, tuple[SkillContract, dict[str, Any]]] = {}

    # Reconcile successful publications and retry pending/failed publications.
    for stage_id in sorted(tracked_stage_ids(status)):
        contract = contract_by_node_id(contracts, stage_id)
        if contract is not None:
            contexts[stage_id] = (contract, record)
            continue
        fallback = latest_stage_context(workspace, status, stage_id)
        if fallback is not None:
            contexts[stage_id] = fallback

    direct_contract: SkillContract | None = None
    if is_direct_publish_transition(old_checkpoint, new_checkpoint):
        direct_contract = contract_for_checkpoint(contracts, old_checkpoint)
        if direct_contract is not None:
            contexts[direct_contract.node_id] = (direct_contract, record)

    event_ids: list[str] = []
    unchanged: list[str] = []
    for stage_id in sorted(contexts):
        contract, contract_record = contexts[stage_id]
        if contract.group not in UPLOAD_GROUPS or not contract.outputs:
            continue
        status = read_status(feature_dir)
        artifacts, missing, side_entries = snapshot_sync_candidates(
            feature_dir,
            project_code=project_code,
            feature=feature,
            contract=contract,
            status=status,
            include_unpublished_missing=(
                direct_contract is not None
                and contract.node_id == direct_contract.node_id
                and contract.node_id == "dev.verify"
            ),
        )
        if not artifacts and not missing and not side_entries:
            continue
        if not missing and not side_entries and not stage_needs_upload(status, artifacts):
            unchanged.append(stage_id)
            continue
        artifacts, missing = snapshot_event_artifacts(
            feature_dir,
            project_code=project_code,
            feature=feature,
            contract=contract,
            status=status,
            include_unpublished_missing=(
                direct_contract is not None
                and contract.node_id == direct_contract.node_id
                and contract.node_id == "dev.verify"
            ),
        )
        if not artifacts:
            continue
        if missing:
            append_sync_hook_log(
                feature_dir,
                feature=feature,
                status="missing",
                message=f"{contract.node_id} 部分产物缺失，仅同步已存在产物: " + ", ".join(missing),
            )
        event_id = create_pending_event(
            feature_dir,
            feature=feature,
            trigger_checkpoint=new_checkpoint,
            contract=contract,
            workflow_record=contract_record,
            artifacts=artifacts,
        )
        if event_id not in event_ids:
            event_ids.append(event_id)
        status = read_status(feature_dir)

    if unchanged:
        append_sync_hook_log(
            feature_dir,
            feature=feature,
            status="unchanged",
            message="产物 Hash 未变化，跳过上传: " + ", ".join(unchanged),
        )
    return feature_dir, event_ids


def prepare_reconcile_events(
    *,
    workspace: Path,
    feature: str,
    project_code: str,
) -> tuple[Path | None, list[str]]:
    feature_dir = resolve_feature_dir(workspace, feature)
    if feature_dir is None:
        return None, []
    record = current_feature_record(workspace, feature)
    contracts = load_contracts(workspace, record)
    status = read_status(feature_dir)
    event_ids: list[str] = []

    for stage_id in sorted(tracked_stage_ids(status)):
        contract = contract_by_node_id(contracts, stage_id)
        contract_record = record
        if contract is None:
            fallback = latest_stage_context(workspace, status, stage_id)
            if fallback is None:
                continue
            contract, contract_record = fallback
        if contract.group not in UPLOAD_GROUPS or not contract.outputs:
            continue
        status = read_status(feature_dir)
        artifacts, missing, side_entries = snapshot_sync_candidates(
            feature_dir,
            project_code=project_code,
            feature=feature,
            contract=contract,
            status=status,
            include_unpublished_missing=False,
        )
        if not artifacts and not missing and not side_entries:
            continue
        if not missing and not side_entries and not stage_needs_upload(status, artifacts):
            continue
        artifacts, missing = snapshot_event_artifacts(
            feature_dir,
            project_code=project_code,
            feature=feature,
            contract=contract,
            status=status,
            include_unpublished_missing=False,
        )
        if not artifacts:
            continue
        if missing:
            append_sync_hook_log(
                feature_dir,
                feature=feature,
                status="missing",
                message=f"{contract.node_id} 部分产物缺失，仅同步已存在产物: " + ", ".join(missing),
            )
        event_id = create_pending_event(
            feature_dir,
            feature=feature,
            trigger_checkpoint="manual_reconcile",
            contract=contract,
            workflow_record=contract_record,
            artifacts=artifacts,
        )
        if event_id not in event_ids:
            event_ids.append(event_id)
        status = read_status(feature_dir)
    return feature_dir, event_ids


def run_sync_subprocesses(
    *,
    feature_dir: Path,
    feature: str,
    event_ids: list[str],
    timeout_seconds: int = SYNC_PROCESS_TIMEOUT_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    script = ROOT / "hooks" / "sync_artifacts.py"
    for event_id in event_ids:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            mark_event_failed(feature_dir, event_id, "产物上传超时: 总等待时间超过 60 秒")
            continue
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--feature",
                    feature,
                    "--event-id",
                    event_id,
                ],
                text=True,
                capture_output=True,
                timeout=remaining,
                check=False,
            )
        except subprocess.TimeoutExpired:
            mark_event_failed(feature_dir, event_id, "产物上传超时: 总等待时间超过 60 秒")
            continue
        except OSError as exc:
            mark_event_failed(feature_dir, event_id, f"无法启动产物上传脚本: {exc}")
            continue

        current = event_by_id(feature_dir, event_id)
        if result.returncode != 0 and current and current.get("status") == "pending":
            detail = (result.stderr or result.stdout or "").strip()
            mark_event_failed(feature_dir, event_id, detail or f"产物上传脚本退出码 {result.returncode}")


def schedule_checkpoint_sync_best_effort(
    *,
    workspace: Path,
    feature: str,
    old_checkpoint: str | None,
    new_checkpoint: str | None,
    workflow_profile: str = "standard",
    workflow_decisions: dict[str, str] | None = None,
) -> None:
    project_code = str(os.environ.get("PROJECT_CODE") or "").strip()
    if not project_code:
        return
    feature_dir, event_ids = prepare_checkpoint_sync_events(
        workspace=workspace,
        feature=feature,
        old_checkpoint=old_checkpoint,
        new_checkpoint=new_checkpoint,
        workflow_profile=workflow_profile,
        workflow_decisions=workflow_decisions,
        project_code=project_code,
    )
    if feature_dir is None or not event_ids:
        return
    run_sync_subprocesses(
        feature_dir=feature_dir,
        feature=feature,
        event_ids=event_ids,
    )
