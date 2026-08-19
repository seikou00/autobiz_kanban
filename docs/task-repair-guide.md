# 任务修复指南

## 概述

当在 Code 阶段发现某个已完成的任务存在问题时，可以使用任务修复功能来修正实现，同时保留完整的证据链。

## 适用场景

- ✅ 任务状态为 `implemented` 或 `done`
- ✅ 发现实现有 bug 或不符合预期
- ✅ 需要保留修复历史的引用链
- ✅ 需要重新验证批次编译

## 在 Claw 对话中触发修复

### 方式一：直接说明需要修复的任务

```
用户: "我发现 T001 的实现有问题，登录验证码校验不正确，需要修复"
```

Claude 会自动：
1. 查看 T001 的当前状态和 evidence ID
2. 启动任务修复流程
3. 根据你的描述修改代码
4. 完成修复并记录新的 evidence
5. 如果需要，重新验证批次编译

### 方式二：明确请求修复流程

```
用户: "帮我修复 T002，它的实现不对"
```

Claude 会询问具体问题，然后执行完整的修复流程。

## 命令行手动执行

如果需要手动控制修复流程：

### 步骤 1：查看任务信息

```bash
# 查看 PLAN.md 或使用 inspect 命令
python hooks/task_runner.py inspect \
  --feature <FEATURE> \
  --task-id T001 \
  --code-workspace <BUSINESS_REPO>
```

从输出中获取 `latestImplementationEvidenceId`。

### 步骤 2：启动任务修复

```bash
python hooks/task_runner.py start-task-repair \
  --feature <FEATURE> \
  --task-id T001 \
  --prior-evidence-id ev-20240818-abc123 \
  --code-workspace <BUSINESS_REPO>
```

命令会返回：
```json
{
  "ok": true,
  "runId": "run-20240818-xyz789",
  "priorEvidenceId": "ev-20240818-abc123",
  "taskId": "T001"
}
```

### 步骤 3：修改代码

在你的代码仓库中修复问题。

### 步骤 4：完成修复

```bash
python hooks/task_runner.py finish-implementation \
  --feature <FEATURE> \
  --task-id T001 \
  --run-id run-20240818-xyz789 \
  --code-workspace <BUSINESS_REPO> \
  --repair-mode
```

返回新的 evidence ID：
```json
{
  "ok": true,
  "implementationEvidenceId": "ev-20240818-def456",
  "status": "implemented"
}
```

注意：如果原任务状态是 `done`，会自动恢复为 `done`（见下方"状态保持"特性）。

### 步骤 5：（可选）重新验证批次编译

如果这个任务属于某个批次，需要重新验证：

```bash
python hooks/task_runner.py revalidate-batch-compile \
  --feature <FEATURE> \
  --batch-id B01 \
  --code-workspace <BUSINESS_REPO>
```

## 核心特性

### ✅ 状态保持

- 如果原任务状态是 `done`，修复后会自动恢复为 `done`
- 如果原任务状态是 `implemented`，修复后保持 `implemented`

### ✅ 证据链

每次修复都会创建新的 evidence，通过 `priorEvidenceId` 链接到上一个版本：

```
ev-001 (初始实现)
  ↓ priorEvidenceId
ev-002 (第一次修复)
  ↓ priorEvidenceId
ev-003 (第二次修复)
```

### ✅ 批次重验

修复任务后，可以重新验证整个批次的编译状态，确保所有任务的最新实现都能通过编译。

## 错误处理

### 任务状态不正确

```
Error: task_repair_requires_implemented_or_done_status:T001
```

解决方案：只能修复状态为 `implemented` 或 `done` 的任务。

### Evidence ID 不匹配

```
Error: prior_evidence_mismatch
```

解决方案：检查提供的 `prior-evidence-id` 是否是该任务的最新 evidence ID。

### 批次编译失败

```json
{
  "compileStatus": "failed",
  "errors": ["..."]
}
```

解决方案：查看编译错误信息，继续修复相关任务。

## 最佳实践

1. **修复前先检查**：确认任务的最新 evidence ID，避免基于旧版本修复
2. **小步修复**：每次只修复一个明确的问题，而不是多个问题一起改
3. **验证批次**：修复后及时验证批次编译，确保没有引入新问题
4. **查看证据链**：通过 evidence 的 `priorEvidenceId` 可以追溯完整的修复历史

## 示例场景

### 场景 1：修复单个任务的 bug

```bash
# 1. 发现 T001 有 bug
# 2. 启动修复
python hooks/task_runner.py start-task-repair \
  --feature login \
  --task-id T001 \
  --prior-evidence-id ev-20240818-001 \
  --code-workspace ./business-repo

# 3. 修改代码
# ... 修复 bug ...

# 4. 完成修复
python hooks/task_runner.py finish-implementation \
  --feature login \
  --task-id T001 \
  --run-id run-20240818-001 \
  --code-workspace ./business-repo \
  --repair-mode
```

### 场景 2：修复后重新验证批次

```bash
# 1. 修复 T001（同上）

# 2. 重新验证批次 B01
python hooks/task_runner.py revalidate-batch-compile \
  --feature login \
  --batch-id B01 \
  --code-workspace ./business-repo

# 3. 如果还有失败，继续修复其他任务
```

### 场景 3：在 Claw 对话中快速修复

```
用户: "T003 的密码加密算法用错了，应该用 bcrypt 而不是 md5"

Claude:
好的，我来修复 T003：
1. 检查当前实现...
2. 启动任务修复（prior-evidence-id: ev-20240818-003）
3. 将密码加密从 md5 改为 bcrypt...
4. 完成修复，新的 evidence ID: ev-20240818-004
5. 重新验证批次 B02... ✓ 编译通过

修复完成！
```

## 相关文档

- [Code 阶段技能文档](../skills/autodev/autodev-code/SKILL.md)
- [计划系统设计](../hooks/plan_writer.py) - 查看 `update_task_evidence_only` 函数
- [证据存储实现](../hooks/evidence_store.py) - 查看 `priorEvidenceId` 字段
