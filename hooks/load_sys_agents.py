#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统上下文加载脚本
用法:
    python hooks/load_sys_agents.py [workspace_path]
    # 或从其他脚本导入调用 load_sys_agents()
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, Optional

try:
    from init_dev_agents import DevAgentsInitError, resolve_system_no, validate_system_no
    from paths import get_sys_agents_md_path, get_workspace
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from init_dev_agents import DevAgentsInitError, resolve_system_no, validate_system_no  # type: ignore[no-redef]
    from paths import get_sys_agents_md_path, get_workspace  # type: ignore[no-redef]


def load_sys_agents(workspace: Optional[Path] = None) -> Dict[str, object]:
    # workspace is kept for CLI/import compatibility; sys selection no longer
    # depends on .autobizdevops/PROJECT.md.
    result = {
        "ok": True,
        "skipped": False,
        "sysid": None,
        "system_no": None,
        "agents_md_path": None,
        "content": "",
        "message": "",
    }

    system_no = resolve_system_no()
    try:
        validate_system_no(system_no)
    except DevAgentsInitError as error:
        result["ok"] = False
        result["message"] = f"系统上下文加载失败：{error}"
        return result

    agents_md = get_sys_agents_md_path(system_no)
    result["sysid"] = system_no
    result["system_no"] = system_no
    result["agents_md_path"] = str(agents_md)

    if not agents_md.exists():
        result["skipped"] = True
        result["message"] = (
            f"跳过可选系统上下文加载："
            f"projectCode={system_no} 对应的 AGENTS.md 不存在: {agents_md}"
        )
        return result

    result["content"] = agents_md.read_text(encoding="utf-8")
    result["message"] = f"成功加载 sys/{system_no}/AGENTS.md ({len(result['content'])} 字符)"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Load sys AGENTS.md context")
    parser.add_argument("workspace", nargs="?", help="Workspace path (kept for compatibility)")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve() if args.workspace else get_workspace()
    if args.workspace and not workspace.exists():
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
