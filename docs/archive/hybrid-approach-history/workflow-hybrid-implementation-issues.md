# 方案 3 实施方案问题分析与修正

> 历史问题记录。本文对应的混合和 generated workflow 方案均已退役；当前执行
> 入口是 `workflows/code-batched-execution.workflow.js`。

## 🔍 发现的问题

### 问题 1: workflow_launcher.py 不调用 Workflow 工具 ⚠️

**现状**：
- `workflow_launcher.py` 只是一个**分析器**，返回决策结果（JSON）
- 它**不负责实际调用** Workflow 工具
- 真正调用 Workflow 的是上层（可能是 SKILL.md 或其他 launcher）

**影响**：
- 我的快速实施指南中 Step 3 的修改位置**错误**
- 需要找到真正调用 Workflow 的位置

### 问题 2: 参数传递链路不清晰 ⚠️

**当前链路**：
```
??? (上层调用者)
  → workflow_launcher.py (返回 JSON 决策)
    → ??? (谁调用 Workflow 工具?)
      → code-batched-execution.workflow.js
```

**缺失**：
- 不知道谁负责调用 Workflow 工具
- 不知道如何将 strategy 参数传递到 workflow

### 问题 3: 快速实施指南的 Step 3 不可行 ❌

**问题代码位置**：
`docs/workflow-hybrid-quick-start.md` 第 127-182 行

**错误原因**：
- workflow_launcher.py 是纯分析函数
- 没有调用 Workflow 工具的代码
- 建议的修改无法生效

## 🔧 需要的信息

### 1. 找到真正的 Workflow 调用者

需要检查：
- `skills/autodev/autodev-code/SKILL.md` 
- 是否有其他 Python 脚本调用 Workflow 工具
- 是否有 shell 脚本或其他入口

### 2. 确认参数传递方式

需要了解：
- 上层如何传递参数给 Workflow
- args 参数如何构造
- 是否已经有 strategy 相关的传递机制

## ✅ 修正方案

### 方案 A: 找到真正的调用点（推荐）

**步骤**：
1. 搜索调用 Workflow 工具的位置
2. 在那里增加策略决策逻辑
3. 传递 strategy 参数到 workflow

### 方案 B: 修改 workflow_launcher.py 返回策略建议

**步骤**：
1. workflow_launcher.py 在返回的 JSON 中增加 `recommendedStrategy` 字段
2. 上层调用者读取这个字段
3. 将其作为 args.strategy 传递给 workflow

**示例修改**：

```python
# workflow_launcher.py 的 analyze_batches 函数返回值增加：
return {
    "useWorkflow": True,
    "strategy": "parallel",
    "batchCount": batch_count,
    "batches": valid_batches,
    "workflowScript": str(workflow_script),
    # 🔥 新增：推荐的执行策略
    "recommendedStrategy": _recommend_strategy(batch_count, valid_batches, bundle),
    ...
}

def _recommend_strategy(batch_count: int, batches: list, bundle: Any) -> dict:
    """推荐执行策略"""
    # 简单规则
    if batch_count <= 3:
        return {
            "mode": "full-parallel",
            "riskLevel": "low",
            "reasoning": f"只有 {batch_count} 个 batch，全并发执行"
        }
    elif batch_count <= 8:
        # 检查依赖关系
        has_deps = any(b.get("deps") for b in batches)
        if has_deps:
            return {
                "mode": "wave-based",
                "waveSize": 2,
                "riskLevel": "medium",
                "reasoning": "有依赖关系，使用波次模式"
            }
        else:
            return {
                "mode": "full-parallel",
                "riskLevel": "low",
                "reasoning": "batch 独立，全并发执行"
            }
    else:
        return {
            "mode": "wave-based",
            "waveSize": 3,
            "riskLevel": "high",
            "reasoning": f"{batch_count} 个 batch，使用波次模式降低风险"
        }
```

### 方案 C: 在 Workflow 内部调用策略建议器（最简单）

**优点**：
- 不需要修改外部调用链
- Workflow 内部自己决策
- 向后兼容

**实现**：

```javascript
// 在 code-batched-execution.workflow.js 开头
const meta = { ... };

// 🔥 新增：如果没有传入 strategy，自己调用策略建议器
let strategy = args.strategy;

if (!strategy) {
  log("未指定策略，分析 plan 自动决策...");
  
  try {
    const advisorResult = await agent(
      `分析 feature "${feature}" 的 plan，推荐执行策略。
      
      考虑因素：
      - Batch 数量和依赖关系
      - writeSet 重叠情况
      - 执行风险
      
      返回 JSON 格式：
      {
        "mode": "full-parallel" | "wave-based" | "conservative",
        "waveSize": 2-4,
        "riskLevel": "low" | "medium" | "high",
        "reasoning": "决策理由"
      }`,
      {
        label: "策略分析",
        phase: "准备",
        schema: {
          type: "object",
          properties: {
            mode: { type: "string", enum: ["full-parallel", "wave-based", "conservative"] },
            waveSize: { type: "number" },
            riskLevel: { type: "string" },
            reasoning: { type: "string" }
          },
          required: ["mode", "riskLevel", "reasoning"]
        }
      }
    );
    
    strategy = advisorResult;
    log(`推荐策略: ${strategy.mode} (风险: ${strategy.riskLevel})`);
    log(`理由: ${strategy.reasoning}`);
    
  } catch (e) {
    log(`策略分析失败，使用默认策略: ${e.message}`);
    strategy = {
      mode: "full-parallel",
      riskLevel: "medium",
      reasoning: "策略分析失败，使用默认全并发"
    };
  }
}

// 后续使用 strategy ...
```

## 📋 正确的实施顺序

### 阶段 1: 理解现有架构（今天）

```bash
# 1. 找到真正调用 Workflow 的位置
grep -r "Workflow" skills/autodev/autodev-code/ --include="*.md" --include="*.py"

# 2. 查看 SKILL.md
cat skills/autodev/autodev-code/SKILL.md | grep -A 20 -i workflow

# 3. 搜索 code-batched-execution 的调用
grep -r "code-batched-execution" . --include="*.py" --include="*.md" --include="*.sh"
```

### 阶段 2: 选择修改方案（今天）

根据阶段 1 的发现，选择：
- **方案 A**: 如果找到了明确的调用点
- **方案 B**: 如果调用点在外部且容易修改
- **方案 C**: 如果不想动外部，只改 workflow（推荐）

### 阶段 3: 实施修改（1 小时）

#### 如果选择方案 C（推荐）：

**Step 1**: 修改 workflow（30 分钟）
- 增加策略自动决策逻辑（上面的代码）
- 增加 3 种模式的执行逻辑（原快速指南的 Step 1.2）
- 测试语法

**Step 2**: 测试（20 分钟）
- 准备测试 feature
- 运行 workflow
- 观察日志中的策略信息

**Step 3**: 验证（10 分钟）
- 确认策略分析正常
- 确认不同模式正常切换
- 确认向后兼容

### 阶段 4: 推广使用（1 周）

- 10% 流量试运行
- 收集数据和反馈
- 调优策略规则
- 100% 推广

## 📝 修正后的快速实施指南

基于方案 C，我会创建一个新的、完全可执行的指南。

## 🎯 关键发现总结

1. **workflow_launcher.py 不是调用点**
   - 它只是分析器
   - 真正的调用点需要找到

2. **最简单的方案是 Workflow 内部决策**
   - 不依赖外部传参
   - 向后兼容
   - 容易实施

3. **原快速指南的 Step 3 需要废弃**
   - 修改位置错误
   - 需要重新定位

## ⚠️ 行动建议

**立即执行**：
```bash
# 找到真正的调用点
grep -r "code-batched-execution" . --include="*.py" --include="*.md"
cat skills/autodev/autodev-code/SKILL.md
```

**然后决定**：
- 如果找到了外部调用点 → 考虑方案 A/B
- 如果不想动外部 → 使用方案 C（推荐）

**我接下来会**：
1. 帮你搜索真正的调用点
2. 创建修正后的实施指南（基于方案 C）
3. 提供完整的可执行步骤
