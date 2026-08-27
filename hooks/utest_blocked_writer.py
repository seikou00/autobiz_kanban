#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persist a plan-bound UTest blocked handoff from inspector facts."""

from __future__ import print_function

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import atomic_write_json, resolve_feature, resolve_workspace  # noqa: E402
from hooks.unit_test_result_writer import ensure_plan_result  # noqa: E402


BLOCKING_STATUSES = {
    "contract_gap",
    "workspace_binding_missing",
    "workspace_binding_invalid",
    "conflict",
    "unsupported",
    "environment_inspection_failed",
}


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _append_log(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "kind": "environment_inspection",
        "recordedAt": _utc_now(),
        "payload": payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")


def _blocking_reason(payload):
    errors = payload.get("errors")
    if isinstance(errors, list):
        values = [str(value).strip() for value in errors if str(value).strip()]
        if values:
            return "；".join(values)
    return "UTest 环境检查返回阻断状态 {}".format(payload.get("status", "unknown"))


def record_utest_block(workspace, feature, payload):
    workspace = resolve_workspace(workspace)
    feature = resolve_feature(feature)
    if not isinstance(payload, dict):
        raise ValueError("blocked payload 必须是 JSON object。修复：原样传入环境检查器返回值。")
    status = payload.get("status")
    if status not in BLOCKING_STATUSES:
        raise ValueError(
            "status={} 不是可落盘的 UTest 阻断状态。修复：ready 继续测试，init_required 完成初始化，ambiguous 等待用户选择。".format(
                status
            )
        )

    feature_dir = workspace / ".autobizdevops" / "features" / feature
    result, result_write = ensure_plan_result(workspace, feature, create=True)
    if result.get("verdict") != "BLOCKED":
        raise ValueError(
            "UNIT_TEST_RESULT 当前 verdict={}，不能用环境阻断覆盖已完成结果。修复：保留现有 Evidence 并人工归因。".format(
                result.get("verdict")
            )
        )

    plan_gap = status == "contract_gap"
    root_cause = "plan_contract_gap" if plan_gap else "environment_issue"
    suggested_checkpoint = "plan_in_progress" if plan_gap else "unit_test_in_progress"
    repair_strategy = "rollback_plan_keep_source" if plan_gap else "resolve_environment_then_retry_utest"
    reason = _blocking_reason(payload)
    created_at = _utc_now()
    fix_request = {
        "version": 1,
        "featureId": feature,
        "sourceCheckpoint": "unit_test_in_progress",
        "sourceNodeId": "dev.utest",
        "suggestedCheckpoint": suggested_checkpoint,
        "rootCause": root_cause,
        "blockingReason": reason,
        "humanActionRequired": True,
        "failedSpecRefs": [],
        "failedEvidenceIds": [],
        "failedDesignRefs": [],
        "requiredAction": payload.get("requiredAction") or repair_strategy,
        "repairStrategy": repair_strategy,
        "createdAt": created_at,
    }
    fix_path = feature_dir / "FIX_REQUEST.json"
    atomic_write_json(fix_path, fix_request)

    report_path = feature_dir / "UNIT_TEST_REPORT.md"
    report = (
        "# Unit Test Report\n\n"
        "- **Feature:** {feature}\n"
        "- **Generated At:** {created_at}\n"
        "- **Verdict:** BLOCKED\n"
        "- **Test Log:** test-output.log\n\n"
        "## Test Plan\n\n"
        "UT targets 已由当前 Plan 初始化，尚未进入测试生成与执行。\n\n"
        "## Execution Summary\n\n"
        "- Inspector status: `{status}`\n"
        "- Required action: `{required_action}`\n\n"
        "## Coverage Matrix\n\n"
        "未执行测试，coverage 保持 missing。\n\n"
        "## Failure Analysis\n\n"
        "{reason}\n\n"
        "## Fix Attempts\n\n"
        "未执行猜测性修复。\n\n"
        "## Commands\n\n"
        "环境检查输出已追加到 `test-output.log`。\n\n"
        "## Handoff\n\n"
        "- Repair strategy: `{repair_strategy}`\n"
        "- Suggested checkpoint: `{suggested_checkpoint}`\n"
    ).format(
        feature=feature,
        created_at=created_at,
        status=status,
        required_action=fix_request["requiredAction"],
        reason=reason,
        repair_strategy=repair_strategy,
        suggested_checkpoint=suggested_checkpoint,
    )
    _write_text(report_path, report)

    log_path = feature_dir / "test-output.log"
    _append_log(log_path, payload)
    return {
        "unitTestResult": str(result_write.path),
        "unitTestReport": str(report_path),
        "testOutput": str(log_path),
        "fixRequest": str(fix_path),
        "nextCheckpoint": "needs_fix",
    }
