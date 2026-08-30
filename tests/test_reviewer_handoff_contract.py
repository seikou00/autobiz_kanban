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
    def test_reviewer_handoff_depends_on_execution_mode(self) -> None:
        prompt = REVIEWER_AGENT.read_text(encoding="utf-8")

        self.assertIn("independent_task", prompt)
        self.assertIn("把控制权交还主 agent", prompt)
        self.assertIn("inline_main_agent", prompt)
        self.assertIn("必须停止当前回合", prompt)
        self.assertIn("请用户确认是否在下一回合切回 executor", prompt)

    def test_parent_separates_inline_reviewer_and_executor_turns(self) -> None:
        skill = REVIEWER_SKILL.read_text(encoding="utf-8")

        self.assertIn("independent_task", skill)
        self.assertIn("主 agent 在同一回合继续执行第 4 步", skill)
        self.assertIn("inline_main_agent", skill)
        self.assertIn("通过用户确认把 reviewer 与 executor 分隔到不同回合", skill)
        self.assertIn("未获得确认前", skill)

    def test_report_discloses_review_execution_mode(self) -> None:
        schema = REVIEWER_SCHEMA.read_text(encoding="utf-8")

        self.assertIn("## Review Mode", schema)
        self.assertIn("independent_task | inline_main_agent", schema)
        self.assertIn("inline_main_agent` 不得表述为独立子代理审查", schema)


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
