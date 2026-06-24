#!/usr/bin/env python3
"""Run Autodev artifact checks from board_config.json."""

from __future__ import annotations

import argparse
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
from board_core.contracts import BoardConfigError, load_board_config, load_repo_workflow_contracts  # noqa: E402
from board_core.workflow_compiler import BASE_WORKFLOW_PROFILE, configured_profile_names  # noqa: E402


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
STABLE_TASK_HEADING_RE = re.compile(r"^###\s+Task\s+\[T\d{3}\]\s*:\s+.+$", re.MULTILINE)
LEGACY_TASK_HEADING_RE = re.compile(r"^###\s+\d+[.)]\s+.+$", re.MULTILINE)
TASK_BLOCK_RE = re.compile(
    r"^###\s+Task\s+\[(T\d{3})\]\s*:\s+.+?(?=^###\s+Task\s+\[T\d{3}\]\s*:\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)
OLD_PLAN_TASK_BLOCK_RE = re.compile(r"^###\s+\d+[.)]\s+.+?(?=^###\s+\d+[.)]\s+|\Z)", re.MULTILINE | re.DOTALL)


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


def _plan_is_legacy_format(plan_text: str) -> bool:
    return bool(LEGACY_TASK_HEADING_RE.search(plan_text)) and not STABLE_TASK_HEADING_RE.search(plan_text)


def _task_field(block_text: str, label: str) -> str | None:
    match = re.search(rf"^\s*-\s*\*\*{re.escape(label)}[:：]\*\*\s*(.+)$", block_text, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


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


def validate_plan_finished_tasks(ctx: HookContext) -> int:
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
        evidence_refs = _task_field(block_text, "证据依据")
        if spec_refs is None:
            failures += fail_line(ctx, "missing_task_spec_refs", f" task={task_id}")
            spec_refs = ""
        if design_refs is None:
            failures += fail_line(ctx, "missing_task_design_refs", f" task={task_id}")
            design_refs = ""
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

        api_refs = re.findall(r"\bAPI-\d{3}\b", design_refs)
        data_refs = re.findall(r"\bDATA-\d{3}\b", design_refs)
        decision_refs = re.findall(r"\bD-\d{3}\b", design_refs)
        if not no_http_api and not api_refs:
            failures += fail_line(ctx, "missing_task_api_id", f" task={task_id}")
        if not no_sql and not data_refs:
            failures += fail_line(ctx, "missing_task_data_id", f" task={task_id}")
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
    "plan_initial_tasks": validate_plan_initial_tasks,
    "plan_finished_tasks": validate_plan_finished_tasks,
    "requirements_eval_verdict": validate_requirements_eval_verdict,
    "unit_test_report_contract": validate_unit_test_report_contract,
    "e2e_report_contract": validate_e2e_report_contract,
    "verify_report_contract": validate_verify_report_contract,
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
