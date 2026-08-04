"""PRD_DISCUSS.md -> PRD.md 搬运脚本：待确认内容保留、其余正文逐字保真。"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
AUTOBIZ_HOOKS_DIR = ROOT / "skills" / "autobiz" / "hooks"
if str(AUTOBIZ_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOBIZ_HOOKS_DIR))

from prd_rules import (  # noqa: E402
    FORMAL_PRD_TITLE,
    iter_headings,
    plan_transplant,
)
import prd_transplant  # noqa: E402


# 贴近真实产物：prd_module.md 的正文结构 + discuss 技能沉淀的六块内容
DISCUSS_FULL = """# 需求摘要

本文档为需求讨论中间稿，用于记录需求讨论过程和结论。

## 1.需求概述

### 1.1.背景、痛点、改进思路及价值（必填）

| 填写项 |  |
| --- | --- |
| 背景/目标 | 报销单审批链路过长 |
| 痛点（必填） | 财务需要人工核对三套系统 |

### 1.2.用户角色表（必填）

| 用户类型 | 说明 | 关注点 |
| --- | --- | --- |
| 申请人 | 提交报销单的员工 | 提交效率 |
| 审批人 | 部门负责人【待确认】 | 审批准确性 |

## 2.需求解析

### 2.6. 功能清单（必填）

| 类型      | 功能名称 | 功能描述 |
| --------- | -------- | -------- |
| 新增 | 报销单提交 | 支持批量上传票据 |

#### 2.6.1. 功能详情
##### FR1: 报销单提交

######  功能概述

**路径**: 工作台 > 报销 > 新建

**适用角色**: 申请人

######  验收标准

- 单次最多上传 20 张票据
- 金额超过 5000 元时必须填写说明

## 当前已确认结论

- 一期只做部门负责人单级审批

## 问题清单与处理状态

| 序号 | 重要性 | 检查项 | 处理状态 |
|------|--------|--------|----------|
| 1 | P0 | 审批层级 | 已确认 |

示例输出格式：

```markdown
## 问题清单

| 序号 | 重要性 |
|------|--------|
| 1 | P0 |
```

## 待确认事项

- 高保真链接待 UED 提供
- 接口文档待后端补充

### 待确认事项明细

- 数据同步机制未定

## 假设与风险

- 假设：财务系统开放了查询接口

## 历次讨论记录

### 2026-07-20 第一轮

- 用户选择：一期只做单级审批
- 确认结论：二期再做多级

### 2026-07-21 第二轮

- 用户补充：票据上限 20 张

## 外部依赖

- 依赖财务中台 v2.3
"""

DISCUSS_RESOLVED = DISCUSS_FULL.replace("部门负责人【待确认】", "部门负责人").replace(
    "## 待确认事项\n\n"
    "- 高保真链接待 UED 提供\n"
    "- 接口文档待后端补充\n\n"
    "### 待确认事项明细\n\n"
    "- 数据同步机制未定\n\n",
    "",
)


class TransplantRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.result = plan_transplant(DISCUSS_FULL)

    def test_h1_replaced_with_formal_title(self) -> None:
        self.assertEqual(self.result.text.splitlines()[0], FORMAL_PRD_TITLE)
        self.assertEqual(self.result.retitled, ("# 需求摘要", FORMAL_PRD_TITLE))
        self.assertFalse(self.result.title_prepended)
        self.assertNotIn("# 需求摘要", self.result.text)

    def test_title_prepended_when_source_has_no_h1(self) -> None:
        result = plan_transplant("\n## 2.需求解析\n\n内容\n")
        self.assertEqual(result.text.splitlines()[0], FORMAL_PRD_TITLE)
        self.assertTrue(result.title_prepended)
        self.assertIsNone(result.retitled)
        self.assertIn("## 2.需求解析", result.text)

    def test_title_untouched_when_already_formal(self) -> None:
        result = plan_transplant(f"{FORMAL_PRD_TITLE}\n\n## 正文\n")
        self.assertIsNone(result.retitled)
        self.assertFalse(result.title_prepended)
        self.assertEqual(result.text, f"{FORMAL_PRD_TITLE}\n\n## 正文\n")

    def test_discussion_log_dropped_with_all_subsections(self) -> None:
        self.assertNotIn("历次讨论记录", self.result.text)
        self.assertNotIn("2026-07-20 第一轮", self.result.text)
        self.assertNotIn("票据上限 20 张", self.result.text)
        # 遇到同级标题（## 外部依赖）就停止，不越界
        titles = [section.title for section in self.result.dropped_sections]
        self.assertIn("历次讨论记录", titles)
        self.assertIn("外部依赖", titles)

    def test_non_pending_forbidden_sections_dropped(self) -> None:
        for dropped in ("外部依赖", "依赖财务中台"):
            self.assertNotIn(dropped, self.result.text)

    def test_pending_sections_preserved_for_prd_resolution(self) -> None:
        for retained in ("待确认事项", "高保真链接待 UED 提供", "数据同步机制未定"):
            self.assertIn(retained, self.result.text)

    def test_nested_pending_heading_not_double_reported(self) -> None:
        # `### 待确认事项明细` 在 `## 待确认事项` 区间内，只上报外层一次
        titles = [section.title for section in self.result.pending_sections]
        self.assertEqual(titles.count("待确认事项"), 1)
        self.assertNotIn("待确认事项明细", titles)

    def test_confirmed_sections_retained(self) -> None:
        self.assertIn("## 当前已确认结论", self.result.text)
        self.assertIn("一期只做部门负责人单级审批", self.result.text)
        self.assertIn("## 问题清单与处理状态", self.result.text)
        self.assertIn("## 假设与风险", self.result.text)
        self.assertIn("假设：财务系统开放了查询接口", self.result.text)

    def test_fenced_sample_heading_is_not_a_heading(self) -> None:
        # ```markdown 围栏内的 `## 问题清单` 是示例，不能被当成标题
        headings = [heading.text for heading in iter_headings(DISCUSS_FULL)]
        self.assertEqual(headings.count("问题清单"), 0)
        # 围栏整体随「问题清单与处理状态」保留，未被误删
        self.assertIn("```markdown", self.result.text)

    def test_discuss_notice_line_dropped(self) -> None:
        self.assertNotIn("本文档为需求讨论中间稿", self.result.text)
        self.assertEqual(len(self.result.dropped_notices), 1)
        self.assertEqual(self.result.dropped_notices[0][0], 3)

    def test_pending_marker_reported_not_modified(self) -> None:
        self.assertIn("部门负责人【待确认】", self.result.text)
        self.assertEqual(len(self.result.pending_markers), 1)
        line_no, text = self.result.pending_markers[0]
        self.assertEqual(self.result.text.splitlines()[line_no - 1].strip(), text)

    def test_retained_body_is_verbatim(self) -> None:
        """核心不变量：保留的每一行都逐字不变、顺序不变，只有首行标题被替换。"""
        source_lines = DISCUSS_FULL.split("\n")
        dropped = set()
        for section in self.result.dropped_sections:
            dropped.update(range(section.start_line, section.end_line + 1))
        for line_no, _ in self.result.dropped_notices:
            dropped.add(line_no)

        expected = [
            line
            for idx, line in enumerate(source_lines, start=1)
            if idx not in dropped and line.strip() and idx != 1
        ]
        actual = [line for line in self.result.text.split("\n") if line.strip()]
        self.assertEqual(actual[0], FORMAL_PRD_TITLE)
        self.assertEqual(actual[1:], expected)

    def test_retained_blocks_are_contiguous(self) -> None:
        """表格、FR 详情等整块内容不被拆散或重排。"""
        self.assertIn(
            "| 类型      | 功能名称 | 功能描述 |\n"
            "| --------- | -------- | -------- |\n"
            "| 新增 | 报销单提交 | 支持批量上传票据 |",
            self.result.text,
        )
        self.assertIn(
            "######  验收标准\n\n"
            "- 单次最多上传 20 张票据\n"
            "- 金额超过 5000 元时必须填写说明",
            self.result.text,
        )

    def test_fr_level_heading_does_not_satisfy_required_section(self) -> None:
        """功能详情里的 `###### 验收标准` 随正文搬进来，不能顶替正式的 `## 验收标准`。"""
        import biz_validate

        self.assertIn("######  验收标准", self.result.text)
        section_headings = [
            heading.text
            for heading in iter_headings(self.result.text)
            if heading.level <= biz_validate.FORMAL_SECTION_MAX_LEVEL
        ]
        self.assertNotIn("验收标准", section_headings)

    def test_output_passes_prd_heading_rules(self) -> None:
        """搬运结果 + 模型追加的四段，应当通过 biz_validate 的标题类规则。"""
        import biz_validate

        content = plan_transplant(DISCUSS_RESOLVED).text + (
            "\n## 用户故事\n\n- 作为申请人，我希望批量上传票据\n"
            "\n## 验收口径\n\n- 用户视角：可提交\n"
            "\n## 验收标准\n\n- 单次最多 20 张\n"
            "\n## 关键约束\n\n- 仅部门负责人可审批\n"
        )
        all_headings = iter_headings(content)
        headings = [heading.text for heading in all_headings]
        section_headings = [
            heading.text
            for heading in all_headings
            if heading.level <= biz_validate.FORMAL_SECTION_MAX_LEVEL
        ]
        self.assertEqual(content.splitlines()[0].strip(), biz_validate.FORMAL_PRD_TITLE)
        self.assertEqual(
            [h for h in headings if biz_validate.heading_matches(h, biz_validate.DISCUSSION_SECTION_TITLES)],
            [],
        )
        self.assertEqual(
            [
                h
                for h in headings
                if biz_validate.heading_matches(h, biz_validate.FORBIDDEN_PRD_SECTION_TITLES)
            ],
            [],
        )
        missing = [
            s for s in biz_validate.REQUIRED_PRD_SECTIONS
            if not any(s in h for h in section_headings)
        ]
        self.assertEqual(missing, [])


class TransplantCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        plugin_workspace = Path(self._tmp.name)
        self.project_dir = "demo-project"
        self.feature = "expense-approval"
        self.workspace = plugin_workspace / self.project_dir
        autobizdevops = self.workspace / ".autobizdevops"
        self.feature_dir = autobizdevops / "features" / self.feature
        self.feature_dir.mkdir(parents=True)
        (autobizdevops / "state.json").write_text(
            json.dumps({"features": {}}, ensure_ascii=False), encoding="utf-8"
        )
        (self.feature_dir / "PRD_DISCUSS.md").write_text(DISCUSS_FULL, encoding="utf-8")

        for key, value in (
            ("PLUGIN_WORKSPACE", str(plugin_workspace)),
            ("PROJECT_DIR", self.project_dir),
        ):
            previous = os.environ.get(key)
            os.environ[key] = value
            self.addCleanup(self._restore_env, key, previous)

    @staticmethod
    def _restore_env(key: str, previous) -> None:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous

    def _run(self, *args: str):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = prd_transplant.main(["--feature", self.feature, *args])
        return code, buffer.getvalue()

    @property
    def prd_path(self) -> Path:
        return self.feature_dir / "PRD.md"

    def test_writes_prd_and_reports(self) -> None:
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertTrue(self.prd_path.is_file())
        self.assertEqual(
            self.prd_path.read_text(encoding="utf-8"), plan_transplant(DISCUSS_FULL).text
        )
        self.assertIn("[通过]", output)
        self.assertIn("历次讨论记录", output)
        self.assertIn("待确认章节（已保留在 PRD.md", output)
        self.assertIn("【待确认】告警: 1 处", output)
        self.assertIn("## 用户故事", output)

    def test_refuses_existing_prd_without_force(self) -> None:
        self.prd_path.write_text("# 需求正式稿\n\n人工成稿\n", encoding="utf-8")
        code, output = self._run()
        self.assertEqual(code, 1)
        self.assertIn("--force", output)
        self.assertEqual(self.prd_path.read_text(encoding="utf-8"), "# 需求正式稿\n\n人工成稿\n")

    def test_force_overwrites(self) -> None:
        self.prd_path.write_text("# 需求正式稿\n\n人工成稿\n", encoding="utf-8")
        code, _ = self._run("--force")
        self.assertEqual(code, 0)
        self.assertIn("报销单提交", self.prd_path.read_text(encoding="utf-8"))

    def test_missing_discuss_reports_degrade_hint(self) -> None:
        (self.feature_dir / "PRD_DISCUSS.md").unlink()
        code, output = self._run()
        self.assertEqual(code, 1)
        self.assertIn(prd_transplant.MISSING_DISCUSS_HINT, output)
        self.assertFalse(self.prd_path.exists())

    def test_json_output(self) -> None:
        code, output = self._run("--json")
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["feature"], self.feature)
        self.assertEqual(payload["retitled"], ["# 需求摘要", FORMAL_PRD_TITLE])
        self.assertIn("历次讨论记录", [s["title"] for s in payload["dropped_sections"]])
        self.assertEqual(len(payload["pending_sections"]), 1)
        self.assertEqual(len(payload["pending_markers"]), 1)

    def test_rejects_workspace_argument(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = prd_transplant.main(["--workspace", "/tmp", "--feature", self.feature])
        self.assertEqual(code, 2)
        self.assertIn("不接受 --workspace/-w", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
