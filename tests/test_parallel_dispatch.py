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

    def test_provision_wait_preserves_prior_business_failure_counter(self):
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["batches"]["B001"]["recovery"] = {"retryAttempts": 1}
        save_manifest(self.workspace, "alpha", self.run, manifest)
        owner = self.claim()
        defer(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], "parallel_worktree_provision_lock_busy")
        self.assertEqual(1, load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]["recovery"]["retryAttempts"])
        self.assertIn("B001", resume_run(self.workspace, "alpha", self.run)["rescheduledRetryBatches"])
        owner = self.claim(2)
        defer(self.workspace, "alpha", self.run, "B001", 2, owner["ownerToken"], "transport_failure")
        resumed = resume_run(self.workspace, "alpha", self.run)
        self.assertIn("B001", resumed["rescheduledRetryBatches"])
        self.assertEqual([], resumed["retryExhaustedBatches"])
        self.assertEqual(2, load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]["recovery"]["retryAttempts"])

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

    def test_preconditions_are_rechecked_without_a_terminal_retry_limit(self):
        owner = self.claim()
        result = defer(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], "engineering_handoff_identity_mismatch")
        self.assertEqual("retry_pending", result["status"])
        batch = load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]
        self.assertEqual("pending_retry", batch["recovery"]["status"])
        self.assertEqual("precondition", batch["recovery"]["failureClassification"])
        self.assertEqual(["B001"], resume_run(self.workspace, "alpha", self.run)["rescheduledRetryBatches"])

    def _review_checkpoint(self):
        owner, worktree, branch = self._single_slot_worktree()
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True,
                                text=True, check=True).stdout.strip()
        mark_batch(self.workspace, "alpha", self.run, "B001", "sealed",
                   worktreePath=str(worktree), branchName=branch, commitSha=commit)
        for stage in ("prepare", "implement"):
            start_stage(self.workspace, "alpha", self.run, "B001", stage)
            complete_stage(self.workspace, "alpha", self.run, "B001", stage, metadata={"batchCommit": commit})
        start_stage(self.workspace, "alpha", self.run, "B001", "review")
        return owner, worktree, commit

    def test_repeated_review_interruptions_resume_the_same_unfinished_stage_until_done(self):
        from hooks.parallel_batch_stage import finish_review
        owner, worktree, commit = self._review_checkpoint()
        original = load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]["stageStates"]
        for gen in range(1, 7):
            result = defer(self.workspace, "alpha", self.run, "B001", gen, owner["ownerToken"],
                           "review_stage_not_finalized:B001:incomplete")
            self.assertEqual(result["status"], "retry_pending")
            resumed = resume_run(self.workspace, "alpha", self.run)
            self.assertEqual(resumed["stageRecoveryBatches"][0]["nextStage"], "review")
            batch = load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]
            self.assertEqual(batch["stageStates"], original)
            self.assertEqual(batch["commitSha"], commit)
            owner = claim(self.workspace, "alpha", self.run, "B001", gen + 1, "stage_recovery", "review-worker")
            self.assertEqual(owner["status"], "claimed")
        self.assertEqual(finish_review(self.workspace, "alpha", self.run, "B001", decision="passed")["status"], "passed")
        # Simulate a lost return after finish-review persisted its decision.
        defer(self.workspace, "alpha", self.run, "B001", 7, owner["ownerToken"], "lost_review_response")
        resumed = resume_run(self.workspace, "alpha", self.run)
        self.assertEqual(resumed["stageRecoveryBatches"][0]["nextStage"], "delivery")
        self.assertEqual(subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree,
            capture_output=True, text=True, check=True).stdout.strip(), commit)

    def test_poll_recovers_a_legacy_blocked_review_without_stealing_a_live_owner(self):
        owner, _, _ = self._review_checkpoint()
        manifest = load_manifest(self.workspace, "alpha", self.run)
        batch = manifest["batches"]["B001"]
        batch.update(status="blocked", error="review_stage_not_finalized:B001")
        batch["recovery"] = {"status": "precondition_blocked", "failureClassification": "precondition", "retryAttempts": 99}
        save_manifest(self.workspace, "alpha", self.run, manifest)
        self.assertEqual(schedule(self.workspace, "alpha", self.run)["retryPendingBatches"], [])
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["batches"]["B001"]["dispatch"]["status"] = "failed"
        save_manifest(self.workspace, "alpha", self.run, manifest)
        self.assertEqual(schedule(self.workspace, "alpha", self.run)["retryPendingBatches"], ["B001"])
        resumed = resume_run(self.workspace, "alpha", self.run)
        self.assertEqual(resumed["stageRecoveryBatches"][0]["nextStage"], "review")
        self.assertEqual(resumed["dispatchGrants"]["B001"], 2)
        for _ in range(2):
            self.assertEqual(schedule(self.workspace, "alpha", self.run)["dispatchGrants"]["B001"], 2)
        self.assertEqual(finish(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"])["status"], "stale_owner_ignored")

    def test_poll_recovers_a_sealed_review_when_the_worker_never_deferred_it(self):
        self._review_checkpoint()
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["batches"]["B001"]["dispatch"]["expiresEpoch"] = 0
        save_manifest(self.workspace, "alpha", self.run, manifest)
        self.assertEqual(schedule(self.workspace, "alpha", self.run)["retryPendingBatches"], ["B001"])
        resumed = resume_run(self.workspace, "alpha", self.run)
        self.assertEqual(resumed["stageRecoveryBatches"][0]["nextStage"], "review")

    def test_run_lock_contention_times_out_and_can_be_retried_after_release(self):
        from hooks import parallel_runtime
        with patch.object(parallel_runtime, "DEFAULT_RUN_LOCK_TIMEOUT_SECONDS", 0.03):
            with parallel_runtime.run_lock(self.workspace, "alpha", self.run):
                with self.assertRaisesRegex(TimeoutError, "file_lock_timeout"):
                    with parallel_runtime.run_lock(self.workspace, "alpha", self.run):
                        self.fail("must not take a live lock")
            with parallel_runtime.run_lock(self.workspace, "alpha", self.run):
                self.assertEqual(load_manifest(self.workspace, "alpha", self.run)["runId"], self.run)

    def test_failed_dispatch_after_lost_defer_cannot_strand_a_rescheduled_review(self):
        owner, _, _ = self._review_checkpoint()
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["batches"]["B001"]["recovery"] = {"status": "rescheduled", "kind": "stage_resume"}
        save_manifest(self.workspace, "alpha", self.run, manifest)
        finish(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], "failed")
        scheduled = schedule(self.workspace, "alpha", self.run)
        self.assertEqual(scheduled["activeWorkers"], 0)
        self.assertEqual(scheduled["retryPendingBatches"], ["B001"])
        resumed = resume_run(self.workspace, "alpha", self.run)
        self.assertEqual(resumed["stageRecoveryBatches"][0]["nextStage"], "review")
        self.assertEqual(resumed["dispatchGrants"]["B001"], 2)

    def test_lost_delivery_handoff_continues_after_review_without_reopening_it(self):
        from hooks.parallel_batch_stage import finish_review, gate_batch
        owner, _, _ = self._review_checkpoint()
        finish_review(self.workspace, "alpha", self.run, "B001", decision="passed")
        gate_batch(self.workspace, "alpha", self.run, "B001")
        finish(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"])
        self.assertEqual(schedule(self.workspace, "alpha", self.run)["retryPendingBatches"], ["B001"])
        resumed = resume_run(self.workspace, "alpha", self.run)
        self.assertEqual(resumed["mergeableBatches"], ["B001"])
        self.assertEqual(resumed["stageRecoveryBatches"], [])

    def test_missing_review_receipt_reopens_only_review_instead_of_looping_at_delivery(self):
        from hooks.parallel_batch_stage import finish_review, gate_batch
        owner, _, _ = self._review_checkpoint()
        finish_review(self.workspace, "alpha", self.run, "B001", decision="passed")
        self.assertEqual(gate_batch(self.workspace, "alpha", self.run, "B001")["status"], "ready_to_candidate")
        batch = load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]
        Path(batch["stageStates"]["review"]["evidencePath"]).unlink()
        rejected = gate_batch(self.workspace, "alpha", self.run, "B001")
        self.assertEqual(rejected["error"], "parallel_batch_review_not_bound_to_seal")
        defer(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], rejected["error"])
        resumed = resume_run(self.workspace, "alpha", self.run)
        self.assertEqual(resumed["stageRecoveryBatches"][0]["nextStage"], "review")
        self.assertEqual(resumed["mergeableBatches"], [])
        recovered = load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]
        for stage in ("prepare", "implement"):
            self.assertEqual(recovered["stageStates"][stage], batch["stageStates"][stage])
        start_stage(self.workspace, "alpha", self.run, "B001", "review")
        self.assertEqual(finish_review(self.workspace, "alpha", self.run, "B001", decision="passed")["status"], "passed")

    def test_ensure_replay_and_dispatch_preserve_cancellation(self):
        from hooks.parallel_batch_scheduler import ensure_run
        from hooks.parallel_runtime import list_runs

        options = {"max_parallel": 2, "timeout_seconds": 60,
                   "code_workspaces": [load_manifest(self.workspace, "alpha", self.run)["repositories"]["default"]["gitRoot"]],
                   "task_card_id": "TEST-001", "workflow_invocation_id": "a" * 32}
        first = ensure_run(self.workspace, "alpha", **options)
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["status"] = "cancelled"
        save_manifest(self.workspace, "alpha", self.run, manifest)
        replay = ensure_run(self.workspace, "alpha", **options)
        self.assertEqual((replay["runId"], replay["status"]), (first["runId"], "cancelled"))
        self.assertEqual(len(list_runs(self.workspace, "alpha")), 1)
        self.assertEqual(schedule(self.workspace, "alpha", self.run)["readyBatches"], [])
        self.assertEqual(self.claim()["reasonCode"], "parallel_dispatch_run_inactive")

        created = ensure_run(self.workspace, "alpha", **{**options, "workflow_invocation_id": "b" * 32})
        manifest = load_manifest(self.workspace, "alpha", created["runId"])
        manifest["status"] = "cancelled"
        save_manifest(self.workspace, "alpha", created["runId"], manifest)
        replay = ensure_run(self.workspace, "alpha", **{**options, "workflow_invocation_id": "b" * 32})
        self.assertEqual((replay["runId"], replay["status"]), (created["runId"], "cancelled"))
        self.assertEqual(len(list_runs(self.workspace, "alpha")), 2)

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

    def test_unclaimed_retry_requires_a_grant_only_after_dispatched_work(self):
        from hooks.batch_lease_manager import main as lease_main

        common = ["acquire", "--workspace", str(self.workspace), "--feature", "alpha",
                  "--run-id", self.run, "--batch-id", "B001"]
        mark_batch(self.workspace, "alpha", self.run, "B001", "retry_pending")
        resume_run(self.workspace, "alpha", self.run)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, lease_main(common))
        mark_batch(self.workspace, "alpha", self.run, "B001", "retry_pending")
        resume_run(self.workspace, "alpha", self.run)
        owner = self.claim(3)
        defer(self.workspace, "alpha", self.run, "B001", 3, owner["ownerToken"], "interrupted")
        resume_run(self.workspace, "alpha", self.run)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(1, lease_main(common))
        self.assertFalse(lease_path(self.workspace, "alpha", self.run, "B001").exists())

    def test_cancelled_invocation_review_is_superseded_on_relaunch_and_fences_late_writes(self):
        from hooks.batch_lease_manager import main as lease_main
        from hooks.parallel_batch_scheduler import main as scheduler_main, manual_resume_run
        from hooks.parallel_batch_stage import main as stage_main

        inv_a, inv_b = "a" * 32, "b" * 32
        # Deliberately pass invocation via legacy requestId prefix to cover pre-existing manifests.
        owner = claim(self.workspace, "alpha", self.run, "B001", 1, "initial", "drain-1",
                      request_id=f"{inv_a}:drain:1:1")
        provisioned = provision_parallel_worktree(self.workspace, "alpha", self.run, "B001")
        worktree, branch = Path(provisioned["worktreePath"]), provisioned["branchName"]
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True, check=True).stdout.strip()
        mark_batch(self.workspace, "alpha", self.run, "B001", "sealed",
                   worktreePath=str(worktree), branchName=branch, commitSha=commit)
        for completed in ("prepare", "implement"):
            start_stage(self.workspace, "alpha", self.run, "B001", completed)
            complete_stage(self.workspace, "alpha", self.run, "B001", completed, metadata={"batchCommit": commit})
        start_stage(self.workspace, "alpha", self.run, "B001", "review")

        # Without a new invocation ID (or with the same invocation ID), live Review is never stolen.
        same_resume = resume_run(self.workspace, "alpha", self.run, workflow_invocation_id=inv_a)
        self.assertEqual(1, same_resume["activeWorkers"])
        self.assertEqual("running", claim(self.workspace, "alpha", self.run, "B001", 1, "stage_recovery", "other", invocation_id=inv_a)["reasonCode"])

        # Simulate cancel + relaunch with fresh workflowInvocationId while TTL is still valid.
        resumed = resume_run(self.workspace, "alpha", self.run, workflow_invocation_id=inv_b)
        self.assertEqual(0, resumed["activeWorkers"])
        self.assertEqual(2, resumed["dispatchGrants"]["B001"])
        self.assertEqual("review", resumed["stageRecoveryBatches"][0]["nextStage"])
        self.assertEqual([{"batchId": "B001", "reason": "workflow_invocation_superseded"}], resumed["reclaimedStaleBatches"])
        for caller in (inv_a, None):
            rejected = claim(self.workspace, "alpha", self.run, "B001", 2, "stage_recovery", "old", invocation_id=caller)
            self.assertEqual("workflow_invocation_superseded", rejected["reasonCode"])
        batch_after_resume = load_manifest(self.workspace, "alpha", self.run)
        self.assertNotIn("dispatch", batch_after_resume["batches"]["B001"])
        self.assertEqual("running", batch_after_resume["batches"]["B001"]["stageStates"]["review"]["status"])

        # Pre-claim gap: old generation CLI calls must be fenced even when `dispatch` was popped.
        common = ["--workspace", str(self.workspace), "--feature", "alpha", "--run-id", self.run, "--batch-id", "B001"]
        for cli_main, argv, expected_error in (
            (stage_main, ["finish-review", *common, "--decision", "passed",
                          "--dispatch-generation", "1", "--dispatch-token", owner["ownerToken"]],
             "parallel_dispatch_stale_stage_owner"),
            (lease_main, ["acquire", *common, "--dispatch-generation", "1", "--dispatch-token", owner["ownerToken"]],
             "parallel_dispatch_stale_lease_owner"),
            (scheduler_main, ["mark-batch", *common, "--status", "running",
                              "--worktree-path", str(worktree), "--branch-name", branch],
             "parallel_dispatch_stale_batch_owner"),
            (stage_main, ["finish-review", *common, "--decision", "passed"], "parallel_dispatch_stale_stage_owner"),
            (lease_main, ["acquire", *common], "parallel_dispatch_stale_lease_owner"),
            (scheduler_main, ["mark-batch", *common, "--status", "running",
                              "--worktree-path", str(worktree), "--branch-name", branch,
                              "--dispatch-generation", "1", "--dispatch-token", owner["ownerToken"]],
             "parallel_dispatch_stale_batch_owner"),
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(1, cli_main(argv), out.getvalue())
            self.assertIn(expected_error, out.getvalue())
        self.assertEqual("stale_owner_ignored", defer(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], "late")["status"])
        self.assertEqual("stale_owner_ignored", finish(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"])["status"])
        self.assertEqual(batch_after_resume, load_manifest(self.workspace, "alpha", self.run))

        next_owner = claim(self.workspace, "alpha", self.run, "B001", 2, "stage_recovery", "drain-1",
                           request_id=f"{inv_b}:drain:1:1", invocation_id=inv_b)
        self.assertEqual("claimed", next_owner["status"])
        before = load_manifest(self.workspace, "alpha", self.run)
        for command in (resume_run, manual_resume_run):
            self.assertEqual("superseded", command(self.workspace, "alpha", self.run, workflow_invocation_id=inv_a)["status"])
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, scheduler_main(["status", "--workflow-view", "--workspace", str(self.workspace),
                                              "--feature", "alpha", "--run-id", self.run, "--workflow-invocation-id", inv_a]))
        self.assertEqual("superseded", json.loads(out.getvalue())["status"])
        self.assertEqual(before, load_manifest(self.workspace, "alpha", self.run))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, stage_main([
                "finish-review", *common, "--decision", "passed",
                "--dispatch-generation", "2", "--dispatch-token", next_owner["ownerToken"],
            ]), out.getvalue())
        self.assertEqual("passed", json.loads(out.getvalue())["status"])

    def test_cancelled_invocation_dirty_fix_resumes_end_to_end_across_repeated_resumes(self):
        from hooks.batch_lease_manager import main as lease_main
        from hooks.parallel_batch_scheduler import manual_resume_run
        from hooks.parallel_batch_stage import finalize_implementation, finish_review, prepare_implementation_repair, record_single_repair_resolution
        from hooks.parallel_runtime import release_lease
        from hooks.task_runner import finish_implementation, prepare_compile, record_compile, start_task_repair
        from hooks.worktree_manager import seal_parallel_batch

        inv_a, inv_b = "1" * 32, "2" * 32
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["taskCardId"] = "Z990692-294"
        manifest["maxParallel"] = 1
        save_manifest(self.workspace, "alpha", self.run, manifest)

        owner1 = claim(self.workspace, "alpha", self.run, "B001", 1, "initial", "drain-1", invocation_id=inv_a)
        provisioned = provision_parallel_worktree(self.workspace, "alpha", self.run, "B001")
        worktree, branch = Path(provisioned["worktreePath"]), provisioned["branchName"]
        lease1 = acquire_lease(self.workspace, "alpha", self.run, "B001", ttl_seconds=3600,
                               lease_guard=True, dispatch_auth={"generation": 1, "token": owner1["ownerToken"]})
        mark_batch(self.workspace, "alpha", self.run, "B001", "running", worktreePath=str(worktree), branchName=branch)

        def finish_task(state, *, repair=False, lease_token):
            attempt = prepare_compile(
                self.workspace, "alpha", "T001", worktree, state["runId"],
                argv=["mvn", "compile"], execution_cwd=worktree,
                parallel_run_id=self.run, lease_token=lease_token, workspace_ref="default",
            )
            log = self.workspace.parent / f"compile-{state['runId']}.log"
            log.write_text("BUILD SUCCESS\n", encoding="utf-8")
            record_compile(
                self.workspace, "alpha", "T001", worktree, state["runId"],
                attempt_id=attempt["attemptId"], exit_code=0, log_exit_code=0, output_file=log,
                parallel_run_id=self.run, lease_token=lease_token, workspace_ref="default",
            )
            ok, done = finish_implementation(
                self.workspace, "alpha", "T001", worktree, state["runId"],
                no_code_change_why=None, supporting_files=[], repair_mode=repair,
                parallel_run_id=self.run, lease_token=lease_token, workspace_ref="default",
            )
            self.assertTrue(ok, done)
            return done

        initial_task = start_task(self.workspace, "alpha", "T001", worktree,
                                  parallel_run_id=self.run, lease_token=lease1["ownerToken"], workspace_ref="default")
        (worktree / "service.py").write_text("value = None\n", encoding="utf-8")
        finished_initial = finish_task(initial_task, lease_token=lease1["ownerToken"])
        prior_evidence_id = finished_initial["implementationEvidenceId"]
        sealed_initial = seal_parallel_batch(self.workspace, "alpha", self.run, "B001", worktree, lease1["ownerToken"],
                                             purpose="review", commit_stage="code", commit_summary="初次实现")
        self.assertTrue(sealed_initial["success"], sealed_initial)
        release_lease(self.workspace, "alpha", self.run, "B001", lease1["ownerToken"], final_status="sealed")
        finalize_implementation(self.workspace, "alpha", self.run, "B001")
        start_stage(self.workspace, "alpha", self.run, "B001", "review")
        self.assertEqual("failed", finish_review(self.workspace, "alpha", self.run, "B001",
                                                 decision="failed", message="service.py:1 unexpected None")["status"])

        # Start Fix under inv_a, leave uncommitted changes + running implement stage + valid lease, then kill inv_a.
        self.assertEqual("ready", prepare_implementation_repair(self.workspace, "alpha", self.run, "B001", "review")["status"])
        fix_lease1 = acquire_lease(self.workspace, "alpha", self.run, "B001", ttl_seconds=3600,
                                   lease_guard=True, dispatch_auth={"generation": 1, "token": owner1["ownerToken"]})
        mark_batch(self.workspace, "alpha", self.run, "B001", "running", worktreePath=str(worktree), branchName=branch)
        start_task_repair(self.workspace, "alpha", "T001", worktree, prior_evidence_id,
                          parallel_run_id=self.run, lease_token=fix_lease1["ownerToken"], workspace_ref="default")
        (worktree / "service.py").write_text("value = 42\n", encoding="utf-8")
        self.assertEqual("running", load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]["stageStates"]["implement"]["status"])

        # Relaunch with inv_b; run both manual_resume_run and a second resume_run while worktree is dirty.
        resumed1 = manual_resume_run(self.workspace, "alpha", self.run, workflow_invocation_id=inv_b)
        self.assertEqual(0, resumed1["activeWorkers"])
        self.assertEqual("implement", resumed1["stageRecoveryBatches"][0]["nextStage"])
        resumed2 = resume_run(self.workspace, "alpha", self.run, workflow_invocation_id=inv_b)
        self.assertEqual("running", resumed2["status"])
        self.assertEqual([], resumed2["blockedBatches"])
        self.assertEqual("implement", resumed2["stageRecoveryBatches"][0]["nextStage"])
        self.assertEqual("value = 42\n", (worktree / "service.py").read_text(encoding="utf-8"))

        # New worker claims Fix, runs prepare-repair, acquires business lease via CLI, finishes and seals rework.
        owner2 = claim(self.workspace, "alpha", self.run, "B001", 2, "stage_recovery", "drain-1", invocation_id=inv_b)
        self.assertEqual("claimed", owner2["status"])
        self.assertEqual("ready", prepare_implementation_repair(self.workspace, "alpha", self.run, "B001", "review")["status"])
        out = io.StringIO()
        with redirect_stdout(out):
            code = lease_main([
                "acquire", "--workspace", str(self.workspace), "--feature", "alpha",
                "--run-id", self.run, "--batch-id", "B001", "--lease-guard",
                "--dispatch-generation", "2", "--dispatch-token", owner2["ownerToken"],
            ])
        self.assertEqual(0, code, out.getvalue())
        fix_lease2_token = json.loads(out.getvalue())["businessLeaseToken"]
        from hooks.parallel_batch_scheduler import main as scheduler_main
        mark = ["mark-batch", "--workspace", str(self.workspace), "--feature", "alpha", "--run-id", self.run,
                "--batch-id", "B001"]
        before = load_manifest(self.workspace, "alpha", self.run)
        for status in ("running", "retry_pending"):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(1, scheduler_main([*mark, "--status", status]))
        self.assertEqual(before, load_manifest(self.workspace, "alpha", self.run))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, scheduler_main([*mark, "--status", "running", "--dispatch-generation", "2",
                                               "--dispatch-token", owner2["ownerToken"]]))
        restarted_fix = start_task_repair(self.workspace, "alpha", "T001", worktree, prior_evidence_id,
                                          parallel_run_id=self.run, lease_token=fix_lease2_token, workspace_ref="default")
        self.assertEqual("value = 42\n", (worktree / "service.py").read_text(encoding="utf-8"))
        finish_task(restarted_fix, repair=True, lease_token=fix_lease2_token)
        sealed_rework = seal_parallel_batch(self.workspace, "alpha", self.run, "B001", worktree, fix_lease2_token,
                                            purpose="review", commit_stage="rework", commit_summary="修复空值返回")
        self.assertTrue(sealed_rework["success"], sealed_rework)
        release_lease(self.workspace, "alpha", self.run, "B001", fix_lease2_token, final_status="sealed")
        recorded = record_single_repair_resolution(
            self.workspace, "alpha", self.run, "B001", failed_stage="review",
            metadata={"batchCommit": sealed_rework["commitSha"], "worktreePath": str(worktree), "branchName": branch},
        )
        self.assertEqual("success", recorded["status"])

    def test_intra_invocation_resume_keeps_siblings_and_respects_delivery_lock_and_failed_pending_grant(self):
        from hooks.parallel_delivery import _execution_lock

        inv_a, inv_b = "3" * 32, "4" * 32
        owner = claim(self.workspace, "alpha", self.run, "B001", 1, "initial", "drain-1", invocation_id=inv_a)
        self.assertEqual("claimed", owner["status"])
        # Same invocation resume leaves running B001 untouched.
        same = resume_run(self.workspace, "alpha", self.run, workflow_invocation_id=inv_a)
        self.assertEqual(1, same["activeWorkers"])
        self.assertEqual(1, same["dispatchGrants"]["B001"])

        # Held delivery OS lock prevents a newer invocation from revoking an in-flight Git settlement.
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["controlProtocolVersion"] = 2
        save_manifest(self.workspace, "alpha", self.run, manifest)
        with _execution_lock(self.workspace, "alpha", self.run, "B001"):
            locked_resume = resume_run(self.workspace, "alpha", self.run, workflow_invocation_id=inv_b)
            self.assertEqual(1, locked_resume["activeWorkers"])
            self.assertEqual(1, locked_resume["dispatchGrants"]["B001"])

        # Once unlocked, inv_b supersedes inv_a; and if a pending claim later closes via finish(failed),
        # poll + resume advances the generation instead of stranding on grant_consumed.
        unlocked = resume_run(self.workspace, "alpha", self.run, workflow_invocation_id=inv_b)
        self.assertEqual(2, unlocked["dispatchGrants"]["B001"])
        owner2 = claim(self.workspace, "alpha", self.run, "B001", 2, "initial", "drain-1", invocation_id=inv_b)
        finish(self.workspace, "alpha", self.run, "B001", 2, owner2["ownerToken"], outcome="failed")
        self.assertEqual("grant_consumed", claim(self.workspace, "alpha", self.run, "B001", 2, "initial", "drain-1", invocation_id=inv_b)["reasonCode"])
        self.assertEqual(["B001"], schedule(self.workspace, "alpha", self.run)["retryPendingBatches"])
        resumed3 = resume_run(self.workspace, "alpha", self.run, workflow_invocation_id=inv_b)
        self.assertEqual(3, resumed3["dispatchGrants"]["B001"])
        self.assertEqual("claimed", claim(self.workspace, "alpha", self.run, "B001", 3, "initial", "drain-1", invocation_id=inv_b)["status"])
