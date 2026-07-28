#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRD 正式稿规则：讨论稿 -> 正式稿的搬运规则与正式稿校验规则的单一事实源。

本模块只依赖标准库，且只包含常量与纯函数，供:
- prd_transplant.py  搬运讨论稿正文生成 PRD.md
- biz_validate.py    校验 PRD.md
共用同一份规则，避免搬运侧与校验侧漂移。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, Union


# 正式稿首行标题（由 biz_validate.py prd 强制）
FORMAL_PRD_TITLE = "# 需求正式稿"

# 正式稿必须包含的四段（由模型追加，不由搬运脚本生成）
REQUIRED_PRD_SECTIONS = ("用户故事", "验收口径", "验收标准", "关键约束")

# 四段作为正式章节的最深标题层级。
# 功能详情里的 `###### 验收标准` 会随正文一起搬进 PRD.md，
# 不限层级的话它会顶替掉本该由模型追加的正式 `## 验收标准`。
FORMAL_SECTION_MAX_LEVEL = 3

# 讨论记录类标题：标题与其下全部正文都不得进入正式稿
DISCUSSION_SECTION_TITLES = ("历次讨论记录", "讨论记录")

# 正式稿禁用标题（本元组为禁用标题的单一事实源，由 biz_validate.py prd 强制）
FORBIDDEN_PRD_SECTION_TITLES = ("审理提炼", "待确认事项", "待确认项", "外部依赖", "第三方依赖")

# 搬运时需要连标题带正文整段删除的章节
DROP_SECTION_TITLES = DISCUSSION_SECTION_TITLES + FORBIDDEN_PRD_SECTION_TITLES

# 讨论稿说明句：命中的整行删除
DISCUSS_NOTICE_PATTERNS = ("本文档为需求讨论中间稿",)

# 讨论稿在描述不明确处打的内联标记：搬运脚本只告警，不改动正文
PENDING_MARKER = "【待确认】"


_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class Heading:
    """一个 Markdown 标题。line_index 为 0 基行号。"""

    level: int
    text: str
    line_index: int


@dataclass(frozen=True)
class DroppedSection:
    """一段被删除的章节。start_line / end_line 均为源文件 1 基行号，闭区间。"""

    title: str
    level: int
    start_line: int
    end_line: int

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass(frozen=True)
class TransplantResult:
    """搬运结果。

    text            搬运后的 PRD.md 全文
    retitled        (源标题行, 正式标题) — 首行标题被替换时给出，否则 None
    title_prepended 源文件没有可用 H1，正式标题为前置新增
    dropped_sections 被删章节（源文件行号）
    dropped_notices  被删讨论稿说明句 (源文件行号, 行内容)
    pending_markers  残留的【待确认】(输出文件行号, 行内容)，脚本未改动
    """

    text: str
    retitled: Optional[Tuple[str, str]]
    title_prepended: bool
    dropped_sections: List[DroppedSection]
    dropped_notices: List[Tuple[int, str]]
    pending_markers: List[Tuple[int, str]]


def iter_headings(source: Union[str, Sequence[str]]) -> List[Heading]:
    """提取 Markdown 标题，跳过围栏代码块内的 `#` 行。

    讨论稿正文里存在 ```markdown 代码块（内含 `## 问题清单` 这类示例标题），
    裸正则会把它们误判成真标题。
    """
    lines = source.split("\n") if isinstance(source, str) else list(source)
    headings: List[Heading] = []
    fence: Optional[str] = None
    for idx, line in enumerate(lines):
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
                Heading(len(heading_match.group(1)), heading_match.group(2), idx)
            )
    return headings


def heading_matches(heading_text: str, titles: Iterable[str]) -> bool:
    return any(title in heading_text for title in titles)


def _drop_ranges(total_lines: int, headings: Sequence[Heading]) -> List[DroppedSection]:
    """定位需要整段删除的章节。

    命中标题后一直删到下一个 level <= 自身的标题为止（或文件尾），
    即整棵子树一起删；已被外层区间覆盖的标题不再重复上报。
    """
    dropped: List[DroppedSection] = []
    covered_until = 0  # 0 基、开区间上界
    for pos, heading in enumerate(headings):
        if heading.line_index < covered_until:
            continue
        if not heading_matches(heading.text, DROP_SECTION_TITLES):
            continue
        end = total_lines
        for later in headings[pos + 1:]:
            if later.level <= heading.level:
                end = later.line_index
                break
        dropped.append(
            DroppedSection(heading.text, heading.level, heading.line_index + 1, end)
        )
        covered_until = end
    return dropped


def plan_transplant(source_text: str) -> TransplantResult:
    """把讨论稿正文搬成正式稿正文。

    不变量：输出正文 = 输入逐字去掉「被删章节 + 讨论稿说明句 + 首尾空行」，
    首行标题替换为 FORMAL_PRD_TITLE。保留的每一行都不改写、不重排、不重新编号。
    """
    lines = source_text.split("\n")
    # 文件末尾换行会切出一个空串，不算一行；去掉它，报告的行号才与编辑器一致
    if lines and lines[-1] == "":
        lines.pop()
    dropped_sections = _drop_ranges(len(lines), iter_headings(lines))

    drop_flags = [False] * len(lines)
    for section in dropped_sections:
        for idx in range(section.start_line - 1, section.end_line):
            drop_flags[idx] = True

    dropped_notices: List[Tuple[int, str]] = []
    for idx, line in enumerate(lines):
        if drop_flags[idx]:
            continue
        if any(pattern in line for pattern in DISCUSS_NOTICE_PATTERNS):
            drop_flags[idx] = True
            dropped_notices.append((idx + 1, line.strip()))
            # 说明句自成一段时，连同其后的空行一起删，避免留下双空行
            if idx + 1 < len(lines) and not lines[idx + 1].strip():
                drop_flags[idx + 1] = True

    kept = [line for idx, line in enumerate(lines) if not drop_flags[idx]]

    # 首行必须是正式标题，先去掉前导空行
    while kept and not kept[0].strip():
        kept.pop(0)

    retitled: Optional[Tuple[str, str]] = None
    title_prepended = False
    kept_headings = iter_headings(kept)
    first_heading_is_title = (
        bool(kept_headings)
        and kept_headings[0].level == 1
        and kept_headings[0].line_index == 0
    )
    if first_heading_is_title:
        if kept[0].strip() != FORMAL_PRD_TITLE:
            retitled = (kept[0].strip(), FORMAL_PRD_TITLE)
        kept[0] = FORMAL_PRD_TITLE
    else:
        kept.insert(0, "")
        kept.insert(0, FORMAL_PRD_TITLE)
        title_prepended = True

    while kept and not kept[-1].strip():
        kept.pop()
    text = "\n".join(kept) + "\n"

    pending_markers = [
        (idx + 1, line.strip())
        for idx, line in enumerate(text.split("\n"))
        if PENDING_MARKER in line
    ]

    return TransplantResult(
        text=text,
        retitled=retitled,
        title_prepended=title_prepended,
        dropped_sections=dropped_sections,
        dropped_notices=dropped_notices,
        pending_markers=pending_markers,
    )
