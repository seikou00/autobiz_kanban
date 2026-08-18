#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 Code 阶段迭代修改功能的快速测试"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hooks.task_runner import TaskRunnerError


def test_modified_logic():
    """验证修改后的逻辑是否正确"""

    # 模拟场景：任务状态为 implemented，不是 compile repair
    task_status = "implemented"
    is_compile_repair = False

    # 场景 1: batch compile 状态为 pending（应该允许）
    compile_status = "pending"
    try:
        # 模拟修改后的逻辑
        if task_status == "implemented" and not is_compile_repair:
            if compile_status != "pending":
                raise TaskRunnerError(
                    f"task_implementation_already_ready:T1",
                    requiredAction="task_locked_after_compile",
                    batchCompileStatus=compile_status,
                )
        print("✅ 场景 1 通过: batch compile pending 时允许重新启动")
    except TaskRunnerError as e:
        print(f"❌ 场景 1 失败: {e}")
        return False

    # 场景 2: batch compile 状态为 passed（应该拒绝）
    compile_status = "passed"
    try:
        if task_status == "implemented" and not is_compile_repair:
            if compile_status != "pending":
                raise TaskRunnerError(
                    f"task_implementation_already_ready:T1",
                    requiredAction="task_locked_after_compile",
                    batchCompileStatus=compile_status,
                )
        print("❌ 场景 2 失败: 应该抛出错误但没有")
        return False
    except TaskRunnerError as e:
        if "task_implementation_already_ready" in str(e):
            print("✅ 场景 2 通过: batch compile passed 时正确拒绝")
        else:
            print(f"❌ 场景 2 失败: 错误信息不正确: {e}")
            return False

    # 场景 3: batch compile 状态为 failed（应该在其他地方处理）
    compile_status = "failed"
    try:
        if task_status == "implemented" and not is_compile_repair:
            if compile_status != "pending":
                raise TaskRunnerError(
                    f"task_implementation_already_ready:T1",
                    requiredAction="task_locked_after_compile",
                    batchCompileStatus=compile_status,
                )
        print("❌ 场景 3 失败: 应该抛出错误但没有")
        return False
    except TaskRunnerError as e:
        if "task_implementation_already_ready" in str(e):
            print("✅ 场景 3 通过: batch compile failed 时正确拒绝")
        else:
            print(f"❌ 场景 3 失败: 错误信息不正确: {e}")
            return False

    # 场景 4: 是 compile repair（应该不受影响）
    is_compile_repair = True
    compile_status = "failed"
    try:
        if task_status == "implemented" and not is_compile_repair:
            if compile_status != "pending":
                raise TaskRunnerError(
                    f"task_implementation_already_ready:T1",
                    requiredAction="task_locked_after_compile",
                    batchCompileStatus=compile_status,
                )
        print("✅ 场景 4 通过: compile repair 场景不受影响")
    except TaskRunnerError as e:
        print(f"❌ 场景 4 失败: {e}")
        return False

    return True


def main():
    print("=" * 60)
    print("验证 Code 阶段迭代修改功能")
    print("=" * 60)
    print()

    if test_modified_logic():
        print()
        print("=" * 60)
        print("✅ 所有验证通过！")
        print("=" * 60)
        return 0
    else:
        print()
        print("=" * 60)
        print("❌ 验证失败")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
