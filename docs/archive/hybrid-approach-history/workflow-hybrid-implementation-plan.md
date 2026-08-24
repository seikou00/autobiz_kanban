# 方案 3：混合方案完整落地实施（已归档）

> 本文描述的固定骨架 + strategy 方案已停止使用。当前执行入口是
> `workflows/code-batched-execution.workflow.js`。

> 插件自管 worktree、seal、`parallel_conflict_resolver.py` 和 generated
> workflow 已删除；本文中的代码片段仅是历史资料，当前执行入口是
> `workflows/code-batched-execution.workflow.js`。

## 📋 总体方案

基于当前的 `code-batched-execution.workflow.js`，在**关键决策点**引入模型智能，同时保持核心执行逻辑的稳定性。

---

## 🎯 实施架构

```
/autodev-code (SKILL.md)
    ↓
hooks/workflow_launcher.py (决策：是否用 workflow)
    ↓
workflows/code-batched-execution.workflow.js (主 workflow)
    ├─ 🔥 决策点 1: 执行策略分析 (新增)
    │   └─ 决定 mode/waveSize/preCheckConflicts
    ├─ 准备阶段 (现有)
    ├─ 并行实现 (现有 + 增强)
    │   ├─ 🔥 决策点 2: 冲突预检测 (新增)
    │   └─ pipeline() 执行 batch
    ├─ 顺序合并 (现有 + 增强)
    │   └─ 🔥 决策点 3: 冲突处理策略 (新增)
    └─ 最终验证 (现有)
```

---

## 📂 文件清单

### 需要修改的文件
1. ✅ `workflows/code-batched-execution.workflow.js` - 主 workflow（增强版）
2. 🆕 `hooks/workflow_strategy_advisor.py` - 策略建议器（可选）
3. 📝 `skills/autodev/autodev-code/SKILL.md` - 文档更新

### 新增文件
4. 🆕 `workflows/code-batched-execution-v2.workflow.js` - 增强版 workflow
5. 🆕 `docs/workflow-hybrid-implementation.md` - 实施文档

---

## 🔧 实施步骤

### Step 1: 增强现有 Workflow（最小改动）

修改 `workflows/code-batched-execution.workflow.js`，在关键位置增加决策点。

#### 1.1 增加策略参数支持

```javascript
// 在现有 workflow 的参数解析部分增加
const userStrategy = args.strategy;  // 用户可以强制指定策略

// 默认策略（向后兼容）
let strategy = {
  mode: "auto",  // auto | full-parallel | wave-based | conservative
  maxParallel: maxParallel,
  waveSize: maxParallel,
  preCheckConflicts: false,
  conflictStrategy: "stop",  // stop | continue | auto-resolve
  riskLevel: "medium",
  reasoning: "default-strategy"
};

// 如果用户传入了策略，合并
if (userStrategy && typeof userStrategy === "object") {
  strategy = { ...strategy, ...userStrategy };
  log(`使用用户指定策略: ${strategy.mode}`);
}
```

#### 1.2 决策点 1: 执行策略分析（可选启用）

```javascript
// 🔥 新增：在准备阶段后，增加策略分析（只在 strategy.mode === "auto" 时执行）
if (strategy.mode === "auto" && scheduledGroups.length > 0) {
  phase("策略分析");
  
  const totalBatches = scheduledGroups.length;
  
  const strategyDecision = await agent(
    `分析当前 run 并制定执行策略。

## 当前状态
- Run ID: ${runId}
- 待执行 batch 数: ${totalBatches}
- Scheduled groups: ${JSON.stringify(scheduledGroups.slice(0, 5))}
- Max parallel: ${maxParallel}

## Batch 详情（前 5 个）
${JSON.stringify(
  Object.entries(preparation.batchWorkspaces || {})
    .slice(0, 5)
    .reduce((acc, [k, v]) => ({ ...acc, [k]: v }), {}),
  null, 2
)}

## 决策要点
1. 如果 batch <= 3: 建议 full-parallel
2. 如果 batch > 10: 建议 wave-based，waveSize=2-3
3. 如果有复杂依赖: 建议 conservative
4. 评估风险等级: low/medium/high

返回 JSON:
{
  "mode": "full-parallel" | "wave-based" | "conservative",
  "waveSize": 2-4,
  "preCheckConflicts": boolean,
  "riskLevel": "low" | "medium" | "high",
  "reasoning": "简短理由"
}`,
    {
      label: "analyze-strategy",
      phase: "策略分析",
      effort: "low",
      schema: {
        type: "object",
        properties: {
          mode: { enum: ["full-parallel", "wave-based", "conservative"] },
          waveSize: { type: "number", minimum: 1, maximum: 8 },
          preCheckConflicts: { type: "boolean" },
          riskLevel: { enum: ["low", "medium", "high"] },
          reasoning: { type: "string" }
        },
        required: ["mode", "riskLevel", "reasoning"]
      }
    }
  );
  
  if (strategyDecision) {
    strategy = { ...strategy, ...strategyDecision };
    log(`智能策略: ${strategy.mode}, 风险: ${strategy.riskLevel}`);
    log(`理由: ${strategy.reasoning}`);
  }
}
```

#### 1.3 根据策略调整执行

```javascript
phase("并行实现");
let schedulerWaves = 0;

while (scheduledGroups.length > 0) {
  schedulerWaves += 1;
  if (schedulerWaves > MAX_SCHEDULER_WAVES) {
    return { error: "parallel_scheduler_wave_limit_exceeded", runId, schedulerWaves, batchResults, mergeResults };
  }
  
  // 🔥 根据策略决定本轮执行的 batch 数量
  let currentWave = scheduledGroups;
  
  if (strategy.mode === "conservative") {
    // 保守模式：每次只执行 1 个
    currentWave = scheduledGroups.slice(0, 1);
    log(`保守模式: 波次 ${schedulerWaves} 串行执行 ${currentWave.length} 个 batch`);
  } else if (strategy.mode === "wave-based") {
    // 波次模式：每次执行 waveSize 个
    const waveSize = strategy.waveSize || maxParallel;
    currentWave = scheduledGroups.slice(0, waveSize);
    log(`波次模式: 波次 ${schedulerWaves} 执行 ${currentWave.length}/${scheduledGroups.length} 个 batch (waveSize=${waveSize})`);
  } else {
    // 全并发模式：执行所有 ready 的 batch
    log(`全并发: 波次 ${schedulerWaves} 并发执行 ${currentWave.length} 个 batch`);
  }
  
  // 执行当前波次（保持原有逻辑）
  const executions = currentWave.map(([batchId]) => ({ batchId, runId }));
  
  const waveResults = await pipeline(executions, async execution => {
    return agent(
      `执行唯一 Batch ${execution.batchId}。Feature=${feature}，runId=${execution.runId}，插件路径=${pluginPath}，artifact workspace=${artifactWorkspace}。` +
      `从 manifest.batchWorkspaces 读取该 batch 的 workspaceRef、组件根目录和业务仓库，禁止选择其他仓库。\n` +
      `先获取 lease，再创建并行 worktree。在 worktree 中执行本 batch 的 task_runner start、finish-implementation、batch-compile，` +
      `所有调用携带 --parallel-run-id ${execution.runId} 和 lease token。\n` +
      `batch-compile 成功会自动回写 ready_to_merge；随后调用 "${pluginPath}/hooks/worktree_manager.py" seal 提交该 worktree。` +
      `失败时调用 "${pluginPath}/hooks/parallel_batch_scheduler.py" mark-batch failed；最后用 "${pluginPath}/hooks/batch_lease_manager.py" release --final-status ready_to_merge 释放 lease。` +
      `不要修改主工作区、不要解决冲突、不要删除 worktree。`,
      {
        label: `batch-${execution.batchId}`,
        phase: "并行实现",
        schema: BATCH_EXECUTION_SCHEMA
      }
    );
  });
  
  batchResults.push(...waveResults);
  const failed = waveResults.filter(item => !item || item.status !== "success");
  if (failed.length > 0) {
    return { error: "batch_execution_failed", runId, failed, batchResults };
  }
  
  // ... 后续合并和调度逻辑保持不变 ...
}
```

#### 1.4 决策点 2: 冲突预检测（可选启用）

在执行循环中增加：

```javascript
// 🔥 可选：冲突预检测（只在策略启用时执行）
if (strategy.preCheckConflicts && batchResults.length > 0 && currentWave.length > 1) {
  const conflictCheck = await agent(
    `快速检查即将执行的 batch 是否可能冲突。

已完成: ${batchResults.map(b => b.batchId).join(', ')} (共 ${batchResults.length} 个)
待执行: ${currentWave.map(([bid]) => bid).join(', ')} (本波 ${currentWave.length} 个)

从 manifest (runId=${runId}) 读取每个 batch 的 writeSet。
如果待执行 batch 的 writeSet 与已完成 batch 的 changedFiles 有重叠，返回 hasConflict=true。

只需简单的文件路径比较，不需要读取文件内容。`,
    {
      label: `conflict-precheck-wave${schedulerWaves}`,
      phase: "并行实现",
      effort: "low",
      schema: {
        type: "object",
        properties: {
          hasConflict: { type: "boolean" },
          conflictingBatches: { type: "array", items: { type: "string" } },
          overlappingFiles: { type: "array", items: { type: "string" } }
        },
        required: ["hasConflict"]
      }
    }
  );
  
  if (conflictCheck?.hasConflict && conflictCheck.conflictingBatches?.length > 0) {
    log(`⚠️  预检测到潜在冲突: ${conflictCheck.conflictingBatches.join(', ')}`);
    log(`重叠文件: ${(conflictCheck.overlappingFiles || []).slice(0, 3).join(', ')}${conflictCheck.overlappingFiles?.length > 3 ? '...' : ''}`);
    
    // 如果是全并发模式，降级为保守模式
    if (strategy.mode === "full-parallel") {
      log(`由全并发降级为串行执行`);
      strategy.mode = "conservative";
      currentWave = scheduledGroups.slice(0, 1);
    }
  }
}
```

#### 1.5 决策点 3: 智能冲突处理（可选启用）

在合并阶段增加：

```javascript
phase("顺序合并");
const mergeResult = await agent(
  `只执行确定性合并，不进行人工改写。执行 python "${pluginPath}/hooks/batch_merger.py" --workspace "${artifactWorkspace}" ` +
  `--feature "${feature}" --run-id "${runId}"。合并器必须从 manifest 对每个 batch 选择绑定的 Git 根。` +
  `主工作区变化、planDigest 漂移或 Git 冲突时立即停止；禁止 --ours、--theirs 和手动编辑冲突文件。`,
  { label: "merge-batches", phase: "顺序合并", schema: MERGE_RESULT_SCHEMA }
);

if (!mergeResult || !mergeResult.success) {
  if (!mergeResult?.needsResolution) {
    return { error: "merge_failed", runId, mergeResult: mergeResult || null, batchResults };
  }
  
  // 🔥 新增：智能冲突分析（只在策略允许时执行）
  const resolutionTargets = (mergeResult.failed || []).filter(item => item && item.needsResolution);
  
  let useAutoResolve = false;
  if (strategy.conflictStrategy === "auto-resolve" && resolutionTargets.length > 0) {
    const conflictAnalysis = await agent(
      `快速评估冲突是否可以自动解决。

冲突概况:
- 冲突 batch 数: ${resolutionTargets.length}
- 冲突文件总数: ${resolutionTargets.reduce((sum, t) => sum + (t.conflicts?.length || 0), 0)}

只需判断:
1. 冲突文件是否 < 5 个
2. 是否都是常见的业务代码冲突（非配置、非构建文件）
3. 置信度评估

返回 { canAutoResolve: boolean, confidence: "high"|"medium"|"low", reasoning: string }`,
      {
        label: "analyze-conflicts",
        phase: "顺序合并",
        effort: "low",
        schema: {
          type: "object",
          properties: {
            canAutoResolve: { type: "boolean" },
            confidence: { enum: ["high", "medium", "low"] },
            reasoning: { type: "string" }
          },
          required: ["canAutoResolve", "reasoning"]
        }
      }
    );
    
    if (conflictAnalysis?.canAutoResolve && conflictAnalysis.confidence === "high") {
      log(`冲突分析: ${conflictAnalysis.reasoning}`);
      log(`尝试自动解决冲突 (置信度: ${conflictAnalysis.confidence})`);
      useAutoResolve = true;
    } else {
      log(`冲突需要人工处理: ${conflictAnalysis?.reasoning || 'unknown'}`);
    }
  }
  
  // 执行冲突解决（保持原有逻辑，只是增加了 auto-resolve 的标记）
  const resolutionResults = await pipeline(resolutionTargets, async item => agent(
    `${useAutoResolve ? '自动' : ''}处理 Code 合并冲突。Feature=${feature}，runId=${runId}，Batch=${item.batchId}。` +
    `读取 manifest 中 resolution.worktreePath，在该 Worktree 执行 git status、git diff、git diff --cc，` +
    `同时阅读该 Batch 与冲突来源 Batch 的 goal、implementation Evidence 和提交 diff。` +
    `只解决 Git 标记的冲突文件及实现所必需的适配；禁止使用 git checkout --ours/--theirs、git merge -s ours、--no-verify、删除一侧变更或直接改主工作区。` +
    `按两个 Batch 的业务目标保留兼容行为，解决后运行该 Batch 的 required compile 命令。` +
    `然后执行 python "${pluginPath}/hooks/parallel_conflict_resolver.py" complete --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${item.batchId}"，` +
    `返回冲突文件、解决理由、验证输出摘要和 resolutionCommitSha。`,
    { label: `resolve-conflict-${item.batchId}`, phase: "顺序合并", schema: { type: "object" } }
  ));
  
  // ... 后续合并逻辑保持不变 ...
}
```

---

### Step 2: 创建增强版 Workflow

将修改后的 workflow 保存为新文件，保留原版本作为回退。

```bash
# 备份原版本
cp workflows/code-batched-execution.workflow.js \
   workflows/code-batched-execution-legacy.workflow.js

# 应用增强版本
# （手动应用上述修改，或使用下面提供的完整文件）
```

---

### Step 3: 更新 Launcher

修改 `hooks/workflow_launcher.py`，支持传递策略参数：

```python
# hooks/workflow_launcher.py 新增

def decide_strategy(bundle: PlanBundle, config: dict) -> dict:
    """简单规则决定初始策略"""
    batches = [b for b in bundle.root.get("batches", []) 
               if b.get("status") not in {"done", "failed"}]
    batch_count = len(batches)
    
    # 规则 1: 少量 batch，全并发
    if batch_count <= 3:
        return {
            "mode": "full-parallel",
            "preCheckConflicts": False,
            "conflictStrategy": "stop",
            "riskLevel": "low",
            "reasoning": f"只有 {batch_count} 个 batch，全并发执行"
        }
    
    # 规则 2: 中等数量，让模型决定
    if batch_count <= 8:
        return {
            "mode": "auto",  # 让 workflow 中的模型分析
            "preCheckConflicts": True,
            "conflictStrategy": "stop",
            "riskLevel": "medium",
            "reasoning": "中等数量 batch，由模型分析决定策略"
        }
    
    # 规则 3: 大量 batch，保守模式
    return {
        "mode": "wave-based",
        "waveSize": 2,
        "preCheckConflicts": True,
        "conflictStrategy": "stop",
        "riskLevel": "high",
        "reasoning": f"较多 batch ({batch_count} 个)，使用波次模式"
    }

# 在 main() 函数中
def main(args):
    # ... 现有逻辑 ...
    
    if use_workflow:
        bundle = load_plan_bundle(feature_dir)
        strategy = decide_strategy(bundle, config)
        
        return {
            "useWorkflow": True,
            "strategy": strategy,  # 🔥 新增
            "artifactWorkspace": artifact_workspace,
            "codeWorkspaces": code_workspaces,
            # ... 其他参数 ...
        }
```

---

### Step 4: 更新 SKILL.md

在 `skills/autodev/autodev-code/SKILL.md` 的 workflow 部分增加说明：

```markdown
## Workflow 并行执行模式

当 Code 阶段存在两个或更多合法待执行 Batch 时，先调用 `hooks/workflow_launcher.py`：

```bash
launcher_result=$(python "${pluginPath}/hooks/workflow_launcher.py" \
  --feature "${feature}" \
  --plugin-path "${pluginPath}" \
  --workspace "${pluginWorkspace}/${projectDir}" \
  --json)
useWorkflow=$(printf '%s' "$launcher_result" | jq -r '.useWorkflow')
strategy=$(printf '%s' "$launcher_result" | jq -r '.strategy // empty')
```

launcher 会返回:
- `useWorkflow`: 是否使用 workflow
- `strategy`: 执行策略配置（可选）
  - `mode`: "auto" | "full-parallel" | "wave-based" | "conservative"
  - `preCheckConflicts`: 是否预检测冲突
  - `conflictStrategy`: "stop" | "continue" | "auto-resolve"

启动 workflow 时传入 strategy:

```javascript
{
  "feature": "${feature}",
  "pluginPath": "${pluginPath}",
  "artifactWorkspace": "${artifactWorkspace}",
  "codeWorkspaces": {...},
  "strategy": ${strategy}  // 🔥 新增策略参数
}
```

### 执行策略说明

- **auto**: 由 workflow 中的模型分析决定最优策略
- **full-parallel**: 全并发执行所有 ready 的 batch
- **wave-based**: 分波次执行，每波 waveSize 个 batch
- **conservative**: 保守串行，每次只执行 1 个 batch
```

---

## 🧪 测试计划

### Phase 1: 单元测试（本周）

```bash
# 1. 测试策略决策逻辑
pytest tests/test_workflow_strategy.py -v

# 2. 测试 workflow 语法
node --check workflows/code-batched-execution.workflow.js

# 3. 测试 launcher 集成
python tests/test_workflow_launcher.py
```

### Phase 2: 集成测试（下周）

```bash
# 准备测试 feature
python hooks/parallel_batch_scheduler.py create \
  --feature test-hybrid \
  --max-parallel 4 \
  --code-workspace /path/to/repo

# 测试不同策略
for mode in auto full-parallel wave-based conservative; do
  echo "Testing mode: $mode"
  # 运行 workflow with strategy.mode=$mode
done
```

### Phase 3: 金丝雀发布（第 3 周）

```python
# 10% 流量使用新 workflow
if random.random() < 0.1:
    use_enhanced = True
else:
    use_enhanced = False
```

---

## 📊 监控指标

### 关键指标

1. **策略分布**
   - auto → full-parallel: X%
   - auto → wave-based: Y%
   - auto → conservative: Z%

2. **执行效率**
   - 平均执行时间
   - 冲突率
   - 自动解决成功率

3. **稳定性**
   - 首次成功率
   - 回滚次数
   - 错误率

### 监控实现

```python
# hooks/parallel_runtime.py 增加
def record_strategy_metrics(workspace: Path, feature: str, run_id: str, strategy: dict):
    """记录策略使用指标"""
    metrics = {
        "timestamp": utc_now(),
        "runId": run_id,
        "strategy": strategy,
        "batchCount": 0,  # 从 manifest 读取
        "executionTime": 0,  # 计算
        "conflictCount": 0,  # 从 manifest 读取
        "success": False,  # 从 manifest 读取
    }
    
    metrics_file = workspace / ".autobizdevops" / "features" / feature / ".strategy-metrics.jsonl"
    with metrics_file.open("a") as f:
        f.write(json.dumps(metrics) + "\n")
```

---

## 🔄 回滚方案

### 立即回滚

```python
# hooks/workflow_launcher.py
USE_ENHANCED_WORKFLOW = False  # 🔥 设置为 False 立即回滚

if USE_ENHANCED_WORKFLOW:
    workflow_name = "code-batched-execution"
else:
    workflow_name = "code-batched-execution-legacy"
```

### 渐进回滚

```python
# 逐步减少新 workflow 的使用比例
ENHANCED_WORKFLOW_RATIO = 0.5  # 从 1.0 降到 0.5 → 0.2 → 0.0
```

---

## 📝 迁移检查清单

### 准备阶段 ✅
- [x] 完成代码检视
- [x] 设计方案评审
- [x] 创建测试用例
- [x] 编写文档

### 实施阶段 🚧
- [ ] 修改 workflow 文件（应用上述改动）
- [ ] 修改 launcher（支持策略参数）
- [ ] 更新 SKILL.md
- [ ] 运行单元测试
- [ ] 运行集成测试

### 发布阶段 🔜
- [ ] 金丝雀发布 (10%)
- [ ] 监控指标收集
- [ ] 逐步扩大 (30% → 50% → 100%)
- [ ] 正式发布

### 验证阶段 🔜
- [ ] 7 天稳定性观察
- [ ] 用户反馈收集
- [ ] 性能对比分析
- [ ] 文档完善

---

## 🎯 成功标准

- ✅ 向后兼容：不传 strategy 参数时，行为与旧版本一致
- ✅ 性能提升：平均执行时间减少 20%+
- ✅ 冲突处理：自动解决率 > 40%
- ✅ 稳定性：首次成功率 > 85%
- ✅ 可观测性：所有决策都有日志记录

---

## 📚 参考文档

- [当前固定 Workflow](../../../workflows/code-batched-execution.workflow.js)
- [迁移指南](./workflow-migration-guide.md)
- [快速开始](./adaptive-workflow-quickstart.md)
- [代码检视](./summary-workflow-review.md)

---

## 🚀 立即开始

### 最小可行实施（1 小时）

1. **只增加策略参数支持**（不启用模型决策）
   ```javascript
   const strategy = args.strategy || { mode: "full-parallel" };
   ```

2. **根据策略调整执行**（Wave-based 支持）
   ```javascript
   if (strategy.mode === "wave-based") {
     currentWave = scheduledGroups.slice(0, strategy.waveSize);
   }
   ```

3. **Launcher 传递策略**
   ```python
   strategy = { "mode": "full-parallel" }  # 先固定
   ```

这样可以立即获得：
- ✅ 策略参数化能力
- ✅ Wave-based 执行模式
- ✅ 向后兼容
- ⏱️ 只需 1 小时实施

### 完整实施（1 周）

按照上述 Step 1-4 完整实施，获得全部智能决策能力。

---

**你想从哪里开始？**

1. **最小实施** - 1 小时快速获得 wave-based 能力
2. **完整实施** - 1 周获得全部智能决策
3. **先看示例** - 我提供完整的修改后文件

请告诉我你的选择！ 🎯
