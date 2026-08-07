"""模板说明不得残留进产物；这条检查跨四种产物、五个调用点，却一直没有测试。

`validate_no_template_guidance` 挂在 proposal / spec / design / PLAN 上，
横跨 dev.specs 与 dev.plan 两个节点。它的核心 `find_template_guidance_residue`
是一个手写围栏状态机：支持 ``` 与 ~~~ 两种围栏、要求闭合围栏长度不短于开启
围栏、解析 info string、并用 `seen_content` 区分「文件整体被包进 ```markdown」
与「正文中间的示例围栏」。这种状态机没测试就等于随时可以被改坏而不自知。

原测试随 `a6868a2`（D 式清理）一并删除，但被删的是测试、不是被测的代码——
该 commit 明确保留了这条检查。本文件补回覆盖，并把 `design_has_pending_cells`
的误报边界一起钉住（旧测试里有一条 `test_enum_prose_does_not_false_positive`，
说明历史上踩过）。
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
    PENDING_CELL,
    HookContext,
    find_template_guidance_residue,
    validate_no_template_guidance,
)
from hooks.init_workspace import create_feature, init_workspace  # noqa: E402


def kinds(text: str) -> list[str]:
    return [kind for _, kind in find_template_guidance_residue(text)]


class BlockquoteResidueTest(unittest.TestCase):
    def test_blockquote_is_residue(self) -> None:
        self.assertEqual(kinds("正文\n\n> 模板说明：这里填写理由\n"), ["blockquote"])

    def test_indented_blockquote_is_residue(self) -> None:
        self.assertEqual(kinds("正文\n\n   > 缩进三格仍是引用块\n"), ["blockquote"])

    def test_four_space_indent_is_code_not_blockquote(self) -> None:
        self.assertEqual(kinds("正文\n\n    > 缩进四格是代码块\n"), [])

    def test_blockquote_inside_fence_passes(self) -> None:
        text = "正文\n\n```markdown\n> 这是围栏里的示例，不是残留\n```\n"
        self.assertEqual(kinds(text), [])

    def test_comparison_operator_in_fence_passes(self) -> None:
        text = "正文\n\n```bash\nif [ $n > 3 ]; then echo hi; fi\n```\n"
        self.assertEqual(kinds(text), [])

    def test_blockquote_after_fence_closes_is_residue(self) -> None:
        """围栏闭合后状态机必须回到正常模式，否则后面的残留会被漏掉。"""
        text = "正文\n\n```bash\necho hi\n```\n\n> 闭合之后的引用块\n"
        self.assertEqual(kinds(text), ["blockquote"])


class FenceStateMachineTest(unittest.TestCase):
    def test_outer_markdown_fence_is_residue(self) -> None:
        text = "```markdown\n# 技术设计\n\n内容\n```\n"
        self.assertEqual(kinds(text), ["outer_markdown_fence"])

    def test_md_info_string_also_counts(self) -> None:
        self.assertEqual(kinds("```md\n内容\n```\n"), ["outer_markdown_fence"])

    def test_markdown_fence_after_content_is_a_normal_example(self) -> None:
        """正文之后的 ```markdown 是举例，不是把整个产物包起来。"""
        text = "# 技术设计\n\n示例：\n\n```markdown\n### 标题\n```\n"
        self.assertEqual(kinds(text), [])

    def test_tilde_fence_hides_blockquote(self) -> None:
        text = "正文\n\n~~~\n> 波浪线围栏里的引用块\n~~~\n"
        self.assertEqual(kinds(text), [])

    def test_shorter_run_does_not_close_longer_fence(self) -> None:
        """```` 开启的围栏不能被 ``` 关掉，否则后半段会被误判为正文。"""
        text = "正文\n\n````\n```\n> 仍在围栏内\n````\n"
        self.assertEqual(kinds(text), [])

    def test_backtick_fence_not_closed_by_tilde(self) -> None:
        text = "正文\n\n```\n~~~\n> 仍在围栏内\n```\n"
        self.assertEqual(kinds(text), [])


class WrapperHeadingTest(unittest.TestCase):
    def test_design_wrapper_heading_is_residue(self) -> None:
        self.assertEqual(kinds("# 技术设计模板\n\n内容\n"), ["wrapper_heading"])

    def test_plan_wrapper_heading_is_residue(self) -> None:
        self.assertEqual(kinds("# 计划模板\n\n内容\n"), ["wrapper_heading"])

    def test_real_title_is_not_residue(self) -> None:
        self.assertEqual(kinds("# 技术设计: 订单导出\n\n内容\n"), [])


class AutodevTemplatesAreCleanTest(unittest.TestCase):
    """autodev 自己发的模板不能含有会被这条检查拦下的内容。

    模板教的写法必须正好是校验器放行的写法。模板本身带引用块，就等于
    每个照着模板写的产物都会在 postcheck 被自己的检查拦住。
    """

    TEMPLATES = (
        "skills/autodev/autodev-specs/templates/proposal.md",
        "skills/autodev/autodev-specs/templates/spec.md",
        "skills/autodev/autodev-plan/templates/design.md",
        "skills/autodev/autodev-plan/templates/plan.md",
    )

    def test_output_templates_have_no_residue(self) -> None:
        for relative in self.TEMPLATES:
            path = ROOT / relative
            with self.subTest(template=relative):
                self.assertTrue(path.is_file(), f"模板缺失: {relative}")
                self.assertEqual(
                    kinds(path.read_text(encoding="utf-8")), [], f"{relative} 含模板残留"
                )


class ValidateNoTemplateGuidanceTest(unittest.TestCase):
    def _run(self, body: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve() / "demo"
            project.mkdir()
            init_workspace(project)
            create_feature(project, "alpha")
            ctx = HookContext(skill="autodev-plan", slug="alpha", root=project)
            path = ctx.feature_dir / "design.md"
            path.write_text(body, encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                failures = validate_no_template_guidance(ctx, path, body)
            return failures, buffer.getvalue()

    def test_clean_artifact_passes(self) -> None:
        failures, _ = self._run("# 技术设计: 导出\n\n## 决策\n\n- 用队列\n")
        self.assertEqual(failures, 0)

    def test_residue_reports_relative_path_line_and_kind(self) -> None:
        failures, output = self._run("# 技术设计: 导出\n\n> 填写理由\n")
        self.assertEqual(failures, 1)
        self.assertIn("artifact_template_guidance_residue", output)
        self.assertIn("'design.md'", output)
        self.assertIn("line=3", output)
        self.assertIn("kind=blockquote", output)
        self.assertIn("POST_SKILL_REPAIR", output)

    def test_each_residue_line_reports_separately(self) -> None:
        failures, _ = self._run("# 技术设计: 导出\n\n> 一\n\n> 二\n")
        self.assertEqual(failures, 2)


class PendingCellTest(unittest.TestCase):
    """`待确认` 必须是整个单元格才算未决，出现在散文或枚举里不算。"""

    def test_pending_cell_matches(self) -> None:
        self.assertTrue(PENDING_CELL.search("| D-001 | 存储选型 | 待确认 |\n"))

    def test_reading_diff_cell_matches(self) -> None:
        self.assertTrue(PENDING_CELL.search("| D-002 | 接口 | 读码差异 |\n"))

    def test_enum_prose_in_cell_does_not_match(self) -> None:
        self.assertIsNone(PENDING_CELL.search("| D-003 | 状态取值: 风险/待确认/已确认 | 已确认 |\n"))

    def test_pending_outside_table_does_not_match(self) -> None:
        self.assertIsNone(PENDING_CELL.search("该决策原为待确认，现已定稿。\n"))
