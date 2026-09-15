# Plan JSON 数据契约

`plan.json` 是 Feature 的根计划；每个 Batch 的详细任务位于
`plans/{batchId}/plan.json`。正式文件只能通过 `hooks/plan_writer.py`
创建或更新。旧的 `batchValidationProfiles` / `batchValidation` 契约不再
受支持，遇到它们必须重跑 Plan。

## 模型输入与增量修订

运行时 JSON 保持完整、可执行的合同；模型不直接生成它。模型使用两个较小的
输入合同，writer 负责投影、补齐和校验：

| 输入 | Schema | 目的 |
|---|---|---|
| Plan Core | `autodev.plan-core.v1` | 候选 Task 的 outcome、dependsOn、writeSet、refs 与 validation.seam；仅超出粒度软上限时再写 validation.mergeJustification。`writeSet` 是唯一候选写入归属；默认按文件独占，也可用稳定 `symbols` 声明同一 Controller/Service 的互不重叠方法归属。 |
| Task Detail | `autodev.plan-detail.v1` | 单一 Task 的 context、implementation、acceptance、checks、refs 和 nonGoals；默认复用 Core 的 outcome。 |
| Repair Patch | `autodev.plan-repair-patch.v1` | 仅修订工单允许的字段；Draft 修订带 `workId`、`baseRevision`，预 Draft 的 Core 修订带 `workId`、`baseGroupingDigest`；两者每条操作都必须带 `expectedHash`。 |

Core 中 `outcome`、`dependsOn`、`writeSet`、`refs`、`validation.seam`、`validation.mergeJustification` 分别投影到
运行时的标题/默认 goal、依赖、写入提示/范围、规格/API 引用、验证边界和（仅例外任务的）`mergedScenarioRefs` / `splitRationale`。模型不得显式写后两项，且 merge 理由必须列出完整 `specs/<capability>/spec.md#SCN-NNN` 引用。Detail 中的验收 ID、
命令 ID、命令 cwd、workspace roots、lane、Batch 与 `PLAN.md` 都由 writer 生成，不能
由模型回写。

生成某个任务时使用：

```bash
python hooks/plan_writer.py show-draft-task-work \
  --feature "<feature>" --task-id T001
```

它只返回 T001、当前 Draft revision 及 writer-owned 字段。修订时先把 validator 或
评审输出交给 `create-repair-work`，再用 `apply-draft-patch` 提交受限 `replace` 操作。首次
Core 预检尚未创建 Draft 时，传 `create-repair-work --group-file <task-groups.json>`：工单绑定
原文件的 `groupingDigest`，只开放 `writeSet`、`dependsOn`、`validation.mergeJustification`
等必要路径。writer 拒绝过期 revision/digest、字段哈希不匹配、未列出的路径、全量计划替换
以及对 Batch/`PLAN.md` 的修改；它不会通过删除或重建 `task-groups.json` 完成修复。Draft
已存在时成功后仅重投影 patch 涉及的 Task，并记录 `preservedTaskIds` / `resetTaskIds`；共享
写集、DAG 等跨 Task 不变量仍由脚本做全局校验。未提供 `symbols` 的共享路径仍按整文件
冲突处理；提供 symbols 时，只有跨 Batch 重叠的同一 symbol 才冲突。

Core 使用 `write-task-groups --body-stdin` 提交：它会先在内存中运行完整预检，只有通过后才原子
写入 `.tmp/plan_writer/task-groups.json`，不需要可复用的临时 JSON 文件。返回的
`grouping.detailObligations` 会提前声明 SCN matrix 的 Detail 命令要求。`preflight-task-groups`
仍可对已存在的 Core 源做只读检查，并会在一次调用中返回可独立判断的结构、粒度、共享写集、Design API、
SCN 实体与场景覆盖问题；每项带 `validationStage`。SCN 必须逐条写成
`specs/<capability>/spec.md#SCN-NNN`，writer 会校验该 ID 真正定义在该文件中。首次完整 Detail
提交使用 `lint-draft-task-details --full --body-stdin`，通过后将同一 `{taskId, body}` payload 交给
`set-draft-task-details --full --body-stdin`；二者使用同一候选 Draft 和聚合预检，任一失败均不写入。
已 finalized 且尚未执行时，
若 Design snapshot 与 Core digest 同时漂移，`diagnose-plan-repair` 返回
`full_rebuild_required`；使用 `rebuild-finalized-draft --group-file <file>
--design-revision-confirmed --reason <reason>` 重投影，不能删除 Draft。

## 根计划

| 字段 | 必填 | 说明 |
|---|---:|---|
| `featureId` | 是 | Feature 标识。 |
| `status` | 是 | Feature 状态。 |
| `taskSetStatus` | 是 | `collecting`、`finalized` 或完成态。 |
| `taskSetDigest` | 是 | 根计划和 Batch 投影的一致性摘要。 |
| `implementationScope` | 是 | `backend_only`、`frontend_only` 或 `full_stack`。 |
| `batchPolicy` | 是 | 固定策略 `minimal_closed_delivery_v2`；默认一 TASK 一 Batch，显式原子组最多 3 个 TASK。 |
| `taskValidationPolicy` | 是 | 目前固定为 `defer_to_test_stages` / `review_only`。 |
| `batches` | 是 | Batch 索引；每项包含 `id`、`path`、`executionLane`、`deliveryKind`、`taskIds`、`deps`、`status`。 |
| `qualityGateProfiles` | 是 | 按 lane 的静态检查命令源，可为空。只允许 required `static_check` 命令。 |
| `projectValidationCommands` | 是 | B-INT 唯一拥有的集成验证命令。 |
| `parallelBatchPipeline` | 最终化后 | 批次 DAG、命令唯一归属和 B-INT/B-E2E 约束。 |

`qualityGateProfiles` 的结构如下：

```json
{
  "backend": {
    "commands": [
      {
        "argv": ["mvn", "checkstyle:check", "-pl", "ruoyi-admin"],
        "cwd": ".",
        "kind": "static_check",
        "required": true,
        "repo": "RouYi"
      }
    ]
  }
}
```

`qualityGateProfiles` 的命令 `kind` 必须为 `static_check`，例如 lint、typecheck 或其他不运行
TASK 测试的静态检查。

## Batch 计划

| 字段 | 必填 | 说明 |
|---|---:|---|
| `featureId`、`batchId`、`title` | 是 | Batch 身份信息。 |
| `executionLane` | 是 | `backend` 或 `frontend`。 |
| `status`、`taskCount`、`completedTaskCount` | 是 | Batch 进度投影。 |
| `tasks` | 是 | 当前 Batch 唯一的 TASK 合同。 |
| `qualityGateCommands` | 是 | 当前 Batch 的 required `static_check` 命令数组；没有静态检查时为 `[]`。ID 为 `BATCH-Bxxx-QUALITY-nnn`。 |
| `mergeCommitSha`、`deliveryRunId` | 运行时 | Merge Train 推广后的提交与运行引用。 |

`deliveryKind` 为 `single_task` 时，`taskIds` 必须恰好一个。只有 Task 明确携带
相同的 `atomicGroup: { id, rationale }` 时才允许 `atomic_group`；该组必须有 2–3 个
Task，且全部同一 `workspaceRef`、执行 lane 与执行阶段。依赖、相同模块/页面或文件
重叠都不会自动合并 Batch；共享写入必须指定唯一 owner，不能指定 owner 时才可声明原子组。
旧的自动聚合策略不再兼容；其 Plan 必须重新生成，不能在运行中改写 Batch 映射。

`qualityGateCommands` 不得用于补跑编译、单测或 E2E。数组为空时，运行时
不会创建 `quality_gate` 状态或空证据；数组非空时，在 `test` 通过或失败已记录后逐条
运行这些命令。

## 验证阶段和命令归属

| 阶段 | 拥有的命令/工作 | 是否每 Batch 都有 |
|---|---|---:|
| `prepare` | Worktree 与交付准备 | 是 |
| `implement` | TASK 实现与草稿封存 | 是 |
| `review` | 代码评审 | 是 |
| `test` | TASK unit/behavior test intent 与测试资产封存 | 是 |
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

每个 Batch 先编码并草稿封存，随后执行 `review`；Review 通过（或其一次
定向修复完成）后直接进入 Batch `test`。
`review` 发现可由当前 Batch 修复的生产代码问题时，必须以 `implementation`
分类回流：在原 Worktree、原分支中按失败上下文修复一次，实际
封存，并以 `single_repair_accepted` 记录该检查已被定向修复。Review
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
