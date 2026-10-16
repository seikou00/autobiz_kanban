#!/usr/bin/env python3
"""Bound, demand-driven context for Code, Review, UTest and repairs.

This does not explore before Workflow, alter Plan, or grant write authority.
Only the current Batch is inspected. Shared facts are validated in its actual
worktree before being attached. Runtime owns every generated artifact.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.engineering_facts import FactStore, digest, environment_scope, file_digest, git, locate, signature_excerpt, source_path
from hooks.evidence_kernel import FileLock
from hooks.engineering_paths import batch_paths, same_path
from hooks.runtime_guidance import RuntimeArgumentParser
from hooks.json_writer_common import WriterError, atomic_write_json, feature_dir, resolve_feature, resolve_workspace
from hooks.parallel_runtime import check_lease, load_manifest, run_dir
from hooks.plan_json import find_task, load_plan_bundle, task_contract_sha256

CONTEXT_SCHEMA = "autodev.engineering.context.v1"
HANDOFF_SCHEMA = "autodev.engineering.handoff.v2"
REPAIR_SCHEMA = "autodev.engineering.repair.v1"
STAGES = ("code", "review", "utest", "fix", "rework")


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def context_base(workspace: Path, feature: str, run_id: str, batch_id: str) -> Path:
    if not re.fullmatch(r"B\d{3,}", batch_id):
        raise ValueError("engineering_invalid_batch_id")
    return run_dir(workspace, feature, run_id) / "engineering" / batch_id


def bound_context(args, *, mutate: bool = False) -> dict:
    workspace, feature = resolve_workspace(args.workspace), resolve_feature(args.feature)
    manifest = load_manifest(workspace, feature, args.run_id)
    batch = manifest.get("batches", {}).get(args.batch_id)
    if not isinstance(batch, dict) or not batch.get("worktreePath"):
        raise ValueError("engineering_batch_worktree_missing")
    root, _ = batch_paths(batch, Path(args.batch_worktree))
    if not same_path(root, args.batch_worktree):
        raise ValueError("engineering_worktree_mismatch")
    bundle = load_plan_bundle(feature_dir(workspace, feature))
    tasks = []
    for task_id in (getattr(args, "task_id", None) or batch.get("taskIds", [])):
        owner_batch, task = find_task(bundle, task_id)
        if owner_batch != args.batch_id:
            raise ValueError("engineering_task_batch_mismatch")
        expected = batch.get("taskContractDigests", {}).get(task_id)
        if expected and expected != task_contract_sha256(task):
            raise ValueError("engineering_task_contract_changed")
        tasks.append(task)
    if not tasks:
        raise ValueError("engineering_tasks_missing")
    stage = args.stage
    runtime_stage = {"code": "implement", "rework": "implement", "review": "review",
                     "utest": "test", "fix": "test"}[stage]
    states = batch.get("stageStates", {})
    status = states.get(runtime_stage, {}).get("status")
    if mutate:
        metadata_repair = args.action in {"repair-scope", "record-attempt"} and stage == "rework" and status == "pending"
        metadata_discovery = (stage in {"code", "rework"} and status == "pending"
                              and args.action in {"query", "resolve", "publish", "fail", "lookup-class", "inspect-class"}
                              and batch.get("activeStage") in {None, "", "prepare", "implement"}
                              and not any(states.get(s, {}).get("status") == "running" for s in ("review", "test")))
        if stage == "review" or (status != "running" and not metadata_repair and not metadata_discovery):
            raise ValueError("engineering_fact_write_requires_running_code_or_test")
        if not check_lease(workspace, feature, args.run_id, args.batch_id, args.owner_token or ""):
            raise ValueError("engineering_lease_invalid")
        if stage in {"utest", "fix"}:
            execution = read_json(run_dir(workspace, feature, args.run_id) / "utest" / args.batch_id / "UTEST_EXECUTION.json")
            from hooks.utest_policy import stage_budget, deadline_reason
            if states.get("test", {}).get("stopRequested") or deadline_reason(stage_budget(manifest, batch, execution), execution, time.time()):
                raise ValueError("engineering_utest_budget_exhausted")
    # Reading a retained context is allowed before start/recovery, never as
    # permission to edit business files or skip stage validation.
    base = context_base(workspace, feature, args.run_id, args.batch_id)
    return {"workspace": workspace, "feature": feature, "runId": args.run_id,
            "batchId": args.batch_id, "root": root, "batch": batch, "tasks": tasks,
            "base": base, "stage": stage, "store": FactStore(workspace, feature, root)}


def record_implementation(workspace: Path, feature: str, parallel_id: str, batch_id: str,
                          root: Path, task: dict, state: dict) -> dict:
    """Called after successful finish, including idempotent finish recovery."""
    base = context_base(workspace, feature, parallel_id, batch_id)
    batch = load_manifest(workspace, feature, parallel_id)["batches"][batch_id]
    root, task_workspace = batch_paths(batch, root)
    paths = []
    for raw in [*state.get("changedFiles", []), *state.get("supportingFiles", [])]:
        relative = raw.split(":", 1)[-1]
        source_path(root, relative)
        paths.append(relative)
    final = next((repo.get("snapshot") for repo in state.get("finalRepositories", [])
                  if same_path(repo.get("path", ""), root)), state.get("finalSnapshot"))
    if not isinstance(final, dict):
        raise ValueError("engineering_implementation_snapshot_missing")
    # Completion snapshots are authoritative. Never relabel current dirty
    # content as the original implementation during an idempotent finish.
    snapshots = {p: final.get(p) for p in sorted(set(paths))}
    store = FactStore(workspace, feature, root)
    automatic = store.record_declarations(snapshots, ":".join([parallel_id, batch_id, task["id"]]))
    usage = read_json(base / "usage" / (task["id"] + ".json"))
    facts = store.related(list(snapshots), usage.get("symbols", []))
    payload = {"schemaVersion": HANDOFF_SCHEMA, "runId": parallel_id, "batchId": batch_id,
               "taskId": task["id"], "taskRunId": state["runId"], "worktreePath": str(root),
               "taskWorkspace": str(task_workspace), "pathBase": "batch_git_root",
               "executionCwds": sorted({a["executionCwd"] for a in state.get("compileAttempts", []) if a.get("executionCwd")}),
               "taskDigest": task_contract_sha256(task), "files": snapshots,
               "implementationEvidenceId": state.get("implementationEvidenceId"),
               "compileEvidenceIds": [a["evidenceId"] for a in state.get("compileAttempts", []) if a.get("passed") and a.get("evidenceId")],
               "factIds": [f["factId"] for f in facts],
               "cacheWriteSummary": automatic,
               "testPoints": task.get("testPoints", []),
               "verificationIntent": task.get("verificationIntent"),
               "limitations": ["Entry points are changed-file references; inspect relevant behavior on demand."]}
    path = base / "handoffs" / (task["id"] + ".json")
    with FileLock(base / "handoffs" / (task["id"] + ".lock")):
        _, current = find_task(load_plan_bundle(feature_dir(workspace, feature)), task["id"])
        if current.get("latestImplementationEvidenceId") != state.get("implementationEvidenceId"):
            raise ValueError("engineering_stale_implementation_publisher")
        payload["implementationRevision"] = current.get("implementationRevision", 0)
        old = read_json(path)
        if old.get("implementationRevision", -1) > payload["implementationRevision"]:
            raise ValueError("engineering_stale_handoff_revision")
        snapshot_path = base / "handoffs" / "snapshots" / (task["id"] + "-" + digest(payload) + ".json")
        atomic_write_json(snapshot_path, payload)
        atomic_write_json(path, payload)
    return {"path": str(path), "digest": digest(payload)}


def _current_handoffs(ctx: dict) -> list[dict]:
    result = []
    for task in ctx["tasks"]:
        path = ctx["base"] / "handoffs" / (task["id"] + ".json")
        value = read_json(path)
        if value.get("schemaVersion") not in {HANDOFF_SCHEMA, "autodev.engineering.handoff.v1"} or value.get("taskDigest") != task_contract_sha256(task):
            continue
        if (value.get("runId") != ctx["runId"] or value.get("batchId") != ctx["batchId"]
                or value.get("taskId") != task["id"] or not same_path(value.get("worktreePath", ""), ctx["root"])):
            raise ValueError("engineering_handoff_identity_mismatch")
        stale = [p for p, sha in value.get("files", {}).items() if file_digest(source_path(ctx["root"], p)) != sha]
        result.append({**value, "handoffPath": str(path), "staleFiles": stale,
                       "freshness": "stale" if stale else "current"})
    return result


def build_stage_context(ctx: dict) -> dict:
    handoffs = _current_handoffs(ctx)
    paths = [p for h in handoffs for p in h["files"]]
    # Code before start has no handoff yet. Only use explicitly declared files,
    # never scan directories to guess a new task's entry points.
    for task in ctx["tasks"]:
        paths.extend(p for p in task.get("scope", {}).get("paths", []) if isinstance(p, str)
                     and source_path(ctx["root"], p).is_file() and p not in paths)
    symbols = []
    for task in ctx["tasks"]:
        symbols.extend(read_json(ctx["base"] / "usage" / (task["id"] + ".json")).get("symbols", []))
    # Read imports only in actual changed files, not a repository-wide index.
    for relative in paths[:64]:
        path = source_path(ctx["root"], relative)
        if path.is_file() and path.stat().st_size <= 1024 * 1024:
            text = path.read_text(encoding="utf-8", errors="replace")
            symbols.extend(re.findall(r"(?m)^\s*import\s+(?:static\s+)?([\w.$]+)", text))
    facts = ctx["store"].related(paths, symbols)
    # Keep the prompt bounded; full provenance/content remains at factPath.
    facts = [{"factId": f["factId"], "symbol": f["symbol"], "kind": f["kind"],
              "status": f["status"], "verification": f.get("verification"),
              "querySources": f.get("querySources", []), "factPath": f["factPath"],
              "contentPreview": json.dumps(f.get("content", {}), ensure_ascii=False)[:2000],
              "contentTruncated": len(json.dumps(f.get("content", {}), ensure_ascii=False)) > 2000,
              "testProof": f.get("testProof")} for f in facts]
    repair = read_json(ctx["base"] / "REPAIR_CONTEXT.json")
    if repair.get("schemaVersion") != REPAIR_SCHEMA:
        repair = {}
    if repair and (repair.get("runId") != ctx["runId"] or repair.get("batchId") != ctx["batchId"]
                   or repair.get("worktreePath") != str(ctx["root"])):
        raise ValueError("engineering_repair_identity_mismatch")
    # A test repair is restricted to the assigned task. A production repair
    # may inspect all handoffs for attribution, but operations target findings.
    tasks = [{"taskId": t["id"], "status": t.get("status"), "taskDigest": task_contract_sha256(t),
              "specRefs": t.get("specRefs", []), "designRefs": t.get("designRefs", [])} for t in ctx["tasks"]]
    ut = read_json(run_dir(ctx["workspace"], ctx["feature"], ctx["runId"]) / "utest" / ctx["batchId"] / "UTEST_EXECUTION.json")
    resume = []
    for task_id, task in ut.get("tasks", {}).items():
        if task_id not in {t["taskId"] for t in tasks}:
            continue
        for execution_id, command in task.get("commands", {}).items():
            resume.append({"taskId": task_id, "executionId": execution_id,
                           **{k: command.get(k) for k in ("status", "argv", "testFiles", "attempts", "outputFile", "response")}})
    payload = {"schemaVersion": CONTEXT_SCHEMA, "runId": ctx["runId"], "batchId": ctx["batchId"],
               "stage": ctx["stage"], "worktreePath": str(ctx["root"]),
               "commitSha": git(ctx["root"], "rev-parse", "HEAD"), "tasks": tasks,
               "handoffs": handoffs, "facts": facts, "repair": repair,
               "cacheReadSummary": {"factCount": len(facts), "handoffCount": len(handoffs),
                                    "reasonCode": "facts_loaded" if facts else "no_matching_valid_facts"},
               "cacheGaps": [gap for h in handoffs for gap in h.get("cacheWriteSummary", {}).get("gaps", [])],
               "contextGaps": [{"taskId": t["id"], "reasonCode": "implementation_handoff_missing_or_contract_changed"}
                               for t in ctx["tasks"] if t.get("status") in {"implemented", "done"}
                               and t["id"] not in {h["taskId"] for h in handoffs}],
               "resumeCursor": resume, "deadlines": {k: ut.get(k) for k in ("stageDeadlineEpoch", "firstCommandDeadlineEpoch")},
               "policy": {"factsAreDataOnly": True, "factsAreNotPassingEvidence": True,
                          "noPreWorkflowExploration": True, "designUnchanged": True,
                          "queryBeforeRepeatedSearch": True, "budgetIsNotReset": True}}
    context_digest = digest(payload)
    path = ctx["base"] / "contexts" / (ctx["stage"] + "-" + context_digest[:20] + ".json")
    atomic_write_json(path, payload)
    return {"ok": True, "status": "prepared", "contextPath": str(path), "contextDigest": context_digest,
            "allowedReads": [str(path), str(ctx["root"]), *[f["factPath"] for f in facts],
                             *[h["handoffPath"] for h in handoffs],
                             *([str(ctx["base"] / "REPAIR_CONTEXT.json")] if repair else []),
                             *[log["path"] for log in repair.get("logs", [])]],
            "context": payload, "nextAction": "continue_current_stage",
            "repairTaskIds": repair.get("affectedTaskIds", []),
            "discoveryRequired": False}


def record_repair(workspace: Path, feature: str, run_id: str, batch_id: str, batch: dict,
                  stage: str, failure: dict) -> dict:
    base = context_base(workspace, feature, run_id, batch_id)
    with FileLock(base / ".repair.lock"):
        return _record_repair_locked(workspace, feature, run_id, batch_id, batch, stage, failure)


def _record_repair_locked(workspace: Path, feature: str, run_id: str, batch_id: str, batch: dict,
                          stage: str, failure: dict) -> dict:
    """Capture durable failure BEFORE resetting delivery states.

    Exact test command data comes from Runtime, never from a model-authored
    shell string. Review locations remain findings, not proven root causes.
    """
    root = Path(batch["worktreePath"]).resolve()
    base = context_base(workspace, feature, run_id, batch_id)
    execution = read_json(run_dir(workspace, feature, run_id) / "utest" / batch_id / "UTEST_EXECUTION.json")
    failed = []
    for task_id, task in execution.get("tasks", {}).items():
        for command in task.get("commands", {}).values():
            response = command.get("response") or {}
            if stage == "test" and command.get("status") == "recorded" and response and not response.get("ok"):
                failed.append({"taskId": task_id, "argv": command.get("argv", []),
                               "testFiles": command.get("testFiles", []),
                               "startedEpoch": command.get("startedEpoch", 0),
                               **{k: response.get(k) for k in ("targetId", "commandId", "evidenceId", "cwd", "failureClassification", "evidenceLogPath")}})
    # Runtime command ordering is persisted; use the latest matching failure.
    if failed:
        message = str(failure.get("message", ""))
        anchored = [item for item in failed if any(item.get(k) and str(item[k]) in message for k in ("evidenceId", "commandId", "targetId"))]
        failed = anchored or [max(failed, key=lambda item: item["startedEpoch"])]
    logs = []
    for item in failed:
        evidence_id = item.get("evidenceId")
        if isinstance(evidence_id, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", evidence_id):
            log = feature_dir(workspace, feature) / "evidence" / (evidence_id + ".log")
            if log.is_file():
                with log.open("rb") as handle:
                    body = handle.read(32768)
                logs.append({"path": str(log), "excerpt": body.decode("utf-8", errors="replace"),
                             "truncated": log.stat().st_size > len(body)})
    handoffs = [read_json(p) for p in sorted((base / "handoffs").glob("*.json"))]
    message = str(failure.get("message", ""))
    affected = sorted({item["taskId"] for item in failed})
    if not affected:
        affected = sorted({h["taskId"] for h in handoffs if any(
            re.search(re.escape(p) + r"(?::\d+|\b)", message) for p in h.get("files", {}))})
    task_ids = batch.get("taskIds", [])
    previous = read_json(base / "REPAIR_CONTEXT.json")
    payload = {"schemaVersion": REPAIR_SCHEMA, "runId": run_id, "batchId": batch_id,
               "worktreePath": str(root), "commitSha": batch.get("commitSha"),
               "failedStage": stage, "failure": failure, "failedCommands": failed, "logs": logs,
               "affectedTaskIds": affected, "taskCandidates": task_ids,
               "scopeStatus": "resolved" if affected else "needs_bounded_location",
               "handoffPaths": [str(base / "handoffs" / (h["taskId"] + ".json")) for h in handoffs if h.get("taskId")],
               "deadlines": {k: execution.get(k) for k in ("stageDeadlineEpoch", "firstCommandDeadlineEpoch")},
               "hypothesisOnly": stage == "review", "previousAttempts": [*previous.get("previousAttempts", []), *previous.get("attempts", [])][-20:],
               "attempts": [], "createdEpoch": time.time()}
    payload["revision"] = previous.get("revision", 0) + 1
    if (previous.get("scopeStatus") == "resolved" and not affected
            and previous.get("failure") == failure and previous.get("failedStage") == stage
            and previous.get("commitSha") == batch.get("commitSha")):
        payload.update(affectedTaskIds=previous.get("affectedTaskIds", []), scopeStatus="resolved",
                       scopeReason=previous.get("scopeReason", "retained_attribution"))
    path = base / "repairs" / (digest(payload) + ".json")
    atomic_write_json(path, payload)
    atomic_write_json(base / "REPAIR_CONTEXT.json", payload)
    return {"repairContextPath": str(path), "repairContextDigest": digest(payload),
            "affectedTaskIds": payload["affectedTaskIds"]}


def cli_argv(args, action: str) -> list[str]:
    values = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), action,
              "--workspace", args.workspace, "--feature", args.feature, "--run-id", args.run_id,
              "--batch-id", args.batch_id, "--batch-worktree", args.batch_worktree, "--stage", args.stage]
    for task in args.task_id or []:
        values += ["--task-id", task]
    # Never persist lease-bearing argv. These are returned only to this caller.
    if args.owner_token:
        values += ["--owner-token", args.owner_token]
    return values


def input_file(ctx: dict, raw: str) -> dict:
    path = Path(raw).resolve()
    if not path.is_relative_to(ctx["root"]) and not path.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise ValueError("engineering_input_outside_worktree_or_temp")
    if not path.is_file() or path.stat().st_size > 32768:
        raise ValueError("engineering_invalid_input_file")
    value = read_json(path)
    if not value:
        raise ValueError("engineering_invalid_input_json")
    return value


def context_reference(result: dict) -> dict:
    """CLI callers read the exact pack once instead of echoing it twice."""
    if "context" not in result:
        return result
    payload = result["context"]
    return {**{k: v for k, v in result.items() if k != "context"}, "summary": {
        "taskIds": [t["taskId"] for t in payload["tasks"]], "factCount": len(payload["facts"]),
        "handoffCount": len(payload["handoffs"]), "repairScopeStatus": payload["repair"].get("scopeStatus"),
        "deadlines": payload["deadlines"]},
        "brief": {"handoffs": [{k: h.get(k) for k in ("taskId", "files", "factIds", "handoffPath", "freshness", "testPoints")}
                               for h in payload["handoffs"]],
                  "facts": [{**fact, "contentPreview": fact["contentPreview"][:1000]} for fact in payload["facts"][:8]],
                  "cacheGaps": payload["cacheGaps"][:20], "cacheReadSummary": payload["cacheReadSummary"],
                  "resumeCursor": [{k: item.get(k) for k in ("taskId", "executionId", "status", "argv", "testFiles", "outputFile")}
                                   for item in payload["resumeCursor"][:16]]}}


def parser() -> argparse.ArgumentParser:
    result = RuntimeArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="action", required=True)
    contract = commands.add_parser("contract", help="verify the launcher deployment before any Batch work")
    contract.add_argument("--expected-digest", required=True)
    diagnostic = commands.add_parser("inspect", help="read-only paths, versions, failures and cache counters")
    for name in ("workspace", "feature", "run-id"):
        diagnostic.add_argument("--" + name, required=True)
    diagnostic.add_argument("--batch-id")
    for action in ("prepare", "locate", "lookup", "lookup-class", "inspect-class", "query", "resolve", "publish", "fail", "repair-scope", "record-attempt"):
        cmd = commands.add_parser(action)
        for name in ("workspace", "feature", "run-id", "batch-id", "batch-worktree"):
            cmd.add_argument("--" + name, required=True)
        cmd.add_argument("--stage", choices=STAGES, required=True)
        cmd.add_argument("--task-id", action="append")
        cmd.add_argument("--owner-token")
        if action in {"locate", "lookup", "query", "resolve"}:
            cmd.add_argument("--symbol", required=True)
        if action == "locate":
            cmd.add_argument("--module", default=".")
        if action in {"query", "resolve"}:
            from hooks.engineering_facts import KINDS
            cmd.add_argument("--kind", choices=KINDS, default="api_signature")
            cmd.add_argument("--source", action="append", required=True)
            cmd.add_argument("--classpath-file", help="Optional worktree-relative resolved JAR snapshot; bound into provenance")
        if action == "lookup":
            from hooks.engineering_facts import KINDS
            cmd.add_argument("--kind", choices=KINDS, default="api_signature")
            cmd.add_argument("--source", action="append")
        if action in {"lookup-class", "inspect-class"}:
            cmd.add_argument("--classpath-file", required=True)
            cmd.add_argument("--class-name", required=True)
            cmd.add_argument("--module", default=".")
        if action in {"publish", "fail"}:
            cmd.add_argument("--fact-id", required=True)
            cmd.add_argument("--claim-token", required=True)
            cmd.add_argument("--content-file", required=True)
            cmd.add_argument("--transient", action="store_true")
        if action in {"repair-scope", "record-attempt"}:
            cmd.add_argument("--content-file", required=True)
    return result


def execute(args) -> dict:
    if args.action == "contract":
        from hooks.engineering_contract import runtime_contract
        actual = runtime_contract()
        valid = actual["complete"] and actual["digest"] == args.expected_digest
        return {"ok": valid, "success": valid, "status": "verified" if valid else "blocked",
                "runtimeContractDigest": actual["digest"],
                "missingFiles": [name for name, sha in actual["files"].items() if sha is None],
                "reasonCode": None if valid else "utest_runtime_deployment_changed:rerun_launcher" if actual["complete"] else "runtime_contract_incomplete:restore_plugin_files"}
    if args.action == "inspect":
        from hooks.engineering_diagnostics import inspect
        return inspect(resolve_workspace(args.workspace), resolve_feature(args.feature), args.run_id, args.batch_id)
    read_actions = {"prepare", "locate", "lookup"}
    ctx = bound_context(args, mutate=args.action not in read_actions)
    owner = ":".join([args.run_id, args.batch_id, ",".join(sorted(t["id"] for t in ctx["tasks"]))])
    if args.action == "prepare":
        result = build_stage_context(ctx)
        from hooks.runtime_guidance import bound_operations, command_catalog
        catalog = command_catalog()
        catalog_path = feature_dir(ctx["workspace"], ctx["feature"]) / ".engineering" / ("commands-" + digest(catalog)[:20] + ".json")
        atomic_write_json(catalog_path, catalog)
        result["commandCatalogPath"] = str(catalog_path)
        result["allowedReads"].append(str(catalog_path))
        token = args.owner_token if check_lease(ctx["workspace"], ctx["feature"], args.run_id, args.batch_id, args.owner_token or "") else None
        operation_ctx = ctx
        if ctx["stage"] == "rework":
            operation_ctx = {**ctx, "tasks": [t for t in ctx["tasks"] if t["id"] in result["repairTaskIds"]]}
        result["operations"] = bound_operations(operation_ctx, token)
        result["nextActionArgv"] = next((op["argv"] for op in result["operations"] if not op.get("missingArguments")), None)
        from hooks.engineering_observability import observe
        observe(ctx["workspace"], ctx["feature"], ctx["store"].repository, "context_prepared",
                runId=ctx["runId"], batchId=ctx["batchId"], stage=ctx["stage"],
                contextDigest=result["contextDigest"], count=len(result["context"]["facts"]))
        return result
    if args.action == "locate":
        return {"ok": True, **locate(ctx["root"], args.symbol, args.module), "nextAction": "query_exact_candidate"}
    if args.action == "lookup":
        return {"ok": True, **ctx["store"].lookup(args.symbol, args.kind, args.source)}
    if args.action in {"lookup-class", "inspect-class"}:
        from hooks.engineering_dependencies import inspect_class, lookup
        module = source_path(ctx["root"], args.module)
        runtime_ctx = (ctx["workspace"], ctx["feature"], ctx["root"], module)
        result = (lookup if args.action == "lookup-class" else inspect_class)(args, runtime_ctx)
        bound_context(args, mutate=True)
        return {"ok": True, **result}
    if args.action in {"query", "resolve"}:
        if args.action == "resolve" and args.kind != "api_signature":
            raise ValueError("engineering_resolve_only_source_signatures")
        scope = ""
        if args.kind in {"test_recipe", "capability_result"}:
            scope = environment_scope()
        result = ctx["store"].request(args.symbol, args.kind, args.source, owner=owner, scope=scope,
                                      classpath_file=getattr(args, "classpath_file", None))
        if result["status"] == "claimed" and args.action == "resolve":
            result = ctx["store"].publish(result["factId"], result["claimToken"], owner=owner,
                                          content=signature_excerpt(ctx["root"], args.source, args.symbol))
        for task in ctx["tasks"]:
            usage_path = ctx["base"] / "usage" / (task["id"] + ".json")
            with FileLock(ctx["base"] / "usage" / (task["id"] + ".lock")):
                used = read_json(usage_path)
                atomic_write_json(usage_path, {"symbols": sorted(set(used.get("symbols", []) + [args.symbol.split("#", 1)[0]])),
                                               "factIds": sorted(set(used.get("factIds", []) + [result["factId"]]))})
        result["nextAction"] = {"in_progress": "continue_independent_work_then_query", "claimed": "inspect_listed_sources_then_publish"}.get(result["status"], "reuse_fact_with_provenance")
        if result["status"] == "claimed":
            result["publishArgv"] = cli_argv(args, "publish") + ["--fact-id", result["factId"], "--claim-token", result["claimToken"]]
            result["failArgv"] = cli_argv(args, "fail") + ["--fact-id", result["factId"], "--claim-token", result["claimToken"]]
            result["missingArguments"] = ["--content-file"]
        result["nextActionArgv"] = cli_argv(args, args.action) + ["--symbol", args.symbol, "--kind", args.kind] + [v for p in args.source for v in ("--source", p)]
        if getattr(args, "classpath_file", None):
            result["nextActionArgv"] += ["--classpath-file", args.classpath_file]
        return {"ok": True, **result}
    if args.action in {"publish", "fail"}:
        content = input_file(ctx, args.content_file)
        # Model claims cannot promote verification level. Test Evidence is
        # attached by the existing runner; consumers can inspect its identity.
        return {"ok": True, **ctx["store"].publish(args.fact_id, args.claim_token, owner=owner,
                content=content, failure=args.action == "fail", transient=getattr(args, "transient", False))}
    path = ctx["base"] / "REPAIR_CONTEXT.json"
    with FileLock(ctx["base"] / ".repair.lock"):
        repair = read_json(path)
        if repair.get("schemaVersion") != REPAIR_SCHEMA:
            raise ValueError("engineering_repair_context_missing")
        content = input_file(ctx, args.content_file)
        if args.action == "repair-scope":
            selected = content.get("affectedTaskIds")
            if (not isinstance(selected, list) or not selected or not set(selected) <= set(repair["taskCandidates"])
                    or not content.get("reason")):
                raise ValueError("engineering_repair_scope_requires_batch_tasks_and_reason")
            repair.update(affectedTaskIds=sorted(set(selected)), scopeStatus="resolved", scopeReason=content["reason"])
        else:
            if not content.get("hypothesis") or not content.get("result"):
                raise ValueError("engineering_attempt_requires_hypothesis_and_result")
            repair["attempts"] = [*repair.get("attempts", []), content][-20:]
        repair["revision"] = repair.get("revision", 0) + 1
        atomic_write_json(path, repair)
    return {"ok": True, "status": "recorded", "repairContextPath": str(path),
            "repairTaskIds": repair["affectedTaskIds"], "nextAction": "prepare_fresh_context"}


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        result = execute(args)
    except (ValueError, OSError, KeyError, WriterError) as exc:
        result = {"ok": False, "status": "blocked", "reasonCode": str(exc),
                  "nextAction": "repair_reported_binding_or_input", "nextActionArgv": None}
    print(json.dumps(context_reference(result), ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
