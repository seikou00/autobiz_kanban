# 方案 3 混合方案 - 快速实施指南（已归档）

> 本文是旧的“固定骨架 + strategy”方案，仅供历史参考。当前执行入口是
> `workflows/code-batched-execution.workflow.js`，不得按本文命令操作。

## 🎯 目标

**1 小时内**完成基础实施，获得核心能力：
- ✅ 策略参数化
- ✅ Wave-based 执行模式
- ✅ 向后兼容

## 📋 实施步骤

### Step 1: 修改 Workflow（30 分钟）

打开 `workflows/code-batched-execution.workflow.js`，应用以下修改：

#### 1.1 在参数解析部分增加策略支持（第 56-72 行之后）

```javascript
// ========== 🔥 新增：策略支持 ==========
const userStrategy = args.strategy;  // 从 args 读取策略

// 默认策略（向后兼容）
let strategy = {
  mode: userStrategy?.mode || "full-parallel",  // 默认全并发
  waveSize: userStrategy?.waveSize || maxParallel,
  preCheckConflicts: userStrategy?.preCheckConflicts || false,
  conflictStrategy: userStrategy?.conflictStrategy || "stop",
  riskLevel: userStrategy?.riskLevel || "medium",
  reasoning: userStrategy?.reasoning || "default-strategy"
};

log(`执行策略: mode=${strategy.mode}, waveSize=${strategy.waveSize}, riskLevel=${strategy.riskLevel}`);
if (strategy.reasoning !== "default-strategy") {
  log(`策略理由: ${strategy.reasoning}`);
}
// ========== 策略支持结束 ==========
```

#### 1.2 修改执行循环（第 100 行左右的 while 循环内）

找到这段代码：
```javascript
const executions = scheduledGroups.map(([batchId]) => ({ batchId, runId }));
```

**替换为**：

```javascript
// ========== 🔥 新增：根据策略决定本轮执行的 batch ==========
let currentWave = scheduledGroups;

if (strategy.mode === "conservative") {
  // 保守模式：每次只执行 1 个
  currentWave = scheduledGroups.slice(0, 1);
  log(`保守模式: 波次 ${schedulerWaves} 串行执行 1 个 batch`);
} else if (strategy.mode === "wave-based") {
  // 波次模式：每次执行 waveSize 个
  const waveSize = strategy.waveSize || maxParallel;
  currentWave = scheduledGroups.slice(0, waveSize);
  log(`波次模式: 波次 ${schedulerWaves} 执行 ${currentWave.length}/${scheduledGroups.length} 个 batch (waveSize=${waveSize})`);
} else {
  // 全并发模式：执行所有 ready 的 batch（默认行为）
  log(`全并发: 波次 ${schedulerWaves} 并发执行 ${currentWave.length} 个 batch`);
}

const executions = currentWave.map(([batchId]) => ({ batchId, runId }));
// ========== 策略执行结束 ==========
```

#### 1.3 修改 resume 调用（第 160 行左右）

找到这段代码：
```javascript
const resumed = await agent(
  `执行 python "${pluginPath}/hooks/parallel_batch_scheduler.py" resume ...`,
  { label: "schedule-next-wave", phase: "准备", schema: { type: "object" } }
);
scheduledGroups = resumed?.scheduledGroups || [];
```

**在它之后增加**：

```javascript
// ========== 🔥 新增：波次模式下只取下一波，而不是所有剩余 ==========
if (strategy.mode === "wave-based" || strategy.mode === "conservative") {
  // 波次模式：继续下一波
  if (!scheduledGroups.length) {
    // 当前波次组已执行完，获取新的调度
    scheduledGroups = resumed?.scheduledGroups || [];
  }
  // 否则继续执行当前波次组的剩余 batch
}
// ========== 波次逻辑结束 ==========
```

**完成！** workflow 修改完成。

---

### Step 2: 创建策略建议器（10 分钟）

策略建议器文件已创建：`hooks/workflow_strategy_advisor.py`

**测试运行**：

```bash
# 测试建议器
python hooks/workflow_strategy_advisor.py \
  --feature test-feature \
  --json

# 应该输出类似：
# {
#   "mode": "full-parallel",
#   "maxParallel": 4,
#   "waveSize": 2,
#   "preCheckConflicts": false,
#   "conflictStrategy": "stop",
#   "riskLevel": "low",
#   "reasoning": "只有 2 个独立 batch，全并发执行最快",
#   "analysis": { ... }
# }
```

---

### Step 3: 修改 Launcher（10 分钟）

打开 `hooks/workflow_launcher.py`，找到调用 workflow 的部分。

**在调用 workflow 前增加**：

```python
# ========== 🔥 新增：策略决策 ==========
strategy = None

# 只有多个 batch 时才需要策略
if use_workflow:
    try:
        # 调用策略建议器
        result = subprocess.run(
            [
                sys.executable,
                str(PLUGIN_DIR / "hooks" / "workflow_strategy_advisor.py"),
                "--workspace", str(workspace),
                "--feature", feature,
                "--json"
            ],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            strategy = json.loads(result.stdout)
            logger.info(f"策略建议: {strategy['mode']} (风险: {strategy['riskLevel']})")
            logger.info(f"理由: {strategy['reasoning']}")
        else:
            logger.warning(f"策略建议失败，使用默认策略: {result.stderr}")
            strategy = {"mode": "full-parallel", "riskLevel": "medium"}
    except Exception as e:
        logger.warning(f"策略建议异常，使用默认策略: {e}")
        strategy = {"mode": "full-parallel", "riskLevel": "medium"}
# ========== 策略决策结束 ==========
```

**在传递给 workflow 的 args 中增加**：

```python
workflow_args = {
    "feature": feature,
    "pluginPath": str(plugin_path),
    "artifactWorkspace": str(artifact_workspace),
    "codeWorkspaces": code_workspaces,
    "maxParallel": 4,
    "timeoutPerBatch": 3600,
    "strategy": strategy,  # 🔥 新增
}
```

**完成！** launcher 修改完成。

---

### Step 4: 测试（10 分钟）

#### 4.1 语法检查

```bash
# 检查 workflow 语法
node --check workflows/code-batched-execution.workflow.js

# 检查 Python 语法
python -m py_compile hooks/workflow_strategy_advisor.py
python -m py_compile hooks/workflow_launcher.py
```

#### 4.2 单元测试

```bash
# 测试策略建议器
pytest tests/test_workflow_strategy_advisor.py -v

# 测试完整 workflow（如果有现有测试）
pytest tests/test_parallel_batch_runtime.py -v -k "scheduler"
```

#### 4.3 手动测试

```bash
# 1. 准备测试 feature（需要有真实的 plan）
# 假设你有一个 feature 叫 "test-hybrid"

# 2. 测试策略建议
python hooks/workflow_strategy_advisor.py \
  --feature test-hybrid \
  --json

# 3. 测试 launcher（dry-run 如果支持）
python hooks/workflow_launcher.py \
  --feature test-hybrid \
  --plugin-path . \
  --workspace .autobizdevops \
  --json

# 4. 如果都正常，尝试实际运行
# （根据你的实际调用方式）
```

---

## ✅ 完成检查清单

实施完成后，检查以下项：

- [ ] workflow 文件语法正确（node --check 通过）
- [ ] 策略建议器可以正常运行
- [ ] launcher 可以调用策略建议器
- [ ] 不传 strategy 参数时，workflow 使用默认行为（向后兼容）
- [ ] 传入 strategy 参数时，workflow 按策略执行
- [ ] 日志中可以看到策略信息

---

## 🎉 验证效果

### 测试不同策略

创建测试脚本 `test_strategies.sh`：

```bash
#!/bin/bash

FEATURE="test-hybrid"

echo "=== 测试 1: 全并发模式 ==="
python hooks/workflow_launcher.py \
  --feature "$FEATURE" \
  --strategy '{"mode":"full-parallel"}' \
  --json

echo "=== 测试 2: 波次模式 ==="
python hooks/workflow_launcher.py \
  --feature "$FEATURE" \
  --strategy '{"mode":"wave-based","waveSize":2}' \
  --json

echo "=== 测试 3: 保守模式 ==="
python hooks/workflow_launcher.py \
  --feature "$FEATURE" \
  --strategy '{"mode":"conservative"}' \
  --json

echo "=== 测试 4: 自动决策（不传 strategy）==="
python hooks/workflow_launcher.py \
  --feature "$FEATURE" \
  --json
```

### 预期结果

**全并发模式**：
```
[准备] Run ID: cw-20260821-001
[准备] 执行策略: mode=full-parallel, waveSize=4, riskLevel=low
[并行实现] 全并发: 波次 1 并发执行 4 个 batch
```

**波次模式**：
```
[准备] Run ID: cw-20260821-002
[准备] 执行策略: mode=wave-based, waveSize=2, riskLevel=medium
[并行实现] 波次模式: 波次 1 执行 2/4 个 batch (waveSize=2)
[并行实现] 波次模式: 波次 2 执行 2/4 个 batch (waveSize=2)
```

**保守模式**：
```
[准备] Run ID: cw-20260821-003
[准备] 执行策略: mode=conservative, waveSize=1, riskLevel=high
[并行实现] 保守模式: 波次 1 串行执行 1 个 batch
[并行实现] 保守模式: 波次 2 串行执行 1 个 batch
...
```

---

## 🔄 回滚方案

如果出现问题，立即回滚：

### 方法 1: Git 回滚

```bash
# 查看修改
git diff workflows/code-batched-execution.workflow.js
git diff hooks/workflow_launcher.py

# 回滚
git checkout workflows/code-batched-execution.workflow.js
git checkout hooks/workflow_launcher.py
```

### 方法 2: 注释掉策略逻辑

在 workflow 中：
```javascript
// 🚨 临时禁用策略
const strategy = {
  mode: "full-parallel",  // 固定使用全并发
  waveSize: maxParallel,
  preCheckConflicts: false,
  conflictStrategy: "stop",
  riskLevel: "low",
  reasoning: "rollback-to-default"
};
// const userStrategy = args.strategy;  // 注释掉
```

在 launcher 中：
```python
# 🚨 临时禁用策略建议
strategy = {"mode": "full-parallel", "riskLevel": "low"}  # 固定策略
# strategy = None  # 注释掉策略调用逻辑
```

---

## 📊 监控要点

实施后，注意观察：

1. **执行日志**：确认策略按预期应用
   ```
   [准备] 执行策略: mode=wave-based, waveSize=2, riskLevel=medium
   [准备] 策略理由: 有依赖关系，使用波次模式
   ```

2. **执行时间**：对比不同策略的耗时
   - 全并发应该最快（如果无冲突）
   - 波次模式应该适中
   - 保守模式应该最慢但最稳定

3. **冲突率**：记录实际冲突情况
   - 如果频繁冲突，考虑默认使用 wave-based
   - 如果很少冲突，可以默认 full-parallel

4. **错误率**：确保没有引入新的错误
   - 检查 manifest 状态
   - 检查 batch 状态
   - 检查合并结果

---

## 🚀 下一步

完成基础实施后，可以逐步增强：

### 短期（1-2 周）
- [ ] 收集执行数据
- [ ] 调优策略规则
- [ ] 增加更多测试用例

### 中期（1 个月）
- [ ] 引入决策点 2（冲突预检测）
- [ ] 引入决策点 3（智能冲突处理）
- [ ] 实现历史数据收集

### 长期（2-3 个月）
- [ ] 完整的 adaptive workflow
- [ ] 模型生成 workflow（方案 1）
- [ ] 性能优化和稳定性提升

---

## 📚 相关文档

- [完整实施计划](./workflow-hybrid-implementation-plan.md)
- [当前固定 Workflow](../../../workflows/code-batched-execution.workflow.js)
- [快速开始](./adaptive-workflow-quickstart.md)

---

## ❓ 常见问题

### Q1: 修改后 workflow 不工作？

**检查**：
1. 语法是否正确：`node --check workflows/code-batched-execution.workflow.js`
2. 日志中是否有错误信息
3. 是否正确传递了 strategy 参数

**调试**：
```javascript
// 在 workflow 开头增加调试日志
log(`策略参数: ${JSON.stringify(args.strategy || {})}`);
log(`解析后策略: ${JSON.stringify(strategy)}`);
```

### Q2: 策略建议器返回错误？

**检查**：
1. feature 是否存在
2. plan.json 是否有效
3. 是否有 batch 数据

**调试**：
```bash
# 直接运行看详细错误
python hooks/workflow_strategy_advisor.py \
  --feature test-feature

# 不加 --json 会输出更多信息
```

### Q3: 波次模式不生效？

**检查**：
1. strategy.mode 是否正确设置为 "wave-based"
2. waveSize 是否合理（1-8）
3. 日志中是否显示波次信息

**调试**：
在 workflow 中增加：
```javascript
log(`当前波次: ${schedulerWaves}, 剩余: ${scheduledGroups.length}, 本轮: ${currentWave.length}`);
```

---

## 🎯 成功标准

实施成功的标志：

- ✅ 可以通过参数控制执行策略
- ✅ Wave-based 模式正常工作
- ✅ 保守模式正常工作
- ✅ 不传 strategy 时使用默认行为（向后兼容）
- ✅ 日志清晰显示策略信息
- ✅ 没有引入新的 bug

**恭喜！你已经实现了方案 3 的基础版本！** 🎉
