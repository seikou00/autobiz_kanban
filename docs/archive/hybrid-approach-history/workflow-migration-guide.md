# Workflow Migration Guide: 从静态到动态（已归档）

> 本文是旧迁移路线。模型生成 workflow 已退役；当前执行入口是
> `workflows/code-batched-execution.workflow.js`，不得按本文命令操作。

## 阶段 1：最小改动（1-2 天）

### 目标
在现有 workflow 中增加参数化能力，无需重写整个脚本。

### 实现

#### 1. 扩展 workflow args

```javascript
// workflows/code-batched-execution.workflow.js

// 现有
const maxParallel = Number.isInteger(args.maxParallel) && args.maxParallel > 0
  ? args.maxParallel
  : MAX_PARALLEL_BATCHES;

// 新增：接受策略参数
const strategy = args.strategy || {
  mode: 'full-parallel',        // full-parallel | wave-based | adaptive
  waveSize: maxParallel,
  earlyMerge: false,            // 每波次后是否立即合并
  conflictStrategy: 'stop',     // stop | continue | auto-resolve
  preCheckConflicts: false      // 执行前是否预检测冲突
};

log(`执行策略: ${strategy.mode}, waveSize=${strategy.waveSize}`);
```

#### 2. 修改执行循环

```javascript
// 原有逻辑
phase("并行实现");
let schedulerWaves = 0;
while (scheduledGroups.length) {
  schedulerWaves += 1;
  if (schedulerWaves > MAX_SCHEDULER_WAVES) {
    return { error: "parallel_scheduler_wave_limit_exceeded", ... };
  }
  
  const executions = scheduledGroups.map(([batchId]) => ({ batchId, runId }));
  const waveResults = await pipeline(executions, async execution => { ... });
  
  // ... 合并逻辑
}

// 改为支持策略的版本
phase("并行实现");
let schedulerWaves = 0;

while (scheduledGroups.length) {
  schedulerWaves += 1;
  if (schedulerWaves > MAX_SCHEDULER_WAVES) {
    return { error: "parallel_scheduler_wave_limit_exceeded", runId, schedulerWaves, batchResults, mergeResults };
  }
  
  // 🔥 新增：根据策略决定本轮执行的 batch 数量
  let currentWave = scheduledGroups;
  if (strategy.mode === 'wave-based') {
    currentWave = scheduledGroups.slice(0, strategy.waveSize);
    scheduledGroups = scheduledGroups.slice(strategy.waveSize);
  }
  
  // 🔥 新增：冲突预检测
  if (strategy.preCheckConflicts && batchResults.length > 0) {
    const conflictCheck = await agent(
      `检查即将执行的 ${currentWave.length} 个 batch 是否会与已完成的 ${batchResults.length} 个 batch 冲突。` +
      `已完成: ${batchResults.map(b => b.batchId).join(', ')}，` +
      `待执行: ${currentWave.map(([bid]) => bid).join(', ')}。` +
      `从 manifest 读取 writeSet 进行分析。`,
      { label: "conflict-precheck", phase: "并行实现", schema: { type: "object", properties: { hasConflict: { type: "boolean" }, conflictingBatches: { type: "array" } } } }
    );
    
    if (conflictCheck?.hasConflict) {
      log(`检测到潜在冲突: ${JSON.stringify(conflictCheck.conflictingBatches)}，将串行执行`);
      currentWave = currentWave.slice(0, 1);  // 降级为串行
    }
  }
  
  const executions = currentWave.map(([batchId]) => ({ batchId, runId }));
  const waveResults = await pipeline(executions, async execution => { ... });
  
  batchResults.push(...waveResults);
  const failed = waveResults.filter(item => !item || item.status !== "success");
  if (failed.length) {
    return { error: "batch_execution_failed", runId, failed, batchResults };
  }

  phase("顺序合并");
  const mergeResult = await agent(/* ... 同原有逻辑 ... */);
  
  if (!mergeResult || !mergeResult.success) {
    // 🔥 新增：根据策略决定是否继续
    if (strategy.conflictStrategy === 'stop' || !mergeResult?.needsResolution) {
      return { error: "merge_failed", runId, mergeResult: mergeResult || null, batchResults };
    }
    
    // 解决冲突的逻辑（同原有）
    const resolutionTargets = (mergeResult.failed || []).filter(item => item && item.needsResolution);
    const resolutionResults = await pipeline(resolutionTargets, async item => agent(/* ... */));
    // ...
  } else {
    mergeResults.push(mergeResult);
  }

  // 🔥 新增：非 wave-based 模式下，一次性调度所有剩余 batch
  if (strategy.mode === 'full-parallel') {
    const resumed = await agent(/* ... resume scheduler ... */);
    scheduledGroups = resumed?.scheduledGroups || [];
    if (!scheduledGroups.length && !["verifying", "succeeded"].includes(resumed?.status)) {
      return { error: "parallel_scheduler_stalled", runId, scheduler: resumed || null, batchResults, mergeResults };
    }
  } else {
    // wave-based 模式下，继续执行下一波
    if (!scheduledGroups.length) {
      const resumed = await agent(/* ... resume scheduler ... */);
      scheduledGroups = resumed?.scheduledGroups || [];
    }
  }
}
```

#### 3. 修改 launcher 传递策略

```python
# skills/autodev/autodev-code/launcher.py

def decide_strategy(bundle: PlanBundle) -> dict:
    """简单规则决定策略"""
    batch_count = len([b for b in bundle.root.get("batches", []) if b.get("status") not in {"done", "failed"}])
    
    # 简单规则
    if batch_count <= 3:
        return {"mode": "full-parallel"}
    
    # 检查是否有深度依赖
    max_depth = calculate_max_dependency_depth(bundle)
    if max_depth > 2:
        return {
            "mode": "wave-based",
            "waveSize": 2,
            "earlyMerge": True,
            "conflictStrategy": "stop"
        }
    
    return {
        "mode": "full-parallel",
        "preCheckConflicts": batch_count > 5
    }

def launch_parallel_code(workspace, feature, ...):
    bundle = load_plan_bundle(feature_dir)
    strategy = decide_strategy(bundle)
    
    return call_workflow(
        name="code-batched-execution",
        args={
            "feature": feature,
            # ... 其他参数
            "strategy": strategy  # 🔥 新增
        }
    )
```

### 测试

```bash
# 测试 full-parallel 模式（默认）
python hooks/parallel_batch_scheduler.py create --feature test --max-parallel 4

# 测试 wave-based 模式
# 需要修改 launcher 传递 strategy 参数
```

---

## 阶段 2：模板库（1 周）

### 目标
为常见场景创建专门优化的 workflow 模板。

### 实现

#### 1. 创建模板

```javascript
// workflows/simple-serial.workflow.js
export const meta = {
  name: "code-simple-serial",
  description: "简单串行执行，适合 < 5 个 batch",
  phases: [
    { title: "准备" },
    { title: "串行执行" },
    { title: "验证" }
  ]
};

const batches = args.batches || [];  // launcher 传入需要执行的 batch 列表

for (const batchId of batches) {
  const result = await agent(`执行 batch ${batchId}`, { ... });
  if (!result.success) {
    return { error: "batch_failed", batchId, result };
  }
  
  // 立即合并
  const merged = await agent(`合并 batch ${batchId}`, { ... });
  if (!merged.success) {
    return { error: "merge_failed", batchId, merged };
  }
}

return { ok: true, completed: batches };
```

```javascript
// workflows/parallel-independent.workflow.js
export const meta = {
  name: "code-parallel-independent",
  description: "完全并行执行，适合无依赖场景",
  phases: [
    { title: "准备" },
    { title: "并行执行" },
    { title: "批量合并" },
    { title: "验证" }
  ]
};

const batches = args.batches || [];

// 一次性并发执行所有 batch
const results = await parallel(
  batches.map(batchId => () => agent(`执行 batch ${batchId}`, { ... }))
);

// 检查失败
const failed = results.filter(r => !r || !r.success);
if (failed.length) {
  return { error: "batches_failed", failed };
}

// 批量合并（按依赖顺序）
const merged = await agent(`批量合并所有 batch`, { ... });

return { ok: true, results, merged };
```

#### 2. 创建选择器

```python
# hooks/workflow_selector.py

from enum import Enum
from pathlib import Path
from typing import Dict, Any

class WorkflowTemplate(Enum):
    SIMPLE_SERIAL = "simple-serial"
    PARALLEL_INDEPENDENT = "parallel-independent"
    DAG_DEPENDENT = "dag-dependent"
    MULTI_REPO = "multi-repo"

def select_workflow(bundle: PlanBundle) -> tuple[WorkflowTemplate, Dict[str, Any]]:
    """选择最合适的 workflow 模板"""
    
    batches = [b for b in bundle.root.get("batches", []) if b.get("status") not in {"done", "failed"}]
    batch_count = len(batches)
    
    # 规则 1：少量 batch，使用串行
    if batch_count <= 3:
        return WorkflowTemplate.SIMPLE_SERIAL, {
            "batches": [b["id"] for b in batches]
        }
    
    # 规则 2：检查依赖关系
    has_deps = any(b.get("deps") for b in batches)
    
    if not has_deps:
        return WorkflowTemplate.PARALLEL_INDEPENDENT, {
            "batches": [b["id"] for b in batches],
            "maxParallel": min(batch_count, 4)
        }
    
    # 规则 3：检查多仓库
    workspace_refs = set()
    for batch_id, batch in bundle.batches.items():
        ref = batch_workspace_ref(batch)
        if ref:
            workspace_refs.add(ref)
    
    if len(workspace_refs) > 1:
        return WorkflowTemplate.MULTI_REPO, {
            "repositories": list(workspace_refs)
        }
    
    # 默认：DAG 依赖模式
    return WorkflowTemplate.DAG_DEPENDENT, {
        "maxParallel": 4
    }

def get_workflow_path(template: WorkflowTemplate) -> str:
    """获取 workflow 文件路径"""
    base = Path(__file__).parent.parent / "workflows"
    mapping = {
        WorkflowTemplate.SIMPLE_SERIAL: "simple-serial.workflow.js",
        WorkflowTemplate.PARALLEL_INDEPENDENT: "parallel-independent.workflow.js",
        WorkflowTemplate.DAG_DEPENDENT: "code-batched-execution.workflow.js",
        WorkflowTemplate.MULTI_REPO: "multi-repo.workflow.js",
    }
    return str(base / mapping[template])
```

#### 3. 集成到 launcher

```python
# skills/autodev/autodev-code/launcher.py

from hooks.workflow_selector import select_workflow, get_workflow_path

def launch_parallel_code(workspace, feature, ...):
    bundle = load_plan_bundle(feature_dir)
    
    # 🔥 选择 workflow 模板
    template, template_args = select_workflow(bundle)
    workflow_path = get_workflow_path(template)
    
    logger.info(f"选择 workflow 模板: {template.value}")
    
    # 合并参数
    workflow_args = {
        "feature": feature,
        "pluginPath": plugin_path,
        "artifactWorkspace": workspace,
        "codeWorkspaces": code_workspaces,
        **template_args  # 模板特定参数
    }
    
    return call_workflow(
        scriptPath=workflow_path,
        args=workflow_args
    )
```

---

## 阶段 3：模型决策点（2-3 周）

### 目标
在关键位置引入模型决策，保持执行逻辑稳定。

### 实现

#### 决策点 1：执行前分析

```javascript
// workflows/adaptive-execution.workflow.js

phase("智能分析");

const analysis = await agent(
  `分析 plan 并给出执行建议。

Plan 概况:
- Total batches: ${preparation.readyBatches.length}
- Dependencies: ${JSON.stringify(manifest.batches)}
- Repositories: ${Object.keys(manifest.repositories)}

历史数据（如果有）:
- 上次执行冲突率: X%
- 平均执行时间: X 分钟

请给出:
1. 推荐的并发度 (1-8)
2. 是否分波次执行
3. 是否需要冲突预检测
4. 预估的执行时间和风险`,
  { 
    label: "analyze-plan", 
    phase: "智能分析",
    effort: "medium",
    schema: {
      type: "object",
      properties: {
        recommendedParallel: { type: "number", minimum: 1, maximum: 8 },
        useWaves: { type: "boolean" },
        waveSize: { type: "number" },
        preCheckConflicts: { type: "boolean" },
        estimatedMinutes: { type: "number" },
        riskLevel: { enum: ["low", "medium", "high"] },
        reasoning: { type: "string" }
      },
      required: ["recommendedParallel", "useWaves", "riskLevel", "reasoning"]
    }
  }
);

log(`分析结果: ${analysis.reasoning}`);
log(`风险等级: ${analysis.riskLevel}, 预计 ${analysis.estimatedMinutes} 分钟`);

// 应用分析结果
const maxParallel = analysis.recommendedParallel;
const strategy = {
  mode: analysis.useWaves ? 'wave-based' : 'full-parallel',
  waveSize: analysis.waveSize || maxParallel,
  preCheckConflicts: analysis.preCheckConflicts
};
```

#### 决策点 2：冲突处理策略

```javascript
phase("智能冲突处理");

if (mergeResult?.needsResolution) {
  const conflictAnalysis = await agent(
    `分析冲突并给出处理建议。

冲突详情:
${JSON.stringify(mergeResult.failed, null, 2)}

涉及的 batch:
${resolutionTargets.map(t => `- ${t.batchId}: ${t.conflicts?.length || 0} 个文件冲突`).join('\n')}

请分析:
1. 冲突的性质（简单/复杂）
2. 是否可以自动解决
3. 推荐的解决策略
4. 预估解决时间`,
    {
      label: "analyze-conflicts",
      phase: "智能冲突处理",
      effort: "high",
      schema: {
        type: "object",
        properties: {
          complexity: { enum: ["simple", "medium", "complex"] },
          autoResolvable: { type: "boolean" },
          strategy: { enum: ["auto", "semi-auto", "manual"] },
          estimatedMinutes: { type: "number" },
          reasoning: { type: "string" }
        },
        required: ["complexity", "autoResolvable", "strategy", "reasoning"]
      }
    }
  );
  
  log(`冲突复杂度: ${conflictAnalysis.complexity}`);
  log(`处理策略: ${conflictAnalysis.strategy}`);
  
  if (conflictAnalysis.autoResolvable && conflictAnalysis.strategy === "auto") {
    // 自动解决
    const autoResolved = await pipeline(
      resolutionTargets,
      async item => agent(
        `自动解决 ${item.batchId} 的冲突。
        
策略: 优先保留后执行的 batch 的更改，但保持功能完整性。
必须确保编译通过。`,
        { label: `auto-resolve-${item.batchId}`, schema: RESOLUTION_SCHEMA }
      )
    );
  } else {
    // 人工介入（同原有逻辑）
  }
}
```

#### 决策点 3：失败恢复

```javascript
// 在执行循环中增加智能重试

const waveResults = await pipeline(executions, async execution => {
  let attempts = 0;
  let lastError = null;
  
  while (attempts < 3) {
    attempts += 1;
    
    const result = await agent(/* 执行 batch */);
    
    if (result?.status === "success") {
      return result;
    }
    
    lastError = result;
    
    // 🔥 决策：是否重试
    if (attempts < 3) {
      const shouldRetry = await agent(
        `Batch ${execution.batchId} 执行失败。
        
错误: ${result?.errorMessage || 'unknown'}
尝试次数: ${attempts}/3

是否应该重试？考虑：
1. 错误是否是瞬时的（网络、资源）
2. 重试是否可能成功
3. 是否需要调整策略`,
        {
          label: `retry-decision-${execution.batchId}`,
          effort: "low",
          schema: {
            type: "object",
            properties: {
              shouldRetry: { type: "boolean" },
              waitSeconds: { type: "number" },
              reasoning: { type: "string" }
            },
            required: ["shouldRetry", "reasoning"]
          }
        }
      );
      
      if (shouldRetry?.shouldRetry) {
        log(`${execution.batchId} 将在 ${shouldRetry.waitSeconds || 5} 秒后重试: ${shouldRetry.reasoning}`);
        await new Promise(resolve => setTimeout(resolve, (shouldRetry.waitSeconds || 5) * 1000));
        continue;
      }
    }
    
    break;
  }
  
  return lastError;
});
```

---

## 阶段 4：完全生成（长期）

### 架构

```
Launcher
  ↓
Workflow Generator Agent (Opus)
  ├─ 读取 plan bundle
  ├─ 分析历史数据
  ├─ 生成定制 workflow 脚本
  └─ 验证脚本正确性
  ↓
执行生成的 workflow
  ↓
收集执行数据（成功/失败/耗时）
  ↓
反馈到下次生成
```

### 关键组件

#### 1. Workflow 生成器

```python
# hooks/workflow_generator.py

WORKFLOW_TEMPLATE = """
export const meta = {
  name: "{name}",
  description: "{description}",
  phases: {phases}
};

{body}
"""

def generate_workflow(
    bundle: PlanBundle,
    history: Optional[ExecutionHistory] = None
) -> str:
    """使用模型生成 workflow 脚本"""
    
    prompt = f"""
你是一个 workflow 脚本生成专家。根据以下信息生成优化的并行执行脚本。

## Plan 分析

{format_plan_analysis(bundle)}

## 历史执行数据

{format_history(history) if history else "无历史数据"}

## 要求

1. 生成完整的 JavaScript ES module
2. 必须包含 `export const meta = {{...}}`
3. 使用 pipeline() 而不是 parallel()，除非确实需要 barrier
4. 包含详细的错误处理
5. 添加适当的 log() 调用
6. 使用 phase() 组织执行阶段

## 可用 API

- agent(prompt, options): 执行子任务
- pipeline(items, ...stages): 流水线执行
- parallel(thunks): 并行执行（慎用）
- phase(title): 切换执行阶段
- log(message): 输出日志
- args: 输入参数
- budget: token 预算

## 输出格式

返回完整的 JavaScript 代码，不要包含 markdown 代码块标记。
"""
    
    # 调用模型生成
    generated = call_model(
        prompt,
        model="opus",
        max_tokens=8000
    )
    
    return generated
```

#### 2. 脚本验证器

```python
# hooks/workflow_validator.py

def validate_workflow_script(script: str) -> list[str]:
    """验证生成的 workflow 脚本"""
    errors = []
    
    # 1. 语法检查
    try:
        result = subprocess.run(
            ["node", "--check"],
            input=script,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            errors.append(f"syntax_error: {result.stderr}")
    except Exception as e:
        errors.append(f"syntax_check_failed: {e}")
    
    # 2. 结构检查
    if "export const meta" not in script:
        errors.append("missing_meta_export")
    
    if "name:" not in script:
        errors.append("missing_meta_name")
    
    # 3. API 使用检查
    dangerous_patterns = [
        (r"parallel\([^)]*\)", "使用了 parallel()，建议改用 pipeline()"),
        (r"await\s+await", "重复的 await"),
        (r"while\s*\(true\)", "无限循环"),
    ]
    
    for pattern, message in dangerous_patterns:
        if re.search(pattern, script):
            errors.append(f"suspicious_pattern: {message}")
    
    return errors
```

#### 3. 执行历史收集

```python
# hooks/execution_history.py

@dataclass
class ExecutionHistory:
    feature: str
    run_id: str
    batch_count: int
    execution_time_seconds: float
    conflict_count: int
    conflict_rate: float
    success: bool
    strategy_used: dict
    
    def to_summary(self) -> str:
        return f"""
- Batches: {self.batch_count}
- 执行时间: {self.execution_time_seconds:.1f}s
- 冲突: {self.conflict_count} ({self.conflict_rate:.1%})
- 成功: {'是' if self.success else '否'}
- 策略: {self.strategy_used.get('mode', 'unknown')}
"""

def save_execution_history(workspace: Path, feature: str, run_id: str, manifest: dict):
    """保存执行历史供下次参考"""
    history = ExecutionHistory(
        feature=feature,
        run_id=run_id,
        batch_count=len(manifest["batches"]),
        execution_time_seconds=calculate_duration(manifest),
        conflict_count=count_conflicts(manifest),
        conflict_rate=calculate_conflict_rate(manifest),
        success=manifest["status"] == "succeeded",
        strategy_used=manifest.get("strategy", {})
    )
    
    history_file = workspace / ".autobizdevops" / "features" / feature / ".execution-history.jsonl"
    with history_file.open("a") as f:
        f.write(json.dumps(asdict(history)) + "\n")

def load_recent_history(workspace: Path, feature: str, limit: int = 5) -> list[ExecutionHistory]:
    """加载最近的执行历史"""
    # ...
```

---

## 测试计划

### 阶段 1 测试

```bash
# 测试不同策略
pytest tests/test_workflow_strategy.py -v

# 集成测试
python -m hooks.parallel_batch_scheduler create \
  --feature test-strategy \
  --max-parallel 4 \
  --code-workspace /path/to/repo
```

### 阶段 2 测试

```bash
# 测试模板选择
pytest tests/test_workflow_selector.py -v

# 测试每个模板
for template in simple-serial parallel-independent dag-dependent; do
  python -m hooks.workflow_launcher test-$template
done
```

### 阶段 3 测试

```bash
# 测试决策点
pytest tests/test_workflow_decisions.py -v

# 端到端测试
./scripts/e2e-test-adaptive-workflow.sh
```

### 阶段 4 测试

```bash
# 测试生成器
pytest tests/test_workflow_generator.py -v

# 测试验证器
pytest tests/test_workflow_validator.py -v

# 当前固定 workflow 验证
node --check workflows/code-batched-execution.workflow.js
```

---

## 回滚计划

每个阶段都保持向后兼容：

- 阶段 1：如果 strategy 参数缺失，使用默认行为
- 阶段 2：如果模板选择失败，回退到 dag-dependent
- 阶段 3：如果决策失败，使用固定策略
- 阶段 4：如果生成失败，回退到模板库

---

## 监控指标

- 执行成功率
- 平均执行时间
- 冲突率
- 重试次数
- 模型决策准确率（需要人工标注）
- workflow 生成成功率

---

## 总结

推荐路径：
1. **本周**：实现阶段 1（最小改动）
2. **下周**：实现阶段 2（模板库）
3. **本月**：逐步引入阶段 3 的决策点
4. **下个季度**：评估是否需要阶段 4

关键原则：
- **渐进式**：每个阶段都是独立可用的
- **可回退**：保持向后兼容
- **可观测**：收集数据指导优化
- **稳定性优先**：核心执行逻辑尽量固定
