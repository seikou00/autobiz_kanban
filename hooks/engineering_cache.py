"""Short per-key claims for dependency inspection; producers run outside locks."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Callable

from hooks.evidence_kernel import FileLock
from hooks.json_writer_common import atomic_write_json


def read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def resolve(path: Path, producer: Callable[[], dict], *, validate: Callable[[], bool],
            ttl: float = 60, notify: Callable[[str], None] | None = None) -> dict:
    """Only one process resolves a key. Polls never extend its ownership."""
    lock, claim_path = path.with_suffix(".lock"), path.with_suffix(".claim.json")
    with FileLock(lock):
        cached = read(path)
        now = time.time()
        expiry = cached.get("cacheExpiresEpoch")
        if (cached.get("cacheVersion") == 1 and isinstance(cached.get("status"), str)
                and (expiry is None or isinstance(expiry, (int, float)) and expiry > now)):
            result = {**cached, "reused": True, "cacheStatus": "hit", "cachePath": str(path)}
            state = "dependency_hit"
        else:
            claim = read(claim_path)
            if (isinstance(claim.get("expiresEpoch"), (int, float))
                    and isinstance(claim.get("token"), str) and now < claim["expiresEpoch"] <= now + 300):
                result = {"status": "in_progress", "matches": [], "reused": False,
                          "cacheStatus": "in_progress", "cachePath": str(path), "retryAfterSeconds": 2}
                state = "dependency_wait"
            else:
                token = uuid.uuid4().hex
                atomic_write_json(claim_path, {"token": token, "expiresEpoch": now + ttl})
                result, state = None, "dependency_takeover" if claim else "dependency_claimed"
    if notify:
        notify(state)
    if result is not None:
        if result.get("reused") and not validate():
            raise ValueError("engineering_dependency_inputs_changed")
        return result
    try:
        value = producer()
        if not validate():
            raise ValueError("engineering_dependency_inputs_changed")
    except BaseException:
        with FileLock(lock):
            if read(claim_path).get("token") == token:
                claim_path.unlink(missing_ok=True)
        raise
    with FileLock(lock):
        claim = read(claim_path)
        if claim.get("token") != token or claim.get("expiresEpoch", 0) <= time.time():
            raise ValueError("engineering_dependency_claim_expired_or_replaced")
        value = {**value, "cacheVersion": 1, "cacheExpiresEpoch": time.time() + 60 if value.get("status") == "blocked" else None}
        atomic_write_json(path, value)
        claim_path.unlink(missing_ok=True)
    if notify:
        notify("dependency_published")
    return {**value, "reused": False, "cacheStatus": "published", "cachePath": str(path)}
