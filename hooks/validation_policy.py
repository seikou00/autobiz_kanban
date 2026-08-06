#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared validation-command policy for plan, writer, and runner gates."""

from __future__ import annotations

import re
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


BEHAVIOR_TASK_VALIDATION_KINDS = frozenset({
    "behavior_test",
    "integration_test",
    "e2e_test",
    "static_check",
})
FRONTEND_COMPILE_VALIDATION_KINDS = frozenset({"build", "compile", "typecheck"})
TASK_VALIDATION_KINDS = BEHAVIOR_TASK_VALIDATION_KINDS | FRONTEND_COMPILE_VALIDATION_KINDS
BATCH_VALIDATION_KINDS = frozenset({"build", "typecheck", "lint", "compile"})
MAVEN_EXECUTABLES = frozenset({"mvn", "mvn.cmd", "mvnw", "mvnw.cmd"})
_MAVEN_PROJECT_LIST_FLAGS = ("-pl", "--projects")

_NOOP_EXECUTABLES = {"echo", "false", "printf", "true"}
_INLINE_SHELL_FLAGS = {
    "bash": {"-c"},
    "cmd": {"/c"},
    "cmd.exe": {"/c"},
    "dash": {"-c"},
    "ksh": {"-c"},
    "powershell": {"-command", "-c"},
    "powershell.exe": {"-command", "-c"},
    "pwsh": {"-command", "-c"},
    "sh": {"-c"},
    "zsh": {"-c"},
}
_PLACEHOLDER_MARKERS = (
    "[executable]",
    "[task test arguments]",
    "placeholder",
    "replace with real",
    "validation placeholder",
    "占位",
    "待替换",
)
_NOOP_SCRIPT_RE = re.compile(
    r"^\s*(?:(?:echo|printf)\b[^;&|]*(?:;\s*exit\s+0)?|true|false|exit\s+0)\s*$",
    re.IGNORECASE,
)


def normalized_argv(command: Any) -> list[str] | None:
    if not isinstance(command, dict):
        return None
    argv = command.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item.strip() for item in argv):
        return None
    return [item.strip() for item in argv]


def command_executable(argv: list[str]) -> str:
    return PurePosixPath(argv[0].replace("\\", "/")).name.lower() if argv else ""


def command_policy_errors(command: Any) -> list[str]:
    """Reject commands that can pass without performing a validation action."""

    argv = normalized_argv(command)
    if not argv:
        return []
    executable = command_executable(argv)
    lowered = " ".join(argv).lower()
    errors: list[str] = []
    if executable in _NOOP_EXECUTABLES:
        errors.append("validation_command_noop")
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        errors.append("validation_command_placeholder")
    inline_flags = _INLINE_SHELL_FLAGS.get(executable, set())
    if any(item.lower() in inline_flags for item in argv[1:]):
        errors.append("validation_command_inline_shell_forbidden")
    errors.extend(maven_test_policy_errors(command))
    errors.extend(maven_project_selector_errors(command))
    return errors


def _normalized_repo_relative_path(value: str) -> str:
    """Normalize a Git-root-relative path so two spellings compare equal."""

    candidate = value.replace("\\", "/").strip()
    while candidate.startswith("./"):
        candidate = candidate[2:]
    candidate = candidate.rstrip("/")
    return candidate or "."


def _maven_project_list_values(argv: list[str]) -> list[str]:
    """Return raw -pl/--projects values, covering both `-pl x` and `--projects=x`."""

    values: list[str] = []
    index = 1
    while index < len(argv):
        token = argv[index]
        lowered = token.lower()
        if lowered in _MAVEN_PROJECT_LIST_FLAGS:
            if index + 1 < len(argv):
                values.append(argv[index + 1])
            index += 2
            continue
        for flag in _MAVEN_PROJECT_LIST_FLAGS:
            if lowered.startswith(f"{flag}="):
                values.append(token[len(flag) + 1:])
                break
        index += 1
    return values


def maven_project_selector_errors(command: Any) -> list[str]:
    """Reject `-pl <path>` that re-names the directory the command already runs in.

    ``-pl`` selects modules from the reactor of the POM in ``cwd``. When ``cwd``
    is already the module directory, that reactor contains only the module
    itself, so a path selector naming the same directory can never resolve and
    Maven exits non-zero with ``Could not find the selected project in the
    reactor``. Selecting a submodule from an aggregator root keeps working and
    is not flagged, so this only rejects the provably unresolvable spelling.
    """

    if not isinstance(command, dict):
        return []
    argv = normalized_argv(command)
    if not argv or command_executable(argv) not in MAVEN_EXECUTABLES:
        return []
    cwd = command.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        return []
    normalized_cwd = _normalized_repo_relative_path(cwd)
    if normalized_cwd == ".":
        return []
    for raw_value in _maven_project_list_values(argv):
        for item in raw_value.split(","):
            candidate = item.strip()
            # `!module` excludes instead of selects; `:artifactId` is not a path.
            if not candidate or candidate.startswith("!") or ":" in candidate:
                continue
            if _normalized_repo_relative_path(candidate) == normalized_cwd:
                return ["maven_project_selector_duplicates_cwd"]
    return []


def maven_test_selectors(command: Any) -> list[str]:
    """Return concrete Maven test selectors from -Dtest/-Dit.test properties."""

    argv = normalized_argv(command)
    if not argv or command_executable(argv) not in MAVEN_EXECUTABLES:
        return []
    selectors: list[str] = []
    for token in argv[1:]:
        lowered = token.lower()
        if lowered.startswith("-dtest=") or lowered.startswith("-dit.test="):
            value = token.split("=", 1)[1].strip()
            selectors.extend(item.strip() for item in value.split(",") if item.strip())
    return selectors


def maven_test_policy_errors(command: Any) -> list[str]:
    """Reject Maven options that can make a targeted test pass without running."""

    argv = normalized_argv(command)
    if not argv or command_executable(argv) not in MAVEN_EXECUTABLES:
        return []
    selectors = maven_test_selectors(command)
    if not selectors:
        return []
    errors: list[str] = []
    for token in argv[1:]:
        lowered = token.lower()
        if not lowered.startswith("-d"):
            continue
        property_value = lowered[2:]
        name, separator, value = property_value.partition("=")
        if name in {
            "skiptests",
            "maven.test.skip",
            "surefire.skip",
            "failsafe.skip",
            "skipits",
        } and (not separator or value in {"", "true", "1", "yes"}):
            errors.append("maven_test_execution_skipped")
        if name in {
            "failifnotests",
            "surefire.failifnospecifiedtests",
            "failsafe.failifnospecifiedtests",
        } and separator and value in {"false", "0", "no"}:
            errors.append("maven_test_zero_match_allowed")
    for selector in selectors:
        class_selector = selector.split("#", 1)[0].strip()
        if (
            not class_selector
            or any(char in class_selector for char in "*?[]%")
            or class_selector.endswith((".java", ".kt", ".groovy", ".scala"))
            or "/" in class_selector
            or "\\" in class_selector
        ):
            errors.append("maven_test_selector_must_name_class")
    return sorted(set(errors))


def maven_test_target_sources(command_dir: Path, selector: str) -> list[Path]:
    """Resolve a Maven class selector to source files below conventional test roots."""

    class_selector = selector.split("#", 1)[0].strip()
    if not class_selector or any(char in class_selector for char in "*?[]%"):
        return []
    class_name = class_selector.rsplit(".", 1)[-1]
    class_name = class_name.split("$", 1)[0]
    if not class_name:
        return []
    source_selector = class_selector.split("$", 1)[0]
    expected_suffix = tuple(part for part in source_selector.split(".") if part)
    candidates: list[Path] = []
    for suffix in (".java", ".kt", ".groovy", ".scala"):
        for path in command_dir.rglob(f"{class_name}{suffix}"):
            if not path.is_file():
                continue
            try:
                relative_parts = path.relative_to(command_dir).parts
            except ValueError:
                continue
            if any(part in {".git", "target", "node_modules", "build"} for part in relative_parts):
                continue
            root_index = next(
                (
                    index
                    for index, part in enumerate(relative_parts[:-1])
                    if part == "test"
                    or part == "tests"
                    or (part == "src" and relative_parts[index + 1] in {"test", "it"})
                ),
                None,
            )
            if root_index is None:
                continue
            if len(expected_suffix) > 1:
                source_start = root_index + 2 if relative_parts[root_index] == "src" else root_index + 1
                if (
                    relative_parts[root_index] in {"test", "tests"}
                    and source_start < len(relative_parts)
                    and relative_parts[source_start] in {"java", "kotlin", "groovy", "scala"}
                ):
                    source_start += 1
                source_parts = relative_parts[source_start:]
                source_stem = tuple(Path(item).stem for item in source_parts)
                if source_stem[-len(expected_suffix):] != expected_suffix:
                    continue
            candidates.append(path)
    return sorted(set(candidates))


def maven_test_plan(command: Any, command_dir: Path) -> dict[str, Any] | None:
    selectors = maven_test_selectors(command)
    if not selectors:
        return None
    targets = []
    for selector in selectors:
        files = maven_test_target_sources(command_dir, selector)
        targets.append({
            "selector": selector,
            "mode": "reuse_existing" if files else "create_in_code",
            "sourceFiles": [path.relative_to(command_dir).as_posix() for path in files],
        })
    return {
        "commandId": command.get("id"),
        "framework": "maven",
        "targets": targets,
    }


def task_validation_kinds_for_lane(lane: str) -> frozenset[str]:
    if lane == "frontend":
        return TASK_VALIDATION_KINDS
    return BEHAVIOR_TASK_VALIDATION_KINDS


def task_validation_assurance_level(command: dict[str, Any]) -> str:
    return "compile" if command.get("kind") in FRONTEND_COMPILE_VALIDATION_KINDS else "behavior"


def package_script_name(command: Any) -> str | None:
    argv = normalized_argv(command)
    if not argv:
        return None
    executable = command_executable(argv)
    args = argv[1:]
    if executable not in {"npm", "npm.cmd", "pnpm", "pnpm.cmd", "yarn", "yarn.cmd"} or not args:
        return None
    lowered = [item.lower() for item in args]
    if lowered[0] == "run" and len(args) > 1 and not args[1].startswith("-"):
        return args[1]
    if executable in {"pnpm", "pnpm.cmd", "yarn", "yarn.cmd"} and not args[0].startswith("-"):
        return args[0]
    if executable in {"npm", "npm.cmd"} and lowered[0] in {"start", "stop", "test"}:
        return args[0]
    return None


def package_script_policy_errors(script: Any) -> list[str]:
    if not isinstance(script, str) or not script.strip():
        return ["validation_package_script_missing"]
    lowered = script.lower()
    errors: list[str] = []
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        errors.append("validation_command_placeholder")
    if _NOOP_SCRIPT_RE.fullmatch(script):
        errors.append("validation_command_noop")
    return errors


def frontend_compile_command_matches_kind(command: Any) -> bool:
    if not isinstance(command, dict) or command.get("kind") not in FRONTEND_COMPILE_VALIDATION_KINDS:
        return False
    argv = normalized_argv(command)
    if not argv or command_policy_errors(command):
        return False
    kind = str(command["kind"])
    executable = command_executable(argv)
    tokens = [item.lower() for item in argv[1:] if not item.startswith("-")]
    if kind in tokens:
        return True
    if kind == "typecheck":
        return executable in {"tsc", "tsc.cmd", "vue-tsc", "vue-tsc.cmd"} or (
            executable in {"npx", "npx.cmd"}
            and bool(tokens)
            and tokens[0] in {"tsc", "vue-tsc"}
        )
    if kind == "build":
        build_tools = {"next", "ng", "vite", "webpack"}
        return executable in build_tools or (
            executable in {"npx", "npx.cmd"} and bool(tokens) and tokens[0] in build_tools
        )
    if kind == "compile":
        return executable in {"tsc", "tsc.cmd", "vue-tsc", "vue-tsc.cmd"} and "--noemit" not in {
            item.lower() for item in argv[1:]
        }
    return False


def frontend_command_provides_task_coverage(command: Any) -> bool:
    return (
        isinstance(command, dict)
        and command.get("required") is True
        and frontend_compile_command_matches_kind(command)
    )
