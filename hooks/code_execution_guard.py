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
import sys
from pathlib import Path
from typing import Any


ACTIVE_CODE_RUN_STATUSES = frozenset({"started", "in_progress", "implementation_recording"})
GUARD_VERSION = "code-stage-v3"

try:
    from hooks.code_shell_analysis import analyze_script, normalize_path, absolute_path, executable, Invocation
except ImportError:  # Direct hook execution from the plugin hooks directory.
    from code_shell_analysis import analyze_script, normalize_path, absolute_path, executable, Invocation

# Keep this focused on executable validation entrypoints rather than generic
# commands such as git or package-manager inspection.
FORBIDDEN_COMMAND_RE = re.compile(
    r"(?:"
    r"(?<![\w.-])(?:mvn(?:\.cmd)?|mvnw(?:\.cmd)?|gradle(?:\.bat)?|gradlew(?:\.bat)?)(?![\w.-])"
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
    return normalize_path(raw.strip().strip('"').strip("'"))


def _code_workspaces(*, active_only: bool = True) -> dict[str, list[dict[str, str]]]:
    feature_dir = _feature_dir()
    if feature_dir is None:
        return {}

    active: dict[str, list[dict[str, str]]] = {}
    for run_path in feature_dir.glob(".task-runs/*/*.json"):
        try:
            state = json.loads(run_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict) or (active_only and state.get("status") not in ACTIVE_CODE_RUN_STATUSES):
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
                    "status": str(state.get("status") or "unknown"),
                    "codeWorkspace": str(state.get("codeWorkspace") or ""),
                    "parallelRunId": str(state.get("parallelRunId") or ""),
                    "batchId": str(state.get("batchId") or ""),
                })
    return active


def _active_code_workspaces() -> dict[str, list[dict[str, str]]]:
    return _code_workspaces()


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
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
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
                "batchStatus": str(batch.get("status") or "unknown"),
                "implementStatus": str(implement.get("status") or "pending"),
                "reviewStatus": str(review.get("status") or "pending"),
                "testStatus": str(test.get("status") or "pending"),
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
                    and batch.get("status") not in {"retry_pending", "blocked", "failed", "cancelled", "merged", "ready_to_candidate"}
                    and review.get("status") != "running"
                    and test.get("status") != "running"
                    and implement.get("status") not in {"passed", "skipped", "deferred", "needs_triage"}
                ),
                "stage": stage,
            }
    return batches_by_workspace


def _executable(token: str) -> str:
    return executable(token)


def _execution_directory(payload: dict[str, Any], tool_input: dict[str, Any], cd: str = "") -> str:
    cwd = next((value for value in (
        tool_input.get("cwd"), tool_input.get("workdir"), tool_input.get("workingDirectory"),
        payload.get("cwd"), payload.get("workdir"),
    ) if isinstance(value, str) and value.strip()), "")
    base = _normalize_path(cwd)
    root = next((value for value in (
        tool_input.get("workspaceRoot"), tool_input.get("workspace_root"),
        payload.get("workspaceRoot"), payload.get("workspace_root"),
    ) if isinstance(value, str) and value.strip()), "")
    if not absolute_path(base) and root:
        base = _normalize_path(posixpath.join(_normalize_path(root), base))
    if cd:
        return _normalize_path(cd if absolute_path(cd) else posixpath.join(base, cd))
    return base


def _within(directory: str, workspace: str) -> bool:
    if not directory or not workspace:
        return False
    if directory == workspace or directory.startswith(workspace + "/"):
        return True
    # macOS can use a case-insensitive filesystem while POSIX spelling remains
    # case-sensitive. Compare real filesystem identities when paths are local.
    if directory.startswith("/") and workspace.startswith("/"):
        root = Path(workspace)
        candidate = Path(directory)
        for ancestor in (candidate, *candidate.parents):
            try:
                if ancestor.samefile(root):
                    return True
            except OSError:
                continue
    return False


def _is_environment_query(argv: list[str]) -> bool:
    executable = _executable(argv[0])
    lookup_args = argv[1:]
    if executable in {"pwd", "ls", "dir"}:
        return True
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
    analysis = analyze_script(command, "")
    return not analysis.issues and bool(analysis.invocations) and all(
        _production_compile_argv(invocation.argv) for invocation in analysis.invocations
    )


def _maven_arguments(argv: list[str]) -> tuple[list[str], list[str], str | None]:
    goals: list[str] = []
    files: list[str] = []
    values = {"-f", "--file", "-pl", "--projects", "-P", "--activate-profiles", "-D", "--define",
              "-s", "--settings", "-gs", "--global-settings", "-t", "--toolchains", "-gt", "--global-toolchains",
              "-T", "--threads", "-rf", "--resume-from", "-l", "--log-file"}
    index = 1
    while index < len(argv):
        token = argv[index]
        if "$" in token:
            return goals, files, "Maven 参数包含无法确定的运行时变量"
        if token in values:
            if index + 1 >= len(argv) or argv[index + 1].startswith("-"):
                return goals, files, f"Maven 选项 {token} 缺少参数值"
            if token in {"-f", "--file"}:
                files.append(argv[index + 1])
            index += 2
            continue
        if token.startswith("--file=") or token.startswith("-f="):
            files.append(token.split("=", 1)[1])
        elif token.startswith("-f") and len(token) > 2:
            files.append(token[2:])
        elif not token.startswith("-"):
            goals.append(token)
        index += 1
    if any(not path or "$" in path for path in files):
        return goals, files, "Maven -f/--file 必须指定明确的构建文件"
    return goals, files, None


def _production_compile_argv(tokens: list[str]) -> bool:
    if any("$" in token for token in tokens):
        return False
    executable = _executable(tokens[0])
    if executable in {"mvn", "mvnw"}:
        goals, _, error = _maven_arguments(tokens)
        return error is None and "compile" in goals and set(goals) <= {"compile", "clean"}
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


def _command_issue(invocation: Invocation) -> dict[str, Any] | None:
    argv = invocation.argv
    if _is_environment_query(argv) or _production_compile_argv(argv):
        return None
    goals: list[str] = []
    error = None
    if _executable(argv[0]) in {"mvn", "mvnw"}:
        goals, _, error = _maven_arguments(argv)
    return {
        "code": "VALIDATION_GOALS_NOT_ALLOWED_IN_CODE",
        "reason": error or f"Code 仅允许生产编译；实际程序 {_executable(argv[0])}、Maven 目标 {goals} 不属于生产 compile-only 或环境查询",
        "line": invocation.line, "statementIndex": invocation.statement_index,
        "statement": invocation.statement, "executable": _executable(argv[0]), "goals": goals,
    }


def _is_compile_writer(argv: list[str]) -> bool:
    """The trusted writer checks its own TASK/lease/stage and compile argv."""
    if _executable(argv[0]) not in {"python", "python3", "python.exe", "python3.exe"}:
        return False
    args = argv[1:]
    if args[:2] == ["-X", "utf8"]:
        args = args[2:]
    if len(args) < 2:
        return False
    script = Path(args[0]).expanduser().resolve()
    return script == Path(__file__).resolve().with_name("task_runner.py") and args[1] in {"prepare-compile", "record-compile"}


def _block_reason(prefix: str, failures: list[dict[str, Any]], directory: str,
                  batch: dict[str, Any] | None, runs: list[dict[str, str]], command_kind: str,
                  tool_input: dict[str, Any]) -> str:
    codes = list(dict.fromkeys(failure["code"] for failure in failures))
    details = {
        "guardVersion": GUARD_VERSION, "guardPath": str(Path(__file__).resolve()),
        "failedChecks": codes, "failures": failures,
        "featureDir": str(_feature_dir()) if _feature_dir() else None,
        "batch": batch, "commandKind": command_kind, "executionCwd": directory,
        "toolCwd": tool_input.get("cwd") or tool_input.get("workdir") or tool_input.get("workingDirectory"),
        "taskRuns": list({run["identity"]: run for run in runs}.values()),
    }
    explanations = "；".join(
        f'{failure["code"]}: ' + (f'第 {failure["line"]} 行，' if failure.get("line") else "") + failure["reason"]
        for failure in failures
    )
    return f"{prefix}: {explanations}\nGUARD_DIAGNOSTICS: " + json.dumps(details, ensure_ascii=False)


def guard(payload: dict[str, Any]) -> str | None:
    """Authorize every executable statement, with a concrete reason on rejection."""
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").lower()
    if tool_name not in {"execute", "bash", "shell"}:
        return None
    tool_input = _tool_input(payload)
    command = _command(payload, tool_input)
    if not command:
        return None
    base = _execution_directory(payload, tool_input)
    analysis = analyze_script(command, base)
    if not FORBIDDEN_COMMAND_RE.search(command) and not any(FORBIDDEN_COMMAND_RE.search(" ".join(inv.argv)) for inv in analysis.invocations):
        return None
    active = _active_code_workspaces()
    batches = _parallel_batch_workspaces()
    all_runs = _code_workspaces(active_only=False)
    # Lexical failures must not escape the guard because their cd could not be parsed.
    fallback_directories = [base]
    for workspace in [*batches, *active]:
        if workspace in _normalize_path(command):
            fallback_directories.append(workspace)
    scopes = [(inv.cwd, inv) for inv in analysis.invocations]
    if analysis.issues:
        scopes += [(directory, None) for directory in fallback_directories]
    for directory, invocation in scopes:
        if invocation and (_is_environment_query(invocation.argv) or _is_compile_writer(invocation.argv)):
            continue
        matching_runs = [run for workspace, runs in active.items() if _within(directory, workspace) for run in runs]
        batch = next((batches[workspace] for workspace in sorted(batches, key=len, reverse=True) if _within(directory, workspace)), None)
        if not batch and not matching_runs:
            # A relative tool cwd without its root cannot be compared to absolute runtime paths.
            if not absolute_path(directory) and (active or batches):
                failure = {"code": "EXECUTION_CWD_UNRESOLVED", "reason": "执行目录是相对路径，但工具未提供绝对 workspaceRoot；无法核对 TASK 绑定，请提供实际 workspaceRoot 或明确的 cd 目录"}
                return _block_reason("CODE_EXECUTION_VALIDATION_FORBIDDEN", [failure], directory, None,
                                     [run for runs in active.values() for run in runs], "unresolved_directory", tool_input)
            continue  # Preserve authorization boundaries of unrelated workspaces.
        if batch and batch["testRunning"] is True:
            continue
        failures = list(analysis.issues)
        compile_only = bool(invocation and _production_compile_argv(invocation.argv))
        if invocation:
            command_issue = _command_issue(invocation)
            if command_issue:
                failures.append(command_issue)
            if _executable(invocation.argv[0]) in {"mvn", "mvnw"}:
                _, files, _ = _maven_arguments(invocation.argv)
                roots = [workspace for workspace in active if _within(directory, workspace)]
                if batch:
                    roots = [workspace for workspace, value in batches.items() if value == batch]
                for file in files:
                    target = _normalize_path(file if absolute_path(_normalize_path(file)) else posixpath.join(directory, file))
                    if not any(_within(target, root) for root in roots):
                        failures.append({"code": "BUILD_FILE_WORKSPACE_MISMATCH", "reason": f"Maven 构建文件 {target} 超出当前授权工作区 {roots}", "line": invocation.line, "statementIndex": invocation.statement_index})
        bound_runs: list[dict[str, str]] = []
        if batch:
            bound_runs = [run for runs in all_runs.values() for run in runs
                          if run["parallelRunId"] == batch["runId"] and run["batchId"] == batch["batchId"]]
            if batch["codeAllowed"] is not True:
                if batch["batchStatus"] in {"retry_pending", "blocked", "failed", "cancelled", "merged", "ready_to_candidate"}:
                    failures.append({"code": "BATCH_NOT_EXECUTABLE", "reason": f'Batch {batch["identity"]} 状态为 {batch["batchStatus"]}'})
                if batch["stage"] not in {"none", "implement"}:
                    failures.append({"code": "BATCH_STAGE_NOT_CODE_OR_REPAIR", "reason": f'当前阶段为 {batch["stage"]}；生产编译只允许活动 Code/修复，其他验证要求 Review passed 且 test running'})
                elif batch["reviewStatus"] == "running" or batch["testStatus"] == "running":
                    failures.append({"code": "CODE_STAGE_STATE_INCONSISTENT", "reason": f'阶段指针为 {batch["stage"]}，但 review={batch["reviewStatus"]}、test={batch["testStatus"]}'})
                elif batch["implementStatus"] in {"passed", "skipped", "deferred", "needs_triage"}:
                    failures.append({"code": "IMPLEMENTATION_ALREADY_FINISHED", "reason": f'实现阶段已结束，implement={batch["implementStatus"]}，不能继承旧 TASK 编译权限'})
            if not any(run["parallelRunId"] == batch["runId"] and run["batchId"] == batch["batchId"] for run in matching_runs):
                if matching_runs:
                    code, reason = "TASK_BATCH_BINDING_MISMATCH", f'执行目录的 TASK 不属于 Batch/run {batch["identity"]}'
                elif not bound_runs:
                    code, reason = "ACTIVE_TASK_RUN_MISSING", f'Batch/run {batch["identity"]} 没有登记 TASK run'
                elif any(run["status"] in ACTIVE_CODE_RUN_STATUSES for run in bound_runs):
                    code, reason = "TASK_WORKSPACE_MISMATCH", f'实际执行目录 {directory} 不匹配当前 Batch/run 活动 TASK 的工作区'
                else:
                    code, reason = "TASK_RUN_INACTIVE", "当前 Batch/run 的 TASK 已结束或中止，不能继续使用其编译权限"
                failures.append({"code": code, "reason": reason})
        if failures:
            return _block_reason("BATCH_STAGE_VALIDATION_FORBIDDEN" if batch else "CODE_EXECUTION_VALIDATION_FORBIDDEN",
                                 failures, directory, batch, [*matching_runs, *bound_runs],
                                 "production_compile" if compile_only else "validation" if invocation else "unsupported_shell_form", tool_input)
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
