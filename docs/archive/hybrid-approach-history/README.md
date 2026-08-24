# 混合方案历史文档归档

## 📚 归档说明

这些文档记录了从"固定骨架 + 模型决策策略"到"模型生成完整 workflow"，再回到当前固定 Workflow 的演进过程。

## 🎯 当前权威方案

**当前执行方案**: [`workflows/code-batched-execution.workflow.js`](../../../workflows/code-batched-execution.workflow.js)

本目录只保存历史资料，文中的 generated workflow、strategy advisor、旧 resolver
和插件自管 worktree 均不可执行。当前 Code 执行说明见
[`skills/autodev/autodev-code/references/batch-workflow-guide.md`](../../../skills/autodev/autodev-code/references/batch-workflow-guide.md)。

## 📁 归档内容（14 个文档）

### 混合方案文档（8 个）
这些是"方案 3"的实施文档，该方案后来被更彻底的"模型生成"方案替代：

1. `workflow-hybrid-quick-start.md` - 1 小时快速实施指南
2. `workflow-hybrid-implementation-plan.md` - 完整实施计划
3. `workflow-hybrid-execution-plan.md` - 执行方案
4. `workflow-hybrid-implementation-issues.md` - 问题分析
5. `workflow-hybrid-delivery-checklist.md` - 交付清单
6. `workflow-migration-guide.md` - 迁移指南
7. `adaptive-workflow-quickstart.md` - Adaptive 版本指南
8. `generated-workflow-review.md` - 早期检视（与最终版重复）

### 早期检视文档（6 个）
这些是实施前的代码检视和分析文档：

1. `summary-workflow-review.md` - 第一次代码检视总结
2. `code-workflow-review.md` - 代码检视详细报告
3. `code-workflow-summary.md` - 代码检视总结
4. `code-workflow-checklist.md` - 检视清单
5. `code-workflow-implementation.md` - 实施文档
6. `autobiz-dynamic-workflow-integration-design.md` - 早期设计（140K）

## 📈 演进历程

1. **阶段 1**: 代码检视（发现 15 个问题）
2. **阶段 2**: 设计 4 种方案
3. **阶段 3**: 选择方案 3（混合方案）
4. **阶段 4**: 实现方案 3 的详细文档
5. **阶段 5**: 升级到模型生成完整 workflow
6. **阶段 6**: 实施 generated workflow 方案
7. **阶段 7**: 回退到固定 Workflow，保留本目录作为历史记录 ✅

## 🎯 为什么归档？

1. **已被替代**: 混合方案和 generated workflow 方案均已被固定 Workflow 替代
2. **代码已删除**: 相关代码（generated workflow、adaptive-execution.workflow.js、workflow_strategy_advisor.py）已删除
3. **保持简洁**: 减少 78% 的文档数量，聚焦核心方案

## 📖 如何查看

如需参考历史文档：
```bash
# 查看归档目录
ls docs/archive/hybrid-approach-history/

# 查看具体文档
cat docs/archive/hybrid-approach-history/workflow-hybrid-quick-start.md
```

## ✅ 当前文档结构

```
workflows/
└── code-batched-execution.workflow.js                 # 当前固定 Workflow

skills/autodev/autodev-code/references/
└── batch-workflow-guide.md                            # 当前执行说明
```

---

**归档时间**: 2026-08-21  
**归档原因**: 文档清理，保留核心方案  
**可恢复性**: ✅ 完整保留，可随时恢复
