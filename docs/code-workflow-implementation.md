# Code 阶段 Workflow 并行化完整实现方案

## 一、方案概述

### 1.1 目标

将 autobiz_kanban 的 code 阶段接入 dynamic workflow，实现多个 batch 的并行执行，使用 worktree 隔离避免代码冲突。

### 1.2 核心特性

- ✅ **智能判断**：单 batch 使用串行，多 batch 自动启用并行
- ✅ **Worktree 隔离**：每个 batch 在独立 Git worktree 中执行
- ✅ **冲突检测**：自动检测多个 batch 修改同一文件
- ✅ **顺序合并**：按 batch ID 顺序依次合并到主分支
- ✅ **渐进式**：向后兼容，不影响现有单 batch 流程

### 1.3 性能提升

```
传统串行：B001(10分钟) → B002(8分钟) → B003(12分钟) = 30分钟
并行模式：max(10,8,12) + 合并(3分钟) = ~15分钟

加速比：约 2-3 倍
```

## 二、架构设计

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    /autodev-code 入口                         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │ workflow_launcher.py   │  判断执行模式
              └──────────┬─────────────┘
                         │
            ┌────────────┴────────────┐
            │                         │
      单 Batch                   多 Batch
            │                         │
            ▼                         ▼
    ┌──────────────┐      ┌──────────────────────┐
    │  串行流程     │      │  Workflow 并行流程    │
    │  (原有)      │      │  (新增)              │
    └──────────────┘      └──────────┬───────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
           ┌────────▼────────┐ ┌───▼────┐ ┌────────▼────────┐
           │ worktree_mgr    │ │workflow│ │ batch_merger    │
           │ (创建/删除)      │ │ 脚本   │ │ (冲突/合并)      │
           └─────────────────┘ └────────┘ └─────────────────┘
```

### 2.2 文件结构

```
autobiz_kanban/
├── hooks/
│   ├── workflow_launcher.py          # 判断是否启用 workflow
│   ├── worktree_manager.py           # Worktree 生命周期管理
│   ├── batch_merger.py               # Batch 合并策略
│   └── task_runner.py                # 现有任务执行器（无需修改）
├── workflows/
│   └── code-batched-execution.workflow.js  # 主 workflow 脚本
├── skills/autodev/autodev-code/
│   ├── SKILL.md                      # 已更新：添加 workflow 入口
│   └── references/
│       └── batch-workflow-guide.md   # 使用指南
└── tests/
    └── test_code_workflow_integration.py  # 集成测试
```

## 三、核心组件详解

### 多仓库与多组件边界

并行运行不再假设只有一个业务仓库。Task 的 `workspaceRef` 是唯一仓库标识，Batch 内所有 Task 必须使用同一个 `workspaceRef`；`scope.workspaceRoots` 表示该仓库内的组件根目录。创建 run 时必须传入完整映射：

```bash
python hooks/parallel_batch_scheduler.py create \
  --workspace <artifact-workspace> --feature <feature> \
  --code-workspace backend-api=/repo/services/api \
  --code-workspace frontend-app=/repo/apps/web
```

manifest 会冻结每个仓库的 `requestedPath`、`gitRoot`、`baseSha`、`baseBranch`，并维护只由本 run 合并推进的 `headSha`；每个 Batch 记录 `repositoryRef`、`componentRoots` 和 `gitRoot`。后继 Batch 从该仓库受控的 `headSha` 创建 Worktree，因此能看到已合并依赖；外部提交或未提交修改会被阻断。Worktree、seal、合并、清理、回滚和最终编译均按该绑定执行。

所有无依赖的 Batch 都在独立 Worktree 中并行执行，仓库、组件、Lane 和写集都不构成执行锁；`maxParallel` 只限制全局并发数。合并阶段按 `repositoryRef` 分组并在每个 Git 根内按依赖拓扑顺序回并，Git 冲突在此阶段检测并阻断。跨仓库依赖必须通过 `deps` 表达，不能依赖文件路径推断。

### 冲突策略

Task 通过 `touches=[{path,kind}]` 声明文件触点。普通 `code` 重叠只作事前 warning；`shared`、`proto`、`database`、`configuration` 分别收敛到唯一的 integration、proto 或 global-change Batch。策略分析器会自动生成跨 Batch 依赖；策略不完整时多 Batch 直接阻断并回流 Plan 修复，不能退回串行。Git 冲突不采用 ours/theirs：`batch_merger.py` 预检失败后创建 resolution Worktree，Agent 在其中基于双方目标和 diff 解决冲突、执行 compile、提交 resolution commit，`parallel_conflict_resolver.py` 再把该 commit 合并回对应仓库并记录审计信息。

### 3.1 workflow_launcher.py - 执行模式判断

**功能**：分析 feature 的 batch 结构，决定使用串行还是并行。

路径边界：`--plugin-path` 只定位插件内的 hooks/workflow；`--workspace` 必须是包含 `.autobizdevops/state.json` 的产物目录。业务代码目录不传给 launcher，而是在启动 Workflow 时通过 `codeWorkspaces` 按 `workspaceRef` 传入。

**判断逻辑**：
```python
if batch_count == 0:
    return {"useWorkflow": False, "reason": "no_pending_batches"}
elif batch_count == 1:
    return {"useWorkflow": False, "reason": "single_batch_use_serial"}
else:
    return {"useWorkflow": True, "reason": f"multiple_batches:{batch_count}"}
```

**使用方式**：
```bash
python hooks/workflow_launcher.py \
  --feature "feat-user-auth" \
  --plugin-path "/path/to/plugin" \
  --workspace "/path/to/artifacts/project" \
  --json
```

**输出示例**：
```json
{
  "useWorkflow": true,
  "strategy": "parallel",
  "batchCount": 3,
  "batches": [
    {"id": "B001", "lane": "backend", "taskCount": 4},
    {"id": "B002", "lane": "frontend", "taskCount": 3},
    {"id": "B003", "lane": "backend", "taskCount": 2}
  ],
  "workflowScript": "workflows/code-batched-execution.workflow.js"
}
```

### 3.2 worktree_manager.py - Worktree 管理

**功能**：创建、列出、删除 Git worktree。

**命令**：

1. **创建 worktree**：
```bash
python hooks/worktree_manager.py create \
  --repo /path/to/repo \
  --name feat-user-auth-B001 \
  --json
```

输出：
```json
{
  "success": true,
  "worktreePath": "/path/to/repo/.worktrees/feat-user-auth-B001",
  "branchName": "worktree/feat-user-auth-B001"
}
```

2. **列出 worktrees**：
```bash
python hooks/worktree_manager.py list --repo /path/to/repo --json
```

3. **删除 worktree**：
```bash
python hooks/worktree_manager.py remove \
  --repo /path/to/repo \
  --name feat-user-auth-B001 \
  --force \
  --json
```

**关键特性**：
- 自动验证 `.worktrees/` 在 `.gitignore` 中
- 支持强制删除（即使有未提交的变更）
- 自动清理关联的分支

### 3.3 batch_merger.py - 合并策略

**功能**：检测冲突、合并 worktree、顺序合并多个 batch。

**命令**：

1. **检测冲突**：
```bash
python hooks/batch_merger.py detect-conflicts \
  --batches '[
    {"id":"B001","changedFiles":["src/a.py","src/b.py"]},
    {"id":"B002","changedFiles":["src/b.py","src/c.py"]}
  ]' \
  --json
```

输出：
```json
{
  "conflicts": [
    {"file": "src/b.py", "batches": ["B001", "B002"]}
  ]
}
```

2. **顺序合并**：
```bash
python hooks/batch_merger.py sequential-merge \
  --repo /path/to/repo \
  --worktrees "feat-auth-B001,feat-auth-B002,feat-auth-B003" \
  --batch-ids "B001,B002,B003" \
  --json
```

输出：
```json
{
  "success": true,
  "merged": [
    {"batchId": "B001", "commitSha": "abc123", "filesChanged": 5},
    {"batchId": "B002", "commitSha": "def456", "filesChanged": 3},
    {"batchId": "B003", "commitSha": "ghi789", "filesChanged": 4}
  ],
  "failed": [],
  "totalConflicts": 0
}
```

**合并策略**：
- 使用 `git merge --no-ff` 合并 worktree 分支
- 每次合并后立即提交
- 如果有冲突，手动解决后继续
- 支持部分失败（已成功的不回滚）

### 3.4 code-batched-execution.workflow.js - 主 Workflow

**阶段划分**：

```javascript
phases: [
  { title: "准备", detail: "读取 plan，分析 batch 依赖，创建 worktree" },
  { title: "并行实现", detail: "每个 batch 在独立 worktree 中执行" },
  { title: "冲突检测", detail: "检测 batch 间的文件冲突" },
  { title: "顺序合并", detail: "按依赖顺序合并 batch 到主分支" },
  { title: "最终验证", detail: "验证合并后的完整代码" }
]
```

**关键代码片段**：

```javascript
// 并行执行所有 batch
const batchResults = await pipeline(
  batchExecutions,
  async (execution) => {
    return await agent(
      `执行 batch ${execution.batchId}...`,
      {
        label: `batch-${execution.batchId}`,
        phase: "并行实现",
        isolation: "worktree",  // 关键：使用 worktree 隔离
        schema: BATCH_EXECUTION_SCHEMA
      }
    );
  }
);
```

**Schema 定义**：
- `BATCH_EXECUTION_SCHEMA`：batch 执行结果
- `MERGE_RESULT_SCHEMA`：合并结果
- `VERIFICATION_SCHEMA`：验证结果

## 四、执行流程详解

### 4.1 完整执行流程

```
1. /autodev-code 入口
   ↓
2. 判断执行模式
   python workflow_launcher.py --feature "feat-xxx" --plugin-path "/path/to/plugin" --workspace "/path/to/artifacts/project"
   ↓
   ├─ useWorkflow: false → 串行流程（原有）
   └─ useWorkflow: true  → 启动 workflow
      ↓
3. Workflow 准备阶段
   - 读取 plan.json
   - 解析待执行的 batch 列表
   - 为每个 batch 规划 worktree 名称
   ↓
4. 并行实现阶段
   ┌────────────────────────────────────┐
   │ B001 (worktree isolation)          │
   │ - EnterWorktree                   │
   │ - code-session → B001             │
   │ - 执行所有任务                      │
   │ - batch-compile                   │
   │ - 记录变更文件                      │
   └────────────────────────────────────┘

   ┌────────────────────────────────────┐
   │ B002 (worktree isolation)          │
   │ 同时执行...                         │
   └────────────────────────────────────┘

   ┌────────────────────────────────────┐
   │ B003 (worktree isolation)          │
   │ 同时执行...                         │
   └────────────────────────────────────┘
   ↓
5. 冲突检测阶段
   - 收集所有成功 batch 的变更文件
   - 检测是否有文件被多个 batch 修改
   - 生成冲突报告
   ↓
6. 顺序合并阶段
   B001 → merge → commit
   ↓
   B002 → merge → commit (基于 B001)
   ↓
   B003 → merge → commit (基于 B001+B002)
   ↓
7. 最终验证阶段
   - 运行编译命令
   - 运行类型检查
   - 验证语法正确性
   ↓
8. 完成
   - 更新 checkpoint 为 code_done
   - 清理 worktree（可选）
```

### 4.2 单个 Batch 的执行细节

在 worktree 中，每个 batch 的执行遵循完整的 Code 协议：

```bash
# 1. 进入 worktree
EnterWorktree --name feat-user-auth-B001

# 2. 启动 code session
python task_runner.py code-session --feature "feat-user-auth"
# 返回: action=execute_active_batch, activeBatchId=B001

# 3. 执行该 batch 的所有任务
for task_id in [T001, T002, T003]:
    # 3.1 解析任务上下文
    python code_task_context.py --feature "feat-user-auth" \
      --task-id "$task_id" --code-workspace "backend"

    # 3.2 探索/验证缓存
    # (根据 explorationDirective 执行)

    # 3.3 启动任务
    python task_runner.py start --feature "feat-user-auth" \
      --task-id "$task_id" --code-workspace "backend"

    # 3.4 实现代码
    # (修改业务代码)

    # 3.5 完成实现
    python task_runner.py finish-implementation \
      --feature "feat-user-auth" --task-id "$task_id" \
      --run-id "$run_id" --code-workspace "backend"

# 4. 批次编译
python task_runner.py batch-compile --feature "feat-user-auth" \
  --batch-id "B001" --code-workspace "backend"

# 5. 记录变更的文件
git diff --name-only HEAD~1 HEAD

# 6. 不退出 worktree，保持变更供合并阶段使用
```

### 4.3 合并阶段详细步骤

```bash
# 假设有 3 个成功的 batch: B001, B002, B003

# 回到主工作区
cd <main-workspace>

# 合并 B001
git merge --no-ff worktree/feat-user-auth-B001
if [ $? -eq 0 ]; then
    git commit -m "feat: implement batch B001"
else
    # 解决冲突
    # (手动编辑冲突文件)
    git add <冲突文件>
    git commit -m "feat: implement batch B001"
fi

# 合并 B002（基于 B001 的结果）
git merge --no-ff worktree/feat-user-auth-B002
git commit -m "feat: implement batch B002"

# 合并 B003（基于 B001+B002 的结果）
git merge --no-ff worktree/feat-user-auth-B003
git commit -m "feat: implement batch B003"

# 最终验证
npm run build      # 或 mvn compile
yarn typecheck     # 类型检查

# 清理 worktree（可选）
git worktree remove .worktrees/feat-user-auth-B001 --force
git worktree remove .worktrees/feat-user-auth-B002 --force
git worktree remove .worktrees/feat-user-auth-B003 --force
```

## 五、集成到现有系统

### 5.1 修改 autodev-code/SKILL.md

在 `## 准入检查` 之后、`## 写入 checkpoint` 之前添加：

```markdown
## Workflow 并行执行模式（多 Batch）

当 feature 包含多个待执行的 batch 时，Code 阶段支持使用 workflow 并行执行...
```

✅ 已完成

### 5.2 在 /autodev-code 入口添加判断

```python
# 在 code_in_progress checkpoint 之后添加

# 判断是否使用 workflow
launcher_result = subprocess.run([
    "python", f"{pluginPath}/hooks/workflow_launcher.py",
    "--feature", feature,
    "--plugin-path", pluginPath,
    "--workspace", artifactWorkspace,
    "--json"
], capture_output=True, text=True)

if launcher_result.returncode == 0:
    data = json.loads(launcher_result.stdout)

    if data.get("useWorkflow"):
        # 启动 workflow
        # (使用 Workflow 工具)
        workflow_args = {
            "feature": feature,
            "pluginPath": pluginPath,
            "artifactWorkspace": artifactWorkspace,
            "codeWorkspaces": codeWorkspaces,
        }
        # Workflow 工具调用...
        return
    else:
        # 使用原有串行流程
        # (现有代码继续执行)
        pass
```

## 六、测试验证

### 6.1 运行集成测试

```bash
cd /Users/liuxuyang/WebstormProjects/autobiz_kanban
python3 tests/test_code_workflow_integration.py
```

**预期输出**：
```
============================================================
Code Workflow 集成测试
============================================================

测试 1: Workflow Launcher
------------------------------------------------------------
✓ 处理不存在的 feature

测试 2: Worktree Manager
------------------------------------------------------------
✓ 创建 worktree: /tmp/xxx/test-repo/.worktrees/test-batch-001
  分支: worktree/test-batch-001
✓ Worktree 目录存在
✓ 列出 worktrees: 2 个
✓ 删除 worktree

测试 3: Batch Merger
------------------------------------------------------------
✓ 检测到 2 个冲突
  - src/b.py: B001, B002 ✓
  - src/c.py: B002, B003 ✓
✓ 冲突检测准确

测试 4: Workflow 脚本语法
------------------------------------------------------------
✓ meta 定义
✓ phase 函数调用
✓ agent 函数调用
✓ pipeline 函数调用
✓ 执行 schema
✓ 合并 schema
✓ 验证 schema

测试 5: 技能集成
------------------------------------------------------------
✓ Workflow 章节
✓ 启动器引用
✓ Worktree 隔离说明
✓ 阶段说明

============================================================
测试结果汇总
============================================================
✓ 通过   Workflow Launcher
✓ 通过   Worktree Manager
✓ 通过   Batch Merger
✓ 通过   Workflow Script Syntax
✓ 通过   Skill Integration
------------------------------------------------------------
总计: 5/5 通过
```

### 6.2 手动测试场景

**场景 1：单 Batch**
```bash
# 预期：使用串行流程
python hooks/workflow_launcher.py --feature "feat-single-batch" --plugin-path "/path/to/plugin" --workspace "/path/to/artifacts/project" --json
# 输出: {"useWorkflow": false, "reason": "single_batch_use_serial"}
```

**场景 2：多 Batch**
```bash
# 预期：启用 workflow
python hooks/workflow_launcher.py --feature "feat-multi-batch" --plugin-path "/path/to/plugin" --workspace "/path/to/artifacts/project" --json
# 输出: {"useWorkflow": true, "batchCount": 3}
```

**场景 3：完整流程**
```bash
# 1. 创建测试 feature（包含 3 个 batch）
# 2. 调用 /autodev-code
# 3. 观察 workflow 执行
# 4. 验证最终合并结果
```

## 七、监控和调试

### 7.1 Workflow 执行监控

使用平台的 workflow 面板：
```
/workflows
```

查看：
- 各 batch 的执行状态
- 并行度
- 错误信息
- Token 消耗

### 7.2 调试工具

**查看 worktree 列表**：
```bash
cd <repo>
git worktree list
```

**查看 batch 变更**：
```bash
git log worktree/feat-xxx-B001 --oneline -5
git diff HEAD worktree/feat-xxx-B001
```

**手动触发合并**：
```bash
python hooks/batch_merger.py sequential-merge \
  --repo <repo> \
  --worktrees "feat-xxx-B001,feat-xxx-B002" \
  --batch-ids "B001,B002" \
  --json
```

### 7.3 常见问题排查

**问题 1：Workflow 未启动**
```bash
# 检查判断逻辑
python hooks/workflow_launcher.py --feature "feat-xxx" --plugin-path "/path/to/plugin" --workspace "/path/to/artifacts/project" --json

# 查看 batch 状态
cat .autobizdevops/features/feat-xxx/plan.json | jq '.batches'
```

**问题 2：Worktree 创建失败**
```bash
# 检查 .gitignore
cat .gitignore | grep worktrees

# 手动添加
echo ".worktrees/" >> .gitignore
git add .gitignore
git commit -m "chore: ignore worktrees"
```

**问题 3：合并冲突**
```bash
# 查看冲突文件
git diff --name-only --diff-filter=U

# 手动解决
vim <冲突文件>
git add <冲突文件>
git commit -m "fix: resolve merge conflicts"
```

## 八、性能优化建议

### 8.1 并行度调整

在 workflow 脚本中调整：
```javascript
const MAX_PARALLEL_BATCHES = 4;  // 根据机器性能调整
```

### 8.2 Worktree 清理策略

**策略 1：延迟清理**
- Workflow 完成后保留 worktree
- 手动清理或定期清理

**策略 2：即时清理**
- 合并成功后立即删除对应 worktree
- 节省磁盘空间

### 8.3 缓存优化

- 复用 code exploration 缓存
- Batch 间共享基础探索结果
- 减少重复的项目框架分析

## 九、后续扩展

### 9.1 智能批次划分

根据任务依赖自动优化 batch 划分：
```python
# 分析任务依赖图
# 生成最优 batch 分组
# 最小化跨 batch 依赖
```

### 9.2 增量合并

只合并有变更的 batch：
```python
# 跳过空 batch
# 减少不必要的 merge 操作
```

### 9.3 冲突预测

在执行前预测可能的冲突：
```python
# 分析任务的 scope
# 预测文件修改范围
# 提前警告可能的冲突
```

## 十、总结

### 10.1 已实现功能

✅ Workflow 启动判断（单/多 batch）
✅ Worktree 生命周期管理
✅ Batch 冲突检测和合并
✅ 主 Workflow 脚本
✅ 技能文档更新
✅ 使用指南
✅ 集成测试

### 10.2 优势

- **性能提升**：2-3 倍加速
- **隔离安全**：避免代码冲突
- **向后兼容**：不影响现有流程
- **可观察性**：通过 workflow 面板监控
- **可恢复性**：支持中断后继续

### 10.3 文件清单

```
autobiz_kanban/
├── hooks/
│   ├── workflow_launcher.py          ✅ 已创建
│   ├── worktree_manager.py           ✅ 已创建
│   └── batch_merger.py               ✅ 已创建
├── workflows/
│   └── code-batched-execution.workflow.js  ✅ 已创建
├── skills/autodev/autodev-code/
│   ├── SKILL.md                      ✅ 已更新
│   └── references/
│       └── batch-workflow-guide.md   ✅ 已创建
└── tests/
    └── test_code_workflow_integration.py  ✅ 已创建
```

### 10.4 下一步

1. **运行测试**：验证所有组件正常工作
2. **试点运行**：在小型 feature 上测试完整流程
3. **监控优化**：收集性能数据，优化并行度
4. **推广使用**：在团队中推广 workflow 模式

---

**实施完成！** 🎉

所有代码和文档已就绪，可以开始测试和使用。
