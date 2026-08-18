#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 Code 阶段批次内迭代修改功能"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.task_runner import (
    start_task,
    finish_implementation,
    run_batch_compile,
    TaskRunnerError,
)


@pytest.fixture
def test_workspace(tmp_path):
    """创建测试工作空间"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    feature_dir = workspace / ".autobizdevops" / "features" / "test-feature"
    feature_dir.mkdir(parents=True)
    return workspace, feature_dir


@pytest.fixture
def mock_git_repo(tmp_path):
    """创建模拟 Git 仓库"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hello')")
    return repo


@pytest.fixture
def test_plan(feature_dir):
    """创建测试计划"""
    plan = {
        "version": 2,
        "featureId": "test-feature",
        "status": "in_progress",
        "activeBatchId": "B1",
        "deferToTestStages": True,
        "batches": [
            {
                "id": "B1",
                "executionLane": "code",
                "taskIds": ["T1"],
            }
        ],
        "tasks": [
            {
                "id": "T1",
                "goal": "Implement hello world",
                "status": "todo",
                "workspaceRef": "default",
                "workspaceRoots": {"default": "."},
                "acceptanceCriteria": [{"id": "C1", "text": "Code compiles"}],
                "validationCommands": [
                    {
                        "id": "compile-1",
                        "kind": "compile",
                        "required": True,
                        "covers": ["C1"],
                        "argv": ["python", "-m", "py_compile", "src/main.py"],
                        "cwd": ".",
                    }
                ],
            }
        ],
        "_batchPlans": {
            "B1": {
                "batchId": "B1",
                "executionLane": "code",
                "tasks": [
                    {
                        "id": "T1",
                        "goal": "Implement hello world",
                        "status": "todo",
                    }
                ],
                "batchValidation": {
                    "commands": [
                        {
                            "id": "compile-1",
                            "kind": "compile",
                            "required": True,
                            "argv": ["python", "-m", "py_compile", "src/main.py"],
                            "cwd": ".",
                        }
                    ]
                },
            }
        },
    }
    plan_path = feature_dir / "PLAN.json"
    plan_path.write_text(json.dumps(plan, indent=2))
    return plan, plan_path


def test_iterative_editing_before_compile(test_workspace, mock_git_repo, test_plan):
    """测试在批次编译前允许迭代修改"""
    workspace, feature_dir = test_workspace
    plan, plan_path = test_plan

    # 第一次实现
    with patch("hooks.task_runner._git_root", return_value=mock_git_repo):
        with patch("hooks.task_runner._git_snapshot", return_value={"src/main.py": "hash1"}):
            with patch("hooks.task_runner._git_untracked_files", return_value=[]):
                state1 = start_task(workspace, "test-feature", "T1", mock_git_repo)
                assert state1["status"] == "started"
                run_id_1 = state1["runId"]

                # 模拟代码修改（第一次）
                (mock_git_repo / "src" / "main.py").write_text("print('hello v1')")

                with patch("hooks.task_runner._git_snapshot", return_value={"src/main.py": "hash2"}):
                    success1, result1 = finish_implementation(
                        workspace,
                        "test-feature",
                        "T1",
                        mock_git_repo,
                        run_id_1,
                        no_code_change_why=None,
                        supporting_files=[],
                    )
                    assert success1
                    assert result1["status"] == "implemented"
                    evidence_id_1 = result1["implementationEvidenceId"]

    # 重新加载计划，验证任务状态
    plan_data = json.loads(plan_path.read_text())
    task = next(t for t in plan_data["tasks"] if t["id"] == "T1")
    assert task["status"] == "implemented"
    assert task["implementationRevision"] == 1
    assert task["latestImplementationEvidenceId"] == evidence_id_1

    # 第二次实现（用户继续对话修改）
    with patch("hooks.task_runner._git_root", return_value=mock_git_repo):
        with patch("hooks.task_runner._git_snapshot", return_value={"src/main.py": "hash2"}):
            with patch("hooks.task_runner._git_untracked_files", return_value=[]):
                # 应该允许重新启动
                state2 = start_task(workspace, "test-feature", "T1", mock_git_repo)
                assert state2["status"] == "started"
                run_id_2 = state2["runId"]
                assert run_id_2 != run_id_1

    # 重新加载计划，验证状态回退
    plan_data = json.loads(plan_path.read_text())
    task = next(t for t in plan_data["tasks"] if t["id"] == "T1")
    assert task["status"] == "in_progress"  # 状态已回退

    # 模拟第二次代码修改
    (mock_git_repo / "src" / "main.py").write_text("print('hello v2')")

    with patch("hooks.task_runner._git_root", return_value=mock_git_repo):
        with patch("hooks.task_runner._git_snapshot", return_value={"src/main.py": "hash3"}):
            with patch("hooks.task_runner._git_untracked_files", return_value=[]):
                success2, result2 = finish_implementation(
                    workspace,
                    "test-feature",
                    "T1",
                    mock_git_repo,
                    run_id_2,
                    no_code_change_why=None,
                    supporting_files=[],
                )
                assert success2
                assert result2["status"] == "implemented"
                evidence_id_2 = result2["implementationEvidenceId"]
                assert evidence_id_2 != evidence_id_1

    # 验证最终状态
    plan_data = json.loads(plan_path.read_text())
    task = next(t for t in plan_data["tasks"] if t["id"] == "T1")
    assert task["status"] == "implemented"
    assert task["implementationRevision"] == 2  # 版本递增
    assert task["latestImplementationEvidenceId"] == evidence_id_2
    assert evidence_id_1 in task["implementationEvidenceIds"]  # 历史保留
    assert evidence_id_2 in task["implementationEvidenceIds"]


def test_reject_editing_after_compile_passed(test_workspace, mock_git_repo, test_plan):
    """测试在批次编译通过后拒绝重新启动"""
    workspace, feature_dir = test_workspace
    plan, plan_path = test_plan

    # 完成任务实现
    with patch("hooks.task_runner._git_root", return_value=mock_git_repo):
        with patch("hooks.task_runner._git_snapshot", return_value={"src/main.py": "hash1"}):
            with patch("hooks.task_runner._git_untracked_files", return_value=[]):
                state = start_task(workspace, "test-feature", "T1", mock_git_repo)
                run_id = state["runId"]

                (mock_git_repo / "src" / "main.py").write_text("print('hello')")

                with patch("hooks.task_runner._git_snapshot", return_value={"src/main.py": "hash2"}):
                    finish_implementation(
                        workspace,
                        "test-feature",
                        "T1",
                        mock_git_repo,
                        run_id,
                        no_code_change_why=None,
                        supporting_files=[],
                    )

    # 模拟批次编译通过
    plan_data = json.loads(plan_path.read_text())
    plan_data["_batchPlans"]["B1"]["batchCompile"] = {
        "status": "passed",
        "commandId": "compile-1",
    }
    plan_path.write_text(json.dumps(plan_data, indent=2))

    # 尝试重新启动任务
    with patch("hooks.task_runner._git_root", return_value=mock_git_repo):
        with patch("hooks.task_runner._git_snapshot", return_value={"src/main.py": "hash2"}):
            with patch("hooks.task_runner._git_untracked_files", return_value=[]):
                with pytest.raises(TaskRunnerError) as exc_info:
                    start_task(workspace, "test-feature", "T1", mock_git_repo)

                assert "task_implementation_already_ready" in str(exc_info.value)
                assert exc_info.value.details.get("batchCompileStatus") == "passed"


def test_reject_editing_after_compile_failed(test_workspace, mock_git_repo, test_plan):
    """测试在批次编译失败后拒绝直接重新启动"""
    workspace, feature_dir = test_workspace
    plan, plan_path = test_plan

    # 完成任务实现
    with patch("hooks.task_runner._git_root", return_value=mock_git_repo):
        with patch("hooks.task_runner._git_snapshot", return_value={"src/main.py": "hash1"}):
            with patch("hooks.task_runner._git_untracked_files", return_value=[]):
                state = start_task(workspace, "test-feature", "T1", mock_git_repo)
                run_id = state["runId"]

                (mock_git_repo / "src" / "main.py").write_text("print('hello')")

                with patch("hooks.task_runner._git_snapshot", return_value={"src/main.py": "hash2"}):
                    finish_implementation(
                        workspace,
                        "test-feature",
                        "T1",
                        mock_git_repo,
                        run_id,
                        no_code_change_why=None,
                        supporting_files=[],
                    )

    # 模拟批次编译失败
    plan_data = json.loads(plan_path.read_text())
    plan_data["_batchPlans"]["B1"]["batchCompile"] = {
        "status": "failed",
        "commandId": "compile-1",
        "repairOwnerTaskIds": ["T1"],
    }
    plan_path.write_text(json.dumps(plan_data, indent=2))

    # 尝试重新启动任务（应该被拒绝，必须用 start-batch-compile-repair）
    with patch("hooks.task_runner._git_root", return_value=mock_git_repo):
        with patch("hooks.task_runner._git_snapshot", return_value={"src/main.py": "hash2"}):
            with patch("hooks.task_runner._git_untracked_files", return_value=[]):
                with pytest.raises(TaskRunnerError) as exc_info:
                    start_task(workspace, "test-feature", "T1", mock_git_repo)

                assert "batch_compile_repair_requires_explicit_start" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
