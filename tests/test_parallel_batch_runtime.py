from __future__ import annotations

import json
import copy
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from hooks.batch_merger import merge_run
from hooks.parallel_batch_lifecycle import cleanup_run, rollback_run
from hooks.parallel_final_verify import verify_final
from hooks.parallel_runtime import (
    acquire_lease,
    check_lease,
    load_manifest,
    plan_digest,
    reclaim_lease,
    release_lease,
    resource_groups,
    run_lock,
    save_manifest,
)
from hooks.parallel_batch_scheduler import (
    assert_batch_worktree_isolated,
    create_run as _create_run,
    mark_batch,
    schedule,
    validate_plan_for_parallel,
)
from hooks.plan_json import PlanBundle, load_plan_bundle
from hooks.plan_json import task_set_digest
from hooks.worktree_manager import create_parallel_worktree, seal_parallel_batch
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
    """Create the production plugin-managed delivery worktree."""
    return create_parallel_worktree(workspace, feature, run_id, batch_id, repo_path, owner_token)


def _seal_native_worktree(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    repo_path: Path | None,
    owner_token: str,
) -> dict[str, Any]:
    """Seal the production plugin-managed delivery worktree."""
    return seal_parallel_batch(workspace, feature, run_id, batch_id, repo_path, owner_token)


class ParallelBatchRuntimeTest(unittest.TestCase):
    def test_partial_rollback_run_can_be_cleaned(self) -> None:
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
            self.assertIn(str(delivery_path), cleanup["removedWorktrees"])
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
            self.assertEqual(manifest["isolation"]["mode"], "plugin_managed_git_worktrees")

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

    def test_scheduler_provisions_linked_worktree_before_batch_lease(self) -> None:
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
            tree = Path(provisioned["worktreePath"])
            self.assertTrue(tree.is_dir())
            self.assertEqual(tree.parent, (repo / ".worktrees").resolve())
            self.assertTrue(provisioned["branchName"])
            self.assertEqual(_git(tree, "rev-parse", "HEAD"), _git(repo, "rev-parse", "HEAD"))
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(manifest["batches"]["B001"]["status"], "pending")
            self.assertIsNone(manifest["batches"]["B001"]["lease"])

            lease = acquire_lease(workspace, "alpha", run_id, "B001")
            delivery = _create_native_worktree(workspace, "alpha", run_id, "B001", repo, lease["ownerToken"])
            self.assertTrue(delivery["success"], delivery)
            self.assertEqual(delivery["worktreePath"], str(tree))
            try:
                manifest = load_manifest(workspace, "alpha", run_id)
                assert_batch_worktree_isolated(manifest, "B001", tree)
                mark_batch(
                    workspace,
                    "alpha",
                    run_id,
                    "B001",
                    "running",
                    worktreePath=str(tree),
                    branchName=delivery["branchName"],
                )
                self.assertEqual(load_manifest(workspace, "alpha", run_id)["batches"]["B001"]["status"], "running")
            finally:
                subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=repo, check=True)

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
                worktree = repo / ".worktrees" / f"native-{batch_id}"
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
            self.assertEqual(manifest["isolation"]["mode"], "plugin_managed_git_worktrees")

            for worktree in deliveries:
                task_runner_git(repo, "worktree", "remove", str(worktree))

    def test_scheduler_bootstraps_dirty_repository_without_manual_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            (repo / "existing.txt").write_text("changed before Code\n", encoding="utf-8")
            (repo / "uncommitted.txt").write_text("preserve this baseline\n", encoding="utf-8")

            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            self.assertTrue(_git(repo, "status", "--porcelain") == "")
            self.assertEqual(_git(repo, "show", "-s", "--format=%s", "HEAD"), "autodev: bootstrap alpha baseline")
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

    def test_resource_groups_do_not_serialize_repository_or_component_overlap(self) -> None:
        manifest = {
            "batches": {
                "B001": {"status": "pending", "workspaceRef": "api", "executionLane": "backend", "writeSet": ["a.py"], "dependencies": []},
                "B002": {"status": "pending", "workspaceRef": "api", "executionLane": "backend", "writeSet": ["a.py"], "dependencies": []},
                "B003": {"status": "pending", "workspaceRef": "worker", "executionLane": "backend", "writeSet": ["c.py"], "dependencies": []},
                "B004": {"status": "pending", "workspaceRef": "cli", "executionLane": "backend", "writeSet": ["d.py"], "dependencies": []},
            }
        }
        groups = {tuple(group) for group in resource_groups(manifest, ["B001", "B002", "B003", "B004"])}
        self.assertEqual(groups, {("B001",), ("B002",), ("B003",), ("B004",)})

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
            self.assertEqual({tuple(group) for group in run["scheduledGroups"]}, {("B001",), ("B002",)})
            bindings = run["batchWorkspaces"]
            self.assertEqual(bindings["B001"]["requestedPath"], str(api.resolve()))
            self.assertEqual(bindings["B002"]["requestedPath"], str(web.resolve()))

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
            for batch_id, repo in (("B001", api), ("B002", web)):
                mark_batch(workspace, "alpha", run_id, batch_id, "ready_to_merge", compileStatus="passed")
                sealed = _seal_native_worktree(workspace, "alpha", run_id, batch_id, repo, leases[batch_id]["ownerToken"])
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

    def test_same_repository_overlap_runs_in_parallel_and_blocks_at_merge(self) -> None:
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
            self.assertEqual({tuple(group) for group in scheduled["scheduledGroups"]}, {("B001",), ("B002",)})
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

    def test_dependent_batch_starts_from_its_repositorys_advanced_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
            task_runner_git(repo, "add", ".gitignore")
            task_runner_git(repo, "commit", "-m", "ignore worktrees")

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
            self.assertTrue(Path(first_workspace["worktreePath"]).is_dir())
            self.assertTrue(first_workspace["branchName"])
            self.assertEqual(load_manifest(workspace, "alpha", run_id)["batches"]["B001"]["status"], "pending")
            first_lease = acquire_lease(workspace, "alpha", run_id, "B001")
            first_tree = _create_native_worktree(workspace, "alpha", run_id, "B001", None, first_lease["ownerToken"])
            self.assertTrue(first_tree["success"])
            self.assertEqual(first_tree["worktreePath"], first_workspace["worktreePath"])
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
            self.assertTrue(Path(second_workspace["worktreePath"]).is_dir())
            self.assertTrue(second_workspace["branchName"])
            self.assertEqual(_git(Path(second_workspace["worktreePath"]), "rev-parse", "HEAD"), first_merge_head)
            self.assertEqual(load_manifest(workspace, "alpha", run_id)["batches"]["B002"]["status"], "pending")

            second_lease = acquire_lease(workspace, "alpha", run_id, "B002")
            second_tree = _create_native_worktree(workspace, "alpha", run_id, "B002", None, second_lease["ownerToken"])
            self.assertTrue(second_tree["success"])
            self.assertEqual(second_tree["worktreePath"], second_workspace["worktreePath"])
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
            with self.assertRaisesRegex(ValueError, "parallel_batch_dependency_incomplete:B002:B001"):
                acquire_lease(workspace, feature, run, "B002")

if __name__ == "__main__":
    unittest.main()
