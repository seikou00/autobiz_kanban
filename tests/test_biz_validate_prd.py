from __future__ import annotations

import os
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


DISCUSS_WITH_HISTORY = """# 支付审批优化

## 需求摘要

审批人需要快速识别异常付款。

## 当前已确认结论

- 本期只处理审批列表异常标记。

## 问题清单与处理状态

- P2: 上线窗口待确认。

## 待确认事项

- 待确认上线窗口。

## 假设与风险

- 假设异常标记由后端字段提供。
- 风险：风控系统字段可能延迟。

## 历次讨论记录

- 2026-06-03: 用户确认先生成 PRD。
"""


DISCUSS_PREFIX = DISCUSS_WITH_HISTORY.split("## 历次讨论记录", 1)[0]


VALID_PRD = DISCUSS_PREFIX + """## 审理提炼

### 用户故事

- 作为财务审批人，我希望在支付审批列表中识别异常单据，以便优先处理高风险付款。

### 验收口径

- 用户视角：审批人能看到异常标记。
- 工程视角：接口返回异常标记字段。
- 回归视角：原有审批状态和分页不受影响。

### 验收标准

- 当单据满足异常条件时，列表展示异常标记。
- 当按异常标记筛选时，只返回符合条件的单据。

### 关键约束

| 类别 | 约束 | 来源/原因 |
|------|------|-----------|
| 数据 | 异常标记由后端字段提供 | 假设与风险 |
"""


class BizValidatePrdTests(unittest.TestCase):
    def make_workspace(self, prd_content: str, discuss_content: str = DISCUSS_WITH_HISTORY) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        workspace = Path(tempdir.name)
        feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
        feature_dir.mkdir(parents=True)
        (feature_dir / "PRD_DISCUSS.md").write_text(discuss_content, encoding="utf-8")
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
                self.assertIn(f"{key} 未设置", result.stderr)

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

    def test_rejects_missing_new_required_section(self) -> None:
        workspace = self.make_workspace(VALID_PRD.replace("### 关键约束", "### 约束"))

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        self.assertIn("关键约束", "\n".join(result["errors"]))

    def test_rejects_modified_copied_prefix(self) -> None:
        workspace = self.make_workspace(
            VALID_PRD.replace("审批人需要快速识别异常付款。", "审批人需要识别异常付款。", 1)
        )

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        self.assertIn("原文复制区与 PRD_DISCUSS.md 截断前内容不一致", "\n".join(result["errors"]))

    def test_rejects_required_sections_only_in_copied_prefix(self) -> None:
        discuss_with_section_names = DISCUSS_WITH_HISTORY.replace(
            "## 假设与风险",
            "## 用户故事\n\n复制区里的同名章节不算追加区。\n\n"
            "## 验收口径\n\n复制区里的同名章节不算追加区。\n\n"
            "## 验收标准\n\n复制区里的同名章节不算追加区。\n\n"
            "## 关键约束\n\n复制区里的同名章节不算追加区。\n\n"
            "## 假设与风险",
        )
        prd_without_suffix_sections = discuss_with_section_names.split("## 历次讨论记录", 1)[0]
        prd_without_suffix_sections += "## 审理提炼\n\n- 追加区没有必需段落。\n"
        workspace = self.make_workspace(prd_without_suffix_sections, discuss_with_section_names)

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        errors = "\n".join(result["errors"])
        self.assertIn("用户故事", errors)
        self.assertIn("验收口径", errors)
        self.assertIn("验收标准", errors)
        self.assertIn("关键约束", errors)

    def test_rejects_prd_with_discussion_record_heading(self) -> None:
        workspace = self.make_workspace(VALID_PRD + "\n## 历次讨论记录\n\n- 不应进入正式 PRD。\n")

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        self.assertIn("不应包含讨论记录标题", "\n".join(result["errors"]))

    def test_rejects_discuss_without_copy_cutoff_heading(self) -> None:
        discuss_without_history = DISCUSS_WITH_HISTORY.replace("## 历次讨论记录", "## 沟通纪要")
        workspace = self.make_workspace(VALID_PRD, discuss_without_history)

        result = validate_prd("alpha", workspace)

        self.assertFalse(result["ok"])
        self.assertIn("缺少讨论记录标题", "\n".join(result["errors"]))


if __name__ == "__main__":
    unittest.main()
