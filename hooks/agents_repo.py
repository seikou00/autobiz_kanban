#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared helpers for the agents knowledge-base repo (service units + AGENTS.md).

This module is the single source of truth shared by:
- ``hooks/sync_agents.py``           (UI 触发：克隆/更新 agents 仓库 + 产出 service units)
- ``hooks/render_session_context.py`` (createFeature 时：把选中服务单元映射到 AGENTS.md 注入)

它统一定义三件事，避免两个脚本各写一份：
1. 克隆缓存的磁盘布局：``<pluginPath>/sys/``。
2. 清单 schema：``agents.manifest.json``——把 system_id 与其下的后端单元 id
   (serviceUnitId) 以及每个系统的 AGENTS.md 对应起来。
3. 清单的解析/校验、serviceUnitId -> systemId 的索引、以及 sync 脚本输出形状的整形。

清单（克隆后位于 ``<pluginPath>/sys/agents.manifest.json``）::

    {
      "schemaVersion": "autobizdevops.agents.manifest.v1",
      "systems": [
        {
          "systemId": "LF39",
          "systemName": "外联服务系统",
          "agents": "LF39/AGENTS.md",
          "serviceUnits": [
            { "serviceUnitId": "LF39.18_Outservice", "name": "外联出站服务" }
          ]
        }
      ]
    }
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.paths import get_sys_agents_md_path  # noqa: E402

# ---- 磁盘布局 / schema 常量（唯一事实来源）-------------------------------

AGENTS_DIRNAME = "sys"
MANIFEST_NAME = "agents.manifest.json"
AGENTS_MD_NAME = "AGENTS.md"
MANIFEST_SCHEMA_VERSION = "v1"
SYNC_SCHEMA_VERSION = "autobizdevops.agents.sync.v1"


class AgentsManifestError(Exception):
    """Raised when agents.manifest.json is missing or fails schema validation."""


@dataclass(frozen=True)
class ServiceUnit:
    service_unit_id: str
    name: str = ""          # 展示名（清单 description；兼容旧 name）→ §4 引用范围
    agents_rel: str = ""    # 单元级 description.md 相对路径（清单 agentsPath）；空则无独立 md


@dataclass(frozen=True)
class SystemEntry:
    system_id: str
    system_name: str = ""
    agents_rel: str = ""  # relative to agents root; default "<systemId>/AGENTS.md"
    service_units: Tuple[ServiceUnit, ...] = ()

    def agents_relpath(self) -> str:
        return self.agents_rel or f"{self.system_id}/{AGENTS_MD_NAME}"


@dataclass(frozen=True)
class Manifest:
    schema_version: str
    systems: Tuple[SystemEntry, ...]


# ---- 路径助手 ------------------------------------------------------------

def _plugin_root(plugin_root: Optional[Path] = None) -> Path:
    return Path(plugin_root) if plugin_root is not None else ROOT


def get_agents_root(plugin_root: Optional[Path] = None) -> Path:
    """克隆缓存根目录 ``<pluginPath>/sys/``。"""
    return _plugin_root(plugin_root) / AGENTS_DIRNAME


def get_manifest_path(plugin_root: Optional[Path] = None) -> Path:
    return get_agents_root(plugin_root) / MANIFEST_NAME


def _safe_join(root: Path, rel: str) -> Path:
    """把 ``rel`` 拼到 ``root`` 下并防目录穿越（绝对路径 / ``..`` 越界）。"""
    if not rel or not isinstance(rel, str):
        raise AgentsManifestError("agents 路径不能为空")
    candidate = Path(rel)
    if candidate.is_absolute():
        raise AgentsManifestError(f"agents 路径不能为绝对路径: {rel}")
    resolved = (root / candidate).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise AgentsManifestError(f"agents 路径越界 sys/ 目录: {rel}")
    return resolved


def agents_path_for_system(system: SystemEntry, plugin_root: Optional[Path] = None) -> Path:
    """系统 AGENTS.md 的绝对路径；默认布局复用 paths.get_sys_agents_md_path。"""
    root = get_agents_root(plugin_root)
    if not system.agents_rel:
        return get_sys_agents_md_path(system.system_id, _plugin_root(plugin_root))
    return _safe_join(root, system.agents_rel)


def read_agents_md(system: SystemEntry, plugin_root: Optional[Path] = None) -> Optional[str]:
    path = agents_path_for_system(system, plugin_root)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="ignore")


# ---- 清单解析 / 校验 -----------------------------------------------------

def parse_manifest(data: object) -> Manifest:
    if not isinstance(data, dict):
        raise AgentsManifestError("manifest 根必须是对象")
    schema_version = data.get("schemaVersion", MANIFEST_SCHEMA_VERSION)
    if not isinstance(schema_version, str) or not schema_version:
        raise AgentsManifestError("schemaVersion 必须是非空字符串")

    raw_systems = data.get("systems")
    if not isinstance(raw_systems, list):
        raise AgentsManifestError("systems 必须是数组")

    systems: List[SystemEntry] = []
    seen_systems: set[str] = set()
    seen_units: dict[str, str] = {}  # serviceUnitId -> systemId（全局唯一）

    for idx, raw in enumerate(raw_systems):
        ctx = f"systems[{idx}]"
        if not isinstance(raw, dict):
            raise AgentsManifestError(f"{ctx} 必须是对象")

        system_id = raw.get("systemId")
        if not isinstance(system_id, str) or not system_id.strip():
            raise AgentsManifestError(f"{ctx}.systemId 必须是非空字符串")
        system_id = system_id.strip()
        if system_id in seen_systems:
            raise AgentsManifestError(f"systemId 重复: {system_id}")
        seen_systems.add(system_id)

        # 展示名：新字段 description 优先，兼容旧 systemName。
        system_name = raw.get("description", raw.get("systemName", ""))
        if not isinstance(system_name, str):
            raise AgentsManifestError(f"{ctx}.description 必须是字符串")

        # 系统级 AGENTS.md 路径：新字段 agentsPath 优先，兼容旧 agents。
        agents_rel = raw.get("agentsPath", raw.get("agents", ""))
        if not isinstance(agents_rel, str):
            raise AgentsManifestError(f"{ctx}.agentsPath 必须是字符串")
        if agents_rel:
            # 提前校验路径合法性（穿越/绝对路径），失败即报错。
            _safe_join(Path("/__agents_root__"), agents_rel)

        raw_units = raw.get("serviceUnits")
        if not isinstance(raw_units, list):
            actual = "缺失" if raw_units is None else f"当前是 {type(raw_units).__name__}"
            raise AgentsManifestError(
                f"{ctx}.serviceUnits 必须是数组（{actual}），系统 {system_id}"
            )

        units: List[ServiceUnit] = []
        for uidx, raw_unit in enumerate(raw_units):
            uctx = f"{ctx}.serviceUnits[{uidx}]"
            if not isinstance(raw_unit, dict):
                raise AgentsManifestError(f"{uctx} 必须是对象")
            unit_id = raw_unit.get("serviceUnitId")
            if not isinstance(unit_id, str) or not unit_id.strip():
                raise AgentsManifestError(f"{uctx}.serviceUnitId 必须是非空字符串")
            unit_id = unit_id.strip()
            if unit_id in seen_units:
                raise AgentsManifestError(
                    f"serviceUnitId 全局重复: {unit_id} "
                    f"(系统 {seen_units[unit_id]} 与 {system_id})"
                )
            seen_units[unit_id] = system_id
            # 展示名：description 优先，兼容旧 name。
            name = raw_unit.get("description", raw_unit.get("name", ""))
            if not isinstance(name, str):
                raise AgentsManifestError(f"{uctx}.description 必须是字符串")
            # 单元级 description.md 路径（新增，可空）。
            unit_agents_rel = raw_unit.get("agentsPath", "")
            if not isinstance(unit_agents_rel, str):
                raise AgentsManifestError(f"{uctx}.agentsPath 必须是字符串")
            if unit_agents_rel:
                _safe_join(Path("/__agents_root__"), unit_agents_rel)
            units.append(
                ServiceUnit(service_unit_id=unit_id, name=name, agents_rel=unit_agents_rel)
            )

        systems.append(
            SystemEntry(
                system_id=system_id,
                system_name=system_name,
                agents_rel=agents_rel,
                service_units=tuple(units),
            )
        )

    return Manifest(schema_version=schema_version, systems=tuple(systems))


def load_manifest(plugin_root: Optional[Path] = None) -> Manifest:
    path = get_manifest_path(plugin_root)
    if not path.is_file():
        raise AgentsManifestError(f"未找到清单文件，请先同步 agents 仓库: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AgentsManifestError(f"清单 JSON 解析失败: {path}:{exc.lineno}:{exc.colno}") from exc
    return parse_manifest(data)


def index_units(manifest: Manifest) -> Dict[str, str]:
    """serviceUnitId -> systemId（清单已保证全局唯一）。"""
    return {
        unit.service_unit_id: system.system_id
        for system in manifest.systems
        for unit in system.service_units
    }


def systems_by_id(manifest: Manifest) -> Dict[str, SystemEntry]:
    return {system.system_id: system for system in manifest.systems}


def index_unit_pairs(manifest: Manifest) -> Dict[str, Tuple[SystemEntry, ServiceUnit]]:
    """serviceUnitId -> (所属系统, 单元)；注入段一步取到系统级与单元级两条路径。"""
    return {
        unit.service_unit_id: (system, unit)
        for system in manifest.systems
        for unit in system.service_units
    }


def sys_abspath(rel: str, plugin_root: Optional[Path] = None) -> Path:
    """清单相对路径 -> sys/ 下绝对路径（防目录穿越）。空/非法抛 AgentsManifestError。"""
    return _safe_join(get_agents_root(plugin_root), rel)


def sys_display(rel: str, plugin_root: Optional[Path] = None) -> str:
    """sys/ 前缀的展示路径（字符串拼接，避免 macOS symlink 解析偏差）。"""
    root = get_agents_root(plugin_root)
    return f"{root.name}/{rel}".replace("\\", "/")


# ---- sync 输出整形（与 git 解耦，便于单测）-------------------------------

def build_sync_payload(
    plugin_root: Optional[Path] = None,
    *,
    repo_info: Optional[dict] = None,
) -> dict:
    """读取已克隆的清单，整形为 sync_agents.py 打到 stdout 的形状。

    与 git 操作解耦：只要 ``<pluginPath>/sys/`` 里有合法清单即可调用，便于测试。
    清单缺失/非法时抛 AgentsManifestError，由调用方转为 ok:false。
    """
    manifest = load_manifest(plugin_root)
    agents_root = get_agents_root(plugin_root)

    supported_units: List[str] = []
    systems_payload: List[dict] = []
    ready_count = 0
    for system in manifest.systems:
        agents_path = agents_path_for_system(system, plugin_root)
        agents_ready = agents_path.is_file()
        if agents_ready:
            ready_count += 1
        # 显示路径直接由清单推导（如 "sys/LF39/AGENTS.md"），不依赖文件系统
        # 解析，避免 macOS /var->/private/var 等 symlink 造成的相对路径偏差。
        agents_rel_display = f"{agents_root.name}/{system.agents_relpath()}".replace("\\", "/")
        units_payload = []
        for unit in system.service_units:
            supported_units.append(unit.service_unit_id)
            units_payload.append({"serviceUnitId": unit.service_unit_id, "name": unit.name})
        systems_payload.append(
            {
                "systemId": system.system_id,
                "systemName": system.system_name,
                "agentsReady": agents_ready,
                "agentsPath": agents_rel_display,
                "serviceUnits": units_payload,
            }
        )

    message = (
        f"agents 仓库已同步：{len(manifest.systems)} 个系统、"
        f"{len(supported_units)} 个服务单元，AGENTS.md 就绪 {ready_count}/{len(manifest.systems)}"
    )
    return {
        "ok": True,
        "schemaVersion": SYNC_SCHEMA_VERSION,
        "message": message,
        "repo": dict(repo_info or {}),
        # 知识库落盘路径（克隆缓存根 <pluginPath>/sys/）；与 repo 同级，供宿主写进 board.json
        # 的 inspectCommands.<platform>.knowledge_path。
        "knowledge_path": str(agents_root),
        "supported_service_units": supported_units,
        "systems": systems_payload,
    }
