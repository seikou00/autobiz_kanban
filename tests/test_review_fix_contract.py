"""Check same-conversation Code Review and the optional Design reviewer."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_SKILL = ROOT / "skills/autodev/autodev-code/SKILL.md"
VERIFICATION_AGENT = ROOT / "agents/verification.md"
REFERENCES = ROOT / "skills/references"


def _read(path):
    return path.read_text(encoding="utf-8")


def _node_runtime(node_id):
    board = json.loads(_read(ROOT / "board_core/board_config.json"))
    return next(node for node in board["workflow"]["nodes"] if node["id"] == node_id)["runtimePolicy"]


class CodeReviewUsesBatchOnlyTest(unittest.TestCase):
    def test_legacy_code_review_is_removed(self) -> None:
        self.assertFalse((REFERENCES / "review-protocol-code.md").exists())
        self.assertFalse((ROOT / "agents" / "code-reviewer.md").exists())
        self.assertFalse((ROOT / "agents" / "code-simplifier.md").exists())
        skill = _read(CODE_SKILL)
        self.assertIn("在本对话 Review", skill)
        self.assertIn("启动对应 TASK 返修", skill)
        runtime = _node_runtime("dev.code")
        self.assertNotIn("subagentConfig", runtime)
        self.assertFalse(runtime["toolCustomConfig"]["task"]["enabled"])


class VerificationRoleIsResolvableTest(unittest.TestCase):
    """Historical Workflow verification instructions still resolve their role."""

    def test_agent_name_matches_runner_directive(self) -> None:
        self.assertIn("name: verification-autodev", _read(VERIFICATION_AGENT))

class OptionalDesignReviewTest(unittest.TestCase):
    def test_design_critic_is_available_and_read_only(self):
        files = _node_runtime("dev.design")["subagentConfig"]["customSubagentFiles"]
        agent = ROOT / "agents/critic_autodev_design_zh.md"
        self.assertIn(str(agent.relative_to(ROOT)), files)
        metadata = _read(agent).split("---")[1]
        self.assertIn("name: critic-autodev-design-zh", metadata)
        for tool in ("write_file", "edit_file", "write_todos"):
            self.assertIn(tool, metadata)
