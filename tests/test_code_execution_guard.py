#!/usr/bin/env python3
"""Regression tests for the no-validation boundary of active Code runs."""

from __future__ import annotations

import json
import os
import subprocess
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
                "parallelRunId": "cw-007",
                "batchId": "B005",
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

    def test_allows_maven_compile_in_active_task_worktree(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            reason = self._execute('cd "{}" && mvn compile -DskipTests'.format(self.code_worktree / "module"))
        self.assertIsNone(reason)

    def test_allows_windows_worktree_command_used_by_batch_agents(self) -> None:
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
        self.assertIsNone(reason)

    def test_guard_is_registered_before_execute_commands(self) -> None:
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        commands = [
            hook.get("command")
            for registration in hooks.get("PreToolUse", [])
            if registration.get("matcher") == "execute"
            for hook in registration.get("hooks", [])
        ]
        self.assertIn("python -X utf8 hooks/code_execution_guard.py", commands)

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

    def test_allows_compile_output_forms_before_stage_registration(self) -> None:
        self._write_parallel_batch(review_status="pending", test_status="pending", active_stage="")
        module = self.code_worktree / "module"
        commands = [
            "mvn clean compile -DskipTests 2>&1 | tail -100",
            f'cd "{module}" && mvn clean compile -DskipTests -Dmaven.test.skip=true 2>&1 | tail -80',
            'mvn compile > "compile log.txt" 2>&1',
            'mvn compile 2> errors.log | head -n 5',
            'mvn compile 2>&1 | tee -a "$COMPILE_LOG" | tail -n 100',
            'set -o pipefail && mvn compile 2>&1 | tee "compile log.txt"',
            f'set -o pipefail\ncd "{module}" && mvn compile 2>&1 | tee compile.log',
            "bash -lc 'mvn clean compile -DskipTests 2>&1 | tail -100'",
            'mvn -f "pom.xml" -s "settings.xml" -P production compile -DskipTests',
            'mvn compile -Dmessage="hello world"',
            'mvn compile &> compile.log',
            'mvn compile>>compile.log 2>&1',
            'gradlew compileJava 2>&1 | tee compile.log',
            'go build ./... 2>&1 | tail -100',
            'cargo check 2>&1 | head -5',
        ]
        with mock.patch.dict(os.environ, self.env, clear=False):
            for command in commands:
                with self.subTest(command=command):
                    self.assertIsNone(self._execute(command, cwd=str(module)))

    def test_allows_compile_during_active_repair(self) -> None:
        self._write_run(self.code_worktree, status="implementation_recording")
        self._write_parallel_batch(review_status="pending", test_status="pending", active_stage="implement")
        with mock.patch.dict(os.environ, self.env, clear=False):
            self.assertIsNone(self._execute("mvn compile 2>&1 | tee compile.log", cwd=str(self.code_worktree)))

    def test_allows_version_and_help_queries_without_active_code_run(self) -> None:
        self._write_run(self.code_worktree, status="implemented")
        self._write_parallel_batch(review_status="running", test_status="pending", active_stage="review")
        commands = [
            "mvn -v 2>&1 | head -5", "mvn --version", "mvn --help",
            "gradlew --version", "cargo --version", "which mvn 2>/dev/null",
            "command -v mvn", "type -a mvn", "where mvn",
        ]
        with mock.patch.dict(os.environ, self.env, clear=False):
            for command in commands:
                with self.subTest(command=command):
                    self.assertIsNone(self._execute(command, cwd=str(self.code_worktree)))

    def test_does_not_allow_validation_hidden_in_shell_forms(self) -> None:
        self._write_parallel_batch(review_status="pending", test_status="pending", active_stage="")
        commands = [
            'mvn compile && mvn test', 'mvn compile; mvn test',
            'mvn compile\nmvn test', 'mvn compile || mvn test',
            'mvn compile | mvn test', 'mvn compile | sh',
            'mvn compile | tee log && npm test',
            'mvn compile $(mvn test)', 'mvn compile `mvn test`',
            'mvn compile | tee >(mvn test)', 'mvn compile < <(mvn test)',
            'mvn compile &', 'nohup mvn compile',
            "mvn compile '>' test", "mvn compile '2>&1' test",
            'mvn compile >', 'mvn compile |', 'mvn compile | tail -100 test',
            'mvn compile test -DskipTests', 'mvn clean package -Dmaven.test.skip=true',
            'mvn compile verify', 'mvn compile $EXTRA_GOALS',
            'mvn -v test', 'mvn -v && mvn test', 'which mvn && mvn test',
            'command mvn test', 'gradlew compileJava test',
            'cargo check --all-targets test', 'go build && go test ./...',
        ]
        with mock.patch.dict(os.environ, self.env, clear=False):
            for command in commands:
                with self.subTest(command=command):
                    self.assertIn("BATCH_STAGE_VALIDATION_FORBIDDEN", self._execute(command, cwd=str(self.code_worktree)))

    def test_stale_active_run_cannot_allow_compile_during_review(self) -> None:
        self._write_parallel_batch(review_status="running", test_status="pending", active_stage="review")
        with mock.patch.dict(os.environ, self.env, clear=False):
            for command in ['mvn compile', 'mvn compile 2>&1 | tee compile.log']:
                self.assertIn("BATCH_STAGE_VALIDATION_FORBIDDEN", self._execute(command, cwd=str(self.code_worktree)))

    def test_review_status_blocks_compile_even_when_active_stage_missing(self) -> None:
        self._write_parallel_batch(review_status="running", test_status="pending", active_stage="")
        with mock.patch.dict(os.environ, self.env, clear=False):
            self.assertIn("BATCH_STAGE_VALIDATION_FORBIDDEN", self._execute("mvn compile", cwd=str(self.code_worktree)))

    def test_compile_requires_current_batch_run_binding(self) -> None:
        self._write_parallel_batch(review_status="pending", test_status="pending", active_stage="")
        path = self.feature_dir / ".task-runs" / "T007" / "run-007.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        with mock.patch.dict(os.environ, self.env, clear=False):
            for field, value, check in [
                ("parallelRunId", "old-run", "TASK_BATCH_BINDING_MISMATCH"),
                ("batchId", "B001", "TASK_BATCH_BINDING_MISMATCH"),
                ("status", "implemented", "TASK_RUN_INACTIVE"),
            ]:
                with self.subTest(field=field):
                    path.write_text(json.dumps({**original, field: value}), encoding="utf-8")
                    reason = self._execute("mvn compile", cwd=str(self.code_worktree))
                    details = json.loads(reason.split("GUARD_DIAGNOSTICS: ", 1)[1])
                    self.assertEqual(details["guardVersion"], "code-stage-v2")
                    self.assertIn(check, details["failedChecks"])
                    self.assertEqual(details["commandKind"], "production_compile")
                    self.assertEqual(details["batch"]["stage"], "none")

    def test_workspace_mismatch_diagnostic_includes_actual_task_paths(self) -> None:
        self._write_parallel_batch(review_status="pending", test_status="pending", active_stage="")
        path = self.feature_dir / ".task-runs" / "T007" / "run-007.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state.update(codeWorkspace=str(self.root / 'different'), requestedCodeWorkspaces=[], resolvedGitRoots=[])
        path.write_text(json.dumps(state), encoding="utf-8")
        with mock.patch.dict(os.environ, self.env, clear=False):
            reason = self._execute("mvn compile", cwd=str(self.code_worktree))
        details = json.loads(reason.split("GUARD_DIAGNOSTICS: ", 1)[1])
        self.assertIn("TASK_WORKSPACE_MISMATCH", details["failedChecks"])
        self.assertEqual(details["taskRuns"][0]["codeWorkspace"], str(self.root / 'different'))
        self.assertEqual(details["featureDir"], str(self.feature_dir.resolve()))

    def test_windows_utf8_bom_runtime_files_do_not_hide_code_authority(self) -> None:
        self._write_parallel_batch(review_status="pending", test_status="pending", active_stage="")
        paths = [
            self.feature_dir / '.task-runs' / 'T007' / 'run-007.json',
            self.feature_dir / '.parallel-runs' / 'cw-007' / 'manifest.json',
        ]
        for path in paths:
            source = path.read_text(encoding="utf-8")
            path.write_text(source, encoding="utf-8-sig")
        with mock.patch.dict(os.environ, self.env, clear=False):
            self.assertIsNone(self._execute("mvn clean compile 2>&1 | tail -100", cwd=str(self.code_worktree)))

    def test_blocked_batch_cannot_inherit_stale_active_code_authority(self) -> None:
        self._write_parallel_batch(review_status="pending", test_status="pending", active_stage="implement")
        path = self.feature_dir / '.parallel-runs' / 'cw-007' / 'manifest.json'
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest['batches']['B005']['status'] = 'blocked'
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with mock.patch.dict(os.environ, self.env, clear=False):
            reason = self._execute("mvn compile", cwd=str(self.code_worktree))
        self.assertIn("BATCH_NOT_EXECUTABLE", reason)

    def test_completed_implementation_does_not_inherit_stale_compile_authority(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            for stage in ["", "implement"]:
                for status in ["passed", "skipped", "deferred", "needs_triage"]:
                    with self.subTest(stage=stage, status=status):
                        self._write_parallel_batch(review_status="passed", test_status="pending", active_stage=stage)
                        path = self.feature_dir / ".parallel-runs" / "cw-007" / "manifest.json"
                        manifest = json.loads(path.read_text(encoding="utf-8"))
                        manifest["batches"]["B005"]["stageStates"]["implement"] = {"status": status}
                        path.write_text(json.dumps(manifest), encoding="utf-8")
                        self.assertIn("IMPLEMENTATION_ALREADY_FINISHED", self._execute("mvn compile", cwd=str(self.code_worktree)))

    def test_stale_test_status_does_not_authorize_other_stages(self) -> None:
        self._write_parallel_batch(review_status="passed", test_status="running", active_stage="review")
        with mock.patch.dict(os.environ, self.env, clear=False):
            self.assertIn("BATCH_STAGE_VALIDATION_FORBIDDEN", self._execute("mvn test", cwd=str(self.code_worktree)))

    def test_effective_directory_must_match_active_task(self) -> None:
        other = self.root / "worktrees" / "B001"
        other.mkdir(parents=True)
        self._write_parallel_batch(review_status="pending", test_status="pending", active_stage="")
        path = self.feature_dir / ".parallel-runs" / "cw-007" / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["batches"]["B001"] = {"worktreePath": str(other), "activeStage": None}
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with mock.patch.dict(os.environ, self.env, clear=False):
            commands = [
                f'cd "{other}" && mvn compile',
                f'mvn compile -f "{self.code_worktree}/pom.xml"',
                f'mvn compile > "{self.code_worktree}/compile.log"',
            ]
            for command in commands:
                with self.subTest(command=command):
                    self.assertIn("BATCH_STAGE_VALIDATION_FORBIDDEN", self._execute(command, cwd=str(other)))
            # Explicit cd takes precedence over the tool's initial cwd.
            self.assertIn("BATCH_STAGE_VALIDATION_FORBIDDEN", self._execute(commands[0], cwd=str(self.code_worktree)))
            self.assertIsNone(self._execute('cd module && mvn compile', cwd=str(self.code_worktree)))

    def test_windows_and_git_bash_paths_identify_same_workspace(self) -> None:
        windows = r'D:\autobiz-test\工作管理\B005'
        self._write_run(Path(windows), status="started")
        commands = [
            ('mvn clean compile 2>&1 | tail -100', windows + r'\后台服务'),
            ('mvn clean compile', '/d/autobiz-test/工作管理/B005/后台服务'),
            (r'"C:\Program Files\Maven\bin\mvn.cmd" compile', windows),
        ]
        with mock.patch.dict(os.environ, self.env, clear=False):
            for command, cwd in commands:
                with self.subTest(command=command, cwd=cwd):
                    self.assertIsNone(self._execute(command, cwd=cwd))

    def test_hook_process_reports_real_allow_and_block_exit_codes(self) -> None:
        self._write_parallel_batch(review_status="pending", test_status="pending", active_stage="")
        with mock.patch.dict(os.environ, self.env, clear=False):
            for command, expected in [('mvn compile 2>&1 | tail -100', 0), ('mvn compile && mvn test', 2)]:
                with self.subTest(command=command):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / 'hooks' / 'code_execution_guard.py')],
                        input=json.dumps({'tool_name': 'execute', 'tool_input': {'command': command, 'cwd': str(self.code_worktree)}}),
                        text=True, capture_output=True, env=os.environ.copy(),
                    )
                    self.assertEqual(expected, result.returncode, result.stderr)
                    if expected:
                        self.assertIn('BATCH_STAGE_VALIDATION_FORBIDDEN', result.stdout)


if __name__ == "__main__":
    unittest.main()
