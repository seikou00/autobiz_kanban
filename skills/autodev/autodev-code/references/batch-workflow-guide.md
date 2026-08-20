# Code 阶段 Workflow 并行执行指南

本指南说明如何使用 workflow 并行执行 code 阶段的多个 batch。

## 概述

当一个 feature 包含多个独立的 batch 时，传统的串行执行模式效率较低。Workflow 并行执行模式通过以下机制提高效率：

1. **并行执行**：多个 batch 同时在独立 worktree 中实现
2. **隔离保护**：每个 batch 在独立的 Git worktree 中工作，避免代码冲突
3. **冲突检测**：自动检测多个 batch 修改同一文件的情况
4. **顺序合并**：按依赖顺序将各 batch 的变更合并到主分支
5. **统一验证**：合并完成后，对整体代码进行编译和类型检查

## 执行流程

```
┌─────────────────┐
│   准备阶段      │  读取 plan.json，分析 batch 结构
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  并行实现阶段   │  每个 batch 在独立 worktree 中执行
│                 │
│  ┌──────────┐   │
│  │ Batch 1  │   │  worktree: .worktrees/feature-B001
│  └──────────┘   │  - code-session
│                 │  - 执行所有任务
│  ┌──────────┐   │  - batch-compile
│  │ Batch 2  │   │
│  └──────────┘   │  worktree: .worktrees/feature-B002
│                 │
│  ┌──────────┐   │
│  │ Batch 3  │   │  worktree: .worktrees/feature-B003
│  └──────────┘   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  冲突检测阶段   │  检测是否有文件被多个 batch 修改
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  顺序合并阶段   │  依次合并各 batch 到主分支
│                 │
│  B001 → merge   │  git merge worktree/feature-B001
│       ↓         │  git commit
│    verify       │
│       ↓         │
│  B002 → merge   │  git merge worktree/feature-B002
│       ↓         │  (基于 B001 的结果)
│    verify       │  git commit
│       ↓         │
│  B003 → merge   │  git merge worktree/feature-B003
│       ↓         │  (基于 B001+B002 的结果)
│    verify       │  git commit
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  最终验证阶段   │  验证合并后的完整代码
│                 │  - 编译检查
│                 │  - 类型检查
│                 │  - 语法检查
└─────────────────┘
```

## 启用条件

Workflow 自动在以下情况启用：

✅ **自动启用**：
- Feature 包含 **2 个或更多** 待执行的 batch
- 各 batch 状态为 `todo` 或 `in_progress`

✗ **不启用**（使用串行模式）：
- 只有 1 个 batch
- 所有 batch 都已完成（`done` 或 `failed`）

## 使用方式

### 方式 1: 自动判断（推荐）

在 `/autodev-code` 技能执行时，自动判断：

```bash
# 1. 判断是否使用 workflow
python "${pluginPath}/hooks/workflow_launcher.py" --feature "${feature}" --json

# 2. 根据返回的 useWorkflow 决定执行路径
# - useWorkflow: true → 启动 workflow
# - useWorkflow: false → 使用原有串行流程
```

### 方式 2: 手动启动

直接调用 workflow：

```bash
# 使用 Workflow 工具
# scriptPath: workflows/code-batched-execution.workflow.js
# args: {
#   feature: "feat-xxx",
#   pluginPath: "...",
#   codeWorkspaces: {
#     "backend-api": "/repo/services/api",
#     "frontend-app": "/repo/apps/web"
#   }
# }
```

## 关键技术点

### 1. Worktree 隔离

每个 batch 在其 `workspaceRef` 绑定仓库的独立 worktree 中执行。一个 feature 可同时涉及多个 Git 仓库，或同一仓库下多个组件：

```
.worktrees/
├── feat-user-auth-B001/    # Batch 1 的工作区
│   ├── backend/
│   └── frontend/
├── feat-user-auth-B002/    # Batch 2 的工作区
│   ├── backend/
│   └── frontend/
└── feat-user-auth-B003/    # Batch 3 的工作区
    ├── backend/
    └── frontend/
```

**优势**：
- 各 batch 互不干扰
- 可以并行修改同一文件（冲突在合并时处理）
- 失败的 batch 不影响成功的 batch

### 2. 冲突检测

在合并前检测文件冲突：

```javascript
// 示例：Batch 1 和 Batch 2 都修改了 user.service.ts
{
  "conflicts": [
    {
      "file": "src/services/user.service.ts",
      "batches": ["B001", "B002"]
    }
  ]
}
```

**处理策略**：
- Git 冲突会立即将 run 置为 `blocked`；合并器不会自动使用 `ours`、`theirs` 或修改冲突文件。
- 不同仓库的相同相对路径不算冲突，检测和合并严格按 `workspaceRef` / Git 根隔离。
- 修复应在新的受控 Batch run 中完成，再执行 `resume`。

### 3. 顺序合并

即使并行执行，合并仍按顺序进行：

```bash
# 合并器按 manifest 的 Batch deps 排序；每个 Batch 合并到自己的 Git 根。
python hooks/batch_merger.py \
  --workspace <artifact-workspace> \
  --feature feat-user-auth \
  --run-id cw-YYYYMMDD-001
```

### 4. 最终验证

合并完成后，验证整体代码：

```bash
# 编译检查（如果有）
npm run build    # 或 mvn compile
yarn typecheck   # 类型检查

# 不运行单元测试（留给 UTest 阶段）
```

## 错误处理

### 场景 1: 某个 Batch 执行失败

```javascript
{
  "error": "batch_execution_failed",
  "failed": [
    {
      "batchId": "B002",
      "status": "failed",
      "errorMessage": "编译失败: ..."
    }
  ]
}
```

**处理**：
- 当前 wave 不合并，worktree 与 manifest 保留供诊断。
- 修复后执行 `parallel_batch_scheduler.py resume`；已 seal 或已 merge 的 Batch 会被幂等跳过。
- 跨仓库依赖必须写入 Batch `deps`。所有没有未完成依赖的 Batch 都可以并行，即使它们属于同一仓库、同一组件或声明了重叠路径；每个 Batch 都在独立 Worktree 中执行。
- `scope.paths`、`expectedFiles` 与组件根目录用于任务范围、预警和合并诊断，不作为调度串行锁。回并时按 `workspaceRef` / Git 根隔离；同仓库冲突由 Git 检测并阻断，不自动选边。

### 冲突预防与自动解决

Plan 启用 `parallelPolicy` 后，每个 Task 必须声明 `touches`：`code` 表示普通实现文件，`shared` 表示入口/装配等共享文件，`proto` 表示协议与桩代码，`database` 表示迁移/Schema，`configuration` 表示运行时或部署配置。

- 普通 `code` touches 重叠只产生 planner warning，应该回到 task-planner 拆分隔离；执行仍可并行。
- `shared` 只能由单个 integration Batch 在收口阶段修改，并等待同仓库普通 Batch 完成。
- `proto` 只能由单个 proto Batch 前置修改并生成桩代码，下游 Batch 自动依赖它；`has_pb_change=true` 时没有该 Batch 会阻断并行。
- `database` / `configuration` 只能由单个 global-change Batch 执行，必须在 Plan 中记录确认和 owner，下游 Batch 自动依赖它。
- 真正发生 Git 冲突时，系统创建 resolution Worktree。Agent 正常解决冲突、运行 compile、提交 resolution commit，再由 resolver 回并；任何未解决标记、编译失败或主工作区漂移都会阻断。

### 场景 2: 合并冲突

```javascript
{
  "error": "merge_failed",
  "conflicts": [
    {
      "file": "src/services/user.service.ts",
      "batches": ["B001", "B003"]
    }
  ]
}
```

**处理**：
1. 工作流进入 `blocked`，不会在主分支写入冲突标记
2. 保留冲突信息和 worktree 供诊断
3. 通过新的受控 Batch 修复后再恢复调度

### 场景 3: 最终验证失败

```javascript
{
  "error": "verification_failed",
  "errors": [
    "TypeScript 错误: src/types.ts(15,3): Type mismatch"
  ]
}
```

**处理**：
- 所有 batch 已合并到主分支
- 需要修复验证错误
- 可能是 batch 间集成问题

## 性能优势

### 串行模式（传统）

```
B001: 10 分钟
  ↓
B002: 8 分钟
  ↓
B003: 12 分钟
────────────────
总计: 30 分钟
```

### 并行模式（Workflow）

```
B001: 10 分钟  ┐
B002: 8 分钟   ├─ 并行执行
B003: 12 分钟  ┘
  ↓
合并: 3 分钟
────────────────
总计: ~15 分钟
```

**加速比**：约 2-3 倍（取决于 batch 数量和并行度）

## 限制和约束

### 1. 最大并行数

- 默认最大并行 4 个 batch
- 超过限制时，剩余 batch 排队等待

### 2. Worktree 空间

- 每个 worktree 占用磁盘空间
- 建议：至少保留 2GB 可用空间

### 3. Git 要求

- 必须是 Git 仓库
- 需要 Git 2.5+ (支持 worktree)
- `.worktrees/` 必须在 `.gitignore` 中

### 4. Batch 独立性

- 各 batch 应该相对独立
- 强依赖的 batch 应该拆分到不同 feature

## 最佳实践

### ✅ 推荐

1. **合理划分 Batch**
   - 按模块划分（用户模块、订单模块）
   - 按层次划分（后端 API、前端页面）
   - 每个 batch 3-5 个任务为宜

2. **减少文件冲突**
   - 不同 batch 修改不同文件
   - 避免多个 batch 修改配置文件

3. **明确依赖关系**
   - 有依赖的 batch 拆分到不同 feature
   - 或在 plan 中明确依赖顺序

### ❌ 避免

1. **过度并行**
   - 不要为了并行而强行拆分 batch
   - 单个大 batch 可能比多个小 batch 更好

2. **共享文件过多**
   - 避免所有 batch 都修改同一个文件
   - 会导致大量合并冲突

3. **跳过验证**
   - 不要在 batch 中跳过 batch-compile
   - 最终验证不能替代批次验证

## 故障排查

### 问题 1: Workflow 未启动

**症状**：即使有多个 batch，仍使用串行模式

**排查**：
```bash
python hooks/workflow_launcher.py --feature "feat-xxx" --json
```

**可能原因**：
- Batch 状态都是 `done` 或 `failed`
- Plan.json 格式错误

### 问题 2: Worktree 创建失败

**症状**：`git worktree add` 失败

**排查**：
```bash
git worktree list
git check-ignore .worktrees
```

**解决**：
- 确保 `.worktrees/` 在 `.gitignore` 中
- 删除冲突的 worktree：`git worktree remove .worktrees/xxx --force`

### 问题 3: 合并冲突无法解决

**症状**：自动合并失败，手动解决后仍报错

**解决**：
```bash
# 查看冲突文件
git diff --name-only --diff-filter=U

# 手动解决冲突后
git add <冲突文件>
git commit -m "fix: resolve merge conflicts"
```

## 总结

Workflow 并行执行模式适合：
- ✅ 多 batch、相对独立的 feature
- ✅ 需要快速完成的大型 feature
- ✅ 团队协作、多人并行开发

不适合：
- ❌ 单 batch 或少量任务的小 feature
- ❌ Batch 间有强依赖关系
- ❌ 需要逐步验证的复杂重构

选择合适的执行模式，可以显著提高开发效率。
