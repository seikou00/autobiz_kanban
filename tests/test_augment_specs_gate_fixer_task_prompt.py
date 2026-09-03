#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门禁修复子代理拿到的上下文必须自洽：命令绝对路径、phase 正确、越界不注入。"""

from __future__ import print_function

import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "hooks" / "augment_specs_gate_fixer_task_prompt.py"
SPEC = importlib.util.spec_from_file_location(
    "augment_specs_gate_fixer_task_prompt",
    str(MODULE_PATH),
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

HOOK_ENV_KEYS = ("PLUGIN_WORKSPACE", "PROJECT_DIR", "PROJECT_CODE", "FEATURE_ID")


def payload(description, subagent_type="specs-gate-fixer-autodev", alias=False):
    tool_input = {"description": description, "subagent_type": subagent_type}
    if alias:
        return {"toolName": "Task", "input": tool_input}
    return {"tool_name": "task", "tool_input": tool_input}


class FeatureFixture(object):
    def __init__(self, tmp, checkpoint="specs_in_progress"):
        workspace = Path(tmp) / "demo"
        self.feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
        self.feature_dir.mkdir(parents=True)
        (workspace / ".autobizdevops" / "state.json").write_text(
            json.dumps({"features": {"alpha": {"checkpoint": checkpoint}}}),
            encoding="utf-8",
        )


class AugmentSpecsGateFixerTest(unittest.TestCase):
    def _emit(self, description, checkpoint="specs_in_progress", **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = FeatureFixture(tmp, checkpoint=checkpoint)
            stdout, stderr = io.StringIO(), io.StringIO()
            emitted = MODULE.emit_updated_input(
                payload(description, **kwargs),
                stdout,
                stderr,
                root=ROOT,
                feature_dir=fixture.feature_dir,
            )
            return emitted, stdout.getvalue(), stderr.getvalue()

    def test_injects_absolute_gate_command_and_writable_paths(self):
        emitted, stdout, stderr = self._emit("把 dev.specs 的 structure 门禁修到通过")

        self.assertTrue(emitted)
        description = json.loads(stdout)["updatedInput"]["description"]
        self.assertTrue(description.startswith("把 dev.specs 的 structure 门禁修到通过"))
        self.assertIn(MODULE.APPEND_MARKER, description)
        self.assertIn(str(ROOT / "hooks" / "stage_gate.py"), description)
        self.assertIn("validate --stage dev.specs --phase structure", description)
        self.assertIn('--feature "alpha"', description)
        self.assertIn("proposal.md", description)
        self.assertIn("SPECS_REVIEW.md", description)
        self.assertIn("appended", stderr)

    def test_final_phase_is_resolved_from_the_dispatch_prompt(self):
        _, stdout, _ = self._emit("phase=final，把产物契约预检修到通过")
        description = json.loads(stdout)["updatedInput"]["description"]
        self.assertIn("--phase final", description)
        self.assertNotIn("--phase structure", description)

    def test_ambiguous_phase_falls_back_to_final(self):
        _, stdout, _ = self._emit("structure 已过，现在跑 final")
        description = json.loads(stdout)["updatedInput"]["description"]
        self.assertIn("--phase final", description)

    def test_unnamed_phase_falls_back_to_final(self):
        _, stdout, _ = self._emit("把门禁修到通过")
        description = json.loads(stdout)["updatedInput"]["description"]
        self.assertIn("--phase final", description)

    def test_does_not_append_marker_twice(self):
        emitted, stdout, stderr = self._emit(
            "重新派发 <{}> 已注入".format(MODULE.APPEND_MARKER)
        )
        self.assertFalse(emitted)
        self.assertEqual(stdout, "")
        self.assertIn("already present", stderr)

    def test_skips_when_checkpoint_is_not_specs_in_progress(self):
        emitted, stdout, stderr = self._emit("跑 structure 门禁", checkpoint="specs_done")
        self.assertFalse(emitted)
        self.assertEqual(stdout, "")
        self.assertIn("update_checkpoint.py", stderr)

    def test_non_target_subagent_is_not_modified(self):
        emitted, stdout, stderr = self._emit(
            "审查产物", subagent_type="critic-autodev"
        )
        self.assertFalse(emitted)
        self.assertEqual(stdout, "")
        self.assertIn("subagent_type must be", stderr)

    def test_missing_description_prints_repair_instruction(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        emitted = MODULE.emit_updated_input(
            {"tool_name": "task", "tool_input": {"subagent_type": "specs-gate-fixer-autodev"}},
            stdout,
            stderr,
            root=ROOT,
        )
        self.assertFalse(emitted)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("tool_input.description", stderr.getvalue())

    def test_accepts_input_alias(self):
        emitted, stdout, _ = self._emit("跑 structure 门禁", alias=True)
        self.assertTrue(emitted)
        self.assertIn(MODULE.APPEND_MARKER, json.loads(stdout)["updatedInput"]["description"])

    def test_missing_env_prints_repair_instruction(self):
        saved = {key: os.environ.pop(key, None) for key in HOOK_ENV_KEYS}
        try:
            stdout, stderr = io.StringIO(), io.StringIO()
            emitted = MODULE.emit_updated_input(
                payload("跑 structure 门禁"), stdout, stderr, root=ROOT
            )
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value
        self.assertFalse(emitted)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("PLUGIN_WORKSPACE", stderr.getvalue())


class RegistrationTest(unittest.TestCase):
    @staticmethod
    def _agent_name(path):
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith("name:"):
                return line.split(":", 1)[1].strip()
        raise AssertionError("{} 缺 frontmatter name".format(path))

    @classmethod
    def _all_agent_names(cls):
        return {cls._agent_name(path) for path in sorted((ROOT / "agents").glob("*.md"))}

    @staticmethod
    def _dev_specs_node():
        config = json.loads(
            (ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8")
        )
        return next(
            node for node in config["workflow"]["nodes"] if node.get("id") == "dev.specs"
        )

    def test_hook_is_registered_on_the_task_matcher(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        commands = [
            hook.get("command", "")
            for entry in hooks["PreToolUse"]
            if entry.get("matcher") == "task|Task"
            for hook in entry.get("hooks", [])
        ]
        self.assertTrue(
            any("augment_specs_gate_fixer_task_prompt.py" in command for command in commands),
            commands,
        )

    def test_agent_definition_matches_the_hook_target(self):
        agent = (ROOT / "agents" / "specs-gate-fixer.md").read_text(encoding="utf-8")
        self.assertIn("name: {}".format(MODULE.TARGET_SUBAGENT_TYPE), agent)
        self.assertIn("task", agent.split("---")[1])

    def test_agent_is_registered_on_the_dev_specs_node(self):
        """agents/ 下放了文件还不够：节点的 customSubagentFiles 没列上就加载不到。"""
        node = self._dev_specs_node()
        policy = node["runtimePolicy"]
        self.assertTrue(policy["toolCustomConfig"]["task"]["enabled"])
        self.assertIn(
            "agents/specs-gate-fixer.md",
            policy["subagentConfig"]["customSubagentFiles"],
        )

    def test_every_role_the_skill_dispatches_is_registered_on_its_node(self):
        """技能正文点名的角色，必须在同节点注册；这条是上一个 bug 的通用版。"""
        registered = {
            self._agent_name(ROOT / path)
            for path in self._dev_specs_node()["runtimePolicy"]["subagentConfig"][
                "customSubagentFiles"
            ]
        }
        skill = (ROOT / "skills" / "autodev" / "autodev-specs" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        for name in self._all_agent_names():
            if name in skill:
                self.assertIn(name, registered, "{} 被技能点名但未在 dev.specs 注册".format(name))

    def test_skill_keeps_an_inline_path_when_task_is_unavailable(self):
        """task 被平台禁用时仍要有修复通道，否则门禁失败没有任何指令兜底。"""
        skill = (ROOT / "skills" / "autodev" / "autodev-specs" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("independent_task", skill)
        self.assertIn("inline_main_agent", skill)
        self.assertIn("agents/specs-gate-fixer.md", skill)

    def test_skill_delegates_both_gates_to_the_subagent(self):
        skill = (ROOT / "skills" / "autodev" / "autodev-specs" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(MODULE.TARGET_SUBAGENT_TYPE, skill)
        for phase in ("structure", "final"):
            self.assertIn(
                "validate --stage dev.specs --phase {}".format(phase), skill
            )


if __name__ == "__main__":
    unittest.main()
