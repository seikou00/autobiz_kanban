# Adaptive Workflow 快速开始指南（已归档）

> 本文对应的 `adaptive-execution.workflow.js` 已删除。请勿按本文命令执行；
> 当前执行入口是 `workflows/code-batched-execution.workflow.js`。

## 概述

新的 `adaptive-execution.workflow.js` 引入了智能决策能力，可以根据 plan 的特征自动调整执行策略。

## 主要改进

### 1. 智能策略分析 🧠

Workflow 在执行前会分析 plan 并选择最优策略：

```javascript
// 模型自动分析并决定策略
const strategy = await agent(`分析 plan 并制定最优执行策略...`);

// 策略包含:
// - mode: "full-parallel" | "wave-based" | "conservative"
// - maxParallel: 1-8
// - preCheckConflicts: boolean
// - conflictStrategy: "stop" | "continue" | "auto-resolve"
// - riskLevel: "low" | "medium" | "high"
```

### 2. 冲突预检测 🔍

执行前可以检测潜在冲突：

```javascript
if (strategy.preCheckConflicts) {
  // 分析 writeSet 重叠
  // 如果检测到冲突，降级为保守模式
}
```

### 3. 智能冲突处理 🔧

遇到冲突时，模型会分析最佳处理策略：

```javascript
const conflictAnalysis = await agent(`分析冲突并给出处理建议...`);

// 可能的策略:
// - auto: 自动解决（高置信度时）
// - semi-auto: 半自动（给建议）
// - manual: 人工处理
```

## 使用方式

### 方式 1: 自动决策（推荐）

让模型自动分析并决定策略：

```python
# launcher.py
result = call_workflow(
    name="adaptive-code-execution",
    args={
        "feature": feature,
        "pluginPath": plugin_path,
        "artifactWorkspace": workspace,
        "codeWorkspaces": code_workspaces,
        # 不传 strategy，让模型决定
    }
)
```

### 方式 2: 指定策略

手动指定策略（跳过分析步骤）：

```python
# 保守模式 - 适合首次执行或高风险场景
strategy = {
    "mode": "conservative",
    "maxParallel": 1,
    "conflictStrategy": "stop",
    "riskLevel": "high",
    "reasoning": "First time execution, use conservative approach"
}

result = call_workflow(
    name="adaptive-code-execution",
    args={
        "feature": feature,
        "pluginPath": plugin_path,
        "artifactWorkspace": workspace,
        "codeWorkspaces": code_workspaces,
        "strategy": strategy  # 强制使用此策略
    }
)
```

```python
# 全并发模式 - 适合独立 batch
strategy = {
    "mode": "full-parallel",
    "maxParallel": 4,
    "preCheckConflicts": False,
    "riskLevel": "low",
    "reasoning": "All batches are independent"
}
```

```python
# 波次模式 - 适合有依赖的场景
strategy = {
    "mode": "wave-based",
    "maxParallel": 4,
    "waveSize": 2,
    "earlyMerge": True,
    "preCheckConflicts": True,
    "riskLevel": "medium",
    "reasoning": "Has dependencies, merge early to detect conflicts"
}
```

## 策略详解

### full-parallel（全并发）

```
特点: 一次性并发所有 ready 的 batch
适用: batch 间独立，writeSet 无重叠
优点: 最快
风险: 可能产生较多冲突
```

**何时使用**:
- Batch 数量 < 10
- 无依赖关系或依赖深度 = 1
- 预期冲突率 < 20%
- 首次尝试（快速失败）

### wave-based（分波次）

```
特点: 分批次执行，每批 waveSize 个
适用: 有依赖关系或预期有冲突
优点: 可控，减少冲突
风险: 执行时间较长
```

**何时使用**:
- Batch 数量 5-20
- 有依赖关系（深度 ≥ 2）
- 预期冲突率 20-50%
- 想要平衡速度和稳定性

**参数调优**:
```javascript
{
  waveSize: 2,        // 每波 2 个 batch
  earlyMerge: true,   // 每波次后立即合并
  preCheckConflicts: true  // 检测潜在冲突
}
```

### conservative（保守）

```
特点: 串行执行，每次只执行 1 个
适用: 首次执行，复杂依赖，高冲突预期
优点: 最稳定
风险: 最慢
```

**何时使用**:
- 首次执行，不了解冲突情况
- Batch 数量 > 20
- 依赖关系复杂（深度 ≥ 3）
- 预期冲突率 > 50%
- 关键发布（稳定性优先）

## 执行流程

```
1. 智能分析阶段
   ├─ 创建 run manifest
   ├─ 分析 plan 特征
   ├─ 决定执行策略
   └─ 输出策略和理由

2. 动态执行阶段
   ├─ 根据策略决定本轮 batch
   ├─ (可选) 冲突预检测
   ├─ 并发执行 batch
   └─ 循环直到完成

3. 智能合并阶段
   ├─ 合并已完成的 batch
   ├─ 检测冲突
   ├─ (可选) 分析冲突复杂度
   ├─ (可选) 自动解决冲突
   └─ 或人工介入

4. 最终验证阶段
   └─ 编译门禁验证
```

## 监控和日志

Workflow 会输出详细的决策日志：

```
[智能分析] Run ID: cw-20260821-001
[智能分析] 执行策略: wave-based
[智能分析] 最大并发: 4
[智能分析] 风险等级: medium
[智能分析] 理由: Plan has 8 batches with dependencies, using wave-based to control conflicts
[智能分析] 预计耗时: 25 分钟

[动态执行] 波次 1: 执行 2/8 个 batch (wave-based 模式)
[动态执行] 执行冲突预检测...
[动态执行] ⚠️  检测到潜在冲突: B003, B004
[动态执行] 冲突文件: src/main.py, src/utils.py
[动态执行] 降级为保守模式，串行执行剩余 batch

[智能合并] 检测到合并冲突，分析处理策略...
[智能合并] 冲突分析: Conflicts are in utility files, auto-merge is safe
[智能合并] 建议策略: auto (置信度: high)
[智能合并] 尝试自动解决冲突...

[动态执行] 已完成 6 个 batch，剩余 2 个
[最终验证] ✅ 验证通过
```

## 对比：旧 vs 新

### 旧 workflow (code-batched-execution.workflow.js)

```javascript
// 固定逻辑
const maxParallel = 4;

// 一次性调度所有 ready batch
const executions = scheduledGroups.map(...);

// 固定的冲突处理
if (mergeResult.needsResolution) {
  // 总是人工处理
}
```

**特点**:
- ✅ 稳定可靠
- ✅ 逻辑清晰
- ❌ 无法适应不同场景
- ❌ 冲突处理单一

### 新 workflow (adaptive-execution.workflow.js)

```javascript
// 🔥 智能分析
const strategy = await agent(`分析 plan...`);

// 🔥 动态调整
if (strategy.mode === "wave-based") {
  currentWave = scheduledGroups.slice(0, strategy.waveSize);
}

// 🔥 冲突预检测
if (strategy.preCheckConflicts) {
  const conflictCheck = await agent(`检查冲突...`);
}

// 🔥 智能冲突处理
if (conflictAnalysis.strategy === "auto") {
  // 自动解决
}
```

**特点**:
- ✅ 适应不同场景
- ✅ 减少人工介入
- ✅ 更快或更稳定（根据情况）
- ⚠️ 依赖模型质量
- ⚠️ 增加了复杂度

## 迁移建议

### 阶段 1: 并行运行（本周）

同时保留两个 workflow，根据场景选择：

```python
# launcher.py

def launch_parallel_code(workspace, feature, ...):
    bundle = load_plan_bundle(feature_dir)
    batch_count = len([b for b in bundle.root["batches"] if ...])
    
    # 简单规则
    if batch_count <= 3:
        # 少量 batch，用旧 workflow
        return call_workflow(name="code-batched-execution", ...)
    else:
        # 多 batch，用新 workflow
        return call_workflow(name="adaptive-code-execution", ...)
```

### 阶段 2: 逐步切换（下周）

增加使用新 workflow 的比例：

```python
# 70% 使用新 workflow
use_adaptive = batch_count > 3 or random.random() < 0.7

if use_adaptive:
    return call_workflow(name="adaptive-code-execution", ...)
else:
    return call_workflow(name="code-batched-execution", ...)
```

### 阶段 3: 完全切换（本月）

所有场景都使用新 workflow：

```python
# 默认使用 adaptive
return call_workflow(name="adaptive-code-execution", ...)
```

保留旧 workflow 作为回退方案。

## 测试

```bash
# 运行单元测试
pytest tests/test_adaptive_workflow.py -v

# 验证 workflow 语法
node --check workflows/adaptive-execution.workflow.js

# 端到端测试（需要准备测试 feature）
python -m hooks.parallel_batch_scheduler create \
  --feature test-adaptive \
  --max-parallel 4 \
  --code-workspace /path/to/repo
```

## 故障排查

### 问题 1: 策略分析失败

**症状**: `strategy_decision_failed`

**原因**: 模型无法生成有效策略

**解决**:
```python
# 传入固定策略
args = {
    ...,
    "strategy": {
        "mode": "full-parallel",
        "maxParallel": 4,
        "riskLevel": "medium",
        "reasoning": "fallback strategy"
    }
}
```

### 问题 2: 冲突预检测误报

**症状**: 不必要的串行化

**原因**: writeSet 过于保守或模型判断过于严格

**解决**:
```python
# 关闭冲突预检测
strategy = {
    ...,
    "preCheckConflicts": False
}
```

### 问题 3: 自动冲突解决失败

**症状**: 编译失败或功能错误

**原因**: 冲突过于复杂，自动合并不可靠

**解决**:
```python
# 禁用自动解决
strategy = {
    ...,
    "conflictStrategy": "manual"
}
```

## 下一步

1. **收集数据**: 记录每次执行的策略、耗时、冲突率
2. **优化决策**: 根据历史数据调优模型 prompt
3. **增强功能**: 添加更多决策点（如智能重试）
4. **完全生成**: 长期目标是让模型生成完整 workflow

## 参考

- [当前固定 Workflow](../../../workflows/code-batched-execution.workflow.js)
- [迁移指南](./workflow-migration-guide.md)
- [代码检视报告](./code-review-findings.md)
