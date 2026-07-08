#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Incrementally write REVIEW_FINDINGS.json."""

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
    load_json,
    next_numbered_id,
    render_result,
    resolve_feature,
    resolve_workspace,
)


FILE_NAME = "REVIEW_FINDINGS.json"
VERDICTS = {"PASS", "PASS_WITH_WARNINGS", "FAIL", "DEGRADED"}
SEVERITIES = {"blocker", "high", "medium", "low", "info", "minor", "important"}


def _path(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, FILE_NAME)


def _initial() -> dict[str, Any]:
    return {"version": 1, "verdict": "PASS", "findings": []}


def _load(workspace: Path, feature: str) -> dict[str, Any]:
    data = load_json(_path(workspace, feature), default=_initial())
    if not isinstance(data, dict):
        raise ValueError(f"{FILE_NAME} root 必须是 object")
    data.setdefault("version", 1)
    data.setdefault("verdict", "PASS")
    data.setdefault("findings", [])
    return data


def _findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    findings = data.setdefault("findings", [])
    if not isinstance(findings, list):
        raise ValueError("findings 必须是数组")
    return findings


def _find(data: dict[str, Any], finding_id: str) -> dict[str, Any]:
    for finding in _findings(data):
        if isinstance(finding, dict) and finding.get("id") == finding_id:
            return finding
    raise ValueError(f"finding 不存在: {finding_id}")


def _write(workspace: Path, feature: str, data: dict[str, Any]) -> WriterResult:
    changed = atomic_write_json(_path(workspace, feature), data)
    return WriterResult(ok=True, path=_path(workspace, feature), changed=changed)


def _cmd_init(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _initial()
    data["verdict"] = args.verdict
    return render_result(_write(workspace, feature, data))


def _cmd_add_finding(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    finding_id = args.id or next_numbered_id(
        {finding.get("id") for finding in _findings(data) if isinstance(finding, dict) and isinstance(finding.get("id"), str)},
        "FIND",
    )
    if any(isinstance(finding, dict) and finding.get("id") == finding_id for finding in _findings(data)):
        return render_result(fail("duplicate_review_finding_id", finding_id))
    finding: dict[str, Any] = {
        "id": finding_id,
        "taskId": args.task_id,
        "specRefs": args.spec_ref or [],
        "evidenceIds": args.evidence_id or [],
        "severity": args.severity,
        "message": args.message,
    }
    if args.suggested_checkpoint:
        finding["suggestedCheckpoint"] = args.suggested_checkpoint
    if args.ui_required:
        finding["uiRequired"] = True
        finding["pageRefs"] = args.page_ref or []
        finding["interactionRefs"] = args.interaction_ref or []
        finding["visualSourceRefs"] = args.visual_source_ref or []
    _findings(data).append(finding)
    if args.severity in {"blocker", "high"} and data.get("verdict") == "PASS":
        data["verdict"] = "FAIL"
    return render_result(_write(workspace, feature, data))


def _cmd_update_finding(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    finding = _find(data, args.id)
    for field, value in (("taskId", args.task_id), ("severity", args.severity), ("message", args.message)):
        if value is not None:
            finding[field] = value
    if args.spec_ref is not None:
        finding["specRefs"] = args.spec_ref
    if args.evidence_id is not None:
        finding["evidenceIds"] = args.evidence_id
    if args.suggested_checkpoint is not None:
        finding["suggestedCheckpoint"] = args.suggested_checkpoint
    if args.ui_required:
        finding["uiRequired"] = True
        finding["pageRefs"] = args.page_ref or finding.get("pageRefs", [])
        finding["interactionRefs"] = args.interaction_ref or finding.get("interactionRefs", [])
        finding["visualSourceRefs"] = args.visual_source_ref or finding.get("visualSourceRefs", [])
    return render_result(_write(workspace, feature, data))


def _cmd_set_verdict(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    data["verdict"] = args.verdict
    return render_result(_write(workspace, feature, data))


def _cmd_validate(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    errors: list[dict[str, str]] = []
    if data.get("verdict") not in VERDICTS:
        errors.append({"reason": "invalid_review_findings_verdict"})
    if not isinstance(data.get("findings"), list):
        errors.append({"reason": "invalid_review_findings_items"})
    for index, finding in enumerate(data.get("findings", []) if isinstance(data.get("findings"), list) else []):
        if not isinstance(finding, dict):
            errors.append({"reason": "invalid_review_finding", "detail": f"findings[{index}]"})
            continue
        for field in ("id", "taskId", "severity", "message"):
            if not isinstance(finding.get(field), str) or not finding.get(field):
                errors.append({"reason": f"missing_review_finding_{field}", "detail": f"findings[{index}]"})
        for field in ("specRefs", "evidenceIds"):
            if not isinstance(finding.get(field), list) or not finding.get(field):
                errors.append({"reason": f"missing_review_finding_{field}", "detail": f"findings[{index}]"})
    return render_result(WriterResult(ok=not errors, path=_path(workspace, feature), errors=errors))


def _cmd_show(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    return render_result(
        WriterResult(
            ok=True,
            path=_path(workspace, feature),
            data={"summary": {"verdict": data.get("verdict"), "findings": len(data.get("findings", []))}},
        )
    )


def _resolve(args: argparse.Namespace) -> tuple[Path, str]:
    return resolve_workspace(args.workspace), resolve_feature(args.feature)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace")
    parser.add_argument("--feature")


def _finding_args(parser: argparse.ArgumentParser, *, require_id: bool) -> None:
    _common(parser)
    parser.add_argument("--id", required=require_id)
    parser.add_argument("--task-id")
    parser.add_argument("--spec-ref", action="append")
    parser.add_argument("--evidence-id", action="append")
    parser.add_argument("--severity", choices=sorted(SEVERITIES))
    parser.add_argument("--message")
    parser.add_argument("--suggested-checkpoint")
    parser.add_argument("--ui-required", action="store_true")
    parser.add_argument("--page-ref", action="append")
    parser.add_argument("--interaction-ref", action="append")
    parser.add_argument("--visual-source-ref", action="append")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Incrementally write REVIEW_FINDINGS.json")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    _common(init)
    init.add_argument("--verdict", default="PASS", choices=sorted(VERDICTS))
    init.set_defaults(func=_cmd_init)

    add = sub.add_parser("add-finding")
    _finding_args(add, require_id=False)
    add.set_defaults(func=_cmd_add_finding, writer_command="add-finding")

    update = sub.add_parser("update-finding")
    _finding_args(update, require_id=True)
    update.set_defaults(func=_cmd_update_finding)

    verdict = sub.add_parser("set-verdict")
    _common(verdict)
    verdict.add_argument("verdict", choices=sorted(VERDICTS))
    verdict.set_defaults(func=_cmd_set_verdict)

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
        if getattr(args, "writer_command", args.command) == "add-finding":
            missing = [
                name
                for name in ("task_id", "spec_ref", "evidence_id", "severity", "message")
                if not getattr(args, name)
            ]
            if missing:
                return render_result(fail("missing_review_finding_args", ",".join(missing)))
        return args.func(args)
    except Exception as exc:
        return render_result(fail("review_findings_writer_failed", str(exc)))


if __name__ == "__main__":
    raise SystemExit(main())
