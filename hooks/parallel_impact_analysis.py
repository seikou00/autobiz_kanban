#!/usr/bin/env python3
"""Content-based evidence impact analysis for staged parallel Batches.

The implementation starts with deterministic L1 path overlap plus explicit DAG
dependencies.  The returned shape reserves import/coverage/contract sources so
future analyzers can add precision without changing the invalidation contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import resolve_feature, resolve_workspace
from hooks.parallel_runtime import append_event, load_manifest, run_lock, save_manifest


def _overlap(left: str, right: str) -> bool:
    a, b = left.replace("\\", "/").strip("/"), right.replace("\\", "/").strip("/")
    return not a or not b or a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _depends_on(manifest: dict[str, Any], batch_id: str, target: str) -> bool:
    batches = manifest.get("batches", {})
    seen: set[str] = set()
    todo = list((batches.get(batch_id) or {}).get("dependencies", []))
    while todo:
        current = str(todo.pop())
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        item = batches.get(current)
        if isinstance(item, dict):
            todo.extend(str(dep) for dep in item.get("dependencies", []))
    return False


def compute_impact_set(manifest: dict[str, Any], batch_id: str, changed_files: list[str]) -> dict[str, Any]:
    batches = manifest.get("batches", {})
    source = batches.get(batch_id)
    if not isinstance(source, dict):
        raise ValueError(f"parallel_batch_not_found:{batch_id}")
    changed = sorted({str(path).replace("\\", "/").strip("/") for path in changed_files if str(path).strip()})
    impacted: list[dict[str, Any]] = []
    for other_id, other in sorted(batches.items()):
        if other_id == batch_id or not isinstance(other, dict):
            continue
        paths = [str(path) for path in other.get("writeSet", []) if isinstance(path, str)]
        l1 = any(_overlap(file_name, path) for file_name in changed for path in paths)
        dependency = _depends_on(manifest, other_id, batch_id)
        if l1 or dependency:
            impacted.append({
                "batchId": other_id,
                "reasons": (["l1_path_overlap"] if l1 else []) + (["dag_dependency"] if dependency else []),
                "staleStages": ["test", "quality_gate"],
            })
    return {
        "sourceBatchId": batch_id,
        "changedFiles": changed,
        "impactedBatches": impacted,
        "analysisLevels": {"l1": True, "l2ImportGraph": False, "l3Coverage": False, "l4Contracts": False},
    }


def invalidate_impacted_evidence(workspace: Path, feature: str, run_id: str, batch_id: str, changed_files: list[str]) -> dict[str, Any]:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        result = compute_impact_set(manifest, batch_id, changed_files)
        stale: list[dict[str, str]] = []
        for impact in result["impactedBatches"]:
            batch = manifest["batches"][impact["batchId"]]
            states = batch.get("stageStates", {})
            batch_stale = False
            for stage in impact["staleStages"]:
                state = states.get(stage) if isinstance(states, dict) else None
                if isinstance(state, dict) and state.get("status") == "passed":
                    state.update({"status": "stale", "staleReason": {"sourceBatchId": batch_id, "changedFiles": result["changedFiles"], "reasons": impact["reasons"]}})
                    stale.append({"batchId": impact["batchId"], "stage": stage})
                    batch_stale = True
            if batch_stale and batch.get("status") in {"ready_to_candidate", "merged"}:
                batch["status"] = "needs_revalidation"
        manifest.setdefault("impactAnalysis", []).append({"at": manifest.get("updatedAt"), **result, "staleEvidence": stale})
        save_manifest(workspace, feature, run_id, manifest)
    append_event(workspace, feature, run_id, "evidence_invalidated", sourceBatchId=batch_id, changedFiles=result["changedFiles"], staleEvidence=stale)
    return {"success": True, **result, "staleEvidence": stale}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze content-based Batch evidence impact")
    parser.add_argument("command", choices=("analyze", "invalidate"))
    parser.add_argument("--workspace")
    parser.add_argument("--feature", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--changed-file", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        workspace, feature = resolve_workspace(args.workspace), resolve_feature(args.feature)
        if args.command == "analyze":
            result = compute_impact_set(load_manifest(workspace, feature, args.run_id), args.batch_id, args.changed_file)
            result["success"] = True
        else:
            result = invalidate_impacted_evidence(workspace, feature, args.run_id, args.batch_id, args.changed_file)
        print(json.dumps({"ok": bool(result.get("success")), **result}, ensure_ascii=False, indent=2))
        return 0 if result.get("success") else 1
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
