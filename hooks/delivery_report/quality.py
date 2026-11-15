"""Consume version-bound results. Missing observations have no numeric value."""
from collections import defaultdict

from .sources import metric


def count(value):
    return value if type(value) is int and value >= 0 else None


def delivery_heads(runs, changes):
    by_ref = defaultdict(list)
    for run in runs.values():
        for repo in run["repositories"]:
            by_ref[repo["ref"]].append(repo)
    heads = {}
    for ref, entries in by_ref.items():
        observed = {r.get("headSha") for r in entries}
        if None in observed or len({r.get("root") for r in entries}) != 1:
            return {}
        if len(observed) == 1:
            heads[ref] = next(iter(observed))
            continue
        segments = [row for row in changes if row["repository"] == ref]
        if len(segments) != 1 or segments[0]["headSha"] not in observed:
            return {}
        heads[ref] = segments[0]["headSha"]
    return heads


def binding_matches(data, heads, feature, *, require_feature=False):
    return (bool(heads) and data.get("repositoryHeads") == heads
            and data.get("featureId", None if require_feature else feature) == feature)


def collect_quality(src, heads, checks):
    extra_ref = "DELIVERY_MEASUREMENTS.json"
    extra = src.obj(extra_ref)
    if extra and not binding_matches(extra, heads, src.scope.feature, require_feature=True):
        src.gap(extra_ref, "测量数据未绑定本次交付版本")
        extra = {}
    ms = {}
    cases = []
    summaries = {}
    known = []
    for key, filename, field, identity in [('unit', 'UNIT_TEST_RESULT.json', 'targets', 'targetId'),
                                           ('e2e', 'E2E_RESULT.json', 'cases', 'caseId')]:
        data = src.obj(filename)
        subset = extra.get(key)
        source = extra_ref
        provided = key in extra or bool(data)
        if not isinstance(subset, list) and data:
            if binding_matches(data, heads, src.scope.feature):
                subset = data.get(field)
                source = filename
            else:
                src.gap(filename, "未能核对测试结果的交付版本")
        available = isinstance(subset, list)
        items = {}
        if available:
            for entry in subset:
                if not isinstance(entry, dict):
                    available = False
                    break
                case = entry.get(identity) or entry.get("id")
                if not isinstance(case, str) or not case or case in items:
                    available = False
                    break
                result = str(entry.get("result") or entry.get("verdict") or "NOT_RUN").upper()
                if result not in {"PASS", "PASS_WITH_WARNINGS", "FAIL", "SKIP", "BLOCKED", "NOT_RUN", "PENDING"}:
                    available = False
                    break
                items[case] = {"id": case, "type": key, "task": entry.get("taskId"), "title": entry.get("title") or entry.get("name"),
                               "risk": entry.get("risk"), "action": entry.get("nextAction"),
                               "result": result, "reason": entry.get("reason") or entry.get("reasonCode"), "sourceRefs": [source]}
        if not available:
            items = {}
            if provided:
                src.gap(source, '用例清单或版本绑定无效', reason_code='INVALID_TEST_RESULT',
                        scope={'source': source, 'type': key}, metrics=[key], source_refs=[source],
                        action='提供与本次交付版本匹配的用例清单')
        passed = sum(r["result"] in {"PASS", "PASS_WITH_WARNINGS"} for r in items.values())
        executed = passed + sum(r["result"] == "FAIL" for r in items.values())
        reason = None if available else "当前 Code 流程未执行此项，缺少绑定交付版本的用例清单"
        refs = [source] if available else []
        ms[key] = metric(executed if available else None, refs, reason)
        if not available and not provided:
            ms[key]['status'] = 'not_collected'
        summaries[key] = {"status": "measured" if available else "not_executed_by_workflow", "total": len(items) if available else None,
                          "passed": passed if available else None, "displayTotal": len(items), "displayPassed": passed, "sourceRefs": refs}
        if available:
            known.append((len(items), executed, passed, source))
        cases.extend(items.values())
    total = sum(item[0] for item in known)
    executed = sum(item[1] for item in known)
    passed = sum(item[2] for item in known)
    refs = [item[3] for item in known]
    reason = "缺少用例计划或执行结果" if not known else "仅含已核验用例清单" if len(known) < 2 else None
    ms["untested"] = metric(total - executed if known else None, refs, reason)
    ms["caseCoverage"] = metric(round(100 * executed / total, 1)
                                if total else None, refs, reason if total else "无有效分母")
    ms["testPassRate"] = metric(round(100 * passed / executed, 1)
                                if executed else None, refs, reason if executed else "无已执行用例")
    for key in ('untested', 'caseCoverage', 'testPassRate'):
        if not known:
            ms[key]['status'] = 'not_collected'
        elif ms[key]['value'] is None:
            ms[key]['status'] = 'not_applicable'
    coverage = {}
    for name in ('lineCoverage', 'branchCoverage'):
        item = extra.get(name) or {}
        covered = count(item.get("covered")) if isinstance(item, dict) else None
        size = count(item.get("total")) if isinstance(item, dict) else None
        valid = covered is not None and size is not None and covered <= size
        ms[name] = metric(round(100 * covered / size, 1) if valid and size else None, [extra_ref] if valid else [],
                          None if valid and size else "缺少有效覆盖率分母")
        if name not in extra:
            ms[name]['status'] = 'not_collected'
        elif valid and size == 0:
            ms[name]['status'] = 'not_applicable'
        elif not valid:
            src.gap(extra_ref, '覆盖率数据格式无效', reason_code='INVALID_COVERAGE',
                    scope={'source': extra_ref, 'measurement': name}, metrics=[name], source_refs=[extra_ref])
        coverage[name] = {**ms[name], "covered": covered if valid else None, "total": size if valid else None}
    ai = count(extra.get("aiGeneratedLines"))
    ms["aiLines"] = metric(ai, [extra_ref] if ai is not None else [],
                           None if ai is not None else "缺少 AI 生成行数记录")
    if ai is not None:
        ms['aiLines']['basis'] = 'recorded_cumulative_generation'
        ms['aiLines']['description'] = '版本绑定的外部记录提供的累计 AI 生成行数。'
    # Explicit findings can disprove a check. Absence of recorded issues is not a pass.
    gates = []
    for key, label in [('tests', '自动化测试'), ('security', '高危安全漏洞'), ('architecture', '架构红线'), ('confirmation', '人工确认')]:
        status = (extra.get("gates") or {}).get(key) if isinstance(extra.get("gates"), dict) else None
        status = status if isinstance(status, str) else None
        sources = [extra_ref] if status in {'passed', 'failed'} else []
        if key == 'tests' and (executed > passed or status != "failed" and len(known) == 2 and total > 0 and total == passed):
            status = "failed" if executed > passed else "passed"
            sources = refs
        gates.append({"id": key, "label": label, "status": status if status in {
                     "passed", "failed"} else "pending", "sourceRefs": sources})
    build = {key: extra[key] if isinstance(extra.get(key), (str, int)) and not isinstance(extra[key], bool)
             else None for key in ("version", "buildNumber")}
    assessments = [{"id": key, "label": label, "value": 0, "status": "pending",
                    "reason": "专项评估数据待后续补充，当前为占位值。", "sourceRefs": []}
                   for key, label in [('architectureRedlines', '架构红线'),
                                      ('architectureQuality', '架构质量'), ('securityVulnerabilities', '安全漏洞')]]
    return {"build": build, "checks": checks, "specialAssessments": assessments,
            "unitTests": summaries["unit"], "e2eTests": summaries["e2e"], "cases": cases, **coverage}, ms, gates
