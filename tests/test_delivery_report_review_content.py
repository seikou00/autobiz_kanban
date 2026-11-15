from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_delivery_report import make_feature, put
from hooks.delivery_report.collect import collect
from hooks.delivery_report.content import document_sections
from hooks.delivery_report.review import attach_plan_scope
from hooks.delivery_report.publish import publish, validate, HTML
from hooks.delivery_report.sources import canonical, digest, resolve_scope


class ReviewContentTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workspace = Path(tmp.name).resolve()
        self.root, self.head = make_feature(self.workspace)
        self.scope = resolve_scope(self.workspace, 'alpha')
        self.run = self.root / '.parallel-runs/run-1'
        self.manifest = json.loads((self.run / 'manifest.json').read_text())
        self.review = json.loads((self.run / 'review.json').read_text())
        self.state = self.manifest['batches']['B001']['stageStates']['review']
        self.failure = {'issueId': 'BUG-1', 'type': 'implementation', 'message': '首次 Review 发现空列表缺少状态',
                        'taskId': 'T001', 'file': 'Order.java'}

    def save(self):
        self.review['digest'] = 'sha256:' + digest(canonical({k: v for k, v in self.review.items() if k != 'digest'}))
        put(self.run, 'review.json', self.review)
        put(self.run, 'manifest.json', self.manifest)

    def accept(self):
        self.review.update(attempt=2, createdAt='2026-11-13T02:00:00Z')
        self.review['metadata'].update(repairDisposition='single_repair_accepted', repairedFailure=self.failure)
        self.state['repairResolution'] = {'disposition': 'single_repair_accepted', 'failure': self.failure}

    def snapshot(self, failure=None, **extra):
        data = {'schemaVersion': 'autodev.engineering.repair.v1', 'runId': 'run-1', 'batchId': 'B001',
                'failedStage': 'review', 'failure': failure or self.failure, 'commitSha': self.head,
                'createdEpoch': 1794528000, 'revision': 1, **extra}
        name = 'engineering/B001/repairs/' + digest(canonical(data)) + '.json'
        put(self.run, name, data)
        return self.run / name

    def check(self, data):
        return next(c for c in data['quality']['checks'] if c['type'] == 'Batch Review' and c['batch'] == 'B001')

    def test_first_failure_fixed_and_advisory_stays_open(self):
        self.accept()
        path = self.snapshot()
        advisory = {'issueId': 'ADV-1', 'message': '外部枚举待核对'}
        self.review['metadata']['reviewAdvisories'] = [advisory]
        self.manifest['deferredIssues'] = [dict(advisory, batchId='B001', blocksWorkflow=False, status='open')]
        self.save()
        data = collect(self.scope)
        self.assertEqual([h['status'] for h in self.check(data)['history']], ['failed', 'single_repair_accepted'])
        self.assertEqual(self.check(data)['history'][0]['findings'][0]['title'], self.failure['message'])
        self.assertEqual(self.check(data)['history'][0]['findings'][0]['status'], 'unverified')
        self.assertEqual(data['metrics']['reviewFixed']['value'], 1)
        self.assertEqual(data['metrics']['open']['value'], 1)
        self.assertEqual(data['metrics']['nonBlocking']['value'], 1)
        fixed = next(r for r in data['risks'] if r['id'] == 'BUG-1')
        self.assertEqual(fixed['status'], 'resolved')
        self.assertEqual(len(fixed['resolutionSourceRefs']), 2)
        self.assertIn(path.relative_to(self.root).as_posix(), fixed['reviewHistoryRefs'])

    def test_repair_acceptance_does_not_fix_other_snapshot_findings(self):
        self.accept()
        self.snapshot()
        self.snapshot(dict(self.failure, issueId='BUG-2', message='另一个未验证问题'), revision=2)
        self.save()
        data = collect(self.scope)
        statuses = {r['id']: r['status'] for r in data['risks']}
        self.assertEqual(statuses['BUG-1'], 'resolved')
        self.assertEqual(statuses['BUG-2'], 'unverified')
        self.assertEqual(data['metrics']['reviewFixed']['value'], 1)
        self.assertEqual(data['metrics']['reviewFindings']['value'], 2)

    def test_first_review_finding_stays_in_review_filter_when_deferred(self):
        self.snapshot()
        self.manifest['deferredIssues'] = [dict(self.failure, batchId='B001', status='open', blocksWorkflow=False)]
        self.save()
        data = collect(self.scope)
        issue = next(r for r in data['risks'] if r['id'] == 'BUG-1')
        self.assertEqual(issue['origin'], 'deferred')
        self.assertTrue(issue['reviewFinding'])
        self.assertEqual(data['metrics']['reviewFindings']['value'], 1)
        self.assertEqual(data['metrics']['nonBlocking']['value'], 1)
        self.assertEqual(data['metrics']['reviewFixed']['value'], 0)

    def test_review_advisory_is_review_source_without_inflating_code_defects(self):
        note = {'issueId': 'ADV-1', 'message': '外部契约待核对'}
        self.review['metadata']['reviewAdvisories'] = [note]
        self.state['advisories'] = [note]
        self.manifest['deferredIssues'] = [dict(note, batchId='B001', stage='review', kind='review_advisory',
                                              status='open', blocksWorkflow=False)]
        self.save()
        data = collect(self.scope)
        self.assertEqual(len(data['risks']), 1)
        issue = data['risks'][0]
        self.assertEqual(issue['sourceStage'], 'review')
        self.assertEqual(issue['findingType'], 'advisory')
        self.assertEqual(issue['origin'], 'deferred')
        self.assertFalse(issue['reviewFinding'])
        self.assertEqual(data['metrics']['reviewRecords']['value'], 1)
        self.assertEqual(data['metrics']['reviewAdvisories']['value'], 1)
        self.assertEqual(data['metrics']['reviewFindings']['value'], 0)
        self.assertEqual(data['metrics']['reviewFixed']['value'], 0)
        self.assertEqual(data['metrics']['nonBlocking']['value'], 1)

    def test_batch_advisory_links_plan_tasks_without_claiming_task_level_attribution(self):
        self.review['metadata']['reviewAdvisories'] = [{'issueId': 'ADV-1', 'message': 'Batch 范围待核对'}]
        self.save()
        data = collect(self.scope)
        issue = next(r for r in data['risks'] if r['id'] == 'ADV-1')
        task = next(t for t in data['tasks'] if t['id'] == 'T001')
        self.assertIsNone(issue['task'])
        self.assertIsNone(issue['module'])
        self.assertEqual(issue['planScope']['taskIds'], ['T001'])
        self.assertEqual(issue['planScope']['modules'], sorted(task['modules']))
        self.assertEqual(issue['planScope']['basis'], 'batch_plan')
        self.assertEqual(issue['planScope']['sourceRefs'], ['plans/B001/plan.json'])

    def test_scope_preserves_explicit_task_and_limits_its_modules(self):
        tasks = [
            {'id': 'T001', 'batch': 'B001', 'modules': ['api'], 'sourceRefs': ['plans/B001/plan.json']},
            {'id': 'T002', 'batch': 'B001', 'modules': ['domain'], 'sourceRefs': ['plans/B001/plan.json']},
            {'id': 'T003', 'batch': 'B002', 'modules': ['other'], 'sourceRefs': ['plans/B002/plan.json']},
        ]
        issues = [{'task': None, 'batch': 'B001'}, {'task': 'T002', 'batch': 'B001', 'module': 'explicit-module'},
                  {'task': 'T999', 'batch': 'B001'}, {'task': None, 'batch': None}]
        attach_plan_scope(issues, tasks)
        self.assertEqual(issues[0]['planScope']['taskIds'], ['T001', 'T002'])
        self.assertEqual(issues[0]['planScope']['modules'], ['api', 'domain'])
        self.assertEqual(issues[1]['planScope']['taskIds'], ['T002'])
        self.assertEqual(issues[1]['planScope']['modules'], ['domain'])
        self.assertEqual(issues[1]['module'], 'explicit-module')
        self.assertEqual(issues[1]['planScope']['basis'], 'task_plan')
        self.assertEqual(issues[2]['planScope']['taskIds'], ['T999'])
        self.assertEqual(issues[2]['planScope']['modules'], [])
        self.assertEqual(issues[2]['planScope']['basis'], 'record_only')
        self.assertEqual(issues[3]['planScope']['taskIds'], [])

    def test_manifest_only_advisory_keeps_review_source_but_test_deferral_does_not(self):
        self.manifest['deferredIssues'] = [
            {'issueId': 'OLD-REVIEW', 'batchId': 'B001', 'kind': 'review_advisory', 'message': '历史评审建议'},
            {'issueId': 'TEST', 'batchId': 'B001', 'stage': 'test', 'message': '测试环境待补充'},
        ]
        self.save()
        data = collect(self.scope)
        sources = {r['id']: r['sourceStage'] for r in data['risks']}
        self.assertEqual(sources, {'OLD-REVIEW': 'review', 'TEST': 'test'})
        self.assertEqual(data['metrics']['reviewRecords']['value'], 1)
        self.assertEqual(data['metrics']['reviewAdvisories']['value'], 1)
        self.assertEqual(data['metrics']['reviewFindings']['value'], 0)

    def test_deferring_an_original_code_defect_preserves_its_finding_type(self):
        self.snapshot()
        self.manifest['deferredIssues'] = [dict(self.failure, batchId='B001', kind='review_advisory',
                                              status='open', blocksWorkflow=False)]
        self.save()
        data = collect(self.scope)
        issue = next(r for r in data['risks'] if r['id'] == 'BUG-1')
        self.assertEqual(issue['findingType'], 'code_defect')
        self.assertNotEqual(issue['category'], 'review_advisory')
        self.assertEqual(issue['sourceStage'], 'review')
        self.assertTrue(issue['reviewFinding'])
        self.assertEqual(data['metrics']['reviewRecords']['value'], 1)
        self.assertEqual(data['metrics']['reviewAdvisories']['value'], 0)
        self.assertEqual(data['metrics']['reviewFindings']['value'], 1)
        self.assertEqual(data['metrics']['reviewFixed']['value'], 0)

    def test_generic_review_pass_does_not_claim_historical_problem_fixed(self):
        self.snapshot()
        self.save()
        data = collect(self.scope)
        self.assertEqual(data['metrics']['reviewFixed']['value'], 0)
        self.assertEqual(data['metrics']['open']['value'], 1)
        self.assertEqual(next(r for r in data['risks'] if r['id'] == 'BUG-1')['status'], 'unverified')

    def test_legacy_final_repair_reference_is_labeled_as_reconstruction(self):
        self.accept()
        self.save()
        data = collect(self.scope)
        history = self.check(data)['history']
        self.assertEqual(history[0]['recordType'], 'repair_reference')
        self.assertIsNone(history[0]['commitSha'])
        self.assertEqual(data['metrics']['reviewFixed']['value'], 1)

    def test_manifest_registered_prior_evidence_retains_all_findings(self):
        prior = dict(self.review, evidenceId='old-review', attempt=1, createdAt='2026-11-12T01:00:00Z',
                     metadata={'findings': [self.failure]})
        prior['digest'] = 'sha256:' + digest(canonical({k: v for k, v in prior.items() if k != 'digest'}))
        put(self.run, 'stages/B001/review/001.json', prior)
        self.state['evidenceIds'] = ['old-review', self.review['evidenceId']]
        self.accept()
        self.save()
        data = collect(self.scope)
        history = self.check(data)['history']
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['evidenceId'], 'old-review')
        self.assertEqual(data['metrics']['reviewFindings']['value'], 1)
        self.assertEqual(data['metrics']['reviewFixed']['value'], 1)

    def test_invalid_current_evidence_preserves_history_without_fix_claim(self):
        self.accept()
        self.snapshot()
        self.save()
        invalid = dict(self.review, metadata={'repairDisposition': 'single_repair_accepted'})
        put(self.run, 'review.json', invalid)  # digest no longer matches
        data = collect(self.scope)
        self.assertFalse(self.check(data)['verified'])
        self.assertEqual(self.check(data)['status'], 'unverified')
        self.assertEqual(len(self.check(data)['history']), 1)
        self.assertEqual(data['metrics']['reviewFixed']['displayValue'], 0)
        self.assertEqual(data['metrics']['reviewFixed']['status'], 'missing')
        self.assertTrue(any('Review' in g['reason'] for g in data['gaps']))

    def test_corrupt_and_foreign_repair_snapshots_are_excluded(self):
        self.accept()
        path = self.snapshot()
        broken = json.loads(path.read_text())
        broken['failure']['message'] = '被篡改的内容'
        put(path.parent, path.name, broken)
        self.snapshot(dict(self.failure, issueId='FOREIGN'), runId='other-run')
        self.save()
        data = collect(self.scope)
        self.assertFalse(any(r['title'] == '被篡改的内容' or r['id'] == 'FOREIGN' for r in data['risks']))
        self.assertEqual(sum('修复快照' in g['reason'] for g in data['gaps']), 2)

    def test_conflicting_resolution_does_not_mark_both_findings_fixed(self):
        self.accept()
        self.state['repairResolution']['failure'] = dict(self.failure, issueId='OTHER', message='另一轮旧修复')
        self.save()
        data = collect(self.scope)
        self.assertIsNone(data['metrics']['reviewFixed']['value'])
        self.assertEqual(data['metrics']['reviewFixed']['displayValue'], 0)
        self.assertTrue(any('修复处置' in g['reason'] for g in data['gaps']))

    def test_missing_registered_history_keeps_known_counts_partial(self):
        self.accept()
        self.state['evidenceIds'] = ['STAGE-B001-REVIEW-001', self.review['evidenceId']]
        self.save()
        data = collect(self.scope)
        self.assertFalse(self.check(data)['historyComplete'])
        self.assertEqual(data['metrics']['reviewFindings']['value'], 1)
        self.assertEqual(data['metrics']['reviewFindings']['status'], 'partial')
        self.assertEqual(data['metrics']['reviewFixed']['value'], 1)
        gap = next(g for g in data['gaps'] if '历史 Review 证据' in g['reason'])
        self.assertIn(gap['id'], data['metrics']['reviewFindings']['diagnosticIds'])

    def test_first_pass_has_one_result_and_no_invented_failure(self):
        data = collect(self.scope)
        history = self.check(data)['history']
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['status'], 'passed')
        self.assertEqual(history[0]['findings'], [])
        self.assertEqual(data['metrics']['reviewFixed']['value'], 0)

    def test_document_sections_and_traceability_are_source_linked(self):
        (self.root / 'PRD.md').write_text('# 需求\n\n## 目标\n减少拜访查询操作\n\n## 范围\n扩展既有页面', encoding='utf-8')
        (self.root / 'proposal.md').write_text('# 提案\n\n## Scope\n支持筛选\n\n## 非目标\n不增加第二套查询', encoding='utf-8')
        (self.root / 'design.md').write_text('# 设计\n\n## Technical Design\n复用服务，追加筛选字段', encoding='utf-8')
        data = publish(self.scope)
        self.assertEqual(len(data['deliveryBrief']), 3)
        sections = [s for g in data['deliveryBrief'] for s in g['sections']]
        self.assertTrue(any('第二套' in s['text'] for s in sections))
        self.assertFalse(any(s['title'] == '非目标' for s in data['deliveryBrief'][0]['sections']))
        self.assertTrue(any('追加筛选' in s['text'] for s in sections))
        self.assertTrue(all(s['path'] in {r['path'] for r in data['sources']} and s['line'] > 0 for s in sections))
        self.assertIn('acceptance', data['tasks'][0])
        self.assertEqual(validate(self.scope)['reportId'], data['reportId'])
        (self.root / 'design.md').write_text('方案已变化')
        with self.assertRaises(ValueError):
            validate(self.scope)

    def test_full_markdown_is_preserved_beyond_summary_limits(self):
        text = '# 目标\n\n' + '这是必须完整保留的需求。' * 500 + '\n\n## 最后验收\n- [ ] 完整文档尾部标记'
        (self.root / 'PRD.md').write_text(text, encoding='utf-8')
        data = publish(self.scope)
        artifact = next(a for s in data['stageArtifacts'] for a in s['artifacts'] if a['path'] == 'PRD.md')
        self.assertEqual(artifact['markdown'], text)
        self.assertIn('完整文档尾部标记', artifact['markdown'])
        self.assertEqual(validate(self.scope)['reportId'], data['reportId'])
        (self.root / 'PRD.md').write_text(text + '\n新的尾部内容', encoding='utf-8')
        with self.assertRaises(ValueError):
            validate(self.scope)

    def test_short_inner_fence_does_not_end_a_long_outer_fence(self):
        text = '# 目标\n\n````markdown\n```python\n# 代码中的标题\n```\n````\n\n## 范围\n正文'
        sections = document_sections(text, 'PRD.md')
        self.assertEqual([part['title'] for part in sections], ['目标', '范围'])
        self.assertIn('# 代码中的标题', sections[0]['text'])

    def test_markdown_parser_and_sanitizer_are_version_bound(self):
        from hooks.delivery_report.publish import templates
        publish(self.scope)
        for name in ('report-markdown.js', 'vendor/marked.umd.js', 'vendor/purify.min.js'):
            assets = templates()
            assets[name] += '\n// changed renderer version\n'
            with self.subTest(asset=name), patch('hooks.delivery_report.publish.templates', return_value=assets):
                with self.assertRaisesRegex(ValueError, '模板'):
                    validate(self.scope)

    def test_markdown_fences_are_not_section_headings_and_scripts_stay_text(self):
        text = '# 目标\n<script>window.injected=true</script>\n```python\n# code comment\n```\n## 范围\n' + '边界' * 2000
        sections = document_sections(text, 'PRD.md')
        self.assertEqual([s['title'] for s in sections], ['目标', '范围'])
        self.assertIn('# code comment', sections[0]['text'])
        self.assertTrue(sections[-1]['truncated'])
        (self.root / 'PRD.md').write_text(text, encoding='utf-8')
        publish(self.scope)
        self.assertNotIn('<script>window.injected=true</script>', (self.root / HTML).read_text())
