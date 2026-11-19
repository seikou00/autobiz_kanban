"""Real Git coverage for incremental, uncommitted user-workspace delivery."""
import json
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
import unittest
from unittest.mock import patch

from hooks.parallel_delivery import settle_delivery, prepare_finalization
from hooks.parallel_integration import git, prepare_promotion
from hooks.parallel_workspace_sync import sync_workspace, safely_sync
from hooks import parallel_workspace_sync as sync
from tests import test_parallel_delivery as fixtures


class WorkspaceSyncTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DeliveryTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        plan_path = self.fixture.feature / "plan.json"
        plan = json.loads(plan_path.read_text())
        plan["codeWorkspaces"] = {"default": str(self.fixture.repo)}
        plan_path.write_text(json.dumps(plan))
        self.fixture.create(2)
        self.repo = self.fixture.repo
        self.workspace, self.run = self.fixture.workspace, self.fixture.run
        self.head = git(self.repo, "rev-parse", "HEAD")

    def publish(self, batch_id="B001", content=None):
        args, _ = self.fixture.delivery(batch_id, content=content)
        self.assertEqual(settle_delivery(*args)["status"], "merged")
        return safely_sync(self.workspace, "alpha", self.run, "default")

    def synchronize(self, resolver=None, complete=None):
        return sync_workspace(self.workspace, "alpha", self.run, "default", resolver, complete_attempt=complete)

    def source_index(self):
        return git(self.repo, "ls-files", "--stage")

    def publish_extended(self, edit):
        args, tree = self.fixture.delivery(review=False)
        edit(tree)
        git(tree, "add", "-A")
        git(tree, "commit", "--amend", "--no-edit")
        from hooks.parallel_batch_scheduler import mark_batch
        from hooks.parallel_batch_stage import start_stage, complete_stage
        sha = git(tree, "rev-parse", "HEAD")
        mark_batch(self.workspace, "alpha", self.run, "B001", "sealed", commitSha=sha)
        start_stage(self.workspace, "alpha", self.run, "B001", "review")
        complete_stage(self.workspace, "alpha", self.run, "B001", "review", metadata={"batchCommit": sha})
        self.assertEqual(settle_delivery(*args[:6])["status"], "merged")
        return safely_sync(self.workspace, "alpha", self.run, "default")

    def test_each_batch_is_visible_without_a_source_commit_or_index_change(self):
        initial_index = self.source_index()
        for batch_id in ("B001", "B002"):
            result = self.publish(batch_id)
            self.assertEqual(result["status"], "synced", result)
            self.assertEqual((self.repo / (batch_id + ".txt")).read_text(), batch_id)
            self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)
            self.assertEqual(self.source_index(), initial_index)
        self.assertTrue(self.synchronize()["reused"])
        self.assertEqual(result["lastSyncedSha"], git(self.fixture.integration, "rev-parse", "HEAD"))

    def test_ignored_platform_files_do_not_block_business_publication(self):
        (self.repo / ".gitignore").write_text(".cmbdevclaw/\n")
        runtime = self.repo / ".cmbdevclaw" / "workflows" / "state.json"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("live platform state\n")
        (self.repo / "本地 新文件.txt").write_text("local business edit\n")
        initial_index = self.source_index()
        result = self.publish()
        self.assertEqual(result["status"], "synced", result)
        self.assertEqual((self.repo / "B001.txt").read_text(), "B001")
        self.assertEqual(runtime.read_text(), "live platform state\n")
        self.assertEqual((self.repo / "本地 新文件.txt").read_text(), "local business edit\n")
        self.assertEqual(self.source_index(), initial_index)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)
        journal = json.loads((Path(result["backupPath"]) / "journal.json").read_text())
        self.assertFalse(any(name.startswith(".cmbdevclaw/") for name in journal["originals"]))

    def test_frontend_intermediates_are_not_published_and_local_analysis_is_preserved(self):
        local = self.repo / ".frontend/html-analysis/page.json"
        local.parent.mkdir(parents=True)
        local.write_text("local analysis\n")
        git(self.repo, "add", ".frontend/")
        initial_index = self.source_index()
        business = {"src/page.vue": "page\n", "public/data.json": "{}", "public/logo.png": "image"}
        intermediates = {".frontend/html-analysis/page.json": "incoming analysis\n",
                         "web/.frontend/html-input/original.html": "original"}

        def edit(tree):
            for name, contents in {**business, **intermediates}.items():
                target = tree / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(contents)

        # Deliberately include intermediates in a legacy delivery commit to
        # exercise the publication safeguard independently of the sealer.
        result = self.publish_extended(edit)
        self.assertEqual(result["status"], "synced", result)
        self.assertEqual(local.read_text(), "local analysis\n")
        self.assertFalse((self.repo / "web/.frontend/html-input/original.html").exists())
        self.assertEqual(self.source_index(), initial_index)
        for name, contents in business.items():
            self.assertEqual((self.repo / name).read_text(), contents)
        journal = json.loads((Path(result["backupPath"]) / "journal.json").read_text())
        self.assertTrue(set(intermediates).issubset(journal["skippedIgnoredFiles"]))

    def test_staged_addition_removed_from_disk_stays_only_in_the_user_index(self):
        staged_only = self.repo / "staged-only.txt"
        staged_only.write_text("staged content\n")
        git(self.repo, "add", "--", staged_only.name)
        staged_only.unlink()
        initial_index = self.source_index()
        result = self.publish()
        self.assertEqual(result["status"], "synced", result)
        self.assertFalse(staged_only.exists())
        self.assertEqual(self.source_index(), initial_index)

    def test_merge_and_finalization_return_pending_without_running_publication(self):
        with patch.object(sync, "sync_workspace", side_effect=AssertionError("publication must be a separate step")):
            for batch_id in ("B001", "B002"):
                args, _ = self.fixture.delivery(batch_id)
                result = settle_delivery(*args)
                self.assertEqual(result["status"], "merged")
                self.assertTrue(result["dispatchFinalized"])
                self.assertEqual(result["workspaceSync"]["status"], "queued")
                self.assertFalse((self.repo / (batch_id + ".txt")).exists())
            result = prepare_finalization(self.workspace, "alpha", self.run)
            self.assertEqual(result["workspaceSync"][0]["status"], "queued")
        self.assertEqual(self.synchronize()["status"], "synced")
        self.assertTrue((self.repo / "B001.txt").exists())
        self.assertTrue((self.repo / "B002.txt").exists())

    def test_failed_global_checkout_hook_is_isolated_and_preparing_checkout_is_reused(self):
        args, _ = self.fixture.delivery()
        settle_delivery(*args)
        hooks = self.repo.parent / "global-hooks"
        hooks.mkdir()
        checkout = hooks / "post-checkout"
        checkout.write_text('#!/bin/sh\necho called > "$0.called"\nprintf "\\225\\241" >&2\nexit 1\n')
        checkout.chmod(0o755)
        git(self.repo, "config", "core.hooksPath", str(hooks))
        call = sync._git
        def legacy_checkout(repo, *arguments, **options):
            if arguments[:2] == ("worktree", "add"):
                options.pop("hooks_path", None)
            return call(repo, *arguments, **options)
        with patch.object(sync, "_git", side_effect=legacy_checkout):
            failed = safely_sync(self.workspace, "alpha", self.run, "default")
        self.assertEqual(failed["status"], "pending")
        self.assertIn("\ufffd", failed["error"])
        backup = Path(failed["backupPath"])
        self.assertIn(b"\x95\xa1", (backup / "worktree-add.stderr.log").read_bytes())
        self.assertEqual(json.loads((backup / "journal.json").read_text())["status"], "preparing")
        self.assertTrue(Path(failed["worktreePath"]).is_dir())
        self.assertFalse((self.repo / "B001.txt").exists())
        with patch.object(sync, "_git", wraps=call) as commands:
            recovered = self.synchronize()
        self.assertEqual(recovered["status"], "synced", recovered)
        self.assertEqual(recovered["attemptId"], failed["attemptId"])
        self.assertFalse(any(item.args[1:3] == ("worktree", "add") for item in commands.call_args_list))
        self.assertEqual(git(self.repo, "config", "core.hooksPath"), str(hooks))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)

    def test_fresh_sync_bypasses_checkout_and_merge_hooks_without_changing_config(self):
        args, _ = self.fixture.delivery()
        settle_delivery(*args)
        hooks = self.repo.parent / "user-hooks"
        hooks.mkdir()
        for name in ("post-checkout", "post-merge"):
            hook = hooks / name
            hook.write_text('#!/bin/sh\necho called > "$0.called"\nexit 1\n')
            hook.chmod(0o755)
        git(self.repo, "config", "core.hooksPath", str(hooks))
        result = self.synchronize()
        self.assertEqual(result["status"], "synced", result)
        self.assertEqual(git(self.repo, "config", "core.hooksPath"), str(hooks))
        self.assertEqual(list(hooks.glob("*.called")), [])

    def _pause_after_checkout(self):
        args, _ = self.fixture.delivery()
        settle_delivery(*args)
        call = sync._git
        def pause(repo, *arguments, **options):
            result = call(repo, *arguments, **options)
            if arguments[:2] == ("worktree", "add"):
                raise OSError("stopped after checkout")
            return result
        with patch.object(sync, "_git", side_effect=pause):
            return safely_sync(self.workspace, "alpha", self.run, "default")

    def test_incomplete_preparing_checkout_is_recreated_in_place(self):
        original = (self.repo / "existing.txt").read_text()
        stopped = self._pause_after_checkout()
        (Path(stopped["worktreePath"]) / "existing.txt").unlink()
        recovered = self.synchronize()
        self.assertEqual(recovered["status"], "synced", recovered)
        self.assertEqual(recovered["attemptId"], stopped["attemptId"])
        self.assertEqual((self.repo / "existing.txt").read_text(), original)
        self.assertEqual((self.repo / "B001.txt").read_text(), "B001")

    def test_preparing_resume_refreshes_new_user_edits(self):
        stopped = self._pause_after_checkout()
        (self.repo / "B001.txt").write_text("new user edit\n")
        result = self.synchronize()
        self.assertEqual(result["status"], "conflicted", result)
        self.assertNotEqual(result["attemptId"], stopped["attemptId"])
        self.assertEqual((self.repo / "B001.txt").read_text(), "new user edit\n")
        context = json.loads(Path(result["contextPath"]).read_text())
        self.assertEqual(Path(context["files"][0]["user"]).read_text(), "new user edit\n")

    def test_preparing_resume_adopts_a_merge_finished_before_its_receipt(self):
        args, _ = self.fixture.delivery()
        settle_delivery(*args)
        persist = sync._persist
        def pause(*arguments):
            if arguments[-1]["status"] == "merged":
                raise OSError("stopped after merge")
            return persist(*arguments)
        with patch.object(sync, "_persist", side_effect=pause):
            stopped = safely_sync(self.workspace, "alpha", self.run, "default")
        self.assertEqual(stopped["status"], "pending")
        with patch.object(sync, "_git", wraps=sync._git) as commands:
            recovered = self.synchronize()
        self.assertEqual(recovered["status"], "synced", recovered)
        self.assertEqual(recovered["attemptId"], stopped["attemptId"])
        self.assertFalse(any(item.args[1] == "merge" for item in commands.call_args_list))

    def test_sync_cli_records_a_pending_error_without_reopening_the_batch(self):
        args, _ = self.fixture.delivery()
        settle_delivery(*args)
        output = io.StringIO()
        with patch.object(sync, "_finish_preparation", side_effect=OSError("Git LFS \ufffd\ufffd")), redirect_stdout(output):
            code = sync.main(["sync", "--workspace", str(self.workspace), "--feature", "alpha", "--run-id", self.run,
                              "--repository-ref", "default", "--resolver-id", "resolver"])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "pending")
        self.assertEqual(self.fixture.manifest()["workspaceSync"]["default"]["error"], result["error"])
        self.assertEqual(self.fixture.manifest()["batches"]["B001"]["dispatch"]["status"], "completed")
        self.assertEqual(self.synchronize("resolver")["status"], "synced")

    def test_staged_unstaged_and_untracked_user_changes_and_backups_survive(self):
        existing = self.repo / "existing.txt"
        existing.write_text("user staged\n")
        git(self.repo, "add", "existing.txt")
        existing.write_text("user unstaged\n")
        (self.repo / "local.txt").write_text("user untracked\n")
        initial_index = self.source_index()
        result = self.publish()
        self.assertEqual(result["status"], "synced", result)
        self.assertEqual(existing.read_text(), "user unstaged\n")
        self.assertEqual((self.repo / "local.txt").read_text(), "user untracked\n")
        self.assertEqual(self.source_index(), initial_index)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)
        backup = Path(result["backupPath"])
        journal = json.loads((backup / "journal.json").read_text())
        self.assertEqual(git(self.repo, "show", journal["backupRef"] + "/index:existing.txt"), "user staged")
        self.assertEqual(git(self.repo, "show", journal["backupRef"] + "/files:existing.txt"), "user unstaged")
        self.assertEqual(git(self.repo, "show", journal["backupRef"] + "/files:local.txt"), "user untracked")
        self.assertTrue((backup / "user.index").exists())

    def test_later_batch_merges_with_user_edits_against_the_last_published_version(self):
        first = "batch=1\n" + "separator\n" * 8 + "user=0\n"
        self.assertEqual(self.publish(content=first)["status"], "synced")
        (self.repo / "shared.txt").write_text(first.replace("user=0", "user=edited"))
        second = first.replace("batch=1", "batch=2")
        # B002 starts from the latest integrated HEAD, not the edited source.
        result = self.publish("B002", second)
        self.assertEqual(result["status"], "synced", result)
        self.assertEqual((self.repo / "shared.txt").read_text(), second.replace("user=0", "user=edited"))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)

    def test_same_path_conflict_is_resolved_in_a_temporary_worktree(self):
        (self.repo / "B001.txt").write_text("user local content\n")
        initial_index = self.source_index()
        result = self.publish()
        self.assertEqual(result["status"], "conflicted", result)
        self.assertEqual((self.repo / "B001.txt").read_text(), "user local content\n")
        result = self.synchronize("resolver-1")
        candidate = Path(result["worktreePath"])
        self.assertNotEqual(candidate.resolve(), self.repo.resolve())
        (candidate / "B001.txt").write_text("B001 and user local content\n")
        completed = self.synchronize("resolver-1", result["attemptId"])
        self.assertEqual(completed["status"], "synced", completed)
        self.assertEqual((self.repo / "B001.txt").read_text(), "B001 and user local content\n")
        self.assertEqual(self.source_index(), initial_index)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)
        self.assertTrue(self.synchronize("resolver-1", result["attemptId"])["reused"])

    def test_incoming_file_does_not_overwrite_a_locally_ignored_file(self):
        (self.repo / ".gitignore").write_text("B001.txt\n")
        (self.repo / "B001.txt").write_text("ignored local content\n")
        result = self.publish()
        self.assertEqual(result["status"], "synced", result)
        self.assertEqual((self.repo / "B001.txt").read_text(), "ignored local content\n")
        self.assertIn("B001.txt", result["skippedIgnoredFiles"])
        self.assertEqual(self.publish("B002")["status"], "synced")
        self.assertEqual((self.repo / "B002.txt").read_text(), "B002")

    def test_all_git_ignore_sources_skip_tracked_and_new_files_but_keep_negations(self):
        (self.repo / ".gitignore").write_text("*.cache\ncache/*\n!cache/keep.txt\nexisting.txt\n")
        nested = self.repo / "nested"
        nested.mkdir()
        (nested / ".gitignore").write_text("private.txt\n")
        exclude = Path(git(self.repo, "rev-parse", "--git-path", "info/exclude"))
        if not exclude.is_absolute():
            exclude = self.repo / exclude
        with exclude.open("a") as stream:
            stream.write("\nlocal-only.tmp\n")
        global_ignore = self.repo.parent / "global-ignore"
        global_ignore.write_text("global-only.tmp\n")
        git(self.repo, "config", "core.excludesFile", str(global_ignore))
        legacy = self.repo / "legacy.cache"
        legacy.write_text("legacy tracked version\n")
        git(self.repo, "add", "-f", "--", legacy.name)
        git(self.repo, "commit", "-m", "legacy ignored file")
        self.head = git(self.repo, "rev-parse", "HEAD")
        skipped = ["legacy.cache", "cache/private.txt", "nested/private.txt", "local-only.tmp",
                   "global-only.tmp", "中文 缓存.cache", "换行\n缓存.cache", "existing.txt"]
        for name in skipped:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("user ignored content\n")
        initial_index = self.source_index()

        def edit(tree):
            files = skipped[:-1] + ["remote.cache", "cache/keep.txt"]
            for name in files:
                path = tree / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("batch content\n")
            git(tree, "add", "-f", "--", *files)
            git(tree, "rm", "--", "existing.txt")

        result = self.publish_extended(edit)
        self.assertEqual(result["status"], "synced", result)
        self.assertTrue(set(skipped + ["remote.cache"]) <= set(result["skippedIgnoredFiles"]))
        for name in skipped:
            self.assertEqual((self.repo / name).read_text(), "user ignored content\n")
        self.assertFalse((self.repo / "remote.cache").exists())
        self.assertEqual((self.repo / "cache/keep.txt").read_text(), "batch content\n")
        self.assertEqual((self.repo / "B001.txt").read_text(), "B001")
        self.assertEqual(self.source_index(), initial_index)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)

    def test_new_ignore_rule_refreshes_an_existing_conflict_without_overwriting_it(self):
        (self.repo / "B001.txt").write_text("user content\n")
        self.assertEqual(self.publish()["status"], "conflicted")
        conflict = self.synchronize("resolver")
        (self.repo / ".gitignore").write_text("B001.txt\n")
        refreshed = self.synchronize("resolver", conflict["attemptId"])
        self.assertEqual(refreshed["status"], "refresh_required", refreshed)
        result = self.synchronize("resolver")
        self.assertEqual(result["status"], "synced", result)
        self.assertEqual((self.repo / "B001.txt").read_text(), "user content\n")
        self.assertIn("B001.txt", result["skippedIgnoredFiles"])

    def test_ignore_rules_introduced_during_publication_skip_later_files(self):
        result = self.publish_extended(lambda tree: (tree / ".gitignore").write_text("B001.txt\n"))
        self.assertEqual(result["status"], "synced", result)
        self.assertTrue((self.repo / ".gitignore").is_file())
        self.assertFalse((self.repo / "B001.txt").exists())
        self.assertIn("B001.txt", result["skippedIgnoredFiles"])

    def test_ignore_check_errors_stop_publication_without_reopening_the_batch(self):
        import subprocess
        call = sync._git

        def failed_ignore_check(root, *args, **kwargs):
            if args[0] == "check-ignore":
                return subprocess.CompletedProcess(args, 128, b"", b"cannot read ignore rules")
            return call(root, *args, **kwargs)

        with patch.object(sync, "_git", side_effect=failed_ignore_check):
            result = self.publish()
        self.assertEqual(result["status"], "pending", result)
        self.assertIn("workspace_sync_ignore_check_failed", result["error"])
        self.assertFalse((self.repo / "B001.txt").exists())
        self.assertEqual(self.fixture.manifest()["batches"]["B001"]["status"], "merged")

    def test_new_user_edits_during_conflict_resolution_are_not_overwritten(self):
        (self.repo / "B001.txt").write_text("user v1\n")
        self.publish()
        result = self.synchronize("resolver")
        (Path(result["worktreePath"]) / "B001.txt").write_text("combined v1\n")
        (self.repo / "B001.txt").write_text("user v2\n")
        result = self.synchronize("resolver", result["attemptId"])
        self.assertEqual(result["status"], "refresh_required")
        self.assertEqual((self.repo / "B001.txt").read_text(), "user v2\n")
        refreshed = self.synchronize("resolver")
        self.assertEqual(refreshed["status"], "conflicted")
        context = json.loads(Path(refreshed["contextPath"]).read_text())
        self.assertEqual(Path(context["files"][0]["user"]).read_text(), "user v2\n")

    def test_interrupted_file_writes_resume_without_a_second_merge_or_lost_edit(self):
        args, tree = self.fixture.delivery(review=False)
        (tree / "second.txt").write_text("second\n")
        git(tree, "add", "second.txt")
        git(tree, "commit", "--amend", "--no-edit")
        from hooks.parallel_batch_scheduler import mark_batch
        from hooks.parallel_batch_stage import start_stage, complete_stage
        sha = git(tree, "rev-parse", "HEAD")
        mark_batch(self.workspace, "alpha", self.run, "B001", "sealed", commitSha=sha)
        start_stage(self.workspace, "alpha", self.run, "B001", "review")
        complete_stage(self.workspace, "alpha", self.run, "B001", "review", metadata={"batchCommit": sha})
        write = sync._write_file
        calls = []
        def interrupted(*arguments):
            calls.append(arguments[-1]["path"])
            if len(calls) == 2:
                raise OSError("interrupted publication")
            return write(*arguments)
        with patch.object(sync, "_write_file", side_effect=interrupted):
            result = settle_delivery(*args[:6])
            publication = safely_sync(self.workspace, "alpha", self.run, "default")
        self.assertEqual(result["status"], "merged")
        self.assertEqual(publication["status"], "pending")
        self.assertEqual(self.synchronize()["status"], "synced")
        self.assertEqual((self.repo / "B001.txt").read_text(), "B001")
        self.assertEqual((self.repo / "second.txt").read_text(), "second\n")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)

    def test_resolution_scope_and_old_resolvers_are_fenced(self):
        (self.repo / "B001.txt").write_text("user\n")
        self.publish()
        first = self.synchronize("old")
        current = self.synchronize("new")
        self.assertNotEqual(first["worktreePath"], current["worktreePath"])
        with self.assertRaisesRegex(ValueError, "stale_resolution"):
            self.synchronize("old", first["attemptId"])
        before = self.fixture.manifest()["workspaceSync"]["default"]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(sync.main(["complete", "--workspace", str(self.workspace), "--feature", "alpha", "--run-id", self.run,
                                        "--repository-ref", "default", "--resolver-id", "old", "--attempt-id", first["attemptId"]]), 1)
        self.assertEqual(self.fixture.manifest()["workspaceSync"]["default"], before)
        (Path(current["worktreePath"]) / "existing.txt").write_text("unauthorized\n")
        with self.assertRaisesRegex(ValueError, "outside_scope"):
            self.synchronize("new", current["attemptId"])
        self.assertEqual((self.repo / "B001.txt").read_text(), "user\n")

    def test_real_write_guard_authorizes_only_the_current_temporary_conflict_file(self):
        from hooks.frontend_route_write_guard import validate_merge_resolution_write
        (self.repo / "B001.txt").write_text("user\n")
        result = self.publish()
        candidate = Path(result["worktreePath"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(validate_merge_resolution_write(self.workspace, "alpha", candidate / "B001.txt"), 2)
        current = self.synchronize("resolver")
        self.assertEqual(validate_merge_resolution_write(self.workspace, "alpha", candidate / "B001.txt"), 0)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(validate_merge_resolution_write(self.workspace, "alpha", candidate / "existing.txt"), 2)
        replacement = self.synchronize("replacement")
        self.assertNotEqual(replacement["worktreePath"], current["worktreePath"])
        with redirect_stdout(io.StringIO()):
            self.assertNotEqual(validate_merge_resolution_write(self.workspace, "alpha", candidate / "B001.txt"), 0)

    def test_finalization_replays_publication_missed_after_a_merge(self):
        self.publish()
        args, _ = self.fixture.delivery("B002")
        settle_delivery(*args)
        self.assertFalse((self.repo / "B002.txt").exists())
        ready = prepare_finalization(self.workspace, "alpha", self.run)
        self.assertEqual(ready["workspaceSync"][0]["status"], "queued")
        self.assertFalse((self.repo / "B002.txt").exists())
        self.assertEqual(self.synchronize()["status"], "synced")
        self.assertEqual((self.repo / "B002.txt").read_text(), "B002")

    def test_user_branch_switch_is_preserved_and_recorded_without_reversing_the_batch(self):
        git(self.repo, "checkout", "-b", "user-other")
        result = self.publish()
        self.assertEqual(result["status"], "pending")
        self.assertIn("source_branch_changed", result["error"])
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD"), "user-other")
        self.assertEqual(self.fixture.manifest()["batches"]["B001"]["status"], "merged")

    def test_binary_executable_symlink_and_unicode_paths_are_published_without_committing(self):
        def edit(tree):
            (tree / "二进制 文件.bin").write_bytes(b"\x00\xff\x01\r\n")
            (tree / "run.sh").write_text("#!/bin/sh\nexit 0\n")
            os.chmod(tree / "run.sh", 0o755)
            (tree / "link").symlink_to("二进制 文件.bin")
        result = self.publish_extended(edit)
        self.assertEqual(result["status"], "synced", result)
        self.assertEqual((self.repo / "二进制 文件.bin").read_bytes(), b"\x00\xff\x01\r\n")
        self.assertTrue(os.stat(self.repo / "run.sh").st_mode & 0o100)
        self.assertEqual(os.readlink(self.repo / "link"), "二进制 文件.bin")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)

    def test_batch_rename_preserves_an_uncommitted_user_edit(self):
        (self.repo / "existing.txt").write_text("user edit before rename\n")
        result = self.publish_extended(lambda tree: git(tree, "mv", "existing.txt", "renamed.txt"))
        self.assertEqual(result["status"], "synced", result)
        self.assertFalse((self.repo / "existing.txt").exists())
        self.assertEqual((self.repo / "renamed.txt").read_text(), "user edit before rename\n")

    def test_pending_publication_can_resume_after_final_report_was_prepared(self):
        self.publish()
        args, _ = self.fixture.delivery("B002")
        settle_delivery(*args)
        from hooks.parallel_evidence_aggregate import aggregate_evidence
        aggregate_evidence(self.workspace, "alpha", self.run)
        report = prepare_promotion(self.workspace, "alpha", self.run)
        self.assertEqual(report["mainPromotion"]["deliveryMode"], "uncommitted")
        from hooks.workflow_launcher import analyze_batches
        launch = analyze_batches("alpha", plugin_path=Path(__file__).resolve().parents[1],
                                 workspace=self.workspace, task_card_id="TEST-001")
        self.assertTrue(launch["canStartWorkflow"], launch)
        self.assertEqual(launch["requiredAction"], "resume_fixed_workflow")
        from hooks.parallel_batch_scheduler import resume_run
        resumed = resume_run(self.workspace, "alpha", self.run)
        self.assertEqual(resumed["status"], "running")
        self.assertFalse(prepare_finalization(self.workspace, "alpha", self.run)["repairRequired"])
        self.assertEqual(self.synchronize()["status"], "synced")
        self.assertEqual((self.repo / "B002.txt").read_text(), "B002")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)

    def test_finished_workflow_reports_issues_without_confirmation_or_restarting(self):
        from hooks.parallel_cleanup import drain
        from hooks.parallel_evidence_aggregate import aggregate_evidence
        from hooks.workflow_launcher import analyze_batches
        initial_index = self.source_index()
        for batch_id in ("B001", "B002"):
            self.assertEqual(self.publish(batch_id)["status"], "synced")
        self.assertFalse(drain(self.workspace, "alpha", self.run)["cleanupPending"])
        # An audit finding must remain visible without reopening delivery or
        # turning the final report into a merge-confirmation request.
        evidence = Path(self.fixture.manifest()["batches"]["B001"]["stageStates"]["review"]["evidencePath"])
        evidence.unlink()
        report = aggregate_evidence(self.workspace, "alpha", self.run, finish_workflow=True)
        self.assertFalse(report["passed"])
        self.assertIn("B001.review_evidence_missing", report["errors"])
        self.assertEqual(report["status"], "succeeded_with_issues")
        manifest = self.fixture.manifest()
        self.assertTrue(manifest["workflowCompletedAt"])
        self.assertNotIn("mainPromotion", manifest)
        launch = analyze_batches("alpha", plugin_path=Path(__file__).resolve().parents[1],
                                 workspace=self.workspace, task_card_id="TEST-001")
        self.assertFalse(launch["canStartWorkflow"], launch)
        self.assertEqual(launch["requiredAction"], "code_done_ready")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)
        self.assertEqual(self.source_index(), initial_index)
        self.assertTrue((self.repo / "B001.txt").exists())
        self.assertTrue((self.repo / "B002.txt").exists())

    def test_cleanup_failure_after_retry_does_not_reopen_a_finished_workflow(self):
        from hooks.parallel_cleanup import drain
        from hooks.parallel_evidence_aggregate import aggregate_evidence
        from hooks.parallel_batch_scheduler import resume_run
        from hooks.workflow_launcher import analyze_batches
        for batch_id in ("B001", "B002"):
            self.publish(batch_id)
        tree = Path(self.fixture.manifest()["batches"]["B001"]["worktreePath"])
        local_file = tree / "retain-local-edit.txt"
        local_file.write_text("preserve")
        self.assertTrue(drain(self.workspace, "alpha", self.run)["cleanupPending"])
        self.assertTrue(drain(self.workspace, "alpha", self.run)["cleanupPending"])
        report = aggregate_evidence(self.workspace, "alpha", self.run, finish_workflow=True)
        self.assertEqual(report["status"], "succeeded_with_issues")
        before = self.fixture.manifest()["batches"]
        launch = analyze_batches("alpha", plugin_path=Path(__file__).resolve().parents[1],
                                 workspace=self.workspace, task_card_id="TEST-001")
        self.assertFalse(launch["canStartWorkflow"], launch)
        self.assertEqual(launch["requiredAction"], "code_done_ready")
        self.assertEqual(resume_run(self.workspace, "alpha", self.run)["skipped"], "terminal_run")
        self.assertEqual(self.fixture.manifest()["batches"], before)
        local_file.unlink()
        self.assertFalse(drain(self.workspace, "alpha", self.run)["cleanupPending"])
        report = aggregate_evidence(self.workspace, "alpha", self.run, finish_workflow=True)
        self.assertEqual(report["status"], "succeeded", report)
        self.assertEqual(report["errors"], [])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)

    def test_legacy_prepared_run_without_pending_jobs_resumes_to_automatic_reporting(self):
        from hooks.parallel_cleanup import drain
        from hooks.parallel_evidence_aggregate import aggregate_evidence
        from hooks.parallel_batch_scheduler import resume_run
        from hooks.workflow_launcher import analyze_batches
        for batch_id in ("B001", "B002"):
            self.publish(batch_id)
        self.assertFalse(drain(self.workspace, "alpha", self.run)["cleanupPending"])
        aggregate_evidence(self.workspace, "alpha", self.run)
        prepare_promotion(self.workspace, "alpha", self.run)
        self.assertEqual(self.fixture.manifest()["status"], "promotion_prepared")
        launch = analyze_batches("alpha", plugin_path=Path(__file__).resolve().parents[1],
                                 workspace=self.workspace, task_card_id="TEST-001")
        self.assertTrue(launch["canStartWorkflow"], launch)
        self.assertEqual(launch["requiredAction"], "resume_fixed_workflow")
        self.assertEqual(resume_run(self.workspace, "alpha", self.run)["status"], "running")
        self.assertNotIn("mainPromotion", self.fixture.manifest())
        report = aggregate_evidence(self.workspace, "alpha", self.run, finish_workflow=True)
        self.assertEqual(report["status"], "succeeded", report)
        self.assertEqual(report["errors"], [])


class LegacyRuntimeWorkspaceSyncTest(unittest.TestCase):
    def test_eight_pending_batches_recover_with_previously_committed_runtime_files(self):
        from hooks import parallel_workspace_queue as queue
        from hooks.parallel_batch_scheduler import mark_batch
        from hooks.parallel_batch_stage import start_stage, complete_stage

        fixture = fixtures.DeliveryTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        repo, workspace = fixture.repo, fixture.workspace
        runtime = repo / ".cmbdevclaw" / "state.json"
        runtime.parent.mkdir()
        runtime.write_text("legacy committed runtime\n")
        (repo / ".gitignore").write_text(".cmbdevclaw/\n")
        git(repo, "add", "--", ".gitignore")
        git(repo, "add", "-f", "--", ".cmbdevclaw/state.json")
        git(repo, "commit", "-m", "legacy runtime baseline")
        plan_path = fixture.feature / "plan.json"
        plan = json.loads(plan_path.read_text())
        plan["codeWorkspaces"] = {"default": str(repo)}
        plan_path.write_text(json.dumps(plan))
        fixture.create(8)
        head = git(repo, "rev-parse", "HEAD")
        for number in range(1, 9):
            batch_id = f"B{number:03d}"
            args, tree = fixture.delivery(batch_id, review=False)
            if number == 1:
                # Legacy deliveries can contain runtime changes, too.
                (tree / ".cmbdevclaw/state.json").write_text("batch runtime\n")
                git(tree, "add", "-f", "--", ".cmbdevclaw/state.json")
                git(tree, "commit", "--amend", "--no-edit")
                mark_batch(workspace, "alpha", fixture.run, batch_id, "sealed", commitSha=git(tree, "rev-parse", "HEAD"))
            elif number == 2:
                git(tree, "rm", "--", ".cmbdevclaw/state.json")
                git(tree, "commit", "--amend", "--no-edit")
                mark_batch(workspace, "alpha", fixture.run, batch_id, "sealed", commitSha=git(tree, "rev-parse", "HEAD"))
            start_stage(workspace, "alpha", fixture.run, batch_id, "review")
            complete_stage(workspace, "alpha", fixture.run, batch_id, "review", metadata={"batchCommit": git(tree, "rev-parse", "HEAD")})
            self.assertEqual(settle_delivery(*args[:6])["status"], "merged")

        runtime.write_text("user staged runtime\n")
        git(repo, "add", "-f", "--", ".cmbdevclaw/state.json")
        runtime.write_text("live platform runtime\n")
        ignored = runtime.parent / "new-runtime.json"
        ignored.write_text("ignored runtime\n")
        existing = repo / "existing.txt"
        existing.write_text("staged business edit\n")
        git(repo, "add", "--", "existing.txt")
        existing.write_text("unstaged business edit\n")
        index = Path(git(repo, "rev-parse", "--git-path", "index"))
        if not index.is_absolute():
            index = repo / index
        original_index = index.read_bytes()
        call = sync._git

        def old_staging(root, *args, **kwargs):
            if args[:2] == ("add", "-A"):
                args = ("add", "-A", "--", ".", ":(exclude).cmbdevclaw/**")
            return call(root, *args, **kwargs)

        # Reproduce the old add failure, including its durable queue state.
        with patch.object(sync, "_git", side_effect=old_staging), patch.object(queue.time, "time", return_value=10000000000):
            failed = queue.operate(workspace, "alpha", fixture.run, "default", "resolver")
        self.assertEqual(failed["status"], "retry_wait", failed)
        self.assertIn("workspace_sync_git_failed:add", failed["error"])
        self.assertEqual(len(failed["pendingBatchIds"]), 8)

        with patch.object(queue.time, "time", return_value=10000000010):
            for _ in range(8):
                result = queue.operate(workspace, "alpha", fixture.run, "default", "resolver")
                self.assertTrue(result["success"], result)
        self.assertEqual(result["status"], "synced", result)
        self.assertEqual(result["pendingBatchIds"], [])
        self.assertTrue(all(job["status"] == "synced" for job in result["queue"]))
        self.assertTrue(all(batch["status"] == "merged" for batch in fixture.manifest()["batches"].values()))
        for number in range(1, 9):
            batch_id = f"B{number:03d}"
            self.assertEqual((repo / f"{batch_id}.txt").read_text(), batch_id)
        self.assertEqual(runtime.read_text(), "live platform runtime\n")
        self.assertEqual(ignored.read_text(), "ignored runtime\n")
        self.assertEqual(existing.read_text(), "unstaged business edit\n")
        self.assertEqual(index.read_bytes(), original_index)
        self.assertEqual(git(repo, "rev-parse", "HEAD"), head)


if __name__ == "__main__":
    unittest.main()
