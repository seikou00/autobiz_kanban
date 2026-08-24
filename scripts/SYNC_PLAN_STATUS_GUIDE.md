# Plan 状态同步脚本使用指南

## 概述

当你手动修改 `plan.json` 中的任务或批次状态后，使用 `sync_plan_status.py` 脚本自动同步所有相关字段，确保工作流可以继续执行。

## 功能列表

### 1. 任务级别同步
- ✅ 为 `done` 状态的任务生成缺失的 `evidenceIds`
- ✅ 同步 `completionEvidenceIds` 和 `latestPassEvidenceId`
- ✅ 同步 `implementationRevision` 和 `latestImplementationEvidenceId`
- ✅ 支持 `defer_to_test_stages` 验证策略

### 2. 批次级别同步
- ✅ 自动计算并更新 `completedTaskCount`
- ✅ 根据任务完成情况自动调整批次状态
- ✅ 为完成的批次添加 `completedAt` 时间戳

### 3. 功能级别同步
- ✅ 同步根 `plan.json` 中的批次状态投影
- ✅ 自动更新 `activeBatchId`（当前进行中的批次）
- ✅ 自动更新 `nextBatchId`（下一个待执行的批次）
- ✅ 根据批次状态调整功能整体状态

### 4. Digest 更新
- ✅ 自动重新计算并更新 `taskSetDigest`

## 使用方法

### 基本命令

```bash
# 预览变更（推荐先执行）
python scripts/sync_plan_status.py <feature_dir> --dry-run

# 实际应用变更
python scripts/sync_plan_status.py <feature_dir>
```

### 命令参数

- `feature_dir`: 功能目录路径（必需）
- `--dry-run`: 预览模式，仅显示将要做的变更，不实际写入文件（可选）

## 使用示例

### 示例 1：标记单个任务为完成

**场景**：你已经完成了 T001 任务的开发和测试，想手动标记为完成。

```bash
# 步骤 1：手动编辑批次 plan.json
# 文件：.autodev/features/feature-001/plans/B001/plan.json
# 找到 T001，将 status 从 "in_progress" 改为 "done"

# 步骤 2：预览同步结果
python scripts/sync_plan_status.py .autodev/features/feature-001 --dry-run

# 步骤 3：确认无误后执行
python scripts/sync_plan_status.py .autodev/features/feature-001
```

**脚本自动处理：**
- 为 T001 生成 `evidenceIds: ["ev_0001"]`
- 设置 `completionEvidenceIds: ["ev_0001"]`
- 设置 `latestPassEvidenceId: "ev_0001"`
- 更新批次的 `completedTaskCount`
- 重新计算并更新 `taskSetDigest`

### 示例 2：标记整个批次完成

**场景**：批次 B001 的所有任务都已完成，想一次性标记整个批次。

```bash
# 步骤 1：手动编辑
# 将批次中所有任务的 status 改为 "done"

# 步骤 2：运行同步
python scripts/sync_plan_status.py .autodev/features/feature-001
```

**脚本自动处理：**
- 同步所有任务的完成字段
- 更新批次状态为 `"done"`
- 添加 `completedAt` 时间戳（如：`"2026-08-13T10:30:00Z"`）
- 更新根 plan.json 中的批次状态投影
- 更新 `activeBatchId` 指向下一个批次
- 更新 `nextBatchId` 指向后续批次
- 重新计算 `taskSetDigest`

### 示例 3：跳过某个批次

**场景**：当前批次遇到阻塞问题，想先跳过执行下一个批次。

```bash
# 步骤 1：手动编辑根 plan.json
# - 将当前批次 B001 标记为 "done"（或 "failed"）
# - 将下一个批次 B002 标记为 "in_progress"

# 步骤 2：运行同步
python scripts/sync_plan_status.py .autodev/features/feature-001
```

**脚本自动处理：**
- 更新 `activeBatchId` 从 B001 到 B002
- 更新 `nextBatchId` 指向 B003
- 更新功能整体状态
- 重新计算 `taskSetDigest`

### 示例 4：仅重新计算 digest

**场景**：没有手动改动，但需要重新计算 digest。

```bash
# 直接运行脚本
python scripts/sync_plan_status.py .autodev/features/feature-001
```

**脚本会：**
- 检查所有字段是否需要同步
- 重新计算并更新 `taskSetDigest`（如果有变化）

## 输出示例

### 成功执行输出

```
Loading plan bundle: .autodev/features/feature-001
  ℹ Using defer_to_test_stages validation policy

📋 Syncing task fields...
  ✓ T001: {'evidenceIds': 'generated [ev_0001]', 'completionEvidenceIds': 'set to [ev_0001]'}
  ✓ T002: {'latestPassEvidenceId': 'set to ev_0002'}

📦 Syncing batch status...
  ✓ B001: {'completedTaskCount': '1 -> 2', 'status': 'in_progress -> done', 'completedAt': 'set to 2026-08-13T10:30:00Z'}

🎯 Syncing feature status...
  ✓ batches.B001.status: in_progress -> done
  ✓ activeBatchId: B001 -> B002
  ✓ nextBatchId: B002 -> B003

🔐 Recalculating taskSetDigest...
  ✓ Digest updated
    Old: abc123...
    New: def456...

💾 Writing changes to disk...
  ✓ .autodev/features/feature-001/plan.json
  ✓ .autodev/features/feature-001/plans/B001/plan.json

✅ Successfully synced and saved 4 groups of changes
```

### 预览模式输出

```
[DRY RUN] Loading plan bundle: .autodev/features/feature-001

📋 Syncing task fields...
  ✓ T001: {'evidenceIds': 'generated [ev_0001]'}

📦 Syncing batch status...
  ℹ No batch changes needed

🎯 Syncing feature status...
  ℹ No feature status changes needed

🔐 Recalculating taskSetDigest...
  ✓ Digest updated
    Old: abc123...
    New: def456...

🔍 [DRY RUN] Would apply 2 groups of changes
    Run without --dry-run to apply
```

### 无需变更输出

```
Loading plan bundle: .autodev/features/feature-001

📋 Syncing task fields...
  ℹ No task changes needed

📦 Syncing batch status...
  ℹ No batch changes needed

🎯 Syncing feature status...
  ℹ No feature status changes needed

🔐 Recalculating taskSetDigest...
  ℹ Digest already correct: abc123...

✅ No changes needed - plan is already in sync
```

## 常见场景对照表

| 场景 | 手动操作 | 脚本自动处理 |
|------|---------|------------|
| 完成一个任务 | 改 task status → `"done"` | 添加 evidence 字段、更新 batch 计数、更新 digest |
| 跳过失败任务 | 改 task status → `"failed"` | 更新 batch 计数、可能调整 batch 状态、更新 digest |
| 完成整个批次 | 所有 task → `"done"` | 批次 → `"done"`、添加时间戳、更新指针、更新 digest |
| 批次失败 | batch status → `"failed"` | 更新指针、调整功能状态、更新 digest |
| 回退批次 | 改回 `"todo"` 或 `"in_progress"` | 调整指针、更新功能状态、更新 digest |
| 标记任务进行中 | task status → `"in_progress"` | 可能调整 batch 状态、更新 digest |
| 仅更新 digest | 无需手动改动 | 只运行脚本即可重新计算 |

## 状态转换规则

### 任务状态流转
```
todo → in_progress → done
               ↓
            failed
```

### 批次状态流转
```
todo → in_progress → done
               ↓
            failed
```

### 功能状态流转
```
todo → in_progress → done
               ↓
            failed
```

## 注意事项

### 使用建议

1. **始终先用 `--dry-run` 预览**
   - 确认变更符合预期
   - 避免意外修改

2. **备份重要数据**
   - 虽然脚本会验证 plan 完整性
   - 但重要变更前建议备份

3. **理解状态转换规则**
   - 遵循上述状态流转图
   - 避免不合理的状态跳转

4. **digest 总是会重新计算**
   - 无论是否有其他变更
   - 确保流程一致性

### 字段说明

**evidenceIds**：记录所有相关 evidence 的 ID 列表

**completionEvidenceIds**：标记任务完成的 evidence ID 列表

**latestPassEvidenceId**：最近一次通过验证的 evidence ID

**implementationRevision**：实现版本号，与 implementationEvidenceIds 数组长度一致

**completedTaskCount**：批次中已完成的任务数量

**activeBatchId**：当前正在执行的批次 ID

**nextBatchId**：下一个待执行的批次 ID

**taskSetDigest**：任务集的 SHA256 哈希值，用于检测变更

## 错误处理

### 常见错误及解决方法

#### 错误 1：plan.json 格式错误
```
✗ Plan validation error: invalid_plan_json:...
```
**解决**：检查 JSON 语法，确保格式正确（逗号、引号、括号等）

#### 错误 2：缺少必需字段
```
✗ Plan validation error: plan_json_missing_feature_id
```
**解决**：确保 plan.json 包含所有必需字段（featureId、batches 等）

#### 错误 3：引用了不存在的 ID
```
✗ Plan validation error: T001.dependency_unknown:T999
```
**解决**：检查任务依赖关系，确保引用的 ID 存在

#### 错误 4：目录不存在
```
✗ Directory not found: .autodev/features/feature-001
```
**解决**：检查路径是否正确，目录是否存在

### 调试技巧

1. **使用 --dry-run 模式**
   ```bash
   python scripts/sync_plan_status.py <feature_dir> --dry-run
   ```

2. **查看详细错误信息**
   - 脚本会输出完整的错误堆栈
   - 根据错误信息定位问题

3. **验证 plan.json 完整性**
   ```bash
   python hooks/plan_json.py validate <feature_dir>/plan.json
   ```

## 工作流集成

### 推荐工作流

```bash
# 1. 查看当前状态
cat .autodev/features/feature-001/plan.json | jq '.status, .activeBatchId'

# 2. 手动修改状态
vim .autodev/features/feature-001/plans/B001/plan.json

# 3. 预览同步结果
python scripts/sync_plan_status.py .autodev/features/feature-001 --dry-run

# 4. 应用变更
python scripts/sync_plan_status.py .autodev/features/feature-001

# 5. 验证结果
cat .autodev/features/feature-001/plan.json | jq '.taskSetDigest'
```

### Git 提交建议

```bash
# 同步后提交变更
git add .autodev/features/feature-001/
git commit -m "chore: sync plan status after manual task completion"
```

## 技术细节

### digest 计算规则

`taskSetDigest` 基于以下内容计算 SHA256：
- 所有批次的元数据（id、title、executionLane 等）
- 所有任务的契约（contract）字段（排除运行时字段）
- batchCompile 状态（如果启用）
- taskValidationPolicy 配置

### defer_to_test_stages 策略

当 plan 启用 `defer_to_test_stages` 验证策略时：
- 任务 `done` 状态不强制要求 `completionEvidenceIds`
- 依赖 `batchCompile.status == "passed"` 作为完成证据
- 脚本会自动识别并适配此策略

## 更新日志

### v1.0.0 (2026-08-13)
- ✅ 初始版本发布
- ✅ 支持任务、批次、功能三级状态同步
- ✅ 自动重新计算 taskSetDigest
- ✅ 支持 --dry-run 预览模式
- ✅ 支持 defer_to_test_stages 策略

---

**脚本位置**：`scripts/sync_plan_status.py`  
**维护者**：Autodev Team  
**最后更新**：2026-08-13
