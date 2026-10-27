"""Control transactions preserve real worktree, stage, evidence and retry boundaries."""
import hashlib
import subprocess
import time
import unittest
from unittest.mock import patch

from hooks.parallel_batch_stage import (prepare_review, finish_review, review_checkpoint,
    close_test, _CLI_DISPATCH_AUTH)
from hooks.parallel_runtime import load_manifest, save_manifest
from hooks.utest_runtime import preflight
from hooks.compile_coverage import compile_coverage
from tests import test_code_stage_authority as authority_fixture
from tests import test_utest_optimization as utest_fixture


class BatchControlOperationsTest(unittest.TestCase):
    def feature(self):
        fixture = authority_fixture.CodeStageAuthorityTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture._initial_delivery()
        return fixture

    def test_review_preparation_and_finish_are_idempotent(self):
        f = self.feature()
        args = (f.workspace, 'alpha', f.parallel_id, 'B001')
        first = prepare_review(*args)
        second = prepare_review(*args)
        self.assertEqual(first['attempt'], second['attempt'])
        self.assertTrue(second['reused'])
        self.assertIn('engineeringContext', first)
        passed = finish_review(*args, decision='passed')
        self.assertEqual(passed['status'], 'passed')
        self.assertEqual(finish_review(*args, decision='passed')['evidenceId'], passed['evidenceId'])
        self.assertEqual(prepare_review(*args)['requiredAction'], 'reuse_review_result')
        checkpoint = review_checkpoint(*args)
        self.assertEqual(checkpoint['utestPreparation']['status'], 'test_required')
        self.assertEqual([a['batchId'] for a in checkpoint['utestPreparation']['assignments']], ['B001'])

    def test_triage_never_prepares_utest(self):
        f = self.feature()
        args = (f.workspace, 'alpha', f.parallel_id, 'B001')
        prepare_review(*args)
        finish_review(*args, decision='needs_triage', message='T001 missing public contract')
        checkpoint = review_checkpoint(*args)
        self.assertEqual(checkpoint['status'], 'needs_triage')
        self.assertNotIn('utestPreparation', checkpoint)

    def test_stale_dispatch_cannot_use_combined_operations(self):
        f = self.feature()
        m = load_manifest(f.workspace, 'alpha', f.parallel_id)
        m['batches']['B001'].update(dispatchGeneration=1, dispatch={
            'generation': 1, 'status': 'running', 'expiresEpoch': time.time() + 300,
            'tokenSha256': hashlib.sha256(b'current').hexdigest()})
        save_manifest(f.workspace, 'alpha', f.parallel_id, m)
        auth = _CLI_DISPATCH_AUTH.set({'generation': 0, 'token': 'stale'})
        try:
            with self.assertRaisesRegex(ValueError, 'stale_stage_owner'):
                prepare_review(f.workspace, 'alpha', f.parallel_id, 'B001')
        finally:
            _CLI_DISPATCH_AUTH.reset(auth)

    def test_compile_proof_survives_test_only_changes_but_not_production_or_log_changes(self):
        f = self.feature()
        args = (f.feature_dir, f.parallel_id, 'B001', {'id': 'T001'}, f.worktree)
        self.assertTrue(compile_coverage(*args)['productionCompileCovered'])
        tests = f.worktree / 'tests'
        tests.mkdir()
        (tests / 'test_new.py').write_text('assert True\n')
        self.assertTrue(compile_coverage(*args)['productionCompileCovered'])
        (f.worktree / 'production.txt').write_text('changed production\n')
        self.assertFalse(compile_coverage(*args)['productionCompileCovered'])
        (f.worktree / 'production.txt').write_text('production\n')
        log = next((f.feature_dir / '.compile-logs').glob('*.log'))
        log.write_text('tampered')
        self.assertFalse(compile_coverage(*args)['productionCompileCovered'])

    def utest(self):
        f = utest_fixture.UTestOptimizationTest('test_real_bash_execution_records_cases_and_prevents_replay')
        f.setUp()
        self.addCleanup(f.doCleanups)
        return f

    def test_test_only_pom_changes_preserve_production_compile_proof(self):
        f = self.utest()
        args = (f.a.feature_dir, f.a.parallel_id, "B001", {"id": "T001"}, f.a.worktree)
        self.assertTrue(compile_coverage(*args)["productionCompileCovered"])
        pom = f.a.worktree / "pom.xml"
        pom.write_text(utest_fixture.POM.replace("</dependencies>", utest_fixture.H2 + "</dependencies>"))
        self.assertTrue(compile_coverage(*args)["productionCompileCovered"])
        pom.write_text(pom.read_text().replace("<version>1</version>", "<version>2</version>", 1))
        self.assertFalse(compile_coverage(*args)["productionCompileCovered"])

    def test_close_requires_real_test_then_reuses_without_another_commit_or_execution(self):
        f = self.utest()
        args = (f.a.workspace, 'alpha', f.a.parallel_id, 'B001')
        kwargs = {'owner_token': f.a.token, 'commit_summary': '增加行为测试'}
        with self.assertRaisesRegex(ValueError, 'requires_actual_pass'):
            close_test(*args, **kwargs)
        f.write_test()
        prepared = preflight(f.args, f.ctx())
        run = subprocess.run(prepared['bashCommand'], text=True, capture_output=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = close_test(*args, **kwargs)
        self.assertEqual(result['testStatus'], 'passed')
        self.assertTrue(result['testEvidenceIds'])
        self.assertTrue((f.a.feature_dir / 'UNIT_TEST_REPORT.md').is_file())
        self.assertTrue((f.a.feature_dir / '.parallel-runs' / f.a.parallel_id / 'validation-summary.md').is_file())
        again = close_test(*args, **kwargs)
        self.assertTrue(again['reused'])
        self.assertEqual(again['commitSha'], result['commitSha'])
        self.assertIsNone(f.a._batch()['lease'])

    def test_test_asset_changes_after_execution_cannot_close_as_passed(self):
        f = self.utest()
        test_file = f.write_test()
        prepared = preflight(f.args, f.ctx())
        executed = subprocess.run(prepared["bashCommand"], text=True, capture_output=True, timeout=20)
        self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
        test_file.write_text(test_file.read_text() + "# changed after execution\n")
        with self.assertRaisesRegex(ValueError, "test_inputs_changed"):
            close_test(f.a.workspace, "alpha", f.a.parallel_id, "B001",
                       owner_token=f.a.token, commit_summary="追加测试")
        self.assertEqual(f.a._batch()["stageStates"]["test"]["status"], "running")

    def test_budget_expiry_during_closure_retains_assets_and_defers_without_retesting(self):
        f = self.utest()
        f.write_test()
        prepared = preflight(f.args, f.ctx())
        executed = subprocess.run(prepared["bashCommand"], text=True, capture_output=True, timeout=20)
        self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
        manifest = load_manifest(f.a.workspace, "alpha", f.a.parallel_id)
        manifest["batches"]["B001"]["stageStates"]["test"]["stageDeadlineEpoch"] = 0
        save_manifest(f.a.workspace, "alpha", f.a.parallel_id, manifest)
        result = close_test(f.a.workspace, "alpha", f.a.parallel_id, "B001",
                            owner_token=f.a.token, commit_summary="保留实际测试结果")
        self.assertEqual(result["testStatus"], "deferred")
        self.assertIsNone(f.a._batch()["lease"])
        self.assertEqual(f.a._batch()["testClosure"]["phase"], "released")

    def test_deferred_issue_recovers_after_stage_evidence_before_issue_registration(self):
        f = self.utest()
        args = (f.a.workspace, "alpha", f.a.parallel_id, "B001")
        kwargs = {"owner_token": f.a.token, "commit_summary": "保留失败资产",
                  "failure_type": "environment", "message": "offline dependency"}
        from hooks.parallel_batch_stage import complete_stage, gate_batch
        def interrupted(*args, **kwargs):
            complete_stage(*args, **kwargs)
            raise OSError("interrupted after stage evidence")
        with patch("hooks.parallel_batch_stage.complete_stage", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "after stage evidence"):
                close_test(*args, **kwargs)
        self.assertEqual(f.a._batch()["stageStates"]["test"]["status"], "passed")
        self.assertEqual(gate_batch(*args)["error"], "utest_asset_closure_pending")
        result = close_test(*args, **kwargs)
        self.assertEqual(result["testStatus"], "deferred")
        manifest = load_manifest(f.a.workspace, "alpha", f.a.parallel_id)
        self.assertEqual(len(manifest["deferredIssues"]), 1)
        self.assertIsNone(f.a._batch()["lease"])

    def test_tampered_test_output_log_cannot_close_as_passed(self):
        f = self.utest()
        f.write_test()
        prepared = preflight(f.args, f.ctx())
        executed = subprocess.run(prepared["bashCommand"], text=True, capture_output=True, timeout=20)
        self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
        response = f.ctx()[-1]["tasks"]["T001"]["commands"][prepared["executionId"]]["response"]
        from pathlib import Path
        Path(response["evidenceLogPath"]).write_text("changed captured output")
        with self.assertRaisesRegex(ValueError, "evidence_log_invalid"):
            close_test(f.a.workspace, "alpha", f.a.parallel_id, "B001",
                       owner_token=f.a.token, commit_summary="保留真实结果")

    def test_close_recovers_an_interruption_after_release_without_retesting(self):
        f = self.utest()
        args = (f.a.workspace, 'alpha', f.a.parallel_id, 'B001')
        kwargs = {'owner_token': f.a.token, 'commit_summary': '保留环境失败证据',
                  'failure_type': 'environment', 'message': 'offline dependency unavailable'}
        from hooks.parallel_runtime import release_lease
        def interrupted(*args, **kwargs):
            release_lease(*args, **kwargs)
            raise OSError('host interrupted after release')
        with patch('hooks.parallel_runtime.release_lease', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'host interrupted'):
                close_test(*args, **kwargs)
        self.assertIsNone(f.a._batch()['lease'])
        result = close_test(*args, **kwargs)
        self.assertEqual(result['testStatus'], 'deferred')
        self.assertEqual(f.a._batch()['testClosure']['phase'], 'released')
        with self.assertRaisesRegex(ValueError, 'request_changed'):
            close_test(*args, **{**kwargs, 'owner_token': 'wrong'})
