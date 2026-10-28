#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve trusted UTest workspaces from the current Plan contract."""

from __future__ import print_function

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import resolve_feature, resolve_workspace  # noqa: E402
from hooks.plan_write_ownership import normalize_owned_path  # noqa: E402
from hooks.task_run_integrity import task_run_integrity_error  # noqa: E402
from hooks.utest_plan_contract import (  # noqa: E402
    UTestPlanContractError,
    load_utest_plan,
)


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
            "命令参数无效：{}。修复：只传入产物 workspace、feature。".format(message),
            "repair_workspace_binding_input",
        )


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


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


def _git_common_dir(raw_path):
    root = _git_root(raw_path)
    if root is None:
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    candidate = Path(completed.stdout.strip())
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve() if candidate.is_dir() else None


def _resolve_execution_repository(binding, code_workspace):
    """Allow only a native Worktree of the Plan-bound repository.

    The UTest runner normally resolves the primary checkout from the Plan
    codeWorkspaces field. A Batch may instead need to generate tests in its
    isolated native Worktree before that Batch is merged. Matching Git common
    directories proves that the supplied checkout belongs to the same
    repository without allowing a model to redirect a test command elsewhere.
    """

    planned_root = Path(binding["root"]).resolve()
    if code_workspace is None:
        return planned_root, binding
    candidate = _git_root(code_workspace)
    if candidate is None:
        raise UTestWorkspaceBindingError(
            "workspace_binding_invalid",
            "UTest code workspace 不是有效 Git Worktree。修复：使用当前 Batch 的插件原生 Worktree。",
            "use_batch_native_worktree",
        )
    if _git_common_dir(planned_root) != _git_common_dir(candidate):
        raise UTestWorkspaceBindingError(
            "workspace_binding_invalid",
            "UTest code workspace 不属于当前 TASK 的 Plan 绑定仓库。修复：只使用该 Batch 的插件原生 Worktree。",
            "use_batch_native_worktree",
        )
    overridden = dict(binding)
    overridden["root"] = str(candidate)
    overridden["source"] = "batch_native_worktree"
    return candidate, overridden


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
            "repositoryId": root.name,
            "root": key,
            "sources": [],
        }
        target[key] = record
    if source not in record["sources"]:
        record["sources"].append(source)


def _plan_workspace_candidates(feature_dir, workspace_ref):
    """Read the Plan's explicit, repository-owned Code workspace binding."""
    root = _read_json(Path(feature_dir) / "plan.json")
    mapping = root.get("codeWorkspaces") if isinstance(root, dict) else None
    if not isinstance(mapping, dict):
        return {}
    candidates = {}
    if workspace_ref == "default":
        for raw_path in mapping.values():
            _add_candidate(candidates, raw_path, workspace_ref, "feature_plan_code_workspace")
    else:
        _add_candidate(
            candidates,
            mapping.get(workspace_ref),
            workspace_ref,
            "feature_plan_code_workspace",
        )
    return candidates


def discover_candidates(workspace, feature, workspace_ref):
    feature_dir = Path(workspace) / ".autobizdevops" / "features" / feature
    candidates = _plan_workspace_candidates(feature_dir, workspace_ref)
    return [candidates[key] for key in sorted(candidates)]


def resolve_workspace_binding(workspace, feature, workspace_ref):
    workspace = Path(workspace).resolve()
    candidates = discover_candidates(workspace, feature, workspace_ref)
    if not candidates:
        raise UTestWorkspaceBindingError(
            "workspace_binding_missing",
            "Plan 的 codeWorkspaces 未提供 workspaceRef={} 对应的有效 Git 仓库。修复：回到 /autodev-plan 修正当前 Plan 的代码仓库映射后重试。".format(workspace_ref),
            "repair_plan_code_workspaces",
        )
    if len(candidates) > 1:
        raise UTestWorkspaceBindingError(
            "workspace_binding_invalid",
            "Plan 的 codeWorkspaces 对 workspaceRef={} 解析出多个 Git 仓库。修复：回到 /autodev-plan 保留唯一映射。".format(workspace_ref),
            "repair_plan_code_workspaces",
        )
    result = dict(candidates[0])
    result["workspaceRef"] = workspace_ref
    result["source"] = "plan_code_workspaces"
    return result


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
    """Use the task's Plan-bound repository as UTest's only location authority."""
    return [{
        "repo": task["workspaceRef"],
        "cwd": ".",
        "root": Path(repository_root).resolve(),
    }]


_PROJECT_MANIFESTS = {
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "package.json",
}
_PROJECT_SCAN_PRUNED_DIRS = {
    ".autobizdevops",
    ".git",
    ".idea",
    ".next",
    ".nuxt",
    ".pytest_cache",
    ".venv",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "target",
    "vendor",
}


def _discover_project_roots(repository_root):
    """Find build roots only inside the Plan-bound repository/worktree.

    Symlinked directories and nested Git repositories are not traversed.
    """
    root = Path(repository_root).resolve()
    found = []
    for current, dirnames, filenames in os.walk(str(root), followlinks=False):
        current_path = Path(current).resolve()
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _PROJECT_SCAN_PRUNED_DIRS
            and not (current_path / name).is_symlink()
            and not (current_path / name / ".git").exists()
        ]
        manifests = _PROJECT_MANIFESTS.intersection(filenames)
        if not any(
            not (current_path / name).is_symlink()
            and path_within((current_path / name).resolve(), root)
            for name in manifests
        ):
            continue
        if not path_within(current_path, root):
            continue
        found.append(current_path)
    return sorted(set(found), key=lambda path: (len(path.parts), str(path).casefold()))


def _git_changed_paths(repository_root):
    """Return paths changed by the current sealed commit or local UTest edits."""
    root = Path(repository_root).resolve()
    commands = (
        ["git", "-C", str(root), "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", "HEAD"],
        ["git", "-C", str(root), "diff", "--name-only", "-z", "HEAD", "--"],
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
    )
    paths = []
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError:
            continue
        if completed.returncode != 0:
            continue
        for raw_path in completed.stdout.split(b"\0"):
            if not raw_path:
                continue
            value = os.fsdecode(raw_path)
            if value not in paths:
                paths.append(value)
    return paths


def _projects_for_changed_paths(repository_root, project_roots, changed_paths):
    repository_root = Path(repository_root).resolve()
    matched_roots = set()
    for changed_path in changed_paths:
        changed = (repository_root / changed_path).resolve()
        if not path_within(changed, repository_root):
            continue
        matches = [root for root in project_roots if path_within(changed, root)]
        if matches:
            # A changed file belongs to the nearest project root. This avoids
            # selecting an unrelated Maven aggregator above a nested module.
            matched_roots.add(max(matches, key=lambda root: len(root.parts)))
    return sorted(matched_roots, key=lambda path: (len(path.parts), str(path).casefold()))


def _task_implementation_paths(feature_dir, task, repository_root):
    """Reuse the current Task's verified Code handoff before Batch-wide diffs."""
    evidence_id = task["rawTask"].get("latestImplementationEvidenceId")
    if not evidence_id:
        return []
    for path in (feature_dir / ".task-runs" / task["id"]).glob("*.json"):
        state = _read_json(path)
        if not state or state.get("implementationEvidenceId") != evidence_id or task_run_integrity_error(state):
            continue
        if not any(Path(item["path"]).resolve() == repository_root for item in state.get("repositories", [])):
            continue
        return [normalized for raw in [*state.get("changedFiles", []), *state.get("supportingFiles", [])]
                if (normalized := normalize_owned_path(raw, task["workspaceRef"])) is not None]
    return []


def _execution_target_id(task_id, repository_root, execution_root):
    relative = execution_root.relative_to(repository_root).as_posix()
    digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:8].upper()
    return "ENV-{}-{}".format(task_id, digest)


def resolve_task_workspace(
    workspace,
    feature,
    task_id,
    selected_target_id=None,
    *,
    code_workspace=None,
):
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
    repository_root, binding = _resolve_execution_repository(binding, code_workspace)
    locations = _location_roots(task, repository_root)
    workspace_root = _workspace_prefix(task)
    scan_root = (repository_root / workspace_root).resolve()
    if not path_within(scan_root, repository_root) or not scan_root.is_dir():
        raise UTestWorkspaceBindingError(
            "contract_gap",
            "{} scope.workspaceRoots 指向绑定仓库外或不存在的目录：{}。修复：在 /autodev-plan 修正 workspaceRoots。".format(
                task_id, workspace_root
            ),
            "repair_plan_task_location",
        )
    discovered_roots = _discover_project_roots(scan_root)
    changed_paths = _task_implementation_paths(feature_dir, task, repository_root) or _git_changed_paths(repository_root)
    changed_roots = _projects_for_changed_paths(repository_root, discovered_roots, changed_paths)
    location_warnings = []
    if changed_roots:
        discovered_roots = changed_roots
        location_warnings.append(
            "{} 已按实现文件或 Git 改动定位工程：{}。".format(
                task_id, ", ".join(path.relative_to(repository_root).as_posix() for path in discovered_roots)
            )
        )
    if not discovered_roots:
        location_warnings.append("{} 未发现构建清单；保留 workspaceRoot 作为检查目标。".format(task_id))
        discovered_roots = [scan_root]

    targets = []
    for execution_root in discovered_roots:
        module = execution_root.relative_to(repository_root).as_posix()
        allowed = [
            item
            for item in locations
            if path_within(execution_root, item["root"])
            or path_within(item["root"], execution_root)
        ]
        if not allowed:
            raise UTestWorkspaceBindingError(
                "contract_gap",
                "{} 的模块 {} 不属于当前 workspaceRef 的绑定仓库。修复：在 /autodev-plan 统一模块与仓库声明。".format(
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
            "projectBoundary": str(scan_root),
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
        "locationWarnings": location_warnings,
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
            "{} 的测试文件不属于本 TASK 的实现工程。修复：依据实际实现文件和构建清单定位测试目录。".format(
                context.get("taskId", "TASK")
            ),
            "align_test_file_with_task_module",
        )
    if len(targets) != 1:
        raise UTestWorkspaceBindingError(
            "project_context_required",
            "{} 需要进一步定位测试工程。执行 Agent 应检查当前 TASK 的实现、编译目录和构建清单，自动确定 environmentTargetId 或测试文件后继续，不向用户提问。".format(
                context.get("taskId", "TASK")
            ),
            "resolve_project_from_code",
            targets,
        )
    return targets[0]


def resolve_feature_bindings(workspace, feature):
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
    result = {}
    for workspace_ref in refs:
        result[workspace_ref] = resolve_workspace_binding(workspace, feature, workspace_ref)
    return result


def main(argv=None):
    parser = RepairArgumentParser(description="从当前 Plan 解析 UTest workspace")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--feature", required=True)
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        workspace = resolve_workspace(args.workspace)
        feature = resolve_feature(args.feature)
        bindings = resolve_feature_bindings(workspace, feature)
        result = {"status": "ready", "bindings": bindings, "errors": []}
    except UTestWorkspaceBindingError as exc:
        result = exc.payload()
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False))
        return 2
    except (ValueError, OSError) as exc:
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
