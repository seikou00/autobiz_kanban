#!/usr/bin/env python3
"""
Analyze standard DOM/class-based HTML before LLM conversion.

This branch is for ordinary HTML pages whose semantics live in:
- DOM hierarchy
- class names and <style> rules
- form controls and repeated blocks
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
IGNORED_TAGS = {"head", "style", "script", "meta", "title", "link", "noscript"}
STATE_CLASS_TOKENS = {"active", "selected", "on", "done", "pending", "disabled", "hover"}


def configure_stdout_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


CONTROL_WORDS = {
    "确定", "取消", "重置", "提交", "保存", "返回", "新增", "添加", "删除", "编辑",
    "上一步", "下一步", "保存草稿",
}

FORM_TRIGGER_WORDS = {
    "请输入", "请选择", "上传", "保存", "提交", "确认", "重置", "搜索",
}

CLASS_SIGNAL_SPECS = {
    "top-nav": {"kind": "shell-top-nav", "family": "shell", "title": "顶部全局导航", "whole": True},
    "side-nav": {"kind": "shell-side-nav", "family": "shell", "title": "左侧导航", "whole": True},
    "breadcrumb": {"kind": "breadcrumb", "family": "breadcrumb", "title": "面包屑", "whole": True},
    "fixed-title-steps": {"kind": "steps-header", "family": "steps", "title": "标题与步骤条", "whole": True},
    "white-card": {"kind": "main-card", "family": "card", "title": "主内容卡片", "whole": True},
    "form-section": {"kind": "form-section", "family": "form", "title": "表单区", "whole": False},
    "module-card": {"kind": "repeated-module", "family": "card", "title": "业务细项卡片", "whole": True},
    "guan-zone-section": {"kind": "repeated-zone", "family": "card", "title": "逛专区模块", "whole": True},
    "zone-group": {"kind": "repeated-zone", "family": "card", "title": "活动专区模块", "whole": True},
    "phone-preview": {"kind": "mobile-preview", "family": "preview", "title": "手机预览区", "whole": True},
    "bottom-bar": {"kind": "footer-actions", "family": "button", "title": "底部操作栏", "whole": True},
    "radio-group": {"kind": "radio-group", "family": "radio", "title": "单选组", "whole": False},
    "tab-group": {"kind": "tabs", "family": "tabs", "title": "选项卡组", "whole": False},
    "upload-zone": {"kind": "upload", "family": "upload", "title": "上传区", "whole": False},
    "switch-wrapper": {"kind": "switch-row", "family": "switch", "title": "开关项", "whole": False},
    "tag-group": {"kind": "tag-group", "family": "tag", "title": "标签选择区", "whole": False},
    "card-group": {"kind": "card-group", "family": "card", "title": "卡片选择区", "whole": False},
}

TAG_CONTROL_DEFAULTS = {
    "input": "input",
    "textarea": "textarea",
    "select": "select",
    "button": "button",
}

REPEATED_COMPONENT_NAMES = {
    "module-card": "BusinessItemCard",
    "guan-zone-section": "BrowseZoneCard",
    "zone-group": "ActivityZoneCard",
    "card-option": "SelectableCardOption",
    "preview-card": "PreviewSummaryCard",
    "preview-list-item": "PreviewListItem",
    "top-nav-item": "TopNavItem",
    "side-nav-item": "SideNavItem",
    "side-nav-sub-item": "SideNavSubItem",
    "radio-option": "RadioCardOption",
    "tab-item": "TabItem",
    "tag-option": "TagOptionChip",
}

PROJECT_COMPONENT_KEYWORDS = {
    "shell": ["layout", "nav", "menu", "header", "sidebar", "sider"],
    "breadcrumb": ["breadcrumb"],
    "steps": ["step", "steps", "progress"],
    "form": ["form", "search", "filter"],
    "input": ["input", "field"],
    "textarea": ["textarea", "editor"],
    "radio": ["radio", "selector", "segmented"],
    "tabs": ["tab"],
    "upload": ["upload", "uploader", "import"],
    "switch": ["switch", "toggle"],
    "tag": ["tag", "badge", "chip"],
    "button": ["button", "action"],
    "card": ["card", "panel", "item"],
    "preview": ["preview", "phone"],
}

CHART_LIBRARY_PACKAGES = [
    ("echarts", "ECharts", {"echarts", "echarts-for-react", "vue-echarts"}),
    ("ant-design-charts", "Ant Design Charts", {"@ant-design/charts"}),
    ("antv-g2", "AntV G2", {"@antv/g2", "@antv/g2plot", "@antv/plots", "@antv/l7"}),
    ("bizcharts", "BizCharts", {"bizcharts"}),
    ("recharts", "Recharts", {"recharts"}),
    ("chartjs", "Chart.js", {"chart.js", "react-chartjs-2"}),
    ("highcharts", "Highcharts", {"highcharts", "highcharts-react-official"}),
]

UI_LIBRARY_PACKAGES = [
    ("antd", "Ant Design", {"antd", "@ant-design/pro-components"}),
    ("antd-mobile", "Ant Design Mobile", {"antd-mobile"}),
    ("element-plus", "Element Plus", {"element-plus", "element-ui"}),
]

COMPONENT_NAMES = {
    "antd": {
        "shell": "AntD Layout/Menu shell",
        "breadcrumb": "AntD Breadcrumb",
        "steps": "AntD Steps",
        "form": "AntD Form",
        "input": "AntD Input",
        "textarea": "AntD Input.TextArea",
        "select": "AntD Select",
        "radio": "AntD Radio.Group",
        "tabs": "AntD Tabs",
        "upload": "AntD Upload",
        "switch": "AntD Switch",
        "tag": "AntD Tag / CheckableTag",
        "button": "AntD Button",
        "card": "AntD Card",
        "preview": "custom preview component",
    },
    "antd-mobile": {
        "shell": "AntD Mobile NavBar / SideBar shell",
        "breadcrumb": "custom breadcrumb",
        "steps": "AntD Mobile Steps / Stepper",
        "form": "AntD Mobile Form",
        "input": "AntD Mobile Input",
        "textarea": "AntD Mobile TextArea",
        "select": "AntD Mobile Selector / Picker",
        "radio": "AntD Mobile Selector / Radio.Group",
        "tabs": "AntD Mobile Tabs",
        "upload": "AntD Mobile ImageUploader / Uploader",
        "switch": "AntD Mobile Switch",
        "tag": "AntD Mobile Tag",
        "button": "AntD Mobile Button",
        "card": "AntD Mobile Card",
        "preview": "custom mobile preview component",
    },
    "element-plus": {
        "shell": "Element Plus Container/Menu shell",
        "breadcrumb": "Element Plus ElBreadcrumb",
        "steps": "Element Plus ElSteps",
        "form": "Element Plus ElForm",
        "input": "Element Plus ElInput",
        "textarea": "Element Plus ElInput textarea",
        "select": "Element Plus ElSelect",
        "radio": "Element Plus ElRadioGroup",
        "tabs": "Element Plus ElTabs",
        "upload": "Element Plus ElUpload",
        "switch": "Element Plus ElSwitch",
        "tag": "Element Plus ElTag",
        "button": "Element Plus ElButton",
        "card": "Element Plus Card / custom card",
        "preview": "custom preview component",
    },
    "project-ui": {
        "shell": "project shell component",
        "breadcrumb": "project Breadcrumb component",
        "steps": "project Steps component",
        "form": "project Form component",
        "input": "project Input component",
        "textarea": "project TextArea component",
        "select": "project Select component",
        "radio": "project Radio component",
        "tabs": "project Tabs component",
        "upload": "project Upload component",
        "switch": "project Switch component",
        "tag": "project Tag/Badge component",
        "button": "project Button component",
        "card": "project Card component",
        "preview": "project preview component",
    },
}

STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]+)\}")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def px(value: str | None, basis: float = 0.0) -> float | None:
    if not value:
        return None
    value = value.strip().lower()
    if value in {"auto", "unset", "initial"}:
        return None
    if value.endswith("%"):
        try:
            return basis * float(value[:-1]) / 100.0
        except ValueError:
            return None
    m = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(m.group(0)) if m else None


def parse_style(style: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in style.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        result[key.strip().lower()] = value.strip()
    return result


def class_tokens(class_name: str) -> list[str]:
    return [token for token in re.split(r"\s+", class_name.strip()) if token]


def selector_leaf(selector: str) -> tuple[str, list[str], str] | None:
    raw = selector.strip()
    if not raw or raw.startswith("@") or ":" in raw:
        return None
    leaf = raw.replace(">", " ").replace("+", " ").replace("~", " ").split()[-1].strip()
    if not leaf:
        return None
    tag_match = re.match(r"^[A-Za-z][A-Za-z0-9_-]*", leaf)
    tag = tag_match.group(0).lower() if tag_match else ""
    classes = re.findall(r"\.([A-Za-z0-9_-]+)", leaf)
    node_id_match = re.search(r"#([A-Za-z0-9_-]+)", leaf)
    node_id = node_id_match.group(1) if node_id_match else ""
    if not tag and not classes and not node_id:
        return None
    return tag, classes, node_id


@dataclass
class CSSRule:
    tag: str
    classes: list[str]
    node_id: str
    declarations: dict[str, str]
    specificity: tuple[int, int, int]
    order: int


def parse_css_rules(html: str) -> list[CSSRule]:
    rules: list[CSSRule] = []
    order = 0
    for block in STYLE_BLOCK_RE.findall(html):
        cleaned = CSS_COMMENT_RE.sub("", block)
        for selector_group, body in CSS_RULE_RE.findall(cleaned):
            declarations = parse_style(body)
            if not declarations:
                continue
            for selector in selector_group.split(","):
                parsed = selector_leaf(selector)
                if not parsed:
                    continue
                tag, classes, node_id = parsed
                rules.append(CSSRule(
                    tag=tag,
                    classes=classes,
                    node_id=node_id,
                    declarations=declarations,
                    specificity=(1 if node_id else 0, len(classes), 1 if tag else 0),
                    order=order,
                ))
                order += 1
    return rules


def merge_style_dict(base: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    merged = dict(base)
    for key, value in extra.items():
        if value != "":
            merged[key] = value
    return merged


def css_rule_matches(rule: CSSRule, tag: str, attrs: dict[str, str]) -> bool:
    if rule.tag and rule.tag != tag:
        return False
    if rule.node_id and rule.node_id != attrs.get("id", ""):
        return False
    node_classes = set(class_tokens(attrs.get("class", "")))
    return all(token in node_classes for token in rule.classes)


def parse_box_values(style: dict[str, str], prefix: str) -> tuple[float, float, float, float]:
    default = style.get(prefix, "")
    parts = [part for part in default.split() if part]
    values = [0.0, 0.0, 0.0, 0.0]
    if parts:
        if len(parts) == 1:
            values = [px(parts[0]) or 0.0] * 4
        elif len(parts) == 2:
            top = px(parts[0]) or 0.0
            right = px(parts[1]) or 0.0
            values = [top, right, top, right]
        elif len(parts) == 3:
            top = px(parts[0]) or 0.0
            right = px(parts[1]) or 0.0
            bottom = px(parts[2]) or 0.0
            values = [top, right, bottom, right]
        else:
            values = [(px(part) or 0.0) for part in parts[:4]]
    top = px(style.get(f"{prefix}-top")) if style.get(f"{prefix}-top") else values[0]
    right = px(style.get(f"{prefix}-right")) if style.get(f"{prefix}-right") else values[1]
    bottom = px(style.get(f"{prefix}-bottom")) if style.get(f"{prefix}-bottom") else values[2]
    left = px(style.get(f"{prefix}-left")) if style.get(f"{prefix}-left") else values[3]
    return float(top or 0.0), float(right or 0.0), float(bottom or 0.0), float(left or 0.0)


def combined_bbox(boxes: list[dict[str, float]]) -> dict[str, float]:
    valid = [box for box in boxes if box and {"x", "y", "w", "h"} <= set(box)]
    if not valid:
        return {}
    x0 = min(box["x"] for box in valid)
    y0 = min(box["y"] for box in valid)
    x1 = max(box["x"] + box["w"] for box in valid)
    y1 = max(box["y"] + box["h"] for box in valid)
    return {"x": round(x0, 2), "y": round(y0, 2), "w": round(x1 - x0, 2), "h": round(y1 - y0, 2)}


def area(box: dict[str, Any]) -> float:
    return max(float(box.get("w", 0)), 0.0) * max(float(box.get("h", 0)), 0.0)


def contains_bbox(outer: dict[str, float], inner: dict[str, float], pad: float = 0.0) -> bool:
    return (
        inner.get("x", 0) >= outer.get("x", 0) - pad
        and inner.get("y", 0) >= outer.get("y", 0) - pad
        and inner.get("x", 0) + inner.get("w", 0) <= outer.get("x", 0) + outer.get("w", 0) + pad
        and inner.get("y", 0) + inner.get("h", 0) <= outer.get("y", 0) + outer.get("h", 0) + pad
    )


def overlap_ratio(inner: dict[str, Any], outer: dict[str, Any]) -> float:
    ix0 = max(float(inner.get("x", 0)), float(outer.get("x", 0)))
    iy0 = max(float(inner.get("y", 0)), float(outer.get("y", 0)))
    ix1 = min(float(inner.get("x", 0)) + float(inner.get("w", 0)), float(outer.get("x", 0)) + float(outer.get("w", 0)))
    iy1 = min(float(inner.get("y", 0)) + float(inner.get("h", 0)), float(outer.get("y", 0)) + float(outer.get("h", 0)))
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return round(((ix1 - ix0) * (iy1 - iy0)) / max(area(inner), 1.0), 4)


def approx_text_size(text: str, font_size: float, max_width: float | None = None) -> tuple[float, float]:
    if not text:
        return 0.0, max(font_size * 1.4, 18.0)
    avg_char = max(font_size * 0.62, 7.0)
    width = max(len(text) * avg_char, 24.0)
    if max_width and max_width > 24:
        lines = max(1, math.ceil(width / max_width))
        return min(width, max_width), max(lines * font_size * 1.45, 18.0)
    return width, max(font_size * 1.45, 18.0)


def default_display(tag: str) -> str:
    if tag in {"span", "a", "strong", "b", "em", "i", "small", "label"}:
        return "inline"
    if tag in {"input", "button", "textarea", "select"}:
        return "inline-block"
    return "block"


def inferred_text_role(tag: str, attrs: dict[str, str]) -> tuple[str, str]:
    if tag == "input":
        input_type = attrs.get("type", "").strip().lower()
        if input_type in {"hidden", "checkbox", "radio", "file", "button", "submit", "reset"}:
            return "", ""
        value = clean_text(attrs.get("value", ""))
        if value:
            return value, "value"
        placeholder = clean_text(attrs.get("placeholder", ""))
        if placeholder:
            return placeholder, "placeholder"
    if tag == "textarea":
        value = clean_text(attrs.get("value", ""))
        if value:
            return value, "value"
        placeholder = clean_text(attrs.get("placeholder", ""))
        if placeholder:
            return placeholder, "placeholder"
    return "", ""


@dataclass
class Node:
    idx: int
    tag: str
    attrs: dict[str, str]
    parent: int | None
    children: list[int] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    style: dict[str, str] = field(default_factory=dict)
    depth: int = 0
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    synthetic_text: str = ""
    synthetic_text_role: str = ""

    @property
    def class_name(self) -> str:
        return self.attrs.get("class", "")

    @property
    def node_id(self) -> str:
        return self.attrs.get("id", "")

    @property
    def classes(self) -> list[str]:
        return class_tokens(self.class_name)

    @property
    def text(self) -> str:
        return clean_text(" ".join(self.text_parts))

    @property
    def all_text(self) -> str:
        base = self.text
        synthetic = self.synthetic_text
        return clean_text(" ".join(part for part in [base, synthetic] if part))


class StandardHTMLParser(HTMLParser):
    def __init__(self, css_rules: list[CSSRule]) -> None:
        super().__init__(convert_charrefs=True)
        self.css_rules = css_rules
        self.nodes: list[Node] = []
        self.stack: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_dict = {key: value or "" for key, value in attrs}
        computed_style: dict[str, str] = {}
        matched_rules = [rule for rule in self.css_rules if css_rule_matches(rule, tag, attr_dict)]
        matched_rules.sort(key=lambda rule: (rule.specificity, rule.order))
        for rule in matched_rules:
            computed_style = merge_style_dict(computed_style, rule.declarations)
        computed_style = merge_style_dict(computed_style, parse_style(attr_dict.get("style", "")))
        parent = self.stack[-1] if self.stack else None
        synthetic_text, synthetic_role = inferred_text_role(tag, attr_dict)
        node = Node(
            idx=len(self.nodes),
            tag=tag,
            attrs=attr_dict,
            parent=parent,
            style=computed_style,
            depth=len(self.stack),
            synthetic_text=synthetic_text,
            synthetic_text_role=synthetic_role,
        )
        self.nodes.append(node)
        if parent is not None:
            self.nodes[parent].children.append(node.idx)
        if tag not in VOID_TAGS:
            self.stack.append(node.idx)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        while self.stack:
            idx = self.stack.pop()
            if self.nodes[idx].tag == tag:
                break

    def handle_data(self, data: str) -> None:
        text = clean_text(data)
        if not text or not self.stack:
            return
        current = self.nodes[self.stack[-1]]
        if current.tag in IGNORED_TAGS:
            return
        current.text_parts.append(text)


def decode_html_file(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def root_viewport(nodes: list[Node]) -> tuple[float, float]:
    body = next((node for node in nodes if node.tag == "body"), None)
    width = px(body.style.get("width"), 1440.0) if body else None
    height = px(body.style.get("height"), 1200.0) if body else None
    return float(width or 1440.0), float(height or 1200.0)


def text_block_height(node: Node) -> float:
    font_size = px(node.style.get("font-size")) or 14.0
    line_height = px(node.style.get("line-height")) or max(font_size * 1.45, 18.0)
    return max(line_height, 18.0)


def node_has_class(node: Node, token: str) -> bool:
    return token in node.classes


def node_has_class_like(node: Node, token: str) -> bool:
    return token in node.class_name


def compute_layout(nodes: list[Node]) -> None:
    if not nodes:
        return
    viewport_w, viewport_h = root_viewport(nodes)
    body = next((node for node in nodes if node.tag == "body"), None)
    root_idx = body.idx if body else 0

    def layout_children(parent_idx: int, content_x: float, content_y: float, content_w: float) -> float:
        parent = nodes[parent_idx]
        display = re.sub(r"\s+", "", parent.style.get("display", "").lower()) or default_display(parent.tag)
        flex_direction = parent.style.get("flex-direction", "").strip().lower() or "row"
        gap = px(parent.style.get("gap"), content_w) or 0.0
        padding_top, padding_right, padding_bottom, padding_left = parse_box_values(parent.style, "padding")
        child_x = content_x + padding_left
        child_y = content_y + padding_top
        child_w = max(content_w - padding_left - padding_right, 0.0)
        max_bottom = child_y
        current_x = child_x
        current_y = child_y

        for child_idx in parent.children:
            child = nodes[child_idx]
            width = px(child.style.get("width"), child_w)
            if width is None:
                width = child_w if display not in {"flex", "inline-flex"} or flex_direction == "column" else max(min(child_w, viewport_w), 80.0)
            height = px(child.style.get("height"), viewport_h)
            if height is None:
                if child.all_text:
                    _, text_h = approx_text_size(child.all_text, px(child.style.get("font-size")) or 14.0, width)
                    height = text_h
                else:
                    height = 24.0

            if display in {"flex", "inline-flex"}:
                if flex_direction == "column":
                    child.x = child_x
                    child.y = current_y
                    child.w = width
                    child.h = height
                    used_h = layout_children(child_idx, child.x, child.y, child.w)
                    child.h = max(child.h, used_h - child.y)
                    current_y = child.y + child.h + gap
                    max_bottom = max(max_bottom, child.y + child.h)
                else:
                    child.x = current_x
                    child.y = child_y
                    child.w = width
                    child.h = height
                    used_h = layout_children(child_idx, child.x, child.y, child.w)
                    child.h = max(child.h, used_h - child.y)
                    current_x = child.x + child.w + gap
                    max_bottom = max(max_bottom, child.y + child.h)
            else:
                child.x = child_x
                child.y = current_y
                child.w = width
                child.h = height
                used_h = layout_children(child_idx, child.x, child.y, child.w)
                child.h = max(child.h, used_h - child.y)
                current_y = child.y + child.h + gap
                max_bottom = max(max_bottom, child.y + child.h)

        return max(max_bottom + padding_bottom, content_y + max(parent.h, 0.0))

    root = nodes[root_idx]
    root.x = 0.0
    root.y = 0.0
    root.w = viewport_w
    root.h = viewport_h
    layout_children(root_idx, 0.0, 0.0, viewport_w)


def extract_text_items(nodes: list[Node]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for node in nodes:
        text = node.all_text
        if not text:
            continue
        font_size = px(node.style.get("font-size")) or 14.0
        width = node.w or approx_text_size(text, font_size)[0]
        height = node.h or approx_text_size(text, font_size, width)[1]
        kind = "label"
        if node.synthetic_text_role == "placeholder":
            kind = "placeholder"
        elif node.tag == "button" or text in CONTROL_WORDS:
            kind = "action"
        elif node.tag == "label":
            kind = "field-label"
        items.append({
            "text": text,
            "nodeId": node.node_id,
            "tag": node.tag,
            "bbox": {"x": round(node.x, 2), "y": round(node.y, 2), "w": round(width, 2), "h": round(height, 2)},
            "fontSize": round(font_size, 2),
            "color": node.style.get("color", ""),
            "depth": node.depth,
            "className": node.class_name,
            "kind": kind,
            **({"textRole": node.synthetic_text_role} if node.synthetic_text_role else {}),
        })
    items.sort(key=lambda item: (item["bbox"]["y"], item["bbox"]["x"]))
    return items


def extract_visual_boxes(nodes: list[Node]) -> list[dict[str, Any]]:
    boxes: list[dict[str, Any]] = []
    for node in nodes:
        has_box = bool(node.style.get("background") or node.style.get("border") or node.style.get("outline"))
        if not has_box:
            continue
        boxes.append({
            "nodeId": node.node_id,
            "bbox": {"x": round(node.x, 2), "y": round(node.y, 2), "w": round(node.w, 2), "h": round(node.h, 2)},
            "background": node.style.get("background", ""),
            "border": node.style.get("border", "") or node.style.get("outline", ""),
            "borderRadius": node.style.get("border-radius", ""),
            "tag": node.tag,
            "className": node.class_name,
        })
    return boxes


def infer_regions(nodes: list[Node], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for token, spec in CLASS_SIGNAL_SPECS.items():
        matched_nodes = [node for node in nodes if node_has_class(node, token)]
        if not matched_nodes:
            continue
        boxes = [{"x": node.x, "y": node.y, "w": node.w, "h": node.h} for node in matched_nodes]
        bbox = combined_bbox(boxes)
        texts = [item["text"] for item in items if contains_bbox(bbox, item["bbox"], pad=4)] if bbox else []
        regions.append({
            "name": spec["title"],
            "kind": spec["kind"],
            "family": spec["family"],
            "whole": spec["whole"],
            "bbox": bbox,
            "texts": texts[:40],
        })
    return regions


def infer_sections(nodes: list[Node]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for token, spec in CLASS_SIGNAL_SPECS.items():
        if spec["whole"]:
            continue
        matched_nodes = [node for node in nodes if node_has_class(node, token)]
        for node in matched_nodes:
            bbox = {"x": round(node.x, 2), "y": round(node.y, 2), "w": round(node.w, 2), "h": round(node.h, 2)}
            sections.append({
                "title": spec["title"],
                "ownerPath": spec["title"],
                "bbox": bbox,
                "containerBbox": bbox,
                "contentBbox": bbox,
                "layoutHint": spec["family"],
                "subsectionTitles": [],
                "tagStripTexts": [],
                "renderContract": {
                    "mustRenderWholeSection": spec["whole"],
                    "kind": spec["kind"],
                    "contentMode": "standard-html-structure",
                    "componentFamily": spec["family"],
                },
                "texts": [node.all_text] if node.all_text else [],
            })
    return sections


def infer_fields(nodes: list[Node]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    labels = [node for node in nodes if node.tag == "label" and node.all_text]
    controls = [node for node in nodes if node.tag in {"input", "textarea", "select"}]
    for label in labels:
        control = next((node for node in controls if node.parent == label.parent), None)
        if not control:
            continue
        label_bbox = {"x": round(label.x, 2), "y": round(label.y, 2), "w": round(label.w, 2), "h": round(label.h, 2)}
        control_bbox = {"x": round(control.x, 2), "y": round(control.y, 2), "w": round(control.w, 2), "h": round(control.h, 2)}
        fields.append({
            "label": label.all_text,
            "required": "*" in label.all_text,
            "placeholder": control.synthetic_text if control.synthetic_text_role == "placeholder" else "",
            "valueText": control.synthetic_text if control.synthetic_text_role == "value" else "",
            "controlType": "textarea" if control.tag == "textarea" else control.tag,
            "sourceTag": control.tag,
            "labelNodeId": label.node_id,
            "controlNodeId": control.node_id,
            "labelBbox": label_bbox,
            "controlBbox": control_bbox,
            "bbox": combined_bbox([label_bbox, control_bbox]),
        })
    return fields


def infer_repeated_structures(nodes: list[Node]) -> list[dict[str, Any]]:
    groups: dict[str, list[Node]] = {}
    for node in nodes:
        if not node.class_name:
            continue
        signature = " ".join(sorted(node.classes))
        if not signature:
            continue
        groups.setdefault(signature, []).append(node)
    result: list[dict[str, Any]] = []
    for signature, matched in groups.items():
        if len(matched) < 2:
            continue
        component_name = REPEATED_COMPONENT_NAMES.get(matched[0].classes[0], "RepeatedBlock")
        boxes = [{"x": node.x, "y": node.y, "w": node.w, "h": node.h} for node in matched]
        result.append({
            "signature": signature,
            "count": len(matched),
            "componentName": component_name,
            "family": "card",
            "bbox": combined_bbox(boxes),
            "sampleTexts": [node.all_text for node in matched[:4] if node.all_text],
        })
    return result


def build_content_inventory(sections: list[dict[str, Any]], regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if sections:
        return [{
            "owner": section.get("ownerPath", ""),
            "title": section.get("title", ""),
            "kind": "section",
            "bbox": section.get("bbox", {}),
            "containerBbox": section.get("containerBbox", {}),
            "contentBbox": section.get("contentBbox", {}),
            "layoutHint": section.get("layoutHint", ""),
            "subsectionTitles": section.get("subsectionTitles", []),
            "tagStripTexts": section.get("tagStripTexts", []),
            "renderContract": section.get("renderContract", {}),
            "textCount": len(section.get("texts", [])),
            "visibleTexts": section.get("texts", []),
            "controlTexts": [text for text in section.get("texts", []) if text in CONTROL_WORDS],
            "fieldLikeTexts": [text for text in section.get("texts", []) if len(text) <= 20],
            "mustPreserve": True,
        } for section in sections]
    return [{
        "owner": region.get("name", ""),
        "kind": "region",
        "bbox": region.get("bbox", {}),
        "textCount": len(region.get("texts", [])),
        "visibleTexts": region.get("texts", []),
        "controlTexts": [text for text in region.get("texts", []) if text in CONTROL_WORDS],
        "fieldLikeTexts": [text for text in region.get("texts", []) if len(text) <= 20],
        "mustPreserve": True,
    } for region in regions]


def scan_project_components(project_root: Path | None) -> list[dict[str, Any]]:
    components: dict[str, dict[str, Any]] = {}
    if not project_root or not project_root.exists():
        return []
    search_roots = [
        project_root / "architecture" / "components",
        project_root / "components",
        project_root / "src" / "components",
    ]
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".tsx", ".jsx", ".vue", ".ts", ".md"}:
                continue
            name = path.stem
            if name in {"index", "README"}:
                name = path.parent.name
            low = name.lower()
            kind = "unknown"
            for family, keywords in PROJECT_COMPONENT_KEYWORDS.items():
                if any(keyword in low for keyword in keywords):
                    kind = family
                    break
            entry = components.setdefault(name, {
                "name": name,
                "paths": [],
                "kind": kind,
                "usageCount": 0,
            })
            try:
                entry["paths"].append(str(path.relative_to(project_root)))
            except ValueError:
                entry["paths"].append(str(path))
    src = project_root / "src"
    if src.exists():
        corpus_parts: list[str] = []
        for path in src.rglob("*"):
            if path.suffix.lower() in {".tsx", ".jsx", ".vue"} and path.is_file():
                try:
                    corpus_parts.append(path.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    pass
        corpus = "\n".join(corpus_parts)
        for name, entry in components.items():
            entry["usageCount"] = len(re.findall(rf"<{re.escape(name)}\b", corpus))
    return sorted(components.values(), key=lambda item: (-int(item.get("usageCount", 0)), item.get("name", "")))


def detect_chart_libraries(project_root: Path | None) -> list[dict[str, Any]]:
    if not project_root or not project_root.exists():
        return []
    package_file = project_root / "package.json"
    if not package_file.exists():
        return []
    try:
        package = json.loads(package_file.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return []
    deps: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        value = package.get(key)
        if isinstance(value, dict):
            deps.update(value)
    source_files: list[Path] = []
    src = project_root / "src"
    if src.exists():
        for path in src.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".tsx", ".jsx", ".vue", ".ts", ".js"}:
                source_files.append(path)
    corpus_parts: list[str] = []
    for path in source_files:
        try:
            corpus_parts.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    corpus = "\n".join(corpus_parts)
    found: list[dict[str, Any]] = []
    for lib_id, name, packages in CHART_LIBRARY_PACKAGES:
        installed = sorted(pkg for pkg in packages if pkg in deps)
        if not installed:
            continue
        if any(pkg in corpus for pkg in installed):
            found.append({"id": lib_id, "name": name, "packages": installed, "evidence": "source-import"})
    return found


def detect_ui_libraries(project_root: Path | None, page_platform: str) -> list[dict[str, Any]]:
    if not project_root or not project_root.exists():
        return [{"id": "project-ui", "name": "Project UI", "packages": [], "evidence": "project root unavailable"}]
    package_file = project_root / "package.json"
    if not package_file.exists():
        return [{"id": "project-ui", "name": "Project UI", "packages": [], "evidence": "package.json not found"}]
    try:
        package = json.loads(package_file.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return [{"id": "project-ui", "name": "Project UI", "packages": [], "evidence": "package.json unreadable"}]
    deps: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        value = package.get(key)
        if isinstance(value, dict):
            deps.update(value)
    found: list[dict[str, Any]] = []
    for lib_id, name, packages in UI_LIBRARY_PACKAGES:
        installed = sorted(pkg for pkg in packages if pkg in deps)
        if installed:
            found.append({"id": lib_id, "name": name, "packages": installed, "evidence": "package.json"})
    if not found:
        return [{"id": "project-ui", "name": "Project UI", "packages": [], "evidence": "no recognized UI library"}]
    if page_platform == "desktop":
        preferred = {"antd": 0, "element-plus": 1, "antd-mobile": 2}
    else:
        preferred = {"antd-mobile": 0, "antd": 1, "element-plus": 2}
    found.sort(key=lambda item: (preferred.get(item["id"], 9), item["name"]))
    return found


def primary_ui_library(ui_libraries: list[dict[str, Any]]) -> str:
    return str((ui_libraries[0] if ui_libraries else {}).get("id") or "project-ui")


def component_name(ui_library: str, family: str) -> str:
    mapping = COMPONENT_NAMES.get(ui_library) or COMPONENT_NAMES["project-ui"]
    return mapping.get(family, COMPONENT_NAMES["project-ui"].get(family, f"{ui_library} {family} component"))


def pick_project_component(components: list[dict[str, Any]], family: str) -> dict[str, Any] | None:
    keywords = PROJECT_COMPONENT_KEYWORDS.get(family, [family])
    best: tuple[float, dict[str, Any]] | None = None
    for component in components:
        name = str(component.get("name", "")).lower()
        kind = str(component.get("kind", "")).lower()
        score = 0.0
        if kind == family:
            score += 3.0
        if any(keyword in name for keyword in keywords):
            score += 2.0
        score += min(float(component.get("usageCount", 0)), 20.0) / 20.0
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, component)
    return best[1] if best else None


def build_replacement_slots(
    fields: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    repeated_structures: list[dict[str, Any]],
    ui_libraries: list[dict[str, Any]],
    components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    ui_library = primary_ui_library(ui_libraries)
    ui_library_name = str((ui_libraries[0] if ui_libraries else {}).get("name") or "Project UI")

    slots.append({
        "slot": "page-form-contract",
        "kind": "form-contract",
        "bbox": combined_bbox([field.get("bbox", {}) for field in fields]) if fields else {},
        "decision": "must-use-form-container" if fields else "not-applicable",
        "candidate": component_name(ui_library, "form") if fields else "",
        "componentPath": "",
        "evidence": f"standard HTML contains labeled fields; uiLibrary={ui_library_name}",
        "constraint": "when fields exist, wrap the related editable area in the corresponding Form component instead of leaving isolated raw inputs",
        "contentContract": "preserve labels, required markers, helper texts, and field ordering inside the form",
        "layoutContract": "Form may organize internals, but must keep the original row/column grouping and spacing rhythm",
        "rawLayerAction": "replace raw form container and child controls together when the contract is proven",
        "fallback": "only fall back if there is explicit project evidence that this area is not implemented with a Form abstraction",
    })

    for section in sections:
        contract = section.get("renderContract") or {}
        family = str(contract.get("componentFamily") or "card")
        project_candidate = pick_project_component(components, family)
        slots.append({
            "slot": f"section:{section.get('title', '')}",
            "kind": contract.get("kind", "section"),
            "bbox": section.get("bbox", {}),
            "decision": "reuse-project-component-or-pattern" if project_candidate else "use-ui-library-or-fidelity",
            "candidate": project_candidate.get("name") if project_candidate else component_name(ui_library, family),
            "componentPath": (project_candidate.get("paths") or [""])[0] if project_candidate else "",
            "evidence": f"standard HTML section identified from class semantics; uiLibrary={ui_library_name}",
            "constraint": "preserve the section boundary, class-driven spacing, and visible texts one-to-one",
            "contentContract": "keep all visible labels, values, buttons, tags, and helper texts in the same section",
            "layoutContract": "preserve flex/grid/block structure from the source classes before componentization",
            "rawLayerAction": "replace raw DOM only inside this section when the component contract is proven",
            "fallback": "keep fidelity DOM for this section if the component changes spacing, grouping, or visual hierarchy",
        })

    for field in fields:
        family = field.get("controlType", "input")
        project_candidate = pick_project_component(components, family)
        source_tag = str(field.get("sourceTag", "") or "")
        replacement_rule = "replace-native-control-with-library-component" if source_tag in TAG_CONTROL_DEFAULTS else "replace-structure-with-library-component"
        slots.append({
            "slot": f"field:{field.get('label', '')}",
            "kind": "field-control",
            "bbox": field.get("bbox", {}),
            "decision": "reuse-project-component-or-pattern" if project_candidate else "use-ui-library-control",
            "candidate": project_candidate.get("name") if project_candidate else component_name(ui_library, family),
            "componentPath": (project_candidate.get("paths") or [""])[0] if project_candidate else "",
            "evidence": f"label/control pairing from standard HTML DOM; controlType={family}; sourceTag={source_tag or 'synthetic'}; uiLibrary={ui_library_name}",
            "constraint": "keep the control inside the original field row and preserve label, count, and helper text alignment; native controls must be replaced by the mapped library component, not left as raw tags",
            "contentContract": "preserve label text, required marker, value/placeholder, and inline count if present",
            "layoutContract": "do not change row/column structure or right-aligned input positioning without source evidence",
            "rawLayerAction": "replace only the field control subtree after the component mapping is confirmed",
            "fallback": "use fidelity DOM for this field if the component contract breaks spacing or accessory content",
            "replacementRule": replacement_rule,
            "sourceTag": source_tag,
        })

    for group in repeated_structures:
        project_candidate = pick_project_component(components, group.get("family", "card"))
        slots.append({
            "slot": f"repeat:{group.get('signature', '')}",
            "kind": "repeated-structure",
            "bbox": group.get("bbox", {}),
            "decision": "extract-local-component",
            "candidate": project_candidate.get("name") if project_candidate else group.get("componentName", ""),
            "componentPath": (project_candidate.get("paths") or [""])[0] if project_candidate else "",
            "evidence": f"detected {group.get('count')} sibling blocks with the same structure signature `{group.get('signature')}`",
            "constraint": "extract repeating blocks while keeping per-item text, state class, and spacing identical to the source",
            "contentContract": "keep per-item titles, descriptions, delete/add actions, and state classes",
            "layoutContract": "preserve repeated block order and wrapper spacing from the source HTML",
            "rawLayerAction": "replace repeated blocks with a local component only after prop boundaries are mapped",
            "fallback": "keep repeated fidelity DOM if extraction changes vertical rhythm or nested adornments",
        })

    slots.append({
        "slot": "page-upload-contract",
        "kind": "upload-contract",
        "bbox": combined_bbox([section.get("bbox", {}) for section in sections if str(section.get("layoutHint", "")) == "upload"]) if sections else {},
        "decision": "must-use-upload-component",
        "candidate": component_name(ui_library, "upload"),
        "componentPath": "",
        "evidence": f"standard HTML contains upload-like zones; uiLibrary={ui_library_name}",
        "constraint": "upload-like regions must map to the corresponding Upload/ImageUploader/ElUpload component rather than stay as passive dashed boxes",
        "contentContract": "preserve upload title, hint text, size note, and trigger affordance",
        "layoutContract": "keep dashed border area, centered icon/text stack, and helper text spacing",
        "rawLayerAction": "replace upload zone subtree with the mapped upload component plus local style overrides when needed",
        "fallback": "only keep fidelity DOM if the product explicitly requires a static placeholder instead of a real upload interaction",
    })

    return slots


def build_component_matches(
    fields: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    repeated_structures: list[dict[str, Any]],
    project_components: list[dict[str, Any]],
    ui_library: str,
) -> list[dict[str, Any]]:
    families = {"shell", "breadcrumb", "steps", "form", "input", "textarea", "radio", "tabs", "upload", "switch", "tag", "button", "card", "preview"}
    result: list[dict[str, Any]] = []
    for family in sorted(families):
        candidate = pick_project_component(project_components, family)
        result.append({
            "regionKind": family,
            "candidates": [candidate] if candidate else [{
                "name": component_name(ui_library, family),
                "paths": [],
                "kind": family,
                "usageCount": 0,
                "sourceType": "ui-library",
            }],
            "rule": "matched by standard DOM structure, class semantics, and installed UI library",
        })
    return result


def build_matched_components_summary(replacement_slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for slot in replacement_slots:
        candidate = str(slot.get("candidate") or "")
        path = str(slot.get("componentPath") or "")
        kind = str(slot.get("kind") or "")
        if not candidate:
            continue
        key = (candidate, path, kind)
        if key in seen:
            continue
        seen.add(key)
        summary.append({
            "component": candidate,
            "componentPath": path,
            "slot": slot.get("slot", ""),
            "kind": kind,
            "decision": slot.get("decision", ""),
            "evidence": slot.get("evidence", ""),
        })
    return summary


def semantic_terms(fields: list[dict[str, Any]], sections: list[dict[str, Any]], repeated_structures: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[str]:
    terms: list[str] = []
    for field in fields:
        terms.append(str(field.get("label", "")))
    for section in sections:
        terms.append(str(section.get("title", "")))
    for group in repeated_structures:
        terms.extend(group.get("sampleTexts", []))
    for item in items:
        if item.get("kind") in {"action", "section-title"}:
            terms.append(str(item.get("text", "")))
    return [term for term in dict.fromkeys(term for term in terms if term and len(term) <= 40)][:120]


def page_platform(nodes: list[Node]) -> str:
    if any(node_has_class(node, "top-nav") or node_has_class(node, "side-nav") for node in nodes):
        return "desktop"
    return "mobile"


def canvas_size(nodes: list[Node]) -> dict[str, float]:
    max_r = 0.0
    max_b = 0.0
    for node in nodes:
        max_r = max(max_r, node.x + node.w)
        max_b = max(max_b, node.y + node.h)
    return {"width": round(max_r, 2), "height": round(max_b, 2)}


def section_html_policy_note(repeated_structures: list[dict[str, Any]]) -> str:
    if repeated_structures:
        return "Standard DOM mode detected. Prefer source DOM/class structure first, then componentize repeated groups and control clusters."
    return "Standard DOM mode detected. Prefer source DOM/class structure first."


def render_page_layout_html(manifest: dict[str, Any]) -> str:
    canvas = manifest.get("summary", {}).get("canvas", {})
    width = max(float(canvas.get("width", 0)), 1280.0)
    height = max(float(canvas.get("height", 0)), 900.0)
    lines = [
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width, initial-scale=1" />',
        "<title>Standard HTML Layout Reference</title>",
        "<style>",
        "body{margin:0;background:#eef1f6;font-family:Arial,'Microsoft YaHei',sans-serif;color:#1f2329;}",
        ".canvas{position:relative;margin:0 auto;background:#f5f7fa;overflow:hidden;}",
        ".region,.section{position:absolute;box-sizing:border-box;}",
        ".region{border:1px dashed rgba(23,116,255,.35);background:rgba(23,116,255,.05);}",
        ".section{border:1px solid rgba(38,38,38,.18);background:rgba(255,255,255,.72);border-radius:6px;}",
        ".label{position:absolute;left:8px;top:6px;font-size:12px;line-height:18px;}",
        "</style>",
        "</head>",
        "<body>",
        f'<main class="canvas" style="width:{width}px;height:{height}px">',
    ]
    for region in manifest.get("regions", []):
        bbox = region.get("bbox", {})
        lines.append(
            f'<div class="region" style="left:{bbox.get("x",0)}px;top:{bbox.get("y",0)}px;width:{bbox.get("w",0)}px;height:{bbox.get("h",0)}px">'
            f'<span class="label">{html_lib.escape(str(region.get("name", "")))}</span></div>'
        )
    for section in manifest.get("sections", []):
        bbox = section.get("bbox", {})
        lines.append(
            f'<div class="section" style="left:{bbox.get("x",0)}px;top:{bbox.get("y",0)}px;width:{bbox.get("w",0)}px;height:{bbox.get("h",0)}px">'
            f'<span class="label">{html_lib.escape(str(section.get("title", "")))}</span></div>'
        )
    lines.extend(["</main>", "</body>", "</html>"])
    return "\n".join(lines) + "\n"


def render_page_reference_html(manifest: dict[str, Any]) -> str:
    canvas = manifest.get("summary", {}).get("canvas", {})
    width = max(float(canvas.get("width", 0)), 1280.0)
    height = max(float(canvas.get("height", 0)), 900.0)
    lines = [
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width, initial-scale=1" />',
        "<title>Standard HTML Reference</title>",
        "<style>",
        "body{margin:0;background:#eef1f6;font-family:Arial,'Microsoft YaHei',sans-serif;color:#1f2329;}",
        ".page{position:relative;margin:0 auto;background:#f5f7fa;overflow:hidden;}",
        ".box,.text{position:absolute;box-sizing:border-box;}",
        ".text{white-space:pre-wrap;line-height:1.35;}",
        "</style>",
        "</head>",
        "<body>",
        f'<main class="page" style="width:{width}px;height:{height}px">',
    ]
    for box in manifest.get("visualBoxes", [])[:600]:
        bbox = box.get("bbox", {})
        style_parts = [
            f'left:{bbox.get("x",0)}px',
            f'top:{bbox.get("y",0)}px',
            f'width:{bbox.get("w",0)}px',
            f'height:{bbox.get("h",0)}px',
        ]
        if box.get("background"):
            style_parts.append(f'background:{box.get("background")}')
        if box.get("border"):
            style_parts.append(f'border:{box.get("border")}')
        if box.get("borderRadius"):
            style_parts.append(f'border-radius:{box.get("borderRadius")}')
        lines.append(f'<div class="box" style="{";".join(style_parts)}"></div>')
    for item in manifest.get("texts", [])[:900]:
        bbox = item.get("bbox", {})
        style_parts = [
            f'left:{bbox.get("x",0)}px',
            f'top:{bbox.get("y",0)}px',
            f'width:{bbox.get("w",0)}px',
            f'min-height:{bbox.get("h",0)}px',
            f'font-size:{item.get("fontSize",14)}px',
        ]
        if item.get("color"):
            style_parts.append(f'color:{item.get("color")}')
        lines.append(f'<div class="text" style="{";".join(style_parts)}">{html_lib.escape(str(item.get("text", "")))}</div>')
    lines.extend(["</main>", "</body>", "</html>"])
    return "\n".join(lines) + "\n"


def safe_stem(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-._")
    return stem[:80] if stem else "standard-html"


def ensure_dir(path: Path) -> None:
    os.makedirs(path, exist_ok=True)


def build_manifest(html_file: Path, project_root: Path | None = None) -> dict[str, Any]:
    html = decode_html_file(html_file)
    css_rules = parse_css_rules(html)
    parser = StandardHTMLParser(css_rules)
    parser.feed(html)
    nodes = parser.nodes
    compute_layout(nodes)

    items = extract_text_items(nodes)
    visual_boxes = extract_visual_boxes(nodes)
    regions = infer_regions(nodes, items)
    sections = infer_sections(nodes)
    fields = infer_fields(nodes)
    repeated_structures = infer_repeated_structures(nodes)
    source_inventory = build_content_inventory(sections, regions)
    platform = page_platform(nodes)
    ui_libraries = detect_ui_libraries(project_root, platform)
    chart_libraries = detect_chart_libraries(project_root)
    project_components = scan_project_components(project_root)
    replacement_slots = build_replacement_slots(fields, sections, repeated_structures, ui_libraries, project_components)
    matched_components = build_matched_components_summary(replacement_slots)
    component_matches = build_component_matches(fields, sections, repeated_structures, project_components, primary_ui_library(ui_libraries))
    canvas = canvas_size(nodes)

    return {
        "source": str(html_file),
        "summary": {
            "nodeCount": len(nodes),
            "textCount": len(items),
            "canvas": canvas,
            "classification": "standard-html",
            "analysisMode": "standard-html",
            "pagePlatform": platform,
            "styleRuleCount": len(css_rules),
            "repeatedStructureCount": len(repeated_structures),
            "analysisNote": section_html_policy_note(repeated_structures),
        },
        "texts": items,
        "fields": fields,
        "sections": sections,
        "sourceContentInventory": source_inventory,
        "semanticTerms": semantic_terms(fields, sections, repeated_structures, items),
        "regions": regions,
        "visualBoxes": visual_boxes,
        "tableStructures": [],
        "progressCandidates": [],
        "chartCandidates": [],
        "uiLibraries": ui_libraries,
        "chartLibraries": chart_libraries,
        "projectComponents": project_components[:80],
        "componentMatches": component_matches,
        "matchedComponents": matched_components,
        "similarPages": [],
        "replacementSlots": replacement_slots,
        "repeatedStructures": repeated_structures,
        "sectionFiles": [],
        "coverageReport": {
            "mode": "standard-html-source",
            "totalTextCount": len(items),
            "coveredTextCount": len(items),
            "unassignedCount": 0,
            "duplicateCount": 0,
            "note": "Standard DOM mode: use the original HTML/class structure as Stage 1 source before component replacement.",
        },
        "localRegressionTargets": [
            {
                "owner": group.get("signature", ""),
                "action": "verify-repeated-component-extraction",
                "flags": ["repeated-structure"],
                "path": group.get("componentName", ""),
            }
            for group in repeated_structures[:20]
        ] + [
            {
                "owner": slot.get("slot", ""),
                "action": "verify-candidate-component-is-actually-used-or-removed",
                "flags": ["component-usage-contract"],
                "path": slot.get("componentPath", ""),
            }
            for slot in replacement_slots[:40]
            if str(slot.get("candidate", "")).strip()
        ],
    }


def main() -> int:
    configure_stdout_utf8()
    ap = argparse.ArgumentParser(description="Analyze standard DOM/class-based HTML.")
    ap.add_argument("html_file")
    ap.add_argument("--project-root", default="")
    ap.add_argument("--out-dir", default="output/html-analysis")
    ap.add_argument("--output-name", default="", help="Stable ASCII output stem, e.g. task-1-standard-page.")
    ap.add_argument("--emit-section-html", action="store_true", help="Accepted for interface compatibility; no-op for now.")
    ap.add_argument("--emit-reference-html", action="store_true", help="Emit page helper HTML files.")
    args = ap.parse_args()

    html_file = Path(args.html_file).resolve()
    project_root = Path(args.project_root).resolve() if args.project_root else None
    out_dir = Path(args.out_dir)
    ensure_dir(out_dir)

    manifest = build_manifest(html_file, project_root)
    stem = args.output_name.strip() or safe_stem(html_file)
    json_path = out_dir / f"{stem}.json"
    manifest["source"] = str(html_file)
    json_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    page_layout_path = ""
    whole_page_path = ""
    if args.emit_reference_html:
        page_layout_path = str(out_dir / f"{stem}-page-layout.html")
        Path(page_layout_path).write_text(render_page_layout_html(manifest), encoding="utf-8")
        whole_page_path = str(out_dir / f"{stem}-whole-page-reference.html")
        Path(whole_page_path).write_text(render_page_reference_html(manifest), encoding="utf-8")
        print(f"[OK] wrote {page_layout_path}")
        print(f"[OK] wrote {whole_page_path}")

    print(f"[OK] wrote {json_path}")
    print(json.dumps({
        "source": str(html_file),
        "classification": manifest["summary"]["classification"],
        "pagePlatform": manifest["summary"]["pagePlatform"],
        "repeatedStructureCount": len(manifest.get("repeatedStructures", [])),
        "replacementSlotCount": len(manifest.get("replacementSlots", [])),
        "referenceHtmlFiles": {
            "pageLayoutHtml": page_layout_path,
            "wholePageReferenceHtml": whole_page_path,
        } if args.emit_reference_html else {},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
