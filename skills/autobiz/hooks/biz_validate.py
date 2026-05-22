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
    get_features_active_dir,
    get_project_md_path,
    get_state_md_path,
    get_workspace,
)


def _extract_state_checkpoint(state_md_content: str, feature: str) -> Optional[str]:
    """从 STATE.md 的 Feature 进度表格中提取指定 feature 的 checkpoint。"""
    for line in state_md_content.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if cells[0] in ("Feature", "") or set(cells[0]) <= {"-", ":", "|"}:
            continue
        if cells[0] == feature:
            return cells[2] if cells[2] else None
    return None


def _validate_state_sync(feature: str, expected_cp: str, workspace: Path, errors: List[str]) -> None:
    state_md = get_state_md_path(workspace)
    if not state_md.exists():
        errors.append(f"STATE.md 不存在: {state_md}")
        return
    content = state_md.read_text(encoding="utf-8")
    actual_cp = _extract_state_checkpoint(content, feature)
    if actual_cp != expected_cp:
        errors.append(
            f"STATE.md 中 Feature '{feature}' 的 checkpoint 应为 {expected_cp}，当前为: {actual_cp or '未设置'}"
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


def validate_prd(feature: Optional[str], workspace: Path) -> Dict[str, Any]:
    feature_dir = _get_feature_dir(feature, workspace)
    if not feature_dir:
        return _fail(f"未找到 feature 目录: feature={feature}")

    errors: List[str] = []
    discuss_md = feature_dir / "PRD_DISCUSS.md"
    prd_md = feature_dir / "PRD.md"
    if not discuss_md.exists():
        errors.append(f"PRD_DISCUSS.md 不存在: {discuss_md}")

    if not prd_md.exists():
        errors.append(f"PRD.md 不存在: {prd_md}")
    else:
        content = prd_md.read_text(encoding="utf-8")
        required_sections = [
            "目标", "核心价值", "具体要求", "非目标",
            "边界说明", "验收标准", "关键约束", "风险与假设",
        ]
        # 检查 Markdown 标题或加粗文本
        headings = re.findall(r"^#{1,3}\s+(.+)$", content, re.MULTILINE)
        bolds = re.findall(r"\*\*(.+?)\*\*", content)
        all_markers = headings + bolds
        missing = [s for s in required_sections if not any(s in m for m in all_markers)]
        if missing:
            errors.append(f"PRD.md 缺少必要段落: {', '.join(missing)}")

        # 粗略检查开放式问题残留
        open_indicators = ["开放式问题", "原始追问", "未决候选方案", "待讨论"]
        found = [i for i in open_indicators if i in content]
        if found:
            errors.append(f"PRD.md 可能残留开放式内容标记: {', '.join(found)}")

    _validate_state_sync(feature_dir.name, "prd_done", workspace, errors)

    if errors:
        return _fail("prd 阶段产出物校验未通过", {"feature": feature_dir.name, "errors": errors})
    return _ok("prd 阶段产出物校验通过", {"feature": feature_dir.name})


def main() -> int:
    parser = argparse.ArgumentParser(description="autobiz 统一校验脚本")
    parser.add_argument(
        "stage",
        choices=["discuss", "prd"],
        help="校验阶段",
    )
    parser.add_argument("--feature", "-f", default=None, help="feature slug（如不传则自动检测）")
    parser.add_argument("--workspace", "-w", default=".", help="workspace 路径（默认当前目录）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON，不输出可读文本")
    args = parser.parse_args()

    workspace = get_workspace(args.workspace)

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
