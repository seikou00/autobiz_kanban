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
    / "reviewer-agent.md"
)
REVIEWER_SCHEMA = (
    ROOT
    / "skills"
    / "autodev"
    / "autodev-reviewer"
    / "references"
    / "schemas.md"
)
STANDARDS_BASELINE = (
    ROOT
    / "skills"
    / "autodev"
    / "autodev-reviewer"
    / "references"
    / "standards-baseline.md"
)
AXIS_REVIEWERS = (
    ROOT
    / "skills"
    / "autodev"
    / "autodev-reviewer"
    / "references"
    / "axis-reviewers.md"
)
CODE_SKILL = ROOT / "skills" / "autodev" / "autodev-code" / "SKILL.md"


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

    def test_code_stage_captures_baseline_before_business_changes(self) -> None:
        skill = CODE_SKILL.read_text(encoding="utf-8")

        self.assertIn("capture_review_baseline.py", skill)
        self.assertIn("修改任何业务文件前", skill)
        self.assertIn("不得重新捕获或覆盖", skill)
        self.assertIn("scope_confidence: partial", skill)

    def test_reviewer_uses_pinned_scope_instead_of_recent_commit_window(self) -> None:
        prompt = REVIEWER_AGENT.read_text(encoding="utf-8")
        schema = REVIEWER_SCHEMA.read_text(encoding="utf-8")

        for required in (
            "review-baseline.json",
            "git merge-base <base-sha> HEAD",
            "git log --oneline <base-sha>..HEAD",
            "git diff --binary <base-sha>",
            "git ls-files --others --exclude-standard",
            "legacy_scope",
        ):
            self.assertIn(required, prompt)
        self.assertNotIn("git log --oneline -n 5", prompt)
        self.assertIn('"review_scope"', schema)
        self.assertIn('"head_sha_at_proposal"', schema)
        self.assertIn('"include_untracked": true', schema)

    def test_dual_axis_findings_remain_separate(self) -> None:
        skill = REVIEWER_SKILL.read_text(encoding="utf-8")
        prompt = REVIEWER_AGENT.read_text(encoding="utf-8")
        axis_prompts = AXIS_REVIEWERS.read_text(encoding="utf-8")

        for required in (
            "dual_axis_parallel",
            "dual_axis_single_reviewer",
            "Standards",
            "Spec",
        ):
            self.assertIn(required, skill)
            self.assertIn(required, prompt)
        self.assertIn("不跨轴合并、删除或重新排序严重性", axis_prompts)
        self.assertIn("只有 coordinator 写", axis_prompts)

    def test_smells_are_judgement_calls_and_never_blockers(self) -> None:
        baseline = STANDARDS_BASELINE.read_text(encoding="utf-8")
        prompt = REVIEWER_AGENT.read_text(encoding="utf-8")

        for smell in (
            "Mysterious Name",
            "Duplicated Code",
            "Feature Envy",
            "Speculative Generality",
            "Middle Man",
        ):
            self.assertIn(smell, baseline)
        self.assertIn("仓库明确规范覆盖通用 baseline", baseline)
        self.assertIn("smell 必须 `judgement_call=true`", prompt)
        self.assertIn("不能单独造成 FAIL", prompt)

    def test_report_schema_exposes_both_axes_without_numeric_scores(self) -> None:
        schema = REVIEWER_SCHEMA.read_text(encoding="utf-8")
        prompt = REVIEWER_AGENT.read_text(encoding="utf-8")

        for section in (
            "## Review Scope",
            "## Axis Summary",
            "## Standards Sources",
            "## Standards Review",
            "## Spec Review",
            "## Requirement Coverage",
            "## E2E Focus",
        ):
            self.assertIn(section, schema)
        self.assertIn("删除 1–5 主观评分", prompt)
        self.assertNotIn("claim_accuracy: 1-5", prompt)


if __name__ == "__main__":
    unittest.main()
