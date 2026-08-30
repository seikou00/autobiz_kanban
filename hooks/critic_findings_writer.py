#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PostToolUse(task): append critic-autodev findings to an owned JSONL ledger."""

from __future__ import print_function

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.specs_hook_context import feature_dir_from_env, is_specs_in_progress


BLOCK = re.compile(
    r"```autodev-review-findings\s*(?P<body>\{.*?\})\s*```",
    re.DOTALL,
)
RUN_ID = re.compile(r"^RV-[A-Za-z0-9-]+$")
FINDING_ID = re.compile(r"^RV-[A-Za-z0-9-]+-F\d{3}$")
SEVERITIES = {"Critical", "Major", "Minor"}


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _tool_input(payload):
    return _as_dict(payload.get("tool_input") or payload.get("input"))


def _is_target(payload):
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").lower()
    subagent = _tool_input(payload).get("subagent_type")
    return tool_name == "task" and isinstance(subagent, str) and subagent.strip().lower() == "critic-autodev"


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            for item in _strings(child):
                yield item
    elif isinstance(value, list):
        for child in value:
            for item in _strings(child):
                yield item


def parse_response(payload):
    bodies = []
    for text in _strings(payload.get("tool_response") or payload.get("response")):
        bodies.extend(match.group("body") for match in BLOCK.finditer(text))
    if len(bodies) != 1:
        raise ValueError(
            "CRITIC_FINDINGS_BLOCK_INVALID: critic-autodev 必须返回唯一 autodev-review-findings block；修复：按增强 prompt 重跑 critic。"
        )
    try:
        data = json.loads(bodies[0])
    except ValueError as exc:
        raise ValueError("CRITIC_FINDINGS_JSON_INVALID: {}；修复：按固定 JSON 形状重跑 critic。".format(exc))
    if not isinstance(data, dict):
        raise ValueError("CRITIC_FINDINGS_JSON_INVALID: block 顶层必须是 object。")
    run_id = data.get("reviewRunId")
    findings = data.get("findings")
    if not isinstance(run_id, str) or not RUN_ID.match(run_id) or not isinstance(findings, list):
        raise ValueError("CRITIC_FINDINGS_SCHEMA_INVALID: reviewRunId 或 findings 非法。")
    normalized = []
    seen = set()
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("CRITIC_FINDINGS_SCHEMA_INVALID: finding 必须是 object。")
        finding_id = finding.get("id")
        severity = finding.get("severity")
        claim = finding.get("claim")
        evidence = finding.get("evidence")
        if (
            not isinstance(finding_id, str)
            or not FINDING_ID.match(finding_id)
            or not finding_id.startswith(run_id + "-F")
            or finding_id in seen
            or severity not in SEVERITIES
            or not isinstance(claim, str)
            or not claim.strip()
            or not isinstance(evidence, str)
            or not evidence.strip()
        ):
            raise ValueError(
                "CRITIC_FINDINGS_SCHEMA_INVALID: 每条 finding 需要唯一 id、Critical/Major/Minor、非空 claim/evidence。"
            )
        seen.add(finding_id)
        normalized.append(
            {
                "recordType": "finding",
                "reviewRunId": run_id,
                "findingId": finding_id,
                "severity": severity,
                "claim": claim.strip(),
                "evidence": evidence.strip(),
            }
        )
    return run_id, normalized


def append_run(feature_dir, run_id, findings):
    path = Path(feature_dir) / ".runtime" / "REVIEW_FINDINGS.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    if '"reviewRunId":"{}"'.format(run_id) in existing.replace(" ", ""):
        return path, False
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    run = {
        "recordType": "review_run",
        "reviewRunId": run_id,
        "timestamp": timestamp,
        "findingIds": [finding["findingId"] for finding in findings],
    }
    records = [run] + list(findings)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path, True


def run_hook(payload, feature_dir=None):
    if not _is_target(payload):
        return None
    run_id, findings = parse_response(payload)
    return append_run(feature_dir or feature_dir_from_env(), run_id, findings)


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not _is_target(payload):
            return 0
        feature_dir = feature_dir_from_env()
        if not is_specs_in_progress(feature_dir):
            return 0
        result = run_hook(payload, feature_dir=feature_dir)
    except ValueError as exc:
        print("CRITIC_FINDINGS_SKIPPED {}".format(exc))
        return 0
    if result is not None:
        print("CRITIC_FINDINGS_RECORDED path={} changed={}".format(result[0], str(result[1]).lower()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
