#!/usr/bin/env python3
"""Upload staged Feature artifacts and maintain local synchronization state."""

from __future__ import annotations

import argparse
import mimetypes
import os
import socket
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.artifact_sync import (  # noqa: E402
    MAX_FILE_SIZE,
    append_sync_hook_log,
    batch_fingerprint,
    mark_event_failed,
    pending_event_ids,
    prepare_reconcile_events,
    read_status,
    refresh_event_snapshot,
    resolve_feature_dir,
    sha256_file,
    utc_now,
    write_status,
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


def start_attempt(feature_dir: Path, event_id: str) -> dict[str, Any] | None:
    status = read_status(feature_dir)
    event = status.get("events", {}).get(event_id)
    if not isinstance(event, dict):
        return None
    event["status"] = "pending"
    event["attempts"] = int(event.get("attempts", 0) or 0) + 1
    event["updated_at"] = utc_now()
    event.pop("last_error", None)
    write_status(feature_dir, status)
    return dict(event)


def fail_event(feature_dir: Path, event_id: str, message: str) -> int:
    mark_event_failed(feature_dir, event_id, message)
    print(message, file=sys.stderr)
    return 1


def complete_event(
    feature_dir: Path,
    *,
    event_id: str,
    event: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> None:
    now = utc_now()
    status = read_status(feature_dir)
    stored_event = status.get("events", {}).get(event_id)
    if not isinstance(stored_event, dict):
        return
    stored_event.update(
        {
            "status": "success",
            "fingerprint": batch_fingerprint(artifacts),
            "artifacts": artifacts,
            "updated_at": now,
            "synced_at": now,
        }
    )
    stored_event.pop("manifest", None)
    stored_event.pop("last_error", None)

    published = status["published_artifacts"]
    for artifact in artifacts:
        published[artifact["path"]] = {
            "stage": event.get("source_stage", ""),
            "sha256": artifact["sha256"],
            "size": artifact["size"],
            "object_key": f"{artifact['upload_path']}/{artifact['file_name']}",
            "synced_at": now,
        }
    write_status(feature_dir, status)
    append_sync_hook_log(
        feature_dir,
        feature=str(event.get("feature", "")),
        status="success",
        event_id=event_id,
        message=f"{event.get('source_stage', '')} 产物上传成功，共 {len(artifacts)} 个文件",
    )


def execute_event(workspace: Path, feature: str, event_id: str, project_code: str) -> int:
    feature_dir = resolve_feature_dir(workspace, feature)
    if feature_dir is None:
        print(f"Feature 目录不存在: {feature}", file=sys.stderr)
        return 1

    event = start_attempt(feature_dir, event_id)
    if event is None:
        print(f"同步事件不存在: {event_id}", file=sys.stderr)
        return 1

    try:
        artifacts, missing = refresh_event_snapshot(
            workspace=workspace,
            feature_dir=feature_dir,
            project_code=project_code,
            event=event,
        )
    except Exception as exc:
        return fail_event(feature_dir, event_id, f"无法刷新同步产物清单: {exc}")

    if missing:
        append_sync_hook_log(
            feature_dir,
            feature=feature,
            status="missing",
            event_id=event_id,
            message="部分产物缺失，仅同步已存在产物: " + ", ".join(missing),
        )
        if not artifacts:
            return fail_event(feature_dir, event_id, f"缺少待上传产物: {', '.join(missing)}")
    errors = preflight_errors(artifacts)
    if errors:
        return fail_event(feature_dir, event_id, "\n".join(errors))

    for artifact in artifacts:
        ok, error = upload_file(artifact)
        if not ok:
            return fail_event(feature_dir, event_id, error)

    complete_event(
        feature_dir,
        event_id=event_id,
        event=event,
        artifacts=artifacts,
    )
    print(f"artifact sync success: event_id={event_id} files={len(artifacts)}")
    return 0


def execute_many(workspace: Path, feature: str, event_ids: list[str], project_code: str) -> int:
    failures = 0
    for event_id in event_ids:
        failures += int(execute_event(workspace, feature, event_id, project_code) != 0)
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload AutobizDevOps Feature artifacts")
    parser.add_argument("--feature", "-f", help="feature name; defaults to FEATURE_ID")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--event-id", help="execute one synchronization event")
    action.add_argument(
        "--retry-failed",
        "--drain-outbox",
        dest="retry_failed",
        action="store_true",
        help="retry pending and failed events (--drain-outbox is a compatibility alias)",
    )
    action.add_argument("--reconcile", action="store_true", help="scan published stages and upload changed artifacts")
    args = parser.parse_args(argv)

    try:
        workspace = get_plugin_output_workspace()
        feature = resolve_env_feature(args.feature, required=True)
    except ValueError as exc:
        print(f"产物同步失败: {exc}", file=sys.stderr)
        return 1

    project_code = str(os.environ.get("PROJECT_CODE") or "").strip()
    if not project_code:
        print("产物同步失败: PROJECT_CODE 未设置", file=sys.stderr)
        return 1
    feature_dir = resolve_feature_dir(workspace, feature)
    if feature_dir is None:
        print(f"产物同步失败: Feature 目录不存在: {feature}", file=sys.stderr)
        return 1

    if args.event_id:
        return execute_event(workspace, feature, args.event_id, project_code)
    if args.retry_failed:
        return execute_many(workspace, feature, pending_event_ids(feature_dir), project_code)

    _, event_ids = prepare_reconcile_events(
        workspace=workspace,
        feature=feature,
        project_code=project_code,
    )
    return execute_many(workspace, feature, event_ids, project_code)


if __name__ == "__main__":
    raise SystemExit(main())
