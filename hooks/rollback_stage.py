#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rollback a feature to the done checkpoint before a workflow stage."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.artifact_paths import has_glob, resolve_exact_relative_path  # noqa: E402
from board_core.contracts import (  # noqa: E402
    BoardConfigError,
    artifact_dicts,
    load_record_workflow_contracts,
)
from board_core.state_store import (  # noqa: E402
    StateRecords,
    get_state_json_path,
    load_state_json_records_result,
    render_state_md,
    state_json_content_from_records_preserving_raw,
    write_state_records_preserving_raw,
)
from board_core.workflow import find_effective_current_node  # noqa: E402
from hooks.paths import (  # noqa: E402
    STATE_SCRIPTS_WORKSPACE_ARGUMENT_ERROR,
    contains_workspace_argument,
    get_feature_active_dir,
    get_features_archive_dir,
    get_plugin_output_workspace,
    resolve_env_feature,
)
from hooks.state_checkpoint import append_checkpoint_hook_logs, safe_feature_slug  # noqa: E402


NON_FILESYSTEM_ARTIFACT_TYPES = frozenset({"external", "virtual"})


@dataclass(frozen=True)
class RollbackPlan:
    ok: bool
    workspace: Path
    feature: str
    requested_stage: str
    target_node_id: Optional[str] = None
    previous_node_id: Optional[str] = None
    old_checkpoint: Optional[str] = None
    new_checkpoint: Optional[str] = None
    feature_dir: Optional[Path] = None
    active_feature_dir: Optional[Path] = None
    artifact_paths: Tuple[Path, ...] = ()
    old_records: StateRecords = field(default_factory=dict)
    records: StateRecords = field(default_factory=dict)
    raw_records: Dict[str, Any] = field(default_factory=dict)
    workflow_profile: str = "standard"
    workflow_decisions: Dict[str, str] = field(default_factory=dict)
    errors: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RollbackResult:
    ok: bool
    plan: RollbackPlan
    deleted_artifacts: Tuple[str, ...] = ()
    restored_active_dir: bool = False
    errors: Tuple[str, ...] = ()


def _failed_plan(
    *,
    workspace: Path,
    feature: str,
    stage: str,
    errors: Union[List[str], Tuple[str, ...]],
    **values: Any,
) -> RollbackPlan:
    return RollbackPlan(
        ok=False,
        workspace=workspace,
        feature=feature,
        requested_stage=stage,
        errors=tuple(errors),
        **values,
    )


def _checkpoint_base(checkpoint: str) -> str:
    for suffix in ("_in_progress", "_done"):
        if checkpoint.endswith(suffix):
            return checkpoint[: -len(suffix)]
    return checkpoint


def _node_aliases(node: dict, stage_labels: Dict[str, str]) -> Set[str]:
    aliases: Set[str] = set()
    for value in (node.get("id"), node.get("label"), node.get("skill")):
        if isinstance(value, str) and value.strip():
            aliases.add(value.strip().casefold())

    node_id = node.get("id")
    if isinstance(node_id, str) and "." in node_id:
        aliases.add(node_id.rsplit(".", 1)[-1].casefold())

    for checkpoint in node.get("checkpoints", []):
        if not isinstance(checkpoint, str) or not checkpoint:
            continue
        aliases.add(checkpoint.casefold())
        aliases.add(_checkpoint_base(checkpoint).casefold())
        label = stage_labels.get(checkpoint)
        if isinstance(label, str) and label.strip():
            aliases.add(label.strip().casefold())
    return aliases


def _resolve_target_node(
    nodes: List[dict],
    stage: str,
    stage_labels: Dict[str, str],
) -> Tuple[Optional[dict], Tuple[str, ...]]:
    query = stage.strip().casefold()
    matches = [
        node
        for node in nodes
        if query and query in _node_aliases(node, stage_labels)
    ]
    if len(matches) == 1:
        return matches[0], ()
    if len(matches) > 1:
        node_ids = ", ".join(str(node.get("id", "")) for node in matches)
        return None, (f"阶段 '{stage}' 匹配多个节点: {node_ids}；请改用完整 node id",)

    available = ", ".join(
        str(node.get("id", ""))
        for node in nodes
        if not node.get("skipped")
    )
    return None, (f"未知阶段: {stage}；可用 node id: {available}",)


def _done_checkpoint(node: dict) -> Optional[str]:
    return next(
        (
            checkpoint
            for checkpoint in node.get("checkpoints", [])
            if isinstance(checkpoint, str) and checkpoint.endswith("_done")
        ),
        None,
    )


def _archive_feature_dir(workspace: Path, feature: str, iteration: object) -> Optional[Path]:
    archive_dir = get_features_archive_dir(workspace)
    iteration_text = str(iteration or "").strip()
    if iteration_text and iteration_text != "—":
        exact = archive_dir / f"{feature}-iter{iteration_text}"
        if exact.is_dir():
            return exact

    if not archive_dir.is_dir():
        return None
    matches = sorted(
        entry
        for entry in archive_dir.iterdir()
        if entry.is_dir() and entry.name.startswith(f"{feature}-iter")
    )
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_feature_dir(
    workspace: Path,
    feature: str,
    record: Dict[str, Any],
) -> Tuple[Optional[Path], Tuple[str, ...]]:
    active_dir = get_feature_active_dir(workspace, feature)
    if active_dir.is_dir():
        return active_dir, ()

    if record.get("checkpoint") != "archived":
        return None, (f"Feature 产物目录不存在: {active_dir}",)

    archived_dir = _archive_feature_dir(workspace, feature, record.get("iteration"))
    if archived_dir is None:
        return None, (f"Feature 归档目录无法唯一定位: {feature}",)
    return archived_dir, ()


def _validate_artifact_path(path: str) -> Optional[str]:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return f"产物路径必须位于 Feature 目录内: {path}"
    if not path.strip() or candidate == Path("."):
        return f"产物路径不能为空或 Feature 根目录: {path}"
    return None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_existing_candidate(feature_dir: Path, candidate: Path) -> Tuple[Optional[Path], Optional[str]]:
    try:
        relative = candidate.relative_to(feature_dir)
    except ValueError:
        return None, f"产物路径越出 Feature 目录: {candidate}"
    if not relative.parts:
        return None, f"拒绝删除 Feature 根目录: {candidate}"

    resolved_root = feature_dir.resolve()
    resolved_parent = candidate.parent.resolve()
    if not _is_within(resolved_parent, resolved_root):
        return None, f"产物父目录通过符号链接越出 Feature 目录: {candidate}"
    return candidate, None


def _artifact_candidates(
    feature_dir: Path,
    nodes: List[dict],
) -> Tuple[Tuple[Path, ...], Tuple[str, ...]]:
    candidates: Set[Path] = set()
    errors: List[str] = []

    for node in nodes:
        for artifact in artifact_dicts(node, "outputs"):
            kind = str(artifact.get("artifactType", "file")).strip().casefold()
            if kind in NON_FILESYSTEM_ARTIFACT_TYPES:
                continue
            artifact_path = artifact.get("path")
            if not isinstance(artifact_path, str):
                errors.append(f"{node.get('id', '<unknown>')} 的产物 path 必须是字符串")
                continue
            path_error = _validate_artifact_path(artifact_path)
            if path_error:
                errors.append(path_error)
                continue

            if has_glob(artifact_path):
                matches = sorted(feature_dir.glob(artifact_path))
            else:
                exact = resolve_exact_relative_path(feature_dir, artifact_path)
                matches = [exact] if exact is not None else []

            for match in matches:
                if match is None or not (match.exists() or match.is_symlink()):
                    continue
                safe_match, candidate_error = _safe_existing_candidate(feature_dir, match)
                if candidate_error:
                    errors.append(candidate_error)
                elif safe_match is not None:
                    candidates.add(safe_match)

    ordered = sorted(
        candidates,
        key=lambda path: (len(path.relative_to(feature_dir).parts), path.as_posix()),
    )
    top_level: List[Path] = []
    for candidate in ordered:
        if any(parent == candidate or parent in candidate.parents for parent in top_level):
            continue
        top_level.append(candidate)
    return tuple(top_level), tuple(errors)


def prepare_stage_rollback(
    *,
    workspace: Path,
    feature: str,
    stage: str,
    updated_at: Optional[str] = None,
) -> RollbackPlan:
    workspace = workspace.resolve()
    feature = feature.strip()
    stage = stage.strip()
    if not feature:
        return _failed_plan(workspace=workspace, feature=feature, stage=stage, errors=["feature 不能为空"])
    if not safe_feature_slug(feature):
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=[f"feature 不是安全的相对路径: {feature}"],
        )
    if not stage:
        return _failed_plan(workspace=workspace, feature=feature, stage=stage, errors=["stage 不能为空"])

    state_result = load_state_json_records_result(workspace)
    if not state_result.exists:
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=[f"state.json 不存在: {get_state_json_path(workspace)}"],
        )
    if state_result.fatal_errors:
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=state_result.fatal_errors,
            raw_records=state_result.raw_records,
        )
    if state_result.record_errors.get(feature):
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=state_result.record_errors[feature],
            raw_records=state_result.raw_records,
        )

    record = state_result.records.get(feature)
    if record is None:
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=[f"Feature '{feature}' 不存在"],
            raw_records=state_result.raw_records,
        )

    try:
        contracts = load_record_workflow_contracts(ROOT, record, workspace=workspace)
    except BoardConfigError as exc:
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=[f"workflow 配置无法编译: {exc}"],
            raw_records=state_result.raw_records,
        )

    nodes = list(contracts.nodes)
    target_node, target_errors = _resolve_target_node(nodes, stage, contracts.stage_labels)
    if target_errors or target_node is None:
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=target_errors,
            old_checkpoint=record.get("checkpoint"),
            raw_records=state_result.raw_records,
        )
    target_node_id = str(target_node.get("id", ""))
    if target_node.get("skipped"):
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=[f"阶段 {target_node_id} 已跳过，不能作为回退目标"],
            target_node_id=target_node_id,
            old_checkpoint=record.get("checkpoint"),
            raw_records=state_result.raw_records,
        )

    active_nodes = [node for node in nodes if not node.get("skipped")]
    active_index = {
        str(node.get("id", "")): index
        for index, node in enumerate(active_nodes)
    }
    target_index = active_index[target_node_id]
    if target_index == 0:
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=[f"阶段 {target_node_id} 是首个有效阶段，没有可回退的前置 done checkpoint"],
            target_node_id=target_node_id,
            old_checkpoint=record.get("checkpoint"),
            raw_records=state_result.raw_records,
        )

    current_index, current_node_id = find_effective_current_node(
        nodes,
        str(record.get("checkpoint", "")),
        record.get("needsFixFromCheckpoint"),
        stage=record.get("stage"),
        stage_labels=contracts.stage_labels,
    )
    if current_index < 0 or current_node_id not in active_index:
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=[f"当前 checkpoint 无法映射到有效阶段: {record.get('checkpoint', '')}"],
            target_node_id=target_node_id,
            old_checkpoint=record.get("checkpoint"),
            raw_records=state_result.raw_records,
        )
    if target_index > active_index[current_node_id]:
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=[
                f"阶段 {target_node_id} 尚未到达；当前阶段为 {current_node_id}"
            ],
            target_node_id=target_node_id,
            old_checkpoint=record.get("checkpoint"),
            raw_records=state_result.raw_records,
        )

    previous_node = active_nodes[target_index - 1]
    previous_node_id = str(previous_node.get("id", ""))
    new_checkpoint = _done_checkpoint(previous_node)
    if new_checkpoint is None:
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=[f"前置阶段 {previous_node_id} 没有 done checkpoint"],
            target_node_id=target_node_id,
            previous_node_id=previous_node_id,
            old_checkpoint=record.get("checkpoint"),
            raw_records=state_result.raw_records,
        )

    feature_dir, feature_dir_errors = _resolve_feature_dir(workspace, feature, record)
    if feature_dir_errors or feature_dir is None:
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=feature_dir_errors,
            target_node_id=target_node_id,
            previous_node_id=previous_node_id,
            old_checkpoint=record.get("checkpoint"),
            new_checkpoint=new_checkpoint,
            raw_records=state_result.raw_records,
        )

    artifact_paths, artifact_errors = _artifact_candidates(
        feature_dir,
        active_nodes[target_index:],
    )
    if artifact_errors:
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=artifact_errors,
            target_node_id=target_node_id,
            previous_node_id=previous_node_id,
            old_checkpoint=record.get("checkpoint"),
            new_checkpoint=new_checkpoint,
            feature_dir=feature_dir,
            raw_records=state_result.raw_records,
        )

    new_record = dict(record)
    new_record["checkpoint"] = new_checkpoint
    new_record["stage"] = contracts.stage_labels.get(new_checkpoint, "")
    new_record["updated_at"] = updated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_record.pop("needsFixFromCheckpoint", None)
    new_records = {
        slug: dict(existing)
        for slug, existing in state_result.records.items()
    }
    new_records[feature] = new_record
    try:
        state_json_content_from_records_preserving_raw(
            new_records,
            raw_records=state_result.raw_records,
            workspace=workspace,
        )
        render_state_md(new_records, workspace=workspace)
    except ValueError as exc:
        return _failed_plan(
            workspace=workspace,
            feature=feature,
            stage=stage,
            errors=str(exc).splitlines(),
            target_node_id=target_node_id,
            previous_node_id=previous_node_id,
            old_checkpoint=record.get("checkpoint"),
            new_checkpoint=new_checkpoint,
            feature_dir=feature_dir,
            raw_records=state_result.raw_records,
        )

    return RollbackPlan(
        ok=True,
        workspace=workspace,
        feature=feature,
        requested_stage=stage,
        target_node_id=target_node_id,
        previous_node_id=previous_node_id,
        old_checkpoint=str(record.get("checkpoint", "")),
        new_checkpoint=new_checkpoint,
        feature_dir=feature_dir,
        active_feature_dir=get_feature_active_dir(workspace, feature),
        artifact_paths=artifact_paths,
        old_records={
            slug: dict(existing)
            for slug, existing in state_result.records.items()
        },
        records=new_records,
        raw_records=state_result.raw_records,
        workflow_profile=str(record.get("workflowProfile", "standard")),
        workflow_decisions=dict(record.get("workflowDecisions") or {}),
    )


def _restore_moved_artifacts(feature_dir: Path, backup_dir: Path, paths: List[Path]) -> None:
    for original in reversed(paths):
        relative = original.relative_to(feature_dir)
        backup = backup_dir / relative
        if not (backup.exists() or backup.is_symlink()):
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        backup.replace(original)


def _prune_empty_parents(feature_dir: Path, deleted_paths: Tuple[Path, ...]) -> None:
    directories = sorted(
        {
            parent
            for path in deleted_paths
            for parent in path.parents
            if parent != feature_dir and _is_within(parent, feature_dir)
        },
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass


def execute_stage_rollback(plan: RollbackPlan) -> RollbackResult:
    if not plan.ok:
        return RollbackResult(ok=False, plan=plan, errors=plan.errors)
    if plan.feature_dir is None or plan.active_feature_dir is None:
        return RollbackResult(ok=False, plan=plan, errors=("回退计划缺少 Feature 目录",))

    feature_dir = plan.feature_dir
    active_feature_dir = plan.active_feature_dir
    backup_parent = feature_dir.parent
    backup_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{feature_dir.name}.rollback-",
            dir=backup_parent,
        )
    )
    moved_artifacts: List[Path] = []
    moved_to_active = False
    try:
        for original in plan.artifact_paths:
            relative = original.relative_to(feature_dir)
            backup = backup_dir / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            original.replace(backup)
            moved_artifacts.append(original)

        if feature_dir != active_feature_dir:
            if active_feature_dir.exists():
                raise FileExistsError(f"活跃 Feature 目录已存在: {active_feature_dir}")
            active_feature_dir.parent.mkdir(parents=True, exist_ok=True)
            feature_dir.replace(active_feature_dir)
            moved_to_active = True

        write_state_records_preserving_raw(
            plan.workspace,
            plan.records,
            raw_records=plan.raw_records,
        )
    except Exception as exc:
        recovery_errors: List[str] = []
        if moved_to_active and active_feature_dir.exists() and not feature_dir.exists():
            try:
                active_feature_dir.replace(feature_dir)
            except Exception as recovery_exc:
                recovery_errors.append(f"Feature 目录恢复失败: {recovery_exc}")
        try:
            _restore_moved_artifacts(feature_dir, backup_dir, moved_artifacts)
        except Exception as recovery_exc:
            recovery_errors.append(f"产物恢复失败: {recovery_exc}")
        try:
            write_state_records_preserving_raw(
                plan.workspace,
                plan.old_records,
                raw_records=plan.raw_records,
            )
        except Exception as recovery_exc:
            recovery_errors.append(f"状态恢复失败: {recovery_exc}")
        shutil.rmtree(backup_dir, ignore_errors=True)
        recovery_summary = (
            "；".join(recovery_errors)
            if recovery_errors
            else "产物与状态已恢复"
        )
        return RollbackResult(
            ok=False,
            plan=plan,
            errors=(f"回退执行失败: {exc}；{recovery_summary}",),
        )

    shutil.rmtree(backup_dir, ignore_errors=True)
    effective_feature_dir = active_feature_dir if moved_to_active else feature_dir
    effective_deleted_paths = tuple(
        effective_feature_dir / path.relative_to(feature_dir)
        for path in plan.artifact_paths
    )
    _prune_empty_parents(effective_feature_dir, effective_deleted_paths)
    deleted = tuple(
        path.relative_to(effective_feature_dir).as_posix()
        for path in effective_deleted_paths
    )
    append_checkpoint_hook_logs(
        plan.workspace,
        [(plan.feature, plan.old_checkpoint, plan.new_checkpoint)],
        event_id="stage-rollback",
        label="阶段回退",
        errors=[],
        event_status="success",
        exit_code=0,
        message=(
            f"{plan.old_checkpoint} -> {plan.new_checkpoint}: "
            f"rollback {plan.target_node_id}; deleted={len(deleted)}"
        ),
        workflow_profiles={plan.feature: plan.workflow_profile},
        workflow_decisions={plan.feature: plan.workflow_decisions},
    )
    return RollbackResult(
        ok=True,
        plan=plan,
        deleted_artifacts=deleted,
        restored_active_dir=moved_to_active,
    )


def _result_payload(
    result: RollbackResult,
    *,
    dry_run: bool,
) -> Dict[str, Any]:
    plan = result.plan
    planned_artifacts = (
        [
            path.relative_to(plan.feature_dir).as_posix()
            for path in plan.artifact_paths
        ]
        if plan.feature_dir is not None
        else []
    )
    return {
        "ok": result.ok,
        "feature": plan.feature,
        "requestedStage": plan.requested_stage,
        "targetNodeId": plan.target_node_id,
        "previousNodeId": plan.previous_node_id,
        "oldCheckpoint": plan.old_checkpoint,
        "newCheckpoint": plan.new_checkpoint,
        "dryRun": dry_run,
        "plannedArtifacts": planned_artifacts,
        "deletedArtifacts": list(result.deleted_artifacts),
        "restoredActiveDir": result.restored_active_dir,
        "errors": list(result.errors or plan.errors),
    }


def main(argv: Optional[List[str]] = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if contains_workspace_argument(raw_args):
        print(STATE_SCRIPTS_WORKSPACE_ARGUMENT_ERROR, file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(
        description="删除指定阶段及后续阶段产物，并回退到其前一有效阶段的 done checkpoint",
        allow_abbrev=False,
    )
    parser.add_argument("--feature", "-f", help="feature slug；必须与 FEATURE_ID 一致")
    parser.add_argument(
        "--stage",
        "-s",
        required=True,
        help="阶段 node id、skill、label 或 checkpoint 前缀，如 dev.specs / specs",
    )
    parser.add_argument("--dry-run", action="store_true", help="只展示回退计划，不删除或写状态")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(raw_args)

    try:
        workspace = get_plugin_output_workspace()
        feature = resolve_env_feature(args.feature, required=True)
    except ValueError as exc:
        if args.json:
            print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        else:
            print(f"阶段回退失败: {exc}", file=sys.stderr)
        return 1

    plan = prepare_stage_rollback(
        workspace=workspace,
        feature=feature,
        stage=args.stage,
    )
    if args.dry_run or not plan.ok:
        result = RollbackResult(ok=plan.ok, plan=plan, errors=plan.errors)
    else:
        result = execute_stage_rollback(plan)

    if args.json:
        print(json.dumps(_result_payload(result, dry_run=args.dry_run), ensure_ascii=False, indent=2))
    elif not result.ok:
        print("阶段回退失败:", file=sys.stderr)
        for error in result.errors or plan.errors:
            print(f"  - {error}", file=sys.stderr)
    elif args.dry_run:
        print(
            f"DRY_RUN stage rollback: feature={feature} stage={plan.target_node_id} "
            f"checkpoint={plan.old_checkpoint}->{plan.new_checkpoint}"
        )
        for path in plan.artifact_paths:
            print(f"  - {path.relative_to(plan.feature_dir).as_posix()}")
    else:
        print(
            f"stage rolled back: feature={feature} stage={plan.target_node_id} "
            f"checkpoint={plan.old_checkpoint}->{plan.new_checkpoint}"
        )
        for path in result.deleted_artifacts:
            print(f"  - deleted {path}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
