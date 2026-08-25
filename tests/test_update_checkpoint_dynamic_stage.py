from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from board_core.state_store import load_state_json_records, write_state_records
from hooks.init_workspace import create_feature, init_workspace
from hooks.update_checkpoint import apply_implicit_skip_decisions, main


class UpdateCheckpointDynamicStageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.plugin_workspace = Path(self.tmp.name).resolve()
        self.project = self.plugin_workspace / "demo"
        self.project.mkdir()
        init_workspace(self.project)
        self.feature = "direct-code-entry"
        create_feature(self.project, self.feature)

        records, errors, exists = load_state_json_records(self.project)
        self.assertTrue(exists)
        self.assertEqual(errors, [])
        record = dict(records[self.feature])
        record["checkpoint"] = "plan_done"
        record["stage"] = "Plan 完成"
        record["workflowDecisions"] = {}
        records[self.feature] = record
        write_state_records(self.project, records)

        self.env = {
            "PLUGIN_WORKSPACE": str(self.plugin_workspace),
            "PROJECT_DIR": "demo",
            "FEATURE_ID": self.feature,
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _enter_code(self) -> int:
        stdout = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True):
            with patch("hooks.update_checkpoint.validate_lifecycle", return_value=[]), patch(
                "hooks.update_checkpoint.write_hook_logs"
            ), contextlib.redirect_stdout(stdout):
                exit_code = main(["--checkpoint", "code_in_progress"])
        self.assertEqual(exit_code, 0, stdout.getvalue())
        return exit_code

    def _assert_code_skip_persisted(self) -> None:
        records, errors, exists = load_state_json_records(self.project)
        self.assertTrue(exists)
        self.assertEqual(errors, [])
        record = records[self.feature]
        self.assertEqual(record["checkpoint"], "code_in_progress")
        self.assertEqual(
            record["workflowDecisions"],
            {"detail_design_before_code": "skipped"},
        )

    def test_direct_code_entry_persists_detail_design_skip(self) -> None:
        self._enter_code()
        self._assert_code_skip_persisted()

    def test_existing_code_entry_backfills_detail_design_skip(self) -> None:
        records, errors, exists = load_state_json_records(self.project)
        self.assertTrue(exists)
        self.assertEqual(errors, [])
        record = dict(records[self.feature])
        record["checkpoint"] = "code_in_progress"
        record["stage"] = "代码实现"
        record["workflowDecisions"] = {}
        records[self.feature] = record
        write_state_records(self.project, records)

        self._enter_code()
        self._assert_code_skip_persisted()

    def test_explicit_detail_design_decision_is_not_overwritten(self) -> None:
        decisions = apply_implicit_skip_decisions(
            target_checkpoint="code_in_progress",
            decisions={"detail_design_before_code": "enabled"},
        )

        self.assertEqual(decisions, {"detail_design_before_code": "enabled"})


if __name__ == "__main__":
    unittest.main()
