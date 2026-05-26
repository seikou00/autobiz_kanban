#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Best-effort recorder for build/compile command evidence.

This hook is intentionally non-blocking: every path through this script exits 0.
It records facts only; code_done gating should decide whether the evidence is
good enough.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


EVIDENCE_FILE = "compile-evidence.ndjson"
OUTPUT_LIMIT = int(os.environ.get("AUTOBIZ_COMPILE_EVIDENCE_OUTPUT_LIMIT", "6000"))
SESSION_DIR = Path(
    os.environ.get(
        "AUTOBIZ_COMPILE_EVIDENCE_SESSION_DIR",
        str(Path(tempfile.gettempdir()) / "autobiz_compile_evidence_recorder"),
    )
)

BUILD_PATTERNS = [
    r"(^|\s)(\./)?mvnw?(\.cmd)?\s+.*\b(compile|test-compile|package|verify|install)\b",
    r"(^|\s)(\./)?gradlew?(\.bat)?\s+.*\b(build|assemble|classes|testClasses|check)\b",
    r"(^|\s)(npm|pnpm|yarn|bun)\s+(run\s+)?build\b",
    r"(^|\s)go\s+(build|test)\b",
    r"(^|\s)cargo\s+(build|check|test)\b",
    r"(^|\s)dotnet\s+(build|test|publish)\b",
    r"(^|\s)(g?make)(\s+.*\b(build|all|compile)\b|\s*$)",
    r"(^|\s)(npx\s+)?(tsc|vue-tsc)\b",
]


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
        elif "update_checkpoint.py" in token and " " in token:
            variants.append(token)
    return variants


def option_value(command: str, *names: str) -> str:
    for variant in command_variants(command):
        tokens = command_words(variant)
        for index, token in enumerate(tokens):
            for name in names:
                if token == name and index + 1 < len(tokens):
                    return tokens[index + 1]
                if token.startswith(name + "="):
                    return token.split("=", 1)[1]
    return ""


def extract_command(payload: dict[str, Any]) -> str:
    tool_input = as_dict(payload.get("tool_input") or payload.get("input"))
    return first_text(
        tool_input.get("command"),
        tool_input.get("cmd"),
        tool_input.get("script"),
        payload.get("command"),
        payload.get("cmd"),
    )


def extract_cwd(payload: dict[str, Any]) -> Path | None:
    tool_input = as_dict(payload.get("tool_input") or payload.get("input"))
    raw = first_text(
        tool_input.get("cwd"),
        tool_input.get("workdir"),
        tool_input.get("working_directory"),
        payload.get("cwd"),
        payload.get("working_directory"),
    )
    if not raw:
        return None
    return Path(raw).expanduser().resolve(strict=False)


def response_obj(payload: dict[str, Any]) -> Any:
    return (
        payload.get("tool_response")
        or payload.get("response")
        or payload.get("result")
        or payload.get("tool_output")
        or {}
    )


def normalize_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def find_values(obj: Any, names: set[str], depth: int = 0) -> list[Any]:
    if depth > 8:
        return []
    found: list[Any] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if normalize_key(key) in names:
                found.append(value)
            found.extend(find_values(value, names, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_values(item, names, depth + 1))
    return found


def output_text(resp: Any) -> str:
    if isinstance(resp, str):
        return resp

    parts: list[str] = []
    for value in find_values(resp, {"stdout", "stderr", "output", "text", "content", "message"}):
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n".join(parts)


def extract_exit_code(resp: Any) -> int | None:
    for value in find_values(resp, {"exitcode", "returncode", "statuscode"}):
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())

    for value in find_values(resp, {"success", "ok"}):
        if value is True:
            return 0
        if value is False:
            return 1

    text = output_text(resp)
    match = re.search(r"(?:exit|exited|return)(?:ed)?(?:\s+with)?(?:\s+code)?[:= ]+(-?\d+)", text, re.I)
    if match:
        return int(match.group(1))
    return None


def extract_output_tail(resp: Any) -> str:
    return output_text(resp)[-OUTPUT_LIMIT:]


def looks_like_build(command: str) -> bool:
    return any(re.search(pattern, command, re.IGNORECASE) for pattern in BUILD_PATTERNS)


def looks_like_update_checkpoint(command: str) -> bool:
    for variant in command_variants(command):
        if re.search(r"(^|[/\s])update_checkpoint\.py(\s|$)", variant):
            return True
        if any(Path(token).name == "update_checkpoint.py" for token in command_words(variant)):
            return True
    return False


def session_key(payload: dict[str, Any]) -> str:
    raw = first_text(
        os.environ.get("AUTOBIZ_COMPILE_EVIDENCE_SESSION_ID"),
        payload.get("transcript_path"),
        payload.get("session_id"),
        payload.get("conversation_id"),
        "default",
    )
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def context_path(payload: dict[str, Any]) -> Path:
    return SESSION_DIR / f"{session_key(payload)}.json"


def pending_path(payload: dict[str, Any]) -> Path:
    return SESSION_DIR / f"{session_key(payload)}.pending.ndjson"


def evidence_path_for_workspace(root: Path, *, create: bool) -> Path | None:
    evidence_dir = root if root.name == ".autobizdevops" else root / ".autobizdevops"
    if not evidence_dir.exists() and not create:
        return None
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return evidence_dir / EVIDENCE_FILE


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def read_context(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(context_path(payload).read_text(encoding="utf-8"))
    except Exception:
        return {}


def remember_update_checkpoint_workspace(payload: dict[str, Any], command: str, cwd: Path | None) -> None:
    if not looks_like_update_checkpoint(command):
        return

    workspace = option_value(command, "--workspace", "-w")
    if not workspace:
        return

    path = Path(workspace).expanduser()
    if not path.is_absolute() and cwd:
        path = cwd / path

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    context_path(payload).write_text(
        json.dumps(
            {
                "workspace": str(path.resolve(strict=False)),
                "feature": option_value(command, "--feature", "-f"),
                "checkpoint": option_value(command, "--checkpoint", "-c"),
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def evidence_paths(payload: dict[str, Any], cwd: Path | None) -> list[Path]:
    paths: list[Path] = []

    explicit = os.environ.get("AUTOBIZ_EVIDENCE_WORKSPACE")
    if explicit:
        path = evidence_path_for_workspace(Path(explicit).expanduser().resolve(strict=False), create=True)
        if path:
            paths.append(path)

    workspace = read_context(payload).get("workspace")
    if isinstance(workspace, str) and workspace:
        path = evidence_path_for_workspace(Path(workspace), create=True)
        if path:
            paths.append(path)

    if cwd:
        for parent in [cwd, *cwd.parents]:
            candidate = parent / ".autobizdevops"
            if candidate.exists():
                paths.append(candidate / EVIDENCE_FILE)
                break

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def append_pending(payload: dict[str, Any], record: dict[str, Any]) -> None:
    append_record(pending_path(payload), record)


def flush_pending(payload: dict[str, Any], cwd: Path | None) -> None:
    pending = pending_path(payload)
    if not pending.exists():
        return

    targets = evidence_paths(payload, cwd)
    if not targets:
        return

    lines = [line for line in pending.read_text(encoding="utf-8").splitlines() if line.strip()]
    for line in lines:
        record = json.loads(line)
        for target in targets:
            append_record(target, record)
    pending.unlink(missing_ok=True)


def build_record(payload: dict[str, Any], command: str, cwd: Path | None) -> dict[str, Any]:
    resp = response_obj(payload)
    return {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cwd": str(cwd) if cwd else None,
        "command": command,
        "exit_code": extract_exit_code(resp),
        "output_tail": extract_output_tail(resp),
        "tool_name": payload.get("tool_name"),
        "hook_event_name": payload.get("hook_event_name"),
        "session_key": session_key(payload),
    }


def run(payload: dict[str, Any]) -> None:
    command = extract_command(payload)
    cwd = extract_cwd(payload)

    if command:
        remember_update_checkpoint_workspace(payload, command, cwd)
        flush_pending(payload, cwd)

    if not command or not looks_like_build(command):
        return

    record = build_record(payload, command, cwd)
    targets = evidence_paths(payload, cwd)
    if not targets:
        append_pending(payload, record)
        return

    for target in targets:
        append_record(target, record)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if isinstance(payload, dict):
            run(payload)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
