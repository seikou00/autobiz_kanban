#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safely update one Feature checkpoint in .autobizdevops/state.json."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = ROOT / "hooks"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from state_checkpoint import (  # noqa: E402
    DEFAULT_STAGE_BY_CHECKPOINT,
    INITIAL_CHECKPOINTS,
    KNOWN_CHECKPOINTS,
    append_checkpoint_hook_logs,
    features_entering_code_done,
    parse_state_table,
    validate_lifecycle,
    validate_transitions,
)
from board_core.state_store import (  # noqa: E402
    EMPTY_CELL,
    StateRecords,
    check_or_fix_state_sync,
    render_state_md,
    state_json_content_from_records,
    state_rows_from_records,
    write_state_records,
)


STATE_RELATIVE_PATH = Path(".autobizdevops") / "STATE.md"
STATE_JSON_RELATIVE_PATH = Path(".autobizdevops") / "state.json"
CHECKPOINT_LOG_EVENTS = (
    ("state-done", "STATE checkpoint 转移校验", "transition_errors"),
    ("autodev-lifecycle", "Autodev 产物校验", "lifecycle_errors"),
    ("code-compile", "code_done 编译校验", "compile_errors"),
)


@dataclass(frozen=True)
class CheckpointUpdate:
    ok: bool
    state_path: Path
    state_json_path: Path
    content: str
    state_json_content: str
    records: StateRecords
    transition_errors: tuple[str, ...]
    lifecycle_errors: tuple[str, ...]
    compile_errors: tuple[str, ...]
    old_checkpoint: str | None
    new_checkpoint: str | None
    compile_features: tuple[str, ...] = ()

    @property
    def errors(self) -> tuple[str, ...]:
        return (*self.transition_errors, *self.lifecycle_errors, *self.compile_errors)


def replace_feature_record(
    records: StateRecords,
    *,
    feature: str,
    checkpoint: str,
    stage: str | None,
    owner: str | None,
    iteration: str | None,
    allow_create: bool,
    updated_at: str,
) -> tuple[StateRecords, list[str]]:
    errors: list[str] = []
    resolved_stage = stage if stage is not None else DEFAULT_STAGE_BY_CHECKPOINT.get(checkpoint, "")
    new_records: StateRecords = {slug: dict(record) for slug, record in records.items()}
    if feature in new_records:
        record = dict(new_records[feature])
        record["feature"] = feature
        record["checkpoint"] = checkpoint
        record["stage"] = resolved_stage
        if owner is not None:
            record["owner"] = owner
        if iteration is not None:
            record["iteration"] = iteration
        record["updated_at"] = updated_at
        new_records[feature] = record
    else:
        if not allow_create:
            errors.append(f"Feature '{feature}' 不存在；新增行必须显式传入 --allow-create")
        if checkpoint not in INITIAL_CHECKPOINTS:
            allowed = " / ".join(sorted(INITIAL_CHECKPOINTS))
            errors.append(f"Feature '{feature}' 是新增行，只允许从空状态进入 {allowed}，当前为 {checkpoint}")
        if errors:
            return records, errors

        new_records[feature] = {
            "feature": feature,
            "owner": owner if owner is not None else EMPTY_CELL,
            "checkpoint": checkpoint,
            "stage": resolved_stage,
            "iteration": iteration if iteration is not None else EMPTY_CELL,
            "updated_at": updated_at,
        }

    return new_records, []


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
            transition_errors=(f"未知 checkpoint: {checkpoint}",),
            lifecycle_errors=(),
            compile_errors=(),
            records={},
            old_checkpoint=None,
            new_checkpoint=None,
            compile_features=(),
        )
    if not feature.strip():
        return CheckpointUpdate(
            ok=False,
            state_path=state_path,
            state_json_path=state_json_path,
            content="",
            state_json_content="",
            records={},
            transition_errors=("feature 不能为空",),
            lifecycle_errors=(),
            compile_errors=(),
            old_checkpoint=None,
            new_checkpoint=None,
            compile_features=(),
        )

    sync_result = check_or_fix_state_sync(workspace, fix=True)
    if not sync_result.state_exists:
        return CheckpointUpdate(
            ok=False,
            state_path=state_path,
            state_json_path=state_json_path,
            content="",
            state_json_content="",
            records={},
            transition_errors=(f"state.json 不存在且无法从 STATE.md 迁移: {state_json_path}",),
            lifecycle_errors=(),
            compile_errors=(),
            old_checkpoint=None,
            new_checkpoint=None,
            compile_features=(),
        )
    if sync_result.errors:
        return CheckpointUpdate(
            ok=False,
            state_path=state_path,
            state_json_path=state_json_path,
            content="",
            state_json_content="",
            transition_errors=(f"STATE.md 不存在: {state_path}",),
            lifecycle_errors=(),
            compile_errors=(),
            records=sync_result.records,
            old_checkpoint=None,
            new_checkpoint=None,
            compile_features=(),
        )

    old_records = sync_result.records
    old_map = state_rows_from_records(old_records)
    new_records, update_errors = replace_feature_record(
        old_records,
        feature=feature,
        checkpoint=checkpoint,
        stage=stage,
        owner=owner,
        iteration=iteration,
        allow_create=allow_create,
        updated_at=updated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    new_map = state_rows_from_records(new_records)

    content = ""
    state_json_content = ""
    render_errors: list[str] = []
    if not update_errors:
        try:
            content = render_state_md(new_records)
            state_json_content = state_json_content_from_records(new_records)
        except ValueError as exc:
            render_errors.extend(str(exc).splitlines())

    transition_errors = [
        *update_errors,
        *render_errors,
        *validate_transitions(old_map, new_map),
    ]
    lifecycle_errors: list[str] = []
    compile_errors: list[str] = []
    compile_features = tuple(features_entering_code_done(old_map, new_map))
    if not transition_errors:
        lifecycle_errors.extend(validate_lifecycle(workspace, old_map, new_map))

    errors = [*transition_errors, *lifecycle_errors, *compile_errors]
    if not errors:
        errors.extend(validate_lifecycle(workspace, old_map, new_map))

    return CheckpointUpdate(
        ok=not errors,
        state_path=state_path,
        state_json_path=state_json_path,
        transition_errors=tuple(transition_errors),
        lifecycle_errors=tuple(lifecycle_errors),
        compile_errors=tuple(compile_errors),
        content=content if not errors else "",
        state_json_content=state_json_content if not errors else "",
        records=new_records if not errors else {},
        old_checkpoint=old_map.get(feature),
        new_checkpoint=new_map.get(feature),
        compile_features=compile_features,
    )


def stage_result_code(result: CheckpointUpdate, stage: str) -> str:
    if stage == "transition_errors":
        return "blocked" if result.transition_errors else "done"
    if stage == "lifecycle_errors":
        if result.transition_errors:
            return "skipped"
        return "blocked" if result.lifecycle_errors else "done"
    if stage == "compile_errors":
        if result.transition_errors or result.lifecycle_errors:
            return "skipped"
        if not result.compile_features:
            return "skipped"
        return "blocked" if result.compile_errors else "done"
    return "error"


def stage_errors(result: CheckpointUpdate, stage: str) -> tuple[str, ...]:
    if stage == "transition_errors":
        return result.transition_errors
    if stage == "lifecycle_errors":
        return result.lifecycle_errors
    if stage == "compile_errors":
        return result.compile_errors
    return ()


def stage_message(result: CheckpointUpdate, *, label: str, stage: str) -> str:
    transition = f"{result.old_checkpoint or 'empty'} -> {result.new_checkpoint or 'empty'}"
    result_code = stage_result_code(result, stage)
    errors = stage_errors(result, stage)
    if result_code == "done":
        return f"{transition}: {label} 通过"
    if result_code == "blocked":
        return f"{transition}: " + "\n".join(errors)
    if stage == "lifecycle_errors":
        return f"{transition}: {label} 未执行，因为 state-done 已阻断"
    if stage == "compile_errors":
        blocker = "state-done" if result.transition_errors else "autodev-lifecycle"
        if result.transition_errors or result.lifecycle_errors:
            return f"{transition}: {label} 未执行，因为 {blocker} 已阻断"
        return f"{transition}: {label} 未执行，因为本次 checkpoint 不进入 code_done"
    return f"{transition}: {label} 执行异常"


def write_hook_logs(result: CheckpointUpdate, *, workspace: Path, feature: str) -> None:
    changes = [(feature, result.old_checkpoint, result.new_checkpoint)]
    for event_id, label, stage in CHECKPOINT_LOG_EVENTS:
        append_checkpoint_hook_logs(
            workspace,
            changes,
            event_id=event_id,
            label=label,
            errors=list(stage_errors(result, stage)),
            result_code=stage_result_code(result, stage),
            exit_code=0 if stage_result_code(result, stage) == "done" else 1,
            message=stage_message(result, label=label, stage=stage),
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
    parser = argparse.ArgumentParser(description="Safely update .autobizdevops/state.json checkpoint")
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
        print("--- state.json ---")
        print(result.state_json_content, end="")
        print("--- STATE.md ---")
        print(result.content, end="")
    else:
        print(f"checkpoint updated: feature={args.feature} checkpoint={args.checkpoint}")

    if not result.ok:
        if not args.dry_run:
            write_hook_logs(result, workspace=Path(args.workspace).resolve(), feature=args.feature)
        return 1
    if not args.dry_run:
        write_state_records(Path(args.workspace), result.records)
        write_hook_logs(result, workspace=Path(args.workspace).resolve(), feature=args.feature)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
