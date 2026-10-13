"""Real Batch regression coverage for test POM ownership and finite execution."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from hooks.json_writer_common import atomic_write_json
from hooks.frontend_route_write_guard import main as write_guard
from hooks.inspect_test_environment import inspect_environment
from hooks.parallel_batch_stage import complete_stage, finalize_implementation, start_stage
from hooks.parallel_runtime import run_lock
from hooks.run_utest_command import execute_utest_command, observed_test_count, _failure_classification
from hooks.runtime_artifact_guard import guard as artifact_guard
from hooks.utest_runtime import apply_pom, begin, choose, context, inspect_class, lookup, preflight, prepare, shell_quote, validate_exact_test_argv
from hooks.utest_test_environment import validate_test_pom_change
from hooks.worktree_manager import seal_parallel_batch
from tests import test_code_stage_authority as authority_fixture


POM = '<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId><artifactId>legacy</artifactId><version>1</version><dependencies><dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>4.13.2</version><scope>test</scope></dependency></dependencies></project>'
H2 = '<dependency><groupId>com.h2database</groupId><artifactId>h2</artifactId><version>2.2.224</version><scope>test</scope></dependency>'


class MavenTestOwnershipTest(unittest.TestCase):
    def test_allows_test_dependencies_without_a_name_whitelist(self):
        self.assertTrue(validate_test_pom_change(POM, POM.replace('</dependencies>', H2 + '</dependencies>'))['allowed'])
        dependency = H2.replace('h2', 'custom-driver').replace('com.h2database', 'company')
        self.assertTrue(validate_test_pom_change(POM, POM.replace('</dependencies>', dependency + '</dependencies>'))['allowed'])

    def test_rejects_production_dependency_and_platform_changes(self):
        for changed in (POM.replace('</dependencies>', H2.replace('<scope>test</scope>', '') + '</dependencies>'),
                        POM.replace('<version>1</version>', '<version>2</version>', 1),
                        POM.replace('</project>', '<properties><java.version>21</java.version></properties></project>')):
            with self.subTest(changed=changed):
                self.assertFalse(validate_test_pom_change(POM, changed)['allowed'])

    def test_test_plugin_allowed_but_failure_masking_rejected(self):
        plugin = '<build><plugins><plugin><artifactId>maven-surefire-plugin</artifactId><version>2.22.2</version><configuration>{}</configuration></plugin></plugins></build>'
        self.assertTrue(validate_test_pom_change(POM, POM.replace('</project>', plugin.format('<forkCount>1</forkCount>') + '</project>'))['allowed'])
        for config in ('<skipTests>true</skipTests>', '<testFailureIgnore>true</testFailureIgnore>', '<failIfNoTests>false</failIfNoTests>', '<excludes><exclude>**/*</exclude></excludes>'):
            self.assertFalse(validate_test_pom_change(POM, POM.replace('</project>', plugin.format(config) + '</project>'))['allowed'])

    def test_skipped_cases_do_not_prove_execution(self):
        cases = [('Tests run: 3, Failures: 0, Errors: 0, Skipped: 3', 0),
                 ('Tests: 5 skipped, 5 total', 0), ('Tests: 1 failed, 2 passed, 4 skipped, 7 total', 3),
                 ('Ran 2 tests in 0.001s\nOK (skipped=2)', 0), ('1 passed, 3 skipped in 0.01s', 1)]
        for output, expected in cases:
            with self.subTest(output=output):
                self.assertEqual(expected, observed_test_count(output, Path('/unused'), []))

    def test_missing_dependencies_and_test_compile_are_not_production_compile(self):
        self.assertEqual('environment', _failure_classification(1, False, 'completed', 'Could not find artifact byte-buddy', ''))
        self.assertEqual('test_failure', _failure_classification(1, False, 'completed', 'maven-compiler-plugin:3.8.1:testCompile COMPILATION ERROR', ''))
        self.assertEqual('source_compile', _failure_classification(1, False, 'completed', 'maven-compiler-plugin:3.8.1:compile COMPILATION ERROR', ''))

    def test_precise_maven_target_and_windows_bash_paths(self):
        validate_exact_test_argv(['mvn', '-Dtest=ExampleTest', 'test'], ['src/test/java/ExampleTest.java'], Path('/module'))
        for argv in (['mvn', 'test'], ['mvn', '-Dtest=OtherTest', 'test'], ['mvn', '-f', '../other/pom.xml', '-Dtest=ExampleTest', 'test']):
            with self.assertRaises(ValueError):
                validate_exact_test_argv(argv, ['src/test/java/ExampleTest.java'], Path('/module'))
        self.assertEqual("'D:/仓库 路径/pom.xml'", shell_quote('D:\\仓库 路径\\pom.xml'))


class UTestOptimizationTest(unittest.TestCase):
    def setUp(self):
        self.a = authority_fixture.CodeStageAuthorityTest()
        original_workspace = authority_fixture._workspace
        def bound_workspace(root):
            workspace, feature, repo = original_workspace(root)
            path = feature / 'plan.json'
            plan = json.loads(path.read_text())
            plan['codeWorkspaces'] = {'default': str(repo)}
            plan['batches'][0]['workspaceRef'] = 'default'
            path.write_text(json.dumps(plan))
            return workspace, feature, repo
        with patch.object(authority_fixture, '_workspace', side_effect=bound_workspace):
            self.a.setUp()
        self.addCleanup(self.a.doCleanups)
        task = self.a._task()
        (self.a.worktree / 'pom.xml').write_text(POM, encoding='utf-8')
        if self._testMethodName == 'test_seal_handles_nested_unicode_pom_path':
            nested = self.a.worktree / '后台 服务' / 'pom.xml'
            nested.parent.mkdir()
            nested.write_text(POM, encoding='utf-8')
        (self.a.worktree / 'production.txt').write_text('production\n')
        self.a._finish(task)
        self.a._seal()
        finalize_implementation(self.a.workspace, 'alpha', self.a.parallel_id, 'B001')
        start_stage(self.a.workspace, 'alpha', self.a.parallel_id, 'B001', 'review')
        complete_stage(self.a.workspace, 'alpha', self.a.parallel_id, 'B001', 'review')
        start_stage(self.a.workspace, 'alpha', self.a.parallel_id, 'B001', 'test')
        self.args = argparse.Namespace(workspace=str(self.a.workspace), feature='alpha', run_id=self.a.parallel_id,
                                       batch_id='B001', batch_worktree=str(self.a.worktree), task_id='T001',
                                       owner_token=self.a.token, environment_target_id=None, stage_timeout=1800,
                                       test_file=None, preset=None, reason=None, retry_kind=None, command_timeout=10)

    def ctx(self):
        return context(self.args)

    def write_test(self, body=None):
        test = self.a.worktree / 'tests' / 'test_sample.py'
        test.parent.mkdir(exist_ok=True)
        test.write_text(body or 'import unittest\nclass SampleTest(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(2 + 2, 4)\n', encoding='utf-8')
        self.args.test_file = ['tests/test_sample.py']
        self.args.argv = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_sample.py']
        return test

    def test_guard_allows_test_pom_without_active_production_task_then_seals(self):
        pom = self.a.worktree / 'pom.xml'
        changed = POM.replace('</dependencies>', H2 + '</dependencies>')
        payload = json.dumps({'tool_name': 'write_file', 'tool_input': {'file_path': str(pom), 'content': changed}})
        with patch.dict(os.environ, self.a.environment), patch('hooks.frontend_route_write_guard.read_stdin_text', return_value=payload), patch('hooks.frontend_route_write_guard.current_checkpoint', return_value='code_in_progress'):
            self.assertEqual(0, write_guard())
        pom.write_text(changed, encoding='utf-8')
        sealed = seal_parallel_batch(self.a.workspace, 'alpha', self.a.parallel_id, 'B001', self.a.worktree, self.a.token)
        self.assertTrue(sealed['success'], sealed)
        self.assertEqual('utest', sealed['purpose'])

    def test_guard_and_sealer_both_reject_production_pom_delta(self):
        pom = self.a.worktree / 'pom.xml'
        changed = POM.replace('</dependencies>', H2.replace('<scope>test</scope>', '') + '</dependencies>')
        payload = json.dumps({'tool_input': {'file_path': str(pom), 'content': changed}})
        with patch.dict(os.environ, self.a.environment), patch('hooks.frontend_route_write_guard.read_stdin_text', return_value=payload), patch('hooks.frontend_route_write_guard.current_checkpoint', return_value='code_in_progress'), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(2, write_guard())
        pom.write_text(changed, encoding='utf-8')
        self.assertEqual('parallel_utest_production_change_forbidden', seal_parallel_batch(self.a.workspace, 'alpha', self.a.parallel_id, 'B001', self.a.worktree, self.a.token)['error'])

    def test_expired_lease_blocks_direct_pom_edit(self):
        lease = self.a.feature_dir / '.parallel-runs' / self.a.parallel_id / 'leases' / 'B001.json'
        payload = json.loads(lease.read_text())
        payload['expiresEpoch'] = 0
        lease.write_text(json.dumps(payload))
        request = json.dumps({'tool_input': {'file_path': str(self.a.worktree / 'pom.xml'), 'content': POM.replace('</dependencies>', H2 + '</dependencies>')}})
        with patch.dict(os.environ, self.a.environment), patch('hooks.frontend_route_write_guard.read_stdin_text', return_value=request), patch('hooks.frontend_route_write_guard.current_checkpoint', return_value='code_in_progress'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(2, write_guard())

    def test_seal_handles_nested_unicode_pom_path(self):
        pom = self.a.worktree / '后台 服务' / 'pom.xml'
        pom.write_text(POM.replace('</dependencies>', H2 + '</dependencies>'))
        sealed = seal_parallel_batch(self.a.workspace, 'alpha', self.a.parallel_id, 'B001', self.a.worktree, self.a.token)
        self.assertTrue(sealed['success'], sealed)

    def test_runtime_writer_applies_h2_and_invalidates_environment_snapshot(self):
        first = prepare(self.args, self.ctx())
        self.assertFalse(first['environment']['capabilities']['database'])
        candidate = self.a.worktree / 'candidate-pom.txt'
        candidate.write_text(POM.replace('</dependencies>', H2 + '</dependencies>'), encoding='utf-8')
        self.args.content_file = str(candidate)
        result = apply_pom(self.args, self.ctx())
        self.assertTrue(result['pomChange']['allowed'])
        self.assertTrue(result['environment']['capabilities']['database'])
        self.assertTrue(prepare(self.args, self.ctx())['environmentReused'])
        candidate.unlink()

    def test_legacy_junit4_is_ready_and_not_replaced(self):
        result = inspect_environment(self.a.worktree, 'spring')
        self.assertEqual('ready', result['status'])
        self.assertEqual('junit4', result['capabilities']['junitGeneration'])
        self.assertFalse(result['capabilities']['staticMock'])

    def test_strategy_change_and_recovery_keep_original_budget(self):
        first = prepare(self.args, self.ctx())
        self.args.preset, self.args.reason = 'java-mockito', 'source calls an external repository'
        choose(self.args, self.ctx())
        self.args.preset = 'java-unit'
        with self.assertRaisesRegex(ValueError, 'budget_exhausted'):
            choose(self.args, self.ctx())
        ctx = self.ctx()
        ctx[-1]['firstCommandDeadlineEpoch'] = time.time() - 1
        atomic_write_json(ctx[-2], ctx[-1])
        with self.assertRaisesRegex(ValueError, 'first_execution_deadline_exceeded'):
            self.ctx()
        self.assertTrue(Path(first['executionPath']).is_file())

    def test_real_bash_execution_records_cases_and_prevents_replay(self):
        self.write_test()
        prepared = preflight(self.args, self.ctx())
        executed = subprocess.run(prepared['bashCommand'], text=True, capture_output=True, timeout=20)
        self.assertEqual(0, executed.returncode, executed.stdout + executed.stderr)
        cmd = self.ctx()[-1]['tasks']['T001']['commands'][prepared['executionId']]
        self.assertTrue(cmd['passed'], cmd)
        self.assertEqual(1, cmd['response']['testsExecuted'])
        replay = subprocess.run(prepared['bashCommand'], text=True, capture_output=True, timeout=10)
        self.assertNotEqual(0, replay.returncode)
        with self.assertRaisesRegex(ValueError, 'already_passed'):
            preflight(self.args, self.ctx())

    def test_failure_retry_keeps_command_and_requires_classification(self):
        self.write_test('import unittest\nclass SampleTest(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(2 + 2, 5)\n')
        prepared = preflight(self.args, self.ctx())
        self.assertEqual(0, subprocess.run(prepared['bashCommand'], capture_output=True, timeout=15).returncode)
        with self.assertRaisesRegex(ValueError, 'classified_failure'):
            preflight(self.args, self.ctx())
        self.args.retry_kind = 'source_repair'
        with self.assertRaisesRegex(ValueError, 'new_reviewed_production_commit'):
            preflight(self.args, self.ctx())
        self.write_test('import unittest\nclass SampleTest(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(2 + 2, 4)\n# repaired assertion\n')
        self.args.retry_kind = 'test_bug'
        retry = preflight(self.args, self.ctx())
        self.assertEqual(prepared['executionId'], retry['executionId'])
        self.assertEqual(0, subprocess.run(retry['bashCommand'], capture_output=True, timeout=15).returncode)
        cmd = self.ctx()[-1]['tasks']['T001']['commands'][retry['executionId']]
        self.assertEqual(2, cmd['attempts'])
        self.assertTrue(cmd['passed'], cmd['response'])
        self.args.argv += ['-v']
        with self.assertRaisesRegex(ValueError, 'command_frozen'):
            preflight(self.args, self.ctx())

    def test_zero_tests_is_blocked_even_with_exit_zero(self):
        self.write_test('import unittest\n')
        prepared = preflight(self.args, self.ctx())
        executed = subprocess.run(prepared['bashCommand'], text=True, capture_output=True, timeout=20)
        self.assertEqual(0, executed.returncode, executed.stdout + executed.stderr)
        cmd = self.ctx()[-1]['tasks']['T001']['commands'][prepared['executionId']]
        self.assertEqual('BLOCKED', cmd['result'])
        self.assertEqual('no_tests_executed', cmd['response']['executionOutcome'])
        self.assertNotEqual('PASS', cmd['result'])

    def test_maven_zero_exit_and_capture_failure_are_recorded_as_blocked(self):
        self.write_test()
        output = Path(tempfile.gettempdir()) / ('utest-evidence-' + str(time.time_ns()))
        self.addCleanup(lambda: output.unlink(missing_ok=True))
        output.write_text('Tests run: 0, Failures: 0, Errors: 0\n')
        kwargs = dict(workspace=self.a.workspace, feature='alpha', task_id='T001', kind='test',
                      code_workspace=self.a.worktree, test_files=self.args.test_file,
                      argv=['mvn', '-Dtest=SampleTest', 'test'], record_only=True,
                      recorded_exit_code=0, recorded_output_file=str(output))
        zero = execute_utest_command(**kwargs)
        self.assertEqual('BLOCKED', zero['result'])
        self.assertEqual(0, zero['exitCode'])
        output.write_text('Tests run: 1, Failures: 0, Errors: 0\n')
        failed_capture = execute_utest_command(**kwargs, log_exit_code=1)
        self.assertEqual('BLOCKED', failed_capture['result'])
        self.assertEqual('output_capture_failed', failed_capture['executionOutcome'])

    def test_runtime_script_is_protected_and_checks_attempt_before_launch(self):
        self.write_test()
        prepared = preflight(self.args, self.ctx())
        command = 'bash ' + prepared['scriptPath']
        self.assertIsNone(artifact_guard({'tool_name': 'execute', 'tool_input': {'command': command}}))
        path = Path(prepared['scriptPath'])
        self.assertIsNotNone(artifact_guard({'tool_name': 'write_file', 'tool_input': {'path': str(path), 'content': 'echo fake'}}))
        path.write_text(path.read_text() + '\necho modified\n')
        self.assertIsNotNone(artifact_guard({'tool_name': 'execute', 'tool_input': {'command': command}}))

    def test_bad_lease_cannot_apply_pom_or_prepare(self):
        self.args.owner_token = 'not-the-current-lease'
        with self.assertRaisesRegex(ValueError, 'lease_invalid'):
            self.ctx()

    def test_timeout_retains_evidence_and_bash_can_finish_recording(self):
        self.write_test('import unittest,time\nclass HangTest(unittest.TestCase):\n    def test_hang(self):\n        time.sleep(20)\n')
        self.args.command_timeout = 1
        prepared = preflight(self.args, self.ctx())
        executed = subprocess.run(prepared['bashCommand'], text=True, capture_output=True, timeout=15)
        self.assertEqual(0, executed.returncode, executed.stdout + executed.stderr)
        cmd = self.ctx()[-1]['tasks']['T001']['commands'][prepared['executionId']]
        self.assertEqual('command_timeout', cmd['response']['executionOutcome'])
        self.assertEqual(124, cmd['response']['exitCode'])
        self.assertIsNotNone(cmd['response']['evidenceId'])

    def test_cancellation_terminates_test_children_and_keeps_raw_output(self):
        self.write_test('import unittest,time\nfrom pathlib import Path\nclass HangTest(unittest.TestCase):\n    def test_hang(self):\n        Path("started.marker").write_text("started")\n        time.sleep(20)\n')
        prepared = preflight(self.args, self.ctx())
        process = subprocess.Popen(prepared['bashCommand'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        marker = self.a.worktree / 'started.marker'
        deadline = time.monotonic() + 5
        while not marker.is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(marker.is_file())
        process.terminate()
        process.communicate(timeout=5)
        self.assertEqual(143, process.returncode)
        self.assertTrue(Path(prepared['outputFile']).is_file())

    def test_class_lookup_caches_duplicates_and_negative_results_without_repo_scan(self):
        paths = []
        for name in ('a.jar', 'b.jar'):
            path = self.a.worktree / name
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('example/CookieUtil.class', b'fixture')
            paths.append(str(path))
        snapshot = self.a.worktree / 'classpath.txt'
        snapshot.write_text(os.pathsep.join(paths), encoding='utf-8')
        self.args.classpath_file, self.args.class_name = str(snapshot), 'example.CookieUtil'
        result = lookup(self.args, self.ctx())
        self.assertTrue(result['duplicateClass'])
        self.assertEqual(paths, result['matches'])
        with patch('hooks.utest_runtime.zipfile.ZipFile', side_effect=AssertionError('must use cache')):
            self.assertTrue(lookup(self.args, self.ctx())['reused'])

    def test_source_jar_inspection_is_read_once_and_cached(self):
        jar = self.a.worktree / 'legacy-1.jar'
        with zipfile.ZipFile(jar, 'w') as archive:
            archive.writestr('example/CookieUtil.class', b'fixture')
        with zipfile.ZipFile(self.a.worktree / 'legacy-1-sources.jar', 'w') as archive:
            archive.writestr('example/CookieUtil.java', 'package example; class CookieUtil {}')
        snapshot = self.a.worktree / 'classpath.txt'
        snapshot.write_text(str(jar))
        self.args.classpath_file, self.args.class_name = str(snapshot), 'example.CookieUtil'
        result = inspect_class(self.args, self.ctx())
        self.assertEqual('source', result['kind'])
        self.assertIn('class CookieUtil', result['content'])
        with patch('hooks.utest_runtime.zipfile.ZipFile', side_effect=AssertionError('must not reopen sources')):
            self.assertTrue(inspect_class(self.args, self.ctx())['reused'])
        self.args.class_name = 'example.Missing'
        self.assertEqual('missing', lookup(self.args, self.ctx())['status'])
        with patch('hooks.utest_runtime.zipfile.ZipFile', side_effect=AssertionError('must use negative cache')):
            self.assertTrue(lookup(self.args, self.ctx())['reused'])

    def test_failed_maven_diagnostic_reused_until_environment_changes(self):
        kwargs = dict(workspace=self.a.workspace, feature='alpha', kind='setup', task_id='T001',
                      code_workspace=self.a.worktree, argv=['mvn', '-o', 'dependency:tree'])
        with patch('hooks.run_utest_command._run', return_value=(1, '', 'missing Byte Buddy', False, 'exit_nonzero')) as run:
            first = execute_utest_command(**kwargs)
            second = execute_utest_command(**kwargs)
            self.assertEqual(1, run.call_count)
            self.assertTrue(second['diagnosticReused'])
            self.assertEqual(first['exitCode'], second['exitCode'])
            (self.a.worktree / 'pom.xml').write_text(POM.replace('</dependencies>', H2 + '</dependencies>'))
            execute_utest_command(**kwargs)
            self.assertEqual(2, run.call_count)


if __name__ == '__main__':
    unittest.main()
