#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "hooks" / "augment_test_engineer_task_prompt.py"
SPEC = importlib.util.spec_from_file_location("augment_test_engineer_task_prompt", str(MODULE_PATH))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

from hooks.utest_assignment_router import build_assignments  # noqa: E402


def payload(description, subagent_type="test-engineer-autodev"):
    return {
        "tool_name": "task",
        "tool_input": {
            "description": description,
            "subagent_type": subagent_type,
        },
    }


SOURCE_TASK = {
    "id": "T001",
    "title": "实现用户登录",
    "goal": "实现用户登录功能",
    "implementationPoints": ["创建登录表单", "实现认证逻辑"],
    "nonGoals": ["不实现注册功能"],
    "validationBoundary": "登录接口返回正确的 token",
    "workspaceRef": "default",
    "specRefs": ["REQ-001", "SCN-001"],
    "acceptanceCriteria": [
        {"id": "AC-T001-01", "text": "正确用户名密码可以登录", "scenarioRefs": ["SCN-001"]}
    ],
    "validationCommands": [],
}
class AugmentTestEngineerTaskPromptTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        feature_dir = Path(temporary.name)
        batch_path = feature_dir / "plans" / "B001" / "plan.json"
        batch_path.parent.mkdir(parents=True)
        batch_path.write_text(
            json.dumps(
                {"batchId": "B001", "executionLane": "backend", "tasks": [SOURCE_TASK]}
            ),
            encoding="utf-8",
        )
        (feature_dir / "plan.json").write_text(
            json.dumps({"batches": [{"batchId": "B001", "path": "plans/B001/plan.json"}]}),
            encoding="utf-8",
        )
        assignment = build_assignments(feature_dir)[0]
        self.assignment = assignment["promptContent"]
        self.assignment_payload = json.loads(
            self.assignment.split("\n", 1)[1].rsplit("\n", 1)[0]
        )

    def _augment(self, description=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        emitted = MODULE.emit_updated_input(
            payload(self.assignment if description is None else description), stdout, stderr
        )
        return emitted, stdout, stderr

    def test_appends_complete_post_implementation_contract(self):
        emitted, stdout, stderr = self._augment()

        self.assertTrue(emitted)
        description = json.loads(stdout.getvalue())["updatedInput"]["description"]
        required = (
            "<AGENTS_INSTRUCTIONS>",
            "<SCOPE>",
            "<SYSTEM>",
            "<UNIT>",
            "executionLane",
            "workspaceRef",
            "batchPlanPath",
            "post_implementation=true",
            "tdd_rebuild=false",
            "source_fix_request",
            "contract_gap",
            "implementationPoints",
            "nonGoals",
            "validationLocations",
            "non-zero Evidence",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, description)
        fields = (
            "assignment",
            "constraint_files",
            "lane",
            "framework",
            "runner",
            "environment_initialization",
            "test_targets",
            "command_results",
            "failure_classification",
            "source_bug_attestation",
            "source_fix_request",
            "e2e_handoff",
        )
        for field in fields:
            with self.subTest(field=field):
                self.assertIn("`{}`".format(field), description)
        for classification in (
            "test_bug",
            "source_bug",
            "contract_gap",
            "environment",
            "flaky",
            "unknown",
        ):
            with self.subTest(classification=classification):
                self.assertIn("`{}`".format(classification), description)
        self.assertIn("appended", stderr.getvalue())

    def test_framework_and_runner_sources_are_explicit(self):
        _, stdout, _ = self._augment()
        description = json.loads(stdout.getvalue())["updatedInput"]["description"]

        self.assertIn("actually opened", description)
        self.assertIn("real manifests", description)
        self.assertIn("blocking `contract_gap`", description)
        self.assertIn("fall back to repository facts", description)

    def test_source_bug_contract_matches_utest_skill(self):
        _, stdout, _ = self._augment()
        description = json.loads(stdout.getvalue())["updatedInput"]["description"]

        self.assertIn("minimum current-feature production source", description)
        self.assertIn("machine-validated attestation", description)
        self.assertIn("static observation and exit 0 are invalid", description)
        self.assertIn("crosses the assignment boundary", description)

    def test_only_target_role_is_modified(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        emitted = MODULE.emit_updated_input(
            payload("review", subagent_type="verification-autodev"), stdout, stderr
        )

        self.assertFalse(emitted)
        self.assertEqual("", stdout.getvalue())
        self.assertIn(MODULE.TARGET_SUBAGENT_TYPE, stderr.getvalue())

    def test_marker_is_not_appended_twice(self):
        description = "{}\n{}".format(self.assignment, MODULE.APPEND_INSTRUCTION)
        emitted, stdout, stderr = self._augment(description)

        self.assertFalse(emitted)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("already present", stderr.getvalue())

    def test_accepts_input_alias(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        value = {
            "toolName": "Task",
            "input": {
                "description": "run",
                "subagent_type": "TEST-ENGINEER-AUTODEV",
            },
        }

        value["input"]["description"] = self.assignment
        self.assertTrue(MODULE.emit_updated_input(value, stdout, stderr))
        self.assertIn(MODULE.APPEND_MARKER, stdout.getvalue())

    def test_rejects_model_authored_summary_without_router_block(self):
        emitted, stdout, stderr = self._augment("summarized task")

        self.assertFalse(emitted)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("promptContent", stderr.getvalue())

    def test_rejects_reworded_implementation_points_against_plan(self):
        payload_value = json.loads(json.dumps(self.assignment_payload))
        payload_value["tasks"][0]["implementationPoints"] = ["阶梯折扣"]
        description = "<UTEST_ASSIGNMENT>\n{}\n</UTEST_ASSIGNMENT>".format(
            json.dumps(payload_value, ensure_ascii=False)
        )

        emitted, stdout, stderr = self._augment(description)

        self.assertFalse(emitted)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("当前 Batch plan 不一致", stderr.getvalue())


class UTestRoleRegistrationTest(unittest.TestCase):
    def test_dev_utest_registers_test_engineer_only(self):
        board = json.loads((ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8"))
        nodes = board["workflow"]["nodes"]
        node = next(item for item in nodes if item.get("id") == "dev.utest")
        policy = node["runtimePolicy"]

        self.assertTrue(policy["toolCustomConfig"]["task"]["enabled"])
        self.assertEqual(
            ["agents/test-engineer.md"],
            policy["subagentConfig"]["customSubagentFiles"],
        )
        self.assertEqual(
            ["test-engineer"],
            policy["subagentConfig"]["disabledBuiltinSubagents"],
        )
        self.assertNotIn("verification", json.dumps(policy))
        agent = (ROOT / "agents" / "test-engineer.md").read_text(encoding="utf-8")
        self.assertIn("name: test-engineer-autodev", agent)

    def test_hook_is_registered_for_task(self):
        config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        registrations = [item for item in config["PreToolUse"] if item.get("matcher") == "task|Task"]
        commands = [hook.get("command") for hook in registrations[0]["hooks"]]

        self.assertIn("python3 hooks/augment_test_engineer_task_prompt.py", commands)


if __name__ == "__main__":
    unittest.main()
