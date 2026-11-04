"""Real worktree recovery for persisted findings and already sealed repairs."""
import tempfile
import json
import unittest
from pathlib import Path

from hooks.parallel_batch_scheduler import mark_batch
from hooks.parallel_batch_stage import (
    begin_implementation, complete_stage, fail_stage, finish_review,
    prepare_implementation_repair, review_checkpoint, start_stage, triage_failure,
)
from hooks.parallel_runtime import acquire_lease, load_manifest, release_lease, save_manifest
from hooks.worktree_manager import provision_parallel_worktree, seal_parallel_batch
from tests.test_parallel_staged_pipeline import _enable_pipeline, _git_output, create_run
from tests.test_task_runner import _git, _workspace


class RepairRecoveryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace, feature, repo = _workspace(Path(temporary.name))
        _enable_pipeline(feature)
        self.run = create_run(self.workspace, "alpha", max_parallel=1, timeout_seconds=60,
                              code_workspaces=[str(repo)])["runId"]
        provisioned = provision_parallel_worktree(self.workspace, "alpha", self.run, "B001")
        self.root = Path(provisioned["worktreePath"])
        (self.root / "source.py").write_text("result = None\n")
        _git(self.root, "add", "source.py")
        _git(self.root, "commit", "-m", "initial delivery")
        self.base = _git_output(self.root, "rev-parse", "HEAD")
        self.args = (self.workspace, "alpha", self.run, "B001")
        mark_batch(*self.args, "sealed", worktreePath=str(self.root),
                   branchName=provisioned["branchName"], commitSha=self.base, compileStatus="passed")
        for stage in ("prepare", "implement"):
            start_stage(*self.args, stage)
            complete_stage(*self.args, stage, metadata={"batchCommit": self.base})
        start_stage(*self.args, "review")

    def batch(self):
        return load_manifest(self.workspace, "alpha", self.run)["batches"]["B001"]

    def finding(self):
        fail_stage(*self.args, "review", failure_type="implementation", message="source.py:1 unexpected None")

    def test_recorded_finding_opens_stale_passed_implementation_without_changing_the_guard(self):
        self.finding()
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["batches"]["B001"]["stageStates"]["implement"]["status"] = "passed"
        save_manifest(self.workspace, "alpha", self.run, manifest)
        with self.assertRaisesRegex(ValueError, "implementation_not_open"):
            begin_implementation(*self.args)
        self.assertEqual(prepare_implementation_repair(*self.args, "review")["status"], "ready")
        started = begin_implementation(*self.args)
        prepare_implementation_repair(*self.args, "review")
        self.assertEqual(begin_implementation(*self.args)["attempt"], started["attempt"])

    def test_sealed_repair_cannot_hide_missing_task_completion_evidence(self):
        self.finding()
        lease = acquire_lease(*self.args)
        (self.root / "source.py").write_text("result = 1\n")
        sealed = seal_parallel_batch(*self.args, self.root, lease["ownerToken"], purpose="review", commit_stage="rework")
        self.assertTrue(sealed["success"], sealed)
        release_lease(*self.args, lease["ownerToken"], final_status="sealed")
        with self.assertRaisesRegex(ValueError, "implementation_tasks_incomplete"):
            prepare_implementation_repair(*self.args, "review")
        self.assertNotEqual(self.batch()["stageStates"]["review"]["status"], "passed")
        self.assertEqual(_git_output(self.root, "rev-parse", "HEAD"), sealed["commitSha"])

    def test_an_unproven_commit_cannot_be_claimed_as_a_completed_repair(self):
        self.finding()
        (self.root / "unrelated.txt").write_text("unrelated\n")
        _git(self.root, "add", "unrelated.txt")
        _git(self.root, "commit", "-m", "unrelated change")
        manifest = load_manifest(self.workspace, "alpha", self.run)
        manifest["batches"]["B001"]["commitSha"] = _git_output(self.root, "rev-parse", "HEAD")
        save_manifest(self.workspace, "alpha", self.run, manifest)
        with self.assertRaisesRegex(ValueError, "repair_commit_unproven"):
            prepare_implementation_repair(*self.args, "review")
        self.assertEqual(self.batch()["stageStates"]["review"]["status"], "pending")

    def test_environment_triage_can_retry_review_and_record_a_new_decision(self):
        fail_stage(*self.args, "review", failure_type="needs_triage", message="review interrupted before a decision")
        triage_failure(*self.args, "review", "environment")
        start_stage(*self.args, "review")
        decision = finish_review(*self.args, decision="passed")
        self.assertEqual(decision["status"], "passed")
        self.assertEqual(self.batch()["stageStates"]["implement"]["status"], "passed")

    def test_external_uncertainty_is_recorded_once_and_does_not_block_testing(self):
        message = "External API empty response behavior is undocumented"
        first = finish_review(*self.args, decision="passed", advisory_message=message)
        self.assertEqual(first["status"], "passed")
        self.assertEqual(first["advisories"][0]["message"], message)
        self.assertEqual(finish_review(*self.args, decision="passed", advisory_message=message), first)
        manifest = load_manifest(self.workspace, "alpha", self.run)
        issue, = manifest["deferredIssues"]
        self.assertFalse(issue["blocksWorkflow"])
        self.assertEqual(issue["evidenceId"], first["evidenceId"])
        evidence = json.loads(Path(self.batch()["stageStates"]["review"]["evidencePath"]).read_text())
        self.assertEqual(evidence["metadata"]["reviewAdvisories"], first["advisories"])
        self.assertEqual(review_checkpoint(*self.args)["utestPreparation"]["status"], "test_required")

    def test_proven_defect_with_advisory_still_requires_implementation_repair(self):
        result = finish_review(*self.args, decision="failed", message="source.py:1 proven defect",
                               advisory_message="External API behavior is undocumented")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure"]["type"], "implementation")
        self.assertEqual(review_checkpoint(*self.args)["status"], "failed")
        self.assertEqual(finish_review(*self.args, decision="passed")["status"], "failed")
        self.assertEqual(self.batch()["stageStates"]["implement"]["status"], "pending")

    def _assert_legacy_advisory_is_normalized(self, failure_type):
        message = "External contract unavailable for verification"
        fail_stage(*self.args, "review", failure_type=failure_type, message=message)
        result = review_checkpoint(*self.args)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["utestPreparation"]["status"], "test_required")
        self.assertEqual(result["advisories"][0]["message"], message)
        self.assertEqual(self.batch()["stageStates"]["implement"]["status"], "passed")
        self.assertNotEqual(self.batch()["status"], "blocked")
        self.assertEqual(review_checkpoint(*self.args)["evidenceId"], result["evidenceId"])
        issues = load_manifest(self.workspace, "alpha", self.run)["deferredIssues"]
        self.assertEqual(len(issues), 1)
        self.assertFalse(issues[0]["blocksWorkflow"])

    def test_legacy_needs_triage_becomes_a_nonblocking_advisory(self):
        self._assert_legacy_advisory_is_normalized("needs_triage")

    def test_legacy_documentation_gap_becomes_a_nonblocking_advisory(self):
        self._assert_legacy_advisory_is_normalized("documentation")


if __name__ == "__main__":
    unittest.main()
