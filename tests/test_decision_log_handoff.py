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
    DECISION_HEADING,
    DECISION_ID,
    HookContext,
    _unresolved_decision_refs,
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
                self.assertEqual(DECISION_HEADING.findall(line), ["DEC-001"])

    def test_heading_pattern_requires_three_digits(self) -> None:
        self.assertEqual(DECISION_HEADING.findall("### DEC-01: 两位"), [])

    def test_id_pattern_does_not_match_technical_decision(self) -> None:
        self.assertEqual(DECISION_ID.findall("| REQ-001 | D-001 | API-001 |"), [])


class TemplateAndSkillWiringTest(unittest.TestCase):
    """模板与技能教的写法必须正好是校验器认的写法。"""

    def test_proposal_template_defines_the_section(self) -> None:
        template = (ROOT / "skills/autodev/autodev-specs/templates/proposal.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("## Decision Log", template)
        self.assertEqual(DECISION_HEADING.findall(template), ["DEC-001"])

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
        """`D-NNN` 与 `DEC-NNN` 都叫「决策」，方向却相反，必须在稳定 ID 清单处分清。

        a6868a2 删掉 Decision Log 后只剩一种决策，把 `D-001` 直呼「Decision」是
        自洽的；恢复 DEC 通道后这个裸名就同时指两个东西。plan 阶段若据此自造
        `DEC-NNN`，会撞上 design_decision_ref_unresolved 而报错原因看着莫名其妙。
        """
        plan = (ROOT / "skills/autodev/autodev-plan/SKILL.md").read_text(encoding="utf-8")
        id_list = next(
            line for line in plan.splitlines() if "Requirement `REQ-001`" in line
        )
        self.assertIn("技术决策 `D-001`", id_list, "D-001 必须限定为技术决策，不能裸称 Decision")
        self.assertIn("DEC-001", id_list, "同一处必须点出规格决策 DEC-001")
        self.assertIn("只引用不新增", id_list, "必须写明 DEC 是上游输入、本阶段不新增")


if __name__ == "__main__":
    unittest.main()
