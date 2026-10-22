"""Worktree registry parsing across Git's NUL and older line formats."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from hooks.parallel_integration import _target_worktree


class GitWorktreeCompatibilityTest(unittest.TestCase):
    def test_legacy_format_keeps_windows_unicode_spaces_and_multiple_records(self):
        output = (
            'worktree D:/仓库/客户 拜访\r\nHEAD ' + 'a' * 40 + '\r\nbranch refs/heads/release_gray_0903\r\n\r\n'
            'worktree D:/仓库/候选\r\nHEAD ' + 'b' * 40 + '\r\ndetached\r\n\r\n'
        ).encode()
        with patch("subprocess.run", side_effect=[
            subprocess.CompletedProcess([], 129, b"", b"error: unknown switch `z'"),
            subprocess.CompletedProcess([], 0, output, b""),
        ]) as run:
            self.assertEqual(_target_worktree(Path("/repo"), "release_gray_0903"), Path("D:/仓库/客户 拜访"))
        self.assertEqual(run.call_count, 2)
        self.assertNotIn("-z", run.call_args.args[0])

    def test_nul_format_preserves_quotes_tabs_and_newlines(self):
        path = '/repo/客户 "quoted"\tline\nnext\rreturn '
        output = (f'worktree {path}\0HEAD ' + 'a' * 40 + '\0branch refs/heads/main\0\0').encode()
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, b"")) as run:
            self.assertEqual(_target_worktree(Path("/repo"), "main"), Path(path))
        self.assertEqual(run.call_count, 1)

    def test_branch_not_checked_out_is_the_only_normal_none_result(self):
        output = ('worktree /repo\0HEAD ' + 'a' * 40 + '\0branch refs/heads/main\0\0').encode()
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, b"")):
            self.assertIsNone(_target_worktree(Path("/repo"), "main-other"))

    def test_git_errors_are_not_treated_as_an_unchecked_out_branch(self):
        for responses in (
            [subprocess.CompletedProcess([], 128, b"", b"fatal: not a git repository")],
            [subprocess.CompletedProcess([], 129, b"", b"unknown switch `z'"),
             subprocess.CompletedProcess([], 128, b"", b"fatal: access denied")],
        ):
            with self.subTest(responses=responses), patch("subprocess.run", side_effect=responses):
                with self.assertRaisesRegex(ValueError, "worktree_list_failed"):
                    _target_worktree(Path("/repo"), "main")

    def test_ambiguous_legacy_output_is_rejected(self):
        output = ('worktree /repo/path\nunexpected path continuation\nHEAD ' + 'a' * 40 + '\nbranch refs/heads/main\n\n').encode()
        with patch("subprocess.run", side_effect=[
            subprocess.CompletedProcess([], 129, b"", b"unknown switch `z'"),
            subprocess.CompletedProcess([], 0, output, b""),
        ]):
            with self.assertRaisesRegex(ValueError, "worktree_list_invalid"):
                _target_worktree(Path("/repo"), "main")
