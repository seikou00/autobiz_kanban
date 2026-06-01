#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统上下文加载脚本
用法:
    python hooks/load_sys_agents.py [workspace_path]
    # 或从其他脚本导入调用 load_sys_agents()
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, Optional

try:
    from paths import get_project_md_path, get_sys_agents_md_path, get_workspace, is_initialized
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from paths import get_project_md_path, get_sys_agents_md_path, get_workspace, is_initialized  # type: ignore[no-redef]


def _extract_sysid(project_md_content: str) -> Optional[str]:
    patterns = [
        r"(?i)[-*]\s*\*\*sysid\*\*\s*[:：]\s*(.+)",
        r"(?i)[-*]\s*sysid\s*[:：]\s*(.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, project_md_content)
        if not match:
            continue
        value = match.group(1).strip()
        if value and not value.startswith("[") and value != "待填写":
            return value
    return None


def load_sys_agents(workspace: Optional[Path] = None) -> Dict[str, object]:
    ws = (workspace or get_workspace()).resolve()
    result = {
        "ok": True,
        "skipped": False,
        "sysid": None,
        "agents_md_path": None,
        "content": "",
        "message": "",
    }

    if not is_initialized(ws):
        result["skipped"] = True
        result["message"] = f"跳过可选系统上下文加载：Workspace 未初始化: {ws / '.autobizdevops'}"
        return result

    project_md = get_project_md_path(ws)
    if not project_md.exists():
        result["skipped"] = True
        result["message"] = f"跳过可选系统上下文加载：PROJECT.md 不存在: {project_md}"
        return result

    sysid = _extract_sysid(project_md.read_text(encoding="utf-8"))
    if not sysid:
        result["skipped"] = True
        result["message"] = (
            "跳过可选系统上下文加载："
            "PROJECT.md 中未声明有效 SysId"
        )
        return result

    agents_md = get_sys_agents_md_path(sysid, ws)
    result["sysid"] = sysid
    result["agents_md_path"] = str(agents_md)

    if not agents_md.exists():
        result["skipped"] = True
        result["message"] = (
            f"跳过可选系统上下文加载："
            f"SysId={sysid} 对应的 AGENTS.md 不存在: {agents_md}"
        )
        return result

    result["content"] = agents_md.read_text(encoding="utf-8")
    result["message"] = f"成功加载 sys/{sysid}/AGENTS.md ({len(result['content'])} 字符)"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Load sys AGENTS.md context")
    parser.add_argument("workspace", help="Workspace path (required)")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.exists():
        print(f"ERROR: Workspace does not exist: {workspace}", file=sys.stderr)
        sys.exit(1)

    result = load_sys_agents(workspace)
    print(result["message"])
    if result["ok"] and result["content"]:
        print("\n--- AGENTS.md 内容 ---\n")
        print(result["content"])

    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
