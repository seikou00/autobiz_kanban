from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from board_core.contracts import load_board_config, load_record_workflow_contracts
from board_core.state_store import load_state_json_records, write_state_records
from board_core.workflow_compiler import configured_template_options
from hooks.init_workspace import create_feature, init_workspace
from hooks.route_checkpoint import resolve_route


ROOT = Path(__file__).resolve().parents[1]
BOARD_CONFIG_PATH = ROOT / "board_core" / "board_config.json"


class LegacyLeanCompatibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name).resolve() / "demo"
        self.project.mkdir()
        init_workspace(self.project)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_template_catalog_does_not_expose_lean(self) -> None:
        options = configured_template_options(load_board_config(BOARD_CONFIG_PATH))

        self.assertNotIn("lean", [option["id"] for option in options])

    def test_new_feature_rejects_lean(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            create_feature(self.project, "fresh-lean", workflow_template="lean")

        self.assertIn("已不可用于新建 Feature", stderr.getvalue())
        self.assertFalse((self.project / ".autobizdevops" / "features" / "fresh-lean").exists())

    def test_existing_lean_feature_remains_loadable_and_routable(self) -> None:
        feature = "legacy-lean"
        (self.project / ".autobizdevops" / "features" / feature).mkdir(parents=True)
        write_state_records(
            self.project,
            {
                feature: {
                    "checkpoint": "code_in_progress",
                    "workflowProfile": "standard",
                    "workflowDecisions": {},
                    "workflowTemplate": "lean",
                }
            },
        )

        records, errors, exists = load_state_json_records(self.project)
        payload, exit_code = resolve_route(self.project, feature)
        contracts = load_record_workflow_contracts(
            ROOT,
            records[feature],
            workspace=self.project,
        )

        self.assertTrue(exists)
        self.assertEqual([], errors)
        self.assertEqual("lean", records[feature]["workflowTemplate"])
        self.assertEqual(0, exit_code)
        self.assertTrue(payload["ok"])
        self.assertEqual("dev.code", payload["currentNodeId"])
        self.assertEqual(
            ["dev.specs", "dev.code", "ops.archive"],
            [node["id"] for node in contracts.nodes],
        )


if __name__ == "__main__":
    unittest.main()
