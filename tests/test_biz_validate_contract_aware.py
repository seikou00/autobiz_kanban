from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.state_store import write_state_records  # noqa: E402
from skills.autobiz.hooks.biz_validate import validate_prd  # noqa: E402
from tests.test_biz_validate_prd import VALID_PRD  # noqa: E402


CUSTOM_WITH_PRD_NODE = {
    "checkpoint": "prd_done",
    "owner": "tester",
    "iteration": "1",
    "updated_at": "2026-06-12 12:00:00",
    "workflowTemplate": "custom",
    "workflowNodes": ["biz.prd", "dev.code", "ops.archive"],
}

LEAN_RECORD = {
    "checkpoint": "code_in_progress",
    "owner": "tester",
    "iteration": "1",
    "updated_at": "2026-06-12 12:00:00",
    "workflowTemplate": "lean",
}


class BizValidateContractAwareTests(unittest.TestCase):
    def make_workspace(self, record: dict, *, prd_content: str | None = None) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        workspace = Path(tempdir.name)
        feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
        feature_dir.mkdir(parents=True)
        if prd_content is not None:
            (feature_dir / "PRD.md").write_text(prd_content, encoding="utf-8")
        write_state_records(workspace, {"alpha": dict(record)})
        return workspace

    def test_custom_chain_with_biz_prd_validates_merged_skill_output(self) -> None:
        workspace = self.make_workspace(CUSTOM_WITH_PRD_NODE, prd_content=VALID_PRD)
        result = validate_prd("alpha", workspace)
        self.assertTrue(result["ok"], result)
        self.assertNotIn("skipped", result)

    def test_lean_chain_skips_prd_validation(self) -> None:
        workspace = self.make_workspace(LEAN_RECORD)
        prd = validate_prd("alpha", workspace)
        self.assertTrue(prd["ok"], prd)
        self.assertTrue(prd.get("skipped"))

    def test_custom_chain_still_requires_prd_output(self) -> None:
        # The node's own output contract stays enforced.
        workspace = self.make_workspace(CUSTOM_WITH_PRD_NODE)
        result = validate_prd("alpha", workspace)
        self.assertFalse(result["ok"])
        self.assertTrue(any("PRD.md 不存在" in error for error in result["errors"]))

    def test_missing_feature_record_fails_with_clear_error(self) -> None:
        workspace = self.make_workspace(CUSTOM_WITH_PRD_NODE, prd_content=VALID_PRD)
        result = validate_prd(None, workspace)
        self.assertTrue(result["ok"], result)  # auto-detected single dir, record exists
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        empty_workspace = Path(tempdir.name)
        (empty_workspace / ".autobizdevops" / "features" / "beta").mkdir(parents=True)
        write_state_records(empty_workspace, {})
        result = validate_prd("beta", empty_workspace)
        self.assertFalse(result["ok"])
        self.assertTrue(any("不存在" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
