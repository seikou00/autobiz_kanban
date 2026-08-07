"""tests/test_auto_next_step.py — auto_next_step 决策逻辑单元测试。

测试策略：直接调用 hooks/auto_next_step 中的 decide() / main()，
不走平台 execFileSync，保持测试轻量且无副作用。
复用 test_workflow_skip.make_workspace / seed_feature 搭建测试工作区。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.auto_next_step import (  # noqa: E402
    EMPTY_RUN_STATE,
    DEFAULT_AUTO_MODE_CONFIG,
    auto_mode_state_path,
    decide,
    feature_dir,
    load_run_state,
    main,
    observed_progress_fingerprint,
    parse_event,
    progress_fingerprint,
    result,
    run_state_transaction,
)
from board_core.state_store import load_state_json_records_result, write_state_records  # noqa: E402
from tests.test_dynamic_workflow import record as dynamic_record, write_plan_artifacts  # noqa: E402
from tests.test_workflow_skip import make_workspace, seed_feature  # noqa: E402


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _event(
    outcome: str = "success",
    code: str = "normal",
    *,
    event_id: str = "evt-001",
    thread_id: str = "thr-001",
    input_tokens: int | None = None,
    max_tokens: int = 200_000,
    session_workspace_path: str | None = None,
) -> dict:
    e: dict = {
        "eventId": event_id,
        "eventType": "agent_turn_end",
        "eventTime": "2026-07-31 10:00:00",
        "threadId": thread_id,
        "outcome": outcome,
        "endReason": {"code": code},
    }
    if input_tokens is not None:
        e["contextUsage"] = {"inputTokens": input_tokens, "maxTokens": max_tokens}
    if session_workspace_path is not None:
        e["sessionWorkspacePath"] = session_workspace_path
    return e


def _route(
    checkpoint: str = "code_in_progress",
    node_status: str = "in_progress",
    *,
    node_id: str = "dev.code",
    skill: str = "autodev-code",
    message: str = "请使用 /autodev-code 继续推进当前 Feature。",
    requires_workflow_choice: bool = False,
    requires_profile_choice: bool = False,
    fix_request: dict | None = None,
    fix_errors: list | None = None,
) -> dict:
    return {
        "ok": True,
        "feature": "alpha",
        "checkpoint": checkpoint,
        "currentNodeId": node_id,
        "currentNodeStatus": node_status,
        "nextAction": {"slashSkill": skill, "userMessage": message},
        "recommendedNextSkill": skill,
        "requiresWorkflowChoice": requires_workflow_choice,
        "requiresProfileChoice": requires_profile_choice,
        "workflowChoices": [],
        "profileChoices": [],
        "allowedNextCheckpoints": [],
        "fixRequest": fix_request,
        "fixRequestErrors": fix_errors or [],
    }


def _decide(
    *,
    event: dict | None = None,
    route: dict | None = None,
    run_state: dict | None = None,
    fingerprint: str | None = "fp-001",
    config: dict | None = None,
) -> tuple[dict, dict]:
    return decide(
        event=event or _event(),
        route=route or _route(),
        run_state=run_state or dict(EMPTY_RUN_STATE, history=[]),
        fingerprint=fingerprint,
        config=config or dict(DEFAULT_AUTO_MODE_CONFIG),
    )


def _action_types(payload: dict) -> list[str]:
    return [a.get("actionType") for a in payload.get("action", [])]


def _first_action(payload: dict) -> dict:
    return payload["action"][0]


# ---------------------------------------------------------------------------
# parse_event
# ---------------------------------------------------------------------------


class ParseEventTest(unittest.TestCase):
    def test_valid_success_event(self) -> None:
        e = parse_event(json.dumps(_event()))
        self.assertEqual(e["eventType"], "agent_turn_end")
        self.assertEqual(e["outcome"], "success")

    def test_valid_error_event(self) -> None:
        e = parse_event(json.dumps(_event("error", "provider_error")))
        self.assertEqual(e["outcome"], "error")

    def test_empty_raises(self) -> None:
        from hooks.auto_next_step import EventError
        with self.assertRaises(EventError):
            parse_event("")

    def test_bad_json_raises(self) -> None:
        from hooks.auto_next_step import EventError
        with self.assertRaises(EventError):
            parse_event("{bad")

    def test_unknown_outcome_raises(self) -> None:
        from hooks.auto_next_step import EventError
        data = _event()
        data["outcome"] = "cancelled"
        with self.assertRaises(EventError):
            parse_event(json.dumps(data))


# ---------------------------------------------------------------------------
# decide — 正常推进
# ---------------------------------------------------------------------------


class DecideNormalProgressTest(unittest.TestCase):
    def test_node_in_progress_same_session(self) -> None:
        payload, state = _decide()
        self.assertTrue(payload["ok"])
        self.assertEqual(_action_types(payload), ["continue_current_session"])
        a = _first_action(payload)
        self.assertTrue(a["nextAction"]["autoSend"])

    def test_node_done_creates_new_session(self) -> None:
        payload, state = _decide(route=_route("code_done", "done"))
        self.assertTrue(payload["ok"])
        self.assertEqual(_action_types(payload), ["create_new_session"])
        a = _first_action(payload)
        self.assertTrue(a["nextAction"]["autoSend"])

    def test_archived_returns_complete(self) -> None:
        payload, state = _decide(route=_route("archived", "archived"))
        self.assertEqual(_action_types(payload), ["complete"])

    def test_context_over_threshold_creates_new_session(self) -> None:
        # 91% 触发新会话（需求 2）
        payload, state = _decide(
            event=_event(input_tokens=182_000, max_tokens=200_000),
            route=_route("code_in_progress", "in_progress"),
        )
        self.assertEqual(_action_types(payload), ["create_new_session"])
        a = _first_action(payload)
        self.assertTrue(a["nextAction"]["autoSend"])
        # 新会话消息包含续跑提示
        msg = a["nextAction"]["userMessage"]
        self.assertIn("托管模式", msg)

    def test_context_below_threshold_stays_in_session(self) -> None:
        # 89% 不新开
        payload, _ = _decide(
            event=_event(input_tokens=178_000, max_tokens=200_000),
        )
        self.assertEqual(_action_types(payload), ["continue_current_session"])

    def test_missing_context_usage_stays_in_session(self) -> None:
        # 平台说拿不到 contextUsage 时按不超阈值处理
        payload, _ = _decide(event=_event())
        self.assertEqual(_action_types(payload), ["continue_current_session"])


# ---------------------------------------------------------------------------
# decide — 异常续跑（需求 3）
# ---------------------------------------------------------------------------


class DecideErrorRetryTest(unittest.TestCase):
    def test_first_error_retries_same_session(self) -> None:
        payload, state = _decide(event=_event("error", "provider_error"))
        self.assertTrue(payload["ok"])
        self.assertEqual(_action_types(payload), ["continue_current_session"])
        self.assertTrue(_first_action(payload)["nextAction"]["autoSend"])

    def test_error_high_context_retries_new_session(self) -> None:
        payload, _ = _decide(
            event=_event("error", "provider_error", input_tokens=185_000, max_tokens=200_000),
        )
        self.assertEqual(_action_types(payload), ["create_new_session"])

    def test_error_streak_exceeds_limit_pauses(self) -> None:
        # 已重试 2 次 (maxErrorRetries=2)，第 3 次应暂停。
        # code 必须是可重试类型：hook_halt / failure_fuse 会先被「有意停止」分支拦下，
        # 走不到重试计数这一层。
        run_state = dict(EMPTY_RUN_STATE, history=[], errorStreak=2, errorCheckpoint="code_in_progress")
        payload, _ = _decide(
            event=_event("error", "provider_error"),
            run_state=run_state,
        )
        self.assertEqual(_action_types(payload), ["continue_current_session"])
        a = _first_action(payload)
        self.assertFalse(a["nextAction"]["autoSend"])  # 暂停填草稿
        self.assertIn("上限", payload["messages"])

    def test_error_streak_resets_on_different_checkpoint(self) -> None:
        # checkpoint 切换了，errorStreak 重置
        run_state = dict(EMPTY_RUN_STATE, history=[], errorStreak=2, errorCheckpoint="prd_in_progress")
        payload, state = _decide(
            event=_event("error", "provider_error"),
            route=_route("code_in_progress", "in_progress"),
            run_state=run_state,
        )
        self.assertEqual(state["errorStreak"], 1)  # 从 1 开始，不继承跨 checkpoint 的 streak
        self.assertEqual(_action_types(payload), ["continue_current_session"])
        self.assertTrue(_first_action(payload)["nextAction"]["autoSend"])


# ---------------------------------------------------------------------------
# decide — 系统有意停止（平台 endReason.code 新增 hook_halt / failure_fuse）
# ---------------------------------------------------------------------------


class DecideDeliberateHaltTest(unittest.TestCase):
    """hook_halt / failure_fuse 是有意终止，重试无效，必须首轮就暂停。"""

    def test_hook_halt_pauses_on_first_occurrence(self) -> None:
        payload, _ = _decide(event=_event("error", "hook_halt"))
        self.assertTrue(payload["ok"])
        # 不复用重试额度：第一次就暂停，而不是 continue+autoSend 重试。
        self.assertEqual(_action_types(payload), ["continue_current_session"])
        self.assertFalse(_first_action(payload)["nextAction"]["autoSend"])
        self.assertIn("hook_halt", payload["messages"])

    def test_failure_fuse_pauses_on_first_occurrence(self) -> None:
        payload, _ = _decide(event=_event("error", "failure_fuse"))
        self.assertEqual(_action_types(payload), ["continue_current_session"])
        self.assertFalse(_first_action(payload)["nextAction"]["autoSend"])
        self.assertIn("failure_fuse", payload["messages"])

    def test_halt_pauses_even_when_outcome_is_success(self) -> None:
        """平台把 outcome 与 endReason 设计成独立字段，不能只在 error 分支里判 code。"""
        payload, _ = _decide(event=_event("success", "hook_halt"))
        self.assertEqual(_action_types(payload), ["continue_current_session"])
        self.assertFalse(_first_action(payload)["nextAction"]["autoSend"])

    def test_halt_pause_marks_awaiting_human(self) -> None:
        payload, state = _decide(event=_event("error", "failure_fuse", thread_id="thr-halt"))
        self.assertEqual(state["awaitingHumanThreadId"], "thr-halt")

    def test_halt_records_code_in_history_decision(self) -> None:
        _, state = _decide(event=_event("error", "hook_halt"))
        self.assertEqual(state["history"][-1]["decision"], "pause:deliberate_halt:hook_halt")

    def test_retryable_codes_still_retry(self) -> None:
        """回归：provider_error / unknown 不能被新分支误伤。"""
        for code in ("provider_error", "unknown"):
            with self.subTest(code=code):
                payload, _ = _decide(event=_event("error", code))
                self.assertEqual(_action_types(payload), ["continue_current_session"])
                self.assertTrue(_first_action(payload)["nextAction"]["autoSend"])

    def test_normal_success_unaffected(self) -> None:
        payload, _ = _decide(event=_event("success", "normal"))
        self.assertTrue(_first_action(payload)["nextAction"]["autoSend"])

    def test_archived_wins_over_halt(self) -> None:
        """归档是终态，不该被有意停止分支改写成暂停。"""
        payload, _ = _decide(
            event=_event("error", "hook_halt"),
            route=_route("archived", "done", node_id="archived"),
        )
        self.assertEqual(_action_types(payload), ["complete"])

    def test_halt_does_not_increment_total_steps(self) -> None:
        """hook_halt 本质是门禁判定写入不合法，本轮没有实际推进，不应虚增步数。"""
        _, state = _decide(event=_event("error", "hook_halt"), run_state=dict(EMPTY_RUN_STATE, totalSteps=5))
        self.assertEqual(state["totalSteps"], 5)

    def test_halt_does_not_increment_error_streak(self) -> None:
        """有意停止不消耗重试配额，不应递增 errorStreak。"""
        _, state = _decide(event=_event("error", "hook_halt"), run_state=dict(EMPTY_RUN_STATE, errorStreak=1))
        self.assertEqual(state["errorStreak"], 1)

    def test_halt_does_not_increment_stalled_steps(self) -> None:
        """有意停止不应递增停滞计数，即使指纹未变。"""
        _, state = _decide(
            event=_event("error", "hook_halt"),
            run_state=dict(
                EMPTY_RUN_STATE,
                stalledSteps=1,
                lastCheckpoint="code_in_progress",
                lastFingerprint="abc123",
            ),
            fingerprint="abc123",
        )
        self.assertEqual(state["stalledSteps"], 1)

    def test_halt_wins_over_stalled_detection(self) -> None:
        """P2 回归：stalledSteps=2 + hook_halt + 指纹未变，不能被空转熔断掩盖门禁原因。"""
        payload, state = _decide(
            event=_event("error", "hook_halt"),
            run_state=dict(
                EMPTY_RUN_STATE,
                stalledSteps=2,
                lastCheckpoint="code_in_progress",
                lastFingerprint="abc123",
            ),
            fingerprint="abc123",
        )
        self.assertEqual(_action_types(payload), ["continue_current_session"])
        self.assertFalse(_first_action(payload)["nextAction"]["autoSend"])
        self.assertIn("hook_halt", payload["messages"])
        # 确认不是空转提示
        self.assertNotIn("空转", payload["messages"])
        self.assertNotIn("stalled", state["history"][-1]["decision"])
        # 确认 stalledSteps 没有递增到 3
        self.assertEqual(state["stalledSteps"], 2)

    def test_halt_wins_over_max_total_steps(self) -> None:
        """有意停止应在 totalSteps 递增前拦住，不会因为刚好到上限而被掩盖。"""
        payload, state = _decide(
            event=_event("error", "failure_fuse"),
            run_state=dict(EMPTY_RUN_STATE, totalSteps=10),
            config=dict(DEFAULT_AUTO_MODE_CONFIG, maxTotalSteps=10),
        )
        self.assertEqual(_action_types(payload), ["continue_current_session"])
        self.assertFalse(_first_action(payload)["nextAction"]["autoSend"])
        self.assertIn("failure_fuse", payload["messages"])
        self.assertNotIn("maxTotalSteps", payload["messages"])
        self.assertEqual(state["totalSteps"], 10)


# ---------------------------------------------------------------------------
# decide — needs_fix 回流
# ---------------------------------------------------------------------------


class DecideNeedsFixTest(unittest.TestCase):
    def _needs_fix_route(self, **kwargs) -> dict:
        return _route(
            "needs_fix", "blocked",
            node_id="needs_fix",
            skill="autodev-code",
            fix_request={"suggestedCheckpoint": "code_in_progress"},
            **kwargs,
        )

    def test_auto_reflows_to_suggested_checkpoint(self) -> None:
        payload, state = _decide(route=self._needs_fix_route())
        self.assertTrue(payload["ok"])
        self.assertEqual(_action_types(payload), ["create_new_session"])
        self.assertIn("回流", payload["messages"])
        self.assertEqual(state["fixReflows"], 1)

    def test_exceeds_max_reflows_pauses(self) -> None:
        run_state = dict(EMPTY_RUN_STATE, history=[], fixReflows=3)  # maxFixReflows=3
        payload, _ = _decide(route=self._needs_fix_route(), run_state=run_state)
        a = _first_action(payload)
        self.assertFalse(a["nextAction"]["autoSend"])
        self.assertIn("上限", payload["messages"])

    def test_missing_fix_request_pauses(self) -> None:
        route = self._needs_fix_route()
        route["fixRequest"] = None
        route["fixRequestErrors"] = ["FIX_REQUEST.json 未找到"]
        payload, _ = _decide(route=route)
        a = _first_action(payload)
        self.assertFalse(a["nextAction"]["autoSend"])
        self.assertIn("无法自动回流", payload["messages"])


# ---------------------------------------------------------------------------
# decide — 动态阶段 / profile 选择
# ---------------------------------------------------------------------------


class DecideChoiceTest(unittest.TestCase):
    def test_workflow_choice_pauses_with_draft(self) -> None:
        route = _route("plan_done", "done", requires_workflow_choice=True)
        route["workflowChoices"] = [
            {
                "stageId": "detail_design_before_code",
                "stageLabel": "详细设计",
                "decision": "enabled",
                "label": "需要，生成详细设计",
                "targetCheckpoint": "detail_design_in_progress",
            },
            {
                "stageId": "detail_design_before_code",
                "stageLabel": "详细设计",
                "decision": "skipped",
                "label": "不需要，直接编码",
                "targetCheckpoint": "code_in_progress",
            },
        ]
        payload, _ = _decide(route=route)
        self.assertEqual(_action_types(payload), ["continue_current_session"])
        a = _first_action(payload)
        self.assertFalse(a["nextAction"]["autoSend"])
        msg = a["nextAction"]["userMessage"]
        self.assertIn("detail_design_before_code", msg)

    def test_profile_choice_pauses(self) -> None:
        route = _route("prd_done", "done", requires_profile_choice=True)
        payload, _ = _decide(route=route)
        a = _first_action(payload)
        self.assertFalse(a["nextAction"]["autoSend"])


# ---------------------------------------------------------------------------
# decide — 守卫熔断
# ---------------------------------------------------------------------------


class DecideGuardTest(unittest.TestCase):
    def test_total_steps_limit_pauses(self) -> None:
        config = dict(DEFAULT_AUTO_MODE_CONFIG, maxTotalSteps=5)
        run_state = dict(EMPTY_RUN_STATE, history=[], totalSteps=5)
        payload, _ = _decide(run_state=run_state, config=config)
        a = _first_action(payload)
        self.assertFalse(a["nextAction"]["autoSend"])
        self.assertIn("总步数上限", payload["messages"])

    def test_stalled_guard_pauses_after_threshold(self) -> None:
        # 连续 3 次同 checkpoint 同指纹 -> 空转熔断
        config = dict(DEFAULT_AUTO_MODE_CONFIG, maxStalledSteps=3)
        run_state = dict(
            EMPTY_RUN_STATE,
            history=[],
            stalledSteps=2,
            lastCheckpoint="code_in_progress",
            lastFingerprint="same-fp",
        )
        payload, state = _decide(
            run_state=run_state,
            fingerprint="same-fp",
            config=config,
        )
        a = _first_action(payload)
        self.assertFalse(a["nextAction"]["autoSend"])
        self.assertIn("空转", payload["messages"])

    def test_stalled_resets_when_fingerprint_changes(self) -> None:
        run_state = dict(
            EMPTY_RUN_STATE,
            history=[],
            stalledSteps=2,
            lastCheckpoint="code_in_progress",
            lastFingerprint="old-fp",
        )
        payload, state = _decide(
            run_state=run_state,
            fingerprint="new-fp",  # 产物有变
        )
        self.assertEqual(state["stalledSteps"], 0)
        self.assertEqual(_action_types(payload), ["continue_current_session"])

    def test_pause_marks_awaiting_human(self) -> None:
        config = dict(DEFAULT_AUTO_MODE_CONFIG, maxTotalSteps=5)
        run_state = dict(EMPTY_RUN_STATE, history=[], processedEventIds=[], totalSteps=5)
        payload, state = _decide(run_state=run_state, config=config)
        self.assertFalse(_first_action(payload)["nextAction"]["autoSend"])
        self.assertEqual(state["awaitingHumanThreadId"], "thr-001")

    def test_auto_send_action_does_not_mark_awaiting_human(self) -> None:
        payload, state = _decide()
        self.assertTrue(_first_action(payload)["nextAction"]["autoSend"])
        self.assertEqual(state["awaitingHumanThreadId"], "")

    def test_human_resume_resets_total_steps_budget(self) -> None:
        """熔断后人工发送草稿 -> 下一轮必须恢复自动推进，而不是再次暂停。"""
        config = dict(DEFAULT_AUTO_MODE_CONFIG, maxTotalSteps=5)
        # 上一轮已熔断并留下 awaitingHuman 标记
        run_state = dict(
            EMPTY_RUN_STATE,
            history=[],
            processedEventIds=[],
            totalSteps=5,
            awaitingHumanThreadId="thr-001",
        )
        payload, state = _decide(
            event=_event(event_id="after-human-send"),
            run_state=run_state,
            config=config,
        )
        # 预算已重置，本轮重新计为第 1 步，正常自动推进
        self.assertEqual(state["totalSteps"], 1)
        self.assertEqual(state["awaitingHumanThreadId"], "")
        self.assertTrue(_first_action(payload)["nextAction"]["autoSend"])

    def test_human_resume_resets_error_and_stall_counters(self) -> None:
        run_state = dict(
            EMPTY_RUN_STATE,
            history=[],
            processedEventIds=[],
            errorStreak=2,
            errorCheckpoint="code_in_progress",
            stalledSteps=3,
            fixReflows=3,
            awaitingHumanThreadId="thr-001",
        )
        payload, state = _decide(run_state=run_state)
        self.assertEqual(state["stalledSteps"], 0)
        self.assertEqual(state["fixReflows"], 0)
        self.assertEqual(state["errorStreak"], 0)
        self.assertTrue(_first_action(payload)["nextAction"]["autoSend"])

    def test_other_thread_cannot_consume_human_resume(self) -> None:
        config = dict(DEFAULT_AUTO_MODE_CONFIG, maxTotalSteps=5)
        run_state = dict(
            EMPTY_RUN_STATE,
            history=[],
            processedEventIds=[],
            totalSteps=5,
            awaitingHumanThreadId="waiting-thread",
        )
        payload, state = _decide(
            event=_event(event_id="other-thread-event", thread_id="other-thread"),
            run_state=run_state,
            config=config,
        )
        self.assertEqual(_action_types(payload), ["complete"])
        self.assertEqual(state["totalSteps"], 5)
        self.assertEqual(state["awaitingHumanThreadId"], "waiting-thread")

    def test_stall_not_counted_without_observable_workspace_fingerprint(self) -> None:
        """不能观测业务 workspace 时不猜测空转，交给总步数熔断。"""
        run_state = dict(
            EMPTY_RUN_STATE,
            history=[],
            processedEventIds=[],
            stalledSteps=2,
            lastCheckpoint="code_in_progress",
            lastFingerprint="same-fp",
        )
        payload, state = _decide(
            run_state=run_state,
            fingerprint=None,
        )
        self.assertEqual(state["stalledSteps"], 0)
        self.assertTrue(_first_action(payload)["nextAction"]["autoSend"])

    def test_leaving_needs_fix_resets_reflow_budget(self) -> None:
        run_state = dict(
            EMPTY_RUN_STATE,
            history=[],
            processedEventIds=[],
            lastCheckpoint="needs_fix",
            fixReflows=3,
        )
        _, state = _decide(run_state=run_state)
        self.assertEqual(state["fixReflows"], 0)

    def test_duplicate_event_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            seed_feature(workspace, "code_in_progress")
            event_json = json.dumps(_event(event_id="dup-001"))
            locator = [
                "--plugin-workspace", str(root),
                "--project", "workspace",
                "--feature", "alpha",
                "--event-json", event_json,
            ]
            # 第一次调用
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main(locator)
            first = json.loads(buf.getvalue())
            # 第二次用同一 eventId
            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                main(locator)
            second = json.loads(buf2.getvalue())
            self.assertTrue(second["ok"])
            self.assertEqual(second["action"], [])  # 重复事件不执行动作

    def test_non_adjacent_replay_is_still_deduped(self) -> None:
        """A -> B -> 重放 A：只存 lastEventId 会漏掉，需要有界已处理集合。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            seed_feature(workspace, "code_in_progress")

            def call(event_id: str) -> dict:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    main([
                        "--plugin-workspace", str(root),
                        "--project", "workspace",
                        "--feature", "alpha",
                        "--event-json", json.dumps(_event(event_id=event_id)),
                    ])
                return json.loads(buf.getvalue())

            call("evt-A")
            call("evt-B")
            replay = call("evt-A")  # 重放最早那条

            self.assertTrue(replay["ok"])
            self.assertEqual(replay["action"], [])
            self.assertIn("重复事件", replay["messages"])

    def test_processed_event_ids_are_bounded(self) -> None:
        from hooks.auto_next_step import PROCESSED_EVENT_LIMIT, remember_event

        state = dict(EMPTY_RUN_STATE, processedEventIds=[], history=[])
        for i in range(PROCESSED_EVENT_LIMIT + 20):
            remember_event(state, f"evt-{i}")
        self.assertEqual(len(state["processedEventIds"]), PROCESSED_EVENT_LIMIT)
        self.assertEqual(state["lastEventId"], f"evt-{PROCESSED_EVENT_LIMIT + 19}")


# ---------------------------------------------------------------------------
# progress_fingerprint
# ---------------------------------------------------------------------------


class ProgressFingerprintTest(unittest.TestCase):
    def test_empty_dir_returns_empty_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(progress_fingerprint(Path(tmp)), "")

    def test_adding_file_changes_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            fp_before = progress_fingerprint(p)
            (p / "artifact.md").write_text("content", encoding="utf-8")
            fp_after = progress_fingerprint(p)
            self.assertNotEqual(fp_before, fp_after)

    def test_auto_mode_dir_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "artifact.md").write_text("content", encoding="utf-8")
            fp_with_artifact = progress_fingerprint(p)
            (p / ".auto-mode").mkdir()
            (p / ".auto-mode" / "state.json").write_text("{}", encoding="utf-8")
            fp_after_state = progress_fingerprint(p)
            self.assertEqual(fp_with_artifact, fp_after_state)

    def test_observed_fingerprint_tracks_dirty_workspace_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feature = root / "feature"
            workspace = root / "workspace"
            feature.mkdir()
            workspace.mkdir()
            source = workspace / "app.py"
            source.write_text("first", encoding="utf-8")
            git_result = mock.Mock(stdout=b"app.py\0")
            with mock.patch("hooks.auto_next_step.subprocess.run", return_value=git_result):
                before = observed_progress_fingerprint(feature, str(workspace))
                previous = source.stat()
                source.write_text("second", encoding="utf-8")
                os.utime(source, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1))
                after = observed_progress_fingerprint(feature, str(workspace))
            self.assertIsNotNone(before)
            self.assertNotEqual(before, after)

    def test_observed_fingerprint_tracks_committed_workspace_progress(self) -> None:
        """提交后工作区重新变干净时，HEAD 变化仍必须被视为进展。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feature = root / "feature"
            workspace = root / "workspace"
            feature.mkdir()
            workspace.mkdir()

            def git(*args: str) -> None:
                subprocess.run(
                    ["git", "-C", str(workspace), *args],
                    check=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            git("init")
            source = workspace / "app.py"
            source.write_text("first", encoding="utf-8")
            git("add", "app.py")
            git("-c", "user.name=Auto Mode Test", "-c", "user.email=auto-mode@example.test", "commit", "-m", "first")
            before = observed_progress_fingerprint(feature, str(workspace))

            source.write_text("second", encoding="utf-8")
            git("add", "app.py")
            git("-c", "user.name=Auto Mode Test", "-c", "user.email=auto-mode@example.test", "commit", "-m", "second")
            after = observed_progress_fingerprint(feature, str(workspace))

            self.assertIsNotNone(before)
            self.assertNotEqual(before, after)

    def test_observed_fingerprint_is_none_when_nothing_observable(self) -> None:
        """FEATURE_DIR 空 + 无 workspace：必须是 None，不能是空串。

        空串会让 "" == "" 把「观测不到」当成「确认没进展」，凭空触发空转熔断。
        """
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(observed_progress_fingerprint(Path(tmp), None))

    def test_observed_fingerprint_degrades_to_feature_dir_without_workspace(self) -> None:
        """平台当前分支不传 sessionWorkspacePath，此时降级为只用 FEATURE_DIR 指纹。

        若这里返回 None，stalledSteps 将永远停在 0，空转熔断实际失效。
        """
        with tempfile.TemporaryDirectory() as tmp:
            feature = Path(tmp)
            (feature / "PRD.md").write_text("content", encoding="utf-8")
            fingerprint = observed_progress_fingerprint(feature, None)
            self.assertIsNotNone(fingerprint)
            # 同样内容必须稳定，否则每轮都被算成「有进展」，熔断照样失效。
            self.assertEqual(fingerprint, observed_progress_fingerprint(feature, None))

    def test_observed_fingerprint_without_workspace_tracks_feature_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature = Path(tmp)
            (feature / "PRD.md").write_text("content", encoding="utf-8")
            before = observed_progress_fingerprint(feature, None)
            (feature / "DESIGN.md").write_text("more", encoding="utf-8")
            self.assertNotEqual(before, observed_progress_fingerprint(feature, None))

    def test_observed_fingerprint_degrades_when_workspace_not_a_git_repo(self) -> None:
        """给了 workspace 但 Git 观测不到时，也应降级而不是禁用熔断。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feature = root / "feature"
            workspace = root / "workspace"
            feature.mkdir()
            workspace.mkdir()
            (feature / "PRD.md").write_text("content", encoding="utf-8")
            with mock.patch(
                "hooks.auto_next_step.workspace_progress_fingerprint", return_value=None
            ):
                degraded = observed_progress_fingerprint(feature, str(workspace))
            self.assertIsNotNone(degraded)
            self.assertEqual(degraded, observed_progress_fingerprint(feature, None))


class RunStateTransactionTest(unittest.TestCase):
    def test_body_oserror_propagates_without_contextmanager_double_yield(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(OSError, "route read failed"):
                with run_state_transaction(Path(tmp)):
                    raise OSError("route read failed")


# ---------------------------------------------------------------------------
# main() — 始终 exit 0，stdout 是合法 JSON
# ---------------------------------------------------------------------------


class MainEntryTest(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, dict]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(argv)
        return code, json.loads(buf.getvalue())

    def test_always_exit_0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            seed_feature(workspace, "code_in_progress")
            code, _ = self._run([
                "--plugin-workspace", str(root),
                "--project", "workspace",
                "--feature", "alpha",
                "--event-json", json.dumps(_event()),
            ])
            self.assertEqual(code, 0)

    def test_stdout_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            seed_feature(workspace, "code_done")
            _, payload = self._run([
                "--plugin-workspace", str(root),
                "--project", "workspace",
                "--feature", "alpha",
                "--event-json", json.dumps(_event()),
            ])
            self.assertIn("ok", payload)
            self.assertIn("messages", payload)
            self.assertIn("action", payload)
            self.assertIsInstance(payload["action"], list)

    def test_bad_event_json_returns_ok_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            seed_feature(workspace, "code_in_progress")
            _, payload = self._run([
                "--plugin-workspace", str(root),
                "--project", "workspace",
                "--feature", "alpha",
                "--event-json", "NOT_JSON",
            ])
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["action"], [])

    def test_missing_required_arg_returns_ok_false_not_systemexit(self) -> None:
        """argparse 默认 SystemExit(2) 会被平台当成 hook 调用失败。"""
        buf = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = main(["--project", "workspace"])  # 缺 --plugin-workspace / --feature / --event-json
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["action"], [])

    def test_unknown_arg_returns_ok_false(self) -> None:
        buf = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = main([
                "--plugin-workspace", "/tmp",
                "--project", "workspace",
                "--feature", "alpha",
                "--event-json", "{}",
                "--nonexistent-flag", "x",
            ])
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["ok"])

    def test_help_returns_ok_false_json(self) -> None:
        code, payload = self._run(["--help"])
        self.assertEqual(code, 0)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["action"], [])

    def test_bad_workspace_returns_ok_false(self) -> None:
        _, payload = self._run([
            "--plugin-workspace", "/nonexistent/path",
            "--project", "project",
            "--feature", "feat",
            "--event-json", json.dumps(_event()),
        ])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["action"], [])

    def test_node_done_creates_new_session_via_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            seed_feature(workspace, "code_done")
            _, payload = self._run([
                "--plugin-workspace", str(root),
                "--project", "workspace",
                "--feature", "alpha",
                "--event-json", json.dumps(_event()),
            ])
            self.assertTrue(payload["ok"])
            actions = payload["action"]
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0]["actionType"], "create_new_session")
            self.assertTrue(actions[0]["nextAction"]["autoSend"])

    def test_plan_done_auto_enters_code_and_records_skip_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            target_feature_dir = feature_dir(workspace, "alpha")
            target_feature_dir.mkdir(parents=True, exist_ok=True)
            write_plan_artifacts(target_feature_dir)
            write_state_records(workspace, {"alpha": dynamic_record("plan_done", profile="standard")})

            _, payload = self._run([
                "--plugin-workspace", str(root),
                "--project", "workspace",
                "--feature", "alpha",
                "--event-json", json.dumps(_event(event_id="plan-to-code-001")),
            ])

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["action"][0]["actionType"], "create_new_session")
            self.assertEqual(payload["action"][0]["nextAction"]["slashSkill"], "autodev-code")
            self.assertTrue(payload["action"][0]["nextAction"]["autoSend"])
            records = load_state_json_records_result(workspace).records
            self.assertEqual(records["alpha"]["checkpoint"], "code_in_progress")
            self.assertEqual(
                records["alpha"]["workflowDecisions"],
                {"detail_design_before_code": "skipped"},
            )

    def test_plan_done_keeps_workflow_choice_when_auto_entry_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            target_feature_dir = feature_dir(workspace, "alpha")
            target_feature_dir.mkdir(parents=True, exist_ok=True)
            write_plan_artifacts(target_feature_dir)
            write_state_records(workspace, {"alpha": dynamic_record("plan_done", profile="standard")})
            config = dict(DEFAULT_AUTO_MODE_CONFIG, autoEnterCodeAfterPlan=False)

            with mock.patch("hooks.auto_next_step.auto_mode_config", return_value=config):
                _, payload = self._run([
                    "--plugin-workspace", str(root),
                    "--project", "workspace",
                    "--feature", "alpha",
                    "--event-json", json.dumps(_event(event_id="plan-choice-001")),
                ])

            action = payload["action"][0]
            self.assertEqual(action["actionType"], "continue_current_session")
            self.assertFalse(action["nextAction"]["autoSend"])
            records = load_state_json_records_result(workspace).records
            self.assertEqual(records["alpha"]["checkpoint"], "plan_done")
            self.assertEqual(records["alpha"].get("workflowDecisions", {}), {})

    def test_run_state_persisted_after_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            seed_feature(workspace, "code_in_progress")
            self._run([
                "--plugin-workspace", str(root),
                "--project", "workspace",
                "--feature", "alpha",
                "--event-json", json.dumps(_event(event_id="state-test-001")),
            ])
            state_path = auto_mode_state_path(feature_dir(workspace, "alpha"))
            self.assertTrue(state_path.is_file())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["totalSteps"], 1)
            self.assertEqual(state["lastEventId"], "state-test-001")

    def test_archived_returns_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            seed_feature(workspace, "archived")
            _, payload = self._run([
                "--plugin-workspace", str(root),
                "--project", "workspace",
                "--feature", "alpha",
                "--event-json", json.dumps(_event()),
            ])
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["action"], [{"actionType": "complete"}])


if __name__ == "__main__":
    unittest.main()
