#!/usr/bin/env python3
"""Command-line entrypoint and HTTP transport for the artifact sync skill."""

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

from artifact_sync_utils import SyncSession  # noqa: E402
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload configured AutobizDevOps Feature artifacts")
    parser.add_argument("--feature", "-f", help="feature name; defaults to FEATURE_ID")
    parser.add_argument("--employee-id", required=True, help="confirmed employee ID; preserved as a string")
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
        session = SyncSession(workspace, feature, project_code, args.employee_id)
        if args.check:
            print(json.dumps({"feature": feature, "checkpoint": session.checkpoint,
                              "feature_dir": str(session.feature_dir), "employee_id": session.employee_id,
                              "upload_base": session.upload_base}, ensure_ascii=False))
            return 0
        if args.event_id:
            event_ids = [args.event_id]
        elif args.retry_failed:
            event_ids = session.pending_event_ids()
        else:
            event_ids = session.prepare()
        if args.prepare_only:
            print(json.dumps({"feature": feature, "employee_id": session.employee_id,
                              "upload_base": session.upload_base, "event_ids": event_ids,
                              "uploaded": False}, ensure_ascii=False))
            return 0
        if not event_ids:
            print("没有待上传的产物或目录。")
        return session.execute(event_ids, upload_file)
    except (OSError, ValueError) as exc:
        print(f"产物同步失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
