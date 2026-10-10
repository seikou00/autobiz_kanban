#!/usr/bin/env python3
"""Plan Feature artifact synchronization and maintain durable local state."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "artifact-sync.json"
CATALOG_STAGE = "artifact_catalog"
STATUS_FILE_NAME = "sync-status.json"
CATALOG_FILE_NAME = "ARTIFACT_CATALOG.json"
CATALOG_SCHEMA_VERSION = "autobizdevops.artifact-catalog.v1"
PRD_ORIGINAL_DIR_NAME = "prd_original"
SOURCE_SNAPSHOT_DIR_NAME = "sources"
OPTIONAL_VERIFY_ARTIFACTS = (
    "FEATURE_API_DETAIL.md",
)
LEGACY_SYNC_DIR_NAME = "artifact-sync"
LEGACY_OUTBOX_FILE_NAME = "outbox.ndjson"
LEGACY_MANIFESTS_DIR_NAME = "manifests"
MAX_FILE_SIZE = 5 * 1024 * 1024
IGNORED_PRD_ORIGINAL_NAMES = frozenset({".DS_Store", "Thumbs.db"})
IGNORED_PRD_ORIGINAL_SUFFIXES = frozenset({".tmp", ".swp", ".swo", ".part"})
LEGACY_STAGE_ALIASES = {"biz.discuss": "biz.prd"}


def utc_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_status() -> dict[str, Any]:
    return {
        "version": 1,
        "published_artifacts": {},
        "events": {},
    }


def migrate_legacy_status(data: dict[str, Any]) -> bool:
    """Map old retryable node IDs without changing publication history."""
    changed = False
    for event in data.get("events", {}).values():
        if (not isinstance(event, dict) or event.get("status") not in {"pending", "failed"}
                or "config_snapshot" in event):
            continue
        stage = event.get("source_stage")
        if stage in LEGACY_STAGE_ALIASES:
            event["source_stage"] = LEGACY_STAGE_ALIASES[stage]
            changed = True
    return changed


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
    migrated = migrate_legacy_status(data) or migrated
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
        "source": "skill",
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
    try:
        path.resolve().relative_to(feature_dir.resolve())
        return path.absolute().relative_to(feature_dir.absolute()).as_posix()
    except ValueError as exc:
        raise ValueError(f"产物路径越界: {path}") from exc


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
) -> dict[str, Any]:
    relative_path = relative_artifact_path(feature_dir, path)
    return {
        "path": relative_path,
        "local_path": str(path),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "upload_path": object_directory(project_code, feature, relative_path),
        "file_name": path.name,
    }


CATALOG_EXACT_METADATA: dict[str, dict[str, Any]] = {
    "PRD.md": {
        "category": "requirement",
        "lifecycle": "final",
    },
    "source-context.json": {
        "category": "requirement_source",
        "lifecycle": "final",
    },
    "UI_CONTEXT.json": {
        "category": "ui_context",
        "lifecycle": "final",
    },
    "proposal.md": {
        "category": "behavior_proposal",
        "lifecycle": "final",
    },
    "design.md": {
        "category": "technical_design",
        "lifecycle": "process",
    },
    ".design-contract.lock.json": {
        "category": "technical_design_contract",
        "lifecycle": "process",
    },
    "PLAN.md": {
        "category": "implementation_plan",
        "lifecycle": "process",
    },
    "plan.json": {
        "category": "implementation_plan",
        "lifecycle": "final",
    },
    "SMOKE_TEST_PLAN.json": {
        "category": "smoke_test_plan",
        "lifecycle": "process",
    },
    "SMOKE_RESULT.json": {
        "category": "smoke_result",
        "lifecycle": "evidence",
    },
    "DETAIL_DESIGN.md": {
        "category": "technical_detail",
        "lifecycle": "process",
    },
    "SPECS_REVIEW.md": {
        "category": "review_report",
        "lifecycle": "evidence",
    },
    "REQUIREMENTS_EVAL.md": {
        "category": "review_report",
        "lifecycle": "evidence",
    },
    "UNIT_TEST_REPORT.md": {
        "category": "unit_test_report",
        "lifecycle": "evidence",
    },
    "UNIT_TEST_RESULT.json": {
        "category": "unit_test_result",
        "lifecycle": "evidence",
    },
    "test-output.log": {
        "category": "log",
        "lifecycle": "log",
    },
    "E2E_TEST_CASES.yaml": {
        "category": "e2e_cases",
        "lifecycle": "evidence",
    },
    "E2E_REPORT.md": {
        "category": "e2e_report",
        "lifecycle": "evidence",
    },
    "E2E_RESULT.json": {
        "category": "e2e_result",
        "lifecycle": "evidence",
    },
    "E2E_QUALITY_SCAN.json": {
        "category": "e2e_quality_scan",
        "lifecycle": "evidence",
    },
    "e2e-run.log": {
        "category": "log",
        "lifecycle": "log",
    },
    "VERIFY_REPORT.md": {
        "category": "verify_report",
        "lifecycle": "final",
    },
    "VERIFY_DECISION.json": {
        "category": "verify_decision",
        "lifecycle": "final",
    },
    "FIX_REQUEST.json": {
        "category": "fix_request",
        "lifecycle": "process",
    },
    "evidence/EVIDENCE.jsonl": {
        "category": "evidence_stream",
        "lifecycle": "evidence",
    },
    "FEATURE_API_DETAIL.md": {
        "category": "api_detail",
        "lifecycle": "final",
    },
    CATALOG_FILE_NAME: {
        "category": "artifact_catalog",
        "lifecycle": "system",
    },
}


def catalog_source_for_path(relative_path: str) -> str:
    if relative_path == CATALOG_FILE_NAME:
        return "hook_generated"
    if (
        relative_path.startswith(f"{PRD_ORIGINAL_DIR_NAME}/")
        or relative_path.startswith(f"{SOURCE_SNAPSHOT_DIR_NAME}/")
        or relative_path in OPTIONAL_VERIFY_ARTIFACTS
    ):
        return "extra"
    return "workflow"


def catalog_metadata_for_path(relative_path: str) -> dict[str, Any]:
    if relative_path.startswith("specs/") and relative_path.endswith(".md"):
        return {
            "category": "behavior_spec",
            "lifecycle": "final",
        }
    if relative_path.startswith(f"{PRD_ORIGINAL_DIR_NAME}/"):
        return {
            "category": "source_reference",
            "lifecycle": "reference",
        }
    if relative_path.startswith(f"{SOURCE_SNAPSHOT_DIR_NAME}/"):
        return {
            "category": "requirement_source_snapshot",
            "lifecycle": "reference",
        }
    if relative_path.startswith("e2e-diagnostics/"):
        return {
            "category": "e2e_diagnostic",
            "lifecycle": "evidence",
        }
    return dict(
        CATALOG_EXACT_METADATA.get(
            relative_path,
            {
                "category": "artifact",
                "lifecycle": "process",
            },
        )
    )



def json_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def configured_nodes(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {node: paths for nodes in config["stages"].values() for node, paths in nodes.items()}


def config_digest(config: dict[str, Any]) -> str:
    # Order determines ownership when multiple patterns match the same path.
    return json_digest({
        "allowedCheckpoints": sorted(config["allowedCheckpoints"]),
        "stages": [(group, [(node, list(paths.items())) for node, paths in nodes.items()])
                   for group, nodes in config["stages"].items()],
    })


def load_config() -> dict[str, Any]:
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取产物同步配置 {CONFIG_PATH}: {exc}") from exc
    if not isinstance(config, dict) or set(config) != {"allowedCheckpoints", "stages"}:
        raise ValueError("同步配置必须包含 allowedCheckpoints 和 stages")
    allowed = config["allowedCheckpoints"]
    if not isinstance(allowed, list) or not allowed or any(not isinstance(cp, str) or not cp.strip() for cp in allowed):
        raise ValueError("allowedCheckpoints 必须是非空 checkpoint 字符串列表")
    stages = config["stages"]
    if not isinstance(stages, dict) or set(stages) != {"biz", "dev"}:
        raise ValueError("stages 必须包含 biz 和 dev 对象")
    seen = set()
    for group, nodes in stages.items():
        if not isinstance(nodes, dict):
            raise ValueError(f"stages.{group} 必须是对象")
        for node, paths in nodes.items():
            if not node.startswith(group + ".") or node in seen or not isinstance(paths, dict):
                raise ValueError(f"非法或重复的 nodeId: {node}")
            seen.add(node)
            for pattern, description in paths.items():
                parts = PurePosixPath(pattern).parts
                if (not pattern or pattern.startswith("/") or "\\" in pattern or ":" in pattern
                        or ".." in parts or pattern == "." or "\x00" in pattern):
                    raise ValueError(f"产物路径必须位于 Feature 目录内: {pattern}")
                if pattern in {CATALOG_FILE_NAME, STATUS_FILE_NAME, "hooks.ndjson"}:
                    raise ValueError(f"同步运行时文件不能作为输入产物: {pattern}")
                if not isinstance(description, str) or not description.strip():
                    raise ValueError(f"产物简述不能为空: {pattern}")
    return config


def validate_path_component(value: str, label: str) -> None:
    if not value or value in {".", ".."} or any(c in value for c in "/\\\x00"):
        raise ValueError(f"{label} 不是合法目录名: {value}")


def current_feature_record(workspace: Path, feature: str) -> dict[str, Any]:
    validate_path_component(feature, "Feature")
    path = workspace / ".autobizdevops" / "state.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取 state.json: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("state.json 顶层必须是对象")
    records = payload.get("features", payload)
    if not isinstance(records, dict):
        raise ValueError("state.json.features 必须是对象")
    record = records.get(feature)
    if isinstance(record, str):
        record = {"checkpoint": record}
    if not isinstance(record, dict):
        raise ValueError(f"Feature 状态记录不存在或非法: {feature}")
    if record.get("feature", feature) != feature:
        raise ValueError(f"Feature key 与记录 feature 不一致: {feature}")
    if not isinstance(record.get("checkpoint"), str) or not record["checkpoint"].strip():
        raise ValueError(f"Feature checkpoint 不能为空: {feature}")
    return dict(record, feature=feature, checkpoint=record["checkpoint"].strip())


def resolve_feature_dir(workspace: Path, feature: str) -> Path | None:
    record = current_feature_record(workspace, feature)
    base = workspace / ".autobizdevops"
    active = base / "features" / feature
    if active.is_dir():
        return active
    archive = base / "archive"
    iteration = str(record.get("iteration") or "").strip()
    if record["checkpoint"] == "archived" and iteration not in {"", "-", "—"}:
        validate_path_component(iteration, "iteration")
        selected = archive / f"{feature}-iter{iteration}"
        return selected if selected.is_dir() else None
    exact = archive / feature
    if exact.is_dir():
        return exact
    if archive.is_dir():
        return next((p for p in sorted(archive.iterdir()) if p.is_dir() and p.name.startswith(f"{feature}-iter")), None)
    return None


def load_sync_context(workspace: Path, feature: str) -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_config()
    record = current_feature_record(workspace, feature)
    if record["checkpoint"] not in config["allowedCheckpoints"]:
        raise ValueError(f"当前 checkpoint {record['checkpoint']} 不允许产物同步；允许值: "
                         + ", ".join(config["allowedCheckpoints"]))
    return record, config


def ignored_artifact(relative_path: str, *, globbed: bool) -> bool:
    parts = PurePosixPath(relative_path).parts
    return any(part in IGNORED_PRD_ORIGINAL_NAMES or part.startswith("~$")
               or Path(part).suffix.lower() in IGNORED_PRD_ORIGINAL_SUFFIXES
               or (globbed and part.startswith(".")) for part in parts)


def scan_artifacts(
    feature_dir: Path, *, feature: str, project_code: str, config: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    selected: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    for node, paths in configured_nodes(config).items():
        for pattern, description in paths.items():
            # Also reject an escaping symlink in a fixed prefix of a glob.
            relative_artifact_path(feature_dir, feature_dir / pattern)
            globbed = has_glob(pattern)
            matches = sorted(feature_dir.glob(pattern)) if globbed else [feature_dir / pattern]
            files = []
            for path in matches:
                relative = relative_artifact_path(feature_dir, path)
                if relative in {CATALOG_FILE_NAME, STATUS_FILE_NAME, "hooks.ndjson"}:
                    continue
                if path.is_file() and not ignored_artifact(relative, globbed=globbed):
                    files.append(path)
            if not files:
                diagnostics.append({"path": pattern, "stage": node, "reason": "file_not_found"})
            for path in files:
                relative = relative_artifact_path(feature_dir, path)
                try:
                    size = path.stat().st_size
                    if size > MAX_FILE_SIZE:
                        selected.pop(relative, None)
                        diagnostics.append({"path": relative, "stage": node, "size": size,
                                            "reason": "file_size_exceeds_5mb"})
                        continue
                    artifact = snapshot_file_artifact(feature_dir, path, project_code=project_code, feature=feature)
                except OSError as exc:
                    selected.pop(relative, None)
                    diagnostics.append({"path": relative, "stage": node, "reason": "file_unreadable", "error": str(exc)})
                    continue
                selected[relative] = dict(artifact, stage=node, description=description)
    return dict(sorted(selected.items())), diagnostics


def artifact_object_key(artifact: dict[str, Any]) -> str:
    return f"{artifact['upload_path']}/{artifact['file_name']}"


def needs_upload(artifact: dict[str, Any], status: dict[str, Any]) -> bool:
    previous = status["published_artifacts"].get(artifact["path"])
    return not isinstance(previous, dict) or any((
        previous.get("sha256") != artifact["sha256"],
        previous.get("size") != artifact["size"],
        previous.get("object_key") != artifact_object_key(artifact),
    ))


def catalog_content(feature: str, selected: dict[str, dict[str, Any]], status: dict[str, Any]) -> dict[str, Any]:
    entries = []
    for path, artifact in selected.items():
        published = status["published_artifacts"].get(path)
        if (not isinstance(published, dict) or not published.get("sha256")
                or not isinstance(published.get("size"), int)
                or not 0 <= published["size"] <= MAX_FILE_SIZE
                or published.get("object_key") != artifact_object_key(artifact)):
            continue
        metadata = catalog_metadata_for_path(path)
        entries.append({
            "path": path, "stage": artifact["stage"], "source": catalog_source_for_path(path),
            **metadata, "upload_status": "uploaded", "description": artifact["description"],
            # If an update failed, these still describe the last uploaded version.
            "size": published["size"], "sha256": published["sha256"],
        })
    return {"schema_version": CATALOG_SCHEMA_VERSION, "feature_id": feature, "artifacts": entries}


def write_artifact_catalog(
    feature_dir: Path, *, feature: str, project_code: str,
    selected: dict[str, dict[str, Any]], status: dict[str, Any],
) -> dict[str, Any]:
    content = catalog_content(feature, selected, status)
    path = feature_dir / CATALOG_FILE_NAME
    relative_artifact_path(feature_dir, path)
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = None
    if not isinstance(existing, dict) or {k: v for k, v in existing.items() if k != "generated_at"} != content:
        atomic_write_json(path, dict(content, generated_at=utc_now()))
    artifact = snapshot_file_artifact(feature_dir, path, project_code=project_code, feature=feature)
    return dict(artifact, content_digest=json_digest(content))


def catalog_needs_upload(artifact: dict[str, Any], status: dict[str, Any]) -> bool:
    previous = status.get("catalog", {})
    return (previous.get("content_digest") != artifact["content_digest"]
            or previous.get("object_key") != artifact_object_key(artifact))


def batch_fingerprint(artifacts: Iterable[dict[str, Any]], digest: str = "") -> str:
    return json_digest({"config_digest": digest, "artifacts": [
        {key: item.get(key) for key in ("path", "sha256", "size", "upload_path", "file_name", "stage", "description", "content_digest")}
        for item in artifacts
    ]})


def create_pending_event(
    feature_dir: Path, *, feature: str, source_stage: str, config: dict[str, Any],
    artifacts: list[dict[str, Any]], kind: str = "artifacts",
) -> str:
    status = read_status(feature_dir)
    digest = config_digest(config)
    fingerprint = batch_fingerprint(artifacts, digest)
    for event_id, event in status["events"].items():
        if (isinstance(event, dict) and event.get("status") in {"pending", "failed"}
                and event.get("source_stage") == source_stage and event.get("kind", "artifacts") == kind):
            event.update(artifacts=artifacts, fingerprint=fingerprint, config_digest=digest, config_snapshot=config)
            write_status(feature_dir, status)
            return event_id
    event_id = f"{datetime.now().strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:8]}"
    status["events"][event_id] = {
        "event_id": event_id, "feature": feature, "source_stage": source_stage, "kind": kind,
        "config_digest": digest, "config_snapshot": config, "fingerprint": fingerprint,
        "status": "pending", "attempts": 0, "created_at": utc_now(), "updated_at": utc_now(),
        "artifacts": artifacts,
    }
    write_status(feature_dir, status)
    return event_id


def pending_event_ids(feature_dir: Path) -> list[str]:
    status = read_status(feature_dir)
    return [event_id for event_id, event in status["events"].items()
            if isinstance(event, dict) and event.get("status") in {"pending", "failed"}]


def mark_event_failed(feature_dir: Path, event_id: str, error: str) -> None:
    status = read_status(feature_dir)
    event = status["events"][event_id]
    event.update(status="failed", last_error=error, updated_at=utc_now())
    write_status(feature_dir, status)
    append_sync_hook_log(feature_dir, feature=event["feature"], status="failed", message=error, event_id=event_id)


def record_diagnostics(feature_dir: Path, feature: str, diagnostics: list[dict[str, Any]]) -> None:
    for item in diagnostics:
        append_sync_hook_log(feature_dir, feature=feature, status="skipped",
                             message=f"{item['path']}: {item['reason']}")
    if diagnostics:
        print("跳过产物: " + json.dumps(diagnostics, ensure_ascii=False), file=sys.stderr)


def prepare_reconcile_events(
    *, workspace: Path, feature: str, project_code: str, include_completed: bool = True,
) -> tuple[Path, list[str]]:
    # include_completed is retained for callers of the old reconciliation API.
    _, config = load_sync_context(workspace, feature)
    feature_dir = resolve_feature_dir(workspace, feature)
    if feature_dir is None:
        raise ValueError(f"Feature 目录不存在: {feature}")
    selected, diagnostics = scan_artifacts(feature_dir, feature=feature, project_code=project_code, config=config)
    status = read_status(feature_dir)
    record_diagnostics(feature_dir, feature, diagnostics)
    event_ids = pending_event_ids(feature_dir)
    for node in configured_nodes(config):
        changed = [artifact for artifact in selected.values() if artifact["stage"] == node and needs_upload(artifact, status)]
        if changed:
            event_id = create_pending_event(feature_dir, feature=feature, source_stage=node, config=config, artifacts=changed)
            if event_id not in event_ids:
                event_ids.append(event_id)
    catalog = write_artifact_catalog(feature_dir, feature=feature, project_code=project_code, selected=selected, status=status)
    if event_ids or catalog_needs_upload(catalog, status):
        event_id = create_pending_event(feature_dir, feature=feature, source_stage=CATALOG_STAGE,
                                        config=config, artifacts=[catalog], kind="catalog")
        if event_id not in event_ids:
            event_ids.append(event_id)
    return feature_dir, event_ids
