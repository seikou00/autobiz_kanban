"""Real Git coverage for coordinated promotion, replay and deferred cleanup."""
from __future__ import annotations

import copy
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from hooks.parallel_batch_scheduler import create_run, mark_batch, schedule
from hooks.parallel_batch_stage import start_stage, complete_stage
from hooks.parallel_dispatch import claim
from hooks.parallel_delivery import settle_delivery, prepare_finalization
from hooks.parallel_cleanup import drain, pending
from hooks.parallel_runtime import load_manifest, run_lock, save_manifest
from hooks.parallel_integration import git, integration_repo
from hooks import parallel_merge_train as train
from tests.test_task_runner import _workspace, _refresh_parallel_pipeline


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="delivery-control-")
        self.addCleanup(temporary.cleanup)
        self.workspace, self.feature, self.repo = _workspace(Path(temporary.name))

    def create(self, count=1):
        root_path = self.feature / "plan.json"
        root = json.loads(root_path.read_text())
        original_entry = copy.deepcopy(root["batches"][0])
        original_entry.update(executionStage="parallel", workspaceRef="default")
        root["batches"][0] = original_entry
        original_plan = json.loads((self.feature / original_entry["path"]).read_text())
        original_plan.update(executionStage="parallel")
        original_plan["taskIds"] = ["T001"]
        original_plan["tasks"][0]["status"] = "implemented"
        (self.feature / original_entry["path"]).write_text(json.dumps(original_plan))
        for index in range(2, count + 1):
            batch_id, task_id = f"B{index:03d}", f"T{index:03d}"
            entry, batch = copy.deepcopy(original_entry), copy.deepcopy(original_plan)
            entry.update(id=batch_id, path=f"plans/{batch_id}/plan.json", taskIds=[task_id])
            batch.update(batchId=batch_id, taskIds=[task_id])
            task = batch["tasks"][0]
            task["id"] = task_id
            for ac in task["acceptanceCriteria"]:
                ac["id"] = ac["id"].replace("T001", task_id)
            target = self.feature / entry["path"]
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(batch))
            root["batches"].append(entry)
        root_path.write_text(json.dumps(root))
        _refresh_parallel_pipeline(self.feature)
        self.created = create_run(self.workspace, "alpha", max_parallel=count, timeout_seconds=300,
                                  code_workspaces=[str(self.repo)], task_card_id="TEST-001")
        self.run = self.created["runId"]
        self.assertEqual(self.created["controlProtocolVersion"], 2)
        self.integration = integration_repo(self.manifest()["repositories"]["default"])

    def manifest(self):
        return load_manifest(self.workspace, "alpha", self.run)

    def delivery(self, batch_id="B001", content=None, review=True):
        from hooks.worktree_manager import provision_parallel_worktree
        owner = claim(self.workspace, "alpha", self.run, batch_id, 1, "initial", batch_id)
        self.assertEqual(owner["status"], "claimed", owner)
        result = provision_parallel_worktree(self.workspace, "alpha", self.run, batch_id)
        tree = Path(result["worktreePath"])
        filename = "shared.txt" if content is not None else batch_id + ".txt"
        (tree / filename).write_text(content if content is not None else batch_id)
        git(tree, "add", filename)
        git(tree, "commit", "-m", batch_id)
        sha = git(tree, "rev-parse", "HEAD")
        mark_batch(self.workspace, "alpha", self.run, batch_id, "sealed", worktreePath=str(tree),
                   branchName=result["branchName"], commitSha=sha)
        for stage in ("prepare", "implement", *(("review", "test") if review else ())):
            start_stage(self.workspace, "alpha", self.run, batch_id, stage)
            complete_stage(self.workspace, "alpha", self.run, batch_id, stage, metadata={"batchCommit": sha})
        return (self.workspace, "alpha", self.run, batch_id, 1, owner["ownerToken"], sha), tree

    def test_settle_replays_without_git_mutation_and_cleanup_is_deferred(self):
        self.create()
        args, tree = self.delivery()
        result = settle_delivery(*args)
        head = git(self.integration, "rev-parse", "HEAD")
        replay = settle_delivery(*args)
        self.assertEqual(result["mergeCommitSha"], head)
        self.assertTrue(replay["reused"])
        self.assertEqual(head, git(self.integration, "rev-parse", "HEAD"))
        self.assertTrue(tree.exists())
        self.assertEqual(len(pending(self.manifest())), 2)
        self.assertEqual(self.manifest()["batches"]["B001"]["dispatch"]["status"], "completed")
        final = prepare_finalization(self.workspace, "alpha", self.run)
        self.assertFalse(final["repairRequired"])
        self.assertEqual(final["batchIds"], [])
        self.assertNotIn("schedulerSnapshot", final)
        self.assertTrue(self.manifest()["finalizationReceipt"]["digest"])
        cleanup = drain(self.workspace, "alpha", self.run)
        self.assertEqual(cleanup["cleanupPending"], [], cleanup)
        self.assertFalse(tree.exists())
        self.assertEqual(drain(self.workspace, "alpha", self.run)["cleanedJobIds"], [])

    def test_five_simultaneous_deliveries_never_rebuild_stale_candidates(self):
        self.create(5)
        deliveries = [self.delivery(f"B{i:03d}")[0] for i in range(1, 6)]
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda args: settle_delivery(*args), deliveries))
        self.assertTrue(all(result["status"] == "merged" for result in results))
        self.assertEqual(len(self.manifest()["mergeTrains"]), 5)
        self.assertTrue(all(item["status"] == "promoted" for item in self.manifest()["mergeTrains"].values()))
        self.assertEqual(len({result["wave"] for result in results}), 5)
        self.assertTrue(all((self.integration / f"B{i:03d}.txt").exists() for i in range(1, 6)))

    def test_crash_after_fast_forward_recovers_plan_without_second_merge(self):
        self.create()
        args, _ = self.delivery()
        with patch.object(train, "_checkpoint_promoted_plan_recovery", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                settle_delivery(*args)
        head = git(self.integration, "rev-parse", "HEAD")
        result = settle_delivery(*args)
        self.assertEqual(head, result["mergeCommitSha"])
        self.assertEqual(head, git(self.integration, "rev-parse", "HEAD"))
        self.assertEqual(self.manifest()["batches"]["B001"]["status"], "merged")

    def test_crash_before_dispatch_finish_recovers_only_receipt(self):
        self.create()
        args, _ = self.delivery()
        with patch("hooks.parallel_dispatch.finish", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                settle_delivery(*args)
        head = git(self.integration, "rev-parse", "HEAD")
        self.assertTrue(settle_delivery(*args)["dispatchFinalized"])
        self.assertEqual(head, git(self.integration, "rev-parse", "HEAD"))

    def test_gate_and_stale_owner_cannot_be_bypassed(self):
        self.create()
        args, _ = self.delivery(review=False)
        with self.assertRaisesRegex(ValueError, "delivery_gate_failed"):
            settle_delivery(*args)
        with self.assertRaisesRegex(ValueError, "stale_stage_owner"):
            settle_delivery(*args[:4], 2, *args[5:])
        self.assertEqual(self.manifest()["mergeTrains"], {})

    def test_completed_grant_cannot_start_a_new_promotion(self):
        from hooks.parallel_dispatch import finish
        self.create()
        args, _ = self.delivery()
        finish(self.workspace, "alpha", self.run, "B001", 1, args[5])
        with self.assertRaisesRegex(ValueError, "stale_stage_owner"):
            settle_delivery(*args)
        self.assertEqual(self.manifest()["mergeTrains"], {})

    def test_cleanup_failure_preserves_merged_delivery_and_can_resume(self):
        self.create()
        args, tree = self.delivery()
        settle_delivery(*args)
        (tree / "local-change.txt").write_text("preserve")
        result = drain(self.workspace, "alpha", self.run)
        self.assertTrue(result["cleanupPending"])
        self.assertTrue(tree.exists())
        self.assertEqual(self.manifest()["batches"]["B001"]["status"], "merged")
        (tree / "local-change.txt").unlink()
        self.assertEqual(drain(self.workspace, "alpha", self.run)["cleanupPending"], [])

    def test_cleanup_waits_for_unmerged_deliveries_and_finalization_requests_repair(self):
        self.create(2)
        args, tree = self.delivery()
        settle_delivery(*args)
        self.assertEqual(drain(self.workspace, "alpha", self.run)["status"], "waiting_for_deliveries")
        self.assertTrue(tree.exists())
        self.assertTrue(prepare_finalization(self.workspace, "alpha", self.run)["repairRequired"])

    def test_stale_manifest_cannot_overwrite_current_revision(self):
        self.create()
        before = self.manifest()
        schedule(self.workspace, "alpha", self.run)
        with run_lock(self.workspace, "alpha", self.run):
            with self.assertRaisesRegex(ValueError, "parallel_manifest_stale_revision"):
                save_manifest(self.workspace, "alpha", self.run, before)

    def test_conflict_reservation_keeps_target_stable_without_holding_os_lock(self):
        from hooks.parallel_repository import repository_lock, reservation
        self.create(2)
        first, _ = self.delivery("B001", "first\n")
        second, _ = self.delivery("B002", "second\n")
        settle_delivery(*first)
        conflict = settle_delivery(*second)
        self.assertEqual(conflict["status"], "candidate_conflicted")
        binding = self.manifest()["repositories"]["default"]
        with repository_lock(binding, timeout=0.1):
            self.assertEqual(reservation(binding)["wave"], conflict["wave"])
        with self.assertRaisesRegex(ValueError, "parallel_repository_target_reserved"):
            train.build_candidate(self.workspace, "alpha", self.run, wave=99, batch_ids=["B002"])
        prepared = train.prepare_resolution(self.workspace, "alpha", self.run, conflict["wave"], "default", "attempt")
        self.assertTrue(prepared["success"], prepared)
        (Path(prepared["worktreePath"]) / "shared.txt").write_text("first and second\n")
        self.assertTrue(train.complete_resolution(self.workspace, "alpha", self.run, conflict["wave"], "default")["success"])
        self.assertEqual(settle_delivery(*second)["status"], "merged")
        self.assertEqual(reservation(binding), {})

    def test_different_repositories_build_concurrently(self):
        other = DeliveryTest()
        other.setUp()
        self.addCleanup(other.doCleanups)
        self.create()
        other.create()
        calls = [self.delivery()[0], other.delivery()[0]]
        barrier = threading.Barrier(2, timeout=10)
        original = train._build_candidate
        def build(*args, **kwargs):
            barrier.wait()
            return original(*args, **kwargs)
        with patch.object(train, "_build_candidate", side_effect=build), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda args: settle_delivery(*args), calls))
        self.assertTrue(all(item["status"] == "merged" for item in results))

    def test_upgrade_rejects_active_execution_and_preserves_test_policy(self):
        from hooks.parallel_delivery import upgrade_protocol
        self.create()
        with run_lock(self.workspace, "alpha", self.run):
            manifest = self.manifest()
            manifest["controlProtocolVersion"] = 1
            save_manifest(self.workspace, "alpha", self.run, manifest)
        owner = claim(self.workspace, "alpha", self.run, "B001", 1, "initial", "worker")
        with self.assertRaisesRegex(ValueError, "quiescent"):
            upgrade_protocol(self.workspace, "alpha", self.run)
        from hooks.parallel_dispatch import finish
        finish(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], "failed")
        before = self.manifest()
        self.assertEqual(upgrade_protocol(self.workspace, "alpha", self.run)["status"], "upgraded")
        after = self.manifest()
        self.assertEqual(before["timeoutPerBatch"], after["timeoutPerBatch"])
        self.assertEqual(before["batches"], after["batches"])

    def test_partial_cleanup_after_branch_deletion_is_recoverable(self):
        from hooks import parallel_cleanup
        self.create()
        settle_delivery(*self.delivery()[0])
        original = parallel_cleanup._git
        interrupted = False
        def execute(repo, deadline, *argv):
            nonlocal interrupted
            result = original(repo, deadline, *argv)
            if argv[:2] == ("branch", "-d") and not interrupted:
                interrupted = True
                raise ValueError("interrupted_after_branch_delete")
            return result
        with patch.object(parallel_cleanup, "_git", side_effect=execute):
            self.assertTrue(drain(self.workspace, "alpha", self.run)["cleanupPending"])
        self.assertEqual(drain(self.workspace, "alpha", self.run)["cleanupPending"], [])

    def test_cleanup_budget_bounds_job_lock_wait(self):
        from hooks.evidence_kernel import FileLock
        from hooks.parallel_runtime import run_dir
        self.create()
        settle_delivery(*self.delivery()[0])
        job_id = next(iter(self.manifest()["cleanupQueue"]))
        held, stop = threading.Event(), threading.Event()
        def hold():
            with FileLock(run_dir(self.workspace, "alpha", self.run) / "cleanup-locks" / (job_id + ".lock")):
                held.set()
                stop.wait(10)
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(hold)
            self.assertTrue(held.wait(3))
            try:
                started = time.monotonic()
                result = drain(self.workspace, "alpha", self.run, budget_seconds=1)
                self.assertLess(time.monotonic() - started, 5)
                self.assertTrue(result["cleanupPending"])
            finally:
                stop.set()
                task.result()


if __name__ == "__main__":
    unittest.main()
