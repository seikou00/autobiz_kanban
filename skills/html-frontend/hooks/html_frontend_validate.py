#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
html-frontend 统一校验脚本
用法:
    python html-frontend/hooks/html_frontend_validate.py --feature <slug>
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 将项目根目录加入 sys.path，以便导入 hooks.paths
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from hooks.paths import (
    get_features_active_dir,
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


def validate_html_frontend(feature: Optional[str], workspace: Path) -> Dict[str, Any]:
    feature_dir = _get_feature_dir(feature, workspace)
    if not feature_dir:
        return _fail(f"未找到 feature 目录: feature={feature}, 请确认 .autobizdevops/features/ 下存在对应目录")

    errors: List[str] = []

    # 检查 HTML 分析输出目录
    html_analysis_dir = feature_dir / "output" / "html-analysis"
    if not html_analysis_dir.exists() or not any(html_analysis_dir.iterdir()):
        errors.append(f"HTML 分析输出目录不存在或为空: {html_analysis_dir}")

    # 检查 checkpoint 同步
    _validate_state_sync(feature_dir.name, "html_frontend_done", workspace, errors)

    if errors:
        return _fail("html-frontend 阶段产出物校验未通过", {"feature": feature_dir.name, "errors": errors})
    return _ok("html-frontend 阶段产出物校验通过", {"feature": feature_dir.name})


def main() -> int:
    parser = argparse.ArgumentParser(description="html-frontend 统一校验脚本")
    parser.add_argument(
        "stage",
        nargs="?",
        default="html_frontend",
        choices=["html_frontend"],
        help="校验阶段（当前仅支持 html_frontend）",
    )
    parser.add_argument("--feature", "-f", default=None, help="feature slug（如不传则自动检测）")
    parser.add_argument("--workspace", "-w", default=".", help="workspace 路径（默认当前目录）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON，不输出可读文本")
    args = parser.parse_args()

    workspace = get_workspace(args.workspace)

    result = validate_html_frontend(args.feature, workspace)

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
