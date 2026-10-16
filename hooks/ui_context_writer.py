#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Incrementally write UI_CONTEXT.json."""

from __future__ import annotations

import argparse
import html
import shutil
import sys
import uuid
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
    visual_source_bundle_sha256,
    visual_source_content_sha256,
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


def _load_plan_tasks(workspace: Path, feature: str) -> list[dict[str, Any]]:
    """Best-effort load of formal Plan tasks for the generated Markdown view."""
    feature_dir = _path(workspace, feature).parent
    plan_path = feature_dir / "plan.json"
    if not plan_path.is_file():
        return []

    try:
        root = load_json(plan_path)
    except Exception:
        return []
    if not isinstance(root, dict):
        return []

    # Compatibility with the former monolithic plan format.
    root_tasks = root.get("tasks")
    if isinstance(root_tasks, list):
        return [task for task in root_tasks if isinstance(task, dict)]

    tasks: list[dict[str, Any]] = []
    batches = root.get("batches")
    if not isinstance(batches, list):
        return tasks
    for batch in batches:
        if not isinstance(batch, dict):
            continue
        batch_id = batch.get("id")
        if not isinstance(batch_id, str) or not batch_id.startswith("B") or not batch_id[1:].isdigit():
            continue
        try:
            batch_plan = load_json(feature_dir / "plans" / batch_id / "plan.json")
        except Exception:
            continue
        if not isinstance(batch_plan, dict):
            continue
        batch_tasks = batch_plan.get("tasks")
        if isinstance(batch_tasks, list):
            tasks.extend(task for task in batch_tasks if isinstance(task, dict))
    return tasks


def _ref_ids(item: dict[str, Any], field: str) -> list[str]:
    values = item.get(field)
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value.strip()))


def _md_cell(value: Any) -> str:
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def _pre_plan_mapping_lines(data: dict[str, Any]) -> list[str]:
    """Render PAGE/VIS/UIX scope from capabilities before a formal Plan exists."""
    pages = {page["pageId"]: page for page in data.get("pages", [])}
    interactions = {item["interactionId"]: item for item in data.get("interactions", [])}
    specs_finalized = (
        data.get("lockedAtCheckpoint") == "specs_done"
        or data.get("decisionStatus") == "locked"
    )
    unbound_page_label = "未明确页面归属" if specs_finalized else "待specs阶段归属"
    raw_sources = [source for source in data.get("visualSources", []) if isinstance(source, dict)]
    ignored_source_ids = {
        source.get("sourceId")
        for source in raw_sources
        if source.get("type") == "prototype_link" and isinstance(source.get("sourceId"), str)
    }
    sources = {
        source["sourceId"]: source
        for source in raw_sources
        if source.get("type") != "prototype_link" and isinstance(source.get("sourceId"), str)
    }
    capabilities = [cap for cap in data.get("capabilities", []) if cap.get("uiRequired") is not False]
    records: list[dict[str, Any]] = []
    shown_pages: set[str] = set()
    shown_interactions: set[str] = set()
    shown_sources: set[str] = set()

    def interaction_page(interaction_id: str) -> str | None:
        return interactions.get(interaction_id, {}).get("pageId")

    def page_scope(item: dict[str, Any]) -> list[str | None]:
        result: list[str | None] = list(_ref_ids(item, "pageRefs"))
        for interaction_id in _ref_ids(item, "interactionRefs"):
            page_id = interaction_page(interaction_id)
            if page_id not in result:
                result.append(page_id)
        return result

    def source_scope(item: dict[str, Any]) -> list[str]:
        return [
            source_id
            for source_id in _ref_ids(item, "visualSourceRefs")
            if source_id not in ignored_source_ids
        ]

    def add_record(page_id: str | None, interaction_ids: list[str], source_ids: list[str],
                   routes: list[str]) -> None:
        records.append({
            "pageId": page_id,
            "interactionIds": interaction_ids,
            "sourceIds": source_ids,
            "routes": routes,
        })
        if page_id is not None:
            shown_pages.add(page_id)
        shown_interactions.update(interaction_ids)
        shown_sources.update(source_ids)

    for capability in capabilities:
        capability_pages = page_scope(capability)
        capability_interactions = _ref_ids(capability, "interactionRefs")
        capability_sources = source_scope(capability)
        if not capability_pages and not capability_interactions and not capability_sources:
            continue
        routes = list(dict.fromkeys(
            sources.get(source_id, {}).get("route", "未指定")
            for source_id in capability_sources
        )) or ["spec-driven-ui"]
        for page_id in capability_pages or [None]:
            page_interactions = [
                interaction_id
                for interaction_id in capability_interactions
                if interaction_page(interaction_id) == page_id
            ]
            add_record(page_id, page_interactions, capability_sources, routes)

    # Definitions that are not referenced by a capability remain visible but
    # do not acquire an inferred PAGE relationship.
    for page_id in pages:
        if page_id not in shown_pages:
            add_record(page_id, [], [], ["未指定"])
    remaining_interactions = [
        interaction_id for interaction_id in interactions if interaction_id not in shown_interactions
    ]
    remaining_sources = [source_id for source_id in sources if source_id not in shown_sources]
    if remaining_interactions or remaining_sources:
        routes = list(dict.fromkeys(
            sources.get(source_id, {}).get("route", "未指定")
            for source_id in remaining_sources
        )) or ["未指定"]
        add_record(None, remaining_interactions, remaining_sources, routes)

    aggregates: dict[str | None, dict[str, Any]] = {}

    def extend_unique(target: list[Any], values: list[Any]) -> None:
        for value in values:
            if value not in target:
                target.append(value)

    for record in records:
        page_id = record["pageId"]
        aggregate = aggregates.setdefault(page_id, {
            "sourceIds": [], "interactionIds": [], "routes": [],
            "firstIndex": len(aggregates),
        })
        extend_unique(aggregate["sourceIds"], record["sourceIds"])
        extend_unique(aggregate["interactionIds"], record["interactionIds"])
        extend_unique(aggregate["routes"], record["routes"])

    page_rank = {page_id: index for index, page_id in enumerate(pages)}

    def sort_key(item: tuple[str | None, dict[str, Any]]) -> tuple[int, int]:
        page_id, aggregate = item
        if page_id in page_rank:
            return page_rank[page_id], 0
        return len(page_rank) + (1 if page_id is None else 0), aggregate["firstIndex"]

    rows: list[str] = []
    for page_id, aggregate in sorted(aggregates.items(), key=sort_key):
        source_ids = aggregate["sourceIds"]
        interaction_ids = aggregate["interactionIds"]
        page = pages.get(page_id) if page_id is not None else None
        cells = [
            _md_cell(page_id if page_id is not None else unbound_page_label),
            _md_cell(page.get("name", "未填写名称")) if page is not None else "-",
            "<br>".join(_md_cell(source_id) for source_id in source_ids) or "-",
            "<br>".join(
                _md_cell(sources[source_id].get("path", "未填写路径"))
                if source_id in sources else "未定义"
                for source_id in source_ids
            ) or "-",
            "<br>".join(_md_cell(interaction_id) for interaction_id in interaction_ids) or "-",
            "<br>".join(
                _md_cell(interactions[interaction_id].get("summary", ""))
                if interaction_id in interactions else "未定义"
                for interaction_id in interaction_ids
            ) or "-",
            "<br>".join(_md_cell(route) for route in aggregate["routes"]),
        ]
        rows.append("| " + " | ".join(cells) + " |")

    lines = [
        "## PAGE / UIX / VIS 映射", "",
        "Plan 生成前以 PAGE 为主维度，按已明确的关系聚合 PAGE、VIS、UIX。",
        (
            "未明确归属的有效 UIX/VIS 统一列在表尾。"
            if specs_finalized
            else "未明确归属的有效 UIX/VIS 统一列在表尾，等待 specs 阶段归属。"
        ), "",
    ]
    if rows:
        lines.extend([
            "| PAGE ID | 页面名称 | VIS ID | 视觉资源路径 | UIX ID | 交互名称 | 还原路径 |",
            "| --- | --- | --- | --- | --- | --- | --- |", *rows, "",
        ])
    else:
        lines.extend(["暂无 UI 映射范围。", ""])
    return lines


def _ui_mapping_lines(data: dict[str, Any], tasks: list[dict[str, Any]]) -> list[str]:
    """Join explicit Plan scope with capability bindings and aggregate by PAGE."""
    pages = {page["pageId"]: page for page in data.get("pages", [])}
    interactions = {item["interactionId"]: item for item in data.get("interactions", [])}
    raw_sources = [source for source in data.get("visualSources", []) if isinstance(source, dict)]
    ignored_source_ids = {
        source.get("sourceId")
        for source in raw_sources
        if source.get("type") == "prototype_link" and isinstance(source.get("sourceId"), str)
    }
    sources = {
        source["sourceId"]: source
        for source in raw_sources
        if source.get("type") != "prototype_link" and isinstance(source.get("sourceId"), str)
    }
    capabilities = [cap for cap in data.get("capabilities", []) if cap.get("uiRequired") is not False]
    records: list[dict[str, Any]] = []
    shown_pages: set[str] = set()
    shown_interactions: set[str] = set()
    shown_sources: set[str] = set()
    # Coverage stays per capability/page so partially planned UI remains visible.
    coverage: dict[tuple[int, str | None], tuple[set[str], set[str]]] = {}

    def interaction_page(interaction_id: str) -> str | None:
        return interactions.get(interaction_id, {}).get("pageId")

    def page_scope(item: dict[str, Any]) -> list[str | None]:
        result: list[str | None] = list(_ref_ids(item, "pageRefs"))
        for interaction_id in _ref_ids(item, "interactionRefs"):
            page_id = interaction_page(interaction_id)
            if page_id not in result:
                result.append(page_id)
        return result

    def source_scope(item: dict[str, Any]) -> list[str]:
        return [
            source_id
            for source_id in _ref_ids(item, "visualSourceRefs")
            if source_id not in ignored_source_ids
        ]

    def add_row(task_id: str, task_name: str, page_id: str | None, interaction_ids: list[str],
                source_ids: list[str], route: str) -> None:
        records.append({
            "taskId": task_id,
            "taskName": task_name,
            "pageId": page_id,
            "interactionIds": interaction_ids,
            "sourceIds": source_ids,
            "route": route or "未指定",
        })
        if page_id is not None:
            shown_pages.add(page_id)
        shown_interactions.update(interaction_ids)
        shown_sources.update(source_ids)

    for task in tasks:
        ui_refs = task.get("uiRefs")
        if task.get("uiRequired") is False or not isinstance(ui_refs, dict) or not task.get("id"):
            continue
        task_id = str(task["id"])
        task_name = str(task.get("title", ""))
        task_interactions = _ref_ids(ui_refs, "interactionRefs")
        task_sources = source_scope(ui_refs)
        if task.get("uiRequired") is not True and not (
            _ref_ids(ui_refs, "pageRefs") or task_interactions or task_sources
        ):
            # Explicit legacy UI references remain traceable even when all
            # IDs are undefined; an empty legacy UI block adds no scope.
            continue
        task_specs = _ref_ids(task, "specRefs")
        bound_sources: set[str] = set()
        for page_id in page_scope(ui_refs):
            page_interactions = [item for item in task_interactions if interaction_page(item) == page_id]
            bound_interactions: set[str] = set()
            has_capability = False
            page_capabilities = [cap for cap in capabilities if page_id in page_scope(cap)]
            for cap_index, cap in enumerate(capabilities):
                if page_id not in page_scope(cap):
                    continue
                cap_specs = _ref_ids(cap, "specRefs")
                common_specs = [ref for ref in task_specs if ref in cap_specs]
                cap_interactions = _ref_ids(cap, "interactionRefs")
                selected_interactions = [item for item in page_interactions if item in cap_interactions]
                selected_sources = [item for item in task_sources if item in source_scope(cap)]
                if task_specs and cap_specs:
                    if not common_specs:
                        continue
                elif page_interactions and cap_interactions and not selected_interactions:
                    continue
                elif not selected_interactions and not selected_sources and len(page_capabilities) > 1:
                    # PAGE alone cannot establish which of several capabilities
                    # a legacy Task owns; UIX or VIS must disambiguate it.
                    continue
                # Unknown interaction ownership must never join by None == None.
                if page_id is None:
                    continue
                add_row(task_id, task_name, page_id, selected_interactions, selected_sources,
                        ui_refs.get("frontendRoute", ""))
                covered_interactions, covered_sources = coverage.setdefault((cap_index, page_id), (set(), set()))
                covered_interactions.update(selected_interactions)
                covered_sources.update(selected_sources)
                bound_interactions.update(selected_interactions)
                bound_sources.update(selected_sources)
                has_capability = True
            remaining_interactions = [item for item in page_interactions if item not in bound_interactions]
            if remaining_interactions or not has_capability:
                add_row(task_id, task_name, page_id, remaining_interactions, [], ui_refs.get("frontendRoute", ""))
        remaining_sources = [item for item in task_sources if item not in bound_sources]
        if remaining_sources or not page_scope(ui_refs):
            add_row(task_id, task_name, None, [], remaining_sources, ui_refs.get("frontendRoute", ""))

    pending_task = "未关联Task"
    for cap_index, cap in enumerate(capabilities):
        cap_pages = page_scope(cap)
        cap_interaction_ids = _ref_ids(cap, "interactionRefs")
        cap_sources = source_scope(cap)
        if not cap_pages and not cap_interaction_ids and not cap_sources:
            continue
        for page_id in cap_pages or [None]:
            cap_interactions = [item for item in cap_interaction_ids if interaction_page(item) == page_id]
            key = (cap_index, page_id)
            covered_interactions, covered_sources = coverage.get(key, (set(), set()))
            remaining_interactions = [item for item in cap_interactions if item not in covered_interactions]
            remaining_sources = [item for item in cap_sources if item not in covered_sources]
            if key in coverage and not remaining_interactions and not remaining_sources:
                continue
            # Unplanned interactions still use their capability's visual input,
            # including sources already assigned to another Task.
            scope_sources = cap_sources if remaining_interactions else remaining_sources
            routes = list(dict.fromkeys(sources.get(item, {}).get("route", "未指定") for item in scope_sources))
            add_row(pending_task, "", page_id, remaining_interactions, scope_sources,
                    ", ".join(routes) if routes else "spec-driven-ui")

    # Keep scope outside capabilities visible instead of silently dropping it.
    for page_id in dict.fromkeys([*pages, *(interaction_page(item) for item in interactions)]):
        remaining_interactions = [item for item in interactions if interaction_page(item) == page_id and item not in shown_interactions]
        if remaining_interactions or (page_id is not None and page_id not in shown_pages):
            add_row(pending_task, "", page_id, remaining_interactions, [], "未指定")
    for source_id, source in sources.items():
        if source_id not in shown_sources:
            add_row(pending_task, "", None, [], [source_id], source.get("route", ""))

    aggregates: dict[str | None, dict[str, Any]] = {}

    def extend_unique(target: list[Any], values: list[Any]) -> None:
        for value in values:
            if value not in target:
                target.append(value)

    for record in records:
        page_id = record["pageId"]
        aggregate = aggregates.setdefault(page_id, {
            "sourceIds": [], "interactionIds": [], "tasks": [],
            "routes": [], "firstIndex": len(aggregates),
        })
        extend_unique(aggregate["sourceIds"], record["sourceIds"])
        extend_unique(aggregate["interactionIds"], record["interactionIds"])
        task = (record["taskId"], record["taskName"])
        if task not in aggregate["tasks"]:
            aggregate["tasks"].append(task)
        extend_unique(aggregate["routes"], [record["route"]])

    page_rank = {page_id: index for index, page_id in enumerate(pages)}

    def aggregate_sort_key(item: tuple[str | None, dict[str, Any]]) -> tuple[int, int]:
        page_id, aggregate = item
        if page_id in page_rank:
            return page_rank[page_id], 0
        return len(page_rank) + (1 if page_id is None else 0), aggregate["firstIndex"]

    def page_description(page_id: str | None) -> str:
        if page_id is None:
            return "-"
        page = pages.get(page_id)
        if page is None:
            return "未定义"
        return _md_cell(page.get("name", "未填写名称"))

    def source_path(source_id: str) -> str:
        source = sources.get(source_id)
        if source is None:
            return "未定义"
        return _md_cell(source.get("path", "未填写路径"))

    rows: list[str] = []
    for page_id, aggregate in sorted(aggregates.items(), key=aggregate_sort_key):
        source_ids = aggregate["sourceIds"]
        interaction_ids = aggregate["interactionIds"]
        task_values = aggregate["tasks"]
        cells = [
            _md_cell(page_id if page_id is not None else "未明确页面归属"),
            page_description(page_id),
            "<br>".join(_md_cell(task_id) for task_id, _ in task_values) or "-",
            "<br>".join(_md_cell(task_name) if task_name else "-" for _, task_name in task_values) or "-",
            "<br>".join(_md_cell(source_id) for source_id in source_ids) or "-",
            "<br>".join(source_path(source_id) for source_id in source_ids) or "-",
            "<br>".join(_md_cell(interaction_id) for interaction_id in interaction_ids) or "-",
            "<br>".join(
                _md_cell(interactions[interaction_id].get("summary", ""))
                if interaction_id in interactions else "未定义"
                for interaction_id in interaction_ids
            ) or "-",
            "<br>".join(_md_cell(route) for route in aggregate["routes"]),
        ]
        rows.append("| " + " | ".join(cells) + " |")

    lines = [
        "## TASK / PAGE / UIX / VIS 映射", "",
        "以 PAGE 为主维度，按 UI_CONTEXT.json 中的页面声明顺序展示；同一 PAGE 的 TASK、VIS、UIX 分列聚合。",
        "视觉资源按已明确的关系与任务关联；无法确定 PAGE 的范围统一列在表尾。", "",
    ]
    if rows:
        lines.extend([
            "| PAGE ID | 页面名称 | TASK ID | 任务名称 | VIS ID | 视觉资源路径 | UIX ID | 交互名称 | 还原路径 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |", *rows, "",
        ])
    else:
        lines.extend(["暂无 UI 映射范围。", ""])
    return lines


def _generate_ui_context_md(
    data: dict[str, Any],
    feature: str,
    *,
    tasks: list[dict[str, Any]] | None = None,
) -> str:
    """生成 UI_CONTEXT.json 的人类可读视图。

    对纯后端 Feature，先明确说明 UI 不适用及其依据，避免把空的
    前端表格误读为 JSON → MD 同步失败。
    """
    projection_intro = (
        "本文档基于 UI_CONTEXT.json 生成；正式 Plan 尚未生成，当前仅展示 PAGE、VIS、UIX 范围。"
        if tasks is None
        else "本文档基于 UI_CONTEXT.json 与正式 Plan 生成；JSON 是 UI 范围的机器事实源，Plan 提供任务绑定。"
    )
    lines = [
        f"# UI Context - {feature}",
        "",
        projection_intro,
        "",
    ]

    ui_required = data.get("uiRequired")
    decision_status = data.get("decisionStatus", "未知")
    decision_source = data.get("decisionSource", "未知")
    ui_required_display = "是" if ui_required is True else "否" if ui_required is False else "未确定"
    lines.extend([
        "## UI 范围决策",
        "",
        f"- 是否需要 UI 实现：{ui_required_display}",
        f"- 决策状态：{decision_status}",
        f"- 决策来源：{decision_source}",
    ])
    if ui_required is False:
        reason = data.get("notApplicableReason")
        reason_display = reason.strip() if isinstance(reason, str) and reason.strip() else "未填写"
        lines.extend([
            f"- 不适用原因：{reason_display}",
            "",
            "本 Feature 不包含前端交付；无需定义页面、交互或视觉资源。",
            "",
        ])
        return "\n".join(lines)
    lines.append("")

    if tasks is None:
        lines.extend(_pre_plan_mapping_lines(data))
    else:
        lines.extend(_ui_mapping_lines(data, tasks))
    lines.extend([
        "### 还原路径说明",
        "",
        "- **absolute-html**: 使用高保真HTML进行像素级还原",
        "- **standard-html**: 使用标准HTML作为参考",
        "- **spec-driven-ui**: 基于需求规格说明驱动UI开发",
        "- **missing-html**: 缺少HTML资源",
        "",
    ])

    return "\n".join(lines)


def _write_md(
    workspace: Path,
    feature: str,
    data: dict[str, Any],
    *,
    tasks: list[dict[str, Any]] | None = None,
    force_pre_plan: bool = False,
) -> None:
    """写入UI_CONTEXT.md文件"""
    md_path = _path(workspace, feature).parent / "UI_CONTEXT.md"
    if force_pre_plan:
        plan_tasks = None
    elif tasks is None:
        plan_path = _path(workspace, feature).parent / "plan.json"
        plan_tasks = _load_plan_tasks(workspace, feature) if plan_path.is_file() else None
    else:
        plan_tasks = tasks
    md_content = _generate_ui_context_md(data, feature, tasks=plan_tasks)
    md_path.write_text(md_content, encoding="utf-8")


def refresh_ui_context_md(
    workspace: Path,
    feature: str,
    *,
    tasks: list[dict[str, Any]] | None = None,
    force_pre_plan: bool = False,
) -> bool:
    """Refresh UI_CONTEXT.md when UI context exists; return whether it ran."""
    context_path = _path(workspace, feature)
    if not context_path.is_file():
        return False
    data = load_json(context_path)
    if not isinstance(data, dict):
        return False
    _write_md(workspace, feature, data, tasks=tasks, force_pre_plan=force_pre_plan)
    return True


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

    # 同时生成UI_CONTEXT.md
    try:
        _write_md(workspace, feature, data)
    except Exception as exc:
        # MD生成失败不影响JSON写入结果，但记录警告
        print(f"Warning: Failed to generate UI_CONTEXT.md: {exc}", file=sys.stderr)

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


def _archive_visual_source(
    workspace: Path,
    feature: str,
    source_id: str,
    source_file_value: str,
    source_root_value: str | None,
) -> tuple[str, str, str, Path]:
    source_file = Path(source_file_value).expanduser().resolve()
    if not source_file.is_file():
        raise ValueError(f"visual_source_file_missing:{source_file}")
    source_root = Path(source_root_value).expanduser().resolve() if source_root_value else None
    if source_root is not None:
        if not source_root.is_dir():
            raise ValueError(f"visual_source_root_missing:{source_root}")
        try:
            entry_relative = source_file.relative_to(source_root)
        except ValueError as exc:
            raise ValueError(f"visual_source_file_outside_root:{source_file}") from exc
    else:
        entry_relative = Path(source_file.name)

    archive_parent = _path(workspace, feature).parent / "frontend-html"
    if source_root is not None:
        try:
            archive_parent.resolve().relative_to(source_root)
        except ValueError:
            pass
        else:
            raise ValueError(f"visual_source_root_contains_archive:{source_root}")
    archive_dir = archive_parent / source_id
    if archive_dir.exists():
        raise ValueError(f"archived_visual_source_immutable:{source_id}")
    archive_parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_parent / f".{source_id}-{uuid.uuid4().hex[:8]}.tmp"
    try:
        if source_root is not None:
            shutil.copytree(source_root, temporary)
        else:
            temporary.mkdir()
            shutil.copy2(source_file, temporary / source_file.name)
        archived_entry = temporary / entry_relative
        if not archived_entry.is_file():
            raise ValueError(f"archived_visual_source_entry_missing:{entry_relative.as_posix()}")
        content_sha256 = visual_source_content_sha256(archived_entry)
        bundle_sha256 = visual_source_bundle_sha256(temporary)
        temporary.replace(archive_dir)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    relative_path = (Path("frontend-html") / source_id / entry_relative).as_posix()
    return relative_path, content_sha256, bundle_sha256, archive_dir


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
    if args.type == "prototype_link":
        return render_result(fail(
            "visual_source_type_unsupported",
            "prototype_link 不是可还原视觉资源，请勿写入 visualSources",
        ))
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
    existing = _find(sources, "sourceId", source_id)
    if args.source_file and existing is not None and existing.get("contentSha256"):
        return render_result(fail("archived_visual_source_immutable", source_id))
    if args.source_file and args.path:
        return render_result(fail("visual_source_path_conflict", "--path 与 --source-file 只能使用一个"))
    if args.source_file and args.type not in {"high_fidelity_html", "standard_html"}:
        return render_result(fail("visual_source_archive_type_invalid", args.type))
    if args.source_root and not args.source_file:
        return render_result(fail("visual_source_root_without_file", "--source-root 需要 --source-file"))
    if not args.source_file and not args.path:
        return render_result(fail("visual_source_path_missing", "--path 或 --source-file 必填"))
    if existing is not None and existing.get("contentSha256") and not args.source_file:
        return render_result(fail("archived_visual_source_immutable", source_id))

    archive_dir: Path | None = None
    if args.source_file:
        try:
            path, content_sha256, bundle_sha256, archive_dir = _archive_visual_source(
                workspace,
                feature,
                source_id,
                args.source_file,
                args.source_root,
            )
        except ValueError as exc:
            return render_result(fail("visual_source_archive_failed", str(exc)))
        item = {
            "sourceId": source_id,
            "type": args.type,
            "path": path,
            "contentSha256": content_sha256,
            "bundleSha256": bundle_sha256,
        }
    else:
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
    result = _write(workspace, feature, data)
    if not result.ok and archive_dir is not None and archive_dir.exists():
        shutil.rmtree(archive_dir)
    return render_result(result)


def _cmd_add_capability(args: argparse.Namespace) -> int:
    workspace, feature = _resolve(args)
    data = _load(workspace, feature)
    capabilities = data.setdefault("capabilities", [])
    item = {
        "capabilityId": args.capability_id,
        "pageRefs": _string_array(args.page_ref),
        "interactionRefs": _string_array(args.interaction_ref),
        "visualSourceRefs": _string_array(args.visual_source_ref),
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


def _parse_md_section(lines: list[str], start_marker: str) -> tuple[int, list[str]]:
    """提取Markdown中某个section的内容"""
    start = -1
    for i, line in enumerate(lines):
        if line.strip().startswith(start_marker):
            start = i
            break
    if start == -1:
        return -1, []

    # 找到下一个同级或更高级标题
    level = len(start_marker.split()[0])  # ## 是2级
    content = []
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip().startswith("#"):
            # 检查标题级别
            header_level = len(line.split()[0])
            if header_level <= level:
                break
        content.append(line)

    return start, content


def _parse_pages_from_md(lines: list[str]) -> list[dict[str, Any]]:
    """从MD解析页面列表"""
    _, section = _parse_md_section(lines, "## 页面列表")
    if not section:
        return []

    pages = []
    current_page = None

    for line in section:
        line = line.strip()
        if line.startswith("### "):
            # 新页面: ### PAGE-001: 订单列表页
            if current_page:
                pages.append(current_page)
            parts = line[4:].split(":", 1)
            if len(parts) == 2:
                page_id = parts[0].strip()
                name = parts[1].strip()
                current_page = {"pageId": page_id, "name": name, "goal": ""}
        elif line.startswith("**目标**:") and current_page:
            current_page["goal"] = line.split(":", 1)[1].strip()
        elif line.startswith("**路由提示**:") and current_page:
            route_hint = line.split(":", 1)[1].strip().strip("`")
            if route_hint:
                current_page["routeHint"] = route_hint
        elif line.startswith("**状态**:") and current_page:
            states_str = line.split(":", 1)[1].strip()
            states = [s.strip() for s in states_str.split(",")]
            if states:
                current_page["states"] = states

    if current_page:
        pages.append(current_page)

    return pages


def _parse_interactions_from_md(lines: list[str]) -> list[dict[str, Any]]:
    """从MD解析交互列表"""
    _, section = _parse_md_section(lines, "## 交互列表")
    if not section:
        return []

    interactions = []
    current_interaction = None

    for line in section:
        line = line.strip()
        if line.startswith("### "):
            # 新交互: ### UIX-001: 点击订单行跳转到详情页
            if current_interaction:
                interactions.append(current_interaction)
            parts = line[4:].split(":", 1)
            if len(parts) == 2:
                interaction_id = parts[0].strip()
                summary = parts[1].strip()
                current_interaction = {"interactionId": interaction_id, "summary": summary, "pageId": ""}
        elif line.startswith("**所属页面**:") and current_interaction:
            current_interaction["pageId"] = line.split(":", 1)[1].strip()
        elif line.startswith("**关联状态**:") and current_interaction:
            states_str = line.split(":", 1)[1].strip()
            states = [s.strip() for s in states_str.split(",")]
            if states:
                current_interaction["stateRefs"] = states

    if current_interaction:
        interactions.append(current_interaction)

    return interactions


def _parse_capabilities_from_md(lines: list[str]) -> list[dict[str, Any]]:
    """从MD解析能力列表"""
    _, section = _parse_md_section(lines, "## 能力与资源映射关系")
    if not section:
        return []

    capabilities = []
    current_capability = None

    for line in section:
        line = line.strip()
        if line.startswith("### ") and not line.startswith("### 还原路径说明"):
            # 新能力: ### order-list-ui
            if current_capability:
                capabilities.append(current_capability)
            capability_id = line[4:].strip()
            current_capability = {
                "capabilityId": capability_id,
                "pageRefs": [],
                "interactionRefs": [],
                "visualSourceRefs": [],
                "specRefs": []
            }
        elif line.startswith("**UI需求**:") and current_capability:
            ui_req_str = line.split(":", 1)[1].strip()
            current_capability["uiRequired"] = ui_req_str == "是"
        elif line.startswith("**关联页面**:") and current_capability:
            refs = line.split(":", 1)[1].strip()
            if refs and refs != "无":
                current_capability["pageRefs"] = [r.strip() for r in refs.split(",")]
        elif line.startswith("**关联交互**:") and current_capability:
            refs = line.split(":", 1)[1].strip()
            if refs and refs != "无":
                current_capability["interactionRefs"] = [r.strip() for r in refs.split(",")]
        elif line.startswith("**视觉资源**:") and current_capability:
            refs = line.split(":", 1)[1].strip()
            if refs and not refs.startswith("无"):
                current_capability["visualSourceRefs"] = [r.strip() for r in refs.split(",")]
        elif line.startswith("**规格引用**:") and current_capability:
            refs = line.split(":", 1)[1].strip()
            if refs and refs != "无":
                current_capability["specRefs"] = [r.strip() for r in refs.split(",")]

    if current_capability:
        capabilities.append(current_capability)

    return capabilities


def _cmd_sync_from_md(args: argparse.Namespace) -> int:
    """从UI_CONTEXT.md同步内容到UI_CONTEXT.json"""
    workspace, feature = _resolve(args)

    # 读取MD文件
    md_path = _path(workspace, feature).parent / "UI_CONTEXT.md"
    if not md_path.exists():
        return render_result(fail("md_file_not_found", f"UI_CONTEXT.md 不存在: {md_path}"))

    try:
        md_content = md_path.read_text(encoding="utf-8")
        md_lines = md_content.split("\n")
    except Exception as exc:
        return render_result(fail("md_read_failed", str(exc)))

    # 读取现有JSON数据（保留MD中没有的字段）
    data = _load(workspace, feature)

    # 解析MD中的基本信息
    ui_required = None
    for line in md_lines:
        if "**UI需求**:" in line:
            ui_required = "是" in line
            break

    if ui_required is not None:
        data["uiRequired"] = ui_required

    # 解析并更新pages、interactions、capabilities
    try:
        pages = _parse_pages_from_md(md_lines)
        if pages:
            data["pages"] = pages

        interactions = _parse_interactions_from_md(md_lines)
        if interactions:
            data["interactions"] = interactions

        capabilities = _parse_capabilities_from_md(md_lines)
        if capabilities:
            data["capabilities"] = capabilities
    except Exception as exc:
        return render_result(fail("md_parse_failed", f"解析MD失败: {exc}"))

    # 写入JSON（会触发MD重新生成）
    return render_result(_write(workspace, feature, data))


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
    visual.add_argument("--path")
    visual.add_argument("--source-file")
    visual.add_argument("--source-root")
    visual.add_argument("--route", choices=sorted(FRONTEND_ROUTES - {"none"}))
    visual.add_argument("--required", choices=["true", "false"])
    visual.set_defaults(func=_cmd_add_visual_source)
    update_visual = sub.add_parser("update-visual-source")
    _add_common(update_visual)
    update_visual.add_argument("--source-id", required=True)
    update_visual.add_argument("--type", required=True)
    update_visual.add_argument("--path")
    update_visual.add_argument("--source-file")
    update_visual.add_argument("--source-root")
    update_visual.add_argument("--route", choices=sorted(FRONTEND_ROUTES - {"none"}))
    update_visual.add_argument("--required", choices=["true", "false"])
    update_visual.set_defaults(func=_cmd_add_visual_source)

    capability = sub.add_parser("add-capability")
    _add_common(capability)
    capability.add_argument("--capability-id", required=True)
    capability.add_argument("--page-ref", action="append")
    capability.add_argument("--interaction-ref", action="append")
    capability.add_argument("--visual-source-ref", action="append")
    capability.add_argument("--spec-ref", action="append")
    capability.add_argument("--ui-required", choices=["true", "false"])
    capability.set_defaults(func=_cmd_add_capability)
    update_capability = sub.add_parser("update-capability")
    _add_common(update_capability)
    update_capability.add_argument("--capability-id", required=True)
    update_capability.add_argument("--page-ref", action="append")
    update_capability.add_argument("--interaction-ref", action="append")
    update_capability.add_argument("--visual-source-ref", action="append")
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

    sync = sub.add_parser("sync-from-md")
    _add_common(sync)
    sync.set_defaults(func=_cmd_sync_from_md)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        return render_result(fail("ui_context_writer_failed", str(exc)))


if __name__ == "__main__":
    raise SystemExit(main())
