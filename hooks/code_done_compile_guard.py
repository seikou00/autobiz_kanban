#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-execute guard that compiles declared modules before code_done."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paths import (
    STATE_SCRIPTS_WORKSPACE_ARGUMENT_ERROR,
    contains_workspace_argument,
    get_plugin_output_workspace,
)


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
        if checkpoint != "code_done":
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
            "systemMessage": "code_done 前模块编译失败。请修复编译错误或更新 .autobizdevops/modules_compile.json 后重试。",
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


def run_compile(module: CompileModule) -> tuple[bool, str]:
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

    try:
        workspace = get_plugin_output_workspace()
    except ValueError as exc:
        return block(f"code_done 编译校验失败: {exc}")

    modules_path = workspace / MODULES_COMPILE_RELATIVE_PATH
    try:
        modules = load_modules(modules_path)
    except ValueError as exc:
        return block(str(exc))

    errors: list[str] = []
    for module in modules:
        ok, message = run_compile(module)
        if not ok:
            errors.append(message)

    if errors:
        return block("\n\n".join(errors))

    print(f"code_done 模块编译校验通过: {len(modules)} 个模块")
    return 0


def main() -> int:
    raw_input = sys.stdin.read()
    if not raw_input.strip():
        return 0
    try:
        payload = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        return block(f"code_done 编译校验失败: hook payload JSON 非法: {exc}")
    if not isinstance(payload, dict):
        return 0
    return run_guard(payload)


if __name__ == "__main__":
    raise SystemExit(main())
