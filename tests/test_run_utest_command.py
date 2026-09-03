#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import contextlib
import io
import json
import importlib.util
import subprocess
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
)
from hooks.utest_assignment_router import build_assignments  # noqa: E402
from hooks.unit_test_result_writer import ensure_plan_result  # noqa: E402
from hooks.utest_plan_contract import canonical_task_digest, validate_result_against_plan  # noqa: E402
from hooks.validate_utest_source_bug import (  # noqa: E402
    SourceBugValidationError,
    validate_claim,
)


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
        subprocess.run(
            ["git", "init", "-q", str(self.repo)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        (self.repo / "test_sample.py").write_text(
            "import unittest\n\n"
            "class SampleTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (self.repo / "test_failing.py").write_text(
            "import unittest\n\n"
            "class FailingTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.fail('source behavior mismatch')\n",
            encoding="utf-8",
        )
        (self.repo / "test_large_output.py").write_text(
            "import unittest\n\n"
            "class LargeOutputTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        print('x' * 6000)\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self._write_plan()
        run_path = self.feature_dir / ".task-runs" / "T001" / "run.json"
        run_path.parent.mkdir(parents=True)
        run_path.write_text(
            json.dumps(
                {
                    "status": "done",
                    "repositories": [{"id": self.repo.name, "path": str(self.repo)}],
                }
            ),
            encoding="utf-8",
        )
    def _task(self, task_id="T001", argv=None, behavior="fixed amount discount"):
        command_id = "VAL-{}-01".format(task_id)
        criterion_id = "AC-{}-01".format(task_id)
        acceptance = [
            {
                "id": criterion_id,
                "text": behavior,
                "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
            }
        ]
        return {
            "id": task_id,
            "title": "Task {}".format(task_id),
            "goal": behavior,
            "implementationPoints": [behavior, "expose public pricing seam"],
            "workspaceRef": self.repo.name,
            "validationBoundary": "public pricing seam",
            "nonGoals": ["tiered discount"],
            "specRefs": [
                "specs/cap/spec.md#REQ-001",
                "specs/cap/spec.md#SCN-001",
            ],
            "acceptanceCriteria": acceptance,
            "validationCommands": [
                {
                    "id": command_id,
                    "argv": argv or ["mvn", "test-compile"],
                    "cwd": ".",
                    "kind": "behavior_test",
                    "required": True,
                    "covers": [criterion_id],
                }
            ],
            "validationTestPlan": [
                {
                    "commandId": command_id,
                    "assetType": "unit_test",
                    "executionStage": "post_batch",
                    "covers": [criterion_id],
                    "testIntent": {
                        "behavior": behavior,
                        "acceptanceCriteria": acceptance,
                    },
                }
            ],
        }

    def _write_plan(self, task=None):
        self.task = task or self._task()
        batch_path = self.feature_dir / "plans" / "B001" / "plan.json"
        batch_path.parent.mkdir(parents=True, exist_ok=True)
        batch_path.write_text(
            json.dumps(
                {"batchId": "B001", "executionLane": "backend", "tasks": [self.task]}
            ),
            encoding="utf-8",
        )
        (self.feature_dir / "plan.json").write_text(
            json.dumps({"batches": [{"id": "B001", "path": "plans/B001/plan.json"}]}),
            encoding="utf-8",
        )

    def _execute(self, **overrides):
        values = {
            "workspace": self.workspace,
            "feature": "alpha",
            "kind": "test",
            "task_id": self.task["id"],
            "task_digest": canonical_task_digest(self.task),
            "test_files": ["test_sample.py"],
            "argv": [sys.executable, "-m", "unittest", "test_sample"],
            "timeout": 10,
        }
        values.update(overrides)
        return execute_utest_command(**values)

    def _records(self):
        path = self.feature_dir / "evidence" / "EVIDENCE.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def _unit_result(self):
        return json.loads((self.feature_dir / "UNIT_TEST_RESULT.json").read_text(encoding="utf-8"))

    def test_success_binds_generated_test_command_to_plan_digest(self):
        result = self._execute()

        self.assertTrue(result["ok"])
        self.assertEqual("UT-001", result["targetId"])
        self.assertEqual(canonical_task_digest(self.task), result["taskDigest"])
        record = self._records()[0]
        target = self._unit_result()["targets"][0]
        self.assertEqual(record["taskDigest"], target["taskDigest"])
        self.assertEqual("UTEST-T001", target["commandId"])
        self.assertEqual(record["validation"]["commandId"], target["commandId"])
        self.assertEqual(["test_sample.py"], record["validation"]["testFiles"])
        self.assertEqual(["test_sample.py"], record["changedFiles"])
        self.assertEqual(record["covers"], target["covers"])
        bindings = json.loads(
            (self.workspace / ".autobizdevops" / "workspace-bindings.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            str(self.repo.resolve()),
            bindings["features"]["alpha"][self.repo.name]["root"],
        )
        self.assertEqual("PASS", self._unit_result()["verdict"])
        self.assertEqual([], validate_result_against_plan(self.feature_dir, self._unit_result()))

    def test_runner_resolves_digest_without_prompt_input(self):
        result = self._execute(task_digest=None)

        self.assertTrue(result["ok"])
        self.assertEqual(canonical_task_digest(self.task), result["taskDigest"])

    def test_rerun_appends_history_on_same_plan_target(self):
        first = self._execute()
        second = self._execute()

        self.assertEqual(first["targetId"], second["targetId"])
        self.assertEqual(2, len(self._unit_result()["targets"][0]["evidenceIds"]))

    def test_failure_is_derived_and_source_bug_attestation_accepts_nonzero(self):
        failing = self._task(
            argv=[sys.executable, "-m", "unittest", "test_failing"],
        )
        self._write_plan(failing)

        result = self._execute(
            argv=[sys.executable, "-m", "unittest", "test_failing"],
            test_files=["test_failing.py"],
        )
        attestation = validate_claim(
            self.workspace,
            "alpha",
            "T001",
            "UTEST-T001",
            result["targetId"],
            result["taskDigest"],
            result["evidenceId"],
        )

        self.assertEqual("FAIL", result["result"])
        self.assertEqual("FAIL", self._unit_result()["verdict"])
        self.assertEqual("source_bug", attestation["classification"])

    def test_source_bug_rejects_exit_zero_and_static_observation(self):
        result = self._execute()

        with self.assertRaises(SourceBugValidationError) as caught:
            validate_claim(
                self.workspace,
                "alpha",
                "T001",
                "UTEST-T001",
                result["targetId"],
                result["taskDigest"],
                result["evidenceId"],
            )

        self.assertIn("FAIL", str(caught.exception))

    def test_missing_executable_is_blocked_with_evidence(self):
        result = self._execute(argv=[str(self.repo / "missing" / "pytest")])

        self.assertEqual(127, result["exitCode"])
        self.assertEqual("BLOCKED", result["result"])
        self.assertEqual("blocked", self._records()[0]["validation"]["result"])
        self.assertEqual("BLOCKED", self._unit_result()["targets"][0]["result"])

    def test_full_output_is_not_truncated(self):
        self._execute(
            argv=[sys.executable, "-m", "unittest", "test_large_output"],
            test_files=["test_large_output.py"],
        )

        log = (self.feature_dir / "test-output.log").read_text(encoding="utf-8")
        self.assertIn("x" * 6000, log)

    def test_caller_cannot_override_assignment_binding(self):
        cases = (
            {"environment_target_id": "ENV-T001-NOTREAL"},
            {"target_id": "UT-999"},
        )
        for override in cases:
            with self.subTest(override=override):
                with self.assertRaises(UTestCommandError):
                    self._execute(**override)
        self.assertFalse((self.feature_dir / "test-output.log").exists())
        self.assertFalse((self.feature_dir / "evidence" / "EVIDENCE.jsonl").exists())
        self.assertFalse((self.feature_dir / "UNIT_TEST_RESULT.json").exists())

    def test_runtime_command_must_execute_tests(self):
        with self.assertRaises(UTestCommandError) as caught:
            self._execute(argv=["mvn", "validate"])

        self.assertIn("真实的精确测试命令", str(caught.exception))
        self.assertFalse((self.feature_dir / "test-output.log").exists())

    def test_runtime_requires_an_existing_test_file(self):
        for files in ([], ["missing_test.py"]):
            with self.subTest(files=files):
                with self.assertRaises(UTestCommandError) as caught:
                    self._execute(test_files=files)
                self.assertIn("测试文件", str(caught.exception))
        self.assertFalse((self.feature_dir / "test-output.log").exists())

    def test_digest_mismatch_rerun_is_rejected_before_execution(self):
        first = self._execute()
        log_before = (self.feature_dir / "test-output.log").read_text(encoding="utf-8")
        evidence_before = list(self._records())
        changed = self._task(behavior="阶梯折扣")
        self._write_plan(changed)

        with self.assertRaises(UTestCommandError) as caught:
            self._execute(task_digest=first["taskDigest"])

        self.assertIn("taskDigest", str(caught.exception))
        self.assertEqual(log_before, (self.feature_dir / "test-output.log").read_text(encoding="utf-8"))
        self.assertEqual(evidence_before, self._records())

    def test_t008_old_tiered_discount_model_restatement_is_rejected(self):
        correct = self._task(
            task_id="T008",
            behavior="限时时间段、商品范围与次数限制",
        )
        correct["acceptanceCriteria"][0]["text"] = "限时时间段、商品范围与次数限制"
        correct["validationTestPlan"][0]["testIntent"]["acceptanceCriteria"] = correct[
            "acceptanceCriteria"
        ]
        self._write_plan(correct)
        old_digest = canonical_task_digest(
            self._task(task_id="T008", behavior="阶梯折扣")
        )
        assignment = build_assignments(self.feature_dir)[0]
        prompt = json.loads(
            assignment["promptContent"].split("\n", 1)[1].rsplit("\n", 1)[0]
        )
        assigned = prompt["tasks"][0]
        self.assertIn("限时时间段、商品范围与次数限制", json.dumps(assigned, ensure_ascii=False))
        self.assertNotIn("阶梯折扣", json.dumps(assigned, ensure_ascii=False))
        self.assertNotIn("taskDigest", assigned)

        with self.assertRaises(UTestCommandError) as caught:
            self._execute(
                task_digest=old_digest,
            )

        self.assertIn("taskDigest", str(caught.exception))
        self.assertFalse((self.feature_dir / "test-output.log").exists())
        self.assertFalse((self.feature_dir / "evidence" / "EVIDENCE.jsonl").exists())
        self.assertFalse((self.feature_dir / "UNIT_TEST_RESULT.json").exists())

    def test_plan_maven_validate_is_location_only_and_does_not_block_tests(self):
        source = self._task(
            task_id="T008",
            argv=["mvn", "validate"],
            behavior="限时时间段、商品范围与次数限制",
        )
        self._write_plan(source)

        result = self._execute()

        self.assertTrue(result["ok"])
        self.assertNotIn("mvn validate", result["command"])

    def test_scope_module_selects_execution_cwd_without_model_path_input(self):
        module = self.repo / "yudao-module-mkt"
        module.mkdir()
        (module / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        (module / "test_module.py").write_text(
            "import unittest\n\n"
            "class ModuleTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        task = self._task()
        task["scope"] = {
            "modules": ["yudao-module-mkt"],
            "workspaceRoots": {self.repo.name: "."},
        }
        self._write_plan(task)

        result = self._execute(
            test_files=["yudao-module-mkt/test_module.py"],
            argv=[sys.executable, "-m", "unittest", "test_module"],
        )

        self.assertEqual(str(module.resolve()), result["cwd"])
        self.assertEqual("yudao-module-mkt", result["executionCwd"])
        validation = self._records()[0]["validation"]
        self.assertEqual(".", validation["cwd"])
        self.assertEqual("yudao-module-mkt", validation["executionCwd"])
        self.assertEqual([], validate_result_against_plan(self.feature_dir, self._unit_result()))

    def test_semantic_scope_module_blocks_without_fallback(self):
        task = self._task()
        task["scope"] = {
            "modules": ["AiReview 评分模块"],
            "workspaceRoots": {self.repo.name: "."},
        }
        self._write_plan(task)

        with self.assertRaises(UTestCommandError) as caught:
            self._execute()

        self.assertIn("禁止降级到 validationLocations 或 '.'", str(caught.exception))

    def test_writer_failure_retains_evidence_and_reports_recovery(self):
        with mock.patch(
            "hooks.run_utest_command.record_execution", side_effect=OSError("read-only")
        ):
            with self.assertRaises(UTestCommandError) as caught:
                self._execute()

        self.assertEqual(1, len(self._records()))
        self.assertIn("不要重跑测试", str(caught.exception))

    def test_writer_rejects_tampered_target_and_free_verdict(self):
        self._execute()
        data = self._unit_result()
        data["targets"][0]["commandId"] = "VAL-T008-TIERED"
        data["targets"][0]["taskDigest"] = "old-tiered-discount-digest"
        data["verdict"] = "PASS_WITH_WARNINGS"
        (self.feature_dir / "UNIT_TEST_RESULT.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

        with self.assertRaises(ValueError) as caught:
            ensure_plan_result(self.workspace, "alpha", create=False)

        self.assertIn("commandId", str(caught.exception))
        self.assertIn("taskDigest", str(caught.exception))
        self.assertIn("verdict", str(caught.exception))

    def test_board_artifact_gate_reuses_plan_result_semantics(self):
        self._execute()
        data = self._unit_result()
        data["targets"][0]["commandId"] = "VAL-T008-TIERED"
        (self.feature_dir / "UNIT_TEST_RESULT.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        hook_dir = ROOT / "skills" / "autodev" / "hooks"
        sys.path.insert(0, str(hook_dir))
        self.addCleanup(lambda: sys.path.remove(str(hook_dir)))
        spec = importlib.util.spec_from_file_location(
            "utest_artifact_check", str(hook_dir / "artifact_check.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ctx = module.HookContext(
            skill="autodev-utest",
            slug="alpha",
            root=self.workspace,
            required_outputs=("UNIT_TEST_RESULT.json",),
        )
        reasons = []

        def capture(_ctx, reason, *args, **kwargs):
            del _ctx, args, kwargs
            reasons.append(reason)
            return 1

        with mock.patch.object(module, "fail_line", side_effect=capture):
            failures = module.validate_unit_test_result_json(ctx)

        self.assertGreater(failures, 0)
        self.assertIn(
            "unit_test_target_plan_mismatch:UT-001:commandId", reasons
        )

    def test_board_gate_accepts_plan_bound_blocked_result_for_needs_fix(self):
        spec = self.feature_dir / "specs" / "cap" / "spec.md"
        spec.parent.mkdir(parents=True)
        spec.write_text(
            "## ADDED Requirements\n\n"
            "### Requirement [REQ-001]: pricing\n\n"
            "#### Scenario [SCN-001]: fixed discount\n",
            encoding="utf-8",
        )
        ensure_plan_result(self.workspace, "alpha", create=True)
        hook_dir = ROOT / "skills" / "autodev" / "hooks"
        sys.path.insert(0, str(hook_dir))
        self.addCleanup(lambda: sys.path.remove(str(hook_dir)))
        module_spec = importlib.util.spec_from_file_location(
            "utest_blocked_artifact_check", str(hook_dir / "artifact_check.py")
        )
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        ctx = module.HookContext(
            skill="autodev-utest",
            slug="alpha",
            root=self.workspace,
            required_outputs=("UNIT_TEST_RESULT.json",),
            target_checkpoint="needs_fix",
        )
        reasons = []

        def capture(_ctx, reason, *args, **kwargs):
            del _ctx, args, kwargs
            reasons.append(reason)
            return 1

        with mock.patch.object(module, "fail_line", side_effect=capture):
            failures = module.validate_unit_test_result_json(ctx)

        self.assertEqual(0, failures, reasons)
        self.assertEqual("BLOCKED", self._unit_result()["verdict"])

    def test_board_gate_rejects_fail_result_for_needs_fix(self):
        ensure_plan_result(self.workspace, "alpha", create=True)
        data = self._unit_result()
        data["verdict"] = "FAIL"
        (self.feature_dir / "UNIT_TEST_RESULT.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        hook_dir = ROOT / "skills" / "autodev" / "hooks"
        sys.path.insert(0, str(hook_dir))
        self.addCleanup(lambda: sys.path.remove(str(hook_dir)))
        module_spec = importlib.util.spec_from_file_location(
            "utest_failed_artifact_check", str(hook_dir / "artifact_check.py")
        )
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        ctx = module.HookContext(
            skill="autodev-utest",
            slug="alpha",
            root=self.workspace,
            required_outputs=("UNIT_TEST_RESULT.json",),
            target_checkpoint="needs_fix",
        )
        reasons = []

        def capture(_ctx, reason, *args, **kwargs):
            del _ctx, args, kwargs
            reasons.append(reason)
            return 1

        with mock.patch.object(module, "fail_line", side_effect=capture):
            failures = module.validate_unit_test_result_json(ctx)

        self.assertGreater(failures, 0)
        self.assertIn("non_blocked_unit_test_needs_fix_verdict", reasons)

    def test_malformed_evidence_returns_gate_error_instead_of_raising(self):
        self._execute()
        (self.feature_dir / "evidence" / "EVIDENCE.jsonl").write_text(
            "{not-json}\n", encoding="utf-8"
        )

        errors = validate_result_against_plan(self.feature_dir, self._unit_result())

        self.assertTrue(
            any(
                error.startswith("unit_test_evidence_contract_invalid:")
                for error in errors
            ),
            errors,
        )

    def test_setup_only_appends_log(self):
        result = execute_utest_command(
            workspace=self.workspace,
            feature="alpha",
            argv=[sys.executable, "-c", "print('setup')"],
            kind="setup",
            task_id="T001",
        )

        self.assertTrue(result["ok"])
        self.assertFalse((self.feature_dir / "evidence" / "EVIDENCE.jsonl").exists())
        self.assertFalse((self.feature_dir / "UNIT_TEST_RESULT.json").exists())

    def test_plan_cwd_escape_is_rejected_before_execution(self):
        broken = self._task()
        broken["validationCommands"][0]["cwd"] = "../outside"
        self._write_plan(broken)

        with self.assertRaises(UTestCommandError) as caught:
            self._execute()

        self.assertIn("cwd", str(caught.exception))
        self.assertIn("修复：", str(caught.exception))
        self.assertFalse((self.feature_dir / "test-output.log").exists())

    def test_invalid_target_has_repair_instruction(self):
        with self.assertRaises(UTestCommandError) as caught:
            self._execute(target_id="one")
        self.assertIn("修复：", str(caught.exception))
        self.assertFalse((self.feature_dir / "test-output.log").exists())

    def test_cli_setup_strips_remainder_separator(self):
        exit_code = main(
            [
                "--kind",
                "setup",
                "--workspace",
                str(self.workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T001",
                "--",
                sys.executable,
                "-c",
                "print('separator-ok')",
            ]
        )
        self.assertEqual(0, exit_code)
        self.assertIn(
            "separator-ok",
            (self.feature_dir / "test-output.log").read_text(encoding="utf-8"),
        )

    def test_cli_test_accepts_rendered_binding_and_generated_command(self):
        exit_code = main(
            [
                "--kind",
                "test",
                "--workspace",
                str(self.workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T001",
                "--test-file",
                "test_sample.py",
                "--",
                sys.executable,
                "-m",
                "unittest",
                "test_sample",
            ]
        )
        self.assertEqual(0, exit_code)

    def test_cli_rejects_model_authored_repository_path(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "--kind",
                    "test",
                    "--workspace",
                    str(self.workspace),
                    "--feature",
                    "alpha",
                    "--code-workspace",
                    str(self.repo),
                    "--task-id",
                    "T001",
                    "--task-digest",
                    canonical_task_digest(self.task),
                    "--test-file",
                    "test_sample.py",
                    "--",
                    sys.executable,
                    "-m",
                    "unittest",
                    "test_sample",
                ]
            )

        self.assertEqual(2, exit_code)
        self.assertIn("不要传 repo/cwd/framework", stderr.getvalue())
        self.assertFalse((self.feature_dir / "test-output.log").exists())
if __name__ == "__main__":
    unittest.main()
