#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Incrementally write UNIT_TEST_RESULT.json."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import (  # noqa: E402
    WriterResult,
    artifact_path,
    atomic_write_json,
    fail,
    fail_if_artifact_exists,
    load_json,
    next_numbered_id,
    render_result,
    resolve_feature,
    resolve_workspace,
    shell_join,
    with_result_data,
)
from hooks.result_writer_common import (  # noqa: E402
    collect_plan_tasks,
    derive_coverage_from_evidence,
    empty_coverage,
)


FILE_NAME = "UNIT_TEST_RESULT.json"
RESULTS = {"PASS", "PASS_WITH_WARNINGS", "FAIL", "BLOCKED", "SKIP"}
VERDICTS = {"PASS", "PASS_WITH_WARNINGS", "FAIL", "BLOCKED"}


def _path(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, FILE_NAME)


def _feature_dir(workspace: Path, feature: str) -> Path:
    return workspace / ".autobizdevops" / "features" / feature


def _initial(feature: str) -> dict[str, Any]:
    return {"version": 1, "verdict": "BLOCKED", "targets": [], "scenarioCoverage": []}


def _load(workspace: Path, feature: str) -> dict[str, Any]:
    data = load_json(_path(workspace, feature), default=_initial(feature))
    if not isinstance(data, dict):
        raise ValueError(f"{FILE_NAME} root 必须是 object")
    data.setdefault("version", 1)
    data.setdefault("verdict", "BLOCKED")
    data.setdefault("targets", [])
    data.setdefault("scenarioCoverage", [])
    return data


def _targets(data: dict[str, Any]) -> list[dict[str, Any]]:
    targets = data.setdefault("targets", [])
    if not isinstance(targets, list):
        raise ValueError("targets 必须是数组")
    return targets


def _find(data: dict[str, Any], target_id: str) -> dict[str, Any]:
    for target in _targets(data):
        if isinstance(target, dict) and target.get("targetId") == target_id:
            return target
    raise ValueError(f"target 不存在: {target_id}")


def _write(workspace: Path, feature: str, data: dict[str, Any]) -> WriterResult:
    changed = atomic_write_json(_path(workspace, feature), data)
    return WriterResult(ok=True, path=_path(workspace, feature), changed=changed)


def _append_unique(existing, values):
    result = list(existing) if isinstance(existing, list) else []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def record_execution(
    workspace,
    feature,
    *,
    target_id=None,
    task_id,
    spec_refs,
    evidence_id,
    result,
    command,
):
    """Create/update one UT target while preserving prior evidence history."""
    if result not in RESULTS:
        raise ValueError(
            "result 无效: {}。修复：使用 {}。".format(result, ", ".join(sorted(RESULTS)))
        )
    for label, value in (
        ("task_id", task_id),
        ("evidence_id", evidence_id),
        ("command", command),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("{} 不能为空。修复：传入本次测试执行的真实值。".format(label))
    if not isinstance(spec_refs, list) or not spec_refs or not all(
        isinstance(value, str) and value.strip() for value in spec_refs
    ):
        raise ValueError("spec_refs 必须是非空字符串数组。修复：传入 assignment 的 spec refs。")

    data = _load(workspace, feature)
    if target_id is None:
        target_id = next_numbered_id(
            {
                target.get("targetId")
                for target in _targets(data)
                if isinstance(target, dict) and isinstance(target.get("targetId"), str)
            },
            "UT",
        )
    elif not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("target_id 不能为空。修复：省略该参数自动分配，或传入稳定 UT ID。")
    target = None
    for candidate in _targets(data):
        if isinstance(candidate, dict) and candidate.get("targetId") == target_id:
            target = candidate
            break
    if target is None:
        target = {
            "targetId": target_id,
            "taskId": task_id,
            "specRefs": [],
            "evidenceIds": [],
            "result": result,
            "command": command,
        }
        _targets(data).append(target)

    target["taskId"] = task_id
    target["specRefs"] = _append_unique(target.get("specRefs"), spec_refs)
    target["evidenceIds"] = _append_unique(target.get("evidenceIds"), [evidence_id])
    target["result"] = result
    target["command"] = command
    return with_result_data(_write(workspace, feature, data), target=target)


record_test_execution = record_execution


def _cmd_init(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    existing = fail_if_artifact_exists(_path(workspace, feature), force=args.force)
    if existing:
        return render_result(existing)
    data = _initial(feature)
    feature_dir = _feature_dir(workspace, feature)
    if args.from_plan:
        for index, task in enumerate(collect_plan_tasks(feature_dir), start=1):
            commands = task.get("validationCommands") if isinstance(task.get("validationCommands"), list) else []
            command = ""
            if commands and isinstance(commands[0], dict):
                argv = commands[0].get("argv")
                command = (
                    shell_join(argv)
                    if isinstance(argv, list) and all(isinstance(item, str) for item in argv)
                    else str(commands[0].get("command", ""))
                )
            data["targets"].append(
                {
                    "targetId": f"UT-{index:03d}",
                    "taskId": task.get("id", ""),
                    "specRefs": task.get("specRefs", []) if isinstance(task.get("specRefs"), list) else [],
                    "evidenceIds": task.get("evidenceIds", []) if isinstance(task.get("evidenceIds"), list) else [],
                    "result": "BLOCKED",
                    "command": command or "待执行",
                }
            )
    data["scenarioCoverage"] = empty_coverage(feature_dir)
    return render_result(with_result_data(_write(workspace, feature, data), reset=bool(args.force)))


def _cmd_add_target(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    target_id = args.target_id or next_numbered_id(
        {target.get("targetId") for target in _targets(data) if isinstance(target, dict) and isinstance(target.get("targetId"), str)},
        "UT",
    )
    if any(isinstance(target, dict) and target.get("targetId") == target_id for target in _targets(data)):
        return render_result(fail("duplicate_unit_target_id", target_id))
    _targets(data).append(
        {
            "targetId": target_id,
            "taskId": args.task_id,
            "specRefs": args.spec_ref or [],
            "evidenceIds": args.evidence_id or [],
            "result": args.result,
            "command": args.command,
        }
    )
    return render_result(_write(workspace, feature, data))


def _cmd_update_target(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    target = _find(data, args.target_id)
    for field, value in (("taskId", args.task_id), ("result", args.result), ("command", args.command)):
        if value is not None:
            target[field] = value
    if args.spec_ref is not None:
        target["specRefs"] = args.spec_ref
    if args.evidence_id is not None:
        target["evidenceIds"] = args.evidence_id
    return render_result(_write(workspace, feature, data))


def _cmd_record_execution(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    return render_result(
        record_execution(
            workspace,
            feature,
            target_id=args.target_id,
            task_id=args.task_id,
            spec_refs=args.spec_ref,
            evidence_id=args.evidence_id,
            result=args.result,
            command=args.command,
        )
    )


def _cmd_set_verdict(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    data["verdict"] = args.verdict
    return render_result(_write(workspace, feature, data))


def _cmd_derive_coverage(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    data["scenarioCoverage"] = derive_coverage_from_evidence(
        _feature_dir(workspace, feature),
        action="validation",
    )
    return render_result(_write(workspace, feature, data))


def _cmd_validate(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    errors: list[dict[str, str]] = []
    if data.get("verdict") not in VERDICTS:
        errors.append({"reason": "invalid_unit_test_result_verdict"})
    if not isinstance(data.get("targets"), list) or not data["targets"]:
        errors.append({"reason": "invalid_unit_test_targets"})
    if not isinstance(data.get("scenarioCoverage"), list):
        errors.append({"reason": "invalid_scenario_coverage"})
    for index, target in enumerate(data.get("targets", []) if isinstance(data.get("targets"), list) else []):
        if not isinstance(target, dict):
            errors.append({"reason": "invalid_unit_test_target", "detail": f"targets[{index}]"})
            continue
        for field in ("targetId", "taskId", "result", "command"):
            if not isinstance(target.get(field), str) or not target.get(field):
                errors.append({"reason": f"missing_unit_test_target_{field}", "detail": f"targets[{index}]"})
        for field in ("specRefs", "evidenceIds"):
            if not isinstance(target.get(field), list) or not target.get(field):
                errors.append({"reason": f"missing_unit_test_target_{field}", "detail": f"targets[{index}]"})
    return render_result(WriterResult(ok=not errors, path=_path(workspace, feature), errors=errors))


def _cmd_show(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    return render_result(
        WriterResult(
            ok=True,
            path=_path(workspace, feature),
            data={
                "summary": {
                    "verdict": data.get("verdict"),
                    "targets": len(data.get("targets", [])) if isinstance(data.get("targets"), list) else 0,
                    "scenarioCoverage": len(data.get("scenarioCoverage", []))
                    if isinstance(data.get("scenarioCoverage"), list)
                    else 0,
                }
            },
        )
    )


def _resolve(args: argparse.Namespace) -> tuple[Path, str]:
    return resolve_workspace(args.workspace), resolve_feature(args.feature)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace")
    parser.add_argument("--feature")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Incrementally write UNIT_TEST_RESULT.json")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    _common(init)
    init.add_argument("--force", action="store_true")
    init.add_argument("--from-plan", action="store_true")
    init.set_defaults(func=_cmd_init)

    add = sub.add_parser("add-target")
    _common(add)
    add.add_argument("--target-id")
    add.add_argument("--task-id", required=True)
    add.add_argument("--spec-ref", action="append")
    add.add_argument("--evidence-id", action="append")
    add.add_argument("--result", default="BLOCKED", choices=sorted(RESULTS))
    add.add_argument("--command", required=True)
    add.set_defaults(func=_cmd_add_target, writer_command="add-target")

    update = sub.add_parser("update-target")
    _common(update)
    update.add_argument("--target-id", required=True)
    update.add_argument("--task-id")
    update.add_argument("--spec-ref", action="append")
    update.add_argument("--evidence-id", action="append")
    update.add_argument("--result", choices=sorted(RESULTS))
    update.add_argument("--command")
    update.set_defaults(func=_cmd_update_target)

    execution = sub.add_parser("record-execution")
    _common(execution)
    execution.add_argument("--target-id")
    execution.add_argument("--task-id", required=True)
    execution.add_argument("--spec-ref", action="append", required=True)
    execution.add_argument("--evidence-id", required=True)
    execution.add_argument("--result", required=True, choices=sorted(RESULTS))
    execution.add_argument("--command", required=True)
    execution.set_defaults(func=_cmd_record_execution)

    verdict = sub.add_parser("set-verdict")
    _common(verdict)
    verdict.add_argument("verdict", choices=sorted(VERDICTS))
    verdict.set_defaults(func=_cmd_set_verdict)

    coverage = sub.add_parser("derive-scenario-coverage")
    _common(coverage)
    coverage.set_defaults(func=_cmd_derive_coverage)

    validate = sub.add_parser("validate")
    _common(validate)
    validate.add_argument("--structure", action="store_true")
    validate.add_argument("--gate", action="store_true")
    validate.set_defaults(func=_cmd_validate)

    show = sub.add_parser("show")
    _common(show)
    show.add_argument("--summary", action="store_true")
    show.set_defaults(func=_cmd_show)

    args = parser.parse_args(argv)
    try:
        if getattr(args, "writer_command", args.command) == "add-target":
            missing = [name for name in ("spec_ref", "evidence_id") if not getattr(args, name)]
            if missing:
                return render_result(fail("missing_unit_target_trace_args", ",".join(missing)))
        return args.func(args)
    except Exception as exc:
        return render_result(fail("unit_test_result_writer_failed", str(exc)))


if __name__ == "__main__":
    raise SystemExit(main())
