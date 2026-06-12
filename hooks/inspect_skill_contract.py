#!/usr/bin/env python3
"""Render a skill contract from board_config.json as Markdown."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.contracts import (  # noqa: E402
    BoardConfigError,
    SkillContract,
    load_record_workflow_contracts,
    load_repo_workflow_contracts,
)
from board_core.state_store import load_state_json_records_result  # noqa: E402
from board_core.workflow_compiler import (  # noqa: E402
    BASE_WORKFLOW_PROFILE,
    BASE_WORKFLOW_TEMPLATE,
    WorkflowCompileError,
    configured_profile_names,
    normalize_workflow_decisions,
    read_json,
)


def _artifact_lines(title: str, artifacts: tuple, *, heading: str = "##") -> list[str]:
    lines = [f"{heading} {title}"]
    if not artifacts:
        lines.append("- 无")
        return lines

    for artifact in artifacts:
        required = "必需" if artifact.required else "可选"
        qualifier = f"{required}，外部输入" if artifact.external else required
        lines.append(f"- `{artifact.path}`：{artifact.label}（{qualifier}）")
        extract = getattr(artifact, "extract", None)
        if extract is None:
            continue
        if extract.focus:
            lines.append(f"  - 读取重点: {'；'.join(extract.focus)}")
        if extract.method:
            lines.append(f"  - 读取方式: {extract.method}")
        if extract.degrade:
            lines.append(f"  - 缺失降级: {extract.degrade}")
    return lines


def render_contract(contract: SkillContract) -> str:
    lines = [
        f"# {contract.skill} 流程契约",
        "",
        f"- **节点:** {contract.node_id}",
        f"- **阶段:** {contract.label}",
        f"- **分组:** {contract.group}",
        f"- **Checkpoints:** {', '.join(f'`{item}`' for item in contract.checkpoints) or '无'}",
        "",
        *_artifact_lines("输入产物（Source Bundle + Method Bundle）", contract.inputs),
        "",
        *_artifact_lines("输出产物", contract.outputs),
        "",
        "## Validators",
    ]
    if contract.validators:
        lines.extend(f"- `{validator}`" for validator in contract.validators)
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def contract_to_dict(contract: SkillContract) -> dict:
    return {
        "node_id": contract.node_id,
        "label": contract.label,
        "group": contract.group,
        "skill": contract.skill,
        "checkpoints": list(contract.checkpoints),
        "inputs": [asdict(artifact) for artifact in contract.inputs],
        "outputs": [asdict(artifact) for artifact in contract.outputs],
        "required_inputs": list(contract.required_inputs),
        "required_outputs": list(contract.required_outputs),
        "sourceBundle": [
            {
                "path": artifact.path,
                "label": artifact.label,
                "required": artifact.required,
                "external": artifact.external,
            }
            for artifact in contract.inputs
        ],
        "methodBundle": [
            {
                "path": artifact.path,
                "extract": asdict(artifact.extract) if artifact.extract is not None else None,
            }
            for artifact in contract.inputs
        ],
        "validators": list(contract.validators),
        "guards": list(contract.guards),
    }


def _profile_names(repo_root: Path) -> tuple[str, ...]:
    return configured_profile_names(read_json(repo_root / "board_core" / "board_config.json"))


def _find_contract(
    repo_root: Path,
    *,
    skill: str,
    workspace: Path | None,
    workflow_profile: str,
    workflow_decisions: dict[str, str],
) -> SkillContract:
    profiles = (workflow_profile,)
    if workspace is None and workflow_profile == BASE_WORKFLOW_PROFILE and not workflow_decisions:
        profiles = _profile_names(repo_root)

    last_error: BoardConfigError | None = None
    for profile in profiles:
        try:
            contracts = load_repo_workflow_contracts(
                repo_root,
                workspace=workspace,
                profile=profile,
                workflow_decisions=workflow_decisions,
            )
            return contracts.contract_for_skill(skill)
        except BoardConfigError as error:
            last_error = error
    raise last_error or BoardConfigError(f"unknown skill in board_config.json: {skill}")


def _resolve_feature_workspace(workspace_arg: str | None) -> Path:
    if workspace_arg:
        return Path(workspace_arg).resolve()
    from hooks.paths import get_plugin_output_workspace  # noqa: PLC0415

    return get_plugin_output_workspace()


def _find_feature_contract(
    repo_root: Path,
    *,
    skill: str,
    feature: str,
    workspace: Path,
) -> tuple[SkillContract, dict]:
    result = load_state_json_records_result(workspace)
    if not result.exists:
        raise BoardConfigError(f"state.json 未找到: {workspace}")
    if result.errors:
        raise BoardConfigError("; ".join(result.errors))
    record = result.records.get(feature)
    if record is None:
        raise BoardConfigError(f"feature '{feature}' 未在 state.json 中找到")
    contracts = load_record_workflow_contracts(repo_root, record, workspace=workspace)
    workflow_context = {
        "feature": feature,
        "workflowProfile": record.get("workflowProfile", BASE_WORKFLOW_PROFILE),
        "workflowTemplate": record.get("workflowTemplate", BASE_WORKFLOW_TEMPLATE),
        "workflowDecisions": record.get("workflowDecisions", {}),
    }
    if record.get("workflowNodes"):
        workflow_context["workflowNodes"] = record.get("workflowNodes")
    if record.get("workflowSkippedNodes"):
        workflow_context["workflowSkippedNodes"] = record.get("workflowSkippedNodes")
    return contracts.contract_for_skill(skill), workflow_context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a board_config-backed skill contract")
    parser.add_argument("skill", help="skill name, e.g. autodev-plan")
    parser.add_argument("--repo-root", default=str(ROOT), help="plugin repository root")
    parser.add_argument("--workspace", help="project workspace for profile overlays")
    parser.add_argument(
        "--feature",
        default=None,
        help="feature slug; resolves workflow profile/template/decisions from state.json",
    )
    parser.add_argument("--workflow-profile", default=BASE_WORKFLOW_PROFILE)
    parser.add_argument(
        "--workflow-decision",
        action="append",
        default=[],
        help="workflow decision in stage=enabled|skipped form; may be repeated",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable contract JSON")
    args = parser.parse_args(argv)

    workflow_context: dict = {}
    try:
        repo_root = Path(args.repo_root).resolve()
        if args.feature is not None:
            if args.workflow_profile != BASE_WORKFLOW_PROFILE or args.workflow_decision:
                raise BoardConfigError("--feature 与 --workflow-profile/--workflow-decision 不能同时使用")
            workspace = _resolve_feature_workspace(args.workspace)
            contract, workflow_context = _find_feature_contract(
                repo_root,
                skill=args.skill,
                feature=args.feature,
                workspace=workspace,
            )
        else:
            workspace = Path(args.workspace).resolve() if args.workspace else None
            workflow_decisions = {}
            for raw_decision in args.workflow_decision:
                if "=" not in raw_decision:
                    raise BoardConfigError(f"invalid workflow decision: {raw_decision}")
                stage_id, decision = raw_decision.split("=", 1)
                workflow_decisions[stage_id.strip()] = decision.strip()
            workflow_decisions = normalize_workflow_decisions(workflow_decisions)
            contract = _find_contract(
                repo_root,
                skill=args.skill,
                workspace=workspace,
                workflow_profile=args.workflow_profile,
                workflow_decisions=workflow_decisions,
            )
    except WorkflowCompileError as error:
        print(f"inspect_skill_contract failed: {error}", file=sys.stderr)
        return 1
    except BoardConfigError as error:
        print(f"inspect_skill_contract failed: {error}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"inspect_skill_contract failed: {error}", file=sys.stderr)
        return 1

    if args.json:
        payload = contract_to_dict(contract)
        if workflow_context:
            payload["workflow"] = workflow_context
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(render_contract(contract), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
