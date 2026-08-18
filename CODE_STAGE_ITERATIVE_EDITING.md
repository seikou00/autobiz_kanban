# Code 阶段批次内迭代修改方案

## 问题背景

在 Code 阶段，一个 batch 中的任务完成实现后（状态为 `implemented`），用户可能继续对话并希望修改该任务的代码。原有设计不允许重新启动已 `implemented` 的任务，导致用户无法在批次内迭代。

## 解决方案

### 核心设计

**允许在 batch compile 执行前重新启动已完成的任务**：

- **兼容条件**：`batchCompile.status == "pending"`（批次编译尚未执行）
- **阻止条件**：`batchCompile.status in {"passed", "failed", "repairing"}`（编译已运行）

### 实现流程

```
用户完成 Task1 实现
  ↓
Task1.status = "implemented"
Task1.implementationRevision = 1
Task1.latestImplementationEvidenceId = "E1"
  ↓
用户继续对话："我想修改 Task1"
  ↓
调用 start --task-id T1
  ↓ (检查 batchCompile.status == "pending")
Task1.status = "in_progress" (回退)
创建新的 run-xxx 会话
  ↓
用户修改代码
  ↓
调用 finish-implementation --task-id T1 --run-id run-xxx
  ↓
Task1.implementationEvidenceIds = ["E1", "E2"] (追加)
Task1.latestImplementationEvidenceId = "E2" (更新)
Task1.implementationRevision = 2 (递增)
Task1.status = "implemented"
```

### 代码修改

#### 1. `task_runner.py` - `_start_task_unlocked` (line 723-730)

**修改前**：
```python
if normalize_status(task.get("status")) == "implemented" and not is_compile_repair:
    raise TaskRunnerError(
        f"task_implementation_already_ready:{task_id}",
        requiredAction="run_batch_compile",
    )
```

**修改后**：
```python
if normalize_status(task.get("status")) == "implemented" and not is_compile_repair:
    # 允许在 batch compile pending 时用户继续对话修改代码
    if compile_status != "pending":
        raise TaskRunnerError(
            f"task_implementation_already_ready:{task_id}",
            requiredAction="run_batch_compile" if compile_status == "pending" else "task_locked_after_compile",
            batchCompileStatus=compile_status,
        )
```

## 关键保障

### 1. 状态转换安全性

- `set_task_execution_status` 支持任意状态转换（无限制）
- `record_task_implementation` 要求 `status == "in_progress"`（保证正确的工作流）

### 2. Evidence 追踪

- `implementationEvidenceIds`: 追加所有 evidence（历史完整）
- `latestImplementationEvidenceId`: 指向最新（用于编译基线）
- `implementationRevision`: 版本号递增（审计追踪）

### 3. 编译基线锁定

**Batch compile 执行后不允许重新启动**：
- `passed`: 批次已通过，禁止修改
- `failed`: 必须通过 `start-batch-compile-repair` 启动受控修复
- `repairing`: 修复任务运行中，禁止其他任务重启

## 使用示例

### 场景 1：批次内正常迭代

```bash
# 第一次实现
python hooks/task_runner.py start --task-id T1 --code-workspace /path
python hooks/task_runner.py finish-implementation --task-id T1 --run-id run-001

# 用户继续对话："我需要修改这个函数"
python hooks/task_runner.py start --task-id T1 --code-workspace /path  # ✅ 允许
python hooks/task_runner.py finish-implementation --task-id T1 --run-id run-002

# 所有任务完成后
python hooks/task_runner.py batch-compile --batch-id B1 --code-workspace /path
```

### 场景 2：编译后禁止修改

```bash
# 批次编译已执行
python hooks/task_runner.py batch-compile --batch-id B1 --code-workspace /path
# → batchCompile.status = "passed"

# 尝试重新启动任务
python hooks/task_runner.py start --task-id T1 --code-workspace /path
# ❌ 错误：task_implementation_already_ready:T1
#    requiredAction: task_locked_after_compile
#    batchCompileStatus: passed
```

### 场景 3：编译失败后的修复

```bash
# 批次编译失败
python hooks/task_runner.py batch-compile --batch-id B1 --code-workspace /path
# → batchCompile.status = "failed"

# 尝试直接重启任务
python hooks/task_runner.py start --task-id T1 --code-workspace /path
# ❌ 错误：batch_compile_repair_requires_explicit_start:T1

# 正确方式：启动受控修复
python hooks/task_runner.py start-batch-compile-repair --batch-id B1 --task-id T1 --code-workspace /path
```

## 测试要点

1. ✅ **批次内迭代**：多次 start → finish-implementation 循环
2. ✅ **Evidence 累积**：验证 `implementationEvidenceIds` 正确追加
3. ✅ **版本递增**：验证 `implementationRevision` 正确递增
4. ❌ **编译后锁定**：验证 `batchCompile.status != "pending"` 时拒绝 start
5. ✅ **修复隔离**：验证 compile repair 流程不受影响

## 兼容性

- **向后兼容**：不影响现有单次实现流程
- **修复流程兼容**：`start-batch-compile-repair` 逻辑不变
- **编译门禁兼容**：批次编译仍基于 `latestImplementationEvidenceId`

## 限制与约束

1. **仅在批次内**：跨批次修改仍需重新规划
2. **编译前窗口**：仅在 `batchCompile.status == "pending"` 时允许
3. **无回滚机制**：每次修改都会创建新 evidence，无法删除历史记录
4. **Git 快照一致性**：每次 start 都会重新捕获 git 快照，用户需保证工作区干净

## 实现状态

- [x] 修改 `task_runner.py::_start_task_unlocked` 逻辑
- [ ] 添加集成测试（批次内多次迭代）
- [ ] 更新用户文档
- [ ] 添加错误场景测试（编译后禁止修改）
