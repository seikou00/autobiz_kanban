#!/usr/bin/env python3
"""Run Autodev YAML artifact checks."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from common import (
    ArtifactConfig,
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


PENDING_STATUS = re.compile(r"待做|进行中|in[-_ ]?progress|todo|pending", re.IGNORECASE)
VALID_VERDICT = re.compile(r"verdict\s*[:=]\s*(PASS_WITH_WARNINGS|PASS|FAIL|DEGRADED)\b", re.IGNORECASE)
TERMINAL_PASS = {"PASS", "PASS_WITH_WARNINGS"}


def repo_root_from_this_file() -> Path:
    return Path(__file__).resolve().parents[3]


def validate_design_contract(ctx: HookContext) -> int:
    design = ctx.file("design.md")
    if not is_nonempty(design):
        return fail_line(ctx, "missing_design")

    text = read_text(design)
    failures = 0
    required_sections = [
        "Proposal",
        "Behavior Specs",
        "API Decisions",
        "Data Decisions",
        "Technical Design",
        "Risks / Open Questions",
    ]
    for section in required_sections:
        if section not in text:
            failures += fail_line(ctx, "invalid_design_missing_section", f" section={section!r}")

    if not re.search(r"x-auto-no-http-api\s*:\s*(true|false)", text, re.IGNORECASE):
        failures += fail_line(ctx, "missing_design_api_marker")
    if not re.search(r"x-auto-no-sql\s*:\s*(true|false)", text, re.IGNORECASE):
        failures += fail_line(ctx, "missing_design_data_marker")
    if not re.search(r"Requirement:|REQ-[0-9]+|Scenario:", text, re.IGNORECASE):
        failures += fail_line(ctx, "missing_design_behavior_specs")
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


VALIDATORS = {
    "design_contract": validate_design_contract,
    "plan_initial_tasks": validate_plan_initial_tasks,
    "plan_finished_tasks": validate_plan_finished_tasks,
    "requirements_eval_verdict": validate_requirements_eval_verdict,
}


def validate_config_schema(repo_root: Path, skill: str) -> None:
    config = load_artifact_config(repo_root, skill)
    for validator in config.validators:
        if validator not in VALIDATORS:
            raise HookCheckError("unknown_validator", f"{skill}:{validator}")


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
