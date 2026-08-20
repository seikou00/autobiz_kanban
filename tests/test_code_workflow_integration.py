#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Code Workflow 集成测试：验证并行执行流程。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_command(cmd: list[str], cwd: Path | None = None) -> dict[str, Any]:
    """执行命令并返回结果。"""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr
    }


def test_workflow_launcher():
    """测试 workflow_launcher.py。"""
    print("测试 1: Workflow Launcher")
    print("-" * 60)

    # 测试不存在的 feature
    result = run_command([
        "python3",
        str(ROOT / "hooks" / "workflow_launcher.py"),
        "--feature", "non-existent-feature",
        "--json"
    ])

    if result["returncode"] == 0:
        data = json.loads(result["stdout"])
        assert data["useWorkflow"] is False
        # reason 可能是 plan_not_found 或其他错误
        assert "reason" in data
        print("✓ 处理不存在的 feature")
    else:
        print(f"✗ 失败: {result['stderr']}")
        return False

    print()
    return True


def test_worktree_manager():
    """测试 worktree_manager.py。"""
    print("测试 2: Worktree Manager")
    print("-" * 60)

    # 创建临时 Git 仓库
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "test-repo"
        repo_path.mkdir()

        # 初始化 Git 仓库
        run_command(["git", "init"], cwd=repo_path)
        run_command(["git", "config", "user.name", "Test"], cwd=repo_path)
        run_command(["git", "config", "user.email", "test@example.com"], cwd=repo_path)

        # 创建初始提交
        (repo_path / "README.md").write_text("# Test Repo")
        run_command(["git", "add", "."], cwd=repo_path)
        run_command(["git", "commit", "-m", "Initial commit"], cwd=repo_path)

        # 添加 .gitignore
        (repo_path / ".gitignore").write_text(".worktrees/\n")
        run_command(["git", "add", ".gitignore"], cwd=repo_path)
        run_command(["git", "commit", "-m", "Add .gitignore"], cwd=repo_path)

        # 测试创建 worktree
        result = run_command([
            "python3",
            str(ROOT / "hooks" / "worktree_manager.py"),
            "--json",
            "create",
            "--repo", str(repo_path),
            "--name", "test-batch-001"
        ])

        if result["returncode"] == 0:
            data = json.loads(result["stdout"])
            if data.get("success"):
                print(f"✓ 创建 worktree: {data['worktreePath']}")
                print(f"  分支: {data['branchName']}")

                # 验证 worktree 存在
                worktree_path = Path(data["worktreePath"])
                if worktree_path.exists():
                    print("✓ Worktree 目录存在")
                else:
                    print("✗ Worktree 目录不存在")
                    return False

                # 测试列出 worktrees
                list_result = run_command([
                    "python3",
                    str(ROOT / "hooks" / "worktree_manager.py"),
                    "--json",
                    "list",
                    "--repo", str(repo_path)
                ])

                if list_result["returncode"] == 0:
                    list_data = json.loads(list_result["stdout"])
                    print(f"✓ 列出 worktrees: {len(list_data.get('worktrees', []))} 个")
                else:
                    print(f"✗ 列出失败: {list_result['stderr']}")
                    return False

                # 测试删除 worktree
                remove_result = run_command([
                    "python3",
                    str(ROOT / "hooks" / "worktree_manager.py"),
                    "--json",
                    "remove",
                    "--repo", str(repo_path),
                    "--name", "test-batch-001",
                    "--force"
                ])

                if remove_result["returncode"] == 0:
                    remove_data = json.loads(remove_result["stdout"])
                    if remove_data.get("success"):
                        print("✓ 删除 worktree")
                    else:
                        print(f"✗ 删除失败: {remove_data.get('error')}")
                        return False
                else:
                    print(f"✗ 删除命令失败: {remove_result['stderr']}")
                    return False

            else:
                print(f"✗ 创建失败: {data.get('error')}")
                return False
        else:
            print(f"✗ 命令失败: {result['stderr']}")
            return False

    print()
    return True


def test_batch_merger():
    """测试 batch_merger.py。"""
    print("测试 3: Batch Merger")
    print("-" * 60)

    # 测试冲突检测
    batches = [
        {"id": "B001", "changedFiles": ["src/a.py", "src/b.py"]},
        {"id": "B002", "changedFiles": ["src/b.py", "src/c.py"]},
        {"id": "B003", "changedFiles": ["src/c.py", "src/d.py"]}
    ]

    result = run_command([
        "python3",
        str(ROOT / "hooks" / "batch_merger.py"),
        "--json",
        "detect-conflicts",
        "--batches", json.dumps(batches)
    ])

    # batch_merger 返回 exit code 1 但输出 JSON
    data = None
    try:
        data = json.loads(result["stdout"])
    except:
        pass

    if data:
        conflicts = data.get("conflicts", [])
        print(f"✓ 检测到 {len(conflicts)} 个冲突")

        # 验证冲突检测正确性
        expected_conflicts = {
            "src/b.py": {"B001", "B002"},
            "src/c.py": {"B002", "B003"}
        }

        all_correct = True
        for conflict in conflicts:
            file = conflict["file"]
            batches_set = set(conflict["batches"])
            if file in expected_conflicts:
                if batches_set == expected_conflicts[file]:
                    print(f"  - {file}: {', '.join(conflict['batches'])} ✓")
                else:
                    print(f"  - {file}: 冲突检测错误 ✗")
                    all_correct = False
            else:
                print(f"  - {file}: 不应该有冲突 ✗")
                all_correct = False

        if all_correct and len(conflicts) == len(expected_conflicts):
            print("✓ 冲突检测准确")
        else:
            print("✗ 冲突检测不完整或不准确")
            return False
    else:
        print(f"✗ 命令失败: {result['stderr']}")
        return False

    print()
    return True


def test_workflow_script_syntax():
    """测试 workflow 脚本语法。"""
    print("测试 4: Workflow 脚本语法")
    print("-" * 60)

    workflow_script = ROOT / "workflows" / "code-batched-execution.workflow.js"

    if not workflow_script.exists():
        print(f"✗ Workflow 脚本不存在: {workflow_script}")
        return False

    # 读取脚本内容
    content = workflow_script.read_text(encoding="utf-8")

    # 检查必要的元素
    checks = [
        ("export const meta", "meta 定义"),
        ("phase(", "phase 函数调用"),
        ("agent(", "agent 函数调用"),
        ("pipeline(", "pipeline 函数调用"),
        ("BATCH_EXECUTION_SCHEMA", "执行 schema"),
        ("MERGE_RESULT_SCHEMA", "合并 schema"),
        ("VERIFICATION_SCHEMA", "验证 schema"),
    ]

    all_passed = True
    for pattern, description in checks:
        if pattern in content:
            print(f"✓ {description}")
        else:
            print(f"✗ 缺少 {description}")
            all_passed = False

    print()
    return all_passed


def test_skill_integration():
    """测试技能集成。"""
    print("测试 5: 技能集成")
    print("-" * 60)

    skill_file = ROOT / "skills" / "autodev" / "autodev-code" / "SKILL.md"

    if not skill_file.exists():
        print(f"✗ 技能文件不存在: {skill_file}")
        return False

    content = skill_file.read_text(encoding="utf-8")

    # 检查是否添加了 Workflow 入口
    checks = [
        ("## Workflow 并行执行模式", "Workflow 章节"),
        ("workflow_launcher.py", "启动器引用"),
        ("isolation: \"worktree\"", "Worktree 隔离说明"),
        ("并行实现阶段", "阶段说明"),
    ]

    all_passed = True
    for pattern, description in checks:
        if pattern in content:
            print(f"✓ {description}")
        else:
            print(f"✗ 缺少 {description}")
            all_passed = False

    print()
    return all_passed


def main():
    """运行所有测试。"""
    print("=" * 60)
    print("Code Workflow 集成测试")
    print("=" * 60)
    print()

    tests = [
        ("Workflow Launcher", test_workflow_launcher),
        ("Worktree Manager", test_worktree_manager),
        ("Batch Merger", test_batch_merger),
        ("Workflow Script Syntax", test_workflow_script_syntax),
        ("Skill Integration", test_skill_integration),
    ]

    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, passed))
        except Exception as e:
            print(f"✗ 测试异常: {e}")
            results.append((name, False))

    # 汇总结果
    print("=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    total = len(results)
    passed = sum(1 for _, p in results if p)

    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{status:8s} {name}")

    print("-" * 60)
    print(f"总计: {passed}/{total} 通过")
    print()

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
