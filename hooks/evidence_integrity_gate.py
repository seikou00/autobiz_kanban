#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integrity and completion gates for Autodev evidence streams."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HOOKS_DIR = ROOT / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from evidence_store import (  # noqa: E402
    EVIDENCE_ID_RE,
    EvidenceStoreError,
    index_path,
    load_index,
    read_records,
    snapshot,
    stream_path,
    validate_record,
)
from plan_json import (  # noqa: E402
    blocked_tasks,
    failed_tasks,
    load_and_validate_plan,
    plan_json_path,
    task_ids,
    tasks,
    unfinished_tasks,
)


PASS_RESULTS = {"pass", "passed", "success", "ok", "PASS", "PASS_WITH_WARNINGS"}


def _expected_evidence_id(index: int) -> str:
    return f"ev_{index:04d}"


def check_integrity(target_feature_dir: Path, *, require_index: bool = True) -> list[str]:
    errors: list[str] = []
    path = stream_path(target_feature_dir)
    if not path.is_file() or path.stat().st_size <= 0:
        return ["missing_evidence_stream"]

    try:
        records = read_records(path)
    except EvidenceStoreError as exc:
        return [str(exc)]
    if not records:
        errors.append("empty_evidence_stream")

    seen: set[str] = set()
    for line_no, record in enumerate(records, start=1):
        for reason in validate_record(record):
            errors.append(f"line={line_no}:{reason}")
        evidence_id = record.get("evidenceId")
        if isinstance(evidence_id, str):
            if evidence_id in seen:
                errors.append(f"duplicate_evidence_id:{evidence_id}")
            seen.add(evidence_id)
            if evidence_id != _expected_evidence_id(line_no):
                errors.append(f"non_sequential_evidence_id:line={line_no}:id={evidence_id}")
        task_id = record.get("taskId")
        if isinstance(task_id, str) and task_id and not task_id.startswith("T"):
            errors.append(f"line={line_no}:invalid_task_id:{task_id}")

    try:
        index = load_index(target_feature_dir)
    except EvidenceStoreError as exc:
        errors.append(str(exc))
        index = None
    if index is None:
        if require_index:
            errors.append(f"missing_evidence_index:{index_path(target_feature_dir)}")
    else:
        snap = snapshot(target_feature_dir)
        if index.get("lineCount") != snap.line_count:
            errors.append("evidence_index_mismatch:lineCount")
        if index.get("lastEvidenceId") != snap.last_evidence_id:
            errors.append("evidence_index_mismatch:lastEvidenceId")
        if index.get("sha256") != snap.sha256:
            errors.append("evidence_stream_rewritten_or_truncated:sha256")
    return errors


def _validation_passed(record: dict[str, Any]) -> bool:
    validation = record.get("validation")
    if not isinstance(validation, dict):
        return False
    result = validation.get("result")
    if isinstance(result, str) and result.strip() in PASS_RESULTS:
        return True
    exit_code = validation.get("exitCode")
    return exit_code == 0


def check_plan_evidence_refs(target_feature_dir: Path) -> list[str]:
    errors: list[str] = []
    plan, plan_errors = load_and_validate_plan(plan_json_path(target_feature_dir))
    if plan_errors:
        return [f"plan_json:{error}" for error in plan_errors]
    if plan is None:
        return ["missing_plan_json"]

    records = read_records(stream_path(target_feature_dir))
    known_evidence_ids = {
        evidence_id
        for record in records
        if isinstance((evidence_id := record.get("evidenceId")), str)
    }
    known_tasks = task_ids(plan)
    for record in records:
        task_id = record.get("taskId")
        if isinstance(task_id, str) and task_id and task_id not in known_tasks:
            errors.append(f"unknown_evidence_task_id:{task_id}")
    for task in tasks(plan):
        task_id = str(task.get("id", ""))
        evidence_ids = task.get("evidenceIds")
        if not isinstance(evidence_ids, list):
            continue
        for evidence_id in evidence_ids:
            if not isinstance(evidence_id, str) or not EVIDENCE_ID_RE.match(evidence_id):
                errors.append(f"{task_id}.invalid_evidence_id:{evidence_id}")
            elif evidence_id not in known_evidence_ids:
                errors.append(f"{task_id}.unknown_evidence_id:{evidence_id}")
    return errors


def check_code_done(target_feature_dir: Path) -> list[str]:
    errors = check_integrity(target_feature_dir, require_index=True)
    if not errors:
        errors.extend(check_plan_evidence_refs(target_feature_dir))

    plan, plan_errors = load_and_validate_plan(plan_json_path(target_feature_dir), require_all_done=True)
    if plan_errors:
        errors.extend(f"plan_json:{error}" for error in plan_errors)
        if plan is not None and (blocked := blocked_tasks(plan)):
            errors.append("unresolved_blocker:" + ",".join(blocked))
        return errors
    if plan is None:
        errors.append("missing_plan_json")
        return errors

    if unfinished := unfinished_tasks(plan):
        errors.append("plan_json_unfinished_tasks:" + ",".join(unfinished))
    if failed := failed_tasks(plan):
        errors.append("plan_json_failed_tasks:" + ",".join(failed))
    if blocked := blocked_tasks(plan):
        errors.append("unresolved_blocker:" + ",".join(blocked))

    try:
        records = read_records(stream_path(target_feature_dir))
    except EvidenceStoreError as exc:
        errors.append(str(exc))
        records = ()
    pass_by_task = {
        str(record.get("taskId"))
        for record in records
        if isinstance(record.get("taskId"), str) and _validation_passed(record)
    }
    for task in tasks(plan):
        task_id = str(task.get("id", ""))
        if task_id and task_id not in pass_by_task:
            errors.append(f"missing_pass_evidence_for_task:{task_id}")
    return errors


def _cmd_check(args: argparse.Namespace) -> int:
    target = Path(args.feature_dir).resolve()
    errors = check_integrity(target, require_index=not args.allow_missing_index)
    if args.refs:
        errors.extend(check_plan_evidence_refs(target))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"EVIDENCE_INTEGRITY_PASS path={stream_path(target)}")
    return 0


def _cmd_code_done(args: argparse.Namespace) -> int:
    target = Path(args.feature_dir).resolve()
    errors = check_code_done(target)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"CODE_DONE_EVIDENCE_PASS path={stream_path(target)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Autodev evidence integrity")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check")
    check.add_argument("--feature-dir", required=True)
    check.add_argument("--allow-missing-index", action="store_true")
    check.add_argument("--refs", action="store_true", help="Also validate plan.json evidence references")
    check.set_defaults(func=_cmd_check)

    code_done = subparsers.add_parser("code-done")
    code_done.add_argument("--feature-dir", required=True)
    code_done.set_defaults(func=_cmd_code_done)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
