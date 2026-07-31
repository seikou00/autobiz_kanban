#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""托管模式（auto mode）决策入口：把 Feature 当前状态编译成平台可执行的 action 数组。

board_config.json 注册（参数式定位，与 skip_node.py 同构——托管调用不注入
PLUGIN_WORKSPACE / PROJECT_DIR / FEATURE_ID 环境变量）::

    "auto_next_step": "python3 ${pluginPath}/hooks/auto_next_step.py --plugin-workspace ${pluginWorkspace} --project ${projectDir} --feature ${feature} --event-json ${eventJson}"

``${eventJson}`` 在平台侧是「先分词、再逐 token 替换占位符」，并通过 execFileSync 直接
传 argv（无 shell），因此整段 JSON 会作为**一个** argv 到达，不需要额外引号处理。

输出契约（平台 normalizeAutoNextStepResult）::

    { "ok": bool, "messages": str, "action": [<=10 个动作] }

    action 项:
      {"actionType": "continue_current_session", "nextAction": {slashSkill?, userMessage?, autoSend}}
      {"actionType": "create_new_session", "sessionWorkspace"?, "nextAction": {...}}
      {"actionType": "complete"}

硬约束：
  · stdout 只输出最终 JSON，日志走 stderr；
  · **任何情况都 exit 0**——execFileSync 在非零退出时抛异常，ok:false 就传不到控制面；
  · autoSend=true 时 userMessage 必须非空；
  · complete 必须是唯一 action；continue_current_session 最多一个。

「暂停等用户」为什么不返回 action:[]：平台 renderer 只对 AUTO_MODE_CANCELLED_MESSAGE
弹 toast，其余 messages 只进主进程 console，空数组在 UI 上完全不可见。因此需要人决策的
场景统一返回 continue_current_session + autoSend:false，把说明写进 userMessage 落成
pendingAutoDraft 填进输入框（renderer 侧已有「不覆盖用户已有草稿」保护）。

除 `autoMode.autoEnterCodeAfterPlan` 外，本脚本对 state.json 只读；该策略只在托管模式
完成 plan 时，复用 update_checkpoint.py 的校验与原子写入，把可选详细设计标记为跳过并
进入 code。其他 checkpoint 推进仍然只由技能经 update_checkpoint.py 完成。唯一常规写入是
自己的记账文件 {FEATURE_DIR}/.auto-mode/state.json。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_kernel import FileLock  # noqa: E402
from hooks.json_writer_common import atomic_write_json  # noqa: E402
from hooks.paths import get_plugin_output_workspace  # noqa: E402
from hooks.route_checkpoint import resolve_route  # noqa: E402
from hooks.update_checkpoint import prepare_checkpoint_update, write_hook_logs  # noqa: E402
from board_core.state_store import write_state_records  # noqa: E402
from board_core.workflow_compiler import read_json  # noqa: E402


BOARD_CONFIG_PATH = ROOT / "board_core" / "board_config.json"

AUTO_MODE_DIR_NAME = ".auto-mode"
AUTO_MODE_STATE_FILE = "state.json"
HISTORY_LIMIT = 20
PROCESSED_EVENT_LIMIT = 50

DEFAULT_AUTO_MODE_CONFIG = {
    "contextUsageThreshold": 0.9,
    "maxErrorRetries": 2,
    "maxStalledSteps": 3,
    "maxFixReflows": 3,
    "maxTotalSteps": 120,
    "autoEnterCodeAfterPlan": True,
}

# 托管续跑消息前缀：让模型知道本轮由托管模式自动发起，而不是用户新提的需求。
AUTO_PREFIX = "[托管模式自动推进]"


def log(message: str) -> None:
    """日志只走 stderr——stdout 被平台当作纯 JSON 解析。"""
    print(f"[auto_next_step] {message}", file=sys.stderr)


def auto_mode_config() -> dict[str, Any]:
    """读取 board_config.json 顶层 autoMode，缺失字段回落默认值。"""
    merged = dict(DEFAULT_AUTO_MODE_CONFIG)
    try:
        raw = read_json(BOARD_CONFIG_PATH).get("autoMode")
    except Exception as exc:  # 配置损坏不应让托管链直接崩掉
        log(f"读取 autoMode 配置失败，使用默认值: {exc}")
        return merged
    if not isinstance(raw, dict):
        return merged
    for key, default in DEFAULT_AUTO_MODE_CONFIG.items():
        value = raw.get(key)
        if isinstance(default, bool):
            if isinstance(value, bool):
                merged[key] = value
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if key == "contextUsageThreshold":
            if 0 < float(value) <= 1:
                merged[key] = float(value)
        elif int(value) >= 0:
            merged[key] = int(value)
    return merged


# --------------------------------------------------------------------------- #
# 结果构造
# --------------------------------------------------------------------------- #


def _next_action(slash_skill: str, user_message: str, *, auto_send: bool) -> dict[str, Any]:
    action: dict[str, Any] = {"autoSend": auto_send}
    if slash_skill:
        action["slashSkill"] = slash_skill
    if user_message:
        action["userMessage"] = user_message
    return action


def continue_session(slash_skill: str, user_message: str, *, auto_send: bool = True) -> dict[str, Any]:
    return {
        "actionType": "continue_current_session",
        "nextAction": _next_action(slash_skill, user_message, auto_send=auto_send),
    }


def new_session(slash_skill: str, user_message: str) -> dict[str, Any]:
    """新开会话总是 autoSend=true；需要人确认的场景一律走 pause() 填草稿。"""
    return {
        "actionType": "create_new_session",
        "nextAction": _next_action(slash_skill, user_message, auto_send=True),
    }


def pause(user_message: str, slash_skill: str = "") -> dict[str, Any]:
    """暂停并把说明预填到来源会话输入框，等用户按发送。"""
    return continue_session(slash_skill, user_message, auto_send=False)


def result(ok: bool, messages: str, action: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"ok": ok, "messages": messages, "action": action or []}


# --------------------------------------------------------------------------- #
# 记账（循环与熔断保护；框架侧第一版明确不做）
# --------------------------------------------------------------------------- #


def feature_dir(workspace: Path, feature: str) -> Path:
    return workspace / ".autobizdevops" / "features" / feature


def auto_mode_dir(target_feature_dir: Path) -> Path:
    return target_feature_dir / AUTO_MODE_DIR_NAME


def auto_mode_state_path(target_feature_dir: Path) -> Path:
    return auto_mode_dir(target_feature_dir) / AUTO_MODE_STATE_FILE


EMPTY_RUN_STATE: dict[str, Any] = {
    "totalSteps": 0,
    "lastEventId": "",
    "lastCheckpoint": "",
    "lastFingerprint": "",
    "stalledSteps": 0,
    "errorStreak": 0,
    "errorCheckpoint": "",
    "fixReflows": 0,
    # 上一轮暂停草稿所属会话。预算只能由该会话的后续人工消息解除，不能被同一
    # Feature 的其他线程结束事件意外清零。
    "awaitingHumanThreadId": "",
    "processedEventIds": [],
    "history": [],
}


def empty_run_state() -> dict[str, Any]:
    """新建空状态；list 字段必须是独立实例，不能共享 EMPTY_RUN_STATE 里的那一份。"""
    state = dict(EMPTY_RUN_STATE)
    state["processedEventIds"] = []
    state["history"] = []
    return state


def _clean_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def load_run_state(target_feature_dir: Path) -> dict[str, Any]:
    path = auto_mode_state_path(target_feature_dir)
    state = empty_run_state()
    if not path.is_file() or path.stat().st_size <= 0:
        return state
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # 记账文件损坏只损失保护计数，不该让托管链失败——重置即可。
        log(f"记账文件不可读，按空状态处理: {exc}")
        return state
    if not isinstance(raw, dict):
        return state

    for key in ("totalSteps", "stalledSteps", "errorStreak", "fixReflows"):
        cleaned = _clean_int(raw.get(key))
        if cleaned is not None:
            state[key] = cleaned
    for key in ("lastEventId", "lastCheckpoint", "lastFingerprint", "errorCheckpoint"):
        value = raw.get(key)
        if isinstance(value, str):
            state[key] = value
    waiting_thread = raw.get("awaitingHumanThreadId")
    if isinstance(waiting_thread, str):
        state["awaitingHumanThreadId"] = waiting_thread
    processed = raw.get("processedEventIds")
    if isinstance(processed, list):
        state["processedEventIds"] = [
            item for item in processed if isinstance(item, str) and item
        ][-PROCESSED_EVENT_LIMIT:]
    history = raw.get("history")
    if isinstance(history, list):
        state["history"] = [item for item in history if isinstance(item, dict)][-HISTORY_LIMIT:]
    return state


def write_run_state(target_feature_dir: Path, state: dict[str, Any]) -> None:
    """写回记账文件。调用方必须已持有 run_state_transaction 的锁。"""
    state["history"] = state.get("history", [])[-HISTORY_LIMIT:]
    state["processedEventIds"] = state.get("processedEventIds", [])[-PROCESSED_EVENT_LIMIT:]
    path = auto_mode_state_path(target_feature_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, state)
    except OSError as exc:
        # 写不进去只是失去保护计数，不阻断本次决策。
        log(f"记账文件写入失败: {exc}")


@contextmanager
def run_state_transaction(target_feature_dir: Path):
    """把「读状态 → 判重 → 决策 → 写回」放进同一临界区。

    平台目前用 execFileSync 同步串行调用，单实例下不会并发；但锁只包住写文件时，
    多窗口 / 多实例 / 未来改异步调用都会让两个进程读到同一份旧状态、各自返回
    create_new_session。临界区覆盖全流程后这条竞态被消除。

    取锁失败（只读挂载等）不阻断决策——退化成无保护的单次执行。
    """
    lock_path = auto_mode_dir(target_feature_dir) / ".lock"
    try:
        auto_mode_dir(target_feature_dir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log(f"记账目录不可创建，本次不加锁: {exc}")
        yield None
        return
    lock = FileLock(lock_path)
    try:
        lock.__enter__()
    except OSError as exc:
        log(f"记账锁不可用，本次不加锁: {exc}")
        yield None
        return
    try:
        yield lock_path
    finally:
        try:
            lock.__exit__(None, None, None)
        except OSError as exc:
            # 解锁失败不应覆盖事务主体的异常；进程退出后 OS 也会释放文件锁。
            log(f"记账锁释放失败: {exc}")


def is_duplicate_event(run_state: dict[str, Any], event_id: str) -> bool:
    """是否已处理过该 eventId。

    只比 lastEventId 挡不住 A→B→重放 A：状态里只有一个槽位，A 已被 B 覆盖。
    因此维护一个有界的已处理集合。
    """
    if not event_id:
        return False
    if event_id == run_state.get("lastEventId"):
        return True
    processed = run_state.get("processedEventIds")
    return isinstance(processed, list) and event_id in processed


def remember_event(state: dict[str, Any], event_id: str) -> None:
    """把 eventId 记进已处理集合（消费掉事件的每条路径都要调用）。"""
    if not event_id:
        return
    state["lastEventId"] = event_id
    processed = [item for item in state.get("processedEventIds", []) if item != event_id]
    processed.append(event_id)
    state["processedEventIds"] = processed[-PROCESSED_EVENT_LIMIT:]


def progress_fingerprint(target_feature_dir: Path) -> str:
    """FEATURE_DIR 产物指纹。

    只看 (相对路径, size, mtime_ns)，不读文件内容——Feature 目录通常几十个文件，
    这比逐文件哈希便宜得多，而任何写入（含 EVIDENCE.jsonl 追加）都会改变它。
    排除 .auto-mode/ 自身，避免记账写入把自己算成"有进展"。

    该指纹不能单独作为空转依据：业务代码通常写在 session workspace，而不在
    FEATURE_DIR。实际空转判断由 observed_progress_fingerprint() 合并来源 workspace
    的可观测 Git 变更；无法观测时宁可不触发空转熔断，交给 maxTotalSteps 兜底。
    """
    if not target_feature_dir.is_dir():
        return ""
    digest = hashlib.sha256()
    entries: list[tuple[str, int, int]] = []
    for path in target_feature_dir.rglob("*"):
        try:
            if not path.is_file():
                continue
            relative = path.relative_to(target_feature_dir)
            if relative.parts and relative.parts[0] == AUTO_MODE_DIR_NAME:
                continue
            stat = path.stat()
            entries.append((relative.as_posix(), stat.st_size, stat.st_mtime_ns))
        except OSError:
            continue
    if not entries:
        return ""
    for relative_path, size, mtime_ns in sorted(entries):
        digest.update(f"{relative_path}|{size}|{mtime_ns}\n".encode("utf-8"))
    return digest.hexdigest()


def workspace_progress_fingerprint(session_workspace_path: str) -> str | None:
    """返回来源 workspace 的 Git 提交与脏文件元数据指纹，无法可靠观测时返回 None。

    只枚举 Git 标记为 modified / untracked 的文件，再读取其 size 和 mtime_ns，避免
    全目录遍历 node_modules 等大目录。文件在同一脏状态下再次被修改时 mtime 仍会变化；
    当前 HEAD 也会纳入指纹，因此已提交且工作区重新变干净的进展不会被遗漏。
    Git 不可用、不是仓库或命令失败时不猜测进度。
    """
    workspace = Path(session_workspace_path).expanduser()
    if not workspace.is_dir():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(workspace), "ls-files", "-m", "-o", "--exclude-standard", "-z"],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    digest = hashlib.sha256()
    try:
        head = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--verify", "HEAD"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    head_returncode = getattr(head, "returncode", 1)
    head_id = (
        os.fsdecode(head.stdout).strip()
        if isinstance(head_returncode, int) and head_returncode == 0
        else "(unborn)"
    )
    digest.update(f"head:{head_id}\n".encode("utf-8"))
    for raw_path in completed.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative_text = os.fsdecode(raw_path)
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        path = workspace / relative
        try:
            stat = path.stat()
            digest.update(f"{relative.as_posix()}|{stat.st_size}|{stat.st_mtime_ns}\n".encode("utf-8"))
        except OSError:
            # 删除的已跟踪文件同样代表进度，给它稳定的缺失标记。
            digest.update(f"{relative.as_posix()}|missing\n".encode("utf-8"))
    return digest.hexdigest()


def observed_progress_fingerprint(
    target_feature_dir: Path,
    session_workspace_path: str | None,
) -> str | None:
    """合并 Feature 产物和业务 workspace；缺一不可时禁用空转熔断。"""
    if not session_workspace_path:
        return None
    workspace_fingerprint = workspace_progress_fingerprint(session_workspace_path)
    if workspace_fingerprint is None:
        return None
    digest = hashlib.sha256()
    digest.update(f"feature:{progress_fingerprint(target_feature_dir)}\n".encode("utf-8"))
    digest.update(f"workspace:{workspace_fingerprint}\n".encode("utf-8"))
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# 事件解析
# --------------------------------------------------------------------------- #


class EventError(ValueError):
    """--event-json 非法。"""


def parse_event(raw: str | None) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise EventError("--event-json 为空")
    try:
        event = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EventError(f"--event-json 不是合法 JSON: {exc}") from exc
    if not isinstance(event, dict):
        raise EventError("--event-json 必须是 JSON 对象")

    event_type = event.get("eventType")
    if event_type != "agent_turn_end":
        raise EventError(f"不支持的 eventType: {event_type or '(missing)'}")
    outcome = event.get("outcome")
    if outcome not in ("success", "error"):
        raise EventError(f"不支持的 outcome: {outcome or '(missing)'}")
    session_workspace_path = event.get("sessionWorkspacePath")
    if session_workspace_path is not None and not isinstance(session_workspace_path, str):
        raise EventError("sessionWorkspacePath 必须是字符串")
    return event


def context_ratio(event: dict[str, Any]) -> float | None:
    """上下文占用比例；平台在拿不到可靠值时会整体省略 contextUsage。"""
    usage = event.get("contextUsage")
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("inputTokens")
    max_tokens = usage.get("maxTokens")
    if isinstance(input_tokens, bool) or isinstance(max_tokens, bool):
        return None
    if not isinstance(input_tokens, (int, float)) or not isinstance(max_tokens, (int, float)):
        return None
    if max_tokens <= 0 or input_tokens < 0:
        return None
    return float(input_tokens) / float(max_tokens)


def end_reason_text(event: dict[str, Any]) -> str:
    reason = event.get("endReason")
    if not isinstance(reason, dict):
        return "unknown"
    code = reason.get("code")
    code_text = code if isinstance(code, str) and code else "unknown"
    message = reason.get("message")
    if isinstance(message, str) and message.strip():
        return f"{code_text}: {message.strip()}"
    return code_text


# --------------------------------------------------------------------------- #
# 消息组装
# --------------------------------------------------------------------------- #


def route_next_action(route: dict[str, Any]) -> tuple[str, str]:
    """取 route 的 nextAction（board_config 各节点 state 已配好），回落 recommendedNextSkill。"""
    action = route.get("nextAction")
    action = action if isinstance(action, dict) else {}
    skill = action.get("slashSkill")
    skill = skill.strip() if isinstance(skill, str) else ""
    message = action.get("userMessage")
    message = message.strip() if isinstance(message, str) else ""
    if not skill:
        recommended = route.get("recommendedNextSkill")
        skill = recommended.strip() if isinstance(recommended, str) else ""
    if skill and not message:
        message = f"请使用 /{skill} 继续推进当前 Feature。"
    return skill, message


def auto_advance_plan_done_to_code(
    *,
    workspace: Path,
    feature: str,
    route: dict[str, Any],
) -> str | None:
    """在托管模式自动跳过所有可直接落到 code 的 plan 后动态阶段。

    返回非空说明代表 checkpoint 已持久化为 code_in_progress。任何不满足当前
    策略的动态选择或状态校验失败都保留原有的人工确认分支。
    """
    if route.get("checkpoint") != "plan_done" or not route.get("requiresWorkflowChoice"):
        return None

    choices = route.get("workflowChoices")
    choices = [item for item in choices if isinstance(item, dict)] if isinstance(choices, list) else []
    stage_ids: set[str] = set()
    skip_decisions: dict[str, str] = {}
    for choice in choices:
        stage_id = choice.get("stageId")
        if not isinstance(stage_id, str) or not stage_id:
            continue
        stage_ids.add(stage_id)
        if (
            choice.get("decision") == "skipped"
            and choice.get("targetCheckpoint") == "code_in_progress"
        ):
            skip_decisions[stage_id] = "skipped"

    if not stage_ids or set(skip_decisions) != stage_ids:
        log("plan_done 存在不能直接落到 code 的动态选择，保留人工确认。")
        return None

    checkpoint_update = prepare_checkpoint_update(
        workspace=workspace,
        feature=feature,
        checkpoint="code_in_progress",
        workflow_decision_updates=skip_decisions,
    )
    if not checkpoint_update.ok:
        errors = "；".join(checkpoint_update.errors) or "未知校验错误"
        log(f"plan_done 自动进入 code 的 checkpoint 更新失败: {errors}")
        try:
            write_hook_logs(checkpoint_update, workspace=workspace, feature=feature)
        except Exception as exc:
            log(f"checkpoint 更新失败日志写入失败: {exc}")
        return None

    try:
        write_state_records(workspace, checkpoint_update.records)
    except Exception as exc:
        log(f"plan_done 自动进入 code 的状态写入失败: {exc}")
        return None
    try:
        write_hook_logs(checkpoint_update, workspace=workspace, feature=feature)
    except Exception as exc:
        # 状态已原子写入，日志失败不能阻断已经确定的后续 code 会话。
        log(f"checkpoint 更新日志写入失败: {exc}")

    stages = ", ".join(sorted(skip_decisions))
    return f"已自动跳过可选阶段 {stages}，checkpoint 已进入 code_in_progress。"


def auto_message(body: str, *, note: str = "") -> str:
    """统一给托管发起的消息加前缀，让模型知道这不是用户新提的需求。"""
    parts = [f"{AUTO_PREFIX} {body}".strip()]
    if note:
        parts.append(note)
    return "\n".join(parts)


def checkpoint_note(route: dict[str, Any]) -> str:
    checkpoint = route.get("checkpoint") or "(unknown)"
    node_id = route.get("currentNodeId") or "(unknown)"
    return f"当前 checkpoint={checkpoint}，节点={node_id}。"


def resume_note(route: dict[str, Any]) -> str:
    """新会话没有上一轮对话，必须提示先读状态再干活。"""
    return (
        f"本会话由托管模式新开，没有上一轮对话上下文。{checkpoint_note(route)}"
        "请先读取 state.json 与该阶段既有产物，确认已完成到哪一步，再从断点继续，不要重头再来。"
    )


# --------------------------------------------------------------------------- #
# 决策
# --------------------------------------------------------------------------- #


def decide(
    *,
    event: dict[str, Any],
    route: dict[str, Any],
    run_state: dict[str, Any],
    fingerprint: str | None,
    config: dict[str, Any],
    force_new_session: bool = False,
    auto_advance_note: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回 (平台结果, 更新后的记账状态)。

    按序短路，每条分支都要么给出动作、要么明确暂停——不返回不可见的空数组
    （complete 与「重复事件」两种终止态例外）。
    """
    checkpoint = str(route.get("checkpoint") or "")
    outcome = event.get("outcome")
    ratio = context_ratio(event)
    threshold = float(config["contextUsageThreshold"])
    skill, message = route_next_action(route)

    state = dict(run_state)
    state["processedEventIds"] = list(run_state.get("processedEventIds", []))
    remember_event(state, str(event.get("eventId") or ""))
    state["lastFingerprint"] = fingerprint or ""

    # 人工接管后解除熔断：autoSend:false 的草稿落在来源会话，因此只能由同一会话的
    # 后续事件解除预算。Feature 允许多会话，不能让其他线程错误消费这次人工接管。
    event_thread_id = str(event.get("threadId") or "")
    awaiting_human_thread_id = str(run_state.get("awaitingHumanThreadId") or "")
    resumed_by_human = bool(
        event_thread_id and event_thread_id == awaiting_human_thread_id
    )
    if resumed_by_human:
        log("检测到人工发送草稿后的首次结束事件，重置托管预算。")
        state["totalSteps"] = 0
        state["stalledSteps"] = 0
        state["errorStreak"] = 0
        state["errorCheckpoint"] = ""
        state["fixReflows"] = 0
    elif checkpoint != "needs_fix" and run_state.get("lastCheckpoint") == "needs_fix":
        # 已离开 needs_fix，说明上一轮回流已经推动 Feature 进入可执行阶段；不应让
        # 旧循环继续挤占后续独立修复的额度。
        state["fixReflows"] = 0
    def finish(payload: dict[str, Any], summary: str) -> tuple[dict[str, Any], dict[str, Any]]:
        # 本轮以「暂停等人」收尾时记录来源会话；下一次同会话事件据此重置预算。
        paused = any(
            item.get("actionType") == "continue_current_session"
            and not item.get("nextAction", {}).get("autoSend", False)
            for item in payload.get("action", [])
        )
        if paused:
            state["awaitingHumanThreadId"] = event_thread_id
        elif not (awaiting_human_thread_id and not resumed_by_human):
            state["awaitingHumanThreadId"] = ""
        history = list(state.get("history", []))
        history.append({
            "eventId": state["lastEventId"],
            "eventTime": str(event.get("eventTime") or ""),
            "outcome": outcome,
            "checkpoint": checkpoint,
            "contextRatio": round(ratio, 4) if ratio is not None else None,
            "decision": summary,
        })
        state["history"] = history
        return payload, state

    # 同一 Feature 可以有多个会话。A 会话暂停等人时，B 会话的结束事件不得清除 A
    # 的预算熔断或再次自动推进；只有 A 的草稿被人工发送后才允许整个 Feature 恢复。
    if awaiting_human_thread_id and not resumed_by_human:
        return finish(
            result(
                True,
                "当前 Feature 正等待另一会话中的人工确认，本次会话不执行自动推进。",
                [{"actionType": "complete"}],
            ),
            "complete:awaiting_human_in_other_thread",
        )

    state["totalSteps"] = int(state.get("totalSteps", 0)) + 1

    # 停滞检测只接受可观测的业务 workspace + FEATURE_DIR 组合指纹。inputTokens 是整轮
    # 请求的输入上下文高水位，不是本轮工作量，不能用于判断是否真的修改了代码。
    same_checkpoint = checkpoint == run_state.get("lastCheckpoint")
    same_fingerprint = fingerprint is not None and fingerprint == run_state.get("lastFingerprint")
    no_progress = same_checkpoint and same_fingerprint
    if resumed_by_human:
        state["stalledSteps"] = 0
    elif no_progress:
        state["stalledSteps"] = int(state.get("stalledSteps", 0)) + 1
    else:
        state["stalledSteps"] = 0
    state["lastCheckpoint"] = checkpoint

    if outcome == "error":
        if state.get("errorCheckpoint") == checkpoint:
            state["errorStreak"] = int(state.get("errorStreak", 0)) + 1
        else:
            state["errorStreak"] = 1
        state["errorCheckpoint"] = checkpoint
    else:
        state["errorStreak"] = 0
        state["errorCheckpoint"] = ""

    # 1) 归档：整链结束。
    if checkpoint == "archived":
        return finish(
            result(True, "Feature 已归档，托管推进链结束。", [{"actionType": "complete"}]),
            "complete:archived",
        )

    # 2) 总步数硬闸。
    if state["totalSteps"] > int(config["maxTotalSteps"]):
        body = (
            f"托管模式已累计自动推进 {state['totalSteps'] - 1} 次，达到上限"
            f"（maxTotalSteps={config['maxTotalSteps']}），已停止自动推进以免失控。"
            f"{checkpoint_note(route)}确认无误后手动发送本条消息即可继续，"
            "并重新获得一个完整的自动推进额度。"
        )
        return finish(
            result(True, "达到托管总步数上限，暂停自动推进。", [pause(auto_message(body), skill)]),
            "pause:max_total_steps",
        )

    # 3) 空转熔断：连续多轮 checkpoint、Feature 产物和业务工作区 Git 状态都没变化。
    if state["stalledSteps"] >= int(config["maxStalledSteps"]):
        body = (
            f"托管模式连续 {state['stalledSteps']} 轮没有观察到 checkpoint、Feature 产物或业务工作区变更，"
            f"疑似空转（例如 Agent 以纯文本提问结束回合），已停止自动推进。"
            f"{checkpoint_note(route)}"
            "请补充所需信息后手动发送本条消息继续，之后会重新开始计数。"
        )
        return finish(
            result(True, "检测到托管空转，暂停自动推进。", [pause(auto_message(body), skill)]),
            "pause:stalled",
        )

    # 4) 异常中断续跑（用户主动停止不会走到这里——平台侧只弹 toast，不调本命令）。
    if outcome == "error":
        reason = end_reason_text(event)
        max_retries = int(config["maxErrorRetries"])
        if state["errorStreak"] > max_retries:
            body = (
                f"当前阶段连续 {state['errorStreak']} 次异常结束（最近原因：{reason}），"
                f"已达重试上限（maxErrorRetries={max_retries}），停止自动推进。"
                f"{checkpoint_note(route)}请人工排查后手动发送本条消息继续，"
                "之后会重新开始计数。"
            )
            return finish(
                result(True, f"异常重试已达上限：{reason}", [pause(auto_message(body), skill)]),
                "pause:error_retry_exhausted",
            )
        if not skill:
            body = (
                f"当前阶段异常结束（{reason}），但插件无法解析下一步技能，停止自动推进。"
                f"{checkpoint_note(route)}"
            )
            return finish(
                result(True, f"异常结束但无可用技能：{reason}", [pause(auto_message(body))]),
                "pause:error_without_skill",
            )
        body = f"上一轮执行异常结束（{reason}），请从当前断点继续本阶段工作。{message}"
        # 上下文已经接近上限时，同会话重试会立刻再撞上限，改为新开会话。
        if ratio is not None and ratio >= threshold:
            return finish(
                result(
                    True,
                    f"异常结束且上下文占用 {ratio:.0%} 已超阈值，新开会话重试（第 {state['errorStreak']} 次）。",
                    [new_session(skill, auto_message(body, note=resume_note(route)))],
                ),
                "new_session:error_retry_high_context",
            )
        return finish(
            result(
                True,
                f"异常结束，同会话重试（第 {state['errorStreak']} 次，原因：{reason}）。",
                [continue_session(skill, auto_message(body))],
            ),
            "continue:error_retry",
        )

    # 5) needs_fix 回流：按 FIX_REQUEST.json 的 suggestedCheckpoint 新开会话修复。
    if checkpoint == "needs_fix":
        return finish(*_decide_needs_fix(route, state, config, skill, message))

    # 6) 需要人决策的岔路口（动态阶段 / profile 选择）。
    if route.get("requiresWorkflowChoice"):
        return finish(*_decide_workflow_choice(route, skill))
    if route.get("requiresProfileChoice"):
        body = (
            "当前 checkpoint 需要先选择 workflow profile，托管模式不代替你做该决策。"
            f"{checkpoint_note(route)}确认后手动发送继续。"
        )
        return finish(
            result(True, "等待用户选择 workflow profile。", [pause(auto_message(body), skill)]),
            "pause:profile_choice",
        )

    # 7) 正常推进。
    if not skill:
        body = (
            "插件无法解析下一步技能，已停止自动推进。"
            f"{checkpoint_note(route)}请手动指定下一步。"
        )
        return finish(
            result(True, "无法解析下一步技能，暂停自动推进。", [pause(auto_message(body))]),
            "pause:no_skill",
        )

    if force_new_session:
        return finish(
            result(
                True,
                f"{auto_advance_note}新开会话进入代码实现 /{skill}。",
                [
                    new_session(
                        skill,
                        auto_message(
                            message,
                            note=f"{auto_advance_note}{resume_note(route)}",
                        ),
                    )
                ],
            ),
            "new_session:auto_plan_to_code",
        )

    node_status = str(route.get("currentNodeStatus") or "")
    if node_status == "done":
        # 需求 1：节点完成 -> 新开会话跑下一节点（route.nextAction 已指向下一节点技能）。
        return finish(
            result(
                True,
                f"{checkpoint} 已完成，新开会话推进下一阶段 /{skill}。",
                [new_session(skill, auto_message(message, note=resume_note(route)))],
            ),
            "new_session:node_done",
        )

    # 需求 2：同一节点未完成，但上下文将满 -> 新开会话续跑同一技能。
    if ratio is not None and ratio >= threshold:
        return finish(
            result(
                True,
                f"上下文占用 {ratio:.0%} 已超阈值 {threshold:.0%}，新开会话续跑 /{skill}。",
                [new_session(skill, auto_message(message, note=resume_note(route)))],
            ),
            "new_session:context_threshold",
        )

    ratio_text = f"{ratio:.0%}" if ratio is not None else "未知"
    return finish(
        result(
            True,
            f"{checkpoint} 仍在进行中（上下文占用 {ratio_text}），同会话继续 /{skill}。",
            [continue_session(skill, auto_message(message))],
        ),
        "continue:node_in_progress",
    )


def _decide_needs_fix(
    route: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
    skill: str,
    message: str,
) -> tuple[dict[str, Any], str]:
    """needs_fix 分支：route 已把 allowedNextCheckpoints 收窄到 suggestedCheckpoint。"""
    errors = route.get("fixRequestErrors")
    errors = [item for item in errors if isinstance(item, str)] if isinstance(errors, list) else []
    fix_request = route.get("fixRequest")
    suggested = ""
    if isinstance(fix_request, dict):
        raw = fix_request.get("suggestedCheckpoint")
        suggested = raw.strip() if isinstance(raw, str) else ""

    if errors or not suggested or not skill:
        detail = "；".join(errors) if errors else "FIX_REQUEST.json 缺少可用的 suggestedCheckpoint"
        body = (
            f"当前 Feature 处于 needs_fix，但无法自动确定回流阶段（{detail}），已停止自动推进。"
            "请检查 FIX_REQUEST.json 后手动继续。"
        )
        return (
            result(True, f"needs_fix 无法自动回流：{detail}", [pause(auto_message(body))]),
            "pause:needs_fix_unresolved",
        )

    max_reflows = int(config["maxFixReflows"])
    reflows = int(state.get("fixReflows", 0)) + 1
    if reflows > max_reflows:
        body = (
            f"当前 Feature 已自动回流修复 {reflows - 1} 次，达到上限（maxFixReflows={max_reflows}），"
            "停止自动推进。请人工介入判断根因，确认后手动发送本条消息继续，"
            "之后会重新开始计数。"
        )
        return (
            result(True, "needs_fix 回流已达上限。", [pause(auto_message(body), skill)]),
            "pause:fix_reflow_exhausted",
        )
    state["fixReflows"] = reflows

    body = (
        f"上一阶段判定失败，需要回流到 {suggested} 修复。请先读取 FIX_REQUEST.json "
        f"了解失败项与修复建议，再执行修复。{message}"
    )
    return (
        result(
            True,
            f"needs_fix 自动回流到 {suggested}（第 {reflows} 次），新开会话执行 /{skill}。",
            [new_session(skill, auto_message(body, note=resume_note(route)))],
        ),
        "new_session:needs_fix_reflow",
    )


def _decide_workflow_choice(route: dict[str, Any], skill: str) -> tuple[dict[str, Any], str]:
    """动态阶段（如 detail_design_before_code）必须由人决定，托管模式不代拍。"""
    choices = route.get("workflowChoices")
    choices = [item for item in choices if isinstance(item, dict)] if isinstance(choices, list) else []
    lines: list[str] = []
    stage_label = ""
    for choice in choices:
        stage_label = stage_label or str(choice.get("stageLabel") or choice.get("stageId") or "")
        stage_id = choice.get("stageId") or ""
        decision = choice.get("decision") or ""
        label = choice.get("label") or ""
        target = choice.get("targetCheckpoint") or ""
        lines.append(f"- {label}：--workflow-decision {stage_id}={decision}（进入 {target}）")

    stage_text = f"「{stage_label}」" if stage_label else ""
    body = (
        f"当前流程到了可选阶段{stage_text}，需要你决定是否启用，托管模式不代替你做该决策。"
        f"{checkpoint_note(route)}"
    )
    if lines:
        body += "\n可选项：\n" + "\n".join(lines)
    body += "\n确认后手动发送本条消息，由技能写入 workflowDecisions 后继续。"
    return (
        result(True, f"等待用户选择动态阶段{stage_text}。", [pause(auto_message(body), skill)]),
        "pause:workflow_choice",
    )


# --------------------------------------------------------------------------- #
# CLI 入口
# --------------------------------------------------------------------------- #


def emit(payload: dict[str, Any]) -> int:
    """把最终 JSON 写到 stdout 并返回 0——平台只认 stdout 上的 JSON。"""
    json.dump(payload, sys.stdout, ensure_ascii=False)
    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="托管模式决策：把 Feature 当前状态编译成平台可执行的 action 数组",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--plugin-workspace",
        required=True,
        help="项目集合工作区路径（对应 PLUGIN_WORKSPACE 环境变量）",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="项目目录名（对应 PROJECT_DIR 环境变量）",
    )
    parser.add_argument(
        "--feature",
        "-f",
        required=True,
        help="Feature slug（对应 FEATURE_ID 环境变量）",
    )
    parser.add_argument(
        "--event-json",
        required=True,
        dest="event_json",
        help="平台传入的 AgentTurnEndEvent JSON 字符串",
    )
    return parser


def _run(argv: list[str]) -> int:
    config = auto_mode_config()

    # 这是平台内部 JSON hook，不提供人类 CLI help，以保持 stdout 的纯 JSON 契约。
    if "-h" in argv or "--help" in argv:
        return emit(result(False, "auto_next_step 是平台内部 hook，不支持 --help。"))

    # 参数解析失败也必须变成 stdout JSON + exit 0：argparse 默认走 SystemExit(2)，
    # 那会让 execFileSync 抛异常，平台把它当成 hook 调用失败。
    try:
        args = build_parser().parse_args(argv)
    except SystemExit:
        return emit(result(False, "auto_next_step 参数无效，请检查 board_config.json 中的命令配置。"))

    # 解析事件。
    try:
        event = parse_event(args.event_json)
    except EventError as exc:
        return emit(result(False, f"event-json 无效：{exc}"))

    event_id = str(event.get("eventId") or "")

    # 定位工作区。
    try:
        workspace = get_plugin_output_workspace(
            env={
                "PLUGIN_WORKSPACE": args.plugin_workspace,
                "PROJECT_DIR": args.project,
            }
        )
    except ValueError as exc:
        return emit(result(False, f"工作区定位失败：{exc}"))

    target_feature_dir = feature_dir(workspace, args.feature)

    # 「读状态 → 判重 → 决策 → 写回」全程持锁，避免并发调用各自读到旧状态。
    with run_state_transaction(target_feature_dir):
        run_state = load_run_state(target_feature_dir)

        # 重复事件防护（幂等）：比对有界的已处理集合，不只看紧邻的上一条。
        if is_duplicate_event(run_state, event_id):
            log(f"重复 eventId={event_id}，跳过。")
            return emit(result(True, f"重复事件 {event_id}，已忽略。"))

        # 解析 Feature 当前状态。
        route_payload, _exit_code = resolve_route(workspace, args.feature)
        if not route_payload.get("ok"):
            errors = route_payload.get("errors") or []
            errors_text = "；".join(str(e) for e in errors) if errors else "未知错误"
            # 记账：即使路由失败也记下 eventId，防止同一事件重投时重复执行。
            remember_event(run_state, event_id)
            write_run_state(target_feature_dir, run_state)
            return emit(result(False, f"Feature 状态读取失败：{errors_text}"))

        auto_advance_note = ""
        if event.get("outcome") == "success" and config["autoEnterCodeAfterPlan"]:
            auto_advance_note = auto_advance_plan_done_to_code(
                workspace=workspace,
                feature=args.feature,
                route=route_payload,
            ) or ""
            if auto_advance_note:
                route_payload, _exit_code = resolve_route(workspace, args.feature)
                if not route_payload.get("ok"):
                    errors = route_payload.get("errors") or []
                    errors_text = "；".join(str(e) for e in errors) if errors else "未知错误"
                    remember_event(run_state, event_id)
                    write_run_state(target_feature_dir, run_state)
                    return emit(result(False, f"自动进入 code 后状态读取失败：{errors_text}"))

        # 只有平台传入来源业务 workspace 且 Git 可观测时才启用空转熔断。
        raw_workspace = event.get("sessionWorkspacePath")
        session_workspace_path = raw_workspace.strip() if isinstance(raw_workspace, str) else None
        fingerprint = observed_progress_fingerprint(target_feature_dir, session_workspace_path)

        try:
            payload, new_state = decide(
                event=event,
                route=route_payload,
                run_state=run_state,
                fingerprint=fingerprint,
                config=config,
                force_new_session=bool(auto_advance_note),
                auto_advance_note=auto_advance_note,
            )
        except Exception as exc:
            # 防御性：决策函数崩溃不得向平台抛异常（否则 execFileSync 崩溃，控制面收不到任何响应）。
            log(f"decide() 意外异常: {exc}")
            import traceback

            traceback.print_exc(file=sys.stderr)
            payload = result(False, f"插件决策异常：{exc}")
            new_state = dict(run_state)
            remember_event(new_state, event_id)

        write_run_state(target_feature_dir, new_state)

    log(
        f"event={event_id} checkpoint={route_payload.get('checkpoint')} "
        f"outcome={event.get('outcome')} -> "
        f"actions={[a.get('actionType') for a in payload.get('action', [])]}"
    )
    return emit(payload)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口；永远 exit 0，平台用 execFileSync 调用。"""
    resolved = list(sys.argv[1:] if argv is None else argv)
    try:
        return _run(resolved)
    except Exception as exc:  # 兜底：任何未预期异常都要变成 JSON，而不是非零退出
        log(f"未预期异常: {exc}")
        import traceback

        traceback.print_exc(file=sys.stderr)
        return emit(result(False, f"auto_next_step 未预期异常：{exc}"))


if __name__ == "__main__":
    raise SystemExit(main())
