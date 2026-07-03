#!/usr/bin/env python3
"""Block frontend code writes until the autodev-code HTML route is entered."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.state_store import load_state_json_records_result  # noqa: E402
from paths import get_plugin_output_workspace, resolve_project_dir  # noqa: E402
from resolve_frontend_html_route import (  # noqa: E402
    FrontendRouteError,
    ROUTE_ABSOLUTE,
    ROUTE_MISSING,
    ROUTE_NONE,
    ROUTE_SPEC_DRIVEN,
    ROUTE_STANDARD,
    evidence_path,
    read_json,
    resolve_frontend_route,
)
from ui_context import UIContextError, load_ui_context  # noqa: E402


BLOCK_EXIT_CODE = 2
PATH_KEYS = {
    "filePath",
    "file_path",
    "path",
    "filename",
    "absolutePath",
    "relativePath",
}
FRONTEND_CODE_SUFFIXES = {
    ".tsx",
    ".jsx",
    ".vue",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".styl",
    ".ts",
    ".js",
    ".mjs",
    ".cjs",
}


def read_stdin_text() -> str:
    raw = sys.stdin.buffer.read()
    if not raw:
        return ""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode(sys.stdin.encoding or "utf-8", errors="replace")


def path_from_uri(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "file":
        return value
    return unquote(parsed.path)


def normalize_path(path: str, cwd: Path) -> Path:
    normalized = Path(path_from_uri(path).replace("\\", "/")).expanduser()
    if not normalized.is_absolute():
        normalized = cwd / normalized
    return normalized.resolve(strict=False)


def extract_candidate_paths(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    paths: list[str] = []
    for key in PATH_KEYS:
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            paths.append(raw)
        elif isinstance(raw, list):
            paths.extend(item for item in raw if isinstance(item, str) and item.strip())
    return paths


def workspace_from_payload(payload: dict) -> Path | None:
    plugin_workspace = str(os.environ.get("PLUGIN_WORKSPACE") or "").strip()
    project_code = resolve_project_dir(os.environ)
    if plugin_workspace and project_code and "/" not in project_code and "\\" not in project_code:
        return (Path(plugin_workspace).expanduser() / project_code).resolve(strict=False)
    try:
        return get_plugin_output_workspace()
    except ValueError:
        workspace = os.environ.get("WORKSPACE_PATH") or payload.get("cwd")
        return Path(workspace).resolve(strict=False) if workspace else None


def current_feature() -> str:
    return str(os.environ.get("FEATURE_ID") or "").strip()


def current_checkpoint(workspace: Path, feature: str) -> str:
    result = load_state_json_records_result(workspace)
    if not result.exists or result.errors:
        return ""
    record = result.records.get(feature)
    if not isinstance(record, dict):
        return ""
    checkpoint = record.get("checkpoint", "")
    return checkpoint if isinstance(checkpoint, str) else ""


def block(reason: str) -> int:
    print(reason, file=sys.stderr)
    json.dump(
        {
            "decision": "block",
            "reason": reason,
            "systemMessage": (
                "前端代码生成必须先进入 with-absolute-html 或 with-standard-html：运行 route resolver、"
                "完整读取 route SKILL.md，创建 route write_todos，并由 route SKILL 转交 parser 后再写前端源码。"
            ),
        },
        sys.stdout,
        ensure_ascii=False,
    )
    return BLOCK_EXIT_CODE


def is_frontend_code_path(path: Path) -> bool:
    return path.suffix.lower() in FRONTEND_CODE_SUFFIXES


def validate_frontend_write(workspace: Path, feature: str) -> int:
    try:
        ui_context = load_ui_context(workspace / ".autobizdevops" / "features" / feature)
    except UIContextError as exc:
        return block(f"UI_CONTEXT.json 非法，无法解析前端 route: {exc}")
    if isinstance(ui_context, dict) and ui_context.get("uiRequired") is False:
        return block("UI_CONTEXT.json 标记 uiRequired=false，当前任务不允许写前端业务代码")

    evidence_file = evidence_path(workspace, feature)
    evidence = read_json(evidence_file)
    if not evidence:
        try:
            resolved = resolve_frontend_route(workspace, feature, write_evidence=False)
        except FrontendRouteError as exc:
            return block(f"UI_CONTEXT.json 非法，无法解析前端 route: {exc}")
        if resolved.get("source") == "UI_CONTEXT.json" and resolved.get("route") == ROUTE_NONE:
            return block("UI_CONTEXT.json 标记 uiRequired=false，当前任务不允许写前端业务代码")
        if resolved.get("triggered"):
            return block(f"写前端代码前缺少 FRONTEND_ROUTE.json: {evidence_file}")
        return 0

    route = evidence.get("route")
    if route == ROUTE_NONE and evidence.get("source") == "UI_CONTEXT.json":
        return block("UI_CONTEXT.json 标记 uiRequired=false，当前任务不允许写前端业务代码")
    if route == ROUTE_SPEC_DRIVEN:
        return 0
    if route == ROUTE_MISSING and evidence.get("source") == "UI_CONTEXT.json":
        return 0
    if route not in {ROUTE_ABSOLUTE, ROUTE_STANDARD}:
        return block(f"当前 frontend route 不允许写前端代码: {route}")
    if evidence.get("routeSkillReadComplete") is not True:
        return block("写前端代码前必须完整读取对应 route SKILL.md")
    if evidence.get("routeTodosCreated") is not True:
        return block("写前端代码前必须按 route SKILL.md 创建 write_todos 清单")
    if evidence.get("parserRead") is not True:
        return block("写前端代码前必须由 route SKILL 转交并读取对应 parser 文档")
    return 0


def main() -> int:
    raw_input = read_stdin_text()
    if not raw_input.strip():
        return 0
    payload = json.loads(raw_input)
    feature = current_feature()
    if not feature:
        return 0
    workspace = workspace_from_payload(payload)
    if workspace is None or current_checkpoint(workspace, feature) != "code_in_progress":
        return 0

    cwd = Path(payload.get("cwd") or Path.cwd()).resolve(strict=False)
    tool_input = payload.get("tool_input", {})
    for raw_path in extract_candidate_paths(tool_input):
        if is_frontend_code_path(normalize_path(raw_path, cwd)):
            result = validate_frontend_write(workspace, feature)
            if result:
                return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
