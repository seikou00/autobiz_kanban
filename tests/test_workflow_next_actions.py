from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def next_action(node_id: str, node_status: str) -> dict:
    config = json.loads((ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in config["workflow"]["nodes"]}
    states = {state["nodeStatus"]: state for state in nodes[node_id]["states"]}
    return states[node_status]["nextAction"]


class WorkflowNextActionsTest(unittest.TestCase):
    def test_biz_to_dev_handoff_uses_autodev_root(self) -> None:
        action = next_action("biz.prd", "done")

        self.assertEqual(action["slashSkill"], "autodev")
        self.assertIn("初始化代码工作区 AGENTS.md", action["dialogTips"])


if __name__ == "__main__":
    unittest.main()
