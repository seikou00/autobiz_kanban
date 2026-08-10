#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run one setup/test argv inside an assigned repository without a shell."""

from __future__ import print_function

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_store import EvidenceStoreError, append_evidence  # noqa: E402
from hooks.json_writer_common import resolve_feature, resolve_workspace, shell_join  # noqa: E402
from hooks.unit_test_result_writer import record_execution  # noqa: E402


UT_ID_RE = re.compile(r"^UT-\d{3,}$")
MODES = ("setup", "test")


class UTestCommandError(Exception):
    """Raised when a command cannot be safely prepared or recorded."""


class RepairArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise UTestCommandError(
            "命令参数无效：{}。修复：运行 `{} --help`，并在 `--` 后传入 argv。".format(
                message, self.prog
            )
        )


def _within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_command_cwd(code_workspace, cwd=None):
    root = Path(code_workspace).expanduser().resolve()
    if not root.is_dir():
        raise UTestCommandError(
            "code-workspace 不存在或不是目录：{}。修复：传入 workspaceRef 对应仓库根目录。".format(
                root
            )
        )
    requested = Path(cwd).expanduser() if cwd else root
    if not requested.is_absolute():
        requested = root / requested
    resolved = requested.resolve()
    if not resolved.is_dir():
        raise UTestCommandError(
            "cwd 不存在或不是目录：{}。修复：传入 code-workspace 内的现有目录。".format(
                resolved
            )
        )
    if not _within(resolved, root):
        raise UTestCommandError(
            "cwd 越出分配仓库：{}。修复：把 --cwd 限制在 {} 内。".format(
                resolved, root
            )
        )
    return root, resolved


def _validate_argv(argv):
    if not isinstance(argv, list) or not argv:
        raise UTestCommandError(
            "argv 不能为空。修复：在 `--` 后传入可执行文件和参数，或使用 --argv-json。"
        )
    if not all(isinstance(value, str) and value for value in argv):
        raise UTestCommandError(
            "argv 必须是非空字符串数组。修复：不要传 shell 命令字符串或空参数。"
        )
    return list(argv)


def _run(argv, cwd, timeout):
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout or "", completed.stderr or "", False
    except FileNotFoundError as exc:
        return (
            127,
            "",
            "{}。修复：安装可执行文件，或从真实 manifest 选择项目 runner。".format(exc),
            True,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        timeout_message = "命令超过 {} 秒。修复：缩小测试范围或检查阻塞资源。".format(timeout)
        stderr = "{}\n{}".format(stderr.rstrip(), timeout_message).lstrip()
        return 124, stdout, stderr, True
    except OSError as exc:
        return (
            126,
            "",
            "{}。修复：确认可执行文件权限和当前平台支持。".format(exc),
            True,
        )


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _append_log(path, mode, target_id, cwd, command, exit_code, stdout, stderr):
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        "=== UTEST COMMAND {} ===".format(_timestamp()),
        "mode: {}".format(mode),
        "target: {}".format(target_id or "setup"),
        "cwd: {}".format(cwd),
        "command: {}".format(command),
        "exit_code: {}".format(exit_code),
        "--- stdout ---",
        stdout.rstrip(),
        "--- stderr ---",
        stderr.rstrip(),
        "=== END UTEST COMMAND ===",
        "",
    ]
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(parts) + "\n")


def _result_for(exit_code, blocked):
    if blocked:
        return "BLOCKED", "blocked"
    if exit_code == 0:
        return "PASS", "pass"
    return "FAIL", "fail"


def execute_utest_command(
    *,
    workspace,
    feature,
    code_workspace,
    argv,
    kind=None,
    mode=None,
    cwd=None,
    target_id=None,
    task_id=None,
    spec_refs=None,
    timeout=600,
):
    selected_kind = kind or mode
    if selected_kind not in MODES:
        raise UTestCommandError(
            "kind 无效：{}。修复：使用 setup 或 test。".format(selected_kind)
        )
    if not isinstance(timeout, int) or timeout <= 0:
        raise UTestCommandError(
            "timeout 必须是正整数。修复：传入大于 0 的秒数。"
        )
    command_argv = _validate_argv(argv)
    artifact_workspace = resolve_workspace(workspace)
    resolved_feature = resolve_feature(feature)
    _, command_cwd = resolve_command_cwd(code_workspace, cwd)
    if selected_kind == "test":
        if target_id is not None and (
            not isinstance(target_id, str) or not UT_ID_RE.match(target_id)
        ):
            raise UTestCommandError(
                "--target-id 无效。修复：省略该参数自动分配，或使用 UT-001 形式的稳定 ID。"
            )
        if not isinstance(task_id, str) or not task_id.strip():
            raise UTestCommandError(
                "test 模式需要 --task-id。修复：传入 assignment 中的 TASK ID。"
            )
        if not isinstance(spec_refs, list) or not spec_refs:
            raise UTestCommandError(
                "test 模式需要 --spec-ref。修复：至少传入一个 SCOPE/spec 行为引用。"
            )

    command = shell_join(command_argv)
    feature_dir = artifact_workspace / ".autobizdevops" / "features" / resolved_feature
    log_path = feature_dir / "test-output.log"
    exit_code, stdout, stderr, blocked = _run(command_argv, command_cwd, timeout)
    _append_log(
        log_path,
        selected_kind,
        target_id,
        command_cwd,
        command,
        exit_code,
        stdout,
        stderr,
    )
    unit_result, evidence_result = _result_for(exit_code, blocked)
    response = {
        "ok": exit_code == 0,
        "kind": selected_kind,
        "result": unit_result,
        "exitCode": exit_code,
        "command": command,
        "cwd": str(command_cwd),
        "logPath": str(log_path),
        "evidenceId": None,
        "targetId": target_id,
    }
    if selected_kind == "setup":
        return response

    output_tail = "\n".join(
        part for part in (stdout.rstrip(), stderr.rstrip()) if part
    ) or "(command produced no output)"
    record = {
        "featureId": resolved_feature,
        "checkpoint": "unit_test_in_progress",
        "nodeId": "dev.utest",
        "skill": "autodev-utest",
        "taskId": task_id,
        "action": "validation",
        "specRefs": list(spec_refs),
        "designRefs": [],
        "changedFiles": [],
        "validation": {
            "command": command,
            "exitCode": exit_code,
            "result": evidence_result,
        },
    }
    try:
        evidence = append_evidence(feature_dir, record, output_tail=output_tail)
    except EvidenceStoreError as exc:
        raise UTestCommandError(
            "Evidence 写入失败：{}。修复：修复 evidence 索引/冻结状态后重跑同一命令。".format(
                exc
            )
        )
    evidence_id = str(evidence["evidenceId"])
    try:
        writer_result = record_execution(
            artifact_workspace,
            resolved_feature,
            target_id=target_id,
            task_id=task_id,
            spec_refs=list(spec_refs),
            evidence_id=evidence_id,
            result=unit_result,
            command=command,
        )
    except Exception as exc:
        raise UTestCommandError(
            "UNIT_TEST_RESULT 写入失败，Evidence {} 已保留：{}。"
            "修复：不要重跑测试；修复结果文件的权限或结构后，使用 "
            "unit_test_result_writer.py record-execution 携带该 evidence ID 补记同一次执行。".format(
                evidence_id, exc
            )
        )
    response["evidenceId"] = evidence_id
    if writer_result.data and isinstance(writer_result.data.get("target"), dict):
        response["targetId"] = writer_result.data["target"].get("targetId")
    response["resultPath"] = str(writer_result.path)
    response["resultChanged"] = writer_result.changed
    return response


def _parse_argv_json(raw):
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise UTestCommandError(
            "--argv-json 不是合法 JSON：{}。修复：传入 JSON 字符串数组。".format(exc)
        )
    return _validate_argv(value)


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] in MODES and "--kind" not in raw_argv and "--mode" not in raw_argv:
        raw_argv = ["--kind", raw_argv[0]] + raw_argv[1:]
    parser = RepairArgumentParser(description="在分配仓库内无 shell 执行 UTest 命令")
    parser.add_argument("--kind", "--mode", dest="kind", choices=MODES)
    parser.add_argument("--workspace")
    parser.add_argument("--feature")
    parser.add_argument("--code-workspace")
    parser.add_argument("--cwd")
    parser.add_argument("--target-id", "--ut-id", dest="target_id")
    parser.add_argument("--task-id")
    parser.add_argument("--spec-ref", action="append")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--argv-json", "--command-json", dest="argv_json")
    parser.add_argument("command_argv", nargs=argparse.REMAINDER)
    try:
        args = parser.parse_args(raw_argv)
        missing = [
            name
            for name, value in (
                ("--kind", args.kind),
                ("--workspace", args.workspace),
                ("--feature", args.feature),
                ("--code-workspace", args.code_workspace),
            )
            if not value
        ]
        if missing:
            raise UTestCommandError(
                "缺少 {}。修复：从 UTest assignment 传入这些参数。".format(
                    ", ".join(missing)
                )
            )
        trailing_argv = list(args.command_argv)
        if trailing_argv and trailing_argv[0] == "--":
            trailing_argv = trailing_argv[1:]
        if args.argv_json and trailing_argv:
            raise UTestCommandError(
                "不能同时传 --argv-json 与尾随 argv。修复：只保留一种 argv 输入。"
            )
        command_argv = (
            _parse_argv_json(args.argv_json)
            if args.argv_json
            else _validate_argv(trailing_argv)
        )
        result = execute_utest_command(
            workspace=args.workspace,
            feature=args.feature,
            code_workspace=args.code_workspace,
            argv=command_argv,
            kind=args.kind,
            cwd=args.cwd,
            target_id=args.target_id,
            task_id=args.task_id,
            spec_refs=args.spec_ref,
            timeout=args.timeout,
        )
    except UTestCommandError as exc:
        print("run_utest_command_failed: {}".format(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            "run_utest_command_failed: {}。修复：检查 workspace、feature 与结果产物后重跑。".format(
                exc
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False))
    return int(result["exitCode"])


if __name__ == "__main__":
    raise SystemExit(main())
