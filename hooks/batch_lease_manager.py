#!/usr/bin/env python3
"""CLI façade for durable parallel batch leases."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import resolve_feature, resolve_workspace
from hooks.parallel_runtime import (
    DEFAULT_TTL_SECONDS,
    HEARTBEAT_SECONDS,
    acquire_lease,
    check_lease,
    reclaim_lease,
    release_lease,
    renew_lease,
)


def _emit(ok: bool, **payload: Any) -> int:
    print(json.dumps({"ok": ok, **payload}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _heartbeat(args: argparse.Namespace, workspace: Path, feature: str) -> int:
    if args.ttl_seconds <= 0 or args.interval_seconds <= 0 or args.max_seconds <= 0:
        return _emit(False, error="heartbeat_timing_must_be_positive")

    stopped = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    started = time.monotonic()
    pid_path = Path(args.pid_file).expanduser() if args.pid_file else None
    if pid_path is not None:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()), encoding="utf-8")

    try:
        while not stopped:
            lease = renew_lease(
                workspace,
                feature,
                args.run_id,
                args.batch_id,
                args.owner_token,
                ttl_seconds=args.ttl_seconds,
            )
            print(json.dumps({"ok": True, "lease": lease}, ensure_ascii=False), flush=True)
            elapsed = time.monotonic() - started
            remaining = args.max_seconds - elapsed
            if remaining <= 0:
                break
            time.sleep(min(args.interval_seconds, remaining))
        return 0
    except (ValueError, OSError) as exc:
        _emit(False, error=str(exc))
        return 1
    finally:
        if pid_path is not None:
            try:
                pid_path.unlink(missing_ok=True)
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage parallel Code batch leases")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("acquire", "renew", "release", "check", "reclaim", "heartbeat"):
        item = subparsers.add_parser(command)
        item.add_argument("--workspace")
        item.add_argument("--feature", required=True)
        item.add_argument("--run-id", required=True)
        item.add_argument("--batch-id", required=True)
        item.add_argument("--owner-token")
        item.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
        if command == "heartbeat":
            item.add_argument("--interval-seconds", type=int, default=HEARTBEAT_SECONDS)
            item.add_argument("--max-seconds", type=int, default=DEFAULT_TTL_SECONDS)
            item.add_argument("--pid-file")
        if command == "release":
            item.add_argument("--final-status", default="pending")
        if command == "reclaim":
            item.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        if args.command == "heartbeat":
            if not args.owner_token:
                return _emit(False, error="owner_token_required")
            return _heartbeat(args, workspace, feature)
        if args.command == "acquire":
            lease = acquire_lease(workspace, feature, args.run_id, args.batch_id, ttl_seconds=args.ttl_seconds, owner_token=args.owner_token)
            return _emit(True, lease=lease)
        if args.command in {"check", "renew", "release"} and not args.owner_token:
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
