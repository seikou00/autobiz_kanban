#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出知识库支持的部署单元 ID（只读，不拉取仓库）。

合并两个来源，输出去重后的 JSON 字符串数组：

1. ``node collect-knowledge.js --listDeployUnits --knowledgePath <path>``（按需 ``npm install``）；
2. ``<knowledgePath>/agents.manifest.json`` 中的 ``deployUnitId``（兼容旧 ``serviceUnitId``）。

保留 collector 顺序，再补充清单独有的 ID。一个来源不可用时只返回另一个来源；
两个来源都不可用时输出 ``[]`` 并以状态码 1 退出。诊断日志输出到 stderr。

与 ``sync_agents.py`` 的区别：后者先删除并重新克隆 ``sys/``，且 collector 成功时不合并清单。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.agents_repo import MANIFEST_NAME, get_agents_root  # noqa: E402
from hooks.render_collected_session_context import (  # noqa: E402
    DEFAULT_KNOWLEDGE_COLLECTOR,
    KnowledgeCollectorError,
    _timing_log,
    list_supported_deploy_units,
)


def _manifest_deploy_ids(knowledge_path: str) -> List[str]:
    """从知识库清单提取 ID；兼容旧 serviceUnits/serviceUnitId 字段。

    只校验部署单元 ID，不复用 ``agents_repo.parse_manifest`` 的全量严格校验，
    避免系统级字段缺失时整份清单不可用。
    """
    manifest_path = Path(knowledge_path) / MANIFEST_NAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KnowledgeCollectorError(f"读取 {manifest_path} 失败: {exc}") from exc
    systems = payload.get("systems") if isinstance(payload, dict) else None
    if not isinstance(systems, list):
        raise KnowledgeCollectorError(f"{manifest_path} 的 systems 必须是数组")

    ids: List[str] = []
    for system_index, system in enumerate(systems):
        units = (
            system.get("deployUnits", system.get("serviceUnits"))
            if isinstance(system, dict) else None
        )
        if not isinstance(units, list):
            raise KnowledgeCollectorError(
                f"{manifest_path} 的 systems[{system_index}].deployUnits 必须是数组"
            )
        for unit_index, unit in enumerate(units):
            unit_id = (
                unit.get("deployUnitId", unit.get("serviceUnitId"))
                if isinstance(unit, dict) else None
            )
            if not isinstance(unit_id, str) or not unit_id.strip():
                raise KnowledgeCollectorError(
                    f"{manifest_path} 的 systems[{system_index}].deployUnits[{unit_index}] "
                    "缺少非空 deployUnitId"
                )
            ids.append(unit_id.strip())
    return list(dict.fromkeys(ids))


def get_supported_deploy_ids(
    collector_script: str,
    *,
    knowledge_path: str,
    node_command: str = "node",
    npm_command: str = "npm",
) -> List[str]:
    """合并两个来源的 ID，保留 collector 顺序，再补充清单独有的 ID。"""
    collector_ids: List[str] = []
    manifest_ids: List[str] = []
    errors: List[str] = []
    try:
        collector_ids = list_supported_deploy_units(
            collector_script,
            knowledge_path=knowledge_path,
            node_command=node_command,
            npm_command=npm_command,
        )
    except KnowledgeCollectorError as exc:
        errors.append(f"collect-knowledge.js: {exc}")
    try:
        manifest_ids = _manifest_deploy_ids(knowledge_path)
    except KnowledgeCollectorError as exc:
        errors.append(f"agents.manifest.json: {exc}")

    if len(errors) == 2:
        raise KnowledgeCollectorError("；".join(errors))
    for error in errors:
        _timing_log("supported.deploy_ids", "warning", error)
    return list(dict.fromkeys([*collector_ids, *manifest_ids]))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="输出 collect-knowledge.js 与 agents.manifest.json 的去重部署单元 ID 数组",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--knowledge-path",
        "--knowledgePath",
        dest="knowledge_path",
        default="",
        help="知识库根目录，默认 <pluginPath>/sys",
    )
    parser.add_argument(
        "--knowledge-collector",
        dest="collector_script",
        default=str(ROOT / "hooks" / DEFAULT_KNOWLEDGE_COLLECTOR),
    )
    parser.add_argument("--node-command", dest="node_command", default="node")
    parser.add_argument("--npm-command", dest="npm_command", default="npm")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    knowledge_path = (args.knowledge_path or "").strip() or str(get_agents_root().resolve())
    try:
        ids = get_supported_deploy_ids(
            args.collector_script,
            knowledge_path=knowledge_path,
            node_command=args.node_command,
            npm_command=args.npm_command,
        )
    except KnowledgeCollectorError as exc:
        _timing_log("supported.deploy_ids", "error", str(exc))
        print("[]")
        return 1
    json.dump(ids, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
