"""dev.specs 的 critic 审查日志是可选原始记录，不是门禁。"""

from __future__ import print_function

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
AUTODEV_HOOKS = ROOT / "skills" / "autodev" / "hooks"
if str(AUTODEV_HOOKS) not in sys.path:
    sys.path.insert(0, str(AUTODEV_HOOKS))

from hooks.critic_review_log_writer import append_review, run_hook  # noqa: E402
from hooks.init_workspace import create_feature, init_workspace  # noqa: E402
from hooks.runtime_artifact_guard import protected_path  # noqa: E402
from hooks.stage_gate import validate_stage  # noqa: E402


PROPOSAL = """# Proposal: 导出

## Why

需要导出。

## What Changes

- 新增导出。

## Capabilities

### New Capabilities

- `order-export`: 导出

### Modified Capabilities

无

### Removed Capabilities

无

## Impact

- export

## Out of Scope

- 无

## Decision Log

无

## Open Questions

无
"""

SPEC = """# Order Export

## Source References / 外部资料引用

无

## ADDED Requirements

### Requirement [REQ-001]: 创建导出

The system SHALL 创建导出任务。

#### Scenario [SCN-001]: 创建成功

- **WHEN** 用户提交请求
- **THEN** 返回任务号

## MODIFIED Requirements

无

## REMOVED Requirements

无
"""

REVIEW = """# Specs Review

## Verdict

PASS

## Findings

无

## Unresolved

无
"""


class SpecsRuntimeAuditTest(unittest.TestCase):
    def _feature(self, tmp):
        workspace = Path(tmp) / "demo"
        workspace.mkdir()
        init_workspace(workspace)
        create_feature(workspace, "alpha")
        feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
        (feature_dir / "proposal.md").write_text(PROPOSAL, encoding="utf-8")
        spec = feature_dir / "specs" / "order-export" / "spec.md"
        spec.parent.mkdir(parents=True)
        spec.write_text(SPEC, encoding="utf-8")
        return workspace, feature_dir

    def _set_checkpoint(self, workspace, checkpoint):
        state = workspace / ".autobizdevops" / "state.json"
        data = json.loads(state.read_text(encoding="utf-8"))
        data["features"]["alpha"]["checkpoint"] = checkpoint
        state.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _run_hook(self, workspace, payload):
        env = dict(os.environ)
        env.update(
            {
                "PLUGIN_WORKSPACE": str(workspace.parent),
                "PROJECT_DIR": workspace.name,
                "FEATURE_ID": "alpha",
            }
        )
        return subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "critic_review_log_writer.py")],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
        )

    @staticmethod
    def _critic_payload(response, tool_use_id="tool-critic-1"):
        payload = {
            "tool_name": "task",
            "tool_input": {
                "subagent_type": "critic-autodev",
                "description": "review specs",
            },
            "tool_response": response,
        }
        if tool_use_id is not None:
            payload["tool_use_id"] = tool_use_id
        return payload

    def test_final_gate_needs_no_audit_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = self._feature(tmp)
            (feature_dir / "SPECS_REVIEW.md").write_text(REVIEW, encoding="utf-8")
            result = validate_stage(
                workspace=workspace, feature="alpha", stage="dev.specs", phase="final"
            )
            self.assertTrue(result.ok, result.errors)

    def test_editing_artifacts_after_review_does_not_reopen_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = self._feature(tmp)
            (feature_dir / "SPECS_REVIEW.md").write_text(REVIEW, encoding="utf-8")
            spec = feature_dir / "specs" / "order-export" / "spec.md"
            spec.write_text(
                SPEC.replace("创建导出任务", "创建异步导出任务"), encoding="utf-8"
            )
            result = validate_stage(
                workspace=workspace, feature="alpha", stage="dev.specs", phase="final"
            )
            self.assertTrue(result.ok, result.errors)

    def test_archives_the_raw_response_without_a_model_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, feature_dir = self._feature(tmp)
            response = {"output": "Critical: 分类证据不足\n普通文本即可。"}
            path, changed = append_review(
                feature_dir,
                self._critic_payload(response),
                response,
                now=datetime(2026, 9, 2, 12, 0, 0),
            )
            self.assertTrue(changed)
            record = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["recordType"], "critic_review")
            self.assertTrue(record["reviewRunId"].startswith("RV-"))
            self.assertEqual(record["timestamp"], "2026-09-02T12:00:00Z")
            self.assertEqual(record["response"], response)

    def test_tool_use_id_makes_replayed_events_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, feature_dir = self._feature(tmp)
            response = {"output": "review"}
            payload = self._critic_payload(response)
            path, first = append_review(feature_dir, payload, response)
            _, second = append_review(feature_dir, payload, response)
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)

    def test_fallback_id_includes_the_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, feature_dir = self._feature(tmp)
            first_response = {"output": "first"}
            second_response = {"output": "second"}
            first = self._critic_payload(first_response, tool_use_id=None)
            second = self._critic_payload(second_response, tool_use_id=None)
            path, _ = append_review(feature_dir, first, first_response)
            append_review(feature_dir, second, second_response)
            records = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(records), 2)
            self.assertNotEqual(records[0]["reviewRunId"], records[1]["reviewRunId"])

    def test_plain_critic_output_is_recorded_and_never_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = self._feature(tmp)
            self._set_checkpoint(workspace, "specs_in_progress")
            result = self._run_hook(
                workspace,
                self._critic_payload({"output": "没有 fenced block 的普通审查结论"}),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("CRITIC_REVIEW_RECORDED", result.stdout)
            self.assertTrue((feature_dir / ".runtime" / "CRITIC_REVIEWS.jsonl").is_file())

    def test_missing_response_and_non_target_tasks_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, feature_dir = self._feature(tmp)
            missing = self._critic_payload({}, tool_use_id="missing")
            missing.pop("tool_response")
            self.assertIsNone(run_hook(missing, feature_dir=feature_dir))
            other = self._critic_payload({"output": "other"})
            other["tool_input"]["subagent_type"] = "explore-autodev"
            self.assertIsNone(run_hook(other, feature_dir=feature_dir))

    def test_runtime_guard_protects_only_the_surviving_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, feature_dir = self._feature(tmp)
            self.assertTrue(
                protected_path(str(feature_dir / ".runtime" / "CRITIC_REVIEWS.jsonl"))
            )
            self.assertFalse(
                protected_path(str(feature_dir / ".runtime" / "DECISIONS.jsonl"))
            )
            self.assertFalse(
                protected_path(str(feature_dir / ".runtime" / "REVIEW_FINDINGS.jsonl"))
            )

    def test_only_the_raw_critic_log_hook_remains(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        pre_commands = [
            hook.get("command", "")
            for registration in hooks["PreToolUse"]
            for hook in registration.get("hooks", [])
        ]
        post_hooks = [
            hook
            for registration in hooks["PostToolUse"]
            for hook in registration.get("hooks", [])
        ]
        commands = [hook.get("command", "") for hook in post_hooks]
        self.assertNotIn("python hooks/augment_critic_task_prompt.py", pre_commands)
        self.assertIn("python hooks/critic_review_log_writer.py", commands)
        self.assertNotIn("python hooks/critic_findings_writer.py", commands)
        self.assertNotIn("python hooks/decision_ledger_writer.py", commands)
        entry = next(
            hook
            for hook in post_hooks
            if hook.get("command", "").endswith("critic_review_log_writer.py")
        )
        self.assertNotIn("onBlock", entry)

    def test_demoted_validators_stay_off_the_dev_specs_gate(self):
        board = json.loads(
            (ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8")
        )
        specs = next(node for node in board["workflow"]["nodes"] if node["id"] == "dev.specs")
        for validator in (
            "specs_review_finding_ledger",
            "specs_decision_ledger",
            "specs_review_freshness",
        ):
            self.assertNotIn(validator, specs["validators"])


if __name__ == "__main__":
    unittest.main()
