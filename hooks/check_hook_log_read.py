#!/usr/bin/env python3
"""Pre-tool guard that blocks reading hooks.ndjson as a checkpoint source.

hooks.ndjson 是 append-only 的 hook 审计日志：写入失败会被静默吞掉，可能缺行或滞后，
末行看似"最新状态"但不可作为事实源。当前 checkpoint 的唯一权威来源是
read_state_json.py / state.json。本守卫拦截读取动作并把调用方引导回脚本。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BLOCK_EXIT_CODE = 2
HOOK_LOG_FILENAME = "hooks.ndjson"
PATH_KEYS = {
    "filePath",
    "file_path",
    "path",
    "filename",
    "absolutePath",
    "relativePath",
}
# 只拦"读文件内容"的命令；grep/rg/find/ls 等按名字搜索的命令不拦，
# 否则维护本仓库时检索 "hooks.ndjson" 字面量会被误伤。
READER_COMMANDS = {
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "bat",
    "nl",
    "od",
    "xxd",
    "strings",
    "jq",
}
COMMAND_SEPARATORS = re.compile(r"\|\||&&|[|;&\n]")


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


def basename(value: str) -> str:
    return value.rstrip("/").rsplit("/", 1)[-1].lower()


def is_hook_log_path(value: str) -> bool:
    """按文件名判断，覆盖 features/ 与 archive/ 下的所有副本。"""
    normalized = path_from_uri(str(value)).replace("\\", "/").strip().strip("'\"")
    if not normalized:
        return False
    return basename(normalized) == HOOK_LOG_FILENAME


def extract_candidate_paths(tool_input: object) -> list[str]:
    if not isinstance(tool_input, dict):
        return []

    paths: list[str] = []
    for key in PATH_KEYS:
        raw = tool_input.get(key)
        if isinstance(raw, str) and raw.strip():
            paths.append(raw)
        elif isinstance(raw, list):
            paths.extend(item for item in raw if isinstance(item, str) and item.strip())
    return paths


def command_reads_hook_log(command: str) -> bool:
    """命令里出现 hooks.ndjson 且由读取类命令发起时才算读取。"""
    if HOOK_LOG_FILENAME not in command:
        return False

    for segment in COMMAND_SEPARATORS.split(command):
        tokens = segment.split()
        if not tokens:
            continue
        executable = basename(tokens[0])
        if executable not in READER_COMMANDS:
            continue
        if any(is_hook_log_path(token) for token in tokens[1:]):
            return True
    return False


def payload_reads_hook_log(payload: dict) -> bool:
    tool_input = payload.get("tool_input", {})
    if any(is_hook_log_path(path) for path in extract_candidate_paths(tool_input)):
        return True

    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str) and command_reads_hook_log(command):
            return True
    return False


def block_reason() -> str:
    script = f"{PLUGIN_ROOT}/read_state_json.py"
    return (
        "hooks.ndjson 是 append-only 的 hook 审计日志，不是状态源，"
        "不得据其判断或推断当前 checkpoint（写入失败会被静默丢弃，可能缺行或滞后）。"
        "当前 checkpoint 的唯一权威来源是 state.json，请改用脚本读取：\n"
        f'python "{script}" --feature "${{feature}}"'
    )


def block(reason: str) -> int:
    print(reason, file=sys.stderr)
    json.dump(
        {
            "decision": "block",
            "reason": reason,
            "systemMessage": reason,
        },
        sys.stdout,
        ensure_ascii=False,
    )
    return BLOCK_EXIT_CODE


def main() -> int:
    raw_input = read_stdin_text()
    if not raw_input.strip():
        return 0

    try:
        payload = json.loads(raw_input)
    except json.JSONDecodeError:
        # 守卫不能因为脏输入阻断正常工具调用
        return 0

    if not isinstance(payload, dict):
        return 0

    if payload_reads_hook_log(payload):
        return block(block_reason())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
