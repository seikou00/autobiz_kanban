from __future__ import annotations

import json
import copy
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from hooks.batch_merger import _merge_probe, merge_run, preflight_merge, recover_plan_state_after_merge, resolve_merge_conflict
from hooks.parallel_batch_lifecycle import cleanup_run, rollback_run
from hooks.parallel_final_verify import verify_final
from hooks.parallel_runtime import (
    acquire_lease,
    check_lease,
    load_manifest,
    plan_digest,
    reclaim_lease,
    ready_batches,
    release_lease,
    resource_groups,
    run_lock,
    save_manifest,
)
from hooks.repository_snapshot import current_git_branch, git_status_porcelain
from hooks.json_writer_common import WriterResult
from hooks.parallel_batch_scheduler import (
    assert_batch_worktree_isolated,
    create_run as _create_run,
    ensure_run,
    mark_batch,
    resume_run,
    schedule,
    validate_plan_for_parallel,
)
from hooks.plan_json import PlanBundle, load_plan_bundle
from hooks.plan_json import task_set_digest
from hooks.worktree_manager import provision_parallel_worktree, remove_parallel_worktree, seal_parallel_batch
from tests.test_task_runner import (
    _add_second_compile_only_batch,
    _configure_defer_to_test_stages,
    _configure_runtime_ignore,
    _git as task_runner_git,
    _workspace,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def create_run(workspace: Path, feature: str, **kwargs):
    """Create a fixed-workflow scheduler run for runtime tests."""
    return _create_run(workspace, feature, **kwargs)


def _create_native_worktree(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    repo_path: Path | None,
    owner_token: str,
) -> dict[str, Any]:
    """Simulate a plugin-provisioned native Git worktree.

    Production uses ``worktree_manager.py provision``. Tests create the same
    Git-registered checkout outside the source repository and record it through
    the scheduler boundary.
    """
    manifest = load_manifest(workspace, feature, run_id)
    batch = manifest["batches"][batch_id]
    repository_ref = batch["repositoryRef"]
    git_root = Path(manifest["repositories"][repository_ref]["gitRoot"])
    if repo_path is not None and repo_path.resolve() != git_root.resolve():
        return {"success": False, "error": f"parallel_repository_binding_mismatch:{repository_ref}"}
    target = workspace.parent / "native-worktrees" / run_id / batch_id
    target.parent.mkdir(parents=True, exist_ok=True)
    branch = f"cmbcowork/{run_id.lower()}/{batch_id.lower()}"
    base_sha = manifest["repositories"][repository_ref]["headSha"]
    created = subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(target), base_sha],
        cwd=git_root,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        return {"success": False, "error": f"git_worktree_add_failed:{created.stderr.strip()}"}
    mark_batch(
        workspace,
        feature,
        run_id,
        batch_id,
        "running",
        worktreePath=str(target),
        branchName=branch,
    )
    return {"success": True, "worktreePath": str(target.resolve()), "branchName": branch, "error": None}


def _seal_native_worktree(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    repo_path: Path | None,
    owner_token: str,
) -> dict[str, Any]:
    """Seal a plugin-provisioned native delivery worktree."""
    return seal_parallel_batch(workspace, feature, run_id, batch_id, repo_path, owner_token)


class ParallelBatchRuntimeTest(unittest.TestCase):
    def test_current_branch_uses_legacy_git_compatible_plumbing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)

            self.assertEqual(
                current_git_branch(repo),
                _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD"),
            )

    def test_ensure_reuses_existing_scheduler_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            reused = ensure_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            self.assertTrue(reused["reused"])
            self.assertEqual(reused["runId"], created["runId"])
            self.assertEqual(reused["scheduledGroups"], [["B001"]])

    def test_ensure_preserves_needs_resolution_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            mark_batch(workspace, "alpha", created["runId"], "B001", "needs_resolution")

            ensured = ensure_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            self.assertEqual(ensured["runId"], created["runId"])
            self.assertEqual(ensured["status"], "needs_resolution")
            self.assertTrue(ensured["recoveryRequired"])
            self.assertEqual(ensured["scheduledGroups"], [])

    def test_ensure_blocks_merged_batch_without_a_merge_commit(self) -> None:
        """A legacy/corrupt merged flag must never release dependent work."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            manifest = load_manifest(workspace, "alpha", created["runId"])
            manifest["batches"]["B001"].update({"status": "merged", "mergeCommitSha": None})
            save_manifest(workspace, "alpha", created["runId"], manifest)

            ensured = ensure_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            self.assertTrue(ensured["reused"])
            self.assertEqual(ensured["runId"], created["runId"])
            self.assertEqual(ensured["status"], "blocked")
            self.assertEqual(ensured["scheduledGroups"], [])
            self.assertIn("parallel_batch_merge_evidence_required:B001", ensured["errors"])
            self.assertEqual(load_manifest(workspace, "alpha", created["runId"])["batches"]["B001"]["status"], "blocked")

    def test_ensure_blocks_shared_source_head_drift_without_a_second_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            (repo / "external.txt").write_text("outside run\n", encoding="utf-8")
            task_runner_git(repo, "add", "external.txt")
            task_runner_git(repo, "commit", "-m", "external source change")

            ensured = ensure_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            self.assertTrue(ensured["reused"])
            self.assertEqual(ensured["runId"], created["runId"])
            self.assertEqual(ensured["status"], "blocked")
            self.assertTrue(any(error.startswith("parallel_repository_head_changed:default:") for error in ensured["errors"]))

    def test_worker_facing_mark_batch_cannot_mark_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            with self.assertRaisesRegex(ValueError, "parallel_batch_merge_owner_required:B001"):
                mark_batch(workspace, "alpha", created["runId"], "B001", "merged")

    def test_resume_preserves_conflicting_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            manifest = load_manifest(workspace, "alpha", created["runId"])
            manifest["batches"]["B001"].update({"status": "conflict", "error": "merge_conflict"})
            save_manifest(workspace, "alpha", created["runId"], manifest)

            resumed = resume_run(workspace, "alpha", created["runId"])

            self.assertEqual(resumed["status"], "needs_resolution")
            self.assertTrue(resumed["recoveryRequired"])
            self.assertEqual(resumed["scheduledGroups"], [])

    def test_final_verify_rejects_merged_batch_without_merge_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            manifest = load_manifest(workspace, "alpha", created["runId"])
            manifest["batches"]["B001"].update({"status": "merged", "mergeCommitSha": None})
            manifest["status"] = "succeeded"
            save_manifest(workspace, "alpha", created["runId"], manifest)

            with self.assertRaisesRegex(ValueError, "parallel_final_verify_merge_commit_missing:B001"):
                verify_final(workspace, "alpha", created["runId"])

    def test_resume_blocks_when_sealed_native_delivery_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = scheduled["runId"]
            lease = acquire_lease(workspace, "alpha", run_id, "B001")
            delivery = _create_native_worktree(
                workspace,
                "alpha",
                run_id,
                "B001",
                repo,
                lease["ownerToken"],
            )
            self.assertTrue(delivery["success"], delivery)
            tree = Path(delivery["worktreePath"])
            (tree / "delivery.txt").write_text("sealed\n", encoding="utf-8")
            runtime_file = tree / ".cmbdevclaw" / "workflows" / "batch.journal"
            runtime_file.parent.mkdir(parents=True)
            runtime_file.write_text("platform runtime\n", encoding="utf-8")
            mark_batch(workspace, "alpha", run_id, "B001", "ready_to_merge", compileStatus="passed")
            sealed = _seal_native_worktree(workspace, "alpha", run_id, "B001", tree, lease["ownerToken"])
            self.assertTrue(sealed["success"], sealed)
            committed_files = _git(tree, "show", "--format=", "--name-only", sealed["commitSha"]).splitlines()
            self.assertIn("delivery.txt", committed_files)
            self.assertNotIn(".cmbdevclaw/workflows/batch.journal", committed_files)
            release_lease(workspace, "alpha", run_id, "B001", lease["ownerToken"], final_status="ready_to_merge")
            subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=repo, check=True)

            resumed = resume_run(workspace, "alpha", run_id)

            self.assertEqual(resumed["status"], "blocked")
            self.assertTrue(resumed["recoveryRequired"])
            self.assertIn("native_worktree_delivery_missing:B001:worktree", resumed["errors"])

    def test_unsealed_batch_cannot_be_released_or_report_an_empty_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _feature_dir, repo = _workspace(Path(tmp))
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = scheduled["runId"]
            lease = acquire_lease(workspace, "alpha", run_id, "B001")
            delivery = _create_native_worktree(
                workspace,
                "alpha",
                run_id,
                "B001",
                None,
                lease["ownerToken"],
            )
            self.assertTrue(delivery["success"], delivery)
            mark_batch(workspace, "alpha", run_id, "B001", "ready_to_merge", compileStatus="passed")

            with self.assertRaisesRegex(ValueError, "parallel_batch_not_sealed:B001"):
                release_lease(
                    workspace,
                    "alpha",
                    run_id,
                    "B001",
                    lease["ownerToken"],
                    final_status="ready_to_merge",
                )
            self.assertTrue(check_lease(workspace, "alpha", run_id, "B001", lease["ownerToken"]))

            resumed = resume_run(workspace, "alpha", run_id)
            self.assertEqual(resumed["status"], "blocked")
            self.assertIn("parallel_batch_seal_required:B001", resumed["errors"])

            merged = merge_run(workspace, "alpha", run_id)
            self.assertFalse(merged["success"])
            self.assertEqual(merged["failed"][0]["error"], "parallel_merge_frontier_empty")
            self.assertEqual(merged["failed"][0]["pendingBatches"], ["B001"])

    def test_platform_workflow_runtime_files_do_not_dirty_merge_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _feature_dir, repo = _workspace(Path(tmp))
            runtime_file = repo / ".cmbdevclaw" / "workflows" / "run.journal"
            runtime_file.parent.mkdir(parents=True)
            runtime_file.write_text("initial\n", encoding="utf-8")
            task_runner_git(repo, "add", ".cmbdevclaw/workflows/run.journal")
            task_runner_git(repo, "commit", "-m", "track runtime fixture")
            runtime_file.write_text("changed by platform\n", encoding="utf-8")

            self.assertTrue(preflight_merge(repo)["ok"])
            (repo / "business.txt").write_text("must block\n", encoding="utf-8")
            preflight = preflight_merge(repo)
            self.assertFalse(preflight["ok"])
            self.assertEqual(preflight["error"], "main_worktree_dirty")
            self.assertEqual(len(preflight["changes"]), 1)

    def test_partial_rollback_run_removes_native_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=repo,
            )
            lease = acquire_lease(workspace, "alpha", scheduled["runId"], "B001")
            delivery = _create_native_worktree(
                workspace,
                "alpha",
                scheduled["runId"],
                "B001",
                repo,
                lease["ownerToken"],
            )
            self.assertTrue(delivery["success"], delivery)
            delivery_path = Path(delivery["worktreePath"])

            rollback = rollback_run(workspace, "alpha", scheduled["runId"], mode="partial")
            cleanup = cleanup_run(workspace, "alpha", scheduled["runId"])

            self.assertEqual(rollback["status"], "rolled_back")
            self.assertEqual(cleanup["status"], "cleaned")
            self.assertNotIn(str(delivery_path), cleanup["retainedWorktrees"])
            self.assertTrue(any(Path(path).resolve() == delivery_path.resolve() for path in cleanup["removedWorktrees"]))
            self.assertFalse(delivery_path.exists())

    def test_scheduler_does_not_bind_a_run_to_the_workflow_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            neutral = root / "neutral-artifact-directory"
            neutral.mkdir()
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=neutral,
            )
            manifest = load_manifest(workspace, "alpha", scheduled["runId"])
            self.assertEqual(manifest["isolation"]["mode"], "native_git_worktrees")

    def test_scheduler_rejects_source_checkout_as_batch_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=repo,
            )

            manifest = load_manifest(workspace, "alpha", scheduled["runId"])
            with self.assertRaisesRegex(ValueError, "parallel_batch_worktree_not_isolated"):
                assert_batch_worktree_isolated(manifest, "B001", repo)
            with self.assertRaisesRegex(ValueError, "parallel_batch_worktree_not_isolated"):
                mark_batch(
                    workspace,
                    "alpha",
                    scheduled["runId"],
                    "B001",
                    "running",
                    worktreePath=str(repo),
                    branchName="main",
                )

            self.assertEqual(load_manifest(workspace, "alpha", scheduled["runId"])["batches"]["B001"]["status"], "pending")

    def test_scheduler_defers_worktree_provisioning_to_plugin_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=repo,
            )
            run_id = scheduled["runId"]
            provisioned = scheduled["batchWorkspaces"]["B001"]
            self.assertIsNone(provisioned["worktreePath"])
            self.assertIsNone(provisioned["branchName"])
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(manifest["batches"]["B001"]["status"], "pending")
            self.assertIsNone(manifest["batches"]["B001"]["lease"])

            lease = acquire_lease(workspace, "alpha", run_id, "B001")
            delivery = _create_native_worktree(workspace, "alpha", run_id, "B001", repo, lease["ownerToken"])
            self.assertTrue(delivery["success"], delivery)
            tree = Path(delivery["worktreePath"])
            try:
                manifest = load_manifest(workspace, "alpha", run_id)
                assert_batch_worktree_isolated(manifest, "B001", tree)
                self.assertEqual(load_manifest(workspace, "alpha", run_id)["batches"]["B001"]["status"], "running")
            finally:
                subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=repo, check=True)

    def test_plugin_worktree_manager_provisions_reuses_and_removes_native_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=root / "artifact-host",
            )
            run_id = scheduled["runId"]

            first = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            self.assertTrue(first["success"], first)
            worktree = Path(first["worktreePath"])
            self.assertTrue(worktree.is_dir())
            self.assertEqual(_git(worktree, "rev-parse", "--show-toplevel"), str(worktree))
            self.assertEqual(first["repositoryRef"], "default")
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(manifest["batches"]["B001"]["worktreeOwner"], "plugin")

            reused = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            self.assertTrue(reused["success"], reused)
            self.assertTrue(reused["reused"])
            self.assertEqual(reused["worktreePath"], first["worktreePath"])

            removed = remove_parallel_worktree(workspace, "alpha", run_id, "B001")
            self.assertTrue(removed["success"], removed)
            self.assertTrue(removed["removed"])
            self.assertFalse(worktree.exists())
            self.assertIsNotNone(load_manifest(workspace, "alpha", run_id)["batches"]["B001"].get("worktreeRemovedAt"))

    def test_native_rebase_mode_auto_merges_parallel_deliveries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            b1_path = feature_dir / "plans" / "B001" / "plan.json"
            b2_path = feature_dir / "plans" / "B002" / "plan.json"
            b1 = json.loads(b1_path.read_text(encoding="utf-8"))
            b2 = json.loads(b2_path.read_text(encoding="utf-8"))
            b2["tasks"][0]["deps"] = []
            b2_path.write_text(json.dumps(b2), encoding="utf-8")
            root_path = feature_dir / "plan.json"
            plan = json.loads(root_path.read_text(encoding="utf-8"))
            plan["batches"][1]["deps"] = []
            plan["taskSetDigest"] = task_set_digest(plan, {"B001": b1, "B002": b2})
            root_path.write_text(json.dumps(plan), encoding="utf-8")

            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=repo,
            )
            run_id = scheduled["runId"]
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(manifest["batches"]["B001"]["taskIds"], ["T001"])
            self.assertEqual(manifest["batches"]["B002"]["taskIds"], ["T002"])
            scheduled_again = schedule(workspace, "alpha", run_id)
            self.assertEqual(scheduled_again["batchTaskIds"]["B001"], ["T001"])
            self.assertEqual(scheduled_again["batchTaskIds"]["B002"], ["T002"])
            self.assertTrue(all(
                "canParallelInSameLane" not in item and "canParallelInSameRepository" not in item
                for item in manifest["batches"].values()
            ))
            base_sha = _git(repo, "rev-parse", "HEAD")
            deliveries = []
            for batch_id, filename in (("B001", "first.txt"), ("B002", "second.txt")):
                worktree = workspace.parent / "native-worktrees" / run_id / f"native-{batch_id}"
                worktree.parent.mkdir(parents=True, exist_ok=True)
                branch = f"cmb/workflow-{batch_id.lower()}"
                task_runner_git(repo, "worktree", "add", "-b", branch, str(worktree), base_sha)
                (worktree / filename).write_text(f"{batch_id}\n", encoding="utf-8")
                task_runner_git(worktree, "add", filename)
                task_runner_git(worktree, "commit", "-m", f"implement {batch_id}")
                commit_sha = _git(worktree, "rev-parse", "HEAD")
                mark_batch(
                    workspace,
                    "alpha",
                    run_id,
                    batch_id,
                    "ready_to_merge",
                    worktreePath=str(worktree),
                    branchName=branch,
                    commitSha=commit_sha,
                    compileStatus="passed",
                )
                deliveries.append(worktree)

            merged = merge_run(
                workspace,
                "alpha",
                run_id,
                conflict_mode="native-rebase",
            )

            self.assertTrue(merged["success"], merged)
            self.assertEqual((repo / "first.txt").read_text(encoding="utf-8"), "B001\n")
            self.assertEqual((repo / "second.txt").read_text(encoding="utf-8"), "B002\n")
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(manifest["status"], "verifying")
            self.assertEqual(manifest["isolation"]["mode"], "native_git_worktrees")

            for worktree in deliveries:
                task_runner_git(repo, "worktree", "remove", str(worktree))

    def test_scheduler_rejects_dirty_repository_without_explicit_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            (repo / "existing.txt").write_text("changed before Code\n", encoding="utf-8")
            (repo / "uncommitted.txt").write_text("preserve this baseline\n", encoding="utf-8")
            runtime = repo / ".cmbdevclaw" / "setup-state.json"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("platform runtime\n", encoding="utf-8")
            task_runner_git(repo, "add", str(runtime.relative_to(repo)))

            before_head = _git(repo, "rev-parse", "HEAD")
            with self.assertRaisesRegex(ValueError, "parallel_code_workspace_bootstrap_required:dirty_worktree"):
                create_run(
                    workspace,
                    "alpha",
                    max_parallel=4,
                    timeout_seconds=60,
                    code_workspaces=[str(repo)],
                )

            self.assertEqual(_git(repo, "rev-parse", "HEAD"), before_head)
            self.assertIn("existing.txt", _git(repo, "status", "--porcelain"))
            self.assertFalse((feature_dir / ".parallel-runs").exists())

    def test_scheduler_bootstraps_dirty_repository_only_when_explicitly_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            (repo / "existing.txt").write_text("changed before Code\n", encoding="utf-8")
            (repo / "uncommitted.txt").write_text("preserve this baseline\n", encoding="utf-8")
            runtime = repo / ".cmbdevclaw" / "setup-state.json"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("platform runtime\n", encoding="utf-8")
            task_runner_git(repo, "add", str(runtime.relative_to(repo)))

            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                allow_bootstrap=True,
            )

            self.assertEqual(git_status_porcelain(repo).stdout, "")
            self.assertIn("?? .cmbdevclaw/", _git(repo, "status", "--porcelain"))
            self.assertEqual(_git(repo, "show", "-s", "--format=%s", "HEAD"), "autodev: bootstrap alpha baseline")
            self.assertNotIn(".cmbdevclaw/setup-state.json", _git(repo, "show", "--format=", "--name-only", "HEAD"))
            manifest = load_manifest(workspace, "alpha", scheduled["runId"])
            bootstrap = manifest["repositories"]["default"]["bootstrap"]
            self.assertTrue(bootstrap["performed"])
            self.assertFalse(bootstrap["initialized"])
            self.assertEqual(bootstrap["reason"], "dirty_worktree")
            self.assertIn(
                ".autobizdevops/features/*/.parallel-runs/",
                manifest["repositories"]["default"]["runtimeIgnoreAdditions"],
            )
            self.assertIn(
                ".autobizdevops/features/*/.parallel-runs/",
                (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8"),
            )

            lease = acquire_lease(workspace, "alpha", scheduled["runId"], "B001")
            worktree = _create_native_worktree(
                workspace,
                "alpha",
                scheduled["runId"],
                "B001",
                None,
                lease["ownerToken"],
            )
            self.assertTrue(worktree["success"], worktree)
            self.assertEqual(
                (Path(worktree["worktreePath"]) / "uncommitted.txt").read_text(encoding="utf-8"),
                "preserve this baseline\n",
            )

    def test_scheduler_ignores_platform_runtime_when_source_is_otherwise_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            runtime = repo / ".cmbdevclaw" / "workflows" / "thread" / "journal"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("platform runtime\n", encoding="utf-8")

            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            manifest = load_manifest(workspace, "alpha", scheduled["runId"])
            additions = manifest["repositories"]["default"]["runtimeIgnoreAdditions"]
            self.assertNotIn(".cmbdevclaw/workflows/", additions)
            self.assertEqual(manifest["repositories"]["default"]["bootstrap"]["reason"], None)

    def test_scheduler_initializes_unborn_repository_and_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, _committed_repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            repo = root / "uninitialized-code"
            repo.mkdir()
            (repo / "src.txt").write_text("initial source\n", encoding="utf-8")

            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                allow_bootstrap=True,
            )

            self.assertTrue((repo / ".git").is_dir())
            self.assertTrue(_git(repo, "rev-parse", "--verify", "HEAD"))
            self.assertEqual(_git(repo, "status", "--porcelain"), "")
            manifest = load_manifest(workspace, "alpha", scheduled["runId"])
            bootstrap = manifest["repositories"]["default"]["bootstrap"]
            self.assertTrue(bootstrap["performed"])
            self.assertTrue(bootstrap["initialized"])
            self.assertEqual(bootstrap["reason"], "unborn_head")

            lease = acquire_lease(workspace, "alpha", scheduled["runId"], "B001")
            worktree = _create_native_worktree(
                workspace,
                "alpha",
                scheduled["runId"],
                "B001",
                None,
                lease["ownerToken"],
            )
            self.assertTrue(worktree["success"], worktree)
            self.assertEqual(
                (Path(worktree["worktreePath"]) / "src.txt").read_text(encoding="utf-8"),
                "initial source\n",
            )

    def test_plan_digest_ignores_execution_updates_but_detects_contract_drift(self) -> None:
        root = {"status": "todo", "taskSetDigest": "before", "batches": [{"id": "B001", "status": "todo", "completedTaskCount": 0}]}
        batches = {"B001": {"batchCompile": {"status": "pending"}, "tasks": [{"id": "T001", "status": "todo", "goal": "original"}]}}
        bundle = PlanBundle(root=root, batches=batches, tasks=batches["B001"]["tasks"], task_batches={"T001": "B001"})
        digest = plan_digest(bundle)
        updated = copy.deepcopy(bundle)
        updated.root.update({"status": "in_progress", "taskSetDigest": "after"})
        updated.root["batches"][0].update({"status": "done", "completedTaskCount": 1})
        updated.batches["B001"]["batchCompile"]["status"] = "passed"
        updated.batches["B001"]["tasks"][0]["status"] = "done"
        self.assertEqual(digest, plan_digest(updated))
        updated.batches["B001"]["tasks"][0]["goal"] = "changed"
        self.assertNotEqual(digest, plan_digest(updated))

    def test_resource_groups_isolate_overlapping_write_sets(self) -> None:
        manifest = {
            "batches": {
                "B001": {"status": "pending", "workspaceRef": "api", "executionLane": "backend", "writeSet": ["a.py"], "dependencies": []},
                "B002": {"status": "pending", "workspaceRef": "api", "executionLane": "backend", "writeSet": ["a.py"], "dependencies": []},
                "B003": {"status": "pending", "workspaceRef": "worker", "executionLane": "backend", "writeSet": ["c.py"], "dependencies": []},
                "B004": {"status": "pending", "workspaceRef": "cli", "executionLane": "backend", "writeSet": ["d.py"], "dependencies": []},
            }
        }
        groups = resource_groups(manifest, ["B001", "B002", "B003", "B004"])
        self.assertEqual(groups, [["B001", "B003", "B004"], ["B002"]])

    def test_resource_groups_treats_repository_root_write_set_as_conflicting(self) -> None:
        manifest = {
            "batches": {
                "B001": {"status": "pending", "repositoryRef": "api", "writeSet": ["."], "dependencies": []},
                "B002": {"status": "pending", "repositoryRef": "api", "writeSet": ["src/api.py"], "dependencies": []},
            }
        }
        self.assertEqual(resource_groups(manifest, ["B001", "B002"]), [["B001"], ["B002"]])

    def test_merge_probe_does_not_write_tree_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            task_runner_git(repo, "init", "-b", "main")
            task_runner_git(repo, "config", "user.email", "test@example.com")
            task_runner_git(repo, "config", "user.name", "Test")
            (repo / "shared.txt").write_text("base\n", encoding="utf-8")
            task_runner_git(repo, "add", "shared.txt")
            task_runner_git(repo, "commit", "-m", "base")
            task_runner_git(repo, "checkout", "-b", "feature")
            (repo / "shared.txt").write_text("feature\n", encoding="utf-8")
            task_runner_git(repo, "commit", "-am", "feature")
            source = _git(repo, "rev-parse", "HEAD")
            task_runner_git(repo, "checkout", "main")
            (repo / "shared.txt").write_text("main\n", encoding="utf-8")
            task_runner_git(repo, "commit", "-am", "main")
            target = _git(repo, "rev-parse", "HEAD")
            before = set(_git(repo, "count-objects", "-v").splitlines())
            probe = _merge_probe(repo, target, "feature")
            after = set(_git(repo, "count-objects", "-v").splitlines())
            self.assertFalse(probe["success"])
            self.assertTrue(probe["conflicts"])
            self.assertEqual(before, after)
            self.assertNotEqual(source, target)

    def test_plan_state_recovery_marks_git_delivered_batch_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, _feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(workspace / ".autobizdevops" / "features" / "alpha")
            scheduled = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = scheduled["runId"]
            (repo / "merged.txt").write_text("merged\n", encoding="utf-8")
            task_runner_git(repo, "add", "merged.txt")
            task_runner_git(repo, "commit", "-m", "merged delivery")
            commit_sha = _git(repo, "rev-parse", "HEAD")
            manifest = load_manifest(workspace, "alpha", run_id)
            manifest["batches"]["B001"].update(
                {
                    "status": "needs_resolution",
                    "mergeCommitSha": commit_sha,
                    "resolution": {
                        "kind": "plan_state_update",
                        "mergeCommitSha": commit_sha,
                        "deliveryRunId": run_id,
                    },
                }
            )
            manifest["repositories"]["default"]["headSha"] = commit_sha
            save_manifest(workspace, "alpha", run_id, manifest)
            with patch(
                "hooks.batch_merger.mark_parallel_batch_tasks_merged",
                return_value=WriterResult(ok=True, changed=False, errors=[]),
            ):
                recovered = recover_plan_state_after_merge(workspace, "alpha", run_id, "B001")
            self.assertTrue(recovered["success"], recovered)
            persisted = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(persisted["batches"]["B001"]["status"], "merged")
            self.assertEqual(persisted["batches"]["B001"]["mergeCommitSha"], commit_sha)
            self.assertNotIn("resolution", persisted["batches"]["B001"])

    def test_special_execution_stages_are_serialized_before_parallel_work(self) -> None:
        manifest = {
            "batches": {
                "B001": {"status": "pending", "repositoryRef": "api", "executionStage": "parallel", "writeSet": ["api.py"]},
                "B002": {"status": "pending", "repositoryRef": "api", "executionStage": "proto", "writeSet": ["schema.proto"]},
                "B003": {"status": "pending", "repositoryRef": "api", "executionStage": "global", "writeSet": ["application.yml"]},
            }
        }
        self.assertEqual(resource_groups(manifest, ["B001", "B002", "B003"]), [["B002"]])
        manifest["batches"]["B002"]["status"] = "merged"
        self.assertEqual(resource_groups(manifest, ["B001", "B003"]), [["B003"]])

    def test_multi_repository_run_binds_each_workspace_and_schedules_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, api = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            web = root / "web"
            web.mkdir()
            task_runner_git(web, "init", "-b", "main")
            task_runner_git(web, "config", "user.email", "test@example.com")
            task_runner_git(web, "config", "user.name", "Test")
            _configure_runtime_ignore(web)
            (web / "site.txt").write_text("base\n", encoding="utf-8")
            task_runner_git(web, "add", ".")
            task_runner_git(web, "commit", "-m", "initial")

            b1_path = feature_dir / "plans" / "B001" / "plan.json"
            b2_path = feature_dir / "plans" / "B002" / "plan.json"
            b1 = json.loads(b1_path.read_text(encoding="utf-8"))
            b2 = json.loads(b2_path.read_text(encoding="utf-8"))
            b2["tasks"][0].update({"workspaceRef": "web", "deps": []})
            b2["tasks"][0]["scope"]["workspaceRoots"] = {"web": "."}
            for command in b2["tasks"][0]["validationCommands"]:
                command["repo"] = "web"
            for command in b2["batchValidation"]["commands"]:
                command["repo"] = "web"
            b2_path.write_text(json.dumps(b2), encoding="utf-8")
            root_path = feature_dir / "plan.json"
            plan = json.loads(root_path.read_text(encoding="utf-8"))
            plan["batches"][1].update({"workspaceRef": "web", "deps": []})
            plan["taskSetDigest"] = task_set_digest(plan, {"B001": b1, "B002": b2})
            root_path.write_text(json.dumps(plan), encoding="utf-8")

            verdict = validate_plan_for_parallel(workspace, "alpha")
            self.assertTrue(verdict["canParallel"])
            self.assertEqual(verdict["workspaceRefs"], ["default", "web"])
            with self.assertRaisesRegex(ValueError, "parallel_code_workspace_missing:web"):
                create_run(workspace, "alpha", max_parallel=4, timeout_seconds=60, code_workspaces=[f"default={api}"])
            run = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[f"default={api}", f"web={web}"],
            )
            self.assertEqual(run["scheduledGroups"], [["B001", "B002"]])
            bindings = run["batchWorkspaces"]
            self.assertEqual(bindings["B001"]["requestedPath"], str(api.resolve()))
            self.assertEqual(bindings["B002"]["requestedPath"], str(web.resolve()))

            api_wave = schedule(workspace, "alpha", run["runId"], workspace_refs=["default"])
            web_wave = schedule(workspace, "alpha", run["runId"], workspace_refs=["web"])
            self.assertEqual(api_wave["scheduledGroups"], [["B001"]])
            self.assertEqual(web_wave["scheduledGroups"], [["B002"]])
            self.assertEqual(api_wave["workspaceRefs"], ["default"])
            self.assertEqual(web_wave["workspaceRefs"], ["web"])
            self.assertEqual(api_wave["allParallelGroups"], [["B001", "B002"]])

            waiting_manifest = load_manifest(workspace, "alpha", run["runId"])
            waiting_manifest["batches"]["B001"]["dependencies"] = ["B002"]
            waiting_manifest["batches"]["B002"]["status"] = "running"
            save_manifest(workspace, "alpha", run["runId"], waiting_manifest)
            waiting = schedule(workspace, "alpha", run["runId"], workspace_refs=["default"])
            self.assertTrue(waiting["waitingForRepositories"])
            self.assertEqual(waiting["scheduledGroups"], [])

    def test_multi_repository_worktrees_merge_back_to_their_own_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, api = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            (api / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
            task_runner_git(api, "add", ".gitignore")
            task_runner_git(api, "commit", "-m", "ignore worktrees")
            web = root / "web"
            web.mkdir()
            task_runner_git(web, "init", "-b", "main")
            task_runner_git(web, "config", "user.email", "test@example.com")
            task_runner_git(web, "config", "user.name", "Test")
            _configure_runtime_ignore(web)
            (web / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
            (web / "site.txt").write_text("base\n", encoding="utf-8")
            task_runner_git(web, "add", ".")
            task_runner_git(web, "commit", "-m", "initial")

            b1_path = feature_dir / "plans" / "B001" / "plan.json"
            b2_path = feature_dir / "plans" / "B002" / "plan.json"
            b1 = json.loads(b1_path.read_text(encoding="utf-8"))
            b2 = json.loads(b2_path.read_text(encoding="utf-8"))
            b2["tasks"][0].update({"workspaceRef": "web", "deps": []})
            b2["tasks"][0]["scope"]["workspaceRoots"] = {"web": "."}
            for command in b2["tasks"][0]["validationCommands"]:
                command["repo"] = "web"
            for command in b2["batchValidation"]["commands"]:
                command["repo"] = "web"
            b2_path.write_text(json.dumps(b2), encoding="utf-8")
            root_path = feature_dir / "plan.json"
            plan = json.loads(root_path.read_text(encoding="utf-8"))
            plan["batches"][1].update({"workspaceRef": "web", "deps": []})
            plan["taskSetDigest"] = task_set_digest(plan, {"B001": b1, "B002": b2})
            root_path.write_text(json.dumps(plan), encoding="utf-8")

            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[f"default={api}", f"web={web}"],
            )
            run_id = scheduled["runId"]
            leases = {
                "B001": acquire_lease(workspace, "alpha", run_id, "B001"),
                "B002": acquire_lease(workspace, "alpha", run_id, "B002"),
            }
            api_tree = _create_native_worktree(workspace, "alpha", run_id, "B001", None, leases["B001"]["ownerToken"])
            web_tree = _create_native_worktree(workspace, "alpha", run_id, "B002", None, leases["B002"]["ownerToken"])
            self.assertTrue(api_tree["success"])
            self.assertTrue(web_tree["success"])
            (Path(api_tree["worktreePath"]) / "api-change.txt").write_text("api\n", encoding="utf-8")
            (Path(web_tree["worktreePath"]) / "web-change.txt").write_text("web\n", encoding="utf-8")
            deliveries = {"B001": api_tree, "B002": web_tree}
            for batch_id in ("B001", "B002"):
                mark_batch(workspace, "alpha", run_id, batch_id, "ready_to_merge", compileStatus="passed")
                sealed = _seal_native_worktree(
                    workspace,
                    "alpha",
                    run_id,
                    batch_id,
                    Path(deliveries[batch_id]["worktreePath"]),
                    leases[batch_id]["ownerToken"],
                )
                self.assertTrue(sealed["success"])
                release_lease(workspace, "alpha", run_id, batch_id, leases[batch_id]["ownerToken"], final_status="ready_to_merge")
            merged = merge_run(workspace, "alpha", run_id)
            self.assertTrue(merged["success"])
            self.assertEqual((api / "api-change.txt").read_text(encoding="utf-8"), "api\n")
            self.assertEqual((web / "web-change.txt").read_text(encoding="utf-8"), "web\n")
            self.assertEqual(load_manifest(workspace, "alpha", run_id)["status"], "verifying")

    def test_monorepo_components_share_one_git_root_but_merge_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            component = repo / "apps" / "web"
            component.mkdir(parents=True)
            (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
            (component / "site.txt").write_text("base\n", encoding="utf-8")
            task_runner_git(repo, "add", ".")
            task_runner_git(repo, "commit", "-m", "prepare components")

            b1_path = feature_dir / "plans" / "B001" / "plan.json"
            b2_path = feature_dir / "plans" / "B002" / "plan.json"
            b1 = json.loads(b1_path.read_text(encoding="utf-8"))
            b2 = json.loads(b2_path.read_text(encoding="utf-8"))
            b1["batchValidation"]["commands"][0]["argv"] = [sys.executable, "-c", "print('api compile')"]
            b2["tasks"][0].update({"workspaceRef": "web", "deps": []})
            b2["tasks"][0]["scope"]["workspaceRoots"] = {"web": "apps/web"}
            for command in b2["tasks"][0]["validationCommands"]:
                command["repo"] = "web"
                command["cwd"] = "apps/web"
            for command in b2["batchValidation"]["commands"]:
                command["repo"] = "web"
                command["cwd"] = "apps/web"
                command["argv"] = [sys.executable, "-c", "print('web compile')"]
            b1_path.write_text(json.dumps(b1), encoding="utf-8")
            b2_path.write_text(json.dumps(b2), encoding="utf-8")
            root_path = feature_dir / "plan.json"
            plan = json.loads(root_path.read_text(encoding="utf-8"))
            plan["batches"][1].update({"workspaceRef": "web", "deps": []})
            plan["taskSetDigest"] = task_set_digest(plan, {"B001": b1, "B002": b2})
            root_path.write_text(json.dumps(plan), encoding="utf-8")

            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[f"default={repo}", f"web={component}"],
            )
            self.assertEqual(scheduled["scheduledGroups"], [["B001"]])
            run_id = scheduled["runId"]
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(
                manifest["repositories"]["default"]["gitRoot"],
                manifest["repositories"]["web"]["gitRoot"],
            )
            leases = {
                batch_id: acquire_lease(workspace, "alpha", run_id, batch_id)
                for batch_id in ("B001", "B002")
            }
            api_tree = _create_native_worktree(workspace, "alpha", run_id, "B001", None, leases["B001"]["ownerToken"])
            web_tree = _create_native_worktree(workspace, "alpha", run_id, "B002", None, leases["B002"]["ownerToken"])
            self.assertTrue(api_tree["success"])
            self.assertTrue(web_tree["success"])
            (Path(api_tree["worktreePath"]) / "api-change.txt").write_text("api\n", encoding="utf-8")
            (Path(web_tree["worktreePath"]) / "apps" / "web" / "web-change.txt").write_text("web\n", encoding="utf-8")
            for batch_id in ("B001", "B002"):
                mark_batch(workspace, "alpha", run_id, batch_id, "ready_to_merge", compileStatus="passed")
                sealed = _seal_native_worktree(workspace, "alpha", run_id, batch_id, None, leases[batch_id]["ownerToken"])
                self.assertTrue(sealed["success"])
                release_lease(workspace, "alpha", run_id, batch_id, leases[batch_id]["ownerToken"], final_status="ready_to_merge")
            merged = merge_run(workspace, "alpha", run_id)
            self.assertTrue(merged["success"])
            self.assertEqual((repo / "api-change.txt").read_text(encoding="utf-8"), "api\n")
            self.assertEqual((component / "web-change.txt").read_text(encoding="utf-8"), "web\n")
            verified = verify_final(workspace, "alpha", run_id)
            self.assertTrue(verified["passed"])
            self.assertEqual({item["workspaceRef"] for item in verified["commands"]}, {"default", "web"})

    def test_same_repository_overlap_is_resolved_then_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
            task_runner_git(repo, "add", ".gitignore")
            task_runner_git(repo, "commit", "-m", "ignore worktrees")

            b1_path = feature_dir / "plans" / "B001" / "plan.json"
            b2_path = feature_dir / "plans" / "B002" / "plan.json"
            b1 = json.loads(b1_path.read_text(encoding="utf-8"))
            b2 = json.loads(b2_path.read_text(encoding="utf-8"))
            b2["tasks"][0]["deps"] = []
            b2_path.write_text(json.dumps(b2), encoding="utf-8")
            root_path = feature_dir / "plan.json"
            plan = json.loads(root_path.read_text(encoding="utf-8"))
            plan["batches"][1]["deps"] = []
            plan["taskSetDigest"] = task_set_digest(plan, {"B001": b1, "B002": b2})
            root_path.write_text(json.dumps(plan), encoding="utf-8")

            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            self.assertEqual(scheduled["scheduledGroups"], [["B001"]])
            run_id = scheduled["runId"]
            leases = {
                batch_id: acquire_lease(workspace, "alpha", run_id, batch_id)
                for batch_id in ("B001", "B002")
            }
            first_tree = _create_native_worktree(workspace, "alpha", run_id, "B001", None, leases["B001"]["ownerToken"])
            second_tree = _create_native_worktree(workspace, "alpha", run_id, "B002", None, leases["B002"]["ownerToken"])
            self.assertTrue(first_tree["success"])
            self.assertTrue(second_tree["success"])
            (Path(first_tree["worktreePath"]) / "existing.txt").write_text("first\n", encoding="utf-8")
            (Path(second_tree["worktreePath"]) / "existing.txt").write_text("second\n", encoding="utf-8")
            for batch_id in ("B001", "B002"):
                mark_batch(workspace, "alpha", run_id, batch_id, "ready_to_merge", compileStatus="passed")
                sealed = _seal_native_worktree(workspace, "alpha", run_id, batch_id, None, leases[batch_id]["ownerToken"])
                self.assertTrue(sealed["success"])
                release_lease(workspace, "alpha", run_id, batch_id, leases[batch_id]["ownerToken"], final_status="ready_to_merge")
            merged = merge_run(workspace, "alpha", run_id)
            self.assertFalse(merged["success"])
            self.assertTrue(merged["needsResolution"])
            self.assertEqual([item["batchId"] for item in merged["merged"]], ["B001"])
            self.assertEqual(merged["failed"][0]["batchId"], "B002")
            self.assertEqual(merged["failed"][0]["error"], "native_rebase_conflict")
            self.assertEqual((repo / "existing.txt").read_text(encoding="utf-8"), "first\n")
            self.assertEqual(merged["failed"][0]["resolution"]["mode"], "native_rebase")
            self.assertEqual(merged["failed"][0]["resolution"]["worktreePath"], str(second_tree["worktreePath"]))
            resolution = merged["failed"][0]["resolution"]
            second_worktree = Path(second_tree["worktreePath"])
            rebase = subprocess.run(
                ["git", "rebase", resolution["targetSha"]],
                cwd=second_worktree,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(rebase.returncode, 0, rebase.stdout + rebase.stderr)
            (second_worktree / "existing.txt").write_text("first\nsecond\n", encoding="utf-8")
            _git(second_worktree, "add", "existing.txt")
            continued = subprocess.run(
                ["git", "rebase", "--continue"],
                cwd=second_worktree,
                env={**os.environ, "GIT_EDITOR": "true"},
                capture_output=True,
                text=True,
            )
            self.assertEqual(continued.returncode, 0, continued.stdout + continued.stderr)
            resolved = resolve_merge_conflict(workspace, "alpha", run_id, "B002")
            self.assertTrue(resolved["success"], resolved)
            completed = merge_run(workspace, "alpha", run_id, batch_ids=["B002"])
            self.assertTrue(completed["success"], completed)
            self.assertEqual((repo / "existing.txt").read_text(encoding="utf-8"), "first\nsecond\n")
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(manifest["batches"]["B002"]["status"], "merged")
            self.assertTrue(manifest["batches"]["B002"]["mergeCommitSha"])

    def test_dependent_batch_starts_from_its_repositorys_advanced_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = scheduled["runId"]
            self.assertEqual(scheduled["readyBatches"], ["B001"])
            self.assertEqual(scheduled["mergeableBatches"], [])
            first_workspace = scheduled["batchWorkspaces"]["B001"]
            self.assertIsNone(first_workspace["worktreePath"])
            self.assertIsNone(first_workspace["branchName"])
            self.assertEqual(load_manifest(workspace, "alpha", run_id)["batches"]["B001"]["status"], "pending")
            first_lease = acquire_lease(workspace, "alpha", run_id, "B001")
            first_tree = _create_native_worktree(workspace, "alpha", run_id, "B001", None, first_lease["ownerToken"])
            self.assertTrue(first_tree["success"])
            (Path(first_tree["worktreePath"]) / "first.txt").write_text("first\n", encoding="utf-8")
            mark_batch(workspace, "alpha", run_id, "B001", "ready_to_merge", compileStatus="passed")
            self.assertTrue(_seal_native_worktree(workspace, "alpha", run_id, "B001", None, first_lease["ownerToken"])["success"])
            release_lease(workspace, "alpha", run_id, "B001", first_lease["ownerToken"], final_status="ready_to_merge")
            merge_wave = schedule(workspace, "alpha", run_id)
            self.assertEqual(merge_wave["mergeableBatches"], ["B001"])
            merged_first = merge_run(workspace, "alpha", run_id)
            self.assertTrue(merged_first["success"])
            self.assertEqual(merged_first["nextReadyBatches"], ["B002"])
            first_merge_head = _git(repo, "rev-parse", "HEAD")
            self.assertEqual(load_manifest(workspace, "alpha", run_id)["repositories"]["default"]["headSha"], first_merge_head)
            next_wave = schedule(workspace, "alpha", run_id)
            self.assertEqual(next_wave["scheduledGroups"], [["B002"]])
            second_workspace = next_wave["batchWorkspaces"]["B002"]
            self.assertIsNone(second_workspace["worktreePath"])
            self.assertIsNone(second_workspace["branchName"])
            self.assertEqual(load_manifest(workspace, "alpha", run_id)["batches"]["B002"]["status"], "pending")

            second_lease = acquire_lease(workspace, "alpha", run_id, "B002")
            second_tree = _create_native_worktree(workspace, "alpha", run_id, "B002", None, second_lease["ownerToken"])
            self.assertTrue(second_tree["success"])
            self.assertEqual(_git(Path(second_tree["worktreePath"]), "rev-parse", "HEAD"), first_merge_head)
            self.assertEqual((Path(second_tree["worktreePath"]) / "first.txt").read_text(encoding="utf-8"), "first\n")
            (Path(second_tree["worktreePath"]) / "second.txt").write_text("second\n", encoding="utf-8")
            mark_batch(workspace, "alpha", run_id, "B002", "ready_to_merge", compileStatus="passed")
            self.assertTrue(_seal_native_worktree(workspace, "alpha", run_id, "B002", None, second_lease["ownerToken"])["success"])
            release_lease(workspace, "alpha", run_id, "B002", second_lease["ownerToken"], final_status="ready_to_merge")
            self.assertTrue(merge_run(workspace, "alpha", run_id)["success"])
            self.assertEqual((repo / "second.txt").read_text(encoding="utf-8"), "second\n")

    def test_expired_lease_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature = "alpha"
            run = "cw-20260819-000000-test"
            run_dir = workspace / ".autobizdevops" / "features" / feature / ".parallel-runs" / run
            (run_dir / "leases").mkdir(parents=True)
            manifest = {
                "runId": run,
                "batches": {"B001": {"status": "pending", "lease": None}},
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            lease = acquire_lease(workspace, feature, run, "B001", ttl_seconds=1)
            self.assertTrue(lease["ownerToken"])
            persisted = load_manifest(workspace, feature, run)["batches"]["B001"]["lease"]
            self.assertNotIn("ownerToken", persisted)
            time.sleep(1.05)
            self.assertTrue(reclaim_lease(workspace, feature, run, "B001"))

    def test_lease_heartbeat_renews_and_exits_with_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature = "alpha"
            run = "cw-20260819-000002-test"
            state_path = workspace / ".autobizdevops" / "state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({"features": {feature: {"checkpoint": "code_in_progress"}}}), encoding="utf-8")
            run_dir = workspace / ".autobizdevops" / "features" / feature / ".parallel-runs" / run
            (run_dir / "leases").mkdir(parents=True)
            manifest = {
                "runId": run,
                "batches": {"B001": {"status": "pending", "lease": None}},
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            lease = acquire_lease(workspace, feature, run, "B001", ttl_seconds=10)
            pid_file = workspace / "heartbeat.pid"
            manager = Path(__file__).resolve().parents[1] / "hooks" / "batch_lease_manager.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(manager),
                    "heartbeat",
                    "--workspace",
                    str(workspace),
                    "--feature",
                    feature,
                    "--run-id",
                    run,
                    "--batch-id",
                    "B001",
                    "--owner-token",
                    lease["ownerToken"],
                    "--ttl-seconds",
                    "10",
                    "--interval-seconds",
                    "1",
                    "--max-seconds",
                    "1",
                    "--pid-file",
                    str(pid_file),
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(pid_file.exists())
            self.assertTrue(check_lease(workspace, feature, run, "B001", lease["ownerToken"]))

    def test_force_reclaim_cli_does_not_require_worker_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature = "alpha"
            run = "cw-20260819-000003-test"
            state_path = workspace / ".autobizdevops" / "state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({"features": {feature: {"checkpoint": "code_in_progress"}}}), encoding="utf-8")
            run_dir = workspace / ".autobizdevops" / "features" / feature / ".parallel-runs" / run
            (run_dir / "leases").mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                json.dumps({"runId": run, "batches": {"B001": {"status": "leased", "lease": {}}}}),
                encoding="utf-8",
            )
            acquire_lease(workspace, feature, run, "B001", ttl_seconds=60)
            manager = Path(__file__).resolve().parents[1] / "hooks" / "batch_lease_manager.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(manager),
                    "reclaim",
                    "--workspace",
                    str(workspace),
                    "--feature",
                    feature,
                    "--run-id",
                    run,
                    "--batch-id",
                    "B001",
                    "--force",
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["reclaimed"])
            self.assertEqual(load_manifest(workspace, feature, run)["batches"]["B001"]["status"], "pending")

    def test_lease_rejects_dependency_without_merge_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature = "alpha"
            run = "cw-20260819-000001-test"
            run_dir = workspace / ".autobizdevops" / "features" / feature / ".parallel-runs" / run
            (run_dir / "leases").mkdir(parents=True)
            manifest = {
                "runId": run,
                "batches": {
                    "B001": {"status": "merged", "mergeCommitSha": None, "lease": None},
                    "B002": {"status": "pending", "dependencies": ["B001"], "lease": None},
                },
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(ready_batches(manifest), [])
            with self.assertRaisesRegex(ValueError, "parallel_batch_dependency_incomplete:B002:B001"):
                acquire_lease(workspace, feature, run, "B002")

if __name__ == "__main__":
    unittest.main()
