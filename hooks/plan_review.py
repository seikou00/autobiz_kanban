"""Determine the semantic review scope from Plan changes, without a new artifact."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from hooks.plan_json import task_contract_sha256


def review_context_digest(feature_dir: Path, code_workspaces: dict) -> str:
    """Bind review reuse to the upstream contracts and repository bindings.

    Sources are consumed through Specs and their source index; Plan does not
    reopen external documents or business code to rebuild Design's evidence.
    """
    paths = [feature_dir / name for name in (
        "proposal.md", "design.md", ".design-contract.lock.json",
        "IMPLEMENTATION_SCOPE.json", "UI_CONTEXT.json", "source-context.json",
    )]
    paths.extend(sorted((feature_dir / "specs").glob("**/*.md")))
    inputs = {
        path.relative_to(feature_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        for path in paths
    }
    payload = {"inputs": inputs, "codeWorkspaces": code_workspaces}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _tasks(data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {task["id"]: task for task in (data or {}).get("tasks", []) if isinstance(task, dict) and isinstance(task.get("id"), str)}


def _contracts(task: dict[str, Any]) -> set[str]:
    refs = {ref for ref in task.get("specRefs", []) if isinstance(ref, str) and "#SCN-" in ref}
    for field in ("apiIds", "dataIds", "decisionIds"):
        refs.update(task.get(field, []))
    refs.update(ref.split("#", 1)[-1] for ref in task.get("designRefs", []) if isinstance(ref, str))
    return refs


def review_changes(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Include changed tasks, contract collaborators and downstream deliveries.

    This is review guidance, never an approval or a replacement for the global
    reference, coverage, ownership and DAG checks.
    """
    before, after = _tasks(previous), _tasks(current)
    added, removed = set(after) - set(before), set(before) - set(after)
    changed = {task_id for task_id in set(before) & set(after)
               if task_contract_sha256(before[task_id]) != task_contract_sha256(after[task_id])}
    context_changed = previous is not None and previous.get("reviewContextDigest") != current.get("reviewContextDigest")
    seeds = added | removed | changed
    affected = set(seeds)
    touched_contracts = set().union(*(_contracts(task) for tasks in (before, after) for task_id, task in tasks.items() if task_id in seeds))
    for task_id, task in after.items():
        if _contracts(task) & touched_contracts:
            affected.add(task_id)
    expanded = True
    while expanded:
        expanded = False
        for tasks in (before, after):
            for task_id, task in tasks.items():
                if task_id not in affected and set(task.get("deps", [])) & affected:
                    affected.add(task_id)
                    expanded = True
    if previous is None or context_changed:
        scope, affected = "full", set(after)
    else:
        scope = "affected" if seeds else "none"
        affected &= set(after)
    return {
        "reviewScope": scope,
        "contextChanged": context_changed,
        "addedTaskIds": sorted(added), "removedTaskIds": sorted(removed),
        "changedTaskIds": sorted(changed), "affectedTaskIds": sorted(affected),
    }
