"""Read retained Batch Review attempts and explicit issue resolution evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .sources import canonical, digest, rows


def risk(item, source, batch=None, fallback=None, *, origin='delivery', status=None, source_stage=None, finding_type=None):
    item = item if isinstance(item, dict) else {"message": str(item)}
    blocking = item.get('blocksWorkflow')
    advisory = finding_type == 'advisory' or item.get('kind') == 'review_advisory' or item.get('category') == 'review_advisory'
    category = item.get("category") or item.get("kind") or ('review_advisory' if advisory else 'Review 发现' if origin == 'review' else '交付')
    stage = source_stage or ('review' if origin == 'review' or advisory else item.get('stage'))
    kind = (finding_type or ('advisory' if advisory else 'code_defect')) if stage == 'review' else None
    return {"id": item.get("issueId") or item.get("id"), "title": item.get("message") or item.get("title") or fallback,
            "category": category, "module": item.get("module"), "level": item.get("severity") or item.get("level"),
            "status": status or item.get("status", "open"), "origin": origin,
            "sourceStage": stage, "findingType": kind, "reviewFinding": kind == 'code_defect',
            "blocksWorkflow": blocking if isinstance(blocking, bool) else None, "disposition": item.get('disposition'),
            "owner": item.get("owner"), "batch": batch or item.get("batchId"), "task": item.get("taskId"),
            "needsConfirm": item.get("needsConfirm") is True, "action": item.get("recommendation") or item.get("nextAction"),
            "file": item.get("file") or item.get("path"), "sourceRefs": [source]}


def same_finding(left, right):
    """Repair acceptance applies to its recorded failure, never the whole Batch."""
    if left.get("id") and right.get("id"):
        return left["id"] == right["id"]
    return bool(left.get("title") and left["title"] == right.get("title")
                and all(not left.get(k) or not right.get(k) or left[k] == right[k]
                        for k in ("file", "task")))


def attach_plan_scope(issues, tasks):
    """Add Plan context without changing the original finding's task identity."""
    def names(value):
        if isinstance(value, str):
            return [value] if value else []
        return [v for v in value if isinstance(v, str) and v] if isinstance(value, list) else []

    for issue in issues:
        explicit = issue.get('task')
        if explicit:
            related = [t for t in tasks if t['id'] == explicit and (not issue.get('batch') or t['batch'] == issue['batch'])]
        else:
            related = [t for t in tasks if issue.get('batch') and t['batch'] == issue['batch']]
        issue['planScope'] = {
            'taskIds': [explicit] if explicit else list(dict.fromkeys(t['id'] for t in related)),
            'modules': sorted({m for t in related for m in names(t.get('modules'))}),
            'basis': ('task_plan' if explicit else 'batch_plan') if related else 'record_only' if explicit else 'missing',
            'sourceRefs': sorted({ref for t in related for ref in t.get('sourceRefs', [])
                                  if ref == 'plan.json' or ref.startswith('plans/')}),
        }


def read_review(src, batch_id, run_batch, run):
    states = run_batch.get("stageStates") or {}
    state = states.get("review") if isinstance(states, dict) else {}
    state = state if isinstance(state, dict) else {}
    resolution = state.get("repairResolution") or {}
    resolution = resolution if isinstance(resolution, dict) else {}
    prefix = Path(run["ref"]).parent.as_posix() + "/"
    revision = run["manifest"].get("pipeline", {}).get("planRevision")
    current = src.referenced(state.get("evidencePath"))
    issues, history, proofs = [], [], []
    current_rec = None
    current_error = False
    history_complete = True
    seen_ids = set()
    ids = state.get("evidenceIds") or []
    ids = {i for i in ids if isinstance(i, str)} if isinstance(ids, list) else set()
    if state.get("latestEvidenceId"):
        ids.add(state["latestEvidenceId"])
    paths = {p.relative_to(src.root).as_posix() for p in
             src.root.glob(prefix + "stages/" + batch_id + "/review/*.json")}
    if current and current.startswith(prefix):
        paths.add(current)
    for name in sorted(paths):
        rec = src.obj(name)
        if rec.get("evidenceId") not in ids:
            continue
        seen_ids.add(rec["evidenceId"])
        valid = (isinstance(rec.get("inputs"), dict) and isinstance(rec.get("metadata", {}), dict)
                 and rec.get("batchId") == batch_id and rec.get("stage") == "review"
                 and rec.get("digest") == "sha256:" + digest(canonical({k: v for k, v in rec.items() if k != "digest"}))
                 and rec["inputs"].get("planRevision") == revision)
        latest = name == current
        if latest:
            valid = valid and rec.get("evidenceId") == state.get("latestEvidenceId") and rec.get("inputs", {}).get("batchCommit") == run_batch.get("commitSha")
        if not valid:
            current_error = current_error or latest
            history_complete = False
            src.gap(name, "Review 身份或内容摘要不匹配", metrics=["reviewRecords", "reviewAdvisories", "reviewFindings", "reviewFixed", "riskCount", "open"])
            continue
        if latest:
            current_rec = rec
        meta = rec.get("metadata", {})
        findings = [risk(item, name, batch_id, origin="review") for item in rows(meta.get("findings"))]
        disposition = meta.get("repairDisposition")
        if isinstance(meta.get("repairedFailure"), dict):
            failed = risk(meta["repairedFailure"], name, batch_id, origin="review")
            issues.append(failed)
            if latest and state.get("status") == "passed" and disposition == "single_repair_accepted":
                proofs.append({**failed, "disposition": disposition, "resolvedAt": rec.get("createdAt")})
        issues.extend(findings)
        advisories = rows(meta.get("reviewAdvisories"))
        issues.extend(risk(dict(item, blocksWorkflow=False), name, batch_id, origin="deferred",
                           source_stage='review', finding_type='advisory') for item in advisories)
        history.append({"batch": batch_id, "attempt": rec.get("attempt"), "recordType": "stage_evidence",
                        "recordedAt": rec.get("createdAt"), "commitSha": rec["inputs"].get("batchCommit"),
                        "status": (disposition or state.get("status")) if latest else "recorded", "disposition": disposition,
                        "findings": [dict(f) for f in findings], "advisories": advisories, "evidenceId": rec.get("evidenceId"),
                        "sourceRefs": [name]})
    for missing in sorted(ids - seen_ids - {state.get("latestEvidenceId")}):
        history_complete = False
        src.gap(batch_id + ".review.history." + missing, "缺少 manifest 引用的历史 Review 证据",
                metrics=["reviewRecords", "reviewAdvisories", "reviewFindings", "reviewFixed", "riskCount", "open"])
    if current_rec is None and not current_error:
        src.gap(batch_id + ".review", "缺少有效的当前 Review 证据")
    if current_rec is not None and state.get("status") == "passed" and resolution.get("disposition") == "single_repair_accepted" and isinstance(resolution.get("failure"), dict):
        proofs.append({**risk(resolution["failure"], run["ref"], batch_id, origin="review"),
                       "disposition": resolution["disposition"], "resolvedAt": resolution.get("resolvedAt")})
    if proofs and any(not same_finding(proofs[0], p) for p in proofs[1:]):
        history_complete = False
        src.gap(run["ref"], "Review 修复处置与当前证据不一致", metrics=["reviewRecords", "reviewAdvisories", "reviewFindings", "reviewFixed", "riskCount", "open"])
        proofs = []
    if isinstance(state.get("failure"), dict):
        issues.append(risk(state["failure"], run["ref"], batch_id, origin="review"))
    if isinstance(resolution.get("failure"), dict):
        issues.append(risk(resolution["failure"], run["ref"], batch_id, origin="review"))
    issues.extend(risk(dict(item, blocksWorkflow=False), run["ref"], batch_id, origin="deferred",
                       source_stage='review', finding_type='advisory')
                  for item in rows(state.get("advisories")))
    # Failed Review does not produce stage evidence. Runtime seals it in repairs/.
    for path in sorted(src.root.glob(prefix + "engineering/" + batch_id + "/repairs/*.json")):
        name = path.relative_to(src.root).as_posix()
        rec = src.obj(name)
        if rec.get("failedStage") != "review":
            continue
        if (rec.get("schemaVersion") != "autodev.engineering.repair.v1"
                or rec.get("runId") != run["manifest"]["runId"] or rec.get("batchId") != batch_id
                or path.stem != digest(canonical(rec)) or not isinstance(rec.get("failure"), dict)):
            history_complete = False
            src.gap(name, "Review 修复快照身份或内容摘要不匹配", metrics=["reviewRecords", "reviewAdvisories", "reviewFindings", "reviewFixed", "riskCount", "open"])
            continue
        finding = risk(rec["failure"], name, batch_id, origin="review", status="unverified")
        issues.append(finding)
        history.append({"batch": batch_id, "attempt": None, "recordType": "repair_snapshot",
                        "recordedAt": None, "createdEpoch": rec.get("createdEpoch"), "revision": rec.get("revision"),
                        "commitSha": rec.get("commitSha"), "status": "failed", "findings": [dict(finding)],
                        "advisories": [], "sourceRefs": [name]})
    # Older runs may retain only the repair reference in the final evidence.
    for proof in proofs:
        if not any(same_finding(proof, f) for h in history for f in h["findings"]):
            history.append({"batch": batch_id, "attempt": None, "recordType": "repair_reference",
                            "recordedAt": None, "commitSha": None, "status": "failed", "findings": [proof],
                            "advisories": [], "sourceRefs": proof["sourceRefs"]})
    for issue in issues:
        proof = next((p for p in proofs if same_finding(issue, p)), None) if issue["origin"] == "review" else None
        if proof:
            issue.update(status="resolved", disposition=proof["disposition"], resolvedAt=proof.get("resolvedAt"),
                         resolutionSourceRefs=sorted(set(ref for p in proofs if same_finding(issue, p) for ref in p["sourceRefs"])))
        matches = [h for h in history if any(same_finding(issue, f) for f in h["findings"])]
        issue["reviewHistoryRefs"] = sorted({ref for h in matches for ref in h["sourceRefs"]})

    def order(item):
        epoch = item.get("createdEpoch")
        if not isinstance(epoch, (int, float)):
            try:
                parsed = datetime.fromisoformat((item.get("recordedAt") or "").replace("Z", "+00:00"))
                epoch = (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
            except (ValueError, TypeError):
                epoch = None
        return (epoch if epoch is not None else float("inf") if item["recordType"] == "stage_evidence" else float("-inf"),
                str(item.get("attempt") or item.get("revision") or 0).zfill(8), item["sourceRefs"][0])
    history.sort(key=order)
    refs = sorted({ref for h in history for ref in h["sourceRefs"]})
    return ({"batch": batch_id, "status": (current_rec or {}).get("metadata", {}).get("repairDisposition") or (state.get("status") if current_rec else "unverified"),
             "verified": current_rec is not None, "historyComplete": history_complete, "evidenceId": (current_rec or {}).get("evidenceId"), "findings": issues, "history": history,
             "sourceRefs": refs or [run["ref"]]}, issues)
