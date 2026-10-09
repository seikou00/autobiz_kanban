"""Exercise real task/lease/worktree state through the Code compile guard."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hooks.code_execution_guard import guard
from hooks.parallel_batch_scheduler import create_run, mark_batch
from hooks.parallel_batch_stage import complete_stage, fail_stage, finalize_implementation, start_stage
from hooks.parallel_evidence_aggregate import _evidence_errors
from hooks.parallel_runtime import acquire_lease, load_manifest, release_lease, save_manifest
from hooks.task_runner import TaskRunnerError, abort_task, finish_implementation, resume_task, start_task, start_task_repair
from hooks.worktree_manager import provision_parallel_worktree, seal_parallel_batch
from tests.test_task_runner import _configure_defer_to_test_stages, _run, _workspace


class CodeStageAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="code-stage-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.workspace, self.feature_dir, self.repo = _workspace(root)
        _configure_defer_to_test_stages(self.feature_dir)
        created = create_run(
            self.workspace, "alpha", max_parallel=1, timeout_seconds=60,
            code_workspaces=[str(self.repo)], task_card_id="Z990692-294",
        )
        self.parallel_id = created["runId"]
        provisioned = provision_parallel_worktree(self.workspace, "alpha", self.parallel_id, "B001")
        self.worktree = Path(provisioned["worktreePath"])
        self.branch = provisioned["branchName"]
        self.token = self._acquire()
        self.environment = {
            "PLUGIN_WORKSPACE": str(self.workspace.parent),
            "PROJECT_DIR": self.workspace.name,
            "FEATURE_ID": "alpha",
        }

    def _acquire(self) -> str:
        lease = acquire_lease(self.workspace, "alpha", self.parallel_id, "B001", ttl_seconds=60)
        mark_batch(self.workspace, "alpha", self.parallel_id, "B001", "running", worktreePath=str(self.worktree), branchName=self.branch)
        return lease["ownerToken"]

    def _batch(self) -> dict:
        return load_manifest(self.workspace, "alpha", self.parallel_id)["batches"]["B001"]

    def _task(self) -> dict:
        return start_task(self.workspace, "alpha", "T001", self.worktree, parallel_run_id=self.parallel_id, lease_token=self.token, workspace_ref="default")

    def _guard(self, command: str) -> str | None:
        with patch.dict(os.environ, self.environment):
            return guard({"tool_name": "execute", "tool_input": {"command": command, "cwd": str(self.worktree)}})

    def _finish(self, state: dict, *, repair: bool = False) -> dict:
        ok, finished = finish_implementation(
            self.workspace, "alpha", "T001", self.worktree, state["runId"],
            no_code_change_why=None, supporting_files=[], repair_mode=repair,
            parallel_run_id=self.parallel_id, lease_token=self.token, workspace_ref="default",
        )
        self.assertTrue(ok)
        return finished

    def _seal(self) -> dict:
        result = seal_parallel_batch(self.workspace, "alpha", self.parallel_id, "B001", self.worktree, self.token, purpose="review")
        self.assertTrue(result["success"], result)
        return result

    def _initial_delivery(self) -> tuple[dict, dict]:
        state = self._task()
        (self.worktree / "production.txt").write_text("production\n", encoding="utf-8")
        finished = self._finish(state)
        draft = self._seal()
        finalized = finalize_implementation(self.workspace, "alpha", self.parallel_id, "B001")
        self.assertEqual(finalized["status"], "passed")
        return finished, draft

    def test_real_cli_start_registers_code_and_none_does_not_block_compile(self) -> None:
        self.assertIn("ACTIVE_TASK_RUN_MISSING", self._guard("mvn compile"))
        result = _run(
            "start", "--workspace", str(self.workspace), "--feature", "alpha",
            "--task-id", "T001", "--code-workspace", str(self.worktree),
            "--parallel-run-id", self.parallel_id, "--lease-token", self.token,
            "--workspace-ref", "default",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = json.loads(result.stdout)
        batch = self._batch()
        self.assertEqual(batch["activeStage"], "implement")
        self.assertEqual(batch["stageStates"]["prepare"]["status"], "passed")
        self.assertEqual(batch["stageStates"]["implement"]["status"], "running")
        manifest = load_manifest(self.workspace, "alpha", self.parallel_id)
        manifest["batches"]["B001"]["activeStage"] = None
        save_manifest(self.workspace, "alpha", self.parallel_id, manifest)
        for command in [
            "mvn clean compile -DskipTests",
            f'cd "{self.worktree}" && mvn clean compile -Dmaven.test.skip=true 2>&1 | tail -100',
            'set -o pipefail && mvn compile 2>&1 | tee compile.log',
        ]:
            with self.subTest(command=command):
                self.assertIsNone(self._guard(command))
        self.assertIn("VALIDATION_GOALS_NOT_ALLOWED_IN_CODE", self._guard("mvn test"))
        (self.worktree / "production.txt").write_text("production\n", encoding="utf-8")
        self._finish(state)
        self.assertEqual(self._batch()["activeStage"], "implement")
        self.assertIn("TASK_RUN_INACTIVE", self._guard("mvn compile"))

    def test_sealed_stage_evidence_remains_fresh_through_utest_reseal(self) -> None:
        _, draft = self._initial_delivery()
        batch = self._batch()
        preparation = json.loads(Path(batch["stageStates"]["prepare"]["evidencePath"]).read_text(encoding="utf-8"))
        self.assertEqual(preparation["inputs"]["commitScope"], "repository_base")
        self.assertNotEqual(preparation["inputs"]["batchCommit"], draft["commitSha"])
        self.assertEqual(batch["stageStates"]["implement"]["status"], "passed")
        start_stage(self.workspace, "alpha", self.parallel_id, "B001", "review")
        self.assertIn("BATCH_STAGE_NOT_CODE_OR_REPAIR", self._guard("mvn compile"))
        complete_stage(self.workspace, "alpha", self.parallel_id, "B001", "review")
        start_stage(self.workspace, "alpha", self.parallel_id, "B001", "test")
        self.assertIsNone(self._guard("mvn test"))
        tests = self.worktree / "tests"
        tests.mkdir()
        (tests / "test_production.py").write_text("def test_production():\n    assert True\n", encoding="utf-8")
        resealed = seal_parallel_batch(self.workspace, "alpha", self.parallel_id, "B001", self.worktree, self.token)
        self.assertTrue(resealed["success"], resealed)
        self.assertEqual(resealed["purpose"], "utest")
        complete_stage(self.workspace, "alpha", self.parallel_id, "B001", "test")
        manifest = load_manifest(self.workspace, "alpha", self.parallel_id)
        self.assertEqual(_evidence_errors(manifest, "B001", manifest["batches"]["B001"]), [])

    def test_repair_reopens_code_before_compilation_and_keeps_attempt(self) -> None:
        finished, _ = self._initial_delivery()
        release_lease(self.workspace, "alpha", self.parallel_id, "B001", self.token, final_status="sealed")
        start_stage(self.workspace, "alpha", self.parallel_id, "B001", "review")
        fail_stage(self.workspace, "alpha", self.parallel_id, "B001", "review", failure_type="implementation", message="production defect")
        self.token = self._acquire()
        repaired = start_task_repair(
            self.workspace, "alpha", "T001", self.worktree,
            prior_evidence_id=finished["implementationEvidenceId"],
            parallel_run_id=self.parallel_id, lease_token=self.token, workspace_ref="default",
        )
        batch = self._batch()
        self.assertEqual(batch["activeStage"], "implement")
        self.assertEqual(batch["stageStates"]["implement"]["attempt"], 2)
        self.assertIsNone(self._guard("mvn compile 2>&1 | tail -100"))
        (self.worktree / "production.txt").write_text("repaired production\n", encoding="utf-8")
        self._finish(repaired, repair=True)
        self.assertEqual(self._batch()["stageStates"]["implement"]["attempt"], 2)
        self._seal()
        finalize_implementation(self.workspace, "alpha", self.parallel_id, "B001")
        self.assertEqual(self._batch()["stageStates"]["implement"]["status"], "passed")

    def test_aborted_task_resume_restores_stage_without_new_attempt(self) -> None:
        state = self._task()
        (self.worktree / "production.txt").write_text("preserve\n", encoding="utf-8")
        abort_task(self.workspace, "alpha", "T001", self.worktree, state["runId"], force_with_changes=True, abort_why="interrupted", workspace_ref="default")
        manifest = load_manifest(self.workspace, "alpha", self.parallel_id)
        manifest["batches"]["B001"]["activeStage"] = None
        save_manifest(self.workspace, "alpha", self.parallel_id, manifest)
        resumed = resume_task(self.workspace, "alpha", "T001", self.worktree, state["runId"], parallel_run_id=self.parallel_id, lease_token=self.token, workspace_ref="default")
        self.assertEqual(resumed["runId"], state["runId"])
        self.assertEqual(self._batch()["activeStage"], "implement")
        self.assertEqual(self._batch()["stageStates"]["implement"]["attempt"], 1)
        self.assertIsNone(self._guard("mvn clean compile 2>&1 | tail -100"))
        self.assertEqual((self.worktree / "production.txt").read_text(encoding="utf-8"), "preserve\n")

    def test_finalize_cannot_hide_an_active_task(self) -> None:
        self._task()
        (self.worktree / "production.txt").write_text("unfinished\n", encoding="utf-8")
        self._seal()
        with self.assertRaisesRegex(ValueError, "implementation_tasks_active"):
            finalize_implementation(self.workspace, "alpha", self.parallel_id, "B001")
        self.assertEqual(self._batch()["stageStates"]["implement"]["status"], "running")

    def test_legacy_sealed_run_is_adopted_only_after_tasks_finished(self) -> None:
        state = self._task()
        (self.worktree / "production.txt").write_text("legacy delivery\n", encoding="utf-8")
        self._finish(state)
        draft = self._seal()
        manifest = load_manifest(self.workspace, "alpha", self.parallel_id)
        for name in ("prepare", "implement"):
            manifest["batches"]["B001"]["stageStates"][name].update(status="pending", attempt=0, latestEvidenceId=None)
        manifest["batches"]["B001"]["activeStage"] = None
        save_manifest(self.workspace, "alpha", self.parallel_id, manifest)
        finalized = finalize_implementation(self.workspace, "alpha", self.parallel_id, "B001")
        self.assertEqual(finalized["evidence"]["inputs"]["batchCommit"], draft["commitSha"])
        again = finalize_implementation(self.workspace, "alpha", self.parallel_id, "B001")
        self.assertTrue(again["reused"])

    def test_start_repair_during_review_does_not_create_active_task(self) -> None:
        finished, _ = self._initial_delivery()
        start_stage(self.workspace, "alpha", self.parallel_id, "B001", "review")
        before = list((self.feature_dir / ".task-runs" / "T001").glob("*.json"))
        with self.assertRaisesRegex(TaskRunnerError, "implementation_stage_conflict"):
            start_task_repair(
                self.workspace, "alpha", "T001", self.worktree,
                prior_evidence_id=finished["implementationEvidenceId"],
                parallel_run_id=self.parallel_id, lease_token=self.token, workspace_ref="default",
            )
        self.assertEqual(list((self.feature_dir / ".task-runs" / "T001").glob("*.json")), before)
        self.assertEqual(self._batch()["activeStage"], "review")


if __name__ == "__main__":
    unittest.main()
