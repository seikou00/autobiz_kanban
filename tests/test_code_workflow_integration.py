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


def test_fixed_workflow_entrypoint():
    """测试多 Batch 使用仓库固定的 workflow 脚本。"""
    print("测试 4: Fixed Workflow 入口")
    print("-" * 60)

    workflow_script = ROOT / "workflows" / "code-batched-execution.workflow.js"
    if not workflow_script.exists():
        print(f"✗ 固定脚本不存在: {workflow_script}")
        return False
    launcher = (ROOT / "hooks" / "workflow_launcher.py").read_text(encoding="utf-8")
    if "code-batched-execution.workflow.js" not in launcher:
        print("✗ launcher 未返回固定 workflow 脚本")
        return False
    content = workflow_script.read_text(encoding="utf-8")
    checks = [
        "await parallel",
        "worktree_manager.py",
        "parallel_merge_train.py",
        "parallel_stage_validation.py",
        "parallel_batch_lifecycle.py",
        "cleanup-merged",
        "merged_worktree_cleanup_incomplete",
        "schedulerPath}\" ensure",
        "resume --workspace",
        "worktreeManagerPath}",
        "--json provision",
        "parallel_evidence_aggregate.py",
        "ready_to_candidate",
        "B-E2E",
        "batchTaskIds",
        "不要用 read_file 读取 artifact 目录",
        "function usableString",
        "function normalizePath",
        "function samePath",
        "function joinPath",
        "taskContract.uiRequired",
        "Route resolver",
        "invalid_code_workspace_path",
        "不得创建任何 workflow",
        "required: [\"batchId\", \"status\", \"compileStatus\", \"worktreePath\", \"branchName\", \"commitSha\"]",
        "parallel_scheduler_stalled",
        "errorMessage",
        "--ttl-seconds ${timeoutPerBatch}",
        "heartbeat",
        "heartbeatDirectory",
        "后续实现、编译和 seal 全程保持 heartbeat 运行",
        "Start-Process",
        "仅 valid=true 才可继续",
        "停止 heartbeat",
        "git symbolic-ref --quiet --short HEAD",
        "LEASE_TOKEN",
        "reclaim --workspace",
        "--force",
        "禁止检查/修改插件源码",
    ]
    missing = [check for check in checks if check not in content]
    if missing:
        print(f"✗ 固定脚本缺少执行协议: {', '.join(missing)}")
        return False
    route_start = content.find("start-route-run")
    task_prompt = content.find("以 taskContract.uiRequired 为唯一条件")
    if route_start < 0 or task_prompt < 0 or route_start < task_prompt:
        print("✗ Route resolver 未绑定到前端 Task Agent 协议")
        return False
    print("✓ 多 Batch 使用固定 workflow 脚本")
    print("✓ 每个波次完成后合并并重新调度")
    print()
    return True


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
        ("native_git_worktrees", "插件原生 Worktree 隔离说明"),
        ("worktree_manager.py provision", "插件 Worktree 创建阶段说明"),
        ("不得调用 `task_runner.py code-session`", "废弃 Code 会话入口保护"),
        ("mergeCommitSha", "合并证据保护"),
        ("前端 Route 闸门（按 Task 在 Agent 内执行）", "按 Task 执行 Route 闸门"),
        ("taskContract.uiRequired=true", "Task UI 机器事实"),
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
        ("Fixed Workflow Entrypoint", test_fixed_workflow_entrypoint),
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
