from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_delivery_report import make_feature, put
from hooks.delivery_report.collect import collect, read_run
from hooks.delivery_report.integration import read_integration
from hooks.delivery_report.publish import publish, validate
from hooks.delivery_report.sources import Inputs, resolve_scope
from hooks.parallel_merge_train import record_candidate_review, main


class IntegrationReportTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.root, self.head = make_feature(self.workspace)
        self.scope = resolve_scope(self.workspace, 'alpha')
        self.run = self.root / '.parallel-runs/run-1'
        self.manifest = json.loads((self.run / 'manifest.json').read_text())
        self.base = self.manifest['repositories']['service']['baseSha']
        self.train = {'status': 'built', 'repositoryRef': 'service', 'wave': 1, 'batchIds': ['B001'],
                      'candidateSha': self.head, 'baseSha': self.base, 'deliveryCommits': {'B001': self.head}}
        self.manifest['mergeTrains'] = {'service:wave-001': self.train}
        put(self.run, 'manifest.json', self.manifest)

    def payload(self, status='failed', attempt='attempt-1'):
        return {'status': status, 'attemptId': attempt, 'candidateSha': self.head, 'baseSha': self.base,
                'reviewScope': [{'batchId': 'B001', 'deliverySha': self.head, 'paths': ['Order.java']}],
                'reviewResult': {'status': status, 'candidateSha': self.head,
                                 **({'error': "Order.java:1 中文 ' $(literal) 合并丢失分支"} if status != 'passed' else {})}}

    def record(self, payload):
        return record_candidate_review(self.workspace, 'alpha', 'run-1', repository_ref='service', wave=1, payload=payload)

    def test_failure_and_later_pass_are_retained_without_changing_code_defect_counts(self):
        original_manifest = (self.run / 'manifest.json').read_bytes()
        with patch('hooks.parallel_merge_train.utc_now', return_value='2026-11-15T01:00:00Z'):
            first = self.record(self.payload())
            again = self.record(self.payload())
            passed = self.record(self.payload('passed', 'attempt-2'))
        self.assertTrue(again['reused'])
        self.assertEqual(first['reviewId'], again['reviewId'])
        self.assertEqual(len(list((self.run / 'candidate-reviews').glob('*.json'))), 2)
        self.assertEqual((self.run / 'manifest.json').read_bytes(), original_manifest)
        failed = collect(self.scope)['integration']['items'][0]
        self.assertEqual([h['status'] for h in failed['history']], ['failed', 'passed'])
        self.assertEqual(failed['status'], 'review_passed')
        self.assertEqual(failed['originalStatus'], 'failed')
        self.assertIn('合并丢失分支', failed['reason'])
        self.assertEqual(failed['history'][1]['reviewId'], passed['reviewId'])
        data = publish(self.scope)
        self.assertEqual(data['metrics']['reviewFixed']['value'], 0)
        self.assertEqual(data['metrics']['reviewFindings']['value'], 0)
        self.assertEqual(validate(self.scope)['reportId'], data['reportId'])
        Path(first['evidencePath']).write_text('{}')
        with self.assertRaises(ValueError):
            validate(self.scope)

    def test_changed_binding_or_scope_and_false_pass_are_rejected(self):
        for payload in [dict(self.payload(), candidateSha='a' * 40),
                        dict(self.payload(), reviewScope=[{'batchId': 'B999', 'deliverySha': self.head, 'paths': ['x']}]),
                        dict(self.payload(), status='passed')]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.record(payload)
        self.assertFalse((self.run / 'candidate-reviews').exists())

    def test_cli_preserves_unicode_and_literal_shell_characters(self):
        payload = self.payload()
        encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()
        with redirect_stdout(io.StringIO()) as output:
            result = main(['record-candidate-review', '--workspace', str(self.workspace), '--feature', 'alpha',
                           '--run-id', 'run-1', '--repository-ref', 'service', '--wave', '1', '--review-base64', encoded])
        self.assertEqual(result, 0)
        receipt = json.loads(output.getvalue())
        record = json.loads(Path(receipt['evidencePath']).read_text())
        self.assertEqual(record['reviewResult'], payload['reviewResult'])

    def test_resolved_and_open_conflicts_stay_separate_from_review_repairs(self):
        self.train.update(status='promoted', reviewedCandidateSha=self.head)
        self.manifest['mergeTrains']['service:wave-002'] = {**self.train, 'wave': 2, 'status': 'candidate_conflicted'}
        put(self.run, 'manifest.json', self.manifest)
        events = [
            {'event': 'merge_train_conflicted', 'at': 't1', 'repositoryRef': 'service', 'wave': 1,
             'batchIds': ['B001'], 'conflictedFiles': ['Order.java']},
            {'event': 'merge_train_conflict_resolved', 'at': 't2', 'repositoryRef': 'service', 'wave': 1, 'candidateSha': self.head},
            {'event': 'merge_train_conflicted', 'at': 't3', 'repositoryRef': 'service', 'wave': 2,
             'batchIds': ['B001'], 'conflictedFiles': ['Mapper.xml']},
        ]
        (self.run / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events) + '\n')
        data = collect(self.scope)
        items = data['integration']['items']
        self.assertEqual([r['status'] for r in items], ['conflict_resolved', 'blocked'])
        self.assertEqual(items[0]['history'][0]['line'], 1)
        self.assertEqual(items[0]['resolvedAt'], 't2')
        self.assertEqual(data['integration']['legacyReviewCount'], 2)
        self.assertEqual(data['metrics']['reviewFixed']['value'], 0)

    def test_corrupt_review_is_diagnostic_and_legacy_runs_need_no_new_files(self):
        self.assertEqual(collect(self.scope)['integration']['items'], [])
        receipt = self.record(self.payload())
        Path(receipt['evidencePath']).write_text('{"schemaVersion":"autodev.candidate-review.v1","status":"passed"}')
        data = collect(self.scope)
        self.assertEqual(data['integration']['items'], [])
        self.assertTrue(any('候选 Review 身份' in g['reason'] for g in data['gaps']))


if __name__ == '__main__':
    unittest.main()
