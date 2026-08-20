#!/usr/bin/env python3
"""CLI façade for durable parallel batch leases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from hooks.json_writer_common import resolve_feature, resolve_workspace
from hooks.parallel_runtime import acquire_lease, check_lease, reclaim_lease, release_lease, renew_lease


def _emit(ok: bool, **payload: Any) -> int:
    print(json.dumps({"ok": ok, **payload}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage parallel Code batch leases")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("acquire", "renew", "release", "check", "reclaim"):
        item = subparsers.add_parser(command)
        item.add_argument("--workspace")
        item.add_argument("--feature", required=True)
        item.add_argument("--run-id", required=True)
        item.add_argument("--batch-id", required=True)
        item.add_argument("--owner-token")
        item.add_argument("--ttl-seconds", type=int, default=900)
        if command == "release":
            item.add_argument("--final-status", default="pending")
        if command == "reclaim":
            item.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        if args.command == "acquire":
            lease = acquire_lease(workspace, feature, args.run_id, args.batch_id, ttl_seconds=args.ttl_seconds, owner_token=args.owner_token)
            return _emit(True, lease=lease)
        if not args.owner_token:
            return _emit(False, error="owner_token_required")
        if args.command == "check":
            return _emit(True, valid=check_lease(workspace, feature, args.run_id, args.batch_id, args.owner_token))
        if args.command == "reclaim":
            return _emit(True, reclaimed=reclaim_lease(workspace, feature, args.run_id, args.batch_id, force=args.force))
        if args.command == "renew":
            return _emit(True, lease=renew_lease(workspace, feature, args.run_id, args.batch_id, args.owner_token, ttl_seconds=args.ttl_seconds))
        release_lease(workspace, feature, args.run_id, args.batch_id, args.owner_token, final_status=args.final_status)
        return _emit(True)
    except (ValueError, OSError) as exc:
        return _emit(False, error=str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
