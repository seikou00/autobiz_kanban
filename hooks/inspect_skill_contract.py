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

from board_core.contracts import BoardConfigError, SkillContract, load_repo_workflow_contracts  # noqa: E402
from board_core.workflow_compiler import BASE_WORKFLOW_PROFILE, configured_profile_names, read_json  # noqa: E402


def _artifact_lines(title: str, artifacts: tuple, *, heading: str = "##") -> list[str]:
    lines = [f"{heading} {title}"]
    if not artifacts:
        lines.append("- 无")
        return lines

    for artifact in artifacts:
        required = "必需" if artifact.required else "可选"
        lines.append(f"- `{artifact.path}`：{artifact.label}（{required}）")
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
        *_artifact_lines("输入产物", contract.inputs),
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
) -> SkillContract:
    profiles = (workflow_profile,)
    if workspace is None and workflow_profile == BASE_WORKFLOW_PROFILE:
        profiles = _profile_names(repo_root)

    last_error: BoardConfigError | None = None
    for profile in profiles:
        try:
            contracts = load_repo_workflow_contracts(repo_root, workspace=workspace, profile=profile)
            return contracts.contract_for_skill(skill)
        except BoardConfigError as error:
            last_error = error
    raise last_error or BoardConfigError(f"unknown skill in board_config.json: {skill}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a board_config-backed skill contract")
    parser.add_argument("skill", help="skill name, e.g. autodev-plan")
    parser.add_argument("--repo-root", default=str(ROOT), help="plugin repository root")
    parser.add_argument("--workspace", help="project workspace for profile overlays")
    parser.add_argument("--workflow-profile", default=BASE_WORKFLOW_PROFILE)
    parser.add_argument("--json", action="store_true", help="emit machine-readable contract JSON")
    args = parser.parse_args(argv)

    try:
        repo_root = Path(args.repo_root).resolve()
        workspace = Path(args.workspace).resolve() if args.workspace else None
        contract = _find_contract(
            repo_root,
            skill=args.skill,
            workspace=workspace,
            workflow_profile=args.workflow_profile,
        )
    except BoardConfigError as error:
        print(f"inspect_skill_contract failed: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(contract_to_dict(contract), ensure_ascii=False, indent=2))
        return 0

    print(render_contract(contract), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
