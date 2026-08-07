#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import importlib.util
import io
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "hooks" / "augment_explore_task_prompt.py"
SPEC = importlib.util.spec_from_file_location(
    "augment_explore_task_prompt",
    str(MODULE_PATH),
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def explore_payload(description):
    return {
        "tool_name": "task",
        "tool_input": {
            "description": description,
            "subagent_type": "Explore-autodev",
        },
    }


class AugmentExploreTaskPromptTest(unittest.TestCase):
    def test_appends_agents_instruction_check_to_explore_prompt(self):
        payload = explore_payload("读取 PRD，然后探索现有代码")
        original = dict(payload["tool_input"])
        stdout = io.StringIO()
        stderr = io.StringIO()

        emitted = MODULE.emit_updated_input(payload, stdout, stderr)

        self.assertTrue(emitted)
        result = json.loads(stdout.getvalue())
        updated_description = result["updatedInput"]["description"]
        self.assertTrue(updated_description.startswith("读取 PRD，然后探索现有代码"))
        self.assertIn(MODULE.APPEND_MARKER, updated_description)
        self.assertIn("<AGENTS_INSTRUCTIONS>", updated_description)
        self.assertIn("<SCOPE>", updated_description)
        self.assertIn("<SYSTEM>", updated_description)
        self.assertIn("<UNIT>", updated_description)
        self.assertIn("## 架构约束", updated_description)
        self.assertIn("appended", stderr.getvalue())
        self.assertEqual(payload["tool_input"], original)

    def test_does_not_append_marker_twice(self):
        description = "探索工作空间{}".format(MODULE.APPEND_INSTRUCTION)
        stdout = io.StringIO()
        stderr = io.StringIO()

        emitted = MODULE.emit_updated_input(
            explore_payload(description),
            stdout,
            stderr,
        )

        self.assertFalse(emitted)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(description.count(MODULE.APPEND_MARKER), 2)
        self.assertIn("already present", stderr.getvalue())

    def test_does_not_append_when_agents_instructions_block_exists(self):
        description = """
<AGENTS_INSTRUCTIONS>
<SCOPE>部署单元映射</SCOPE>
<SYSTEM>系统级约束</SYSTEM>
<UNIT>单元级约束</UNIT>
</AGENTS_INSTRUCTIONS>
探索现有代码
"""
        stdout = io.StringIO()
        stderr = io.StringIO()

        emitted = MODULE.emit_updated_input(
            explore_payload(description),
            stdout,
            stderr,
        )

        self.assertFalse(emitted)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("already present", stderr.getvalue())

    def test_does_not_append_to_existing_explicit_constraint_prompt(self):
        description = """
### 系统约束文档（必须先读取）
请先读取 `/knowledge/local_architecture.md`，然后再探索代码。

### 输出格式
## 架构约束（从文档中提取）
"""
        stdout = io.StringIO()
        stderr = io.StringIO()

        emitted = MODULE.emit_updated_input(
            explore_payload(description),
            stdout,
            stderr,
        )

        self.assertFalse(emitted)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("already present", stderr.getvalue())

    def test_partial_constraint_prompt_still_gets_supplement(self):
        description = """
### 系统约束文档
参考 `/knowledge/local_architecture.md`。
"""
        stdout = io.StringIO()
        stderr = io.StringIO()

        emitted = MODULE.emit_updated_input(
            explore_payload(description),
            stdout,
            stderr,
        )

        self.assertTrue(emitted)
        result = json.loads(stdout.getvalue())
        self.assertIn(
            MODULE.APPEND_MARKER,
            result["updatedInput"]["description"],
        )

    def test_non_explore_subagent_is_not_modified(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        emitted = MODULE.emit_updated_input(
            {
                "tool_name": "task",
                "tool_input": {
                    "description": "审查代码",
                    "subagent_type": "code-reviewer",
                },
            },
            stdout,
            stderr,
        )

        self.assertFalse(emitted)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("subagent_type must be Explore-autodev", stderr.getvalue())

    def test_missing_description_prints_repair_instruction(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        emitted = MODULE.emit_updated_input(
            {
                "tool_name": "task",
                "tool_input": {"subagent_type": "Explore-autodev"},
            },
            stdout,
            stderr,
        )

        self.assertFalse(emitted)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("tool_input.description", stderr.getvalue())

    def test_accepts_input_alias(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        emitted = MODULE.emit_updated_input(
            {
                "toolName": "Task",
                "input": {
                    "description": "探索工作空间",
                    "subagent_type": "Explore-autodev",
                },
            },
            stdout,
            stderr,
        )

        self.assertTrue(emitted)
        result = json.loads(stdout.getvalue())
        self.assertIn(
            MODULE.APPEND_MARKER,
            result["updatedInput"]["description"],
        )


if __name__ == "__main__":
    unittest.main()
