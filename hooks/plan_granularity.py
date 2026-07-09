#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared plan task granularity checks."""

from __future__ import annotations

import re
from typing import Any


SCN_ID = re.compile(r"\bSCN-\d{3}\b")

PLAN_TASK_MAX_SCENARIOS = 5
PLAN_TASK_MAX_APIS = 2
PLAN_TASK_MAX_UI_PAGES = 1
PLAN_TASK_MAX_UI_INTERACTIONS = 3
PLAN_TASK_HARD_MAX_SCENARIOS = 8
PLAN_TASK_HARD_MAX_APIS = 3
PLAN_TASK_HARD_MAX_UI_PAGES = 2
PLAN_TASK_HARD_MAX_UI_INTERACTIONS = 4
PLAN_TASK_SPLIT_RATIONALE_MIN_LENGTH = 30
PLAN_TASK_SPLIT_RATIONALE_BANNED = (
    "同一模块",
    "同一个模块",
    "同一capability",
    "同一 capability",
    "同一个capability",
    "同一个 capability",
    "实现方便",
    "一起实现",
    "顺手一起",
)
PLAN_TASK_SPLIT_RATIONALE_PAGE_ONLY_BANNED = (
    "同一页面",
    "同一个页面",
    "同一列表",
    "同一个列表",
    "同一表单",
    "同一个表单",
    "不同组成部分",
    "不同交互元素",
)
PLAN_TASK_SPLIT_RATIONALE_VALIDATION_TERMS = (
    "无法独立验证",
    "不能独立验证",
    "不可独立验证",
    "同一验证闭环",
    "同一个验证闭环",
    "共享同一验证闭环",
    "同一次提交动作",
    "同一个响应断言",
    "同一响应断言",
    "same validation loop",
    "shared validation loop",
    "single validation loop",
    "cannot be validated independently",
    "not independently verifiable",
    "same request",
    "same response assertion",
)
PLAN_TASK_SPLIT_RATIONALE_MIN_IDS_BY_PREFIX = {
    "SCN": 3,
    "API": 2,
    "PAGE": 2,
    "UIX": 3,
}


def scenario_refs_from_spec_refs(spec_refs: list[str]) -> set[str]:
    refs: set[str] = set()
    for raw_ref in spec_refs:
        if not isinstance(raw_ref, str):
            continue
        stripped = raw_ref.strip()
        if not stripped:
            continue
        if "#" in stripped:
            path_part, _, anchor = stripped.partition("#")
        else:
            path_part, anchor = "", stripped
        scenario_ids = SCN_ID.findall(anchor)
        if not scenario_ids:
            continue
        normalized_path = path_part.strip().replace("\\", "/")
        for scn_id in scenario_ids:
            refs.add(f"{normalized_path}#{scn_id}" if normalized_path else scn_id)
    return refs


def _string_list_value(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _task_ui_refs(task: dict[str, Any], field: str) -> list[str]:
    ui_refs = task.get("uiRefs")
    if not isinstance(ui_refs, dict):
        return []
    return _string_list_value(ui_refs.get(field))


def _mentioned_related_scenario_refs(rationale: str, related_ids: set[str]) -> set[str]:
    mentioned: set[str] = set()
    normalized_rationale = rationale.replace("\\", "/")
    by_scn_id: dict[str, set[str]] = {}
    for related_id in related_ids:
        scenario_ids = SCN_ID.findall(related_id)
        if not scenario_ids:
            continue
        by_scn_id.setdefault(scenario_ids[-1], set()).add(related_id)
        if "#" in related_id and related_id in normalized_rationale:
            mentioned.add(related_id)

    mentioned_scn_ids = {match.group(0) for match in SCN_ID.finditer(rationale)}
    for scn_id in mentioned_scn_ids:
        candidates = by_scn_id.get(scn_id, set())
        if len(candidates) == 1:
            mentioned.update(candidates)
        elif not candidates and scn_id in related_ids:
            mentioned.add(scn_id)
    return mentioned


def _mentioned_related_ids(prefix: str, rationale: str, related_ids: set[str]) -> set[str]:
    if prefix == "SCN":
        return _mentioned_related_scenario_refs(rationale, related_ids)
    mentioned = {match.group(0) for match in re.finditer(rf"\b{prefix}-\d{{3}}\b", rationale)}
    return mentioned & related_ids


def _split_rationale_is_invalid(rationale: str, related_ids_by_prefix: dict[str, set[str]]) -> bool:
    stripped = rationale.strip()
    if len(stripped) < PLAN_TASK_SPLIT_RATIONALE_MIN_LENGTH:
        return True
    lowered = stripped.lower()
    if any(pattern.lower() in lowered for pattern in PLAN_TASK_SPLIT_RATIONALE_BANNED):
        return True
    if not any(term.lower() in lowered for term in PLAN_TASK_SPLIT_RATIONALE_VALIDATION_TERMS):
        return True
    if any(pattern.lower() in lowered for pattern in PLAN_TASK_SPLIT_RATIONALE_PAGE_ONLY_BANNED) and not any(
        term.lower() in lowered for term in PLAN_TASK_SPLIT_RATIONALE_VALIDATION_TERMS
    ):
        return True
    for prefix, related_ids in related_ids_by_prefix.items():
        if not related_ids:
            continue
        required_count = min(PLAN_TASK_SPLIT_RATIONALE_MIN_IDS_BY_PREFIX[prefix], len(related_ids))
        mentioned_related_ids = _mentioned_related_ids(prefix, stripped, related_ids)
        if len(mentioned_related_ids) < required_count:
            return True
    return False


def validate_plan_task_granularity_item(task: dict[str, Any], *, task_id: str) -> list[dict[str, str]]:
    spec_refs = _string_list_value(task.get("specRefs"))
    scenario_refs = scenario_refs_from_spec_refs(spec_refs)
    api_ids = set(_string_list_value(task.get("apiIds")))
    page_refs = set(_task_ui_refs(task, "pageRefs"))
    interaction_refs = set(_task_ui_refs(task, "interactionRefs"))

    hard_reasons: list[str] = []
    if len(scenario_refs) > PLAN_TASK_HARD_MAX_SCENARIOS:
        hard_reasons.append(f"scenarios={len(scenario_refs)}>{PLAN_TASK_HARD_MAX_SCENARIOS}")
    if len(api_ids) > PLAN_TASK_HARD_MAX_APIS:
        hard_reasons.append(f"apis={len(api_ids)}>{PLAN_TASK_HARD_MAX_APIS}")
    if len(page_refs) > PLAN_TASK_HARD_MAX_UI_PAGES:
        hard_reasons.append(f"pages={len(page_refs)}>{PLAN_TASK_HARD_MAX_UI_PAGES}")
    if len(interaction_refs) > PLAN_TASK_HARD_MAX_UI_INTERACTIONS:
        hard_reasons.append(f"interactions={len(interaction_refs)}>{PLAN_TASK_HARD_MAX_UI_INTERACTIONS}")
    if hard_reasons:
        return [
            {
                "reason": "oversized_plan_task_must_split",
                "detail": f"task={task_id} detail={','.join(hard_reasons)}",
            }
        ]

    threshold_reasons: list[str] = []
    related_ids_by_prefix: dict[str, set[str]] = {}
    if len(scenario_refs) > PLAN_TASK_MAX_SCENARIOS:
        threshold_reasons.append(f"scenarios={len(scenario_refs)}")
        related_ids_by_prefix["SCN"] = set(scenario_refs)
    if len(api_ids) > PLAN_TASK_MAX_APIS:
        threshold_reasons.append(f"apis={len(api_ids)}")
        related_ids_by_prefix["API"] = set(api_ids)
    if len(page_refs) > PLAN_TASK_MAX_UI_PAGES:
        threshold_reasons.append(f"pages={len(page_refs)}")
        related_ids_by_prefix["PAGE"] = set(page_refs)
    if len(interaction_refs) > PLAN_TASK_MAX_UI_INTERACTIONS:
        threshold_reasons.append(f"interactions={len(interaction_refs)}")
        related_ids_by_prefix["UIX"] = set(interaction_refs)

    if not threshold_reasons:
        return []
    rationale = task.get("splitRationale")
    if not isinstance(rationale, str) or not rationale.strip():
        return [
            {
                "reason": "missing_plan_task_split_rationale",
                "detail": f"task={task_id} detail={','.join(threshold_reasons)}",
            }
        ]
    if _split_rationale_is_invalid(rationale, related_ids_by_prefix):
        return [
            {
                "reason": "invalid_plan_task_split_rationale",
                "detail": f"task={task_id} detail={','.join(threshold_reasons)}",
            }
        ]
    return []
