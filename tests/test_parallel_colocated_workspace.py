"""Real Git coverage when the artifact workspace is the business repository."""
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from hooks import parallel_workspace_sync as sync
from hooks.parallel_cleanup import drain
from hooks.parallel_delivery import prepare_finalization, settle_delivery
from hooks.parallel_evidence_aggregate import aggregate_evidence
from hooks.parallel_integration import git
from hooks.parallel_workspace_queue import operate
from hooks.workflow_launcher import analyze_batches
from tests import test_parallel_delivery as fixtures


class ColocatedWorkspaceTest(unittest.TestCase):
    def deliver_task(self, fixture, batch_id):
        from hooks.parallel_batch_stage import start_stage, complete_stage
        from hooks.parallel_dispatch import claim
        from hooks.parallel_runtime import acquire_lease, release_lease
        from hooks.task_runner import start_task, finish_implementation, prepare_compile, record_compile
        from hooks.worktree_manager import provision_parallel_worktree, seal_parallel_batch

        workspace, run_id = fixture.workspace, fixture.run
        task_id = "T" + batch_id[1:]
        owner = claim(workspace, "alpha", run_id, batch_id, 1, "initial", batch_id)
        tree = Path(provision_parallel_worktree(workspace, "alpha", run_id, batch_id)["worktreePath"])
        lease = acquire_lease(workspace, "alpha", run_id, batch_id, lease_guard=True,
                              dispatch_auth={"generation": 1, "token": owner["ownerToken"]})
        start_stage(workspace, "alpha", run_id, batch_id, "prepare")
        complete_stage(workspace, "alpha", run_id, batch_id, "prepare",
                       metadata={"preparationSource": "task_runner_workspace_binding"})
        task_args = dict(parallel_run_id=run_id, lease_token=lease["ownerToken"], workspace_ref="default")
        task = start_task(workspace, "alpha", task_id, tree, **task_args)
        (tree / f"{batch_id}.txt").write_text(batch_id)
        attempt = prepare_compile(workspace, "alpha", task_id, tree, task["runId"],
                                  argv=["mvn", "compile"], execution_cwd=tree, **task_args)
        output = workspace.parent / f"{task_id}-simulated-compile.log"
        output.write_text("Simulated compiler output for workspace binding regression\n")
        record_compile(workspace, "alpha", task_id, tree, task["runId"], attempt_id=attempt["attemptId"],
                       exit_code=0, log_exit_code=0, output_file=output, **task_args)
        success, finished = finish_implementation(workspace, "alpha", task_id, tree, task["runId"],
                                                   no_code_change_why=None, supporting_files=[], **task_args)
        self.assertTrue(success, finished)
        sealed = seal_parallel_batch(workspace, "alpha", run_id, batch_id, tree, lease["ownerToken"],
                                     purpose="review", commit_stage="code", commit_summary="验证同目录交付")
        self.assertTrue(sealed["success"], sealed)
        release_lease(workspace, "alpha", run_id, batch_id, lease["ownerToken"], final_status="sealed")
        complete_stage(workspace, "alpha", run_id, batch_id, "implement", metadata={"batchCommit": sealed["commitSha"]})
        start_stage(workspace, "alpha", run_id, batch_id, "review")
        complete_stage(workspace, "alpha", run_id, batch_id, "review", metadata={"batchCommit": sealed["commitSha"]})
        return settle_delivery(workspace, "alpha", run_id, batch_id, 1, owner["ownerToken"], sealed["commitSha"])

    def exercise(self, *, tracked_artifacts=False, legacy_ignore_rules=False):
        fixture = fixtures.DeliveryTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        repo = fixture.repo
        shutil.move(str(fixture.workspace / ".autobizdevops"), repo / ".autobizdevops")
        fixture.workspace = repo
        fixture.feature = repo / ".autobizdevops/features/alpha"
        runtime_view = repo / ".autobizdevops/STATE.md"
        runtime_view.write_text("initial workflow progress\n")
        plan_path = fixture.feature / "plan.json"
        plan = json.loads(plan_path.read_text())
        plan["codeWorkspaces"] = {"default": str(repo)}
        plan_path.write_text(json.dumps(plan))
        if tracked_artifacts:
            git(repo, "add", "-f", "--", ".autobizdevops")
            git(repo, "commit", "-m", "legacy artifact baseline")
        head = git(repo, "rev-parse", "HEAD")
        plugin = Path(__file__).resolve().parents[1]
        from hooks.rollback_stage import capture_code_session_baseline
        baseline = capture_code_session_baseline(workspace=repo, feature="alpha", code_workspaces=[repo])
        files = next(iter(baseline["repositories"].values()))["files"]
        self.assertIn("existing.txt", files)
        self.assertFalse(any(name.startswith(".autobizdevops/") for name in files))
        fixture.create(2)
        for batch_plan in (fixture.feature / "plans").glob("*/plan.json"):
            batch = json.loads(batch_plan.read_text())
            for task in batch["tasks"]:
                task["status"] = "todo"
            batch_plan.write_text(json.dumps(batch))
        launch = analyze_batches("alpha", plugin_path=plugin, workspace=repo,
                                 workflow_workspace=repo, task_card_id="TEST-001")
        self.assertTrue(launch["canStartWorkflow"], launch)
        self.assertEqual(launch["artifactWorkspace"], str(repo.resolve()))
        self.assertEqual(launch["workflowWorkspaceRoot"], str(repo.resolve()))
        self.assertEqual(sync._git(repo, "check-ignore", "--no-index", "--quiet", "--",
                                   ".autobizdevops/STATE.md", check=False).returncode, 0)
        if legacy_ignore_rules:
            # An already running version may only have the old partial rules.
            exclude = Path(git(repo, "rev-parse", "--git-path", "info/exclude"))
            if not exclude.is_absolute():
                exclude = repo / exclude
            exclude.write_text("\n".join(line for line in exclude.read_text().splitlines()
                                         if line != ".autobizdevops/") + "\n")
        (repo / "existing.txt").write_text("user staged business edit\n")
        git(repo, "add", "--", "existing.txt")
        (repo / "existing.txt").write_text("user unstaged business edit\n")
        (repo / "local.txt").write_text("user untracked business edit\n")
        original_index = git(repo, "ls-files", "--stage")
        command = sync._git
        updates = []

        def changing_artifact_view(root, *args, **kwargs):
            result = command(root, *args, **kwargs)
            if args[:2] == ("add", "-A") and "GIT_INDEX_FILE" in (kwargs.get("env") or {}):
                updates.append(True)
                runtime_view.write_text(f"concurrent workflow progress {len(updates)}\n")
            return result

        with patch.object(sync, "_git", side_effect=changing_artifact_view):
            for batch_id in ("B001", "B002"):
                self.assertEqual(self.deliver_task(fixture, batch_id)["status"], "merged")
                result = operate(repo, "alpha", fixture.run, "default", "resolver")
                self.assertTrue(result["success"], result)
        self.assertTrue(updates)
        self.assertEqual(result["status"], "synced", result)
        self.assertFalse(result["hasPending"])
        self.assertFalse(prepare_finalization(repo, "alpha", fixture.run)["repairRequired"])
        self.assertFalse(drain(repo, "alpha", fixture.run)["cleanupPending"])
        report = aggregate_evidence(repo, "alpha", fixture.run, finish_workflow=True)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["status"], "succeeded", report)
        from hooks.evidence_integrity_gate import check_code_done
        self.assertEqual(check_code_done(fixture.feature), [])
        launch = analyze_batches("alpha", plugin_path=plugin, workspace=repo,
                                 workflow_workspace=repo, task_card_id="TEST-001")
        self.assertEqual(launch["requiredAction"], "code_done_ready", launch)
        self.assertEqual(git(repo, "rev-parse", "HEAD"), head)
        self.assertEqual(git(repo, "ls-files", "--stage"), original_index)
        self.assertEqual((repo / "existing.txt").read_text(), "user unstaged business edit\n")
        self.assertEqual((repo / "local.txt").read_text(), "user untracked business edit\n")
        self.assertTrue(runtime_view.read_text().startswith("concurrent workflow progress"))
        for batch_id in ("B001", "B002"):
            self.assertEqual((repo / f"{batch_id}.txt").read_text(), batch_id)
        for journal_path in (fixture.feature / ".parallel-runs" / fixture.run / "workspace-sync").glob("*/*/journal.json"):
            journal = json.loads(journal_path.read_text())
            self.assertFalse(any(name.startswith(".autobizdevops/") for name in journal["originals"]))

    def test_artifact_and_code_workspaces_can_share_the_repository_root(self):
        self.exercise()

    def test_legacy_tracked_artifacts_and_partial_ignore_rules_can_finish(self):
        self.exercise(tracked_artifacts=True, legacy_ignore_rules=True)


if __name__ == "__main__":
    unittest.main()
