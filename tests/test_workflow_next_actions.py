from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def board_config() -> dict:
    return json.loads((ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8"))


def next_action(node_id: str, node_status: str) -> dict:
    config = board_config()
    nodes = {node["id"]: node for node in config["workflow"]["nodes"]}
    states = {state["nodeStatus"]: state for state in nodes[node_id]["states"]}
    return states[node_status]["nextAction"]


class WorkflowNextActionsTest(unittest.TestCase):
    def test_biz_to_dev_handoff_uses_autodev_root(self) -> None:
        action = next_action("biz.prd", "done")

        self.assertEqual(action["slashSkill"], "autodev")
        self.assertIn("下一步进入行为规格", action["dialogTips"])
        self.assertIn("autodev-code", action["dialogTips"])
        self.assertNotIn("autodev-frontend", action["dialogTips"])
        self.assertNotIn("初始化代码工作区 AGENTS.md", action["dialogTips"])

    def test_system_prompt_inject_distinguishes_workspace_and_feature_dir(self) -> None:
        config = board_config()

        for platform, commands in config["inspectCommands"].items():
            with self.subTest(platform=platform):
                prompt = commands["system_prompt_inject"]
                self.assertIn("PLUGIN_WORKSPACE", prompt)
                self.assertIn("PROJECT_CODE", prompt)
                self.assertIn("FEATURE_ID", prompt)
                self.assertIn("PROJECT_PLUGIN_DIR", prompt)
                self.assertIn("FEATURE_DIR", prompt)
                self.assertNotIn("slug所在目录", prompt)

    def test_skill_docs_do_not_use_legacy_plugin_output_feature_dir_formula(self) -> None:
        legacy = "工作目录 = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}/"

        for path in (ROOT / "skills").rglob("SKILL.md"):
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                content = path.read_text(encoding="utf-8")
                self.assertIn("PLUGIN_WORKSPACE", content)
                self.assertIn("PROJECT_CODE", content)
                self.assertIn("FEATURE_ID", content)
                self.assertIn("PROJECT_PLUGIN_DIR", content)
                self.assertNotIn("PLUGIN_OUTPUT_DIR", content)
                self.assertIn("FEATURE_DIR", content)
                self.assertNotIn('read_state_json.py" --workspace', content)
                self.assertNotIn('update_checkpoint.py" --workspace', content)
                self.assertNotIn(legacy, content)


if __name__ == "__main__":
    unittest.main()
