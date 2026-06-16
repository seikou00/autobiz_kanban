#!/usr/bin/env python3
"""Smoke-test init_workspace.py feature_context generation.

Run:
    python3 examples/init_workspace_feature_context_demo.py

The script creates a temporary plugin workspace, uses the plugin-owned
sys/<systemId>/harness.config, runs hooks/init_workspace.py through its CLI,
then prints the generated
feature_context.json and the inspect_state.py view after filling one service
code directory.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INIT_WORKSPACE = ROOT / "hooks" / "init_workspace.py"
INSPECT_STATE = ROOT / "inspect_state.py"


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        text=True,
        capture_output=True,
        check=False,
    )
    print("$ " + " ".join(args))
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    print()
    return result


def find_plugin_harness(system_id: str) -> Path:
    harness_root = ROOT / "sys" / system_id
    if not harness_root.is_dir():
        raise SystemExit(f"Plugin harness directory not found: {harness_root}")

    matches = sorted(harness_root.rglob("harness.config"))
    if not matches:
        raise SystemExit(f"Plugin harness.config not found under: {harness_root}")
    return matches[0]


def add_sysid(project_dir: Path, system_id: str) -> None:
    project_md = project_dir / ".autobizdevops" / "PROJECT.md"
    content = project_md.read_text(encoding="utf-8")
    if "SysId" not in content and "sysid" not in content.lower():
        content += f"\n## System\n\n- **SysId**: {system_id}\n"
    project_md.write_text(content, encoding="utf-8")


def fill_one_service_code_dir(context_path: Path) -> None:
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context["serviceCodeDirectories"]["LF3905_compliancemng"] = (
        r"D:\workspace\LF39.05_BCWplus_cust\后台服务\零售客户经营\LF39.05_bccompliancemng"
    )
    context_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run init_workspace.py feature_context demo")
    parser.add_argument("--workspace", default=None, help="collection workspace to create/use")
    parser.add_argument("--project", default="demo_project", help="project code")
    parser.add_argument("--feature", default="demo_feature", help="feature id")
    parser.add_argument("--system-id", default="LF3905", help="system id for PROJECT.md")
    args = parser.parse_args()

    collection = Path(args.workspace) if args.workspace else Path(
        tempfile.mkdtemp(prefix="autobiz-init-demo-")
    )
    collection = collection.resolve()
    project_dir = collection / args.project
    project_dir.mkdir(parents=True, exist_ok=True)

    print(f"Demo workspace: {collection}")
    print()

    run_command(
        [
            sys.executable,
            str(INIT_WORKSPACE),
            "--mode",
            "createProject",
            "--workspace",
            str(collection),
            "--project",
            args.project,
        ]
    )

    add_sysid(project_dir, args.system_id)
    harness_path = find_plugin_harness(args.system_id)
    print(f"Using plugin harness.config: {harness_path}")
    print()

    run_command(
        [
            sys.executable,
            str(INIT_WORKSPACE),
            "--mode",
            "createFeature",
            "--workspace",
            str(collection),
            "--project",
            args.project,
            "--feature",
            args.feature,
        ]
    )

    context_path = (
        project_dir
        / ".autobizdevops"
        / "features"
        / args.feature
        / "feature_context.json"
    )
    print("Generated feature_context.json:")
    print(context_path.read_text(encoding="utf-8").rstrip())
    print()

    fill_one_service_code_dir(context_path)
    print("After filling one serviceCodeDirectories entry:")
    print(context_path.read_text(encoding="utf-8").rstrip())
    print()

    inspect = run_command(
        [
            sys.executable,
            str(INSPECT_STATE),
            "--mode",
            "run",
            "--workspace",
            str(collection),
            "--project",
            args.project,
            "--feature",
            args.feature,
        ]
    )
    payload = json.loads(inspect.stdout)
    print("inspect_state.py run.featureContext:")
    print(json.dumps(payload["run"]["featureContext"], ensure_ascii=False, indent=2))
    print()
    print(f"Kept demo workspace at: {collection}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
