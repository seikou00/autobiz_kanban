from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
PROTOCOL = SKILLS / "references" / "ask-user-question.md"
DISCUSS_SKILL = SKILLS / "autobiz" / "autobiz-requirement-discuss" / "SKILL.md"


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
            "如需补充其他问题，请直接在客户端自动提供的「其他」中填写问题内容",
            "不得再次询问“请补充说明”",
            "不得生成「现在提供」「提供路径」「补充说明」等空动作选项",
            "自由文本补充类问题仍必须提供至少 2 个预设选项",
            "同一轮不得再次追问该内容",
        ):
            self.assertIn(required_rule, content)

        self.assertNotIn("**选项2**：补充其他问题", content)
        self.assertNotIn("引导用户补充说明", content)

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
