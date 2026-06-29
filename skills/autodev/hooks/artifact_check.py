#!/usr/bin/env python3
"""Run Autodev artifact checks from board_config.json."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from common import (
    HookCheckError,
    HookContext,
    fail_line,
    info,
    is_nonempty,
    load_artifact_config,
    read_text,
    task_count,
    task_statuses,
    validate_required_files,
)
from board_core.contracts import (  # noqa: E402
    BoardConfigError,
    load_board_config,
    load_record_workflow_contracts,
    load_repo_workflow_contracts,
)
from board_core.state_store import load_state_json_records_result  # noqa: E402
from board_core.workflow_compiler import BASE_WORKFLOW_PROFILE, configured_profile_names  # noqa: E402
from hooks.evidence_integrity_gate import check_code_done, check_integrity, check_plan_evidence_refs  # noqa: E402
from hooks.evidence_store import EvidenceStoreError, read_records, stream_path  # noqa: E402
from hooks.plan_json import (  # noqa: E402
    failed_tasks,
    load_and_validate_plan,
    parse_plan_markdown,
    plan_json_path,
    unfinished_tasks,
    validate_plan_data,
    write_plan_json,
)


PENDING_STATUS = re.compile(r"待做|进行中|in[-_ ]?progress|todo|pending", re.IGNORECASE)
VALID_VERDICT = re.compile(r"verdict\s*[:=]\s*(PASS_WITH_WARNINGS|PASS|FAIL|DEGRADED)\b", re.IGNORECASE)
TERMINAL_PASS = {"PASS", "PASS_WITH_WARNINGS"}
UNIT_TEST_VERDICT = re.compile(
    r"verdict\W*[:=]\W*(PASS_WITH_WARNINGS|PASS|FAIL|BLOCKED)\b",
    re.IGNORECASE,
)
E2E_ID = re.compile(r"\bE2E-[A-Za-z0-9_-]+-\d{3}\b")
UNIT_TEST_PASS = {"PASS", "PASS_WITH_WARNINGS"}
REQ_ID = re.compile(r"\bREQ-\d{3}\b")
SCN_ID = re.compile(r"\bSCN-\d{3}\b")
TASK_ID = re.compile(r"\bT\d{3}\b")
EVIDENCE_ID = re.compile(r"\bev_\d{4}\b")
SPEC_REQUIREMENT_DEF_RE = re.compile(r"^###\s+Requirement\s+\[(REQ-\d{3})\]:\s+.+$", re.MULTILINE)
SPEC_SCENARIO_DEF_RE = re.compile(r"^####\s+Scenario\s+\[(SCN-\d{3})\]:\s+.+$", re.MULTILINE)
DESIGN_API_DEF_RE = re.compile(r"^\|\s*(API-\d{3})\s*\|", re.MULTILINE)
DESIGN_DATA_DEF_RE = re.compile(r"^\|\s*(DATA-\d{3})\s*\|", re.MULTILINE)
DESIGN_DECISION_DEF_RE = re.compile(r"^\|\s*(D-\d{3})\s*\|", re.MULTILINE)
DETAIL_DESIGN_ID = re.compile(r"\bDD-\d{2,3}\b")
STABLE_TASK_HEADING_RE = re.compile(r"^###\s+Task\s+\[T\d{3}\]\s*:\s+.+$", re.MULTILINE)
LEGACY_TASK_HEADING_RE = re.compile(r"^###\s+\d+[.)]\s+.+$", re.MULTILINE)
TASK_BLOCK_RE = re.compile(
    r"^###\s+Task\s+\[(T\d{3})\]\s*:\s+.+?(?=^###\s+Task\s+\[T\d{3}\]\s*:\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)
OLD_PLAN_TASK_BLOCK_RE = re.compile(r"^###\s+\d+[.)]\s+.+?(?=^###\s+\d+[.)]\s+|\Z)", re.MULTILINE | re.DOTALL)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _spec_definition_index(text: str) -> tuple[dict[str, set[str]], list[str]]:
    req_ids = SPEC_REQUIREMENT_DEF_RE.findall(text)
    scn_ids = SPEC_SCENARIO_DEF_RE.findall(text)
    failures: list[str] = []
    if len(req_ids) != len(set(req_ids)):
        failures.append("duplicate_requirement_id")
    if len(scn_ids) != len(set(scn_ids)):
        failures.append("duplicate_scenario_id")
    return {"REQ": set(req_ids), "SCN": set(scn_ids)}, failures


def collect_spec_definition_index(ctx: HookContext) -> tuple[dict[str, set[str]], int]:
    failures = 0
    index = {"REQ": set(), "SCN": set()}
    for spec in spec_files(ctx):
        definitions, duplicate_reasons = _spec_definition_index(read_text(spec))
        rel = spec.relative_to(ctx.feature_dir)
        for reason in duplicate_reasons:
            failures += fail_line(ctx, reason, f" file={rel}")
        index["REQ"].update(definitions["REQ"])
        index["SCN"].update(definitions["SCN"])
    return index, failures


def _design_definition_index(text: str) -> tuple[dict[str, set[str]], list[str]]:
    api_ids = DESIGN_API_DEF_RE.findall(text)
    data_ids = DESIGN_DATA_DEF_RE.findall(text)
    decision_ids = DESIGN_DECISION_DEF_RE.findall(text)
    failures: list[str] = []
    if len(api_ids) != len(set(api_ids)):
        failures.append("duplicate_design_api_id")
    if len(data_ids) != len(set(data_ids)):
        failures.append("duplicate_design_data_id")
    if len(decision_ids) != len(set(decision_ids)):
        failures.append("duplicate_design_decision_id")
    return {"API": set(api_ids), "DATA": set(data_ids), "D": set(decision_ids)}, failures


def collect_design_definition_index(ctx: HookContext) -> tuple[dict[str, set[str]], int]:
    design = ctx.file("design.md")
    if not is_nonempty(design):
        return {"API": set(), "DATA": set(), "D": set()}, 0
    definitions, duplicate_reasons = _design_definition_index(read_text(design))
    failures = 0
    for reason in duplicate_reasons:
        failures += fail_line(ctx, reason)
    return definitions, failures


def load_json_artifact(ctx: HookContext, name: str, *, required: bool = True) -> tuple[dict | None, int]:
    path = ctx.file(name)
    if not is_nonempty(path):
        if not required:
            info(ctx, "json_artifact_missing_degrade", f" file={name}")
            return None, 0
        return None, fail_line(ctx, "missing_json_artifact", f" file={name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, fail_line(ctx, "invalid_json_artifact", f" file={name} detail={exc}")
    if not isinstance(data, dict):
        return None, fail_line(ctx, "invalid_json_artifact_root", f" file={name}")
    return data, 0


def _string_list_value(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        stripped = item.strip()
        if stripped:
            result.append(stripped)
    return result


def _scenario_refs_from_spec_refs(spec_refs: list[str]) -> set[str]:
    return set(SCN_ID.findall(" ".join(spec_refs)))


def _check_scenario_ref_projection(
    ctx: HookContext,
    item: dict,
    spec_refs: list[str],
    *,
    context: str,
) -> int:
    failures = 0
    projected = _scenario_refs_from_spec_refs(spec_refs)
    for field in ("scenarioRef", "scenarioId"):
        value = item.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or value not in projected:
            failures += fail_line(ctx, "scenario_ref_not_projected_from_spec_refs", f" item={context} field={field} value={value}")
    return failures


def _known_plan_task_ids(ctx: HookContext) -> set[str]:
    plan, errors = load_and_validate_plan(plan_json_path(ctx.feature_dir))
    return set() if errors or plan is None else {str(task.get("id")) for task in plan.get("tasks", []) if isinstance(task, dict)}


def _known_evidence_ids(ctx: HookContext) -> set[str]:
    try:
        return {
            evidence_id
            for record in read_records(stream_path(ctx.feature_dir))
            if isinstance((evidence_id := record.get("evidenceId")), str)
        }
    except EvidenceStoreError:
        return set()


def _evidence_stream_exists(ctx: HookContext) -> bool:
    return stream_path(ctx.feature_dir).is_file()


def _check_evidence_stream_for_refs(ctx: HookContext, evidence_ids: list[str], *, context: str) -> int:
    if not evidence_ids:
        return 0
    if not _evidence_stream_exists(ctx):
        return fail_line(ctx, "missing_evidence_stream_for_json_refs", f" item={context}")
    try:
        read_records(stream_path(ctx.feature_dir))
    except EvidenceStoreError as exc:
        return fail_line(ctx, "invalid_evidence_stream_for_json_refs", f" item={context} detail={exc}")
    return 0


def _check_string_field(ctx: HookContext, item: dict, field: str, *, context: str, required: bool = True) -> int:
    value = item.get(field)
    if value is None and not required:
        return 0
    if not isinstance(value, str) or not value.strip():
        return fail_line(ctx, "invalid_json_field", f" item={context} field={field}")
    return 0


def _check_bool_field(ctx: HookContext, item: dict, field: str, *, context: str, required: bool = True) -> int:
    value = item.get(field)
    if value is None and not required:
        return 0
    if not isinstance(value, bool):
        return fail_line(ctx, "invalid_json_field", f" item={context} field={field}")
    return 0


def _check_string_array_field(
    ctx: HookContext,
    item: dict,
    field: str,
    *,
    context: str,
    required: bool = True,
    allow_empty: bool = False,
    item_re: re.Pattern[str] | None = None,
) -> tuple[list[str], int]:
    value = item.get(field)
    if value is None and not required:
        return [], 0
    values = _string_list_value(value)
    if values is None:
        return [], fail_line(ctx, "invalid_json_array_field", f" item={context} field={field}")
    failures = 0
    if required and not allow_empty and not values:
        failures += fail_line(ctx, "missing_json_array_items", f" item={context} field={field}")
    if item_re is not None:
        for entry in values:
            if not item_re.fullmatch(entry):
                failures += fail_line(ctx, "invalid_json_array_item", f" item={context} field={field} value={entry}")
    return values, failures


def _check_trace_refs(
    ctx: HookContext,
    item: dict,
    *,
    context: str,
    require_task: bool = False,
    require_evidence: bool = False,
    require_spec_refs: bool = True,
) -> tuple[list[str], list[str], int]:
    failures = 0
    known_tasks = _known_plan_task_ids(ctx)
    known_evidence = _known_evidence_ids(ctx)
    spec_ids, spec_failures = collect_spec_definition_index(ctx)
    failures += spec_failures

    task_id = item.get("taskId")
    if task_id is None and not require_task:
        pass
    elif not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        failures += fail_line(ctx, "invalid_json_task_id", f" item={context} taskId={task_id}")
    elif known_tasks and task_id not in known_tasks:
        failures += fail_line(ctx, "unknown_json_task_id", f" item={context} taskId={task_id}")

    spec_refs, spec_ref_failures = _check_string_array_field(
        ctx,
        item,
        "specRefs",
        context=context,
        required=require_spec_refs,
    )
    failures += spec_ref_failures
    req_refs = set(REQ_ID.findall(" ".join(spec_refs)))
    scenario_refs = _scenario_refs_from_spec_refs(spec_refs)
    if spec_refs and not req_refs:
        failures += fail_line(ctx, "missing_json_requirement_ref", f" item={context}")
    if spec_refs and not scenario_refs:
        failures += fail_line(ctx, "missing_json_scenario_ref", f" item={context}")
    for req_id in sorted(req_refs):
        if req_id not in spec_ids["REQ"]:
            failures += fail_line(ctx, "unknown_json_requirement_ref", f" item={context} id={req_id}")
    for scn_id in sorted(scenario_refs):
        if scn_id not in spec_ids["SCN"]:
            failures += fail_line(ctx, "unknown_json_scenario_ref", f" item={context} id={scn_id}")

    evidence_ids, evidence_failures = _check_string_array_field(
        ctx,
        item,
        "evidenceIds",
        context=context,
        required=require_evidence,
        item_re=EVIDENCE_ID,
    )
    failures += evidence_failures
    if require_evidence:
        failures += _check_evidence_stream_for_refs(ctx, evidence_ids, context=context)
    for evidence_id in evidence_ids:
        if known_evidence and evidence_id not in known_evidence:
            failures += fail_line(ctx, "unknown_json_evidence_id", f" item={context} evidenceId={evidence_id}")
    failures += _check_scenario_ref_projection(ctx, item, spec_refs, context=context)
    return spec_refs, evidence_ids, failures


def _scenario_covering_evidence(ctx: HookContext) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    try:
        records = read_records(stream_path(ctx.feature_dir))
    except EvidenceStoreError:
        return result
    for record in records:
        evidence_id = record.get("evidenceId")
        spec_refs = record.get("specRefs")
        if not isinstance(evidence_id, str) or not isinstance(spec_refs, list):
            continue
        scenario_refs = _scenario_refs_from_spec_refs([ref for ref in spec_refs if isinstance(ref, str)])
        for scenario_ref in scenario_refs:
            result.setdefault(scenario_ref, set()).add(evidence_id)
    return result


def _validate_scenario_coverage(
    ctx: HookContext,
    data: dict,
    *,
    field: str,
    required: bool,
    require_pass_evidence: bool,
    covering_evidence: dict[str, set[str]] | None = None,
) -> int:
    failures = 0
    spec_ids, spec_failures = collect_spec_definition_index(ctx)
    failures += spec_failures
    defined_scenarios = set(spec_ids["SCN"])
    matrix = data.get(field)
    if matrix is None:
        if required:
            return failures + fail_line(ctx, "missing_scenario_coverage", f" field={field}")
        return failures
    if not isinstance(matrix, list):
        return failures + fail_line(ctx, "invalid_scenario_coverage", f" field={field}")

    seen_scenarios: set[str] = set()
    known_evidence = _known_evidence_ids(ctx)
    evidence_by_scenario = covering_evidence if covering_evidence is not None else _scenario_covering_evidence(ctx)
    allowed_verdicts = {"pass", "fail", "manual", "missing"}
    for index, row in enumerate(matrix):
        context = f"{field}[{index}]"
        if not isinstance(row, dict):
            failures += fail_line(ctx, "invalid_scenario_coverage_row", f" item={context}")
            continue
        scenario_ref = row.get("scenarioRef")
        if not isinstance(scenario_ref, str) or scenario_ref not in defined_scenarios:
            failures += fail_line(ctx, "unknown_scenario_coverage_ref", f" item={context} id={scenario_ref}")
            continue
        if scenario_ref in seen_scenarios:
            failures += fail_line(ctx, "duplicate_scenario_coverage_row", f" item={context} id={scenario_ref}")
        seen_scenarios.add(scenario_ref)
        row_verdict = row.get("verdict")
        normalized_verdict = row_verdict.lower() if isinstance(row_verdict, str) else ""
        if normalized_verdict not in allowed_verdicts:
            failures += fail_line(ctx, "invalid_scenario_coverage_verdict", f" item={context}")
        row_evidence, row_evidence_failures = _check_string_array_field(
            ctx,
            row,
            "evidenceIds",
            context=context,
            required=normalized_verdict == "pass" and require_pass_evidence,
            item_re=EVIDENCE_ID,
        )
        failures += row_evidence_failures
        failures += _check_evidence_stream_for_refs(ctx, row_evidence, context=context)
        for evidence_id in row_evidence:
            if known_evidence and evidence_id not in known_evidence:
                failures += fail_line(ctx, "unknown_scenario_coverage_evidence_id", f" item={context} evidenceId={evidence_id}")
        if normalized_verdict == "pass" and require_pass_evidence:
            covering_ids = evidence_by_scenario.get(scenario_ref, set())
            if not row_evidence:
                failures += fail_line(ctx, "scenario_coverage_pass_without_evidence", f" item={context} id={scenario_ref}")
            elif not any(evidence_id in covering_ids for evidence_id in row_evidence):
                failures += fail_line(ctx, "scenario_coverage_pass_evidence_mismatch", f" item={context} id={scenario_ref}")

    missing_rows = defined_scenarios - seen_scenarios
    if missing_rows:
        failures += fail_line(ctx, "missing_scenario_coverage_rows", f" field={field} ids={','.join(sorted(missing_rows))}")
    return failures


def _known_design_refs(ctx: HookContext) -> set[str]:
    design_ids, _ = collect_design_definition_index(ctx)
    refs = {
        f"design.md#{item}"
        for kind in ("API", "DATA", "D")
        for item in design_ids[kind]
    }
    detail = ctx.file("DETAIL_DESIGN.md")
    if is_nonempty(detail):
        refs.update(f"DETAIL_DESIGN.md#{item}" for item in DETAIL_DESIGN_ID.findall(read_text(detail)))
    return refs


def _effective_needs_fix_targets(ctx: HookContext) -> set[str]:
    result = load_state_json_records_result(ctx.root)
    if result.exists and not result.errors:
        record = result.records.get(ctx.slug)
        if record is not None:
            try:
                return set(load_record_workflow_contracts(REPO_ROOT, record, workspace=ctx.root).allowed_next.get("needs_fix", frozenset()))
            except BoardConfigError:
                return set()
    try:
        return set(load_repo_workflow_contracts(REPO_ROOT, workspace=ctx.root).allowed_next.get("needs_fix", frozenset()))
    except BoardConfigError:
        return set()


def _check_verify_scenario_decisions(
    ctx: HookContext,
    data: dict,
    *,
    defined_scenarios: set[str],
    passed: list[str],
    failed: list[str],
    manual: list[str],
) -> int:
    failures = 0
    passed_set = set(passed)
    failed_set = set(failed)
    manual_set = set(manual)
    overlaps = (passed_set & failed_set) | (passed_set & manual_set) | (failed_set & manual_set)
    if overlaps:
        failures += fail_line(ctx, "duplicate_verify_scenario_decision", f" ids={','.join(sorted(overlaps))}")

    decided = passed_set | failed_set | manual_set
    missing_decisions = defined_scenarios - decided
    if missing_decisions:
        failures += fail_line(ctx, "missing_verify_scenario_decision", f" ids={','.join(sorted(missing_decisions))}")

    verdict = data.get("verdict")
    if isinstance(verdict, str):
        normalized_verdict = verdict.lower()
        if normalized_verdict == "pass" and (failed_set or manual_set or missing_decisions):
            failures += fail_line(ctx, "invalid_verify_decision_summary")
        if normalized_verdict == "fail" and not failed_set:
            failures += fail_line(ctx, "invalid_verify_decision_summary")
        if normalized_verdict == "manual" and not manual_set:
            failures += fail_line(ctx, "invalid_verify_decision_summary")

    matrix = data.get("scenarioCoverage")
    if not isinstance(matrix, list):
        return failures
    for index, row in enumerate(matrix):
        context = f"scenarioCoverage[{index}]"
        if not isinstance(row, dict):
            continue
        scenario_ref = row.get("scenarioRef")
        row_verdict = row.get("verdict")
        if not isinstance(scenario_ref, str) or scenario_ref not in defined_scenarios or not isinstance(row_verdict, str):
            continue
        normalized_row_verdict = row_verdict.lower()
        mismatch = False
        if normalized_row_verdict == "pass":
            mismatch = scenario_ref not in passed_set
        elif normalized_row_verdict == "fail":
            mismatch = scenario_ref not in failed_set
        elif normalized_row_verdict == "manual":
            mismatch = scenario_ref not in manual_set
        elif normalized_row_verdict == "missing":
            mismatch = scenario_ref in passed_set or scenario_ref not in (failed_set | manual_set)
        if mismatch:
            failures += fail_line(ctx, "verify_scenario_coverage_decision_mismatch", f" item={context} id={scenario_ref}")
    return failures


def validate_review_findings_json(ctx: HookContext) -> int:
    data, failures = load_json_artifact(
        ctx,
        "REVIEW_FINDINGS.json",
        required=ctx.requires_artifact("REVIEW_FINDINGS.json"),
    )
    if data is None:
        return failures
    if data.get("version") != 1:
        failures += fail_line(ctx, "invalid_review_findings_version")
    findings = data.get("findings")
    if not isinstance(findings, list):
        return failures + fail_line(ctx, "invalid_review_findings_items")
    severities = {"blocker", "high", "medium", "low", "info", "minor", "important"}
    for index, finding in enumerate(findings):
        context = f"findings[{index}]"
        if not isinstance(finding, dict):
            failures += fail_line(ctx, "invalid_review_finding", f" item={context}")
            continue
        failures += _check_string_field(ctx, finding, "id", context=context)
        failures += _check_string_field(ctx, finding, "message", context=context)
        severity = finding.get("severity")
        if not isinstance(severity, str) or severity.strip().lower() not in severities:
            failures += fail_line(ctx, "invalid_review_finding_severity", f" item={context}")
        failures += _check_trace_refs(ctx, finding, context=context, require_task=True, require_evidence=True)[2]
        suggested = finding.get("suggestedCheckpoint")
        if suggested is not None and (not isinstance(suggested, str) or not suggested.strip()):
            failures += fail_line(ctx, "invalid_json_field", f" item={context} field=suggestedCheckpoint")
    return failures


def validate_unit_test_result_json(ctx: HookContext) -> int:
    data, failures = load_json_artifact(
        ctx,
        "UNIT_TEST_RESULT.json",
        required=ctx.requires_artifact("UNIT_TEST_RESULT.json"),
    )
    if data is None:
        return failures
    if data.get("version") != 1:
        failures += fail_line(ctx, "invalid_unit_test_result_version")
    verdict = data.get("verdict")
    if verdict is not None and (not isinstance(verdict, str) or verdict.upper() not in {"PASS", "PASS_WITH_WARNINGS", "FAIL", "BLOCKED"}):
        failures += fail_line(ctx, "invalid_unit_test_result_verdict")
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        return failures + fail_line(ctx, "invalid_unit_test_targets")
    for index, target in enumerate(targets):
        context = f"targets[{index}]"
        if not isinstance(target, dict):
            failures += fail_line(ctx, "invalid_unit_test_target", f" item={context}")
            continue
        failures += _check_string_field(ctx, target, "targetId", context=context)
        failures += _check_trace_refs(ctx, target, context=context, require_task=True, require_evidence=True)[2]
        result = target.get("result")
        if not isinstance(result, str) or result.upper() not in {"PASS", "PASS_WITH_WARNINGS", "FAIL", "BLOCKED", "SKIP"}:
            failures += fail_line(ctx, "invalid_unit_test_target_result", f" item={context}")
        failures += _check_string_field(ctx, target, "command", context=context)
        coverage = target.get("coverage")
        if coverage is not None and not isinstance(coverage, (dict, list, int, float, str)):
            failures += fail_line(ctx, "invalid_json_field", f" item={context} field=coverage")
    failures += _validate_scenario_coverage(
        ctx,
        data,
        field="scenarioCoverage",
        required=True,
        require_pass_evidence=True,
    )
    return failures


def validate_e2e_result_json(ctx: HookContext) -> int:
    data, failures = load_json_artifact(
        ctx,
        "E2E_RESULT.json",
        required=ctx.requires_artifact("E2E_RESULT.json"),
    )
    if data is None:
        return failures
    if data.get("version") != 1:
        failures += fail_line(ctx, "invalid_e2e_result_version")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        return failures + fail_line(ctx, "invalid_e2e_result_cases")
    for index, case in enumerate(cases):
        context = f"cases[{index}]"
        if not isinstance(case, dict):
            failures += fail_line(ctx, "invalid_e2e_result_case", f" item={context}")
            continue
        failures += _check_string_field(ctx, case, "caseId", context=context)
        case_id = case.get("caseId")
        if isinstance(case_id, str) and not E2E_ID.fullmatch(case_id):
            failures += fail_line(ctx, "invalid_e2e_result_case_id", f" item={context}")
        failures += _check_trace_refs(ctx, case, context=context, require_task=True, require_evidence=True)[2]
        failures += _check_bool_field(ctx, case, "uiRequired", context=context)
        failures += _check_string_field(ctx, case, "executionMode", context=context)
        steps = case.get("steps")
        if not isinstance(steps, list):
            failures += fail_line(ctx, "invalid_json_array_field", f" item={context} field=steps")
        verdict = case.get("verdict")
        if not isinstance(verdict, str) or verdict.upper() not in {"PASS", "FAIL", "BLOCKED", "SKIP"}:
            failures += fail_line(ctx, "invalid_e2e_result_verdict", f" item={context}")
    failures += _validate_scenario_coverage(
        ctx,
        data,
        field="scenarioCoverage",
        required=True,
        require_pass_evidence=True,
    )
    return failures


def validate_verify_decision_json(ctx: HookContext) -> int:
    data, failures = load_json_artifact(
        ctx,
        "VERIFY_DECISION.json",
        required=ctx.requires_artifact("VERIFY_DECISION.json"),
    )
    if data is None:
        return failures
    if data.get("version") != 1:
        failures += fail_line(ctx, "invalid_verify_decision_version")
    verdict = data.get("verdict")
    if not isinstance(verdict, str) or verdict.lower() not in {"pass", "fail", "manual"}:
        failures += fail_line(ctx, "invalid_verify_decision_verdict")
    next_checkpoint = data.get("nextCheckpoint")
    if not isinstance(next_checkpoint, str) or next_checkpoint not in {"verify_done", "needs_fix", "verify_in_progress"}:
        failures += fail_line(ctx, "invalid_verify_next_checkpoint")
    elif isinstance(verdict, str):
        normalized_verdict = verdict.lower()
        if normalized_verdict == "pass" and next_checkpoint != "verify_done":
            failures += fail_line(ctx, "invalid_verify_decision_transition")
        if normalized_verdict == "fail" and next_checkpoint != "needs_fix":
            failures += fail_line(ctx, "invalid_verify_decision_transition")
        if normalized_verdict == "manual" and next_checkpoint not in {"verify_in_progress", "needs_fix"}:
            failures += fail_line(ctx, "invalid_verify_decision_transition")

    spec_ids, spec_failures = collect_spec_definition_index(ctx)
    failures += spec_failures
    defined_scenarios = set(spec_ids["SCN"])
    covered_by_evidence = _scenario_covering_evidence(ctx)
    known_evidence = _known_evidence_ids(ctx)

    passed, passed_failures = _check_string_array_field(
        ctx,
        data,
        "passedScenarioRefs",
        context="VERIFY_DECISION",
        required=True,
        allow_empty=True,
    )
    failed, failed_failures = _check_string_array_field(
        ctx,
        data,
        "failedScenarioRefs",
        context="VERIFY_DECISION",
        required=True,
        allow_empty=True,
    )
    manual, manual_failures = _check_string_array_field(
        ctx,
        data,
        "manualVerificationRefs",
        context="VERIFY_DECISION",
        required=True,
        allow_empty=True,
    )
    evidence_ids, evidence_failures = _check_string_array_field(
        ctx,
        data,
        "evidenceIds",
        context="VERIFY_DECISION",
        required=True,
        item_re=EVIDENCE_ID,
    )
    failures += passed_failures + failed_failures + manual_failures + evidence_failures
    failures += _check_evidence_stream_for_refs(ctx, evidence_ids, context="VERIFY_DECISION")
    for field, scenario_refs in (
        ("passedScenarioRefs", passed),
        ("failedScenarioRefs", failed),
        ("manualVerificationRefs", manual),
    ):
        for scenario_ref in scenario_refs:
            if scenario_ref not in defined_scenarios:
                failures += fail_line(ctx, "unknown_verify_scenario_ref", f" field={field} id={scenario_ref}")
    for evidence_id in evidence_ids:
        if known_evidence and evidence_id not in known_evidence:
            failures += fail_line(ctx, "unknown_verify_evidence_id", f" evidenceId={evidence_id}")

    failures += _validate_scenario_coverage(
        ctx,
        data,
        field="scenarioCoverage",
        required=True,
        require_pass_evidence=True,
        covering_evidence=covered_by_evidence,
    )
    failures += _check_verify_scenario_decisions(
        ctx,
        data,
        defined_scenarios=defined_scenarios,
        passed=passed,
        failed=failed,
        manual=manual,
    )

    passed_without_evidence = [scenario for scenario in passed if not covered_by_evidence.get(scenario)]
    if passed_without_evidence:
        failures += fail_line(ctx, "verify_passed_scenario_without_evidence", f" ids={','.join(sorted(passed_without_evidence))}")
    return failures


def validate_fix_request_json(ctx: HookContext) -> int:
    data, failures = load_json_artifact(
        ctx,
        "FIX_REQUEST.json",
        required=ctx.requires_artifact("FIX_REQUEST.json"),
    )
    if data is None:
        return failures
    if data.get("version") != 1:
        failures += fail_line(ctx, "invalid_fix_request_version")
    for field in ["featureId", "sourceCheckpoint", "sourceNodeId", "suggestedCheckpoint", "rootCause", "blockingReason", "createdAt"]:
        failures += _check_string_field(ctx, data, field, context="FIX_REQUEST")
    root_cause = data.get("rootCause")
    if isinstance(root_cause, str) and root_cause not in {
        "requirement_ambiguous",
        "spec_gap",
        "design_conflict",
        "implementation_bug",
        "test_bug",
        "environment_issue",
        "permission_issue",
        "dependency_issue",
        "unknown",
    }:
        failures += fail_line(ctx, "invalid_fix_request_root_cause")
    suggested = data.get("suggestedCheckpoint")
    allowed_fix_targets = _effective_needs_fix_targets(ctx)
    if isinstance(suggested, str) and allowed_fix_targets and suggested not in allowed_fix_targets:
        failures += fail_line(ctx, "invalid_fix_request_suggested_checkpoint")
    human_action = data.get("humanActionRequired")
    if not isinstance(human_action, bool):
        failures += fail_line(ctx, "invalid_json_field", " item=FIX_REQUEST field=humanActionRequired")
    failed_spec_refs, spec_failures = _check_string_array_field(
        ctx,
        data,
        "failedSpecRefs",
        context="FIX_REQUEST",
        required=False,
    )
    failures += spec_failures
    failed_evidence_ids, evidence_failures = _check_string_array_field(
        ctx,
        data,
        "failedEvidenceIds",
        context="FIX_REQUEST",
        required=False,
        item_re=EVIDENCE_ID,
    )
    failures += evidence_failures
    failures += _check_evidence_stream_for_refs(ctx, failed_evidence_ids, context="FIX_REQUEST")
    _, design_failures = _check_string_array_field(
        ctx,
        data,
        "failedDesignRefs",
        context="FIX_REQUEST",
        required=False,
    )
    failures += design_failures

    spec_ids, spec_id_failures = collect_spec_definition_index(ctx)
    failures += spec_id_failures
    for req_id in set(REQ_ID.findall(" ".join(failed_spec_refs))):
        if req_id not in spec_ids["REQ"]:
            failures += fail_line(ctx, "unknown_fix_request_requirement_ref", f" id={req_id}")
    for scn_id in _scenario_refs_from_spec_refs(failed_spec_refs):
        if scn_id not in spec_ids["SCN"]:
            failures += fail_line(ctx, "unknown_fix_request_scenario_ref", f" id={scn_id}")
    known_evidence = _known_evidence_ids(ctx)
    for evidence_id in failed_evidence_ids:
        if known_evidence and evidence_id not in known_evidence:
            failures += fail_line(ctx, "unknown_fix_request_evidence_id", f" evidenceId={evidence_id}")
    known_design_refs = _known_design_refs(ctx)
    for design_ref in data.get("failedDesignRefs", []) if isinstance(data.get("failedDesignRefs"), list) else []:
        if isinstance(design_ref, str) and known_design_refs and design_ref not in known_design_refs:
            failures += fail_line(ctx, "unknown_fix_request_design_ref", f" ref={design_ref}")
    return failures


def _plan_is_legacy_format(plan_text: str) -> bool:
    return bool(LEGACY_TASK_HEADING_RE.search(plan_text)) and not STABLE_TASK_HEADING_RE.search(plan_text)


def _task_field(block_text: str, label: str) -> str | None:
    match = re.search(rf"^\s*-\s*\*\*{re.escape(label)}[:：]\*\*\s*(.+)$", block_text, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def _task_id_values(block_text: str, label: str, pattern: str) -> list[str] | None:
    value = _task_field(block_text, label)
    if value is None:
        return None
    value = value.strip()
    if not value or value.lower() == "无":
        return []
    return list(dict.fromkeys(re.findall(pattern, value)))


def _ids_from_design_refs(design_refs: str) -> dict[str, list[str]]:
    return {
        "API": re.findall(r"\bAPI-\d{3}\b", design_refs),
        "DATA": re.findall(r"\bDATA-\d{3}\b", design_refs),
        "D": re.findall(r"\bD-\d{3}\b", design_refs),
    }


def _boolean_marker_value(text: str, marker: str) -> bool | None:
    match = re.search(rf"{re.escape(marker)}\W*:\W*(true|false)\b", text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower() == "true"


def _design_escape_hatches(ctx: HookContext) -> tuple[bool, bool]:
    design = ctx.file("design.md")
    if not is_nonempty(design):
        return False, False
    text = read_text(design)
    return (
        _boolean_marker_value(text, "x-auto-no-http-api") is True,
        _boolean_marker_value(text, "x-auto-no-sql") is True,
    )


def spec_files(ctx: HookContext) -> list[Path]:
    return sorted(
        path
        for path in ctx.feature_dir.glob("specs/**/*.md")
        if path.is_file() and path.stat().st_size > 0
    )


def validate_proposal_contract(ctx: HookContext) -> int:
    proposal = ctx.file("proposal.md")
    if not is_nonempty(proposal):
        return fail_line(ctx, "missing_proposal")

    text = read_text(proposal)
    failures = 0
    required_sections = [
        "Why",
        "What Changes",
        "Capabilities",
        "Impact",
        "Out of Scope",
    ]
    for section in required_sections:
        if section not in text:
            failures += fail_line(ctx, "invalid_proposal_missing_section", f" section={section!r}")
    return failures


def validate_specs_contract(ctx: HookContext) -> int:
    specs = spec_files(ctx)
    if not specs:
        return fail_line(ctx, "missing_specs")

    failures = 0
    for spec in specs:
        text = read_text(spec)
        rel = spec.relative_to(ctx.feature_dir)
        _, duplicate_reasons = _spec_definition_index(text)
        for reason in duplicate_reasons:
            failures += fail_line(ctx, reason, f" file={rel}")
        if not re.search(r"^##\s+(ADDED|MODIFIED|REMOVED|RENAMED)\s+Requirements\b", text, re.MULTILINE):
            failures += fail_line(ctx, "invalid_spec_missing_operation_header", f" file={rel}")
        if not re.search(r"^###\s+Requirement\s+\[REQ-\d{3}\]:\s+.+", text, re.MULTILINE):
            failures += fail_line(ctx, "invalid_spec_missing_requirement", f" file={rel}")
        if not re.search(r"^####\s+Scenario\s+\[SCN-\d{3}\]:\s+.+", text, re.MULTILINE):
            failures += fail_line(ctx, "invalid_spec_missing_scenario", f" file={rel}")
    return failures


def repo_root_from_this_file() -> Path:
    return Path(__file__).resolve().parents[3]


def validate_design_contract(ctx: HookContext) -> int:
    design = ctx.file("design.md")
    if not is_nonempty(design):
        return fail_line(ctx, "missing_design")

    text = read_text(design)
    failures = 0
    required_sections = [
        "Context / 输入上下文",
        "Spec Traceability",
        "API Decisions",
        "Data Decisions",
        "Technical Design",
        "Risks / Open Questions",
    ]
    for section in required_sections:
        if section not in text:
            failures += fail_line(ctx, "invalid_design_missing_section", f" section={section!r}")

    design_ids, duplicate_reasons = _design_definition_index(text)
    for reason in duplicate_reasons:
        failures += fail_line(ctx, reason)
    no_http_api = _boolean_marker_value(text, "x-auto-no-http-api")
    no_sql = _boolean_marker_value(text, "x-auto-no-sql")
    if no_http_api is None:
        failures += fail_line(ctx, "missing_design_api_marker")
    if no_sql is None:
        failures += fail_line(ctx, "missing_design_data_marker")
    if not REQ_ID.search(text):
        failures += fail_line(ctx, "missing_design_requirement_id")
    if not SCN_ID.search(text):
        failures += fail_line(ctx, "missing_design_scenario_id")
    if no_http_api is not True and not design_ids["API"]:
        failures += fail_line(ctx, "missing_design_api_id")
    if no_sql is not True and not design_ids["DATA"]:
        failures += fail_line(ctx, "missing_design_data_id")
    if not design_ids["D"]:
        failures += fail_line(ctx, "missing_design_decision_id")
    return failures


def validate_plan_initial_tasks(ctx: HookContext) -> int:
    plan = ctx.file("PLAN.md")
    if not is_nonempty(plan):
        return fail_line(ctx, "missing_plan")
    failures = 0
    plan_text = read_text(plan)
    if "任务总览" not in plan_text or "任务详情" not in plan_text:
        failures += fail_line(ctx, "invalid_plan_structure")
    if task_count(plan) <= 0:
        failures += fail_line(ctx, "invalid_plan_no_tasks")
    statuses = task_statuses(plan)
    if not statuses:
        failures += fail_line(ctx, "missing_task_statuses")
    elif any("待做" not in status for status in statuses):
        failures += fail_line(ctx, "invalid_initial_task_status")
    return failures


def validate_plan_json_contract(ctx: HookContext) -> int:
    plan_json = ctx.file("plan.json")
    data, errors = load_and_validate_plan(plan_json)
    failures = 0
    if errors:
        for error in errors:
            failures += fail_line(ctx, "invalid_plan_json", f" detail={error}")
        return failures
    if data is None:
        return fail_line(ctx, "missing_plan_json")
    return 0


def validate_plan_finished_tasks(ctx: HookContext) -> int:
    plan_json = ctx.file("plan.json")
    if is_nonempty(plan_json):
        data, errors = load_and_validate_plan(plan_json, require_all_done=True)
        failures = 0
        for error in errors:
            failures += fail_line(ctx, "invalid_plan_json", f" detail={error}")
        if data is not None:
            if unfinished := unfinished_tasks(data):
                failures += fail_line(ctx, "plan_json_has_pending_tasks", f" tasks={','.join(unfinished)}")
            if failed := failed_tasks(data):
                failures += fail_line(ctx, "plan_json_has_failed_tasks", f" tasks={','.join(failed)}")
        if failures:
            return failures
        return 0

    plan = ctx.file("PLAN.md")
    if not is_nonempty(plan):
        # PLAN.md not in this workflow's contract (e.g. lean): degrade,
        # task closure lives in the completion summary instead.
        if not ctx.requires_artifact("PLAN.md"):
            info(ctx, "plan_not_in_contract_degrade")
            return 0
        return fail_line(ctx, "missing_plan")
    failures = 0
    plan_text = read_text(plan)
    if task_count(plan) <= 0:
        failures += fail_line(ctx, "invalid_plan_no_tasks")
    statuses = task_statuses(plan)
    if not statuses:
        failures += fail_line(ctx, "missing_task_statuses")
    elif any(PENDING_STATUS.search(status) for status in statuses):
        failures += fail_line(ctx, "plan_has_pending_tasks")
    elif any("失败" in status for status in statuses):
        failures += fail_line(ctx, "plan_has_failed_tasks")
    elif any("完成" not in status for status in statuses):
        failures += fail_line(ctx, "invalid_task_status")
    if STABLE_TASK_HEADING_RE.search(plan_text):
        failures += validate_stable_plan_contract(ctx, plan_text)
    elif _plan_is_legacy_format(plan_text):
        info(ctx, "plan_legacy_format_degrade")
    else:
        failures += fail_line(ctx, "missing_task_id_heading")
    return failures


def validate_code_done_gate(ctx: HookContext) -> int:
    if not ctx.requires_artifact("evidence/EVIDENCE.jsonl"):
        info(ctx, "code_done_gate_not_in_contract_degrade")
        return 0
    failures = 0
    for error in check_code_done(ctx.feature_dir):
        failures += fail_line(ctx, "invalid_code_done_gate", f" detail={error}")
    return failures


def validate_evidence_integrity(ctx: HookContext) -> int:
    if not ctx.requires_artifact("evidence/EVIDENCE.jsonl"):
        info(ctx, "evidence_not_in_contract_degrade")
        return 0
    failures = 0
    for error in check_integrity(ctx.feature_dir, require_index=True):
        failures += fail_line(ctx, "invalid_evidence_stream", f" detail={error}")
    if is_nonempty(plan_json_path(ctx.feature_dir)):
        for error in check_plan_evidence_refs(ctx.feature_dir):
            failures += fail_line(ctx, "invalid_evidence_trace", f" detail={error}")
    return failures


def validate_stable_plan_contract(ctx: HookContext, plan_text: str) -> int:
    failures = 0
    spec_ids, spec_failures = collect_spec_definition_index(ctx)
    design_ids, design_failures = collect_design_definition_index(ctx)
    no_http_api, no_sql = _design_escape_hatches(ctx)
    failures += spec_failures + design_failures
    blocks = list(TASK_BLOCK_RE.finditer(plan_text))
    if not blocks:
        return fail_line(ctx, "missing_task_id_heading")

    seen_ids: set[str] = set()
    for block in blocks:
        task_id = block.group(1)
        if task_id in seen_ids:
            failures += fail_line(ctx, "duplicate_task_id", f" task={task_id}")
        seen_ids.add(task_id)

        block_text = block.group(0)
        spec_refs = _task_field(block_text, "规格依据")
        design_refs = _task_field(block_text, "设计依据")
        api_ids = _task_id_values(block_text, "api_id", r"\bAPI-\d{3}\b")
        data_ids = _task_id_values(block_text, "data_id", r"\bDATA-\d{3}\b")
        decision_ids = _task_id_values(block_text, "decision_id", r"\bD-\d{3}\b")
        evidence_refs = _task_field(block_text, "证据依据")
        if spec_refs is None:
            failures += fail_line(ctx, "missing_task_spec_refs", f" task={task_id}")
            spec_refs = ""
        if design_refs is None and not any(
            values is not None for values in [api_ids, data_ids, decision_ids]
        ):
            failures += fail_line(ctx, "missing_task_design_refs", f" task={task_id}")
        if evidence_refs is None:
            failures += fail_line(ctx, "missing_task_evidence_refs", f" task={task_id}")
            evidence_refs = ""

        req_refs = REQ_ID.findall(spec_refs)
        scn_refs = SCN_ID.findall(spec_refs)
        if not req_refs:
            failures += fail_line(ctx, "missing_task_requirement_id", f" task={task_id}")
        if not scn_refs:
            failures += fail_line(ctx, "missing_task_scenario_id", f" task={task_id}")
        for req_id in req_refs:
            if req_id not in spec_ids["REQ"]:
                failures += fail_line(ctx, "unknown_task_requirement_id", f" task={task_id} id={req_id}")
        for scn_id in scn_refs:
            if scn_id not in spec_ids["SCN"]:
                failures += fail_line(ctx, "unknown_task_scenario_id", f" task={task_id} id={scn_id}")

        summary_refs = _ids_from_design_refs(design_refs) if design_refs is not None else {"API": [], "DATA": [], "D": []}
        if api_ids is None:
            api_refs = summary_refs["API"]
            if not no_http_api and not api_refs:
                failures += fail_line(ctx, "missing_task_api_id", f" task={task_id}")
        else:
            api_refs = api_ids
            if not no_http_api and not api_refs:
                failures += fail_line(ctx, "missing_task_api_id", f" task={task_id}")
        if data_ids is None:
            data_refs = summary_refs["DATA"]
            if not no_sql and not data_refs:
                failures += fail_line(ctx, "missing_task_data_id", f" task={task_id}")
        else:
            data_refs = data_ids
            if not no_sql and not data_refs:
                failures += fail_line(ctx, "missing_task_data_id", f" task={task_id}")
        if decision_ids is None:
            decision_refs = summary_refs["D"]
            if not decision_refs:
                failures += fail_line(ctx, "missing_task_decision_id", f" task={task_id}")
        else:
            decision_refs = decision_ids
            if not decision_refs:
                failures += fail_line(ctx, "missing_task_decision_id", f" task={task_id}")
        for api_id in api_refs:
            if api_id not in design_ids["API"]:
                failures += fail_line(ctx, "unknown_task_api_id", f" task={task_id} id={api_id}")
        for data_id in data_refs:
            if data_id not in design_ids["DATA"]:
                failures += fail_line(ctx, "unknown_task_data_id", f" task={task_id} id={data_id}")
        for decision_id in decision_refs:
            if decision_id not in design_ids["D"]:
                failures += fail_line(ctx, "unknown_task_decision_id", f" task={task_id} id={decision_id}")

        if not EVIDENCE_ID.search(evidence_refs):
            failures += fail_line(ctx, "missing_task_evidence_id", f" task={task_id}")
    return failures


def validate_requirements_eval_verdict(ctx: HookContext) -> int:
    eval_report = ctx.file("REQUIREMENTS_EVAL.md")
    if not is_nonempty(eval_report):
        return fail_line(ctx, "missing_requirements_eval")

    content = read_text(eval_report)
    if not re.search(r"verdict\s*[:=]", content, re.IGNORECASE):
        return fail_line(ctx, "missing_verdict_in_eval")
    verdict_match = VALID_VERDICT.search(content)
    if not verdict_match:
        return fail_line(ctx, "invalid_verdict")
    if verdict_match.group(1).upper() not in TERMINAL_PASS:
        return fail_line(ctx, "non_terminal_verdict")
    return 0


def validate_unit_test_report_contract(ctx: HookContext) -> int:
    report = ctx.file("UNIT_TEST_REPORT.md")
    log = ctx.file("test-output.log")
    failures = 0

    if not is_nonempty(report):
        return fail_line(ctx, "missing_unit_test_report")
    if not is_nonempty(log):
        failures += fail_line(ctx, "missing_test_output_log")

    content = read_text(report)
    required_sections = [
        "Test Plan",
        "Execution Summary",
        "Coverage Matrix",
        "Failure Analysis",
        "Fix Attempts",
        "Commands",
        "Handoff",
    ]
    for section in required_sections:
        if section not in content:
            failures += fail_line(ctx, "invalid_unit_test_report_missing_section", f" section={section!r}")

    if not re.search(r"verdict\W*[:=]", content, re.IGNORECASE):
        failures += fail_line(ctx, "missing_unit_test_verdict")
    else:
        verdict_match = UNIT_TEST_VERDICT.search(content)
        if not verdict_match:
            failures += fail_line(ctx, "invalid_unit_test_verdict")
        elif verdict_match.group(1).upper() not in UNIT_TEST_PASS:
            failures += fail_line(ctx, "non_terminal_unit_test_verdict")

    if "test-output.log" not in content:
        failures += fail_line(ctx, "missing_test_log_reference")
    if not re.search(
        r"\|\s*Source\s*\|\s*Requirement(?:\s*/\s*Scenario)?\s*\|\s*Test\s*\|\s*Result\s*\|",
        content,
    ):
        failures += fail_line(ctx, "missing_coverage_matrix_table")
    if not re.search(r"\|\s*ID\s*\|\s*Classification\s*\|\s*Files Changed\s*\|", content):
        failures += fail_line(ctx, "missing_fix_attempts_table")
    if not TASK_ID.search(content):
        failures += fail_line(ctx, "missing_task_id_reference")
    if not EVIDENCE_ID.search(content):
        failures += fail_line(ctx, "missing_evidence_id_reference")
    return failures


def validate_e2e_report_contract(ctx: HookContext) -> int:
    cases = ctx.file("E2E_TEST_CASES.yaml")
    report = ctx.file("E2E_REPORT.md")
    log = ctx.file("e2e-run.log")
    failures = 0

    if not is_nonempty(cases):
        return fail_line(ctx, "missing_e2e_cases")
    if not is_nonempty(report):
        failures += fail_line(ctx, "missing_e2e_report")
    if not is_nonempty(log):
        failures += fail_line(ctx, "missing_e2e_run_log")

    cases_text = read_text(cases)
    if not E2E_ID.search(cases_text):
        failures += fail_line(ctx, "missing_e2e_case_id")
    if not REQ_ID.search(cases_text):
        failures += fail_line(ctx, "missing_e2e_requirement_id")
    if not SCN_ID.search(cases_text):
        failures += fail_line(ctx, "missing_e2e_scenario_id")
    if "execution_mode:" not in cases_text:
        failures += fail_line(ctx, "missing_e2e_execution_mode")
    if "ui_required:" not in cases_text:
        failures += fail_line(ctx, "missing_e2e_ui_required")

    if is_nonempty(report):
        report_text = read_text(report)
        if not E2E_ID.search(report_text):
            failures += fail_line(ctx, "missing_e2e_report_case_id")
        if not REQ_ID.search(report_text):
            failures += fail_line(ctx, "missing_e2e_report_requirement_id")
        if not SCN_ID.search(report_text):
            failures += fail_line(ctx, "missing_e2e_report_scenario_id")
    return failures


def validate_verify_report_contract(ctx: HookContext) -> int:
    report = ctx.file("VERIFY_REPORT.md")
    if not is_nonempty(report):
        return fail_line(ctx, "missing_verify_report")

    content = read_text(report)
    failures = 0
    required_sections = [
        "验证总览",
        "Specs / Design Contract 验证",
        "结论",
    ]
    for section in required_sections:
        if section not in content:
            failures += fail_line(ctx, "invalid_verify_report_missing_section", f" section={section!r}")
    if not REQ_ID.search(content):
        failures += fail_line(ctx, "missing_verify_requirement_id")
    if not SCN_ID.search(content):
        failures += fail_line(ctx, "missing_verify_scenario_id")
    if "UNIT_TEST_REPORT" not in content and "E2E_REPORT" not in content and "e2e-run.log" not in content:
        failures += fail_line(ctx, "missing_verify_evidence_source")
    return failures


VALIDATORS = {
    "proposal_contract": validate_proposal_contract,
    "specs_contract": validate_specs_contract,
    "design_contract": validate_design_contract,
    "plan_json_contract": validate_plan_json_contract,
    "plan_initial_tasks": validate_plan_initial_tasks,
    "plan_finished_tasks": validate_plan_finished_tasks,
    "code_done_gate": validate_code_done_gate,
    "evidence_integrity": validate_evidence_integrity,
    "requirements_eval_verdict": validate_requirements_eval_verdict,
    "review_findings_json": validate_review_findings_json,
    "unit_test_report_contract": validate_unit_test_report_contract,
    "unit_test_result_json": validate_unit_test_result_json,
    "e2e_report_contract": validate_e2e_report_contract,
    "e2e_result_json": validate_e2e_result_json,
    "verify_report_contract": validate_verify_report_contract,
    "verify_decision_json": validate_verify_decision_json,
    "fix_request_json": validate_fix_request_json,
}


def validate_skill_config_schema(
    repo_root: Path,
    skill: str,
    *,
    workspace_root: Path | None = None,
    workflow_profile: str = BASE_WORKFLOW_PROFILE,
    workflow_decisions: dict[str, str] | None = None,
) -> None:
    config = load_artifact_config(
        repo_root,
        skill,
        workspace_root=workspace_root,
        workflow_profile=workflow_profile,
        workflow_decisions=workflow_decisions,
    )
    for validator in config.validators:
        if validator not in VALIDATORS:
            raise HookCheckError("unknown_validator", f"{skill}:{validator}")


def validate_config_schema(
    repo_root: Path,
    skill: str,
    *,
    workspace_root: Path | None = None,
    workflow_profile: str = BASE_WORKFLOW_PROFILE,
    workflow_decisions: dict[str, str] | None = None,
) -> None:
    if skill != "all":
        validate_skill_config_schema(
            repo_root,
            skill,
            workspace_root=workspace_root,
            workflow_profile=workflow_profile,
            workflow_decisions=workflow_decisions,
        )
        return

    try:
        profiles = (
            configured_profile_names(load_board_config(repo_root / "board_core" / "board_config.json"))
            if workflow_profile == BASE_WORKFLOW_PROFILE
            else (workflow_profile,)
        )
    except BoardConfigError as error:
        raise HookCheckError("invalid_board_config", str(error)) from error

    try:
        for profile in profiles:
            contracts = load_repo_workflow_contracts(
                repo_root,
                workspace=workspace_root,
                profile=profile,
                workflow_decisions=workflow_decisions,
            )
            for contract in contracts.skill_contracts.values():
                for validator in contract.validators:
                    if validator not in VALIDATORS:
                        raise HookCheckError("unknown_validator", f"{contract.skill}:{validator}")
    except BoardConfigError as error:
        raise HookCheckError("invalid_board_config", str(error)) from error


def run_precheck(
    repo_root: Path,
    workspace_root: Path,
    skill: str,
    slug: str,
    *,
    workflow_profile: str = BASE_WORKFLOW_PROFILE,
    workflow_decisions: dict[str, str] | None = None,
    workflow_record: dict | None = None,
) -> tuple[int, str]:
    try:
        config = load_artifact_config(
            repo_root,
            skill,
            workspace_root=workspace_root,
            workflow_profile=workflow_profile,
            workflow_decisions=workflow_decisions,
            workflow_record=workflow_record,
        )
        validate_required_files(workspace_root, slug, config.required_inputs)
    except HookCheckError as error:
        reason = f"{skill} precheck failed for {slug}: {error.reason}"
        if error.detail:
            reason = f"{reason} ({error.detail})"
        return 1, reason
    return 0, f"PRE_SKILL_PASS skill={skill}"


def run_postcheck(
    repo_root: Path,
    workspace_root: Path,
    skill: str,
    slug: str,
    *,
    workflow_profile: str = BASE_WORKFLOW_PROFILE,
    workflow_decisions: dict[str, str] | None = None,
    workflow_record: dict | None = None,
) -> tuple[int, str]:
    try:
        config = load_artifact_config(
            repo_root,
            skill,
            workspace_root=workspace_root,
            workflow_profile=workflow_profile,
            workflow_decisions=workflow_decisions,
            workflow_record=workflow_record,
        )
        maybe_sync_plan_json_for_plan_skill(workspace_root, slug, skill, config.required_outputs)
        validate_required_files(workspace_root, slug, config.required_outputs)
        for validator in config.validators:
            if validator not in VALIDATORS:
                raise HookCheckError("unknown_validator", f"{skill}:{validator}")
    except HookCheckError as error:
        reason = f"{skill} postcheck failed for {slug}: {error.reason}"
        if error.detail:
            reason = f"{reason} ({error.detail})"
        return 1, reason

    ctx = HookContext(
        skill=skill,
        slug=slug,
        root=workspace_root,
        required_inputs=config.required_inputs,
        required_outputs=config.required_outputs,
    )
    failures = 0
    for validator in config.validators:
        failures += VALIDATORS[validator](ctx)
    if failures:
        return 1, f"POST_SKILL_FAIL skill={skill} failures={failures}"
    return 0, f"POST_SKILL_PASS skill={skill}"


def maybe_sync_plan_json_for_plan_skill(
    workspace_root: Path,
    slug: str,
    skill: str,
    required_outputs: tuple[str, ...],
) -> None:
    if skill != "autodev-plan" or "plan.json" not in required_outputs:
        return
    feature_dir = workspace_root / ".autobizdevops" / "features" / slug
    target = feature_dir / "plan.json"
    if is_nonempty(target):
        return
    plan = feature_dir / "PLAN.md"
    if not is_nonempty(plan):
        return
    data = parse_plan_markdown(read_text(plan), feature_id=slug)
    errors = validate_plan_data(data)
    if errors:
        raise HookCheckError("invalid_plan_json_sync", "; ".join(errors))
    write_plan_json(target, data)


def run_check(
    kind: str,
    repo_root: Path,
    workspace_root: Path,
    skill: str,
    slug: str,
    *,
    workflow_profile: str = BASE_WORKFLOW_PROFILE,
    workflow_decisions: dict[str, str] | None = None,
) -> int:
    if kind == "precheck":
        code, message = run_precheck(
            repo_root,
            workspace_root,
            skill,
            slug,
            workflow_profile=workflow_profile,
            workflow_decisions=workflow_decisions,
        )
    elif kind == "postcheck":
        code, message = run_postcheck(
            repo_root,
            workspace_root,
            skill,
            slug,
            workflow_profile=workflow_profile,
            workflow_decisions=workflow_decisions,
        )
    else:
        print(f"UNKNOWN_CHECK kind={kind}", file=sys.stderr)
        return 1
    print(message)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Autodev artifact checks")
    parser.add_argument("kind", choices=("precheck", "postcheck", "schema"))
    parser.add_argument("skill")
    parser.add_argument("slug", nargs="?")
    parser.add_argument("--repo-root", default=str(repo_root_from_this_file()))
    parser.add_argument("--workspace-root", default=str(Path.cwd().resolve()))
    parser.add_argument("--workflow-profile", default=BASE_WORKFLOW_PROFILE)
    parser.add_argument(
        "--workflow-decision",
        action="append",
        default=[],
        help="workflow decision in stage=enabled|skipped form; may be repeated",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    workspace_root = Path(args.workspace_root).resolve()
    workflow_decisions: dict[str, str] = {}
    for raw_decision in args.workflow_decision:
        if "=" not in raw_decision:
            print(f"SCHEMA_FAIL skill={args.skill} reason=invalid_workflow_decision detail={raw_decision}")
            return 1
        stage_id, decision = raw_decision.split("=", 1)
        workflow_decisions[stage_id.strip()] = decision.strip()

    if args.kind == "schema":
        try:
            validate_config_schema(
                repo_root,
                args.skill,
                workspace_root=workspace_root,
                workflow_profile=args.workflow_profile,
                workflow_decisions=workflow_decisions,
            )
        except HookCheckError as error:
            detail = f" detail={error.detail}" if error.detail else ""
            print(f"SCHEMA_FAIL skill={args.skill} reason={error.reason}{detail}")
            return 1
        print(f"SCHEMA_PASS skill={args.skill}")
        return 0

    if not args.slug:
        print(f"{args.kind.upper()}_FAIL skill={args.skill} reason=missing_slug_argument")
        return 1
    return run_check(
        args.kind,
        repo_root,
        workspace_root,
        args.skill,
        args.slug,
        workflow_profile=args.workflow_profile,
        workflow_decisions=workflow_decisions,
    )


if __name__ == "__main__":
    raise SystemExit(main())
