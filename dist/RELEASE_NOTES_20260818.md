# AutobizDevOps Rollback Feature - Release Notes

**版本**: v2.0-rollback  
**日期**: 2026-08-18  
**包名**: `autobiz_kanban_rollback_20260818.zip`

## 新增功能

### 1. 阶段回退系统 (Stage Rollback)

完整的 Feature 阶段回退功能，支持安全地回退到任意已完成的 Biz/Dev/Ops 阶段。

**核心能力**：
- ✓ 产物清理：自动删除目标阶段及后续的所有产物
- ✓ 状态重置：回退 checkpoint 并更新 state.json
- ✓ 事务保护：失败自动恢复，支持 Ctrl+C 中断恢复
- ✓ Dry-run 模式：强制预览后确认执行

**使用方式**：
```bash
# 1. 预览回退计划
python hooks/rollback_stage.py \
  --feature "${feature}" \
  --to-stage dev.specs \
  --dry-run --json

# 2. 确认后执行
python hooks/rollback_stage.py \
  --feature "${feature}" \
  --to-stage dev.specs \
  --apply --json
```

### 2. Code Session 基线与源码恢复

Code 阶段支持可选的源码恢复，基于 content-addressable baseline 系统。

**Code 回退特性**：
- ✓ 整体回退：不支持批次级回退，统一回退到 Code 开始前
- ✓ 任务重置：清空所有任务状态、evidence 引用、批次编译状态
- ✓ 批次清理：自动删除未被引用的旧批次目录
- ✓ 运行产物归档：.task-runs、handoff、evidence index 归档到 history

**源码处理模式**：
- `--code-source keep`（默认）：不修改业务仓库，只报告源码影响
- `--code-source restore`：基于 Session 基线恢复源码，检测冲突后阻断

**基线捕获**（Code 入口自动调用）：
```bash
python hooks/rollback_stage.py \
  --capture-code-session \
  --feature "${feature}" \
  --code-workspace "<business-repository>" \
  --json
```

### 3. History 管理与清理

独立的 history 清理命令，避免无限增长。

**清理策略**：
- 默认每个 Feature 保留最近 10 次
- 损坏的 manifest 不会被删除
- 只清理 `status: committed` 的记录

**使用方式**：
```bash
# 预览全局清理
python hooks/rollback_stage.py \
  --prune-history \
  --keep-history 10 \
  --dry-run --json

# 执行清理
python hooks/rollback_stage.py \
  --prune-history \
  --keep-history 10 \
  --apply --json

# 只清理指定 Feature
python hooks/rollback_stage.py \
  --prune-history \
  --feature "${feature}" \
  --keep-history 10 \
  --apply --json
```

## 技术改进

### 并发控制
- Feature 级锁：保护同一 Feature 的状态、基线、产物操作
- History 级锁：保护 history 目录的提交和清理操作
- 嵌套锁设计避免死锁（Feature → History）

### 事务一致性
- 备份 → 修改 → 提交 → 归档的完整事务流程
- 异常/中断时自动恢复全部状态（产物、plan、state、源码）
- 事务日志记录恢复结果，便于故障排查

### 安全边界
- 路径遍历防护：多层校验，防止符号链接攻击
- 强制 dry-run 工作流：必须先预览后确认
- 阻断不安全回退：首个阶段、未到达阶段、源码冲突

## 集成点

### 新增技能
- `/autobizdevops-rollback`：独立回退技能
- 元数据：`skills/autobizdevops-rollback/agents/openai.yaml`

### Workflow 配置
- `dev.plan` rollback archive: `["plans"]`
- `dev.code` rollback archive: `[".task-runs", "BATCH_HANDOFF.json", "evidence/EVIDENCE.index.json"]`

### Code 入口集成
- `skills/autodev/autodev-code/SKILL.md:45-55`：首次启动前自动捕获基线

## 测试覆盖

**专项测试**: 12/12 通过
- ✓ 产物删除和状态更新
- ✓ glob 模式和空目录清理
- ✓ 动态 workflow 支持
- ✓ 边界拒绝（首个阶段、未到达阶段）
- ✓ 归档 Feature 恢复到 active
- ✓ CLI dry-run 不修改状态
- ✓ 状态写入失败恢复
- ✓ Ctrl+C 中断恢复
- ✓ Code 整体重置
- ✓ Code 源码恢复
- ✓ Code 冲突检测（restore 阻断）
- ✓ Code keep/restore 差异

## 文件清单

### 核心实现
- `hooks/rollback_stage.py` (1727 行)：回退核心逻辑
- `tests/test_rollback_stage.py` (619 行)：专项测试

### 配置更新
- `board_core/board_config.json`：rollback archive 配置
- `board_core/workflow.py`：workflow 编译器支持
- `skills/autodev/autodev-code/SKILL.md`：Code 入口集成

### 技能定义
- `skills/autobizdevops-rollback/SKILL.md`：技能文档
- `skills/autobizdevops-rollback/agents/openai.yaml`：元数据

## 使用建议

### 日常使用
1. 回退前先读取当前状态：`python read_state_json.py --feature "${feature}"`
2. 始终先运行 dry-run，确认影响范围
3. Code 回退默认使用 `--code-source keep`，避免覆盖手工修改
4. 只在确认没有并发修改时使用 `--code-source restore`

### 维护建议
1. 定期清理 history（建议每月一次）：`--prune-history --keep-history 20`
2. 监控 `.autobizdevops/rollback/` 目录大小
3. history 清理可安全执行，已提交的记录可删除
4. 损坏的 manifest 会被跳过，不影响正常记录清理

## 已知限制

1. Code 回退不支持批次级目标，只能整体回退到 plan_done
2. 源码恢复依赖基线捕获，首次 Code Session 前必须调用
3. 回退范围必须是已到达的阶段，不能回退到未来
4. 同一 Feature 不支持并发回退操作（Feature 级锁保护）

## 升级说明

### 从旧版本升级
1. 解压新包到插件目录
2. 已有的 Feature 可直接使用回退功能
3. Code 阶段需要基线时会提示先捕获
4. 不影响已有的 checkpoint 和 state

### 兼容性
- Python 3.8+
- 兼容现有 workflow 配置
- 新增字段向后兼容

## 相关命令

```bash
# 查看当前状态
python read_state_json.py --feature "${feature}"

# 解析下一步路由
python hooks/resolve_next_skill.py --json

# 回退后重新进入阶段
# (根据 resolve_next_skill 的返回调用对应 skill)
```

---

**包信息**：
- 文件名：`autobiz_kanban_rollback_20260818.zip`
- 大小：766 KB
- SHA256：见下方校验和文件
