from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.contracts import ArtifactSpec, ExtractSpec, SkillContract  # noqa: E402
from hooks.inspect_skill_contract import main, render_contract_plain  # noqa: E402


def make_contract(*, inputs: tuple[ArtifactSpec, ...] = ()) -> SkillContract:
    return SkillContract(
        node_id="dev.sample",
        label="示例阶段",
        group="Dev",
        skill="autodev-sample",
        checkpoints=("sample_in_progress", "sample_done"),
        inputs=inputs,
        outputs=(ArtifactSpec(id="report", label="报告", path="REPORT.md", required=True),),
        validators=("sample_gate",),
    )


def spec(
    path: str, *, required: bool, method: str = "怎么读", degrade: str = "降级动作"
) -> ArtifactSpec:
    return ArtifactSpec(
        id=path,
        label=f"{path} 标签",
        path=path,
        required=required,
        extract=ExtractSpec(focus=("聚焦点",), method=method, degrade=degrade),
    )


class RenderContractPlainStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.feature_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, rel: str, body: str = "content") -> None:
        target = self.feature_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def test_present_input_shows_method_only(self) -> None:
        self.write("proposal.md")
        contract = make_contract(inputs=(spec("proposal.md", required=True, method="抽取范围约束"),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertIn("1. proposal.md（必需·已生成）：proposal.md 标签", text)
        self.assertIn("读取方式：抽取范围约束", text)
        self.assertNotIn("缺失处理", text)
        # focus is never rendered in any state.
        self.assertNotIn("读取重点", text)
        self.assertNotIn("聚焦点", text)

    def test_missing_required_input_shows_stop_not_method(self) -> None:
        contract = make_contract(inputs=(spec("design.md", required=True, method="怎么读设计"),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertIn("1. design.md（必需·未生成）：design.md 标签", text)
        self.assertIn("缺失处理：停止——必需输入未生成，回流上游补齐后再执行", text)
        # "读取方式：" (fullwidth colon) is the method line; the section title's
        # "读取方式优先…" must not trip this.
        self.assertNotIn("读取方式：", text)

    def test_missing_optional_input_shows_degrade(self) -> None:
        contract = make_contract(
            inputs=(spec("PRD.md", required=False, degrade="无 PRD 时直接跳过"),)
        )

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertIn("1. PRD.md（可选·未生成）：PRD.md 标签", text)
        self.assertIn("缺失处理：无 PRD 时直接跳过", text)
        self.assertNotIn("读取方式：", text)

    def test_glob_input_present_when_a_match_exists(self) -> None:
        self.write("specs/cap/a.md")
        contract = make_contract(inputs=(spec("specs/**/*.md", required=True),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertIn("specs/**/*.md（必需·已生成）", text)

    def test_glob_input_missing_when_no_match(self) -> None:
        contract = make_contract(inputs=(spec("specs/**/*.md", required=True),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertIn("specs/**/*.md（必需·未生成）", text)

    def test_empty_file_counts_as_missing(self) -> None:
        self.write("plan.json", body="")
        contract = make_contract(inputs=(spec("plan.json", required=True),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertIn("plan.json（必需·未生成）", text)


class RenderContractPlainBaselineTests(unittest.TestCase):
    def test_baseline_without_feature_dir_shows_method_and_no_status(self) -> None:
        contract = make_contract(
            inputs=(
                spec("proposal.md", required=True, method="抽取范围约束"),
                spec("PRD.md", required=False),
            )
        )

        text = render_contract_plain(contract)

        self.assertIn("1. proposal.md（必需）：proposal.md 标签", text)
        self.assertIn("读取方式：抽取范围约束", text)
        # No per-input status markers in baseline; the "·" prefix distinguishes
        # header markers from the boundary rule's "…未生成即停止" text.
        self.assertNotIn("·已生成", text)
        self.assertNotIn("·未生成", text)
        self.assertNotIn("缺失处理", text)
        self.assertNotIn("读取重点", text)

    def test_plain_output_carries_frame_and_boundary(self) -> None:
        text = render_contract_plain(make_contract())

        self.assertIn("节点：dev.sample｜示例阶段", text)
        self.assertIn("checkpoint：sample_in_progress, sample_done", text)
        self.assertIn("未在上表列出的 id 不属于本工作流", text)
        self.assertIn("任一必需输入未生成即停止", text)
        self.assertIn("- REPORT.md（必需）：报告", text)
        self.assertIn("## Validators：sample_gate", text)

    def test_workflow_context_line_is_rendered(self) -> None:
        text = render_contract_plain(
            make_contract(),
            {"feature": "alpha", "workflowProfile": "standard", "workflowDecisions": {"a": "enabled"}},
        )

        self.assertIn("上下文：feature=alpha ｜ profile=standard ｜ decisions=a=enabled", text)


class PlainCliTests(unittest.TestCase):
    def test_json_and_plain_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["autodev-code", "--json", "--plain"])
        self.assertEqual(ctx.exception.code, 2)

    def test_plain_cli_emits_flat_text(self) -> None:
        self.assertEqual(main(["autodev-code", "--plain"]), 0)


if __name__ == "__main__":
    unittest.main()
