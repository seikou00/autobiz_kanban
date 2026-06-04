#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
autobiz 统一校验脚本
用法:
    python autobiz/hooks/biz_validate.py discuss --feature <slug>
    python autobiz/hooks/biz_validate.py prd --feature <slug>
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 将项目根目录加入 sys.path，以便导入 hooks.paths
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from hooks.paths import (
    contains_workspace_argument,
    get_features_active_dir,
    get_plugin_output_workspace,
)
from board_core.state_store import check_or_fix_state_sync, state_rows_from_records


BIZ_VALIDATE_WORKSPACE_ARGUMENT_ERROR = (
    "biz_validate.py 不接受 --workspace/-w；路径由 PLUGIN_WORKSPACE/PROJECT_CODE 环境变量决定。"
)


def _validate_state_sync(feature: str, expected_cp: str, workspace: Path, errors: List[str]) -> None:
    sync_result = check_or_fix_state_sync(workspace, fix=True)
    if not sync_result.state_exists:
        errors.append(f"state.json 不存在且无法从 STATE.md 迁移: {sync_result.state_json_path}")
        return
    if sync_result.errors:
        errors.extend(sync_result.errors)
        return
    actual_cp = state_rows_from_records(sync_result.records).get(feature)
    if actual_cp != expected_cp:
        errors.append(
            f"state.json 中 Feature '{feature}' 的 checkpoint 应为 {expected_cp}，当前为: {actual_cp or '未设置'}"
        )


def _get_feature_dir(feature: Optional[str], workspace: Path) -> Optional[Path]:
    features_dir = get_features_active_dir(workspace)
    if feature:
        d = features_dir / feature
        return d if d.exists() else None
    # 自动检测：若只有一个子目录，则使用该目录
    if features_dir.exists():
        subs = [d for d in features_dir.iterdir() if d.is_dir()]
        if len(subs) == 1:
            return subs[0]
    return None


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


def validate_discuss(feature: Optional[str], workspace: Path) -> Dict[str, Any]:
    feature_dir = _get_feature_dir(feature, workspace)
    if not feature_dir:
        return _fail(f"未找到 feature 目录: feature={feature}, 请确认 .autobizdevops/features/ 下存在对应目录")

    errors: List[str] = []
    discuss_md = feature_dir / "PRD_DISCUSS.md"

    if not discuss_md.exists():
        errors.append(f"PRD_DISCUSS.md 不存在: {discuss_md}")
    else:
        content = discuss_md.read_text(encoding="utf-8")
        required_sections = ["需求摘要", "已确认结论", "问题清单", "待确认事项", "假设与风险"]
        missing = [s for s in required_sections if s not in content]
        if missing:
            errors.append(f"PRD_DISCUSS.md 缺少必要章节: {', '.join(missing)}")

    _validate_state_sync(feature_dir.name, "discuss_done", workspace, errors)

    if errors:
        return _fail("discuss 阶段产出物校验未通过", {"feature": feature_dir.name, "errors": errors})
    return _ok("discuss 阶段产出物校验通过", {"feature": feature_dir.name})


def _markdown_headings(content: str) -> List[str]:
    return re.findall(r"^#{1,6}\s+(.+?)\s*$", content, re.MULTILINE)


def _discussion_record_heading_match(content: str) -> Optional[re.Match[str]]:
    for match in re.finditer(r"^#{1,6}\s+(.+?)\s*$", content, re.MULTILINE):
        heading = match.group(1)
        if "历次讨论记录" in heading or "讨论记录" in heading:
            return match
    return None


def validate_prd(feature: Optional[str], workspace: Path) -> Dict[str, Any]:
    feature_dir = _get_feature_dir(feature, workspace)
    if not feature_dir:
        return _fail(f"未找到 feature 目录: feature={feature}")

    errors: List[str] = []
    discuss_md = feature_dir / "PRD_DISCUSS.md"
    prd_md = feature_dir / "PRD.md"
    expected_prefix: Optional[str] = None
    if not discuss_md.exists():
        errors.append(f"PRD_DISCUSS.md 不存在: {discuss_md}")
    else:
        discuss_content = discuss_md.read_text(encoding="utf-8")
        discussion_match = _discussion_record_heading_match(discuss_content)
        if discussion_match is None:
            errors.append("PRD_DISCUSS.md 缺少讨论记录标题，无法确认 PRD 原文复制区截断点")
        else:
            expected_prefix = discuss_content[:discussion_match.start()]

    if not prd_md.exists():
        errors.append(f"PRD.md 不存在: {prd_md}")
    else:
        content = prd_md.read_text(encoding="utf-8")
        suffix = content
        if expected_prefix is not None:
            if not content.startswith(expected_prefix):
                errors.append("PRD.md 原文复制区与 PRD_DISCUSS.md 截断前内容不一致")
            else:
                suffix = content[len(expected_prefix):]

        required_sections = ["用户故事", "验收口径", "验收标准", "关键约束"]
        # 追加区才是审理提炼区，复制区里的同名文字不算通过。
        suffix_headings = _markdown_headings(suffix)
        suffix_bolds = re.findall(r"\*\*(.+?)\*\*", suffix)
        suffix_markers = suffix_headings + suffix_bolds
        missing = [s for s in required_sections if not any(s in m for m in suffix_markers)]
        if missing:
            errors.append(f"PRD.md 缺少必要段落: {', '.join(missing)}")

        headings = _markdown_headings(content)
        discussion_headings = [
            heading for heading in headings
            if "历次讨论记录" in heading or "讨论记录" in heading
        ]
        if discussion_headings:
            errors.append(f"PRD.md 不应包含讨论记录标题: {', '.join(discussion_headings)}")

    _validate_state_sync(feature_dir.name, "prd_done", workspace, errors)

    if errors:
        return _fail("prd 阶段产出物校验未通过", {"feature": feature_dir.name, "errors": errors})
    return _ok("prd 阶段产出物校验通过", {"feature": feature_dir.name})


def main(argv: Optional[List[str]] = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if contains_workspace_argument(raw_args):
        print(BIZ_VALIDATE_WORKSPACE_ARGUMENT_ERROR, file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(description="autobiz 统一校验脚本")
    parser.add_argument(
        "stage",
        choices=["discuss", "prd"],
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

    if args.stage == "discuss":
        result = validate_discuss(args.feature, workspace)
    elif args.stage == "prd":
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
