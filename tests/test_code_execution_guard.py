#!/usr/bin/env python3
"""Regression tests for the no-validation boundary of active Code runs."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.code_execution_guard import guard  # noqa: E402


class CodeExecutionGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.feature_dir = self.workspace / ".autobizdevops" / "features" / "alpha"
        self.code_worktree = self.root / "worktrees" / "B005"
        (self.code_worktree / "module").mkdir(parents=True)
        self._write_run(self.code_worktree, status="started")
        self.env = {
            "PLUGIN_WORKSPACE": str(self.root),
            "PROJECT_DIR": "workspace",
            "FEATURE_ID": "alpha",
        }

    def _write_run(self, code_workspace: Path, *, status: str) -> None:
        path = self.feature_dir / ".task-runs" / "T007" / "run-007.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "taskId": "T007",
                "runId": "run-007",
                "status": status,
                "codeWorkspace": str(code_workspace),
                "requestedCodeWorkspaces": [str(code_workspace / "module")],
                "resolvedGitRoots": [str(code_workspace)],
            }),
            encoding="utf-8",
        )

    def _write_parallel_batch(self, *, review_status: str, test_status: str, active_stage: str) -> None:
        manifest = self.feature_dir / ".parallel-runs" / "cw-007" / "manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps({
                "runId": "cw-007",
                "batches": {
                    "B005": {
                        "worktreePath": str(self.code_worktree),
                        "activeStage": active_stage,
                        "stageStates": {
                            "review": {"status": review_status},
                            "test": {"status": test_status},
                        },
                    },
                },
            }),
            encoding="utf-8",
        )

    def _execute(self, command: str, **tool_input: str) -> str | None:
        return guard({
            "tool_name": "execute",
            "tool_input": {"command": command, **tool_input},
        })

    def test_blocks_maven_compile_in_active_task_worktree(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            reason = self._execute('cd "{}" && mvn compile -DskipTests'.format(self.code_worktree / "module"))
        self.assertIn("CODE_EXECUTION_VALIDATION_FORBIDDEN", reason)
        self.assertIn("T007:run-007", reason)

    def test_blocks_windows_worktree_command_used_by_batch_agents(self) -> None:
        windows_worktree = r"D:\autobiz-test\simply\.autobizdevops\worktrees\cw-1\B005"
        run_path = self.feature_dir / ".task-runs" / "T007" / "run-007.json"
        run_path.write_text(
            json.dumps({
                "taskId": "T007",
                "runId": "run-007",
                "status": "started",
                "codeWorkspace": windows_worktree,
            }),
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, self.env, clear=False):
            reason = self._execute(
                'cd "{}\\后台服务\\模块" && mvn compile -DskipTests -o'.format(windows_worktree)
            )
        self.assertIn("CODE_EXECUTION_VALIDATION_FORBIDDEN", reason)

    def test_guard_is_registered_before_execute_commands(self) -> None:
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        commands = [
            hook.get("command")
            for registration in hooks.get("PreToolUse", [])
            if registration.get("matcher") == "execute"
            for hook in registration.get("hooks", [])
        ]
        self.assertIn("python hooks/code_execution_guard.py", commands)

    def test_legacy_code_compile_hook_is_removed(self) -> None:
        state_checkpoint = (ROOT / "hooks" / "state_checkpoint.py").read_text(encoding="utf-8")
        self.assertNotIn("run_code_compile", state_checkpoint)
        self.assertNotIn('"code-compile"', state_checkpoint)

    def test_blocks_build_typecheck_lint_and_test_entrypoints(self) -> None:
        commands = [
            "gradlew assemble",
            "npm run build",
            "pnpm typecheck",
            "yarn lint",
            "npx vitest run",
        ]
        with mock.patch.dict(os.environ, self.env, clear=False):
            for command in commands:
                with self.subTest(command=command):
                    reason = self._execute(command, cwd=str(self.code_worktree / "module"))
                    self.assertIn("CODE_EXECUTION_VALIDATION_FORBIDDEN", reason)

    def test_allows_validation_in_another_worktree(self) -> None:
        utest_worktree = self.root / "worktrees" / "B001"
        utest_worktree.mkdir(parents=True)
        with mock.patch.dict(os.environ, self.env, clear=False):
            reason = self._execute('cd "{}" && mvn test'.format(utest_worktree))
        self.assertIsNone(reason)

    def test_allows_validation_after_task_run_is_finished(self) -> None:
        self._write_run(self.code_worktree, status="implemented")
        with mock.patch.dict(os.environ, self.env, clear=False):
            reason = self._execute("mvn compile", cwd=str(self.code_worktree / "module"))
        self.assertIsNone(reason)

    def test_blocks_compile_during_review_after_code_task_has_finished(self) -> None:
        self._write_run(self.code_worktree, status="implemented")
        self._write_parallel_batch(review_status="running", test_status="pending", active_stage="review")
        with mock.patch.dict(os.environ, self.env, clear=False):
            reason = self._execute("mvn compile", cwd=str(self.code_worktree / "module"))
        self.assertIn("BATCH_STAGE_VALIDATION_FORBIDDEN", reason)
        self.assertIn("cw-007:B005", reason)

    def test_allows_compile_only_while_review_passed_test_is_running(self) -> None:
        self._write_run(self.code_worktree, status="implemented")
        self._write_parallel_batch(review_status="passed", test_status="running", active_stage="test")
        with mock.patch.dict(os.environ, self.env, clear=False):
            reason = self._execute("mvn test", cwd=str(self.code_worktree / "module"))
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
