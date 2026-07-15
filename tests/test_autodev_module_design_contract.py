from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTODEV = ROOT / "skills" / "autodev"
PLAN_SKILL = AUTODEV / "autodev-plan" / "SKILL.md"
DESIGN_TEMPLATE = AUTODEV / "autodev-plan" / "templates" / "design.md"
PLAN_TEMPLATE = AUTODEV / "autodev-plan" / "templates" / "plan.md"
DETAIL_DESIGN_SKILL = AUTODEV / "autodev-detail-design" / "SKILL.md"
CODE_SKILL = AUTODEV / "autodev-code" / "SKILL.md"


class AutodevModuleDesignContractTest(unittest.TestCase):
    def test_plan_defines_conditional_deep_module_exploration(self) -> None:
        content = PLAN_SKILL.read_text(encoding="utf-8")

        for required_rule in (
            "新增或修改公共 Interface",
            "删除测试",
            "进程内",
            "本地可替代",
            "远程自有",
            "真正外部",
            "Design It Twice",
            "最小 Interface",
            "最大灵活性",
            "默认调用最简单",
            "Depth、Locality、Seam placement",
            "未命中上述条件时不得为普通任务强制执行 Design It Twice",
        ):
            self.assertIn(required_rule, content)

    def test_design_and_plan_templates_trace_module_decisions(self) -> None:
        design = DESIGN_TEMPLATE.read_text(encoding="utf-8")
        plan = PLAN_TEMPLATE.read_text(encoding="utf-8")

        for required_field in (
            "Module Decisions / 模块决策",
            "Dependency Decisions / 依赖决策",
            "MOD-01",
            "DEP-01",
            "Test Surface",
            "Production / Test Adapter Strategy",
        ):
            self.assertIn(required_field, design)

        for decision_id in ("MOD-01", "DEP-01"):
            self.assertIn(decision_id, plan)

    def test_detail_design_only_maps_confirmed_module_design(self) -> None:
        content = DETAIL_DESIGN_SKILL.read_text(encoding="utf-8")

        for required_rule in (
            "落实设计，不重新设计",
            "不执行 Design It Twice",
            "回流 `/autodev-plan`",
            "模块实现映射",
            "Adapter / 依赖接线",
            "Test Surface",
            "每个已确认 `MOD-xx` / `DEP-xx`",
        ):
            self.assertIn(required_rule, content)

    def test_code_consumes_decisions_and_has_deterministic_return_paths(self) -> None:
        content = CODE_SKILL.read_text(encoding="utf-8")

        for required_rule in (
            "`MOD-xx` Module Decisions",
            "`DEP-xx` Dependency Decisions",
            "架构影响: 无",
            "不执行 Design It Twice",
            "回流 `/autodev-specs`",
            "回流 `/autodev-plan`",
            "回流 `/autodev-detail-design`",
            "不得新增纯转发 Module",
            "不得为测试暴露内部 Seam",
            "猜测性 Port/Adapter",
        ):
            self.assertIn(required_rule, content)


if __name__ == "__main__":
    unittest.main()
