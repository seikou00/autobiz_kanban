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
        "--task-card-id", "Z990692-294",
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
        "utest_assignment_router.py",
        "run_utest_command.py",
        "inspect_test_environment.py",
        "validate_utest_source_bug.py",
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
        "function requireSchedulerResult",
        "parallel_scheduler_result_invalid",
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
        "后续编码和草稿封存全程保持 heartbeat 运行",
        "Start-Process",
        "仅 valid=true 才可继续",
        "停止 heartbeat",
        "git symbolic-ref --quiet --short HEAD",
        "LEASE_TOKEN",
        "reclaim --workspace",
        "--force",
        "禁止检查/修改插件源码",
        "runDeliveryWithImplementationRepair",
        "implementation_rework_required",
        "function isFailedVerdict",
        "function isFailedStatus",
        "function hasFailureSignal",
        "failureType:\"implementation\"",
        "--stage review --failure-type",
        "promotions.flatMap(mergedBatchIds)",
        "promotion_batch_ids_missing",
        "runBatchUtestAndSeal",
        "blockImplementationFinding",
        "delivery_implementation_repair_unresolved",
        "--batch-worktree",
        "不得因 sealed commit 缺少测试文件而判定 Review 不通过",
        "修复、编译和封存一次，然后直接进入 UTest，不会再次执行 Review",
        "failureContext",
        "本次打回的精确问题如下",
        "targetId、commandId、evidenceId、test-output.log 路径",
        "record-test-failure",
        'testStatus:\\"deferred\\"',
        "--purpose review",
        "compileAndSealDelivery",
        "revalidate-batch-compile",
        "SINGLE_REPAIRABLE_STAGES",
        "recordSingleRepairResolution",
        "single_repair_accepted",
        "if (!reviewResolvedByRepair && !testResolvedByRepair)",
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
    review_prompt = content.find("对已草稿封存、尚未编译的 Batch")
    compile_prompt = content.find("已通过业务 Review，现在才执行本 Batch 的首次编译")
    if review_prompt < 0 or compile_prompt < 0:
        print("✗ Review 与编译的固定顺序缺失")
        return False
    print("✓ 多 Batch 使用固定 workflow 脚本")
    print("✓ 每个波次完成后合并并重新调度")
    print()
    return True


def test_workflow_structured_output_normalization():
    """Review 的 think 前缀或 Markdown 围栏不得吞掉失败信号。"""
    print("测试 5: Workflow 结构化输出归一化")
    print("-" * 60)

    workflow_script = ROOT / "workflows" / "code-batched-execution.workflow.js"
    script = r'''
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[1], "utf8");
const context = {};
vm.createContext(context);
const helperStart = source.indexOf("function normalizeStructuredOutput(");
const inputStart = source.indexOf("const input = unwrap(args);");
const reworkStart = source.indexOf("function requiresImplementationRework(");
const reworkEnd = source.indexOf("function withLatestBatchDelivery(", reworkStart);
if (helperStart < 0 || inputStart < 0 || reworkStart < 0 || reworkEnd < 0) process.exit(2);
vm.runInContext(source.slice(helperStart, inputStart), context);
vm.runInContext(source.slice(reworkStart, reworkEnd), context);
const failed = '{"status":"failed","verdict":"FAIL","failureType":"implementation","nextStage":"implement","failure":{"type":"implementation","nextStage":"implement"}}';
const samples = [
  `<think>reasoning that must not be part of the protocol</think>\n${failed}`,
  `\`\`\`json\n${failed}\n\`\`\``
];
for (const sample of samples) {
  const parsed = context.unwrap(sample);
  if (parsed.status !== "failed" || !context.requiresImplementationRework(sample)) process.exit(3);
  let rejected = false;
  try { context.requireSuccess(sample, "review"); } catch (_) { rejected = true; }
  if (!rejected) process.exit(4);
}
const malformed = "review result without JSON";
let rejectedMalformed = false;
try { context.requireSuccess(malformed, "review"); } catch (_) { rejectedMalformed = true; }
if (!rejectedMalformed) process.exit(5);
const repaired = context.implementationReworkRequired(
  { batchId: "B001", worktreePath: "/tmp/worktree", branchName: "batch", commitSha: "sha" },
  "review",
  { status: "failed", failureType: "implementation", failure: { type: "implementation", message: "src/auth.js:42 expected authorization before write", nextStage: "implement" } }
);
if (repaired.recovery.failureContext.message !== "src/auth.js:42 expected authorization before write") process.exit(6);
if (repaired.recovery.failureContext.failedStage !== "review") process.exit(7);
let missingMessageRejected = false;
try {
  context.implementationReworkRequired(
    { batchId: "B001", worktreePath: "/tmp/worktree", branchName: "batch", commitSha: "sha" },
    "test",
    { status: "failed", failureType: "implementation", failure: { type: "implementation", nextStage: "implement" } }
  );
} catch (_) { missingMessageRejected = true; }
if (!missingMessageRejected) process.exit(8);
'''
    result = run_command(["node", "-e", script, str(workflow_script)])
    if result["returncode"] != 0:
        print(f"✗ 结构化输出处理错误: {result['stderr'] or result['stdout']}")
        return False
    print("✓ think 前缀和 JSON 围栏会被正确解析")
    print("✓ 无法解析的阶段输出会阻断流程")
    print()
    return True


def test_skill_integration():
    """测试技能集成。"""
    print("测试 6: 技能集成")
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
        ("Structured Output Normalization", test_workflow_structured_output_normalization),
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
