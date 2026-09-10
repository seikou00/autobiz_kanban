"""spec 稳定 ID 的唯一写法必须被索引识别，且零覆盖必须被拦。

仓库统一使用括号式稳定 ID：

    ### Requirement [REQ-001]:            #### Scenario [SCN-001]:

历史上 `autodev-specs/SKILL.md` 与 specs 模板一度教 `REQ-<capability>-NNN`
这种带 capability 前缀的写法，而下游测试和 B-E2E 证据聚合的 scenario 索引
只识别括号式。索引取不到 ID 时，覆盖门会把空集合误判为完整。

修复方向是让「技能教的」与「校验器索引的」收敛到同一种写法，因此本文件钉三件事：
括号式能被索引、模板与校验器的正则彼此吻合、旧 Verify checkpoint 不得绕过
当前 Batch Pipeline。
"""

from __future__ import annotations

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
    SPEC_REQUIREMENT_DEF_RE,
    SPEC_SCENARIO_DEF_RE,
    HookContext,
    _spec_scenario_refs_by_path,
    collect_spec_definition_index,
)
from hooks.init_workspace import create_feature, init_workspace  # noqa: E402
from hooks.update_checkpoint import prepare_checkpoint_update  # noqa: E402

SPEC_BODY = """## ADDED Requirements

### Requirement [REQ-001]: 创建导出任务

系统 SHALL 支持创建导出任务。

#### Scenario [SCN-001]: 创建成功

当用户提交导出请求时，系统 SHALL 返回任务号。

#### Scenario [SCN-002]: 参数非法

当参数非法时，系统 SHALL 返回 400。
"""

SPEC_TEMPLATE = ROOT / "skills" / "autodev" / "autodev-specs" / "templates" / "spec.md"


class SpecIdConventionTest(unittest.TestCase):
    def _feature(self, tmp: str, spec_body: str) -> tuple[Path, Path]:
        project = Path(tmp).resolve() / "demo"
        project.mkdir()
        init_workspace(project)
        create_feature(project, "alpha")
        feature_dir = project / ".autobizdevops" / "features" / "alpha"
        spec = feature_dir / "specs" / "order-export" / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(spec_body, encoding="utf-8")
        return project, feature_dir

    def test_bracketed_headings_are_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = self._feature(tmp, SPEC_BODY)
            ctx = HookContext(skill="autodev-verify", slug="alpha", root=project)
            index, failures = collect_spec_definition_index(ctx)
            self.assertEqual(failures, 0)
            self.assertEqual(index["REQ"], {"REQ-001"})
            self.assertEqual(index["SCN"], {"SCN-001", "SCN-002"})

    def test_bracketed_headings_resolve_path_qualified_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = self._feature(tmp, SPEC_BODY)
            ctx = HookContext(skill="autodev-verify", slug="alpha", root=project)
            refs = _spec_scenario_refs_by_path(ctx)
            self.assertIn("SCN-001", refs)
            self.assertEqual(
                refs["SCN-001"],
                {"specs/order-export/spec.md#SCN-001"},
            )

    def test_spec_template_matches_indexer_patterns(self) -> None:
        """模板教的写法必须正好是索引器认的写法，否则覆盖门会重新真空。"""
        text = SPEC_TEMPLATE.read_text(encoding="utf-8")
        self.assertTrue(
            SPEC_REQUIREMENT_DEF_RE.search(text),
            f"{SPEC_TEMPLATE} 的 Requirement 标题不被 SPEC_REQUIREMENT_DEF_RE 识别，"
            "修复：模板改用 '### Requirement [REQ-NNN]: <标题>'",
        )
        self.assertTrue(
            SPEC_SCENARIO_DEF_RE.search(text),
            f"{SPEC_TEMPLATE} 的 Scenario 标题不被 SPEC_SCENARIO_DEF_RE 识别，"
            "修复：模板改用 '#### Scenario [SCN-NNN]: <标题>'",
        )

    def test_legacy_verify_checkpoint_is_rejected_after_pipeline_convergence(self) -> None:
        """B-E2E 已归属 Code Pipeline，旧 Verify 阶段不得重新进入流程。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = self._feature(tmp, SPEC_BODY)

            from board_core.state_store import (
                load_state_json_records,
                write_state_records,
            )

            records, _, _ = load_state_json_records(project)
            current = dict(records["alpha"])
            current["checkpoint"] = "code_in_progress"
            current["stage"] = "Code"
            records["alpha"] = current
            write_state_records(project, records)

            result = prepare_checkpoint_update(
                workspace=project, feature="alpha", checkpoint="verify_done"
            )
            self.assertFalse(result.ok, "旧 Verify checkpoint 不得绕过 Batch Pipeline")
            joined = " ".join(str(error) for error in (result.errors or ()))
            self.assertIn("未知 checkpoint", joined)


if __name__ == "__main__":
    unittest.main()
