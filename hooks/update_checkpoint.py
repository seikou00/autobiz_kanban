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

from hooks.paths import (  # noqa: E402
    STATE_SCRIPTS_WORKSPACE_ARGUMENT_ERROR,
    contains_workspace_argument,
    get_plugin_output_workspace,
    resolve_env_feature,
)
from state_checkpoint import (  # noqa: E402
    append_checkpoint_hook_logs,
    validate_lifecycle,
    validate_transitions,
)
from board_core.contracts import BoardConfigError, load_repo_workflow_contracts  # noqa: E402
from board_core.state_store import (  # noqa: E402
    EMPTY_CELL,
    StateRecords,
    check_or_fix_state_sync,
    render_state_md,
    state_json_content_from_records,
    state_rows_from_records,
    write_state_records,
)
from board_core.workflow_compiler import BASE_WORKFLOW_PROFILE, normalize_workflow_profile  # noqa: E402


STATE_RELATIVE_PATH = Path(".autobizdevops") / "STATE.md"
STATE_JSON_RELATIVE_PATH = Path(".autobizdevops") / "state.json"
CHECKPOINT_LOG_EVENTS = (
    ("state-done", "STATE checkpoint 转移校验", "transition_errors"),
    ("autodev-lifecycle", "Autodev 产物校验", "lifecycle_errors"),
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
    old_checkpoint: str | None
    new_checkpoint: str | None
    workflow_profile: str = BASE_WORKFLOW_PROFILE

    @property
    def errors(self) -> tuple[str, ...]:
        return (*self.transition_errors, *self.lifecycle_errors)


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
    workflow_profile: str,
    stage_labels: dict[str, str],
    initial_checkpoints: frozenset[str],
) -> tuple[StateRecords, list[str]]:
    errors: list[str] = []
    resolved_stage = stage if stage is not None else stage_labels.get(checkpoint, "")
    new_records: StateRecords = {slug: dict(record) for slug, record in records.items()}
    if feature in new_records:
        record = dict(new_records[feature])
        old_profile = normalize_workflow_profile(record.get("workflowProfile", BASE_WORKFLOW_PROFILE))
        if old_profile != workflow_profile:
            old_checkpoint = record.get("checkpoint", "")
            if old_checkpoint != "prd_done":
                return records, [
                    f"Feature '{feature}' 已绑定 workflowProfile={old_profile}，不能在 {old_checkpoint} 改为 {workflow_profile}"
                ]
            record["workflowProfile"] = workflow_profile
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
        if checkpoint not in initial_checkpoints:
            allowed = " / ".join(sorted(initial_checkpoints))
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
            "workflowProfile": workflow_profile,
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
    workflow_profile: str | None = None,
) -> CheckpointUpdate:
    workspace = workspace.resolve()
    state_path = workspace / STATE_RELATIVE_PATH
    state_json_path = workspace / STATE_JSON_RELATIVE_PATH
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
            old_checkpoint=None,
            new_checkpoint=None,
            workflow_profile=workflow_profile or BASE_WORKFLOW_PROFILE,
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
            old_checkpoint=None,
            new_checkpoint=None,
            workflow_profile=workflow_profile or BASE_WORKFLOW_PROFILE,
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
            records=sync_result.records,
            old_checkpoint=None,
            new_checkpoint=None,
            workflow_profile=workflow_profile or BASE_WORKFLOW_PROFILE,
        )

    old_records = sync_result.records
    old_map = state_rows_from_records(old_records)
    old_record = old_records.get(feature)
    resolved_profile = normalize_workflow_profile(
        workflow_profile or (
            old_record.get("workflowProfile", BASE_WORKFLOW_PROFILE) if old_record else BASE_WORKFLOW_PROFILE
        )
    )
    try:
        contracts = load_repo_workflow_contracts(ROOT, workspace=workspace, profile=resolved_profile)
    except BoardConfigError as exc:
        return CheckpointUpdate(
            ok=False,
            state_path=state_path,
            state_json_path=state_json_path,
            content="",
            state_json_content="",
            transition_errors=(f"workflowProfile '{resolved_profile}' 无法编译: {exc}",),
            lifecycle_errors=(),
            records={},
            old_checkpoint=old_map.get(feature),
            new_checkpoint=None,
            workflow_profile=resolved_profile,
        )
    if checkpoint not in contracts.known_checkpoints:
        return CheckpointUpdate(
            ok=False,
            state_path=state_path,
            state_json_path=state_json_path,
            content="",
            state_json_content="",
            transition_errors=(f"未知 checkpoint: {checkpoint}",),
            lifecycle_errors=(),
            records={},
            old_checkpoint=old_map.get(feature),
            new_checkpoint=None,
            workflow_profile=resolved_profile,
        )
    new_records, update_errors = replace_feature_record(
        old_records,
        feature=feature,
        checkpoint=checkpoint,
        stage=stage,
        owner=owner,
        iteration=iteration,
        allow_create=allow_create,
        updated_at=updated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        workflow_profile=resolved_profile,
        stage_labels=contracts.stage_labels,
        initial_checkpoints=contracts.initial_checkpoints,
    )
    new_map = state_rows_from_records(new_records)

    content = ""
    state_json_content = ""
    render_errors: list[str] = []
    if not update_errors:
        try:
            content = render_state_md(new_records, workspace=workspace)
            state_json_content = state_json_content_from_records(new_records, workspace=workspace)
        except ValueError as exc:
            render_errors.extend(str(exc).splitlines())

    transition_errors = [
        *update_errors,
        *render_errors,
        *validate_transitions(
            old_map,
            new_map,
            workspace_root=workspace,
            old_records=old_records,
            new_records=new_records,
        ),
    ]
    lifecycle_errors: list[str] = []
    if not transition_errors:
        lifecycle_errors.extend(
            validate_lifecycle(
                workspace,
                old_map,
                new_map,
                old_records=old_records,
                new_records=new_records,
            )
        )

    errors = [*transition_errors, *lifecycle_errors]

    return CheckpointUpdate(
        ok=not errors,
        state_path=state_path,
        state_json_path=state_json_path,
        transition_errors=tuple(transition_errors),
        lifecycle_errors=tuple(lifecycle_errors),
        content=content if not errors else "",
        state_json_content=state_json_content if not errors else "",
        records=new_records if not errors else {},
        old_checkpoint=old_map.get(feature),
        new_checkpoint=new_map.get(feature),
        workflow_profile=resolved_profile,
    )


def stage_event_status(result: CheckpointUpdate, stage: str) -> str:
    if stage == "transition_errors":
        return "blocked" if result.transition_errors else "success"
    if stage == "lifecycle_errors":
        if result.transition_errors:
            return "skipped"
        return "blocked" if result.lifecycle_errors else "success"
    return "error"


def stage_errors(result: CheckpointUpdate, stage: str) -> tuple[str, ...]:
    if stage == "transition_errors":
        return result.transition_errors
    if stage == "lifecycle_errors":
        return result.lifecycle_errors
    return ()


def stage_message(result: CheckpointUpdate, *, label: str, stage: str) -> str:
    transition = f"{result.old_checkpoint or 'empty'} -> {result.new_checkpoint or 'empty'}"
    event_status = stage_event_status(result, stage)
    errors = stage_errors(result, stage)
    if event_status == "success":
        return f"{transition}: {label} 通过"
    if event_status == "blocked":
        return f"{transition}: " + "\n".join(errors)
    if stage == "lifecycle_errors":
        return f"{transition}: {label} 未执行，因为 state-done 已阻断"
    return f"{transition}: {label} 执行异常"


def write_hook_logs(result: CheckpointUpdate, *, workspace: Path, feature: str) -> None:
    changes = [(feature, result.old_checkpoint, result.new_checkpoint)]
    for event_id, label, stage in CHECKPOINT_LOG_EVENTS:
        event_status = stage_event_status(result, stage)
        append_checkpoint_hook_logs(
            workspace,
            changes,
            event_id=event_id,
            label=label,
            errors=list(stage_errors(result, stage)),
            event_status=event_status,
            exit_code=0 if event_status == "success" else 1,
            message=stage_message(result, label=label, stage=stage),
            workflow_profiles={feature: result.workflow_profile},
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
                "workflow_profile": result.workflow_profile,
                "dry_run": dry_run,
                "errors": list(result.errors),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if contains_workspace_argument(raw_args):
        print(STATE_SCRIPTS_WORKSPACE_ARGUMENT_ERROR, file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(
        description="Safely update .autobizdevops/state.json checkpoint",
        allow_abbrev=False,
    )
    parser.add_argument("--feature", "-f", help="feature slug; defaults to FEATURE_ID")
    parser.add_argument("--checkpoint", "-c", required=True, help="target checkpoint")
    parser.add_argument("--stage", help="stage column override")
    parser.add_argument("--owner", help="owner column override")
    parser.add_argument("--iteration", help="iteration column override")
    parser.add_argument("--workflow-profile", help="workflow profile for a new feature row")
    parser.add_argument("--allow-create", action="store_true", help="allow creating a new feature row")
    parser.add_argument("--dry-run", action="store_true", help="validate and print target content without writing")
    parser.add_argument("--json", action="store_true", help="print JSON result")
    args = parser.parse_args(raw_args)

    try:
        workspace = get_plugin_output_workspace()
        feature = resolve_env_feature(args.feature, required=True)
    except ValueError as exc:
        print(f"checkpoint 更新失败: {exc}", file=sys.stderr)
        return 1

    result = prepare_checkpoint_update(
        workspace=workspace,
        feature=feature,
        checkpoint=args.checkpoint,
        stage=args.stage,
        owner=args.owner,
        iteration=args.iteration,
        allow_create=args.allow_create,
        workflow_profile=args.workflow_profile,
    )

    if args.json:
        write_result_json(result, feature=feature, checkpoint=args.checkpoint, dry_run=args.dry_run)
    elif not result.ok:
        print("checkpoint 更新失败:", file=sys.stderr)
        for error in result.errors:
            print(f"  - {error}", file=sys.stderr)
    elif args.dry_run:
        print(f"DRY_RUN checkpoint update: feature={feature} checkpoint={args.checkpoint}")
        print("--- state.json ---")
        print(result.state_json_content, end="")
        print("--- STATE.md ---")
        print(result.content, end="")
    else:
        print(f"checkpoint updated: feature={feature} checkpoint={args.checkpoint}")

    if not result.ok:
        if not args.dry_run:
            write_hook_logs(result, workspace=workspace, feature=feature)
        return 1
    if not args.dry_run:
        write_state_records(workspace, result.records)
        write_hook_logs(result, workspace=workspace, feature=feature)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
