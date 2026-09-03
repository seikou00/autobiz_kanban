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


def spec(path: str, *, required: bool, degrade: str = "降级动作") -> ArtifactSpec:
    return ArtifactSpec(
        id=path,
        label=f"{path} 标签",
        path=path,
        required=required,
        extract=ExtractSpec(degrade=degrade),
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

    def test_present_input_is_always_rendered_with_absolute_read_path(self) -> None:
        self.write("proposal.md")
        contract = make_contract(inputs=(spec("proposal.md", required=True),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertIn("state: `ready`", text)
        self.assertIn("`proposal.md`：proposal.md 标签（必需，status: `present`）", text)
        self.assertIn(f"读取：`{(self.feature_dir / 'proposal.md').resolve()}`", text)
        self.assertNotIn("降级", text)
        self.assertNotIn("缺失处理", text)

    def test_missing_required_input_uses_its_degrade(self) -> None:
        contract = make_contract(
            inputs=(spec("design.md", required=True, degrade="回到设计阶段补齐后再执行"),)
        )

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertEqual(
            text,
            "## 输入产物（state: `blocked`）\n"
            "- `design.md`：design.md 标签（必需，status: `missing`）\n"
            "   缺失处理：回到设计阶段补齐后再执行\n",
        )

    def test_missing_optional_input_uses_its_degrade(self) -> None:
        contract = make_contract(
            inputs=(spec("PRD.md", required=False, degrade="无 PRD 时直接跳过"),)
        )

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertEqual(
            text,
            "## 输入产物（state: `ready`）\n"
            "- `PRD.md`：PRD.md 标签（可选，status: `missing`）\n"
            "   自动降级：无 PRD 时直接跳过\n",
        )

    def test_empty_degrade_falls_back_to_hardcoded_default(self) -> None:
        # Nothing in board_config ships an empty degrade today; this pins the
        # defensive fallback: required stops, optional skips.
        required_no_extract = ArtifactSpec(id="a", label="A 标签", path="a.md", required=True)
        optional_blank_degrade = ArtifactSpec(
            id="b", label="B 标签", path="b.md", required=False, extract=ExtractSpec()
        )
        contract = make_contract(inputs=(required_no_extract, optional_blank_degrade))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertIn("`a.md`：A 标签（必需，status: `missing`）", text)
        self.assertIn("缺失处理：停止——必需输入未生成，回流上游补齐后再执行", text)
        self.assertIn("`b.md`：B 标签（可选，status: `missing`）", text)
        self.assertIn("自动降级：直接跳过，不影响执行", text)

    def test_present_and_missing_inputs_are_both_rendered(self) -> None:
        self.write("PRD.md")
        contract = make_contract(
            inputs=(
                spec("PRD.md", required=True),
                spec("design.md", required=True, degrade="回流上游"),
            )
        )

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertIn("`PRD.md`：PRD.md 标签（必需，status: `present`）", text)
        self.assertIn("`design.md`：design.md 标签（必需，status: `missing`）", text)
        self.assertIn("state: `blocked`", text)

    def test_glob_input_resolves_every_file_in_sorted_order(self) -> None:
        self.write("specs/zeta/z.md")
        self.write("specs/alpha/a.md")
        contract = make_contract(inputs=(spec("specs/**/*.md", required=True),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        alpha = str((self.feature_dir / "specs/alpha/a.md").resolve())
        zeta = str((self.feature_dir / "specs/zeta/z.md").resolve())
        self.assertIn("`specs/**/*.md`：specs/**/*.md 标签（必需，status: `present`）", text)
        self.assertIn(f"读取：`{alpha}`、`{zeta}`", text)
        self.assertLess(text.index(alpha), text.index(zeta))

    def test_glob_input_missing_when_no_match(self) -> None:
        contract = make_contract(inputs=(spec("specs/**/*.md", required=True, degrade="回流上游"),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertIn("`specs/**/*.md`：", text)
        self.assertIn("status: `missing`", text)

    def test_empty_file_counts_as_missing(self) -> None:
        self.write("plan.json", body="")
        contract = make_contract(inputs=(spec("plan.json", required=True, degrade="回流上游"),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertIn("`plan.json`：", text)
        self.assertIn("status: `missing`", text)

    def test_output_does_not_restore_the_old_contract_frame(self) -> None:
        contract = make_contract(inputs=(spec("design.md", required=True, degrade="回流上游"),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        # The compact input projection does not restore the old node, boundary,
        # output, or validator sections.
        self.assertEqual(
            text,
            "## 输入产物（state: `blocked`）\n"
            "- `design.md`：design.md 标签（必需，status: `missing`）\n"
            "   缺失处理：回流上游\n",
        )


class RenderContractPlainBaselineTests(unittest.TestCase):
    def test_baseline_lists_inputs_with_unknown_status_without_degrade(self) -> None:
        contract = make_contract(
            inputs=(
                spec("proposal.md", required=True, degrade="回流上游"),
                spec("PRD.md", required=False, degrade="无 PRD 时跳过"),
            )
        )

        text = render_contract_plain(contract)

        self.assertEqual(
            text,
            "## 输入产物（state: `unknown`）\n"
            "- `proposal.md`：proposal.md 标签（必需，status: `unknown`）\n"
            "- `PRD.md`：PRD.md 标签（可选，status: `unknown`）\n",
        )

    def test_contract_without_inputs_reports_ready_when_feature_is_known(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                render_contract_plain(make_contract(), feature_dir=Path(tmp)),
                "## 输入产物（state: `ready`）\n- 无\n",
            )

    def test_workflow_context_adds_no_standalone_section(self) -> None:
        # Workflow context only ever surfaces as one skipped input's reason;
        # with nothing dropped it stays out of the output entirely.
        contract = make_contract(inputs=(spec("design.md", required=True, degrade="回流上游"),))

        text = render_contract_plain(
            contract,
            {"feature": "alpha", "workflowProfile": "standard", "workflowDecisions": {"a": "enabled"}},
        )

        self.assertNotIn("上下文", text)
        self.assertNotIn("feature=alpha", text)
        self.assertNotIn("工作流模板", text)


class SkippedInputReasonTests(unittest.TestCase):
    """A skipped input must say why, so it never reads as a missing one."""

    def render(self, workflow_context: dict | None) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            return render_contract_plain(
                make_contract(),
                workflow_context,
                Path(tmp),
                extra_skipped_inputs=(spec("PRD.md", required=True),),
            )

    def test_node_subset_template_is_named_as_the_reason(self) -> None:
        text = self.render({"workflowTemplate": "lean"})

        self.assertIn(
            "- `PRD.md`：PRD.md 标签（裁剪前必需，status: `skipped` "
            "— 工作流模板 `lean` 未包含产出该产物的节点）",
            text,
        )

    def test_explicitly_skipped_nodes_are_named_as_the_reason(self) -> None:
        text = self.render({"workflowTemplate": "standard", "workflowSkippedNodes": ["biz.prd"]})

        self.assertIn("status: `skipped` — 上游节点 `biz.prd` 已跳过", text)

    def test_both_causes_are_reported_together(self) -> None:
        text = self.render(
            {"workflowTemplate": "lean", "workflowSkippedNodes": ["biz.prd", "dev.plan"]}
        )

        self.assertIn(
            "status: `skipped` — 工作流模板 `lean` 未包含产出该产物的节点；"
            "上游节点 `biz.prd`、`dev.plan` 已跳过",
            text,
        )

    def test_reason_falls_back_when_no_workflow_context_is_supplied(self) -> None:
        text = self.render(None)

        self.assertIn("status: `skipped` — 产出该产物的节点不在当前工作流链路中", text)

    def test_skipped_input_never_carries_missing_handling(self) -> None:
        text = self.render({"workflowTemplate": "lean"})

        self.assertNotIn("缺失处理", text)
        self.assertNotIn("自动降级", text)


class PlainCliTests(unittest.TestCase):
    def test_json_and_plain_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["autodev-code", "--json", "--plain"])
        self.assertEqual(ctx.exception.code, 2)

    def test_plain_cli_returns_zero(self) -> None:
        self.assertEqual(main(["autodev-code", "--plain"]), 0)


if __name__ == "__main__":
    unittest.main()
