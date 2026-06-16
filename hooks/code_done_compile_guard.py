#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-execute guard helpers for code_done module compilation."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = ROOT / "hooks"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from paths import (
    STATE_SCRIPTS_WORKSPACE_ARGUMENT_ERROR,
    contains_workspace_argument,
    get_plugin_output_workspace,
    resolve_env_feature,
)
from board_core.state_store import load_state_json_records_result
from state_checkpoint import append_checkpoint_hook_logs


BLOCK_EXIT_CODE = 2
MODULES_COMPILE_RELATIVE_PATH = Path(".autobizdevops") / "modules_compile.json"
COMPILE_TIMEOUT_SECONDS = 1800
OUTPUT_TAIL_LIMIT = 4000


@dataclass(frozen=True)
class CheckpointCommand:
    checkpoint: str


@dataclass(frozen=True)
class CompileModule:
    module: str
    path: Path
    compile_command: str


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            return " ".join(str(item) for item in value).strip()
    return ""


def command_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def command_variants(command: str) -> list[str]:
    variants = [command]
    tokens = command_words(command)
    for index, token in enumerate(tokens):
        if token in {"-c", "-lc"} and index + 1 < len(tokens):
            variants.append(tokens[index + 1])
    return variants


def extract_command(payload: dict[str, Any]) -> str:
    tool_input = as_dict(payload.get("tool_input") or payload.get("input"))
    return first_text(
        tool_input.get("command"),
        tool_input.get("cmd"),
        tool_input.get("script"),
        payload.get("command"),
        payload.get("cmd"),
    )


def extract_cwd(payload: dict[str, Any]) -> Path:
    tool_input = as_dict(payload.get("tool_input") or payload.get("input"))
    raw = first_text(
        tool_input.get("cwd"),
        tool_input.get("workdir"),
        tool_input.get("working_directory"),
        payload.get("cwd"),
        payload.get("working_directory"),
    )
    return Path(raw).expanduser().resolve(strict=False) if raw else Path.cwd().resolve(strict=False)


def option_value(tokens: list[str], *names: str) -> str:
    for index, token in enumerate(tokens):
        for name in names:
            if token == name and index + 1 < len(tokens):
                return tokens[index + 1]
            if token.startswith(name + "="):
                return token.split("=", 1)[1]
    return ""


def has_flag(tokens: list[str], *names: str) -> bool:
    return any(token in names for token in tokens)


def parse_checkpoint_command(command: str) -> CheckpointCommand | None:
    state_scripts = {"read_state_json.py", "update_checkpoint.py"}
    for variant in command_variants(command):
        tokens = command_words(variant)
        script_names = {Path(token).name for token in tokens}
        if script_names & state_scripts and contains_workspace_argument(tokens):
            raise ValueError(STATE_SCRIPTS_WORKSPACE_ARGUMENT_ERROR)
        if "update_checkpoint.py" not in script_names:
            continue

        checkpoint = option_value(tokens, "--checkpoint", "-c")
        if checkpoint != "code_done" or has_flag(tokens, "--dry-run"):
            return None

        return CheckpointCommand(checkpoint=checkpoint)
    return None


def tail_output(stdout: str, stderr: str) -> str:
    combined = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part)
    return combined[-OUTPUT_TAIL_LIMIT:]


def block(reason: str) -> int:
    print(reason, file=sys.stderr)
    json.dump(
        {
            "decision": "block",
            "reason": reason,
            "systemMessage": "状态脚本参数不合法。请删除 --workspace/-w，由插件环境决定状态路径。",
        },
        sys.stdout,
        ensure_ascii=False,
    )
    return BLOCK_EXIT_CODE


def read_modules_compile(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"code_done 编译校验失败: 缺少模块编译清单 {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"code_done 编译校验失败: {path} JSON 非法: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"code_done 编译校验失败: {path} 顶层必须是 JSON object")
    return data


def validate_module_record(raw: Any, index: int) -> CompileModule:
    if not isinstance(raw, dict):
        raise ValueError(f"modules[{index}] 必须是 JSON object")

    module = raw.get("module")
    module_path = raw.get("path")
    compile_command = raw.get("compile_command")

    if not isinstance(module, str) or not module.strip():
        raise ValueError(f"modules[{index}].module 缺失或为空")
    if not isinstance(module_path, str) or not module_path.strip():
        raise ValueError(f"modules[{index}].path 缺失或为空")
    if not isinstance(compile_command, str) or not compile_command.strip():
        raise ValueError(f"modules[{index}].compile_command 缺失或为空")

    path = Path(module_path).expanduser()
    if not path.is_absolute():
        raise ValueError(f"modules[{index}].path 必须是绝对路径: {module_path}")
    path = path.resolve(strict=False)
    if not path.is_dir():
        raise ValueError(f"modules[{index}].path 不存在或不是目录: {path}")

    return CompileModule(module=module.strip(), path=path, compile_command=compile_command.strip())


def load_modules(path: Path) -> list[CompileModule]:
    data = read_modules_compile(path)
    if data.get("version") != 1:
        raise ValueError(f"code_done 编译校验失败: {path} version 必须为 1")

    modules = data.get("modules")
    if not isinstance(modules, list) or not modules:
        raise ValueError(f"code_done 编译校验失败: {path} modules 必须是非空数组")

    try:
        return [validate_module_record(raw, index) for index, raw in enumerate(modules)]
    except ValueError as exc:
        raise ValueError(f"code_done 编译校验失败: {path}: {exc}") from exc


def run_compile(module: CompileModule, *, emit_success: bool = True) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            module.compile_command,
            cwd=str(module.path),
            shell=True,
            text=True,
            capture_output=True,
            timeout=COMPILE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = tail_output(exc.stdout or "", exc.stderr or "")
        detail = f"\n输出尾部:\n{output}" if output else ""
        return (
            False,
            f"模块 {module.module} 编译超时: {module.compile_command} 超过 {COMPILE_TIMEOUT_SECONDS} 秒{detail}",
        )
    except OSError as exc:
        return False, f"模块 {module.module} 编译无法执行: {module.compile_command}: {exc}"

    output = tail_output(result.stdout, result.stderr)
    if result.returncode != 0:
        detail = f"\n输出尾部:\n{output}" if output else ""
        return (
            False,
            f"模块 {module.module} 编译失败: path={module.path} command={module.compile_command} exit_code={result.returncode}{detail}",
        )

    if emit_success:
        summary = output or "(无输出)"
        print(
            "\n".join(
                [
                    f"模块 {module.module} 编译通过",
                    f"path: {module.path}",
                    f"command: {module.compile_command}",
                    "输出摘要:",
                    summary,
                ]
            )
        )
    return True, ""


def validate_modules_compile(workspace: Path, *, emit_success: bool = True) -> tuple[int, list[str]]:
    modules_path = workspace / MODULES_COMPILE_RELATIVE_PATH
    try:
        modules = load_modules(modules_path)
    except ValueError as exc:
        return 0, [str(exc)]

    errors: list[str] = []
    for module in modules:
        ok, message = run_compile(module, emit_success=emit_success)
        if not ok:
            errors.append(message)

    return len(modules), errors


def current_checkpoint(workspace: Path, feature: str) -> str | None:
    result = load_state_json_records_result(workspace)
    if not result.exists or result.errors:
        return None
    record = result.records.get(feature)
    if record is None:
        return None
    return record.get("checkpoint", "")


def write_compile_result(
    workspace: Path,
    feature: str,
    *,
    old_checkpoint: str,
    errors: list[str],
) -> int:
    new_checkpoint = "code_done"
    transition = f"{old_checkpoint or 'empty'} -> {new_checkpoint}"
    if errors:
        reason = "\n".join(errors)
        append_checkpoint_hook_logs(
            workspace,
            [(feature, old_checkpoint, new_checkpoint)],
            event_id="code-compile",
            label="code_done 编译校验",
            errors=errors,
            event_status="blocked",
            exit_code=BLOCK_EXIT_CODE,
            message=f"{transition}: " + reason,
        )
        json.dump(
            {
                "decision": "block",
                "reason": reason,
                "systemMessage": f"code 编译未通过：\n{reason}",
                "additionalContext": f"请将以下编译问题展示给用户：\n{reason}",
            },
            sys.stdout,
            ensure_ascii=False,
        )
        return BLOCK_EXIT_CODE

    append_checkpoint_hook_logs(
        workspace,
        [(feature, old_checkpoint, new_checkpoint)],
        event_id="code-compile",
        label="code_done 编译校验",
        errors=[],
        event_status="success",
        exit_code=0,
        message=f"{transition}: code_done 编译校验通过",
    )
    return 0


def run_code_done_compile_hook() -> int:
    try:
        workspace = get_plugin_output_workspace()
        feature = resolve_env_feature(None, required=True)
    except ValueError:
        return 0

    checkpoint = current_checkpoint(workspace, feature)
    if checkpoint != "code_in_progress":
        return 0

    _, errors = validate_modules_compile(workspace, emit_success=False)
    return write_compile_result(
        workspace,
        feature,
        old_checkpoint=checkpoint,
        errors=errors,
    )


def run_guard(payload: dict[str, Any]) -> int:
    command = extract_command(payload)
    if not command:
        return 0

    try:
        checkpoint_command = parse_checkpoint_command(command)
    except ValueError as exc:
        return block(str(exc))

    if checkpoint_command is None:
        return 0

    return run_code_done_compile_hook()


def main() -> int:
    raw_input = sys.stdin.read()
    if not raw_input.strip():
        return 0
    try:
        payload = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        print(f"code_done 编译校验跳过: hook payload JSON 非法: {exc}", file=sys.stderr)
        return 0
    if not isinstance(payload, dict):
        return 0
    return run_guard(payload)


if __name__ == "__main__":
    raise SystemExit(main())
