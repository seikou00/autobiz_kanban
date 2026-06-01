#!/usr/bin/env python3
"""Pre-tool guard that validates workspace init before reading plugin files."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from init_validate import validate_precheck
from paths import get_plugin_output_workspace


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BLOCK_EXIT_CODE = 2
PATH_KEYS = {
    "filePath",
    "file_path",
    "path",
    "filename",
    "absolutePath",
    "relativePath",
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


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


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


def plugin_read_paths(payload: dict, plugin_root: Path = PLUGIN_ROOT) -> list[Path]:
    tool_input = payload.get("tool_input", {})
    cwd = Path(payload.get("cwd") or Path.cwd()).resolve(strict=False)
    root = plugin_root.resolve(strict=False)

    matches: list[Path] = []
    for raw_path in extract_candidate_paths(tool_input):
        candidate = normalize_path(raw_path, cwd)
        if is_relative_to(candidate, root):
            matches.append(candidate)
    return matches


def workspace_from_payload(payload: dict) -> Path:
    plugin_workspace = str(os.environ.get("PLUGIN_WORKSPACE") or "").strip()
    project_code = str(os.environ.get("PROJECT_CODE") or "").strip()
    if plugin_workspace and project_code and "/" not in project_code and "\\" not in project_code:
        return (Path(plugin_workspace).expanduser() / project_code).resolve(strict=False)

    try:
        return get_plugin_output_workspace()
    except ValueError:
        workspace = os.environ.get("WORKSPACE_PATH") or payload.get("cwd")
        return Path(workspace).resolve(strict=False)


def format_precheck_reason(result: dict) -> str:
    lines = [result.get("message", "前置检查未通过")]
    lines.extend(str(error) for error in result.get("errors", []))
    return "\n".join(lines)


def block(reason: str, workspace: Path) -> int:
    print(reason, file=sys.stderr)
    init_script = f"{PLUGIN_ROOT}/hooks/init_workspace.py"
    json.dump(
        {
            "decision": "block",
            "reason": reason,
            "systemMessage": f"继续任务前需要先执行python {init_script} {workspace}",
        },
        sys.stdout,
        ensure_ascii=False,
    )
    return BLOCK_EXIT_CODE


def main() -> int:
    raw_input = read_stdin_text()
    if not raw_input.strip():
        return 0

    payload = json.loads(raw_input)
    matches = plugin_read_paths(payload)
    if matches:
        workspace = workspace_from_payload(payload)
        result = validate_precheck(workspace)
        if not result.get("ok"):
            return block(format_precheck_reason(result), workspace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
