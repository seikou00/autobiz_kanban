"""Exploration stays optional and its runtime roles resolve without prompt inflation."""
import json
import re
import unittest
from pathlib import Path

from hooks.render_session_context import _runtime_policy

ROOT = Path(__file__).resolve().parents[1]


class ExplorationPolicyTest(unittest.TestCase):
    def test_specs_and_design_default_to_the_main_session_with_optional_tools(self):
        for stage in ("dev.specs", "dev.design"):
            with self.subTest(stage=stage):
                policy = _runtime_policy(stage)
                self.assertEqual(policy["agentMode"], "solo")
                self.assertTrue(policy["toolCustomConfig"]["task"]["enabled"])
                agents = policy["subagentConfig"]["customSubagentFiles"]
                self.assertIn("agents/explore.md", agents)
                self.assertNotIn("agents/critic.md", agents)
                self.assertEqual("agents/critic_autodev_design_zh.md" in agents, stage == "dev.design")

    def test_registered_custom_roles_exist_and_have_unique_names(self):
        board = json.loads((ROOT / "board_core/board_config.json").read_text())
        for node in board["workflow"]["nodes"]:
            files = node.get("runtimePolicy", {}).get("subagentConfig", {}).get("customSubagentFiles", [])
            names = []
            for filename in files:
                agent = ROOT / filename
                self.assertTrue(agent.is_file(), filename)
                frontmatter = agent.read_text().split("---")[1]
                names.append(re.search(r"^name: (.+)$", frontmatter, re.MULTILINE).group(1))
            self.assertEqual(len(names), len(set(names)), node["id"])
            if node["id"] in {"dev.specs", "dev.design"}:
                self.assertIn("Explore-autodev", names)

    def test_task_hooks_do_not_rewrite_exploration_prompts(self):
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text())
        commands = [hook.get("command", "") for entry in hooks["PreToolUse"] for hook in entry.get("hooks", [])]
        self.assertFalse(any(command.endswith("hooks/augment_explore_task_prompt.py") for command in commands))
        self.assertTrue(any(command.endswith("hooks/augment_test_engineer_task_prompt.py") for command in commands))
