#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared helpers for JSON artifact writer scripts."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = ROOT / "hooks"
AUTODEV_HOOKS_DIR = ROOT / "skills" / "autodev" / "hooks"
for candidate in (ROOT, HOOKS_DIR, AUTODEV_HOOKS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from hooks.paths import get_plugin_output_workspace  # noqa: E402


POSTCHECK_FAIL_RE = re.compile(r"^POST_SKILL_FAIL\s+skill=(?P<skill>\S+)\s+reason=(?P<reason>\S+)(?P<detail>.*)$")


class WriterError(RuntimeError):
    """Raised for expected writer failures that should be rendered as JSON."""


@dataclass(frozen=True)
class WriterResult:
    ok: bool
    path: Path | None = None
    changed: bool = False
    errors: list[dict[str, str]] | None = None
    data: dict[str, Any] | None = None


def _error(reason: str, detail: str = "") -> dict[str, str]:
    result = {"reason": reason}
    if detail:
        result["detail"] = detail
    return result


def render_result(result: WriterResult) -> int:
    payload: dict[str, Any] = {
        "ok": result.ok,
        "changed": result.changed,
        "errors": result.errors or [],
    }
    if result.path is not None:
        payload["path"] = str(result.path)
    if result.data is not None:
        payload.update(result.data)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
    return 0 if result.ok else 1


def fail(reason: str, detail: str = "", *, path: Path | None = None) -> WriterResult:
    return WriterResult(ok=False, path=path, errors=[_error(reason, detail)])


def resolve_workspace(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        workspace = Path(explicit).expanduser().resolve(strict=False)
        state = workspace / ".autobizdevops" / "state.json"
        if not state.is_file():
            raise WriterError(f"state.json 未找到: {state}")
        return workspace
    try:
        return get_plugin_output_workspace()
    except ValueError as exc:
        raise WriterError(str(exc)) from exc


def resolve_feature(explicit: str | None = None, *, env: dict[str, str] | None = None) -> str:
    values = os.environ if env is None else env
    provided = (explicit or "").strip()
    env_feature = str(values.get("FEATURE_ID", "") or "").strip()
    if provided and env_feature and provided != env_feature:
        raise WriterError(f"--feature 与 FEATURE_ID 不一致: --feature={provided} FEATURE_ID={env_feature}")
    feature = provided or env_feature
    if not feature:
        raise WriterError("feature 不能为空；请传 --feature 或设置 FEATURE_ID")
    if Path(feature).is_absolute() or ".." in Path(feature).parts or "/" in feature or "\\" in feature:
        raise WriterError(f"feature 不能包含路径分隔符: {feature}")
    return feature


def feature_dir(workspace: Path, feature: str) -> Path:
    return workspace / ".autobizdevops" / "features" / feature


def artifact_path(workspace: Path, feature: str, name: str) -> Path:
    return feature_dir(workspace, feature) / name


def load_json(path: Path, *, default: Any | None = None) -> Any:
    if not path.is_file() or path.stat().st_size <= 0:
        if default is not None:
            return default
        raise WriterError(f"JSON 产物不存在: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WriterError(f"JSON 产物格式错误: {path}:{exc}") from exc


def atomic_write_json(path: Path, data: Any) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    if old == content:
        return False
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        tmp_path = Path(handle.name)
        handle.write(content)
    tmp_path.replace(path)
    return True


def write_text(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not content.endswith("\n"):
        content += "\n"
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    if old == content:
        return False
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        tmp_path = Path(handle.name)
        handle.write(content)
    tmp_path.replace(path)
    return True


def next_numbered_id(existing: list[str] | set[str], prefix: str, width: int = 3) -> str:
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d{{{width}}})$")
    highest = 0
    for value in existing:
        match = pattern.fullmatch(str(value))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}-{highest + 1:0{width}d}"


def string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise WriterError("字段必须是字符串数组")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise WriterError("字段必须是非空字符串数组")
        result.append(item.strip())
    return result


def parse_json_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WriterError(f"参数不是合法 JSON: {raw}") from exc


def read_object_file(path: str | Path) -> dict[str, Any]:
    data = load_json(Path(path).expanduser().resolve(strict=False))
    if not isinstance(data, dict):
        raise WriterError(f"JSON 文件顶层必须是 object: {path}")
    return data


def parse_postcheck_output(output: str, *, fallback_message: str = "") -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = POSTCHECK_FAIL_RE.match(line)
        if match:
            detail = match.group("detail").strip()
            errors.append(
                {
                    "skill": match.group("skill"),
                    "reason": match.group("reason"),
                    "detail": detail,
                }
            )
        elif line.startswith("POST_SKILL_FAIL"):
            errors.append(_error("postcheck_failure", line))
    if not errors and fallback_message:
        errors.append(_error("postcheck_failure", fallback_message))
    return errors


def capture_stdout(func: Callable[[], tuple[int, str]]) -> tuple[int, str, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code, message = func()
    return code, message, output.getvalue()

