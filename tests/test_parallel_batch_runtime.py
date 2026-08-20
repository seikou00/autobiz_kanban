from __future__ import annotations

import json
import copy
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from hooks.batch_merger import merge_run, sequential_merge_batches
from hooks.parallel_conflict_resolver import complete_resolution, merge_resolution
from hooks.parallel_final_verify import verify_final
from hooks.parallel_runtime import acquire_lease, load_manifest, plan_digest, reclaim_lease, release_lease, resource_groups
from hooks.parallel_batch_scheduler import create_run, mark_batch, validate_plan_for_parallel
from hooks.plan_json import PlanBundle
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


class ParallelBatchRuntimeTest(unittest.TestCase):
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
            api_tree = create_parallel_worktree(workspace, "alpha", run_id, "B001", None, leases["B001"]["ownerToken"])
            web_tree = create_parallel_worktree(workspace, "alpha", run_id, "B002", None, leases["B002"]["ownerToken"])
            self.assertTrue(api_tree["success"])
            self.assertTrue(web_tree["success"])
            (Path(api_tree["worktreePath"]) / "api-change.txt").write_text("api\n", encoding="utf-8")
            (Path(web_tree["worktreePath"]) / "web-change.txt").write_text("web\n", encoding="utf-8")
            for batch_id, repo in (("B001", api), ("B002", web)):
                mark_batch(workspace, "alpha", run_id, batch_id, "ready_to_merge", compileStatus="passed")
                sealed = seal_parallel_batch(workspace, "alpha", run_id, batch_id, repo, leases[batch_id]["ownerToken"])
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
            api_tree = create_parallel_worktree(workspace, "alpha", run_id, "B001", None, leases["B001"]["ownerToken"])
            web_tree = create_parallel_worktree(workspace, "alpha", run_id, "B002", None, leases["B002"]["ownerToken"])
            self.assertTrue(api_tree["success"])
            self.assertTrue(web_tree["success"])
            (Path(api_tree["worktreePath"]) / "api-change.txt").write_text("api\n", encoding="utf-8")
            (Path(web_tree["worktreePath"]) / "apps" / "web" / "web-change.txt").write_text("web\n", encoding="utf-8")
            for batch_id in ("B001", "B002"):
                mark_batch(workspace, "alpha", run_id, batch_id, "ready_to_merge", compileStatus="passed")
                sealed = seal_parallel_batch(workspace, "alpha", run_id, batch_id, None, leases[batch_id]["ownerToken"])
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
            first_tree = create_parallel_worktree(workspace, "alpha", run_id, "B001", None, leases["B001"]["ownerToken"])
            second_tree = create_parallel_worktree(workspace, "alpha", run_id, "B002", None, leases["B002"]["ownerToken"])
            self.assertTrue(first_tree["success"])
            self.assertTrue(second_tree["success"])
            (Path(first_tree["worktreePath"]) / "existing.txt").write_text("first\n", encoding="utf-8")
            (Path(second_tree["worktreePath"]) / "existing.txt").write_text("second\n", encoding="utf-8")
            for batch_id in ("B001", "B002"):
                mark_batch(workspace, "alpha", run_id, batch_id, "ready_to_merge", compileStatus="passed")
                sealed = seal_parallel_batch(workspace, "alpha", run_id, batch_id, None, leases[batch_id]["ownerToken"])
                self.assertTrue(sealed["success"])
                release_lease(workspace, "alpha", run_id, batch_id, leases[batch_id]["ownerToken"], final_status="ready_to_merge")
            merged = merge_run(workspace, "alpha", run_id)
            self.assertFalse(merged["success"])
            self.assertTrue(merged["needsResolution"])
            self.assertEqual([item["batchId"] for item in merged["merged"]], ["B001"])
            self.assertEqual(merged["failed"][0]["batchId"], "B002")
            self.assertEqual(merged["failed"][0]["error"], "merge_conflict_needs_resolution")
            self.assertEqual((repo / "existing.txt").read_text(encoding="utf-8"), "first\n")
            resolution_tree = Path(merged["failed"][0]["resolution"]["worktreePath"])
            (resolution_tree / "existing.txt").write_text("resolved\n", encoding="utf-8")
            (resolution_tree / "compile-fixed.txt").write_text("ok\n", encoding="utf-8")
            task_runner_git(resolution_tree, "add", "existing.txt")
            completed = complete_resolution(workspace, "alpha", run_id, "B002")
            self.assertTrue(completed["success"])
            resolved_merge = merge_resolution(workspace, "alpha", run_id, "B002")
            self.assertTrue(resolved_merge["success"])
            self.assertEqual((repo / "existing.txt").read_text(encoding="utf-8"), "resolved\n")

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
            first_lease = acquire_lease(workspace, "alpha", run_id, "B001")
            first_tree = create_parallel_worktree(workspace, "alpha", run_id, "B001", None, first_lease["ownerToken"])
            self.assertTrue(first_tree["success"])
            (Path(first_tree["worktreePath"]) / "first.txt").write_text("first\n", encoding="utf-8")
            mark_batch(workspace, "alpha", run_id, "B001", "ready_to_merge", compileStatus="passed")
            self.assertTrue(seal_parallel_batch(workspace, "alpha", run_id, "B001", None, first_lease["ownerToken"])["success"])
            release_lease(workspace, "alpha", run_id, "B001", first_lease["ownerToken"], final_status="ready_to_merge")
            self.assertTrue(merge_run(workspace, "alpha", run_id)["success"])
            first_merge_head = _git(repo, "rev-parse", "HEAD")
            self.assertEqual(load_manifest(workspace, "alpha", run_id)["repositories"]["default"]["headSha"], first_merge_head)

            second_lease = acquire_lease(workspace, "alpha", run_id, "B002")
            second_tree = create_parallel_worktree(workspace, "alpha", run_id, "B002", None, second_lease["ownerToken"])
            self.assertTrue(second_tree["success"])
            self.assertEqual(_git(Path(second_tree["worktreePath"]), "rev-parse", "HEAD"), first_merge_head)
            self.assertEqual((Path(second_tree["worktreePath"]) / "first.txt").read_text(encoding="utf-8"), "first\n")
            (Path(second_tree["worktreePath"]) / "second.txt").write_text("second\n", encoding="utf-8")
            mark_batch(workspace, "alpha", run_id, "B002", "ready_to_merge", compileStatus="passed")
            self.assertTrue(seal_parallel_batch(workspace, "alpha", run_id, "B002", None, second_lease["ownerToken"])["success"])
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

    def test_sequential_merge_stops_on_first_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init", "-b", "main")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test")
            (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
            (repo / "same.txt").write_text("base\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "base")
            _git(repo, "worktree", "add", "-b", "worktree/one", ".worktrees/one")
            one = repo / ".worktrees" / "one"
            (one / "same.txt").write_text("one\n", encoding="utf-8")
            _git(one, "add", "same.txt")
            _git(one, "commit", "-m", "one")
            _git(repo, "worktree", "add", "-b", "worktree/two", ".worktrees/two")
            two = repo / ".worktrees" / "two"
            (two / "same.txt").write_text("two\n", encoding="utf-8")
            _git(two, "add", "same.txt")
            _git(two, "commit", "-m", "two")
            result = sequential_merge_batches(repo, ["one", "two"], ["B001", "B002"])
            self.assertFalse(result["success"])
            self.assertEqual([item["batchId"] for item in result["merged"]], ["B001"])
            self.assertEqual(result["failed"][0]["batchId"], "B002")
            self.assertEqual((repo / "same.txt").read_text(encoding="utf-8"), "one\n")


if __name__ == "__main__":
    unittest.main()
