#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按阶段渲染回检协议。

正文的唯一来源是 ``skills/references/review-protocol.md``；本脚本只做筛选与拼接，
不含任何协议文字。这样三个 SKILL.md 的回检段不再复制通用措辞，改一处即三处同步。

与 2026-07-02 删除的 ``sync_skill_contract_hints.py`` 的区别：那个是构建期生成器，
会把文本块写回 SKILL.md 从而产生漂移；本脚本是运行时渲染，不写任何文件，
与仍在使用的 ``inspect_skill_contract.py --plain`` 同一形态。

用法::

    python hooks/render_review_protocol.py --stage dev.specs
    python hooks/render_review_protocol.py --stage dev.code --source <PATH>
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "skills" / "references" / "review-protocol.md"

STAGES = ("dev.specs", "dev.plan", "dev.code")
ALL_STAGES = "*"

# <!-- section: 名称 | stages: dev.specs,dev.plan -->
SECTION_MARKER = re.compile(
    r"^<!--\s*section:\s*(?P<name>[^|]+?)\s*\|\s*stages:\s*(?P<stages>[^>]+?)\s*-->\s*$"
)


class ReviewProtocolError(Exception):
    """协议文件结构错误。错误信息里必须带修复方式。"""


def _parse_stages(raw: str, *, name: str, lineno: int) -> tuple[str, ...]:
    if raw.strip() == ALL_STAGES:
        return (ALL_STAGES,)
    stages = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not stages:
        raise ReviewProtocolError(
            f"第 {lineno} 行小节「{name}」的 stages 为空。"
            f"修复：写 `stages: *` 表示通用，或写 {'/'.join(STAGES)} 中的一个或多个（逗号分隔）。"
        )
    unknown = [stage for stage in stages if stage not in STAGES]
    if unknown:
        raise ReviewProtocolError(
            f"第 {lineno} 行小节「{name}」引用了未知阶段 {', '.join(unknown)}。"
            f"修复：只能使用 {', '.join(STAGES)} 或 `*`。"
        )
    return stages


def parse_sections(text: str) -> list[dict]:
    """把协议文件切成有序小节。标记之前的内容是编辑说明，不参与渲染。"""
    sections: list[dict] = []
    current: dict | None = None

    for lineno, line in enumerate(text.splitlines(), start=1):
        matched = SECTION_MARKER.match(line)
        if matched:
            name = matched.group("name").strip()
            current = {
                "name": name,
                "stages": _parse_stages(matched.group("stages"), name=name, lineno=lineno),
                "lines": [],
                "lineno": lineno,
            }
            sections.append(current)
            continue
        if current is not None:
            current["lines"].append(line)

    if not sections:
        raise ReviewProtocolError(
            f"{PROTOCOL_PATH.name} 中没有任何 `<!-- section: ... | stages: ... -->` 标记。"
            "修复：至少为每个阶段各写一个小节，通用文字写在 `stages: *` 的小节里。"
        )
    return sections


def sections_for_stage(sections: list[dict], stage: str) -> list[dict]:
    return [
        section
        for section in sections
        if ALL_STAGES in section["stages"] or stage in section["stages"]
    ]


def render(stage: str, *, source: Path | None = None) -> str:
    if stage not in STAGES:
        raise ReviewProtocolError(
            f"未知阶段 {stage}。修复：--stage 只接受 {', '.join(STAGES)}。"
        )
    path = source or PROTOCOL_PATH
    if not path.is_file():
        raise ReviewProtocolError(
            f"找不到协议正文 {path}。"
            "修复：确认插件安装完整，或用 --source 指定 review-protocol.md 的实际路径。"
        )

    sections = parse_sections(path.read_text(encoding="utf-8"))
    selected = sections_for_stage(sections, stage)
    if not selected:
        raise ReviewProtocolError(
            f"阶段 {stage} 没有匹配到任何小节。"
            f"修复：在 {path.name} 中为该阶段补小节，或把通用小节标为 `stages: *`。"
        )

    body = "\n\n".join("\n".join(section["lines"]).strip("\n") for section in selected)
    return f"# 回检协议 · {stage}\n\n{body}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按阶段渲染回检协议")
    parser.add_argument("--stage", required=True, choices=STAGES)
    parser.add_argument("--source", type=Path, help="覆盖协议正文路径（测试用）")
    args = parser.parse_args(argv)

    try:
        sys.stdout.write(render(args.stage, source=args.source))
    except ReviewProtocolError as exc:
        print(f"render_review_protocol_failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
