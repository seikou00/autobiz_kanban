#!/usr/bin/env python3
"""Shared checkpoint parsing, transition, and guard helpers."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
AUTODEV_HOOKS_DIR = ROOT / "skills" / "autodev" / "hooks"
if str(AUTODEV_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(AUTODEV_HOOKS_DIR))

from board_core.workflow import find_current_node  # noqa: E402
from artifact_check import run_postcheck, run_precheck  # noqa: E402
from paths import ensure_dir, get_feature_hook_log_path  # noqa: E402


STATE_PATH = Path(".autobizdevops/STATE.md")
STATE_PATH_SUFFIX = (".autobizdevops", "STATE.md")
BLOCK_EXIT_CODE = 2
MAVEN_COMPILE_TIMEOUT_SECONDS = 290
MAVEN_OUTPUT_LIMIT = 4000
LOG_PATH = Path(tempfile.gettempdir()) / "check_state_done_hook.log"
BOARD_CONFIG_PATH = ROOT / "board_core" / "board_config.json"

KNOWN_CHECKPOINTS = {
    "discuss_in_progress",
    "discuss_done",
    "prd_in_progress",
    "prd_done",
    "plan_in_progress",
    "plan_done",
    "code_in_progress",
    "code_done",
    "unit_test_in_progress",
    "unit_test_done",
    "verify_in_progress",
    "verify_done",
    "requirements_eval_in_progress",
    "requirements_eval_done",
    "e2e_in_progress",
    "e2e_done",
    "cicd_in_progress",
    "cicd_done",
    "needs_fix",
    "archived",
}

ALLOWED_NEXT = {
    "discuss_in_progress": {"discuss_done"},
    "discuss_done": {"prd_in_progress", "plan_in_progress"},
    "prd_in_progress": {"prd_done"},
    "prd_done": {"plan_in_progress"},
    "plan_in_progress": {"plan_done"},
    "plan_done": {"code_in_progress"},
    "code_in_progress": {"code_done"},
    "code_done": {"requirements_eval_in_progress"},
    "requirements_eval_in_progress": {"requirements_eval_done"},
    "requirements_eval_done": {"unit_test_in_progress"},
    "unit_test_in_progress": {"unit_test_done"},
    "unit_test_done": {"e2e_in_progress"},
    "verify_in_progress": {"verify_done", "needs_fix"},
    "verify_done": {"cicd_in_progress"},
    "e2e_in_progress": {"e2e_done", "needs_fix"},
    "e2e_done": {"verify_in_progress"},
    "cicd_in_progress": {"cicd_done"},
    "cicd_done": {"archived"},
    "needs_fix": {
        "discuss_in_progress",
        "prd_in_progress",
        "plan_in_progress",
        "code_in_progress",
        "cicd_in_progress",
    },
    "archived": set(),
}

INITIAL_CHECKPOINTS = {
    "discuss_in_progress",
    "prd_in_progress",
    "plan_in_progress",
    "cicd_in_progress",
}

DEFAULT_STAGE_BY_CHECKPOINT = {
    "discuss_in_progress": "Biz / 需求澄清",
    "discuss_done": "Biz / 需求澄清",
    "prd_in_progress": "Biz / PRD 生成",
    "prd_done": "Biz / PRD",
    "plan_in_progress": "Plan",
    "plan_done": "Plan 完成",
    "code_in_progress": "Code",
    "code_done": "Code 完成",
    "requirements_eval_in_progress": "Requirements Review",
    "requirements_eval_done": "Requirements Review 完成",
    "unit_test_in_progress": "Unit Test",
    "unit_test_done": "Unit Test 完成",
    "e2e_in_progress": "E2E",
    "e2e_done": "E2E 完成",
    "verify_in_progress": "Verify",
    "verify_done": "Verify 完成",
    "cicd_in_progress": "CI/CD",
    "cicd_done": "CI/CD 完成",
    "needs_fix": "需要修复",
    "archived": "已归档",
}

START_CHECKPOINT_TO_SKILL = {
    "plan_in_progress": "autodev-plan",
    "code_in_progress": "autodev-code",
    "requirements_eval_in_progress": "autodev-reviewer",
    "unit_test_in_progress": "autodev-utest",
    "e2e_in_progress": "autodev-e2e",
    "verify_in_progress": "autodev-verify",
}

END_CHECKPOINT_TO_SKILL = {
    "plan_in_progress": "autodev-plan",
    "code_in_progress": "autodev-code",
    "requirements_eval_in_progress": "autodev-reviewer",
    "unit_test_in_progress": "autodev-utest",
    "e2e_in_progress": "autodev-e2e",
    "verify_in_progress": "autodev-verify",
}


def is_separator_row(cells: list[str]) -> bool:
    return all(cell and set(cell) <= {"-", ":"} for cell in cells)


def parse_state_record_table(content: str) -> tuple[dict[str, dict[str, str]], list[str]]:
    result: dict[str, dict[str, str]] = {}
    errors: list[str] = []

    for lineno, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line.startswith("|"):
            continue

        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if cells[0] == "Feature" or is_separator_row(cells):
            continue

        feature = cells[0]
        checkpoint = cells[2]
        if not feature:
            errors.append(f"line {lineno}: Feature 为空")
            continue
        if checkpoint not in KNOWN_CHECKPOINTS:
            errors.append(f"line {lineno}: Feature '{feature}' 使用了未知 checkpoint: {checkpoint}")
            continue
        if feature in result:
            errors.append(f"line {lineno}: Feature '{feature}' 出现重复行")
            continue
        result[feature] = {
            "feature": feature,
            "owner": cells[1] if len(cells) > 1 else "",
            "checkpoint": checkpoint,
            "stage": cells[3] if len(cells) > 3 else "",
            "iteration": cells[4] if len(cells) > 4 else "",
            "updated_at": cells[5] if len(cells) > 5 else "",
        }

    return result, errors


def parse_state_table(content: str) -> tuple[dict[str, str], list[str]]:
    records, errors = parse_state_record_table(content)
    return {feature: row["checkpoint"] for feature, row in records.items()}, errors


def parse_state_rows(content: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if cells[0] == "Feature" or is_separator_row(cells):
            continue
        if cells[0]:
            rows[cells[0]] = cells[2]
    return rows


def read_stdin_text() -> str:
    raw = sys.stdin.buffer.read()
    if not raw:
        return ""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode(sys.stdin.encoding or "utf-8", errors="replace")


def hook_log(message: str) -> None:
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")
    except Exception:
        # 日志不能影响 hook 本身执行
        pass


def load_workflow_nodes() -> list[dict]:
    try:
        config = json.loads(BOARD_CONFIG_PATH.read_text(encoding="utf-8"))
        nodes = config.get("workflow", {}).get("nodes", [])
        return nodes if isinstance(nodes, list) else []
    except Exception:
        return []


def checkpoint_node_id(checkpoint: str | None) -> str:
    if not checkpoint:
        return ""
    _, node_id = find_current_node(load_workflow_nodes(), checkpoint)
    return node_id or ""


def safe_feature_slug(feature: str) -> bool:
    path = Path(feature)
    return bool(feature) and not path.is_absolute() and ".." not in path.parts


def append_feature_hook_log(
    workspace_root: Path,
    feature: str,
    checkpoint: str | None,
    *,
    hook_id: str,
    label: str,
    status: str,
    decision: str,
    exit_code: int,
    summary: str,
) -> None:
    if not safe_feature_slug(feature):
        return
    record = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "featureId": feature,
        "nodeId": checkpoint_node_id(checkpoint),
        "hookId": hook_id,
        "label": label,
        "event": "PreToolUse",
        "status": status,
        "decision": decision,
        "exitCode": exit_code,
        "summary": summary,
    }
    try:
        path = get_feature_hook_log_path(workspace_root, feature)
        ensure_dir(path.parent)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        hook_log(f"feature hook log write failed for feature={feature!r}")


def append_checkpoint_hook_logs(
    workspace_root: Path,
    changes: list[tuple[str, str | None, str | None]],
    *,
    hook_id: str,
    label: str,
    errors: list[str],
    exit_code: int,
) -> None:
    if not changes:
        return
    status = "blocked" if errors else "passed"
    decision = "block" if errors else "pass"
    summary = "\n".join(errors) if errors else f"{label} 通过"
    for feature, old_checkpoint, new_checkpoint in changes:
        transition = f"{old_checkpoint or 'empty'} -> {new_checkpoint or 'empty'}"
        append_feature_hook_log(
            workspace_root,
            feature,
            new_checkpoint or old_checkpoint,
            hook_id=hook_id,
            label=label,
            status=status,
            decision=decision,
            exit_code=exit_code,
            summary=f"{transition}: {summary}",
        )


def normalize_path(path: str, cwd: Path | None = None) -> Path:
    cwd = cwd or Path.cwd()
    normalized = Path(path.replace("\\", "/"))
    if not normalized.is_absolute():
        normalized = cwd / normalized
    return normalized.resolve()


def has_state_path_suffix(path: Path) -> bool:
    parts = tuple(part for part in str(path).replace("\\", "/").split("/") if part and part != ".")
    return parts[-len(STATE_PATH_SUFFIX) :] == STATE_PATH_SUFFIX


def is_state_path(path: str, cwd: Path | None = None) -> bool:
    if not path:
        return False
    return has_state_path_suffix(normalize_path(path, cwd))


def get_current_content(state_path: Path) -> str:
    return state_path.read_text(encoding="utf-8") if state_path.exists() else ""


def build_new_content(
    payload: dict,
    state_path: Path,
    *,
    error_subject: str = "checkpoint 转移",
    include_state_in_missing_error: bool = True,
) -> tuple[str, list[str]]:
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    if tool_name in {"write_file", "WriteFile"}:
        return tool_input.get("content", ""), []

    def missing_old_error(tool_label: str) -> str:
        target = f"STATE.md {error_subject}" if include_state_in_missing_error else error_subject
        return f"{tool_label} 缺少 old，无法验证 {target}"

    if tool_name in {"edit_file", "StrReplaceFile"}:
        content = get_current_content(state_path)

        if "oldString" in tool_input or "newString" in tool_input:
            old = tool_input.get("oldString", "")
            new = tool_input.get("newString", "")
            replace_all = bool(tool_input.get("replaceAll"))
            if not old:
                target = f"STATE.md {error_subject}" if include_state_in_missing_error else error_subject
                return content, [f"edit_file 缺少 oldString，无法验证 {target}"]
            if old not in content:
                return content, [f"edit_file 的 oldString 未在当前 STATE.md 中找到，无法验证 {error_subject}"]
            return (content.replace(old, new) if replace_all else content.replace(old, new, 1)), []

        for edit in tool_input.get("edit", []):
            old = edit.get("old", "")
            new = edit.get("new", "")
            if not old:
                return content, [missing_old_error("edit_file.edit")]
            if old not in content:
                return content, [f"edit_file.edit 的 old 未在当前 STATE.md 中找到，无法验证 {error_subject}"]
            content = content.replace(old, new, 1)
        return content, []

    return "", []


def validate_transitions(old_map: dict[str, str], new_map: dict[str, str]) -> list[str]:
    errors: list[str] = []

    for feature in sorted(set(old_map) | set(new_map)):
        old_cp = old_map.get(feature)
        new_cp = new_map.get(feature)

        if old_cp is None:
            if new_cp not in INITIAL_CHECKPOINTS:
                allowed = " / ".join(sorted(INITIAL_CHECKPOINTS))
                errors.append(f"Feature '{feature}' 是新增行，只允许从空状态进入 {allowed}，当前为 {new_cp}")
            continue

        if new_cp is None:
            errors.append(f"Feature '{feature}' 被从 STATE.md 删除")
            continue

        if old_cp == new_cp:
            continue

        allowed_next = ALLOWED_NEXT.get(old_cp, set())
        if new_cp not in allowed_next:
            allowed = ", ".join(sorted(allowed_next)) if allowed_next else "无"
            errors.append(f"Feature '{feature}' 非法转移: {old_cp} -> {new_cp}；允许的下一个状态: {allowed}")

    return errors


def changed_rows(old_rows: dict[str, str], new_rows: dict[str, str]) -> list[tuple[str, str | None, str | None]]:
    changes: list[tuple[str, str | None, str | None]] = []
    for slug in sorted(set(old_rows) | set(new_rows)):
        old_checkpoint = old_rows.get(slug)
        new_checkpoint = new_rows.get(slug)
        if old_checkpoint != new_checkpoint:
            changes.append((slug, old_checkpoint, new_checkpoint))
    return changes


def check_stage_inputs(root: Path, slug: str, skill: str, repo_root: Path = ROOT) -> str | None:
    code, message = run_precheck(repo_root, root, skill, slug)
    if code != 0:
        return message
    return None


def check_stage_outputs(root: Path, slug: str, skill: str, repo_root: Path = ROOT) -> str | None:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code, message = run_postcheck(repo_root, root, skill, slug)
    if code != 0:
        detail = output.getvalue().strip()
        return f"{message}\n{detail}" if detail else message
    return None


def validate_lifecycle(
    root: Path,
    old_rows: dict[str, str],
    new_rows: dict[str, str],
    repo_root: Path = ROOT,
) -> list[str]:
    errors: list[str] = []
    for slug, old_checkpoint, new_checkpoint in changed_rows(old_rows, new_rows):
        if new_checkpoint:
            start_skill = START_CHECKPOINT_TO_SKILL.get(new_checkpoint)
            if start_skill:
                error = check_stage_inputs(root, slug, start_skill, repo_root)
                if error:
                    errors.append(error)

        if old_checkpoint and old_checkpoint != new_checkpoint:
            end_skill = END_CHECKPOINT_TO_SKILL.get(old_checkpoint)
            if end_skill:
                error = check_stage_outputs(root, slug, end_skill, repo_root)
                if error:
                    errors.append(error)

    return errors


def features_entering_code_done(old_map: dict[str, str], new_map: dict[str, str]) -> list[str]:
    return [
        feature
        for feature in sorted(set(old_map) & set(new_map))
        if old_map.get(feature) == "code_in_progress" and new_map.get(feature) == "code_done"
    ]


def tail_output(stdout: str, stderr: str) -> str:
    combined = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part)
    if len(combined) <= MAVEN_OUTPUT_LIMIT:
        return combined
    return combined[-MAVEN_OUTPUT_LIMIT:]


def validate_maven_compile(cwd: Path, features: list[str]) -> list[str]:
    if not features:
        return []

    feature_list = ", ".join(features)
    if not (cwd / "pom.xml").is_file():
        return [f"Feature '{feature_list}' 进入 code_done 前编译校验失败: workspace 根目录缺少 pom.xml"]

    mvn = shutil.which("mvn")
    if not mvn:
        return [f"Feature '{feature_list}' 进入 code_done 前编译校验失败: 未找到 mvn 命令"]

    try:
        result = subprocess.run(
            [mvn, "compile"],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=MAVEN_COMPILE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        output = tail_output(error.stdout or "", error.stderr or "")
        detail = f"\nMaven 输出尾部:\n{output}" if output else ""
        return [
            f"Feature '{feature_list}' 进入 code_done 前编译校验超时: mvn compile 超过 "
            f"{MAVEN_COMPILE_TIMEOUT_SECONDS} 秒{detail}"
        ]
    except OSError as error:
        return [f"Feature '{feature_list}' 进入 code_done 前编译校验失败: 无法执行 mvn compile: {error}"]

    if result.returncode != 0:
        output = tail_output(result.stdout, result.stderr)
        detail = f"\nMaven 输出尾部:\n{output}" if output else ""
        return [
            f"Feature '{feature_list}' 进入 code_done 前编译校验失败: mvn compile 退出码 "
            f"{result.returncode}{detail}"
        ]

    return []


def payload_state_path(payload: dict) -> Path | None:
    tool_input = payload.get("tool_input", {})
    file_path = tool_input.get("filePath") or tool_input.get("file_path") or tool_input.get("path", "")
    cwd = Path(payload.get("cwd") or Path.cwd())
    if not is_state_path(str(file_path), cwd):
        return None
    return normalize_path(str(file_path), cwd)


def block(reason: str, system_message: str) -> int:
    print(reason, file=sys.stderr)
    json.dump(
        {
            "decision": "block",
            "reason": reason,
            "systemMessage": system_message,
        },
        sys.stdout,
        ensure_ascii=False,
    )
    return BLOCK_EXIT_CODE


def run_state_done(payload: dict) -> int:
    tool_input = payload.get("tool_input", {})
    file_path = tool_input.get("filePath") or tool_input.get("file_path") or tool_input.get("path", "")
    cwd = Path(payload.get("cwd") or Path.cwd())
    hook_log(f"file_path={file_path!r}")
    hook_log(f"cwd={cwd}")

    state_path = payload_state_path(payload)
    if state_path is None:
        hook_log("not state path, skip")
        return 0
    hook_log(f"state_path={state_path}")

    old_map, old_errors = parse_state_table(get_current_content(state_path))
    new_content, edit_errors = build_new_content(payload, state_path)
    new_map, new_errors = parse_state_table(new_content)
    changes = changed_rows(old_map, new_map)
    hook_log(f"oldmap={old_map},new_map={new_map}")
    errors = [*old_errors, *edit_errors, *new_errors, *validate_transitions(old_map, new_map)]
    hook_log(f"errors_count={len(errors)}")

    if errors:
        append_checkpoint_hook_logs(
            state_path.parent.parent,
            changes,
            hook_id="state-done",
            label="STATE checkpoint 转移校验",
            errors=errors,
            exit_code=BLOCK_EXIT_CODE,
        )
        for error in errors:
            hook_log(f"validation_error={error}")
            print(error, file=sys.stderr)
        json.dump(
            {
                "decision": "block",
                "reason": "\n".join(errors),
                "systemMessage": "STATE.md checkpoint 转移校验失败，请按工作流顺序更新 checkpoint。",
            },
            sys.stdout,
            ensure_ascii=False,
        )
        return BLOCK_EXIT_CODE

    append_checkpoint_hook_logs(
        state_path.parent.parent,
        changes,
        hook_id="state-done",
        label="STATE checkpoint 转移校验",
        errors=[],
        exit_code=0,
    )
    hook_log("hook passed")
    return 0


def run_autodev_lifecycle(payload: dict) -> int:
    state_path = payload_state_path(payload)
    if state_path is None:
        return 0

    workspace_root = state_path.parent.parent
    old_content = get_current_content(state_path)
    new_content, edit_errors = build_new_content(payload, state_path)
    old_rows = parse_state_rows(old_content)
    new_rows = parse_state_rows(new_content)
    changes = changed_rows(old_rows, new_rows)
    errors = list(edit_errors)
    if not errors:
        errors.extend(validate_lifecycle(workspace_root, old_rows, new_rows))

    if errors:
        append_checkpoint_hook_logs(
            workspace_root,
            changes,
            hook_id="autodev-lifecycle",
            label="Autodev 产物校验",
            errors=errors,
            exit_code=BLOCK_EXIT_CODE,
        )
        return block(
            "\n".join(errors),
            "Autodev artifact hook failed. Fix the stage artifacts, then retry STATE.md.",
        )
    append_checkpoint_hook_logs(
        workspace_root,
        changes,
        hook_id="autodev-lifecycle",
        label="Autodev 产物校验",
        errors=[],
        exit_code=0,
    )
    return 0


def run_code_compile(payload: dict) -> int:
    state_path = payload_state_path(payload)
    if state_path is None:
        return 0

    workspace_root = state_path.parent.parent
    old_map = parse_state_rows(get_current_content(state_path))
    new_content, edit_errors = build_new_content(
        payload,
        state_path,
        error_subject="code_done 前编译条件",
        include_state_in_missing_error=False,
    )
    new_map = parse_state_rows(new_content)
    compile_features = features_entering_code_done(old_map, new_map)
    changes = [(feature, old_map.get(feature), new_map.get(feature)) for feature in compile_features]
    errors = [
        *edit_errors,
        *validate_maven_compile(workspace_root, compile_features),
    ]
    if errors:
        append_checkpoint_hook_logs(
            workspace_root,
            changes,
            hook_id="code-compile",
            label="code_done 编译校验",
            errors=errors,
            exit_code=BLOCK_EXIT_CODE,
        )
        for error in errors:
            print(error, file=sys.stderr)
        json.dump(
            {
                "decision": "block",
                "reason": "\n".join(errors),
                "systemMessage": "code_done 前编译校验失败，请确保 workspace 根目录 mvn compile 通过后再推进 checkpoint。",
            },
            sys.stdout,
            ensure_ascii=False,
        )
        return BLOCK_EXIT_CODE
    append_checkpoint_hook_logs(
        workspace_root,
        changes,
        hook_id="code-compile",
        label="code_done 编译校验",
        errors=[],
        exit_code=0,
    )
    return 0


HOOK_COMMANDS = {
    "state-done": run_state_done,
    "autodev-lifecycle": run_autodev_lifecycle,
    "code-compile": run_code_compile,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or argv[0] not in HOOK_COMMANDS:
        choices = " | ".join(sorted(HOOK_COMMANDS))
        print(f"usage: state_checkpoint.py <{choices}>", file=sys.stderr)
        return 1

    command = argv[0]
    hook_log("hook invoked")
    hook_log(f"process_cwd={Path.cwd()}")
    raw_input = read_stdin_text()
    hook_log(f"stdin_len={len(raw_input)}")
    hook_log(f"stdin_repr={raw_input[:1000]!r}")
    if not raw_input.strip():
        hook_log("empty stdin, skip json parse")
        return 0

    try:
        payload = json.loads(raw_input)
    except Exception as exc:
        hook_log(f"json parse failed: {type(exc).__name__}: {exc}")
        raise
    hook_log(f"payload_keys={list(payload.keys())}")
    return HOOK_COMMANDS[command](payload)


if __name__ == "__main__":
    raise SystemExit(main())
