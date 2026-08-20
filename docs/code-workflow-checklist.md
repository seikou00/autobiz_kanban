# Code Workflow 实施清单

## ✅ 已完成项

### 核心组件
- [x] `hooks/workflow_launcher.py` - 执行模式判断器
- [x] `hooks/worktree_manager.py` - Worktree 管理器
- [x] `hooks/batch_merger.py` - 批次合并器
- [x] `hooks/parallel_runtime.py` - Manifest、依赖、Lease、幂等 Resume
- [x] `hooks/parallel_batch_scheduler.py` - 多仓库调度与状态机
- [x] `hooks/parallel_batch_lifecycle.py` - 监控、外部修改检测、清理、回滚
- [x] `hooks/parallel_conflict_policy.py` - touches 分析与特殊变更收口
- [x] `hooks/parallel_conflict_resolver.py` - 隔离 Worktree 冲突解决与验证
- [x] `workflows/code-batched-execution.workflow.js` - 主编排脚本

### 文档
- [x] `docs/code-workflow-implementation.md` - 完整实施文档（5000+ 行）
- [x] `docs/code-workflow-summary.md` - 实施总结
- [x] `skills/autodev/autodev-code/references/batch-workflow-guide.md` - 使用指南
- [x] `skills/autodev/autodev-code/SKILL.md` - 技能文档更新

### 测试
- [x] `tests/test_code_workflow_integration.py` - 集成测试
- [x] 所有测试通过（5/5）

---

## 📋 验证与后续项

### 1. 集成到 /autodev-code 技能 ✅ 已完成

**位置**: `skills/autodev/autodev-code/SKILL.md`

**结果**: 已在执行流程中添加 Workflow 判断逻辑。下面的命令仅作为接入协议示例，实际入口已由技能文档和 Workflow 编排脚本固化。

```bash
# 在 "执行所有 batch" 之前添加：

echo "检查是否需要使用 Workflow 并行执行..."
launcher_result=$(python "${pluginPath}/hooks/workflow_launcher.py" \
  --feature "${feature}" \
  --plugin-path "${pluginPath}" \
  --workspace "${pluginWorkspace}/${projectDir}" \
  --json)

useWorkflow=$(echo "$launcher_result" | jq -r '.useWorkflow')
batchCount=$(echo "$launcher_result" | jq -r '.batchCount')

if [ "$useWorkflow" = "true" ]; then
  echo "检测到 ${batchCount} 个 batch，启动 Workflow 并行执行"

  # 使用 Workflow 工具启动并行执行
  # scriptPath: workflows/code-batched-execution.workflow.js
  # args: {
  #   feature: "${feature}",
  #   pluginPath: "${pluginPath}",
  #   artifactWorkspace: "${pluginWorkspace}/${projectDir}"
  # }

  # 等待 Workflow 完成...

else
  echo "单个 batch 或串行模式，使用原有流程"

  # 继续现有的串行执行逻辑
  # ...
fi
```

**验证**:
- [x] 单 batch feature 仍使用串行流程
- [x] 多 batch feature 自动切换到 Workflow
- [x] Workflow 执行成功
- [x] 最终产物正确

---

### 2. 试点测试 🟡 中优先级

**目标**: 选择 1-2 个真实 feature 进行试点

**步骤**:
1. [ ] 选择包含 2-3 个 batch 的 feature
2. [ ] 使用 Workflow 模式执行
3. [ ] 观察并行执行过程
4. [ ] 验证合并结果
5. [ ] 记录性能数据
6. [ ] 收集问题和改进点

**数据收集**:
- [ ] 串行模式总耗时
- [ ] 并行模式总耗时
- [ ] 加速比
- [ ] 是否出现冲突
- [ ] 冲突解决方式

---

### 3. 监控和日志 ✅ 已完成基础能力

**目标**: 增强可观测性

- [x] 结构化事件写入 run 目录
- [x] 记录每个 batch 的执行时间和 timeline
- [x] 记录 worktree、lease、合并、清理事件
- [x] 保存 manifest、事件和最终验证历史

**建议位置**:
```
.autobizdevops/features/${feature}/
├── workflow-execution.log      # Workflow 执行日志
├── batch-timings.json          # 各 batch 耗时
└── merge-report.json           # 合并报告
```

---

### 4. 错误处理增强 ✅ 已完成

**场景**:
- [x] Batch 执行失败后的 full/partial rollback
- [x] 合并失败后的 resolution Worktree 收口
- [x] Worktree 泄漏的 cleanup/auto-cleanup
- [x] lease 超时 reclaim 与 resume

**建议**:
- 添加 `--resume` 支持，失败后可恢复
- 添加 `--cleanup` 命令，清理残留 worktrees
- 添加超时机制

---

### 5. 性能优化 ✅ 已完成基础能力

**可选优化**:
- [x] `maxParallel` 可配置
- [x] Worktree 按仓库复用 Git 对象
- [x] 合并前使用 `merge-tree` 预检
- [ ] 缓存依赖安装

---

### 6. 文档完善 🟢 低优先级

- [ ] 添加故障排查指南
- [ ] 添加常见问题 FAQ
- [ ] 添加性能调优建议
- [ ] 添加架构图示

---

## 🎯 近期重点

### 本周目标
1. **集成到 /autodev-code**（高优先级）
2. **试点测试**（中优先级）

### 下周目标
1. 根据试点反馈调整
2. 添加监控和日志
3. 优化错误处理

---

## 📊 成功指标

### 功能指标
- [x] 所有单元测试通过
- [ ] 试点 feature 成功完成
- [ ] 未引入新的 bug
- [ ] 单 batch 场景保持兼容

### 性能指标
- [ ] 多 batch 场景加速 > 2x
- [ ] Worktree 开销 < 5%
- [ ] 合并时间 < 串行单 batch 时间

### 稳定性指标
- [ ] 无 worktree 泄漏
- [ ] 无代码冲突遗漏
- [ ] 失败后可清理

---

## 🔧 快速命令参考

### 测试整体流程
```bash
# 运行集成测试
python tests/test_code_workflow_integration.py

# 测试单个组件
python hooks/workflow_launcher.py \
  --feature "test-feat" \
  --plugin-path "/path/to/plugin" \
  --workspace "/path/to/artifacts/project" \
  --json
python hooks/worktree_manager.py --json list --repo .
python hooks/batch_merger.py --json detect-conflicts --batches '[...]'
```

### 清理 Worktrees
```bash
# 列出所有 worktrees
git worktree list

# 删除特定 worktree
git worktree remove .worktrees/xxx --force

# 清理所有 autobiz worktrees
find .worktrees -maxdepth 1 -mindepth 1 -type d | while read d; do
  git worktree remove "$d" --force 2>/dev/null || true
done
```

### 查看 Workflow 状态
```bash
# 在 Claude Code 中
/workflows

# 停止运行中的 workflow
# (使用 TaskStop 工具)
```

---

## 📝 问题跟踪

### 已知问题
_目前无已知问题_

### 待讨论
1. 是否需要限制最大并行数？
2. 冲突处理是自动合并还是人工介入？
3. 是否需要支持部分 batch 重试？

---

## 🎉 里程碑

- [x] **2026-08-19**: 核心组件开发完成
- [x] **2026-08-19**: 所有测试通过
- [ ] **TBD**: 集成到 /autodev-code
- [ ] **TBD**: 首个试点 feature 成功
- [ ] **TBD**: 正式启用

---

**最后更新**: 2026-08-19
**负责人**: @您的名字
**状态**: 🟢 核心开发完成，待集成
