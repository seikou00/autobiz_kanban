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


DISCUSS_DRAFT_INTRO = "本文档为需求讨论中间稿，用于记录需求讨论过程和结论"


DISCUSS_WITH_HISTORY = f"""# 需求讨论稿

{DISCUSS_DRAFT_INTRO}

## 需求摘要

审批人需要快速识别异常付款。

## 当前已确认结论

- 本期只处理审批列表异常标记。

## 问题清单与处理状态

- P2: 上线窗口待确认。

## 待确认事项

- 待确认上线窗口。

## 外部依赖

- 风控系统提供异常标记字段。

## 假设与风险

- 假设异常标记由后端字段提供。
- 风险：风控系统字段可能延迟。

## 历次讨论记录

- 2026-06-03: 用户确认先生成 PRD。
"""


DISCUSS_PREFIX = DISCUSS_WITH_HISTORY.split("## 历次讨论记录", 1)[0]
FORMAL_PREFIX = DISCUSS_PREFIX.replace("# 需求讨论稿", "# 需求正式稿", 1).replace(
    f"\n{DISCUSS_DRAFT_INTRO}\n\n",
    "\n",
)
FORMAL_PREFIX = FORMAL_PREFIX.replace("## 待确认事项\n\n- 待确认上线窗口。\n\n", "")
FORMAL_PREFIX = FORMAL_PREFIX.replace("## 外部依赖\n\n- 风控系统提供异常标记字段。\n\n", "")


VALID_PRD = FORMAL_PREFIX + """## 用户故事

- 作为财务审批人，我希望在支付审批列表中识别异常单据，以便优先处理高风险付款。

## 验收口径

- 用户视角：审批人能看到异常标记。
- 工程视角：接口返回异常标记字段。
- 回归视角：原有审批状态和分页不受影响。

## 验收标准

- 当单据满足异常条件时，列表展示异常标记。
- 当按异常标记筛选时，只返回符合条件的单据。

## 关键约束

| 类别 | 约束 | 来源/原因 |
|------|------|-----------|
| 数据 | 异常标记由后端字段提供 | 假设与风险 |

## 外部资料与实现约束

无
"""


PRD_WITH_SOURCE = VALID_PRD.replace(
    "## 外部资料与实现约束\n\n无",
    """## 外部资料与实现约束

| ID | 类型 | 名称 | 地址/路径 | 约束范围 | 必读阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| SRC-001 | 外部接口 | 支付接口 | sources/SRC-001/payment.md | 支付超时与降级 | Specs、Plan、Code、Reviewer、E2E | snapshot_only |""",
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
                    "sha256": "0" * 64,
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

    def test_accepts_new_prd_sections_without_legacy_template(self) -> None:
        workspace = self.make_workspace(VALID_PRD)

        result = validate_prd("alpha", workspace)

        self.assertTrue(result["ok"], result)

    def test_accepts_prd_without_pending_and_dependency_sections(self) -> None:
        workspace = self.make_workspace(VALID_PRD)

        result = validate_prd("alpha", workspace)

        self.assertTrue(result["ok"], result)
        self.assertNotIn("待确认事项", VALID_PRD)
        self.assertNotIn("外部依赖", VALID_PRD)

    def test_rejects_prd_without_formal_title(self) -> None:
        workspace = self.make_workspace(VALID_PRD.replace("# 需求正式稿", "# 需求讨论稿", 1))

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        self.assertIn("# 需求正式稿", "\n".join(result["errors"]))

    def test_accepts_prd_with_preserved_source_intro(self) -> None:
        prd_with_intro = VALID_PRD.replace(
            "# 需求正式稿\n\n## 需求摘要",
            f"# 需求正式稿\n\n{DISCUSS_DRAFT_INTRO}\n\n## 需求摘要",
            1,
        )
        workspace = self.make_workspace(prd_with_intro)

        result = validate_prd("alpha", workspace)

        self.assertTrue(result["ok"], result)

    def test_rejects_forbidden_formal_prd_headings(self) -> None:
        for heading in ("审理提炼", "待确认事项", "待确认项", "外部依赖", "第三方依赖"):
            with self.subTest(heading=heading):
                prd_content = FORMAL_PREFIX + f"## {heading}\n\n- 不应进入正式 PRD。\n\n" + VALID_PRD[len(FORMAL_PREFIX):]
                workspace = self.make_workspace(prd_content)

                result = validate_prd("alpha", workspace)

                self.assertFalse(result["ok"])
                self.assertIn("禁用标题", "\n".join(result["errors"]))

    def test_rejects_pending_marker_after_prd_resolution_gate(self) -> None:
        workspace = self.make_workspace(
            VALID_PRD.replace("审批人需要快速识别异常付款。", "审批人【待确认】需要快速识别异常付款。")
        )

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        self.assertIn("逐项获取用户裁定", "\n".join(result["errors"]))

    def test_rejects_missing_new_required_section(self) -> None:
        workspace = self.make_workspace(VALID_PRD.replace("## 关键约束", "## 约束"))

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        self.assertIn("关键约束", "\n".join(result["errors"]))

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
        self.assertIn("必须生成 source-context.json", "\n".join(result["errors"]))

    def test_accepts_required_sections_anywhere_in_prd(self) -> None:
        discuss_with_section_names = DISCUSS_WITH_HISTORY.replace(
            "## 假设与风险",
            "## 用户故事\n\n正式稿正文里的同名章节也算有效章节。\n\n"
            "## 验收口径\n\n正式稿正文里的同名章节也算有效章节。\n\n"
            "## 验收标准\n\n正式稿正文里的同名章节也算有效章节。\n\n"
            "## 关键约束\n\n正式稿正文里的同名章节也算有效章节。\n\n"
            "## 外部资料与实现约束\n\n无\n\n"
            "## 假设与风险",
        )
        prd_without_suffix_sections = discuss_with_section_names.split("## 历次讨论记录", 1)[0]
        prd_without_suffix_sections = prd_without_suffix_sections.replace("# 需求讨论稿", "# 需求正式稿", 1)
        prd_without_suffix_sections = prd_without_suffix_sections.replace(f"\n{DISCUSS_DRAFT_INTRO}\n\n", "\n")
        prd_without_suffix_sections = prd_without_suffix_sections.replace("## 待确认事项\n\n- 待确认上线窗口。\n\n", "")
        prd_without_suffix_sections = prd_without_suffix_sections.replace("## 外部依赖\n\n- 风控系统提供异常标记字段。\n\n", "")
        workspace = self.make_workspace(prd_without_suffix_sections)

        result = validate_prd("alpha", workspace)

        self.assertTrue(result["ok"], result)

    def test_accepts_external_interface_source_with_full_stage_contract(self) -> None:
        source_table = """## 外部资料与实现约束

| ID | 类型 | 名称 | 地址/路径 | 约束范围 | 必读阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| SRC-001 | 外部接口 | 支付网关 API | https://example.test/openapi | REQ-001 支付提交 | Specs、Plan、Code、Reviewer、E2E | 可访问 |
"""
        workspace = self.make_workspace(
            VALID_PRD.replace("## 外部资料与实现约束\n\n无", source_table.rstrip())
        )
        self.write_source_context(workspace)

        result = validate_prd("alpha", workspace)

        self.assertTrue(result["ok"], result)

    def test_rejects_external_interface_source_without_downstream_stages(self) -> None:
        source_table = """## 外部资料与实现约束

| ID | 类型 | 名称 | 地址/路径 | 约束范围 | 必读阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| SRC-001 | 外部接口 | 支付网关 API | https://example.test/openapi | REQ-001 支付提交 | Specs、Plan | 可访问 |
"""
        workspace = self.make_workspace(
            VALID_PRD.replace("## 外部资料与实现约束\n\n无", source_table.rstrip())
        )

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        self.assertIn("Code、Reviewer、E2E", "\n".join(result["errors"]))

    def test_rejects_duplicate_external_source_ids(self) -> None:
        source_table = """## 外部资料与实现约束

| ID | 类型 | 名称 | 地址/路径 | 约束范围 | 必读阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| SRC-001 | 原型 | 列表原型 | /tmp/list.html | REQ-001 | Specs、Plan | 可访问 |
| SRC-001 | 数据字典 | 付款字典 | /tmp/dict.xlsx | REQ-002 | Specs、Plan | 可访问 |
"""
        workspace = self.make_workspace(
            VALID_PRD.replace("## 外部资料与实现约束\n\n无", source_table.rstrip())
        )

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        self.assertIn("外部资料 ID 重复", "\n".join(result["errors"]))

    def test_rejects_prd_with_discussion_record_heading(self) -> None:
        workspace = self.make_workspace(VALID_PRD + "\n## 历次讨论记录\n\n- 不应进入正式 PRD。\n")

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        self.assertIn("不应包含讨论记录标题", "\n".join(result["errors"]))

if __name__ == "__main__":
    unittest.main()
