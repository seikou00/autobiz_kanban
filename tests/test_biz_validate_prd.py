from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.state_store import write_state_records  # noqa: E402
from skills.autobiz.hooks.biz_validate import (  # noqa: E402
    BIZ_VALIDATE_WORKSPACE_ARGUMENT_ERROR,
    validate_prd,
)


def sample_record(checkpoint: str = "prd_done") -> dict[str, str]:
    return {
        "checkpoint": checkpoint,
        "owner": "tester",
        "iteration": "1",
        "updated_at": "2026-06-03 12:00:00",
    }


# 技能正文（SKILL.md §讨论沉淀生成 + references/prd_module.md）实际产出的结构。
# 校验只认技能正文能推导出的约束，其余章节自由。
VALID_PRD = """# 需求摘要

## 当前实现范围

- 实现范围：backend_only

## 1.需求概述

### 1.1.背景、痛点、改进思路及价值

审批人需要快速识别异常付款。

## 2.需求解析

### 2.6. 功能清单

| 类型 | 功能名称 | 功能描述 |
| --- | --- | --- |
| 新增 | 异常标记 | 审批列表展示异常标记 |

#### 2.6.1. 功能详情

##### FR1: 异常标记

###### 验收标准

- 当单据满足异常条件时，列表展示异常标记。

### 2.7. 外部资料与实现约束

无

## 当前已确认结论

- 本期只处理审批列表异常标记。

## 问题清单与处理状态

| 序号 | 重要性 | 检查项 | 处理状态 |
| --- | --- | --- | --- |
| 1 | P2 - 优化建议 | 上线窗口 | 已确认按建议处理 |

## 待确认事项

无（本轮已全部消解）。

## 假设与风险

- 假设异常标记由后端字段提供。

## 历次讨论记录

- 2026-06-03: 用户确认先生成 PRD。

## 讨论补充资料

无
"""


PRD_WITH_SOURCE = VALID_PRD.replace(
    "### 2.7. 外部资料与实现约束\n\n无",
    """### 2.7. 外部资料与实现约束

| ID | 类型 | 名称 | 地址/路径 | 约束范围 | 必读阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| SRC-001 | 外部接口 | 支付接口 | sources/SRC-001/payment.md | 支付超时与降级 | Specs、Plan、Code、Reviewer、E2E | snapshot_only |""",
)


def with_source_table(rows: str) -> str:
    return VALID_PRD.replace(
        "### 2.7. 外部资料与实现约束\n\n无",
        "### 2.7. 外部资料与实现约束\n\n"
        "| ID | 类型 | 名称 | 地址/路径 | 约束范围 | 必读阶段 | 状态 |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n" + rows,
    )


class BizValidatePrdTests(unittest.TestCase):
    def make_workspace(self, prd_content: str) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        workspace = Path(tempdir.name)
        feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
        feature_dir.mkdir(parents=True)
        (feature_dir / "PRD.md").write_text(prd_content, encoding="utf-8")
        write_state_records(workspace, {"alpha": sample_record("prd_done")})
        return workspace

    def plugin_env(self, workspace: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["PLUGIN_WORKSPACE"] = str(workspace.parent)
        env["PROJECT_CODE"] = workspace.name
        env["FEATURE_ID"] = "alpha"
        env.pop("PLUGIN_OUTPUT_DIR", None)
        return env

    def write_source_context(self, workspace: Path) -> None:
        feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
        snapshot = feature_dir / "sources" / "SRC-001" / "payment.md"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text("支付接口调用超时时间为 3 秒。", encoding="utf-8")
        context = {
            "version": 1,
            "sources": [
                {
                    "id": "SRC-001",
                    "name": "支付网关 API",
                    "path": "sources/SRC-001/payment.md",
                    "availability": "snapshot_only",
                    "readStatus": "complete",
                    "freshness": "unknown",
                    "items": [
                        {
                            "id": "SRC-001-I001",
                            "location": "第 1 行",
                            "original": "支付接口调用超时时间为 3 秒。",
                            "disposition": "requirement",
                            "requirements": [
                                {
                                    "id": "SRC-001-R001",
                                    "text": "支付接口调用超时时间为 3 秒",
                                    "targets": ["spec", "design", "plan", "code", "reviewer", "e2e"],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        (feature_dir / "source-context.json").write_text(
            json.dumps(context, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def run_biz_validate(
        self,
        *args: str,
        workspace: Path,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(ROOT / "skills" / "autobiz" / "hooks" / "biz_validate.py"),
            *args,
        ]
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            cwd=str(cwd or ROOT),
            env=env or self.plugin_env(workspace),
        )

    def test_cli_uses_plugin_workspace_and_project_code_from_any_cwd(self) -> None:
        workspace = self.make_workspace(VALID_PRD)
        unrelated = tempfile.TemporaryDirectory()
        self.addCleanup(unrelated.cleanup)

        result = self.run_biz_validate(
            "prd",
            "--feature",
            "alpha",
            workspace=workspace,
            cwd=Path(unrelated.name),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("prd 阶段产出物校验通过", result.stdout)

    def test_cli_requires_plugin_workspace_and_project_code(self) -> None:
        workspace = self.make_workspace(VALID_PRD)

        for key in ("PLUGIN_WORKSPACE", "PROJECT_CODE"):
            with self.subTest(key=key):
                env = self.plugin_env(workspace)
                env.pop(key)

                result = self.run_biz_validate("prd", "--feature", "alpha", workspace=workspace, env=env)

                self.assertEqual(result.returncode, 1)
                expected_key = "PROJECT_DIR" if key == "PROJECT_CODE" else key
                self.assertIn(f"{expected_key} 未设置", result.stderr)

    def test_cli_rejects_workspace_argument(self) -> None:
        workspace = self.make_workspace(VALID_PRD)

        for args in (("--workspace", str(workspace)), ("-w", str(workspace))):
            with self.subTest(args=args):
                result = self.run_biz_validate("prd", "--feature", "alpha", *args, workspace=workspace)

                self.assertEqual(result.returncode, 2)
                self.assertIn(BIZ_VALIDATE_WORKSPACE_ARGUMENT_ERROR, result.stderr)

    def test_accepts_prd_written_to_the_skill_template(self) -> None:
        """技能正文产出的结构（# 需求摘要 + 讨论章节）必须直接通过。"""
        workspace = self.make_workspace(VALID_PRD)

        result = validate_prd("alpha", workspace)

        self.assertTrue(result["ok"], result)

    def test_discussion_and_pending_sections_are_allowed(self) -> None:
        """SKILL.md 要求写入的章节不得被校验反过来禁止。"""
        workspace = self.make_workspace(VALID_PRD)

        result = validate_prd("alpha", workspace)

        self.assertIn("## 历次讨论记录", VALID_PRD)
        self.assertIn("## 待确认事项", VALID_PRD)
        self.assertTrue(result["ok"], result)

    def test_any_h1_title_is_accepted(self) -> None:
        for title in ("# 需求摘要", "# 需求正式稿", "# 需求讨论稿"):
            with self.subTest(title=title):
                workspace = self.make_workspace(VALID_PRD.replace("# 需求摘要", title, 1))

                result = validate_prd("alpha", workspace)

                self.assertTrue(result["ok"], result)

    def test_rejects_missing_source_section(self) -> None:
        workspace = self.make_workspace(
            VALID_PRD.replace("### 2.7. 外部资料与实现约束\n\n无\n", "")
        )

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        joined = "\n".join(result["errors"])
        self.assertIn("外部资料与实现约束", joined)
        self.assertIn("修复：", joined)

    def test_rejects_pending_marker_and_reports_line_numbers(self) -> None:
        workspace = self.make_workspace(
            VALID_PRD.replace("审批人需要快速识别异常付款。", "审批人【待确认】需要快速识别异常付款。")
        )

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        joined = "\n".join(result["errors"])
        expected_line = VALID_PRD.split("\n").index("审批人需要快速识别异常付款。") + 1
        self.assertIn(f"第 {expected_line} 行", joined)
        self.assertIn("不得靠删除整段待确认内容通过校验", joined)

    def test_checkpoint_mismatch_is_a_warning_not_a_blocker(self) -> None:
        workspace = self.make_workspace(VALID_PRD)
        write_state_records(workspace, {"alpha": sample_record("prd_in_progress")})

        result = validate_prd("alpha", workspace)

        self.assertTrue(result["ok"], result)
        self.assertIn("checkpoint", "\n".join(result["warnings"]))

    def test_draft_mode_drops_the_checkpoint_warning(self) -> None:
        workspace = self.make_workspace(VALID_PRD)
        write_state_records(workspace, {"alpha": sample_record("prd_in_progress")})

        result = validate_prd("alpha", workspace, draft=True)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["warnings"], [])

    def test_accepts_modified_prd_body_when_structure_is_valid(self) -> None:
        workspace = self.make_workspace(
            VALID_PRD.replace("审批人需要快速识别异常付款。", "审批人需要识别异常付款。", 1)
        )

        result = validate_prd("alpha", workspace)

        self.assertTrue(result["ok"], result)

    def test_external_source_requires_source_context(self) -> None:
        workspace = self.make_workspace(PRD_WITH_SOURCE)

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        joined = "\n".join(result["errors"])
        self.assertIn("必须生成 source-context.json", joined)
        self.assertIn("source_context.py sync", joined)

    def test_accepts_external_interface_source_with_full_stage_contract(self) -> None:
        workspace = self.make_workspace(
            with_source_table(
                "| SRC-001 | 外部接口 | 支付网关 API | https://example.test/openapi | "
                "REQ-001 支付提交 | Specs、Plan、Code、Reviewer、E2E | 可访问 |\n"
            )
        )
        self.write_source_context(workspace)

        result = validate_prd("alpha", workspace)

        self.assertTrue(result["ok"], result)

    def test_rejects_external_interface_source_without_downstream_stages(self) -> None:
        workspace = self.make_workspace(
            with_source_table(
                "| SRC-001 | 外部接口 | 支付网关 API | https://example.test/openapi | "
                "REQ-001 支付提交 | Specs、Plan | 可访问 |\n"
            )
        )

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        self.assertIn("Code、Reviewer、E2E", "\n".join(result["errors"]))

    def test_missing_fields_on_non_interface_source_is_only_a_warning(self) -> None:
        workspace = self.make_workspace(
            with_source_table(
                "| SRC-001 | 原型 | 列表原型 | /tmp/list.html | REQ-001 | Specs、Plan |  |\n"
            )
        )
        self.write_source_context(workspace)

        result = validate_prd("alpha", workspace)

        self.assertTrue(result["ok"], result)
        self.assertIn("缺少字段", "\n".join(result["warnings"]))

    def test_rejects_duplicate_external_source_ids(self) -> None:
        workspace = self.make_workspace(
            with_source_table(
                "| SRC-001 | 原型 | 列表原型 | /tmp/list.html | REQ-001 | Specs、Plan | 可访问 |\n"
                "| SRC-001 | 数据字典 | 付款字典 | /tmp/dict.xlsx | REQ-002 | Specs、Plan | 可访问 |\n"
            )
        )

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        self.assertIn("外部资料 ID 重复", "\n".join(result["errors"]))

    def test_every_error_carries_a_repair_hint(self) -> None:
        """AGENTS.md：脚本报错必须打印修复方式，模型才不用去读 .py 源码。"""
        workspace = self.make_workspace(
            VALID_PRD.replace("### 2.7. 外部资料与实现约束\n\n无\n", "").replace(
                "审批人需要快速识别异常付款。", "审批人【待确认】需要快速识别异常付款。"
            )
        )

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        for error in result["errors"]:
            self.assertIn("修复：", error)


if __name__ == "__main__":
    unittest.main()
