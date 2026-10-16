"""Bounded, sharded cache telemetry. No queries, claims, or business writes."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from hooks.evidence_kernel import FileLock
from hooks.json_writer_common import atomic_write_json, feature_dir

MAX_EVENTS = 200


def read_metrics(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def observe(workspace: Path, feature: str, shard: str, event: str, **fields) -> None:
    # Shards are repository digests or the literal runtime shard. Never store
    # bearer/claim tokens, raw argv, source text or model-authored payloads.
    if shard != "runtime" and not re.fullmatch(r"[a-f0-9]{64}", shard):
        raise ValueError("engineering_invalid_observation_shard")
    base = feature_dir(workspace, feature) / ".engineering" / "observations"
    path = base / (shard + ".json")
    safe = {k: v for k, v in fields.items() if k in
            {"runId", "batchId", "taskIds", "factId", "cacheKey", "reasonCode", "generation", "queryKey", "contextDigest", "count", "stage", "durationMs"}}
    with FileLock(base / (shard + ".lock")):
        state = read_metrics(path)
        counts = {k: v for k, v in (state.get("counts") or {}).items() if isinstance(v, int)} if isinstance(state.get("counts"), dict) else {}
        counts[event] = counts.get(event, 0) + 1
        versions = state.get("versions", {}) if isinstance(state.get("versions"), dict) else {}
        query_key, version = safe.get("queryKey"), safe.get("factId") or safe.get("cacheKey")
        if query_key and version:
            old = versions.pop(query_key, None)
            if old and old != version:
                name = "fact_invalidated" if safe.get("factId") else "dependency_invalidated"
                counts[name] = counts.get(name, 0) + 1
            versions[query_key] = version
            versions = dict(list(versions.items())[-512:])
        previous = state.get("events", []) if isinstance(state.get("events"), list) else []
        events = [*[item for item in previous if isinstance(item, dict) and isinstance(item.get("at"), (int, float))], {"event": event, "at": time.time(), **safe}][-MAX_EVENTS:]
        atomic_write_json(path, {"schemaVersion": "autodev.engineering.observations.v1", "counts": counts, "events": events, "versions": versions})
