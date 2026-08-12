from __future__ import annotations

import ast
import unittest
from pathlib import Path

from hooks.check_state_read import (
    HOOK_LOG,
    STATE_JSON,
    STATE_JSON_PATH_SUFFIX,
    STATE_MD,
    STATE_MD_PATH_SUFFIX,
    block_reason,
    blocked_read_target,
    command_read_target,
    payload_read_target,
)


class BlockedReadTargetTest(unittest.TestCase):
    def test_blocks_hook_log_by_filename(self) -> None:
        for path in (
            ".autobizdevops/features/demo-feature/hooks.ndjson",
            "/abs/ws/proj/.autobizdevops/features/demo/hooks.ndjson",
            "/abs/ws/proj/.autobizdevops/archive/demo-iter1/hooks.ndjson",
            r".autobizdevops\features\demo\hooks.ndjson",
            "file:///abs/ws/proj/.autobizdevops/features/demo/hooks.ndjson",
            "hooks.ndjson",
        ):
            with self.subTest(path=path):
                self.assertEqual(blocked_read_target(path), HOOK_LOG)

    def test_blocks_state_json_by_path_suffix(self) -> None:
        for path in (
            ".autobizdevops/state.json",
            "/abs/ws/proj/.autobizdevops/state.json",
            r"proj\.autobizdevops\state.json",
            "file:///abs/ws/proj/.autobizdevops/state.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(blocked_read_target(path), STATE_JSON)

    def test_blocks_state_md_by_path_suffix(self) -> None:
        for path in (
            ".autobizdevops/STATE.md",
            "/abs/ws/proj/.autobizdevops/STATE.md",
        ):
            with self.subTest(path=path):
                self.assertEqual(blocked_read_target(path), STATE_MD)

    def test_ignores_unrelated_files(self) -> None:
        # state.json 按路径后缀匹配，项目里其它同名文件不受影响。
        for path in (
            "",
            "src/state.json",
            "frontend/redux/state.json",
            "docs/STATE.md",
            "hooks/hooks.json",
            "hooks.ndjson.bak",
            ".autobizdevops/features/demo/PRD.md",
        ):
            with self.subTest(path=path):
                self.assertIsNone(blocked_read_target(path))


class CommandReadTargetTest(unittest.TestCase):
    def test_blocks_reader_commands(self) -> None:
        cases = {
            "cat .autobizdevops/features/demo/hooks.ndjson": HOOK_LOG,
            "tail -n 5 /ws/proj/.autobizdevops/features/demo/hooks.ndjson": HOOK_LOG,
            "cat .autobizdevops/state.json": STATE_JSON,
            'jq ".features" .autobizdevops/state.json': STATE_JSON,
            "head -20 .autobizdevops/STATE.md": STATE_MD,
            "ls -la && cat .autobizdevops/state.json": STATE_JSON,
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(command_read_target(command), expected)

    def test_allows_search_and_maintenance_commands(self) -> None:
        # 维护本仓库时按字面量检索不应被误伤。
        for command in (
            'grep -rn "hooks.ndjson" --include="*.py" .',
            "rg state.json hooks/",
            "find . -name hooks.ndjson",
            "ls .autobizdevops/",
            "python hooks/update_checkpoint.py --checkpoint code_done",
            'python read_state_json.py --feature "demo"',
        ):
            with self.subTest(command=command):
                self.assertIsNone(command_read_target(command))


class PayloadReadTargetTest(unittest.TestCase):
    def test_blocks_read_file_hook_log(self) -> None:
        payload = {
            "tool_name": "read_file",
            "tool_input": {"file_path": ".autobizdevops/features/demo/hooks.ndjson"},
        }
        self.assertEqual(payload_read_target(payload), HOOK_LOG)

    def test_blocks_read_file_state_json(self) -> None:
        payload = {
            "tool_name": "read_file",
            "tool_input": {"file_path": ".autobizdevops/state.json"},
        }
        self.assertEqual(payload_read_target(payload), STATE_JSON)

    def test_blocks_alternate_path_keys(self) -> None:
        for key in ("filePath", "path", "absolutePath"):
            with self.subTest(key=key):
                payload = {"tool_input": {key: "/ws/.autobizdevops/state.json"}}
                self.assertEqual(payload_read_target(payload), STATE_JSON)

    def test_blocks_execute_payload(self) -> None:
        payload = {
            "tool_name": "execute",
            "tool_input": {"command": "cat .autobizdevops/state.json"},
        }
        self.assertEqual(payload_read_target(payload), STATE_JSON)

    def test_allows_unrelated_read(self) -> None:
        payload = {
            "tool_name": "read_file",
            "tool_input": {"file_path": ".autobizdevops/features/demo/PRD.md"},
        }
        self.assertIsNone(payload_read_target(payload))

    def test_tolerates_missing_or_odd_input(self) -> None:
        for payload in ({}, {"tool_input": None}, {"tool_input": {}}):
            with self.subTest(payload=payload):
                self.assertIsNone(payload_read_target(payload))


class BlockReasonTest(unittest.TestCase):
    def test_every_target_points_back_to_script(self) -> None:
        for target in (HOOK_LOG, STATE_JSON, STATE_MD):
            with self.subTest(target=target):
                reason = block_reason(target)
                self.assertIn("read_state_json.py", reason)
                self.assertIn('--feature "${feature}"', reason)


class SuffixDriftTest(unittest.TestCase):
    """守卫为保持轻量复制了路径后缀规则，此处断言与 state_checkpoint.py 一致。

    用 ast 读源码而不是 import：守卫刻意不依赖 state_checkpoint 的
    board_core 等重依赖，测试也不应把它们拖进来。
    """

    @staticmethod
    def _module_constant(name: str) -> tuple[str, ...]:
        source = (
            Path(__file__).resolve().parents[1] / "hooks" / "state_checkpoint.py"
        ).read_text(encoding="utf-8")
        for node in ast.parse(source).body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
        raise AssertionError(f"state_checkpoint.py 中未找到常量 {name}")

    def test_suffixes_match_state_checkpoint(self) -> None:
        self.assertEqual(
            STATE_JSON_PATH_SUFFIX,
            tuple(part.lower() for part in self._module_constant("STATE_JSON_PATH_SUFFIX")),
        )
        self.assertEqual(
            STATE_MD_PATH_SUFFIX,
            tuple(part.lower() for part in self._module_constant("STATE_PATH_SUFFIX")),
        )


if __name__ == "__main__":
    unittest.main()
