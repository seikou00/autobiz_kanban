#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Initialize code-workspace AGENTS.md from sys/{projectCode}/AGENTS.md."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Mapping

try:
    from paths import get_sys_agents_md_path
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from paths import get_sys_agents_md_path  # type: ignore[no-redef]


DEFAULT_SYSTEM_NO = "lf39"
PROJECT_CODE_ENV = "projectCode"
SYSTEM_NO_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class DevAgentsInitError(Exception):
    """Raised when AGENTS.md cannot be initialized safely."""


def resolve_system_no(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    system_no = source.get(PROJECT_CODE_ENV, "").strip()
    return system_no or DEFAULT_SYSTEM_NO


def validate_system_no(system_no: str) -> None:
    if not SYSTEM_NO_PATTERN.fullmatch(system_no):
        raise DevAgentsInitError(
            "invalid projectCode: only letters, digits, underscores, and hyphens are allowed"
        )


def init_dev_agents(
    code_workspace: Path,
    *,
    env: Mapping[str, str] | None = None,
    plugin_root: Path | None = None,
) -> dict[str, object]:
    workspace = code_workspace.expanduser().resolve(strict=False)
    if not workspace.is_dir():
        raise DevAgentsInitError(f"code workspace does not exist: {workspace}")

    system_no = resolve_system_no(env)
    validate_system_no(system_no)

    source = get_sys_agents_md_path(system_no, plugin_root)
    target = workspace / "AGENTS.md"

    result = {
        "ok": True,
        "created": False,
        "skipped": False,
        "system_no": system_no,
        "source": str(source),
        "target": str(target),
        "message": "",
    }

    if target.exists():
        result["skipped"] = True
        result["message"] = f"AGENTS.md already exists: {target}"
        return result

    if not source.is_file():
        raise DevAgentsInitError(f"sys AGENTS.md not found: {source}")

    shutil.copyfile(source, target)
    result["created"] = True
    result["message"] = f"AGENTS.md initialized from sys/{system_no}/AGENTS.md: {target}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize Dev AGENTS.md from sys projectCode")
    parser.add_argument("--code-workspace", required=True, help="Code workspace path")
    args = parser.parse_args()

    try:
        result = init_dev_agents(Path(args.code_workspace))
    except DevAgentsInitError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(result["message"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
