from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from board_core.state_store import load_state_json_records, write_state_records
from hooks.init_workspace import create_feature, init_workspace
from hooks.rollback_stage import (
    execute_stage_rollback,
    main,
    prepare_stage_rollback,
)


class RollbackStageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.plugin_workspace = Path(self.tmp.name).resolve()
        self.project = self.plugin_workspace / "demo"
        self.project.mkdir()
        init_workspace(self.project)
        self.feature = "rollback-feature"
        create_feature(self.project, self.feature)
        self.feature_dir = self.project / ".autobizdevops" / "features" / self.feature

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _set_checkpoint(self, checkpoint: str, **updates: object) -> None:
        records, errors, exists = load_state_json_records(self.project)
        self.assertTrue(exists)
        self.assertEqual(errors, [])
        record = dict(records[self.feature])
        record.update(updates)
        record["checkpoint"] = checkpoint
        record["stage"] = checkpoint
        records[self.feature] = record
        write_state_records(self.project, records)

    def test_deletes_target_and_downstream_outputs_then_updates_state(self) -> None:
        self._set_checkpoint("verify_done")
        keep = {
            "PRD.md": "prd",
            "UNIT_TEST_REPORT.md": "unit",
            "test-output.log": "unit log",
        }
        delete = {
            "E2E_TEST_CASES.yaml": "cases",
            "E2E_QUALITY_SCAN.json": "quality",
            "E2E_REPORT.md": "e2e",
            "e2e-run.log": "e2e log",
            "VERIFY_REPORT.md": "verify",
            "CICD_CHECKLIST.md": "cicd",
            "PR_BODY.md": "pr",
        }
        for name, content in {**keep, **delete}.items():
            (self.feature_dir / name).write_text(content, encoding="utf-8")
        diagnostics = self.feature_dir / "e2e-diagnostics" / "round-1"
        diagnostics.mkdir(parents=True)
        (diagnostics / "report.json").write_text("{}\n", encoding="utf-8")
        diagnostics_lock = self.feature_dir / "e2e-diagnostics" / "e2e-run.lock"
        diagnostics_lock.write_text("0", encoding="utf-8")

        plan = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="dev.e2e",
            updated_at="2026-07-29 12:00:00",
        )
        self.assertTrue(plan.ok, plan.errors)
        self.assertEqual(plan.new_checkpoint, "unit_test_done")

        result = execute_stage_rollback(plan)

        self.assertTrue(result.ok, result.errors)
        for name in keep:
            self.assertTrue((self.feature_dir / name).exists(), name)
        for name in delete:
            self.assertFalse((self.feature_dir / name).exists(), name)
        self.assertFalse((diagnostics / "report.json").exists())
        self.assertFalse(diagnostics_lock.exists())
        records, errors, _ = load_state_json_records(self.project)
        self.assertEqual(errors, [])
        self.assertEqual(records[self.feature]["checkpoint"], "unit_test_done")
        self.assertNotEqual(records[self.feature]["stage"], "verify_done")

    def test_glob_removes_only_declared_files_and_prunes_empty_directories(self) -> None:
        self._set_checkpoint("plan_done")
        (self.feature_dir / "PRD.md").write_text("keep\n", encoding="utf-8")
        (self.feature_dir / "proposal.md").write_text("delete\n", encoding="utf-8")
        (self.feature_dir / "design.md").write_text("delete\n", encoding="utf-8")
        (self.feature_dir / "PLAN.md").write_text("delete\n", encoding="utf-8")
        specs_dir = self.feature_dir / "specs" / "nested"
        specs_dir.mkdir(parents=True)
        (specs_dir / "requirements.md").write_text("delete\n", encoding="utf-8")
        (specs_dir / "notes.txt").write_text("keep\n", encoding="utf-8")

        plan = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="specs",
        )
        result = execute_stage_rollback(plan)

        self.assertTrue(result.ok, result.errors)
        self.assertTrue((self.feature_dir / "PRD.md").exists())
        self.assertTrue((specs_dir / "notes.txt").exists())
        self.assertFalse((specs_dir / "requirements.md").exists())
        self.assertFalse((self.feature_dir / "proposal.md").exists())
        self.assertFalse((self.feature_dir / "design.md").exists())
        self.assertFalse((self.feature_dir / "PLAN.md").exists())
        records, _, _ = load_state_json_records(self.project)
        self.assertEqual(records[self.feature]["checkpoint"], "prd_done")

    def test_respects_dynamic_workflow_stage(self) -> None:
        self._set_checkpoint(
            "code_in_progress",
            workflowDecisions={"detail_design_before_code": "enabled"},
        )
        detail_design = self.feature_dir / "DETAIL_DESIGN.md"
        detail_design.write_text("delete\n", encoding="utf-8")

        plan = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="detail_design",
        )
        result = execute_stage_rollback(plan)

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(plan.target_node_id, "dev.detail_design")
        self.assertEqual(plan.new_checkpoint, "plan_done")
        self.assertFalse(detail_design.exists())

    def test_rejects_first_or_not_yet_reached_stage_without_mutation(self) -> None:
        marker = self.feature_dir / "PRD.md"
        marker.write_text("keep\n", encoding="utf-8")

        first = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="biz.prd",
        )
        future = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="dev.specs",
        )

        self.assertFalse(first.ok)
        self.assertIn("首个有效阶段", first.errors[0])
        self.assertFalse(future.ok)
        self.assertIn("尚未到达", future.errors[0])
        self.assertTrue(marker.exists())
        records, _, _ = load_state_json_records(self.project)
        self.assertEqual(records[self.feature]["checkpoint"], "prd_in_progress")

    def test_archived_feature_is_restored_to_active_directory(self) -> None:
        self._set_checkpoint("archived", iteration="1")
        archive_dir = self.project / ".autobizdevops" / "archive" / f"{self.feature}-iter1"
        self.feature_dir.replace(archive_dir)

        plan = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="ops.archive",
        )
        result = execute_stage_rollback(plan)

        self.assertTrue(result.ok, result.errors)
        self.assertTrue(result.restored_active_dir)
        self.assertTrue(self.feature_dir.is_dir())
        self.assertFalse(archive_dir.exists())
        records, _, _ = load_state_json_records(self.project)
        self.assertEqual(records[self.feature]["checkpoint"], "cicd_done")

    def test_cli_dry_run_does_not_delete_or_update(self) -> None:
        self._set_checkpoint("specs_in_progress")
        artifact = self.feature_dir / "proposal.md"
        artifact.write_text("keep for dry run\n", encoding="utf-8")
        stdout = io.StringIO()
        env = {
            "PLUGIN_WORKSPACE": str(self.plugin_workspace),
            "PROJECT_DIR": "demo",
            "FEATURE_ID": self.feature,
        }

        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(stdout):
            exit_code = main(["--stage", "dev.specs", "--feature", self.feature, "--dry-run", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["newCheckpoint"], "prd_done")
        self.assertEqual(payload["deletedArtifacts"], [])
        self.assertEqual(payload["plannedArtifacts"], ["proposal.md"])
        self.assertTrue(artifact.exists())
        records, _, _ = load_state_json_records(self.project)
        self.assertEqual(records[self.feature]["checkpoint"], "specs_in_progress")

    def test_state_write_failure_restores_artifact_and_checkpoint(self) -> None:
        self._set_checkpoint("specs_in_progress")
        artifact = self.feature_dir / "proposal.md"
        artifact.write_text("restore me\n", encoding="utf-8")
        plan = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="dev.specs",
        )
        real_writer = write_state_records
        calls = 0

        def fail_once(workspace: Path, records: dict, *, raw_records: dict) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("simulated state write failure")
            real_writer(workspace, records)

        with patch("hooks.rollback_stage.write_state_records_preserving_raw", side_effect=fail_once):
            result = execute_stage_rollback(plan)

        self.assertFalse(result.ok)
        self.assertIn("产物与状态已恢复", result.errors[0])
        self.assertTrue(artifact.exists())
        self.assertEqual(artifact.read_text(encoding="utf-8"), "restore me\n")
        records, _, _ = load_state_json_records(self.project)
        self.assertEqual(records[self.feature]["checkpoint"], "specs_in_progress")


if __name__ == "__main__":
    unittest.main()
