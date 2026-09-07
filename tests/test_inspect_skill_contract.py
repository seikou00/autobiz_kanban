from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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

    def _plain(self, skill: str, feature: str) -> str:
        contract, workflow_context, extra_missing_inputs = _find_feature_contract(
            ROOT,
            skill=skill,
            feature=feature,
            workspace=self.workspace,
        )
        return render_contract_plain(
            contract,
            workflow_context,
            _resolve_feature_dir(self.workspace, feature),
            extra_missing_inputs=extra_missing_inputs,
        )

    def test_standard_prd_contract_uses_merged_requirement_skill(self) -> None:
        feature = "merged-prd"
        self._create_feature(feature)
        contract, _, extra_missing = _find_feature_contract(
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

    def test_standard_design_and_plan_contracts_have_a_hard_handoff(self) -> None:
        feature = "design-plan-handoff"
        self._create_feature(feature)

        design, _, _ = _find_feature_contract(
            ROOT,
            skill="autodev-design",
            feature=feature,
            workspace=self.workspace,
        )
        plan, _, _ = _find_feature_contract(
            ROOT,
            skill="autodev-plan",
            feature=feature,
            workspace=self.workspace,
        )

        self.assertEqual(design.node_id, "dev.design")
        self.assertEqual(design.checkpoints, ("design_in_progress", "design_done"))
        self.assertEqual(design.required_outputs, ("design.md", ".design-contract.lock.json"))
        self.assertEqual(plan.node_id, "dev.plan")
        self.assertEqual(plan.checkpoints, ("plan_in_progress", "plan_done"))
        self.assertEqual(plan.required_outputs, ("PLAN.md", "plan.json"))
        self.assertIn("design.md", plan.required_inputs)
        self.assertIn(".design-contract.lock.json", plan.required_inputs)

    def test_plain_lean_workflow_requires_prd_only(self) -> None:
        feature = "lean-entry"
        self._create_feature(feature, workflow_template="lean")

        output = self._plain("autodev-specs", feature)

        self.assertIn("PRD.md", output)
        self.assertIn("无 PRD 时基于用户描述直接澄清行为契约", output)
        self.assertIn("UI_CONTEXT.json", output)

    def test_plain_lean_archive_reports_nothing_to_handle(self) -> None:
        # ops.archive's only input is produced by ops.cicd, which lean drops from
        # the chain: it can never exist here, so it is not a missing artifact.
        feature = "lean-archive"
        self._create_feature(feature, workflow_template="lean")

        self.assertEqual(self._plain("autoops-archive", feature), "")

    def test_plain_lean_code_omits_inputs_of_dropped_upstream_nodes(self) -> None:
        feature = "lean-code"
        self._create_feature(feature, workflow_template="lean")

        output = self._plain("autodev-code", feature)

        # Still produced upstream inside lean (dev.specs), just not written yet.
        self.assertIn("proposal.md", output)
        # Produced by nodes lean drops (biz.prd / dev.plan / dev.detail_design).
        self.assertNotIn("PRD.md", output)
        self.assertNotIn("design.md", output)
        self.assertNotIn("plan.json", output)

if __name__ == "__main__":
    unittest.main()
