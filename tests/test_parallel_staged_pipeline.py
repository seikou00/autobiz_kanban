from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from hooks.json_writer_common import atomic_write_json
from hooks.json_writer_common import WriterResult
from hooks.parallel_batch_scheduler import create_run as _create_run, mark_batch, schedule
from hooks.parallel_batch_stage import (
    complete_stage,
    defer_stage,
    fail_stage,
    gate_batch,
    record_single_repair_resolution,
    record_test_failure,
    start_stage,
    validate_review_result,
)
from hooks.parallel_evidence_aggregate import aggregate_evidence
from hooks.parallel_merge_train import _remove_candidate, begin_e2e, build_candidate, finish_e2e, promote_candidate, recover_promoted_plan
from hooks.parallel_runtime import acquire_lease, load_manifest, release_lease
from hooks.parallel_validation_ownership import build_pipeline_contract, validation_ownership_errors
from hooks.worktree_manager import provision_parallel_worktree, seal_parallel_batch
from tests.test_task_runner import _git, _workspace


def create_run(workspace: Path, feature: str, **kwargs):
    """Create a workflow run with the production commit context in tests."""
    kwargs.setdefault("task_card_id", "Z990692-294")
    return _create_run(workspace, feature, **kwargs)


def _git_output(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _enable_pipeline(feature_dir: Path) -> None:
    root_path = feature_dir / "plan.json"
    root = json.loads(root_path.read_text(encoding="utf-8"))
    batches = {
        entry["id"]: json.loads((feature_dir / entry["path"]).read_text(encoding="utf-8"))
        for entry in root["batches"]
    }
    root["parallelBatchPipeline"] = build_pipeline_contract(root, batches)
    atomic_write_json(root_path, root)


class ParallelStagedPipelineTest(unittest.TestCase):
    def test_candidate_cleanup_recovers_interrupted_initializing_worktree(self) -> None:
        """A timed-out candidate build may leave Git's initializing lock behind."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "wave-001"
            candidate.mkdir()
            first_remove = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr=(
                    "fatal: cannot remove a locked working tree, lock reason: initializing\n"
                    "use 'remove -f -f' to override or unlock first"
                ),
            )
            calls: list[tuple[str, ...]] = []

            def fake_git(_repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
                calls.append(args)
                if args == ("worktree", "remove", "--force", str(candidate)):
                    return first_remove
                if args == ("worktree", "remove", "--force", "--force", str(candidate)):
                    candidate.rmdir()
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

            with patch("hooks.parallel_merge_train._git", side_effect=fake_git):
                errors = _remove_candidate(
                    root,
                    candidate,
                    "autodev-candidate/alpha/wave-001",
                    recover_interrupted_initialization=True,
                )

        self.assertEqual(errors, [])
        self.assertIn(("worktree", "remove", "--force", str(candidate)), calls)
        self.assertIn(("worktree", "remove", "--force", "--force", str(candidate)), calls)

    def test_recorded_test_failure_allows_batch_gate_to_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            worktree = Path(provisioned["worktreePath"])
            (worktree / "delivery.txt").write_text("delivery\n", encoding="utf-8")
            _git(worktree, "add", "delivery.txt")
            _git(worktree, "commit", "-m", "delivery")
            commit = _git_output(worktree, "rev-parse", "HEAD")
            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "sealed",
                worktreePath=provisioned["worktreePath"],
                branchName=provisioned["branchName"],
                commitSha=commit,
                compileStatus="passed",
            )
            for stage in ("prepare", "implement", "review"):
                start_stage(workspace, "alpha", run_id, "B001", stage)
                complete_stage(workspace, "alpha", run_id, "B001", stage, metadata={"batchCommit": commit})
            start_stage(workspace, "alpha", run_id, "B001", "test")

            recorded = record_test_failure(
                workspace,
                "alpha",
                run_id,
                "B001",
                failure_type="implementation",
                message="T001 assertion failed; evidence=UTEST-T001-001",
                metadata={"batchCommit": commit, "testEvidenceIds": ["UTEST-T001-001"]},
            )
            gated = gate_batch(workspace, "alpha", run_id, "B001")
            candidate = build_candidate(workspace, "alpha", run_id, wave=1, batch_ids=["B001"])
            self.assertTrue(candidate["success"])
            self.assertEqual(
                _git_output(Path(candidate["worktreePath"]), "log", "-1", "--format=%s"),
                "Z990692-294 #comment 合并候选 default wave-001 B001",
            )
            with patch("hooks.parallel_merge_train.mark_parallel_batch_tasks_merged", return_value=WriterResult(ok=True)):
                self.assertTrue(promote_candidate(workspace, "alpha", run_id, wave=1, repository_ref="default", allow_unverified=True)["success"])
            self.assertTrue(begin_e2e(workspace, "alpha", run_id)["success"])
            self.assertTrue(finish_e2e(
                workspace,
                "alpha",
                run_id,
                passed=True,
                metadata={"environment": {"version": "test", "seedDataDigest": "sha256:test", "dependencies": {"database": "none"}}},
            )["success"])
            aggregate = aggregate_evidence(workspace, "alpha", run_id)
            manifest = load_manifest(workspace, "alpha", run_id)

        self.assertTrue(recorded["success"])
        self.assertFalse(recorded["issue"]["blocksWorkflow"])
        self.assertTrue(gated["success"], gated)
        self.assertEqual(gated["status"], "ready_to_candidate")
        self.assertEqual(gated["continuedTestFailureStages"], ["test"])
        self.assertEqual(manifest["batches"]["B001"]["stageStates"]["test"]["status"], "deferred")
        self.assertEqual(manifest["deferredIssues"][0]["kind"], "test_failure")
        self.assertTrue(aggregate["passed"], aggregate["errors"])
        self.assertTrue(aggregate["hasDeferredIssues"])
        self.assertFalse(aggregate["hasBlockingDeferredIssues"])
        self.assertEqual(manifest["status"], "succeeded_with_issues")

    def test_promoted_candidate_plan_failure_checkpoints_and_recovers(self) -> None:
        """A Plan failure after ff must not leave binding.headSha at base."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            worktree = Path(provisioned["worktreePath"])
            (worktree / "delivery.txt").write_text("delivery\n", encoding="utf-8")
            _git(worktree, "add", "delivery.txt")
            _git(worktree, "commit", "-m", "delivery")
            commit = _git_output(worktree, "rev-parse", "HEAD")
            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "sealed",
                worktreePath=provisioned["worktreePath"],
                branchName=provisioned["branchName"],
                commitSha=commit,
                compileStatus="passed",
            )
            for stage in ("prepare", "implement", "review", "test"):
                start_stage(workspace, "alpha", run_id, "B001", stage)
                complete_stage(workspace, "alpha", run_id, "B001", stage, metadata={"batchCommit": commit})
            self.assertTrue(gate_batch(workspace, "alpha", run_id, "B001")["success"])
            candidate = build_candidate(workspace, "alpha", run_id, wave=1, batch_ids=["B001"])
            self.assertTrue(candidate["success"], candidate)

            failed_writer = WriterResult(ok=False, errors=[{"reason": "parallel_batch_task_not_implemented"}])
            with patch("hooks.parallel_merge_train.mark_parallel_batch_tasks_merged", return_value=failed_writer):
                promoted = promote_candidate(workspace, "alpha", run_id, wave=1, repository_ref="default", allow_unverified=True)
            self.assertFalse(promoted["success"])
            persisted = load_manifest(workspace, "alpha", run_id)
            train = persisted["mergeTrains"]["default:wave-001"]
            self.assertEqual(_git_output(repo, "rev-parse", "HEAD"), candidate["candidateSha"])
            self.assertEqual(persisted["repositories"]["default"]["headSha"], candidate["candidateSha"])
            self.assertEqual(train["resolution"]["kind"], "promoted_plan_state_update")
            self.assertEqual(train["status"], "needs_resolution")

            with patch("hooks.parallel_merge_train.mark_parallel_batch_tasks_merged", return_value=WriterResult(ok=True, errors=[])):
                recovered = recover_promoted_plan(workspace, "alpha", run_id, repository_ref="default", wave=1)
            self.assertTrue(recovered["success"], recovered)
            persisted = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(persisted["batches"]["B001"]["status"], "merged")
            self.assertEqual(persisted["mergeTrains"]["default:wave-001"]["status"], "promoted")
            self.assertNotIn("resolution", persisted["mergeTrains"]["default:wave-001"])

    def test_unresolved_review_deferred_finding_blocks_batch_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "sealed",
                worktreePath=provisioned["worktreePath"],
                branchName=provisioned["branchName"],
                commitSha="reviewed-commit",
            )
            for stage in ("prepare", "implement"):
                start_stage(workspace, "alpha", run_id, "B001", stage)
                complete_stage(workspace, "alpha", run_id, "B001", stage, metadata={"batchCommit": "reviewed-commit"})
            start_stage(workspace, "alpha", run_id, "B001", "review")

            failed = fail_stage(
                workspace,
                "alpha",
                run_id,
                "B001",
                "review",
                failure_type="implementation",
                message="missing authorization check",
            )

            self.assertEqual(failed["nextStage"], "implement")
            self.assertEqual(
                failed["failure"],
                {
                    "type": "implementation",
                    "message": "missing authorization check",
                    "nextStage": "implement",
                },
            )
            states = load_manifest(workspace, "alpha", run_id)["batches"]["B001"]["stageStates"]
            self.assertEqual(states["prepare"]["status"], "pending")
            self.assertEqual(states["implement"]["status"], "pending")
            self.assertEqual(states["review"]["status"], "pending")
            recovery = schedule(workspace, "alpha", run_id)
            self.assertEqual(
                [(item["batchId"], item["nextStage"]) for item in recovery["stageRecoveryBatches"]],
                [("B001", "implement")],
            )
            self.assertEqual(
                recovery["stageRecoveryBatches"][0]["failureContext"],
                {
                    "failedStage": "review",
                    "failureType": "implementation",
                    "message": "missing authorization check",
                },
            )

            for stage in ("prepare", "implement"):
                start_stage(workspace, "alpha", run_id, "B001", stage)
                complete_stage(workspace, "alpha", run_id, "B001", stage, metadata={"batchCommit": "reviewed-commit"})
            deferred = defer_stage(
                workspace,
                "alpha",
                run_id,
                "B001",
                "review",
                disposition="repeated_feedback",
            )
            self.assertEqual(deferred["status"], "deferred")
            self.assertEqual(deferred["issue"]["message"], "missing authorization check")
            self.assertEqual(deferred["issue"]["disposition"], "repeated_feedback")

            start_stage(workspace, "alpha", run_id, "B001", "test")
            complete_stage(workspace, "alpha", run_id, "B001", "test", metadata={"batchCommit": "reviewed-commit"})
            gated = gate_batch(workspace, "alpha", run_id, "B001")
            self.assertFalse(gated["success"])
            self.assertEqual(gated["error"], "parallel_batch_stage_gate_deferred_findings")
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(manifest["batches"]["B001"]["status"], "blocked")
            self.assertEqual(manifest["batches"]["B001"]["stageStates"]["review"]["status"], "deferred")
            self.assertEqual(manifest["deferredIssues"][0]["issueId"], "DEFERRED-B001-REVIEW-001")

    def test_utest_failure_is_recorded_without_creating_recovery_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "sealed",
                worktreePath=provisioned["worktreePath"],
                branchName=provisioned["branchName"],
                commitSha="tested-commit",
            )
            for stage in ("prepare", "implement", "review"):
                start_stage(workspace, "alpha", run_id, "B001", stage)
                complete_stage(workspace, "alpha", run_id, "B001", stage, metadata={"batchCommit": "tested-commit"})
            start_stage(workspace, "alpha", run_id, "B001", "test")
            recorded = fail_stage(
                workspace,
                "alpha",
                run_id,
                "B001",
                "test",
                failure_type="implementation",
                message="targetId=UT-001 evidenceId=ev_0042 expected=200 actual=500",
            )

            recovery = schedule(workspace, "alpha", run_id)["stageRecoveryBatches"]
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(recorded["status"], "deferred")
            self.assertEqual(recovery, [])
            self.assertEqual(manifest["batches"]["B001"]["stageStates"]["test"]["failure"], {
                "type": "implementation",
                "message": "targetId=UT-001 evidenceId=ev_0042 expected=200 actual=500",
                "nextStage": "continue",
            })
            self.assertEqual(manifest["deferredIssues"][0]["kind"], "test_failure")

    def test_single_repair_resolution_preserves_original_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "sealed",
                worktreePath=provisioned["worktreePath"],
                branchName=provisioned["branchName"],
                commitSha="repaired-commit",
            )
            for stage in ("prepare", "implement"):
                start_stage(workspace, "alpha", run_id, "B001", stage)
                complete_stage(workspace, "alpha", run_id, "B001", stage, metadata={"batchCommit": "old-commit"})
            start_stage(workspace, "alpha", run_id, "B001", "review")
            fail_stage(
                workspace,
                "alpha",
                run_id,
                "B001",
                "review",
                failure_type="implementation",
                message="src/auth.py:42 authorization is missing",
            )

            for stage in ("prepare", "implement", "review"):
                start_stage(workspace, "alpha", run_id, "B001", stage)
                complete_stage(
                    workspace,
                    "alpha",
                    run_id,
                    "B001",
                    stage,
                    metadata={
                        "batchCommit": "repaired-commit",
                        **({"repairDisposition": "single_repair_accepted"} if stage == "review" else {}),
                    },
                )

            state = load_manifest(workspace, "alpha", run_id)["batches"]["B001"]["stageStates"]
            self.assertEqual(state["review"]["status"], "passed")
            self.assertEqual(
                state["review"]["repairResolution"],
                {
                    "disposition": "single_repair_accepted",
                    "failure": {
                        "type": "implementation",
                        "message": "src/auth.py:42 authorization is missing",
                        "nextStage": "implement",
                    },
                    "resolvedAt": state["review"]["completedAt"],
                },
            )

    def test_review_validator_and_single_repair_cli_keep_model_output_out_of_control_plane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "sealed",
                worktreePath=provisioned["worktreePath"],
                branchName=provisioned["branchName"],
                commitSha="repaired-commit",
                compileStatus="skipped",
            )
            for stage in ("prepare", "implement"):
                start_stage(workspace, "alpha", run_id, "B001", stage)
                complete_stage(workspace, "alpha", run_id, "B001", stage, metadata={"batchCommit": "old-commit"})
            start_stage(workspace, "alpha", run_id, "B001", "review")
            fail_stage(
                workspace,
                "alpha",
                run_id,
                "B001",
                "review",
                failure_type="implementation",
                message="src/auth.py:42 authorization is missing",
            )

            # The persisted state is intentionally reset to pending for a
            # rework, but the validator exposes the logical failed decision.
            validated = validate_review_result(workspace, "alpha", run_id, "B001")
            self.assertTrue(validated["success"])
            self.assertEqual(validated["status"], "failed")
            self.assertEqual(validated["durableState"], "pending")
            self.assertEqual(validated["failure"]["message"], "src/auth.py:42 authorization is missing")

            recorded = record_single_repair_resolution(
                workspace,
                "alpha",
                run_id,
                "B001",
                failed_stage="review",
                metadata={
                    "batchCommit": "repaired-commit",
                    "worktreePath": provisioned["worktreePath"],
                    "branchName": provisioned["branchName"],
                },
            )
            self.assertTrue(recorded["success"])
            self.assertEqual(recorded["status"], "success")
            self.assertEqual(recorded["stage"], "review")
            self.assertEqual(len(recorded["stages"]), 3)
            verified = validate_review_result(workspace, "alpha", run_id, "B001")
            self.assertEqual(verified["status"], "passed")
            state = load_manifest(workspace, "alpha", run_id)["batches"]["B001"]["stageStates"]["review"]
            self.assertEqual(state["repairResolution"]["disposition"], "single_repair_accepted")

    def test_review_draft_is_sealed_before_post_review_compile_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            worktree = Path(provisioned["worktreePath"])
            lease = acquire_lease(workspace, "alpha", run_id, "B001", ttl_seconds=60)
            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "running",
                worktreePath=str(worktree),
                branchName=provisioned["branchName"],
            )
            (worktree / "reviewable.txt").write_text("review before compile\n", encoding="utf-8")
            draft = seal_parallel_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                worktree,
                lease["ownerToken"],
                purpose="review",
            )
            self.assertTrue(draft["success"], draft)
            self.assertEqual(draft["purpose"], "review")
            release_lease(workspace, "alpha", run_id, "B001", lease["ownerToken"], final_status="sealed")

            for stage in ("prepare", "implement", "review"):
                start_stage(workspace, "alpha", run_id, "B001", stage)
                complete_stage(workspace, "alpha", run_id, "B001", stage, metadata={"batchCommit": draft["commitSha"]})

            compile_lease = acquire_lease(workspace, "alpha", run_id, "B001", ttl_seconds=60)
            self.assertTrue(compile_lease["ownerToken"])
            release_lease(workspace, "alpha", run_id, "B001", compile_lease["ownerToken"], final_status="pending")

    def test_seal_cleans_stale_index_lock_after_bounded_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            worktree = Path(provisioned["worktreePath"])
            lease = acquire_lease(workspace, "alpha", run_id, "B001", ttl_seconds=60)
            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "running",
                worktreePath=str(worktree),
                branchName=provisioned["branchName"],
            )
            (worktree / "delivery.txt").write_text("delivery\n", encoding="utf-8")
            raw_lock_path = _git_output(worktree, "rev-parse", "--git-path", "index.lock")
            lock_path = Path(raw_lock_path)
            if not lock_path.is_absolute():
                lock_path = worktree / lock_path
            lock_path.write_text("stale or externally-held lock\n", encoding="utf-8")

            with patch("hooks.worktree_manager.GIT_INDEX_LOCK_RETRY_DELAY_SECONDS", 0):
                sealed = seal_parallel_batch(
                    workspace,
                    "alpha",
                    run_id,
                    "B001",
                    worktree,
                    lease["ownerToken"],
                    purpose="review",
                )

            self.assertTrue(sealed["success"], sealed)
            self.assertFalse(lock_path.exists())
            self.assertEqual(len(sealed["indexLockRecoveries"]), 1)
            recovery = sealed["indexLockRecoveries"][0]
            self.assertEqual(Path(recovery["lockPath"]), lock_path)
            self.assertEqual(recovery["retryAttempts"], 4)
            self.assertEqual(recovery["action"], "removed_stale_index_lock_and_retried")

    def test_skipped_frontend_compile_can_seal_and_release_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            worktree = Path(provisioned["worktreePath"])
            (worktree / "delivery.ts").write_text("export const delivered = true;\n", encoding="utf-8")
            _git(worktree, "add", "delivery.ts")
            _git(worktree, "commit", "-m", "review draft")
            draft_commit = _git_output(worktree, "rev-parse", "HEAD")
            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "sealed",
                worktreePath=str(worktree),
                branchName=provisioned["branchName"],
                commitSha=draft_commit,
                compileStatus="skipped",
            )
            lease = acquire_lease(workspace, "alpha", run_id, "B001", ttl_seconds=60)
            sealed = seal_parallel_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                worktree,
                lease["ownerToken"],
                purpose="implementation",
            )
            self.assertTrue(sealed["success"], sealed)
            self.assertEqual(sealed["commitSha"], draft_commit)
            release_lease(workspace, "alpha", run_id, "B001", lease["ownerToken"], final_status="sealed")
            batch = load_manifest(workspace, "alpha", run_id)["batches"]["B001"]
            self.assertEqual(batch["status"], "sealed")
            self.assertIsNone(batch["lease"])

    def test_plan_ownership_is_required_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            batch = json.loads((feature_dir / root["batches"][0]["path"]).read_text(encoding="utf-8"))
            root.pop("parallelBatchPipeline")
            self.assertIn("parallel_batch_pipeline_missing", validation_ownership_errors(root, {"B001": batch}))
            root["parallelBatchPipeline"] = build_pipeline_contract(root, {"B001": batch})
            self.assertEqual(validation_ownership_errors(root, {"B001": batch}), [])

    def test_batch_utest_gated_candidate_is_promoted_and_e2e_aggregated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            initial_manifest = load_manifest(workspace, "alpha", run_id)
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            self.assertTrue(provisioned["success"])
            worktree = Path(provisioned["worktreePath"])
            (worktree / "delivery.txt").write_text("delivery\n", encoding="utf-8")
            _git(worktree, "add", "delivery.txt")
            _git(worktree, "commit", "-m", "delivery")
            commit = _git_output(worktree, "rev-parse", "HEAD")
            mark_batch(workspace, "alpha", run_id, "B001", "sealed", worktreePath=str(worktree), branchName=provisioned["branchName"], commitSha=commit, compileStatus="passed")
            for stage in ("prepare", "implement"):
                start_stage(workspace, "alpha", run_id, "B001", stage)
                complete_stage(workspace, "alpha", run_id, "B001", stage, metadata={"batchCommit": commit})
            start_stage(workspace, "alpha", run_id, "B001", "review")
            complete_stage(workspace, "alpha", run_id, "B001", "review", metadata={"batchCommit": commit})
            lease = acquire_lease(workspace, "alpha", run_id, "B001", ttl_seconds=60)
            start_stage(workspace, "alpha", run_id, "B001", "test")
            test_file = worktree / "tests" / "test_delivery.py"
            test_file.parent.mkdir()
            test_file.write_text("def test_delivery():\n    assert True\n", encoding="utf-8")
            resealed = seal_parallel_batch(workspace, "alpha", run_id, "B001", worktree, lease["ownerToken"])
            self.assertTrue(resealed["success"], resealed)
            self.assertEqual(resealed["purpose"], "utest")
            self.assertEqual(resealed["changedFiles"], ["tests/test_delivery.py"])
            commit = resealed["commitSha"]
            complete_stage(workspace, "alpha", run_id, "B001", "test", metadata={"batchCommit": commit})
            release_lease(workspace, "alpha", run_id, "B001", lease["ownerToken"], final_status="sealed")
            gated = gate_batch(workspace, "alpha", run_id, "B001")
            self.assertTrue(gated["success"], gated)

            candidate = build_candidate(workspace, "alpha", run_id, wave=1, batch_ids=["B001"])
            self.assertTrue(candidate["success"])
            with patch("hooks.parallel_merge_train.mark_parallel_batch_tasks_merged", return_value=WriterResult(ok=True)):
                promoted = promote_candidate(workspace, "alpha", run_id, wave=1, repository_ref="default", allow_unverified=True)
            self.assertTrue(promoted["success"], promoted)
            self.assertEqual(_git_output(repo, "rev-parse", "HEAD"), candidate["candidateSha"])

            started = begin_e2e(workspace, "alpha", run_id)
            self.assertTrue(started["success"])
            finished = finish_e2e(workspace, "alpha", run_id, passed=True, metadata={"message": "passed", "environment": {"version": "test", "seedDataDigest": "sha256:test", "dependencies": {"database": "none"}}})
            self.assertTrue(finished["success"])
            aggregate = aggregate_evidence(workspace, "alpha", run_id)
            self.assertTrue(aggregate["passed"], aggregate["errors"])
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(manifest["status"], "succeeded")
            self.assertFalse(aggregate["hasDeferredIssues"])
            record = manifest["mergeTrains"]["default:wave-001"]
            self.assertEqual(record["validation"]["reason"], "batch_utest_gated_e2e_only")

    def test_utest_reseal_rejects_unreviewed_production_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            worktree = Path(provisioned["worktreePath"])
            (worktree / "delivery.txt").write_text("delivery\n", encoding="utf-8")
            _git(worktree, "add", "delivery.txt")
            _git(worktree, "commit", "-m", "delivery")
            commit = _git_output(worktree, "rev-parse", "HEAD")
            mark_batch(workspace, "alpha", run_id, "B001", "sealed", worktreePath=str(worktree), branchName=provisioned["branchName"], commitSha=commit, compileStatus="passed")
            for stage in ("prepare", "implement", "review"):
                start_stage(workspace, "alpha", run_id, "B001", stage)
                complete_stage(workspace, "alpha", run_id, "B001", stage, metadata={"batchCommit": commit})
            lease = acquire_lease(workspace, "alpha", run_id, "B001", ttl_seconds=60)
            start_stage(workspace, "alpha", run_id, "B001", "test")
            (worktree / "delivery.txt").write_text("unreviewed production change\n", encoding="utf-8")
            rejected = seal_parallel_batch(workspace, "alpha", run_id, "B001", worktree, lease["ownerToken"])
            self.assertFalse(rejected["success"])
            self.assertEqual(rejected["error"], "parallel_utest_production_change_forbidden")
            self.assertEqual(rejected["files"], ["delivery.txt"])

    def test_utest_source_bug_releases_resealed_worktree_for_implementation_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            worktree = Path(provisioned["worktreePath"])
            (worktree / "delivery.txt").write_text("delivery\n", encoding="utf-8")
            _git(worktree, "add", "delivery.txt")
            _git(worktree, "commit", "-m", "delivery")
            commit = _git_output(worktree, "rev-parse", "HEAD")
            mark_batch(workspace, "alpha", run_id, "B001", "sealed", worktreePath=str(worktree), branchName=provisioned["branchName"], commitSha=commit, compileStatus="passed")
            for stage in ("prepare", "implement", "review"):
                start_stage(workspace, "alpha", run_id, "B001", stage)
                complete_stage(workspace, "alpha", run_id, "B001", stage, metadata={"batchCommit": commit})
            lease = acquire_lease(workspace, "alpha", run_id, "B001", ttl_seconds=60)
            start_stage(workspace, "alpha", run_id, "B001", "test")
            test_file = worktree / "tests" / "test_source_bug.py"
            test_file.parent.mkdir()
            test_file.write_text("def test_source_bug():\n    assert False\n", encoding="utf-8")
            resealed = seal_parallel_batch(workspace, "alpha", run_id, "B001", worktree, lease["ownerToken"])
            self.assertTrue(resealed["success"], resealed)
            failure = fail_stage(workspace, "alpha", run_id, "B001", "test", failure_type="implementation", message="failing test proves source bug")
            self.assertEqual(failure["status"], "deferred")
            self.assertFalse(failure["issue"]["blocksWorkflow"])
            release_lease(workspace, "alpha", run_id, "B001", lease["ownerToken"], final_status="sealed")
            batch = load_manifest(workspace, "alpha", run_id)["batches"]["B001"]
            self.assertEqual(batch["status"], "sealed")
            self.assertEqual(batch["stageStates"]["test"]["status"], "deferred")


    def test_failed_final_e2e_validation_creates_repair_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            worktree = Path(provisioned["worktreePath"])
            (worktree / "delivery.txt").write_text("delivery\n", encoding="utf-8")
            _git(worktree, "add", "delivery.txt")
            _git(worktree, "commit", "-m", "delivery")
            commit = _git_output(worktree, "rev-parse", "HEAD")
            mark_batch(workspace, "alpha", run_id, "B001", "sealed", worktreePath=str(worktree), branchName=provisioned["branchName"], commitSha=commit, compileStatus="passed")
            for stage in ("prepare", "implement", "review", "test"):
                start_stage(workspace, "alpha", run_id, "B001", stage)
                complete_stage(workspace, "alpha", run_id, "B001", stage, metadata={"batchCommit": commit})
            self.assertTrue(gate_batch(workspace, "alpha", run_id, "B001")["success"])
            self.assertTrue(build_candidate(workspace, "alpha", run_id, wave=1, batch_ids=["B001"])["success"])
            with patch("hooks.parallel_merge_train.mark_parallel_batch_tasks_merged", return_value=WriterResult(ok=True)):
                self.assertTrue(promote_candidate(workspace, "alpha", run_id, wave=1, repository_ref="default", allow_unverified=True)["success"])
            self.assertTrue(begin_e2e(workspace, "alpha", run_id)["success"])
            failed = finish_e2e(workspace, "alpha", run_id, passed=False, metadata={"message": "e2e scenario failed"})
            self.assertFalse(failed["success"])
            self.assertEqual(failed["repair"]["repairFor"], "V-E2E")
            self.assertTrue(failed["repair"]["requireReview"])
            self.assertIn("review", failed["repair"]["stageStates"])
