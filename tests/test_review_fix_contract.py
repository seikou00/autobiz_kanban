"""回检与修复引导的契约测试。

协议正文的单一事实源是 skills/references/review-protocol.md，由
hooks/render_review_protocol.py 按阶段渲染，三个 SKILL.md 只保留调用。
因此绝大多数语义断言打在**渲染输出**上，而不是 SKILL.md 文本上。

只钉语义标记，不锁整段文案。反向断言（assertNotIn）是重点：`ba3f473 -> 4ebed44`
那次「回检四分类处置表」在合并中静默丢失，正是因为没有任何断言守着它。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.render_review_protocol import (  # noqa: E402
    STAGES,
    ReviewProtocolError,
    parse_sections,
    render,
)

CODE_SKILL = ROOT / "skills" / "autodev" / "autodev-code" / "SKILL.md"
SPECS_SKILL = ROOT / "skills" / "autodev" / "autodev-specs" / "SKILL.md"
PLAN_SKILL = ROOT / "skills" / "autodev" / "autodev-plan" / "SKILL.md"
SIMPLIFIER_AGENT = ROOT / "agents" / "code-simplifier.md"
EXPLORE_AGENT = ROOT / "agents" / "explore.md"
VERIFICATION_AGENT = ROOT / "agents" / "verification.md"
BOARD_CONFIG = ROOT / "board_core" / "board_config.json"
PROTOCOL = ROOT / "skills" / "references" / "review-protocol.md"

SKILL_BY_STAGE = {
    "dev.specs": SPECS_SKILL,
    "dev.plan": PLAN_SKILL,
    "dev.code": CODE_SKILL,
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _dev_code_subagents() -> dict:
    config = json.loads(_read(BOARD_CONFIG))
    for node in config["workflow"]["nodes"]:
        if node.get("id") == "dev.code":
            return node["runtimePolicy"]["subagentConfig"]
    raise AssertionError("board_config 中找不到 dev.code 节点")


class ProtocolIsSingleSourceTest(unittest.TestCase):
    """通用文字只写一处：SKILL.md 只留调用，正文全部来自协议文件。"""

    def test_every_stage_skill_invokes_the_renderer(self) -> None:
        for stage, skill_path in SKILL_BY_STAGE.items():
            with self.subTest(stage=stage):
                content = _read(skill_path)

                self.assertIn("hooks/render_review_protocol.py", content)
                self.assertIn(f"--stage {stage}", content)
                self.assertIn("完整遵循其输出", content)
                self.assertIn("不得凭记忆执行本节", content)

    def test_skills_no_longer_duplicate_protocol_prose(self) -> None:
        """正文搬走后，SKILL.md 里不应再留下协议的实体措辞。"""
        duplicated = (
            "【回检结论】",
            "严重度词表",
            "Critical Findings",
            "不作为动作依据",
            "不允许留空或自造取值",
        )
        for stage, skill_path in SKILL_BY_STAGE.items():
            with self.subTest(stage=stage):
                content = _read(skill_path)
                for marker in duplicated:
                    self.assertNotIn(marker, content)

    def test_renderer_rejects_unknown_stage(self) -> None:
        with self.assertRaises(ReviewProtocolError):
            render("dev.nope")

    def test_renderer_errors_explain_the_fix(self) -> None:
        """AGENTS.md 要求脚本报错时给出修复方式。"""
        with self.assertRaises(ReviewProtocolError) as caught:
            render("dev.nope")
        self.assertIn("修复：", str(caught.exception))

    def test_protocol_sections_all_target_known_stages(self) -> None:
        sections = parse_sections(_read(PROTOCOL))
        self.assertGreater(len(sections), 0)
        for section in sections:
            with self.subTest(section=section["name"]):
                for stage in section["stages"]:
                    self.assertIn(stage, ("*",) + STAGES)

    def test_every_stage_renders_non_empty(self) -> None:
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.assertGreater(len(render(stage).strip().splitlines()), 10)


class ReviewSkeletonIsUnifiedTest(unittest.TestCase):
    """三个阶段渲染出的协议同形：严重度词表 -> 逐条分类 -> 产出义务 -> 收口。"""

    SHARED_CLASSES = ("需用户裁定", "回流上游", "仅列出", "结论不成立")

    def test_all_stages_require_a_findings_block(self) -> None:
        for stage in STAGES:
            with self.subTest(stage=stage):
                output = render(stage)

                self.assertIn("产出义务", output)
                self.assertIn("【回检结论】", output)
                self.assertIn("本轮回检无结论", output)
                for field in ("来源:", "原文严重度:", "结论:", "证据:", "分类:", "处置:"):
                    self.assertIn(field, output)

    def test_all_stages_share_the_same_classification_axis(self) -> None:
        for stage in STAGES:
            with self.subTest(stage=stage):
                output = render(stage)

                for classification in self.SHARED_CLASSES:
                    self.assertIn(classification, output)
                self.assertIn("不允许留空或自造取值", output)

    def test_all_stages_declare_their_severity_vocabulary(self) -> None:
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.assertIn("严重度词表", render(stage))

    def test_upstream_stages_bind_to_critic_section_names(self) -> None:
        """严重度必须用 critic 的原文分节名，否则与 code-reviewer 的词表混淆。"""
        for stage in ("dev.specs", "dev.plan"):
            with self.subTest(stage=stage):
                output = render(stage)

                self.assertIn("Critical Findings", output)
                self.assertIn("Major Findings", output)
                self.assertIn("Minor Findings", output)
                self.assertIn("Open Questions (unscored)", output)

    def test_code_stage_records_role_severity_divergence(self) -> None:
        output = render("dev.code")

        # code-reviewer 没有 MAJOR，照抄上游的「Critical / Major」措辞就会错位。
        self.assertIn("没有 MAJOR", output)
        self.assertIn("没有严重度轴", output)

    def test_overall_verdict_is_not_an_action_basis(self) -> None:
        """防止模型看到 VERDICT: ACCEPT / APPROVE 就整体跳过逐条处理。"""
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.assertIn("不作为动作依据", render(stage))


class CodeReviewIsReadOnlyTest(unittest.TestCase):
    """Code 回检在实现和批次编译修复收口后只能上报，不能改码。"""

    def test_code_review_declares_closed_repair_channels(self) -> None:
        output = render("dev.code")

        for contract in (
            "已完成 TASK 不得重启",
            "最多 3 次的模型修复流程",
            "旧逐 TASK 验证和项目检查入口均不可用",
        ):
            self.assertIn(contract, output)

    def test_code_review_forbids_source_changes(self) -> None:
        output = render("dev.code")

        self.assertIn("不得因回检结论修改任何业务源码、测试或配置", output)
        self.assertIn("不得为回检启动新的 task run", output)
        self.assertIn("不得改写任何 `action=validation` evidence", output)

        # 丢失的旧指令：回检后直接修代码。恢复它等于恢复绕过 runner 的裸改。
        self.assertNotIn("如任一子代理返回有问题，则需要修复代码", output)

    def test_code_stage_hands_findings_to_the_batch_pipeline(self) -> None:
        output = render("dev.code")

        self.assertIn("交接下游", output)
        for target in ("review", "test", "quality_gate", "B-INT", "B-E2E"):
            self.assertIn(target, output)

    def test_code_review_does_not_normalize_severity(self) -> None:
        """三个角色词表不同，本轮原样转录；归一属第二轮 disposition 改造。"""
        self.assertIn("原样转录", render("dev.code"))


class SimplifierIsConstrainedAtCallSiteTest(unittest.TestCase):
    """agents/code-simplifier.md 保持社区版写法（可写），因此约束只能加在调用点。

    该代理在回检位置直接改文件会绕过 Evidence 且无法重验，所以协议必须
    (1) 提醒它默认会写文件、(2) 要求启动时在 prompt 里限制它、
    (3) 给出它仍然写了之后的处置——而不是假装它是只读的。
    """

    def test_agent_file_stays_upstream_writable(self) -> None:
        """守住「不改社区版代理定义」这个决定，防止后续有人又去加 disallowedTools。"""
        frontmatter = _read(SIMPLIFIER_AGENT).split("---")[1]
        self.assertNotIn("disallowedTools", frontmatter)

    def test_protocol_warns_the_agent_writes_by_default(self) -> None:
        output = render("dev.code")

        self.assertIn("默认契约是**直接改文件**", output)
        self.assertIn("## Files Simplified", output)

    def test_protocol_constrains_the_agent_in_the_task_prompt(self) -> None:
        output = render("dev.code")

        self.assertIn("task prompt", output)
        self.assertIn("不要落笔修改任何文件", output)

    def test_protocol_forbids_silent_revert_and_requires_disclosure(self) -> None:
        """用户已否决「整体还原」：可能覆盖用户自己的改动。改为如实记录。"""
        output = render("dev.code")

        self.assertIn("不要自行还原", output)
        self.assertIn("未经任何验证", output)

    def test_prohibition_names_its_addressee(self) -> None:
        """禁令只对主 agent 成立；不写明主体会被读成「子代理也不会写」的假保证。"""
        self.assertIn("主 agent 禁止", render("dev.code"))


class ExploreRoleIsResolvableTest(unittest.TestCase):
    """角色名必须与 agents/explore.md 精确一致，且在 dev.code 被真实注入。"""

    def test_agent_name_is_capitalized_explore(self) -> None:
        self.assertIn("name: Explore-autodev", _read(EXPLORE_AGENT))

    def test_protocol_uses_exact_role_name(self) -> None:
        output = render("dev.code")

        self.assertIn("Explore-autodev", output)
        # 小写形态无法匹配 augment_explore_task_prompt.py 的 TARGET_SUBAGENT_TYPE。
        self.assertNotIn("explore-autodev", output)

    def test_dev_code_injects_explore_agent(self) -> None:
        subagents = _dev_code_subagents()

        self.assertIn("agents/explore.md", subagents["customSubagentFiles"])
        self.assertIn("Explore", subagents["disabledBuiltinSubagents"])


class BatchCompileRoleIsResolvableTest(unittest.TestCase):
    """Code 阶段批次编译角色必须可被宿主解析。"""

    def test_agent_name_matches_runner_directive(self) -> None:
        self.assertIn("name: verification-autodev", _read(VERIFICATION_AGENT))

    def test_dev_code_injects_verification_agent(self) -> None:
        subagents = _dev_code_subagents()

        self.assertIn("agents/verification.md", subagents["customSubagentFiles"])
        self.assertIn("verification", subagents["disabledBuiltinSubagents"])


if __name__ == "__main__":
    unittest.main()
