#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.run_utest_command import (  # noqa: E402
    UTestCommandError,
    execute_utest_command,
    main,
    resolve_command_cwd,
)
from hooks.unit_test_result_writer import record_execution  # noqa: E402


class RunUTestCommandTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.workspace = root / "plugin-output"
        self.repo = root / "repo"
        self.feature_dir = self.workspace / ".autobizdevops" / "features" / "alpha"
        self.feature_dir.mkdir(parents=True)
        (self.workspace / ".autobizdevops" / "state.json").write_text("{}\n", encoding="utf-8")
        self.repo.mkdir()

    def _execute(self, argv=None, **overrides):
        values = {
            "workspace": self.workspace,
            "feature": "alpha",
            "code_workspace": self.repo,
            "argv": argv or [sys.executable, "-c", "print('fresh-output')"],
            "kind": "test",
            "task_id": "T001",
            "spec_refs": ["specs/cap/spec.md#SCN-001"],
            "timeout": 10,
        }
        values.update(overrides)
        return execute_utest_command(**values)

    def _records(self):
        path = self.feature_dir / "evidence" / "EVIDENCE.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def _unit_result(self):
        return json.loads((self.feature_dir / "UNIT_TEST_RESULT.json").read_text(encoding="utf-8"))

    def test_success_allocates_first_target_and_records_consistent_evidence(self):
        result = self._execute()

        self.assertTrue(result["ok"])
        self.assertEqual("test", result["kind"])
        self.assertEqual("PASS", result["result"])
        self.assertEqual("UT-001", result["targetId"])
        records = self._records()
        unit = self._unit_result()
        self.assertEqual("validation", records[0]["action"])
        self.assertEqual("autodev-utest", records[0]["skill"])
        self.assertEqual("pass", records[0]["validation"]["result"])
        self.assertEqual(records[0]["evidenceId"], unit["targets"][0]["evidenceIds"][0])
        self.assertEqual(records[0]["validation"]["command"], unit["targets"][0]["command"])
        log = (self.feature_dir / "test-output.log").read_text(encoding="utf-8")
        self.assertIn("fresh-output", log)
        self.assertIn("exit_code: 0", log)

    def test_explicit_target_rerun_appends_history(self):
        first = self._execute(target_id="UT-010")
        second = self._execute(target_id="UT-010")

        self.assertEqual("UT-010", first["targetId"])
        self.assertEqual("UT-010", second["targetId"])
        targets = self._unit_result()["targets"]
        self.assertEqual(1, len(targets))
        self.assertEqual(2, len(targets[0]["evidenceIds"]))
        self.assertEqual(2, len(self._records()))

    def test_failure_is_fail_in_evidence_and_result(self):
        result = self._execute(argv=[sys.executable, "-c", "import sys; print('bad'); sys.exit(3)"])

        self.assertFalse(result["ok"])
        self.assertEqual(3, result["exitCode"])
        self.assertEqual("FAIL", result["result"])
        self.assertEqual("fail", self._records()[0]["validation"]["result"])
        self.assertEqual("FAIL", self._unit_result()["targets"][0]["result"])

    def test_missing_executable_is_blocked_and_recorded(self):
        result = self._execute(argv=["__definitely_missing_utest_binary__"])

        self.assertEqual(127, result["exitCode"])
        self.assertEqual("BLOCKED", result["result"])
        self.assertEqual("blocked", self._records()[0]["validation"]["result"])
        self.assertIn("修复：", (self.feature_dir / "test-output.log").read_text(encoding="utf-8"))

    def test_result_writer_failure_surfaces_retained_evidence_recovery(self):
        with mock.patch(
            "hooks.run_utest_command.record_execution",
            side_effect=OSError("result is read-only"),
        ):
            with self.assertRaises(UTestCommandError) as caught:
                self._execute()

        records = self._records()
        self.assertEqual(1, len(records))
        self.assertIn(records[0]["evidenceId"], str(caught.exception))
        self.assertIn("UNIT_TEST_RESULT", str(caught.exception))
        self.assertIn("不要重跑测试", str(caught.exception))
        self.assertIn("record-execution", str(caught.exception))
        self.assertFalse((self.feature_dir / "UNIT_TEST_RESULT.json").exists())

    def test_setup_only_appends_log(self):
        result = self._execute(kind="setup", task_id=None, spec_refs=None)

        self.assertTrue(result["ok"])
        self.assertEqual("setup", result["kind"])
        self.assertTrue((self.feature_dir / "test-output.log").is_file())
        self.assertFalse((self.feature_dir / "evidence" / "EVIDENCE.jsonl").exists())
        self.assertFalse((self.feature_dir / "UNIT_TEST_RESULT.json").exists())

    def test_full_output_is_appended_to_log(self):
        marker = "x" * 6000
        self._execute(argv=[sys.executable, "-c", "print({!r})".format(marker)])

        log = (self.feature_dir / "test-output.log").read_text(encoding="utf-8")
        self.assertIn(marker, log)

    def test_cwd_escape_is_rejected_before_execution(self):
        outside = self.repo.parent / "outside"
        outside.mkdir()

        with self.assertRaises(UTestCommandError) as caught:
            resolve_command_cwd(self.repo, outside)

        self.assertIn("cwd 越出", str(caught.exception))
        self.assertIn("修复：", str(caught.exception))

    def test_invalid_explicit_target_id_is_rejected(self):
        with self.assertRaises(UTestCommandError) as caught:
            self._execute(target_id="one")

        self.assertIn("修复：", str(caught.exception))
        self.assertFalse((self.feature_dir / "test-output.log").exists())

    def test_cli_strips_remainder_separator_and_uses_approved_names(self):
        exit_code = main(
            [
                "--kind",
                "setup",
                "--workspace",
                str(self.workspace),
                "--feature",
                "alpha",
                "--code-workspace",
                str(self.repo),
                "--",
                sys.executable,
                "-c",
                "print('separator-ok')",
            ]
        )

        self.assertEqual(0, exit_code)
        self.assertIn("separator-ok", (self.feature_dir / "test-output.log").read_text(encoding="utf-8"))


class UnitTestResultExecutionWriterTest(unittest.TestCase):
    def test_auto_ids_and_history_preserve_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (workspace / ".autobizdevops" / "state.json").write_text("{}\n", encoding="utf-8")
            first = record_execution(
                workspace,
                "alpha",
                task_id="T001",
                spec_refs=["specs/a.md#SCN-001"],
                evidence_id="ev_0001",
                result="PASS",
                command="runner one",
            )
            target_id = first.data["target"]["targetId"]
            record_execution(
                workspace,
                "alpha",
                target_id=target_id,
                task_id="T001",
                spec_refs=["specs/a.md#SCN-001", "specs/a.md#SCN-002"],
                evidence_id="ev_0002",
                result="FAIL",
                command="runner two",
            )

            data = json.loads((feature_dir / "UNIT_TEST_RESULT.json").read_text(encoding="utf-8"))
            self.assertEqual(1, data["version"])
            self.assertEqual(
                {"version", "verdict", "targets", "scenarioCoverage"}, set(data)
            )
            self.assertEqual("UT-001", data["targets"][0]["targetId"])
            self.assertEqual(["ev_0001", "ev_0002"], data["targets"][0]["evidenceIds"])
            self.assertEqual("FAIL", data["targets"][0]["result"])


if __name__ == "__main__":
    unittest.main()
