#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
autobiz 统一校验脚本
用法:
    python "${pluginPath}/skills/autobiz/hooks/biz_validate.py" prd --feature "<slug>"
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HOOKS_DIR = Path(__file__).resolve().parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

# 将项目根目录加入 sys.path，以便导入 hooks.paths
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from hooks.paths import (
    contains_workspace_argument,
    get_features_active_dir,
    get_plugin_output_workspace,
)
from board_core.artifact_paths import resolve_exact_relative_path
from board_core.contracts import BoardConfigError, SkillContract, load_record_workflow_contracts
from board_core.state_store import check_or_fix_state_sync
from hooks.implementation_scope import load_scope, scope_path

# 正式稿标题、禁用标题和必需段落的单一事实源在 prd_rules.py。
from prd_rules import (  # noqa: F401  （re-export，供外部按原名引用）
    DISCUSSION_SECTION_TITLES,
    FORBIDDEN_PRD_SECTION_TITLES,
    FORMAL_PRD_TITLE,
    FORMAL_SECTION_MAX_LEVEL,
    PENDING_MARKER,
    REQUIRED_PRD_SECTIONS,
    heading_matches,
    iter_headings,
)


BIZ_VALIDATE_WORKSPACE_ARGUMENT_ERROR = (
    "biz_validate.py 不接受 --workspace/-w；路径由 PLUGIN_WORKSPACE/PROJECT_DIR 环境变量决定。"
)


def _resolve_feature_context(feature: str, workspace: Path):
    """Load the feature's state record and workflow contracts.

    校验范围以本 Feature 契约为准：节点不在链中则整段校验跳过，
    被 drop 的输入不做存在性检查。
    Returns (record, contracts, errors).
    """
    errors: List[str] = []
    sync_result = check_or_fix_state_sync(workspace, fix=True)
    if not sync_result.state_exists:
        errors.append(f"state.json 不存在且无法从 STATE.md 迁移: {sync_result.state_json_path}")
        return None, None, errors
    if sync_result.errors:
        errors.extend(sync_result.errors)
        return None, None, errors
    record = sync_result.records.get(feature)
    if record is None and sync_result.record_errors.get(feature):
        errors.extend(sync_result.record_errors[feature])
        return None, None, errors
    if record is None:
        errors.append(f"state.json 中 Feature '{feature}' 不存在")
        return None, None, errors
    try:
        contracts = load_record_workflow_contracts(_REPO_ROOT, record, workspace=workspace)
    except BoardConfigError as exc:
        errors.append(f"Feature '{feature}' 的 workflow 契约无法解析: {exc}")
        return record, None, errors
    return record, contracts, errors


def _skill_contract_or_none(contracts, skill: str) -> Optional[SkillContract]:
    try:
        return contracts.contract_for_skill(skill)
    except BoardConfigError:
        # 节点未被选入当前工作流，或已被中途跳过。
        return None


def _check_done_checkpoint(record: Dict[str, Any], contract: SkillContract, errors: List[str]) -> None:
    expected_cp = next((cp for cp in contract.checkpoints if cp.endswith("_done")), None)
    if not expected_cp:
        return
    feature = record.get("feature", "")
    actual_cp = record.get("checkpoint")
    if actual_cp != expected_cp:
        errors.append(
            f"state.json 中 Feature '{feature}' 的 checkpoint 应为 {expected_cp}，当前为: {actual_cp or '未设置'}"
        )


def resolve_feature_dir(feature: Optional[str], workspace: Path) -> Optional[Path]:
    features_dir = get_features_active_dir(workspace)
    if feature:
        d = resolve_exact_relative_path(features_dir, feature)
        return d if d is not None and d.is_dir() else None
    # 自动检测：若只有一个子目录，则使用该目录
    if features_dir.exists():
        subs = [d for d in features_dir.iterdir() if d.is_dir()]
        if len(subs) == 1:
            return subs[0]
    return None


def exact_file(feature_dir: Path, name: str) -> Optional[Path]:
    path = resolve_exact_relative_path(feature_dir, name)
    return path if path is not None and path.is_file() else None


def _implementation_scope_errors(feature_dir: Path, content: Optional[str] = None) -> List[str]:
    """Validate the optional scope contract and its visible document marker."""

    if not scope_path(feature_dir).is_file():
        return []
    _, errors = load_scope(feature_dir, required=True)
    if content is not None and "当前实现范围" not in content:
        errors.append("文档缺少必要章节: 当前实现范围")
    return errors


def _fail(message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result = {"ok": False, "message": message}
    if details:
        result.update(details)
    return result


def _ok(message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result = {"ok": True, "message": message}
    if details:
        result.update(details)
    return result


def validate_prd(feature: Optional[str], workspace: Path) -> Dict[str, Any]:
    feature_dir = resolve_feature_dir(feature, workspace)
    if not feature_dir:
        return _fail(f"未找到 feature 目录: feature={feature}")

    slug = feature_dir.name
    record, contracts, errors = _resolve_feature_context(slug, workspace)
    if errors:
        return _fail("prd 阶段产出物校验未通过", {"feature": slug, "errors": errors})

    contract = _skill_contract_or_none(contracts, "autobiz-requirement-discuss")
    if contract is None:
        return _ok(
            "prd 校验跳过：PRD 生成节点不在当前工作流链中",
            {"feature": slug, "skipped": True},
        )

    prd_md = exact_file(feature_dir, "PRD.md")

    if prd_md is None:
        errors.append(f"PRD.md 不存在: {feature_dir / 'PRD.md'}")
    else:
        content = prd_md.read_text(encoding="utf-8")
        errors.extend(_implementation_scope_errors(feature_dir, content))
        first_line = content.splitlines()[0].strip() if content.splitlines() else ""
        if first_line != FORMAL_PRD_TITLE:
            errors.append(f"PRD.md 必须以 {FORMAL_PRD_TITLE} 开头")

        all_headings = iter_headings(content)
        headings = [heading.text for heading in all_headings]
        # 四段必须是正式章节标题；功能详情里的深层 `###### 验收标准` 不算数
        section_headings = [
            heading.text for heading in all_headings
            if heading.level <= FORMAL_SECTION_MAX_LEVEL
        ]
        bolds = re.findall(r"\*\*(.+?)\*\*", content)
        markers = section_headings + bolds
        missing = [s for s in REQUIRED_PRD_SECTIONS if not any(s in m for m in markers)]
        if missing:
            errors.append(f"PRD.md 缺少必要段落: {', '.join(missing)}")

        discussion_headings = [
            heading for heading in headings
            if heading_matches(heading, DISCUSSION_SECTION_TITLES)
        ]
        if discussion_headings:
            errors.append(f"PRD.md 不应包含讨论记录标题: {', '.join(discussion_headings)}")

        forbidden_headings = [
            heading for heading in headings
            if heading_matches(heading, FORBIDDEN_PRD_SECTION_TITLES)
        ]
        if forbidden_headings:
            errors.append(f"PRD.md 不应包含正式稿禁用标题: {', '.join(forbidden_headings)}")

        if PENDING_MARKER in content:
            errors.append(
                f"PRD.md 仍含 {PENDING_MARKER}：请逐项获取用户裁定，"
                "将具体结论写入 PRD.md 对应正文后移除标记"
            )

    _check_done_checkpoint(record, contract, errors)

    if errors:
        return _fail("prd 阶段产出物校验未通过", {"feature": slug, "errors": errors})
    return _ok("prd 阶段产出物校验通过", {"feature": slug})


def main(argv: Optional[List[str]] = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if contains_workspace_argument(raw_args):
        print(BIZ_VALIDATE_WORKSPACE_ARGUMENT_ERROR, file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(description="autobiz 统一校验脚本")
    parser.add_argument(
        "stage",
        choices=["prd"],
        help="校验阶段",
    )
    parser.add_argument("--feature", "-f", default=None, help="feature slug（如不传则自动检测）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON，不输出可读文本")
    args = parser.parse_args(raw_args)

    try:
        workspace = get_plugin_output_workspace()
    except ValueError as exc:
        print(f"biz_validate.py 校验失败: {exc}", file=sys.stderr)
        return 1

    if args.stage == "prd":
        result = validate_prd(args.feature, workspace)
    else:
        result = _fail("未知 stage")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "通过" if result["ok"] else "未通过"
        print(f"[{status}] {result['message']}")
        if "feature" in result:
            print(f"   feature: {result['feature']}")
        for err in result.get("errors", []):
            print(f"   - {err}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
