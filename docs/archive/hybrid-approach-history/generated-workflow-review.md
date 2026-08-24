# 最终方案检查报告

> 历史检视。模型生成 workflow 已退役；当前执行入口是
> `workflows/code-batched-execution.workflow.js`，不得按本文结论实施。

## ✅ 总体评价：设计合理，架构清晰

你修改后的方案从"混合方案（固定骨架 + 模型决策点）"升级到了**"模型生成完整 Workflow"**，这是一个更加彻底和长远的方案。

---

## 🎯 核心改变分析

### 从方案 3 到最终方案的演进

| 维度 | 原方案 3（混合） | 最终方案（生成） |
|------|-----------------|-----------------|
| **脚本来源** | 固定的 JS 文件 | 模型每次生成 |
| **模型角色** | 只决策策略参数 | 生成完整编排逻辑 |
| **灵活性** | 中等（3 种固定模式） | 高（完全自定义编排） |
| **复杂度** | 低 | 高 |
| **实施周期** | 1-2 周 | 2-3 月 |
| **风险** | 低 | 中等 |
| **可维护性** | 高（固定代码） | 中等（生成代码） |

**结论**: 最终方案是更先进的设计，但实施难度也显著增加。

---

## ✅ 设计优点

### 1. 边界清晰 ⭐⭐⭐

```
模型负责：编排逻辑（控制流）
平台负责：安全协议（执行层）
```

这个划分非常合理，避免了：
- ✅ 模型不能自管 worktree（平台负责）
- ✅ 模型不能绕过 lease（平台负责）
- ✅ 模型不能跳过 merge/verify（平台负责）

**评价**: 边界设计优秀，职责清晰

### 2. 安全约束完善 ⭐⭐⭐

validator 的设计很到位：
- ✅ 静态分析生成的脚本
- ✅ 检查危险操作（shell、worktree、路径）
- ✅ 验证必需的协议调用（lease、merge、verify）
- ✅ 哈希和签名机制

**评价**: 安全设计周全

### 3. 持久化和 Resume 机制 ⭐⭐

definition store 的设计合理：
- ✅ 脚本持久化到磁盘
- ✅ 绑定 scriptSha256 和 planDigest
- ✅ resume 时使用原脚本，不重新生成
- ✅ 哈希不一致时阻断

**评价**: 一致性保证良好

### 4. 回退机制 ⭐⭐

多层回退策略：
- ✅ 生成失败 → 回退 legacy
- ✅ 校验失败 → 回退 legacy
- ✅ 运行模式开关（legacy/shadow/canary/enforced）

**评价**: 风险控制到位

---

## ⚠️ 潜在问题和建议

### 问题 1: 模型生成质量不稳定 ⚠️⚠️⚠️

**问题描述**:
- 模型生成的代码质量可能不稳定
- 复杂场景下可能生成错误的编排逻辑
- 难以调试生成的代码

**建议**:
```python
# 1. 增加生成质量评分
def score_generated_workflow(script: str, context: dict) -> float:
    """评估生成质量，低于阈值拒绝使用"""
    scores = {
        "syntax": check_syntax(script),
        "completeness": check_required_calls(script),
        "safety": check_dangerous_patterns(script),
        "complexity": check_complexity(script),
    }
    return sum(scores.values()) / len(scores)

# 2. 低质量时自动回退
if score_generated_workflow(script, context) < 0.8:
    log("生成质量不佳，回退到 legacy")
    return use_legacy_workflow()
```

**优先级**: P0（核心风险）

---

### 问题 2: validator 的静态分析可能不够 ⚠️⚠️

**问题描述**:
- JavaScript 是动态语言，静态分析有局限
- 模型可能通过间接方式绕过检查（如字符串拼接、eval）
- 复杂的控制流可能漏检

**建议**:
```python
# 1. 增加运行时监控
class WorkflowRuntimeMonitor:
    """运行时监控生成脚本的行为"""
    
    def __init__(self, allowed_operations):
        self.allowed = set(allowed_operations)
        self.violations = []
    
    def check_operation(self, op_type, details):
        if op_type not in self.allowed:
            self.violations.append({
                "type": op_type,
                "details": details,
                "timestamp": time.time()
            })
            raise SecurityViolation(f"未授权操作: {op_type}")

# 2. 在 workflow 运行时注入监控
# 拦截所有 agent() 调用和 shell 执行
```

**优先级**: P0（安全关键）

---

### 问题 3: 生成失败的比例可能很高 ⚠️

**问题描述**:
- 前期模型可能频繁生成失败
- 每次失败都回退 legacy，用户体验差
- 没有学习和改进机制

**建议**:
```python
# 1. 增加生成失败的分类和统计
class GenerationFailureTracker:
    """追踪生成失败原因"""
    
    def record_failure(self, reason, context):
        failure_db.insert({
            "reason": reason,
            "context": context,
            "timestamp": time.time(),
            "frequency": self.get_frequency(reason)
        })
    
    def get_top_failures(self, limit=10):
        """返回最常见的失败原因"""
        return failure_db.query().order_by("frequency").limit(limit)

# 2. 根据失败原因优化 prompt
def generate_workflow_with_feedback(context, failures):
    """在 prompt 中包含常见失败案例"""
    prompt = base_prompt + "\n\n常见错误（请避免）:\n"
    for failure in failures:
        prompt += f"- {failure['reason']}\n"
    return call_model(prompt)
```

**优先级**: P1（体验优化）

---

### 问题 4: definition store 的并发控制 ⚠️

**问题描述**:
- 多个 run 同时创建时可能冲突
- definition 文件写入不是原子的
- 缺少锁机制

**建议**:
```python
# 使用文件锁保护 definition 写入
from hooks.evidence_kernel import FileLock

def save_definition_atomic(definition_id, content):
    """原子写入 definition"""
    definition_dir = get_definition_dir()
    lock_file = definition_dir / f".{definition_id}.lock"
    
    with FileLock(lock_file):
        # 写入到临时文件
        temp_file = definition_dir / f"{definition_id}.tmp"
        temp_file.write_text(json.dumps(content))
        
        # 原子重命名
        final_file = definition_dir / f"{definition_id}.json"
        temp_file.rename(final_file)
```

**优先级**: P1（数据一致性）

---

### 问题 5: 缺少生成脚本的审计和调试能力 ⚠️

**问题描述**:
- 生成的脚本出问题时难以调试
- 缺少审计日志（谁生成的、何时生成、输入是什么）
- 难以重现问题

**建议**:
```python
# 1. 增加详细的审计日志
def save_generation_audit(definition_id, context, script):
    """保存生成审计"""
    audit = {
        "definitionId": definition_id,
        "timestamp": utc_now(),
        "model": context["model"],
        "inputHash": hash_input(context["input"]),
        "scriptSha256": hash_script(script),
        "planDigest": context["planDigest"],
        "batchCount": len(context["batches"]),
        "generationTimeMs": context["elapsed_ms"],
    }
    
    audit_file = get_audit_dir() / f"{definition_id}.audit.json"
    audit_file.write_text(json.dumps(audit))

# 2. 增加重现工具
def replay_generation(definition_id):
    """重新生成相同的 workflow（用于调试）"""
    audit = load_audit(definition_id)
    return generate_workflow(
        restore_input_from_hash(audit["inputHash"]),
        model=audit["model"]
    )
```

**优先级**: P1（可调试性）

---

### 问题 6: resume 时的 plan 变化处理 ⚠️

**问题描述**:
- 文档说 plan 变化时 run 进入 `blocked`
- 但没有说明如何恢复（是手动还是自动）
- 用户可能不理解为什么被阻断

**建议**:
```python
# 增加 plan 变化的详细诊断
def check_plan_compatibility(original_digest, current_digest):
    """检查 plan 变化是否兼容"""
    if original_digest == current_digest:
        return {"compatible": True}
    
    # 加载两个版本的 plan
    original = load_plan_by_digest(original_digest)
    current = load_current_plan()
    
    # 详细对比
    changes = {
        "added_batches": set(current.batches) - set(original.batches),
        "removed_batches": set(original.batches) - set(current.batches),
        "modified_batches": find_modified_batches(original, current),
        "dependency_changes": find_dependency_changes(original, current),
    }
    
    # 判断是否可以安全 resume
    can_resume = (
        not changes["removed_batches"] and  # 不能删除 batch
        not changes["dependency_changes"]    # 不能改依赖
    )
    
    return {
        "compatible": can_resume,
        "changes": changes,
        "suggestion": get_resume_suggestion(changes)
    }
```

**优先级**: P2（用户体验）

---

## 📋 实施风险评估

### 高风险项 🔴

1. **模型生成质量不稳定** - P0
   - 影响: 可能频繁回退 legacy，用户体验差
   - 缓解: 增加质量评分、多次重试、改进 prompt

2. **validator 静态分析局限** - P0
   - 影响: 可能被绕过，安全风险
   - 缓解: 运行时监控、沙箱执行、最小权限

3. **调试困难** - P1
   - 影响: 问题定位难，修复周期长
   - 缓解: 详细审计日志、重现工具、可视化

### 中风险项 🟡

4. **definition 并发冲突** - P1
5. **plan 变化处理不清晰** - P2
6. **生成失败比例高** - P1

### 低风险项 🟢

7. 回退机制完善
8. 边界划分清晰
9. 持久化设计合理

---

## 🎯 建议的实施顺序优化

### 原方案的顺序
```
1. 冻结并测试 legacy 协议
2. 实现 validator
3. 实现 definition store
4. 接入 launcher 生成、校验、回退
5. 扩展 scheduler manifest 和 resume
6. 更新 workflow 入口与 skill 文档
7. 完成多 batch / 冲突 / resume 集成测试
8. shadow -> canary -> enforced
9. 根据指标决定是否扩大范围
```

### 建议优化为

```
1. 冻结并测试 legacy 协议
2. 实现 validator（增强版 + 运行时监控）✨
3. 实现 definition store（增加并发控制）✨
4. 实现审计和调试工具 ✨
5. 接入 launcher 生成、校验、回退（增加质量评分）✨
6. 扩展 scheduler manifest 和 resume（增加 plan 兼容性检查）✨
7. 更新 workflow 入口与 skill 文档
8. 完成多 batch / 冲突 / resume 集成测试
9. shadow 模式运行，收集生成质量数据 ✨
10. 根据数据优化 prompt 和 validator ✨
11. canary（10% → 30% → 50%）
12. enforced（100%）
13. 根据指标持续优化
```

**关键增强点**:
- ✨ 增加质量评分和监控
- ✨ 增加审计和调试能力
- ✨ 增加数据收集和优化循环

---

## ✅ 方案核心优势（保持）

1. **边界清晰** - 模型编排，平台执行
2. **安全可控** - validator + 哈希 + resume
3. **可回退** - legacy fallback 机制完善
4. **渐进式** - shadow → canary → enforced

---

## 📊 总体评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ⭐⭐⭐⭐⭐ | 边界清晰，职责分明 |
| 安全性 | ⭐⭐⭐⭐ | 有 validator，但需增强运行时监控 |
| 可维护性 | ⭐⭐⭐ | 生成代码调试困难，需增强工具 |
| 灵活性 | ⭐⭐⭐⭐⭐ | 完全自定义编排 |
| 风险控制 | ⭐⭐⭐⭐ | 回退机制好，但生成质量是风险 |
| 实施难度 | ⭐⭐ | 复杂度高，周期长 |

**总体评分**: ⭐⭐⭐⭐ (4/5)

---

## 🎯 最终建议

### 1. 方案本身没有重大问题 ✅

你的最终方案设计合理，架构清晰，边界清楚，是一个可行的长期方案。

### 2. 需要增强的关键点 ⚠️

- **P0**: 增加生成质量评分和验证
- **P0**: 增强 validator 的运行时监控
- **P1**: 增加审计和调试工具
- **P1**: 增加并发控制和数据一致性保护

### 3. 实施建议 📋

**短期（1-2 月）**:
- 实施原方案的 Phase 1-5
- 同时实施上述 P0 和 P1 的增强点

**中期（3-4 月）**:
- Shadow 模式运行，收集数据
- 根据数据优化 prompt 和 validator
- 逐步推进 canary

**长期（5-6 月）**:
- Enforced 模式
- 持续优化生成质量
- 监控和改进

### 4. 与原混合方案的关系 🔄

**建议**:
1. **保留混合方案作为快速验证原型**
   - 先实施混合方案（1-2 周）
   - 快速获得反馈和数据
   - 验证策略决策的价值

2. **然后升级到生成方案**
   - 有了混合方案的经验
   - 更清楚需要生成什么
   - 降低最终方案的风险

**理由**: 渐进式演进比一步到位更安全

---

## 📝 文档更新建议

### 需要补充的内容

1. **生成质量评估标准**
   - 什么是"合格"的生成脚本
   - 评分标准和阈值
   - 失败时的诊断信息

2. **调试和故障排查指南**
   - 如何查看生成的脚本
   - 如何重现生成过程
   - 如何诊断生成质量问题

3. **性能和成本分析**
   - 每次生成的 token 消耗
   - 生成时间的影响
   - 成本控制策略

4. **监控指标定义**
   - 生成成功率
   - 生成质量分数分布
   - validator 拦截率
   - legacy fallback 比例

---

## 🎉 结论

**你的最终方案设计合理，但需要增强：**

✅ **优点**:
- 架构清晰，边界分明
- 安全机制完善
- 回退方案周全
- 长期目标明确

⚠️ **需要增强**:
- 生成质量控制
- 运行时监控
- 调试工具
- 并发控制

📋 **建议**:
1. 按照本报告的建议增强关键点
2. 考虑先实施混合方案作为过渡
3. 分阶段推进，持续收集数据优化

**总体评价**: 这是一个ambitious但可行的方案，需要仔细实施和持续优化。🚀
