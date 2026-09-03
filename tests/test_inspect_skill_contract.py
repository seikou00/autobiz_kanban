from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from board_core.state_store import write_state_records
from hooks.init_workspace import create_feature, init_workspace
from hooks.inspect_skill_contract import (
    ROOT,
    _find_feature_contract,
    _resolve_feature_dir,
    render_contract_plain,
)


class InspectSkillContractPlainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name) / "demo-project"
        self.workspace.mkdir()
        init_workspace(self.workspace)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_feature(self, feature: str, *, workflow_template: str = "standard") -> None:
        create_feature(self.workspace, feature, workflow_template=workflow_template)

    def _create_legacy_lean_feature(self, feature: str) -> None:
        (self.workspace / ".autobizdevops" / "features" / feature).mkdir(parents=True)
        write_state_records(
            self.workspace,
            {
                feature: {
                    "checkpoint": "specs_in_progress",
                    "workflowProfile": "standard",
                    "workflowDecisions": {},
                    "workflowTemplate": "lean",
                }
            },
        )

    def _plain(self, skill: str, feature: str) -> str:
        contract, workflow_context, extra_skipped_inputs = _find_feature_contract(
            ROOT,
            skill=skill,
            feature=feature,
            workspace=self.workspace,
        )
        return render_contract_plain(
            contract,
            workflow_context,
            _resolve_feature_dir(self.workspace, feature),
            extra_skipped_inputs=extra_skipped_inputs,
        )

    def test_standard_prd_contract_uses_merged_requirement_skill(self) -> None:
        feature = "merged-prd"
        self._create_feature(feature)
        contract, _, extra_skipped = _find_feature_contract(
            ROOT,
            skill="autobiz-requirement-discuss",
            feature=feature,
            workspace=self.workspace,
        )

        self.assertEqual(contract.node_id, "biz.prd")
        self.assertEqual(contract.checkpoints, ("prd_in_progress", "prd_done"))
        self.assertEqual(contract.required_inputs, ())
        self.assertEqual(contract.required_outputs, ("PRD.md", "UI_CONTEXT.json"))
        self.assertEqual(extra_missing, ())
        self.assertEqual(self._plain("autobiz-requirement-discuss", feature), "")

    def test_plain_legacy_lean_workflow_marks_dropped_entry_inputs_as_skipped(self) -> None:
        feature = "lean-entry"
        self._create_legacy_lean_feature(feature)

        output = self._plain("autodev-specs", feature)

        self.assertIn("PRD.md", output)
        self.assertIn("裁剪前必需，status: `skipped`", output)
        self.assertIn("source-context.json", output)
        self.assertNotIn("无 PRD 时基于用户描述直接澄清行为契约", output)
        self.assertNotIn("自动降级", output)
        self.assertIn("UI_CONTEXT.json", output)

    def test_plain_legacy_lean_archive_reports_nothing_to_handle(self) -> None:
        # ops.archive's only input is produced by ops.cicd, which lean drops from
        # the chain: it can never exist here, so it is not a missing artifact.
        feature = "lean-archive"
        self._create_legacy_lean_feature(feature)

        self.assertEqual(
            self._plain("autoops-archive", feature),
            "## 输入产物（state: `ready`）\n- 无\n",
        )

    def test_plain_legacy_lean_code_omits_inputs_of_dropped_upstream_nodes(self) -> None:
        feature = "lean-code"
        self._create_legacy_lean_feature(feature)

        output = self._plain("autodev-code", feature)

        # Still produced upstream inside lean (dev.specs), just not written yet.
        self.assertIn("proposal.md", output)
        # Produced by nodes lean drops (biz.prd / dev.plan / dev.detail_design).
        self.assertNotIn("PRD.md", output)
        self.assertNotIn("design.md", output)
        self.assertNotIn("plan.json", output)

    def test_plain_code_separates_optional_detail_design_from_missing_inputs(self) -> None:
        feature = "code-without-detail-design"
        self._create_feature(feature)
        feature_dir = _resolve_feature_dir(self.workspace, feature)
        for relative_path in ("proposal.md", "PRD.md", "design.md", "plan.json"):
            (feature_dir / relative_path).write_text("content\n", encoding="utf-8")
        spec = feature_dir / "specs" / "capability" / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("content\n", encoding="utf-8")

        output = self._plain("autodev-code", feature)

        self.assertIn("state: `ready`", output)
        self.assertIn("`proposal.md`：变更提案（必需，status: `present`）", output)
        self.assertIn("`DETAIL_DESIGN.md`：详细设计参考（可选，status: `missing`）", output)
        self.assertIn("自动降级：无 DETAIL_DESIGN 时", output)

if __name__ == "__main__":
    unittest.main()
