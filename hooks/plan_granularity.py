#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared plan task granularity checks."""

from __future__ import annotations

import re
from typing import Any

from hooks.plan_json import task_execution_mode
from hooks.validation_policy import (
    BEHAVIOR_TASK_VALIDATION_KINDS,
    FRONTEND_COMPILE_VALIDATION_KINDS,
)


SCN_ID = re.compile(r"\bSCN-\d{3}\b")
SCN_SUBSTRING = re.compile(r"SCN-\d{3}")

PLAN_TASK_MAX_SCENARIOS = 5
PLAN_TASK_MAX_APIS = 2
PLAN_TASK_MAX_UI_PAGES = 1
PLAN_TASK_MAX_UI_INTERACTIONS = 3
PLAN_TASK_MATRIX_MAX_SCENARIOS = 12
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
MATRIX_VALIDATION_KINDS = BEHAVIOR_TASK_VALIDATION_KINDS - {"static_check"}


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
        scenario_ids = SCN_SUBSTRING.findall(anchor)
        if not scenario_ids:
            continue
        normalized_path = path_part.strip().replace("\\", "/")
        for scn_id in scenario_ids:
            refs.add(f"{normalized_path}#{scn_id}" if normalized_path else scn_id)
    return refs


def _scenario_reference_error(spec_refs: list[str]) -> bool:
    for raw_ref in spec_refs:
        if not isinstance(raw_ref, str):
            continue
        stripped = raw_ref.strip()
        if "#" not in stripped:
            continue
        path_part, _, anchor = stripped.partition("#")
        scenario_ids = SCN_SUBSTRING.findall(anchor)
        if not scenario_ids:
            continue
        if not path_part.strip() or len(scenario_ids) != 1 or anchor.strip() != scenario_ids[0]:
            return True
    return False


def _normalized_merged_scenario_refs(task: dict[str, Any]) -> set[str] | None:
    raw_refs = task.get("mergedScenarioRefs")
    if not isinstance(raw_refs, list):
        return None
    normalized: set[str] = set()
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, str):
            return None
        stripped = raw_ref.strip()
        if "#" not in stripped:
            return None
        path_part, _, anchor = stripped.partition("#")
        scenario_ids = SCN_SUBSTRING.findall(anchor)
        if not path_part.strip() or len(scenario_ids) != 1 or anchor.strip() != scenario_ids[0]:
            return None
        normalized_path = path_part.strip().replace("\\", "/")
        normalized.add(f"{normalized_path}#{scenario_ids[0]}")
    return normalized


def _matrix_validation_violations(task: dict[str, Any]) -> list[dict[str, Any]]:
    acceptance_ids = {
        item.get("id")
        for item in task.get("acceptanceCriteria", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    commands = task.get("validationCommands")
    violations: list[dict[str, Any]] = []
    if not acceptance_ids:
        violations.append({
            "code": "matrix_acceptance_criteria_missing",
            "field": "acceptanceCriteria",
            "expected": "at_least_one_acceptance_criterion_with_id",
        })
    if not isinstance(commands, list):
        violations.append({
            "code": "matrix_validation_commands_missing",
            "field": "validationCommands",
            "expected": "exactly_one_required_matrix_validation_command",
        })
        return violations
    allowed_kinds = set(MATRIX_VALIDATION_KINDS)
    if task.get("uiRequired") is True:
        allowed_kinds.update(FRONTEND_COMPILE_VALIDATION_KINDS)
    behavior_commands = [
        command
        for command in commands
        if isinstance(command, dict)
        and command.get("required") is True
        and command.get("kind") in allowed_kinds
    ]
    if len(behavior_commands) != 1:
        violations.append({
            "code": "matrix_validation_command_count_invalid",
            "field": "validationCommands",
            "expectedCount": 1,
            "actualCount": len(behavior_commands),
            "allowedKinds": sorted(allowed_kinds),
            "matchingCommandIds": [
                command.get("id")
                for command in behavior_commands
                if isinstance(command.get("id"), str)
            ],
        })
        return violations
    covers = {
        item for item in behavior_commands[0].get("covers", []) if isinstance(item, str)
    }
    missing_covers = sorted(acceptance_ids - covers)
    extra_covers = sorted(covers - acceptance_ids)
    if missing_covers or extra_covers:
        violations.append({
            "code": "matrix_validation_covers_mismatch",
            "field": "validationCommands",
            "commandId": behavior_commands[0].get("id"),
            "expectedAcceptanceIds": sorted(acceptance_ids),
            "actualCovers": sorted(covers),
            "missingCovers": missing_covers,
            "extraCovers": extra_covers,
        })
    return violations


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


def _split_rationale_violations(
    rationale: str,
    related_ids_by_prefix: dict[str, set[str]],
) -> list[dict[str, Any]]:
    stripped = rationale.strip()
    violations: list[dict[str, Any]] = []
    if len(stripped) < PLAN_TASK_SPLIT_RATIONALE_MIN_LENGTH:
        violations.append({
            "code": "split_rationale_too_short",
            "field": "splitRationale",
            "minimumLength": PLAN_TASK_SPLIT_RATIONALE_MIN_LENGTH,
            "actualLength": len(stripped),
        })
    lowered = stripped.lower()
    banned_terms = [
        pattern for pattern in PLAN_TASK_SPLIT_RATIONALE_BANNED if pattern.lower() in lowered
    ]
    if banned_terms:
        violations.append({
            "code": "split_rationale_contains_banned_term",
            "field": "splitRationale",
            "matchedTerms": banned_terms,
        })
    has_validation_term = any(
        term.lower() in lowered for term in PLAN_TASK_SPLIT_RATIONALE_VALIDATION_TERMS
    )
    if not has_validation_term:
        violations.append({
            "code": "split_rationale_missing_validation_boundary",
            "field": "splitRationale",
            "expectedOneOf": list(PLAN_TASK_SPLIT_RATIONALE_VALIDATION_TERMS),
        })
    page_only_terms = [
        pattern
        for pattern in PLAN_TASK_SPLIT_RATIONALE_PAGE_ONLY_BANNED
        if pattern.lower() in lowered
    ]
    if page_only_terms and not has_validation_term:
        violations.append({
            "code": "split_rationale_page_only",
            "field": "splitRationale",
            "matchedTerms": page_only_terms,
            "expected": "public_seam_and_validation_boundary",
        })
    for prefix, related_ids in related_ids_by_prefix.items():
        if not related_ids:
            continue
        required_count = min(PLAN_TASK_SPLIT_RATIONALE_MIN_IDS_BY_PREFIX[prefix], len(related_ids))
        mentioned_related_ids = _mentioned_related_ids(prefix, stripped, related_ids)
        if len(mentioned_related_ids) < required_count:
            violations.append({
                "code": "split_rationale_missing_related_ids",
                "field": "splitRationale",
                "idPrefix": prefix,
                "requiredCount": required_count,
                "actualCount": len(mentioned_related_ids),
                "mentionedIds": sorted(mentioned_related_ids),
                "eligibleIds": sorted(related_ids),
            })
    return violations


def validate_plan_task_grouping_item(task: dict[str, Any], *, task_id: str) -> list[dict[str, Any]]:
    """Validate split/grouping decisions without requiring the full task contract."""

    spec_refs = _string_list_value(task.get("specRefs"))
    if _scenario_reference_error(spec_refs):
        return [
            {
                "reason": "invalid_plan_task_scenario_reference",
                "detail": f"task={task_id} scenario refs must be individually expanded and fully qualified",
                "taskId": task_id,
                "field": "specRefs",
                "repairTarget": "task_group",
                "violations": [{
                    "code": "scenario_reference_not_fully_qualified",
                    "expected": "one_fully_qualified_scenario_ref_per_item",
                }],
            }
        ]
    scenario_refs = scenario_refs_from_spec_refs(spec_refs)
    api_ids = set(_string_list_value(task.get("apiIds")))
    page_refs = set(_task_ui_refs(task, "pageRefs"))
    interaction_refs = set(_task_ui_refs(task, "interactionRefs"))

    hard_reasons: list[str] = []
    hard_violations: list[dict[str, Any]] = []
    if len(scenario_refs) > PLAN_TASK_MATRIX_MAX_SCENARIOS:
        hard_reasons.append(f"scenarios={len(scenario_refs)}>{PLAN_TASK_MATRIX_MAX_SCENARIOS}")
        hard_violations.append({
            "code": "scenario_hard_limit_exceeded",
            "dimension": "scenarios",
            "field": "specRefs",
            "observed": len(scenario_refs),
            "limit": PLAN_TASK_MATRIX_MAX_SCENARIOS,
        })
    if len(api_ids) > PLAN_TASK_HARD_MAX_APIS:
        hard_reasons.append(f"apis={len(api_ids)}>{PLAN_TASK_HARD_MAX_APIS}")
        hard_violations.append({
            "code": "api_hard_limit_exceeded",
            "dimension": "apis",
            "field": "apiIds",
            "observed": len(api_ids),
            "limit": PLAN_TASK_HARD_MAX_APIS,
        })
    if len(page_refs) > PLAN_TASK_HARD_MAX_UI_PAGES:
        hard_reasons.append(f"pages={len(page_refs)}>{PLAN_TASK_HARD_MAX_UI_PAGES}")
        hard_violations.append({
            "code": "page_hard_limit_exceeded",
            "dimension": "pages",
            "field": "uiRefs.pageRefs",
            "observed": len(page_refs),
            "limit": PLAN_TASK_HARD_MAX_UI_PAGES,
        })
    if len(interaction_refs) > PLAN_TASK_HARD_MAX_UI_INTERACTIONS:
        hard_reasons.append(f"interactions={len(interaction_refs)}>{PLAN_TASK_HARD_MAX_UI_INTERACTIONS}")
        hard_violations.append({
            "code": "interaction_hard_limit_exceeded",
            "dimension": "interactions",
            "field": "uiRefs.interactionRefs",
            "observed": len(interaction_refs),
            "limit": PLAN_TASK_HARD_MAX_UI_INTERACTIONS,
        })
    if hard_reasons:
        return [
            {
                "reason": "oversized_plan_task_must_split",
                "detail": f"task={task_id} detail={','.join(hard_reasons)}",
                "taskId": task_id,
                "fields": sorted({item["field"] for item in hard_violations}),
                "repairTarget": "task_group",
                "mustSplit": True,
                "violations": hard_violations,
            }
        ]

    threshold_reasons: list[str] = []
    related_ids_by_prefix: dict[str, set[str]] = {}
    threshold_diagnostics: list[dict[str, Any]] = []
    if len(scenario_refs) > PLAN_TASK_MAX_SCENARIOS:
        threshold_reasons.append(f"scenarios={len(scenario_refs)}")
        related_ids_by_prefix["SCN"] = set(scenario_refs)
        threshold_diagnostics.append({
            "dimension": "scenarios",
            "field": "specRefs",
            "observed": len(scenario_refs),
            "softLimit": PLAN_TASK_MAX_SCENARIOS,
            "hardLimit": PLAN_TASK_MATRIX_MAX_SCENARIOS,
        })
    if len(api_ids) > PLAN_TASK_MAX_APIS:
        threshold_reasons.append(f"apis={len(api_ids)}")
        related_ids_by_prefix["API"] = set(api_ids)
        threshold_diagnostics.append({
            "dimension": "apis",
            "field": "apiIds",
            "observed": len(api_ids),
            "softLimit": PLAN_TASK_MAX_APIS,
            "hardLimit": PLAN_TASK_HARD_MAX_APIS,
        })
    if len(page_refs) > PLAN_TASK_MAX_UI_PAGES:
        threshold_reasons.append(f"pages={len(page_refs)}")
        related_ids_by_prefix["PAGE"] = set(page_refs)
        threshold_diagnostics.append({
            "dimension": "pages",
            "field": "uiRefs.pageRefs",
            "observed": len(page_refs),
            "softLimit": PLAN_TASK_MAX_UI_PAGES,
            "hardLimit": PLAN_TASK_HARD_MAX_UI_PAGES,
        })
    if len(interaction_refs) > PLAN_TASK_MAX_UI_INTERACTIONS:
        threshold_reasons.append(f"interactions={len(interaction_refs)}")
        related_ids_by_prefix["UIX"] = set(interaction_refs)
        threshold_diagnostics.append({
            "dimension": "interactions",
            "field": "uiRefs.interactionRefs",
            "observed": len(interaction_refs),
            "softLimit": PLAN_TASK_MAX_UI_INTERACTIONS,
            "hardLimit": PLAN_TASK_HARD_MAX_UI_INTERACTIONS,
        })

    if not threshold_reasons:
        return []

    # Crossing a soft cap is advice about granularity, not a contract breach: the
    # hard caps above are what actually stop the stage.
    errors: list[dict[str, Any]] = []
    if len(scenario_refs) > PLAN_TASK_MAX_SCENARIOS:
        raw_merged_refs = task.get("mergedScenarioRefs")
        if "mergedScenarioRefs" not in task or raw_merged_refs == []:
            errors.append({
                "reason": "missing_plan_task_merged_scenario_refs",
                "severity": "warning",
                "detail": f"task={task_id} detail=scenarios={len(scenario_refs)}",
                "taskId": task_id,
                "field": "mergedScenarioRefs",
                "dimension": "scenarios",
                "observed": len(scenario_refs),
                "softLimit": PLAN_TASK_MAX_SCENARIOS,
                "expectedRefs": sorted(scenario_refs),
                "actualRefs": [],
                "repairTarget": "task_group",
                "violations": [{
                    "code": "merged_scenario_refs_missing",
                    "expectedRefs": sorted(scenario_refs),
                }],
            })
        else:
            merged_refs = _normalized_merged_scenario_refs(task)
            if merged_refs != scenario_refs:
                actual_refs = merged_refs or set()
                missing_refs = sorted(scenario_refs - actual_refs)
                extra_refs = sorted(actual_refs - scenario_refs)
                detail_parts = [f"task={task_id}", f"detail=scenarios={len(scenario_refs)}"]
                if missing_refs:
                    detail_parts.append(f"missingRefs={','.join(missing_refs)}")
                if extra_refs:
                    detail_parts.append(f"extraRefs={','.join(extra_refs)}")
                errors.append({
                    "reason": "invalid_plan_task_merged_scenario_refs",
                    "severity": "warning",
                    "detail": ";".join(detail_parts),
                    "taskId": task_id,
                    "field": "mergedScenarioRefs",
                    "dimension": "scenarios",
                    "observed": len(scenario_refs),
                    "softLimit": PLAN_TASK_MAX_SCENARIOS,
                    "expectedRefs": sorted(scenario_refs),
                    "actualRefs": sorted(actual_refs),
                    "missingRefs": missing_refs,
                    "extraRefs": extra_refs,
                    "repairTarget": "task_group",
                    "violations": [{
                        "code": "merged_scenario_refs_mismatch",
                        "missingRefs": missing_refs,
                        "extraRefs": extra_refs,
                    }],
                })
    rationale = task.get("splitRationale")
    if not isinstance(rationale, str) or not rationale.strip():
        errors.append({
            "reason": "missing_plan_task_split_rationale",
            "severity": "warning",
            "detail": f"task={task_id} detail={','.join(threshold_reasons)}",
            "taskId": task_id,
            "field": "splitRationale",
            "exceededDimensions": sorted(related_ids_by_prefix),
            "thresholds": threshold_diagnostics,
            "repairTarget": "task_group",
            "violations": [{
                "code": "split_rationale_missing",
                "requiredFor": threshold_reasons,
            }],
        })
    else:
        rationale_violations = _split_rationale_violations(rationale, related_ids_by_prefix)
        if rationale_violations:
            errors.append({
                "reason": "invalid_plan_task_split_rationale",
                "severity": "warning",
                "detail": f"task={task_id} detail={','.join(threshold_reasons)}",
                "taskId": task_id,
                "field": "splitRationale",
                "exceededDimensions": sorted(related_ids_by_prefix),
                "thresholds": threshold_diagnostics,
                "repairTarget": "task_group",
                "violations": rationale_violations,
            })
    return errors


def validate_plan_task_granularity_item(task: dict[str, Any], *, task_id: str) -> list[dict[str, Any]]:
    grouping_errors = validate_plan_task_grouping_item(task, task_id=task_id)
    if any(error.get("reason") in {
        "invalid_plan_task_scenario_reference",
        "oversized_plan_task_must_split",
    } for error in grouping_errors):
        return grouping_errors

    # external_dependency tasks are forbidden from carrying local
    # validationCommands, so the matrix rule has no satisfiable form for them.
    if task_execution_mode(task) == "external_dependency":
        return grouping_errors

    scenario_refs = scenario_refs_from_spec_refs(_string_list_value(task.get("specRefs")))
    matrix_violations = (
        _matrix_validation_violations(task)
        if len(scenario_refs) > PLAN_TASK_MAX_SCENARIOS
        else []
    )
    if matrix_violations:
        grouping_errors.append({
            "reason": "invalid_plan_task_matrix_validation",
            "detail": f"task={task_id} detail=scenarios={len(scenario_refs)}",
            "taskId": task_id,
            "field": "validationCommands",
            "dimension": "scenarios",
            "observed": len(scenario_refs),
            "softLimit": PLAN_TASK_MAX_SCENARIOS,
            "repairTarget": "task_detail",
            "violations": matrix_violations,
        })
    return grouping_errors
