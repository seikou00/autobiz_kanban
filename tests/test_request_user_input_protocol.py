from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
ROOT_SKILL = SKILLS / "SKILL.md"
PROTOCOL = SKILLS / "references" / "ask-user-question.md"
DISCUSS_SKILL = SKILLS / "autobiz" / "autobiz-requirement-discuss" / "SKILL.md"
AUTODEV_SKILL = SKILLS / "autodev" / "SKILL.md"
PLAN_SKILL = SKILLS / "autodev" / "autodev-plan" / "SKILL.md"
SPECS_SKILL = SKILLS / "autodev" / "autodev-specs" / "SKILL.md"
PRD_SKILL = DISCUSS_SKILL

# plan 的裁定门是三处里被绕过后逐条补厚的那一处，prd/specs 按它对齐。
#
# 钉机制不钉字面：每条列若干可接受写法，命中任一即算在场。措辞由人把关，
# 测试只保证「这条约束还在」——门的构成要素少一个就会被最低成本解释绕过，
# 但换个说法表达同一个意思不该让测试变红。
SHARED_ADJUDICATION_RULES = (
    ("仅声称拥有不算提供", ("声称拥有 ≠ 提供", "声称拥有不等于提供")),
    ("缺材料可暂停", ("暂停，拿到材料后继续",)),
    ("延后按语义判定", ("延后判定按语义不按字面", "按语义不按字面")),
    (
        "不存在先占位后推进的出口",
        ("不存在「先假设 / 先按默认方案 / 先占位」后推进的出口", "先占位」后推进的出口"),
    ),
    ("该出口不得重新引入", ("不得以任何措辞重新引入",)),
    (
        "探索期模板不得搬进裁定门",
        ("禁止搬进裁定门", "在裁定阶段禁止使用", "裁定阶段禁止"),
    ),
    ("生成产物前要自查消解", ("消解自查",)),
    ("展示不等于裁定", ("展示不等于裁定",)),
    (
        "选后仍待确认的选项非法",
        ("凡选中后条目仍处于待确认状态的选项都是非法选项", "仍处于待确认状态的选项都是非法选项"),
    ),
)


def missing_rules(content: str, extra: tuple = ()) -> list[str]:
    """返回未命中任何可接受写法的规则名。"""
    missing = [
        name
        for name, variants in SHARED_ADJUDICATION_RULES
        if not any(v in content for v in variants)
    ]
    missing += [phrase for phrase in extra if phrase not in content]
    return missing


class RequestUserInputProtocolTest(unittest.TestCase):
    def test_shared_protocol_documents_native_tool_contract(self) -> None:
        content = PROTOCOL.read_text(encoding="utf-8")

        for required_rule in (
            "1–3 个 `questions`",
            "2–3 个互斥 `options`",
            "`snake_case`",
            " (Recommended)",
            "不要手工添加 `Other`",
            "`autoResolutionMs`",
            "文本降级",
            "仍须提供 2–3 个互斥 `options`",
            "禁止把“现在提供”“提供路径”“补充说明”",
            "直接吸收并继续，不得再询问一次相同内容",
        ):
            self.assertIn(required_rule, content)

    def test_discuss_routes_free_text_through_other_without_follow_up(self) -> None:
        content = DISCUSS_SKILL.read_text(encoding="utf-8")

        for required_rule in (
            "Other /「其他」中补充新问题",
            "不得再次询问“请补充说明”",
            "不得生成「现在提供」「提供路径」「补充说明」等空动作选项",
            "自由文本补充类问题仍必须提供至少 2 个预设选项",
            "同一轮不得再次追问该内容",
        ):
            self.assertIn(required_rule, content)

        self.assertNotIn("**选项2**：补充其他问题", content)
        self.assertNotIn("引导用户补充说明", content)

    def test_internal_phase_transitions_do_not_require_confirmation(self) -> None:
        root_skill = ROOT_SKILL.read_text(encoding="utf-8")
        protocol = PROTOCOL.read_text(encoding="utf-8")
        autodev = AUTODEV_SKILL.read_text(encoding="utf-8")
        plan = PLAN_SKILL.read_text(encoding="utf-8")
        discuss = DISCUSS_SKILL.read_text(encoding="utf-8")

        self.assertIn("`biz -> dev -> ops` 按状态自动续跑", root_skill)
        self.assertIn("只是结束探索、切换内部阶段", protocol)
        self.assertIn("合法出口立即按 `recommendedNextSkill` 继续", autodev)
        self.assertIn("不询问“是否结束探索”或“是否继续”", plan)
        self.assertIn("完整 Dev 工作流已路由到本节点时，视为已包含写入意图", plan)
        self.assertIn("不先追问“是否开始讨论”", discuss)
        self.assertIn("不询问是否进入下一阶段", discuss)
        self.assertIn("确认设计，进入 PLAN 生成 (Recommended)", plan)

        self.assertNotIn("是否结束探索并进入 Plan 生成？", protocol)
        self.assertNotIn("必须询问用户是否结束探索", plan)
        self.assertNotIn("确认讨论当前问题清单", discuss)

    def test_plan_adjudication_gate_forbids_placeholder_options(self) -> None:
        content = PLAN_SKILL.read_text(encoding="utf-8")

        missing = missing_rules(content, extra=("裁定即消解", "信息实体"))
        self.assertEqual(missing, [], "plan 裁定门缺少条款: " + ", ".join(missing))

        self.assertNotIn("「以假设固化：<假设>」", content)

        protocol = PROTOCOL.read_text(encoding="utf-8")
        self.assertIn("逐条裁定环节禁止使用延后类预设选项", protocol)

    def test_prd_and_specs_gates_match_plan_strictness(self) -> None:
        for skill in (PRD_SKILL, SPECS_SKILL):
            content = skill.read_text(encoding="utf-8")
            missing = missing_rules(content)
            self.assertEqual(
                missing,
                [],
                f"{skill.name} 裁定门缺少与 plan 对齐的条款: " + ", ".join(missing),
            )

    def test_prd_gate_does_not_number_pending_items(self) -> None:
        content = PRD_SKILL.read_text(encoding="utf-8")

        # 裁定逐条进行，条目自身不再编号；`id` 由内容概括而来。
        self.assertNotIn("PRD-001", content)
        self.assertIn("同一决策去重后逐条裁定", content)
        self.assertIn("`id` 用条目内容的简短 snake_case 概括", content)

    def test_specs_only_adjudicates_open_questions(self) -> None:
        content = SPECS_SKILL.read_text(encoding="utf-8")

        for required_rule in (
            "探索结束时先生成待确认问题清单",
            "仅裁定讨论表中的待确认条目",
            # 「不把切分、命名或规格范围交给用户确认」不在此列：AGENTS.md 把该句
            # 本身列为技能编写反模式（不要把需求或改动目的写入技能）。同一语义由
            # 下一条「全部条目裁定后直接生成 proposal 与 specs」所在行的
            # 「不再确认 capability 切分或规格范围」承载。
            "全部条目裁定后直接生成 proposal 与 specs",
            "不得使用「已准备好，稍后提供」",
        ):
            self.assertIn(required_rule, content)

        for forbidden_rule in (
            "等待用户确认规格范围",
            "整体确认门",
            "确认范围并生成 specs",
        ):
            self.assertNotIn(forbidden_rule, content)

    def test_every_usage_loads_the_shared_protocol(self) -> None:
        missing: list[str] = []

        for path in sorted(SKILLS.rglob("*.md")):
            if path == PROTOCOL:
                continue
            content = path.read_text(encoding="utf-8")
            if "request_user_input" in content and "ask-user-question.md" not in content:
                missing.append(str(path.relative_to(ROOT)))

        self.assertEqual([], missing, f"未加载共享提问协议: {missing}")


if __name__ == "__main__":
    unittest.main()
