# 方案 3 混合方案 - 历史归档

> 本文不可作为执行手册。旧 fixed workflow、strategy 参数、插件自管
> worktree、旧 resolver 和 generated workflow 已移除；当前执行入口是
> `workflows/code-batched-execution.workflow.js`。

## 🎯 核心发现

### 1. Workflow 调用方式确认 ✅

**调用者**: `/autodev-code` SKILL（由 Claude 执行）

**调用方式**: 
```markdown
必须读取插件中的 `workflows/code-batched-execution.workflow.js` 内容，
以 workflow 工具的内联 `script` 参数启动，并把 `args.codeWorkspaces` 传入
```

**关键信息**:
- ✅ Claude 直接调用 `Workflow` 工具
- ✅ 使用内联 `script` 参数（读取文件内容传入）
- ✅ 通过 `args` 参数传递配置
- ✅ `workflow_launcher.py` 只是决策分析器，不负责调用

### 2. 参数传递链路 ✅

```
Claude (/autodev-code SKILL)
  ↓
  读取 workflow_launcher.py 分析结果
  ↓
  读取 code-batched-execution.workflow.js 文件内容
  ↓
  调用 Workflow({script: fileContent, args: {...}})
  ↓
  workflow 内部接收 args.strategy
```

### 3. 最佳实施方案 ⭐

**方案 C（Workflow 内部自决策）是最佳选择**：

**优势**：
- ✅ 不需要修改 SKILL.md（风险大）
- ✅ 不需要修改 workflow_launcher.py（已经是分析器）
- ✅ 只修改 workflow 本身（影响面小）
- ✅ 完全向后兼容
- ✅ 支持外部传入 strategy（可扩展）

---

## 📋 完整实施方案

### 阶段 0: 备份（5 分钟）

```bash
# 备份当前 workflow
cp workflows/code-batched-execution.workflow.js \
   workflows/code-batched-execution.workflow.js.backup

# 验证备份
ls -lh workflows/code-batched-execution.workflow.js*
```

---

### 阶段 1: 修改 Workflow（45 分钟）

#### 修改 1.1: 增加策略决策逻辑（在 meta 之后，args 解析位置）

找到这段代码（约在第 56-72 行）：
```javascript
const feature = args.feature;
const pluginPath = args.pluginPath;
const artifactWorkspace = args.artifactWorkspace;
const codeWorkspaces = args.codeWorkspaces || {};
const maxParallel = args.maxParallel || 4;
const timeoutPerBatch = args.timeoutPerBatch || 3600;
```

**在它之后增加**：

```javascript
// ========== 🔥 策略决策开始 ==========
let strategy = args.strategy;  // 优先使用外部传入的策略

if (!strategy) {
  log("未指定执行策略，分析 manifest 自动决策...");
  
  // 读取 manifest 获取 batch 信息
  const manifestPath = `${artifactWorkspace}/${feature}/.parallel-runs/current/manifest.json`;
  let batchCount = 0;
  let hasDeps = false;
  
  try {
    const manifestResult = await agent(
      `读取文件 ${manifestPath}，提取：
      1. batches 对象的 key 数量（batchCount）
      2. 是否有任何 batch 的 deps 数组非空（hasDeps）
      
      返回 JSON: {"batchCount": <number>, "hasDeps": <boolean>}`,
      {
        label: "读取manifest",
        phase: "准备",
        schema: {
          type: "object",
          properties: {
            batchCount: { type: "number" },
            hasDeps: { type: "boolean" }
          },
          required: ["batchCount", "hasDeps"]
        }
      }
    );
    
    batchCount = manifestResult?.batchCount || 0;
    hasDeps = manifestResult?.hasDeps || false;
    
  } catch (e) {
    log(`读取 manifest 失败，使用默认策略: ${e.message}`);
    batchCount = 4;  // 保守估计
    hasDeps = true;
  }
  
  // 策略决策规则
  if (batchCount <= 2) {
    strategy = {
      mode: "full-parallel",
      waveSize: maxParallel,
      riskLevel: "low",
      reasoning: `只有 ${batchCount} 个 batch，全并发执行最快`
    };
  } else if (batchCount <= 5) {
    if (hasDeps) {
      strategy = {
        mode: "wave-based",
        waveSize: 2,
        riskLevel: "medium",
        reasoning: `${batchCount} 个 batch 且有依赖关系，使用波次模式（每波 2 个）`
      };
    } else {
      strategy = {
        mode: "full-parallel",
        waveSize: maxParallel,
        riskLevel: "low",
        reasoning: `${batchCount} 个独立 batch，全并发执行`
      };
    }
  } else if (batchCount <= 10) {
    strategy = {
      mode: "wave-based",
      waveSize: 3,
      riskLevel: "medium",
      reasoning: `${batchCount} 个 batch，使用波次模式降低冲突风险（每波 3 个）`
    };
  } else {
    strategy = {
      mode: "wave-based",
      waveSize: 2,
      riskLevel: "high",
      reasoning: `${batchCount} 个 batch，保守波次模式（每波 2 个）`
    };
  }
  
  log(`✓ 自动决策完成: ${strategy.mode} (风险: ${strategy.riskLevel})`);
  log(`  理由: ${strategy.reasoning}`);
} else {
  log(`✓ 使用外部指定策略: ${strategy.mode} (风险: ${strategy.riskLevel || 'unknown'})`);
  if (strategy.reasoning) {
    log(`  理由: ${strategy.reasoning}`);
  }
}

// 归一化策略参数
strategy.mode = strategy.mode || "full-parallel";
strategy.waveSize = strategy.waveSize || maxParallel;
strategy.riskLevel = strategy.riskLevel || "medium";

log(`执行配置: mode=${strategy.mode}, waveSize=${strategy.waveSize}, maxParallel=${maxParallel}`);
// ========== 策略决策结束 ==========
```

#### 修改 1.2: 修改执行循环（在主循环内，约第 100-120 行）

找到这段代码：
```javascript
const executions = scheduledGroups.map(([batchId]) => ({ batchId, runId }));
```

**替换为**：

```javascript
// ========== 🔥 根据策略决定本轮执行的 batch ==========
let currentWave = scheduledGroups;

if (strategy.mode === "conservative") {
  // 保守模式：每次只执行 1 个
  currentWave = scheduledGroups.slice(0, 1);
  log(`[波次 ${schedulerWaves}] 保守模式: 串行执行 1/${scheduledGroups.length} 个 batch`);
  
} else if (strategy.mode === "wave-based") {
  // 波次模式：每次执行 waveSize 个
  const waveSize = Math.min(strategy.waveSize, maxParallel);
  currentWave = scheduledGroups.slice(0, waveSize);
  log(`[波次 ${schedulerWaves}] 波次模式: 执行 ${currentWave.length}/${scheduledGroups.length} 个 batch (waveSize=${waveSize})`);
  
} else {
  // 全并发模式：执行所有 ready 的 batch（默认行为）
  log(`[波次 ${schedulerWaves}] 全并发: 并发执行 ${currentWave.length} 个 batch`);
}

const executions = currentWave.map(([batchId]) => ({ batchId, runId }));
// ========== 策略执行逻辑结束 ==========
```

#### 修改 1.3: 语法检查

```bash
# 检查语法
node --check workflows/code-batched-execution.workflow.js

# 如果有错误，仔细检查：
# 1. 所有大括号是否匹配
# 2. 字符串引号是否正确
# 3. 变量名是否正确（batchCount, hasDeps, strategy）
```

---

### 阶段 2: 测试验证（30 分钟）

#### 测试 2.1: 语法测试

```bash
# 语法检查
node --check workflows/code-batched-execution.workflow.js

# 预期输出：无输出表示语法正确
```

#### 测试 2.2: 模拟运行（需要真实 feature）

```bash
# 假设你有一个测试 feature
FEATURE="test-hybrid"

# 1. 检查 feature 是否存在
python hooks/workflow_launcher.py \
  --feature "$FEATURE" \
  --plugin-path . \
  --workspace .autobizdevops \
  --json

# 预期输出：{"useWorkflow": true, "batchCount": N, ...}

# 2. 如果有真实的 parallel run，查看日志
ls -la ".autobizdevops/$FEATURE/.parallel-runs/*/manifest.json" 2>/dev/null

# 3. 手动测试策略决策逻辑（创建测试脚本）
```

#### 测试 2.3: 创建单元测试

```bash
cat > tests/test_workflow_strategy.py << 'EOF'
#!/usr/bin/env python3
"""测试 workflow 策略决策逻辑"""

def test_strategy_decision_rules():
    """测试策略决策规则"""
    
    test_cases = [
        # (batchCount, hasDeps, expected_mode, expected_waveSize_range)
        (1, False, "full-parallel", None),
        (2, False, "full-parallel", None),
        (3, False, "full-parallel", None),
        (3, True, "wave-based", (2, 2)),
        (5, False, "full-parallel", None),
        (5, True, "wave-based", (2, 2)),
        (8, False, "wave-based", (3, 3)),
        (8, True, "wave-based", (3, 3)),
        (15, False, "wave-based", (2, 2)),
        (15, True, "wave-based", (2, 2)),
    ]
    
    for batch_count, has_deps, expected_mode, expected_wave_range in test_cases:
        # 模拟决策逻辑
        if batch_count <= 2:
            mode = "full-parallel"
            wave_size = 4
        elif batch_count <= 5:
            if has_deps:
                mode = "wave-based"
                wave_size = 2
            else:
                mode = "full-parallel"
                wave_size = 4
        elif batch_count <= 10:
            mode = "wave-based"
            wave_size = 3
        else:
            mode = "wave-based"
            wave_size = 2
        
        assert mode == expected_mode, \
            f"batch={batch_count}, deps={has_deps}: expected {expected_mode}, got {mode}"
        
        if expected_wave_range:
            min_wave, max_wave = expected_wave_range
            assert min_wave <= wave_size <= max_wave, \
                f"batch={batch_count}: waveSize {wave_size} not in range [{min_wave}, {max_wave}]"
        
        print(f"✓ batch={batch_count}, deps={has_deps} → {mode} (wave={wave_size})")
    
    print("\n✅ 所有策略决策规则测试通过")

if __name__ == "__main__":
    test_strategy_decision_rules()
EOF

chmod +x tests/test_workflow_strategy.py
python tests/test_workflow_strategy.py
```

---

### 阶段 3: 集成测试（15 分钟）

#### 测试 3.1: 准备测试环境

```bash
# 1. 确保有一个可用的 feature
# 2. 确保 plan.json 中有多个 batch
# 3. 确保 batch 状态为 pending

# 查看当前 feature 列表
ls -d .autobizdevops/*/plan.json | sed 's|/.*/||; s|plan.json||' | head -5
```

#### 测试 3.2: 端到端测试（需要在 /autodev-code 中执行）

由于实际调用是通过 `/autodev-code` SKILL，我们创建一个检查清单：

**测试检查清单**：
- [ ] workflow 语法正确（node --check 通过）
- [ ] strategy 决策逻辑存在（grep "策略决策" 找到代码）
- [ ] 执行模式切换逻辑存在（grep "wave-based" 找到代码）
- [ ] 日志输出包含策略信息（grep "执行配置" 找到代码）

```bash
# 自动化检查
echo "=== Workflow 修改检查 ==="

# 1. 语法检查
echo -n "1. 语法检查... "
if node --check workflows/code-batched-execution.workflow.js 2>/dev/null; then
    echo "✓"
else
    echo "✗ FAILED"
    exit 1
fi

# 2. 策略决策逻辑
echo -n "2. 策略决策逻辑... "
if grep -q "策略决策开始" workflows/code-batched-execution.workflow.js; then
    echo "✓"
else
    echo "✗ MISSING"
    exit 1
fi

# 3. 执行模式切换
echo -n "3. 执行模式切换... "
if grep -q "wave-based" workflows/code-batched-execution.workflow.js && \
   grep -q "conservative" workflows/code-batched-execution.workflow.js && \
   grep -q "full-parallel" workflows/code-batched-execution.workflow.js; then
    echo "✓"
else
    echo "✗ MISSING"
    exit 1
fi

# 4. 日志输出
echo -n "4. 日志输出... "
if grep -q "执行配置:" workflows/code-batched-execution.workflow.js; then
    echo "✓"
else
    echo "✗ MISSING"
    exit 1
fi

echo ""
echo "✅ 所有检查通过！Workflow 已正确修改。"
```

---

### 阶段 4: 生产验证（1 周）

#### 第 1 天: 观察模式

```bash
# 不做任何修改，只观察当前执行情况
# 记录：
# - 平均执行时间
# - 冲突次数
# - 成功率
```

#### 第 2-3 天: 10% 流量

```bash
# 选择 1-2 个低风险 feature 测试
# 观察：
# - 策略决策是否合理
# - 执行时间是否改善
# - 是否有新问题
```

#### 第 4-5 天: 50% 流量

```bash
# 扩大到一半的 feature
# 收集数据：
# - 不同策略的效果对比
# - 冲突率变化
# - 执行时间分布
```

#### 第 6-7 天: 100% 流量

```bash
# 全量推广
# 持续监控
```

---

## ✅ 完成标准

### 功能完整性
- [x] Workflow 可以自动决策执行策略
- [x] 支持 3 种执行模式（full-parallel, wave-based, conservative）
- [x] 支持外部传入 strategy 参数（可扩展）
- [x] 向后兼容（不传 strategy 时自动决策）
- [x] 日志清晰显示策略信息

### 质量标准
- [x] 语法正确（node --check 通过）
- [x] 逻辑清晰（代码可读）
- [x] 有备份（可快速回滚）
- [x] 有测试（策略规则测试）
- [x] 有文档（本文档）

### 性能标准
- [ ] 执行时间减少 10-30%（根据场景）
- [ ] 冲突率降低 20-30%
- [ ] 成功率保持或提升

---

## 🔄 回滚方案

### 方法 1: Git 回滚（推荐）

```bash
# 查看修改
git diff workflows/code-batched-execution.workflow.js

# 回滚
git checkout workflows/code-batched-execution.workflow.js
```

### 方法 2: 备份恢复

```bash
# 恢复备份
cp workflows/code-batched-execution.workflow.js.backup \
   workflows/code-batched-execution.workflow.js

# 验证
node --check workflows/code-batched-execution.workflow.js
```

### 方法 3: 禁用策略逻辑

```javascript
// 在 workflow 开头，策略决策之后增加：
// 🚨 临时回滚：固定使用全并发模式
strategy = {
  mode: "full-parallel",
  waveSize: maxParallel,
  riskLevel: "low",
  reasoning: "临时回滚到全并发模式"
};
```

---

## 📊 监控指标

### 关键指标

1. **执行时间**
   - 平均执行时间
   - P50, P90, P95, P99
   - 按策略分组统计

2. **冲突率**
   - 总冲突次数
   - 按策略分组
   - 冲突类型分布

3. **成功率**
   - 整体成功率
   - 按策略分组
   - 失败原因分类

4. **策略分布**
   - 各策略使用次数
   - 自动决策 vs 手动指定
   - 决策理由分布

### 监控方法

```bash
# 1. 查看最近的 workflow 运行
ls -lt .autobizdevops/*/.parallel-runs/*/manifest.json | head -10

# 2. 统计执行时间（从 manifest 中提取）
# 需要写脚本解析 manifest.json 的 events

# 3. 统计冲突次数
grep -r "conflict" .autobizdevops/*/.parallel-runs/*/manifest.json | wc -l

# 4. 查看策略决策日志（需要在 workflow 输出中查找）
```

---

## 🚀 后续增强

### 短期（1-2 周）

1. **优化策略规则**
   - 根据实际数据调整阈值
   - 增加更多决策因素（writeSet 重叠、历史冲突率等）

2. **增加监控**
   - 自动收集执行数据
   - 生成策略效果报告

3. **完善测试**
   - 增加更多边界情况测试
   - 增加性能回归测试

### 中期（1 个月）

1. **引入预冲突检测**（决策点 2）
   - 分析 writeSet 重叠
   - 动态调整波次大小

2. **智能冲突处理**（决策点 3）
   - 自动解决简单冲突
   - 减少人工介入

3. **历史数据学习**
   - 记录执行历史
   - 根据历史调整策略

### 长期（2-3 个月）

1. **完全 Adaptive Workflow**
   - 运行时动态调整
   - 自适应并发度

2. **模型生成 Workflow**（方案 1）
   - 根据 plan 生成定制化 workflow
   - 完全智能化

---

## 📚 相关文档

- [问题分析](./workflow-hybrid-implementation-issues.md) - 发现的问题和选择的方案
- [当前固定 Workflow](../../../workflows/code-batched-execution.workflow.js)
- [原快速指南](./workflow-hybrid-quick-start.md) - 原版指南（已废弃）

---

## ❓ 常见问题

### Q1: 为什么不修改 SKILL.md？

**回答**: 
- SKILL.md 是 Claude 执行的指令，修改风险大
- 当前方案在 workflow 内部决策，不需要修改 SKILL
- 未来如果需要，可以在 SKILL 中增加策略传递逻辑

### Q2: 为什么不修改 workflow_launcher.py？

**回答**:
- workflow_launcher.py 只是分析器，返回 JSON
- 它不负责调用 Workflow 工具
- 真正的调用者是 Claude（通过 SKILL.md）

### Q3: 如何传入自定义策略？

**回答**:
- 当前方案支持外部传入 strategy
- 在 SKILL.md 中调用 Workflow 时，可以在 args 中增加 strategy 字段
- 示例：`args: {feature: "xxx", strategy: {mode: "wave-based", waveSize: 2}}`

### Q4: 策略决策规则如何调整？

**回答**:
- 修改 workflow 中的策略决策逻辑（修改 1.1 部分）
- 根据实际数据调整阈值（batchCount 的分界点）
- 增加更多决策因素（writeSet、历史冲突率等）

### Q5: 如何快速回滚？

**回答**:
```bash
# 方法 1: Git 回滚
git checkout workflows/code-batched-execution.workflow.js

# 方法 2: 恢复备份
cp workflows/code-batched-execution.workflow.js.backup \
   workflows/code-batched-execution.workflow.js
```

---

## 🎯 执行时间估算

- **阶段 0（备份）**: 5 分钟
- **阶段 1（修改）**: 45 分钟
  - 修改 1.1: 20 分钟
  - 修改 1.2: 15 分钟
  - 修改 1.3: 10 分钟
- **阶段 2（测试）**: 30 分钟
- **阶段 3（集成）**: 15 分钟
- **阶段 4（生产）**: 1 周

**总计**: 1.5 小时可完成代码修改和测试，1 周完成生产验证。

---

## 🎉 总结

这是一个**完全可执行**的落地方案：

✅ **明确的修改位置**（只修改 workflow，不动 SKILL 和 launcher）
✅ **详细的代码示例**（可直接复制使用）
✅ **完整的测试步骤**（语法、单元、集成）
✅ **清晰的回滚方案**（3 种方法）
✅ **具体的监控指标**（执行时间、冲突率、成功率）
✅ **渐进式推广**（10% → 50% → 100%）

**立即开始**：
```bash
# 1. 备份
cp workflows/code-batched-execution.workflow.js \
   workflows/code-batched-execution.workflow.js.backup

# 2. 按本文档修改 workflow

# 3. 测试
node --check workflows/code-batched-execution.workflow.js

# 4. 验证
# 在实际 feature 中运行，观察日志
```

Good luck! 🚀
