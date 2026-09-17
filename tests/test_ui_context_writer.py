from __future__ import annotations

import unittest

from hooks.ui_context_writer import _generate_ui_context_md


class UIContextMarkdownTests(unittest.TestCase):
    def test_backend_only_context_renders_decision_and_reason(self) -> None:
        markdown = _generate_ui_context_md(
            {
                "uiRequired": False,
                "decisionStatus": "confirmed",
                "decisionSource": "user_confirmed",
                "notApplicableReason": "本期仅实现后端接口",
                "pages": [],
                "interactions": [],
                "visualSources": [],
                "capabilities": [],
            },
            "backend-only",
        )

        self.assertIn("## UI 范围决策", markdown)
        self.assertIn("是否需要 UI 实现：否", markdown)
        self.assertIn("决策状态：confirmed", markdown)
        self.assertIn("决策来源：user_confirmed", markdown)
        self.assertIn("不适用原因：本期仅实现后端接口", markdown)
        self.assertIn("本 Feature 不包含前端交付", markdown)
        self.assertNotIn("## 视觉资源与还原路径", markdown)
        self.assertNotIn("## 页面与能力映射", markdown)

    def test_ui_context_keeps_frontend_details(self) -> None:
        markdown = _generate_ui_context_md(
            {
                "uiRequired": True,
                "decisionStatus": "confirmed",
                "decisionSource": "user_confirmed",
                "pages": [],
                "interactions": [],
                "visualSources": [],
                "capabilities": [],
            },
            "frontend",
        )

        self.assertIn("是否需要 UI 实现：是", markdown)
        self.assertIn("## 视觉资源与还原路径", markdown)
        self.assertIn("## 页面与能力映射", markdown)


if __name__ == "__main__":
    unittest.main()
