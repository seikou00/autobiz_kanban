from __future__ import annotations

import contextlib
import base64
import io
import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from hooks.json_writer_common import WriterResult
from hooks.parallel_batch_lifecycle import cleanup_run, cleanup_merged_batches, rollback_run
from hooks.parallel_batch_scheduler import create_run, ensure_run, mark_batch, assert_batch_worktree_isolated
from hooks.parallel_batch_stage import begin_implementation, complete_stage, gate_batch, start_stage
from hooks.parallel_evidence_aggregate import aggregate_evidence
from hooks.parallel_integration import (git, integration_repo, keep_integration, prepare_promotion,
                                        promote_main, sync_main)
from hooks.parallel_merge_train import begin_e2e, build_candidate, finish_e2e, promote_candidate, recover_promoted_plan
from hooks.parallel_runtime import acquire_lease, get_active_run, load_manifest, release_lease, save_manifest
from hooks.worktree_manager import provision_parallel_worktree, seal_parallel_batch
from hooks.workflow_launcher import analyze_batches
from tests.test_parallel_staged_pipeline import _enable_pipeline
from tests.test_task_runner import _workspace, _add_second_compile_only_batch


class ParallelIntegrationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="feature-integration-")
        self.addCleanup(temporary.cleanup)
        self.workspace, self.feature_dir, self.source = _workspace(Path(temporary.name))
        _enable_pipeline(self.feature_dir)
        self.baseline = git(self.source, "rev-parse", "main")

    def create(self):
        result = create_run(self.workspace, "alpha", max_parallel=2, timeout_seconds=60,
                            code_workspaces=[str(self.source)], task_card_id="Z990692-294")
        self.run_id = result["runId"]
        self.integration = integration_repo(self.manifest()["repositories"]["default"])
        return result

    def manifest(self):
        return load_manifest(self.workspace, "alpha", self.run_id)

    def deliver(self, *, filename="delivery.txt", content="delivery\n", batch_id="B001", wave=1, utest_changes=None, stage_commits=None):
        provisioned = provision_parallel_worktree(self.workspace, "alpha", self.run_id, batch_id)
        self.assertTrue(provisioned["success"], provisioned)
        worktree = Path(provisioned["worktreePath"])
        begin_implementation(self.workspace, "alpha", self.run_id, batch_id)
        (worktree / filename).write_text(content, encoding="utf-8")
        git(worktree, "add", filename)
        git(worktree, "commit", "-m", "delivery")
        commit = git(worktree, "rev-parse", "HEAD")
        mark_batch(self.workspace, "alpha", self.run_id, batch_id, "sealed", worktreePath=str(worktree),
                   branchName=provisioned["branchName"], commitSha=commit)
        for stage in ("implement", "review", "test"):
            start_stage(self.workspace, "alpha", self.run_id, batch_id, stage)
            if stage == "test" and utest_changes:
                lease = acquire_lease(self.workspace, "alpha", self.run_id, batch_id, ttl_seconds=60)
                for path, contents in utest_changes.items():
                    target = worktree / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(contents, encoding="utf-8")
                sealed = seal_parallel_batch(self.workspace, "alpha", self.run_id, batch_id, worktree, lease["ownerToken"])
                self.assertTrue(sealed["success"], sealed)
                commit = sealed["commitSha"]
            complete_stage(self.workspace, "alpha", self.run_id, batch_id, stage,
                           metadata={"batchCommit": (stage_commits or {}).get(stage, commit)})
            if stage == "test" and utest_changes:
                release_lease(self.workspace, "alpha", self.run_id, batch_id, lease["ownerToken"], final_status="sealed")
        self.assertTrue(gate_batch(self.workspace, "alpha", self.run_id, batch_id)["success"])
        candidate = build_candidate(self.workspace, "alpha", self.run_id, wave=wave, batch_ids=[batch_id])
        # Task execution has its own tests; this fixture exercises the native
        # Git integration lifecycle after the task writer accepts the delivery.
        with patch("hooks.parallel_merge_train.mark_parallel_batch_tasks_merged", return_value=WriterResult(ok=True)):
            promoted = promote_candidate(self.workspace, "alpha", self.run_id, wave=wave, repository_ref=self.manifest()["batches"][batch_id]["repositoryRef"], allow_unverified=True)
        self.assertTrue(promoted["success"], promoted)
        binding = self.manifest()["repositories"][self.manifest()["batches"][batch_id]["repositoryRef"]]
        self.assertEqual(git(integration_repo(binding), "rev-parse", "HEAD"), candidate["candidateSha"])
        return candidate

    def validate(self):
        started = begin_e2e(self.workspace, "alpha", self.run_id)
        self.assertTrue(started["success"], started)
        finished = finish_e2e(self.workspace, "alpha", self.run_id, passed=True,
                             metadata={"environment": {"version": "test", "seedDataDigest": "test-seed", "dependencies": {"db": "none"}}})
        self.assertTrue(finished["success"], finished)
        report = aggregate_evidence(self.workspace, "alpha", self.run_id)
        self.assertTrue(report["passed"], report)
        return report

    def prepare(self):
        return prepare_promotion(self.workspace, "alpha", self.run_id)["mainPromotion"]["approvalDigest"]

    def promote(self, approval, **kwargs):
        return promote_main(self.workspace, "alpha", self.run_id, approval_digest=approval,
                            approved_by="test-user", **kwargs)

    def test_default_uses_current_branch_head_and_preserves_dirty_source(self):
        git(self.source, "switch", "-c", "user-work")
        (self.source / "branch-only.txt").write_text("current branch commit\n")
        git(self.source, "add", "branch-only.txt")
        git(self.source, "commit", "-m", "current branch baseline")
        current_head = git(self.source, "rev-parse", "HEAD")
        (self.source / "existing.txt").write_text("uncommitted user changes\n")
        (self.source / "untracked.txt").write_text("user file\n")
        result = self.create()
        self.assertEqual(result["integrationMode"], "isolated")
        binding = self.manifest()["repositories"]["default"]
        self.assertEqual(binding["baseBranch"], "user-work")
        self.assertEqual(binding["baseSha"], current_head)
        self.assertEqual(git(self.integration, "rev-parse", "HEAD"), current_head)
        self.assertEqual((self.integration / "branch-only.txt").read_text(), "current branch commit\n")
        self.assertEqual((self.integration / "existing.txt").read_text(), "existing\n")
        self.assertEqual(git(self.source, "symbolic-ref", "--short", "HEAD"), "user-work")
        self.assertEqual((self.source / "existing.txt").read_text(), "uncommitted user changes\n")
        self.assertEqual(git(self.source, "rev-parse", "main"), self.baseline)
        provisioned = provision_parallel_worktree(self.workspace, "alpha", self.run_id, "B001")
        self.assertTrue(provisioned["success"], provisioned)
        self.assertEqual(git(Path(provisioned["worktreePath"]), "rev-parse", "HEAD"), current_head)

    def test_master_only_repository_needs_no_branch_configuration(self):
        git(self.source, "branch", "-m", "master")
        self.create()
        self.assertEqual(self.manifest()["repositories"]["default"]["baseBranch"], "master")
        self.assertEqual(git(self.integration, "rev-parse", "HEAD"), self.baseline)
        self.assertFalse((self.workspace / ".autobiz/runtime_config.json").exists())
        self.deliver()
        self.validate()
        self.promote(self.prepare(), confirm=True)
        self.assertTrue((self.source / "delivery.txt").exists())
        self.assertEqual(git(self.source, "symbolic-ref", "--short", "HEAD"), "master")

    def test_legacy_target_config_does_not_redirect_current_branch(self):
        config = self.workspace / ".autobiz" / "runtime_config.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"targetBranches": {"default": "release"}}))
        git(self.source, "switch", "-c", "user-work")
        self.create()
        self.assertEqual(self.manifest()["repositories"]["default"]["baseBranch"], "user-work")
        self.deliver()
        self.validate()
        self.promote(self.prepare(), confirm=True)
        self.assertTrue((self.source / "delivery.txt").exists())
        self.assertEqual(git(self.source, "rev-parse", "main"), self.baseline)

    def test_detached_head_cannot_guess_target_or_bootstrap(self):
        git(self.source, "switch", "--detach")
        (self.source / "uncommitted.txt").write_text("user work\n")
        with self.assertRaisesRegex(ValueError, "integration_current_branch_required:default:detached_head"):
            create_run(self.workspace, "alpha", max_parallel=2, timeout_seconds=60,
                       code_workspaces=[str(self.source)], task_card_id="Z990692-294", allow_bootstrap=True)
        self.assertIsNone(get_active_run(self.workspace, "alpha"))
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.baseline)
        self.assertEqual((self.source / "uncommitted.txt").read_text(), "user work\n")

    def test_unborn_current_branch_does_not_create_a_bootstrap_commit(self):
        git(self.source, "checkout", "--orphan", "new-work")
        with self.assertRaisesRegex(ValueError, "integration_current_branch_no_commits:default:new-work"):
            create_run(self.workspace, "alpha", max_parallel=2, timeout_seconds=60,
                       code_workspaces=[str(self.source)], task_card_id="Z990692-294", allow_bootstrap=True)
        self.assertIsNone(get_active_run(self.workspace, "alpha"))
        self.assertEqual(git(self.source, "symbolic-ref", "--short", "HEAD"), "new-work")
        self.assertEqual(git(self.source, "rev-parse", "main"), self.baseline)
        self.assertEqual(git(self.source, "for-each-ref", "--format=%(refname)", "refs/heads/new-work"), "")

    def test_delivery_and_validation_wait_for_explicit_confirmation(self):
        self.create()
        candidate = self.deliver()
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.baseline)
        self.assertFalse((self.source / "delivery.txt").exists())
        self.validate()
        self.assertEqual(self.manifest()["status"], "awaiting_user_confirmation")
        self.assertEqual(get_active_run(self.workspace, "alpha"), self.run_id)
        with self.assertRaisesRegex(ValueError, "unpromoted_cleanup"):
            cleanup_run(self.workspace, "alpha", self.run_id, force=True)
        prepared = prepare_promotion(self.workspace, "alpha", self.run_id)
        promotion = prepared["mainPromotion"]
        repository = promotion["snapshot"]["repositories"]["default"]
        self.assertEqual(repository["gitRoot"], str(self.source.resolve()))
        self.assertEqual(repository["targetWorktreePath"], str(self.source.resolve()))
        self.assertEqual(repository["targetBranch"], "main")
        self.assertEqual(repository["targetSha"], self.baseline)
        self.assertEqual(repository["worktreePath"], str(self.integration))
        self.assertEqual(repository["candidateSha"], candidate["candidateSha"])
        self.assertEqual(promotion["reportPath"], prepared["reportPath"])
        self.assertEqual(json.loads(Path(promotion["reportPath"]).read_text()), promotion)
        approval = promotion["approvalDigest"]
        with self.assertRaisesRegex(ValueError, "explicit_user_confirmation"):
            self.promote(approval)
        self.assertTrue(self.promote(approval, confirm=True)["success"])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), candidate["candidateSha"])
        self.assertEqual((self.source / "delivery.txt").read_text(), "delivery\n")
        self.assertTrue(self.promote(approval, confirm=True)["reused"])

    def test_waiting_and_keep_resume_without_redispatch(self):
        self.create()
        self.deliver()
        self.validate()
        self.prepare()
        kept = keep_integration(self.workspace, "alpha", self.run_id)
        self.assertEqual(kept["status"], "integration_retained")
        resumed = ensure_run(self.workspace, "alpha", max_parallel=2, timeout_seconds=60,
                             code_workspaces=[str(self.source)], task_card_id="Z990692-294")
        self.assertEqual(resumed["runId"], self.run_id)
        self.assertEqual(resumed["scheduledGroups"], [])
        self.assertEqual(resumed["status"], "integration_retained")
        view = analyze_batches("alpha", ROOT, self.workspace, "Z990692-294")
        self.assertEqual(view["requiredAction"], "review_integration_and_confirm_promotion")
        self.assertFalse(view["canStartWorkflow"])
        self.assertTrue(self.integration.exists())
        self.assertEqual(git(self.source, "rev-parse", "main"), self.baseline)

    def test_test_pom_reseal_can_reaggregate_a_blocked_run_after_worktree_cleanup(self):
        pom = "<project><modelVersion>4.0.0</modelVersion><groupId>demo</groupId><artifactId>demo</artifactId><version>1</version></project>"
        dependency = "<dependencies><dependency><groupId>company</groupId><artifactId>test-helper</artifactId><version>1</version><scope>test</scope></dependency></dependencies>"
        (self.source / "pom.xml").write_text(pom)
        git(self.source, "add", "pom.xml")
        git(self.source, "commit", "-m", "fixture Maven baseline")
        self.create()
        self.deliver(utest_changes={"pom.xml": pom.replace("</project>", dependency + "</project>")})
        batch = self.manifest()["batches"]["B001"]
        evidence_before = {state["evidencePath"]: Path(state["evidencePath"]).read_bytes()
                           for state in batch["stageStates"].values()}
        self.assertTrue(cleanup_merged_batches(self.workspace, "alpha", self.run_id)["success"])
        self.assertFalse(Path(batch["worktreePath"]).exists())
        begin_e2e(self.workspace, "alpha", self.run_id)
        finish_e2e(self.workspace, "alpha", self.run_id, passed=False, metadata={"message": "E2E environment unavailable"})
        manifest = self.manifest()
        manifest["status"] = "blocked"
        manifest["finalEvidenceAggregate"] = {"passed": False, "errors": ["B001.prepare_batch_commit_stale", "B001.review_batch_commit_stale"]}
        save_manifest(self.workspace, "alpha", self.run_id, manifest)
        for _ in range(2):
            report = aggregate_evidence(self.workspace, "alpha", self.run_id)
            self.assertTrue(report["passed"], report)
            self.assertEqual(report["e2eStatus"], "failed")
            self.assertEqual(self.manifest()["status"], "awaiting_user_confirmation")
        self.assertTrue(self.prepare())
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.manifest()["repositories"]["default"]["baseSha"])
        for path, contents in evidence_before.items():
            self.assertEqual(Path(path).read_bytes(), contents)

    def test_failed_evidence_audit_is_reported_and_requires_explicit_confirmation(self):
        self.create()
        candidate = self.deliver(stage_commits={"test": self.baseline})
        begin_e2e(self.workspace, "alpha", self.run_id)
        finish_e2e(self.workspace, "alpha", self.run_id, passed=True,
                   metadata={"environment": {"version": "test", "seedDataDigest": "test", "dependencies": {"db": "none"}}})
        manifest = self.manifest()
        batch = manifest["batches"]["B001"]
        # Model historical prepare binding drift without rewriting evidence.
        batch["startSha"] = batch["commitSha"]
        save_manifest(self.workspace, "alpha", self.run_id, manifest)
        command = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "hooks/parallel_evidence_aggregate.py"),
            "--workspace", str(self.workspace), "--feature", "alpha", "--run-id", self.run_id], capture_output=True, text=True)
        self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
        report = json.loads(command.stdout)
        self.assertTrue(report["ok"])
        self.assertTrue(report["success"])
        self.assertFalse(report["passed"])
        self.assertIn("B001.prepare_batch_commit_stale", report["errors"])
        self.assertIn("B001.test_batch_commit_stale", report["errors"])
        self.assertEqual(report["blockingErrors"], [])
        self.assertEqual(self.manifest()["status"], "awaiting_user_confirmation")
        approval = self.prepare()
        snapshot = self.manifest()["mainPromotion"]["snapshot"]
        self.assertEqual(snapshot["validationStatus"], "failed")
        self.assertEqual(snapshot["evidenceErrors"], report["errors"])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.baseline)
        with self.assertRaisesRegex(ValueError, "explicit_user_confirmation"):
            self.promote(approval)
        result = self.promote(approval, confirm=True)
        self.assertEqual(result["status"], "succeeded_with_issues")
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), candidate["candidateSha"])

    def test_old_blocked_audit_can_prepare_without_rerunning_tests(self):
        self.create()
        self.deliver()
        self.validate()
        manifest = self.manifest()
        manifest["status"] = "blocked"
        manifest["batches"]["B001"]["startSha"] = manifest["batches"]["B001"]["commitSha"]
        manifest["finalEvidenceAggregate"] = {"passed": False, "errors": ["B001.prepare_batch_commit_stale"]}
        save_manifest(self.workspace, "alpha", self.run_id, manifest)
        with patch("hooks.parallel_merge_train.begin_e2e", side_effect=AssertionError("must not rerun E2E")):
            approval = self.prepare()
        self.assertTrue(approval)
        self.assertEqual(self.manifest()["mainPromotion"]["snapshot"]["validationStatus"], "failed")

    def test_changed_merge_evidence_requires_a_new_report_and_confirmation(self):
        self.create()
        self.deliver()
        self.validate()
        approval = self.prepare()
        manifest = self.manifest()
        train_id = next(iter(manifest["mergeTrains"]))
        manifest["mergeTrains"][train_id]["validation"] = {"commands": [{"passed": False}]}
        save_manifest(self.workspace, "alpha", self.run_id, manifest)
        with self.assertRaisesRegex(ValueError, "evidence_report_stale"):
            self.promote(approval, confirm=True)
        new_approval = self.prepare()
        self.assertNotEqual(approval, new_approval)
        snapshot = self.manifest()["mainPromotion"]["snapshot"]
        self.assertIn(f"merge_train_validation_failed:{train_id}", snapshot["evidenceErrors"])
        self.assertEqual(snapshot["validationStatus"], "failed")
        self.assertEqual(self.promote(new_approval, confirm=True)["status"], "succeeded_with_issues")

    def test_incomplete_or_unintegrated_deliveries_still_block_promotion(self):
        self.create()
        self.deliver()
        self.validate()
        original = self.manifest()["batches"]["B001"].copy()
        unrelated = git(self.source, "commit-tree", "HEAD^{tree}", "-m", "unrelated fixture commit")
        for change in ({"status": "sealed"}, {"mergeCommitSha": unrelated}, {"commitSha": unrelated}):
            with self.subTest(change=change):
                manifest = self.manifest()
                manifest["batches"]["B001"] = dict(original, **change)
                save_manifest(self.workspace, "alpha", self.run_id, manifest)
                aggregate_evidence(self.workspace, "alpha", self.run_id)
                with self.assertRaisesRegex(ValueError, "deliveries_incomplete|not_waiting|delivery_commit"):
                    self.prepare()
                self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.baseline)

    def test_failed_e2e_continues_to_confirmation_and_requires_explicit_approval(self):
        self.create()
        candidate = self.deliver()
        begin_e2e(self.workspace, "alpha", self.run_id)
        metadata = {"message": "checkout scenario failed", "outputFile": "/logs/e2e.log"}
        failed = finish_e2e(self.workspace, "alpha", self.run_id, passed=False, metadata=metadata)
        self.assertTrue(failed["success"])
        self.assertEqual(failed["e2eStatus"], "failed")
        self.assertFalse(failed["issue"]["blocksWorkflow"])
        self.assertNotIn("repair", failed)
        repeated = finish_e2e(self.workspace, "alpha", self.run_id, passed=False, metadata=metadata)
        self.assertTrue(repeated["reused"])
        self.assertEqual(len(self.manifest()["deferredIssues"]), 1)
        state = self.manifest()["validationBatches"]["V-E2E"]["stageStates"]["e2e_test"]
        evidence = json.loads(Path(state["evidencePath"]).read_text())
        self.assertEqual(evidence["outcome"], "FAIL")
        self.assertEqual(evidence["metadata"]["outputFile"], "/logs/e2e.log")
        report = aggregate_evidence(self.workspace, "alpha", self.run_id)
        self.assertTrue(report["passed"], report)
        self.assertTrue(report["hasDeferredIssues"])
        self.assertFalse(report["hasBlockingDeferredIssues"])
        self.assertEqual(report["e2eStatus"], "failed")
        self.assertEqual(self.manifest()["status"], "awaiting_user_confirmation")
        approval = self.prepare()
        snapshot = self.manifest()["mainPromotion"]["snapshot"]
        self.assertEqual(snapshot["e2eStatus"], "failed")
        self.assertEqual(snapshot["deferredIssues"][0]["message"], metadata["message"])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.baseline)
        with self.assertRaisesRegex(ValueError, "explicit_user_confirmation"):
            self.promote(approval)
        result = self.promote(approval, confirm=True)
        self.assertEqual(result["status"], "succeeded_with_issues")
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), candidate["candidateSha"])

    def test_e2e_startup_failure_can_be_recorded_by_cli_before_a_worktree_exists(self):
        self.create()
        self.deliver()
        metadata = {"message": '环境异常 "quoted" $(echo not-executed)\n完整输出', "executionOutcome": "not_run"}
        encoded = base64.b64encode(json.dumps(metadata, ensure_ascii=False).encode()).decode()
        result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "hooks/parallel_merge_train.py"),
            "finish-e2e", "--workspace", str(self.workspace), "--feature", "alpha", "--run-id", self.run_id,
            "--passed", "false", "--metadata-base64", encoded], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["e2eStatus"], "failed")
        report = aggregate_evidence(self.workspace, "alpha", self.run_id)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["deferredIssues"][0]["message"], metadata["message"])
        self.assertTrue(self.prepare())

    def test_evidence_changes_invalidate_approval_and_are_exposed_in_a_new_report(self):
        self.create()
        self.deliver()
        begin_e2e(self.workspace, "alpha", self.run_id)
        finish_e2e(self.workspace, "alpha", self.run_id, passed=False, metadata={"message": "failed"})
        self.assertTrue(aggregate_evidence(self.workspace, "alpha", self.run_id)["passed"])
        approval = self.prepare()
        state = self.manifest()["validationBatches"]["V-E2E"]["stageStates"]["e2e_test"]
        path = Path(state["evidencePath"])
        original = path.read_bytes()
        for corrupt in (None, b'{"outcome":"PASS"}'):
            if corrupt is None:
                path.unlink()
            else:
                path.write_bytes(corrupt)
            with self.assertRaisesRegex(ValueError, "evidence_report_stale"):
                self.promote(approval, confirm=True)
            report = aggregate_evidence(self.workspace, "alpha", self.run_id)
            self.assertFalse(report["passed"])
            self.assertEqual(self.manifest()["status"], "awaiting_user_confirmation")
            self.prepare()
            self.assertEqual(self.manifest()["mainPromotion"]["snapshot"]["validationStatus"], "failed")
            path.write_bytes(original)
            aggregate_evidence(self.workspace, "alpha", self.run_id)
            approval = self.prepare()

    def test_e2e_failure_does_not_waive_validation_of_a_new_commit(self):
        self.create()
        self.deliver()
        finish_e2e(self.workspace, "alpha", self.run_id, passed=False, metadata={"message": "environment unavailable"})
        report = aggregate_evidence(self.workspace, "alpha", self.run_id)
        self.assertTrue(report["passed"])
        (self.source / "later.txt").write_text("later main change\n")
        git(self.source, "add", "later.txt")
        git(self.source, "commit", "-m", "later change")
        sync_main(self.workspace, "alpha", self.run_id)
        report = aggregate_evidence(self.workspace, "alpha", self.run_id)
        self.assertFalse(report["passed"])
        self.assertTrue(any(error.startswith("V-E2E.") for error in report["errors"]))

    def test_later_e2e_pass_resolves_the_issue_and_preserves_failure_evidence(self):
        self.create()
        self.deliver()
        finish_e2e(self.workspace, "alpha", self.run_id, passed=False, metadata={"message": "startup failed"})
        state = self.manifest()["validationBatches"]["V-E2E"]["stageStates"]["e2e_test"]
        original_path = Path(state["evidencePath"])
        original = original_path.read_bytes()
        report = self.validate()
        self.assertTrue(report["passed"])
        self.assertEqual(report["e2eStatus"], "passed")
        self.assertFalse(report["hasDeferredIssues"])
        self.assertEqual(self.manifest()["deferredIssues"][0]["status"], "resolved")
        self.assertEqual(original_path.read_bytes(), original)

    def test_main_change_invalidates_approval_then_sync_requires_new_e2e(self):
        self.create()
        self.deliver()
        self.validate()
        approval = self.prepare()
        (self.source / "other-feature.txt").write_text("other feature\n")
        git(self.source, "add", ".")
        git(self.source, "commit", "-m", "other feature")
        advanced = git(self.source, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "target_changed"):
            self.promote(approval, confirm=True)
        synced = sync_main(self.workspace, "alpha", self.run_id)
        self.assertEqual(synced["status"], "running")
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), advanced)
        with self.assertRaisesRegex(ValueError, "validation_required|not_waiting"):
            self.prepare()
        self.validate()
        new_approval = self.prepare()
        self.assertNotEqual(approval, new_approval)
        with self.assertRaisesRegex(ValueError, "approval_mismatch"):
            self.promote(approval, confirm=True)
        self.assertTrue(self.promote(new_approval, confirm=True)["success"])
        self.assertTrue((self.source / "delivery.txt").exists())
        self.assertTrue((self.source / "other-feature.txt").exists())

    def test_changed_integration_or_dirty_main_cannot_be_promoted(self):
        self.create()
        self.deliver()
        self.validate()
        approval = self.prepare()
        (self.source / "user.txt").write_text("uncommitted\n")
        with self.assertRaisesRegex(ValueError, "target_worktree_dirty"):
            self.promote(approval, confirm=True)
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.baseline)
        (self.integration / "manual.txt").write_text("manual change\n")
        with self.assertRaisesRegex(ValueError, "integration_worktree_dirty"):
            self.promote(approval, confirm=True)

    def test_git_220_promotion_retries_without_worktree_list_z(self):
        renamed = self.source.with_name("客户 拜访 仓库")
        self.source.rename(renamed)
        self.source = renamed
        self.create()
        candidate = self.deliver()
        self.validate()
        original = subprocess.run
        listed = []

        def old_git(argv, **kwargs):
            if "worktree" in argv and "list" in argv:
                listed.append(list(argv))
                if "-z" in argv:
                    stderr = "error: unknown switch `z'\nusage: git worktree list [--porcelain]\n"
                    if not kwargs.get("text"):
                        stderr = stderr.encode()
                    return subprocess.CompletedProcess(argv, 129, stdout="" if kwargs.get("text") else b"", stderr=stderr)
            return original(argv, **kwargs)

        with patch("subprocess.run", side_effect=old_git):
            approval = self.prepare()
            repository = self.manifest()["mainPromotion"]["snapshot"]["repositories"]["default"]
            self.assertEqual(repository["targetWorktreePath"], str(self.source.resolve()))
            self.assertTrue(self.promote(approval, confirm=True)["success"])
        self.assertTrue(any("-z" in argv for argv in listed))
        self.assertTrue(any("-z" not in argv for argv in listed))
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), candidate["candidateSha"])

    def test_git_220_promotion_still_checks_dirty_target_before_writing_state(self):
        self.create()
        self.deliver()
        self.validate()
        approval = self.prepare()
        (self.source / "user.txt").write_text("user changes\n")
        original = subprocess.run

        def old_git(argv, **kwargs):
            if "worktree" in argv and "list" in argv and "-z" in argv:
                stderr = "error: unknown switch `z'\n"
                return subprocess.CompletedProcess(argv, 129, stdout="" if kwargs.get("text") else b"",
                                                   stderr=stderr if kwargs.get("text") else stderr.encode())
            return original(argv, **kwargs)

        with patch("subprocess.run", side_effect=old_git):
            with self.assertRaisesRegex(ValueError, "target_worktree_dirty"):
                self.promote(approval, confirm=True)
        self.assertEqual(self.manifest()["status"], "promotion_prepared")
        self.assertEqual(self.manifest()["mainPromotion"]["approvalDigest"], approval)
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.baseline)

    def test_sync_conflict_stays_in_integration_and_resolution_is_revalidated(self):
        self.create()
        self.deliver(filename="existing.txt", content="feature change\n")
        self.validate()
        self.prepare()
        (self.source / "existing.txt").write_text("main change\n")
        git(self.source, "add", "existing.txt")
        git(self.source, "commit", "-m", "main change")
        main_sha = git(self.source, "rev-parse", "HEAD")
        result = sync_main(self.workspace, "alpha", self.run_id)
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "integration_sync_conflicted")
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), main_sha)
        from hooks.frontend_route_write_guard import validate_merge_resolution_write
        self.assertEqual(validate_merge_resolution_write(self.workspace, "alpha", self.integration / "existing.txt"), 0)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(validate_merge_resolution_write(self.workspace, "alpha", self.integration / "other.py"), 2)
        (self.integration / "existing.txt").write_text("resolved behavior\n")
        completed = sync_main(self.workspace, "alpha", self.run_id, complete=True)
        self.assertTrue(completed["success"])
        self.assertEqual(completed["status"], "running")
        self.validate()
        self.promote(self.prepare(), confirm=True)
        self.assertEqual((self.source / "existing.txt").read_text(), "resolved behavior\n")

    def test_interrupted_promotion_recovers_after_git_advanced(self):
        self.create()
        candidate = self.deliver()
        self.validate()
        approval = self.prepare()
        import hooks.parallel_integration as module
        real_save = module.save_manifest

        def interrupt_after_git(workspace, feature, run_id, manifest):
            record = manifest.get("mainPromotion", {}).get("repositories", {}).get("default", {})
            if record.get("status") == "merged":
                raise OSError("simulated interrupted JSON write")
            real_save(workspace, feature, run_id, manifest)

        with patch.object(module, "save_manifest", side_effect=interrupt_after_git):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.promote(approval, confirm=True)
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), candidate["candidateSha"])
        self.assertEqual(self.manifest()["status"], "promoting_main")
        self.assertTrue(self.promote(approval, confirm=True)["success"])
        self.assertTrue(self.manifest()["mainPromotion"]["repositories"]["default"]["recovered"])

    def test_original_current_branch_is_final_target_after_source_switch(self):
        git(self.source, "switch", "-c", "feature/development")
        self.create()
        candidate = self.deliver()
        self.validate()
        git(self.source, "switch", "main")
        (self.source / "untracked.txt").write_text("user work\n")
        approval = self.prepare()
        repository = self.manifest()["mainPromotion"]["snapshot"]["repositories"]["default"]
        self.assertEqual(repository["gitRoot"], str(self.source.resolve()))
        self.assertEqual(repository["targetBranch"], "feature/development")
        self.assertIsNone(repository["targetWorktreePath"])
        self.promote(approval, confirm=True)
        self.assertEqual(git(self.source, "rev-parse", "feature/development"), candidate["candidateSha"])
        self.assertEqual(git(self.source, "rev-parse", "main"), self.baseline)
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.baseline)
        self.assertEqual((self.source / "untracked.txt").read_text(), "user work\n")
        cleaned = cleanup_run(self.workspace, "alpha", self.run_id)
        self.assertEqual(cleaned["status"], "cleaned", cleaned)
        self.assertFalse(self.integration.exists())
        self.assertEqual(git(self.source, "symbolic-ref", "--short", "HEAD"), "main")

    def test_promotion_report_locates_baseline_in_another_worktree(self):
        self.create()
        candidate = self.deliver()
        self.validate()
        git(self.source, "switch", "-c", "user-current")
        target = self.source.parent / "基线 checkout"
        git(self.source, "worktree", "add", str(target), "main")
        approval = self.prepare()
        repository = self.manifest()["mainPromotion"]["snapshot"]["repositories"]["default"]
        self.assertEqual(repository["gitRoot"], str(self.source.resolve()))
        self.assertEqual(repository["targetWorktreePath"], str(target.resolve()))
        self.assertEqual(git(target, "rev-parse", "HEAD"), repository["targetSha"])
        self.assertEqual(git(self.source, "symbolic-ref", "--short", "HEAD"), "user-current")
        self.assertFalse((target / "delivery.txt").exists())
        self.promote(approval, confirm=True)
        self.assertEqual(git(target, "rev-parse", "HEAD"), candidate["candidateSha"])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.baseline)

    def test_initialization_interruption_keeps_isolated_mode_and_reuses_run(self):
        git(self.source, "switch", "-c", "original-work")
        with patch("hooks.parallel_batch_scheduler.initialize_integrations", side_effect=OSError("interrupted initialization")):
            with self.assertRaisesRegex(OSError, "interrupted initialization"):
                self.create()
        active = get_active_run(self.workspace, "alpha")
        self.assertIsNotNone(active)
        self.assertEqual(load_manifest(self.workspace, "alpha", active)["integrationMode"], "isolated")
        git(self.source, "switch", "main")
        (self.source / "later.txt").write_text("changed after run creation\n")
        git(self.source, "add", "later.txt")
        git(self.source, "commit", "-m", "advance another branch")
        later_head = git(self.source, "rev-parse", "HEAD")
        resumed = ensure_run(self.workspace, "alpha", max_parallel=2, timeout_seconds=60,
                             code_workspaces=[str(self.source)], task_card_id="Z990692-294")
        self.assertEqual(resumed["runId"], active)
        self.assertTrue(load_manifest(self.workspace, "alpha", active)["integrationReady"])
        binding = load_manifest(self.workspace, "alpha", active)["repositories"]["default"]
        self.assertEqual(binding["baseBranch"], "original-work")
        self.assertEqual(binding["baseSha"], self.baseline)
        self.assertEqual(git(integration_repo(binding), "rev-parse", "HEAD"), self.baseline)
        self.assertEqual(git(self.source, "rev-parse", "main"), later_head)

    def test_batch_cannot_use_the_integration_checkout(self):
        self.create()
        with self.assertRaisesRegex(ValueError, "not_isolated"):
            assert_batch_worktree_isolated(self.manifest(), "B001", self.integration)

    def test_dependent_batch_starts_from_the_integrated_delivery(self):
        _add_second_compile_only_batch(self.feature_dir)
        _enable_pipeline(self.feature_dir)
        self.create()
        candidate = self.deliver()
        next_batch = provision_parallel_worktree(self.workspace, "alpha", self.run_id, "B002")
        self.assertTrue(next_batch["success"], next_batch)
        self.assertEqual(git(Path(next_batch["worktreePath"]), "rev-parse", "HEAD"), candidate["candidateSha"])
        self.assertEqual(self.manifest()["batches"]["B002"]["startSha"], candidate["candidateSha"])
        cleaned = cleanup_merged_batches(self.workspace, "alpha", self.run_id)
        self.assertTrue(cleaned["success"], cleaned)
        self.assertTrue(self.integration.exists())

    def test_in_progress_batch_keeps_its_start_sha_after_another_delivery(self):
        _add_second_compile_only_batch(self.feature_dir)
        root_path = self.feature_dir / "plan.json"
        root = json.loads(root_path.read_text())
        root["batches"][1]["deps"] = []
        root_path.write_text(json.dumps(root))
        second_path = self.feature_dir / "plans/B002/plan.json"
        second = json.loads(second_path.read_text())
        second["tasks"][0]["deps"] = []
        second_path.write_text(json.dumps(second))
        _enable_pipeline(self.feature_dir)
        self.create()
        second = provision_parallel_worktree(self.workspace, "alpha", self.run_id, "B002")
        worktree = Path(second["worktreePath"])
        (worktree / "unfinished.txt").write_text("unfinished work\n")
        self.deliver()
        begin_implementation(self.workspace, "alpha", self.run_id, "B002")
        manifest = self.manifest()
        batch = manifest["batches"]["B002"]
        preparation = json.loads(Path(batch["stageStates"]["prepare"]["evidencePath"]).read_text())
        self.assertEqual(preparation["inputs"]["batchCommit"], batch["startSha"])
        batch["recovery"] = {"kind": "implementation_resume"}
        save_manifest(self.workspace, "alpha", self.run_id, manifest)
        resumed = ensure_run(self.workspace, "alpha", max_parallel=2, timeout_seconds=60,
                             code_workspaces=[str(self.source)], task_card_id="Z990692-294")
        self.assertEqual(resumed["implementationRecoveryBatches"][0]["batchId"], "B002")
        self.assertEqual(git(worktree, "rev-parse", "HEAD"), self.baseline)
        self.assertEqual((worktree / "unfinished.txt").read_text(), "unfinished work\n")

    def test_shared_physical_repository_has_one_integration_and_one_promotion(self):
        _add_second_compile_only_batch(self.feature_dir)
        root_path = self.feature_dir / "plan.json"
        root = json.loads(root_path.read_text())
        root["codeWorkspaces"] = {"default": str(self.source), "web": str(self.source)}
        root["batches"][0]["workspaceRef"] = "default"
        root["batches"][1]["workspaceRef"] = "web"
        root_path.write_text(json.dumps(root))
        second_path = self.feature_dir / "plans/B002/plan.json"
        second = json.loads(second_path.read_text())
        second["tasks"][0]["workspaceRef"] = "web"
        second["workspaceRef"] = "web"
        second_path.write_text(json.dumps(second))
        _enable_pipeline(self.feature_dir)
        result = create_run(self.workspace, "alpha", max_parallel=2, timeout_seconds=60,
                            code_workspaces=[f"default={self.source}", f"web={self.source}"], task_card_id="Z990692-294")
        self.run_id = result["runId"]
        bindings = self.manifest()["repositories"]
        self.integration = integration_repo(bindings["default"])
        self.assertEqual(integration_repo(bindings["web"]), self.integration)
        candidate = self.deliver()
        self.assertEqual(self.manifest()["repositories"]["web"]["headSha"], candidate["candidateSha"])
        self.deliver(filename="web.txt", batch_id="B002", wave=2)
        self.validate()
        approval = self.prepare()
        self.assertEqual(len(self.manifest()["mainPromotion"]["snapshot"]["repositories"]), 1)
        self.promote(approval, confirm=True)
        self.assertTrue((self.source / "web.txt").exists())
        self.assertTrue((self.source / "delivery.txt").exists())
        self.assertEqual(len(self.manifest()["mainPromotion"]["repositories"]), 1)

    def test_parallel_promotions_preserve_both_deliveries_and_rebuild_stale_candidate(self):
        _add_second_compile_only_batch(self.feature_dir)
        root_path = self.feature_dir / "plan.json"
        root = json.loads(root_path.read_text())
        root["batches"][1]["deps"] = []
        root_path.write_text(json.dumps(root))
        second_path = self.feature_dir / "plans/B002/plan.json"
        second = json.loads(second_path.read_text())
        second["tasks"][0]["deps"] = []
        second_path.write_text(json.dumps(second))
        _enable_pipeline(self.feature_dir)
        self.create()
        for wave, batch_id in enumerate(("B001", "B002"), 1):
            provisioned = provision_parallel_worktree(self.workspace, "alpha", self.run_id, batch_id)
            worktree = Path(provisioned["worktreePath"])
            filename = batch_id + ".txt"
            (worktree / filename).write_text(batch_id)
            git(worktree, "add", filename)
            git(worktree, "commit", "-m", batch_id)
            commit = git(worktree, "rev-parse", "HEAD")
            mark_batch(self.workspace, "alpha", self.run_id, batch_id, "sealed", worktreePath=str(worktree),
                       branchName=provisioned["branchName"], commitSha=commit)
            for stage in ("prepare", "implement", "review", "test"):
                start_stage(self.workspace, "alpha", self.run_id, batch_id, stage)
                complete_stage(self.workspace, "alpha", self.run_id, batch_id, stage, metadata={"batchCommit": commit})
            gate_batch(self.workspace, "alpha", self.run_id, batch_id)
            build_candidate(self.workspace, "alpha", self.run_id, wave=wave, batch_ids=[batch_id])

        def promote(wave):
            return promote_candidate(self.workspace, "alpha", self.run_id, wave=wave,
                                     repository_ref="default", allow_unverified=True)

        with patch("hooks.parallel_merge_train.mark_parallel_batch_tasks_merged", return_value=WriterResult(ok=True)):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(promote, (1, 2)))
            self.assertEqual(sum(bool(result.get("success")) for result in results), 1)
            stale_wave = next(index for index, result in enumerate(results, 1) if result.get("stale"))
            build_candidate(self.workspace, "alpha", self.run_id, wave=stale_wave, batch_ids=[f"B{stale_wave:03d}"])
            self.assertTrue(promote(stale_wave)["success"])
        self.assertTrue((self.integration / "B001.txt").exists())
        self.assertTrue((self.integration / "B002.txt").exists())
        self.assertTrue(recover_promoted_plan(self.workspace, "alpha", self.run_id, repository_ref="default", wave=1)["reused"])
        self.assertEqual(git(self.source, "rev-parse", "main"), self.baseline)

    def test_plan_change_after_approval_blocks_promotion(self):
        self.create()
        self.deliver()
        self.validate()
        approval = self.prepare()
        root_path = self.feature_dir / "plan.json"
        root = json.loads(root_path.read_text())
        root["batches"][0]["title"] = "changed contract"
        root_path.write_text(json.dumps(root))
        with self.assertRaisesRegex(ValueError, "plan_changed"):
            self.promote(approval, confirm=True)
        self.assertEqual(git(self.source, "rev-parse", "main"), self.baseline)

    def test_multiple_repositories_recover_partial_main_promotion(self):
        other = self.source.parent / "other repo"
        git(self.source, "clone", str(self.source), str(other))
        git(other, "config", "user.email", "test@example.com")
        git(other, "config", "user.name", "Test")
        git(other, "branch", "-m", "develop")
        _add_second_compile_only_batch(self.feature_dir)
        root_path = self.feature_dir / "plan.json"
        root = json.loads(root_path.read_text())
        root["codeWorkspaces"] = {"default": str(self.source), "web": str(other)}
        root["batches"][0]["workspaceRef"] = "default"
        root["batches"][1]["workspaceRef"] = "web"
        root_path.write_text(json.dumps(root))
        second_path = self.feature_dir / "plans/B002/plan.json"
        second = json.loads(second_path.read_text())
        second["tasks"][0]["workspaceRef"] = "web"
        second["workspaceRef"] = "web"
        second_path.write_text(json.dumps(second))
        _enable_pipeline(self.feature_dir)
        created = create_run(self.workspace, "alpha", max_parallel=2, timeout_seconds=60,
                             code_workspaces=[f"default={self.source}", f"web={other}"], task_card_id="Z990692-294")
        self.run_id = created["runId"]
        self.integration = integration_repo(self.manifest()["repositories"]["default"])
        self.assertEqual(self.manifest()["repositories"]["default"]["baseBranch"], "main")
        self.assertEqual(self.manifest()["repositories"]["web"]["baseBranch"], "develop")
        self.deliver()
        self.deliver(filename="web.txt", batch_id="B002", wave=2)
        self.validate()
        approval = self.prepare()
        import hooks.parallel_integration as module
        real_git = module.git

        def fail_second_target(repo, *args):
            if repo.resolve() == other.resolve() and args[:2] == ("merge", "--ff-only"):
                raise ValueError("simulated second repository failure")
            return real_git(repo, *args)

        with patch.object(module, "git", side_effect=fail_second_target):
            with self.assertRaisesRegex(ValueError, "second repository failure"):
                self.promote(approval, confirm=True)
        self.assertNotEqual(git(self.source, "rev-parse", "main"), self.baseline)
        self.assertEqual(git(other, "rev-parse", "develop"), self.baseline)
        self.assertEqual(self.manifest()["status"], "promoting_main")
        (other / "other-change.txt").write_text("concurrent main change\n")
        git(other, "add", "other-change.txt")
        git(other, "commit", "-m", "concurrent main change")
        with self.assertRaisesRegex(ValueError, "target_changed"):
            self.promote(approval, confirm=True)
        self.assertEqual(sync_main(self.workspace, "alpha", self.run_id)["status"], "running")
        self.validate()
        new_approval = self.prepare()
        self.assertNotEqual(new_approval, approval)
        self.assertEqual(len(self.manifest()["mainPromotion"]["snapshot"]["previousPromotions"]), 1)
        self.promote(new_approval, confirm=True)
        self.assertTrue((self.source / "delivery.txt").exists())
        self.assertTrue((other / "web.txt").exists())
        self.assertTrue((other / "other-change.txt").exists())

    def test_rollback_does_not_revert_main(self):
        self.create()
        self.deliver()
        rollback_run(self.workspace, "alpha", self.run_id, mode="full", confirm=True)
        self.assertEqual(git(self.source, "rev-parse", "main"), self.baseline)
        self.assertTrue((self.integration / "delivery.txt").exists())


ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    unittest.main()
