#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Workspace 初始化前置校验脚本
用法:
    python hooks/init_validate.py precheck
    python hooks/init_validate.py precheck --workspace /path/to/workspace
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from paths import (
    get_autobizdevops_dir,
    get_project_md_path,
    get_state_md_path,
    get_workspace,
)

BLOCK_EXIT_CODE = 2


def _fail(message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result = {"ok": False, "message": message}
    if details:
        result.update(details)
    return result


def _ok(message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result = {"ok": True, "message": message}
    if details:
        result.update(details)
    return result


def validate_precheck(workspace: Path) -> Dict[str, Any]:
    errors: List[str] = []

    abdo_dir = get_autobizdevops_dir(workspace)
    if not abdo_dir.exists():
        errors.append(f"Workspace 未初始化: {get_autobizdevops_dir(workspace)}")

    project_md = get_project_md_path(workspace)
    if not project_md.exists():
        errors.append(f"PROJECT.md 不存在: {project_md}")

    state_md = get_state_md_path(workspace)
    if not state_md.exists():
        errors.append(f"STATE.md 不存在: {state_md}")

    if errors:
        return _fail("前置检查未通过", {"errors": errors})
    return _ok("前置检查通过")


def main() -> int:
    parser = argparse.ArgumentParser(description="Workspace 初始化前置校验脚本")
    parser.add_argument(
        "stage",
        choices=["precheck"],
        help="校验阶段",
    )
    parser.add_argument("--workspace", "-w", default=".", help="workspace 路径（默认当前目录）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON，不输出可读文本")
    args = parser.parse_args()

    workspace = get_workspace(args.workspace)

    if args.stage == "precheck":
        result = validate_precheck(workspace)
    else:
        result = _fail("未知 stage")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "通过" if result["ok"] else " 未通过"
        print(f"[{status}] {result['message']}")
        for err in result.get("errors", []):
            print(f"   - {err}")
        for warning in result.get("warnings", []):
            print(f"   - WARNING: {warning}")

    return 0 if result["ok"] else BLOCK_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
