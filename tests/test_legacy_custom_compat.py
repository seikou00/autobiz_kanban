from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from hooks.init_workspace import create_feature, init_workspace
from hooks.route_checkpoint import resolve_route
from inspect_state import _load_board_config, project_mode, run_mode
from read_state_json import _build_payload, _read_feature_checkpoint
from board_core.state_store import check_or_fix_state_sync, load_state_json_records


def _legacy_custom_record(feature: str) -> dict:
    return {
        "feature": feature,
        "owner": "\u2014",
        "checkpoint": "code_in_progress",
        "stage": "Code",
        "iteration": "\u2014",
        "updated_at": "2026-01-01 00:00:00",
        "workflowProfile": "standard",
        "workflowDecisions": {},
        "workflowTemplate": "custom",
        "workflowNodes": ["dev.specs", "dev.code", "ops.archive"],
    }


def _write_state_json(project: Path, features: dict[str, object]) -> None:
    state_json = project / ".autobizdevops" / "state.json"
    state_json.write_text(
        json.dumps(
            {
                "schemaVersion": "autobizdevops.state.v3",
                "features": features,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _capture_json(callable_, *args) -> dict:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = callable_(*args)
    assert exit_code == 0
    return json.loads(output.getvalue())


class LegacyCustomCompatibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "demo"
        self.project.mkdir()
        init_workspace(self.project)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_feature_and_project_status_read_legacy_custom(self) -> None:
        _write_state_json(self.project, {"legacy-custom": _legacy_custom_record("legacy-custom")})
        config = _load_board_config()

        feature_payload = _capture_json(run_mode, self.project, "legacy-custom", config)
        self.assertEqual(feature_payload["run"]["workflowTemplate"], "custom")
        self.assertEqual(feature_payload["run"]["currentNodeId"], "dev.code")
        self.assertEqual(
            [node["id"] for node in feature_payload["run"]["nodes"]],
            ["dev.specs", "dev.code", "ops.archive"],
        )

        project_payload = _capture_json(project_mode, self.root, ["demo"], config)
        self.assertEqual(project_payload["projects"]["demo"]["runs"][0]["featureId"], "legacy-custom")
        self.assertEqual(project_payload["projects"]["demo"]["runs"][0]["workflowTemplate"], "custom")

    def test_other_invalid_feature_does_not_block_current_feature_route(self) -> None:
        _write_state_json(
            self.project,
            {
                "legacy-custom": _legacy_custom_record("legacy-custom"),
                "broken-other": {
                    "feature": "broken-other",
                    "checkpoint": "missing_checkpoint",
                    "workflowTemplate": "standard",
                },
            },
        )

        payload, exit_code = resolve_route(self.project, "legacy-custom")

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["workflowTemplate"], "custom")
        self.assertEqual(payload["currentNodeId"], "dev.code")

    def test_other_invalid_feature_does_not_block_read_state_json(self) -> None:
        _write_state_json(
            self.project,
            {
                "legacy-custom": _legacy_custom_record("legacy-custom"),
                "broken-other": {
                    "feature": "broken-other",
                    "checkpoint": "missing_checkpoint",
                    "workflowTemplate": "standard",
                },
            },
        )

        checkpoint, exit_code = _read_feature_checkpoint(self.project, "legacy-custom")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            broken_checkpoint, broken_exit_code = _read_feature_checkpoint(self.project, "broken-other")
        payload, payload_exit_code = _build_payload(self.project)

        self.assertEqual(exit_code, 0)
        self.assertEqual(checkpoint, "code_in_progress")
        self.assertEqual(broken_checkpoint, "")
        self.assertEqual(broken_exit_code, 1)
        self.assertEqual(payload_exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertIn("legacy-custom", payload["records"])
        self.assertIn("broken-other", payload["recordErrors"])

    def test_create_feature_preserves_unrelated_invalid_records(self) -> None:
        _write_state_json(
            self.project,
            {
                "broken-other": {
                    "feature": "broken-other",
                    "checkpoint": "missing_checkpoint",
                    "workflowTemplate": "standard",
                },
            },
        )

        create_feature(self.project, "fresh-standard")

        payload = json.loads((self.project / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))
        self.assertIn("fresh-standard", payload["features"])
        self.assertIn("broken-other", payload["features"])
        self.assertEqual(payload["features"]["broken-other"]["checkpoint"], "missing_checkpoint")

    def test_fresh_standard_feature_starts_at_prd_in_progress(self) -> None:
        create_feature(self.project, "fresh-prd")

        records, errors, exists = load_state_json_records(self.project)

        self.assertTrue(exists)
        self.assertEqual(errors, [])
        self.assertEqual(records["fresh-prd"]["checkpoint"], "prd_in_progress")
        self.assertEqual(records["fresh-prd"]["stage"], "Biz / 需求澄清与 PRD")

    def test_discuss_checkpoint_and_custom_node_are_migrated_to_biz_prd(self) -> None:
        legacy = {
            "feature": "legacy-discuss",
            "owner": "tester",
            "checkpoint": "discuss_done",
            "stage": "Biz / 需求澄清",
            "iteration": "1",
            "updated_at": "2026-08-12 12:00:00",
            "workflowTemplate": "custom",
            "workflowNodes": ["biz.discuss", "biz.prd", "dev.code", "ops.archive"],
            "workflowSkippedNodes": ["biz.discuss"],
        }
        _write_state_json(self.project, {"legacy-discuss": legacy})

        result = check_or_fix_state_sync(self.project, fix=True)

        self.assertTrue(result.ok, result.errors)
        record = result.records["legacy-discuss"]
        self.assertEqual(record["checkpoint"], "prd_in_progress")
        self.assertEqual(record["stage"], "Biz / 需求澄清与 PRD")
        self.assertEqual(record["workflowNodes"], ["biz.prd", "dev.code", "ops.archive"])
        self.assertNotIn("workflowSkippedNodes", record)

    def test_new_custom_feature_is_rejected(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                create_feature(
                    self.project,
                    "new-custom",
                    workflow_template="custom",
                    workflow_nodes=["dev.specs", "dev.code", "ops.archive"],
                )

        self.assertEqual(raised.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
