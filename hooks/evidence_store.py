#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append-only evidence stream helpers for Autodev feature runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EVIDENCE_VERSION = 1
INDEX_VERSION = 1
EVIDENCE_ID_RE = re.compile(r"^ev_(\d{4})$")
DEFAULT_STREAM_RELATIVE_PATH = Path("evidence") / "EVIDENCE.jsonl"
DEFAULT_INDEX_RELATIVE_PATH = Path("evidence") / "EVIDENCE.index.json"
VALIDATION_RESULTS = {"pass", "fail", "blocked", "skipped"}


class EvidenceStoreError(ValueError):
    """Raised when evidence cannot be read, validated, or appended."""


@dataclass(frozen=True)
class EvidenceSnapshot:
    records: tuple[dict[str, Any], ...]
    line_count: int
    last_evidence_id: str
    sha256: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def feature_dir(workspace: Path, feature: str) -> Path:
    return workspace / ".autobizdevops" / "features" / feature


def evidence_dir(target_feature_dir: Path) -> Path:
    return target_feature_dir / "evidence"


def stream_path(target_feature_dir: Path) -> Path:
    return target_feature_dir / DEFAULT_STREAM_RELATIVE_PATH


def index_path(target_feature_dir: Path) -> Path:
    return target_feature_dir / DEFAULT_INDEX_RELATIVE_PATH


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.is_file():
        return digest.hexdigest()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_line(raw: str, line_no: int) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceStoreError(f"invalid_evidence_json line={line_no}: {exc}") from exc
    if not isinstance(data, dict):
        raise EvidenceStoreError(f"invalid_evidence_record line={line_no}: root_must_be_object")
    return data


def read_records(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.is_file():
        return ()
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            records.append(_parse_line(raw, line_no))
    return tuple(records)


def snapshot(target_feature_dir: Path) -> EvidenceSnapshot:
    path = stream_path(target_feature_dir)
    records = read_records(path)
    last_id = ""
    if records:
        last = records[-1].get("evidenceId")
        last_id = last if isinstance(last, str) else ""
    return EvidenceSnapshot(
        records=records,
        line_count=len(records),
        last_evidence_id=last_id,
        sha256=compute_sha256(path),
    )


def next_evidence_id(records: tuple[dict[str, Any], ...]) -> str:
    max_number = 0
    for record in records:
        evidence_id = record.get("evidenceId")
        if isinstance(evidence_id, str):
            match = EVIDENCE_ID_RE.match(evidence_id)
            if match:
                max_number = max(max_number, int(match.group(1)))
    return f"ev_{max_number + 1:04d}"


def load_index(target_feature_dir: Path) -> dict[str, Any] | None:
    path = index_path(target_feature_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvidenceStoreError(f"invalid_evidence_index:{path}:{exc}") from exc
    if not isinstance(data, dict):
        raise EvidenceStoreError(f"invalid_evidence_index_root:{path}")
    return data


def write_index(
    target_feature_dir: Path,
    *,
    feature_id: str | None = None,
    verify_existing: bool = True,
) -> None:
    if verify_existing:
        _ensure_index_matches(target_feature_dir)
    snap = snapshot(target_feature_dir)
    payload = {
        "version": INDEX_VERSION,
        "featureId": feature_id or target_feature_dir.name,
        "stream": str(DEFAULT_STREAM_RELATIVE_PATH),
        "lineCount": snap.line_count,
        "lastEvidenceId": snap.last_evidence_id,
        "sha256": snap.sha256,
        "updatedAt": utc_now(),
    }
    path = index_path(target_feature_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if record.get("version") != EVIDENCE_VERSION:
        errors.append("invalid_evidence_version")
    for field in ["evidenceId", "featureId", "checkpoint", "nodeId", "skill", "taskId", "action", "createdAt"]:
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing_{field}")
    evidence_id = record.get("evidenceId")
    if isinstance(evidence_id, str) and not EVIDENCE_ID_RE.match(evidence_id):
        errors.append(f"invalid_evidence_id:{evidence_id}")
    validation = record.get("validation")
    if validation is not None and not isinstance(validation, dict):
        errors.append("invalid_validation_object")
    if record.get("action") == "validation":
        if not isinstance(validation, dict):
            errors.append("validation_missing")
        else:
            command = validation.get("command")
            if not isinstance(command, str) or not command.strip():
                errors.append("validation.command_missing")
            exit_code = validation.get("exitCode")
            if not isinstance(exit_code, int):
                errors.append("validation.exitCode_missing")
            raw_result = validation.get("result")
            result = raw_result.strip().lower() if isinstance(raw_result, str) else ""
            if result not in VALIDATION_RESULTS:
                errors.append("validation.result_invalid")
            if isinstance(exit_code, int) and result:
                if exit_code == 0 and result != "pass":
                    errors.append("validation.result_exitCode_mismatch")
                if exit_code != 0 and result == "pass":
                    errors.append("validation.result_exitCode_mismatch")
            output_tail_path = validation.get("outputTailPath")
            if result == "fail" and (not isinstance(output_tail_path, str) or not output_tail_path.strip()):
                errors.append("validation.outputTailPath_missing")
    changed_files = record.get("changedFiles")
    if changed_files is not None and not _string_list(changed_files):
        errors.append("invalid_changedFiles")
    spec_refs = record.get("specRefs")
    if spec_refs is not None and not _string_list(spec_refs):
        errors.append("invalid_specRefs")
    design_refs = record.get("designRefs")
    if design_refs is not None and not _string_list(design_refs):
        errors.append("invalid_designRefs")
    return errors


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _ensure_index_matches(target_feature_dir: Path, *, allow_missing_for_empty_stream: bool = False) -> None:
    existing = load_index(target_feature_dir)
    if existing is None:
        if allow_missing_for_empty_stream and not read_records(stream_path(target_feature_dir)):
            return
        if stream_path(target_feature_dir).is_file() and stream_path(target_feature_dir).stat().st_size > 0:
            raise EvidenceStoreError("missing_evidence_index_for_nonempty_stream")
        return
    snap = snapshot(target_feature_dir)
    mismatches: list[str] = []
    if existing.get("lineCount") != snap.line_count:
        mismatches.append("lineCount")
    if existing.get("lastEvidenceId") != snap.last_evidence_id:
        mismatches.append("lastEvidenceId")
    if existing.get("sha256") != snap.sha256:
        mismatches.append("sha256")
    if mismatches:
        raise EvidenceStoreError("evidence_stream_rewritten_or_truncated:" + ",".join(mismatches))


def append_evidence(
    target_feature_dir: Path,
    record: dict[str, Any],
    *,
    output_tail: str | None = None,
) -> dict[str, Any]:
    """Append a single evidence record and refresh the integrity index.

    The JSONL stream is only opened in append mode. If an integrity index exists
    and no longer matches the stream, append is refused so truncation/rewrite is
    noticed before new evidence is added.
    """

    _ensure_index_matches(target_feature_dir, allow_missing_for_empty_stream=True)
    records = read_records(stream_path(target_feature_dir))
    payload = dict(record)
    payload.setdefault("version", EVIDENCE_VERSION)
    payload.setdefault("featureId", target_feature_dir.name)
    payload.setdefault("createdAt", utc_now())
    payload.setdefault("specRefs", [])
    payload.setdefault("designRefs", [])
    payload.setdefault("changedFiles", [])
    payload.setdefault("validation", {})
    payload["evidenceId"] = payload.get("evidenceId") or next_evidence_id(records)

    evidence_id = payload["evidenceId"]
    if isinstance(evidence_id, str) and any(record.get("evidenceId") == evidence_id for record in records):
        raise EvidenceStoreError(f"duplicate_evidence_id:{evidence_id}")

    if output_tail is not None:
        if not isinstance(evidence_id, str):
            raise EvidenceStoreError("invalid_evidence_id_for_tail")
        tail_path = evidence_dir(target_feature_dir) / f"{evidence_id}.log"
        tail_path.parent.mkdir(parents=True, exist_ok=True)
        tail_path.write_text(output_tail, encoding="utf-8")
        validation = payload.get("validation")
        if not isinstance(validation, dict):
            validation = {}
            payload["validation"] = validation
        validation["outputTailPath"] = f"evidence/{evidence_id}.log"

    errors = validate_record(payload)
    if errors:
        raise EvidenceStoreError(";".join(errors))

    path = stream_path(target_feature_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    write_index(
        target_feature_dir,
        feature_id=str(payload.get("featureId") or target_feature_dir.name),
        verify_existing=False,
    )
    return payload


def _cmd_append(args: argparse.Namespace) -> int:
    target = feature_dir(Path(args.workspace).resolve(), args.feature)
    try:
        record = json.loads(Path(args.record).read_text(encoding="utf-8")) if args.record else {}
    except json.JSONDecodeError as exc:
        print(f"invalid record JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(record, dict):
        print("record must be JSON object", file=sys.stderr)
        return 1

    if args.action:
        record["action"] = args.action
    if args.checkpoint:
        record["checkpoint"] = args.checkpoint
    if args.node_id:
        record["nodeId"] = args.node_id
    if args.skill:
        record["skill"] = args.skill
    if args.task_id:
        record["taskId"] = args.task_id
    if args.command:
        validation = record.get("validation") if isinstance(record.get("validation"), dict) else {}
        validation["command"] = args.command
        record["validation"] = validation
    if args.exit_code is not None:
        validation = record.get("validation") if isinstance(record.get("validation"), dict) else {}
        validation["exitCode"] = args.exit_code
        validation["result"] = "pass" if args.exit_code == 0 else "fail"
        record["validation"] = validation
    if not record.get("action") and (args.command or args.exit_code is not None):
        record["action"] = "validation"
    tail = Path(args.output_tail).read_text(encoding="utf-8", errors="ignore") if args.output_tail else None
    try:
        appended = append_evidence(target, record, output_tail=tail)
    except EvidenceStoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(appended, ensure_ascii=False))
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    target = feature_dir(Path(args.workspace).resolve(), args.feature)
    try:
        write_index(target, feature_id=args.feature, verify_existing=True)
    except EvidenceStoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"EVIDENCE_INDEX_UPDATED path={index_path(target)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append or index Autodev evidence")
    subparsers = parser.add_subparsers(dest="command", required=True)

    append = subparsers.add_parser("append")
    append.add_argument("--workspace", default=str(Path.cwd().resolve()))
    append.add_argument("--feature", required=True)
    append.add_argument("--record", help="JSON object file to append")
    append.add_argument("--action")
    append.add_argument("--checkpoint")
    append.add_argument("--node-id")
    append.add_argument("--skill")
    append.add_argument("--task-id")
    append.add_argument("--command")
    append.add_argument("--exit-code", type=int)
    append.add_argument("--output-tail", help="File whose text should be stored as evidence/<id>.log")
    append.set_defaults(func=_cmd_append)

    index = subparsers.add_parser("index")
    index.add_argument("--workspace", default=str(Path.cwd().resolve()))
    index.add_argument("--feature", required=True)
    index.set_defaults(func=_cmd_index)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
