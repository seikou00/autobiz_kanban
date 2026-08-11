#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hooks.e2e_quality_check import scan
from hooks.e2e_result_writer import main as writer_main
from hooks.evidence_store import main as evidence_main
from hooks.run_e2e_command import (
    E2ECommandError,
    _derive_result,
    _load_log_for_append,
    _report_facts,
    execute_e2e_command,
    inject_json_reporter,
    parse_playwright_command,
    resume_e2e_command,
)

ROOT = Path(__file__).resolve().parents[1]
AUTODEV_HOOKS = ROOT / "skills" / "autodev" / "hooks"
if str(AUTODEV_HOOKS) not in sys.path:
    sys.path.insert(0, str(AUTODEV_HOOKS))
from common import HookContext  # noqa: E402
from skills.autodev.hooks.artifact_check import (  # noqa: E402
    _e2e_log_records,
    validate_e2e_result_json,
)


class PlaywrightCommandContractTest(unittest.TestCase):
    def test_npx_prefix_and_reporter_before_separator(self) -> None:
        parsed = parse_playwright_command(
            [
                "npx",
                "--yes",
                "--package",
                "@playwright/test",
                "playwright",
                "test",
                "e2e/a.spec.ts",
                "--",
                "literal",
            ]
        )
        argv, reporter = inject_json_reporter(parsed)
        self.assertEqual(["@playwright/test"], parsed["declaredPackages"])
        self.assertEqual("--reporter=json", reporter)
        self.assertLess(argv.index("--reporter=json"), argv.index("--"))
        self.assertGreater(argv.index("--reporter=json"), argv.index("test"))

    def test_existing_reporter_is_preserved_and_json_added(self) -> None:
        parsed = parse_playwright_command(
            ["playwright", "test", "--reporter=html", "e2e/a.spec.ts"]
        )
        argv, _ = inject_json_reporter(parsed)
        self.assertIn("--reporter=html,json", argv)

    def test_pass_with_no_tests_is_not_accepted(self) -> None:
        from hooks.run_e2e_command import _reject_empty_run_flags

        parsed = parse_playwright_command(
            ["playwright", "test", "--pass-with-no-tests"]
        )
        with self.assertRaises(E2ECommandError) as caught:
            _reject_empty_run_flags(parsed)
        self.assertIn("pass_with_no_tests_forbidden", str(caught.exception))

    def test_exploration_cli_package_script_and_unknown_flag_are_rejected(self) -> None:
        commands = [
            ["npx", "--yes", "--package", "@playwright/cli@latest", "playwright"],
            ["npm", "run", "e2e"],
            ["npx", "--mystery", "value", "playwright", "test"],
            ["yarn", "playwright", "test"],
        ]
        expected = [
            "e2e_verdict_requires_playwright_test",
            "package_script_verdict_not_supported",
            "unknown_package_runner_flag",
            "package_runner_exec_required",
        ]
        for command, marker in zip(commands, expected):
            with self.subTest(command=command):
                with self.assertRaises(E2ECommandError) as caught:
                    parse_playwright_command(command)
                self.assertIn(marker, str(caught.exception))
                self.assertIn("修复：", str(caught.exception))

    def test_flags_after_separator_are_not_treated_as_playwright_options(self) -> None:
        from hooks.run_e2e_command import _config_info, _reject_empty_run_flags

        parsed = parse_playwright_command(
            ["playwright", "test", "e2e/a.spec.ts", "--", "--pass-with-no-tests", "--config=missing.ts"]
        )
        _reject_empty_run_flags(parsed)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                (None, None, "playwright_defaults"),
                _config_info(root, root, parsed),
            )

    def test_invalid_json_tail_with_newline_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "e2e-run.log"
            valid = {"kind": "note", "ts": "now", "phase": "discovery", "text": "ok"}
            path.write_text(json.dumps(valid) + "\n{\"partial\":\n", encoding="utf-8")

            records, repaired = _load_log_for_append(path)

            self.assertTrue(repaired)
            self.assertEqual([valid], records)
            self.assertEqual(json.dumps(valid) + "\n", path.read_text(encoding="utf-8"))


class PlaywrightReportContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        (self.repo / "e2e").mkdir()
        self.spec = self.repo / "e2e" / "a.spec.ts"
        self.spec.write_text("test\n", encoding="utf-8")

    def _report(self, case_id: str, *, status: str = "expected", expected: str = "passed", last: str = "passed") -> dict:
        return {
            "config": {"rootDir": str(self.repo)},
            "suites": [
                {
                    "specs": [
                        {
                            "title": "[{}] works".format(case_id),
                            "tags": [],
                            "location": {"file": str(self.spec)},
                            "tests": [
                                {
                                    "projectId": "chromium",
                                    "projectName": "chromium",
                                    "status": status,
                                    "expectedStatus": expected,
                                    "results": [{"status": last}],
                                }
                            ],
                        }
                    ]
                }
            ],
            "stats": {"expected": 1, "unexpected": 0, "flaky": 0, "skipped": 0},
        }

    def test_case_binding_is_exact_not_substring(self) -> None:
        report = self._report("E2E-x-0010")
        facts, errors = _report_facts(
            report, self.repo, {"e2e/a.spec.ts": "sha256:x"}, "E2E-x-001"
        )
        self.assertIn("case_binding_matched_zero_tests", errors)
        self.assertEqual(0, facts["caseBinding"]["matchedTests"])

    def test_expected_failure_never_passes(self) -> None:
        report = self._report("E2E-x-001", expected="failed", last="failed")
        facts, errors = _report_facts(
            report, self.repo, {"e2e/a.spec.ts": "sha256:x"}, "E2E-x-001"
        )
        result, gate, reasons = _derive_result(0, False, facts, errors)
        self.assertEqual("FAIL", result)
        self.assertEqual(1, gate)
        self.assertIn("expected_status_not_all_passed", reasons)

    def test_flaky_is_recorded_as_flaky(self) -> None:
        report = self._report("E2E-x-001", status="flaky")
        facts, errors = _report_facts(
            report, self.repo, {"e2e/a.spec.ts": "sha256:x"}, "E2E-x-001"
        )
        result, gate, _ = _derive_result(0, False, facts, errors)
        self.assertEqual("FLAKY", result)
        self.assertEqual(1, gate)
        self.assertEqual("chromium", facts["projects"][0]["projectName"])

    def test_reported_spec_must_belong_to_declared_set(self) -> None:
        report = self._report("E2E-x-001")
        facts, errors = _report_facts(
            report, self.repo, {"e2e/other.spec.ts": "sha256:x"}, "E2E-x-001"
        )
        result, _, reasons = _derive_result(0, False, facts, errors)
        self.assertEqual("BLOCKED", result)
        self.assertTrue(any(reason.startswith("report_specs_not_declared") for reason in reasons))

    def test_missing_project_identity_is_report_shape_blocker(self) -> None:
        report = self._report("E2E-x-001")
        del report["suites"][0]["specs"][0]["tests"][0]["projectName"]
        facts, errors = _report_facts(
            report, self.repo, {"e2e/a.spec.ts": "sha256:x"}, "E2E-x-001"
        )
        result, _, reasons = _derive_result(0, False, facts, errors)
        self.assertEqual("BLOCKED", result)
        self.assertIn("invalid_playwright_project_identity", reasons)


class E2ELegacyReadCompatibilityTest(unittest.TestCase):
    def test_legacy_result_and_plaintext_log_are_read_only_and_cannot_support_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (workspace / ".autobizdevops" / "state.json").write_text("{}\n", encoding="utf-8")
            result_path = feature_dir / "E2E_RESULT.json"
            result_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "verdict": "BLOCKED",
                        "cases": [],
                        "scenarioCoverage": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            log_path = feature_dir / "e2e-run.log"
            log_path.write_text("legacy discovery notes\n", encoding="utf-8")
            before_result = result_path.read_bytes()
            before_log = log_path.read_bytes()

            self.assertEqual(
                0,
                writer_main(
                    ["show", "--workspace", str(workspace), "--feature", "alpha", "--summary"]
                ),
            )
            context = HookContext(
                skill="autodev-e2e",
                slug="alpha",
                root=workspace,
                required_outputs=(),
            )
            records, failures = _e2e_log_records(context, pass_claimed=False)
            self.assertEqual([], records)
            self.assertEqual(0, failures)
            _, pass_failures = _e2e_log_records(context, pass_claimed=True)
            self.assertGreater(pass_failures, 0)
            self.assertEqual(before_result, result_path.read_bytes())
            self.assertEqual(before_log, log_path.read_bytes())


class E2ERunnerIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.workspace = root / "workspace"
        self.feature_dir = self.workspace / ".autobizdevops" / "features" / "alpha"
        self.feature_dir.mkdir(parents=True)
        (self.workspace / ".autobizdevops" / "state.json").write_text("{}\n", encoding="utf-8")
        spec_dir = self.feature_dir / "specs" / "cap"
        spec_dir.mkdir(parents=True)
        (spec_dir / "spec.md").write_text(
            "## ADDED Requirements\n"
            "### Requirement [REQ-001]: cap\n"
            "#### Scenario [SCN-001]: pass\n",
            encoding="utf-8",
        )
        self.repo = root / "repo"
        (self.repo / "e2e").mkdir(parents=True)
        self.spec = self.repo / "e2e" / "alpha.spec.ts"
        self.spec_refs = [
            "specs/cap/spec.md#REQ-001",
            "specs/cap/spec.md#SCN-001",
        ]
        self.spec.write_text(
            "import { test, expect } from '@playwright/test';\n"
            "test('[E2E-alpha-001] works', async ({ page }) => { await expect(page).toHaveURL('/'); });\n",
            encoding="utf-8",
        )
        output_dir = self.repo / "test-results"
        output_dir.mkdir()
        self.trace = output_dir / "trace.zip"
        self.screenshot = output_dir / "failure.png"
        self.console = output_dir / "console.log"
        self.network = output_dir / "network.har"
        self.trace.write_bytes(b"trace")
        self.screenshot.write_bytes(b"png")
        self.console.write_text("console", encoding="utf-8")
        self.network.write_text("network", encoding="utf-8")
        (self.repo / "playwright.config.ts").write_text("export default {};\n", encoding="utf-8")

        self.assertEqual(0, writer_main(["init", "--workspace", str(self.workspace), "--feature", "alpha"]))
        self.assertEqual(
            0,
            writer_main(
                [
                    "add-case",
                    "--workspace",
                    str(self.workspace),
                    "--feature",
                    "alpha",
                    "--case-id",
                    "E2E-alpha-001",
                    "--task-id",
                    "T001",
                    "--spec-ref",
                    "specs/cap/spec.md#REQ-001",
                    "--spec-ref",
                    "specs/cap/spec.md#SCN-001",
                    "--priority",
                    "P0",
                    "--ui-required",
                    "true",
                    "--execution-mode",
                    "browser",
                    "--step-json",
                    '{"action":"open","expected":"visible","verification":{"type":"ui","details":"visible text"}}',
                ]
            ),
        )
        self.assertEqual(
            0,
            writer_main(
                [
                    "begin-round",
                    "--workspace",
                    str(self.workspace),
                    "--feature",
                    "alpha",
                    "--kind",
                    "initial",
                ]
            ),
        )
        quality = scan(
            self.workspace,
            "alpha",
            self.repo,
            ["e2e/alpha.spec.ts"],
            [],
            [],
        )
        self.assertTrue(quality["passed"], quality)
        self.assertEqual(
            0,
            writer_main(
                [
                    "sync-quality-gate",
                    "--workspace",
                    str(self.workspace),
                    "--feature",
                    "alpha",
                ]
            ),
        )

    def _report(self) -> dict:
        return {
            "config": {"rootDir": str(self.repo)},
            "suites": [
                {
                    "specs": [
                        {
                            "title": "[E2E-alpha-001] works",
                            "tags": [],
                            "location": {"file": str(self.spec)},
                            "tests": [
                                {
                                    "projectId": "chromium",
                                    "projectName": "chromium",
                                    "status": "expected",
                                    "expectedStatus": "passed",
                                    "results": [
                                        {
                                            "status": "passed",
                                            "attachments": [
                                                {"name": "trace", "contentType": "application/zip", "path": str(self.trace)},
                                                {"name": "screenshot", "contentType": "image/png", "path": str(self.screenshot)},
                                                {"name": "console", "contentType": "text/plain", "path": str(self.console)},
                                                {"name": "network", "contentType": "application/json", "path": str(self.network)},
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                }
            ],
            "stats": {"expected": 1, "unexpected": 0, "flaky": 0, "skipped": 0},
        }

    def _fake_run(self, argv, cwd, timeout, env=None):
        if "--version" in argv:
            return 0, "Version 1.55.0\n", "", False
        report_path = Path(env["PLAYWRIGHT_JSON_OUTPUT_FILE"])
        report_path.write_text(json.dumps(self._report()), encoding="utf-8")
        self.assertNotIn("PLAYWRIGHT_JSON_OUTPUT_DIR", env)
        self.assertNotIn("PLAYWRIGHT_JSON_OUTPUT_NAME", env)
        return 0, "1 passed\n", "", False

    def _execute(self):
        with mock.patch("hooks.run_e2e_command._run", side_effect=self._fake_run):
            return execute_e2e_command(
                self.workspace,
                "alpha",
                self.repo,
                ["npx", "--yes", "--package", "@playwright/test", "playwright", "test", "e2e/alpha.spec.ts"],
                "E2E-alpha-001",
                "T001",
                self.spec_refs,
                ["e2e/alpha.spec.ts"],
                entry_url="http://localhost:3000/",
                auth_status="not_required",
                timeout=10,
            )

    def test_success_commits_evidence_log_execution_and_finalizes(self) -> None:
        result = self._execute()
        self.assertTrue(result["ok"])
        self.assertEqual("PASS", result["result"])
        records = [
            json.loads(line)
            for line in (self.feature_dir / "evidence" / "EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        log = [json.loads(line) for line in (self.feature_dir / "e2e-run.log").read_text(encoding="utf-8").splitlines()]
        data = json.loads((self.feature_dir / "E2E_RESULT.json").read_text(encoding="utf-8"))
        execution = data["cases"][0]["executions"][0]
        self.assertEqual(records[0]["evidenceId"], execution["evidenceId"])
        self.assertEqual(execution["evidenceId"], log[0]["evidenceId"])
        self.assertEqual(0, records[0]["validation"]["exitCode"])
        self.assertEqual(0, records[0]["e2eRun"]["processExitCode"])
        self.assertEqual("chromium", execution["projects"][0]["projectName"])
        self.assertEqual("config_file", execution["configSource"])
        self.assertTrue((self.feature_dir / "e2e-diagnostics" / "e2e-run.lock").is_file())
        self.assertFalse((self.feature_dir / "e2e-run.lock").exists())
        for kind in ("trace", "screenshot", "console", "network", "report"):
            relative = execution["diagnosticPaths"][kind]
            self.assertIsInstance(relative, str)
            self.assertTrue((self.feature_dir / relative).is_file())

        self.assertEqual(
            0,
            writer_main(
                [
                    "derive-scenario-coverage",
                    "--workspace",
                    str(self.workspace),
                    "--feature",
                    "alpha",
                ]
            ),
        )
        self.assertEqual(
            0,
            writer_main(
                ["finalize", "--workspace", str(self.workspace), "--feature", "alpha"]
            ),
        )
        final = json.loads((self.feature_dir / "E2E_RESULT.json").read_text(encoding="utf-8"))
        self.assertEqual("PASS", final["verdict"])
        self.assertEqual("finalize", final["verdictSource"])
        self.assertEqual("PASS", final["cases"][0]["verdict"])
        context = HookContext(
            skill="autodev-e2e",
            slug="alpha",
            root=self.workspace,
            required_outputs=("E2E_RESULT.json",),
        )
        self.assertEqual(0, validate_e2e_result_json(context))

        orphan = dict(log[0])
        orphan["runId"] = "run-9999999999999-aaaaaaaaaaaa"
        with (self.feature_dir / "e2e-run.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(orphan) + "\n")
        self.assertGreater(validate_e2e_result_json(context), 0)

    def test_result_writer_failure_resumes_without_duplicate_evidence_or_log(self) -> None:
        real_record = __import__("hooks.run_e2e_command", fromlist=["record_execution"]).record_execution
        with mock.patch("hooks.run_e2e_command._run", side_effect=self._fake_run), mock.patch(
            "hooks.run_e2e_command.record_execution", side_effect=OSError("interrupted")
        ):
            with self.assertRaises(OSError):
                execute_e2e_command(
                    self.workspace,
                    "alpha",
                    self.repo,
                    ["playwright", "test", "e2e/alpha.spec.ts"],
                    "E2E-alpha-001",
                    "T001",
                    self.spec_refs,
                    ["e2e/alpha.spec.ts"],
                    timeout=10,
                )
        pending = next((self.feature_dir / "e2e-diagnostics").glob("round-*/*.pending.json"))
        pending_data = json.loads(pending.read_text(encoding="utf-8"))
        run_id = pending_data["runId"]
        execution_hash = pending_data["execution"]["specHashes"]["e2e/alpha.spec.ts"]
        self.spec.write_text(self.spec.read_text(encoding="utf-8") + "// changed after run\n", encoding="utf-8")
        with (self.feature_dir / "e2e-run.log").open("ab") as handle:
            handle.write(b'{"partial":')
        with mock.patch("hooks.run_e2e_command.record_execution", side_effect=real_record):
            resumed = resume_e2e_command(self.workspace, "alpha", run_id)
        self.assertEqual("PASS", resumed["result"])
        evidence_lines = (self.feature_dir / "evidence" / "EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines()
        log_lines = (self.feature_dir / "e2e-run.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(1, len(evidence_lines))
        self.assertEqual(1, len(log_lines))
        self.assertFalse(pending.exists())
        result_data = json.loads((self.feature_dir / "E2E_RESULT.json").read_text(encoding="utf-8"))
        self.assertEqual(
            execution_hash,
            result_data["cases"][0]["executions"][0]["specHashes"]["e2e/alpha.spec.ts"],
        )

    def test_finalize_rejects_report_changed_after_execution(self) -> None:
        self._execute()
        self.assertEqual(
            0,
            writer_main(
                [
                    "derive-scenario-coverage",
                    "--workspace",
                    str(self.workspace),
                    "--feature",
                    "alpha",
                ]
            ),
        )
        report = next((self.feature_dir / "e2e-diagnostics").glob("round-*/report-*.json"))
        report.write_text(report.read_text(encoding="utf-8") + " ", encoding="utf-8")
        self.assertEqual(
            1,
            writer_main(
                ["finalize", "--workspace", str(self.workspace), "--feature", "alpha"]
            ),
        )
        data = json.loads((self.feature_dir / "E2E_RESULT.json").read_text(encoding="utf-8"))
        self.assertNotEqual("PASS", data["verdict"])

    def test_repair_round_invalidates_case_pass_and_initial_cannot_repeat(self) -> None:
        self._execute()
        self.assertEqual(
            0,
            writer_main(
                [
                    "derive-scenario-coverage",
                    "--workspace",
                    str(self.workspace),
                    "--feature",
                    "alpha",
                ]
            ),
        )
        self.assertEqual(
            0,
            writer_main(["finalize", "--workspace", str(self.workspace), "--feature", "alpha"]),
        )

        self.assertEqual(
            0,
            writer_main(
                [
                    "begin-round",
                    "--workspace",
                    str(self.workspace),
                    "--feature",
                    "alpha",
                    "--kind",
                    "repair",
                ]
            ),
        )
        data = json.loads((self.feature_dir / "E2E_RESULT.json").read_text(encoding="utf-8"))
        self.assertEqual("BLOCKED", data["cases"][0]["verdict"])
        self.assertNotIn("qualityGate", data)
        self.assertEqual(1, data["repairRounds"])
        self.assertEqual(
            1,
            writer_main(
                [
                    "begin-round",
                    "--workspace",
                    str(self.workspace),
                    "--feature",
                    "alpha",
                    "--kind",
                    "initial",
                ]
            ),
        )

    def test_finalize_rejects_log_projection_tampering_before_writing_pass(self) -> None:
        self._execute()
        self.assertEqual(
            0,
            writer_main(
                [
                    "derive-scenario-coverage",
                    "--workspace",
                    str(self.workspace),
                    "--feature",
                    "alpha",
                ]
            ),
        )
        log_path = self.feature_dir / "e2e-run.log"
        log = json.loads(log_path.read_text(encoding="utf-8"))
        log["specHash"] = {"e2e/other.spec.ts": "sha256:tampered"}
        log_path.write_text(json.dumps(log) + "\n", encoding="utf-8")

        self.assertEqual(
            1,
            writer_main(["finalize", "--workspace", str(self.workspace), "--feature", "alpha"]),
        )
        data = json.loads((self.feature_dir / "E2E_RESULT.json").read_text(encoding="utf-8"))
        self.assertNotEqual("PASS", data["cases"][0]["verdict"])

    def test_finalize_rejects_fabricated_coverage_evidence(self) -> None:
        self._execute()
        result_path = self.feature_dir / "E2E_RESULT.json"
        data = json.loads(result_path.read_text(encoding="utf-8"))
        data["scenarioCoverage"] = [
            {"scenarioRef": "SCN-001", "evidenceIds": ["ev_9999"], "verdict": "pass"}
        ]
        result_path.write_text(json.dumps(data) + "\n", encoding="utf-8")

        self.assertEqual(
            1,
            writer_main(["finalize", "--workspace", str(self.workspace), "--feature", "alpha"]),
        )
        finalized = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertNotEqual("PASS", finalized["verdict"])

    def test_manual_e2e_validation_append_is_rejected(self) -> None:
        record_path = self.feature_dir / "manual.json"
        record_path.write_text(
            json.dumps(
                {
                    "checkpoint": "e2e_in_progress",
                    "nodeId": "dev.e2e",
                    "skill": "autodev-e2e",
                    "taskId": "T001",
                    "action": "validation",
                    "validation": {"command": "fake", "exitCode": 0, "result": "pass"},
                }
            ),
            encoding="utf-8",
        )
        with mock.patch("sys.stderr") as stderr:
            code = evidence_main(
                [
                    "append",
                    "--workspace",
                    str(self.workspace),
                    "--feature",
                    "alpha",
                    "--record",
                    str(record_path),
                ]
            )
        self.assertEqual(1, code)
        self.assertIn("e2e_validation_requires_e2e_runner", str(stderr.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
