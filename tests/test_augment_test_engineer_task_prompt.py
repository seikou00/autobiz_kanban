#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import importlib.util
import io
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "hooks" / "augment_test_engineer_task_prompt.py"
SPEC = importlib.util.spec_from_file_location("augment_test_engineer_task_prompt", str(MODULE_PATH))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def payload(description, subagent_type="test-engineer-autodev"):
    return {
        "tool_name": "task",
        "tool_input": {
            "description": description,
            "subagent_type": subagent_type,
        },
    }


class AugmentTestEngineerTaskPromptTest(unittest.TestCase):
    def _augment(self, description="run assignment"):
        stdout = io.StringIO()
        stderr = io.StringIO()
        emitted = MODULE.emit_updated_input(payload(description), stdout, stderr)
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
            "post_implementation=true",
            "tdd_rebuild=false",
            "source_fix_request",
            "contract_gap",
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
        description = "run\n{}".format(MODULE.APPEND_INSTRUCTION)
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

        self.assertTrue(MODULE.emit_updated_input(value, stdout, stderr))
        self.assertIn(MODULE.APPEND_MARKER, stdout.getvalue())


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
