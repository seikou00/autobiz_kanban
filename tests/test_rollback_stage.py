from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from board_core.state_store import load_state_json_records, write_state_records
from hooks.init_workspace import create_feature, init_workspace
from hooks.rollback_stage import (
    _prepare_code_execution_reset,
    capture_code_session_baseline,
    execute_stage_rollback,
    main,
    prepare_stage_rollback,
    prune_rollback_history,
)
from hooks.plan_json import task_set_digest, write_plan_json
from hooks.repository_snapshot import capture_repository_snapshot
from tests.test_batched_plan import batch_entry, batch_plan, root_plan, task


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

    def _write_completed_code_plan(self) -> None:
        tasks = [task("T001", status="done"), task("T002", deps=["T001"], status="implemented")]
        for index, item in enumerate(tasks, start=1):
            item["evidenceIds"] = [f"EV-T00{index}"]
            item["implementationEvidenceIds"] = [f"EV-T00{index}"]
            item["completionEvidenceIds"] = [f"EV-T00{index}"]
            item["latestImplementationEvidenceId"] = f"EV-T00{index}"
            item["latestPassEvidenceId"] = f"EV-T00{index}"
            item["implementationRevision"] = index

        batch = batch_plan("B001", tasks)
        batch["featureId"] = self.feature
        batch["status"] = "in_progress"
        batch["startedAt"] = "2026-08-18T10:00:00Z"
        batch["batchCompile"] = {"status": "passed", "runId": "compile-1"}
        batch["batchValidation"]["status"] = "passed"
        batch["batchValidation"]["evidenceIds"] = ["EV-COMPILE"]
        batch["batchValidation"]["latestPassEvidenceIds"] = ["EV-COMPILE"]

        root = root_plan(batches=[batch_entry("B001", ["T001", "T002"])])
        root["featureId"] = self.feature
        root["status"] = "in_progress"
        root["projectCheckEvidenceIds"] = ["EV-PROJECT"]
        root["latestProjectCheckEvidenceId"] = "EV-PROJECT"
        root["taskSetDigest"] = task_set_digest(root, {"B001": batch})
        write_plan_json(self.feature_dir / "plans" / "B001" / "plan.json", batch)
        write_plan_json(self.feature_dir / "plan.json", root)

    def _create_code_repository(self) -> Path:
        repository = self.plugin_workspace / "business-repo"
        repository.mkdir()
        subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.email", "rollback@example.test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.name", "Rollback Test"],
            check=True,
        )
        (repository / "app.txt").write_text("before code\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "app.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-m", "baseline"],
            check=True,
            capture_output=True,
        )
        return repository

    def _write_task_run(self, repository: Path) -> None:
        run_dir = self.feature_dir / ".task-runs" / "T001"
        run_dir.mkdir(parents=True)
        snapshot = capture_repository_snapshot(repository)
        (run_dir / "run-1.json").write_text(
            json.dumps(
                {
                    "featureId": self.feature,
                    "taskId": "T001",
                    "runId": "run-1",
                    "status": "implemented",
                    "updatedAt": "2026-08-18T10:00:00Z",
                    "fileChanges": [
                        {"repository": repository.name, "path": "app.txt", "operation": "modified"},
                        {"repository": repository.name, "path": "new.txt", "operation": "created"},
                    ],
                    "finalRepositories": [
                        {"id": repository.name, "path": str(repository), "snapshot": snapshot}
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

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
        batch_plan = self.feature_dir / "plans" / "B001" / "plan.json"
        batch_plan.parent.mkdir(parents=True)
        batch_plan.write_text("{}\n", encoding="utf-8")
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
        self.assertFalse(batch_plan.exists())
        history = self.project / ".autobizdevops" / "rollback" / "history" / plan.rollback_id
        self.assertTrue((history / "artifacts" / "plans" / "B001" / "plan.json").is_file())
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

    def test_target_in_progress_keeps_artifact_scope_identical_to_previous_done(self) -> None:
        self._set_checkpoint("plan_done")
        proposal = self.feature_dir / "proposal.md"
        proposal.write_text("delete regardless of state choice\n", encoding="utf-8")

        previous_done = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="dev.specs",
            state_mode="previous_done",
        )
        target_in_progress = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="dev.specs",
            state_mode="target_in_progress",
        )

        self.assertTrue(previous_done.ok, previous_done.errors)
        self.assertTrue(target_in_progress.ok, target_in_progress.errors)
        self.assertEqual(previous_done.new_checkpoint, "prd_done")
        self.assertEqual(target_in_progress.new_checkpoint, "specs_in_progress")
        self.assertEqual(previous_done.artifact_paths, target_in_progress.artifact_paths)

        result = execute_stage_rollback(target_in_progress)
        self.assertTrue(result.ok, result.errors)
        self.assertFalse(proposal.exists())
        records, _, _ = load_state_json_records(self.project)
        self.assertEqual(records[self.feature]["checkpoint"], "specs_in_progress")

    def test_first_stage_supports_target_in_progress_but_not_previous_done(self) -> None:
        marker = self.feature_dir / "PRD.md"
        marker.write_text("delete\n", encoding="utf-8")

        first_previous_done = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="biz.prd",
        )
        first_target_in_progress = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="biz.prd",
            state_mode="target_in_progress",
        )
        future = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="dev.specs",
        )

        self.assertFalse(first_previous_done.ok)
        self.assertIn("不能回退到前置 done", first_previous_done.errors[0])
        self.assertTrue(first_target_in_progress.ok, first_target_in_progress.errors)
        self.assertEqual(first_target_in_progress.new_checkpoint, "prd_in_progress")
        self.assertEqual(
            tuple(option["mode"] for option in first_target_in_progress.state_options),
            ("target_in_progress",),
        )
        self.assertFalse(future.ok)
        self.assertIn("尚未到达", future.errors[0])

        result = execute_stage_rollback(first_target_in_progress)
        self.assertTrue(result.ok, result.errors)
        self.assertFalse(marker.exists())
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
            exit_code = main([
                "--stage", "dev.specs", "--feature", self.feature,
                "--state-mode", "previous_done", "--dry-run", "--json",
            ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["newCheckpoint"], "prd_done")
        self.assertEqual(payload["deletedArtifacts"], [])
        self.assertEqual(payload["plannedArtifacts"], ["proposal.md"])
        self.assertTrue(artifact.exists())
        records, _, _ = load_state_json_records(self.project)
        self.assertEqual(records[self.feature]["checkpoint"], "specs_in_progress")

    def test_cli_dry_run_requests_explicit_state_choice(self) -> None:
        self._set_checkpoint("specs_in_progress")
        artifact = self.feature_dir / "proposal.md"
        artifact.write_text("keep until apply\n", encoding="utf-8")
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
        self.assertTrue(payload["confirmationRequired"])
        self.assertIsNone(payload["stateMode"])
        self.assertIsNone(payload["newCheckpoint"])
        self.assertEqual(
            [option["mode"] for option in payload["stateOptions"]],
            ["target_in_progress", "previous_done"],
        )
        self.assertEqual(payload["plannedArtifacts"], ["proposal.md"])
        self.assertTrue(artifact.exists())

    def test_cli_apply_requires_explicit_state_choice(self) -> None:
        self._set_checkpoint("specs_in_progress")
        artifact = self.feature_dir / "proposal.md"
        artifact.write_text("do not delete\n", encoding="utf-8")
        stdout = io.StringIO()
        env = {
            "PLUGIN_WORKSPACE": str(self.plugin_workspace),
            "PROJECT_DIR": "demo",
            "FEATURE_ID": self.feature,
        }

        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(stdout):
            exit_code = main(["--stage", "dev.specs", "--feature", self.feature, "--apply", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["confirmationRequired"])
        self.assertIn("--state-mode", payload["errors"][0])
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

    def test_keyboard_interrupt_during_apply_restores_moved_artifact(self) -> None:
        self._set_checkpoint("specs_in_progress")
        artifact = self.feature_dir / "proposal.md"
        artifact.write_text("restore after interrupt\n", encoding="utf-8")
        plan = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="dev.specs",
        )

        with patch("hooks.rollback_stage.write_state_records_preserving_raw", side_effect=KeyboardInterrupt):
            result = execute_stage_rollback(plan)

        self.assertFalse(result.ok)
        self.assertTrue(artifact.exists())
        self.assertIn("状态恢复失败", result.errors[0])
        records, _, _ = load_state_json_records(self.project)
        self.assertEqual(records[self.feature]["checkpoint"], "specs_in_progress")

    def test_code_rollback_resets_all_tasks_and_archives_runtime_as_one_session(self) -> None:
        self._set_checkpoint("code_done")
        self._write_completed_code_plan()
        stale_batch = self.feature_dir / "plans" / "B999" / "plan.json"
        stale_batch.parent.mkdir(parents=True)
        stale_batch.write_text("{\"stale\": true}\n", encoding="utf-8")
        task_runs = self.feature_dir / ".task-runs" / "T001"
        task_runs.mkdir(parents=True)
        (task_runs / "run.json").write_text("{}\n", encoding="utf-8")
        evidence = self.feature_dir / "evidence"
        evidence.mkdir()
        (evidence / "EVIDENCE.index.json").write_text("{}\n", encoding="utf-8")
        (evidence / "EVIDENCE.jsonl").write_text("{}\n", encoding="utf-8")
        exploration = self.feature_dir / "cache" / "code-exploration" / "business-repo"
        exploration.mkdir(parents=True)
        (exploration / "backend.json").write_text("{}\n", encoding="utf-8")
        parallel_run = self.feature_dir / ".parallel-runs" / "cw-test-001"
        parallel_run.mkdir(parents=True)
        (parallel_run / "manifest.json").write_text("{}\n", encoding="utf-8")

        plan = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="dev.code",
        )

        self.assertTrue(plan.ok, plan.errors)
        self.assertTrue(plan.code_in_scope)
        self.assertEqual(plan.new_checkpoint, "plan_done")
        self.assertEqual(plan.code_reset_tasks, ("T001", "T002"))
        result = execute_stage_rollback(plan)
        self.assertTrue(result.ok, result.errors)

        self.assertFalse((self.feature_dir / ".task-runs").exists())
        self.assertFalse((self.feature_dir / "evidence").exists())
        self.assertFalse((self.feature_dir / "cache" / "code-exploration").exists())
        self.assertFalse((self.feature_dir / ".parallel-runs").exists())
        self.assertFalse(stale_batch.exists())
        history = self.project / ".autobizdevops" / "rollback" / "history" / plan.rollback_id
        self.assertTrue((history / "artifacts" / ".task-runs" / "T001" / "run.json").is_file())
        self.assertTrue((history / "artifacts" / "evidence" / "EVIDENCE.jsonl").is_file())
        self.assertTrue((history / "artifacts" / "cache" / "code-exploration" / "business-repo" / "backend.json").is_file())
        self.assertTrue((history / "artifacts" / ".parallel-runs" / "cw-test-001" / "manifest.json").is_file())
        self.assertTrue((history / "plan" / "plans" / "B999" / "plan.json").is_file())
        self.assertTrue((history / "state-before.json").is_file())
        self.assertTrue((history / "state-after.json").is_file())

        root = json.loads((self.feature_dir / "plan.json").read_text(encoding="utf-8"))
        batch = json.loads(
            (self.feature_dir / "plans" / "B001" / "plan.json").read_text(encoding="utf-8")
        )
        self.assertEqual(root["status"], "todo")
        self.assertEqual(root["projectCheckEvidenceIds"], [])
        self.assertNotIn("batchCompile", batch)
        self.assertEqual(batch["batchValidation"]["status"], "pending")
        self.assertEqual([item["status"] for item in batch["tasks"]], ["todo", "todo"])
        self.assertTrue(all(item["evidenceIds"] == [] for item in batch["tasks"]))
        records, _, _ = load_state_json_records(self.project)
        self.assertEqual(records[self.feature]["checkpoint"], "plan_done")

    def test_code_source_restore_uses_session_baseline_and_removes_created_files(self) -> None:
        self._set_checkpoint("code_done")
        repository = self._create_code_repository()
        session = capture_code_session_baseline(
            workspace=self.project,
            feature=self.feature,
            code_workspaces=[repository],
        )
        (repository / "app.txt").write_text("feature implementation\n", encoding="utf-8")
        (repository / "new.txt").write_text("new feature file\n", encoding="utf-8")
        self._write_task_run(repository)

        plan = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="dev.code",
            code_source="restore",
        )
        self.assertTrue(plan.ok, plan.errors)
        self.assertEqual(
            plan.planned_source_files,
            (f"{repository.name}:app.txt", f"{repository.name}:new.txt"),
        )

        result = execute_stage_rollback(plan)

        self.assertTrue(result.ok, result.errors)
        self.assertEqual((repository / "app.txt").read_text(encoding="utf-8"), "before code\n")
        self.assertFalse((repository / "new.txt").exists())
        active = json.loads(
            (
                self.project
                / ".autobizdevops"
                / "rollback"
                / "baselines"
                / self.feature
                / "active.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(active["sessionId"], session["sessionId"])
        self.assertEqual(active["status"], "rolled_back")

    def test_code_source_restore_blocks_when_file_changed_after_task_snapshot(self) -> None:
        self._set_checkpoint("code_done")
        repository = self._create_code_repository()
        capture_code_session_baseline(
            workspace=self.project,
            feature=self.feature,
            code_workspaces=[repository],
        )
        (repository / "app.txt").write_text("feature implementation\n", encoding="utf-8")
        (repository / "new.txt").write_text("new feature file\n", encoding="utf-8")
        self._write_task_run(repository)
        (repository / "app.txt").write_text("manual change after code\n", encoding="utf-8")

        restore = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="dev.code",
            code_source="restore",
        )
        keep = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="dev.code",
            code_source="keep",
        )

        self.assertFalse(restore.ok)
        self.assertIn(f"{repository.name}:app.txt", restore.errors)
        self.assertTrue(keep.ok, keep.errors)
        self.assertEqual(
            keep.planned_source_files,
            (f"{repository.name}:app.txt", f"{repository.name}:new.txt"),
        )
        self.assertEqual((repository / "app.txt").read_text(encoding="utf-8"), "manual change after code\n")

    def test_code_keep_allows_in_progress_run_but_restore_blocks(self) -> None:
        self._set_checkpoint("code_in_progress")
        repository = self._create_code_repository()
        capture_code_session_baseline(
            workspace=self.project,
            feature=self.feature,
            code_workspaces=[repository],
        )
        (repository / "app.txt").write_text("partial implementation\n", encoding="utf-8")
        run_dir = self.feature_dir / ".task-runs" / "T001"
        run_dir.mkdir(parents=True)
        (run_dir / "run-1.json").write_text(
            json.dumps(
                {
                    "featureId": self.feature,
                    "taskId": "T001",
                    "runId": "run-1",
                    "status": "started",
                    "fileChanges": [
                        {"repository": repository.name, "path": "app.txt", "operation": "modified"}
                    ],
                    "repositories": [
                        {
                            "id": repository.name,
                            "path": str(repository),
                            "snapshot": capture_repository_snapshot(repository),
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        keep = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="dev.code",
            code_source="keep",
        )
        restore = prepare_stage_rollback(
            workspace=self.project,
            feature=self.feature,
            stage="dev.code",
            code_source="restore",
        )

        self.assertTrue(keep.ok, keep.errors)
        self.assertEqual(keep.planned_source_files, (f"{repository.name}:app.txt",))
        self.assertFalse(restore.ok)
        self.assertIn("存在未结束的 Code task run", restore.errors[0])

    def test_code_reset_discards_stale_batch_projection_before_rebuild(self) -> None:
        plan_path = self.feature_dir / "plan.json"
        plan_path.write_text("{}\n", encoding="utf-8")
        data = {
            "featureId": self.feature,
            "status": "done",
            "activeBatchId": "B001",
            "nextBatchId": "B999",
            "batches": [{"id": "B001"}, {"id": "B999"}],
            "tasks": [{
                "id": "T001",
                "status": "done",
                "evidenceIds": ["EV-1"],
                "implementationEvidenceIds": ["EV-1"],
                "validationEvidenceIds": ["EV-1"],
                "completionEvidenceIds": ["EV-1"],
                "latestImplementationEvidenceId": "EV-1",
                "latestPassEvidenceId": "EV-1",
                "implementationRevision": 2,
            }],
            "_batchAssignments": {"T001": "B999"},
            "_batchPlans": {
                "B001": {"status": "done"},
                "B999": {"status": "done"},
            },
        }
        with patch("hooks.rollback_stage.load_plan_writer_data", return_value=data):
            reset = _prepare_code_execution_reset(self.project, self.feature)

        self.assertTrue(reset.present)
        self.assertEqual(reset.data["batches"], [])
        self.assertEqual(reset.data["_batchAssignments"], {})
        self.assertEqual(reset.data["_batchPlans"], {})
        self.assertEqual(reset.data["tasks"][0]["status"], "todo")

    def test_capture_code_session_uses_feature_lock(self) -> None:
        repository = self._create_code_repository()
        with patch("hooks.rollback_stage.FileLock") as lock_factory:
            lock = lock_factory.return_value
            lock.__enter__.return_value = lock
            capture_code_session_baseline(
                workspace=self.project,
                feature=self.feature,
                code_workspaces=[repository],
            )

        lock_factory.assert_called_once_with(
            self.project / ".autobizdevops" / "rollback" / "locks" / f"{self.feature}.lock"
        )

    def test_prune_history_keeps_latest_entries_per_feature(self) -> None:
        history_root = self.project / ".autobizdevops" / "rollback" / "history"
        for index in range(3):
            entry = history_root / f"rollback-{index}"
            entry.mkdir(parents=True)
            (entry / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "committed",
                        "feature": self.feature,
                        "committedAt": f"2026-08-18T10:0{index}:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

        preview = prune_rollback_history(
            workspace=self.project,
            feature=self.feature,
            keep=2,
            apply=False,
        )
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["deleted"], [])
        self.assertEqual(len(preview["planned"]), 1)
        self.assertTrue((history_root / "rollback-0").exists())

        result = prune_rollback_history(
            workspace=self.project,
            feature=self.feature,
            keep=2,
            apply=True,
        )
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["deleted"], [
            f".autobizdevops/rollback/history/rollback-0",
        ])
        self.assertFalse((history_root / "rollback-0").exists())
        self.assertTrue((history_root / "rollback-1").exists())
        self.assertTrue((history_root / "rollback-2").exists())


if __name__ == "__main__":
    unittest.main()
