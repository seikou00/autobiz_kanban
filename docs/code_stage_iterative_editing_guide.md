# Code 阶段批次内迭代修改 - 用户指南

## 功能概述

在 Code 阶段，你现在可以在批次编译前多次修改同一个任务的代码。

## 使用场景

### ✅ 允许的场景

**批次内继续对话修改**：

```
你: 实现 Task1 的用户登录功能
AI: [完成实现]
你: finish-implementation --task-id T1 --run-id run-001
结果: Task1 状态 = implemented

你: 我想把密码加密方式改成 bcrypt
AI: [重新启动 Task1]
系统: start --task-id T1  ✅ 允许（batch compile 尚未执行）
AI: [修改代码]
你: finish-implementation --task-id T1 --run-id run-002
结果: Task1 状态 = implemented (版本 2)
```

### ❌ 不允许的场景

**批次编译后禁止修改**：

```
你: batch-compile --batch-id B1
结果: 编译通过，所有任务标记为 done

你: 我想修改 Task1 的实现
系统: start --task-id T1  ❌ 拒绝
错误: task_locked_after_compile
原因: 批次已编译通过，代码快照已锁定
```

**编译失败后必须走修复流程**：

```
你: batch-compile --batch-id B1
结果: 编译失败

你: 我想修改 Task1 修复编译错误
系统: start --task-id T1  ❌ 拒绝
错误: batch_compile_repair_requires_explicit_start
正确方式: start-batch-compile-repair --batch-id B1 --task-id T1
```

## 工作原理

### 状态转换

```
[第一次实现]
start → in_progress → finish → implemented (rev 1)

[用户继续对话]
start → in_progress (状态回退) → finish → implemented (rev 2)

[batch compile]
batch-compile → done (状态锁定，禁止再修改)
```

### Evidence 追踪

每次 `finish-implementation` 都会创建新的 evidence：

```json
{
  "implementationEvidenceIds": ["E1", "E2", "E3"],  // 所有历史
  "latestImplementationEvidenceId": "E3",           // 最新版本
  "implementationRevision": 3                        // 版本号
}
```

批次编译使用 `latestImplementationEvidenceId` 作为基线。

## 命令行示例

### 正常迭代流程

```bash
# 第一次实现
python hooks/task_runner.py start \
  --task-id T1 \
  --code-workspace /path/to/repo
# → runId: run-20260813T120000Z-abc123

# 修改代码...

python hooks/task_runner.py finish-implementation \
  --task-id T1 \
  --run-id run-20260813T120000Z-abc123
# → status: implemented, revision: 1

# 用户继续对话："改一下这个函数"
python hooks/task_runner.py start \
  --task-id T1 \
  --code-workspace /path/to/repo
# → runId: run-20260813T123000Z-def456

# 再次修改代码...

python hooks/task_runner.py finish-implementation \
  --task-id T1 \
  --run-id run-20260813T123000Z-def456
# → status: implemented, revision: 2

# 批次完成后编译
python hooks/task_runner.py batch-compile \
  --batch-id B1 \
  --code-workspace /path/to/repo
# → compileStatus: passed
```

### 错误处理

```bash
# 尝试在编译后修改
python hooks/task_runner.py start \
  --task-id T1 \
  --code-workspace /path/to/repo

# 输出错误：
{
  "ok": false,
  "error": "task_implementation_already_ready:T1",
  "requiredAction": "task_locked_after_compile",
  "batchCompileStatus": "passed"
}
```

## 注意事项

### 1. Git 工作区清洁

每次 `start` 都会重新捕获 git 快照，确保工作区干净：

```bash
# 提交或暂存你的修改
git add .
git commit -m "修改 Task1 实现"

# 然后可以安全调用
python hooks/task_runner.py finish-implementation ...
```

### 2. 编译是单向门

一旦执行 `batch-compile`：
- ✅ 编译通过 → 所有任务锁定为 `done`
- ❌ 编译失败 → 必须用 `start-batch-compile-repair`

无法撤销编译状态回到 `pending`。

### 3. 跨批次修改

如果需要修改已在另一个批次的任务，需要重新规划：

```bash
# Task1 在 Batch1 已完成
# 想修改 Task1 → 需要创建新批次或新计划
```

## 常见问题

**Q: 我可以修改多少次？**  
A: 在 `batch-compile` 前不限次数。

**Q: 历史 evidence 会被删除吗？**  
A: 不会，所有 evidence 都保留在 `implementationEvidenceIds` 中。

**Q: 如果编译失败，我能否直接重新启动任务？**  
A: 不能，必须使用 `start-batch-compile-repair` 启动受控修复。

**Q: 修改后需要重新运行 code-context 吗？**  
A: 仅在需要重新读取任务契约或规格引用时运行；`start` 不依赖预先扫描产物。

## 集成到对话流程

当用户在批次内继续对话时：

```python
# 伪代码
if user_wants_to_modify_task and task.status == "implemented":
    compile_status = batch.batchCompile.status
    if compile_status == "pending":
        # ✅ 允许修改
        runner.start(task_id)
        # ... 修改代码
        runner.finish_implementation(task_id, run_id)
    elif compile_status in ["passed", "failed", "repairing"]:
        # ❌ 拒绝，提示用户
        print("批次已编译，无法直接修改。")
```

## 下一步

- 查看完整技术方案：`CODE_STAGE_ITERATIVE_EDITING.md`
- 运行测试：`pytest tests/test_code_stage_iterative_editing.py`
- 查看代码修改：`git diff hooks/task_runner.py`
