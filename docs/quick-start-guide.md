# 乐观并行执行快速开始指南

## 快速启用

### 1. 创建配置文件

在项目根目录创建 `.autobiz/runtime_config.json`:

```bash
mkdir -p .autobiz
cat > .autobiz/runtime_config.json << 'EOF'
{
  "parallelSchedulingMode": "optimistic",
  "maxParallel": 4,
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
python hooks/workflow_launcher.py analyze \
  --workspace . \
  --feature your-feature-name

# 输出示例：
# Wave 1:
#   Strategy: Optimistic parallel (maxParallel=4)
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
# 3. 自动解决追加式冲突
# 4. 通知人工介入复杂冲突
```

## 处理冲突

### 自动解决（无需操作）

追加式冲突会自动解决：

```python
# B001 添加
def new_method_a():
    return "A"

# B002 添加
def new_method_b():
    return "B"

# 自动合并为：
def new_method_a():
    return "A"

def new_method_b():
    return "B"
```

### 人工介入

当收到通知时：

```
╔══════════════════════════════════════════════════════════════
║ CONFLICT RESOLUTION REQUIRED
╠══════════════════════════════════════════════════════════════
║ Batches:  B003, B004
║ Base SHA: abc12345
║ Worktree: /path/to/.parallel-runs/cw-20260830-001/merge-trains/backend/wave-002
║ 
║ Conflicted files:
║   - src/core.py
║   - src/api.py
║ 
║ Next steps:
║   1. cd /path/to/worktree
║   2. Manually resolve conflicts
║   3. git add <resolved_files>
║   4. git commit
║   5. Run: autobiz resume-merge-train --candidate candidate_wave2_backend
╚══════════════════════════════════════════════════════════════
```

**操作步骤**:

```bash
# 1. 进入 worktree
cd /path/to/worktree

# 2. 查看冲突
git status

# 3. 编辑冲突文件，保留双方业务逻辑
vim src/core.py

# 4. 标记为已解决
git add src/core.py src/api.py

# 5. 提交
git commit -m "Resolve conflicts: preserve both B003 and B004 logic"

# 6. 恢复验证流程
python hooks/parallel_merge_train.py resume \
  --workspace . \
  --feature your-feature \
  --run-id cw-20260830-001 \
  --wave 2
```

## 切换回保守模式

如果遇到问题，随时可以切回：

```json
{
  "parallelSchedulingMode": "conservative",
  "maxParallel": 4
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
  "maxParallel": 4,
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
