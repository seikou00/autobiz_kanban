from pathlib import Path
import json
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD_CONFIG = ROOT / "board_core" / "board_config.json"
REVIEWER_SKILL = ROOT / "skills" / "autodev" / "autodev-reviewer" / "SKILL.md"
REVIEWER_AGENT = ROOT / "agents" / "reviewer.md"
REVIEWER_SCHEMA = (
    ROOT
    / "skills"
    / "autodev"
    / "autodev-reviewer"
    / "references"
    / "schemas.md"
)


class ReviewerHandoffContractTest(unittest.TestCase):
    def test_role_disallows_source_writes_and_nested_delegation(self):
        metadata = REVIEWER_AGENT.read_text().split("---")[1]
        tools = metadata.split("disallowedTools:", 1)[1].splitlines()[0]
        for tool in ("write_file", "edit_file", "task"):
            self.assertIn(tool, tools)

class ReviewerRoleIsResolvableTest(unittest.TestCase):
    """reviewer 角色必须走 agents/ 约定，且在 dev.review 会话里可被宿主解析。"""

    def test_agent_name_matches_skill_directive(self) -> None:
        self.assertIn("name: reviewer-autodev", REVIEWER_AGENT.read_text(encoding="utf-8"))
        self.assertIn("reviewer-autodev", REVIEWER_SKILL.read_text(encoding="utf-8"))

    def test_reviewer_is_not_a_separate_board_stage(self) -> None:
        config = json.loads(BOARD_CONFIG.read_text(encoding="utf-8"))
        node_ids = {node.get("id") for node in config["workflow"]["nodes"]}
        self.assertNotIn("dev.review", node_ids)

    def test_skill_does_not_inline_agent_definition(self) -> None:
        """角色定义只在 agents/reviewer.md；skill 里再贴一份就会与之漂移。"""
        skill = REVIEWER_SKILL.read_text(encoding="utf-8")

        self.assertNotIn("reviewer-agent.md", skill)
        self.assertNotIn("autodev-reviewer-readonly", skill)


if __name__ == "__main__":
    unittest.main()
