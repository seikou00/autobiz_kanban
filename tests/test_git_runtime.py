"""Exercise command-scoped compatibility against real failing Git filters."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hooks.git_runtime import git_command
from hooks.parallel_integration import git
from hooks.parallel_workspace_sync import _git as sync_git
from hooks.repository_snapshot import git_status_porcelain
from hooks.worktree_manager import _git


class GitRuntimeTest(unittest.TestCase):
    def test_disabling_bypass_preserves_windows_long_paths(self):
        with patch("hooks.git_runtime.sys.platform", "win32"), \
                patch.dict(os.environ, {"AUTOBIZ_GIT_LFS_BYPASS": "0"}):
            self.assertEqual(git_command("status"), ["git", "-c", "core.longpaths=true", "status"])
        with patch("hooks.git_runtime.sys.platform", "darwin"), \
                patch.dict(os.environ, {"AUTOBIZ_GIT_LFS_BYPASS": "0"}):
            self.assertEqual(git_command("status"), ["git", "status"])

    def test_default_bypass_covers_checkout_status_stage_commit_and_merge(self):
        with tempfile.TemporaryDirectory(prefix="git-lfs-compat-") as directory:
            root = Path(directory)
            repo = root / "source"
            repo.mkdir()
            global_config, system_config = root / "global.config", root / "system.config"
            filters = (
                '[filter "lfs"]\n'
                '\tprocess = autobiz-test-lfs-not-installed\n'
                '\tclean = autobiz-test-lfs-not-installed\n'
                '\tsmudge = autobiz-test-lfs-not-installed\n'
                '\trequired = true\n'
            )
            for config in (global_config, system_config):
                config.write_text(filters, encoding="utf-8")
            environment = {
                "GIT_CONFIG_GLOBAL": str(global_config),
                "GIT_CONFIG_SYSTEM": str(system_config),
                "GIT_CONFIG_NOSYSTEM": "0",
            }
            with patch.dict(os.environ, environment):
                os.environ.pop("AUTOBIZ_GIT_LFS_BYPASS", None)
                self.assertEqual(_git(repo, "init").returncode, 0)
                # Local configuration also exercises old Git versions that do
                # not support GIT_CONFIG_GLOBAL/GIT_CONFIG_SYSTEM overrides.
                local_config = repo / ".git" / "config"
                with local_config.open("a", encoding="utf-8") as handle:
                    handle.write(filters)
                git(repo, "config", "user.name", "Compatibility Test")
                git(repo, "config", "user.email", "compat@example.invalid")
                git(repo, "config", "core.hooksPath", str(root / "no-hooks"))
                (repo / ".gitattributes").write_text("*.dat filter=lfs\n", encoding="utf-8")
                (repo / "data.dat").write_text("ordinary content\n", encoding="utf-8")
                git(repo, "add", ".")
                git(repo, "commit", "-m", "baseline without LFS pointers")
                before = {path: path.read_bytes() for path in (global_config, system_config, local_config)}

                # Opting out restores the broken filter rather than silently
                # bypassing the user's request for normal LFS processing.
                with patch.dict(os.environ, {"AUTOBIZ_GIT_LFS_BYPASS": "0"}):
                    failed = _git(repo, "worktree", "add", "--detach", str(root / "failed"), "HEAD")
                    self.assertNotEqual(failed.returncode, 0)

                target = root / "工作树 with spaces"
                git(repo, "worktree", "add", "-b", "compat-delivery", str(target), "HEAD")
                self.assertEqual((target / "data.dat").read_text(encoding="utf-8"), "ordinary content\n")
                (target / "data.dat").write_text("updated content\n", encoding="utf-8")
                status = git_status_porcelain(target)
                self.assertEqual(status.returncode, 0, status.stderr)
                self.assertIn("data.dat", status.stdout)
                git(target, "add", "data.dat")
                git(target, "commit", "-m", "delivery")
                git(repo, "merge", "--ff-only", "compat-delivery")
                self.assertEqual((repo / "data.dat").read_text(encoding="utf-8"), "updated content\n")
                self.assertEqual(sync_git(target, "status", "--porcelain").stdout, b"")
                for path, contents in before.items():
                    self.assertEqual(path.read_bytes(), contents, str(path))
                hooks = root / "no-hooks"
                hooks.mkdir()
                hook = hooks / "pre-commit"
                hook.write_text("#!/bin/sh\necho hook-denied-compat-test >&2\nexit 1\n", encoding="utf-8")
                hook.chmod(0o755)
                denied = _git(repo, "commit", "--allow-empty", "-m", "hooks remain enabled")
                self.assertNotEqual(denied.returncode, 0)
                self.assertIn("hook-denied-compat-test", denied.stderr)


if __name__ == "__main__":
    unittest.main()
