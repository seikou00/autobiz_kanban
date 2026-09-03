#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve and persist trusted UTest workspace bindings without model-authored paths."""

from __future__ import print_function

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import (  # noqa: E402
    WriterError,
    atomic_write_json,
    resolve_feature,
    resolve_workspace,
)
from hooks.utest_plan_contract import (  # noqa: E402
    UTestPlanContractError,
    load_utest_plan,
)


SCHEMA_VERSION = "autodev.utest-workspace-bindings.v1"
BINDING_FILE = "workspace-bindings.json"


class UTestWorkspaceBindingError(ValueError):
    """Structured workspace binding or task-location failure."""

    def __init__(self, code, message, required_action, candidates=None):
        ValueError.__init__(self, message)
        self.code = code
        self.required_action = required_action
        self.candidates = list(candidates or [])

    def payload(self):
        result = {
            "status": self.code,
            "owner": "utest_workspace_binding",
            "requiredAction": self.required_action,
            "errors": [str(self)],
        }
        if self.candidates:
            result["candidates"] = list(self.candidates)
        return result


class RepairArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise UTestWorkspaceBindingError(
            "workspace_binding_input_invalid",
            "命令参数无效：{}。修复：只传入产物 workspace、feature；仅在用户选择候选后传 candidate ID。".format(
                message
            ),
            "repair_workspace_binding_input",
        )


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def binding_path(workspace):
    return Path(workspace) / ".autobizdevops" / BINDING_FILE


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _load_bindings(workspace):
    path = binding_path(workspace)
    data = _read_json(path)
    if data is None:
        return {"schemaVersion": SCHEMA_VERSION, "features": {}}
    if data.get("schemaVersion") != SCHEMA_VERSION or not isinstance(data.get("features"), dict):
        raise UTestWorkspaceBindingError(
            "workspace_binding_invalid",
            "workspace binding 文件结构无效：{}。修复：删除该损坏文件后重试，解析器会从已验证的 Code 产物自动重建。".format(
                path
            ),
            "remove_invalid_binding_and_retry",
        )
    return data


def _git_root(raw_path):
    path = Path(raw_path).expanduser().resolve()
    if not path.is_dir():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    root = Path(completed.stdout.strip()).resolve()
    return root if root.is_dir() else None


def _candidate_id(root):
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return "WS-{}".format(digest.upper())


def _matches_workspace_ref(root, workspace_ref):
    return workspace_ref == "default" or root.name == workspace_ref


def _add_candidate(target, raw_path, workspace_ref, source):
    if not isinstance(raw_path, str) or not raw_path.strip():
        return
    root = _git_root(raw_path)
    if root is None or not _matches_workspace_ref(root, workspace_ref):
        return
    key = str(root)
    record = target.get(key)
    if record is None:
        record = {
            "candidateId": _candidate_id(root),
            "repositoryId": root.name,
            "root": key,
            "sources": [],
        }
        target[key] = record
    if source not in record["sources"]:
        record["sources"].append(source)


def _paths_from_runtime_payload(data, workspace_ref):
    if not isinstance(data, dict):
        return []
    paths = []
    repository = data.get("repository")
    if isinstance(repository, dict):
        repository_id = repository.get("id")
        if workspace_ref == "default" or repository_id == workspace_ref:
            paths.append(repository.get("root"))
    repositories = data.get("repositories")
    if isinstance(repositories, list):
        for item in repositories:
            if not isinstance(item, dict):
                continue
            repository_id = item.get("id")
            if workspace_ref == "default" or repository_id == workspace_ref:
                paths.append(item.get("path"))
    scope_workspaces = data.get("scopeWorkspaces")
    if isinstance(scope_workspaces, list):
        for item in scope_workspaces:
            if not isinstance(item, dict):
                continue
            repository_id = item.get("repository")
            if workspace_ref == "default" or repository_id == workspace_ref:
                paths.append(item.get("resolvedGitRoot"))
    for field in ("resolvedGitRoots", "requestedCodeWorkspaces"):
        values = data.get(field)
        if isinstance(values, list):
            paths.extend(values)
    paths.append(data.get("codeWorkspace"))
    return [item for item in paths if isinstance(item, str)]


def _feature_candidates(workspace, feature, workspace_ref):
    feature_dir = Path(workspace) / ".autobizdevops" / "features" / feature
    candidates = {}
    runs_root = feature_dir / ".task-runs"
    if runs_root.is_dir():
        for path in sorted(runs_root.glob("**/*.json")):
            data = _read_json(path)
            if not isinstance(data, dict) or data.get("status") not in {
                "implemented",
                "done",
                "passed",
            }:
                continue
            for raw_path in _paths_from_runtime_payload(data, workspace_ref):
                _add_candidate(candidates, raw_path, workspace_ref, "feature_task_run")
    return candidates


def _cross_feature_candidates(workspace, feature, workspace_ref):
    candidates = {}
    features_root = Path(workspace) / ".autobizdevops" / "features"
    if not features_root.is_dir():
        return candidates
    return candidates


def discover_candidates(workspace, feature, workspace_ref):
    local = _feature_candidates(workspace, feature, workspace_ref)
    if local:
        candidates = local
    elif workspace_ref == "default":
        candidates = {}
    else:
        current = {}
        _add_candidate(current, str(Path.cwd()), workspace_ref, "current_git_workspace")
        candidates = current if current else _cross_feature_candidates(workspace, feature, workspace_ref)
    return [candidates[key] for key in sorted(candidates)]


def _persist_binding(data, workspace, feature, workspace_ref, candidate, source):
    features = data.setdefault("features", {})
    feature_bindings = features.setdefault(feature, {})
    previous = feature_bindings.get(workspace_ref)
    record = {
        "workspaceRef": workspace_ref,
        "repositoryId": candidate["repositoryId"],
        "root": candidate["root"],
        "candidateId": candidate["candidateId"],
        "source": source,
    }
    if previous != record:
        feature_bindings[workspace_ref] = record
        data["updatedAt"] = _utc_now()
        atomic_write_json(binding_path(workspace), data)
    return record


def resolve_workspace_binding(workspace, feature, workspace_ref, selected_candidate_id=None):
    workspace = Path(workspace).resolve()
    data = _load_bindings(workspace)
    feature_bindings = data.get("features", {}).get(feature, {})
    existing = feature_bindings.get(workspace_ref) if isinstance(feature_bindings, dict) else None
    if isinstance(existing, dict) and selected_candidate_id is None:
        root = _git_root(existing.get("root", ""))
        if root is not None and _matches_workspace_ref(root, workspace_ref):
            result = dict(existing)
            result["root"] = str(root)
            result["source"] = "persisted_binding"
            return result

    candidates = discover_candidates(workspace, feature, workspace_ref)
    if selected_candidate_id is not None:
        selected = [item for item in candidates if item["candidateId"] == selected_candidate_id]
        if len(selected) != 1:
            raise UTestWorkspaceBindingError(
                "workspace_binding_candidate_invalid",
                "候选 ID {} 不属于 workspaceRef={}。修复：使用解析器本轮返回的 candidateId。".format(
                    selected_candidate_id, workspace_ref
                ),
                "select_returned_workspace_candidate",
                candidates,
            )
        return _persist_binding(data, workspace, feature, workspace_ref, selected[0], "user_selected")
    if not candidates:
        raise UTestWorkspaceBindingError(
            "workspace_binding_missing",
            "未找到 workspaceRef={} 对应的已验证 Git 仓库。修复：先完成该 Feature 的 Code 执行，或在对应代码仓库中重新运行 UTest；解析器会自动保存绑定。".format(
                workspace_ref
            ),
            "complete_code_or_open_repository_and_retry",
        )
    if len(candidates) > 1:
        raise UTestWorkspaceBindingError(
            "workspace_binding_ambiguous",
            "workspaceRef={} 对应多个已验证仓库。修复：向用户展示 candidates，并将用户选择的 candidateId 交给 workspace binding 脚本保存；不要让模型选择或传递仓库路径。".format(
                workspace_ref
            ),
            "request_user_workspace_candidate_selection",
            candidates,
        )
    return _persist_binding(data, workspace, feature, workspace_ref, candidates[0], "auto_discovered")


def _safe_relative(value, label, task_id):
    if not isinstance(value, str) or not value.strip():
        path = Path(".")
    else:
        path = Path(value)
    if not isinstance(value, str) or not value.strip() or path.is_absolute() or ".." in path.parts:
        raise UTestWorkspaceBindingError(
            "contract_gap",
            "{} {}={} 不是仓库内相对路径。修复：在 /autodev-plan 修正该 TASK 的位置声明。".format(
                task_id, label, value
            ),
            "repair_plan_task_location",
        )
    return path


def path_within(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _task_from_plan(plan, task_id):
    for batch in plan.get("batches", []):
        for task in batch.get("tasks", []):
            if task.get("id") == task_id:
                return batch, task
    raise UTestWorkspaceBindingError(
        "contract_gap",
        "plan 中不存在 TASK {}。修复：重新运行 UTest router，使用当前 plan 的 TASK ID。".format(task_id),
        "reroute_current_utest_plan",
    )


def _task_modules(task):
    raw_task = task.get("rawTask") if isinstance(task, dict) else None
    scope = raw_task.get("scope") if isinstance(raw_task, dict) else None
    modules = scope.get("modules") if isinstance(scope, dict) else []
    if modules is None:
        return []
    if not isinstance(modules, list) or not all(isinstance(item, str) and item.strip() for item in modules):
        raise UTestWorkspaceBindingError(
            "contract_gap",
            "{} scope.modules 不是字符串数组。修复：在 /autodev-plan 修正该 TASK 的模块声明。".format(
                task.get("id", "TASK")
            ),
            "repair_plan_task_location",
        )
    result = []
    for item in modules:
        if item not in result:
            result.append(item)
    return result


def _workspace_prefix(task):
    raw_task = task.get("rawTask") if isinstance(task, dict) else None
    scope = raw_task.get("scope") if isinstance(raw_task, dict) else None
    roots = scope.get("workspaceRoots") if isinstance(scope, dict) else None
    if not isinstance(roots, dict):
        return Path(".")
    workspace_ref = task.get("workspaceRef")
    value = roots.get("default") if "default" in roots else roots.get(workspace_ref, ".")
    return _safe_relative(value, "scope.workspaceRoots", task.get("id", "TASK"))


def _location_roots(task, repository_root):
    task_id = task["id"]
    workspace_ref = task["workspaceRef"]
    result = []
    for location in task["validationLocations"]:
        repository = location.get("repo")
        if repository not in (workspace_ref, "default"):
            raise UTestWorkspaceBindingError(
                "contract_gap",
                "{} validationCommands.repo={} 与 workspaceRef={} 不一致。修复：在 /autodev-plan 统一仓库声明。".format(
                    task_id, repository, workspace_ref
                ),
                "repair_plan_task_location",
            )
        relative = _safe_relative(location.get("cwd"), "validationCommands.cwd", task_id)
        resolved = (repository_root / relative).resolve()
        if not resolved.is_dir() or not path_within(resolved, repository_root):
            raise UTestWorkspaceBindingError(
                "contract_gap",
                "{} validationCommands.cwd={} 在绑定仓库中不存在。修复：在 /autodev-plan 修正该 TASK 的验证目录。".format(
                    task_id, location.get("cwd")
                ),
                "repair_plan_task_location",
            )
        result.append({"repo": repository, "cwd": location["cwd"], "root": resolved})
    return result


def _module_root(repository_root, workspace_root, locations, module, task_id):
    relative = _safe_relative(module, "scope.modules", task_id)
    candidates = [repository_root / relative]
    if workspace_root != Path("."):
        candidates.append(repository_root / workspace_root / relative)
    for location in locations:
        candidates.append(location["root"] / relative)
        if location["root"].name == module:
            candidates.append(location["root"])
    seen = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_dir() and path_within(resolved, repository_root):
            return resolved
    raise UTestWorkspaceBindingError(
        "contract_gap",
        "{} scope.modules 声明的 {} 在绑定仓库中不存在。修复：在 /autodev-plan 修正该 TASK 的模块声明。".format(
            task_id, module
        ),
        "repair_plan_task_location",
    )


def _execution_target_id(task_id, repository_root, execution_root):
    relative = execution_root.relative_to(repository_root).as_posix()
    digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:8].upper()
    return "ENV-{}-{}".format(task_id, digest)


def resolve_task_workspace(workspace, feature, task_id, selected_target_id=None):
    workspace = Path(workspace).resolve()
    feature_dir = workspace / ".autobizdevops" / "features" / feature
    try:
        plan = load_utest_plan(feature_dir)
    except UTestPlanContractError as exc:
        raise UTestWorkspaceBindingError(
            "contract_gap", str(exc), "repair_plan_task_location"
        )
    batch, task = _task_from_plan(plan, task_id)
    binding = resolve_workspace_binding(workspace, feature, task["workspaceRef"])
    repository_root = Path(binding["root"]).resolve()
    locations = _location_roots(task, repository_root)
    workspace_root = _workspace_prefix(task)
    modules = _task_modules(task)
    execution_roots = []
    if modules:
        for module in modules:
            execution_roots.append(
                (module, _module_root(repository_root, workspace_root, locations, module, task_id))
            )
    else:
        execution_roots.extend((None, item["root"]) for item in locations)

    targets = []
    for module, execution_root in execution_roots:
        allowed = [
            item
            for item in locations
            if path_within(execution_root, item["root"])
            or path_within(item["root"], execution_root)
        ]
        if not allowed:
            raise UTestWorkspaceBindingError(
                "contract_gap",
                "{} 的模块 {} 不属于 validationCommands.repo/cwd 确认的目录。修复：在 /autodev-plan 统一模块与验证目录。".format(
                    task_id, module or execution_root.name
                ),
                "repair_plan_task_location",
            )
        allowed.sort(key=lambda item: len(item["root"].parts), reverse=True)
        plan_location = allowed[0]
        target = {
            "environmentTargetId": _execution_target_id(task_id, repository_root, execution_root),
            "taskId": task_id,
            "module": module,
            "repositoryRoot": str(repository_root),
            "executionRoot": str(execution_root),
            "executionCwd": execution_root.relative_to(repository_root).as_posix(),
            "planLocation": {"repo": plan_location["repo"], "cwd": plan_location["cwd"]},
        }
        if target not in targets:
            targets.append(target)
    targets.sort(key=lambda item: (item["executionCwd"], item["environmentTargetId"]))
    if selected_target_id is not None:
        selected = [item for item in targets if item["environmentTargetId"] == selected_target_id]
        if len(selected) != 1:
            raise UTestWorkspaceBindingError(
                "contract_gap",
                "{} 不存在 environmentTargetId={}。修复：使用环境检查器本轮返回的 ID。".format(
                    task_id, selected_target_id
                ),
                "use_current_environment_target_id",
            )
        targets = selected
    return {
        "batchId": batch["batchId"],
        "executionLane": batch["executionLane"],
        "workspaceRef": task["workspaceRef"],
        "taskId": task_id,
        "taskDigest": task["taskDigest"],
        "binding": binding,
        "targets": targets,
    }


def select_task_execution_target(context, test_files=None):
    targets = list(context.get("targets", []))
    if not targets:
        raise UTestWorkspaceBindingError(
            "contract_gap",
            "{} 没有可执行目录。修复：在 /autodev-plan 补齐模块或验证目录。".format(
                context.get("taskId", "TASK")
            ),
            "repair_plan_task_location",
        )
    if test_files:
        repository_root = Path(targets[0]["repositoryRoot"])
        resolved_files = [(repository_root / value).resolve() for value in test_files]
        matches = []
        for target in targets:
            execution_root = Path(target["executionRoot"])
            if all(path_within(path, execution_root) for path in resolved_files):
                matches.append(target)
        if matches:
            matches.sort(key=lambda item: len(Path(item["executionRoot"]).parts), reverse=True)
            return matches[0]
        raise UTestWorkspaceBindingError(
            "contract_gap",
            "{} 的测试文件不属于 scope.modules。修复：把测试写入该 TASK 的模块，或在 /autodev-plan 修正模块范围。".format(
                context.get("taskId", "TASK")
            ),
            "align_test_file_with_task_module",
        )
    if len(targets) != 1:
        raise UTestWorkspaceBindingError(
            "environment_target_ambiguous",
            "{} 对应多个环境目录。修复：使用环境检查器返回的 environmentTargetId 逐个执行 setup。".format(
                context.get("taskId", "TASK")
            ),
            "select_environment_target_id",
            targets,
        )
    return targets[0]


def resolve_feature_bindings(workspace, feature, selected_workspace_ref=None, selected_candidate_id=None):
    feature_dir = Path(workspace) / ".autobizdevops" / "features" / feature
    try:
        plan = load_utest_plan(feature_dir)
    except UTestPlanContractError as exc:
        raise UTestWorkspaceBindingError("contract_gap", str(exc), "repair_plan_task_location")
    refs = []
    for batch in plan["batches"]:
        for task in batch["tasks"]:
            if task["workspaceRef"] not in refs:
                refs.append(task["workspaceRef"])
    if selected_candidate_id is not None:
        if selected_workspace_ref not in refs:
            raise UTestWorkspaceBindingError(
                "workspace_binding_input_invalid",
                "--workspace-ref 不属于当前 plan。修复：使用 candidates 对应的 workspaceRef。",
                "use_current_plan_workspace_ref",
            )
        refs = [selected_workspace_ref]
    result = {}
    for workspace_ref in refs:
        result[workspace_ref] = resolve_workspace_binding(
            workspace,
            feature,
            workspace_ref,
            selected_candidate_id if workspace_ref == selected_workspace_ref else None,
        )
    return result


def main(argv=None):
    parser = RepairArgumentParser(description="自动解析并持久化 UTest workspace binding")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--feature", required=True)
    parser.add_argument("--workspace-ref")
    parser.add_argument("--select-candidate")
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        if bool(args.workspace_ref) != bool(args.select_candidate):
            raise UTestWorkspaceBindingError(
                "workspace_binding_input_invalid",
                "--workspace-ref 与 --select-candidate 必须同时提供。修复：正常自动解析时两者都省略；仅在用户选定候选后同时传入。",
                "repair_workspace_binding_input",
            )
        bindings = resolve_feature_bindings(
            workspace,
            feature,
            args.workspace_ref,
            args.select_candidate,
        )
        result = {"status": "ready", "bindings": bindings, "errors": []}
    except UTestWorkspaceBindingError as exc:
        result = exc.payload()
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False))
        return 2
    except (WriterError, ValueError, OSError) as exc:
        result = {
            "status": "workspace_binding_failed",
            "owner": "utest_workspace_binding",
            "requiredAction": "repair_workspace_binding_artifacts",
            "errors": ["workspace binding 解析失败：{}。修复：检查产物 workspace 与 Feature 后重试。".format(exc)],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
