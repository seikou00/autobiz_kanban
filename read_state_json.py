#!/usr/bin/env python3
"""Read .autobizdevops/state.json without falling back to STATE.md.

Usage:
    python read_state_json.py --workspace <project-workspace>
    python read_state_json.py --workspace <collection-workspace> --project <name>
    python read_state_json.py --workspace <project-workspace> --feature <slug>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.state_store import (  # type: ignore[import-untyped]
    get_state_json_path,
    load_state_json_records_result,
)


SCHEMA_VERSION = "autobizdevops.state.read.v1"


def _resolve_state_workspace(workspace: Path, project: str | None) -> Path:
    workspace = workspace.resolve()
    if not workspace.is_dir():
        print(f"workspace 不存在: {workspace}", file=sys.stderr)
        sys.exit(1)

    if not project:
        return workspace

    project_workspace = (workspace / project).resolve()
    try:
        project_workspace.relative_to(workspace)
    except ValueError:
        print(f"project 路径越界: {project}", file=sys.stderr)
        sys.exit(1)

    if not project_workspace.is_dir():
        print(f"project 不存在: {project_workspace}", file=sys.stderr)
        sys.exit(1)

    return project_workspace


def _build_payload(workspace: Path, feature: str | None = None) -> tuple[dict[str, Any], int]:
    result = load_state_json_records_result(workspace)
    state_json_path = get_state_json_path(workspace)
    errors = list(result.errors)

    if not result.exists:
        errors.append(f"state.json 未找到: {state_json_path}")

    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "workspace": str(workspace),
        "stateJsonPath": str(state_json_path),
        "source": result.source,
        "exists": result.exists,
        "ok": result.exists and not errors,
        "errors": errors,
    }

    if feature:
        record = result.records.get(feature)
        if result.exists and not result.errors and record is None:
            payload["ok"] = False
            payload["errors"].append(f"feature '{feature}' 未在 state.json 中找到")
        payload["feature"] = feature
        payload["record"] = record
        payload["checkpoint"] = record.get("checkpoint", "") if record else ""
    else:
        payload["records"] = result.records

    return payload, 0 if payload["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Read .autobizdevops/state.json only")
    parser.add_argument("--workspace", required=True, help="项目工作区；搭配 --project 时为项目集合工作区")
    parser.add_argument("--project", default=None, help="project name")
    parser.add_argument("--feature", default=None, help="feature slug")
    args = parser.parse_args()

    workspace = _resolve_state_workspace(Path(args.workspace), args.project)
    payload, exit_code = _build_payload(workspace, args.feature)
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
