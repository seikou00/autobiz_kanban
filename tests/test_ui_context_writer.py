from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from hooks.ui_context_writer import _generate_ui_context_md, main, refresh_ui_context_md


class UIContextMarkdownTests(unittest.TestCase):
    @staticmethod
    def _ui_context(**overrides: object) -> dict:
        context = {
            "uiRequired": True,
            "decisionStatus": "confirmed",
            "decisionSource": "user_confirmed",
            "pages": [],
            "interactions": [],
            "visualSources": [],
            "capabilities": [],
        }
        context.update(overrides)
        return context

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

    def test_pre_plan_context_uses_one_page_mapping_section_and_one_route_guide(self) -> None:
        markdown = _generate_ui_context_md(
            self._ui_context(),
            "frontend",
        )

        self.assertIn("是否需要 UI 实现：是", markdown)
        self.assertIn("正式 Plan 尚未生成", markdown)
        self.assertNotIn("Plan 提供任务绑定", markdown)
        self.assertEqual(markdown.count("## PAGE / UIX / VIS 映射"), 1)
        self.assertNotIn("关联依据", markdown)
        self.assertNotIn("## TASK / PAGE / UIX / VIS 映射", markdown)
        self.assertEqual(markdown.count("### 还原路径说明"), 1)
        self.assertNotIn("## 视觉资源与还原路径", markdown)
        self.assertNotIn("## 页面与能力映射", markdown)

    def test_pre_plan_mapping_uses_capability_scope_without_task_columns(self) -> None:
        markdown = _generate_ui_context_md(
            self._ui_context(
                pages=[
                    {
                        "pageId": "PAGE-001", "name": "课程首页", "goal": "查看课程",
                        "routeHint": "/courses", "states": ["默认", "空态"],
                    },
                    {"pageId": "PAGE-002", "name": "课程详情", "goal": "查看详情"},
                ],
                interactions=[
                    {"interactionId": "UIX-001", "pageId": "PAGE-001", "summary": "筛选课程"},
                    {"interactionId": "UIX-002", "pageId": "PAGE-002", "summary": "打开详情"},
                    {"interactionId": "UIX-003", "pageId": "PAGE-001", "summary": "未绑定交互"},
                ],
                visualSources=[
                    {
                        "sourceId": "VIS-001", "type": "standard_html", "path": "list.html",
                        "route": "standard-html", "required": True,
                    },
                    {
                        "sourceId": "VIS-002", "type": "image", "path": "detail.png",
                        "route": "spec-driven-ui", "required": False,
                    },
                    {
                        "sourceId": "VIS-003", "type": "image", "path": "orphan.png",
                        "route": "spec-driven-ui", "required": False,
                    },
                ],
                capabilities=[
                    {
                        "capabilityId": "empty-capability", "uiRequired": True,
                        "pageRefs": [], "interactionRefs": [], "visualSourceRefs": [],
                    },
                    {
                        "capabilityId": "detail-view", "uiRequired": True,
                        "pageRefs": ["PAGE-002"], "interactionRefs": ["UIX-002"],
                        "visualSourceRefs": ["VIS-002"],
                    },
                    {
                        "capabilityId": "course-filter", "uiRequired": True,
                        "pageRefs": ["PAGE-001"], "interactionRefs": ["UIX-001"],
                        "visualSourceRefs": ["VIS-001"],
                    },
                ],
            ),
            "frontend",
        )

        self.assertIn(
            "| PAGE ID | 页面名称 | VIS ID | 视觉资源路径 | UIX ID | 交互名称 | 还原路径 |",
            markdown,
        )
        self.assertNotIn("关联依据", markdown)
        self.assertNotIn("capability", markdown)
        self.assertNotIn("specRefs", markdown)
        self.assertNotIn("| TASK ID |", markdown)
        self.assertNotIn("未关联Task", markdown)
        self.assertNotIn("待Plan阶段关联", markdown)
        self.assertNotIn("empty-capability", markdown)
        page_001_rows = [line for line in markdown.splitlines() if line.startswith("| PAGE-001 |")]
        page_002_rows = [line for line in markdown.splitlines() if line.startswith("| PAGE-002 |")]
        self.assertEqual(len(page_001_rows), 1)
        self.assertEqual(len(page_002_rows), 1)
        self.assertLess(markdown.index(page_001_rows[0]), markdown.index(page_002_rows[0]))
        self.assertIn("课程首页", page_001_rows[0])
        self.assertIn("VIS-001", page_001_rows[0])
        self.assertIn("list.html", page_001_rows[0])
        self.assertIn("UIX-001", page_001_rows[0])
        self.assertIn("筛选课程", page_001_rows[0])
        self.assertNotIn("VIS-003", page_001_rows[0])
        self.assertNotIn("UIX-003", page_001_rows[0])
        unassigned_rows = [line for line in markdown.splitlines() if line.startswith("| 待specs阶段归属 |")]
        self.assertEqual(len(unassigned_rows), 1)
        self.assertIn("VIS-003", unassigned_rows[0])
        self.assertIn("orphan.png", unassigned_rows[0])
        self.assertIn("UIX-003", unassigned_rows[0])
        self.assertIn("未绑定交互", unassigned_rows[0])
        self.assertNotIn("未明确页面归属", markdown)
        for removed_detail in ("目标=", "路由=", "状态=", "type=", "required="):
            self.assertNotIn(removed_detail, markdown)

    def test_specs_done_keeps_unbound_scope_explicitly_unassigned(self) -> None:
        markdown = _generate_ui_context_md(
            self._ui_context(
                decisionStatus="locked",
                lockedAtCheckpoint="specs_done",
                pages=[{"pageId": "PAGE-001", "name": "课程首页", "goal": "查看课程"}],
                interactions=[
                    {"interactionId": "UIX-001", "pageId": "PAGE-001", "summary": "待关联交互"},
                ],
                visualSources=[
                    {
                        "sourceId": "VIS-001", "type": "image", "path": "orphan.png",
                        "route": "spec-driven-ui", "required": False,
                    },
                ],
            ),
            "frontend",
        )

        self.assertIn("| 未明确页面归属 |", markdown)
        self.assertNotIn("关联依据", markdown)
        self.assertNotIn("capability", markdown)
        self.assertNotIn("specRefs", markdown)
        self.assertNotIn("待specs阶段归属", markdown)

    def test_plan_mapping_is_page_primary_with_split_name_columns_and_aggregated_values(self) -> None:
        markdown = _generate_ui_context_md(
            self._ui_context(
                pages=[
                    {
                        "pageId": "PAGE-001", "name": "课程首页", "goal": "查看课程",
                        "routeHint": "/courses", "states": ["默认", "空态"],
                    },
                    {"pageId": "PAGE-002", "name": "课程详情", "goal": "查看详情"},
                ],
                interactions=[
                    {"interactionId": "UIX-002", "pageId": "PAGE-002", "summary": "打开详情"},
                    {"interactionId": "UIX-001", "pageId": "PAGE-001", "summary": "筛选课程"},
                    {"interactionId": "UIX-003", "pageId": "PAGE-001", "summary": "翻页"},
                ],
                visualSources=[
                    {
                        "sourceId": "VIS-002", "type": "image", "path": "detail.png",
                        "route": "spec-driven-ui", "required": False,
                    },
                    {
                        "sourceId": "VIS-001", "type": "standard_html", "path": "list.html",
                        "route": "standard-html", "required": True,
                    },
                    {
                        "sourceId": "VIS-003", "type": "image", "path": "pager.png",
                        "route": "spec-driven-ui", "required": False,
                    },
                ],
                capabilities=[
                    {
                        "capabilityId": "detail-view", "uiRequired": True,
                        "pageRefs": ["PAGE-002"], "interactionRefs": ["UIX-002"],
                        "visualSourceRefs": ["VIS-002"],
                    },
                    {
                        "capabilityId": "course-filter", "uiRequired": True,
                        "pageRefs": ["PAGE-001"], "interactionRefs": ["UIX-001"],
                        "visualSourceRefs": ["VIS-001"], "specRefs": ["REQ-001"],
                    },
                    {
                        "capabilityId": "course-pagination", "uiRequired": True,
                        "pageRefs": ["PAGE-001"], "interactionRefs": ["UIX-003"],
                        "visualSourceRefs": ["VIS-003"],
                    },
                ],
            ),
            "frontend",
            tasks=[
                {
                    "id": "T002", "title": "实现详情页", "uiRequired": True,
                    "uiRefs": {
                        "pageRefs": ["PAGE-002"], "interactionRefs": ["UIX-002"],
                        "visualSourceRefs": ["VIS-002"], "frontendRoute": "spec-driven-ui",
                    },
                },
                {
                    "id": "T001", "title": "实现课程筛选", "uiRequired": True,
                    "specRefs": ["REQ-001"],
                    "uiRefs": {
                        "pageRefs": ["PAGE-001"], "interactionRefs": ["UIX-001"],
                        "visualSourceRefs": ["VIS-001"], "frontendRoute": "standard-html",
                    },
                },
                {
                    "id": "T003", "title": "实现课程翻页", "uiRequired": True,
                    "uiRefs": {
                        "pageRefs": ["PAGE-001"], "interactionRefs": ["UIX-003"],
                        "visualSourceRefs": ["VIS-003"], "frontendRoute": "spec-driven-ui",
                    },
                },
            ],
        )

        self.assertIn(
            "| PAGE ID | 页面名称 | TASK ID | 任务名称 | VIS ID | 视觉资源路径 | "
            "UIX ID | 交互名称 | 还原路径 |",
            markdown,
        )
        self.assertNotIn("关联依据", markdown)
        self.assertNotIn("capability", markdown)
        self.assertNotIn("specRefs", markdown)
        self.assertNotIn("uiRefs", markdown)
        page_001_rows = [line for line in markdown.splitlines() if line.startswith("| PAGE-001 |")]
        page_002_rows = [line for line in markdown.splitlines() if line.startswith("| PAGE-002 |")]
        self.assertEqual(len(page_001_rows), 1)
        self.assertEqual(len(page_002_rows), 1)
        self.assertLess(markdown.index(page_001_rows[0]), markdown.index(page_002_rows[0]))
        page_001 = page_001_rows[0]
        self.assertEqual(
            [cell.strip() for cell in page_001.strip("|").split("|")][:8],
            [
                "PAGE-001", "课程首页",
                "T001<br>T003", "实现课程筛选<br>实现课程翻页",
                "VIS-001<br>VIS-003", "list.html<br>pager.png",
                "UIX-001<br>UIX-003", "筛选课程<br>翻页",
            ],
        )
        for value in (
            "课程首页",
            "VIS-001", "VIS-003", "list.html", "pager.png",
            "UIX-001", "UIX-003", "筛选课程", "翻页",
            "T001", "T003", "实现课程筛选", "实现课程翻页",
        ):
            self.assertIn(value, page_001)
        self.assertNotIn("PAGE-001:", page_001)
        self.assertNotIn("T001:", page_001)
        self.assertNotIn("UIX-001:", page_001)
        self.assertNotIn("VIS-001:", page_001)
        for removed_detail in ("目标=", "路由=", "状态=", "type=", "required="):
            self.assertNotIn(removed_detail, markdown)

    def test_refresh_switches_to_task_mapping_after_formal_plan_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            state_path = workspace / ".autobizdevops" / "state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text("{}\n", encoding="utf-8")

            commands = [
                ["init", "--workspace", str(workspace), "--feature", "alpha", "--ui-required"],
                [
                    "add-page", "--workspace", str(workspace), "--feature", "alpha",
                    "--page-id", "PAGE-001", "--name", "课程首页", "--goal", "查看课程",
                ],
                [
                    "add-interaction", "--workspace", str(workspace), "--feature", "alpha",
                    "--interaction-id", "UIX-001", "--page-id", "PAGE-001", "--summary", "筛选课程",
                ],
                [
                    "add-capability", "--workspace", str(workspace), "--feature", "alpha",
                    "--capability-id", "course-filter", "--page-ref", "PAGE-001",
                    "--interaction-ref", "UIX-001", "--ui-required", "true",
                ],
            ]
            for command in commands:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(command), 0)

            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            markdown_path = feature_dir / "UI_CONTEXT.md"
            before_plan = markdown_path.read_text(encoding="utf-8")
            self.assertIn("## PAGE / UIX / VIS 映射", before_plan)
            self.assertNotIn("TASK ID", before_plan)

            (feature_dir / "plan.json").write_text(json.dumps({
                "tasks": [
                    {
                        "id": "T001", "title": "实现课程筛选", "uiRequired": True,
                        "uiRefs": {
                            "pageRefs": ["PAGE-001"], "interactionRefs": ["UIX-001"],
                            "visualSourceRefs": [], "frontendRoute": "spec-driven-ui",
                        },
                    },
                ],
            }), encoding="utf-8")
            self.assertTrue(refresh_ui_context_md(workspace, "alpha"))
            after_plan = markdown_path.read_text(encoding="utf-8")

        self.assertIn("## TASK / PAGE / UIX / VIS 映射", after_plan)
        self.assertIn("TASK ID", after_plan)
        self.assertIn("T001", after_plan)
        self.assertIn("实现课程筛选", after_plan)
        self.assertIn("Plan 提供任务绑定", after_plan)
        self.assertNotIn("正式 Plan 尚未生成", after_plan)
        self.assertNotIn("## PAGE / UIX / VIS 映射", after_plan)

    def test_formal_plan_without_ui_task_uses_unassigned_task_instead_of_pre_plan_placeholder(self) -> None:
        markdown = _generate_ui_context_md(
            self._ui_context(
                pages=[{"pageId": "PAGE-001", "name": "课程首页", "goal": "查看课程"}],
                capabilities=[
                    {
                        "capabilityId": "course-view", "uiRequired": True,
                        "pageRefs": ["PAGE-001"], "interactionRefs": [],
                        "visualSourceRefs": [],
                    },
                ],
            ),
            "frontend",
            tasks=[],
        )

        self.assertIn("## TASK / PAGE / UIX / VIS 映射", markdown)
        self.assertIn("未关联Task", markdown)
        self.assertNotIn("待Plan阶段关联", markdown)

    def test_empty_capability_is_hidden_but_pending_page_scope_is_kept(self) -> None:
        markdown = _generate_ui_context_md(
            self._ui_context(
                pages=[{"pageId": "PAGE-001", "name": "课程首页", "goal": "查看课程"}],
                interactions=[
                    {"interactionId": "UIX-001", "pageId": "PAGE-001", "summary": "筛选课程"},
                ],
                capabilities=[
                    {
                        "capabilityId": "empty-capability", "uiRequired": True,
                        "pageRefs": [], "interactionRefs": [], "visualSourceRefs": [],
                    },
                    {
                        "capabilityId": "pending-filter", "uiRequired": True,
                        "pageRefs": ["PAGE-001"], "interactionRefs": ["UIX-001"],
                        "visualSourceRefs": [],
                    },
                ],
            ),
            "frontend",
            tasks=[{"id": "T999", "title": "后端任务", "uiRequired": False}],
        )

        self.assertNotIn("empty-capability", markdown)
        self.assertNotIn("pending-filter", markdown)
        self.assertIn("| PAGE-001 | 课程首页 |", markdown)
        self.assertIn("UIX-001", markdown)
        self.assertIn("未关联Task", markdown)
        self.assertNotIn(
            "| 未关联Task | 未明确页面归属 | 无任务交互 | 无视觉资源 |",
            markdown,
        )

    def test_legacy_prototype_link_is_hidden_without_hiding_unknown_visual_reference(self) -> None:
        markdown = _generate_ui_context_md(
            self._ui_context(
                pages=[{"pageId": "PAGE-001", "name": "课程首页", "goal": "查看课程"}],
                visualSources=[
                    {
                        "sourceId": "VIS-001", "type": "prototype_link",
                        "path": "https://prototype.example/page", "route": "spec-driven-ui",
                        "required": True,
                    },
                ],
                capabilities=[
                    {
                        "capabilityId": "course-view", "uiRequired": True,
                        "pageRefs": ["PAGE-001"], "interactionRefs": [],
                        "visualSourceRefs": ["VIS-001"],
                    },
                ],
            ),
            "frontend",
            tasks=[
                {
                    "id": "T001", "title": "实现课程页", "uiRequired": True,
                    "uiRefs": {
                        "pageRefs": ["PAGE-001"], "interactionRefs": [],
                        "visualSourceRefs": ["VIS-001", "VIS-999"],
                        "frontendRoute": "spec-driven-ui",
                    },
                },
            ],
        )

        self.assertNotIn("VIS-001", markdown)
        self.assertNotIn("prototype.example", markdown)
        self.assertIn("VIS-999", markdown)
        self.assertIn("未定义", markdown)

    def test_writer_rejects_new_prototype_link_visual_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            state_path = workspace / ".autobizdevops" / "state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text("{}\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([
                    "init", "--workspace", str(workspace), "--feature", "alpha", "--ui-required",
                ]), 0)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main([
                    "add-visual-source", "--workspace", str(workspace), "--feature", "alpha",
                    "--type", "prototype_link", "--path", "https://prototype.example/page",
                    "--route", "spec-driven-ui", "--required", "true",
                ])

            payload = json.loads(output.getvalue())
            context = json.loads(
                (workspace / ".autobizdevops" / "features" / "alpha" / "UI_CONTEXT.json")
                .read_text(encoding="utf-8")
            )

        self.assertEqual(result, 1)
        self.assertEqual(payload["errors"][0]["reason"], "visual_source_type_unsupported")
        self.assertEqual(context["visualSources"], [])


if __name__ == "__main__":
    unittest.main()
