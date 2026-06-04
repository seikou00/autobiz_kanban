"""JSON-backed state store with STATE.md as a generated view."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from board_core.contracts import BoardConfigError, load_repo_workflow_contracts, load_workflow_contracts
from board_core.workflow_compiler import BASE_WORKFLOW_PROFILE, normalize_workflow_profile


ROOT = Path(__file__).resolve().parents[1]
BOARD_CONFIG_PATH = ROOT / "board_core" / "board_config.json"
STATE_SCHEMA_VERSION = "autobizdevops.state.v3"
STATE_RELATIVE_PATH = Path(".autobizdevops") / "STATE.md"
STATE_JSON_RELATIVE_PATH = Path(".autobizdevops") / "state.json"
STATE_COLUMNS = ("Feature", "负责人", "checkpoint", "阶段", "迭代", "最后更新")
EMPTY_CELL = "—"

WORKFLOW_CONTRACTS = load_workflow_contracts(BOARD_CONFIG_PATH)
KNOWN_CHECKPOINTS = WORKFLOW_CONTRACTS.known_checkpoints
DEFAULT_STAGE_BY_CHECKPOINT = WORKFLOW_CONTRACTS.stage_labels

StateRecord = dict[str, str]
StateRecords = dict[str, StateRecord]


@dataclass(frozen=True)
class StateLoadResult:
    records: StateRecords
    errors: list[str]
    exists: bool
    source: str


@dataclass(frozen=True)
class StateSyncResult:
    ok: bool
    records: StateRecords
    errors: list[str]
    state_exists: bool
    changed: bool
    state_path: Path
    state_json_path: Path


def get_state_path(workspace: Path) -> Path:
    return workspace.resolve() / STATE_RELATIVE_PATH


def get_state_json_path(workspace: Path) -> Path:
    return workspace.resolve() / STATE_JSON_RELATIVE_PATH


def _clean(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _is_separator_row(cells: list[str]) -> bool:
    return all(cell and set(cell) <= {"-", ":"} for cell in cells)


def _split_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _contracts_for_profile(workspace: Path | None, workflow_profile: str):
    if workspace is None or workflow_profile == BASE_WORKFLOW_PROFILE:
        return WORKFLOW_CONTRACTS
    try:
        return load_repo_workflow_contracts(ROOT, workspace=workspace, profile=workflow_profile)
    except BoardConfigError as exc:
        raise ValueError(str(exc)) from exc


def _normalize_record(
    feature: str,
    raw: Any,
    errors: list[str],
    *,
    context: str,
    workspace: Path | None = None,
) -> StateRecord | None:
    feature = _clean(feature)
    if not feature:
        errors.append(f"{context}: feature 不能为空")
        return None

    if isinstance(raw, str):
        raw_record: dict[str, Any] = {"checkpoint": raw}
    elif isinstance(raw, dict):
        raw_record = raw
    else:
        errors.append(f"{context}: Feature '{feature}' 的记录必须是对象或 checkpoint 字符串")
        return None

    record_feature = _clean(raw_record.get("feature"), feature)
    if record_feature != feature:
        errors.append(f"{context}: Feature key '{feature}' 与记录 feature '{record_feature}' 不一致")
        return None

    workflow_profile = normalize_workflow_profile(_clean(raw_record.get("workflowProfile"), BASE_WORKFLOW_PROFILE))
    try:
        contracts = _contracts_for_profile(workspace, workflow_profile)
    except ValueError as exc:
        errors.append(f"{context}: Feature '{feature}' 的 workflowProfile 无效: {exc}")
        return None

    checkpoint = _clean(raw_record.get("checkpoint"))
    if checkpoint not in contracts.known_checkpoints:
        errors.append(f"{context}: Feature '{feature}' 使用了未知 checkpoint: {checkpoint or '未设置'}")
        return None

    stage = _clean(raw_record.get("stage"), contracts.stage_labels.get(checkpoint, ""))
    return {
        "feature": feature,
        "owner": _clean(raw_record.get("owner"), EMPTY_CELL),
        "checkpoint": checkpoint,
        "stage": stage,
        "iteration": _clean(raw_record.get("iteration"), EMPTY_CELL),
        "updated_at": _clean(raw_record.get("updated_at"), EMPTY_CELL),
        "workflowProfile": workflow_profile,
    }


def normalize_state_records(
    records: dict[str, Any],
    *,
    context: str = "state records",
    workspace: Path | None = None,
) -> tuple[StateRecords, list[str]]:
    normalized: StateRecords = {}
    errors: list[str] = []
    for feature in sorted(records):
        record = _normalize_record(feature, records[feature], errors, context=context, workspace=workspace)
        if record is not None:
            normalized[feature] = record
    return normalized, errors


def parse_state_md_records(content: str) -> tuple[StateRecords, list[str]]:
    records: StateRecords = {}
    errors: list[str] = []

    for lineno, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line.startswith("|"):
            continue

        cells = _split_table_cells(line)
        if len(cells) < 3:
            continue
        if cells[0] == "Feature" or _is_separator_row(cells):
            continue

        feature = cells[0]
        if not feature:
            errors.append(f"line {lineno}: Feature 为空")
            continue
        if feature in records:
            errors.append(f"line {lineno}: Feature '{feature}' 出现重复行")
            continue

        raw_record = {
            "feature": feature,
            "owner": cells[1] if len(cells) > 1 else EMPTY_CELL,
            "checkpoint": cells[2] if len(cells) > 2 else "",
            "stage": cells[3] if len(cells) > 3 else "",
            "iteration": cells[4] if len(cells) > 4 else EMPTY_CELL,
            "updated_at": cells[5] if len(cells) > 5 else EMPTY_CELL,
            "workflowProfile": BASE_WORKFLOW_PROFILE,
        }
        record = _normalize_record(feature, raw_record, errors, context=f"STATE.md line {lineno}")
        if record is not None:
            records[feature] = record

    return records, errors


def parse_state_json_records(content: str, *, workspace: Path | None = None) -> tuple[StateRecords, list[str]]:
    errors: list[str] = []
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        return {}, [f"state.json 不是合法 JSON: {exc}"]

    if not isinstance(payload, dict):
        return {}, ["state.json 顶层必须是对象"]

    if "features" in payload:
        features = payload.get("features")
        if not isinstance(features, dict):
            return {}, ["state.json.features 必须是对象"]
        records, record_errors = normalize_state_records(
            features,
            context="state.json.features",
            workspace=workspace,
        )
        errors.extend(record_errors)
        return records, errors

    # Legacy migration format: {"feature": "checkpoint"}.
    records, record_errors = normalize_state_records(payload, context="state.json legacy map", workspace=workspace)
    errors.extend(record_errors)
    return records, errors


def load_state_json_records_result(workspace: Path) -> StateLoadResult:
    """Read only .autobizdevops/state.json without fallback or repair."""
    workspace = workspace.resolve()
    state_json = get_state_json_path(workspace)

    if not state_json.is_file():
        return StateLoadResult(records={}, errors=[], exists=False, source="state.json")

    records, errors = parse_state_json_records(state_json.read_text(encoding="utf-8"), workspace=workspace)
    return StateLoadResult(records=records, errors=errors, exists=True, source="state.json")


def load_state_json_records(workspace: Path) -> tuple[StateRecords, list[str], bool]:
    """Read only .autobizdevops/state.json and return (records, errors, exists)."""
    result = load_state_json_records_result(workspace)
    return result.records, result.errors, result.exists


def _load_state_records_result(workspace: Path) -> StateLoadResult:
    workspace = workspace.resolve()
    state_md = get_state_path(workspace)

    json_result = load_state_json_records_result(workspace)
    if json_result.exists:
        return json_result

    if state_md.is_file():
        records, errors = parse_state_md_records(state_md.read_text(encoding="utf-8"))
        return StateLoadResult(records=records, errors=errors, exists=True, source="STATE.md")

    return StateLoadResult(records={}, errors=[], exists=False, source="")


def load_state_records(workspace: Path) -> tuple[StateRecords, list[str], bool]:
    result = _load_state_records_result(workspace)
    return result.records, result.errors, result.exists


def state_rows_from_records(records: StateRecords) -> dict[str, str]:
    return {feature: record["checkpoint"] for feature, record in sorted(records.items())}


def state_json_content_from_records(records: StateRecords, *, workspace: Path | None = None) -> str:
    normalized, errors = normalize_state_records(records, workspace=workspace)
    if errors:
        raise ValueError("\n".join(errors))
    payload = {
        "schemaVersion": STATE_SCHEMA_VERSION,
        "features": {
            feature: normalized[feature]
            for feature in sorted(normalized)
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render_state_md(records: StateRecords, *, workspace: Path | None = None) -> str:
    normalized, errors = normalize_state_records(records, workspace=workspace)
    if errors:
        raise ValueError("\n".join(errors))

    updated_values = [
        record["updated_at"]
        for record in normalized.values()
        if record.get("updated_at") and record["updated_at"] != EMPTY_CELL
    ]
    last_updated = max(updated_values) if updated_values else EMPTY_CELL
    lines = [
        "# 工程状态",
        "",
        "> 本文件由 `.autobizdevops/state.json` 自动生成，请勿手工修改。请使用 `hooks/update_checkpoint.py` 推进 checkpoint。",
        "",
        "- **里程碑**: [待确定]",
        f"- **最后更新**: {last_updated}",
        "",
        "## Feature 进度",
        "",
        "| Feature | 负责人 | checkpoint | 阶段 | 迭代 | 最后更新 |",
        "|---------|--------|-----------|------|------|---------|",
    ]
    for feature in sorted(normalized):
        record = normalized[feature]
        lines.append(
            "| "
            + " | ".join(
                [
                    record["feature"],
                    record["owner"],
                    record["checkpoint"],
                    record["stage"],
                    record["iteration"],
                    record["updated_at"],
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
            temp_name = tmp.name
            tmp.write(content)
        Path(temp_name).replace(path)
    finally:
        if temp_name:
            temp_path = Path(temp_name)
            if temp_path.exists():
                temp_path.unlink()


def write_state_records(workspace: Path, records: StateRecords) -> None:
    workspace = workspace.resolve()
    state_json = get_state_json_path(workspace)
    state_md = get_state_path(workspace)
    _atomic_write_text(state_json, state_json_content_from_records(records, workspace=workspace))
    _atomic_write_text(state_md, render_state_md(records, workspace=workspace))


def check_or_fix_state_sync(workspace: Path, *, fix: bool = True) -> StateSyncResult:
    workspace = workspace.resolve()
    state_json = get_state_json_path(workspace)
    state_md = get_state_path(workspace)
    result = _load_state_records_result(workspace)
    if not result.exists:
        return StateSyncResult(
            ok=True,
            records={},
            errors=[],
            state_exists=False,
            changed=False,
            state_path=state_md,
            state_json_path=state_json,
        )
    if result.errors:
        return StateSyncResult(
            ok=False,
            records=result.records,
            errors=result.errors,
            state_exists=True,
            changed=False,
            state_path=state_md,
            state_json_path=state_json,
        )

    expected_json = state_json_content_from_records(result.records, workspace=workspace)
    expected_md = render_state_md(result.records, workspace=workspace)
    errors: list[str] = []
    changed = False

    if not state_json.is_file() or state_json.read_text(encoding="utf-8") != expected_json:
        if fix:
            _atomic_write_text(state_json, expected_json)
            changed = True
        else:
            errors.append("state.json 缺失、为旧格式，或与规范化内容不一致")

    if not state_md.is_file() or state_md.read_text(encoding="utf-8") != expected_md:
        if fix:
            _atomic_write_text(state_md, expected_md)
            changed = True
        else:
            errors.append("STATE.md 缺失或与 state.json 生成视图不一致")

    return StateSyncResult(
        ok=not errors,
        records=result.records,
        errors=errors,
        state_exists=True,
        changed=changed,
        state_path=state_md,
        state_json_path=state_json,
    )
