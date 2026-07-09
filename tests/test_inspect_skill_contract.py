from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hooks.init_workspace import create_feature, init_workspace
from hooks.update_checkpoint import prepare_skip_update
from hooks.inspect_skill_contract import (
    ROOT,
    _find_feature_contract,
    _resolve_feature_dir,
    render_contract_plain,
)
from board_core.state_store import write_state_records


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

    def test_plain_treats_case_only_artifact_name_mismatch_as_missing(self) -> None:
        feature = "case-mismatch"
        self._create_feature(feature)
        feature_dir = _resolve_feature_dir(self.workspace, feature)
        (feature_dir / "prd_discuss.md").write_text("not the contract filename", encoding="utf-8")

        output = self._plain("autobiz-prd-generate", feature)

        self.assertIn("PRD_DISCUSS.md", output)
        self.assertIn("无讨论稿时先与用户完成需求澄清，再生成 PRD", output)

    def test_plain_includes_dropped_entry_inputs_for_lean_workflow(self) -> None:
        feature = "lean-entry"
        self._create_feature(feature, workflow_template="lean")

        output = self._plain("autodev-specs", feature)

        self.assertIn("PRD.md", output)
        self.assertIn("无 PRD 时基于用户描述直接澄清行为契约", output)
        self.assertIn("UI_CONTEXT.json", output)

    def test_plain_includes_discuss_input_after_skipping_discuss_node(self) -> None:
        feature = "skip-discuss"
        self._create_feature(feature)
        result = prepare_skip_update(
            workspace=self.workspace,
            feature=feature,
            skip_nodes=["biz.discuss"],
        )
        self.assertTrue(result.ok, result.errors)
        write_state_records(self.workspace, result.records)

        output = self._plain("autobiz-prd-generate", feature)

        self.assertIn("PRD_DISCUSS.md", output)
        self.assertIn("无讨论稿时先与用户完成需求澄清，再生成 PRD", output)


if __name__ == "__main__":
    unittest.main()
