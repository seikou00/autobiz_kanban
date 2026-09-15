from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.human_gate_design_done import (  # noqa: E402
    SYSTEM_MESSAGE,
    is_design_done_checkpoint_command,
    main,
    should_request_human_gate,
)


class HumanGateDesignDoneTest(unittest.TestCase):
    def test_detects_design_done_checkpoint_commands(self) -> None:
        for command in (
            "python hooks/update_checkpoint.py --checkpoint design_done",
            "python3 hooks/update_checkpoint.py --checkpoint=design_done",
            "python hooks/update_checkpoint.py -c design_done",
            "/bin/zsh -lc 'python hooks/update_checkpoint.py --checkpoint design_done'",
            r"python hooks\update_checkpoint.py --checkpoint design_done",
        ):
            with self.subTest(command=command):
                self.assertTrue(is_design_done_checkpoint_command(command))

    def test_ignores_other_and_dry_run_checkpoint_commands(self) -> None:
        for command in (
            "python hooks/update_checkpoint.py --checkpoint prd_done",
            "python hooks/update_checkpoint.py --checkpoint design_in_progress",
            "python hooks/update_checkpoint.py --checkpoint design_done --dry-run",
            "python hooks/other.py --checkpoint design_done",
        ):
            with self.subTest(command=command):
                self.assertFalse(is_design_done_checkpoint_command(command))

    def test_only_execute_payloads_request_the_gate(self) -> None:
        payload = {
            "tool_name": "execute",
            "tool_input": {
                "command": "python hooks/update_checkpoint.py --checkpoint design_done"
            },
        }
        self.assertTrue(should_request_human_gate(payload))
        self.assertFalse(should_request_human_gate({**payload, "tool_name": "read_file"}))

    def test_main_emits_the_human_gate_protocol_response(self) -> None:
        payload = {
            "tool_name": "execute",
            "tool_input": {
                "command": "python hooks/update_checkpoint.py --checkpoint design_done"
            },
        }
        output = io.StringIO()
        original_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            with redirect_stdout(output):
                self.assertEqual(main(), 0)
        finally:
            sys.stdin = original_stdin
        self.assertEqual(
            json.loads(output.getvalue()),
            {"decision": "human_gate", "systemMessage": SYSTEM_MESSAGE},
        )

    def test_hook_config_registers_the_design_gate_before_performance_check(self) -> None:
        config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        execute_hooks = next(
            item["hooks"] for item in config["PreToolUse"] if item.get("matcher") == "execute"
        )
        gate_index = next(
            index
            for index, hook in enumerate(execute_hooks)
            if hook.get("command") == "python hooks/human_gate_design_done.py"
        )
        self.assertEqual(execute_hooks[gate_index].get("if"), "execute(*update_checkpoint.py*)")
        self.assertTrue(execute_hooks[gate_index].get("persistAfterInterrupt"))
        self.assertLess(
            gate_index,
            next(
                index
                for index, hook in enumerate(execute_hooks)
                if hook.get("command") == "python hooks/performance_checker.py"
            ),
        )


if __name__ == "__main__":
    unittest.main()
