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

    def test_reviewer_receives_pre_utest_stage_contract(self) -> None:
        skill = REVIEWER_SKILL.read_text(encoding="utf-8")

        self.assertIn("Review 位于 Code 之后、UTest/E2E 之前", skill)
        self.assertIn("不执行这些命令", skill)
        self.assertIn("不检查目标测试目录或测试文件是否存在", skill)
        self.assertIn("验证错误、`test_gap`、`requirement_gap` 或 `unfinished_work`", skill)
        self.assertIn("不形成 blocker、warning 或交给 executor 修复", skill)
        self.assertIn("completion proposal 声称已执行", skill)

    def test_parent_does_not_repair_invalid_pre_utest_finding(self) -> None:
        skill = REVIEWER_SKILL.read_text(encoding="utf-8")

        self.assertIn("该 verdict 违反 `Stage contract`", skill)
        self.assertIn("不修改源码、PLAN 或测试", skill)
        self.assertIn("重新启动 reviewer 一次", skill)
        self.assertIn("不记为代码 blocker", skill)

    def test_report_discloses_review_execution_mode(self) -> None:
        schema = REVIEWER_SCHEMA.read_text(encoding="utf-8")

        self.assertIn("## Review Mode", schema)
        self.assertIn("independent_task | inline_main_agent", schema)
        self.assertIn("inline_main_agent` 不得表述为独立子代理审查", schema)

    def test_reviewer_reads_complete_review_scope_including_untracked_files(self) -> None:
        prompt = REVIEWER_AGENT.read_text(encoding="utf-8")
        schema = REVIEWER_SCHEMA.read_text(encoding="utf-8")

        for contract in (
            "proposal.files_changed",
            "git status --short",
            "--untracked-files=all",
            "git diff --cached --name-only",
            "untracked 文件没有 diff，必须直接读取完整内容",
            "excluded paths",
            "AGENTS.md",
            "仓库明确规范优先于通用偏好",
        ):
            self.assertIn(contract, prompt)
        self.assertIn("Review file set", schema)
        self.assertIn("Untracked Files", schema)

    def test_findings_require_evidence_and_verdict_is_deterministic(self) -> None:
        prompt = REVIEWER_AGENT.read_text(encoding="utf-8")
        schema = REVIEWER_SCHEMA.read_text(encoding="utf-8")

        for contract in (
            "Finding 准入",
            "requirement_gap",
            "confidence",
            "触发条件",
            "blocker 必须为 `HIGH`",
            "不使用 1–5 主观评分",
            "否则存在至少一个 blocker 时使用 `FAIL`",
            "否则存在至少一个 warning 时使用 `PASS_WITH_WARNINGS`",
        ):
            self.assertIn(contract, prompt)
        self.assertNotIn("claim_accuracy: 1-5", prompt)
        for field in ("Severity:", "Category:", "Confidence:", "Location:", "触发条件:"):
            self.assertIn(field, schema)


class ReviewerRoleIsResolvableTest(unittest.TestCase):
    """reviewer 角色必须走 agents/ 约定，且在 dev.review 会话里可被宿主解析。"""

    def test_agent_name_matches_skill_directive(self) -> None:
        self.assertIn("name: reviewer-autodev", REVIEWER_AGENT.read_text(encoding="utf-8"))
        self.assertIn("reviewer-autodev", REVIEWER_SKILL.read_text(encoding="utf-8"))

    def test_dev_review_injects_reviewer_agent(self) -> None:
        config = json.loads(BOARD_CONFIG.read_text(encoding="utf-8"))
        for node in config["workflow"]["nodes"]:
            if node.get("id") == "dev.review":
                subagents = node["runtimePolicy"]["subagentConfig"]
                break
        else:
            raise AssertionError("board_config 中找不到 dev.review 节点")

        self.assertIn("agents/reviewer.md", subagents["customSubagentFiles"])

    def test_skill_does_not_inline_agent_definition(self) -> None:
        """角色定义只在 agents/reviewer.md；skill 里再贴一份就会与之漂移。"""
        skill = REVIEWER_SKILL.read_text(encoding="utf-8")

        self.assertNotIn("reviewer-agent.md", skill)
        self.assertNotIn("autodev-reviewer-readonly", skill)


if __name__ == "__main__":
    unittest.main()
