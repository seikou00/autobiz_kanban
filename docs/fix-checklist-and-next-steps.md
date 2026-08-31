# 乐观并行执行 - 修复清单与后续工作

## 已修复的问题

### ✅ P0-1: 配置传递到实际执行
**状态**: 已修复

**修改内容**:
1. `hooks/workflow_launcher.py`: 加载 runtime_config 并传入 workflowArgs
2. `hooks/parallel_runtime.py`: create_manifest 接受并保存 runtimeConfig 到 manifest
3. `hooks/parallel_batch_scheduler.py`: 在 create_run 时加载并传递 runtime_config

**验证方法**:
```bash
# 查看 manifest 是否包含 runtimeConfig
cat .autobizdevops/features/*/parallel-runs/*/manifest.json | jq '.runtimeConfig'
```

### ✅ P0-2: 冲突处理工作流分支
**状态**: 已提供实施指南

**文档**: `docs/workflow-conflict-handling-guide.md`

**关键改动**:
- 移除 `requireSuccess()` 对 build-candidate 的包装
- 检查 status 字段并分支处理 candidate_conflicted
- 保留 worktree 并通知人工介入
- 允许其他无依赖波次继续执行

**待实施**: 需要修改 `workflows/code-batched-execution.workflow.js`

### ✅ P0-3: 冲突恢复 CLI
**状态**: 已实现

**新文件**: `hooks/candidate_manager.py`

**提供的命令**:
```bash
# 列出所有冲突的候选
python hooks/candidate_manager.py list --feature myfeature

# 恢复已解决的候选
python hooks/candidate_manager.py resume \
  --feature myfeature --run-id cw-20260830-001 \
  --wave 1 --repository-ref backend

# 放弃候选
python hooks/candidate_manager.py discard \
  --feature myfeature --run-id cw-20260830-001 \
  --wave 1 --repository-ref backend
```

**状态转换**:
- candidate_conflicted → built (resume 后)
- candidate_conflicted → discarded (discard 后)
- needs_resolution → built (resume 后)

### ✅ P1-1: 禁用 append-only 自动提交
**状态**: 已修复

**修改内容**:
- `ConflictResolutionAgent` 和 `ModelBasedResolver` 增加 `enable_auto_commit` 参数
- 默认值为 `False`
- MVP 阶段只分析冲突类型，不自动提交
- 所有冲突都需要人工/Agent 介入

**配置示例**:
```json
{
  "conflictResolution": {
    "enableAutoResolve": false,
    "maxAttempts": 2
  }
}
```

---

## 待修复的问题

### ⚠️ P1-2: 预览写集派生逻辑
**状态**: 待修复

**问题**: 
- `workflow_launcher.py` 的 `_batch_execution_plan` 直接读取 `batch.writeSet`
- 实际 manifest 通过 `batch_write_set()` 从 task.scope.paths 和 expectedFiles 派生
- 导致预览中的写集重叠提示可能不准确

**解决方案**:
1. 在 `parallel_runtime.py` 中找到 `batch_write_set()` 函数
2. 在 `workflow_launcher.py` 中导入并使用该函数
3. 确保预览和实际执行使用相同的写集派生逻辑

**代码位置**:
```python
# hooks/workflow_launcher.py, line ~360
preview_manifest = {
    "batches": {
        batch_id: {
            # 当前: 直接使用 batch.get("writeSet", [])
            # 应改为: 调用 batch_write_set(batch) 派生
            "writeSet": batch.get("writeSet", []),  # 待修复
        }
        for batch_id, batch in by_id.items()
    }
}
```

### ⚠️ P1-3: 端到端测试
**状态**: 待实现

**需要的测试**:
1. **乐观并发 + 无冲突推广**
   - 创建两个 Batch 修改不同文件
   - 启用 optimistic 模式
   - 验证它们在同一 wave 中执行
   - 验证成功推广到 main

2. **冲突保留 worktree**
   - 创建两个 Batch 修改同一文件同一行
   - 验证 build-candidate 返回 candidate_conflicted
   - 验证 worktree 被保留
   - 验证冲突文件包含 <<<< ==== >>>> 标记

3. **解决后验证推广**
   - 手动解决冲突
   - 调用 `candidate_manager.py resume`
   - 验证状态变为 built
   - 验证可以继续 B-INT 和推广

4. **重启后恢复**
   - 在冲突状态时中断 workflow
   - 重新启动
   - 验证冲突状态仍然保留
   - 验证可以恢复

**实施位置**: `tests/integration/test_optimistic_e2e.py`

### ⚠️ P1-4: 修复单元测试断言
**状态**: 待修复

**问题**: `test_critical_phase_still_serial` 的断言不正确

**当前代码**:
```python
groups = resource_groups(manifest, ["B001", "B002"])
assert len(groups) == 1  # 错误
assert groups[0] == ["B001"]
```

**实际行为**: 
- 函数会为每个 proto/global/integration Batch 返回单独的组
- 应该返回 `[['B001'], ['B002']]`

**修复**:
```python
groups = resource_groups(manifest, ["B001", "B002"])
assert len(groups) == 2  # 正确：两个单 Batch 组
assert len(groups[0]) == 1
assert len(groups[1]) == 1
# 或者只取第一个
assert groups[0] == ["B001"]
```

---

## 实施优先级

### 立即需要（可灰度前）

1. **P0-2: 修改 workflow.js 处理冲突** (2小时)
   - 按照 `workflow-conflict-handling-guide.md` 实施
   - 测试无冲突和有冲突场景

2. **P1-4: 修复测试断言** (15分钟)
   - 修正 `test_critical_phase_still_serial`
   - 运行 `pytest tests/test_optimistic_parallel.py -v`

3. **基本端到端测试** (4小时)
   - 创建临时 Git 仓库
   - 测试无冲突场景
   - 测试冲突保留和恢复

### 可以后续（灰度后优化）

4. **P1-2: 预览写集派生** (2小时)
   - 复用 `batch_write_set()` 函数
   - 确保预览准确性

5. **完整端到端测试** (8小时)
   - 覆盖所有场景
   - 包括重启恢复

6. **性能监控** (4小时)
   - 添加冲突率跟踪
   - 时间节省计算
   - 自动解决成功率

---

## 当前状态总结

### 可以工作的部分 ✅
- ✅ Runtime config 传递到 manifest
- ✅ resource_groups 读取并应用配置
- ✅ 冲突检测并保留 worktree
- ✅ 冲突恢复 CLI (resume/discard/list)
- ✅ 自动提交已禁用（安全）

### 需要人工介入的部分 ⚠️
- ⚠️ workflow.js 需要修改（约2小时工作）
- ⚠️ 需要基本的端到端测试验证
- ⚠️ 测试断言需要修正

### 灰度建议

**不建议立即灰度**，原因：
1. Workflow 冲突处理逻辑尚未实施
2. 缺少真实场景的端到端测试
3. 配置虽然传递，但 workflow 可能仍使用 requireSuccess

**建议灰度前完成**:
1. 修改 workflow.js 按指南处理冲突
2. 至少通过一个真实的无冲突场景测试
3. 至少通过一个冲突场景并手动恢复的测试

**最小可测试版本 (2-3 天工作)**:
- 完成 workflow.js 修改
- 修复测试断言
- 创建一个简单的集成测试
- 在开发环境验证完整流程

---

## 验证检查清单

### 配置传递验证
```bash
# 1. 创建配置
mkdir -p .autobiz
cat > .autobiz/runtime_config.json << 'EOF'
{
  "parallelSchedulingMode": "optimistic",
  "maxParallel": 6
}
EOF

# 2. 运行预览
python hooks/workflow_launcher.py analyze --feature test

# 3. 检查预览输出中是否显示 optimistic

# 4. 实际运行后检查 manifest
cat .autobizdevops/features/*/parallel-runs/*/manifest.json | jq '.runtimeConfig'
```

### 冲突处理验证
```bash
# 1. 列出冲突
python hooks/candidate_manager.py list --feature test

# 2. 恢复候选
python hooks/candidate_manager.py resume \
  --feature test --run-id <run-id> \
  --wave 1 --repository-ref backend

# 3. 验证状态变化
cat manifest.json | jq '.mergeTrains'
```

### 单元测试验证
```bash
# 运行所有测试
pytest tests/test_optimistic_parallel.py -v
pytest tests/test_conflict_resolution.py -v

# 应该全部通过（修复 test_critical_phase_still_serial 后）
```

---

## 后续增强路线图

### Phase 1: MVP 稳定 (当前)
- [x] 配置传递
- [x] 冲突检测
- [x] 恢复 CLI
- [ ] Workflow 集成
- [ ] 基本测试

### Phase 2: 自动化增强 (1-2周)
- [ ] 集成 AI Agent 解决简单冲突
- [ ] 追加式冲突自动提交（可选启用）
- [ ] 冲突类型统计和学习

### Phase 3: 性能优化 (1个月)
- [ ] 并行度自适应调整
- [ ] 冲突率预测
- [ ] 智能降级策略

### Phase 4: 高级功能 (2-3个月)
- [ ] 语义冲突检测
- [ ] 分布式 Merge Train
- [ ] 多候选并行验证
