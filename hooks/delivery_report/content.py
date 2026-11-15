"""Bounded, source-linked document excerpts; document text is never executed."""
from __future__ import annotations

import re


def document_sections(text, path):
    sections, body, title, line = [], [], "正文", 1
    fence = None

    def flush():
        content = "\n".join(body).strip()
        if content:
            sections.append({"title": title, "text": content[:2800], "line": line,
                             "truncated": len(content) > 2800, "sourceRefs": [path]})

    for number, raw in enumerate(text.splitlines(), 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})", raw)
        if marker:
            token = marker[1][0]
            if fence is None:
                fence = (token, len(marker[1]))
            elif fence[0] == token and len(marker[1]) >= fence[1] and not raw[marker.end():].strip():
                fence = None
        heading = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", raw) if fence is None and not marker else None
        if heading:
            flush()
            title, line, body = heading[1], number, []
        else:
            body.append(raw)
    flush()
    return sections


def delivery_brief(stages):
    artifacts = {a["path"]: a for stage in stages for a in stage["artifacts"]}
    definitions = (
        ("需求目标", ("PRD.md", "proposal.md"), r"目标|背景|why|overview|problem|概述"),
        ("交付范围与边界", ("proposal.md", "PRD.md"), r"范围|非目标|scope|non.?goal|what changes"),
        ("技术方案与决策", ("design.md", "DETAIL_DESIGN.md"), r"技术|决策|technical|decision|方案|架构"),
    )
    result = []
    used = set()
    for name, paths, pattern in definitions:
        candidates = [(path, section) for path in paths for section in artifacts.get(path, {}).get("sections", [])]
        matched = [(path, section) for path, section in candidates if re.search(pattern, section["title"], re.I)
                   and not (name == "需求目标" and re.search(r"非目标|non.?goals?", section["title"], re.I))]
        selected = [(path, section) for path, section in (matched or candidates)
                    if (path, section["line"]) not in used][:3]
        for path, section in selected:
            used.add((path, section["line"]))
        result.append({"title": name, "sections": [{**section, "path": path, "text": section["text"][:1200],
                       "truncated": section["truncated"] or len(section["text"]) > 1200}
                      for path, section in selected]})
    return result
