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

    def test_present_input_is_omitted_yielding_empty_output(self) -> None:
        self.write("proposal.md")
        contract = make_contract(inputs=(spec("proposal.md", required=True),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        # A present input needs no runtime instruction; with nothing missing the
        # command returns empty output.
        self.assertEqual(text, "")

    def test_missing_required_input_uses_its_degrade(self) -> None:
        contract = make_contract(
            inputs=(spec("design.md", required=True, degrade="回到设计阶段补齐后再执行"),)
        )

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        # Required inputs draw their handling text from extract.degrade too — not
        # a hardcoded stop message; the header carries no flag and no marker.
        self.assertEqual(
            text,
            "## 缺失产物处理\n1. design.md：design.md 标签\n   缺失处理：回到设计阶段补齐后再执行\n",
        )

    def test_missing_optional_input_uses_its_degrade(self) -> None:
        contract = make_contract(
            inputs=(spec("PRD.md", required=False, degrade="无 PRD 时直接跳过"),)
        )

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertEqual(
            text,
            "## 缺失产物处理\n1. PRD.md：PRD.md 标签\n   缺失处理：无 PRD 时直接跳过\n",
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

        self.assertIn("1. a.md：A 标签", text)
        self.assertIn("缺失处理：停止——必需输入未生成，回流上游补齐后再执行", text)
        self.assertIn("2. b.md：B 标签", text)
        self.assertIn("缺失处理：直接跳过，不影响执行", text)

    def test_only_missing_inputs_listed_and_renumbered(self) -> None:
        self.write("PRD.md")
        contract = make_contract(
            inputs=(
                spec("PRD.md", required=True),
                spec("design.md", required=True, degrade="回流上游"),
            )
        )

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        # Present input dropped; the remaining missing one is renumbered to 1.
        self.assertNotIn("PRD.md", text)
        self.assertIn("1. design.md：design.md 标签", text)

    def test_glob_input_present_is_omitted(self) -> None:
        self.write("specs/cap/a.md")
        contract = make_contract(inputs=(spec("specs/**/*.md", required=True),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertEqual(text, "")

    def test_glob_input_missing_when_no_match(self) -> None:
        contract = make_contract(inputs=(spec("specs/**/*.md", required=True, degrade="回流上游"),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertIn("specs/**/*.md：", text)

    def test_empty_file_counts_as_missing(self) -> None:
        self.write("plan.json", body="")
        contract = make_contract(inputs=(spec("plan.json", required=True, degrade="回流上游"),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        self.assertIn("plan.json：", text)

    def test_header_carries_no_flag_no_marker_no_frame(self) -> None:
        contract = make_contract(inputs=(spec("design.md", required=True, degrade="回流上游"),))

        text = render_contract_plain(contract, feature_dir=self.feature_dir)

        # No 必需/可选 flag, no 未生成 marker, and none of the old checklist frame
        # (title / node / boundary / outputs / validators).
        self.assertEqual(
            text,
            "## 缺失产物处理\n1. design.md：design.md 标签\n   缺失处理：回流上游\n",
        )


class RenderContractPlainBaselineTests(unittest.TestCase):
    def test_baseline_previews_every_input_handling(self) -> None:
        contract = make_contract(
            inputs=(
                spec("proposal.md", required=True, degrade="回流上游"),
                spec("PRD.md", required=False, degrade="无 PRD 时跳过"),
            )
        )

        text = render_contract_plain(contract)

        # Existence unknown → all inputs previewed, each with its degrade text and
        # no per-input status.
        self.assertEqual(
            text,
            "## 缺失产物处理\n"
            "1. proposal.md：proposal.md 标签\n"
            "   缺失处理：回流上游\n"
            "2. PRD.md：PRD.md 标签\n"
            "   缺失处理：无 PRD 时跳过\n",
        )

    def test_contract_without_inputs_renders_empty(self) -> None:
        self.assertEqual(render_contract_plain(make_contract()), "")

    def test_workflow_context_is_accepted_but_not_rendered(self) -> None:
        contract = make_contract(inputs=(spec("design.md", required=True, degrade="回流上游"),))

        text = render_contract_plain(
            contract,
            {"feature": "alpha", "workflowProfile": "standard", "workflowDecisions": {"a": "enabled"}},
        )

        self.assertNotIn("上下文", text)
        self.assertNotIn("feature=alpha", text)


class PlainCliTests(unittest.TestCase):
    def test_json_and_plain_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["autodev-code", "--json", "--plain"])
        self.assertEqual(ctx.exception.code, 2)

    def test_plain_cli_returns_zero(self) -> None:
        self.assertEqual(main(["autodev-code", "--plain"]), 0)


if __name__ == "__main__":
    unittest.main()
