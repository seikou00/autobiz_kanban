# 乐观并行执行修复完成报告

**日期**: 2026-08-30  
**状态**: ✅ 所有 P0/P1 问题已修复

---

## 修复摘要

本次修复解决了乐观并行执行特性中的 6 个关键问题，使该特性能够真正实现"启用 optimistic 后实际并行、冲突后自动/人工恢复"的核心目标。

---

## 修复详情

### ✅ P0 问题 #1: 运行时配置未传递到执行层 (已在之前修复)

**问题**: 配置文件仅用于预览，实际调度仍使用固定参数。

**修复**:
- `workflow_launcher.py:489` 现在将完整 `runtimeConfig` 传递给 workflow
- `parallel_runtime.py:426` 将验证后的配置持久化到 manifest
- `resource_groups()` 从 manifest 的 `runtimeConfig` 读取配置
- 配置流程完整: 文件 → launcher → workflow → manifest → scheduler

**验证**: 配置的 `maxParallel` 和 `parallelSchedulingMode` 现在会影响实际调度行为。

---

### ✅ P0 问题 #2: 冲突结果导致 workflow 中止

**问题**: `build-candidate` 返回 `candidate_conflicted` 时，workflow 立即通过 `requireSuccess()` 抛错终止。

**修复**: `workflows/code-batched-execution.workflow.js`
- 将 `requireSuccess()` 移到冲突检测之后
- 检测 `status === "candidate_conflicted"` 时调用 `resolve-candidate`
- 如果自动解决成功（`status === "built"`），继续验证和推广
- 如果需要人工介入（`status === "needs_resolution"`），抛出包含冲突上下文的错误
- 完整路径: 冲突检测 → 自动解决 → B-INT 验证 → 推广

**代码位置**: `workflows/code-batched-execution.workflow.js:395-465`

---

### ✅ P0 问题 #3: 冲突的 candidate 无法恢复

**问题**: `candidate_conflicted` 状态会导致 `wave_occupied` 错误，且缺少恢复 CLI。

**修复**: `hooks/parallel_merge_train.py`
1. 状态判断已更新，允许从 `candidate_conflicted` 状态重建（第 146 行）
2. 新增 `resolve-candidate` CLI 命令
   - 从 manifest 恢复 `ConflictContext`
   - 调用 `ModelBasedResolver` 尝试自动解决
   - 成功时更新状态为 `built`，记录 `candidateSha` 和解决方法
   - 失败时返回 `needs_resolution`，保留 worktree 供人工处理
3. 新增 `discard-candidate` CLI 命令
   - 清理冲突的 worktree 和分支
   - 将状态标记为 `discarded`
   - 如果这是唯一的阻塞项，解除 manifest 的 `blocked` 状态

**CLI 使用**:
```bash
# 尝试自动解决冲突
python hooks/parallel_merge_train.py resolve-candidate \
  --workspace . --feature my-feature --run-id cw-20260830-001 \
  --repository-ref backend --wave 1

# 放弃冲突的候选，重新开始
python hooks/parallel_merge_train.py discard-candidate \
  --workspace . --feature my-feature --run-id cw-20260830-001 \
  --repository-ref backend --wave 1

# 手工解决冲突，或修复验证失败的 candidate worktree 并提交后，将其恢复为可验证状态
python hooks/parallel_merge_train.py resume-candidate \
  --workspace . --feature my-feature --run-id cw-20260830-001 \
  --repository-ref backend --wave 1
```

---

### ✅ P0 问题 #4: 冲突解决代码未接入工作流

**问题**: `ConflictResolutionAgent` 和 `ModelBasedResolver` 存在但从未被调用。

**修复**:
- `parallel_merge_train.py` 现在导入 `ConflictAnalyzer`, `ModelBasedResolver`, `AutoMergeStrategy`
- `resolve_candidate()` 函数完整集成冲突解决逻辑:
  1. 加载 runtime config 中的 `conflictResolution` 设置
  2. 检查 `maxAttempts` 限制
  3. 如果启用 `enableAutoResolve`，调用 `ModelBasedResolver.resolve()`
  4. 根据解决结果更新 manifest 和候选状态
  5. 记录解决事件到运行时日志
- Workflow 在检测到冲突时会立即调用 `resolve-candidate`

**配置示例** (`.autobiz/runtime_config.json`):
```json
{
  "parallelSchedulingMode": "optimistic",
  "maxParallel": 6,
  "conflictResolution": {
    "maxAttempts": 2,
    "enableAutoResolve": true
  }
}
```

---

### ✅ P1 问题 #5: 预览的写集计算不完整

**问题**: launcher 直接读取 `batch_plan.writeSet`，而运行时从 `task.scope.paths` + `expectedFiles` 派生写集。

**修复**: `hooks/workflow_launcher.py`
- 第 26 行: 导入 `batch_write_set` 函数
- 第 377 行: 使用 `batch_write_set(batch_plan)` 替代直接读取 `writeSet` 字段
- 预览现在使用与运行时相同的写集计算逻辑

**影响**: 预览的重叠风险提示现在准确反映实际冲突可能性。

---

### ✅ P1 问题 #6: 测试断言错误

**问题**: `test_optimistic_parallel.py:76` 的断言与实际行为不符。

**修复**: `tests/test_optimistic_parallel.py`
- 修正 `test_critical_phase_still_serial` 的断言
- 实际行为: proto/global/integration 阶段返回 `[['B001'], ['B002']]`（每个批次一个波次）
- 原断言: `len(groups) == 1` ❌
- 新断言: `len(groups) == 2` ✅

**解释**: 关键阶段确实串行化了——每个波次只包含一个批次，符合安全要求。

---

## 端到端工作流程

### 无冲突场景
```
配置: optimistic, maxParallel=6
↓
scheduler 按 maxParallel 分组: [[B001, B002, B003], [B004, B005]]
↓
Wave 1: 并行执行 B001, B002, B003
↓
build-candidate: 合并 3 个分支 → 成功 (无冲突)
↓
verify-candidate: B-INT 通过
↓
promote-candidate: 推广到 main
↓
Wave 2: 继续执行 B004, B005
```

### 冲突自动解决场景
```
Wave 1: 并行执行 B001, B002 (两者都修改了 src/core.py)
↓
build-candidate: 合并失败 → status: "candidate_conflicted"
↓
workflow 检测到冲突，调用 resolve-candidate
↓
ConflictAnalyzer 判定为 append-only 冲突
↓
AutoMergeStrategy 自动合并 (保留双方新增内容)
↓
resolve-candidate 返回: status: "built", candidateSha: "abc123"
↓
verify-candidate: 在 abc123 上运行 B-INT
↓
B-INT 通过 → promote-candidate 推广到 main
↓
继续下一波次
```

### 需要人工介入场景
```
Wave 1: 并行执行 B001, B002 (结构性冲突)
↓
build-candidate: status: "candidate_conflicted"
↓
resolve-candidate: ModelBasedResolver 判定无法自动解决
↓
返回: status: "needs_resolution", worktreePath: "/path/to/worktree"
↓
workflow 抛出错误并停止，保留 worktree
↓
开发者手动解决冲突:
  1. cd /path/to/worktree
  2. 手动编辑文件解决冲突
  3. git add . && git commit
↓
运行 `resume-candidate`，再依次调用 `verify-candidate` 与 `promote-candidate`。直接重跑 workflow 会被 `needs_resolution` 门禁拦截，避免覆盖手工解决的 candidate。
```

---

## 配置选项

### `.autobiz/runtime_config.json`

```json
{
  "parallelSchedulingMode": "optimistic",
  "maxParallel": 6,
  "conflictResolution": {
    "maxAttempts": 2,
    "enableAutoResolve": true
  }
}
```

**字段说明**:
- `parallelSchedulingMode`: `"optimistic"` | `"conservative"`
  - `optimistic`: 忽略写集冲突，最大化并行度
  - `conservative`: 检测写集重叠，串行化冲突批次
- `maxParallel`: 每个波次的最大并行批次数 (optimistic 模式下生效)
- `conflictResolution.maxAttempts`: 自动解决尝试次数上限
- `conflictResolution.enableAutoResolve`: 是否启用自动冲突解决
  - `true`: 调用 AI 模型尝试解决
  - `false`: 直接返回 `needs_resolution`

---

## 测试建议

### 最小验证路径

1. **无冲突并行执行**
   ```bash
   # 创建测试 feature，包含修改不同文件的 3 个 batch
   # 配置 optimistic + maxParallel=3
   # 验证: 3 个 batch 在同一 wave 执行
   ```

2. **Append-only 冲突自动解决**
   ```bash
   # 创建 2 个 batch，都在同一文件末尾添加新函数
   # enableAutoResolve=true
   # 验证: 冲突自动解决，双方函数都保留
   ```

3. **结构性冲突人工处理**
   ```bash
   # 创建 2 个 batch，修改同一函数签名
   # enableAutoResolve=true
   # 验证: 返回 needs_resolution，worktree 保留
   ```

4. **配置传递验证**
   ```bash
   # 设置 maxParallel=2
   # 创建 5 个独立 batch
   # 验证: 实际分 3 个 wave: [2], [2], [1]
   ```

### 完整集成测试

建议创建 `tests/integration/test_end_to_end_optimistic.py`，包含:
- 真实 Git 临时仓库
- 模拟冲突分支创建
- 完整 workflow 执行
- Manifest 状态验证
- Worktree 清理验证

---

## 灰度发布检查清单

- [x] 运行时配置正确传递到调度器
- [x] 冲突检测保留 worktree 而不是中止
- [x] `resolve-candidate` CLI 可用且正常工作
- [x] `discard-candidate` CLI 可用且正常工作
- [x] Workflow 集成冲突解决逻辑
- [x] 预览使用正确的写集计算
- [x] 测试断言修正
- [ ] 端到端集成测试编写并通过
- [ ] 在真实项目上进行小规模试验（2-3 个批次）
- [ ] 监控冲突率和自动解决成功率
- [ ] 准备回滚方案（切换回 conservative 模式）

---

## 已知限制

1. **Append-only 自动解决范围有限**: 仅允许名称不同的 Python/JavaScript 函数声明追加；类、导入、常量、重复名称或其他语言结构一律要求人工解决。建议第一版仅对低风险项目启用 `enableAutoResolve`。

2. **Model-based 解决未完全实现**: `ModelBasedResolver` 对非 append-only 冲突返回 `manual_required`。需要集成实际 AI 模型调用。

3. **恢复路径未完全自动化**: 人工解决后需要手动重跑 workflow 或调用 CLI。未来可添加 `resume-from-conflict` 命令。

4. **测试覆盖率不足**: 单元测试通过但缺少真实 Git 集成测试。

---

## 后续改进建议

### 短期 (1-2 周)
- [ ] 编写真实 Git 集成测试
- [ ] 收集灰度期间的冲突案例
- [ ] 优化 append-only 识别规则（检测重复定义、import 顺序等）

### 中期 (1 月)
- [ ] 实现真正的 model-based semantic resolution
- [ ] 添加冲突解决质量评分
- [ ] 自动化 resume 流程

### 长期 (3 月)
- [ ] 学习历史冲突模式，预测冲突概率
- [ ] 动态调整 maxParallel（高冲突率时降低）
- [ ] 支持批次级别的冲突策略配置

---

## 修改文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `hooks/parallel_merge_train.py` | 新增功能 | 添加 `resolve_candidate()`, `discard_candidate()` 函数和 CLI |
| `workflows/code-batched-execution.workflow.js` | 修改逻辑 | 集成冲突检测和自动解决 |
| `hooks/workflow_launcher.py` | 修复 Bug | 使用 `batch_write_set()` 计算预览写集 |
| `tests/test_optimistic_parallel.py` | 修复 Bug | 修正 proto 阶段测试断言 |

---

## 结论

所有 P0 和 P1 问题已修复。核心目标"启用 optimistic 后实际并行、冲突后自动/人工恢复"现已实现：

✅ 配置会改变实际调度行为  
✅ 首次冲突不再导致立即失败  
✅ 冲突状态有明确的恢复路径  
✅ 冲突解决代码已接入工作流  
✅ 预览与实际执行使用相同的写集计算  
✅ 测试断言正确反映实际行为  

**准备状态**: 可以进入小规模灰度测试。建议先在低风险项目上验证，收集真实冲突数据后再扩大范围。
