#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safely update one Feature checkpoint in .autobizdevops/STATE.md."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = ROOT / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from state_checkpoint import (  # noqa: E402
    DEFAULT_STAGE_BY_CHECKPOINT,
    INITIAL_CHECKPOINTS,
    KNOWN_CHECKPOINTS,
    features_entering_code_done,
    parse_state_table,
    validate_lifecycle,
    validate_maven_compile,
    validate_transitions,
)


STATE_RELATIVE_PATH = Path(".autobizdevops") / "STATE.md"
STATE_JSON_RELATIVE_PATH = Path(".autobizdevops") / "state.json"
STATE_COLUMNS = 6


@dataclass(frozen=True)
class CheckpointUpdate:
    ok: bool
    state_path: Path
    state_json_path: Path
    content: str
    state_json_content: str
    errors: tuple[str, ...]
    old_checkpoint: str | None
    new_checkpoint: str | None


def split_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_state_header(cells: list[str]) -> bool:
    return len(cells) >= 3 and cells[0] == "Feature" and cells[2] == "checkpoint"


def is_separator_row(cells: list[str]) -> bool:
    return all(cell and set(cell) <= {"-", ":"} for cell in cells)


def format_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def ensure_cells(cells: list[str]) -> list[str]:
    padded = list(cells)
    while len(padded) < STATE_COLUMNS:
        padded.append("")
    return padded


def find_state_table(lines: list[str]) -> tuple[int, int] | None:
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = split_table_cells(stripped)
        if not is_state_header(cells):
            continue

        end = index + 1
        while end < len(lines) and lines[end].strip().startswith("|"):
            end += 1
        return index, end
    return None


def replace_feature_row(
    content: str,
    *,
    feature: str,
    checkpoint: str,
    stage: str | None,
    owner: str | None,
    iteration: str | None,
    allow_create: bool,
    updated_at: str,
) -> tuple[str, list[str]]:
    errors: list[str] = []
    lines = content.splitlines()
    trailing_newline = content.endswith("\n")
    table_span = find_state_table(lines)
    if table_span is None:
        return content, ["STATE.md 缺少 Feature 进度表格"]

    table_start, table_end = table_span
    matched_indexes: list[int] = []
    for index in range(table_start + 1, table_end):
        stripped = lines[index].strip()
        if not stripped.startswith("|"):
            continue
        cells = split_table_cells(stripped)
        if not cells or cells[0] == "Feature" or is_separator_row(cells):
            continue
        if cells[0] == feature:
            matched_indexes.append(index)

    if len(matched_indexes) > 1:
        return content, [f"Feature '{feature}' 出现重复行"]

    resolved_stage = stage if stage is not None else DEFAULT_STAGE_BY_CHECKPOINT.get(checkpoint, "")
    if matched_indexes:
        row_index = matched_indexes[0]
        cells = ensure_cells(split_table_cells(lines[row_index].strip()))
        cells[2] = checkpoint
        cells[3] = resolved_stage
        if owner is not None:
            cells[1] = owner
        if iteration is not None:
            cells[4] = iteration
        cells[5] = updated_at
        lines[row_index] = format_row(cells)
    else:
        if not allow_create:
            errors.append(f"Feature '{feature}' 不存在；新增行必须显式传入 --allow-create")
        if checkpoint not in INITIAL_CHECKPOINTS:
            allowed = " / ".join(sorted(INITIAL_CHECKPOINTS))
            errors.append(f"Feature '{feature}' 是新增行，只允许从空状态进入 {allowed}，当前为 {checkpoint}")
        if errors:
            return content, errors

        cells = [
            feature,
            owner if owner is not None else "—",
            checkpoint,
            resolved_stage,
            iteration if iteration is not None else "—",
            updated_at,
        ]
        lines.insert(table_end, format_row(cells))

    new_content = "\n".join(lines)
    if trailing_newline or content == "":
        new_content += "\n"
    return new_content, []


def state_json_content_from_rows(rows: dict[str, str]) -> str:
    return json.dumps(dict(sorted(rows.items())), ensure_ascii=False, indent=2) + "\n"


def prepare_checkpoint_update(
    *,
    workspace: Path,
    feature: str,
    checkpoint: str,
    stage: str | None = None,
    owner: str | None = None,
    iteration: str | None = None,
    allow_create: bool = False,
    updated_at: str | None = None,
) -> CheckpointUpdate:
    workspace = workspace.resolve()
    state_path = workspace / STATE_RELATIVE_PATH
    state_json_path = workspace / STATE_JSON_RELATIVE_PATH
    if checkpoint not in KNOWN_CHECKPOINTS:
        return CheckpointUpdate(
            ok=False,
            state_path=state_path,
            state_json_path=state_json_path,
            content="",
            state_json_content="",
            errors=(f"未知 checkpoint: {checkpoint}",),
            old_checkpoint=None,
            new_checkpoint=None,
        )
    if not feature.strip():
        return CheckpointUpdate(
            ok=False,
            state_path=state_path,
            state_json_path=state_json_path,
            content="",
            state_json_content="",
            errors=("feature 不能为空",),
            old_checkpoint=None,
            new_checkpoint=None,
        )
    if not state_path.is_file():
        return CheckpointUpdate(
            ok=False,
            state_path=state_path,
            state_json_path=state_json_path,
            content="",
            state_json_content="",
            errors=(f"STATE.md 不存在: {state_path}",),
            old_checkpoint=None,
            new_checkpoint=None,
        )

    old_content = state_path.read_text(encoding="utf-8")
    old_map, old_errors = parse_state_table(old_content)
    new_content, update_errors = replace_feature_row(
        old_content,
        feature=feature,
        checkpoint=checkpoint,
        stage=stage,
        owner=owner,
        iteration=iteration,
        allow_create=allow_create,
        updated_at=updated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    new_map, new_errors = parse_state_table(new_content)

    errors = [
        *old_errors,
        *update_errors,
        *new_errors,
        *validate_transitions(old_map, new_map),
    ]
    if not errors:
        errors.extend(validate_lifecycle(workspace, old_map, new_map))
    if not errors:
        errors.extend(validate_maven_compile(workspace, features_entering_code_done(old_map, new_map)))

    return CheckpointUpdate(
        ok=not errors,
        state_path=state_path,
        state_json_path=state_json_path,
        content=new_content,
        state_json_content=state_json_content_from_rows(new_map) if not errors else "",
        errors=tuple(errors),
        old_checkpoint=old_map.get(feature),
        new_checkpoint=new_map.get(feature),
    )


def write_result_json(result: CheckpointUpdate, *, feature: str, checkpoint: str, dry_run: bool) -> None:
    print(
        json.dumps(
            {
                "ok": result.ok,
                "state_path": str(result.state_path),
                "state_json_path": str(result.state_json_path),
                "feature": feature,
                "old_checkpoint": result.old_checkpoint,
                "new_checkpoint": result.new_checkpoint,
                "requested_checkpoint": checkpoint,
                "dry_run": dry_run,
                "errors": list(result.errors),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely update .autobizdevops/STATE.md checkpoint")
    parser.add_argument("--workspace", "-w", required=True, help="workspace path")
    parser.add_argument("--feature", "-f", required=True, help="feature slug")
    parser.add_argument("--checkpoint", "-c", required=True, help="target checkpoint")
    parser.add_argument("--stage", help="stage column override")
    parser.add_argument("--owner", help="owner column override")
    parser.add_argument("--iteration", help="iteration column override")
    parser.add_argument("--allow-create", action="store_true", help="allow creating a new feature row")
    parser.add_argument("--dry-run", action="store_true", help="validate and print target content without writing")
    parser.add_argument("--json", action="store_true", help="print JSON result")
    args = parser.parse_args(argv)

    result = prepare_checkpoint_update(
        workspace=Path(args.workspace),
        feature=args.feature,
        checkpoint=args.checkpoint,
        stage=args.stage,
        owner=args.owner,
        iteration=args.iteration,
        allow_create=args.allow_create,
    )

    if args.json:
        write_result_json(result, feature=args.feature, checkpoint=args.checkpoint, dry_run=args.dry_run)
    elif not result.ok:
        print("checkpoint 更新失败:", file=sys.stderr)
        for error in result.errors:
            print(f"  - {error}", file=sys.stderr)
    elif args.dry_run:
        print(f"DRY_RUN checkpoint update: feature={args.feature} checkpoint={args.checkpoint}")
        print(result.content, end="")
    else:
        print(f"checkpoint updated: feature={args.feature} checkpoint={args.checkpoint}")

    if not result.ok:
        return 1
    if not args.dry_run:
        result.state_path.write_text(result.content, encoding="utf-8")
        result.state_json_path.write_text(result.state_json_content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
