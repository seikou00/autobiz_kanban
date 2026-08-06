#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描知识库 frontmatter，生成索引与各级入口文档。

替代手写的 ``agents.manifest.json``：知识库每份 ``.md`` 头部携带 ``---`` 围栏的
frontmatter，本模块遍历 ``<pluginPath>/sys/`` 把它们归并成：

- ``sys/knowledge.index.json``  —— 沿用 manifest schema，供 ``agents_repo`` 原样解析
- ``sys/.entries/<sub_product>/index.md``          —— 系统级入口，正文是文档地图
- ``sys/.entries/<sub_product>/<deploy_unit>.md``  —— 单元级入口，正文是文档地图

frontmatter 契约（``type``/``title``/``description``/``sub_product`` 必填）::

    ---
    type: Service Knowledge
    title: 领域术语表
    description: focusone服务的领域术语表
    sub_product: LF39.18
    deploy_unit: LF39.18_focusone
    tags:
      - 术语表
      - 中台导航
    timestamp: 2025-07-29
    ---

``deploy_unit`` 有值即单元级文档，留空/缺省即系统级文档。

解析器只认本契约的字段，用标准库手写（本仓零第三方依赖），不引 PyYAML。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 磁盘布局常量的唯一事实来源在 agents_repo，避免文件名在两处漂移。
from hooks.agents_repo import INDEX_NAME  # noqa: E402

# ---- 契约常量 ------------------------------------------------------------

FENCE = "---"
ENTRIES_DIRNAME = ".entries"
SYSTEM_ENTRY_NAME = "index.md"

REQUIRED_FIELDS = ("type", "title", "description", "sub_product")
KNOWN_TYPES = (
    "product knowledge",
    "service knowledge",
    "component reference",
    "reference",
)

# 只读文件头；正文再长也不读，避免全库扫描把大文档整份加载。
MAX_HEAD_BYTES = 16384

# 生成的索引沿用 agents.manifest.json 的 schema 版本号，供 agents_repo.parse_manifest 原样解析。
INDEX_SCHEMA_VERSION = "v1"

SYSTEM_AGENTS_BASENAME = "AGENTS.MD"   # 取 sub_product 展示名时认这个文件（大小写不敏感）
MAP_HEADING = "## 文档地图"
PLUGIN_ROOT_PLACEHOLDER = "{plugin_root}"   # 与 render_session_context 同名占位符

_LIST_INLINE_RE = re.compile(r"^\[(.*)\]$")
_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|]')


class FrontmatterError(Exception):
    """frontmatter 缺失或不合契约；message 内含修复指导。"""


# ---- frontmatter 解析 ----------------------------------------------------

def _strip_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _split_inline_list(body: str) -> List[str]:
    items = []
    for raw in body.split(","):
        item = _strip_quotes(raw)
        if item:
            items.append(item)
    return items


def parse_frontmatter(text: str) -> Dict[str, Union[str, List[str]]]:
    """解析 ``---`` 围栏内的 frontmatter，返回字段字典。

    支持 ``key: value``、行内列表 ``key: [a, b]``、块式列表（``key:`` 后接 ``  - x``）。
    值两侧引号会被剥离；``#`` 开头的整行按注释跳过；未知字段原样保留。

    缺围栏或围栏未闭合时抛 :class:`FrontmatterError`。
    """
    lines = text.splitlines()
    idx = 0
    # 容忍 BOM 与首部空行
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines) or lines[idx].lstrip("﻿").strip() != FENCE:
        raise FrontmatterError(
            "缺少 frontmatter：文件未以 --- 开头。"
            "修复：在文件首行加一对 --- 围栏，并在其中补齐 "
            "type / title / description / sub_product 四个必填字段"
        )

    idx += 1
    end = -1
    for cursor in range(idx, len(lines)):
        if lines[cursor].strip() == FENCE:
            end = cursor
            break
    if end < 0:
        raise FrontmatterError(
            "frontmatter 围栏未闭合：只找到起始的 ---，没找到结束的 ---。"
            "修复：在 frontmatter 最后一个字段之后单起一行写 ---"
        )

    data: Dict[str, Union[str, List[str]]] = {}
    pending_list_key: Optional[str] = None
    for raw in lines[idx:end]:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # 块式列表项：归属上一个「值为空」的键
        if stripped.startswith("- "):
            if pending_list_key is None:
                continue
            bucket = data.get(pending_list_key)
            if not isinstance(bucket, list):
                bucket = []
                data[pending_list_key] = bucket
            item = _strip_quotes(stripped[2:])
            if item:
                bucket.append(item)
            continue

        if ":" not in stripped:
            continue

        key, _, value = stripped.partition(":")
        key = key.strip()
        if not key:
            continue
        value = value.strip()

        if not value:
            # 可能是块式列表的键；先占位成空列表，遇到 "- x" 再填
            data[key] = []
            pending_list_key = key
            continue

        pending_list_key = None
        inline = _LIST_INLINE_RE.match(value)
        if inline:
            data[key] = _split_inline_list(inline.group(1))
        else:
            data[key] = _strip_quotes(value)

    return data


def normalize_type(value: str) -> str:
    """type 值归一：去首尾空白、合并内部空白、casefold。"""
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


# ---- 扫描 ----------------------------------------------------------------

@dataclass(frozen=True)
class DocEntry:
    path: str          # 相对 sys/ 的 posix 路径
    doc_type: str      # 归一化后的 type
    title: str
    description: str
    sub_product: str
    deploy_unit: str = ""   # 空串 = 系统级文档

    @property
    def is_system_level(self) -> bool:
        return not self.deploy_unit


def _read_head(path: Path) -> str:
    with path.open("rb") as handle:
        raw = handle.read(MAX_HEAD_BYTES)
    return raw.decode("utf-8", errors="ignore")


def _as_text(value: Union[str, List[str], None]) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def scan(sys_root: Union[str, Path]) -> Tuple[List[DocEntry], List[str]]:
    """遍历 ``sys_root`` 下所有 ``.md``，返回 (合格条目, warnings)。

    跳过所有点开头的目录（``.git`` / ``.entries`` / ``.cmbdevclaw`` / ``.idea`` 等），
    其中 ``.entries`` 是本模块自己的产物，必须排除以免二次扫描自吞。

    单个文件不合契约只记 warning 并跳过，不中断整体扫描——沉淀侧不在本仓，
    个别文档写错头不应让整库同步失败。
    """
    root = Path(sys_root)
    entries: List[DocEntry] = []
    warnings: List[str] = []

    if not root.is_dir():
        raise FrontmatterError(
            "知识库根目录不存在: {}。修复：先执行 sync_agents.py 克隆知识库仓".format(root)
        )

    for current, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for filename in sorted(filenames):
            if not filename.lower().endswith(".md"):
                continue
            abs_path = Path(current) / filename
            rel = abs_path.relative_to(root).as_posix()

            try:
                data = parse_frontmatter(_read_head(abs_path))
            except FrontmatterError as exc:
                warnings.append("{}: {}".format(rel, exc))
                continue
            except OSError as exc:
                warnings.append("{}: 读取失败 {}。修复：检查文件权限与编码".format(rel, exc))
                continue

            missing = [f for f in REQUIRED_FIELDS if not _as_text(data.get(f))]
            if missing:
                warnings.append(
                    "{}: frontmatter 缺必填字段 {}。修复：在沉淀时补齐这些字段".format(
                        rel, "/".join(missing)
                    )
                )
                continue

            doc_type = normalize_type(_as_text(data.get("type")))
            if doc_type not in KNOWN_TYPES:
                warnings.append(
                    "{}: type 值 {!r} 不在枚举内。修复：改成 {} 之一".format(
                        rel, _as_text(data.get("type")), " / ".join(KNOWN_TYPES)
                    )
                )
                continue

            entries.append(
                DocEntry(
                    path=rel,
                    doc_type=doc_type,
                    title=_as_text(data.get("title")),
                    description=_as_text(data.get("description")),
                    sub_product=_as_text(data.get("sub_product")),
                    deploy_unit=_as_text(data.get("deploy_unit")),
                )
            )

    return entries, warnings


# ---- 分组 ----------------------------------------------------------------

@dataclass(frozen=True)
class UnitGroup:
    deploy_unit: str
    docs: Tuple[DocEntry, ...]


@dataclass(frozen=True)
class SystemGroup:
    sub_product: str
    system_docs: Tuple[DocEntry, ...]   # deploy_unit 为空的文档
    units: Tuple[UnitGroup, ...]


def group(entries: List[DocEntry]) -> Tuple[List[SystemGroup], List[str]]:
    """按 ``(sub_product, deploy_unit)`` 分组，返回 (分组, warnings)。

    同一 ``deploy_unit`` 出现在多个 ``sub_product`` 下时记 warning，并把该单元归给
    排序最靠前的 sub_product——保证同一份知识库每次生成结果一致。
    """
    warnings: List[str] = []

    owners: Dict[str, List[str]] = {}
    for entry in entries:
        if entry.deploy_unit:
            owners.setdefault(entry.deploy_unit, [])
            if entry.sub_product not in owners[entry.deploy_unit]:
                owners[entry.deploy_unit].append(entry.sub_product)

    owner_of: Dict[str, str] = {}
    for unit_id in sorted(owners):
        candidates = sorted(owners[unit_id])
        owner_of[unit_id] = candidates[0]
        if len(candidates) > 1:
            warnings.append(
                "deploy_unit {!r} 同时出现在 sub_product {} 下，已归给 {!r}。"
                "修复：在沉淀时保证一个部署单元只属于一个 sub_product".format(
                    unit_id, "/".join(candidates), candidates[0]
                )
            )

    system_docs: Dict[str, List[DocEntry]] = {}
    unit_docs: Dict[str, Dict[str, List[DocEntry]]] = {}
    for entry in entries:
        if not entry.deploy_unit:
            system_docs.setdefault(entry.sub_product, []).append(entry)
            continue
        owner = owner_of.get(entry.deploy_unit, entry.sub_product)
        unit_docs.setdefault(owner, {}).setdefault(entry.deploy_unit, []).append(entry)

    groups: List[SystemGroup] = []
    for sub_product in sorted(set(system_docs) | set(unit_docs)):
        units = tuple(
            UnitGroup(
                deploy_unit=unit_id,
                docs=tuple(sorted(unit_docs[sub_product][unit_id], key=lambda d: d.path)),
            )
            for unit_id in sorted(unit_docs.get(sub_product, {}))
        )
        groups.append(
            SystemGroup(
                sub_product=sub_product,
                system_docs=tuple(sorted(system_docs.get(sub_product, []), key=lambda d: d.path)),
                units=units,
            )
        )
    return groups, warnings


# ---- 入口渲染 ------------------------------------------------------------

def render_entry_md(docs: Tuple[DocEntry, ...]) -> str:
    """渲染入口文档正文：``description + path`` 的文档地图。

    路径用 ``{plugin_root}`` 占位符，由 render_session_context 在注入前替换为知识库根目录。
    """
    lines = [MAP_HEADING, ""]
    for doc in docs:
        lines.append(
            "- `{}/{}`: {}".format(PLUGIN_ROOT_PLACEHOLDER, doc.path, doc.description)
        )
    lines.append("")
    return "\n".join(lines)


def _safe_component(name: str) -> str:
    """把 id 转成可用作文件/目录名的片段。"""
    return _UNSAFE_NAME_RE.sub("_", name).strip() or "_"


def _system_display_name(system: SystemGroup) -> str:
    """sub_product 展示名：取该系统下 AGENTS.md 的 title，没有则回落 sub_product id。"""
    for doc in system.system_docs:
        if PurePosixPath(doc.path).name.upper() == SYSTEM_AGENTS_BASENAME:
            return doc.title
    return system.sub_product


# ---- 索引与落盘 ----------------------------------------------------------

def build_index(
    groups: List[SystemGroup], entry_paths: Dict[str, str]
) -> dict:
    """整形为 agents.manifest.json 的 schema。

    ``entry_paths`` 是 :func:`write_entries` 产出的 ``key -> 相对 sys/ 路径`` 映射，
    key 为 ``sub_product`` 或 ``sub_product + "\\0" + deploy_unit``。
    """
    systems = []
    for system in groups:
        units = []
        for unit in system.units:
            units.append(
                {
                    "deployUnitId": unit.deploy_unit,
                    # 展示名无来源：留空由 render_session_context 回落到 UI 传入的 description
                    "description": "",
                    "agentsPath": entry_paths.get(_unit_key(system.sub_product, unit.deploy_unit), ""),
                }
            )
        systems.append(
            {
                "systemId": system.sub_product,
                "description": _system_display_name(system),
                "agentsPath": entry_paths.get(system.sub_product, ""),
                "deployUnits": units,
            }
        )
    return {"schemaVersion": INDEX_SCHEMA_VERSION, "systems": systems}


def _unit_key(sub_product: str, deploy_unit: str) -> str:
    return sub_product + "\0" + deploy_unit


def write_entries(sys_root: Path, groups: List[SystemGroup]) -> Tuple[Dict[str, str], List[str]]:
    """落盘各级入口文档，返回 (key -> 相对路径, warnings)。

    每次重建前清空 ``.entries/``，避免上一轮的残留文件被当成当前索引的一部分。
    无文档的层级不生成空入口。
    """
    warnings: List[str] = []
    entries_root = sys_root / ENTRIES_DIRNAME
    if entries_root.exists():
        shutil.rmtree(str(entries_root))

    paths: Dict[str, str] = {}
    used: Dict[str, str] = {}   # 相对路径 -> 占用它的 key，用于探测重名

    def _claim(rel: str, key: str, label: str) -> str:
        if rel in used:
            candidate = rel
            suffix = 2
            stem, _, ext = rel.rpartition(".")
            while candidate in used:
                candidate = "{}~{}.{}".format(stem, suffix, ext)
                suffix += 1
            warnings.append(
                "{} 与 {} 的入口文件名冲突，已改用 {}。"
                "修复：避免 id 里出现会被替换的字符 \\ / : * ? \" < > |".format(
                    label, used[rel], candidate
                )
            )
            rel = candidate
        used[rel] = label
        paths[key] = rel
        return rel

    for system in groups:
        sub_dir = _safe_component(system.sub_product)
        if system.system_docs:
            rel = _claim(
                "{}/{}/{}".format(ENTRIES_DIRNAME, sub_dir, SYSTEM_ENTRY_NAME),
                system.sub_product,
                system.sub_product,
            )
            target = sys_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_entry_md(system.system_docs), encoding="utf-8")

        for unit in system.units:
            rel = _claim(
                "{}/{}/{}.md".format(ENTRIES_DIRNAME, sub_dir, _safe_component(unit.deploy_unit)),
                _unit_key(system.sub_product, unit.deploy_unit),
                unit.deploy_unit,
            )
            target = sys_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_entry_md(unit.docs), encoding="utf-8")

    return paths, warnings


def build(sys_root: Union[str, Path]) -> dict:
    """扫描 → 分组 → 落盘入口与索引。返回给 sync_agents 的结果摘要。

    单个文档不合契约只记 warning；只有整体不可继续（根目录缺失、落盘失败）才抛
    :class:`FrontmatterError`，由调用方转成 ``ok:false``。
    """
    root = Path(sys_root)
    entries, warnings = scan(root)
    index_path = root / INDEX_NAME

    # 一份 frontmatter 都没扫到 = 尚未迁移的旧知识库。此时**不能**写出空索引，
    # 否则 agents_repo 会优先读到它、再也回落不到手写的 agents.manifest.json。
    # 清掉可能残留的生成物后原样返回，让旧链路继续工作。
    if not entries:
        _remove_generated(root)
        warnings.append(
            "未扫到任何带 frontmatter 的文档，已跳过索引生成、回落 agents.manifest.json。"
            "修复：若知识库已迁移，检查 md 头部是否为 --- 围栏的 frontmatter"
        )
        return {
            "generated": False,
            "indexPath": "",
            "entriesRoot": "",
            "documents": 0,
            "systems": 0,
            "deployUnits": 0,
            "warnings": warnings,
        }

    groups, group_warnings = group(entries)
    warnings.extend(group_warnings)

    entry_paths, entry_warnings = write_entries(root, groups)
    warnings.extend(entry_warnings)

    index = build_index(groups, entry_paths)
    try:
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise FrontmatterError(
            "索引写入失败 {}: {}。修复：检查 sys/ 目录是否可写".format(index_path, exc)
        )

    unit_total = sum(len(system.units) for system in groups)
    if unit_total:
        warnings.append(
            "{} 个部署单元没有展示名来源（frontmatter 无单元名字段），"
            "UI 与注入将回落到 createFeature 传入的 description。"
            "修复：若需知识库掌控展示名，在 frontmatter 契约中补充单元名字段".format(unit_total)
        )

    return {
        "generated": True,
        "indexPath": str(index_path),
        "entriesRoot": str(root / ENTRIES_DIRNAME),
        "documents": len(entries),
        "systems": len(groups),
        "deployUnits": unit_total,
        "warnings": warnings,
    }


def _remove_generated(root: Path) -> None:
    """清掉本模块的产物（索引 + 入口目录），使回落路径不被残留文件干扰。"""
    entries_root = root / ENTRIES_DIRNAME
    if entries_root.is_dir():
        shutil.rmtree(str(entries_root), ignore_errors=True)
    index_path = root / INDEX_NAME
    if index_path.is_file():
        try:
            index_path.unlink()
        except OSError:
            pass
