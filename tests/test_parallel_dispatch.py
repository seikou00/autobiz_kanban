"""Exercise durable grants with real manifests, concurrency and retry transitions."""
import concurrent.futures
import base64
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from hooks.parallel_dispatch import claim, defer, finish, main as dispatch_main
from hooks.parallel_runtime import acquire_lease, lease_path, load_manifest, save_manifest
from hooks.parallel_batch_scheduler import mark_batch, resume_run, schedule
from hooks.parallel_batch_stage import start_stage, complete_stage
from hooks.task_runner import start_task
from hooks.worktree_manager import provision_parallel_worktree
from tests.test_parallel_batch_runtime import create_run
from tests.test_task_runner import _workspace, _configure_defer_to_test_stages


class DispatchGrantTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workspace, feature, repo = _workspace(Path(tmp.name))
        _configure_defer_to_test_stages(feature)
        self.run = create_run(self.workspace, "alpha", code_workspaces=[str(repo)], max_parallel=2, timeout_seconds=60)["runId"]

    def claim(self, gen=1, worker="worker"):
        return claim(self.workspace, "alpha", self.run, "B001", gen, "initial", worker)

    def test_unclaimed_legacy_null_dispatch_is_claimable_and_stale_callbacks_are_ignored(self):
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["batches"]["B001"]["dispatch"] = None
        save_manifest(self.workspace, "alpha", self.run, manifest)
        args = (self.workspace, "alpha", self.run, "B001", 1, "old-owner")
        self.assertEqual(finish(*args)["status"], "stale_owner_ignored")
        self.assertEqual(defer(*args, "old failure")["status"], "stale_owner_ignored")
        self.assertEqual(self.claim()["status"], "claimed")

    def test_concurrent_snapshot_claim_has_one_owner_and_finish_does_not_replay(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: self.claim(worker=str(i)), range(8)))
        owners = [r for r in results if r["status"] == "claimed"]
        self.assertEqual(1, len(owners))
        finish(self.workspace, "alpha", self.run, "B001", 1, owners[0]["ownerToken"])
        self.assertEqual("grant_consumed", self.claim()["reasonCode"])
        self.assertEqual(1, schedule(self.workspace, "alpha", self.run)["dispatchGrants"]["B001"])

    def test_same_batch_retry_gets_new_generation_and_stale_owner_cannot_reclaim(self):
        first = self.claim()
        self.assertEqual("retry_pending", defer(self.workspace, "alpha", self.run, "B001", 1, first["ownerToken"], "transport_failure")["status"])
        resumed = resume_run(self.workspace, "alpha", self.run)
        self.assertEqual(2, resumed["dispatchGrants"]["B001"])
        second = self.claim(2)
        self.assertEqual("claimed", second["status"])
        acquire_lease(self.workspace, "alpha", self.run, "B001", ttl_seconds=60)
        before = lease_path(self.workspace, "alpha", self.run, "B001").read_bytes()
        self.assertEqual("stale_generation", self.claim(1)["reasonCode"])
        self.assertEqual("stale_owner_ignored", defer(self.workspace, "alpha", self.run, "B001", 1, first["ownerToken"], "old_failure")["status"])
        self.assertEqual("stale_owner_ignored", finish(self.workspace, "alpha", self.run, "B001", 1, first["ownerToken"])["status"])
        self.assertEqual(before, lease_path(self.workspace, "alpha", self.run, "B001").read_bytes())

    def test_non_utf8_diagnostics_do_not_break_defer_or_reopen_merged_batches(self):
        owner = self.claim()
        payload = base64.b64encode(b"Git LFS failed: \x95\xa1").decode("ascii")
        argv = ["defer", "--workspace", str(self.workspace), "--feature", "alpha", "--run-id", self.run,
                "--batch-id", "B001", "--generation", "1", "--owner-token", owner["ownerToken"], "--error-b64", payload]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, dispatch_main(argv))
        self.assertEqual("retry_pending", json.loads(output.getvalue())["status"])
        manifest = load_manifest(self.workspace, "alpha", self.run)
        self.assertIn("\ufffd", manifest["batches"]["B001"]["recovery"]["lastError"])
        # A matching late callback must return the durable merge even after
        # its dispatch expired or completed. Old generations remain fenced.
        batch = manifest["batches"]["B001"]
        batch.update(status="merged", mergeCommitSha="f" * 40)
        batch["dispatch"].update(status="completed", expiresEpoch=time.time() - 1)
        save_manifest(self.workspace, "alpha", self.run, manifest)
        before = load_manifest(self.workspace, "alpha", self.run)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, dispatch_main(argv))
        self.assertEqual("merged", json.loads(output.getvalue())["status"])
        self.assertEqual(before, load_manifest(self.workspace, "alpha", self.run))
        self.assertEqual("stale_owner_ignored", defer(self.workspace, "alpha", self.run, "B001", 0, owner["ownerToken"], "late")["status"])

    def test_failure_cleanup_cannot_mark_a_merged_dispatch_failed(self):
        owner = self.claim()
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["batches"]["B001"].update(status="merged", mergeCommitSha="f" * 40)
        save_manifest(self.workspace, "alpha", self.run, manifest)
        finish(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], outcome="failed")
        batch = load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]
        self.assertEqual("completed", batch["dispatch"]["status"])
        self.assertEqual("merged", defer(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], "late")["status"])

    def test_repeated_provision_lock_waits_do_not_consume_business_retries(self):
        error = 'provision worktree B001 failed: {"error":"parallel_worktree_provision_lock_busy","retryable":true}'
        for gen in range(1, 4):
            owner = self.claim(gen)
            result = defer(self.workspace, "alpha", self.run, "B001", gen, owner["ownerToken"], error)
            self.assertEqual("retry_pending", result["status"])
            batch = load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]
            self.assertEqual(0, batch["recovery"]["retryAttempts"])
            self.assertEqual("parallel_worktree_provision_lock_busy", batch["recovery"]["reasonCode"])
            resumed = resume_run(self.workspace, "alpha", self.run)
            self.assertIn("B001", resumed["rescheduledRetryBatches"])
            self.assertEqual([], resumed["retryExhaustedBatches"])
            self.assertEqual(gen + 1, resumed["dispatchGrants"]["B001"])
        owner = self.claim(4)
        defer(self.workspace, "alpha", self.run, "B001", 4, owner["ownerToken"], "transport_failure")
        self.assertEqual(1, load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]["recovery"]["retryAttempts"])

    def test_provision_wait_preserves_prior_business_failure_budget(self):
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["batches"]["B001"]["recovery"] = {"retryAttempts": 1}
        save_manifest(self.workspace, "alpha", self.run, manifest)
        owner = self.claim()
        defer(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], "parallel_worktree_provision_lock_busy")
        self.assertEqual(1, load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]["recovery"]["retryAttempts"])
        self.assertIn("B001", resume_run(self.workspace, "alpha", self.run)["rescheduledRetryBatches"])
        owner = self.claim(2)
        defer(self.workspace, "alpha", self.run, "B001", 2, owner["ownerToken"], "transport_failure")
        self.assertIn("B001", resume_run(self.workspace, "alpha", self.run)["retryExhaustedBatches"])

    def _single_slot_worktree(self):
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["maxParallel"] = 1
        save_manifest(self.workspace, "alpha", self.run, manifest)
        owner = self.claim()
        self.assertEqual("claimed", owner["status"])
        provisioned = provision_parallel_worktree(self.workspace, "alpha", self.run, "B001")
        self.assertTrue(provisioned["success"], provisioned)
        return owner, Path(provisioned["worktreePath"]), provisioned["branchName"]

    def _interrupt_started_task(self, *, dirty=False):
        owner, worktree, branch = self._single_slot_worktree()
        lease = acquire_lease(self.workspace, "alpha", self.run, "B001", ttl_seconds=60,
                              dispatch_auth={"generation": 1, "token": owner["ownerToken"]})
        mark_batch(self.workspace, "alpha", self.run, "B001", "running",
                   worktreePath=str(worktree), branchName=branch)
        started = start_task(self.workspace, "alpha", "T001", worktree,
                             parallel_run_id=self.run, lease_token=lease["ownerToken"], workspace_ref="default")
        if dirty:
            (worktree / "unfinished.txt").write_text("preserve unfinished implementation\n", encoding="utf-8")
        before = load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]
        self.assertEqual("running", before["stageStates"]["implement"]["status"])
        failure = "execution was interrupted before implementation completed; lease remains owned; no commitSha produced"
        self.assertEqual("retry_pending", defer(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], failure)["status"])
        resumed = resume_run(self.workspace, "alpha", self.run)
        return owner, worktree, before, started, resumed

    def test_started_task_interruption_releases_slot_without_resetting_stage_progress(self):
        owner, worktree, before, started, resumed = self._interrupt_started_task()
        self.assertEqual(["B001"], resumed["rescheduledRetryBatches"])
        self.assertEqual([["B001"]], resumed["scheduledGroups"])
        self.assertEqual(0, resumed["activeWorkers"])
        self.assertEqual([], resumed["dispatchDiagnostics"]["activeBatchIds"])
        batch = load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]
        self.assertEqual(before["stageStates"], batch["stageStates"])
        self.assertEqual(str(worktree), batch["worktreePath"])
        self.assertIsNone(batch["lease"])
        self.assertFalse(lease_path(self.workspace, "alpha", self.run, "B001").exists())
        self.assertEqual(2, batch["dispatchGeneration"])
        task_path = self.workspace / ".autobizdevops/features/alpha/.task-runs/T001" / (started["runId"] + ".json")
        self.assertEqual("aborted", json.loads(task_path.read_text(encoding="utf-8"))["status"])
        for _ in range(2):
            polled = schedule(self.workspace, "alpha", self.run)
            self.assertEqual([["B001"]], polled["scheduledGroups"])
            self.assertEqual(2, polled["dispatchGrants"]["B001"])
        next_owner = self.claim(2)
        self.assertEqual("claimed", next_owner["status"])
        next_lease = acquire_lease(self.workspace, "alpha", self.run, "B001", ttl_seconds=60,
                                   dispatch_auth={"generation": 2, "token": next_owner["ownerToken"]})
        restarted = start_task(self.workspace, "alpha", "T001", worktree,
                               parallel_run_id=self.run, lease_token=next_lease["ownerToken"], workspace_ref="default")
        self.assertNotEqual(started["runId"], restarted["runId"])
        self.assertEqual("started", restarted["status"])
        self.assertEqual(1, schedule(self.workspace, "alpha", self.run)["activeWorkers"])
        next_lease_bytes = lease_path(self.workspace, "alpha", self.run, "B001").read_bytes()
        self.assertEqual("stale_owner_ignored", finish(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"])["status"])
        self.assertEqual(next_lease_bytes, lease_path(self.workspace, "alpha", self.run, "B001").read_bytes())

    def test_dirty_implementation_recovery_can_use_the_only_worker_slot(self):
        _, worktree, before, _, resumed = self._interrupt_started_task(dirty=True)
        self.assertEqual(0, resumed["activeWorkers"])
        self.assertEqual(["B001"], [item["batchId"] for item in resumed["implementationRecoveryBatches"]])
        self.assertEqual([], resumed["scheduledGroups"])
        self.assertEqual("preserve unfinished implementation\n", (worktree / "unfinished.txt").read_text(encoding="utf-8"))
        self.assertEqual(before["stageStates"], load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]["stageStates"])
        claimed = claim(self.workspace, "alpha", self.run, "B001", 2, "implementation_recovery", "retry-worker")
        self.assertEqual("claimed", claimed["status"])
        next_lease = acquire_lease(self.workspace, "alpha", self.run, "B001", ttl_seconds=60,
                                   dispatch_auth={"generation": 2, "token": claimed["ownerToken"]})
        restarted = start_task(self.workspace, "alpha", "T001", worktree,
                               parallel_run_id=self.run, lease_token=next_lease["ownerToken"], workspace_ref="default")
        self.assertEqual("started", restarted["status"])
        self.assertEqual("preserve unfinished implementation\n", (worktree / "unfinished.txt").read_text(encoding="utf-8"))
        self.assertEqual(1, schedule(self.workspace, "alpha", self.run)["activeWorkers"])

    def _assert_sealed_stage_recovery_reacquires_slot(self, stage):
        owner, worktree, branch = self._single_slot_worktree()
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True, check=True).stdout.strip()
        mark_batch(self.workspace, "alpha", self.run, "B001", "sealed", worktreePath=str(worktree), branchName=branch, commitSha=commit)
        for completed in ("prepare", "implement", *(["review"] if stage == "test" else [])):
            start_stage(self.workspace, "alpha", self.run, "B001", completed)
            complete_stage(self.workspace, "alpha", self.run, "B001", completed, metadata={"batchCommit": commit})
        start_stage(self.workspace, "alpha", self.run, "B001", stage)
        if stage == "test":
            acquire_lease(self.workspace, "alpha", self.run, "B001", ttl_seconds=60,
                          dispatch_auth={"generation": 1, "token": owner["ownerToken"]})
        # A live Review occupies its slot even though it has no business lease.
        self.assertEqual(1, schedule(self.workspace, "alpha", self.run)["activeWorkers"])
        before = load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]
        defer(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], "worker_interrupted")
        resumed = resume_run(self.workspace, "alpha", self.run)
        self.assertEqual(0, resumed["activeWorkers"])
        self.assertEqual(stage, resumed["stageRecoveryBatches"][0]["nextStage"])
        batch = load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]
        self.assertEqual(before["stageStates"], batch["stageStates"])
        self.assertEqual(commit, batch["commitSha"])
        next_owner = claim(self.workspace, "alpha", self.run, "B001", 2, "stage_recovery", "retry-worker")
        self.assertEqual("claimed", next_owner["status"])
        self.assertEqual(1, schedule(self.workspace, "alpha", self.run)["activeWorkers"])

    def test_review_recovery_preserves_progress_and_reacquires_slot(self):
        self._assert_sealed_stage_recovery_reacquires_slot("review")

    def test_utest_recovery_preserves_progress_and_reacquires_slot(self):
        self._assert_sealed_stage_recovery_reacquires_slot("test")

    def test_deterministic_handoff_failure_is_blocked_without_blind_retry(self):
        owner = self.claim()
        result = defer(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], "engineering_handoff_identity_mismatch")
        self.assertEqual("blocked", result["status"])
        batch = load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]
        self.assertEqual("precondition_blocked", batch["recovery"]["status"])
        self.assertEqual([], resume_run(self.workspace, "alpha", self.run)["rescheduledRetryBatches"])

    def test_expired_pending_claim_can_be_taken_over_and_old_finish_is_fenced(self):
        old = self.claim()
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["batches"]["B001"]["dispatch"]["expiresEpoch"] = time.time() - 1
        save_manifest(self.workspace, "alpha", self.run, manifest)
        current = self.claim()
        self.assertEqual("claimed", current["status"])
        self.assertNotEqual(old["ownerToken"], current["ownerToken"])
        self.assertEqual("stale_owner_ignored", finish(self.workspace, "alpha", self.run, "B001", 1, old["ownerToken"])["status"])

    def test_fresh_dispatch_occupies_capacity_before_business_lease(self):
        self.claim()
        state = schedule(self.workspace, "alpha", self.run)
        self.assertEqual(["B001"], state["dispatchDiagnostics"]["activeBatchIds"])
        self.assertEqual(1, state["activeWorkers"])

    def test_stale_review_cli_cannot_mutate_current_generation(self):
        from hooks.parallel_batch_stage import main
        first = self.claim()
        defer(self.workspace, "alpha", self.run, "B001", 1, first["ownerToken"], "transport_interrupted")
        resume_run(self.workspace, "alpha", self.run)
        second = self.claim(2)
        common = ["--workspace", str(self.workspace), "--feature", "alpha", "--run-id", self.run, "--batch-id", "B001"]
        before = load_manifest(self.workspace, "alpha", self.run)
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["start", *common, "--stage", "review", "--dispatch-generation", "1", "--dispatch-token", first["ownerToken"]])
        self.assertEqual(1, code)
        self.assertIn("parallel_dispatch_stale_stage_owner", output.getvalue())
        self.assertEqual(before, load_manifest(self.workspace, "alpha", self.run))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, main(["next", *common, "--dispatch-generation", "2", "--dispatch-token", second["ownerToken"]]))

    def test_processes_cannot_claim_the_same_scheduler_snapshot_twice(self):
        command = [sys.executable, "hooks/parallel_dispatch.py", "claim", "--workspace", str(self.workspace),
                   "--feature", "alpha", "--run-id", self.run, "--batch-id", "B001", "--generation", "1", "--source", "initial"]
        def invoke(worker):
            result = subprocess.run([*command, "--worker", str(worker)], text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            return json.loads(result.stdout)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(invoke, range(4)))
        self.assertEqual(1, sum(r["status"] == "claimed" for r in results))
        self.assertTrue(all(r["status"] == "claimed" or r["reasonCode"] == "running" for r in results))

    def test_lease_acquire_cli_rejects_stale_dispatch_identity(self):
        from hooks.batch_lease_manager import main
        first = self.claim()
        defer(self.workspace, "alpha", self.run, "B001", 1, first["ownerToken"], "worker_interrupted")
        resume_run(self.workspace, "alpha", self.run)
        self.claim(2)
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["acquire", "--workspace", str(self.workspace), "--feature", "alpha", "--run-id", self.run,
                "--batch-id", "B001", "--dispatch-generation", "1", "--dispatch-token", first["ownerToken"]])
        self.assertEqual(1, code)
        self.assertIn("parallel_dispatch_stale_lease_owner", output.getvalue())
        self.assertFalse(lease_path(self.workspace, "alpha", self.run, "B001").exists())

    def test_same_dispatch_can_recover_its_business_lease_after_a_lost_acquire_response(self):
        from hooks.batch_lease_manager import main
        from hooks.parallel_runtime import check_lease
        owner, worktree, branch = self._single_slot_worktree()
        auth = {"generation": 1, "token": owner["ownerToken"]}
        lease = acquire_lease(self.workspace, "alpha", self.run, "B001", lease_guard=True, dispatch_auth=auth)
        mark_batch(self.workspace, "alpha", self.run, "B001", "running", worktreePath=str(worktree), branchName=branch)
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["acquire", "--workspace", str(self.workspace), "--feature", "alpha", "--run-id", self.run,
                         "--batch-id", "B001", "--lease-guard", "--dispatch-generation", "1", "--dispatch-token", owner["ownerToken"]])
        self.assertEqual(code, 0, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual(result["businessLeaseToken"], lease["ownerToken"])
        self.assertNotEqual(result["businessLeaseToken"], owner["ownerToken"])
        self.assertTrue(check_lease(self.workspace, "alpha", self.run, "B001", result["businessLeaseToken"]))
        self.assertFalse(check_lease(self.workspace, "alpha", self.run, "B001", owner["ownerToken"]))
        self.assertEqual(load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]["status"], "running")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True, check=True).stdout.strip()
        mark_batch(self.workspace, "alpha", self.run, "B001", "sealed", commitSha=head)
        repeated = acquire_lease(self.workspace, "alpha", self.run, "B001", lease_guard=True, dispatch_auth=auth)
        self.assertEqual(repeated["ownerToken"], lease["ownerToken"])

    def test_poll_recovers_expired_dispatch_without_waiting_for_final_drain(self):
        owner = self.claim()
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["batches"]["B001"]["dispatch"]["expiresEpoch"] = time.time() - 1
        save_manifest(self.workspace, "alpha", self.run, manifest)
        self.assertEqual("stale_owner_ignored", defer(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], "late")["status"])
        polled = schedule(self.workspace, "alpha", self.run)
        self.assertEqual(["B001"], polled["retryPendingBatches"])
        resumed = resume_run(self.workspace, "alpha", self.run)
        self.assertEqual(2, resumed["dispatchGrants"]["B001"])
        self.assertEqual("claimed", self.claim(2)["status"])

    def test_empty_claim_response_can_reuse_only_the_same_unique_request(self):
        args = (self.workspace, "alpha", self.run, "B001", 1, "initial", "drain-1")
        first = claim(*args, request_id="unique-workflow-request")
        repeated = claim(*args, request_id="unique-workflow-request")
        self.assertEqual(first["ownerToken"], repeated["ownerToken"])
        self.assertTrue(repeated["reused"])
        self.assertEqual("running", claim(*args, request_id="another-workflow-request")["reasonCode"])
        first_defer = defer(self.workspace, "alpha", self.run, "B001", 1, first["ownerToken"], "transport_failure")
        second_defer = defer(self.workspace, "alpha", self.run, "B001", 1, first["ownerToken"], "transport_failure")
        self.assertEqual(first_defer["status"], second_defer["status"])
        self.assertTrue(second_defer["reused"])
        self.assertEqual(1, load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]["recovery"]["retryAttempts"])
