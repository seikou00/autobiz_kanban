# 乐观并行执行快速开始指南

## 快速启用

### 1. 创建配置文件

在项目根目录创建 `.autobiz/runtime_config.json`:

```bash
mkdir -p .autobiz
cat > .autobiz/runtime_config.json << 'EOF'
{
  "parallelSchedulingMode": "optimistic",
  "maxParallel": 5,
  "conflictResolution": {
    "maxAttempts": 2,
    "enableAutoResolve": true,
    "notifyOnManualRequired": true
  }
}
EOF
```

### 2. 验证配置

```bash
# 运行预览，查看执行计划
python -X utf8 hooks/workflow_launcher.py analyze \
  --workspace . \
  --feature your-feature-name

# 输出示例：
# Wave 1:
#   Strategy: Optimistic parallel (maxParallel=5)
#   Batches: B001, B002, B003
#   ⚠️  Write-set overlap: src/core.py
#       Conflicts will be resolved in Merge Train
```

### 3. 运行工作流

正常启动工作流，系统会自动使用乐观并行模式：

```bash
# 工作流会：
# 1. 并行执行所有依赖满足的 Batch
# 2. 在 Merge Train 中检测冲突
# 3. 模型解决候选中的冲突文件
# 4. 模型返回后由插件暂存、创建 merge commit 并推广
```

## 处理冲突

### 固定 Workflow 自动处理

Workflow 先调用 `prepare-resolution`，登记当前候选和允许修改的冲突文件。
模型只编辑这些文件，理解并保留双方业务意图，移除冲突标记后返回
`success=true,status=resolved`。模型不执行暂存或提交。

随后 Workflow 单独调用 `complete-resolution`，由插件在候选目录内暂存文件、
非交互创建带 task-card 信息的 merge commit 并登记实际 SHA，再继续推广。
插件命令使用工具默认合法工作区，候选路径由插件内部处理，无需将 execute
的 `cwd/workdir` 切换到候选目录。

### 保留候选的受控恢复

无法解决时保留 Worktree 和原始错误；独立 Batch 继续运行。
需要手工编辑时也可使用同一插件交接协议，以下占位符须替换为真实路径和 ID：

```bash
# 1. 获取该候选的文件写入授权
python -X utf8 "<pluginPath>/hooks/parallel_merge_train.py" prepare-resolution \
  --workspace "<artifact-workspace>" --feature "<feature>" \
  --run-id "<runId>" --repository-ref "<repositoryRef>" --wave 2

# 2. 只编辑返回 worktreePath 中的 conflictedFiles，保留双方业务逻辑
#    status=ready_to_complete 时跳过编辑，直接恢复插件提交登记

# 3. 交还插件完成 merge commit 和候选状态登记
python -X utf8 "<pluginPath>/hooks/parallel_merge_train.py" complete-resolution \
  --workspace "<artifact-workspace>" --feature "<feature>" \
  --run-id "<runId>" --repository-ref "<repositoryRef>" --wave 2
```

`resume-candidate` 仍用于登记人工已经完成的提交；固定 Workflow 使用上述
模型编辑、插件提交协议。详见 [冲突处理指南](./workflow-conflict-handling-guide.md)。

## 切换回保守模式

如果遇到问题，随时可以切回：

```json
{
  "parallelSchedulingMode": "conservative",
  "maxParallel": 5
}
```

## 监控指标

查看执行指标：

```bash
# 查看 manifest
cat .autobizdevops/features/your-feature/.parallel-runs/cw-20260830-001/manifest.json | jq '.mergeTrains'

# 关键指标：
# - status: "candidate_conflicted" / "built" / "promoted"
# - conflictContext: 冲突详情
# - resolutionStrategy: 使用的解决策略
```

## 常见问题

### Q: 如何知道是否应该使用乐观模式？

**适合场景**：
- 独立功能开发（不同模块）
- 团队协作，改动分散
- 需要高并发度

**不适合场景**：
- 频繁修改相同文件
- 大范围重构
- 高度耦合的代码

### Q: 冲突率多高需要切回保守模式？

**建议阈值**：
- 冲突率 < 20%：继续乐观模式
- 20% - 40%：评估收益
- > 40%：切回保守模式

### Q: 自动解决失败会怎样？

- Batch 状态标记为 `needs_resolution`
- 下游依赖被阻塞
- Worktree 保留供人工处理
- 其他无依赖的 Batch 继续执行

### Q: 如何提高自动解决成功率？

1. **规范代码风格**：减少格式冲突
2. **模块化设计**：减少同文件修改
3. **增量改动**：避免大规模重构并行
4. **及时合并**：减少分支存活时间

## 高级配置

### 自定义冲突解决策略

```json
{
  "parallelSchedulingMode": "optimistic",
  "maxParallel": 6,
  "conflictResolution": {
    "maxAttempts": 3,
    "enableAutoResolve": true,
    "autoResolveStrategies": [
      "append_only",
      "local_modification"
    ],
    "skipPatterns": [
      "*.lock",
      "package-lock.json",
      "yarn.lock"
    ]
  }
}
```

### 调试模式

```json
{
  "parallelSchedulingMode": "optimistic",
  "maxParallel": 5,
  "conflictResolution": {
    "maxAttempts": 2,
    "enableAutoResolve": true,
    "notifyOnManualRequired": true,
    "debugMode": true,
    "preserveAllWorktrees": true
  }
}
```

## 更多资源

- 📖 [完整实施文档](./optimistic-parallel-execution-mvp.md)
- 🔧 [集成指南](./conflict-resolution-integration.md)
- 📊 [实施总结](./implementation-summary.md)

## 获取帮助

遇到问题？

1. 查看 manifest 中的详细错误信息
2. 检查保留的 candidate worktree
3. 查看 `.autobiz/runtime_config.json` 配置
4. 尝试切回保守模式验证

---

**祝使用愉快！🚀**
