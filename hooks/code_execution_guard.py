#!/usr/bin/env python3
"""Block validation commands inside an active Code task run.

The fixed Code Workflow deliberately gives UTest exclusive ownership of
compilation, build, lint, typecheck, test and E2E commands.  Prompt wording is
not sufficient by itself: an implementation agent can still decide to run a
local build "for verification".  This pre-tool hook identifies the active
task's recorded worktree and rejects those commands only in that worktree, so a
different Batch already in UTest remains unaffected.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


ACTIVE_CODE_RUN_STATUSES = frozenset({"started", "in_progress", "implementation_recording"})

# The runner owns validation in UTest.  Keep this intentionally focused on
# executable validation entrypoints rather than generic commands such as git
# or package-manager inspection.
FORBIDDEN_COMMAND_RE = re.compile(
    r"(?:"
    r"(?<![\w.-])(?:mvn|mvnw(?:\.cmd)?|gradle|gradlew(?:\.bat)?)(?![\w.-])"
    r"|(?<![\w.-])(?:npm|pnpm|yarn)(?:\.cmd)?\s+(?:(?:run|exec)\s+)?(?:build|compile|typecheck|lint|test|check)(?![\w.-])"
    r"|(?<![\w.-])(?:npx\s+)?(?:vite|webpack|tsc|vue-tsc|eslint|jest|vitest|pytest)(?![\w.-])"
    r"|(?<![\w.-])go\s+(?:build|test|vet)(?![\w.-])"
    r"|(?<![\w.-])cargo\s+(?:build|check|test|clippy)(?![\w.-])"
    r")",
    re.IGNORECASE,
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(payload.get("tool_input") or payload.get("input"))


def _command(payload: dict[str, Any], tool_input: dict[str, Any]) -> str:
    for value in (
        tool_input.get("command"),
        tool_input.get("cmd"),
        tool_input.get("script"),
        payload.get("command"),
        payload.get("cmd"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _workspace_root() -> Path | None:
    plugin_workspace = str(os.environ.get("PLUGIN_WORKSPACE", "") or "").strip()
    project = str(os.environ.get("PROJECT_DIR", "") or os.environ.get("PROJECT_CODE", "") or "").strip()
    if not plugin_workspace or not project or "/" in project or "\\" in project:
        return None
    return (Path(plugin_workspace).expanduser() / project).resolve(strict=False)


def _feature_dir() -> Path | None:
    workspace = _workspace_root()
    feature = str(os.environ.get("FEATURE_ID", "") or "").strip()
    if workspace is None or not feature:
        return None
    return workspace / ".autobizdevops" / "features" / feature


def _normalize_path(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip().strip('"').strip("'").replace("\\", "/").rstrip("/").casefold()


def _active_code_workspaces() -> dict[str, str]:
    feature_dir = _feature_dir()
    if feature_dir is None:
        return {}

    active: dict[str, str] = {}
    for run_path in feature_dir.glob(".task-runs/*/*.json"):
        try:
            state = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict) or state.get("status") not in ACTIVE_CODE_RUN_STATUSES:
            continue
        identity = "{}:{}".format(
            state.get("taskId", run_path.parent.name),
            state.get("runId", run_path.stem),
        )
        candidates = [state.get("codeWorkspace")]
        for key in ("requestedCodeWorkspaces", "resolvedGitRoots"):
            value = state.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        for candidate in candidates:
            normalized = _normalize_path(candidate)
            if normalized:
                active[normalized] = identity
    return active


def _command_workspace_matches(
    payload: dict[str, Any], tool_input: dict[str, Any], command: str, workspace: str
) -> bool:
    normalized_command = command.replace("\\", "/").casefold()
    if workspace in normalized_command:
        return True
    for candidate in (
        tool_input.get("cwd"),
        tool_input.get("workdir"),
        tool_input.get("workingDirectory"),
        payload.get("cwd"),
        payload.get("workdir"),
    ):
        normalized = _normalize_path(candidate)
        if normalized == workspace or normalized.startswith(workspace + "/"):
            return True
    return False


def guard(payload: dict[str, Any]) -> str | None:
    """Return a blocking reason for Code-stage validation, else ``None``."""
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").lower()
    if tool_name not in {"execute", "bash", "shell"}:
        return None
    tool_input = _tool_input(payload)
    command = _command(payload, tool_input)
    if not command or not FORBIDDEN_COMMAND_RE.search(command):
        return None
    for workspace, run_identity in _active_code_workspaces().items():
        if _command_workspace_matches(payload, tool_input, command, workspace):
            return (
                "CODE_EXECUTION_VALIDATION_FORBIDDEN: 活动 Code task run {} 的 worktree 禁止执行构建、编译、"
                "typecheck、lint、测试或 E2E 命令；请只完成实现并执行 task_runner.py "
                "finish-implementation，Review 通过后由 UTest 阶段运行验证。"
            ).format(run_identity)
    return None


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    reason = guard(payload)
    if reason:
        print(reason)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
