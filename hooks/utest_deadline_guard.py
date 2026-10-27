#!/usr/bin/env python3
"""Stop ordinary tools at the UTest deadline while permitting exact closure."""
from __future__ import annotations
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.utest_policy import stage_budget, deadline_reason
from hooks.runtime_artifact_guard import _feature_root


def read(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def normalize(value):
    return str(value).replace("\\", "/").rstrip("/").casefold()


def closure_command(command: str, run_id: str, batch_id: str) -> bool:
    if re.search(r"[\n;&|`<>]", command) or "$(" in command:
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if not words or Path(words[0]).name not in {"python", "python3", "python.exe", Path(sys.executable).name}:
        return False
    position = 3 if words[1:3] == ["-X", "utf8"] else 1
    if len(words) <= position:
        return False
    script = Path(words[position]).resolve()
    if script.parent != Path(__file__).resolve().parent:
        return False
    args = words[position + 1:]
    def value(name):
        return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None
    if value("--run-id") != run_id or value("--batch-id") != batch_id:
        return False
    action = args[0] if args else ""
    return ((script.name == "parallel_batch_stage.py" and action in {"record-test-failure", "check-test-budget", "close-test"})
            or (script.name == "batch_lease_manager.py" and action in {"check", "release"})
            or (script.name == "worktree_manager.py" and "seal" in args)
            or (script.name == "run_utest_command.py" and "--record-only" in args))


def guard(payload: dict) -> str | None:
    feature = _feature_root()
    if feature is None:
        return None
    data = payload.get("tool_input") or payload.get("input") or {}
    command = data.get("command") or data.get("cmd") or ""
    # Associate only the targeted worktree, never stop another Batch's tools.
    path_keys = {"cwd", "workdir", "workspaceRoot", "workspace_root", "file_path", "filePath", "path", "directory", "paths", "files"}
    candidates = [item for source in (payload, data) for key, value in source.items() if key in path_keys
                  for item in (value if isinstance(value, list) else [value]) if isinstance(item, str)]
    for value in data.values():
        if isinstance(value, str):
            candidates.extend(re.findall(r"(?m)^\*\*\* (?:Update|Add|Delete) File: (.+)$", value))
    try:
        candidates.extend(shlex.split(command))
    except ValueError:
        pass
    if not candidates:
        return None
    for path in feature.glob(".parallel-runs/*/manifest.json"):
        manifest = read(path)
        for batch_id, batch in manifest.get("batches", {}).items():
            state = batch.get("stageStates", {}).get("test", {})
            if not batch.get("worktreePath") or state.get("status") not in {"running", "deferred"}:
                continue
            root = normalize(batch["worktreePath"])
            targeted = any(normalize(c) == root or normalize(c).startswith(root + "/") for c in candidates)
            # Current cwd also binds relative read/grep commands.
            targeted = targeted or normalize(Path.cwd()) == root or normalize(Path.cwd()).startswith(root + "/")
            if not targeted:
                continue
            execution = read(path.parent / "utest" / batch_id / "UTEST_EXECUTION.json")
            reason = (state.get("stopRequested") or {}).get("reasonCode") or deadline_reason(stage_budget(manifest, batch, execution), execution, time.time())
            if not reason:
                continue
            tool = str(payload.get("tool_name") or payload.get("toolName") or "")
            logs = {str(item.get("outputFile")) for task in execution.get("tasks", {}).values()
                    for group in ("commands", "setupCommands") for item in task.get(group, {}).values() if item.get("outputFile")}
            logs.update(str(item.get("response", {}).get(key)) for task in execution.get("tasks", {}).values()
                        for group in ("commands", "setupCommands") for item in task.get(group, {}).values()
                        for key in ("logPath", "evidenceLogPath") if item.get("response", {}).get(key))
            read_targets = [item for key, value in data.items() if key in {"filePath", "file_path", "path", "paths", "files"}
                            for item in (value if isinstance(value, list) else [value]) if isinstance(item, str)]
            if tool in {"read_file", "Read"} and read_targets and all(normalize(c) in {normalize(log) for log in logs} for c in read_targets):
                continue
            if closure_command(command, manifest.get("runId", path.parent.name), batch_id):
                continue
            return (f"UTEST_STOP_REQUESTED:{batch_id}:{reason}. 停止探索、读写测试和新命令；"
                    "仅用当前合法身份执行原输出 record-only、worktree_manager seal、"
                    "parallel_batch_stage record-test-failure、lease release。保留已有资产，不得返回 PASS。")
    return None


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    reason = guard(payload)
    if reason:
        print(json.dumps({"decision": "block", "reason": reason, "systemMessage": reason}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
