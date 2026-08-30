#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PostToolUse(request_user_input): persist the fact of a real user decision."""

from __future__ import print_function

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.specs_hook_context import feature_dir_from_env, is_specs_in_progress


DECISION_ID = re.compile(r"^(?:SPEC|API|DATA)-\d{3}$")


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _tool_input(payload):
    return _as_dict(payload.get("tool_input") or payload.get("input"))


def _normal_id(value):
    normalized = str(value or "").strip().upper().replace("_", "-")
    return normalized if DECISION_ID.match(normalized) else ""


def _answer_for(question_id, response):
    if isinstance(response, dict):
        answers = response.get("answers")
        if isinstance(answers, dict) and question_id in answers:
            return _answer_for(question_id, answers[question_id])
        for key in (question_id, _normal_id(question_id), "answer", "selected", "label", "value"):
            if key in response:
                return _answer_for(question_id, response[key])
    if isinstance(response, list):
        for item in response:
            if isinstance(item, dict) and str(item.get("id") or "") == question_id:
                return _answer_for(question_id, item)
        return ", ".join(str(item) for item in response)
    if response is None:
        return ""
    return str(response).strip()


def records_from_payload(payload):
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").lower()
    if tool_name not in {"request_user_input", "requestuserinput"}:
        return []
    tool_input = _tool_input(payload)
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        raise ValueError("DECISION_LEDGER_INPUT_INVALID: request_user_input.questions 必须是数组。")
    response = payload.get("tool_response") or payload.get("response")
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    request_seed = str(payload.get("tool_use_id") or payload.get("toolUseId") or "")
    if not request_seed:
        request_seed = json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
    request_id = "RQ-" + hashlib.sha256(request_seed.encode("utf-8")).hexdigest()[:12]
    records = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        raw_id = str(question.get("id") or "").strip()
        decision_id = _normal_id(raw_id)
        if not decision_id:
            continue
        selected = _answer_for(raw_id, response)
        if not selected:
            raise ValueError(
                "DECISION_LEDGER_RESPONSE_INVALID: {} 没有可记录的用户选择；修复：重新发起该项裁定。".format(
                    decision_id
                )
            )
        records.append(
            {
                "recordType": "decision",
                "requestId": request_id,
                "questionId": raw_id,
                "decisionId": decision_id,
                "selectedLabel": selected,
                "timestamp": timestamp,
            }
        )
    return records


def append_records(feature_dir, records):
    if not records:
        return None, False
    path = Path(feature_dir) / ".runtime" / "DECISIONS.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    pending = []
    for record in records:
        marker = '"requestId":"{}","questionId":"{}"'.format(
            record["requestId"], record["questionId"]
        )
        if marker not in existing.replace(" ", ""):
            pending.append(record)
    if pending:
        with path.open("a", encoding="utf-8") as handle:
            for record in pending:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path, bool(pending)


def run_hook(payload, feature_dir=None):
    records = records_from_payload(payload)
    if not records:
        return None
    return append_records(feature_dir or feature_dir_from_env(), records)


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
        records = records_from_payload(payload)
        if not records:
            return 0
        feature_dir = feature_dir_from_env()
        if not is_specs_in_progress(feature_dir):
            return 0
        result = append_records(feature_dir, records)
    except ValueError as exc:
        print("DECISIONS_SKIPPED {}".format(exc))
        return 0
    if result is not None:
        print("DECISIONS_RECORDED path={} changed={}".format(result[0], str(result[1]).lower()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
