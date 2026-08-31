from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from hooks.json_writer_common import atomic_write_json
from hooks.json_writer_common import WriterResult
from hooks.parallel_batch_scheduler import create_run, mark_batch, schedule
from hooks.parallel_batch_stage import complete_stage, defer_stage, fail_stage, gate_batch, start_stage
from hooks.parallel_evidence_aggregate import aggregate_evidence
from hooks.parallel_merge_train import begin_e2e, build_candidate, finish_e2e, promote_candidate, verify_candidate
from hooks.parallel_runtime import load_manifest
from hooks.parallel_stage_validation import owned_commands, run_owned_stage
from hooks.parallel_validation_ownership import build_pipeline_contract, validation_ownership_errors
from hooks.worktree_manager import provision_parallel_worktree
from tests.test_task_runner import _git, _workspace


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


def _add_validation_intent(feature_dir: Path, *, asset_type: str = "unit_test") -> None:
    batch_path = feature_dir / "plans" / "B001" / "plan.json"
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    batch["tasks"][0]["validationTestPlan"] = [{
        "commandId": "VAL-T001-01",
        "assetType": asset_type,
        "executionStage": "post_batch" if asset_type != "unit_test" else "with_code",
        "covers": ["AC-T001-01"],
        "testIntent": {"behavior": "deliver behavior", "acceptanceCriteria": batch["tasks"][0]["acceptanceCriteria"]},
    }]
    atomic_write_json(batch_path, batch)
    _enable_pipeline(feature_dir)


class ParallelStagedPipelineTest(unittest.TestCase):
    def test_unresolved_review_is_deferred_without_blocking_batch_gate(self) -> None:
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
            self.assertEqual(states["prepare"]["status"], "passed")
            self.assertEqual(states["implement"]["status"], "pending")
            self.assertEqual(states["review"]["status"], "pending")
            recovery = schedule(workspace, "alpha", run_id)
            self.assertEqual(
                [(item["batchId"], item["nextStage"]) for item in recovery["stageRecoveryBatches"]],
                [("B001", "implement")],
            )

            start_stage(workspace, "alpha", run_id, "B001", "implement")
            complete_stage(workspace, "alpha", run_id, "B001", "implement", metadata={"batchCommit": "reviewed-commit"})
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
            self.assertTrue(gate_batch(workspace, "alpha", run_id, "B001")["success"])
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(manifest["batches"]["B001"]["stageStates"]["review"]["status"], "deferred")
            self.assertEqual(manifest["deferredIssues"][0]["issueId"], "DEFERRED-B001-REVIEW-001")

    def test_plan_ownership_is_required_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, _ = _workspace(Path(tmp))
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            batch = json.loads((feature_dir / root["batches"][0]["path"]).read_text(encoding="utf-8"))
            root.pop("parallelBatchPipeline")
            self.assertIn("parallel_batch_pipeline_missing", validation_ownership_errors(root, {"B001": batch}))
            root["parallelBatchPipeline"] = build_pipeline_contract(root, {"B001": batch})
            self.assertEqual(validation_ownership_errors(root, {"B001": batch}), [])

            root["projectValidationCommands"] = []
            root["parallelBatchPipeline"] = build_pipeline_contract(root, {"B001": batch})
            self.assertIn(
                "parallel_batch_pipeline_integration_commands_missing",
                validation_ownership_errors(root, {"B001": batch}),
            )

    def test_candidate_sha_is_verified_promoted_and_evidence_aggregated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _enable_pipeline(feature_dir)
            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            initial_manifest = load_manifest(workspace, "alpha", run_id)
            self.assertNotIn("quality_gate", initial_manifest["batches"]["B001"]["stageStates"])
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
            fail_stage(
                workspace,
                "alpha",
                run_id,
                "B001",
                "review",
                failure_type="implementation",
                message="known review finding",
            )
            start_stage(workspace, "alpha", run_id, "B001", "implement")
            complete_stage(workspace, "alpha", run_id, "B001", "implement", metadata={"batchCommit": commit})
            defer_stage(workspace, "alpha", run_id, "B001", "review", disposition="repair_limit_reached")
            start_stage(workspace, "alpha", run_id, "B001", "test")
            complete_stage(workspace, "alpha", run_id, "B001", "test", metadata={"batchCommit": commit})
            gated = gate_batch(workspace, "alpha", run_id, "B001")
            self.assertTrue(gated["success"], gated)

            candidate = build_candidate(workspace, "alpha", run_id, wave=1, batch_ids=["B001"])
            self.assertTrue(candidate["success"])
            verified = verify_candidate(workspace, "alpha", run_id, wave=1, repository_ref="default")
            self.assertTrue(verified["success"], verified)
            with patch("hooks.parallel_merge_train.mark_parallel_batch_tasks_merged", return_value=WriterResult(ok=True)):
                promoted = promote_candidate(workspace, "alpha", run_id, wave=1, repository_ref="default")
            self.assertTrue(promoted["success"], promoted)
            self.assertEqual(_git_output(repo, "rev-parse", "HEAD"), candidate["candidateSha"])

            started = begin_e2e(workspace, "alpha", run_id)
            self.assertTrue(started["success"])
            finished = finish_e2e(workspace, "alpha", run_id, passed=True, metadata={"message": "passed", "environment": {"version": "test", "seedDataDigest": "sha256:test", "dependencies": {"database": "none"}}})
            self.assertTrue(finished["success"])
            aggregate = aggregate_evidence(workspace, "alpha", run_id)
            self.assertTrue(aggregate["passed"], aggregate["errors"])
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(manifest["status"], "succeeded_with_issues")
            self.assertTrue(aggregate["hasDeferredIssues"])
            self.assertEqual(aggregate["deferredIssues"][0]["message"], "known review finding")

    def test_quality_gate_exists_only_for_declared_static_check_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            root_path = feature_dir / "plan.json"
            batch_path = feature_dir / "plans" / "B001" / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            static_check = {
                "argv": ["python3", "-c", "print('static check')"],
                "cwd": ".",
                "kind": "static_check",
                "required": True,
            }
            root["qualityGateProfiles"] = {"backend": {"commands": [static_check]}}
            batch["qualityGateCommands"] = [{**static_check, "id": "BATCH-B001-QUALITY-001"}]
            atomic_write_json(root_path, root)
            atomic_write_json(batch_path, batch)
            _enable_pipeline(feature_dir)

            created = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = created["runId"]
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertIn("quality_gate", manifest["batches"]["B001"]["stageStates"])
            self.assertTrue(manifest["batches"]["B001"]["qualityGateRequired"])

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
            self.assertTrue(run_owned_stage(workspace, "alpha", run_id, "B001", "test")["success"])
            quality = run_owned_stage(workspace, "alpha", run_id, "B001", "quality_gate")
            self.assertTrue(quality["success"], quality)
            self.assertEqual([item["commandId"] for item in quality["commands"]], ["BATCH-B001-QUALITY-001"])
            self.assertTrue(gate_batch(workspace, "alpha", run_id, "B001")["success"])

    def test_owned_commands_run_once_in_their_declared_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _add_validation_intent(feature_dir)
            bundle_root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            bundle_batch = json.loads((feature_dir / "plans" / "B001" / "plan.json").read_text(encoding="utf-8"))
            owners = bundle_root["parallelBatchPipeline"]["validationOwnership"]
            self.assertEqual(owners["BATCH-B001-COMPILE"]["stage"], "implement")
            self.assertEqual(owners["VAL-T001-01"], {
                "ownerBatchId": "B001", "stage": "test", "kind": "test_intent", "taskId": "T001", "sourceBatchId": "B001",
            })
            self.assertEqual(owners["PROJECT-VAL-001"]["ownerBatchId"], "V-INT")
            self.assertEqual(validation_ownership_errors(bundle_root, {"B001": bundle_batch}), [])

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
            tested = run_owned_stage(workspace, "alpha", run_id, "B001", "test")
            self.assertTrue(tested["success"], tested)
            self.assertEqual([item["commandId"] for item in tested["commands"]], ["VAL-T001-01"])
            self.assertTrue(gate_batch(workspace, "alpha", run_id, "B001")["success"])

    def test_failed_validation_creates_review_required_repair_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            root["projectValidationCommands"][0]["argv"] = ["python3", "-c", "raise SystemExit(1)"]
            atomic_write_json(root_path, root)
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
            failed = verify_candidate(workspace, "alpha", run_id, wave=1, repository_ref="default")
            self.assertFalse(failed["success"])
            self.assertEqual(failed["repair"]["repairFor"], "V-INT")
            self.assertTrue(failed["repair"]["requireReview"])
            self.assertIn("review", failed["repair"]["stageStates"])
