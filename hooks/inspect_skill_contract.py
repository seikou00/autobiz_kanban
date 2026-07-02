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
from board_core.state import find_feature_dir  # noqa: E402
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
        lines.append(f"- `{artifact.path}`：{artifact.label}（{required}）")
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


_GLOB_CHARS = frozenset("*?[")


def _artifact_present(feature_dir: Path, path: str) -> bool:
    """Whether an input artifact already exists (non-empty) under the feature dir.

    Glob-aware and mirrors the precheck gate's notion of "generated"
    (skills/autodev/hooks/common.py::artifact_exists): a file counts only when it
    exists with size > 0, so empty placeholders are treated as missing.
    """
    if any(char in path for char in _GLOB_CHARS):
        return any(
            match.is_file() and match.stat().st_size > 0 for match in feature_dir.glob(path)
        )
    target = feature_dir / path
    return target.is_file() and target.stat().st_size > 0


def _plain_artifact_block(index: int, artifact: ArtifactSpec, feature_dir: Path | None) -> list[str]:
    """Render one input as a single, state-resolved instruction.

    When ``feature_dir`` is known the on-disk state picks exactly one line so the
    skill never has to reason: present → how to read it (method); missing →
    what to do (required → stop and go upstream; optional → its degrade path).
    Without a feature dir (baseline preview) existence is unknown, so the method
    line is always shown. ``focus`` is intentionally dropped.
    """
    required = "必需" if artifact.required else "可选"
    extract = artifact.extract
    method = (extract.method if extract is not None else "") or "按产物内容读取并纳入上下文"

    if feature_dir is None:
        return [f"{index}. {artifact.path}（{required}）：{artifact.label}", f"   读取方式：{method}"]

    if _artifact_present(feature_dir, artifact.path):
        header = f"{index}. {artifact.path}（{required}·已生成）：{artifact.label}"
        return [header, f"   读取方式：{method}"]

    header = f"{index}. {artifact.path}（{required}·未生成）：{artifact.label}"
    if artifact.required:
        return [header, "   缺失处理：停止——必需输入未生成，回流上游补齐后再执行"]
    degrade = (extract.degrade if extract is not None else "") or "直接跳过，不影响执行"
    return [header, f"   缺失处理：{degrade}"]


def _plain_context_line(workflow_context: dict) -> str | None:
    parts: list[str] = []
    if workflow_context.get("feature"):
        parts.append(f"feature={workflow_context['feature']}")
    if workflow_context.get("workflowProfile"):
        parts.append(f"profile={workflow_context['workflowProfile']}")
    if workflow_context.get("workflowTemplate"):
        parts.append(f"template={workflow_context['workflowTemplate']}")
    decisions = workflow_context.get("workflowDecisions") or {}
    if decisions:
        parts.append("decisions=" + ",".join(f"{key}={value}" for key, value in decisions.items()))
    return "上下文：" + " ｜ ".join(parts) if parts else None


def render_contract_plain(
    contract: SkillContract,
    workflow_context: dict | None = None,
    feature_dir: Path | None = None,
) -> str:
    """Flatten the contract into a state-resolved execution checklist.

    Unlike ``--json`` (which emits overlapping inputs/sourceBundle/methodBundle
    views the skill must cross-reference and apply meta-rules to), this collapses
    each input into a single ordered instruction. When ``feature_dir`` is given,
    on-disk existence already picks method-vs-missing per input, so no judgment
    is left for the consuming skill.
    """
    checkpoints = ", ".join(contract.checkpoints) or "无"
    lines = [
        f"# {contract.skill} · 执行清单（plain）",
        f"节点：{contract.node_id}｜{contract.label}  checkpoint：{checkpoints}",
    ]
    if workflow_context:
        context_line = _plain_context_line(workflow_context)
        if context_line:
            lines.append(context_line)

    lines.append("")
    lines.append("## 输入产物（按序执行；读取方式优先于技能正文默认）")
    if contract.inputs:
        for index, artifact in enumerate(contract.inputs, start=1):
            lines.extend(_plain_artifact_block(index, artifact, feature_dir))
    else:
        lines.append("- 无")

    lines.append("")
    lines.append("## 边界（确定性）")
    lines.append("- 未在上表列出的 id 不属于本工作流：不读、不等、不索要，也不要设想。")
    lines.append("- 任一必需输入未生成即停止。")

    lines.append("")
    lines.append("## 输出产物")
    if contract.outputs:
        for artifact in contract.outputs:
            required = "必需" if artifact.required else "可选"
            lines.append(f"- {artifact.path}（{required}）：{artifact.label}")
    else:
        lines.append("- 无")

    lines.append("")
    lines.append("## Validators：" + ("，".join(contract.validators) or "无"))
    if contract.guards:
        lines.append("## Guards：" + "，".join(contract.guards))
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


def _resolve_feature_dir(workspace: Path, feature: str) -> Path:
    """Feature artifact directory (active→archive), falling back to the active
    path when absent so existence checks run and report everything missing."""
    from hooks.paths import get_feature_active_dir  # noqa: PLC0415

    return find_feature_dir(workspace, feature) or get_feature_active_dir(workspace, feature)


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
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--json", action="store_true", help="emit machine-readable contract JSON")
    output_group.add_argument(
        "--plain",
        action="store_true",
        help="emit a state-resolved execution checklist; with --feature, per-input "
        "present→method / missing→stop-or-degrade is resolved from the feature dir",
    )
    args = parser.parse_args(argv)

    workflow_context: dict = {}
    feature_dir: Path | None = None
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
            feature_dir = _resolve_feature_dir(workspace, args.feature)
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

    if args.plain:
        print(render_contract_plain(contract, workflow_context, feature_dir), end="")
        return 0

    print(render_contract(contract), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
