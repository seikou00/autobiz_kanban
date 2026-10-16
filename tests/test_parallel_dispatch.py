"""Exercise durable grants with real manifests, concurrency and retry transitions."""
import concurrent.futures
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
from hooks.parallel_dispatch import claim, defer, finish
from hooks.parallel_runtime import acquire_lease, lease_path, load_manifest, save_manifest
from hooks.parallel_batch_scheduler import resume_run, schedule
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
