#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""正式 PRD 的结构规则与 Markdown 标题解析。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Union


FORMAL_PRD_TITLE = "# 需求正式稿"
REQUIRED_PRD_SECTIONS = ("用户故事", "验收口径", "验收标准", "关键约束")
FORMAL_SECTION_MAX_LEVEL = 3
DISCUSSION_SECTION_TITLES = ("历次讨论记录", "讨论记录")
PENDING_SECTION_TITLES = ("待确认事项", "待确认项")
FORBIDDEN_PRD_SECTION_TITLES = (
    "审理提炼",
    *PENDING_SECTION_TITLES,
    "外部依赖",
    "第三方依赖",
)
PENDING_MARKER = "【待确认】"


_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    line_index: int


def iter_headings(source: Union[str, Sequence[str]]) -> List[Heading]:
    """提取 Markdown 标题，忽略围栏代码块内的标题示例。"""
    lines = source.split("\n") if isinstance(source, str) else list(source)
    headings: List[Heading] = []
    fence = None
    for index, line in enumerate(lines):
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            headings.append(
                Heading(len(heading_match.group(1)), heading_match.group(2), index)
            )
    return headings


def heading_matches(heading_text: str, titles: Iterable[str]) -> bool:
    return any(title in heading_text for title in titles)
