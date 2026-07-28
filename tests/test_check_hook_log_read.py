from __future__ import annotations

import unittest

from hooks.check_hook_log_read import (
    command_reads_hook_log,
    is_hook_log_path,
    payload_reads_hook_log,
)


class IsHookLogPathTest(unittest.TestCase):
    def test_matches_feature_and_archive_copies(self) -> None:
        for path in (
            ".autobizdevops/features/demo-feature/hooks.ndjson",
            "/abs/ws/proj/.autobizdevops/features/demo/hooks.ndjson",
            "/abs/ws/proj/.autobizdevops/archive/demo-iter1/hooks.ndjson",
            r".autobizdevops\features\demo\hooks.ndjson",
            "file:///abs/ws/proj/.autobizdevops/features/demo/hooks.ndjson",
            "hooks.ndjson",
        ):
            with self.subTest(path=path):
                self.assertTrue(is_hook_log_path(path))

    def test_ignores_other_files(self) -> None:
        for path in (
            "",
            ".autobizdevops/state.json",
            "hooks/hooks.json",
            "hooks.ndjson.bak",
            "my_hooks.ndjson.md",
            ".autobizdevops/features/demo/PRD.md",
        ):
            with self.subTest(path=path):
                self.assertFalse(is_hook_log_path(path))


class CommandReadsHookLogTest(unittest.TestCase):
    def test_blocks_reader_commands(self) -> None:
        for command in (
            "cat .autobizdevops/features/demo/hooks.ndjson",
            "tail -n 5 /ws/proj/.autobizdevops/features/demo/hooks.ndjson",
            "head -1 hooks.ndjson",
            'jq -s ".[-1]" .autobizdevops/features/demo/hooks.ndjson',
            "ls -la && cat .autobizdevops/features/demo/hooks.ndjson",
        ):
            with self.subTest(command=command):
                self.assertTrue(command_reads_hook_log(command))

    def test_allows_search_and_maintenance_commands(self) -> None:
        # 维护本仓库时按字面量检索不应被误伤。
        for command in (
            'grep -rn "hooks.ndjson" --include="*.py" .',
            "rg hooks.ndjson hooks/",
            "find . -name hooks.ndjson",
            "ls .autobizdevops/features/demo/",
            "python hooks/update_checkpoint.py --checkpoint code_done",
        ):
            with self.subTest(command=command):
                self.assertFalse(command_reads_hook_log(command))


class PayloadReadsHookLogTest(unittest.TestCase):
    def test_blocks_read_file_payload(self) -> None:
        payload = {
            "tool_name": "read_file",
            "tool_input": {"file_path": ".autobizdevops/features/demo/hooks.ndjson"},
        }
        self.assertTrue(payload_reads_hook_log(payload))

    def test_blocks_alternate_path_keys(self) -> None:
        for key in ("filePath", "path", "absolutePath"):
            with self.subTest(key=key):
                payload = {"tool_input": {key: "/ws/.autobizdevops/features/d/hooks.ndjson"}}
                self.assertTrue(payload_reads_hook_log(payload))

    def test_blocks_execute_payload(self) -> None:
        payload = {
            "tool_name": "execute",
            "tool_input": {"command": "cat .autobizdevops/features/demo/hooks.ndjson"},
        }
        self.assertTrue(payload_reads_hook_log(payload))

    def test_allows_state_json_read(self) -> None:
        payload = {
            "tool_name": "read_file",
            "tool_input": {"file_path": ".autobizdevops/state.json"},
        }
        self.assertFalse(payload_reads_hook_log(payload))

    def test_tolerates_missing_or_odd_input(self) -> None:
        for payload in ({}, {"tool_input": None}, {"tool_input": {}}):
            with self.subTest(payload=payload):
                self.assertFalse(payload_reads_hook_log(payload))


if __name__ == "__main__":
    unittest.main()
