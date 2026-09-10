# Plan JSON 数据契约

`plan.json` 是 Feature 的根计划；每个 Batch 的详细任务位于
`plans/{batchId}/plan.json`。正式文件只能通过 `hooks/plan_writer.py`
创建或更新。旧的 `batchValidationProfiles` / `batchValidation` 契约不再
受支持，遇到它们必须重跑 Plan。

## 根计划

| 字段 | 必填 | 说明 |
|---|---:|---|
| `featureId` | 是 | Feature 标识。 |
| `status` | 是 | Feature 状态。 |
| `taskSetStatus` | 是 | `collecting`、`finalized` 或完成态。 |
| `taskSetDigest` | 是 | 根计划和 Batch 投影的一致性摘要。 |
| `implementationScope` | 是 | `backend_only`、`frontend_only` 或 `full_stack`。 |
| `batchPolicy` | 是 | 固定策略 `spec_capability_execution_lane_topological` 与最大 5 个 TASK。 |
| `taskValidationPolicy` | 是 | 目前固定为 `defer_to_test_stages` / `batch_compile_only`。 |
| `batches` | 是 | Batch 索引；每项包含 `id`、`path`、`executionLane`、`taskIds`、`deps`、`status`。 |
| `compileProfiles` | 是 | 按 lane 的编译命令源。每个实际使用的 lane 在 Plan 最终化后必须有一条 required `compile` 命令。 |
| `qualityGateProfiles` | 是 | 按 lane 的静态检查命令源，可为空。只允许 required `static_check` 命令。 |
| `projectValidationCommands` | 是 | B-INT 唯一拥有的集成验证命令。 |
| `parallelBatchPipeline` | 最终化后 | 批次 DAG、命令唯一归属和 B-INT/B-E2E 约束。 |

`compileProfiles` 与 `qualityGateProfiles` 的结构相同：

```json
{
  "backend": {
    "commands": [
      {
        "argv": ["mvn", "compile", "-pl", "ruoyi-admin", "-am"],
        "cwd": ".",
        "kind": "compile",
        "required": true,
        "repo": "RouYi"
      }
    ]
  }
}
```

`compileProfiles` 的命令 `kind` 必须为 `compile`；`qualityGateProfiles`
的命令 `kind` 必须为 `static_check`，例如 lint、typecheck 或其他不运行
TASK 测试的静态检查。

## Batch 计划

| 字段 | 必填 | 说明 |
|---|---:|---|
| `featureId`、`batchId`、`title` | 是 | Batch 身份信息。 |
| `executionLane` | 是 | `backend` 或 `frontend`。 |
| `status`、`taskCount`、`completedTaskCount` | 是 | Batch 进度投影。 |
| `tasks` | 是 | 当前 Batch 唯一的 TASK 合同。 |
| `compileCommand` | Plan 最终化后 | 当前 Batch 唯一的 required `compile` 命令，ID 为 `BATCH-Bxxx-COMPILE`。 |
| `qualityGateCommands` | 是 | 当前 Batch 的 required `static_check` 命令数组；没有静态检查时为 `[]`。ID 为 `BATCH-Bxxx-QUALITY-nnn`。 |
| `batchCompile` | 运行时 | 编译执行状态、失败分类及修复次数；不是命令配置。 |
| `mergeCommitSha`、`deliveryRunId` | 运行时 | Merge Train 推广后的提交与运行引用。 |

`compileCommand` 在每个 Batch 的 `review` 通过后（或 Review 的一次定向修复
完成后）首次执行。它是生产编译，不运行 TASK 测试；若 Review 修复了生产代码，
必须使用重新验证命令实际再编译，不能复用原先的通过缓存。

`qualityGateCommands` 不得用于补跑编译、单测或 E2E。数组为空时，运行时
不会创建 `quality_gate` 状态或空证据；数组非空时，在 `test` 通过或失败已记录后逐条
运行这些命令。

## 验证阶段和命令归属

| 阶段 | 拥有的命令/工作 | 是否每 Batch 都有 |
|---|---|---:|
| `prepare` | Worktree 与交付准备 | 是 |
| `implement` | TASK 实现与草稿封存 | 是 |
| `review` | 代码评审；通过后首次生产编译与正式封存 | 是 |
| `test` | 生产编译/正式封存后的 TASK unit/behavior test intent | 是 |
| `quality_gate` | `qualityGateCommands` | 仅有静态检查时 |
| `V-INT` | 临时合并候选上的 `projectValidationCommands` 与 integration intent | 每个 Merge Train |
| `V-E2E` | 所有 delivery Batch 推广后的一次 E2E | 每个 Feature Run 一次 |

`parallelBatchPipeline.validationOwnership` 是可执行验证的唯一归属表：同一
command ID 只能出现一次。Evidence 绑定计划版本、Batch 提交、依赖提交和
命令摘要；任一内容变化都会使相关 evidence 失效。

## 合并与清理

delivery Batch 先完成上述阶段，再进入 Merge Train。B-INT 在临时候选分支
上通过后，才允许 fast-forward 推广同一候选 SHA。所有 delivery Batch 合并
后才执行 B-E2E；它不会在每个 Batch 完成时运行。

每个 Batch 先编码并草稿封存，随后执行 `review`；只有 Review 通过（或其一次
定向修复完成）后，才执行该 Batch 的首次编译和正式封存，再进入 Batch `test`。
`review` 发现可由当前 Batch 修复的生产代码问题时，必须以 `implementation`
分类回流：在原 Worktree、原分支中按失败上下文修复一次，实际
重新编译和封存，并以 `single_repair_accepted` 记录该检查已被定向修复。Review
修复后直接进入 `test`，不会重新执行同一检查形成循环。Batch `test` 的任何最终
失败都会保留真实 runner Evidence 和非阻断 issue，然后继续质量门、Merge Train 与
E2E；最终无其他阻断错误时标记为 `succeeded_with_issues`，不得将失败伪装为通过或
从最终报告中省略。

review 以 `implementation` 分类回流后，Batch 会临时处于 `running`，但其
封存 commit 与 Worktree 仍有效。scheduler 必须把这种“有 commit、后置阶段未
完成”的 `running` Batch 作为 stage recovery 返回，恢复 implement 或记录
deferred finding；不得直接调 test，也不得因它不再是 `sealed` 而停滞。

已推广的 delivery Batch 会立即删除插件管理的 Worktree、Batch 分支和 lease。
失败、阻断或待修复的 Worktree 保留供诊断；回退 Code 阶段时，插件先通过
生命周期接口清理 active run 资源，再重置调度和 Feature 状态。

## Plan Writer 命令

```bash
# 每个实际 lane 必需的一条编译命令
python hooks/plan_writer.py add-compile-command \
  --feature "<feature>" --lane backend \
  --command "mvn compile -pl ruoyi-admin -am" \
  --code-workspace "<business-repository>"

# 可选：仅在需要 lint / 静态检查时声明
python hooks/plan_writer.py add-quality-gate-command \
  --feature "<feature>" --lane backend \
  --command "mvn checkstyle:check" \
  --code-workspace "<business-repository>"
```

执行 Plan 阶段 schema 检查：

```bash
python skills/autodev/hooks/artifact_check.py schema autodev-plan \
  --repo-root . --workspace-root .
```
