"""Design 回检和 Code Batch Review 的契约测试。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CODE_SKILL = ROOT / "skills" / "autodev" / "autodev-code" / "SKILL.md"
SPECS_SKILL = ROOT / "skills" / "autodev" / "autodev-specs" / "SKILL.md"
DESIGN_SKILL = ROOT / "skills" / "autodev" / "autodev-design" / "SKILL.md"
VERIFICATION_AGENT = ROOT / "agents" / "verification.md"
DESIGN_CRITIC_AGENT = ROOT / "agents" / "critic_autodev_design_zh.md"
BOARD_CONFIG = ROOT / "board_core" / "board_config.json"
REFERENCES = ROOT / "skills" / "references"

SKILL_BY_STAGE = {"dev.design": DESIGN_SKILL}
PROTOCOL_BY_STAGE = {"dev.design": REFERENCES / "review-protocol-design.md"}
STAGES = tuple(SKILL_BY_STAGE)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def protocol(stage: str) -> str:
    return _read(PROTOCOL_BY_STAGE[stage])


def _node_subagents(node_id: str) -> dict:
    config = json.loads(_read(BOARD_CONFIG))
    for node in config["workflow"]["nodes"]:
        if node.get("id") == node_id:
            return node["runtimePolicy"]["subagentConfig"]
    raise AssertionError(f"board_config 中找不到 {node_id} 节点")


def _dev_code_subagents() -> dict:
    return _node_subagents("dev.code")


class ProtocolLivesInItsOwnFileTest(unittest.TestCase):
    """Design 的回检协议独立存放。"""

    def test_every_stage_skill_points_at_its_protocol_file(self) -> None:
        for stage, skill_path in SKILL_BY_STAGE.items():
            with self.subTest(stage=stage):
                content = _read(skill_path)
                filename = PROTOCOL_BY_STAGE[stage].name

                self.assertIn(f"${{pluginPath}}/skills/references/{filename}", content)
                self.assertIn("必须先读取并完整遵循该文件", content)
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

    def test_each_file_is_a_standalone_document(self) -> None:
        """拆开后各文件自成一篇，不得再出现按阶段筛选的注释标记。"""
        for stage in STAGES:
            with self.subTest(stage=stage):
                body = protocol(stage)

                self.assertNotIn("<!-- section:", body)
                self.assertIn(f"# 回检协议 · {stage}", body)

    def test_merged_protocol_and_renderer_are_retired(self) -> None:
        """旧的合并协议和渲染器不再使用。"""
        self.assertFalse((REFERENCES / "review-protocol.md").exists())
        self.assertFalse((ROOT / "hooks" / "render_review_protocol.py").exists())

    def test_every_stage_protocol_is_non_empty(self) -> None:
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.assertGreater(len(protocol(stage).strip().splitlines()), 10)


class ReviewSkeletonIsUnifiedTest(unittest.TestCase):
    """Design 协议保留逐条分类与交接格式。"""

    SHARED_CLASSES = ("需用户裁定", "回流上游", "仅列出", "结论不成立")

    def test_all_stages_require_a_findings_block(self) -> None:
        for stage in STAGES:
            with self.subTest(stage=stage):
                output = protocol(stage)

                self.assertIn("产出义务", output)
                self.assertIn("【回检结论】", output)
                self.assertIn("本轮回检无结论", output)
                for field in ("来源:", "原文严重度:", "结论:", "证据:", "分类:", "处置:"):
                    self.assertIn(field, output)

    def test_all_stages_share_the_same_classification_axis(self) -> None:
        for stage in STAGES:
            with self.subTest(stage=stage):
                output = protocol(stage)

                for classification in self.SHARED_CLASSES:
                    self.assertIn(classification, output)
                self.assertIn("不允许留空或自造取值", output)

    def test_all_stages_declare_their_severity_vocabulary(self) -> None:
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.assertIn("严重度词表", protocol(stage))

    def test_upstream_stages_bind_to_critic_section_names(self) -> None:
        """严重度必须用 critic 的原文分节名。"""
        for stage in STAGES:
            with self.subTest(stage=stage):
                output = protocol(stage)

                self.assertIn("Critical Findings", output)
                self.assertIn("Major Findings", output)
                self.assertIn("Minor Findings", output)
                self.assertIn("Open Questions (unscored)", output)

    def test_overall_verdict_is_not_an_action_basis(self) -> None:
        """防止模型看到 VERDICT: ACCEPT / APPROVE 就整体跳过逐条处理。"""
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.assertIn("不作为动作依据", protocol(stage))


class CodeReviewUsesBatchOnlyTest(unittest.TestCase):
    def test_legacy_code_review_is_removed(self) -> None:
        self.assertFalse((REFERENCES / "review-protocol-code.md").exists())
        self.assertFalse((ROOT / "agents" / "code-reviewer.md").exists())
        self.assertFalse((ROOT / "agents" / "code-simplifier.md").exists())
        self.assertIn("每个 Batch `review` 子阶段", _read(CODE_SKILL))

        subagents = _dev_code_subagents()
        self.assertNotIn("agents/code-reviewer.md", subagents["customSubagentFiles"])
        self.assertNotIn("agents/code-simplifier.md", subagents["customSubagentFiles"])
        self.assertNotIn("agents/explore.md", subagents["customSubagentFiles"])

    def test_specs_keeps_its_lightweight_review(self) -> None:
        self.assertIn("review-protocol-specs.md", _read(SPECS_SKILL))
        self.assertIn("行为规格回检", _read(REFERENCES / "review-protocol-specs.md"))


class VerificationRoleIsResolvableTest(unittest.TestCase):
    """Code 阶段的 verification 角色必须可被宿主解析。"""

    def test_agent_name_matches_runner_directive(self) -> None:
        self.assertIn("name: verification-autodev", _read(VERIFICATION_AGENT))

    def test_dev_code_injects_verification_agent(self) -> None:
        subagents = _dev_code_subagents()

        self.assertIn("agents/verification.md", subagents["customSubagentFiles"])
        self.assertIn("verification", subagents["disabledBuiltinSubagents"])


class DesignCriticRoleIsResolvableTest(unittest.TestCase):
    """dev.design 的回检角色是精简版 critic：读码入口收敛到 EVD，且必须能被宿主解析。"""

    def test_protocol_dispatches_the_design_specific_role(self) -> None:
        output = protocol("dev.design")

        self.assertIn("critic-autodev-design-zh", output)
        # 通用 critic 会自主探索代码库，dev.design 不再派发它。
        self.assertNotIn("`critic-autodev`", output)

    def test_agent_name_matches_the_protocol_directive(self) -> None:
        self.assertIn("name: critic-autodev-design-zh", _read(DESIGN_CRITIC_AGENT))

    def test_dev_design_injects_the_design_critic_agent(self) -> None:
        """agents/ 下放了文件还不够：节点的 customSubagentFiles 没列上就加载不到。"""
        subagents = _node_subagents("dev.design")

        self.assertIn("agents/critic_autodev_design_zh.md", subagents["customSubagentFiles"])
        self.assertIn("critic", subagents["disabledBuiltinSubagents"])

    def test_code_reading_entry_is_bounded_to_code_evidence(self) -> None:
        """augment hook 删除后没有运行时注入通道，读码边界只能写在代理定义里。"""
        agent = _read(DESIGN_CRITIC_AGENT)

        self.assertIn("Code Evidence", agent)
        self.assertIn("追调用方", agent)
        self.assertIn("git blame", agent)

    def test_agent_never_reads_plugin_source_or_runs_commands(self) -> None:
        """弱模型会去翻门禁脚本找修法；可读范围必须是白名单，且明确禁执行。"""
        agent = _read(DESIGN_CRITIC_AGENT)

        self.assertIn("## 可读范围", agent)
        self.assertIn("只有两类文件可读", agent)
        self.assertIn("不执行任何命令", agent)

    def test_artifact_paths_come_from_the_dispatch_prompt(self) -> None:
        """没有 augment hook，绝对路径只能由派发点写进 prompt；缺了就退回，不自己搜。"""
        output = protocol("dev.design")
        self.assertIn("feature 目录的绝对路径", output)
        self.assertIn("输入材料清单", output)

    def test_agent_is_read_only_and_terminal(self) -> None:
        agent = _read(DESIGN_CRITIC_AGENT)

        self.assertIn("disallowedTools", agent.split("---")[1])
        self.assertIn("不执行任何命令", agent)

    def test_agent_keeps_the_section_names_the_protocol_binds_to(self) -> None:
        agent = _read(DESIGN_CRITIC_AGENT)

        for section in (
            "Critical Findings",
            "Major Findings",
            "Minor Findings",
            "Open Questions (unscored)",
        ):
            self.assertIn(section, agent)


if __name__ == "__main__":
    unittest.main()
