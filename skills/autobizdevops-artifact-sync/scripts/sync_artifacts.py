#!/usr/bin/env python3
"""Upload staged Feature artifacts and maintain local synchronization state."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import socket
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from artifact_sync import (  # noqa: E402
    CATALOG_STAGE, MAX_FILE_SIZE, append_sync_hook_log, artifact_object_key,
    batch_fingerprint, catalog_needs_upload, config_digest, configured_nodes,
    create_pending_event, load_sync_context, mark_event_failed, needs_upload,
    pending_event_ids, prepare_reconcile_events, read_status, record_diagnostics,
    resolve_feature_dir, scan_artifacts, sha256_file, utc_now, validate_path_component,
    write_artifact_catalog, write_status,
)
from hooks.paths import get_plugin_output_workspace, resolve_env_feature  # noqa: E402


UPLOAD_URL = "https://tscode-cos-plugin.paasuat.cmbchina.cn/file/upload"
REQUEST_TIMEOUT_SECONDS = 50


def multipart_body(artifact: dict[str, Any]) -> tuple[bytes, str]:
    boundary = f"----AutobizDevOps{uuid.uuid4().hex}"
    local_path = Path(str(artifact["local_path"]))
    file_name = str(artifact["file_name"])
    content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    file_content = local_path.read_bytes()
    chunks = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="path"\r\n\r\n',
        str(artifact["upload_path"]).encode("utf-8"),
        b"\r\n",
        f"--{boundary}\r\n".encode(),
        (
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8"),
        file_content,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(chunks), boundary


def upload_file(
    artifact: dict[str, Any],
    *,
    upload_url: str | None = None,
    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    target_url = upload_url or UPLOAD_URL
    try:
        body, boundary = multipart_body(artifact)
    except OSError as exc:
        return False, f"无法读取上传文件 {artifact.get('local_path', '')}: {exc}"

    try:
        request = urllib.request.Request(
            target_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = response.getcode()
            response.read()
    except urllib.error.HTTPError as exc:
        return False, f"上传失败 {artifact.get('path', '')}: HTTP {exc.code}"
    except (socket.timeout, TimeoutError, urllib.error.URLError, OSError) as exc:
        return False, f"上传请求失败 {artifact.get('path', '')}: {exc}"

    if status_code != 200:
        return False, f"上传失败 {artifact.get('path', '')}: HTTP {status_code}"
    return True, ""


def preflight_errors(artifacts: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for artifact in artifacts:
        display_path = str(artifact.get("path") or artifact.get("local_path") or "")
        path = Path(str(artifact.get("local_path", "")))
        if not path.is_file():
            errors.append(f"上传文件不存在: {display_path}")
            continue
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                handle.read(1)
        except OSError as exc:
            errors.append(f"上传文件不可读: {display_path}: {exc}")
            continue
        if size > MAX_FILE_SIZE:
            errors.append(f"文件超过 5 MiB 限制: {display_path} size={size} limit={MAX_FILE_SIZE}")
            continue
        declared_size = artifact.get("size")
        if not isinstance(declared_size, int) or declared_size != size:
            errors.append(f"文件大小与同步快照不一致: {display_path}")
            continue
        declared_hash = artifact.get("sha256")
        if not isinstance(declared_hash, str) or not declared_hash:
            errors.append(f"文件 Hash 缺失: {display_path}")
            continue
        try:
            current_hash = sha256_file(path)
        except OSError as exc:
            errors.append(f"无法计算文件 Hash: {display_path}: {exc}")
            continue
        if current_hash != declared_hash:
            errors.append(f"文件 Hash 与同步快照不一致: {display_path}")
    return errors


def start_attempt(feature_dir: Path, event_id: str) -> dict[str, Any]:
    status = read_status(feature_dir)
    event = status["events"][event_id]
    event.update(status="pending", attempts=int(event.get("attempts", 0) or 0) + 1, updated_at=utc_now())
    event.pop("last_error", None)
    write_status(feature_dir, status)
    return dict(event)


def fail_event(feature_dir: Path, event_id: str, message: str) -> int:
    mark_event_failed(feature_dir, event_id, message)
    print(message, file=sys.stderr)
    return 1


def record_uploaded(feature_dir: Path, artifact: dict[str, Any], stage: str) -> None:
    status = read_status(feature_dir)
    status["published_artifacts"][artifact["path"]] = {
        "stage": stage, "sha256": artifact["sha256"], "size": artifact["size"],
        "object_key": artifact_object_key(artifact), "synced_at": utc_now(),
    }
    if "content_digest" in artifact:
        status["catalog"] = {
            "content_digest": artifact["content_digest"],
            "object_key": artifact_object_key(artifact), "synced_at": utc_now(),
        }
    write_status(feature_dir, status)


def update_event_snapshot(feature_dir: Path, event_id: str, config: dict[str, Any], artifacts: list[dict[str, Any]]) -> None:
    status = read_status(feature_dir)
    status["events"][event_id].update(
        config_digest=config_digest(config), config_snapshot=config,
        artifacts=artifacts, fingerprint=batch_fingerprint(artifacts, config_digest(config)),
        updated_at=utc_now(),
    )
    write_status(feature_dir, status)


def complete_event(feature_dir: Path, event_id: str, *, skipped_reason: str = "") -> None:
    status = read_status(feature_dir)
    event = status["events"][event_id]
    event.update(status="skipped" if skipped_reason else "success", updated_at=utc_now(), synced_at=utc_now())
    event.pop("last_error", None)
    if skipped_reason:
        event["skipped_reason"] = skipped_reason
    write_status(feature_dir, status)
    append_sync_hook_log(feature_dir, feature=event["feature"], event_id=event_id,
                         status=event["status"], message=skipped_reason or f"{event['source_stage']} 同步完成")


def execute_event(
    workspace: Path, feature: str, event_id: str, project_code: str,
    *, config: dict[str, Any] | None = None,
) -> int:
    if config is None:
        return execute_many(workspace, feature, [event_id], project_code)
    load_sync_context(workspace, feature)
    feature_dir = resolve_feature_dir(workspace, feature)
    if feature_dir is None:
        raise ValueError(f"Feature 目录不存在: {feature}")
    stored = read_status(feature_dir)["events"].get(event_id)
    if not isinstance(stored, dict) or stored.get("feature") != feature:
        raise ValueError(f"同步事件不存在或 Feature 不匹配: {event_id}")
    event = start_attempt(feature_dir, event_id)
    stage = event.get("source_stage", "")
    if event.get("kind") != "catalog" and stage not in configured_nodes(config):
        message = f"节点已从同步配置移除，跳过: {stage}"
        complete_event(feature_dir, event_id, skipped_reason=message)
        print(message)
        return 0
    try:
        selected, diagnostics = scan_artifacts(feature_dir, feature=feature, project_code=project_code, config=config)
        status = read_status(feature_dir)
        if event.get("kind") == "catalog":
            artifact = write_artifact_catalog(feature_dir, feature=feature, project_code=project_code,
                                              selected=selected, status=status)
            artifacts = [artifact] if catalog_needs_upload(artifact, status) else []
        else:
            record_diagnostics(feature_dir, feature, [item for item in diagnostics if item["stage"] == stage])
            artifacts = [item for item in selected.values() if item["stage"] == stage and needs_upload(item, status)]
        update_event_snapshot(feature_dir, event_id, config, artifacts)
        errors = []
        for artifact in artifacts:
            # Preflight each file immediately before sending; one failure does not
            # discard successful siblings or prevent their durable publication.
            checks = preflight_errors([artifact])
            if checks:
                errors.extend(checks)
                continue
            ok, error = upload_file(artifact)
            if not ok:
                errors.append(error)
                continue
            record_uploaded(feature_dir, artifact, stage)
        if errors:
            return fail_event(feature_dir, event_id, "\n".join(errors))
    except (OSError, ValueError) as exc:
        return fail_event(feature_dir, event_id, f"产物同步失败: {exc}")
    complete_event(feature_dir, event_id)
    print(f"artifact sync success: event_id={event_id} files={len(artifacts)}")
    return 0


def execute_many(workspace: Path, feature: str, event_ids: list[str], project_code: str) -> int:
    _, config = load_sync_context(workspace, feature)
    feature_dir = resolve_feature_dir(workspace, feature)
    if feature_dir is None:
        raise ValueError(f"Feature 目录不存在: {feature}")
    status = read_status(feature_dir)
    event_ids = list(dict.fromkeys(event_ids))
    for event_id in event_ids:
        event = status["events"].get(event_id)
        if not isinstance(event, dict) or event.get("feature") != feature:
            raise ValueError(f"同步事件不存在或 Feature 不匹配: {event_id}")
    if not event_ids:
        return 0
    file_events = [key for key in event_ids if status["events"][key].get("kind") != "catalog"]
    catalog_events = [key for key in event_ids if status["events"][key].get("kind") == "catalog"]
    # Persist the catalog obligation before uploading files so a crash is retryable.
    if file_events and not catalog_events:
        selected, _ = scan_artifacts(feature_dir, feature=feature, project_code=project_code, config=config)
        catalog = write_artifact_catalog(feature_dir, feature=feature, project_code=project_code,
                                         selected=selected, status=status)
        catalog_events.append(create_pending_event(feature_dir, feature=feature, source_stage=CATALOG_STAGE,
                                                    config=config, artifacts=[catalog], kind="catalog"))
    failures = 0
    for event_id in file_events + catalog_events:
        failures += int(execute_event(workspace, feature, event_id, project_code, config=config) != 0)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload configured AutobizDevOps Feature artifacts")
    parser.add_argument("--feature", "-f", help="feature name; defaults to FEATURE_ID")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="read-only admission and Feature directory check")
    action.add_argument("--sync", "--reconcile", dest="sync", action="store_true", help="sync configured artifacts (--reconcile is an alias)")
    action.add_argument("--event-id", help="execute one event and refresh the published catalog")
    action.add_argument("--retry-failed", "--drain-outbox", dest="retry_failed", action="store_true", help="retry pending/failed events once")
    parser.add_argument("--prepare-only", action="store_true", help="prepare events and the published catalog without uploading")
    args = parser.parse_args(argv)
    if args.prepare_only and not args.sync:
        parser.error("--prepare-only requires --sync or --reconcile")
    try:
        workspace = get_plugin_output_workspace()
        feature = resolve_env_feature(args.feature, required=True)
        project_code = str(os.environ.get("PROJECT_CODE") or "").strip()
        validate_path_component(project_code, "PROJECT_CODE")
        record, config = load_sync_context(workspace, feature)
        feature_dir = resolve_feature_dir(workspace, feature)
        if feature_dir is None:
            raise ValueError(f"Feature 目录不存在: {feature}")
        # Validate every configured path before changing any sync state.
        scan_artifacts(feature_dir, feature=feature, project_code=project_code, config=config)
        if args.check:
            print(json.dumps({"feature": feature, "checkpoint": record["checkpoint"],
                              "feature_dir": str(feature_dir)}, ensure_ascii=False))
            return 0
        if args.event_id:
            return execute_many(workspace, feature, [args.event_id], project_code)
        if args.retry_failed:
            return execute_many(workspace, feature, pending_event_ids(feature_dir), project_code)
        _, event_ids = prepare_reconcile_events(workspace=workspace, feature=feature, project_code=project_code)
        if args.prepare_only:
            print(json.dumps({"feature": feature, "event_ids": event_ids, "uploaded": False}, ensure_ascii=False))
            return 0
        if not event_ids:
            print("产物和目录均无变化，无需上传。")
        return execute_many(workspace, feature, event_ids, project_code)
    except (OSError, ValueError) as exc:
        print(f"产物同步失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
