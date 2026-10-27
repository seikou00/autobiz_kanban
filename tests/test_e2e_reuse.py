"""Real Git/provider snapshots preserve the original E2E execution trust chain."""
import contextlib
import copy
import io
import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from hooks.e2e_quality_check import scan
from hooks.e2e_result_writer import main as writer_main, _finalize_data
from hooks.e2e_reuse import capture_binding, reuse_plan, valid_inherited, POLICY_FILE
from tests import test_run_e2e_command as fixture_module


class E2EReuseTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture_module.E2ERunnerIntegrationTest()
        with contextlib.redirect_stdout(io.StringIO()):
            self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.args = ['--workspace', str(self.f.workspace), '--feature', 'alpha']

    def writer(self, action, *options):
        with contextlib.redirect_stdout(io.StringIO()):
            return writer_main([action, *self.args, *options])

    def read(self):
        return json.loads((self.f.feature_dir / 'E2E_RESULT.json').read_text())

    def enable(self):
        self.receipt = self.f.repo.parent / 'environment.json'
        self.proof = {'kind': 'immutable_snapshot', 'snapshotId': 'snapshot-1',
                      'deploymentDigest': 'a' * 64, 'dataDigest': 'b' * 64, 'authDigest': 'c' * 64,
                      'entryUrl': 'http://localhost:3000/', 'authStatus': 'not_required',
                      'expiresAt': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
        self.receipt.write_text(json.dumps(self.proof))
        (self.f.repo / 'snapshot_probe.py').write_text('import pathlib,sys\nprint(pathlib.Path(sys.argv[1]).read_text())\n')
        (self.f.repo / '.gitignore').write_text('test-results/\n__pycache__/\n')
        (self.f.repo / POLICY_FILE).write_text(json.dumps({'version': 1, 'readOnly': True,
            'environmentProbe': [sys.executable, 'snapshot_probe.py', str(self.receipt)]}))
        for args in (['init'], ['config', 'user.email', 'test@example.invalid'],
                     ['config', 'user.name', 'Test'], ['add', '.'], ['commit', '-m', 'fixture']):
            subprocess.run(['git', '-C', str(self.f.repo), *args], capture_output=True, check=True)

    def execute_finalize(self):
        self.f._execute()
        self.assertEqual(self.writer('derive-scenario-coverage'), 0)
        self.assertEqual(self.writer('finalize'), 0)

    def repair(self):
        self.assertEqual(self.writer('begin-round', '--kind', 'repair'), 0)
        self.assertEqual(self.writer('sync-quality-gate'), 0)
        self.assertEqual(self.writer('derive-scenario-coverage'), 0)

    def test_no_project_snapshot_provider_means_full_execution(self):
        self.execute_finalize()
        self.repair()
        data = self.read()
        self.assertEqual(data['currentRound']['scope'], 'full')
        self.assertEqual(valid_inherited(self.f.feature_dir, data), {})
        self.assertNotEqual(self.writer('finalize'), 0)

    def test_unchanged_immutable_environment_inherits_original_execution_without_copying(self):
        self.enable()
        self.execute_finalize()
        before = self.read()['cases'][0]['executions']
        self.assertIn('reuseBinding', before[0])
        self.repair()
        self.assertEqual(self.read()['currentRound']['affectedCaseIds'], [])
        self.assertEqual(self.writer('finalize'), 0)
        after = self.read()
        self.assertEqual(after['cases'][0]['executions'], before)
        self.assertEqual(after['cases'][0]['executions'][0]['roundIndex'], 1)
        from tests.test_specs_lite_workflow import HookContext
        from artifact_check import validate_e2e_result_json
        with contextlib.redirect_stdout(io.StringIO()):
            failures = validate_e2e_result_json(HookContext(skill='autodev-e2e', slug='alpha', root=self.f.workspace))
        self.assertEqual(failures, 0)

    def test_source_environment_expiry_and_case_changes_invalidate_inheritance(self):
        self.enable()
        self.execute_finalize()
        self.repair()
        data = self.read()
        self.assertTrue(valid_inherited(self.f.feature_dir, data))
        (self.f.repo / 'production.py').write_text('changed production\n')
        self.assertEqual(valid_inherited(self.f.feature_dir, data), {})
        (self.f.repo / 'production.py').unlink()
        for field, value in (('dataDigest', 'different-data'), ('authDigest', 'different-auth'),
                             ('expiresAt', '2000-01-01T00:00:00+00:00')):
            with self.subTest(field=field):
                self.receipt.write_text(json.dumps({**self.proof, field: value}))
                self.assertEqual(valid_inherited(self.f.feature_dir, data), {})
                self.receipt.write_text(json.dumps(self.proof))
        changed = copy.deepcopy(data)
        changed['cases'][0]['steps'][0]['expected'] = 'new expected behavior'
        self.assertEqual(valid_inherited(self.f.feature_dir, changed), {})
        self.assertEqual(self.writer('finalize'), 0)  # Original unchanged data remains valid.

    def test_tampered_original_binding_or_report_cannot_pass_finalize(self):
        self.enable()
        self.execute_finalize()
        self.repair()
        data = self.read()
        altered = copy.deepcopy(data)
        altered['cases'][0]['executions'][0]['reuseBinding']['sourceDigest'] = 'forged'
        _, errors = _finalize_data(self.f.feature_dir, altered)
        self.assertIn('execution_reuse_binding_mismatch', {row['reason'] for row in errors})
        report = self.f.feature_dir / data['cases'][0]['executions'][0]['report']['path']
        report.write_text(report.read_text() + ' ')
        self.assertNotEqual(self.writer('finalize'), 0)

    def test_changing_another_independent_spec_keeps_only_unchanged_case_inherited(self):
        self.enable()
        second = self.f.repo / 'e2e' / 'other.spec.ts'
        second.write_text("import { test, expect } from '@playwright/test';\ntest('[other]', async ({ page }) => { await expect(page).toHaveURL('/'); });\n")
        scan(self.f.workspace, 'alpha', self.f.repo, ['e2e/alpha.spec.ts', 'e2e/other.spec.ts'], [], [])
        self.writer('sync-quality-gate')
        self.execute_finalize()
        self.repair()
        second.write_text(second.read_text().replace("'[other]'", "'[changed]'"))
        scan(self.f.workspace, 'alpha', self.f.repo, ['e2e/alpha.spec.ts', 'e2e/other.spec.ts'], [], [])
        self.writer('sync-quality-gate')
        self.writer('refresh-reuse')
        self.assertEqual(self.read()['currentRound']['affectedCaseIds'], [])
        self.assertEqual(self.writer('finalize'), 0)

    def test_a_current_round_failure_cannot_fall_back_to_an_old_pass(self):
        self.enable()
        self.execute_finalize()
        self.repair()
        original_report = self.f._report
        def failed_report():
            report = original_report()
            case = report["suites"][0]["specs"][0]["tests"][0]
            case["status"] = "unexpected"
            case["results"][0]["status"] = "failed"
            report["stats"].update(expected=0, unexpected=1)
            return report
        with patch.object(self.f, "_report", side_effect=failed_report):
            self.f._execute()
        self.assertEqual(valid_inherited(self.f.feature_dir, self.read()), {})
        self.assertNotEqual(self.writer("finalize"), 0)
        self.assertEqual(self.read()["verdict"], "FAIL")

    def test_pending_execution_and_unknown_environment_cannot_be_inherited(self):
        self.enable()
        self.execute_finalize()
        self.repair()
        data = self.read()
        pending = self.f.feature_dir / "e2e-diagnostics/round-2/unfinished.pending.json"
        pending.parent.mkdir(exist_ok=True)
        pending.write_text("{}")
        self.assertEqual(valid_inherited(self.f.feature_dir, data), {})
        pending.unlink()
        self.receipt.write_text("[]")
        self.assertEqual(valid_inherited(self.f.feature_dir, data), {})
        self.assertNotEqual(self.writer("finalize"), 0)

    def test_repairing_one_of_two_registered_cases_preserves_only_the_other_run(self):
        self.enable()
        second = self.f.repo / "e2e/second.spec.ts"
        second.write_text(self.f.spec.read_text().replace("E2E-alpha-001", "E2E-alpha-002"))
        self.assertEqual(self.writer("add-case", "--case-id", "E2E-alpha-002", "--task-id", "T001",
            "--spec-ref", self.f.spec_refs[0], "--spec-ref", self.f.spec_refs[1], "--priority", "P0",
            "--ui-required", "true", "--execution-mode", "browser", "--step-json",
            '{"action":"open","expected":"visible","verification":{"type":"ui","details":"visible text"}}'), 0)
        scan(self.f.workspace, "alpha", self.f.repo, ["e2e/alpha.spec.ts", "e2e/second.spec.ts"], [], [])
        self.writer("sync-quality-gate")
        self.f._execute()
        from hooks.run_e2e_command import execute_e2e_command
        original_report = self.f._report
        def report_second():
            report = original_report()
            spec = report["suites"][0]["specs"][0]
            spec["title"] = "[E2E-alpha-002] works"
            spec["location"]["file"] = str(second)
            return report
        with patch.object(self.f, "_report", side_effect=report_second), patch("hooks.run_e2e_command._run", side_effect=self.f._fake_run):
            execute_e2e_command(self.f.workspace, "alpha", self.f.repo,
                ["npx", "--yes", "--package", "@playwright/test", "playwright", "test", "e2e/second.spec.ts"],
                "E2E-alpha-002", "T001", self.f.spec_refs, ["e2e/second.spec.ts"],
                entry_url="http://localhost:3000/", auth_status="not_required", timeout=10)
        self.assertEqual(self.writer("derive-scenario-coverage"), 0)
        self.assertEqual(self.writer("finalize"), 0)
        before = self.read()["cases"][1]["executions"]
        self.repair()
        self.f.spec.write_text(self.f.spec.read_text() + "// local test asset repair\n")
        scan(self.f.workspace, "alpha", self.f.repo, ["e2e/alpha.spec.ts", "e2e/second.spec.ts"], [], [])
        self.writer("sync-quality-gate")
        self.writer("refresh-reuse")
        current = self.read()["currentRound"]
        self.assertEqual(current["affectedCaseIds"], ["E2E-alpha-001"])
        self.assertEqual(set(current["inheritedExecutions"]), {"E2E-alpha-002"})
        self.f._execute()
        self.writer("derive-scenario-coverage")
        self.assertEqual(self.writer("finalize"), 0)
        self.assertEqual(self.read()["cases"][1]["executions"], before)

    def test_shared_fixture_or_uncommitted_policy_changes_force_full_execution(self):
        self.enable()
        helper = self.f.repo / 'e2e' / 'helper.ts'
        helper.write_text('export const n = 1;\n')
        self.f.spec.write_text("import './helper';\n" + self.f.spec.read_text())
        scan(self.f.workspace, 'alpha', self.f.repo, ['e2e/alpha.spec.ts'], [], [])
        self.writer('sync-quality-gate')
        self.execute_finalize()
        self.repair()
        data = self.read()
        helper.write_text('export const n = 2;\n')
        scan(self.f.workspace, 'alpha', self.f.repo, ['e2e/alpha.spec.ts'], [], [])
        self.writer('sync-quality-gate')
        self.assertEqual(valid_inherited(self.f.feature_dir, data), {})
        policy = self.f.repo / POLICY_FILE
        policy.write_text(policy.read_text() + ' ')
        self.assertEqual(valid_inherited(self.f.feature_dir, data), {})

    def test_quality_scan_reuses_exact_inputs_but_rechecks_import_graph(self):
        with patch('hooks.e2e_quality_check.scan_source', wraps=fixture_module.scan.__globals__['scan_source']) as scanner:
            first = scan(self.f.workspace, 'alpha', self.f.repo, ['e2e/alpha.spec.ts'], [], [])
            path = self.f.feature_dir / 'E2E_QUALITY_SCAN.json'
            before = (path.read_bytes(), path.stat().st_mtime_ns)
            count = scanner.call_count
            again = scan(self.f.workspace, 'alpha', self.f.repo, ['e2e/alpha.spec.ts'], [], [])
            self.assertTrue(again['reused'])
            self.assertEqual(scanner.call_count, count)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            self.f.spec.write_text("import './new-helper';\n" + self.f.spec.read_text())
            (self.f.repo / 'e2e' / 'new-helper.ts').write_text('export const n = 1;\n')
            changed = scan(self.f.workspace, 'alpha', self.f.repo, ['e2e/alpha.spec.ts'], [], [])
            self.assertFalse(changed.get('reused', False))
            self.assertIn('e2e/new-helper.ts', {row['path'] for row in changed['scannedInputs']})
