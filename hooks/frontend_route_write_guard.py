#!/usr/bin/env python3
"""Block business-code writes until task and route gates authorize them."""

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
    feature_dir,
    read_json,
    resolve_frontend_route,
)
from hooks.task_run_integrity import strict_task_run_integrity_error  # noqa: E402
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
BACKEND_CODE_SUFFIXES = {
    ".java",
    ".kt",
    ".kts",
    ".groovy",
    ".scala",
    ".py",
    ".go",
    ".rs",
    ".cs",
    ".rb",
    ".php",
    ".xml",
    ".sql",
    ".properties",
    ".yaml",
    ".yml",
    ".json",
}
BUSINESS_CODE_SUFFIXES = FRONTEND_CODE_SUFFIXES | BACKEND_CODE_SUFFIXES


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


def block(reason: str, *, system_message: str | None = None) -> int:
    print(reason, file=sys.stderr)
    json.dump(
        {
            "decision": "block",
            "reason": reason,
            "systemMessage": system_message or (
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


def is_business_code_path(path: Path) -> bool:
    return path.suffix.lower() in BUSINESS_CODE_SUFFIXES


def is_managed_task_run_path(path: Path, workspace: Path, feature: str) -> bool:
    managed_root = (feature_dir(workspace, feature) / ".task-runs").resolve(strict=False)
    try:
        path.resolve(strict=False).relative_to(managed_root)
        return True
    except ValueError:
        return False


def is_any_task_run_artifact_path(path: Path) -> bool:
    parts = path.resolve(strict=False).parts
    for index, part in enumerate(parts):
        if part != ".autobizdevops":
            continue
        if index + 3 < len(parts) and parts[index + 1] == "features":
            return ".task-runs" in parts[index + 3:]
    return False


def missing_protocol_flags(evidence: dict) -> list[str]:
    required = ("routeSkillReadComplete", "routeTodosCreated", "parserRead")
    return [flag for flag in required if evidence.get(flag) is not True]


def protocol_next_step(feature: str, missing: list[str]) -> str:
    if "routeSkillReadComplete" in missing:
        return (
            "read route SKILL.md to EOF, then run "
            f"`python hooks/resolve_frontend_html_route.py --feature {feature} "
            "--mark route-skill-read-complete --json`"
        )
    if "routeTodosCreated" in missing:
        return (
            "create route write_todos, then run "
            f"`python hooks/resolve_frontend_html_route.py --feature {feature} "
            "--mark route-todos-created --json`"
        )
    if "parserRead" in missing:
        return (
            "read delegated parser, then run "
            f"`python hooks/resolve_frontend_html_route.py --feature {feature} "
            "--mark parser-read --json`"
        )
    return "rerun frontend route protocol"


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
    missing = missing_protocol_flags(evidence)
    if missing:
        return block(
            "frontend route protocol incomplete: "
            f"missing={','.join(missing)}; next={protocol_next_step(feature, missing)}"
        )
    if evidence.get("routeSkillReadComplete") is not True:
        return block("写前端代码前必须完整读取对应 route SKILL.md")
    if evidence.get("routeTodosCreated") is not True:
        return block("写前端代码前必须按 route SKILL.md 创建 write_todos 清单")
    if evidence.get("parserRead") is not True:
        return block("写前端代码前必须由 route SKILL 转交并读取对应 parser 文档")
    return 0


def validate_code_exploration_write(workspace: Path, feature: str) -> int:
    target = feature_dir(workspace, feature)
    active_runs: list[tuple[Path, dict]] = []
    for path in (target / ".task-runs").glob("T*/*.json"):
        payload = read_json(path)
        if (
            payload.get("featureId") == feature
            and payload.get("status") not in {"implemented", "done", "failed", "aborted"}
        ):
            active_runs.append((path, payload))
    if len(active_runs) != 1:
        return block(
            "business code write requires exactly one active task run: "
            f"found={len(active_runs)}; next=run task_runner.py start after code exploration is fresh",
            system_message="业务代码写入前必须存在一个由 task_runner 创建且带当前探索证明的活动 run。",
        )
    run_path, run = active_runs[0]
    integrity_error = strict_task_run_integrity_error(run)
    if integrity_error is not None:
        return block(
            f"active task run authorization invalid: {integrity_error}",
            system_message="活动 task run 不是完整且可验证的 v2 runner 产物，禁止据此授权写代码。",
        )
    if run_path.parent.name != run.get("taskId") or run_path.stem != run.get("runId"):
        return block(
            "active task run path identity mismatch",
            system_message="活动 task run 的路径身份与密封内容不一致，禁止据此授权写代码。",
        )
    if run.get("status") != "started":
        return block(
            f"task run status={run.get('status')} does not authorize source writes",
            system_message="只有 started 状态的活动 task run 可以授权业务源码写入。",
        )
    execution_mode = run.get("executionMode")
    if execution_mode != "code":
        return block(
            f"executionMode={execution_mode} does not permit business source writes",
            system_message="verified_existing 与 external_dependency 任务不得写入业务源码。",
        )
    gate = run.get("explorationGate")
    repository_gates = gate.get("repositories") if isinstance(gate, dict) else None
    if (
        not isinstance(gate, dict)
        or gate.get("source") not in {"current_cache", "inherited_after_recheck"}
        or not isinstance(repository_gates, dict)
        or not repository_gates
    ):
        return block(
            "active task run has no current sealed exploration proof; "
            "next=finish or abort the legacy run, complete code exploration, and start a new run",
            system_message="业务代码写入前必须由当前 start 基于实时缓存密封探索证明。",
        )
    if gate.get("source") == "inherited_after_recheck" and (
        not isinstance(gate.get("inheritedFromRunId"), str)
        or not isinstance(gate.get("observedRepositories"), dict)
    ):
        return block(
            "inherited exploration proof is missing provenance",
            system_message="继承的探索证明必须记录来源 run 和本次实际缓存状态。",
        )
    invalid = {
        repository_id: item.get("status") if isinstance(item, dict) else None
        for repository_id, item in repository_gates.items()
        if not isinstance(item, dict)
        or item.get("status") not in {"fresh", "fresh_with_trusted_changes"}
    }
    if invalid:
        return block(
            f"active task run exploration proof is invalid: {invalid}",
            system_message="活动 task run 的探索证明不是 fresh，禁止写入业务源码。",
        )
    return 0


def validate_frontend_exploration_write(workspace: Path, feature: str) -> int:
    """Compatibility alias for callers using the original frontend-only name."""

    return validate_code_exploration_write(workspace, feature)


def main() -> int:
    raw_input = read_stdin_text()
    if not raw_input.strip():
        return 0
    payload = json.loads(raw_input)
    cwd = Path(payload.get("cwd") or Path.cwd()).resolve(strict=False)
    candidate_paths = [
        normalize_path(raw_path, cwd)
        for raw_path in extract_candidate_paths(payload.get("tool_input", {}))
    ]
    if any(is_any_task_run_artifact_path(path) for path in candidate_paths):
        return block(
            ".task-runs artifacts are runner-owned and cannot be edited directly",
            system_message=".task-runs 只能由 task_runner 原子写入，禁止手工创建或修改。",
        )
    feature = current_feature()
    if not feature:
        return 0
    workspace = workspace_from_payload(payload)
    if workspace is None or current_checkpoint(workspace, feature) != "code_in_progress":
        return 0

    for target_path in candidate_paths:
        if is_frontend_code_path(target_path):
            result = validate_frontend_write(workspace, feature)
            if result:
                return result
        if is_business_code_path(target_path):
            result = validate_code_exploration_write(workspace, feature)
            if result:
                return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
