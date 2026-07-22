from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
REVIEWER_SKILL = ROOT / "skills" / "autodev" / "autodev-reviewer" / "SKILL.md"
REVIEWER_AGENT = (
    ROOT
    / "skills"
    / "autodev"
    / "autodev-reviewer"
    / "references"
    / "reviewer-agents.md"
)
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
        self.assertIn("把控制权交还主 agents", prompt)
        self.assertIn("inline_main_agent", prompt)
        self.assertIn("必须停止当前回合", prompt)
        self.assertIn("请用户确认是否在下一回合切回 executor", prompt)

    def test_parent_separates_inline_reviewer_and_executor_turns(self) -> None:
        skill = REVIEWER_SKILL.read_text(encoding="utf-8")

        self.assertIn("independent_task", skill)
        self.assertIn("主 agents 在同一回合继续执行第 4 步", skill)
        self.assertIn("inline_main_agent", skill)
        self.assertIn("通过用户确认把 reviewer 与 executor 分隔到不同回合", skill)
        self.assertIn("未获得确认前", skill)

    def test_report_discloses_review_execution_mode(self) -> None:
        schema = REVIEWER_SCHEMA.read_text(encoding="utf-8")

        self.assertIn("## Review Mode", schema)
        self.assertIn("independent_task | inline_main_agent", schema)
        self.assertIn("inline_main_agent` 不得表述为独立子代理审查", schema)


if __name__ == "__main__":
    unittest.main()
