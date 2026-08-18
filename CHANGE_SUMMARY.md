# Code 阶段批次内迭代修改 - 变更总结

## 变更日期
2026-08-13

## 问题描述
在 Code 阶段，当一个 batch 中的任务完成实现（状态为 `implemented`）后，用户继续对话希望修改该任务的代码时，系统会拒绝重新启动该任务，导致无法在批次内进行迭代修改。

## 解决方案
允许在批次编译（batch compile）执行前重新启动已 `implemented` 的任务，支持批次内的代码迭代修改。

## 代码修改

### 文件：`hooks/task_runner.py`

**位置**：`_start_task_unlocked` 函数，约 line 723-730

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

**变更说明**：
- 新增条件判断：仅当 `compile_status != "pending"` 时才拒绝重新启动
- 当 `compile_status == "pending"` 时，允许通过后续逻辑重新启动任务
- 优化错误信息，明确指出编译状态

## 行为变更

### Before (修改前)
```
Task1.status = "implemented"
↓
用户: start --task-id T1
↓
❌ 错误: task_implementation_already_ready:T1
```

### After (修改后)
```
Task1.status = "implemented"
batchCompile.status = "pending"
↓
用户: start --task-id T1
↓
✅ 成功: 状态回退为 in_progress，创建新 run
↓
用户修改代码
↓
finish-implementation
↓
Task1.status = "implemented" (revision 递增)
```

## 安全保障

### 1. 编译后锁定
```
batchCompile.status = "passed" | "failed" | "repairing"
↓
start --task-id T1
↓
❌ 错误: task_locked_after_compile
```

### 2. 状态转换限制
- `set_task_execution_status`: 支持任意状态转换（无限制）
- `record_task_implementation`: 要求 `status == "in_progress"`（保证正确工作流）

### 3. Evidence 追踪
- `implementationEvidenceIds`: 累积所有历史 evidence
- `latestImplementationEvidenceId`: 指向最新版本
- `implementationRevision`: 版本号递增（从 1 开始）

## 影响范围

### ✅ 兼容的功能
- 单次实现流程（无变更）
- Batch compile 流程（无变更）
- Compile repair 流程（无变更）
- Evidence 存储和读取（无变更）

### ⚠️ 新增行为
- 允许 `implemented` → `in_progress` 状态回退（仅在 compile pending 时）
- 同一任务可以有多个 implementation evidence（按时间顺序）
- `implementationRevision` 可以大于 1

### ❌ 破坏性变更
- **无**

## 测试

### 新增测试文件
- `tests/test_code_stage_iterative_editing.py`
  - `test_iterative_editing_before_compile`: 批次内多次迭代
  - `test_reject_editing_after_compile_passed`: 编译通过后拒绝
  - `test_reject_editing_after_compile_failed`: 编译失败后拒绝

### 验证脚本
- `scripts/verify_iterative_editing.py`
  - ✅ 场景 1: batch compile pending 时允许
  - ✅ 场景 2: batch compile passed 时拒绝
  - ✅ 场景 3: batch compile failed 时拒绝
  - ✅ 场景 4: compile repair 不受影响

### 运行测试
```bash
# 快速验证
python scripts/verify_iterative_editing.py

# 完整测试（需要 pytest）
pytest tests/test_code_stage_iterative_editing.py -v
```

## 文档

### 新增文档
1. **技术方案**：`CODE_STAGE_ITERATIVE_EDITING.md`
   - 完整设计说明
   - 实现细节
   - 限制与约束

2. **用户指南**：`docs/code_stage_iterative_editing_guide.md`
   - 使用场景
   - 命令行示例
   - 常见问题

3. **变更总结**：本文档

## 部署检查清单

- [x] 代码修改完成
- [x] 语法检查通过
- [x] 逻辑验证通过
- [ ] 集成测试通过（需要完整环境）
- [ ] Code review
- [ ] 更新 CHANGELOG
- [ ] 用户文档发布

## 回滚计划

如果发现问题，回滚步骤：

```bash
# 1. 恢复代码
git revert <commit-hash>

# 2. 删除测试文件
rm tests/test_code_stage_iterative_editing.py
rm scripts/verify_iterative_editing.py

# 3. 删除文档
rm CODE_STAGE_ITERATIVE_EDITING.md
rm docs/code_stage_iterative_editing_guide.md
rm CHANGE_SUMMARY.md
```

## 后续工作

### 短期（本周）
- [ ] 运行完整集成测试
- [ ] 在真实项目中验证
- [ ] 收集用户反馈

### 中期（下月）
- [ ] 考虑是否需要 UI 提示（显示任务修订版本）
- [ ] 优化错误信息的用户友好性
- [ ] 添加 evidence 历史查询命令

### 长期
- [ ] 考虑是否支持跨批次修改
- [ ] 考虑是否需要 evidence 回滚机制
- [ ] 考虑是否需要编译后的有限修改（hotfix）

## 风险评估

### 低风险 ✅
- 修改范围小（仅一个条件判断）
- 不影响现有单次实现流程
- 有明确的安全边界（编译后锁定）

### 中风险 ⚠️
- 用户可能过度修改导致 evidence 历史混乱
  - **缓解措施**：文档中明确建议一次性完成实现

### 高风险 ❌
- **无**

## 联系人

- **开发者**：Claude (Kiro)
- **审核者**：待定
- **问题反馈**：提交 issue 到项目仓库

## 参考链接

- [GSD Workflow Documentation](./README.md)
- [Task Runner Architecture](./hooks/task_runner.py)
- [Plan Writer API](./hooks/plan_writer.py)
