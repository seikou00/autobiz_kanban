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

    def test_validated_assignment_is_preserved_without_a_second_protocol(self):
        emitted, stdout, stderr = self._augment()
        self.assertTrue(emitted)
        result = json.loads(stdout.getvalue())["updatedInput"]
        description = result["description"]
        self.assertEqual(description.split("</UTEST_ASSIGNMENT>")[0],
                         self.assignment.split("</UTEST_ASSIGNMENT>")[0])
        self.assertTrue(MODULE.validate_assignment_block(description, io.StringIO()))
        self.assertEqual(set(result), {"description"})
        self.assertLess(len(MODULE.APPEND_INSTRUCTION.encode()), 2048)

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
    def test_test_engineer_is_not_a_separate_board_stage(self):
        board = json.loads((ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8"))
        nodes = board["workflow"]["nodes"]
        self.assertNotIn("dev.utest", {node.get("id") for node in nodes})
        agent = (ROOT / "agents" / "test-engineer.md").read_text(encoding="utf-8")
        self.assertIn("name: test-engineer-autodev", agent)

    def test_hook_is_registered_for_task(self):
        config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        registrations = [item for item in config["PreToolUse"] if item.get("matcher") == "task|Task"]
        commands = [hook.get("command") for hook in registrations[0]["hooks"]]

        self.assertIn("python -X utf8 hooks/augment_test_engineer_task_prompt.py", commands)


if __name__ == "__main__":
    unittest.main()
