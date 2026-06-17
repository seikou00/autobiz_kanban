#!/usr/bin/env python3
"""Maintain SKILL.md runtime contract lookup hints for Biz/Dev/Ops node skills."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.contracts import BoardConfigError, SkillContract, WorkflowContracts, load_board_config, load_repo_workflow_contracts  # noqa: E402
from board_core.workflow_compiler import configured_profile_names  # noqa: E402
from hooks.check_skill_artifact_drift import (  # noqa: E402
    check_contracts_for_drift,
    is_managed_contract,
    managed_contracts,
    skill_file_for_contract,
)


LEGACY_CONTRACT_BEGIN_MARKER = "<!-- AUTOBIZDEVOPS_CONTRACT:BEGIN -->"
LEGACY_CONTRACT_END_MARKER = "<!-- AUTOBIZDEVOPS_CONTRACT:END -->"
LEGACY_RULES_BEGIN_MARKER = "<!-- AUTOBIZDEVOPS_ARTIFACT_RULES:BEGIN -->"
LEGACY_RULES_END_MARKER = "<!-- AUTOBIZDEVOPS_ARTIFACT_RULES:END -->"
HINT_BEGIN_MARKER = "<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->"
HINT_END_MARKER = "<!-- AUTODEV_RUNTIME_CONTRACT:END -->"
LEGACY_FEATURE_DIR_LINE = "工作目录 = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}/"
FEATURE_DIR_LINE = "FEATURE_DIR = {PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}"


@dataclass(frozen=True)
class SyncResult:
    skill: str
    path: Path
    changed: bool
    error: str = ""


def runtime_contract_hint_block(contract: SkillContract) -> str:
    return "\n".join(
        [
            HINT_BEGIN_MARKER,
            "## 流程契约（Source Bundle + Method Bundle）",
            "",
            "当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 "
            "`{PLUGIN_ROOT}/board_core/board_config.json` 的编译结果为唯一事实来源；"
            "本文档不维护产物清单，不要依赖文中写死的文件名。",
            "进入执行前，先取当前 Feature 的契约（一次返回两个 bundle）：",
            "",
            "```bash",
            f'python "{{PLUGIN_ROOT}}/hooks/inspect_skill_contract.py" {contract.skill} --feature "{{FEATURE_ID}}" --json',
            "```",
            "",
            "- **Source Bundle（读什么）**：`sourceBundle`/`required_inputs` 列出本 Feature 当前工作流下"
            "要读取的真实产物文件；按清单读原件。",
            "- **Method Bundle（怎么读）**：每个 input 的 `extract` 给出读取重点（focus）、"
            "读取方式（method）和缺失降级（degrade）。",
            "- **方法优先**：每个 input 的 `extract.method` 是它在场时的专属指令，优先于技能正文的通用默认。",
            "- **停止条件**：仅当 `required_inputs` 中的产物缺失时停止。",
            "- **不列即不存在**：bundle 未列出的 id 不属于本工作流，一律不予考虑——不读、不等、不索要，"
            "也不要为其设想任何分支。",
            "- **降级语义**：`required: false` 的输入缺失时按其 `extract.degrade` 继续，不要因缺失而停止。",
            "",
            "无 `FEATURE_ID` 时可省略 `--feature` 查看基线契约。",
            HINT_END_MARKER,
            "",
        ]
    )


def replace_marked_block(
    content: str,
    block: str,
    *,
    begin_marker: str = HINT_BEGIN_MARKER,
    end_marker: str = HINT_END_MARKER,
) -> tuple[str, bool]:
    begin_count = content.count(begin_marker)
    end_count = content.count(end_marker)
    if begin_count != end_count:
        raise ValueError("runtime contract hint marker count mismatch")
    if begin_count == 0:
        return content, False
    if begin_count > 1:
        raise ValueError("multiple runtime contract hint blocks found")

    begin_index = content.index(begin_marker)
    end_index = content.index(end_marker, begin_index)
    end_index += len(end_marker)
    if end_index < len(content) and content[end_index : end_index + 1] == "\n":
        end_index += 1
    return content[:begin_index] + block + content[end_index:], True


def remove_marked_block(content: str, *, begin_marker: str, end_marker: str, label: str) -> tuple[str, bool]:
    begin_count = content.count(begin_marker)
    end_count = content.count(end_marker)
    if begin_count != end_count:
        raise ValueError(f"{label} marker count mismatch")
    if begin_count == 0:
        return content, False
    if begin_count > 1:
        raise ValueError(f"multiple {label} blocks found")

    begin_index = content.index(begin_marker)
    end_index = content.index(end_marker, begin_index)
    end_index += len(end_marker)
    if end_index < len(content) and content[end_index : end_index + 1] == "\n":
        end_index += 1
    return content[:begin_index].rstrip() + "\n" + content[end_index:], True


def remove_legacy_contract_blocks(content: str) -> str:
    content, _ = remove_marked_block(
        content,
        begin_marker=LEGACY_CONTRACT_BEGIN_MARKER,
        end_marker=LEGACY_CONTRACT_END_MARKER,
        label="legacy contract",
    )
    content, _ = remove_marked_block(
        content,
        begin_marker=LEGACY_RULES_BEGIN_MARKER,
        end_marker=LEGACY_RULES_END_MARKER,
        label="legacy artifact rules",
    )
    return content


def replace_old_contract_section(content: str, block: str) -> tuple[str, bool]:
    headings = ("## 流程契约来源", "## 流程契约（由 board_config.json 生成）")
    starts = [content.find(heading) for heading in headings if content.find(heading) >= 0]
    if not starts:
        return content, False

    start = min(starts)
    next_heading = content.find("\n# ", start + 1)
    end = len(content) if next_heading < 0 else next_heading + 1
    return content[:start] + block + content[end:], True


def normalize_legacy_feature_dir_line(content: str) -> str:
    return content.replace(LEGACY_FEATURE_DIR_LINE, FEATURE_DIR_LINE)


def insert_after_feature_dir_block(content: str, block: str) -> tuple[str, bool]:
    feature_dir_index = content.find(FEATURE_DIR_LINE)
    if feature_dir_index < 0:
        return content, False

    fence_end = content.find("```", feature_dir_index)
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


def sync_skill_content(content: str, contract: SkillContract) -> str:
    content = remove_legacy_contract_blocks(content)
    content = normalize_legacy_feature_dir_line(content)
    block = runtime_contract_hint_block(contract)
    updated, replaced = replace_marked_block(content, block)
    if replaced:
        return updated

    updated, replaced = replace_old_contract_section(content, block)
    if replaced:
        return updated

    updated, inserted = insert_after_feature_dir_block(content, block)
    if inserted:
        return updated

    return insert_after_frontmatter(content, block)


def profile_contracts(repo_root: Path) -> list[WorkflowContracts]:
    profiles = configured_profile_names(load_board_config(repo_root / "board_core" / "board_config.json"))
    return [
        load_repo_workflow_contracts(repo_root, profile=profile)
        for profile in profiles
    ]


def unique_contracts(contracts: list[SkillContract]) -> list[SkillContract]:
    result: list[SkillContract] = []
    seen: set[str] = set()
    for contract in contracts:
        if contract.skill in seen:
            continue
        seen.add(contract.skill)
        result.append(contract)
    return result


def selected_contracts(contract_sets: list[WorkflowContracts], skill: str | None) -> list[SkillContract]:
    if skill is None:
        return unique_contracts([
            contract
            for contracts in contract_sets
            for contract in managed_contracts(contracts)
        ])

    last_error: BoardConfigError | None = None
    for contracts in contract_sets:
        try:
            contract = contracts.contract_for_skill(skill)
        except BoardConfigError as error:
            last_error = error
            continue
        if not is_managed_contract(contract):
            raise BoardConfigError(f"skill is outside static compile scope: {skill}")
        return [contract]
    raise last_error or BoardConfigError(f"unknown skill in board_config.json: {skill}")


def sync_contract_hint(repo_root: Path, contract: SkillContract, *, write: bool) -> SyncResult:
    path = skill_file_for_contract(repo_root, contract)
    if not path.is_file():
        return SyncResult(contract.skill, path, changed=False, error=f"SKILL.md not found: {path}")

    content = path.read_text(encoding="utf-8")
    try:
        compiled = sync_skill_content(content, contract)
    except ValueError as error:
        return SyncResult(contract.skill, path, changed=False, error=str(error))

    changed = compiled != content
    if write and changed:
        path.write_text(compiled, encoding="utf-8")
    return SyncResult(contract.skill, path, changed=changed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync runtime contract lookup hints for Biz/Dev/Ops node skills")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="check generated contract blocks without writing")
    mode.add_argument("--write", action="store_true", help="write generated contract blocks")
    parser.add_argument("--skill", help="sync a single node skill hint, e.g. autodev-plan / autobiz-prd-generate")
    parser.add_argument("--repo-root", default=str(ROOT), help="plugin repository root")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    try:
        selected = selected_contracts(profile_contracts(repo_root), args.skill)
    except BoardConfigError as error:
        print(f"SKILL_CONTRACT_HINTS_FAIL {error}", file=sys.stderr)
        return 1

    results = [sync_contract_hint(repo_root, contract, write=args.write) for contract in selected]
    errors = [result for result in results if result.error]
    if errors:
        for result in errors:
            print(f"SKILL_CONTRACT_HINTS_FAIL skill={result.skill} path={result.path} reason={result.error}", file=sys.stderr)
        return 1

    drift_findings = check_contracts_for_drift(repo_root, selected) if args.check else []
    changed = [result for result in results if result.changed]
    if not changed and not drift_findings:
        print("SKILL_CONTRACT_HINTS_UP_TO_DATE")
        return 0

    if args.check:
        for result in changed:
            print(f"STALE_HINT skill={result.skill} path={result.path}")
        for finding in drift_findings:
            print(finding.format(), file=sys.stderr)
        return 1

    for result in changed:
        print(f"WROTE skill={result.skill} path={result.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
