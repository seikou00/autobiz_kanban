#!/usr/bin/env python3
"""产物同步工具模块：配置、产物扫描、目录与状态管理，供 sync_artifacts.py 调用。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "artifact-sync.json"
CATALOG_STAGE = "artifact_catalog"
STATUS_FILE_NAME = "sync-status.json"
CATALOG_FILE_NAME = "ARTIFACT_CATALOG.json"
CATALOG_SCHEMA_VERSION = "autobizdevops.artifact-catalog.v1"
PRD_ORIGINAL_DIR_NAME = "prd_original"
SOURCE_SNAPSHOT_DIR_NAME = "sources"
EXTRA_CATALOG_PATHS = (
    "FEATURE_API_DETAIL.md",
)
LEGACY_SYNC_DIR_NAME = "artifact-sync"
LEGACY_OUTBOX_FILE_NAME = "outbox.ndjson"
LEGACY_MANIFESTS_DIR_NAME = "manifests"
MAX_FILE_SIZE = 5 * 1024 * 1024
IGNORED_ARTIFACT_NAMES = frozenset({".DS_Store", "Thumbs.db"})
IGNORED_ARTIFACT_SUFFIXES = frozenset({".tmp", ".swp", ".swo", ".part"})
LEGACY_STAGE_ALIASES = {"biz.discuss": "biz.prd"}


def utc_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_status() -> dict[str, Any]:
    return {
        "version": 2,
        "targets": {},
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


def cleanup_legacy_sync_files(feature_dir: Path) -> None:
    legacy_dir = feature_dir / LEGACY_SYNC_DIR_NAME
    for path in (
        feature_dir / LEGACY_SYNC_DIR_NAME / STATUS_FILE_NAME,
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
    path = feature_dir / STATUS_FILE_NAME
    legacy_path = feature_dir / LEGACY_SYNC_DIR_NAME / STATUS_FILE_NAME
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
    # Unscoped v1 publication records remain history, never evidence that a
    # newly selected employee's directory has been published.
    data.setdefault("targets", {})
    if not isinstance(data["targets"], dict):
        raise ValueError(f"同步状态文件 targets 必须是对象: {path}")
    if not isinstance(data.get("events"), dict):
        data["events"] = {}
    migrated = migrate_legacy_status(data) or migrated
    if migrated:
        atomic_write_json(feature_dir / STATUS_FILE_NAME, data)
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
    atomic_write_json(feature_dir / STATUS_FILE_NAME, payload)


def append_sync_log(
    feature_dir: Path,
    *,
    feature: str,
    status: str,
    message: str,
    event_id: str = "",
    employee_id: str = "",
    upload_base: str = "",
) -> None:
    record = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "skill",
        "sessionId": os.environ.get("SESSION_ID", ""),
        "pluginId": "AUTOBIZDEVOPS-PLUGIN",
        "featureId": feature,
        "employeeId": employee_id,
        "uploadBase": upload_base,
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


def object_directory(project_code: str, feature: str, employee_id: str, relative_path: str) -> str:
    parent = Path(relative_path).parent.as_posix()
    base = f"{project_code}/DEV/Features/{feature}/{employee_id}"
    return base if parent in {"", "."} else f"{base}/{parent}"


def snapshot_file_artifact(
    feature_dir: Path,
    path: Path,
    *,
    project_code: str,
    feature: str,
    employee_id: str,
) -> dict[str, Any]:
    relative_path = relative_artifact_path(feature_dir, path)
    return {
        "path": relative_path,
        "local_path": str(path),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "upload_path": object_directory(project_code, feature, employee_id, relative_path),
        "file_name": path.name,
    }


CATALOG_EXACT_METADATA = {
    "PRD.md": ("requirement", "final"),
    "source-context.json": ("requirement_source", "final"),
    "UI_CONTEXT.json": ("ui_context", "final"),
    "proposal.md": ("behavior_proposal", "final"),
    "design.md": ("technical_design", "process"),
    ".design-contract.lock.json": ("technical_design_contract", "process"),
    "PLAN.md": ("implementation_plan", "process"),
    "plan.json": ("implementation_plan", "final"),
    "SMOKE_TEST_PLAN.json": ("smoke_test_plan", "process"),
    "SMOKE_RESULT.json": ("smoke_result", "evidence"),
    "DETAIL_DESIGN.md": ("technical_detail", "process"),
    "SPECS_REVIEW.md": ("review_report", "evidence"),
    "REQUIREMENTS_EVAL.md": ("review_report", "evidence"),
    "UNIT_TEST_REPORT.md": ("unit_test_report", "evidence"),
    "UNIT_TEST_RESULT.json": ("unit_test_result", "evidence"),
    "test-output.log": ("log", "log"),
    "E2E_TEST_CASES.yaml": ("e2e_cases", "evidence"),
    "E2E_REPORT.md": ("e2e_report", "evidence"),
    "E2E_RESULT.json": ("e2e_result", "evidence"),
    "E2E_QUALITY_SCAN.json": ("e2e_quality_scan", "evidence"),
    "e2e-run.log": ("log", "log"),
    "VERIFY_REPORT.md": ("verify_report", "final"),
    "VERIFY_DECISION.json": ("verify_decision", "final"),
    "FIX_REQUEST.json": ("fix_request", "process"),
    "evidence/EVIDENCE.jsonl": ("evidence_stream", "evidence"),
    "FEATURE_API_DETAIL.md": ("api_detail", "final"),
}


def catalog_source_for_path(relative_path: str) -> str:
    if (
        relative_path.startswith(f"{PRD_ORIGINAL_DIR_NAME}/")
        or relative_path.startswith(f"{SOURCE_SNAPSHOT_DIR_NAME}/")
        or relative_path in EXTRA_CATALOG_PATHS
    ):
        return "extra"
    return "workflow"


def catalog_metadata_for_path(relative_path: str) -> dict[str, str]:
    category, lifecycle = CATALOG_EXACT_METADATA.get(relative_path, ("artifact", "process"))
    if relative_path.startswith("specs/") and relative_path.endswith(".md"):
        category, lifecycle = "behavior_spec", "final"
    elif relative_path.startswith(f"{PRD_ORIGINAL_DIR_NAME}/"):
        category, lifecycle = "source_reference", "reference"
    elif relative_path.startswith(f"{SOURCE_SNAPSHOT_DIR_NAME}/"):
        category, lifecycle = "requirement_source_snapshot", "reference"
    elif relative_path.startswith("e2e-diagnostics/"):
        category, lifecycle = "e2e_diagnostic", "evidence"
    return {"category": category, "lifecycle": lifecycle}


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


def validate_employee_id(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("工号必须为非空字符串，且只能包含英文字母、数字、下划线或连字符")


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


def resolve_feature_dir(workspace: Path, feature: str, record: dict[str, Any]) -> Path | None:
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


def ignored_artifact(relative_path: str, *, globbed: bool) -> bool:
    parts = PurePosixPath(relative_path).parts
    return any(part in IGNORED_ARTIFACT_NAMES or part.startswith("~$")
               or Path(part).suffix.lower() in IGNORED_ARTIFACT_SUFFIXES
               or (globbed and part.startswith(".")) for part in parts)


def scan_artifacts(
    feature_dir: Path, *, feature: str, project_code: str, employee_id: str, config: dict[str, Any],
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
                    artifact = snapshot_file_artifact(feature_dir, path, project_code=project_code,
                                                      feature=feature, employee_id=employee_id)
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


def catalog_content(feature: str, employee_id: str, selected: dict[str, dict[str, Any]],
                    status: dict[str, Any]) -> dict[str, Any]:
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
    return {"schema_version": CATALOG_SCHEMA_VERSION, "feature_id": feature,
            "employee_id": employee_id, "artifacts": entries}


def write_artifact_catalog(
    feature_dir: Path, *, feature: str, project_code: str, employee_id: str,
    selected: dict[str, dict[str, Any]], status: dict[str, Any],
) -> dict[str, Any]:
    content = catalog_content(feature, employee_id, selected, status)
    path = feature_dir / CATALOG_FILE_NAME
    relative_artifact_path(feature_dir, path)
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = None
    if not isinstance(existing, dict) or {k: v for k, v in existing.items() if k != "generated_at"} != content:
        atomic_write_json(path, dict(content, generated_at=utc_now()))
    artifact = snapshot_file_artifact(feature_dir, path, project_code=project_code,
                                      feature=feature, employee_id=employee_id)
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


def preflight_error(artifact: dict[str, Any]) -> str:
    """Check the current file immediately before uploading its prepared snapshot."""
    path = Path(artifact["local_path"])
    name = artifact["path"]
    try:
        if not path.is_file():
            return f"上传文件不存在: {name}"
        size = path.stat().st_size
        if size > MAX_FILE_SIZE:
            return f"文件超过 5 MiB 限制: {name} size={size} limit={MAX_FILE_SIZE}"
        if size != artifact.get("size") or sha256_file(path) != artifact.get("sha256"):
            return f"文件大小或 Hash 与同步快照不一致: {name}"
    except OSError as exc:
        return f"上传文件不可读: {name}: {exc}"
    return ""


class SyncSession:
    """One admitted invocation, sharing its config, file scan and durable state."""

    def __init__(self, workspace: Path, feature: str, project_code: str, employee_id: str):
        validate_path_component(project_code, "PROJECT_CODE")
        validate_employee_id(employee_id)
        self.config = load_config()
        record = current_feature_record(workspace, feature)
        self.checkpoint = record["checkpoint"]
        if self.checkpoint not in self.config["allowedCheckpoints"]:
            raise ValueError(f"当前 checkpoint {self.checkpoint} 不允许产物同步；允许值: "
                             + ", ".join(self.config["allowedCheckpoints"]))
        self.feature_dir = resolve_feature_dir(workspace, feature, record)
        if self.feature_dir is None:
            raise ValueError(f"Feature 目录不存在: {feature}")
        self.feature, self.project_code = feature, project_code
        self.employee_id = employee_id
        self.upload_base = object_directory(project_code, feature, employee_id, ".")
        self.nodes = configured_nodes(self.config)
        self.config_digest = config_digest(self.config)
        self.selected, self.diagnostics = self.scan()
        # --check must never migrate or write synchronization state.
        self._status: dict[str, Any] | None = None
        self._diagnostics_reported = False

    @property
    def status(self) -> dict[str, Any]:
        if self._status is None:
            self._status = read_status(self.feature_dir)
        return self._status

    def save(self) -> None:
        self.status["version"] = 2
        write_status(self.feature_dir, self.status)

    @property
    def publication(self) -> dict[str, Any]:
        target = self.status["targets"].setdefault(self.upload_base, {
            "employee_id": self.employee_id, "published_artifacts": {},
        })
        if (not isinstance(target, dict) or target.get("employee_id") != self.employee_id
                or not isinstance(target.get("published_artifacts"), dict)
                or ("catalog" in target and not isinstance(target["catalog"], dict))):
            raise ValueError(f"同步目标状态非法: {self.upload_base}")
        return target

    def scan(self) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        return scan_artifacts(self.feature_dir, feature=self.feature,
                              project_code=self.project_code, employee_id=self.employee_id, config=self.config)

    def catalog(self) -> dict[str, Any]:
        return write_artifact_catalog(self.feature_dir, feature=self.feature,
                                      project_code=self.project_code, employee_id=self.employee_id,
                                      selected=self.selected, status=self.publication)

    def report_diagnostics(self) -> None:
        if self._diagnostics_reported:
            return
        self._diagnostics_reported = True
        for item in self.diagnostics:
            append_sync_log(self.feature_dir, feature=self.feature, status="skipped",
                            employee_id=self.employee_id, upload_base=self.upload_base,
                            message=f"{item['path']}: {item['reason']}")
        if self.diagnostics:
            print("跳过产物: " + json.dumps(self.diagnostics, ensure_ascii=False), file=sys.stderr)

    def pending_event_ids(self) -> list[str]:
        return [key for key, event in self.status["events"].items()
                if isinstance(event, dict) and self.matches_target(event)
                and event.get("status") in {"pending", "failed"}]

    def matches_target(self, event: dict[str, Any]) -> bool:
        return (event.get("feature") == self.feature
                and event.get("employee_id") == self.employee_id
                and event.get("upload_base") == self.upload_base)

    def event_for_target(self, event_id: str) -> dict[str, Any]:
        event = self.status["events"].get(event_id)
        if not isinstance(event, dict) or event.get("feature") != self.feature:
            raise ValueError(f"同步事件不存在或 Feature 不匹配: {event_id}")
        if not self.matches_target(event):
            raise ValueError(f"同步事件工号或上传目录不匹配: {event_id}；旧未绑定事件请通过 --sync 重新准备")
        return event

    def changed_artifacts(self, stage: str) -> list[dict[str, Any]]:
        return [item for item in self.selected.values()
                if item["stage"] == stage and needs_upload(item, self.publication)]

    def set_snapshot(self, event: dict[str, Any], artifacts: list[dict[str, Any]]) -> None:
        event.update(config_digest=self.config_digest, config_snapshot=self.config,
                     artifacts=artifacts, fingerprint=batch_fingerprint(artifacts, self.config_digest),
                     updated_at=utc_now())

    def queue_event(self, stage: str, artifacts: list[dict[str, Any]], *, kind: str = "artifacts") -> str:
        events = self.status["events"]
        event_id = next((key for key in self.pending_event_ids()
                         if events[key].get("source_stage") == stage
                         and events[key].get("kind", "artifacts") == kind), None)
        if event_id is None:
            event_id = f"{datetime.now().strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:8]}"
            events[event_id] = {
                "event_id": event_id, "feature": self.feature, "source_stage": stage, "kind": kind,
                "employee_id": self.employee_id, "upload_base": self.upload_base,
                "status": "pending", "attempts": 0, "created_at": utc_now(),
            }
        self.set_snapshot(events[event_id], artifacts)
        self.save()
        return event_id

    def prepare(self) -> list[str]:
        self.report_diagnostics()
        event_ids = self.pending_event_ids()
        for node in self.nodes:
            changed = self.changed_artifacts(node)
            if changed:
                event_ids.append(self.queue_event(node, changed))
        catalog = self.catalog()
        if event_ids or catalog_needs_upload(catalog, self.publication):
            event_ids.append(self.queue_event(CATALOG_STAGE, [catalog], kind="catalog"))
        return list(dict.fromkeys(event_ids))

    def record_uploaded(self, artifact: dict[str, Any], stage: str) -> None:
        self.publication["published_artifacts"][artifact["path"]] = {
            "stage": stage, "sha256": artifact["sha256"], "size": artifact["size"],
            "object_key": artifact_object_key(artifact), "synced_at": utc_now(),
        }
        if "content_digest" in artifact:
            self.publication["catalog"] = {
                "content_digest": artifact["content_digest"],
                "object_key": artifact_object_key(artifact), "synced_at": utc_now(),
            }
        self.save()

    def finish_event(self, event_id: str, *, error: str = "", skipped: str = "") -> int:
        event = self.status["events"][event_id]
        event.update(status="failed" if error else "skipped" if skipped else "success", updated_at=utc_now())
        if error:
            event["last_error"] = error
            print(error, file=sys.stderr)
        elif skipped:
            event["skipped_reason"] = skipped
            print(skipped)
        else:
            event["synced_at"] = utc_now()
            print(f"artifact sync success: event_id={event_id} files={len(event['artifacts'])}")
        self.save()
        append_sync_log(self.feature_dir, feature=self.feature, event_id=event_id, status=event["status"],
                        employee_id=self.employee_id, upload_base=self.upload_base,
                        message=error or skipped or f"{event['source_stage']} 同步完成")
        return int(bool(error))

    def execute_event(self, event_id: str, upload: Callable[[dict[str, Any]], tuple[bool, str]]) -> int:
        event = self.event_for_target(event_id)
        event.update(status="pending", attempts=int(event.get("attempts", 0) or 0) + 1, updated_at=utc_now())
        for field in ("last_error", "skipped_reason", "synced_at"):
            event.pop(field, None)
        self.save()
        stage = event.get("source_stage", "")
        is_catalog = event.get("kind") == "catalog"
        if not is_catalog and stage not in self.nodes:
            return self.finish_event(event_id, skipped=f"节点已从同步配置移除，跳过: {stage}")
        try:
            if is_catalog:
                # Files may disappear or exceed the limit during upload. Recheck
                # eligibility once at the end, not once for every node event.
                self.selected, _ = self.scan()
                catalog = self.catalog()
                artifacts = [catalog] if catalog_needs_upload(catalog, self.publication) else []
            else:
                artifacts = self.changed_artifacts(stage)
            self.set_snapshot(event, artifacts)
            self.save()
            errors = []
            for artifact in artifacts:
                error = preflight_error(artifact)
                if not error:
                    ok, error = upload(artifact)
                    if ok:
                        self.record_uploaded(artifact, stage)
                        continue
                    error = error or f"上传失败: {artifact['path']}"
                errors.append(error)
            return self.finish_event(event_id, error="\n".join(errors))
        except (OSError, ValueError) as exc:
            return self.finish_event(event_id, error=f"产物同步失败: {exc}")

    def execute(self, event_ids: list[str], upload: Callable[[dict[str, Any]], tuple[bool, str]]) -> int:
        file_events, catalog_events = [], []
        for event_id in dict.fromkeys(event_ids):
            event = self.event_for_target(event_id)
            (catalog_events if event.get("kind") == "catalog" else file_events).append(event_id)
        self.report_diagnostics()
        # Persist the catalog obligation before the first upload, including a
        # single-event retry, so an interruption cannot lose the catalog update.
        if file_events and not catalog_events:
            catalog_events.append(self.queue_event(CATALOG_STAGE, [self.catalog()], kind="catalog"))
        failures = sum(self.execute_event(key, upload) for key in file_events + catalog_events)
        return int(bool(failures))
