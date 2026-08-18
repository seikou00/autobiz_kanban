# Scripts 目录

## 📋 脚本列表

### sync_plan_status.py

手动修改 plan.json 状态后的一站式同步工具。

**功能**：
- ✅ 同步任务完成状态字段（evidenceIds、completionEvidenceIds 等）
- ✅ 同步批次状态和计数（completedTaskCount、status、completedAt）
- ✅ 同步功能状态和指针（activeBatchId、nextBatchId）
- ✅ 自动重新计算并更新 taskSetDigest

**快速使用**：
```bash
# 预览变更
python scripts/sync_plan_status.py <feature_dir> --dry-run

# 应用变更
python scripts/sync_plan_status.py <feature_dir>
```

**详细文档**：[SYNC_PLAN_STATUS_GUIDE.md](./SYNC_PLAN_STATUS_GUIDE.md)

---

## 📚 文档索引

- **[SYNC_PLAN_STATUS_GUIDE.md](./SYNC_PLAN_STATUS_GUIDE.md)** - sync_plan_status.py 完整使用指南
  - 功能详解
  - 使用示例
  - 常见场景
  - 错误处理
  - 技术细节

---

## 🚀 快速入门

### 典型工作流

```bash
# 1. 手动修改 plan.json 中的状态
vim .autodev/features/feature-001/plans/B001/plan.json

# 2. 预览同步结果
python scripts/sync_plan_status.py .autodev/features/feature-001 --dry-run

# 3. 确认后应用变更
python scripts/sync_plan_status.py .autodev/features/feature-001

# 4. 验证结果
cat .autodev/features/feature-001/plan.json | jq '.taskSetDigest'
```

### 常见场景速查

| 场景 | 命令 |
|------|------|
| 标记任务完成 | 改 task status → `"done"` → 运行脚本 |
| 标记批次完成 | 改所有 task → `"done"` → 运行脚本 |
| 跳过失败任务 | 改 task status → `"failed"` → 运行脚本 |
| 仅更新 digest | 直接运行脚本 |

---

## ⚠️ 重要提示

1. **始终先用 `--dry-run` 预览变更**
2. **重要操作前备份数据**
3. **理解状态转换规则**（详见完整文档）
4. **digest 总是会重新计算**，确保流程一致性
