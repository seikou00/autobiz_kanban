from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_batched_plan import task, batch_entry, batch_plan, root_plan
from hooks.delivery_report.collect import collect
from hooks.delivery_report.publish import publish, validate, HTML, JSON
from hooks.delivery_report.sources import Inputs, canonical, digest, resolve_scope
from hooks.parallel_validation_ownership import build_pipeline_contract
from hooks.route_checkpoint import resolve_route
from hooks.rollback_stage import prepare_stage_rollback, execute_stage_rollback
from hooks.state_checkpoint import check_stage_outputs


def put(directory, name, obj):
    path = directory / name
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def make_feature(workspace):
    root = workspace / ".autobizdevops/features/alpha"
    root.mkdir(parents=True)
    put(workspace, '.autobizdevops/state.json',
        {"features": {"alpha": {"feature": "alpha", "checkpoint": "code_done"}}})
    (root / "PRD.md").write_text("# 需求\n\n显示订单名称 <script>alert(1)</script>", encoding="utf-8")
    repo = workspace / "service"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args], text=True).strip()
    git("init", "-q")
    git("commit", "--allow-empty", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (repo / "Order.java").write_text(
        '@RestController\n@RequestMapping("/api")\npublic class Order {\n@GetMapping("/orders")\npublic List<?> list() {return null;}\n}\n')
    git("add", "Order.java")
    git("commit", "-qm", "add endpoint")
    head = git("rev-parse", "HEAD")
    groups = {}
    entries = []
    ev = []
    for i in (1, 2):
        bid, tid, rid = f"B00{i}", f"T00{i}", f"run-{i}"
        impl_id, compile_id = f"ev_000{i}", f"ev_001{i}"
        tr = f"task-{i}"
        t = task(tid, status="done")
        t.update(latestImplementationEvidenceId=impl_id,
                 implementationEvidenceIds=[impl_id], evidenceIds=[impl_id])
        b = batch_plan(bid, [t])
        b.update(status="done", completedTaskCount=1, mergeCommitSha=head, deliveryRunId=rid)
        groups[bid] = b
        entry = batch_entry(bid, [tid])
        entry.update(status="done", mergeCommitSha=head, deliveryRunId=rid)
        entries.append(entry)
        put(root, f"plans/{bid}/plan.json", b)
        attempt = {"attemptId": tr, "runId": tr, "parallelRunId": rid, "batchId": bid, "sourceDigest": "s", "passed": True,
                   "argv": ["mvn", "compile"], "exitCode": 0, "logExitCode": 0, "logPath": f".compile-logs/{tr}.log"}
        log = (root / attempt["logPath"])
        log.parent.mkdir(exist_ok=True)
        log.write_bytes(b"compile finished")
        attempt["outputSha256"] = digest(log.read_bytes())
        ev.extend([{"evidenceId": impl_id, "featureId": "alpha", "action": "implementation", "taskId": tid, "runId": tr,
                    "changedFiles": ["Order.java"], "productionCompileEvidenceId":compile_id},
                   {"evidenceId": compile_id, "featureId": "alpha", "action": "production_compile", "taskId": tid, "runId": tr, "compile": attempt}])
        put(root, f".task-runs/{tid}/{tr}.json", {"featureId": "alpha", "taskId": tid, "runId": tr, "parallelRunId": rid, "batchId": bid,
                                                  "compileAttempts": [{**attempt, "evidenceId": compile_id}], "latestCompileAttemptId": tr})
    plan = root_plan(batches=entries, active=None)
    plan.update(status="done")
    put(root, "plan.json", plan)
    revision = build_pipeline_contract(plan, groups)["planRevision"]
    for bid, b in groups.items():
        rid = b["deliveryRunId"]
        run = root / ".parallel-runs" / rid
        audit = {"runId": rid, "errors": [], "blockingErrors": [], "passed": True, "repositoryHeads": {
            "service": head}, "pipelineRevision": revision, "policy": "report_only"}
        evidence = {"stage": "review", "batchId": bid, "evidenceId": "review-" + bid,
                    "inputs": {"planRevision": revision, "batchCommit": head}, "metadata": {}}
        evidence["digest"] = "sha256:" + digest(canonical(evidence))
        put(run, "review.json", evidence)
        put(run, "manifest.json", {"featureId": "alpha", "runId": rid, "pipeline": {"planRevision": revision}, "status": "succeeded", "workflowCompletedAt": "2025-01-01",
                                   "repositories": {"service": {"gitRoot": str(repo), "baseSha": base, "headSha": head}},
                                   "batches": {bid: {"status": "merged", "taskIds": b["tasks"] and [b["tasks"][0]["id"]], "commitSha": head, "mergeCommitSha": head, "stageStates": {"review": {"status": "passed", "latestEvidenceId": evidence["evidenceId"], "evidencePath": str(run / "review.json")}}}},
                                   "finalEvidenceAggregate": audit})
        put(run, "final-evidence-aggregate.json", audit)
    (root / "evidence").mkdir()
    (root / "evidence/EVIDENCE.jsonl").write_text("\n".join(json.dumps(r) for r in ev) + "\n")
    return root, head


class DeliveryReportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workspace = Path(tmp.name).resolve()
        self.root, self.head = make_feature(self.workspace)
        self.scope = resolve_scope(self.workspace, "alpha")

    def test_end_to_end(self):
        self.assertEqual(resolve_route(self.workspace, "alpha")[
                         0]["recommendedNextSkill"], "autodev-delivery-report")
        r = publish(self.scope)
        self.assertEqual((r["metrics"]["delivered"]["value"], r["metrics"]
                          ["api"]["value"]), (2, 1), r["gaps"])
        self.assertEqual(len(r["changes"]), 1)
        self.assertEqual(len(r["quality"]["checks"]), 4)
        self.assertIsNone(r["metrics"]["unit"]["value"])
        self.assertEqual(r["metrics"]["lineCoverage"]["displayValue"], 0)
        self.assertEqual(r["reportId"], publish(self.scope)["reportId"])
        self.assertIsNone(check_stage_outputs(self.workspace, "alpha", "autodev-delivery-report"))
        self.assertNotIn("<script>alert(1)</script>", (self.root / HTML).read_text())
        (self.root / "PRD.md").write_text("new scope")
        with self.assertRaisesRegex(ValueError, "不一致"):
            validate(self.scope)

    def test_chart_asset_is_bound_to_the_offline_template(self):
        from hooks.delivery_report.publish import templates
        publish(self.scope)
        self.assertIn('const ReportCharts', (self.root / HTML).read_text())
        changed = templates()
        changed['report-charts.js'] += '\n// a different chart asset version\n'
        with patch('hooks.delivery_report.publish.templates', return_value=changed):
            with self.assertRaisesRegex(ValueError, '模板'):
                validate(self.scope)

    def test_interrupted_publish_and_archive(self):
        old = publish(self.scope)
        state = self.workspace / ".autobizdevops/state.json"
        dest = self.workspace / ".autobizdevops/archive/alpha-iter2"
        dest.parent.mkdir()
        self.root.rename(dest)
        put(self.workspace, ".autobizdevops/state.json",
            {"features": {"alpha": {"feature": "alpha", "checkpoint": "archived", "iteration": "2"}}})
        unchanged = state.read_bytes()
        scope = resolve_scope(self.workspace, "alpha")
        with patch("hooks.delivery_report.publish.write_text", side_effect=OSError("interrupt")):
            with self.assertRaises(OSError):
                publish(scope)
        self.assertTrue((dest / ".delivery-report/pending").is_dir())
        r = publish(scope)
        self.assertNotEqual(old["reportId"], r["reportId"])
        self.assertEqual(len(r["quality"]["checks"]), 4)
        self.assertEqual(state.read_bytes(), unchanged)
        self.assertFalse((dest / ".delivery-report/pending").exists())

    def test_test_inputs_require_matching_heads(self):
        external = {"featureId": "alpha", "repositoryHeads": {"service": "stale"}, "aiGeneratedLines": 0,
                    "unit": [{"id": "a", "result": "PASS"}, {"id": "b", "result": "NOT_RUN"}], "e2e": [],
                    "lineCoverage": {"covered": 1, "total": 2}}
        put(self.root, "DELIVERY_MEASUREMENTS.json", external)
        self.assertIsNone(collect(self.scope)["metrics"]["unit"]["value"])
        external["repositoryHeads"]["service"] = self.head
        put(self.root, "DELIVERY_MEASUREMENTS.json", external)
        r = collect(self.scope)
        self.assertEqual(r["metrics"]["unit"]["value"], 1)
        self.assertEqual(r["metrics"]["untested"]["value"], 1)
        self.assertEqual(r["metrics"]["lineCoverage"]["value"], 50)
        self.assertEqual(r["metrics"]["aiLines"]["status"], "measured")
        self.assertEqual(r["gates"][0]["status"], "pending")

    def test_partial_plan_keeps_known_batches_and_original_denominator(self):
        (self.root / "plans/B002/plan.json").unlink()
        data = collect(self.scope)
        self.assertEqual([t["id"] for t in data["tasks"]], ["T001", "T002"])
        self.assertEqual(data["tasks"][1]["status"], "unverified")
        self.assertEqual(data["metrics"]["delivered"]["value"], 1)
        self.assertEqual(data["metrics"]["completion"]["value"], 50)
        self.assertEqual(data["metrics"]["completion"]["status"], "partial")

    def test_unbound_measurements_and_explicit_gate_failure(self):
        extra = {"repositoryHeads": {"service": self.head}, "unit": [{"id": "case", "result": "PASS"}],
                 "e2e": [], "gates": {"tests": "failed"}}
        put(self.root, "DELIVERY_MEASUREMENTS.json", extra)
        self.assertIsNone(collect(self.scope)["metrics"]["unit"]["value"])
        put(self.root, "DELIVERY_MEASUREMENTS.json", dict(extra, featureId="alpha"))
        self.assertEqual(collect(self.scope)["gates"][0]["status"], "failed")

    def test_consecutive_run_measurements_bind_latest_delivered_head(self):
        repo = self.workspace / "service"
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=t@example.invalid",
                        "commit", "--allow-empty", "-qm", "follow-up delivery"], check=True)
        end = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        run = self.root / ".parallel-runs/run-2"
        manifest = json.loads((run / "manifest.json").read_text())
        manifest["repositories"]["service"].update(baseSha=self.head, headSha=end)
        manifest["finalEvidenceAggregate"]["repositoryHeads"]["service"] = end
        put(run, "manifest.json", manifest)
        put(run, "final-evidence-aggregate.json", manifest["finalEvidenceAggregate"])
        put(self.root, "DELIVERY_MEASUREMENTS.json", {"featureId": "alpha", "repositoryHeads": {"service": end}, "aiGeneratedLines": 12})
        data = collect(self.scope)
        self.assertEqual(len(data["changes"]), 1)
        self.assertEqual(data["metrics"]["aiLines"]["value"], 12)

    def test_missing_task_run_and_changed_compile_log(self):
        (self.root / ".task-runs/T002/task-2.json").unlink()
        (self.root / ".compile-logs/task-1.log").write_bytes(b"changed")
        data = collect(self.scope)
        self.assertEqual(data["metrics"]["delivered"]["value"], 1)
        self.assertEqual(data["metrics"]["implemented"]["value"], 1)
        self.assertFalse(any(c["type"] == "生产编译" for c in data["quality"]["checks"]))

    def test_empty_archived_iteration_excludes_active_feature(self):
        archived = self.workspace / ".autobizdevops/archive/alpha-iter1"
        archived.mkdir(parents=True)
        data = publish(resolve_scope(self.workspace, "alpha", 1))
        self.assertTrue(all(m["displayValue"] == 0 for m in data["metrics"].values()))
        self.assertTrue(all(g["status"] == "pending" for g in data["gates"]))
        self.assertFalse(data["runIds"])
        self.assertFalse((self.root / HTML).exists())

    def test_rollback_to_code_archives_report_pair(self):
        publish(self.scope)
        plan = prepare_stage_rollback(workspace=self.workspace, feature="alpha", stage="dev.code")
        self.assertTrue(plan.ok, plan.errors)
        self.assertIn(self.root / HTML, plan.artifact_paths)
        self.assertIn(self.root / JSON, plan.artifact_paths)
        result = execute_stage_rollback(plan)
        self.assertTrue(result.ok, result.errors)
        self.assertFalse((self.root / HTML).exists())
        self.assertFalse((self.root / JSON).exists())

    def test_ai_additions_are_collected_from_delivered_tasks_without_manual_file(self):
        data = collect(self.scope)
        self.assertEqual(data['metrics']['aiLines']['value'], 6)
        self.assertEqual(data['metrics']['aiLines']['basis'], 'delivered_task_diff')
        self.assertEqual(data['tasks'][0]['implementationPoints'], ['implement behavior', 'cover boundary'])
        self.assertEqual(data['batches'][0]['tasks'][0]['implementationPoints'], ['implement behavior', 'cover boundary'])

    def test_ai_recovers_absolute_paths_and_verification_only_tasks(self):
        path = self.root / 'evidence/EVIDENCE.jsonl'
        records = [json.loads(line) for line in path.read_text().splitlines()]
        for record in records:
            if record['action'] != 'implementation':
                continue
            record['changedFiles'] = []
            if record['taskId'] == 'T001':
                record['fileChanges'] = [{'path': 'C:\\worktree\\Order.java'}]
            else:
                record['completionMode'] = 'verify'
                record['implementation'] = {'noCodeChange': True}
        path.write_text('\n'.join(json.dumps(r) for r in records) + '\n')
        state_path = self.root / '.task-runs/T001/task-1.json'
        state = json.loads(state_path.read_text())
        state['repositories'] = [{'id': 'service', 'path': 'C:\\worktree'}]
        put(self.root, state_path.relative_to(self.root), state)
        data = collect(self.scope)
        self.assertEqual(data['metrics']['aiLines']['value'], 6)
        self.assertEqual(data['metrics']['aiLines']['status'], 'measured')
        self.assertEqual(data['metrics']['aiLines']['details'][1]['status'], 'verified_zero')

    def test_ai_uses_batch_sealed_range_when_task_file_list_is_missing(self):
        path = self.root / 'evidence/EVIDENCE.jsonl'
        records = [json.loads(line) for line in path.read_text().splitlines()]
        for record in records:
            if record['action'] == 'implementation':
                record['changedFiles'] = []
        path.write_text('\n'.join(json.dumps(r) for r in records) + '\n')
        for i in (1, 2):
            run = self.root / f'.parallel-runs/run-{i}'
            manifest = json.loads((run / 'manifest.json').read_text())
            manifest['batches'][f'B00{i}']['startSha'] = manifest['repositories']['service']['baseSha']
            put(run, 'manifest.json', manifest)
        value = collect(self.scope)['metrics']['aiLines']
        self.assertEqual(value['value'], 6)
        self.assertTrue(all(d['basis'] == 'batch_delivery_diff' for d in value['details']))

    def test_ai_recovers_paths_from_task_run_file_snapshots(self):
        path = self.root / 'evidence/EVIDENCE.jsonl'
        records = [json.loads(line) for line in path.read_text().splitlines()]
        for record in records:
            if record['action'] == 'implementation':
                record['changedFiles'] = []
        path.write_text('\n'.join(json.dumps(r) for r in records) + '\n')
        for i in (1, 2):
            path = self.root / f'.task-runs/T00{i}/task-{i}.json'
            state = json.loads(path.read_text())
            state['repositories'] = [{'id': 'service', 'path': str(self.workspace / 'service'), 'snapshot': {}}]
            state['finalRepositories'] = [{'id': 'service', 'snapshot': {'Order.java': 'sealed-file-hash'}}]
            put(self.root, path.relative_to(self.root), state)
        self.assertEqual(collect(self.scope)['metrics']['aiLines']['value'], 6)

    def test_ai_retains_measured_values_when_another_task_has_no_attribution(self):
        path = self.root / 'evidence/EVIDENCE.jsonl'
        records = [json.loads(line) for line in path.read_text().splitlines()]
        for record in records:
            if record['action'] == 'implementation' and record['taskId'] == 'T002':
                record['changedFiles'] = []
        path.write_text('\n'.join(json.dumps(r) for r in records) + '\n')
        value = collect(self.scope)['metrics']['aiLines']
        self.assertEqual(value['value'], 6)
        self.assertEqual(value['status'], 'partial')
        self.assertTrue(value['details'][1]['unresolved'])

    def test_diagnostics_merge_metrics_and_files_without_duplicating_root_cause(self):
        src = Inputs(self.scope)
        for name, path in [('api', 'a.java'), ('apiAdded', 'b.java'), ('apiModified', 'a.java')]:
            src.gap('接口识别', '方法引用待核对', category='coverage', reason_code='UNRESOLVED_CALL',
                    scope={'repository': 'service'}, metrics=[name], files=[path])
        self.assertEqual(src.gaps, [])
        self.assertEqual(len(src.diagnostics), 1)
        self.assertEqual(src.diagnostics[0]['affectedFiles'], ['a.java', 'b.java'])
        self.assertEqual(len(src.diagnostics[0]['affectedMetrics']), 3)
        src.gap('missing.json', '缺少必需来源')
        self.assertEqual(len(src.gaps), 1)

    def test_uncollected_quality_is_explained_once_outside_actual_gaps(self):
        data = collect(self.scope)
        self.assertFalse(any(g['field'] in {'unit', 'e2e', 'lineCoverage', 'branchCoverage', 'testing'} for g in data['gaps']))
        notes = [d for d in data['diagnostics'] if d['category'] == 'info' and d['field'] == 'testing']
        self.assertEqual(len(notes), 1)
        self.assertIn('lineCoverage', notes[0]['affectedMetrics'])
        self.assertEqual(data['metrics']['unit']['status'], 'not_collected')

    def test_repaired_review_findings_and_nonblocking_issues_are_preserved_once(self):
        run = self.root / '.parallel-runs/run-1'
        manifest = json.loads((run / 'manifest.json').read_text())
        review = json.loads((run / 'review.json').read_text())
        failure = {'type': 'implementation', 'message': 'T001 空列表遗漏状态字段', 'taskId': 'T001', 'file': 'Order.java'}
        advisory = {'issueId': 'ADV-1', 'message': '外部接口契约待核对', 'type': 'external_uncertainty'}
        review['metadata'].update(repairDisposition='single_repair_accepted', repairedFailure=failure, reviewAdvisories=[advisory])
        review['digest'] = 'sha256:' + digest(canonical({k: v for k, v in review.items() if k != 'digest'}))
        put(run, 'review.json', review)
        manifest['batches']['B001']['stageStates']['review'].update(
            repairResolution={'disposition': 'single_repair_accepted', 'failure': failure}, advisories=[advisory])
        manifest['deferredIssues'] = [dict(advisory, batchId='B001', kind='review_advisory', blocksWorkflow=False,
                                           status='open', disposition='recorded_advisory')]
        manifest['finalEvidenceAggregate']['deferredIssues'] = manifest['deferredIssues']
        put(run, 'manifest.json', manifest)
        put(run, 'final-evidence-aggregate.json', manifest['finalEvidenceAggregate'])
        data = collect(self.scope)
        self.assertEqual(len(data['risks']), 2, data['risks'])
        self.assertEqual(data['metrics']['reviewFindings']['value'], 1)
        self.assertEqual(data['metrics']['nonBlocking']['value'], 1)
        finding = next(r for r in data['risks'] if r['origin'] == 'review')
        self.assertEqual(finding['status'], 'resolved')
        self.assertEqual(data['metrics']['open']['value'], 1)
        self.assertEqual(len(finding['sourceRefs']), 2)

    def test_stale_narrative_and_mismatched_pair(self):
        facts = collect(self.scope)
        with self.assertRaisesRegex(ValueError, "快照"):
            publish(self.scope, {"inputDigest": "old"})
        r = publish(self.scope, {"inputDigest": facts["snapshot"]["inputDigest"], "summary": "订单接口已交付",
                                 "nextActions": [], "sourceRefs": ["PRD.md"]})
        self.assertEqual(r["narrative"]["summary"], "订单接口已交付")
        (self.root / HTML).write_text("bad")
        with self.assertRaises(ValueError):
            validate(self.scope)


if __name__ == "__main__":
    unittest.main()
