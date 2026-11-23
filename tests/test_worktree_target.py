"""Tests for native worktree target selection, short candidate paths, and non-retryable blocking."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hooks import parallel_merge_train as train
from hooks import worktree_target
from hooks.parallel_batch_scheduler import create_run, manual_resume_run, mark_batch, resume_run, schedule
from hooks.parallel_batch_stage import complete_stage, start_stage
from hooks.parallel_cleanup import drain
from hooks.parallel_delivery import settle_delivery
from hooks.parallel_dispatch import claim, defer
from hooks.parallel_integration import git, integration_repo
from hooks.parallel_runtime import global_worktrees_root, load_manifest, run_dir
from hooks.repository_snapshot import git_worktree_records
from hooks.worktree_manager import provision_parallel_worktree
from hooks.worktree_target import (
    WORKTREE_GIT_DIR_TOO_LONG,
    git_dir_utf8_bytes,
    resolve_worktree_target_arg,
)
from tests.test_task_runner import _configure_defer_to_test_stages, _refresh_parallel_pipeline, _workspace


class WorktreeTargetSelectionTest(unittest.TestCase):
    def test_prefers_absolute_up_to_215_bytes_and_skips_shortening(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            repo = root / "repo"
            repo.mkdir()
            target = root / "wt"
            abs_bytes = git_dir_utf8_bytes(str(target.resolve()))
            with patch("hooks.worktree_target.os.path.relpath", side_effect=AssertionError("relpath must not run")):
                plan = resolve_worktree_target_arg(
                    repo,
                    target,
                    enforce_limit=True,
                    preferred_bytes=abs_bytes,
                    hard_limit_bytes=abs_bytes + 5,
                    short_path_fn=lambda _: self.fail("short_path_fn must not run"),
                )
            self.assertEqual(plan.strategy, "absolute")
            self.assertEqual(plan.git_arg, str(target.resolve()))
            self.assertEqual(plan.git_dir_bytes, abs_bytes)

    def test_selects_relative_path_above_215_using_utf8_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            # Multi-byte UTF-8 characters push byte length past 215 while char count stays shorter.
            deep = root / ("目录" * 35)
            repo = deep / "repo"
            repo.mkdir(parents=True)
            target = deep / "m1"
            abs_str = str(target.resolve())
            abs_bytes = git_dir_utf8_bytes(abs_str)
            self.assertGreater(abs_bytes, 215)
            self.assertLess(len(abs_str) + 5, 215)
            plan = resolve_worktree_target_arg(repo, target, enforce_limit=True)
            self.assertEqual(plan.strategy, "relative")
            self.assertFalse(os.path.isabs(plan.git_arg))
            self.assertLess(plan.git_dir_bytes, 215)
            self.assertEqual(plan.canonical_path, target.resolve())

    def test_216_to_220_cross_drive_is_allowed_while_over_220_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            repo = root / "repo"
            repo.mkdir()
            parent = root / "p"
            parent.mkdir()
            base_len = git_dir_utf8_bytes(str(parent / "a"))
            for target_bytes, should_raise in ((216, False), (220, False), (221, True)):
                name = "x" * (1 + target_bytes - base_len)
                target = parent / name
                self.assertEqual(git_dir_utf8_bytes(str(target.resolve())), target_bytes)
                with self.subTest(target_bytes=target_bytes), patch(
                    "hooks.worktree_target.os.path.relpath",
                    side_effect=ValueError("different drives"),
                ):
                    if should_raise:
                        with self.assertRaisesRegex(ValueError, WORKTREE_GIT_DIR_TOO_LONG):
                            resolve_worktree_target_arg(
                                repo,
                                target,
                                enforce_limit=True,
                                short_path_fn=lambda p: str(p),
                            )
                        relaxed = resolve_worktree_target_arg(
                            repo,
                            target,
                            enforce_limit=True,
                            allow_fail_fast=False,
                            short_path_fn=lambda p: str(p),
                        )
                        self.assertEqual(relaxed.strategy, "absolute")
                        self.assertEqual(relaxed.git_dir_bytes, 221)
                    else:
                        plan = resolve_worktree_target_arg(
                            repo,
                            target,
                            enforce_limit=True,
                            short_path_fn=lambda p: str(p),
                        )
                        self.assertEqual(plan.strategy, "absolute")
                        self.assertEqual(plan.git_dir_bytes, target_bytes)

    def test_8_3_short_name_verifies_samefile_and_rejoins_leaf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            repo = root / "repo"
            repo.mkdir()
            long_parent = root / ("very_long_directory_" * 12)
            long_parent.mkdir()
            wrong_parent = root / "wrong"
            wrong_parent.mkdir()
            alias = root / "s"
            alias.symlink_to(long_parent, target_is_directory=True)
            target = long_parent / "m1"
            with patch("hooks.worktree_target.os.path.relpath", side_effect=ValueError("cross-drive")):
                plan = resolve_worktree_target_arg(
                    repo,
                    target,
                    enforce_limit=True,
                    short_path_fn=lambda p: str(alias) if p == long_parent else None,
                )
                self.assertEqual(plan.strategy, "short_8_3")
                self.assertEqual(plan.git_arg, os.path.join(str(alias), "m1"))
                self.assertEqual(plan.git_dir_bytes, git_dir_utf8_bytes(os.path.join(str(alias), "m1")))

                with self.assertRaisesRegex(ValueError, WORKTREE_GIT_DIR_TOO_LONG):
                    resolve_worktree_target_arg(
                        repo,
                        target,
                        enforce_limit=True,
                        short_path_fn=lambda _: str(wrong_parent),
                    )

    def test_non_windows_default_does_not_reject_over_220_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            repo = root / "repo"
            repo.mkdir()
            deep = root / ("a" * 230)
            deep.mkdir()
            target = deep / "m1"
            with patch.object(worktree_target.sys, "platform", "darwin"):
                plan = resolve_worktree_target_arg(repo, target)
            self.assertEqual(plan.strategy, "absolute")
            self.assertGreater(plan.git_dir_bytes, 220)


class MergeTrainShortCandidateTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="merge-train-short-")
        self.addCleanup(temporary.cleanup)
        self.workspace, self.feature, self.repo = _workspace(Path(temporary.name))
        _configure_defer_to_test_stages(self.feature)
        root_path = self.feature / "plan.json"
        root = json.loads(root_path.read_text(encoding="utf-8"))
        root["batches"][0].update(executionStage="parallel", workspaceRef="default")
        batch_path = self.feature / root["batches"][0]["path"]
        batch_plan = json.loads(batch_path.read_text(encoding="utf-8"))
        batch_plan["executionStage"] = "parallel"
        batch_plan["tasks"][0]["status"] = "implemented"
        batch_path.write_text(json.dumps(batch_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _refresh_parallel_pipeline(self.feature)
        self.run_id = create_run(
            self.workspace,
            "alpha",
            max_parallel=1,
            timeout_seconds=120,
            code_workspaces=[str(self.repo)],
            task_card_id="TEST-001",
        )["runId"]

    def _prepare_ready_batch(self) -> tuple[str, str]:
        owner = claim(self.workspace, "alpha", self.run_id, "B001", 1, "initial", "B001")
        self.assertEqual(owner["status"], "claimed")
        provisioned = provision_parallel_worktree(self.workspace, "alpha", self.run_id, "B001")
        tree = Path(provisioned["worktreePath"])
        (tree / "feature.txt").write_text("candidate change\n", encoding="utf-8")
        git(tree, "add", "feature.txt")
        git(tree, "commit", "-m", "B001 change")
        sha = git(tree, "rev-parse", "HEAD")
        mark_batch(
            self.workspace,
            "alpha",
            self.run_id,
            "B001",
            "sealed",
            worktreePath=str(tree),
            branchName=provisioned["branchName"],
            commitSha=sha,
        )
        for stage in ("prepare", "implement", "review"):
            start_stage(self.workspace, "alpha", self.run_id, "B001", stage)
            complete_stage(self.workspace, "alpha", self.run_id, "B001", stage, metadata={"batchCommit": sha})
        return owner["ownerToken"], sha

    def test_candidate_uses_short_worktree_path_and_retains_run_dir_lock(self):
        candidate_path, branch = train._candidate_paths(self.workspace, "alpha", self.run_id, "default", 1)
        lock_path = train._candidate_lock_path(self.workspace, "alpha", self.run_id, "default")
        self.assertTrue(candidate_path.is_relative_to(global_worktrees_root(self.workspace) / self.run_id / "c"))
        self.assertNotIn("merge-trains", candidate_path.parts)
        self.assertEqual(
            lock_path,
            run_dir(self.workspace, "alpha", self.run_id) / "merge-trains" / "default" / ".candidate.lock",
        )
        self.assertIn("default", branch)

    def test_build_candidate_relative_arg_cleans_legacy_worktree_and_drains(self):
        owner_token, sha = self._prepare_ready_batch()
        manifest = load_manifest(self.workspace, "alpha", self.run_id)
        integration = integration_repo(manifest["repositories"]["default"])
        legacy_path = train._legacy_candidate_path(self.workspace, "alpha", self.run_id, "default", 1)
        expected_path, _ = train._candidate_paths(self.workspace, "alpha", self.run_id, "default", 1)
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        head_sha = git(integration, "rev-parse", "HEAD")
        git(integration, "worktree", "add", "--detach", str(legacy_path), head_sha)
        git(integration, "worktree", "lock", "--reason", "initializing", str(legacy_path))

        real_resolver = resolve_worktree_target_arg
        strategies: list[str] = []

        def forced_relative(git_cwd, target_path, **kwargs):
            kwargs["enforce_limit"] = True
            kwargs["preferred_bytes"] = 1
            plan = real_resolver(git_cwd, target_path, **kwargs)
            strategies.append(plan.strategy)
            return plan

        with patch("hooks.parallel_merge_train.resolve_worktree_target_arg", side_effect=forced_relative), patch(
            "hooks.parallel_cleanup.resolve_worktree_target_arg", side_effect=forced_relative
        ):
            settled = settle_delivery(self.workspace, "alpha", self.run_id, "B001", 1, owner_token, sha)
            self.assertEqual(settled["status"], "merged")
            self.assertFalse(legacy_path.exists())
            record = load_manifest(self.workspace, "alpha", self.run_id)["mergeTrains"]["default:wave-001"]
            self.assertEqual(record["worktreePath"], str(expected_path))
            self.assertTrue(expected_path.is_dir())
            self.assertGreaterEqual(strategies.count("relative"), 2)
            registered = [Path(item["worktree"]).resolve() for item in git_worktree_records(integration)]
            self.assertIn(expected_path.resolve(), registered)

            drained = drain(self.workspace, "alpha", self.run_id)
            self.assertEqual(drained["status"], "completed")
            self.assertFalse(expected_path.exists())
            self.assertGreaterEqual(strategies.count("relative"), 3)


class SchedulerNonRetryableBlockingTest(unittest.TestCase):
    def test_git_dir_too_long_blocks_without_auto_retry_and_allows_manual_resume(self):
        errors = (
            f"{WORKTREE_GIT_DIR_TOO_LONG}:bytes=225:limit=220:strategy=absolute",
            "parallel_merge_train_create_failed:fatal: '$GIT_DIR' too big",
        )
        for error in errors:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as tmp:
                workspace, feature, repo = _workspace(Path(tmp))
                _configure_defer_to_test_stages(feature)
                run_id = create_run(
                    workspace,
                    "alpha",
                    max_parallel=1,
                    timeout_seconds=120,
                    code_workspaces=[str(repo)],
                    task_card_id="TEST-001",
                )["runId"]
                owner = claim(workspace, "alpha", run_id, "B001", 1, "initial", "B001")
                provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
                tree = Path(provisioned["worktreePath"])
                (tree / "file.txt").write_text("sealed change\n", encoding="utf-8")
                git(tree, "add", "file.txt")
                git(tree, "commit", "-m", "seal")
                sha = git(tree, "rev-parse", "HEAD")
                mark_batch(
                    workspace,
                    "alpha",
                    run_id,
                    "B001",
                    "sealed",
                    worktreePath=str(tree),
                    branchName=provisioned["branchName"],
                    commitSha=sha,
                )
                for stage in ("prepare", "implement", "review"):
                    start_stage(workspace, "alpha", run_id, "B001", stage)
                    complete_stage(workspace, "alpha", run_id, "B001", stage, metadata={"batchCommit": sha})
                mark_batch(workspace, "alpha", run_id, "B001", "ready_to_candidate")

                deferred = defer(workspace, "alpha", run_id, "B001", 1, owner["ownerToken"], error)
                self.assertEqual(deferred["status"], "blocked")
                self.assertEqual(deferred["reasonCode"], WORKTREE_GIT_DIR_TOO_LONG)

                manifest = load_manifest(workspace, "alpha", run_id)
                batch = manifest["batches"]["B001"]
                self.assertEqual(manifest["status"], "blocked")
                self.assertEqual(batch["status"], "blocked")
                self.assertEqual(batch["dispatch"]["status"], "failed")
                self.assertEqual(batch["recovery"]["status"], "blocked_non_retryable")
                self.assertTrue(batch["recovery"]["nonRetryable"])
                self.assertEqual(batch["recovery"]["failureClassification"], "non_retryable")
                self.assertEqual(batch["recovery"]["resumeStatus"], "ready_to_candidate")

                polled = schedule(workspace, "alpha", run_id)
                self.assertEqual(polled["retryPendingBatches"], [])
                self.assertEqual(polled["blockedBatches"], ["B001"])

                auto_resumed = resume_run(workspace, "alpha", run_id)
                self.assertEqual(auto_resumed["rescheduledRetryBatches"], [])
                after_auto = load_manifest(workspace, "alpha", run_id)
                self.assertEqual(after_auto["status"], "blocked")
                self.assertEqual(after_auto["batches"]["B001"]["status"], "blocked")

                manual = manual_resume_run(workspace, "alpha", run_id)
                self.assertEqual(manual["manualRetryBatches"], ["B001"])
                self.assertEqual(manual["rescheduledRetryBatches"], ["B001"])
                after_manual = load_manifest(workspace, "alpha", run_id)
                resumed_batch = after_manual["batches"]["B001"]
                self.assertEqual(after_manual["status"], "running")
                self.assertEqual(resumed_batch["status"], "ready_to_candidate")
                self.assertNotIn("nonRetryable", resumed_batch["recovery"])
                self.assertEqual(resumed_batch["commitSha"], sha)
                self.assertEqual(resumed_batch["worktreePath"], str(tree))
