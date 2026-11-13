"""Real Git coverage for coordinated promotion, replay and deferred cleanup."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hooks.parallel_batch_scheduler import create_run, mark_batch, schedule, resume_run, manual_resume_run
from hooks.parallel_batch_stage import start_stage, complete_stage, review_checkpoint, gate_batch
from hooks.parallel_evidence_aggregate import aggregate_evidence
from hooks.parallel_dispatch import claim, defer, finish
from hooks.parallel_delivery import settle_delivery, prepare_finalization, main as delivery_main
from hooks.parallel_cleanup import drain, pending
from hooks.parallel_runtime import load_manifest, run_lock, save_manifest, run_dir
from hooks.parallel_integration import git, integration_repo, prepare_promotion
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
        stages = ["prepare", "implement"]
        if review:
            stages.append("review")
        for stage in stages:
            start_stage(self.workspace, "alpha", self.run, batch_id, stage)
            complete_stage(self.workspace, "alpha", self.run, batch_id, stage, metadata={"batchCommit": sha})
        return (self.workspace, "alpha", self.run, batch_id, 1, owner["ownerToken"], sha), tree

    def test_frontend_and_backend_deliver_without_any_test_preparation_or_execution(self):
        self.create(2)
        baseline = git(self.repo, "rev-parse", "HEAD")
        manifest = self.manifest()
        for batch_id, lane in (("B001", "frontend"), ("B002", "backend")):
            manifest["batches"][batch_id]["executionLane"] = lane
        save_manifest(self.workspace, "alpha", self.run, manifest)
        with patch("hooks.utest_assignment_router.build_assignments", side_effect=AssertionError("test assignment must not run")), \
                patch("hooks.engineering_context.execute", side_effect=AssertionError("test environment must not be prepared")):
            for batch_id in ("B001", "B002"):
                args, _ = self.delivery(batch_id)
                checkpoint = review_checkpoint(self.workspace, "alpha", self.run, batch_id)
                self.assertNotIn("utestPreparation", checkpoint)
                before = self.manifest()["batches"][batch_id]["stageStates"]
                self.assertNotIn("test", before)
                self.assertEqual(settle_delivery(*args)["status"], "merged")
                self.assertEqual(self.manifest()["batches"][batch_id]["stageStates"], before)
                self.assertTrue(settle_delivery(*args)["reused"])
            self.assertFalse(prepare_finalization(self.workspace, "alpha", self.run)["repairRequired"])
            report = aggregate_evidence(self.workspace, "alpha", self.run)
        self.assertTrue(report["passed"], report)
        self.assertNotIn("e2eStatus", report)
        self.assertNotIn("skippedUtests", report)
        self.assertEqual(self.manifest()["validationBatches"], {})
        self.assertTrue(all(owner["stage"] == "review" for owner in
                            self.manifest()["pipeline"]["validationOwnership"].values()))
        directory = run_dir(self.workspace, "alpha", self.run)
        self.assertFalse((directory / "utest").exists())
        self.assertFalse((directory / "validation-worktrees").exists())
        approval = prepare_promotion(self.workspace, "alpha", self.run)
        self.assertEqual(approval["mainPromotion"]["status"], "prepared")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), baseline)

    def test_retired_test_entries_cannot_create_work_in_a_code_run(self):
        from hooks.parallel_batch_stage import prepare_test
        from hooks.parallel_merge_train import begin_e2e
        self.create()
        before = self.manifest()
        with self.assertRaisesRegex(ValueError, "parallel_code_test_stage_removed"):
            prepare_test(self.workspace, "alpha", self.run, "B001")
        with self.assertRaisesRegex(ValueError, "parallel_code_e2e_stage_removed"):
            begin_e2e(self.workspace, "alpha", self.run)
        self.assertEqual(self.manifest(), before)
        directory = run_dir(self.workspace, "alpha", self.run)
        self.assertFalse((directory / "utest").exists())
        self.assertFalse((directory / "validation-worktrees").exists())

    def test_removing_tests_does_not_bypass_review_or_a_stale_dispatch(self):
        self.create()
        args, _ = self.delivery(review=False)
        with self.assertRaisesRegex(ValueError, "delivery_gate_failed.*review"):
            settle_delivery(*args)
        with self.assertRaisesRegex(ValueError, "stale_stage_owner"):
            settle_delivery(*(*args[:5], "invalid-token", args[6]))

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
        # Waiting releases the old token. Reclaim and continue the same ticket
        # after advancing past the short queue backoff, without code retries.
        clock = time.time()
        for _ in range(5):
            clock += 31
            queued = [item for item in results if item["status"] == "queued"]
            if not queued:
                break
            for item in queued:
                with patch("hooks.parallel_dispatch.time.time", return_value=clock):
                    owner = claim(self.workspace, "alpha", self.run, item["batchId"], 1, "merge_candidate", "resume")
                    self.assertEqual(owner["status"], "claimed", owner)
                    result = settle_delivery(self.workspace, "alpha", self.run, item["batchId"], 1, owner["ownerToken"])
                results[results.index(item)] = result
        self.assertTrue(all(result["status"] == "merged" for result in results))
        self.assertEqual(len(self.manifest()["mergeTrains"]), 5)
        self.assertTrue(all(item["status"] == "promoted" for item in self.manifest()["mergeTrains"].values()))
        self.assertEqual(len({result["wave"] for result in results}), 5)
        self.assertTrue(all((self.integration / f"B{i:03d}.txt").exists() for i in range(1, 6)))

    def test_runtime_resolves_commit_but_preserves_explicit_and_in_flight_version_fences(self):
        self.create()
        args, _ = self.delivery()
        with self.assertRaisesRegex(ValueError, "delivery_commit_changed"):
            settle_delivery(*args[:6], "0" * 40)
        self.assertFalse(self.manifest().get("deliveryOperations"))
        baseline = git(self.integration, "rev-parse", "HEAD")
        build = train._build_candidate
        def change_seal(*build_args, **kwargs):
            built = build(*build_args, **kwargs)
            manifest = self.manifest()
            manifest["batches"]["B001"]["commitSha"] = "0" * 40
            save_manifest(self.workspace, "alpha", self.run, manifest)
            return built
        with patch.object(train, "_build_candidate", side_effect=change_seal):
            with self.assertRaisesRegex(ValueError, "delivery_commit_changed"):
                settle_delivery(*args[:6])
        self.assertEqual(git(self.integration, "rev-parse", "HEAD"), baseline)
        manifest = self.manifest()
        manifest["batches"]["B001"]["commitSha"] = args[6]
        save_manifest(self.workspace, "alpha", self.run, manifest)
        self.assertEqual(settle_delivery(*args[:6])["deliverySha"], args[6])

    def test_queued_delivery_releases_capacity_without_spending_retries_and_resumes_same_ticket(self):
        self.create(2)
        first, _ = self.delivery("B001")
        second, _ = self.delivery("B002")
        with patch.object(train, "_build_candidate", side_effect=RuntimeError("worker terminated")):
            with self.assertRaisesRegex(RuntimeError, "worker terminated"):
                settle_delivery(*first[:6])
        started = time.monotonic()
        queued = settle_delivery(*second[:6])
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["blockingBatchIds"], ["B001"])
        self.assertTrue(queued["dispatchReleased"])
        batch = self.manifest()["batches"]["B002"]
        self.assertEqual(batch["dispatch"]["status"], "waiting")
        self.assertEqual(batch.get("recovery", {}).get("retryAttempts", 0), 0)
        self.assertEqual(queued["schedulerSnapshot"]["activeWorkers"], 0)
        with self.assertRaisesRegex(ValueError, "stale_stage_owner"):
            settle_delivery(*second[:6])
        generation = self.manifest()["batches"]["B001"]["dispatchGeneration"]
        self.assertEqual(generation, 2)
        self.assertEqual(defer(*first[:6], "late failure")["status"], "stale_owner_ignored")
        owner = claim(self.workspace, "alpha", self.run, "B001", generation, "merge_candidate", "recover-head")
        self.assertEqual(owner["status"], "claimed")
        self.assertEqual(settle_delivery(*first[:4], generation, owner["ownerToken"])["status"], "merged")
        owner = claim(self.workspace, "alpha", self.run, "B002", 1, "merge_candidate", "resume")
        self.assertEqual(owner["status"], "claimed")
        merged = settle_delivery(*second[:5], owner["ownerToken"])
        self.assertEqual(merged["operationId"], queued["operationId"])
        self.assertTrue(merged["dispatchFinalized"])

    def test_repository_lock_contention_is_a_normal_wait(self):
        self.create()
        args, _ = self.delivery()
        from hooks.parallel_repository import repository_lock
        started = time.time()
        with repository_lock(self.manifest()["repositories"]["default"]):
            queued = settle_delivery(*args[:6])
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["reasonCode"], "repository_busy")
        self.assertGreaterEqual(self.manifest()["batches"]["B001"]["deliveryQueue"]["retryAfterEpoch"], started + 30)
        self.assertEqual(claim(self.workspace, "alpha", self.run, "B001", 1, "merge_candidate", "resume")["reasonCode"], "delivery_queue_wait")

    def test_a_reserved_integration_target_is_a_normal_wait_without_a_code_retry(self):
        from hooks.parallel_repository import reserve, release
        self.create()
        args, _ = self.delivery()
        binding = self.manifest()["repositories"]["default"]
        reserve(binding, self.workspace, "alpha", self.run, 99, binding["headSha"])
        queued = settle_delivery(*args[:6])
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["reasonCode"], "target_reserved")
        self.assertEqual(self.manifest()["batches"]["B001"].get("recovery", {}).get("retryAttempts", 0), 0)
        release(binding, self.workspace, "alpha", self.run, 99)
        with patch("hooks.parallel_dispatch.time.time", return_value=time.time() + 31):
            owner = claim(self.workspace, "alpha", self.run, "B001", 1, "merge_candidate", "resume")
            result = settle_delivery(*args[:5], owner["ownerToken"])
        self.assertEqual(result["status"], "merged")

    def test_live_settler_and_duplicate_command_are_not_reclaimed_by_a_scheduler_poll(self):
        self.create()
        args, _ = self.delivery()
        entered, proceed = threading.Event(), threading.Event()
        build = train._build_candidate
        def pause(*build_args, **kwargs):
            entered.set()
            self.assertTrue(proceed.wait(10))
            return build(*build_args, **kwargs)
        with patch.object(train, "_build_candidate", side_effect=pause), ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(settle_delivery, *args[:6])
            self.assertTrue(entered.wait(5))
            try:
                # Neither an elapsed caller deadline nor a dispatch TTL can
                # authorize another merge while the original process lives.
                manifest = self.manifest()
                manifest["batches"]["B001"]["dispatch"]["expiresEpoch"] = time.time() - 1
                save_manifest(self.workspace, "alpha", self.run, manifest)
                self.assertEqual(claim(self.workspace, "alpha", self.run, "B001", 1,
                                       "merge_candidate", "duplicate")["reasonCode"], "running")
                self.assertEqual(defer(*args[:6], "caller timed out")["status"], "delivery_running")
                self.assertEqual(finish(*args[:6], "failed")["status"], "delivery_running")
                state = schedule(self.workspace, "alpha", self.run)
                self.assertEqual(state["dispatchGrants"]["B001"], 1)
                duplicate = settle_delivery(*args[:6])
                self.assertEqual(duplicate["status"], "queued")
                self.assertFalse(duplicate["dispatchReleased"])
                self.assertEqual(self.manifest()["batches"]["B001"]["dispatch"]["status"], "running")
            finally:
                proceed.set()
            self.assertEqual(task.result()["status"], "merged")
        self.assertEqual(len(self.manifest()["mergeTrains"]), 1)

    def test_delivery_claim_waits_for_same_repository_and_wakes_on_completion(self):
        self.create(2)
        first, _ = self.delivery("B001")
        second, _ = self.delivery("B002")
        for batch_id in ("B001", "B002"):
            gate_batch(self.workspace, "alpha", self.run, batch_id)
        manifest = self.manifest()
        # A recovery claim reserves delivery even before its process starts.
        manifest["batches"]["B001"]["dispatch"]["source"] = "merge_candidate"
        manifest["batches"]["B002"]["dispatch"]["status"] = "waiting"
        # Different workspaceRefs for one physical repository share admission.
        manifest["repositories"]["alias"] = copy.deepcopy(manifest["repositories"]["default"])
        manifest["batches"]["B002"]["repositoryRef"] = "alias"
        save_manifest(self.workspace, "alpha", self.run, manifest)
        before = self.manifest()
        denied = claim(self.workspace, "alpha", self.run, "B002", 1, "merge_candidate", "second")
        self.assertEqual(denied["reasonCode"], "delivery_queue_wait")
        self.assertEqual(self.manifest(), before)
        settle_delivery(*first[:6])
        owner = claim(self.workspace, "alpha", self.run, "B002", 1, "merge_candidate", "second")
        self.assertEqual(owner["status"], "claimed")
        self.assertEqual(settle_delivery(*second[:5], owner["ownerToken"])["status"], "merged")

    def test_cli_settles_without_a_transcribed_commit_parameter(self):
        self.create()
        args, _ = self.delivery()
        output = io.StringIO()
        with redirect_stdout(output):
            status = delivery_main(["settle-delivery", "--workspace", str(self.workspace), "--feature", "alpha",
                                    "--run-id", self.run, "--batch-id", "B001", "--dispatch-generation", "1",
                                    "--dispatch-token", args[5]])
        self.assertEqual(status, 0, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "merged")
        self.assertEqual(result["deliverySha"], args[6])

    def test_closed_sealed_delivery_is_claimable_after_retry_without_replaying_review(self):
        self.create()
        self.delivery()
        for test_status in ("passed", "skipped", "deferred"):
            with self.subTest(test_status=test_status):
                # One delivery is restored repeatedly; each retry gets its own
                # generation and starts from the already durable stage results.
                manifest = self.manifest()
                manifest["batches"]["B001"]["stageStates"].setdefault("test", {})["status"] = test_status
                save_manifest(self.workspace, "alpha", self.run, manifest)
                mark_batch(self.workspace, "alpha", self.run, "B001", "retry_pending", error="delivery interrupted " + test_status)
                resumed = manual_resume_run(self.workspace, "alpha", self.run)
                recovery = resumed["stageRecoveryBatches"][0]
                self.assertEqual(recovery["nextStage"], "delivery")
                owner = claim(self.workspace, "alpha", self.run, "B001", resumed["dispatchGrants"]["B001"], "stage_recovery", "resume")
                self.assertEqual(owner["status"], "claimed")

    def test_resume_reuses_a_built_candidate_after_settler_dies(self):
        self.create()
        args, _ = self.delivery()
        with patch.object(train, "_promote_candidate", side_effect=RuntimeError("worker terminated")):
            with self.assertRaisesRegex(RuntimeError, "worker terminated"):
                settle_delivery(*args[:6])
        before = next(iter(self.manifest()["mergeTrains"].values()))
        self.assertEqual(before["status"], "built")
        resumed = resume_run(self.workspace, "alpha", self.run)
        self.assertIn("B001", resumed["mergeableBatches"])
        self.assertIsNone(self.manifest()["batches"]["B001"].get("dispatch"))
        generation = resumed["dispatchGrants"]["B001"]
        owner = claim(self.workspace, "alpha", self.run, "B001", generation, "merge_candidate", "resume")
        for _ in range(40):
            polled = schedule(self.workspace, "alpha", self.run)
            self.assertEqual(polled["dispatchGrants"]["B001"], generation)
        result = settle_delivery(*args[:4], generation, owner["ownerToken"])
        self.assertEqual(result["mergeCommitSha"], before["candidateSha"])
        self.assertEqual(len(self.manifest()["mergeTrains"]), 1)

    def test_old_frontend_handoff_resumes_without_reopening_the_report(self):
        self.create()
        args, _ = self.delivery()
        with patch.object(train, "_promote_candidate", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                settle_delivery(*args)
        manifest = self.manifest()
        op = next(iter(manifest["deliveryOperations"].values()))
        op.update(status="awaiting_frontend_recheck", frontendRecheck={"taskId": "T001"})
        save_manifest(self.workspace, "alpha", self.run, manifest)
        self.assertEqual(schedule(self.workspace, "alpha", self.run)["dispatchGrants"]["B001"], 1)
        result = settle_delivery(*args)
        self.assertEqual(result["status"], "merged")
        self.assertEqual(result["operationId"], op["operationId"])
        self.assertNotIn("frontendRecheck", self.manifest()["deliveryOperations"][op["operationId"]])

    def test_frontend_git_checkout_line_endings_do_not_require_candidate_review(self):
        git(self.repo, "config", "core.autocrlf", "true")
        self.create()
        manifest = self.manifest()
        manifest["batches"]["B001"]["executionLane"] = "frontend"
        save_manifest(self.workspace, "alpha", self.run, manifest)
        args, worktree = self.delivery(content="page behaviour\n")
        (worktree / "shared.txt").write_bytes(b"page behaviour\n")
        # A pre-upgrade report belongs to the TASK and stays untouched at delivery.
        report = self.feature / "frontend-verification" / "T001" / "old-run.json"
        report.parent.mkdir(parents=True)
        report.write_text('{"bindingDigest":"old-lf-report","status":"passed"}')
        before = report.read_bytes()
        result = settle_delivery(*args)
        self.assertEqual(result["status"], "merged")
        record = next(iter(self.manifest()["mergeTrains"].values()))
        self.assertEqual((Path(record["worktreePath"]) / "shared.txt").read_bytes(), b"page behaviour\r\n")
        self.assertEqual((worktree / "shared.txt").read_bytes(), b"page behaviour\n")
        self.assertEqual(report.read_bytes(), before)

    def test_automatic_merge_of_overlapping_file_only_reviews_its_changed_content(self):
        original = "top\n" + "unchanged\n" * 10 + "bottom\n"
        (self.repo / "shared.txt").write_text(original)
        git(self.repo, "add", "shared.txt")
        git(self.repo, "commit", "-m", "shared baseline")
        self.create(2)
        manifest = self.manifest()
        manifest["batches"]["B002"]["executionLane"] = "frontend"
        save_manifest(self.workspace, "alpha", self.run, manifest)
        first, _ = self.delivery("B001", original.replace("top", "first"))
        second, _ = self.delivery("B002", original.replace("bottom", "second"))
        self.assertEqual(settle_delivery(*first)["status"], "merged")
        delta = settle_delivery(*second)
        self.assertEqual(delta["status"], "candidate_review_required")
        self.assertEqual(delta["reviewScope"], [{"batchId": "B002", "deliverySha": second[-1], "paths": ["shared.txt"]}])
        self.assertEqual(settle_delivery(*second, reviewed_candidate=delta["candidateSha"])["status"], "merged")
        self.assertEqual((self.integration / "shared.txt").read_text(),
                         original.replace("top", "first").replace("bottom", "second"))

    def test_review_for_another_seal_blocks_delivery(self):
        self.create()
        args, _ = self.delivery()
        batch = self.manifest()["batches"]["B001"]
        path = Path(batch["stageStates"]["review"]["evidencePath"])
        evidence = json.loads(path.read_text())
        evidence["inputs"]["batchCommit"] = "0" * 40
        path.write_text(json.dumps(evidence))
        with self.assertRaisesRegex(ValueError, "review_not_bound_to_seal"):
            settle_delivery(*args)
        self.assertFalse(self.manifest().get("mergeTrains"))

    def test_merge_failure_can_resume_after_repeated_failure_and_a_new_generation(self):
        self.create()
        args, _ = self.delivery()
        for attempt in range(5):
            if attempt:
                generation = self.manifest()["batches"]["B001"]["dispatchGeneration"]
                owner = claim(self.workspace, "alpha", self.run, "B001", generation, "merge_candidate", f"retry-{attempt}")
                args = (*args[:4], generation, owner["ownerToken"], args[6])
            with patch.object(train, "_build_candidate", side_effect=RuntimeError("candidate creation interrupted")):
                with self.assertRaisesRegex(RuntimeError, "candidate creation interrupted"):
                    settle_delivery(*args[:6])
            defer(*args[:6], "candidate creation interrupted")
            self.assertEqual(self.manifest()["batches"]["B001"]["status"], "retry_pending")
            if attempt < 2:
                # An old scheduler could leave either kind of retry block on
                # this same immutable delivery. Automatic resume must retain it.
                manifest = self.manifest()
                batch = manifest["batches"]["B001"]
                batch["status"] = "blocked"
                batch["recovery"]["status"] = ("retry_exhausted", "unchanged_failure")[attempt]
                batch["recovery"]["maxAutomaticAttempts"] = 2
                save_manifest(self.workspace, "alpha", self.run, manifest)
            resumed = resume_run(self.workspace, "alpha", self.run)
            self.assertEqual(resumed["rescheduledRetryBatches"], ["B001"])
        self.assertEqual(self.manifest()["batches"]["B001"]["recovery"]["retryAttempts"], 5)
        generation = resumed["dispatchGrants"]["B001"]
        owner = claim(self.workspace, "alpha", self.run, "B001", generation, "merge_candidate", "resume")
        merged = settle_delivery(*args[:4], generation, owner["ownerToken"])
        self.assertEqual(merged["status"], "merged")
        self.assertEqual(merged["wave"], 1, "the reservation made before candidate creation must be reused")

    def test_scheduler_finishes_receipts_after_git_or_dispatch_closure_interruption(self):
        self.create(2)
        for batch_id, checkpoint in (("B001", "checkpoint"), ("B002", "dispatch")):
            args, _ = self.delivery(batch_id)
            target = patch.object(train, "_checkpoint_promoted_plan_recovery", side_effect=RuntimeError("interrupted")) if checkpoint == "checkpoint" else patch("hooks.parallel_dispatch.finish", side_effect=RuntimeError("interrupted"))
            with target:
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    settle_delivery(*args[:6])
            head = git(self.integration, "rev-parse", "HEAD")
            resume_run(self.workspace, "alpha", self.run)
            batch = self.manifest()["batches"][batch_id]
            self.assertEqual(batch["status"], "merged")
            self.assertEqual(batch["dispatch"]["status"], "completed")
            self.assertEqual(git(self.integration, "rev-parse", "HEAD"), head)
            self.assertEqual(settle_delivery(*args[:6])["mergeCommitSha"], head)
        self.assertFalse(prepare_finalization(self.workspace, "alpha", self.run)["repairRequired"])

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

    def test_consumed_generation_three_resumes_reviewed_delivery_on_generation_four(self):
        self.create(3)
        for index, outcome in enumerate(("completed", "failed", "retry_pending"), 1):
            batch_id = f"B{index:03d}"
            args, tree = self.delivery(batch_id)
            gate_batch(self.workspace, "alpha", self.run, batch_id)
            manifest = self.manifest()
            batch = manifest["batches"][batch_id]
            batch["executionLane"] = "frontend" if index == 1 else "backend"
            batch["dispatchGeneration"] = batch["dispatch"]["generation"] = 3
            stages = copy.deepcopy(batch["stageStates"])
            save_manifest(self.workspace, "alpha", self.run, manifest)
            finish(*args[:4], 3, args[5], outcome)
            self.assertEqual(claim(*args[:4], 3, "merge_candidate", "new-workflow")["reasonCode"], "grant_consumed")

            state = schedule(self.workspace, "alpha", self.run)
            self.assertEqual(state["dispatchGrants"][batch_id], 4)
            self.assertIn(batch_id, state["mergeableBatches"])
            self.assertEqual(self.manifest()["batches"][batch_id]["stageStates"], stages)
            for _ in range(3):
                self.assertEqual(schedule(self.workspace, "alpha", self.run)["dispatchGrants"][batch_id], 4)
            owner = claim(*args[:4], 4, "merge_candidate", "new-workflow")
            self.assertEqual(owner["status"], "claimed")
            self.assertEqual(finish(*args[:4], 3, args[5])["status"], "stale_owner_ignored")
            merged = settle_delivery(*args[:4], 4, owner["ownerToken"])
            self.assertEqual(merged["status"], "merged")
            self.assertEqual(merged["deliverySha"], args[6])
            batch = self.manifest()["batches"][batch_id]
            self.assertEqual(batch["worktreePath"], str(tree))
            self.assertEqual(batch["stageStates"], stages)
            self.assertEqual(batch.get("recovery", {}).get("retryAttempts", 0), 0)
            self.assertEqual(schedule(self.workspace, "alpha", self.run)["dispatchGrants"][batch_id], 4)

    def test_consumed_grant_recovers_its_built_candidate_and_queue_ticket(self):
        self.create()
        args, _ = self.delivery()
        with patch.object(train, "_promote_candidate", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                settle_delivery(*args)
        before = self.manifest()
        operation = next(iter(before["deliveryOperations"].values()))
        candidate = next(iter(before["mergeTrains"].values()))
        finish(*args[:6], "failed")
        state = resume_run(self.workspace, "alpha", self.run)
        self.assertEqual(state["dispatchGrants"]["B001"], 2)
        owner = claim(*args[:4], 2, "merge_candidate", "new-workflow")
        merged = settle_delivery(*args[:4], 2, owner["ownerToken"])
        self.assertEqual(merged["status"], "merged")
        self.assertEqual(merged["mergeCommitSha"], candidate["candidateSha"])
        self.assertEqual(len(self.manifest()["mergeTrains"]), 1)
        recovered = self.manifest()["deliveryOperations"][merged["operationId"]]
        self.assertEqual(recovered["ticket"], operation["ticket"])

    def test_consumed_grant_recovery_waits_for_a_live_settler(self):
        from hooks.parallel_delivery import _execution_lock
        self.create()
        args, _ = self.delivery()
        gate_batch(self.workspace, "alpha", self.run, "B001")
        finish(*args[:6], "failed")
        with _execution_lock(self.workspace, "alpha", self.run, "B001"):
            self.assertEqual(schedule(self.workspace, "alpha", self.run)["dispatchGrants"]["B001"], 1)
        self.assertEqual(schedule(self.workspace, "alpha", self.run)["dispatchGrants"]["B001"], 2)

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

    def test_each_merged_batch_is_cleaned_while_other_worktrees_remain_usable(self):
        self.create(2)
        binding = self.manifest()["repositories"]["default"]
        baseline_branch, integration_branch = binding["baseBranch"], binding["integration"]["branchName"]
        baseline_sha = git(self.repo, "rev-parse", baseline_branch)
        args, tree = self.delivery()
        second, active_tree = self.delivery("B002")
        active_branch = git(active_tree, "symbolic-ref", "--short", "HEAD")
        settle_delivery(*args)
        candidate = Path(self.manifest()["mergeTrains"]["default:wave-001"]["worktreePath"])
        cleanup = drain(self.workspace, "alpha", self.run)
        self.assertEqual(cleanup["status"], "completed", cleanup)
        self.assertFalse(tree.exists())
        self.assertFalse(candidate.exists())
        self.assertTrue(self.repo.exists())
        self.assertTrue(self.integration.exists())
        self.assertTrue(active_tree.exists())
        self.assertEqual(git(active_tree, "symbolic-ref", "--short", "HEAD"), active_branch)
        self.assertTrue(prepare_finalization(self.workspace, "alpha", self.run)["repairRequired"])
        self.assertEqual(settle_delivery(*second)["status"], "merged")
        self.assertEqual(drain(self.workspace, "alpha", self.run)["cleanupPending"], [])
        self.assertFalse(active_tree.exists())
        self.assertEqual((self.integration / "B002.txt").read_text(), "B002")
        self.assertEqual(git(self.repo, "rev-parse", baseline_branch), baseline_sha)
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD"), baseline_branch)
        self.assertEqual(git(self.integration, "symbolic-ref", "--short", "HEAD"), integration_branch)
        self.assertEqual(git(self.repo, "rev-parse", integration_branch), self.manifest()["repositories"]["default"]["headSha"])

    def test_stale_manifest_cannot_overwrite_current_revision(self):
        self.create()
        before = self.manifest()
        schedule(self.workspace, "alpha", self.run)
        with run_lock(self.workspace, "alpha", self.run):
            with self.assertRaisesRegex(ValueError, "parallel_manifest_stale_revision"):
                save_manifest(self.workspace, "alpha", self.run, before)

    def backend_conflict(self):
        self.create(2)
        first, _ = self.delivery("B001", "first\n")
        second, _ = self.delivery("B002", "second\n")
        manifest = self.manifest()
        for batch in manifest["batches"].values():
            batch["executionLane"] = "backend"
        save_manifest(self.workspace, "alpha", self.run, manifest)
        settle_delivery(*first)
        conflict = settle_delivery(*second)
        self.assertEqual(conflict["status"], "candidate_conflicted")
        return second, conflict

    def resolve_backend_conflict(self, conflict):
        prepared = train.prepare_resolution(self.workspace, "alpha", self.run, conflict["wave"], "default", "attempt")
        self.assertTrue(prepared["success"], prepared)
        (Path(prepared["worktreePath"]) / "shared.txt").write_text("first and second\n")
        resolved = train.complete_resolution(self.workspace, "alpha", self.run, conflict["wave"], "default")
        self.assertTrue(resolved["success"], resolved)
        return resolved

    def test_conflict_reservation_keeps_target_stable_without_holding_os_lock(self):
        from hooks.parallel_repository import repository_lock, reservation
        second, conflict = self.backend_conflict()
        binding = self.manifest()["repositories"]["default"]
        owner = copy.deepcopy(self.manifest()["batches"]["B002"]["dispatch"])
        operation = copy.deepcopy(self.manifest()["deliveryOperations"][conflict["operationId"]])
        self.assertEqual(operation["status"], "awaiting_candidate_resolution")
        for _ in range(40):
            self.assertEqual(schedule(self.workspace, "alpha", self.run)["dispatchGrants"]["B002"], 1)
        with repository_lock(binding, timeout=0.1):
            self.assertEqual(reservation(binding)["wave"], conflict["wave"])
        with self.assertRaisesRegex(ValueError, "parallel_repository_target_reserved"):
            train.build_candidate(self.workspace, "alpha", self.run, wave=99, batch_ids=["B002"])
        resolved = self.resolve_backend_conflict(conflict)
        self.assertEqual(resolved["status"], "built")
        for index in range(40):
            state = (resume_run if index % 10 == 0 else schedule)(self.workspace, "alpha", self.run)
            self.assertEqual(state["dispatchGrants"]["B002"], 1)
        self.assertEqual(self.manifest()["batches"]["B002"]["dispatch"], owner)
        self.assertEqual(self.manifest()["deliveryOperations"][conflict["operationId"]], operation)
        delta = settle_delivery(*second)
        self.assertEqual(delta["status"], "candidate_review_required")
        self.assertEqual(delta["reviewScope"], [{"batchId": "B002", "deliverySha": second[-1], "paths": ["shared.txt"]}])
        for _ in range(3):
            self.assertEqual(schedule(self.workspace, "alpha", self.run)["dispatchGrants"]["B002"], 1)
        with self.assertRaisesRegex(ValueError, "candidate_review_sha_changed"):
            settle_delivery(*second, reviewed_candidate="0" * 40)
        merged = settle_delivery(*second, reviewed_candidate=delta["candidateSha"])
        self.assertEqual(merged["status"], "merged")
        self.assertEqual(merged["operationId"], conflict["operationId"])
        self.assertEqual(merged["mergeCommitSha"], resolved["candidateSha"])
        self.assertEqual(len(self.manifest()["mergeTrains"]), 2)
        self.assertEqual(reservation(binding), {})

    def test_resolved_backend_conflict_can_recover_after_original_grant_expires(self):
        second, conflict = self.backend_conflict()
        resolved = self.resolve_backend_conflict(conflict)
        manifest = self.manifest()
        ticket = manifest["deliveryOperations"][conflict["operationId"]]["ticket"]
        manifest["batches"]["B002"]["dispatch"]["expiresEpoch"] = time.time() - 1
        save_manifest(self.workspace, "alpha", self.run, manifest)
        state = schedule(self.workspace, "alpha", self.run)
        self.assertEqual(state["dispatchGrants"]["B002"], 2)
        with self.assertRaisesRegex(ValueError, "stale_stage_owner"):
            settle_delivery(*second)
        owner = claim(self.workspace, "alpha", self.run, "B002", 2, "merge_candidate", "recover")
        merged = settle_delivery(*second[:4], 2, owner["ownerToken"], reviewed_candidate=resolved["candidateSha"])
        self.assertEqual(merged["status"], "merged")
        self.assertEqual(merged["mergeCommitSha"], resolved["candidateSha"])
        self.assertEqual(self.manifest()["deliveryOperations"][merged["operationId"]]["ticket"], ticket)
        self.assertEqual(len(self.manifest()["mergeTrains"]), 2)

    def test_crash_after_resuming_resolved_backend_candidate_does_not_keep_waiting(self):
        second, conflict = self.backend_conflict()
        resolved = self.resolve_backend_conflict(conflict)
        with patch.object(train, "_promote_candidate", side_effect=RuntimeError("worker terminated")):
            with self.assertRaisesRegex(RuntimeError, "worker terminated"):
                settle_delivery(*second)
        self.assertEqual(self.manifest()["deliveryOperations"][conflict["operationId"]]["status"], "queued")
        state = schedule(self.workspace, "alpha", self.run)
        self.assertEqual(state["dispatchGrants"]["B002"], 2)
        owner = claim(self.workspace, "alpha", self.run, "B002", 2, "merge_candidate", "recover")
        self.assertEqual(settle_delivery(*second[:4], 2, owner["ownerToken"], reviewed_candidate=resolved["candidateSha"])["status"], "merged")

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
