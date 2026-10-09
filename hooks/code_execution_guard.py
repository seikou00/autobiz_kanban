#!/usr/bin/env python3
"""Enforce Code production compilation and Batch UTest validation boundaries.

The fixed Code Workflow gives backend implementation a production-only
compile gate before task completion; behavioral tests and other validation
belong to UTest. Prompt wording is not sufficient by itself: an implementation
or review agent can still run broader commands. This pre-tool hook identifies
active Code task worktrees and fixed-workflow Batch worktrees. A Batch may run
validation only after Review passed and while its durable ``test`` stage is
running. Active Code/repair tasks may compile production sources, and read-only
version/help queries are allowed. Review never inherits Code compile authority.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import sys
from pathlib import Path
from typing import Any


ACTIVE_CODE_RUN_STATUSES = frozenset({"started", "in_progress", "implementation_recording"})

# Preserve quoted shell operators until after control-flow/redirection parsing.
# This is a bounded command recognizer, not a general shell interpreter.
SHELL_TOKEN_RE = re.compile(
    r'''(?:[^\s;&|<>()'"\\]+|\\[^\n]|"(?:\\.|[^"\\])*"|'[^']*')+|[;&|<>()]+|\n+'''
)

# Keep this focused on executable validation entrypoints rather than generic
# commands such as git or package-manager inspection.
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
    path = raw.strip().strip('"').strip("'").replace("\\", "/")
    if not path:
        return ""
    # Git Bash and Windows may describe the same worktree using /d/... or D:/...
    path = re.sub(r"^/([a-zA-Z])/(?=.)", r"\1:/", path)
    return posixpath.normpath(path).casefold()


def _active_code_workspaces() -> dict[str, list[dict[str, str]]]:
    feature_dir = _feature_dir()
    if feature_dir is None:
        return {}

    active: dict[str, list[dict[str, str]]] = {}
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
                active.setdefault(normalized, []).append({
                    "identity": identity,
                    "parallelRunId": str(state.get("parallelRunId") or ""),
                    "batchId": str(state.get("batchId") or ""),
                })
    return active


def _parallel_batch_workspaces() -> dict[str, dict[str, str | bool]]:
    """Return fixed-workflow Batch worktrees and their durable test authority.

    ``task_runner.py finish-implementation`` deliberately completes the Code
    task before Review begins. Looking only at ``.task-runs`` therefore leaves
    a gap in Review. The parallel manifest is the durable authority for the
    Batch stage, including across agent restarts.
    """
    feature_dir = _feature_dir()
    if feature_dir is None:
        return {}

    batches_by_workspace: dict[str, dict[str, str | bool]] = {}
    for manifest_path in feature_dir.glob(".parallel-runs/*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        run_id = str(manifest.get("runId") or manifest_path.parent.name)
        batches = manifest.get("batches")
        if not isinstance(batches, dict):
            continue
        for batch_id, batch in batches.items():
            if not isinstance(batch, dict):
                continue
            workspace = _normalize_path(batch.get("worktreePath"))
            if not workspace:
                continue
            states = _as_dict(batch.get("stageStates"))
            review = _as_dict(states.get("review"))
            test = _as_dict(states.get("test"))
            implement = _as_dict(states.get("implement"))
            stage = str(batch.get("activeStage") or "none")
            batches_by_workspace[workspace] = {
                "identity": f"{run_id}:{batch_id}",
                "runId": run_id,
                "batchId": str(batch_id),
                "testRunning": (
                    stage == "test"
                    and review.get("status") == "passed"
                    and test.get("status") == "running"
                ),
                # Initial implementation and controlled repair can precede
                # durable stage registration. A completed implementation or
                # a running Review/UTest must not inherit stale Code authority.
                "codeAllowed": (
                    stage in {"none", "implement"}
                    and review.get("status") != "running"
                    and test.get("status") != "running"
                    and (stage == "implement" or implement.get("status") != "passed")
                ),
                "stage": stage,
            }
    return batches_by_workspace


def _command_workspace_matches(
    payload: dict[str, Any], tool_input: dict[str, Any], command: str, workspace: str
) -> bool:
    invocation = _shell_invocation(command)
    if invocation is not None:
        directory = _execution_directory(payload, tool_input, invocation[1])
        return directory == workspace or directory.startswith(workspace + "/")
    # Unrecognized commands still need blocking coverage. This fallback is
    # never used to grant the compile/query exception.
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


def _executable(token: str) -> str:
    return token.replace("\\", "/").rsplit("/", 1)[-1].casefold().removesuffix(".cmd").removesuffix(".bat")


def _shell_words(command: str) -> list[str] | None:
    if any(marker in command for marker in ("$(", "`", "<(", ">(")):
        return None
    words: list[str] = []
    end = 0
    for match in SHELL_TOKEN_RE.finditer(command):
        if command[end:match.start()].strip(" \t\r"):
            return None
        words.append("\n" if match.group().startswith("\n") else match.group())
        end = match.end()
    return words if not command[end:].strip(" \t\r") else None


def _word(raw: str) -> str:
    return shlex.split(raw)[0]


def _without_output_redirects(words: list[str]) -> list[str] | None:
    result: list[str] = []
    index = 0
    while index < len(words):
        token = words[index]
        if token in {"1", "2"} and index + 1 < len(words) and words[index + 1] in {">", ">>", ">&"}:
            index += 1
            token = words[index]
        if token in {">", ">>", ">&", "&>"}:
            if index + 1 >= len(words):
                return None
            target = words[index + 1]
            if target in {"|", ">", ">>", ">&", "&>"} or (token == ">&" and target not in {"1", "2"}):
                return None
            index += 2
            continue
        result.append(_word(token))
        index += 1
    return result


def _output_filter(argv: list[str]) -> bool:
    if not argv:
        return False
    executable = _executable(argv[0])
    if executable in {"head", "tail"}:
        args = argv[1:]
        return not args or (
            len(args) == 1 and re.fullmatch(r"-(?:\d+|[nc][+-]?\d+)", args[0]) is not None
        ) or (
            len(args) == 2 and args[0] in {"-n", "-c"}
            and re.fullmatch(r"[+-]?\d+", args[1]) is not None
        )
    if executable == "tee":
        return all(not arg.startswith("-") or arg in {"-a", "--append", "-i", "--ignore-interrupts", "--"} for arg in argv[1:])
    return False


def _shell_invocation(command: str, depth: int = 0) -> tuple[list[str], str] | None:
    """Extract one foreground invocation, optional cd, and output-only pipes."""
    if depth > 3:
        return None
    words = _shell_words(command)
    if not words:
        return None
    try:
        directory = ""
        while words:
            if len(words) >= 4 and [_word(word) for word in words[:3]] == ["set", "-o", "pipefail"] and words[3] in {"&&", ";", "\n"}:
                words = words[4:]
            elif _word(words[0]) == "cd":
                path_index = 2 if len(words) > 1 and words[1] == "--" else 1
                if len(words) <= path_index + 1 or words[path_index + 1] != "&&":
                    return None
                path = _normalize_path(_word(words[path_index]))
                if not path or "$" in path:
                    return None
                directory = path if _absolute_directory(path) else posixpath.join(directory, path)
                words = words[path_index + 2:]
            else:
                break
        if not words:
            return None
        if _executable(_word(words[0])) in {"bash", "sh", "zsh"}:
            if len(words) != 3 or words[1] not in {"-c", "-lc", "-cl"}:
                return None
            inner = _shell_invocation(_word(words[2]), depth + 1)
            if inner is None:
                return None
            inner_directory = inner[1]
            return inner[0], inner_directory if _absolute_directory(inner_directory) else posixpath.join(directory, inner_directory)
        pipelines: list[list[str]] = [[]]
        for word in words:
            if word == "|":
                pipelines.append([])
            elif word and all(char in ";&|<>()\n" for char in word) and word not in {">", ">>", ">&", "&>"}:
                return None
            else:
                pipelines[-1].append(word)
        argv = [_without_output_redirects(segment) for segment in pipelines]
        if not argv[0] or any(not segment or not _output_filter(segment) for segment in argv[1:]):
            return None
        return argv[0], directory
    except (ValueError, IndexError):
        return None


def _absolute_directory(path: str) -> bool:
    return path.startswith("/") or re.match(r"^[a-z]:/", path) is not None


def _execution_directory(payload: dict[str, Any], tool_input: dict[str, Any], cd: str) -> str:
    cwd = next((value for value in (
        tool_input.get("cwd"), tool_input.get("workdir"), tool_input.get("workingDirectory"),
        payload.get("cwd"), payload.get("workdir"),
    ) if isinstance(value, str) and value.strip()), "")
    base = _normalize_path(cwd)
    if cd:
        return _normalize_path(cd if _absolute_directory(cd) else posixpath.join(base, cd))
    return base


def _is_environment_query(argv: list[str]) -> bool:
    executable = _executable(argv[0])
    lookup_args = argv[1:]
    if executable in {"command", "type"}:
        if not lookup_args or lookup_args[0] not in {"-v", "-a"}:
            return False
        lookup_args = lookup_args[1:]
    if executable in {"which", "where", "command", "type"}:
        return bool(lookup_args) and all(
            _executable(arg) in {"mvn", "mvnw", "gradle", "gradlew", "go", "cargo"}
            for arg in lookup_args
        )
    if executable in {"mvn", "mvnw", "gradle", "gradlew", "cargo"}:
        return len(argv) == 2 and argv[1] in {"-v", "-V", "--version", "-version", "-h", "--help"}
    return executable == "go" and argv[1:] == ["version"]


def _is_backend_production_compile(command: str) -> bool:
    invocation = _shell_invocation(command)
    return invocation is not None and _production_compile_argv(invocation[0])


def _production_compile_argv(tokens: list[str]) -> bool:
    if any("$" in token for token in tokens):
        return False
    executable = _executable(tokens[0])
    if executable in {"mvn", "mvnw"}:
        goals: set[str] = set()
        skip_next = False
        value_options = {"-f", "--file", "-pl", "--projects", "-P", "--define", "-s", "--settings", "-t", "--toolchains"}
        for token in tokens[1:]:
            if skip_next:
                skip_next = False
                continue
            if token in value_options:
                skip_next = True
                continue
            if not token.startswith("-"):
                goals.add(token)
        return not skip_next and "compile" in goals and goals <= {"compile", "clean"}
    if executable in {"gradle", "gradlew"}:
        tasks = {token.rsplit(":", 1)[-1] for token in tokens[1:] if not token.startswith("-")}
        return bool(tasks) and tasks <= {
            "classes", "compileJava", "compileKotlin", "compileGroovy", "compileScala"
        }
    if executable == "go":
        return len(tokens) >= 2 and tokens[1] == "build" and not any(
            token in {"test", "vet", "run", "install"} for token in tokens[1:]
        )
    if executable == "cargo":
        return len(tokens) >= 2 and tokens[1] == "check" and not any(
            token in {"test", "build", "clippy", "bench"} for token in tokens[1:]
        )
    return False


def guard(payload: dict[str, Any]) -> str | None:
    """Return a blocking reason unless validation is authorized by UTest."""
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").lower()
    if tool_name not in {"execute", "bash", "shell"}:
        return None
    tool_input = _tool_input(payload)
    command = _command(payload, tool_input)
    if not command or not FORBIDDEN_COMMAND_RE.search(command):
        return None
    invocation = _shell_invocation(command)
    if invocation is not None and _is_environment_query(invocation[0]):
        return None
    compile_only = invocation is not None and _production_compile_argv(invocation[0])
    active = _active_code_workspaces()
    matching_runs = [
        run for workspace, runs in active.items()
        if _command_workspace_matches(payload, tool_input, command, workspace)
        for run in runs
    ]
    batches = _parallel_batch_workspaces()
    for workspace in sorted(batches, key=len, reverse=True):
        batch = batches[workspace]
        if not _command_workspace_matches(payload, tool_input, command, workspace):
            continue
        if batch["testRunning"] is True:
            return None
        if compile_only and batch["codeAllowed"] is True and any(
            run["parallelRunId"] == batch["runId"] and run["batchId"] == batch["batchId"]
            for run in matching_runs
        ):
            return None
        return (
            "BATCH_STAGE_VALIDATION_FORBIDDEN: Batch {identity} 当前阶段为 {stage}；"
            "环境查询允许执行；生产纯编译仅允许在当前 Batch 绑定的活动 Code/修复 task 中执行；"
            "Review 禁止验证，其余构建、typecheck、lint、测试和 E2E 仅允许在 Review 已通过且 test 阶段正在运行时执行。"
        ).format(**batch)
    if compile_only and matching_runs:
        return None
    if matching_runs:
        return (
            "CODE_EXECUTION_VALIDATION_FORBIDDEN: 活动 Code task run {} 的 worktree 禁止执行构建、编译、"
            "typecheck、lint、测试或 E2E 命令（环境查询和后端 production-only compile 除外）；"
            "纯编译支持 cd、输出重定向及 head/tail/tee 管道，不允许附加其他命令或后台执行；"
            "其余验证由 Review 通过后的 UTest 阶段运行。"
        ).format(matching_runs[0]["identity"])
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
