#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 PRD_DISCUSS.md 正文逐字搬运成正式 PRD.md。

用法:
    python skills/autobiz/hooks/prd_transplant.py --feature <slug> [--force] [--json]

脚本只做确定性动作：改标题、整段删讨论态章节、删讨论稿说明句；正文一字不改。
`用户故事`/`验收口径`/`验收标准`/`关键约束` 四段仍由技能追加到 PRD.md 末尾。
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HOOKS_DIR = Path(__file__).resolve().parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

# 将项目根目录加入 sys.path，以便导入 hooks.paths
_REPO_ROOT = _HOOKS_DIR.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hooks.paths import contains_workspace_argument, get_plugin_output_workspace

from biz_validate import exact_file, resolve_feature_dir
from prd_rules import PENDING_MARKER, REQUIRED_PRD_SECTIONS, plan_transplant


PRD_TRANSPLANT_WORKSPACE_ARGUMENT_ERROR = (
    "prd_transplant.py 不接受 --workspace/-w；路径由 PLUGIN_WORKSPACE/PROJECT_DIR 环境变量决定。"
)
# 与 board_config.json 中 biz.prd 输入 prd_discuss 的 degrade 文案保持一致
MISSING_DISCUSS_HINT = "无讨论稿时先与用户完成需求澄清，再生成 PRD"
NEXT_STEP_HINT = (
    "把 "
    + " / ".join(f"## {section}" for section in REQUIRED_PRD_SECTIONS)
    + " 追加到 PRD.md 末尾；正文不得改写"
)


def _fail(message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": False, "message": message}
    if details:
        result.update(details)
    return result


def _ok(message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": True, "message": message}
    if details:
        result.update(details)
    return result


def transplant(feature: Optional[str], workspace: Path, *, force: bool = False) -> Dict[str, Any]:
    feature_dir = resolve_feature_dir(feature, workspace)
    if not feature_dir:
        return _fail(
            f"未找到 feature 目录: feature={feature}, 请确认 .autobizdevops/features/ 下存在对应目录"
        )

    slug = feature_dir.name
    source = exact_file(feature_dir, "PRD_DISCUSS.md")
    if source is None:
        return _fail(
            f"PRD_DISCUSS.md 不存在: {feature_dir / 'PRD_DISCUSS.md'}；{MISSING_DISCUSS_HINT}",
            {"feature": slug},
        )

    target = feature_dir / "PRD.md"
    if target.exists() and not force:
        if exact_file(feature_dir, "PRD.md") is None:
            return _fail(
                f"目标路径已被大小写不一致的同名文件占用: {target}；"
                "请先确认该文件内容，再决定是否用 --force 覆盖",
                {"feature": slug},
            )
        return _fail(
            f"PRD.md 已存在: {target}；确认要重刷正文请加 --force",
            {"feature": slug},
        )

    result = plan_transplant(source.read_text(encoding="utf-8"))
    target.write_text(result.text, encoding="utf-8")

    return _ok(
        "PRD.md 已从 PRD_DISCUSS.md 搬运生成",
        {
            "feature": slug,
            "source": str(source),
            "target": str(target),
            "retitled": list(result.retitled) if result.retitled else None,
            "title_prepended": result.title_prepended,
            "dropped_sections": [
                {
                    "title": section.title,
                    "level": section.level,
                    "start_line": section.start_line,
                    "end_line": section.end_line,
                    "line_count": section.line_count,
                }
                for section in result.dropped_sections
            ],
            "dropped_notices": [
                {"line": line_no, "text": text} for line_no, text in result.dropped_notices
            ],
            "pending_markers": [
                {"line": line_no, "text": text} for line_no, text in result.pending_markers
            ],
            "next_step": NEXT_STEP_HINT,
        },
    )


def _print_human(result: Dict[str, Any]) -> None:
    status = "通过" if result["ok"] else "未通过"
    print(f"[{status}] {result['message']}")
    if "feature" in result:
        print(f"   feature: {result['feature']}")
    if not result["ok"]:
        return

    print(f"   源:   {result['source']}")
    print(f"   目标: {result['target']}")
    if result["retitled"]:
        print(f"   标题: {result['retitled'][0]} -> {result['retitled'][1]}")
    elif result["title_prepended"]:
        print("   标题: 源文件无 H1，已前置 # 需求正式稿")
    else:
        print("   标题: 源文件首行已是 # 需求正式稿，未改动")

    sections: List[Dict[str, Any]] = result["dropped_sections"]
    if sections:
        print("   已删除章节（行号对应 PRD_DISCUSS.md）:")
        for section in sections:
            print(
                f"     - {section['title']} "
                f"(L{section['start_line']}-L{section['end_line']}, {section['line_count']} 行)"
            )
    else:
        print("   已删除章节: 无")

    notices: List[Dict[str, Any]] = result["dropped_notices"]
    if notices:
        lines = ", ".join(f"L{item['line']}" for item in notices)
        print(f"   已删除讨论稿说明句: {len(notices)} 行 ({lines})")

    markers: List[Dict[str, Any]] = result["pending_markers"]
    if markers:
        print(f"   {PENDING_MARKER}告警: {len(markers)} 处（脚本未改动，需先与用户确认再落稿）")
        for item in markers:
            print(f"     - L{item['line']}: {item['text']}")

    print(f"   下一步: {result['next_step']}")


def main(argv: Optional[List[str]] = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if contains_workspace_argument(raw_args):
        print(PRD_TRANSPLANT_WORKSPACE_ARGUMENT_ERROR, file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(description="PRD_DISCUSS.md -> PRD.md 正文搬运脚本")
    parser.add_argument("--feature", "-f", default=None, help="feature slug（如不传则自动检测）")
    parser.add_argument("--force", action="store_true", help="PRD.md 已存在时覆盖重刷正文")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON，不输出可读文本")
    args = parser.parse_args(raw_args)

    try:
        workspace = get_plugin_output_workspace()
    except ValueError as exc:
        print(f"prd_transplant.py 搬运失败: {exc}", file=sys.stderr)
        return 1

    result = transplant(args.feature, workspace, force=args.force)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
