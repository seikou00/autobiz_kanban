from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
PROTOCOL = SKILLS / "references" / "ask-user-question.md"


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
        ):
            self.assertIn(required_rule, content)

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
