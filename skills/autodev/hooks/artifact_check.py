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
    is_nonempty,
    load_artifact_config,
    read_text,
    task_count,
    task_statuses,
    validate_required_files,
)
from board_core.contracts import BoardConfigError, load_repo_workflow_contracts  # noqa: E402


PENDING_STATUS = re.compile(r"待做|进行中|in[-_ ]?progress|todo|pending", re.IGNORECASE)
VALID_VERDICT = re.compile(r"verdict\s*[:=]\s*(PASS_WITH_WARNINGS|PASS|FAIL|DEGRADED)\b", re.IGNORECASE)
TERMINAL_PASS = {"PASS", "PASS_WITH_WARNINGS"}
UNIT_TEST_VERDICT = re.compile(
    r"verdict\W*[:=]\W*(PASS_WITH_WARNINGS|PASS|FAIL|BLOCKED)\b",
    re.IGNORECASE,
)
UNIT_TEST_PASS = {"PASS", "PASS_WITH_WARNINGS"}


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
        if not re.search(r"^##\s+(ADDED|MODIFIED|REMOVED|RENAMED)\s+Requirements\b", text, re.MULTILINE):
            failures += fail_line(ctx, "invalid_spec_missing_operation_header", f" file={rel}")
        if not re.search(r"^###\s+Requirement:\s+.+", text, re.MULTILINE):
            failures += fail_line(ctx, "invalid_spec_missing_requirement", f" file={rel}")
        if not re.search(r"^####\s+Scenario:\s+.+", text, re.MULTILINE):
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

    if not re.search(r"x-auto-no-http-api\W*:\W*(true|false)", text, re.IGNORECASE):
        failures += fail_line(ctx, "missing_design_api_marker")
    if not re.search(r"x-auto-no-sql\W*:\W*(true|false)", text, re.IGNORECASE):
        failures += fail_line(ctx, "missing_design_data_marker")
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
        return fail_line(ctx, "missing_plan")
    failures = 0
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
    if not re.search(r"\|\s*Source\s*\|\s*Requirement\s*\|\s*Test\s*\|\s*Result\s*\|", content):
        failures += fail_line(ctx, "missing_coverage_matrix_table")
    if not re.search(r"\|\s*ID\s*\|\s*Classification\s*\|\s*Files Changed\s*\|", content):
        failures += fail_line(ctx, "missing_fix_attempts_table")
    return failures


VALIDATORS = {
    "proposal_contract": validate_proposal_contract,
    "specs_contract": validate_specs_contract,
    "design_contract": validate_design_contract,
    "plan_initial_tasks": validate_plan_initial_tasks,
    "plan_finished_tasks": validate_plan_finished_tasks,
    "requirements_eval_verdict": validate_requirements_eval_verdict,
    "unit_test_report_contract": validate_unit_test_report_contract,
}


def validate_skill_config_schema(repo_root: Path, skill: str) -> None:
    config = load_artifact_config(repo_root, skill)
    for validator in config.validators:
        if validator not in VALIDATORS:
            raise HookCheckError("unknown_validator", f"{skill}:{validator}")


def validate_config_schema(repo_root: Path, skill: str) -> None:
    if skill != "all":
        validate_skill_config_schema(repo_root, skill)
        return

    try:
        contracts = load_repo_workflow_contracts(repo_root)
    except BoardConfigError as error:
        raise HookCheckError("invalid_board_config", str(error)) from error

    for contract in contracts.skill_contracts.values():
        for validator in contract.validators:
            if validator not in VALIDATORS:
                raise HookCheckError("unknown_validator", f"{contract.skill}:{validator}")


def run_precheck(repo_root: Path, workspace_root: Path, skill: str, slug: str) -> tuple[int, str]:
    try:
        config = load_artifact_config(repo_root, skill)
        validate_required_files(workspace_root, slug, config.required_inputs)
    except HookCheckError as error:
        reason = f"{skill} precheck failed for {slug}: {error.reason}"
        if error.detail:
            reason = f"{reason} ({error.detail})"
        return 1, reason
    return 0, f"PRE_SKILL_PASS skill={skill}"


def run_postcheck(repo_root: Path, workspace_root: Path, skill: str, slug: str) -> tuple[int, str]:
    try:
        config = load_artifact_config(repo_root, skill)
        validate_required_files(workspace_root, slug, config.required_outputs)
        for validator in config.validators:
            if validator not in VALIDATORS:
                raise HookCheckError("unknown_validator", f"{skill}:{validator}")
    except HookCheckError as error:
        reason = f"{skill} postcheck failed for {slug}: {error.reason}"
        if error.detail:
            reason = f"{reason} ({error.detail})"
        return 1, reason

    ctx = HookContext(skill=skill, slug=slug, root=workspace_root)
    failures = 0
    for validator in config.validators:
        failures += VALIDATORS[validator](ctx)
    if failures:
        return 1, f"POST_SKILL_FAIL skill={skill} failures={failures}"
    return 0, f"POST_SKILL_PASS skill={skill}"


def run_check(kind: str, repo_root: Path, workspace_root: Path, skill: str, slug: str) -> int:
    if kind == "precheck":
        code, message = run_precheck(repo_root, workspace_root, skill, slug)
    elif kind == "postcheck":
        code, message = run_postcheck(repo_root, workspace_root, skill, slug)
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
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    workspace_root = Path(args.workspace_root).resolve()

    if args.kind == "schema":
        try:
            validate_config_schema(repo_root, args.skill)
        except HookCheckError as error:
            detail = f" detail={error.detail}" if error.detail else ""
            print(f"SCHEMA_FAIL skill={args.skill} reason={error.reason}{detail}")
            return 1
        print(f"SCHEMA_PASS skill={args.skill}")
        return 0

    if not args.slug:
        print(f"{args.kind.upper()}_FAIL skill={args.skill} reason=missing_slug_argument")
        return 1
    return run_check(args.kind, repo_root, workspace_root, args.skill, args.slug)


if __name__ == "__main__":
    raise SystemExit(main())
