"""Project the selected Feature's immutable delivery evidence into report data."""
from __future__ import annotations

import json
from pathlib import Path

from hooks.parallel_validation_ownership import build_pipeline_contract
from hooks.plan_json import (load_plan_bundle, validate_plan_data, validate_batch_plan_data,
                             defer_to_test_stages_enabled, task_contract_sha256, normalize_status)
from hooks.frontend_verification import digest as frontend_digest
from .changes import summarize
from .review import read_review, risk, attach_plan_scope
from .content import document_sections, delivery_brief
from .integration import read_integration
from .sources import Inputs, canonical, component, digest, metric, rows

STREAM = "evidence/EVIDENCE.jsonl"
PLAN = "plan.json"
SCHEMA = "autodev.delivery-report.v1"


def excerpt(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()
             and not l.strip().startswith(('#', '---', '<!--', '```'))]
    return '\n'.join(lines)[:600]


def stages(src):
    result = []
    for node in src.scope.nodes:
        if node.get("group") == "Ops":
            break
        stage = {"id": node["id"], "label": node["label"],
                 "skipped": bool(node.get("skipped")), "artifacts": []}
        result.append(stage)
        if stage["skipped"] or stage["id"] == "dev.delivery_report":
            continue
        specs = list(node.get("artifacts", {}).get("outputs", []))
        if stage["id"] == "dev.code":
            specs.extend({"path": name} for name in ("FRONTEND_ROUTE.json", "FEATURE_API_DETAIL.md"))
        for spec in specs:
            pattern = spec.get("path", "")
            if not pattern or pattern.startswith("/") or ".." in Path(pattern).parts:
                continue
            for path in sorted(src.root.glob(pattern)):
                if not path.is_file():
                    continue
                name = path.relative_to(src.root).as_posix()
                value = src.read(name)
                if value is not None:
                    stage["artifacts"].append(
                        {"path": name, "summary": excerpt(value) if name.casefold().endswith(".md") else None,
                         "sections": document_sections(value, name) if name.casefold().endswith(".md") else [],
                         "markdown": value if name.casefold().endswith(".md") else None})
            if spec.get("required") and not any(src.root.glob(pattern)):
                src.gap(pattern, "缺少阶段产物")
    return result


def evidence_index(src):
    result, duplicates = {}, set()
    for i, line in enumerate((src.read(STREAM) or "").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict) or rec.get("featureId") != src.scope.feature:
                raise ValueError("Feature 不匹配")
            key = rec["evidenceId"]
            if key in result:
                duplicates.add(key)
                raise ValueError("重复的 evidenceId")
            result[key] = rec
        except (KeyError, ValueError, TypeError) as exc:
            src.gap(f"{STREAM}#{i}", str(exc))
    for key in duplicates:
        result.pop(key, None)
    return result



def read_run(src, run_id, revision):
    prefix = f".parallel-runs/{run_id}/"
    ref = prefix + "manifest.json"
    m = src.obj(ref)
    if m.get("featureId") != src.scope.feature or m.get("runId") != run_id:
        src.gap(ref, "缺少与 Feature 和 run 对应的 manifest")
        return {}
    for field in ("pipeline", "repositories", "workspaceSync", "batches"):
        if field in m and not isinstance(m[field], dict):
            src.gap(ref, field + " 格式无效")
            return {}
    if revision and (m.get("pipeline") or {}).get("planRevision") not in (revision, None):
        src.gap(ref, "运行记录与本次 Plan 修订不匹配")
        return {}
    aggref = prefix + "final-evidence-aggregate.json"
    agg = src.obj(aggref)
    embedded = m.get("finalEvidenceAggregate") or {}
    if not isinstance(embedded, dict):
        src.gap(ref, "内嵌审计格式无效")
        embedded = {}
    if not agg:
        agg, aggref = embedded, ref
    if any(k in agg and not isinstance(agg[k], list) for k in ("errors", "cleanupPending")):
        src.gap(aggref, "审计明细格式无效")
        agg = {}
    heads = {k: v.get("headSha") for k, v in m.get("repositories", {}).items() if isinstance(v, dict)}
    if agg and (agg.get("runId") != run_id or agg.get("repositoryHeads") != heads
                or (revision and agg.get("pipelineRevision") != revision)
                or (embedded and agg != embedded)):
        src.gap(aggref, "证据汇总与 manifest 不一致")
        agg = {}
    legacy = m.get("finalVerification") if not embedded and not agg else None
    if not agg:
        src.gap(aggref, "缺少当前版本的证据审计")
    issues = [risk(x, ref, origin="deferred") for x in rows(m.get("deferredIssues"))]
    issues.extend(risk(x, aggref, origin="deferred") for x in rows(agg.get("deferredIssues")))
    issues.extend(risk(x, aggref, origin="audit") for x in agg.get("errors", []))
    if agg.get("passed") is False and not agg.get("errors"):
        issues.append(risk("证据审计未通过", aggref))
    repos = []
    sync = []
    for key, v in m.get("repositories", {}).items():
        if not isinstance(v, dict):
            continue
        repos.append({"ref": key, "runId": run_id, "root": v.get("gitRoot"), "baseSha": v.get("baseSha"),
                      "headSha": v.get("headSha"), "branch": v.get("sourceBranch") or v.get("branch"), "sourceRefs": [ref]})
    for key in sorted(heads.keys() - m.get("workspaceSync", {}).keys()):
        src.gap(ref + "#workspaceSync." + key, "仓库缺少工作区同步记录")
    for key, v in m.get("workspaceSync", {}).items():
        if not isinstance(v, dict):
            continue
        status = v.get("status")
        if status == "synced" and v.get("lastSyncedSha") != heads.get(key):
            status = "pending"
        sync.append({"repository": key, "runId": run_id, "status": status, "sha": v.get("lastSyncedSha"),
                     "branch": v.get("sourceBranch"), "reason": v.get("reasonCode") or v.get("error"), "sourceRefs": [ref]})
        if status not in {"synced", "skipped"}:
            issues.append(risk(f"{key} 工作区同步 {status or '待核验'}", ref))
    return {"manifest": m, "ref": ref, "repositories": repos, "sync": sync, "risks": issues,
            "audit": {"runId": run_id, "status": m.get("status"), "passed": agg.get("passed"), "errors": agg.get("errors", []),
                      "cleanupPending": agg.get("cleanupPending", []), "legacy": legacy, "sourceRefs": [aggref] if agg else [ref]}}



def task_facts(src, task, batch_id, binding, merged, evidence):
    tid = task["id"]
    plan = f"plans/{batch_id}/plan.json"
    impl = evidence.get(task.get("latestImplementationEvidenceId"), {})
    if impl.get("action") != "implementation" or impl.get("taskId") != tid:
        impl = {}
    task_run = impl.get("runId")
    state_rel = f".task-runs/{tid}/{task_run}.json" if component(task_run) else None
    state = src.obj(state_rel) if state_rel else {}
    bound = bool(state and state.get("runId") == task_run and state.get("taskId") == tid and state.get("batchId") == batch_id
                 and state.get("parallelRunId") == binding and binding and state.get("featureId", src.scope.feature) == src.scope.feature
                 and state.get("taskContractSha256", task_contract_sha256(task)) == task_contract_sha256(task))
    if impl and not bound:
        src.gap(tid, "任务执行记录与 Batch 不匹配")
        impl = {}
    status = normalize_status(task.get("status"))
    delivered = status == "done" and bool(impl) and merged
    if status == "done" and not delivered:
        src.gap(tid, "任务完成状态缺少匹配的合入或实现证据")
    refs = [plan]
    if impl:
        refs.extend([STREAM, state_rel])
    if delivered:
        refs.append(f".parallel-runs/{binding}/manifest.json")
    task_row = {"id": tid, "title": task.get("title") or task.get("goal"), "batch": batch_id, "repository": task.get("workspaceRef"),
                "implementationPoints": task.get("implementationPoints", []),
                "completionMode": impl.get("completionMode") or state.get("completionMode") or state.get("executionMode") or task.get("executionMode"),
                "noCodeChange": (impl.get("implementation") or {}).get("noCodeChange") is True,
                "fileChanges":rows(impl.get("fileChanges")) or rows(state.get("fileChanges")),
                "pathContext":{"repositories":[{k:r.get(k) for k in ('id','path')} for r in rows(state.get('repositories'))],
                               "scopeWorkspaces":rows(state.get('scopeWorkspaces'))}, "deliveryRunId": binding,
                "modules": (task.get("scope") or {}).get("modules", []), "outcome": impl.get("outcome") or task.get("goal"), "blockers": task.get("blockers", []),
                "owner": task.get("owner"), "status": "delivered" if delivered else "unverified" if status == "done" else status,
                "implemented": bool(impl) and status in {"implemented", "validating", "done"}, "delivered": delivered,
                "specRefs": task.get("specRefs", []), "designRefs": task.get("designRefs", []),
                "acceptance": task.get("acceptanceCriteria") or task.get("verificationIntent"), "files": impl.get("changedFiles") or state.get("changedFiles", []),
                "evidenceId": impl.get("evidenceId"), "sourceRefs": refs}
    if bound and not task_row['files'] and not task_row['fileChanges']:
        final = {r.get('id'):r for r in rows(state.get('finalRepositories'))}
        for before in rows(state.get('repositories')):
            after = final.get(before.get('id'),{})
            old, new = before.get('snapshot'), after.get('snapshot')
            if isinstance(old,dict) and isinstance(new,dict):
                task_row['files'].extend(f"{before['id']}:{path}" for path in sorted(old.keys() | new.keys())
                                         if old.get(path)!=new.get(path))
        if not task_row['files'] and isinstance(state.get('snapshot'),dict) and isinstance(state.get('finalSnapshot'),dict):
            old, new = state['snapshot'],state['finalSnapshot']
            task_row['files'] = [path for path in sorted(old.keys() | new.keys()) if old.get(path)!=new.get(path)]
    checks = []
    if bound:
        attempt = next((x for x in rows(state.get("compileAttempts")) if x.get(
            "attemptId") == state.get("latestCompileAttemptId")), {})
        ev = evidence.get(attempt.get("evidenceId"), {})
        result = ev.get("compile", {})
        log = src.referenced(result.get("logPath")) if isinstance(result, dict) else None
        log_digest = result.get("outputSha256") if isinstance(result, dict) else None
        content = src.binary(log) if log else None
        log_valid = (content is not None and digest(content) ==
                     log_digest and log_digest == attempt.get("outputSha256"))
        if (result and log_valid and result.get("passed") == (
                result.get("exitCode") == 0 and result.get("logExitCode") == 0)
            and ev.get("action") == "production_compile" and ev.get("taskId") == tid and ev.get("runId") == task_run
            and result.get("parallelRunId") == binding and result.get("batchId") == batch_id
            and result.get("attemptId") == attempt.get("attemptId") and result.get("sourceDigest") == attempt.get("sourceDigest")
                and result.get("sourceDigest") and impl.get("productionCompileEvidenceId") == ev.get("evidenceId")):
            checks.append({"type": "生产编译", "task": tid, "batch": batch_id, "status": "passed" if result.get("passed") is True else "failed",
                           "argv": result.get("argv"), "exitCode": result.get("exitCode"), "log": result.get("logPath"),
                           "sourceRefs": [state_rel, STREAM, log], "evidenceId": ev.get("evidenceId")})
        elif result or state.get("productionCompileRequired"):
            src.gap(tid + ".compile", "未找到与当前任务执行绑定的编译结果")
        front = src.referenced(state.get("frontendVerificationPath"))
        if front:
            f = src.obj(front)
            if (f.get("taskId") == tid and f.get("taskRunId") == task_run
                and f.get("taskContractSha256") == task_contract_sha256(task)
                    and isinstance(f.get("binding"), dict) and f.get("bindingDigest") == frontend_digest(f["binding"])):
                checks.append({"type": "前端检查", "task": tid, "batch": batch_id, "status": f.get("status"),
                               "visual": f.get("visual", {}).get("status"), "projectCheck": f.get("projectCheck", {}).get("status"),
                               "screenshots": (f.get("capture") or {}).get("screenshots", []),
                               "sourceRefs": [front, state_rel]})
            else:
                src.gap(front, "前端验证与任务不匹配")
    return task_row, checks


def plan_batches(src):
    """Keep the validated index and unaffected batches when historical files are missing."""
    root = src.obj(PLAN)
    if root.get("featureId") != src.scope.feature:
        src.gap(PLAN, "缺少本 Feature 的计划")
        return {}, {}, None
    try:
        errors = validate_plan_data(root)
    except (TypeError, AttributeError) as exc:
        errors = [str(exc)]
    if errors:
        src.gap(PLAN, ", ".join(errors))
        return {}, {}, None
    entries = root["batches"]
    all_ids = [tid for entry in entries for tid in entry["taskIds"]]
    if len(set(all_ids)) != len(all_ids):
        src.gap(PLAN, "不同 Batch 使用同一任务 ID")
        return {}, {}, None
    candidates = {}
    for entry in entries:
        bid, name = entry["id"], entry["path"]
        batch = src.obj(name)
        try:
            errs = validate_batch_plan_data(batch, expected_feature_id=src.scope.feature, expected_batch_id=bid,
                                            known_task_ids=set(all_ids), defer_to_test_stages=defer_to_test_stages_enabled(root))
        except (TypeError, AttributeError) as exc:
            errs = [str(exc)]
        if ([t.get("id") for t in rows(batch.get("tasks"))] != entry["taskIds"]
                or any(entry.get(k) != batch.get(k) for k in ("executionLane", "deliveryKind", "status"))
                or any(k in entry and entry[k] != batch.get(k) for k in ("mergeCommitSha", "deliveryRunId"))):
            errs.append("Batch 明细与索引不匹配")
        if errs:
            src.gap(name, ", ".join(errs) if batch else "缺少可核验的 Batch 计划")
        else:
            candidates[bid] = batch
    if len(candidates) < len(entries):
        return root, candidates, (root.get("parallelBatchPipeline") or {}).get("planRevision")
    try:
        bundle = load_plan_bundle(src.root)
    except (ValueError, OSError) as exc:
        src.gap(PLAN, str(exc))
        return root, {}, None
    return root, bundle.batches, build_pipeline_contract(bundle.root, bundle.batches)["planRevision"]


def collect(scope):
    from .quality import collect_quality, delivery_heads
    src = Inputs(scope)
    if scope.historical_workflow_unknown:
        src.gap("workflow", "旧迭代没有独立的流程配置快照，阶段按归档目录中的已有材料核对")
    stage_rows = stages(src)
    active = {node["id"] for node in stage_rows if not node["skipped"]}
    code_active = "dev.code" in active
    evidence = evidence_index(src) if code_active else {}
    root, plans, revision = plan_batches(src) if "dev.plan" in active else ({}, {}, None)
    entries = root.get("batches", [])
    incomplete = len(plans) < len(entries)
    runs, tasks, checks, batches, risks = {}, {}, [], [], []
    for entry in entries:
        bid = entry["id"]
        if bid not in plans:
            batches.append({"id": bid, "runId": None, "status": None, "merged": False,
                            "mergeCommitSha": None, "sourceRefs": [PLAN]})
            for tid in entry["taskIds"]:
                tasks[tid] = {"id": tid, "title": None, "batch": bid, "status": "unverified", "implemented": False,
                              "delivered": False, "sourceRefs": [PLAN]}
            continue
        batch = plans[bid]
        rid = batch.get("deliveryRunId") if code_active else None
        if component(rid) and rid not in runs:
            runs[rid] = read_run(src, rid, revision)
        run = runs.get(rid) or {}
        rb = run.get("manifest", {}).get("batches", {}).get(bid, {})
        rb = rb if isinstance(rb, dict) else {}
        merged = bool(isinstance(rb.get("taskIds"), list) and batch.get("mergeCommitSha") and rb.get("mergeCommitSha") == batch["mergeCommitSha"] and rb.get("status") == "merged"
                      and sorted(str(t) for t in rb["taskIds"]) == sorted(entry["taskIds"]))
        batches.append({"id": bid, "title": batch.get("title"),
                        "implementationPoints": batch.get("implementationPoints", []),
                        "tasks": [{"id": t["id"], "title":t.get("title"), "implementationPoints":t.get("implementationPoints", [])} for t in rows(batch.get("tasks"))],
                        "baseSha": rb.get("startSha") or rb.get("preparationBaseSha"), "commitSha":rb.get("commitSha"),
                        "runId": rid, "status": rb.get("status"), "merged": merged, "mergeCommitSha": rb.get("mergeCommitSha"),
                        "sourceRefs": [f"plans/{bid}/plan.json"] + ([run["ref"]] if run else [])})
        if rb:
            review, findings = read_review(src, bid, rb, run)
            if review:
                checks.append({"type": "Batch Review", **review})
            risks.extend(findings)
        for task in rows(batch.get("tasks")):
            row, quality = task_facts(src, task, bid, rid, merged, evidence)
            tasks[row["id"]] = row
            checks.extend(quality)
    runs = {k: v for k, v in runs.items() if v}
    integration_runs = [read_integration(src, run) for run in runs.values()]
    integration = {'items': [item for result in integration_runs for item in result['items']],
                   'legacyReviewCount': sum(result['legacyReviewCount'] for result in integration_runs)}
    repos = [r for run in runs.values() for r in run["repositories"]]
    sync = [s for run in runs.values() for s in run["sync"]]
    risks.extend(r for run in runs.values() for r in run["risks"])
    # Deferred records carry the final disposition of an advisory projected by Review.
    distinct = {}
    for issue in risks:
        key = (issue['batch'], issue['id']) if issue['id'] else (issue['batch'], issue['title'], issue['file'])
        prior = distinct.get(key)
        if prior is None:
            alias = next((k for k, v in distinct.items() if v['batch'] == issue['batch'] and v['title'] == issue['title']
                          and (not v['id'] or not issue['id']) and (not v['file'] or not issue['file'] or v['file'] == issue['file'])), None)
            if alias is not None:
                prior = distinct.pop(alias)
        if prior:
            issue = {**prior, **{k: v for k, v in issue.items() if v is not None},
                     'sourceRefs': sorted(set(prior['sourceRefs'] + issue['sourceRefs'])),
                     'reviewFinding': prior.get('reviewFinding', prior['origin'] == 'review') or issue.get('reviewFinding', issue['origin'] == 'review'),
                     'sourceStage': 'review' if prior.get('sourceStage') == 'review' or issue.get('sourceStage') == 'review' else issue.get('sourceStage') or prior.get('sourceStage'),
                     'findingType': 'code_defect' if prior.get('reviewFinding') or issue.get('reviewFinding') else issue.get('findingType') or prior.get('findingType'),
                     'category': prior['category'] if prior.get('reviewFinding') and issue.get('findingType') == 'advisory' and issue['category'] == 'review_advisory' else issue['category'],
                     'reviewHistoryRefs': sorted(set(prior.get('reviewHistoryRefs', []) + issue.get('reviewHistoryRefs', []))),
                     'resolutionSourceRefs': sorted(set(prior.get('resolutionSourceRefs', []) + issue.get('resolutionSourceRefs', [])))}
        distinct[key] = issue
    risks = list(distinct.values())
    attach_plan_scope(risks, [t for t in tasks.values() if t['batch'] in plans])
    ms, changes = summarize(repos, src)
    plan_refs = [name for name in src.sources if name == PLAN or name.startswith("plans/")]
    proof_refs = sorted({ref for t in tasks.values() for ref in t["sourceRefs"]} | set(plan_refs))
    partial_reason = "部分 Batch 计划不可用，仅统计已核验任务" if incomplete else None
    for name, value in {"tasks": len(tasks), "delivered": sum(t["delivered"] for t in tasks.values()),
                        "implemented": sum(t["implemented"] for t in tasks.values()),
                        "undone": sum(t["status"] in {"todo", "failed", "in_progress"} and not t["implemented"] for t in tasks.values())}.items():
        ms[name] = metric(value if root else None, proof_refs,
                          (partial_reason if name != "tasks" else None) if root else "计划不可用")
    ms["completion"] = metric(round(100 * ms["delivered"]["value"] / len(tasks), 1)
                              if tasks else None, proof_refs, partial_reason if tasks else "无有效分母")
    quality, qm, gates = collect_quality(src, delivery_heads(runs, changes) if not incomplete and all(b["runId"] in runs for b in batches) else {}, checks)
    if qm['aiLines']['value'] is None:
        from .attribution import code_lines
        qm['aiLines'] = code_lines(list(tasks.values()), changes, repos, batches)
    ms.update(qm)
    reviews = [c for c in checks if c["type"] == "Batch Review"]
    risk_scope_complete = bool(batches) and len(reviews) == len(batches) and all(c.get("verified", True) and c.get("historyComplete", True) for c in reviews) and all(
        run["audit"]["passed"] is not None for run in runs.values())
    risk_refs = sorted({ref for r in risks + reviews for ref in r["sourceRefs"]}
                       | {ref for run in runs.values() for ref in run["audit"]["sourceRefs"]})
    open_risks = [r for r in risks if r["status"] not in {
        "fixed", "resolved", "accepted", "closed", "已修复", "已接受"}]
    for key, value in {"riskCount": len(risks), "open": len(open_risks), "p0": sum(r["level"] == "P0" for r in open_risks),
                       "p1": sum(r["level"] == "P1" for r in open_risks), "confirm": sum(r["needsConfirm"] for r in open_risks),
                       "reviewFindings": sum(r.get('reviewFinding', r['origin'] == 'review') for r in risks),
                       "reviewRecords": sum(r.get('sourceStage') == 'review' for r in risks),
                       "reviewAdvisories": sum(r.get('findingType') == 'advisory' for r in risks),
                       "reviewFixed": sum(r.get('reviewFinding', r['origin'] == 'review') and r['status'] in {"fixed", "resolved", "已修复"} for r in risks),
                       "nonBlocking": sum(r['origin'] == 'deferred' and r['blocksWorkflow'] is False for r in open_risks)}.items():
        ms[key] = metric(value if value or risk_scope_complete else None, risk_refs,
                         None if risk_scope_complete else "评审或审计材料不完整，仅列已记录的风险")
    for t in tasks.values():
        t["risks"] = [r["title"] or r["id"] for r in risks if r["task"]
                      == t["id"] or not r["task"] and r["batch"] == t["batch"]]
    for name, m in ms.items():
        ids = []
        if m['reason']:
            group = 'testing' if name in {'unit','e2e','untested','caseCoverage','testPassRate','lineCoverage','branchCoverage'} else name
            expected = m['status'] in {'not_collected','not_applicable'}
            existing = [issue['id'] for issue in src.gaps + src.diagnostics
                        if name in issue['affectedMetrics'] or issue['field'] in m['sourceRefs']]
            if existing:
                ids = existing
            elif not name.startswith(('api','page')):
                reason = m['reason']
                if expected and group=='testing':
                    reason = '本阶段未采集测试与覆盖率' if m['status']=='not_collected' else '无有效分母，对应比例不适用'
                ids = [src.gap(group, reason, category='info' if expected else 'coverage',
                               reason_code=m['status'].upper() if expected else reason,
                               scope={'subject':group} if expected else {'sourceRefs':m['sourceRefs']},
                               metrics=[name], source_refs=m['sourceRefs'],
                               action=None if expected else '查看指标详情并核对对应产物')]
        m['diagnosticIds'] = ids
    status = "待核验" if not tasks else "已交付" if all(t["delivered"] for t in tasks.values()) else "部分交付"
    if any(run["audit"]["passed"] is False for run in runs.values()):
        status += " · 证据审计未通过"
    data = {"schemaVersion": SCHEMA, "featureId": scope.feature, "project": scope.workspace.name,
            "scope": {"throughStage": "code", "iteration": scope.iteration, "cicdStatus": "统计截止时尚未进入" if "ops.cicd" in {n["id"] for n in scope.nodes if not n.get("skipped")} else None}, "runIds": sorted(runs),
            "snapshot": {"planRevision": revision, "repositories": repos}, "stageArtifacts": stage_rows,
            "deliveryBrief": delivery_brief(stage_rows),
            "tasks": list(tasks.values()), "batches": batches, "changes": changes, "quality": quality, "metrics": ms, "gates": gates,
            "risks": risks, "integration": integration, "workspaceSync": sync, "audits": [r["audit"] for r in runs.values()], "deliveryStatus": status,
            "gaps": src.gaps, "diagnostics":src.diagnostics, "sources": list(src.sources.values()), "generationStatus": "generated_with_gaps" if src.gaps or any(d["category"]=="coverage" for d in src.diagnostics) else "generated"}
    data["snapshot"]["inputDigest"] = digest(canonical(data))
    return data
