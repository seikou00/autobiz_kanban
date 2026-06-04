#!/usr/bin/env python3
"""Resolve the next workflow skill for a feature from board_config profiles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.route_checkpoint import resolve_route  # noqa: E402


def _print_text(payload: dict) -> None:
    if not payload.get("ok"):
        print("NEXT_SKILL_FAIL")
        for error in payload.get("errors", []):
            print(f"- {error}")
        return

    print(f"feature: {payload.get('feature', '')}")
    print(f"checkpoint: {payload.get('checkpoint', '')}")
    print(f"workflowProfile: {payload.get('workflowProfile', '')}")
    recommended = payload.get("recommendedNextSkill") or payload.get("nextAction", {}).get("slashSkill", "")
    print(f"recommendedNextSkill: {recommended or 'none'}")
    allowed = payload.get("allowedNextCheckpoints", [])
    print("allowedNextCheckpoints: " + (", ".join(allowed) if allowed else "none"))
    if payload.get("requiresProfileChoice"):
        print("requiresProfileChoice: true")
        for choice in payload.get("profileChoices", []):
            print(
                "- "
                + f"{choice.get('id')}: {choice.get('label')} "
                + f"-> {choice.get('recommendedNextSkill') or 'none'}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve next workflow skill for a feature")
    parser.add_argument("--workspace", "-w", required=True)
    parser.add_argument("--feature", "-f", required=True)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    payload, exit_code = resolve_route(Path(args.workspace), args.feature)
    if args.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        _print_text(payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
