"""specs 阶段的决策要能传到 plan：design 的 `DEC-NNN` 必须在 proposal 里解析得到。

这条链由 `ec59d6e`（2026-07-22）建立——proposal 的 `## Decision Log` 记「为什么
这么定、否决了什么」，design 的规格追踪表 `Decision` 列按 `DEC-NNN` 引用它。

`fc0cf40`（07-28）又把它征用为 Open Questions `Status=已确认` 的跨文件证据实体，
而那一层在 07-29 实测失效（模型照样零提问推进，报错只教会伪造）。`a6868a2`
（07-31）清理 D 式时把两个身份一起删了——但只有证据实体那个被证伪过。

因此本文件钉的是恢复后的边界：**只判引用解析**。不判 Status、不判理由写得好不好、
不要求每个 Requirement 都有决策（`无` 是合法值）——那些正是被证伪的那一档。
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HOOKS = ROOT / "skills" / "autodev" / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

from artifact_check import (  # noqa: E402
    SPEC_DECISION_HEADING,
    SPEC_DECISION_ID,
    HookContext,
    _unresolved_decision_refs,
    validate_proposal_contract,
)
from hooks.init_workspace import create_feature, init_workspace  # noqa: E402


PROPOSAL = """# Proposal: 导出

## Why

需要导出。

## Decision Log

### DEC-001: 导出走异步

- **决定:** 导出返回任务号，结果异步获取
- **为什么:** 大数据量同步导出会超过网关超时
- **否决:** 同步返回文件流——超过 30s 网关断连
- **约束:** order-export

## Open Questions

无
"""

DESIGN = """## 3. Spec Traceability / 规格追踪

| Requirement | Scenarios | Decision | Design Coverage | Evidence |
|---|---|---|---|---|
| REQ-001 | SCN-001 | {decision} | API-001 / D-001 | EVD-001 |
"""


class DecisionRefResolutionTest(unittest.TestCase):
    def _run(self, decision: str, proposal: str | None = PROPOSAL) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve() / "demo"
            project.mkdir()
            init_workspace(project)
            create_feature(project, "alpha")
            feature_dir = project / ".autobizdevops" / "features" / "alpha"
            if proposal is not None:
                (feature_dir / "proposal.md").write_text(proposal, encoding="utf-8")
            ctx = HookContext(skill="autodev-plan", slug="alpha", root=project)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                failures = _unresolved_decision_refs(ctx, DESIGN.format(decision=decision))
            return failures, buffer.getvalue()

    def test_resolvable_reference_passes(self) -> None:
        failures, _ = self._run("DEC-001")
        self.assertEqual(failures, 0)

    def test_wu_is_a_legal_cell_value(self) -> None:
        """不是每个 Requirement 背后都有值得记的取舍。"""
        failures, _ = self._run("无")
        self.assertEqual(failures, 0)

    def test_unresolvable_reference_is_blocked(self) -> None:
        failures, output = self._run("DEC-009")
        self.assertEqual(failures, 1)
        self.assertIn("design_decision_ref_unresolved", output)
        self.assertIn("DEC-009", output)
        self.assertIn("POST_SKILL_REPAIR", output)

    def test_technical_decision_id_is_not_a_spec_decision(self) -> None:
        """D-NNN 是 design 自己的技术决策，不该被当成 DEC 去 proposal 里找。"""
        failures, _ = self._run("D-001")
        self.assertEqual(failures, 0)

    def test_missing_decision_log_section_is_blocked(self) -> None:
        failures, output = self._run("DEC-001", proposal="# Proposal\n\n## Why\n\n无\n")
        self.assertEqual(failures, 1)
        self.assertIn("design_decision_ref_unresolved", output)

    def test_missing_proposal_does_not_double_report(self) -> None:
        """缺 proposal 是 proposal_contract 的失败，本检查不重复报。"""
        failures, output = self._run("DEC-001", proposal=None)
        self.assertEqual(failures, 0)
        self.assertEqual(output, "")


class DecisionPatternTest(unittest.TestCase):
    def test_heading_pattern_accepts_both_colons(self) -> None:
        for line in ("### DEC-001: 标题", "### DEC-001： 标题"):
            with self.subTest(line=line):
                self.assertEqual(SPEC_DECISION_HEADING.findall(line), ["DEC-001"])

    def test_heading_pattern_requires_three_digits(self) -> None:
        self.assertEqual(SPEC_DECISION_HEADING.findall("### DEC-01: 两位"), [])

    def test_id_pattern_does_not_match_technical_decision(self) -> None:
        self.assertEqual(SPEC_DECISION_ID.findall("| REQ-001 | D-001 | API-001 |"), [])


class TemplateAndSkillWiringTest(unittest.TestCase):
    """模板与技能教的写法必须正好是校验器认的写法。"""

    def test_proposal_template_defines_the_section(self) -> None:
        template = (ROOT / "skills/autodev/autodev-specs/templates/proposal.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("## Decision Log", template)
        self.assertEqual(SPEC_DECISION_HEADING.findall(template), ["DEC-001"])

    def test_design_template_cites_dec_not_d(self) -> None:
        template = (ROOT / "skills/autodev/autodev-plan/templates/design.md").read_text(
            encoding="utf-8"
        )
        traceability = template.split("## 3. Spec Traceability")[1].split("## 4.")[0]
        self.assertIn("DEC-001", traceability)

    def test_both_skills_wire_the_channel(self) -> None:
        specs = (ROOT / "skills/autodev/autodev-specs/SKILL.md").read_text(encoding="utf-8")
        plan = (ROOT / "skills/autodev/autodev-plan/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Decision Log", specs)
        self.assertIn("DEC-NNN", specs)
        self.assertIn("DEC-NNN", plan)
        self.assertIn("Decision Log", plan)

    def test_plan_skill_separates_dec_from_d(self) -> None:
        """`D-NNN` 与 `DEC-NNN` 都叫「决策」，方向和强制力都不同，必须写清。

        a6868a2 删掉 Decision Log 后只剩一种决策，把 `D-001` 直呼「Decision」是
        自洽的；恢复 DEC 通道后这个裸名就同时指两个东西。两者强度也差一个量级：
        `D` 每任务至少引一个、design 里每条都要被引到（双向），`DEC` 只判引用
        能否解析、可写「无」。只说「方向相反」会让人以为 D 写进 design 就完事。
        """
        plan = (ROOT / "skills/autodev/autodev-plan/SKILL.md").read_text(encoding="utf-8")
        id_list = next(line for line in plan.splitlines() if "Requirement `REQ-001`" in line)
        self.assertIn("技术决策 `D-001`", id_list, "D-001 必须限定为技术决策，不能裸称 Decision")
        self.assertIn("decisionIds", id_list, "必须点出 D-NNN 的引用位置")
        self.assertIn("plan_json_contract", id_list, "必须点出判定者")

        dec_rule = next(line for line in plan.splitlines() if "规格决策 `DEC-001`" in line)
        self.assertIn("不新增", dec_rule, "必须写明 DEC 是上游输入、本阶段不新增")
        self.assertIn("design_contract", dec_rule, "必须点出判定者")
        self.assertIn("无", dec_rule, "必须写明可为空")


class DecisionLogSectionScopeTest(unittest.TestCase):
    """定义必须落在 `## Decision Log` 节内，写在 proposal 别处不算。

    早先实现对整个 proposal 做 findall，等于「任意位置有同号三级标题即通过」，
    比它自称的「Decision Log 内存在」弱。节外的 `### DEC-001:` 可能是引用、
    是历史残留、也可能是别的章节恰好同号——都不构成决策定义。
    """

    def _resolve(self, proposal: str) -> int:
        design = "| REQ-001 | SCN-001 | DEC-001 | API-001 / D-001 | EVD-001 |\n"
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve() / "demo"
            project.mkdir()
            init_workspace(project)
            create_feature(project, "alpha")
            feature = project / ".autobizdevops" / "features" / "alpha"
            (feature / "proposal.md").write_text(proposal, encoding="utf-8")
            ctx = HookContext(skill="autodev-plan", slug="alpha", root=project)
            with contextlib.redirect_stdout(io.StringIO()):
                return _unresolved_decision_refs(ctx, design)

    def test_definition_inside_the_section_resolves(self) -> None:
        self.assertEqual(self._resolve(PROPOSAL), 0)

    def test_definition_before_the_section_does_not_resolve(self) -> None:
        proposal = "## Why\n\n### DEC-001: 混在别处\n\n## Decision Log\n\n无\n"
        self.assertEqual(self._resolve(proposal), 1)

    def test_definition_after_the_section_does_not_resolve(self) -> None:
        proposal = "## Decision Log\n\n无\n\n## Open Questions\n\n### DEC-001: 节之后\n"
        self.assertEqual(self._resolve(proposal), 1)


class ProposalRequiresDecisionLogTest(unittest.TestCase):
    """节缺失要在 specs 阶段就报，不能拖到 plan 才由引用解析发现。"""

    def _run(self, proposal: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve() / "demo"
            project.mkdir()
            init_workspace(project)
            create_feature(project, "alpha")
            feature = project / ".autobizdevops" / "features" / "alpha"
            (feature / "proposal.md").write_text(proposal, encoding="utf-8")
            ctx = HookContext(skill="autodev-specs", slug="alpha", root=project)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                return validate_proposal_contract(ctx), buffer.getvalue()

    SECTIONS = ("Why", "What Changes", "Capabilities", "Impact", "Out of Scope", "Open Questions")

    def test_missing_decision_log_is_reported(self) -> None:
        proposal = "".join(f"## {name}\n\n无\n\n" for name in self.SECTIONS)
        failures, output = self._run(proposal)
        self.assertGreaterEqual(failures, 1)
        self.assertIn("invalid_proposal_missing_section", output)
        self.assertIn("Decision Log", output)

    def test_all_sections_present_passes(self) -> None:
        names = (*self.SECTIONS[:-1], "Decision Log", "Open Questions")
        proposal = "".join(f"## {name}\n\n无\n\n" for name in names)
        failures, output = self._run(proposal)
        self.assertEqual(failures, 0, output)


if __name__ == "__main__":
    unittest.main()
