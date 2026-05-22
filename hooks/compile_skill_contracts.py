#!/usr/bin/env python3
"""Compile board_config.json workflow contracts into Autodev SKILL.md files."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.contracts import BoardConfigError, SkillContract, WorkflowContracts, load_repo_workflow_contracts  # noqa: E402
from render_skill_contract import render_compiled_contract  # noqa: E402


BEGIN_MARKER = "<!-- AUTOBIZDEVOPS_CONTRACT:BEGIN -->"
END_MARKER = "<!-- AUTOBIZDEVOPS_CONTRACT:END -->"


@dataclass(frozen=True)
class CompileResult:
    skill: str
    path: Path
    changed: bool
    error: str = ""


def skill_file_for_contract(repo_root: Path, contract: SkillContract) -> Path:
    return repo_root / "skills" / "autodev" / contract.skill / "SKILL.md"


def compiled_block(contract: SkillContract) -> str:
    body = render_compiled_contract(contract).rstrip()
    return f"{BEGIN_MARKER}\n{body}\n{END_MARKER}\n"


def replace_marked_block(content: str, block: str) -> tuple[str, bool]:
    begin_count = content.count(BEGIN_MARKER)
    end_count = content.count(END_MARKER)
    if begin_count != end_count:
        raise ValueError("compiled contract marker count mismatch")
    if begin_count == 0:
        return content, False
    if begin_count > 1:
        raise ValueError("multiple compiled contract blocks found")

    begin_index = content.index(BEGIN_MARKER)
    end_index = content.index(END_MARKER, begin_index)
    end_index += len(END_MARKER)
    if end_index < len(content) and content[end_index : end_index + 1] == "\n":
        end_index += 1
    return content[:begin_index] + block + content[end_index:], True


def replace_old_contract_section(content: str, block: str) -> tuple[str, bool]:
    pattern = re.compile(r"^## 流程契约来源\s*\n.*?(?=^# |\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(content)
    if not match:
        return content, False

    replacement = block + "\n"
    return content[: match.start()] + replacement + content[match.end() :], True


def insert_after_workspace_block(content: str, block: str) -> tuple[str, bool]:
    workspace_index = content.find("工作目录 = ")
    if workspace_index < 0:
        return content, False

    fence_end = content.find("```", workspace_index)
    if fence_end < 0:
        return content, False

    insert_at = content.find("\n", fence_end)
    if insert_at < 0:
        insert_at = len(content)
        suffix = ""
    else:
        insert_at += 1
        suffix = content[insert_at:]

    return content[:insert_at] + "\n" + block + "\n" + suffix, True


def insert_after_frontmatter(content: str, block: str) -> str:
    if not content.startswith("---\n"):
        return block + "\n" + content

    frontmatter_end = content.find("\n---\n", 4)
    if frontmatter_end < 0:
        return block + "\n" + content
    insert_at = frontmatter_end + len("\n---\n")
    return content[:insert_at] + "\n" + block + "\n" + content[insert_at:]


def compile_skill_content(content: str, contract: SkillContract) -> str:
    block = compiled_block(contract)
    updated, replaced = replace_marked_block(content, block)
    if replaced:
        return updated

    updated, replaced = replace_old_contract_section(content, block)
    if replaced:
        return updated

    updated, inserted = insert_after_workspace_block(content, block)
    if inserted:
        return updated

    return insert_after_frontmatter(content, block)


def is_autodev_contract(contract: SkillContract) -> bool:
    return contract.group == "Dev" and contract.skill.startswith("autodev-")


def autodev_contracts(contracts: WorkflowContracts) -> list[SkillContract]:
    result: list[SkillContract] = []
    for node in sorted(contracts.nodes, key=lambda item: item.get("order", 0)):
        skill = node.get("skill")
        if not isinstance(skill, str):
            continue
        contract = contracts.skill_contracts.get(skill)
        if contract and is_autodev_contract(contract):
            result.append(contract)
    return result


def selected_contracts(contracts: WorkflowContracts, skill: str | None) -> list[SkillContract]:
    if skill is None:
        return autodev_contracts(contracts)

    contract = contracts.contract_for_skill(skill)
    if not is_autodev_contract(contract):
        raise BoardConfigError(f"skill is outside static compile scope: {skill}")
    return [contract]


def compile_contract(repo_root: Path, contract: SkillContract, *, write: bool) -> CompileResult:
    path = skill_file_for_contract(repo_root, contract)
    if not path.is_file():
        return CompileResult(contract.skill, path, changed=False, error=f"SKILL.md not found: {path}")

    content = path.read_text(encoding="utf-8")
    try:
        compiled = compile_skill_content(content, contract)
    except ValueError as error:
        return CompileResult(contract.skill, path, changed=False, error=str(error))

    changed = compiled != content
    if write and changed:
        path.write_text(compiled, encoding="utf-8")
    return CompileResult(contract.skill, path, changed=changed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile board_config contracts into Autodev SKILL.md files")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="check generated contract blocks without writing")
    mode.add_argument("--write", action="store_true", help="write generated contract blocks")
    parser.add_argument("--skill", help="compile a single Autodev skill, e.g. autodev-plan")
    parser.add_argument("--repo-root", default=str(ROOT), help="plugin repository root")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    try:
        contracts = load_repo_workflow_contracts(repo_root)
        selected = selected_contracts(contracts, args.skill)
    except BoardConfigError as error:
        print(f"SKILL_CONTRACTS_FAIL {error}", file=sys.stderr)
        return 1

    results = [compile_contract(repo_root, contract, write=args.write) for contract in selected]
    errors = [result for result in results if result.error]
    if errors:
        for result in errors:
            print(f"SKILL_CONTRACTS_FAIL skill={result.skill} path={result.path} reason={result.error}", file=sys.stderr)
        return 1

    changed = [result for result in results if result.changed]
    if not changed:
        print("SKILL_CONTRACTS_UP_TO_DATE")
        return 0

    if args.check:
        for result in changed:
            print(f"STALE skill={result.skill} path={result.path}")
        return 1

    for result in changed:
        print(f"WROTE skill={result.skill} path={result.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
