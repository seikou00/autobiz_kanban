#!/usr/bin/env python3
"""Render a skill contract from board_config.json as Markdown."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.contracts import BoardConfigError, SkillContract, load_repo_workflow_contracts  # noqa: E402


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


def render_compiled_contract(contract: SkillContract) -> str:
    """Render the SKILL.md generated contract block body without boundary markers."""
    checkpoints = ", ".join(f"`{item}`" for item in contract.checkpoints) or "无"
    lines = [
        "## 流程契约（由 board_config.json 生成）",
        "",
        "本区块由 `board_core/board_config.json` 静态编译生成，请勿手工修改；"
        "修改流程契约后运行 `python \"{PLUGIN_DIR}/hooks/compile_skill_contracts.py\" --write` 重新生成。",
        "",
        f"- **唯一事实来源:** `{{PLUGIN_DIR}}/board_core/board_config.json` 中 `skill: \"{contract.skill}\"` 的节点。",
        f"- **节点:** `{contract.node_id}`",
        f"- **阶段:** {contract.label}",
        f"- **分组:** {contract.group}",
        f"- **Checkpoints:** {checkpoints}",
        "",
        *_artifact_lines("输入产物", contract.inputs, heading="###"),
        "",
        *_artifact_lines("输出产物", contract.outputs, heading="###"),
        "",
        "### Validators",
    ]
    if contract.validators:
        lines.extend(f"- `{validator}`" for validator in contract.validators)
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a board_config-backed skill contract")
    parser.add_argument("skill", help="skill name, e.g. autodev-plan")
    parser.add_argument("--repo-root", default=str(ROOT), help="plugin repository root")
    args = parser.parse_args(argv)

    try:
        contracts = load_repo_workflow_contracts(Path(args.repo_root).resolve())
        contract = contracts.contract_for_skill(args.skill)
    except BoardConfigError as error:
        print(f"render_skill_contract failed: {error}", file=sys.stderr)
        return 1

    print(render_contract(contract), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
