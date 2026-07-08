#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Incrementally write E2E_RESULT.json."""

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
    parse_json_value,
    render_result,
    resolve_feature,
    resolve_workspace,
    with_result_data,
)
from hooks.result_writer_common import derive_coverage_from_evidence, empty_coverage  # noqa: E402


FILE_NAME = "E2E_RESULT.json"
RESULTS = {"PASS", "FAIL", "BLOCKED", "SKIP"}
VERDICTS = {"PASS", "PASS_WITH_WARNINGS", "FAIL", "BLOCKED"}


def _path(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, FILE_NAME)


def _feature_dir(workspace: Path, feature: str) -> Path:
    return workspace / ".autobizdevops" / "features" / feature


def _initial(feature: str) -> dict[str, Any]:
    return {"version": 1, "verdict": "BLOCKED", "cases": [], "scenarioCoverage": []}


def _load(workspace: Path, feature: str) -> dict[str, Any]:
    data = load_json(_path(workspace, feature), default=_initial(feature))
    if not isinstance(data, dict):
        raise ValueError(f"{FILE_NAME} root 必须是 object")
    data.setdefault("version", 1)
    data.setdefault("verdict", "BLOCKED")
    data.setdefault("cases", [])
    data.setdefault("scenarioCoverage", [])
    return data


def _cases(data: dict[str, Any]) -> list[dict[str, Any]]:
    cases = data.setdefault("cases", [])
    if not isinstance(cases, list):
        raise ValueError("cases 必须是数组")
    return cases


def _next_case_id(data: dict[str, Any], feature: str) -> str:
    prefix = f"E2E-{feature}-"
    highest = 0
    for case in _cases(data):
        case_id = case.get("caseId") if isinstance(case, dict) else None
        if isinstance(case_id, str) and case_id.startswith(prefix):
            suffix = case_id[len(prefix) :]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:03d}"


def _find(data: dict[str, Any], case_id: str) -> dict[str, Any]:
    for case in _cases(data):
        if isinstance(case, dict) and case.get("caseId") == case_id:
            return case
    raise ValueError(f"E2E case 不存在: {case_id}")


def _write(workspace: Path, feature: str, data: dict[str, Any]) -> WriterResult:
    changed = atomic_write_json(_path(workspace, feature), data)
    return WriterResult(ok=True, path=_path(workspace, feature), changed=changed)


def _cmd_init(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    existing = fail_if_artifact_exists(_path(workspace, feature), force=args.force)
    if existing:
        return render_result(existing)
    data = _initial(feature)
    data["scenarioCoverage"] = empty_coverage(_feature_dir(workspace, feature))
    return render_result(with_result_data(_write(workspace, feature, data), reset=bool(args.force)))


def _cmd_add_case(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    case_id = args.case_id or _next_case_id(data, feature)
    if any(isinstance(case, dict) and case.get("caseId") == case_id for case in _cases(data)):
        return render_result(fail("duplicate_e2e_case_id", case_id))
    steps = [parse_json_value(raw) for raw in args.step_json or []]
    _cases(data).append(
        {
            "caseId": case_id,
            "taskId": args.task_id,
            "specRefs": args.spec_ref or [],
            "evidenceIds": args.evidence_id or [],
            "uiRequired": args.ui_required == "true",
            "pageRefs": args.page_ref or [],
            "interactionRefs": args.interaction_ref or [],
            "visualSourceRefs": args.visual_source_ref or [],
            "executionMode": args.execution_mode,
            "steps": steps,
            "verdict": args.verdict,
        }
    )
    return render_result(_write(workspace, feature, data))


def _cmd_update_case(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    case = _find(data, args.case_id)
    for field, value in (
        ("taskId", args.task_id),
        ("executionMode", args.execution_mode),
        ("verdict", args.verdict),
    ):
        if value is not None:
            case[field] = value
    if args.spec_ref is not None:
        case["specRefs"] = args.spec_ref
    if args.evidence_id is not None:
        case["evidenceIds"] = args.evidence_id
    if args.step_json is not None:
        case["steps"] = [parse_json_value(raw) for raw in args.step_json]
    if args.ui_required is not None:
        case["uiRequired"] = args.ui_required == "true"
    if args.page_ref is not None:
        case["pageRefs"] = args.page_ref
    if args.interaction_ref is not None:
        case["interactionRefs"] = args.interaction_ref
    if args.visual_source_ref is not None:
        case["visualSourceRefs"] = args.visual_source_ref
    return render_result(_write(workspace, feature, data))


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
        skill="autodev-e2e",
    )
    return render_result(_write(workspace, feature, data))


def _cmd_validate(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    errors: list[dict[str, str]] = []
    if data.get("verdict") not in VERDICTS:
        errors.append({"reason": "invalid_e2e_result_summary_verdict"})
    if not isinstance(data.get("cases"), list) or not data["cases"]:
        errors.append({"reason": "invalid_e2e_result_cases"})
    for case in data.get("cases", []) if isinstance(data.get("cases"), list) else []:
        if not isinstance(case, dict):
            continue
        for field in ("caseId", "taskId", "verdict"):
            if not isinstance(case.get(field), str) or not case.get(field):
                errors.append({"reason": f"missing_e2e_case_{field}"})
        for field in ("specRefs", "evidenceIds"):
            if not isinstance(case.get(field), list) or not case.get(field):
                errors.append({"reason": f"missing_e2e_case_{field}"})
        if not isinstance(case.get("executionMode"), str) or not case.get("executionMode"):
            errors.append({"reason": "missing_e2e_execution_mode"})
        if not isinstance(case.get("steps"), list) or not case.get("steps"):
            errors.append({"reason": "invalid_e2e_steps"})
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
                    "cases": len(data.get("cases", [])) if isinstance(data.get("cases"), list) else 0,
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


def _case_args(parser: argparse.ArgumentParser, *, require_case_id: bool) -> None:
    _common(parser)
    parser.add_argument("--case-id", required=require_case_id)
    parser.add_argument("--task-id")
    parser.add_argument("--spec-ref", action="append")
    parser.add_argument("--evidence-id", action="append")
    parser.add_argument("--ui-required", choices=["true", "false"])
    parser.add_argument("--page-ref", action="append")
    parser.add_argument("--interaction-ref", action="append")
    parser.add_argument("--visual-source-ref", action="append")
    parser.add_argument("--execution-mode")
    parser.add_argument("--step-json", action="append")
    parser.add_argument("--verdict", choices=sorted(RESULTS))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Incrementally write E2E_RESULT.json")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    _common(init)
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=_cmd_init)

    add = sub.add_parser("add-case")
    _case_args(add, require_case_id=False)
    add.set_defaults(func=_cmd_add_case, writer_command="add-case")

    update = sub.add_parser("update-case")
    _case_args(update, require_case_id=True)
    update.set_defaults(func=_cmd_update_case)

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
        if getattr(args, "writer_command", args.command) == "add-case":
            missing = [
                name
                for name in ("task_id", "spec_ref", "evidence_id", "execution_mode", "step_json", "verdict")
                if not getattr(args, name)
            ]
            if missing:
                return render_result(fail("missing_e2e_case_args", ",".join(missing)))
        return args.func(args)
    except Exception as exc:
        return render_result(fail("e2e_result_writer_failed", str(exc)))


if __name__ == "__main__":
    raise SystemExit(main())
