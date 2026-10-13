"""Compile recording/completion tests, including real Bash pipeline failures."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from hooks import task_runner as runner
from hooks.code_shell_analysis import analyze_script
from hooks.evidence_store import read_records, stream_path, EvidenceStoreError
from tests.test_task_runner import _workspace, _start, _run, _configure_frontend_without_batch_compile


class CodeCompileResultTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="compile-result-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace, self.feature_dir, self.code = _workspace(self.root)
        self.state = _start(self.workspace, self.code)
        (self.code / "production.txt").write_text("production\n", encoding="utf-8")
        self.log = self.root / "compile.log"
        self.log.write_text("compiler output\n", encoding="utf-8")

    def prepare(self, argv=None, cwd=None):
        return runner.prepare_compile(self.workspace, "alpha", "T001", self.code, self.state["runId"],
                                      argv=argv or ["mvn", "compile", "-o"], execution_cwd=cwd or self.code)

    def record(self, attempt, *, exit_code=0, log_exit_code=0, output=None):
        return runner.record_compile(self.workspace, "alpha", "T001", self.code, self.state["runId"],
                                     attempt_id=attempt["attemptId"], exit_code=exit_code,
                                     log_exit_code=log_exit_code, output_file=output or self.log)

    def finish(self):
        return runner.finish_implementation(self.workspace, "alpha", "T001", self.code, self.state["runId"],
                                            no_code_change_why=None, supporting_files=[])

    def test_missing_failed_and_stale_compile_prevent_completion(self):
        with self.assertRaisesRegex(runner.TaskRunnerError, "production_compile_result_missing"):
            self.finish()
        failed = self.prepare()
        result = self.record(failed, exit_code=7)
        self.assertFalse(result["passed"])
        with self.assertRaisesRegex(runner.TaskRunnerError, "production_compile_failed"):
            self.finish()
        passed = self.prepare()
        self.record(passed)
        (self.code / "production.txt").write_text("new production\n", encoding="utf-8")
        with self.assertRaisesRegex(runner.TaskRunnerError, "production_compile_result_stale"):
            self.finish()
        fresh = self.prepare()
        self.record(fresh)
        self.assertTrue(self.finish()[0])

    def test_log_failure_missing_or_changed_log_prevent_completion(self):
        result = self.record(self.prepare(), log_exit_code=1)
        self.assertFalse(result["passed"])
        with self.assertRaisesRegex(runner.TaskRunnerError, "production_compile_failed"):
            self.finish()
        result = self.record(self.prepare())
        saved_log = self.feature_dir / result["logPath"]
        saved_log.write_text("overwritten", encoding="utf-8")
        with self.assertRaisesRegex(runner.TaskRunnerError, "production_compile_log_changed"):
            self.finish()
        saved_log.unlink()
        with self.assertRaisesRegex(runner.TaskRunnerError, "production_compile_log_missing"):
            self.finish()

    def test_source_changes_during_compilation_cannot_be_recorded_as_fresh(self):
        attempt = self.prepare()
        (self.code / "production.txt").write_text("changed during compiler\n", encoding="utf-8")
        with self.assertRaisesRegex(runner.TaskRunnerError, "compile_sources_changed"):
            self.record(attempt)

    def test_registration_retry_is_idempotent_and_preserves_full_output(self):
        self.log.write_bytes(b"output line\n" * 30000)
        attempt = self.prepare()
        with mock.patch.object(runner, "append_evidence", side_effect=EvidenceStoreError("unavailable")):
            with self.assertRaisesRegex(runner.TaskRunnerError, "compile_evidence_registration_failed") as error:
                self.record(attempt)
        self.assertIn("只重试 record-compile", error.exception.details["reason"])
        result = self.record(attempt)
        self.assertEqual((self.feature_dir / result["logPath"]).read_bytes(), self.log.read_bytes())
        self.assertEqual(self.record(attempt)["evidenceId"], result["evidenceId"])
        records = read_records(stream_path(self.feature_dir))
        self.assertEqual(sum(record.get("action") == "production_compile" for record in records), 1)
        self.assertFalse((self.feature_dir / "UNIT_TEST_RESULT.json").exists())
        with self.assertRaisesRegex(runner.TaskRunnerError, "compile_result_conflict"):
            self.record(attempt, exit_code=1)

    def test_result_registration_recovers_after_evidence_written_but_state_save_failed(self):
        attempt = self.prepare()
        with mock.patch.object(runner, "_save_run", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.record(attempt)
        result = self.record(attempt)
        records = read_records(stream_path(self.feature_dir))
        self.assertEqual(sum(record.get("action") == "production_compile" for record in records), 1)
        self.assertTrue(result["passed"])

    def test_conflicting_retry_does_not_overwrite_the_existing_evidence_log(self):
        attempt = self.prepare()
        original_output = self.log.read_bytes()
        with mock.patch.object(runner, "_save_run", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.record(attempt)
        evidence = next(record for record in read_records(stream_path(self.feature_dir))
                        if record.get("action") == "production_compile")
        self.log.write_text("different output", encoding="utf-8")
        with self.assertRaisesRegex(runner.TaskRunnerError, "compile_result_conflict"):
            self.record(attempt)
        self.assertEqual((self.feature_dir / evidence["compile"]["logPath"]).read_bytes(), original_output)

    def test_wrong_attempt_workspace_or_goals_are_rejected_with_reasons(self):
        with self.assertRaisesRegex(runner.TaskRunnerError, "compile_command_not_production_only") as error:
            self.prepare(["mvn", "compile", "test"])
        self.assertIn("不得包含测试", error.exception.details["reason"])
        with self.assertRaisesRegex(runner.TaskRunnerError, "compile_workspace_mismatch"):
            self.prepare(cwd=self.root)
        with self.assertRaisesRegex(runner.TaskRunnerError, "compile_build_file_workspace_mismatch"):
            self.prepare(["mvn", "compile", "-f", "../other/pom.xml"])
        earlier = self.prepare()
        current = self.prepare()
        with self.assertRaisesRegex(runner.TaskRunnerError, "compile_attempt_not_current"):
            self.record(earlier)
        with self.assertRaisesRegex(runner.TaskRunnerError, "compile_output_unavailable"):
            self.record(current, output=self.root / "missing.log")
        with self.assertRaisesRegex(runner.TaskRunnerError, "compile_exit_code_invalid"):
            self.record(current, exit_code=-1)

    def test_cli_preparation_recording_and_inspection(self):
        common = ["--workspace", str(self.workspace), "--feature", "alpha", "--task-id", "T001",
                  "--code-workspace", str(self.code), "--run-id", self.state["runId"]]
        prepared = _run("prepare-compile", *common, "--execution-cwd", str(self.code), "--", "mvn", "compile", "-o")
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        attempt = json.loads(prepared.stdout)
        recorded = _run("record-compile", *common, "--attempt-id", attempt["attemptId"],
                        "--exit-code", "17", "--log-exit-code", "0", "--output-file", str(self.log))
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        self.assertFalse(json.loads(recorded.stdout)["passed"])
        inspected = _run("inspect", *common)
        self.assertEqual(inspected.returncode, 0, inspected.stdout + inspected.stderr)
        state = json.loads(inspected.stdout)["run"]
        self.assertTrue(state["productionCompileRequired"])
        self.assertEqual(state["compileAttempts"][-1]["exitCode"], 17)
        self.assertEqual(state["compileSummary"]["status"], "failed")
        self.assertTrue(state["compileSummary"]["sourceFresh"])
        (self.code / "production.txt").write_text("later edit\n", encoding="utf-8")
        state = json.loads(_run("inspect", *common).stdout)["run"]
        self.assertEqual(state["compileSummary"]["status"], "stale")
        self.assertFalse(state["compileSummary"]["sourceFresh"])
        (self.feature_dir / 'plan.json').write_text('invalid plan', encoding='utf-8')
        inspected = _run("inspect", *common)
        self.assertEqual(inspected.returncode, 0, inspected.stdout + inspected.stderr)
        self.assertEqual(json.loads(inspected.stdout)["run"]["compileSummary"]["status"], "stale")

    @unittest.skipUnless(shutil.which("bash"), "Bash required for pipeline status behavior")
    def test_real_bash_reports_maven_failure_and_logging_failure(self):
        mock_bin = self.root / "bin"
        mock_bin.mkdir()
        fake_maven = mock_bin / "mvn"
        fake_maven.write_text('#!/bin/bash\nprintf "mock stdout\\n"\nprintf "mock stderr\\n" >&2\nexit "$TEST_MAVEN_EXIT"\n', encoding="utf-8")
        fake_maven.chmod(0o755)
        for compiler_exit, log_exit in [(17, 0), (0, 1), (0, 0)]:
            with self.subTest(compiler_exit=compiler_exit, log_exit=log_exit):
                log_target = self.log if not log_exit else self.root / "missing-directory" / "compile.log"
                script = f'''export MAVEN_OPTS="-Dfile.encoding=UTF-8"
cd {shlex.quote(str(self.code))} || exit "$?"
set -o pipefail
mvn compile -o 2>&1 | tee {shlex.quote(str(log_target))}
compile_status=("${{PIPESTATUS[@]}}")
printf 'MAVEN_EXIT=%s LOG_EXIT=%s\\n' "${{compile_status[0]}}" "${{compile_status[1]}}"
exit "$((compile_status[0] != 0 ? compile_status[0] : compile_status[1]))"
'''
                self.assertEqual(analyze_script(script, str(self.code)).issues, [])
                result = subprocess.run([shutil.which("bash"), "-c", script], capture_output=True, text=True,
                                        env={**os.environ, "PATH": str(mock_bin) + os.pathsep + os.environ["PATH"],
                                             "TEST_MAVEN_EXIT": str(compiler_exit)})
                self.assertEqual(result.returncode, compiler_exit or log_exit, result.stdout + result.stderr)
                self.assertIn(f"MAVEN_EXIT={compiler_exit} LOG_EXIT={log_exit}", result.stdout)
                if not log_exit:
                    self.assertIn("mock stdout", self.log.read_text())
                    self.assertIn("mock stderr", self.log.read_text())


class FrontendCompileExemptionTest(unittest.TestCase):
    def test_frontend_does_not_require_compile_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_frontend_without_batch_compile(feature_dir)
            state = _start(workspace, code)
            (code / "production.ts").write_text("export const ready = true;\n", encoding="utf-8")
            self.assertFalse(state["productionCompileRequired"])
            self.assertTrue(runner.finish_implementation(workspace, "alpha", "T001", code, state["runId"],
                                                        no_code_change_why=None, supporting_files=[])[0])
