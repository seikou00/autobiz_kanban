"""Real Git tests for durable, ordered publication and cumulative recovery."""
import unittest
from unittest.mock import patch

from hooks import parallel_workspace_sync as sync
from hooks import parallel_workspace_queue as queue
from hooks.parallel_delivery import settle_delivery, prepare_finalization
from hooks.parallel_runtime import load_manifest, run_lock, save_manifest
from tests import test_parallel_workspace_sync as fixtures


class WorkspaceQueueTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WorkspaceSyncTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.workspace, self.run, self.repo = self.fixture.workspace, self.fixture.run, self.fixture.repo

    def merge(self, batch_id):
        args, _ = self.fixture.fixture.delivery(batch_id)
        return settle_delivery(*args)

    def process(self, **kwargs):
        return queue.operate(self.workspace, "alpha", self.run, "default", "resolver", **kwargs)

    def manifest(self):
        return load_manifest(self.workspace, "alpha", self.run)

    def test_fifo_targets_are_pinned_and_requests_are_idempotent(self):
        first = self.merge("B001")
        second = self.merge("B002")
        prepare_finalization(self.workspace, "alpha", self.run)
        prepare_finalization(self.workspace, "alpha", self.run)
        self.assertEqual(len(self.manifest()["workspaceSyncQueue"]["default"]), 2)
        result = self.process()
        self.assertEqual(result["lastSyncedSha"], first["mergeCommitSha"])
        self.assertEqual(result["desiredSha"], second["mergeCommitSha"])
        self.assertEqual(result["status"], "queued")
        self.assertTrue((self.repo / "B001.txt").exists())
        self.assertFalse((self.repo / "B002.txt").exists())
        result = self.process()
        self.assertEqual(result["status"], "synced")
        self.assertEqual(result["lastSyncedSha"], second["mergeCommitSha"])
        self.assertEqual([job["status"] for job in result["queue"]], ["synced", "synced"])

    def test_merge_during_publication_keeps_new_demand_and_source_identity(self):
        first = self.merge("B001")
        original_index = self.fixture.source_index()
        apply = sync._apply
        def concurrent_merge(*args):
            self.merge("B002")
            return apply(*args)
        with patch.object(sync, "_apply", side_effect=concurrent_merge):
            result = self.process()
        self.assertEqual(result["lastSyncedSha"], first["mergeCommitSha"])
        self.assertEqual(result["status"], "queued")
        self.assertNotEqual(result["desiredSha"], result["lastSyncedSha"])
        self.assertFalse((self.repo / "B002.txt").exists())
        result = self.process()
        self.assertEqual(result["status"], "synced")
        self.assertTrue((self.repo / "B002.txt").exists())
        self.assertEqual(self.fixture.source_index(), original_index)
        self.assertEqual(sync._value(self.repo, "rev-parse", "HEAD"), self.fixture.head)

    def test_exhausted_job_is_covered_by_later_success_without_retry(self):
        self.merge("B001")
        epoch = 10000000000
        with patch.object(sync, "sync_workspace", side_effect=OSError("write permission denied")) as calls:
            for attempt, delay in enumerate((5, 30, 120, 0)):
                with patch.object(queue.time, "time", return_value=epoch):
                    result = self.process()
                    self.assertEqual(result["retryAfterSeconds"], delay)
                    self.assertEqual(result["status"], "retry_wait" if delay else "failed")
                    self.assertFalse(result["success"])
                    if delay:
                        self.assertEqual(self.process()["status"], "retry_wait")
                        self.assertEqual(calls.call_count, attempt + 1)
                epoch += delay + 1
        self.assertEqual(result["queue"][0]["failures"], 4)
        self.assertIn("permission denied", result["error"])
        self.assertEqual(self.manifest()["batches"]["B001"]["status"], "merged")
        self.merge("B002")
        with patch.object(sync, "sync_workspace", wraps=sync.sync_workspace) as calls:
            result = self.process()
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(result["status"], "synced")
        self.assertEqual([job["status"] for job in result["queue"]], ["covered", "synced"])
        self.assertEqual(result["queue"][0]["coveredBy"], result["queue"][1]["jobId"])
        self.assertEqual(len(result["queue"][0]["errors"]), 4)
        self.assertEqual(result["pendingBatchIds"], [])
        self.assertTrue((self.repo / "B001.txt").exists())
        self.assertTrue((self.repo / "B002.txt").exists())

    def test_write_completed_before_queue_ack_is_recovered_without_reapply(self):
        receipt = self.merge("B001")
        sync.sync_workspace(self.workspace, "alpha", self.run, "default", "resolver",
                            target_sha=receipt["mergeCommitSha"])
        # Simulate interruption between the file journal and queue acknowledgement.
        with run_lock(self.workspace, "alpha", self.run):
            manifest = self.manifest()
            manifest["workspaceSyncQueue"]["default"][0]["status"] = "queued"
            save_manifest(self.workspace, "alpha", self.run, manifest)
        with patch.object(sync, "sync_workspace", side_effect=AssertionError("must not write twice")):
            result = self.process()
        self.assertEqual(result["status"], "synced")
        self.assertEqual(result["queue"][0]["status"], "covered")

    def test_exhausted_conflict_can_be_replaced_by_later_target(self):
        (self.repo / "B001.txt").write_text("user edit\n")
        self.merge("B001")
        conflict = self.process()
        self.assertEqual(conflict["status"], "conflicted")
        from pathlib import Path
        from hooks.frontend_route_write_guard import validate_merge_resolution_write
        self.assertEqual(validate_merge_resolution_write(self.workspace, "alpha", Path(conflict["worktreePath"]) / "B001.txt"), 0)
        self.merge("B002")
        self.assertEqual(validate_merge_resolution_write(self.workspace, "alpha", Path(conflict["worktreePath"]) / "B001.txt"), 0)
        epoch = 10000000000
        for attempt in range(4):
            with patch.object(queue.time, "time", return_value=epoch):
                self.process(action="fail", job_id=conflict["jobId"], error="resolver failed", failure_id=str(attempt))
            epoch += 200
        later = self.process()
        self.assertEqual(later["status"], "conflicted")
        self.assertNotEqual(later["jobId"], conflict["jobId"])
        self.assertEqual((self.repo / "B001.txt").read_text(), "user edit\n")
        self.assertEqual(validate_merge_resolution_write(self.workspace, "alpha", Path(later["worktreePath"]) / "B001.txt"), 0)
        (Path(later["worktreePath"]) / "B001.txt").write_text("B001 and user edit\n")
        done = self.process(action="complete", job_id=later["jobId"], attempt_id=later["attemptId"])
        self.assertEqual(done["status"], "synced", done)
        self.assertEqual([job["status"] for job in done["queue"]], ["covered", "synced"])
        self.assertEqual((self.repo / "B001.txt").read_text(), "B001 and user edit\n")

    def test_failure_receipts_are_idempotent_and_fenced_by_job_and_owner(self):
        self.merge("B001")
        first = self.process(action="fail", error="dispatch unavailable", failure_id="same")
        again = self.process(action="fail", error="dispatch unavailable", failure_id="same")
        self.assertEqual(first["queue"][0]["failures"], again["queue"][0]["failures"])
        stale = self.process(action="fail", job_id="wrong", error="stale", failure_id="other")
        self.assertEqual(stale["reasonCode"], "workspace_sync_stale_job")
        self.assertEqual(stale["queue"][0]["failures"], 1)

    def test_scheduler_recovers_registration_gap_before_finalization(self):
        self.merge("B001")
        with run_lock(self.workspace, "alpha", self.run):
            manifest = self.manifest()
            manifest.pop("workspaceSyncQueue", None)
            manifest.pop("workspaceSync", None)
            save_manifest(self.workspace, "alpha", self.run, manifest)
        from hooks.parallel_batch_scheduler import schedule
        snapshot = schedule(self.workspace, "alpha", self.run, workflow_view=True)
        self.assertEqual(snapshot["workspaceSync"][0]["queue"][0]["batchIds"], ["B001"])
        self.assertEqual(self.process()["status"], "synced")

    def test_dead_process_is_reclaimed_but_live_conflict_editor_is_fenced(self):
        self.merge("B001")
        with run_lock(self.workspace, "alpha", self.run):
            manifest = self.manifest()
            job = manifest["workspaceSyncQueue"]["default"][0]
            job.update(status="running", owner="dead-process", ownerExpiresEpoch=10000000000)
            save_manifest(self.workspace, "alpha", self.run, manifest)
        self.assertEqual(self.process()["status"], "synced")
        (self.repo / "B002.txt").write_text("user local\n")
        self.merge("B002")
        conflict = self.process()
        blocked = queue.operate(self.workspace, "alpha", self.run, "default", "other-resolver")
        self.assertEqual(blocked["status"], "busy")
        self.assertEqual(self.manifest()["workspaceSyncQueue"]["default"][1]["owner"], "resolver")
        self.assertEqual(conflict["status"], "conflicted")

    def test_cli_persists_unicode_failure_and_reports_unsuccessful_sync(self):
        import base64
        import io
        import json
        from contextlib import redirect_stdout
        self.merge("B001")
        output = io.StringIO()
        detail = '写入失败: $() "quoted"'
        with redirect_stdout(output):
            code = sync.main(["fail", "--workspace", str(self.workspace), "--feature", "alpha",
                              "--run-id", self.run, "--repository-ref", "default", "--resolver-id", "resolver",
                              "--failure-id", "dispatch-failed", "--error-base64", base64.b64encode(detail.encode()).decode()])
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "retry_wait")
        self.assertEqual(result["error"], detail)
        self.assertEqual(self.manifest()["batches"]["B001"]["status"], "merged")


if __name__ == "__main__":
    unittest.main()
