#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Incrementally write UI_CONTEXT.json."""

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
    parse_json_value,
    render_result,
    resolve_feature,
    resolve_workspace,
    with_result_data,
)
from hooks.ui_context import (  # noqa: E402
    DECISION_SOURCES,
    FRONTEND_ROUTES,
    UI_CONTEXT_VERSION,
    validate_ui_context_data,
)


UI_CONTEXT_FILE = "UI_CONTEXT.json"
STATUS_ORDER = {"defaulted": 0, "confirmed": 1, "locked": 2}


def _path(workspace: Path, feature: str) -> Path:
    return artifact_path(workspace, feature, UI_CONTEXT_FILE)


def _load(workspace: Path, feature: str) -> dict[str, Any]:
    return load_json(_path(workspace, feature), default=_initial(feature))


def _initial(feature: str) -> dict[str, Any]:
    return {
        "version": UI_CONTEXT_VERSION,
        "featureId": feature,
        "uiRequired": False,
        "decisionStatus": "defaulted",
        "decisionSource": "default_false",
        "notApplicableReason": "未识别 UI 范围",
        "pages": [],
        "interactions": [],
        "visualSources": [],
        "capabilities": [],
    }


STRUCTURE_ALLOWED_ERRORS = {
    "ui_context_required_without_ui_scope",
    "ui_context_not_applicable_reason_missing",
}


def _structure_errors(data: dict[str, Any], feature: str) -> list[str]:
    return [
        error
        for error in validate_ui_context_data(data, feature_id=feature)
        if error not in STRUCTURE_ALLOWED_ERRORS
    ]


def _write(
    workspace: Path,
    feature: str,
    data: dict[str, Any],
    *,
    require_confirmed: bool = False,
    require_locked: bool = False,
) -> WriterResult:
    path = _path(workspace, feature)
    if require_confirmed or require_locked:
        errors = validate_ui_context_data(
            data,
            feature_id=feature,
            require_confirmed=require_confirmed,
            require_locked=require_locked,
        )
    else:
        errors = _structure_errors(data, feature)
    if errors:
        return WriterResult(ok=False, path=path, errors=[{"reason": error} for error in errors])
    changed = atomic_write_json(path, data)
    return WriterResult(ok=True, path=path, changed=changed)


def _find(items: list[Any], field: str, value: str) -> dict[str, Any] | None:
    for item in items:
        if isinstance(item, dict) and item.get(field) == value:
            return item
    return None


def _upsert(items: list[Any], field: str, item: dict[str, Any]) -> None:
    item_id = item[field]
    existing = _find(items, field, item_id)
    if existing is None:
        items.append(item)
    else:
        existing.update(item)


def _string_array(values: list[str] | None) -> list[str]:
    return [value.strip() for value in values or [] if value.strip()]


def _cmd_init(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    existing = fail_if_artifact_exists(_path(workspace, feature), force=args.force)
    if existing:
        return render_result(existing)
    data = _initial(feature)
    if args.ui_required:
        data["uiRequired"] = True
        data["decisionSource"] = args.decision_source
        data["notApplicableReason"] = ""
    return render_result(with_result_data(_write(workspace, feature, data), reset=bool(args.force)))


def _cmd_set_ui_required(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    required = args.required.lower() == "true"
    data["uiRequired"] = required
    data["decisionSource"] = args.decision_source
    if required:
        data["notApplicableReason"] = ""
    else:
        reason = (args.reason or "").strip()
        if not reason:
            return render_result(fail("missing_not_applicable_reason", "--reason 必填"))
        data["notApplicableReason"] = reason
        data["pages"] = []
        data["interactions"] = []
        data["visualSources"] = []
        data["capabilities"] = []
        if data.get("decisionStatus") == "defaulted":
            data.pop("confirmedAtCheckpoint", None)
            data.pop("lockedAtCheckpoint", None)
    return render_result(_write(workspace, feature, data))


def _cmd_add_page(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    pages = data.setdefault("pages", [])
    page_id = args.page_id or next_numbered_id(
        {item.get("pageId") for item in pages if isinstance(item, dict) and isinstance(item.get("pageId"), str)},
        "PAGE",
    )
    item = {
        "pageId": page_id,
        "name": args.name,
        "goal": args.goal,
    }
    if args.route_hint:
        item["routeHint"] = args.route_hint
    if args.state:
        item["states"] = _string_array(args.state)
    _upsert(pages, "pageId", item)
    return render_result(_write(workspace, feature, data))


def _cmd_add_interaction(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    interactions = data.setdefault("interactions", [])
    interaction_id = args.interaction_id or next_numbered_id(
        {
            item.get("interactionId")
            for item in interactions
            if isinstance(item, dict) and isinstance(item.get("interactionId"), str)
        },
        "UIX",
    )
    item = {
        "interactionId": interaction_id,
        "pageId": args.page_id,
        "summary": args.summary,
    }
    if args.state_ref:
        item["stateRefs"] = _string_array(args.state_ref)
    if args.spec_ref:
        item["specRefs"] = _string_array(args.spec_ref)
    _upsert(interactions, "interactionId", item)
    return render_result(_write(workspace, feature, data))


def _cmd_add_visual_source(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    sources = data.setdefault("visualSources", [])
    source_id = args.source_id or next_numbered_id(
        {
            item.get("sourceId")
            for item in sources
            if isinstance(item, dict) and isinstance(item.get("sourceId"), str)
        },
        "VIS",
    )
    item = {
        "sourceId": source_id,
        "type": args.type,
        "path": args.path,
    }
    if args.route:
        item["route"] = args.route
    if args.required is not None:
        item["required"] = args.required.lower() == "true"
    _upsert(sources, "sourceId", item)
    return render_result(_write(workspace, feature, data))


def _cmd_add_capability(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    capabilities = data.setdefault("capabilities", [])
    item = {
        "capabilityId": args.capability_id,
        "pageRefs": _string_array(args.page_ref),
        "interactionRefs": _string_array(args.interaction_ref),
        "specRefs": _string_array(args.spec_ref),
    }
    if args.ui_required is not None:
        item["uiRequired"] = args.ui_required.lower() == "true"
    _upsert(capabilities, "capabilityId", item)
    return render_result(_write(workspace, feature, data))


def _cmd_confirm(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    if STATUS_ORDER.get(str(data.get("decisionStatus")), -1) > STATUS_ORDER["confirmed"]:
        return render_result(fail("ui_context_status_regression", "locked 不能回退到 confirmed"))
    data["decisionStatus"] = "confirmed"
    data["confirmedAtCheckpoint"] = "prd_done"
    if args.decision_source:
        data["decisionSource"] = args.decision_source
    return render_result(_write(workspace, feature, data))


def _cmd_lock(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    if STATUS_ORDER.get(str(data.get("decisionStatus")), -1) < STATUS_ORDER["confirmed"]:
        return render_result(fail("ui_context_lock_without_confirm", "lock 前必须先 confirmed"))
    data["decisionStatus"] = "locked"
    data["lockedAtCheckpoint"] = "specs_done"
    if not data.get("confirmedAtCheckpoint"):
        data["confirmedAtCheckpoint"] = "prd_done"
    return render_result(_write(workspace, feature, data, require_locked=True))


def _cmd_validate(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    path = _path(workspace, feature)
    try:
        data = load_json(path)
    except Exception as exc:
        return render_result(fail("ui_context_load_failed", str(exc), path=path))
    if args.confirmed or args.locked:
        errors = validate_ui_context_data(
            data,
            feature_id=feature,
            require_confirmed=args.confirmed,
            require_locked=args.locked,
        )
    else:
        errors = _structure_errors(data, feature)
    return render_result(
        WriterResult(
            ok=not errors,
            path=path,
            errors=[{"reason": error} for error in errors],
            data={"validation": "gate" if args.confirmed or args.locked else "structure"},
        )
    )


def _cmd_show(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    summary = {
        "featureId": data.get("featureId"),
        "uiRequired": data.get("uiRequired"),
        "decisionStatus": data.get("decisionStatus"),
        "pages": len(data.get("pages", [])) if isinstance(data.get("pages"), list) else 0,
        "interactions": len(data.get("interactions", [])) if isinstance(data.get("interactions"), list) else 0,
        "visualSources": len(data.get("visualSources", [])) if isinstance(data.get("visualSources"), list) else 0,
        "capabilities": len(data.get("capabilities", [])) if isinstance(data.get("capabilities"), list) else 0,
    }
    return render_result(WriterResult(ok=True, path=_path(workspace, feature), data={"summary": summary}))


def _resolve(args: argparse.Namespace) -> tuple[Path, str]:
    return resolve_workspace(args.workspace), resolve_feature(args.feature)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace")
    parser.add_argument("--feature")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Incrementally write UI_CONTEXT.json")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    _add_common(init)
    init.add_argument("--force", action="store_true")
    init.add_argument("--ui-required", action="store_true")
    init.add_argument("--decision-source", default="default_false", choices=sorted(DECISION_SOURCES))
    init.set_defaults(func=_cmd_init)

    set_ui = sub.add_parser("set-ui-required")
    _add_common(set_ui)
    set_ui.add_argument("required", choices=["true", "false"])
    set_ui.add_argument("--reason", default="")
    set_ui.add_argument("--decision-source", default="prd_inferred", choices=sorted(DECISION_SOURCES))
    set_ui.set_defaults(func=_cmd_set_ui_required)

    page = sub.add_parser("add-page")
    _add_common(page)
    page.add_argument("--page-id")
    page.add_argument("--name", required=True)
    page.add_argument("--goal", required=True)
    page.add_argument("--route-hint")
    page.add_argument("--state", action="append")
    page.set_defaults(func=_cmd_add_page)
    update_page = sub.add_parser("update-page")
    update_page.set_defaults(func=_cmd_add_page)
    for action in (update_page,):
        _add_common(action)
        action.add_argument("--page-id", required=True)
        action.add_argument("--name", required=True)
        action.add_argument("--goal", required=True)
        action.add_argument("--route-hint")
        action.add_argument("--state", action="append")

    interaction = sub.add_parser("add-interaction")
    _add_common(interaction)
    interaction.add_argument("--interaction-id")
    interaction.add_argument("--page-id", required=True)
    interaction.add_argument("--summary", required=True)
    interaction.add_argument("--state-ref", action="append")
    interaction.add_argument("--spec-ref", action="append")
    interaction.set_defaults(func=_cmd_add_interaction)
    update_interaction = sub.add_parser("update-interaction")
    _add_common(update_interaction)
    update_interaction.add_argument("--interaction-id", required=True)
    update_interaction.add_argument("--page-id", required=True)
    update_interaction.add_argument("--summary", required=True)
    update_interaction.add_argument("--state-ref", action="append")
    update_interaction.add_argument("--spec-ref", action="append")
    update_interaction.set_defaults(func=_cmd_add_interaction)

    visual = sub.add_parser("add-visual-source")
    _add_common(visual)
    visual.add_argument("--source-id")
    visual.add_argument("--type", required=True)
    visual.add_argument("--path", required=True)
    visual.add_argument("--route", choices=sorted(FRONTEND_ROUTES - {"none"}))
    visual.add_argument("--required", choices=["true", "false"])
    visual.set_defaults(func=_cmd_add_visual_source)
    update_visual = sub.add_parser("update-visual-source")
    _add_common(update_visual)
    update_visual.add_argument("--source-id", required=True)
    update_visual.add_argument("--type", required=True)
    update_visual.add_argument("--path", required=True)
    update_visual.add_argument("--route", choices=sorted(FRONTEND_ROUTES - {"none"}))
    update_visual.add_argument("--required", choices=["true", "false"])
    update_visual.set_defaults(func=_cmd_add_visual_source)

    capability = sub.add_parser("add-capability")
    _add_common(capability)
    capability.add_argument("--capability-id", required=True)
    capability.add_argument("--page-ref", action="append")
    capability.add_argument("--interaction-ref", action="append")
    capability.add_argument("--spec-ref", action="append")
    capability.add_argument("--ui-required", choices=["true", "false"])
    capability.set_defaults(func=_cmd_add_capability)
    update_capability = sub.add_parser("update-capability")
    _add_common(update_capability)
    update_capability.add_argument("--capability-id", required=True)
    update_capability.add_argument("--page-ref", action="append")
    update_capability.add_argument("--interaction-ref", action="append")
    update_capability.add_argument("--spec-ref", action="append")
    update_capability.add_argument("--ui-required", choices=["true", "false"])
    update_capability.set_defaults(func=_cmd_add_capability)

    confirm = sub.add_parser("confirm")
    _add_common(confirm)
    confirm.add_argument("--decision-source", choices=sorted(DECISION_SOURCES))
    confirm.set_defaults(func=_cmd_confirm)

    lock = sub.add_parser("lock")
    _add_common(lock)
    lock.set_defaults(func=_cmd_lock)

    validate = sub.add_parser("validate")
    _add_common(validate)
    validate.add_argument("--structure", action="store_true")
    validate.add_argument("--confirmed", action="store_true")
    validate.add_argument("--locked", action="store_true")
    validate.set_defaults(func=_cmd_validate)

    show = sub.add_parser("show")
    _add_common(show)
    show.add_argument("--summary", action="store_true")
    show.set_defaults(func=_cmd_show)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        return render_result(fail("ui_context_writer_failed", str(exc)))


if __name__ == "__main__":
    raise SystemExit(main())
